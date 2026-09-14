# hl-guardrails

Guard-rail enforcer + swing-setup scanner for a Hyperliquid perp account. Built from the analysis of
`0x68b1…01c9`: the losses came from unstopped, averaged-down positions held >7 days and from sub-24h
taker scalping; the profitable pocket was 3–7 day swings at moderate size. This bot makes the first
impossible and alerts on the second.

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

Enforce mode (actually places stops, trims adds, flattens on limits):

```bash
export HL_PRIVATE_KEY=0x...      # main wallet key OR an API/agent wallet authorised at app.hyperliquid.xyz/API
sed -i 's/^mode: alert/mode: enforce/' config.yaml
python -m hlg.guardrails
```

Use an **API wallet** (agent key) rather than the main key: it can trade but cannot withdraw.

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
Only alert mode runs in Actions (no keys in CI); run enforce mode on a machine you control.
Cron granularity is ~15 min (GitHub may delay further) — this is a safety net, not a real-time stop.

## Rules enforced (config.yaml → `rules`)

| Rule | alert | enforce |
|---|---|---|
| Position without a reduce-only stop | warn, prints required stop price | places stop so loss = `risk_per_trade_pct` of equity; closes if already past max risk |
| Existing stop risks > 1.25× allowed | warn | warn |
| Added to a position while underwater | warn | market-closes the added amount |
| Position older than `max_hold_days` | warn | closes |
| Leverage > `max_leverage` | warn | lowers leverage |
| > `max_positions`, > `max_same_direction`, gross > `max_gross_exposure_x` | warn | warn |
| Coin not in `allowed_coins` | warn | warn |
| Daily / weekly loss limit (transfer-adjusted equity) | warn | cancels all, flattens, locks; any position opened while locked is closed |

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

- Alert mode is fully read-only (public `/info` endpoints only).
- Enforce actions are market orders with 3% slippage cap on stops; on illiquid coins widen/adjust.
- Loss limits use perp `accountValue`; spot balances are ignored by design (keep them out of reach).
- Polling is 60 s: a fast move can exceed the risk cap before the stop is placed. Place stops yourself at entry; the bot is the safety net.

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
