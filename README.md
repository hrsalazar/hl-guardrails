# hl-guardrails

Guard-rail monitor + swing-setup scanner for a Hyperliquid perp account. **Advisory only** — it
watches a configurable account (a public address, no key needed) and warns you; it never places,
modifies, or cancels an order. Built from the analysis of `0x68b1…01c9`: the losses came from
unstopped, averaged-down positions held >7 days and from sub-24h taker scalping; the profitable
pocket was 3–7 day swings at moderate size. This bot flags the first and alerts on the second —
acting on either is always up to you.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml   # or edit config.yaml directly
```

## Run

```bash
# read-only: warns on every rule violation (console + optional Telegram)
python -m hlg.guardrails

# setup / funding scanner (every 30 min)
python -m hlg.scanner
```

Both only ever read public account data and emit warnings — neither one ever places, modifies,
or cancels an order, so there is no private key to configure.

Telegram: create a bot with @BotFather, get your chat id from @userinfobot, then
`export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...` and set `telegram.enabled: true`.

Run both as services with `./run.sh` (tmux) or the systemd units in `deploy/`.

## Serverless mode: GitHub Actions + PWA (no Telegram, no server)

`.github/workflows/monitor.yml` runs `python -m hlg.report` every 15 minutes, publishes
`site/alerts.json` + the PWA in `pwa/` to GitHub Pages, and notifies you three ways:

1. **PWA** (`https://<user>.github.io/<repo>/`) — dashboard; "Add to Home Screen" on phone. Click
   *Enable notifications* to get a browser notification for new alerts whenever the page is open.
2. **Web Push** (alerts with the app closed): run `python -m hlg.vapid` once; add the private key as
   Actions secret `VAPID_PRIVATE_KEY` (+ `VAPID_SUBJECT=mailto:you@x.com`) and the public key as repo
   **variable** `VAPID_PUBLIC_KEY`. Open the PWA, click *Enable push*, copy the subscription JSON into
   secret `PUSH_SUBSCRIPTIONS` (a JSON list for several devices). iOS requires the PWA installed to the
   Home Screen.
3. **GitHub Issue** — every new alert is appended to an open issue labelled `alerts`; subscribe to the
   issue and the GitHub mobile app pushes it to you. Zero setup.

Enable it: repo *Settings → Pages → Source: GitHub Actions*, then run the workflow once manually.
No keys are needed in CI, since nothing this tool does ever requires one.
Cron granularity is ~15 min (GitHub may delay further) — this is a monitoring cadence, not a
real-time alert.

## Rules watched (config.yaml → `rules`)

Every rule below only ever produces a warning (console + optional Telegram) — nothing is ever
enforced automatically. Acting on it is up to you.

| Rule | What triggers the warning |
|---|---|
| Position without a reduce-only stop | prints the stop price that would cap the loss at `risk_per_trade_pct` of equity |
| Existing stop risks > 1.25× allowed | warn |
| Added to a position while underwater | warn — averaging down |
| Position older than `max_hold_days` | warn — time stop |
| Leverage > `max_leverage` | warn |
| > `max_positions`, > `max_same_direction`, gross > `max_gross_exposure_x` | warn |
| Coin not in `allowed_coins` | warn |
| Daily / weekly loss limit (transfer-adjusted equity) | warn that you should be flat; the advice stays "locked" until the next period, and re-fires if a position is still open |

State (day/week start equity, lock, last position snapshot) lives in `state.json`.

## Scanner (config.yaml → `scanner`)

For each coin: daily EMA20/EMA50 trend, 4h RSI14 pullback, proximity to the 20-day extreme, stop from
4h swing / 1.5×ATR, target at the opposite 20-day extreme, RR filter. Alerts include the exact size that
keeps the stop at `risk_per_trade_pct` of equity, flag positive funding on shorts, and warn if you are
already in the coin (no adds). Separately alerts funding-carry opportunities above
`funding_carry_alert_apr`.

The setup rules are a codification of what worked historically, **not a backtested edge**. Run the
scanner in alert mode for 6–8 weeks and log the outcomes before sizing up.

## Caveats

- Fully read-only (public `/info` endpoints only) — it never places, modifies, or cancels an order.
- Loss limits use perp `accountValue`; spot balances are ignored by design (keep them out of reach).
- Polling is 60 s: a fast move can breach the risk cap before you see the warning. Place your own stops at entry; this is a second pair of eyes, not a safety net that acts for you.

## Setup miner (`hlg.miner`)

Finds *discretionary winners with capital similar to yours* on the Hyperliquid leaderboard and
reports which setup fingerprints are profitable across many of them.

```bash
python -m hlg.miner              # ~10-20 min first run (fills are cached in miner_cache/)
python -m hlg.miner --wallets 40 # quicker sample
```

