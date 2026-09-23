# Changelog

Dates are UTC. For the details and reasoning, see the commit messages.

## 2026-09-23 (morning)

- **Pyramiding, live half:** the signal journal records a hypothetical add on every signal under the
  backtested rule (fresh breakout on an open entry, fills at the next open only once the stop is at
  or above the first entry, shares the stop, exits with the entry), with its own R. The journal card
  shows the per-timeframe tally, labelled "tracking only, not advice". No alert text changes.

- **Pyramiding: tested, not adopted** (`--pyramid-study`, README "Pyramiding"). One add on a fresh
  breakout, only once the shared trailing stop is at or above the first entry, 1.5% risk to that
  stop, all units exit together. Fails the pre-set bar on both 1d (PF 1.67 -> 1.72, DD -18.4% ->
  -22.3%) and 4h (1.38 -> 1.41, -40.4% -> -48.2%): the adds are profitable on their own but mostly add
  exposure. A post-hoc check at equal drawdown shows 4h adds beating plain bigger sizing (+321% vs
  +269%, Sharpe 1.29 vs 1.21) and 1d a wash: 4h pyramiding is flagged for its own pre-registered test.
  The engine now carries adds as units of one position (one trade row per unit); the baseline
  reproduces exactly (126 trades, PF 1.67).
- Daily brief cost corrected from the first live run: 8.6k tokens in / 0.8k out = ~$0.05/day.

## 2026-09-23 (late night)

- **Daily brief: JSON enforced by the provider.** The first live brief (06:01 UTC) was dropped: the
  model's reply had an unescaped quote inside a string, so it wasn't valid JSON. OpenRouter calls now
  send a strict `json_schema` with `provider.require_parameters` (only endpoints that enforce it),
  the prompt says how to quote, and a malformed reply is reported as "model reply wasn't valid JSON".
  New manual workflow input **force_digest** regenerates the brief without waiting for the 2h retry.

- **Daily brief via OpenRouter.** `OPENROUTER_API_KEY` (preferred) or `ANTHROPIC_API_KEY`. Through
  OpenRouter it asks Claude Opus 5.5 first, falling back to Claude Sonnet 5 then GPT-6 Sol via
  OpenRouter's documented `models` fallback; the brief shows which model wrote it. ~$0.04/day at
  September 2026 list prices. Model list in `digest.openrouter_models`.

- **Macro tab rebuilt around news, calendar and sentiment.**
  - **Calendar** (`hlg/events.py`): high-impact US releases from the free FairEconomy weekly feed plus
    the Fed's own FOMC schedule. A **pushed** heads-up 24h before each one names your open positions;
    breakout alerts note a release within 24h. The feed rate-limits hard (HTTP 429), so fetches are
    6-hourly with a one-hour backoff after a failure.
  - **Daily brief** (`hlg/digest.py`): once a UTC day, ~70 headlines from 11 RSS feeds (Fed, ECB,
    Bloomberg, FT, CNBC, MarketWatch, CoinDesk, The Block, Cointelegraph, Decrypt) summarised by
    Claude into a headline, a risk-on/neutral/risk-off tilt with confidence, cited points and a watch
    list. Needs the `ANTHROPIC_API_KEY` secret. Only public data goes in the prompt; headlines are
    treated as untrusted (strict JSON reply, feed-sourced http(s)-only links, E2E-tested against
    injected markup). Reading material: not backtestable, never pushed, gates nothing.
  - **Fear & Greed** (`hlg/sentiment.py`): value, band and 90-day line.
- **Tested, not adopted** (README "Sentiment", "Events"):
  - Fear & Greed level vs forward returns is *positive* (30d: BTC +0.20, basket +0.28): the
    contrarian claim is backwards. Extreme-band entry filters fail on 1d; `fng_not_extreme_fear`
    passes on 4h (PF 1.38 -> 1.57, 98th pctile) but is one of four tests and mostly one market phase
    (76 of 82 removed trades in 2025-Q4..2026-Q3), so it's shown as a context line on 4h breakouts
    instead of becoming a rule.
  - Skipping entries in the 24h before FOMC: removes one trade in three years. Nothing to act on.
  - Measured the premise of the event alert: BTC's range in the hour after an FOMC decision is 2.6x
    a normal hour (27 decisions since 2023-06); the FOMC alert quotes it.
