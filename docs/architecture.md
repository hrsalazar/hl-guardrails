# Architecture

hl-guardrails watches one Hyperliquid account and one list of coins, and tells its owner two
things: when the account breaks a risk rule, and when a tested setup appears. It is **advisory
only**. It reads public data, never holds a key, and never places, modifies or cancels an order.

This document covers how the system is put together. For the security model see
[security.md](security.md); for running it, [operations.md](operations.md).

## At a glance

```mermaid
flowchart LR
  subgraph Trigger
    EC[External scheduler<br/>every 15 min] -->|workflow_dispatch API| GA
    CR[GitHub cron<br/>hourly backstop] --> GA
  end
  subgraph GA[GitHub Actions: monitor.yml]
    R[hlg.report] --> G[guardrails.run_once]
    R --> S[scanner.run_once]
    R --> M[market / tradfi]
    R --> L[liquidations]
    R --> V[vault.seal]
  end
  HL[(Hyperliquid /info<br/>public API)] --> G & S & M
  OKX[(OKX public API)] --> L
  V -->|ciphertext only| P[(GitHub Pages)]
  R -->|Web Push, E2E encrypted| PS[Apple / Google push service] --> D[Owner's devices]
  P --> PWA[PWA dashboard<br/>decrypts in browser] --> D
```

One run takes about 15 seconds: fetch, evaluate, encrypt, publish, push. Nothing runs between runs
and there is no server or database. State lives in the published, encrypted `state.json` and is
read back at the start of the next run.

## Components

| Module | Role | Network |
|---|---|---|
| `hlg/common.py` | config loading (+ local overlay, env account), logging with public-log redaction, `Notifier` (alert collection and cooldown), `State` | – |
| `hlg/account.py` | the account model: detects unified vs classic, picks the equity base, computes HL's account ratio and leverage, and nets spot into perp exposure | HL |
| `hlg/guardrails.py` | the risk rules: stops, sizing, leverage, exposure, hold time, averaging down, daily and weekly loss locks | HL |
| `hlg/scanner.py` | the breakout setup on 1d and 4h, the momentum heads-up, funding carry, and the asset-context fetch (`ctx_map`) | HL |
| `hlg/market.py` | pure transforms: volume, OI, premium and spread rows; the TradFi liquidity filter | – |
| `hlg/liquidations.py` | recent liquidation events from OKX, time-boxed and fail-soft | OKX |
| `hlg/macro.py` | FRED macro series for research and backtests (off in CI, see below) | FRED |
| `hlg/vault.py` | AES-256-GCM encryption of everything published | – |
| `hlg/report.py` | the one-shot CI entry point that ties the modules together | via the modules above |
| `hlg/vapid.py` | one-off VAPID key generator for Web Push | – |
| `hlg/backtest.py`, `hlg/regime.py`, `hlg/miner.py` | research tools, run locally; not part of the monitor | HL, FRED |
| `pwa/` | static dashboard: `index.html` (UI, decryption, tabs), `sw.js` (offline shell, push display), manifest, icons | Pages, OKX (fallback) |

`guardrails` and `scanner` also run as long-lived local processes (`python -m hlg.guardrails`,
`run.sh`, the systemd units in `deploy/`), sending to the console or Telegram instead of Pages.

## A monitor run, step by step

`python -m hlg.report`, from `.github/workflows/monitor.yml`:

1. **Load config.** `config.yaml` is overlaid with `config.local.yaml` if present. The account comes
   from `HLG_ACCOUNT`, whitespace-stripped, and `require_account` stops the run if it isn't a
   42-character `0x` address.
2. **Read the previous run.** Fetch `state.json`, `alerts.json` and `history.json` from Pages.
   Reuse the salt of any envelope found, so the key stays the same across runs, then decrypt.
   Plaintext (from before encryption) and the locked stub are handled too; see `load_prev`.
3. **Guardrails.** Evaluate every rule against live positions, open orders and the account model.
   Day and week baselines and the loss lock persist in `state`.
4. **Scanner.** Breakout signals per timeframe, momentum, and funding carry.
5. **Context.** One extra asset-context call gives market and TradFi rows. Liquidations are
   fetched with a hard budget.
6. **Diff.** An alert is *new* if its key wasn't in the previous `alerts.json`.
7. **Publish.** Write `alerts.json`, `history.json`, then `state.json` last, each sealed by the
   vault. With no passphrase in CI, the locked stub is written instead.
