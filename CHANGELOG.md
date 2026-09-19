# Changelog

Dates are UTC. For the details and reasoning, see the commit messages.

## 2026-09-19 (later)

- **Readable alert list:** grouped (positions, entries, heads-up, info, recently ended), one line
  per alert with age and remaining entry window, tap to expand, clear per device. Signals that end
  are shown for 24h as missed / failed / expired instead of vanishing.
- **Focused push:** funding notes and breakouts on coins already held are dashboard-only.
  Guardrail breaches, breakout entries and momentum are still pushed.
- **Scanner request budget:** bar statistics are cached until the bar closes, live prices come from
  one `allMids` call, and momentum uses stored price snapshots. A 15-minute run now makes no
  candle requests unless a bar has closed (was ~28-35 per run).
- **Liquidity universe** (`scanner.universe`, off by default): top N perps by 30-day median volume,
  rebuilt daily; `allowed_coins: auto`. Dashboard: near-breakout list, volume and rank columns.
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