Pipeline: leaderboard (45k wallets) → equity $500–50k with positive month & all-time PnL →
fetch 180d fills → reconstruct round trips → drop market makers (maker share > 60%), HFT (> 15
trades/day), scalpers (median hold < 2h), TWAP bots, hedged farmers (overlapping long/short) and
wallets not actually profitable on closed trades (PF < 1.2) → attach daily-candle context at entry
(EMA20/50 trend, RSI14, distance to 20d high/low, 5d momentum) → aggregate by holding period, size,
side × trend, entry level, adds behaviour, coin and combined fingerprints.

Read `w_pos` (share of wallets for which a setup is net positive) before `pf`: a setup that only
one wallet made money on is noise. Output in `miner_out/report.md`, `wallets.csv`, `trades.csv`.
Caveats: survivorship bias (winners only), leaderboard PnL includes unrealized, 6-month window,
daily-candle context only. Hypothesis generation, not a backtest.

## Backtest (`hlg.backtest`)

```bash
python -m hlg.backtest            # all variants, 15 coins, 2023-06 -> now, real funding, fees
```

Daily-bar portfolio backtest (signal at close, fill next open, stop on low/high, 1.5% risk, max 3
positions, 3x notional cap, maker entry / taker exit, real hourly funding). Results 2023-06 -> 2026-09:

| variant | trades | PF | total | max DD |
|---|---|---|---|---|
| scanner pullback rule (legacy, both sides, 7d) | 319 | 0.85 | -29% | -38% |
| long pullback, 21d + 3 ATR trail | 289 | 1.07 | +22% | -50% |
| long pullback, 7d | 410 | 0.88 | -29% | -46% |
| **long breakout of 20d high in uptrend, 2 ATR stop, 3 ATR trail, 21d** | 126 | **1.67** | **+67%** | **-18%** |
| breakout sensitivity (45d / 2 ATR trail / 1.5 ATR stop / any trend) | 112-176 | 1.45-1.86 | +43..+116% | -19..-23% |
| short pullback (control) | 310 | 0.84 | -28% | -62% |

The breakout rule was positive in every year (2023-2026) and on 11 of 15 coins, with all
sensitivity variants positive, so it is now the default `scanner.strategy: breakout`. It is a
36% win-rate / 3:1 payoff system: most trades stop out small, the profit comes from the ~20% of
trades that run to the 21-day time stop. The legacy pullback rule stays available as
`scanner.strategy: pullback` but tested negative. `rules.max_hold_days` was raised to 21 to match.

`scanner.watch_coins` (default: HL tradfi perps xyz:CL, xyz:GOLD, xyz:XYZ100) are scanned and shown on the dashboard
for information only - no alerts. The same breakout rule tested PF ~1.2 on them, but with only ~9 months of
history and market-hours gaps; revisit with `hlg.backtest --coins xyz:...` once there is a year+ of data.

### Timeframe

`scanner.timeframe` selects the bar the breakout rule runs on: `1d` (default) or `4h`. Backtest on the same
period (2024-06 -> now, 15 coins, `python -m hlg.backtest --interval 4h --native`):

| timeframe | trades | PF | total | max DD | avg hold |
|---|---:|---:|---:|---:|---:|
| 1d | 73 | 2.14 | +60% | -13% | 12 d |
| 4h (20/50-bar windows) | 388 | 1.33 | +141% | -40% | 2.6 d |
| 4h (day-equivalent windows) | 252 | 1.25 | +52% | -30% | 1.4 d |

4h trades ~5x more often and compounds faster in the simulation, but with a third of the edge per trade (PF 1.3 vs 2.1),
three times the drawdown, and no slippage modelled - on a 388-trade sample that matters. Daily stays the default; use
4h only if you accept the drawdown profile. Hyperliquid serves ~5000 candles per interval, so the 4h test covers
~2.3 years vs 3.3 for daily.

### Regime analysis (`python -m hlg.regime`)

Tags every day with a BTC regime (bull/bear vs EMA200, range/trending by 20d span vs ATR, breadth, drawdown) and
attributes each backtest trade to the regime on its signal day, for breakout vs the pullback variants. Also runs an
out-of-sample regime-switch test (pick the best variant per regime on the first half, apply on the second half).
Output: `backtest_out/regime_report.md`, `backtest_out/regimes.csv`.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests use fakes for the Hyperliquid API (`tests/conftest.py::FakeInfo`) and never touch the
network. They cover the guardrail rules in `hlg/guardrails.py` (every rule, including the
daily/weekly loss-limit lock), the breakout/pullback setup detection in `hlg/scanner.py`, and
`hlg/common.py`'s `State` and `Notifier`. CI (`.github/workflows/test.yml`) runs the same suite
on every push and pull request.
