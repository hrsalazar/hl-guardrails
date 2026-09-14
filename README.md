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