- Fixed before it shipped: `fomc_ahead` divided a pandas-3 microsecond index as if it were
  nanoseconds, which would have made the FOMC filter silently never fire; caught by its unit test.

## 2026-09-23 (night)

- **AVAX added to `scanner.coins` and `rules.allowed_coins`** — on your own read that the market is
  moving into a different phase than the one the backtest data covers, not because it cleared the
  candidate-study bar: solo against the other 11 coins it passes 1d on a 4-trade sample (PF 2.03)
  but fails 4h outright (candidates net -$191 over 23 trades), the same fragile shape as the
  INJ/TAO/FET candidates already rejected. Documented as an explicit override in README "Universe"
  so it doesn't read as a backtest result later. Still governed by every existing guardrail and
  stop-loss rule, same as any other scanned coin.
- **Weekly universe-study re-run**, so a real regime shift doesn't require remembering to re-check
  by hand: `.github/workflows/universe-study.yml` (Monday 06:00 UTC + manual dispatch) runs
  `scripts/weekly_universe_study.py`, which reruns `--universe-study` for 1d and 4h against current
  history and commits the trend to `docs/research/universe-study.md` /
  `docs/research/universe-study-history.csv` if the result changed. Pure market backtest output, no
  account data. **Never edits `config.yaml`** — a pass still needs a human decision.
- New `hlg/backtest.py::universe_study` now returns `(rows, passed)` instead of only printing, so
  the weekly script (and anything else) can read the result without re-parsing markdown.

## 2026-09-23 (evening)