8. **Push.** Web Push the new alerts to every subscription, plus a test message if the manual
   `test_push` input was set. Push is skipped when the previous state couldn't be read, since every
   alert would look new.
9. The workflow then copies `pwa/` into `site/`, stamps `config.js` with the public VAPID key, and
   deploys Pages.

## Published data

Every file on Pages is an envelope:

```json
{"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": 600000,
 "salt": "<b64 16B>", "iv": "<b64 12B>", "ct": "<b64 ciphertext+tag>"}
```

or, when encryption isn't configured in CI, `{"locked": "..."}`.

Decrypted `alerts.json`, the dashboard payload:

| key | content |
|---|---|
| `generated` | ISO timestamp (UTC) |
| `account` | the watched address |
| `equity` | the sizing base: USDC collateral (unified) or perp account value (classic) |
| `acct` | the account model: mode, base, base label, ratio, leverage, maintenance margin, notional, spot holdings |
| `day_pnl`, `week_pnl`, `day_start_equity`, `week_start_equity`, `locked_until` | loss-limit tracking |
| `positions` | coin, size, entry, value, uPnL, liquidation price, leverage, margin type |
| `alerts` | `{kind: guardrail/scanner, key, text, new}` |
| `scan` | scanner rows, each tagged with its timeframe `tf` |
| `market`, `tradfi` | volume, OI, 24h change, premium and spread rows; `tradfi.dropped` counts hidden dead listings |
| `liquidations` | `{src: "baked", rows}` or null (the dashboard then fetches OKX itself, labelled `live`) |
| `macro` | FRED backdrop, or null |
| `rules` | the rule thresholds in force |

`history.json` is a list, capped at 2000 entries, of `{t, equity, n_alerts, new}`.

## Data sources

| Source | Used for | Notes |
|---|---|---|
| Hyperliquid `/info` | positions, orders, spot, portfolio PnL series, candles, asset contexts, `userAbstraction` | public, no key; ~5000-candle cap per interval |
| Hyperliquid `xyz` dex | TradFi perps (indices, metals, energy, FX, equities) | many listings are dead; liquidity-filtered |
| OKX public API | recent liquidation events, BTC/ETH/SOL | HL publishes no liquidation data; CORS-open, so the browser can fall back |
| FRED | macro series for research | unreachable from Actions runners, so `macro_context: false` |

## Design decisions

The reasoning behind the non-obvious choices, kept here so they aren't undone by accident.

| Decision | Why |
|---|---|
| Advisory only; no keys | The losses this was built to prevent came from human behaviour, not execution speed. Without keys, a compromise of the repo, CI or dashboard cannot move funds. |
| Serverless: Actions + Pages | Free, nothing to patch, no server to secure. The costs: no server-side auth (solved with encryption) and no reliable short cron (solved with an external trigger). |
| External scheduler, not GitHub cron | Measured: a `*/15` cron gave a median gap of 235 minutes. The dashboard shows staleness instead of trusting the cadence. |
| Encrypt instead of a login | Pages is static, so a login page would be decoration. Ciphertext is safe to serve publicly. |
| Allowlist log redaction | Actions logs are public on a public repo. Only errors and lines marked safe print, so a new log line can't leak by default. |
| Unified-account equity base = USDC | On a unified account, perp `accountValue` was about 1/13 of the real collateral, so risk was sized 13× too small. The formulas reproduce HL's own Unified Account Summary. |
| Breakout, not pullback | Backtested PF 1.67 vs 0.85 over 2023–2026; see README "Backtest". |
| Macro and flow are context only | Every macro filter tested worse than, or no better than, dropping the same trades at random. |
| Fail closed everywhere | A missing secret hides data instead of publishing it; a failed source degrades to null rather than failing the run. |

## Tests and CI

`pytest -q` runs 126 tests in about 2 seconds. An autouse fixture blocks all network access, so
every test uses fakes (`tests/conftest.py::FakeInfo`). Coverage includes every guardrail rule,
the account model (unified and classic), scanner setups, market transforms, the vault (tamper,
wrong key, cross-file substitution, fresh IV), fail-closed publishing, log redaction and config
encodings. `.github/workflows/test.yml` runs the suite on every push and PR.
