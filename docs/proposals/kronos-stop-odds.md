# Proposal: stop-hit odds for open positions (with Kronos as a candidate model)

**Status: parked (2026-09-19).** Nothing here is implemented. This records the analysis and plan so
it can be picked up later without redoing the research.

## Goal

For each open position, show the probability that price touches **the stop** or **the liquidation
price** within the next 24h and 3d, plus the expected 80% price range. Context on the dashboard at
first; an alert rule only if live calibration later justifies it.

This feature ships **with or without Kronos**: a simple volatility model gives usable stop odds.
Kronos is only adopted if it measurably beats that model. That is the same "earn its place" bar the
macro filters were held to (README, "Macro / non-price filters").

## What Kronos is

[shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos), AAAI 2026, MIT licence (code and the
Hugging Face weights). It's a decoder-only foundation model for OHLCV candles:

- a tokenizer quantises candles into hierarchical discrete tokens;
- an autoregressive transformer then samples future candles.

| Model | Parameters | Context | Released |
|---|---:|---:|---|
| Kronos-mini | 4.1M | 2048 | yes |
| Kronos-small | 24.7M | 512 | yes |
| Kronos-base | 102M | 512 | yes |
| Kronos-large | 499M | 512 | no |

It isn't pip-installable (examples import a `model/` folder via `sys.path`). Its dependencies are
torch, einops, huggingface_hub and safetensors.

## Findings that shape the design

1. **`predict()` averages its sampled paths.** At `model/kronos.py:467`,
   `np.mean(preds, axis=1)` discards exactly the distribution this feature needs. Workaround
   without forking: `predict_batch` with N copies of the same series and `sample_count=1` returns N
   independent paths.
2. **Leakage and checkpoint provenance are unresolved upstream.**
   - The paper reportedly pretrains on data through June 2024.
   - Issue #227: the fine-tune dataset normalised over the forecast window (fixed in code since).
   - Issue #307: no maintainer has confirmed whether the published checkpoints were retrained
     after that fix.
   - The *inference* path itself is clean: it normalises on the lookback only.

   **Only data after the weights were published counts as out-of-sample.** Kronos-small was last
   modified on 2025-09-09, so the evaluation window starts **2025-09-10**.
3. **The community reports weak zero-shot skill.** The most-discussed issues include "worse than a
   coin flip" and "useless without finetune" reports. Kronos must beat simple baselines, not just
   draw plausible charts.
4. **Hyperliquid's ~5000-candle cap is enough.** 4h bars give about 2.3 years, which covers
   512-bar contexts across the test window.

## Phase 1: Evaluation (Colab/Kaggle GPU; no production changes)

Deliverable: `research/kronos_stop_odds.ipynb`. Its maths lives in a shared `hlg/stoprisk.py`, so
evaluation and production compute probabilities identically.

**Question.** At each origin bar, what is P(touch a barrier within H bars)?

- **Barriers:** ±{1, 1.5, 2, 3} × ATR14 from the close, on both sides.
- **Horizons:** 6 and 18 4h-bars (24h / 3d).
- **Touch scoring:** by bar high/low, both for outcomes and for Kronos paths.
- **Data:** `hlg/backtest.py::bars(coin, cache, "4h")` for the scanner coins, one origin per day
  per coin. That's about 4,000 origins × 8 barriers × 2 horizons.

**Competitors:**

| # | Model |
|---|---|
| A | Brownian barrier formula with EWMA volatility: `P = 2·(1 − Φ(d / (σ·√H)))` |
| B | Block bootstrap of the coin's last 500 4h returns |
| C | Climatology: the historical touch rate for that k×ATR and H |
| K | Kronos, N sampled paths: mini/small; context 256/512; T ∈ {0.8, 1.0}; N ∈ {32, 64} |

**Protocol (pre-registered):**

- **Tuning half, 2025-09-10 → 2026-02-28:** choose one Kronos config and the baselines'
  parameters.
- **Test half, 2026-03-01 → now:** frozen configs, scored once.
- **Metrics:** Brier score, log loss, reliability/ECE and 80% band coverage per (k, H). Also the
  Brier skill score of K vs the best of A/B/C, with a 95% CI from a **day-block bootstrap** (coins
  on the same day are correlated).
- **Adoption rule:** Kronos ships only if, on the test half, its Brier skill score is > 0 with a
  CI excluding 0 **and** its ECE is no worse. Otherwise the best baseline ships and the README
  records the negative result.
- **Also recorded:** CPU inference time, which decides whether GitHub Actions can run it.
- **Optional 1b:** if K is close but not significant, one fine-tune on pre-window HL 4h data
  (`finetune_csv/`, with the #227-fixed normalisation) and one rescore. Not a tuning loop.

## Phase 2: Production

- **Always:** baseline odds in the monitor (numpy, no torch).
  - New `hlg/stoprisk.py`: `brownian_touch_prob`, `bootstrap_touch_prob`, `paths_touch_prob`,
    `ewma_sigma`.
  - In the guardrails position loop, reuse `stop_coverage` (`trig`), `want_stop` (for positions
    without a stop: "if you place the recommended stop") and `liquidationPx`.
  - 4h candles come from `scanner.candles` for ≤3 open positions.
- **Only if Kronos passes:**
  - Vendor `model/kronos.py` and `model/module.py` into `hlg/vendor/kronos/`, pinned to an
    upstream commit, with the MIT licence and a provenance note.
  - Load weights pinned by HF revision (Kronos-small `901c26c1332695a2a8f243eb2f37243a37bea320`
    plus the tokenizer's SHA).
  - New `hlg/forecast.py` (copies + `predict_batch(sample_count=1)`, fixed seed).
  - New `.github/workflows/forecast.yml`, every 4h:
    - CPU torch from a separate `requirements-forecast.txt`;
    - reads `HLG_ACCOUNT` for positions;
    - output **encrypted with the vault**, because public-repo artifacts are downloadable and
      would reveal held coins;
    - uploaded as a 1-day artifact; it **does not deploy Pages**.
  - `monitor.yml` gains `actions: read` and downloads the artifact. If it's missing, stale (> 8h)
    or undecryptable, the monitor falls back to the baseline, with the source labelled.
  - **The monitor never imports torch.** A test asserts it.
- **Dashboard:** "Stop hit 24h / 3d" and "Liq hit 24h" columns with a source tag, plus the 80%
  range.
- **Live calibration:** each prediction is logged to encrypted `history.json` and resolved after
  H. The Flow tab shows the running Brier score and a reliability table. That evidence, not this
  plan, would decide whether stop odds ever become alerts.

## Tests

- **`stoprisk`:**
  - the formula matches a 200k-path Monte Carlo within 1pp;
  - monotonic in distance and in H;
  - long/short symmetry;
  - a level at or behind price gives 1.0.
- **`forecast`:** a fake predictor tests batching, seeding, shape and encryption. A golden
  regression test (the pattern from Kronos's own `tests/test_kronos_regression.py`) runs only in
  the forecast workflow.
- **`report`:** fallbacks work, and torch is never imported.

## Not adopted from the Kronos repo

- **Direction/price forecasts as trade signals:** no out-of-sample evidence of skill on crypto.
- **Its Flask web UI:** the PWA already covers this, with encryption.
- **The Qlib top-K backtest:** built for equity cross-sections.

## Licensing note for a commercial product

Both the Kronos code and weights are MIT. Commercial use is fine with the copyright and licence
notice kept in the vendored files and in product documentation.