- **Adding specific coins (INJ, AVAX, TAO, FET): tested, not adopted.** New
  `hlg/backtest.py::candidate_study` (`--candidate-study COIN [COIN...]`) tests naming coins onto
  the live `scanner.coins` list directly, rather than ranking by liquidity like `--universe-study`
  does. Pre-registered adoption rule (combined PF >= 1.4, max DD no worse than +5pp, OOS PF > 1.2,
  candidates' own net P&L >= 0): 1d technically passes (PF 1.89 -> 1.92) but the win turns out to
  be a shared-portfolio-slot artifact — three of the four candidates lose money or go flat
  out-of-sample when tested alone; 4h fails outright (PF 1.53 -> 1.41, drawdown -26% -> -32%,
  candidates net -$340). Not added. README "Universe" has the full breakdown.
- **Found and documented, not changed: the live `scanner.coins` (11) isn't the backtest's research
  list (`DEF["coins"]`, 15).** UNI/AAVE/AVAX/TON are backtested but not scanned live. Checked
  per-coin for the first time: AAVE and UNI were the worst two performers of the 15 (reasonably
  left out); AVAX was actually profitable and isn't scanned; XRP and NEAR are scanned despite
  backtesting worse. The live list predates this breakdown rather than deriving from it — left
  alone pending a deliberate decision, documented in README "Universe".

## 2026-09-23 (later)

- **Funding alerts rewritten to explain themselves.** The old text was a bare number and a
  mechanic ("longs pay; carry = long spot + short perp") with no reason to care. Now each alert
  says what the rate costs in real terms (`$/day per $10,000 held`), the crowd-positioning read
  behind it (a large one-sided rate usually means the crowd is leaning hard one way — a caution
  flag, not a directional call), and, only for the side that's actually capturable on Hyperliquid
  (no spot-short here), how to collect it risk-free. The summary line now says which side is
  crowded at a glance. Added a standing one-line explainer under the "Info" group ("context only —
  never pushed, not part of the tested strategy") so the framing doesn't depend on reading one
  alert's full text. `sw.js` cache bumped (v10 -> v11).

## 2026-09-23

- **Theme toggle.** The dashboard already had a dark and a light palette but only ever followed
  the OS setting. A header button now cycles Auto -> Dark -> Light -> Auto; the explicit choice
  is stored in `localStorage` and applied before first paint (an inline script in `<head>`), so
  there's no flash of the wrong theme on load or on a reload. `sw.js` cache bumped (v9 -> v10).

## 2026-09-22 (late night)

- **Correction: missing macro readings were silently coerced to False, not left unknown.**
  `nan > x` evaluates to `False` in plain pandas, so `hy_stress`/`vix_calm`/`spx_bull`/
  `dxy_headwind`/`risk_on` read as a confident, wrong answer during any gap in the underlying FRED
  series - two real ones hit the 2023-06-01 backtest window: this FRED mirror's HY series only
  starts 2023-09-22, and `spx_bull`'s 200-day rolling mean has no warmup buffer when fetched from
  that same start. `hlg/macro.py` now uses pandas' nullable `"boolean"` dtype so a missing reading
  stays genuinely unknown; `hlg/stablecoin.py` gets the same fix for consistency (no trade in the
  current window was actually affected there). `_pass()` updated to recognise it. Regression tests
  that fail against the old code.
- README "Macro" corrected accordingly: `spx_bull`'s filter result moved the most (97%/PF 1.26 →
  99%/PF 1.48); `no_dxy_headwind` flipped from "noise" to "worse" (borderline). The "signal-day
  backdrop" breakdown is rebuilt with the two data gaps kept separate instead of silently folded
  into "calm"/"below 200d" - the 7 credit-unknown trades were the worst-performing group in the
  whole sample (PF 0.20), and "S&P below its 200d" shrinks from a reported 11 trades to a genuine
  4. The core direction (credit-stress signal days outperform) survives; a previously-published
  permutation p-value and "calm goes negative without its top 3 winners" claim do not reproduce
  and are removed rather than restated on data now known to be wrong. Nothing changes about the
  finding's status: still not wired in as a rule.

## 2026-09-22 (night)

- **Dollar index (DXY) vs crypto returns: tested alongside stablecoins** (`--dxy-study`). Unlike
  stablecoin supply, not uniform: 7d/30d correlation is near zero (BTC's 30d quintile table is
  flat), but 90d turns moderately negative for both BTC (−0.23) and the basket (−0.16) - the
  direction the "dollar strength is a crypto headwind" claim predicts. Doesn't help the live rule
  either way: `no_dxy_headwind`, re-run at the same bar as the stablecoin filters, sits at the 5th
  percentile of random subsetting with in-sample PF collapsing to 0.47. README "Stablecoins".
- Fixed a real caching bug found while building that study: `hlg/macro.py`'s FRED cache was keyed
  only by series id, not by the requested `start` date, so a narrower fetch cached earlier in a
  session was silently served back to a later, wider request within the 24h TTL - quietly halving
  this study's first run to ~1,150 observations instead of ~2,200. Also affects any other caller
  requesting a wider window than a previous run's cache; fixed, with a regression test.

## 2026-09-22 (evening)

- **Shorting: tested, not adopted.** `breakout_short`/`breakout_both` (already in `VARIANTS`, never
  reported) run and documented: the short side loses money outright (PF 0.88, in-sample PF 0.56)
  and drags the combined book's Sharpe from 0.98 to 0.28. README "Backtest".
- **Stablecoin supply growth vs forward returns: tested, and the opposite of the claim**
  (`hlg/stablecoin.py`, `--stbl-study`). Total USD-pegged stablecoin market cap from DefiLlama
  (free, no key, daily since 2017) correlates *positively* with forward BTC and basket returns at
  every horizon tested (7/30/90d, ~2,200 observations); gating the live entry rule on it fails the
  same adoption bar the entry study used, badly for the outflow variant (25th percentile of random
  subsetting). Not adopted, not shown as dashboard context. README "Stablecoins".

## 2026-09-22 (later)

- **Real-browser dashboard tests** (`tests/e2e`, Playwright/Chromium, 43 tests): decryption against
  a real `hlg.vault`-sealed envelope, the alert list's interactions and persistence, every SVG
  chart, three viewport widths, both colour schemes, no console errors across a full session.
  Caught a real bug: `renderLiqMap`'s chart width computed `clientWidth - 34` before the zero-width
  fallback could apply, so any page load with the Flow tab inactive built the liquidation chart
  with a negative SVG viewBox. Fixed with a floor at the three affected call sites. Excluded from
  the default `pytest -q`; runs via `.github/workflows/e2e.yml` on demand or when `pwa/` changes.

## 2026-09-22

- **Dashboard redesign with charts:** equity curve with loss-lock lines, risk gauges (loss limits,
  leverage, account ratio), a price ladder per position, a candle chart on expanded breakout alerts,
  a liquidation map per coin, and cumulative R for the signal journal. Hand-written SVG with hover
  tooltips; palette validated for colour-blind separation; dark and light themes.
- History keeps every run for 3 days, hourly to 90 days, daily after, and records portfolio value.

## 2026-09-21

- **Liquidation levels from Hyperliquid positions** (`hlg/liqmap.py`): real liquidation prices of
  ~560 large and active accounts, clustered per coin; Flow-tab card, one context line on breakout
  alerts, recorded in the signal journal. Refreshed every 4h after alerts are pushed.
- **Entry study** (`--entry-study`): fast failure exit, confirmation entry, 0.382 / 0.5 fib retrace
  entries, and two breakout-bar quality filters, on 1d and 4h. None passed the pre-set bar. Retrace
  entries missed most of the biggest winners (fib 0.382: 21 of 24 on 1d). README "Entries".
- **Signal journal:** every breakout signal is followed under the strategy's own rules, taken or not,
  with its result in R, early-failure flag and close location. Dashboard card in the Technical tab.

## 2026-09-19 (later)

- **Readable alert list:** grouped (positions, entries, heads-up, info, recently ended), one line
  per alert with age and remaining entry window, tap to expand, clear per device. Signals that end
  are shown for 24h as missed / failed / expired instead of vanishing.
- **Focused push:** funding notes and breakouts on coins already held are dashboard-only.
  Guardrail breaches, breakout entries and momentum are still pushed.
- **Scanner request budget:** bar statistics are cached until the bar closes, live prices come from
  one `allMids` call, and momentum uses stored price snapshots. A 15-minute run now makes no
  candle requests unless a bar has closed (was ~28-35 per run).
- **Liquidity universe** (`scanner.universe`) built and backtested, then **left off**: scanning the
  top 30 perps by volume tested worse than the curated list on both 1d (PF 1.37 vs 1.75) and 4h
  (1.08 vs 1.36), and the coins it adds lost money. README "Universe". `mode: auto` and
  `allowed_coins: auto` are available for anyone who wants them.
- Dashboard: near-breakout watch list, 24h volume column.
- Backtest: `--universe-study` (point-in-time universe with delisted coins, most-liquid-first fills).
- The Kronos stop-odds idea is parked in `docs/proposals/`.

## 2026-09-19

- **Web Push is on**: alerts reach devices with the dashboard closed. Added a manual
  "test notification" option to the monitor workflow. Push errors log the HTTP status only.
- **Privacy on a public repo**: every published file is encrypted (AES-256-GCM, PBKDF2 600k) and
  decrypted in the browser with a passphrase gate. Public logs are redacted by allowlist. The
  wallet address moved to a secret. The alert-issue channel was removed.
- **Unified-account math**: the equity base is USDC collateral, and account ratio and leverage
  match Hyperliquid's summary. Spot counts toward per-coin exposure. Stops that lock in profit are
  no longer counted as risk.
- A loss-limit lock computed on a different equity base is voided.
- Config: the account secret is whitespace-stripped; the local overlay reads UTF-8 with or without
  a BOM, and UTF-16.
- Documentation set in `docs/`.

## 2026-09-18

- **Advisory only**: all order execution removed. Test suite and CI added.
- Scanner: breakout on 1d and 4h independently; momentum heads-up for fast moves.
- Dashboard: Technical / Macro / Flow tabs; TradFi from HL `xyz` perps with a dead-listing filter;
  recent liquidations from OKX with a browser fallback; staleness indicator.
- Macro filters backtested against random baselines: none adopted; the backdrop is context only
  and off in CI (FRED is unreachable from runners).
- Cadence: an external scheduler replaces GitHub's unreliable cron; hourly cron kept as a backstop.

## 2026-09-13 – 14

- First version: guardrail monitor and swing scanner.
- Serverless mode: GitHub Actions + Pages PWA.
- Setup miner, backtester (breakout adopted: PF 1.67 vs pullback 0.85), regime analysis, TradFi
  watch list, Day/Week PnL from HL portfolio series.
