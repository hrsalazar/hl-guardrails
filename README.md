# hl-guardrails

Guard-rail monitor + swing-setup scanner for a Hyperliquid perp account. **Advisory only** — it
watches a configurable account (a public address, no key needed) and warns you; it never places,
modifies, or cancels an order. Built from the analysis of a real account's history: the losses came from
unstopped, averaged-down positions held >7 days and from sub-24h taker scalping; the profitable
pocket was 3–7 day swings at moderate size. This bot flags the first and alerts on the second —
acting on either is always up to you.

**Documentation:** [setup & operations](docs/operations.md) · [architecture](docs/architecture.md) ·
[security model](docs/security.md) · [product notes](docs/product.md) · [changelog](CHANGELOG.md).
This README holds the research: rules, scanner, backtests and what was tested and rejected.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml   # or edit config.yaml directly
echo "account: '0xYOUR_ADDRESS'" > config.local.yaml   # gitignored: the address stays off GitHub
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
   Actions secret `VAPID_PRIVATE_KEY` (+ `VAPID_SUBJECT`, a `mailto:` or bare `https://host` -
   pywebpush rejects a URL with a path) and the public key as repo **variable** `VAPID_PUBLIC_KEY`.
   Open the PWA, click *Enable notifications* then *Enable push*, and save the JSON it shows as secret
   `PUSH_SUBSCRIPTIONS` (one list, one entry per device). iOS requires the PWA installed to the Home
   Screen and opened from there. A push goes out only for alerts not in the previous run, and its text
   shows on the lock screen. Failures log the HTTP status only (the endpoint is a capability URL); a
   404/410 means the device's subscription expired - re-subscribe and update the secret. To check a
   device, run the workflow manually with **"Also send a test notification"** ticked.

   **What is pushed:** guardrail breaches, new breakout entries and momentum spikes. **Dashboard
   only:** funding notes (never backtested, and not part of the strategy) and breakouts on a coin you
   already hold (not an entry: no adds).
Alerts used to be appended to a GitHub Issue as well. On a public repo that made every position
readable by anyone, logged in or not, so that channel was removed.

Enable it: repo *Settings → Pages → Source: GitHub Actions*, add the secrets below, then run the
workflow once manually. No trading keys are ever needed — nothing this tool does can place an order.

### Keeping it private on a public repo

The repo can stay public (free Pages, free Actions) while the numbers stay yours. That needs more
than a login screen: Pages is static hosting with no server to check a password, so a JavaScript
prompt would stop nobody — the data files are one URL away. Every place the account used to leak
had to be closed:

| Where it leaked | What happens now |
|---|---|
| `alerts.json`, `state.json`, `history.json` on Pages | encrypted in CI before publishing; only ciphertext is ever served |
| an issue collecting every alert | channel removed (Web Push stays: it is end-to-end encrypted to your device) |
| the Actions log, public on a public repo | `HLG_REDACT` drops everything but errors and lines explicitly marked safe |
| the wallet address in `config.yaml` | an Actions secret in CI, `config.local.yaml` (gitignored) locally |

**Encryption** (`hlg/vault.py`, and its WebCrypto twin in `pwa/index.html`): AES-256-GCM with a key
from PBKDF2-SHA256 at 600,000 iterations. GCM is authenticated, so a wrong passphrase or any
tampering fails outright instead of decrypting to garbage; the file name is bound in as associated
data so one file can't be served as another; a fresh random IV is used every time. The salt is
random and carried forward between runs, so the key stays stable and a device can unlock once. The
dashboard keeps the derived key in IndexedDB as a **non-extractable** `CryptoKey` — usable to
decrypt, but not readable, not even by the page's own JavaScript. *Lock* forgets it.

**It fails closed.** If `DASHBOARD_PASSPHRASE` is missing in CI, the job publishes a locked stub, not
the data, and sends no push (every alert would otherwise look new each run). The redaction is an
allowlist for the same reason: a log line added later is hidden unless someone opts it in.

Secrets (*Settings → Secrets and variables → Actions*):

| Secret | Value |
|---|---|
| `DASHBOARD_PASSPHRASE` | a long passphrase only you know — it is the only thing protecting the data; a password manager is the right place for it |
| `HLG_ACCOUNT` | the wallet address being monitored |

**What this cannot hide.** Hyperliquid is a public chain: anyone who knows the address can see its
positions and PnL on Hyperliquid itself, whatever this dashboard does. Keeping the address out of
the repo stops it being handed out here, but anything ever committed stays in git history. The only
complete separation is a wallet that was never published anywhere.

### Keeping the dashboard current

**GitHub will not run a 15-minute cron.** Measured on this repo over 80 hours: 22 scheduled runs
where a `*/15` cron implies 321, a median gap of **235 minutes**, and the shortest gap ever
observed was 121 minutes — it never once fired at 15. Scheduled workflows are explicitly
best-effort and short intervals get deprioritised. Nothing was misconfigured: the workflow was
`active`, no runs were cancelled, and the cron syntax was valid.

That is survivable for the scanner (daily/4h breakouts held for weeks) but not for the guardrails,
which are the safety layer — a stop-coverage or loss-limit check that runs every four hours is
mostly decorative.

So `monitor.yml` keeps an hourly cron as a backstop only, and real cadence comes from an external
scheduler calling the dispatch API:

```
POST https://api.github.com/repos/<you>/hl-guardrails/actions/workflows/monitor.yml/dispatches
Authorization: Bearer <token>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28

{"ref":"main"}
```

Set it up with any free scheduler (cron-job.org, EasyCron, an always-on box's own crontab):

1. GitHub → *Settings → Developer settings → Fine-grained tokens* → new token, **Repository access:
   only `hl-guardrails`**, **Repository permissions → Actions: Read and write**. Nothing else —
   in particular not `contents`, so a leaked token can start this read-only workflow and nothing more.
2. Point the scheduler at the URL above every 15 minutes with that token as a bearer header.
3. Confirm it works: `gh run list --workflow=monitor.yml` should start showing `workflow_dispatch`
   runs on a regular cadence.

The dashboard does not take the cadence on trust either way — the timestamp in the header turns
amber past 20 minutes and red past 40, because a guardrail you believe is live but is four hours
old is worse than no guardrail.

### Dashboard sections

Equity/PnL and the alert list sit at the top and are never behind a tab — they are the act-now
layer. Everything else is grouped into three tabs (the last one you used is remembered):

| Tab | Contents | Source |
|---|---|---|
| **Technical** | open positions; the breakout scanner, grouped by timeframe | Hyperliquid candles |
| **Macro** | TradFi instruments — SP500, gold, silver, oil, copper, natgas, EUR/JPY, plus MSTR/COIN as crypto-equity proxies | HL's `xyz` TradFi perps (`scanner.tradfi_coins`) |
| **Flow** | maintenance-margin buffer, 24h volume / open interest / turnover / mark-vs-oracle premium, recent liquidations | HL asset contexts + OKX |

Three things worth knowing about the data, because each one is a trap:

- **Volume and open interest were free all along.** `metaAndAssetCtxs` is fetched on every scan and
  only `funding` was ever read; `openInterest`, `dayNtlVlm`, `prevDayPx`, `premium` and `impactPxs`
  arrive in the same response. `hlg/market.py` turns them into rows — no extra network cost.
- **Dead TradFi listings are filtered out.** Hyperliquid lists instruments nobody trades — `xyz:DXY`,
  `xyz:VIX`, `xyz:CORN`, `xyz:WHEAT`, `xyz:URANIUM`, `xyz:NIFTY` all sit at zero open interest and
  zero volume with a mark that never moves. DXY and VIX are exactly what a macro panel most wants,
  which is how you end up publishing a frozen number as live data. Anything with under $1M of 24h
  volume, under $250k of open interest, or an unchanged mark is dropped, and the count of what was
  hidden is shown so an empty table is distinguishable from a broken one.
- **Liquidations come from OKX, because Hyperliquid publishes none** — there is no liquidations
  endpoint (`liquidations`, `recentLiquidations`, `openInterestHistory` all 422) and `recentTrades`
  returns ten trades with no liquidation flag. These are therefore *recent events on OKX's book*,
  not a 24h total and not Hyperliquid's own. OKX sends permissive CORS headers, so if the CI fetch
  comes back empty the dashboard asks OKX directly from your browser; the card says which it used.

**The alert list** is grouped by what you would do about it: *Your positions* (guardrail breaches),
*Entries* (live breakouts, with how long the entry window has left), *Heads-up* (momentum), *Info*
(funding, already-held breakouts; collapsed) and *Recently ended*. Each alert is one line; tap to
expand the full text. A signal that stops being live is not just dropped: it moves to *Recently
ended* for 24 hours as **missed** (price ran more than 1 ATR past the signal close), **failed** (closed
back below the breakout level) or **expired** (the entry window passed). ✕ and *Clear all* hide
alerts on that device only; a new signal on the same coin has a new key and shows again.
Guardrail breaches can be collapsed but not cleared: they are live risk on open positions.

**Liquidation risk is account-level.** Per-position `liquidationPx` is `null` for cross-margined
positions — the normal case — because Hyperliquid assesses liquidation on the whole account. The
Flow tab shows the collateral, the maintenance margin and HL's own ratio instead. It deliberately
does not show a derived "move to liquidation": HL does not publish the unified liquidation
threshold, and a precise-looking figure built on a guessed one is worse than none.

## Equity base: unified vs classic accounts

Every rule and every suggested size scales off one number, and on Hyperliquid what that number
should be depends on the account model. `userAbstraction` says which:

| mode | what margins the perps | base used here |
|---|---|---|
| `disabled` / `default` (classic) | the perp account itself | perp `accountValue` |
| `unifiedAccount` | spot balances — USDC is the collateral | **spot USDC** |

On a unified account the perp `accountValue` is only the slice of USDC the perps are drawing at
that moment, and it moves as collateral is allocated internally with no ledger transfer to show
for it. On the account this was built against the perp value was roughly a thirteenth of the USDC
collateral — so the tool was sizing risk about 13x too small, reporting a sub-1x book as several
times "gross exposure", and showing an equity drop that no transfer explained. It also cannot be
switched blindly: of 40 leaderboard accounts sampled, 25 were classic, including ones with large
perp equity and no spot USDC at all.

The unified figures are not an approximation. They reproduce HL's *Unified Account Summary* to
the displayed precision:

- **Unified account ratio** = perp maintenance margin ÷ USDC
- **Unified account leverage** = perp notional ÷ USDC — and `max_gross_exposure_x` is checked
  against exactly this

What else changes on a unified account:

- **Loss limits** use HL's whole-account `day`/`week` PnL series, as a share of the whole
  portfolio. Spot is collateral there, so a bad day in the spot book is a bad day. Classic accounts
  keep the perp-only series, where spot really is a separate wallet.
- **Spot tokens are exposure, not capacity.** They never raise the sizing base — counting a HYPE bag
  as room for more HYPE risk would double-count it. `max_coin_exposure_x` instead nets spot into
  the same coin's perp position, so a perp long stacked on a spot bag is flagged as the one bet it
  is, and a perp short against it reads as a hedge. Spot is valued at the liquid **perp** mid, never
  at its own book: illiquid spot marks are garbage (valuing every token off its own pair once
  put a small memecoin balance in the tens of millions).
- If `userAbstraction` fails, the account is treated as classic — on a unified account that sizes
  off the small perp value, i.e. too conservatively rather than too aggressively.

## Rules watched (config.yaml → `rules`)

Every rule below only ever produces a warning (console + optional Telegram) — nothing is ever
enforced automatically. Acting on it is up to you.

| Rule | What triggers the warning |
|---|---|
| Position without a reduce-only stop | prints the stop price that would cap the loss at `risk_per_trade_pct` of the equity base |
| Existing stop risks > 1.25× allowed | warn |
| Added to a position while underwater | warn — averaging down |
| Position older than `max_hold_days` | warn — time stop |
| Leverage > `max_leverage` | warn |
| > `max_positions`, > `max_same_direction`, account leverage > `max_gross_exposure_x` | warn |
| Net perp + spot in one coin > `max_coin_exposure_x` × base (unified only) | warn — one concentrated bet across spot and perp |
| Coin not in `allowed_coins` | warn |
| Daily / weekly loss limit (whole account when unified, perps when classic) | warn that you should be flat; the advice stays "locked" until the next period, and re-fires if a position is still open |

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
- On a classic account, loss limits use the perp account only and spot is ignored (it is a separate
  wallet). On a unified account spot *is* the collateral, so it counts — see "Equity base" above.
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

`scanner.timeframes` is a list of bars the breakout rule runs on independently - each timeframe gets its own
signals, stops, sizing, and alert cooldown (`24h` for `1d` signals, `4h` for anything faster), so a coin can
show a `1d` row and a `4h` row on the dashboard at the same time. Default is `[1d, 4h]`. Backtest on the same
period (2024-06 -> now, 15 coins, `python -m hlg.backtest --interval 4h --native`):

| timeframe | trades | PF | total | max DD | avg hold |
|---|---:|---:|---:|---:|---:|
| 1d | 73 | 2.14 | +60% | -13% | 12 d |
| 4h (20/50-bar windows) | 388 | 1.33 | +141% | -40% | 2.6 d |
| 4h (day-equivalent windows) | 252 | 1.25 | +52% | -30% | 1.4 d |

4h trades ~5x more often and compounds faster in the simulation, but with a third of the edge per trade (PF 1.3 vs 2.1),
three times the drawdown, and no slippage modelled - on a 388-trade sample that matters. 1d alone will miss moves that
resolve within a day (4h catches most of those); running both means slower, better-tested 1d signals and faster,
weaker-edge 4h signals both surface, each labelled with its own timeframe so you can weight them differently. Hyperliquid
serves ~5000 candles per interval, so the 4h test covers ~2.3 years vs 3.3 for daily. `1d` alone remains the safer choice
if you'd rather not carry 4h's extra drawdown.

We also tested `1h` (`python -m hlg.backtest --interval 1h --native`) to see if it would catch single-hour spikes 4h
still misses: PF 1.16, -44% max DD, and **every one of 426 trades exited via stop** (avg hold 14h) on only ~7 months of
history (Hyperliquid caps `candleSnapshot` at ~5000 bars, so 1h has much less runway than 4h or 1d). That's too thin
and too short a sample to trade on, so `1h` is not offered as a `scanner.timeframes` option - see Momentum below for
how fast moves are surfaced instead.

### Universe: scan the liquid market instead of a hand-picked list? — tested, rejected

The obvious improvement is to scan whatever trades most rather than a list chosen by hand: today
UNI, XMR, LIT, ARB, ENA and TAO each trade more than DOGE, LINK or PENDLE. `hlg/universe.py` does
that (`scanner.universe.mode: auto`: the top N perps by 30-day median daily volume, listed 60+ days,
OI >= $5M, rebuilt once per UTC day), and `python -m hlg.backtest --universe-study` tests it before
it is allowed to change what gets alerted:

- **point-in-time:** on each day a coin qualifies only on volume data up to the day before;
- **delisted coins included** (HL still serves their candles), so the test is not run on survivors;
- same-day signals fill **most-liquid first** in every run, fixed in advance;
- adoption rule written down before the first run: `auto_top30` PF >= 1.4, max drawdown no worse than
  the fixed list's + 5pp, second-half PF > 1.2.

`breakout_long`, 1d, 2023-06 → 2026-09, 231 perps (56 delisted):

| universe | trades | PF | total | max DD | 2nd-half PF | signals/month |
|---|---:|---:|---:|---:|---:|---:|
| fixed 15-coin list | 124 | 1.75 | +80% | -18% | 2.71 | 3.1 |
| **auto top 30 (candidate)** | 101 | **1.37** | +36% | -13% | 1.50 | 2.6 |
| auto top 15 | 99 | 1.22 | +24% | -16% | 1.30 | 2.5 |
| auto top 50 | 101 | 1.37 | +36% | -13% | 1.50 | 2.6 |
| today's top 30 applied to all history (biased control) | 149 | 1.56 | +87% | -17% | 1.76 | 3.8 |

(The fixed list's 1.75 vs 1.67 in the Backtest table above is only the fill order: that table fills
same-day signals in config order, this one most-liquid-first.)

**Result: FAIL (PF 1.37 < 1.4) — `mode` stays `fixed`.** Three things the numbers say:

1. **The coins it adds lose money.** Inside the auto universe, trades in coins outside the curated
   list made PF 0.89 (36 trades); trades in coins on the list made PF 1.70 (65 trades). Liquidity alone
   does not pick coins where 20-day breakouts follow through.
2. **Survivorship bias is worth about 0.2 PF.** Using today's top 30 for all of history reports PF 1.56;
   picking them as they were at the time gives 1.37.
3. **The fixed list's 1.75 is flattered the same way.** It was chosen in 2026, knowing which coins
   survived and trended. The honest forward expectation for the breakout rule sits somewhere between
   the point-in-time universe and the curated list, not at the curated list's number.

**4h says the same, louder.** Same study on 4h bars (222 perps; 12 were skipped after a local network
outage mid-fetch): fixed list PF 1.36 over 400 trades, `auto_top30` PF **1.08** over 379, with the same
-33% drawdown and profit from the added coins strongly negative. More coins here mostly means more
trades at a thinner edge.

Also visible: at a $10M/day bar only ~2 coins qualified per day in 2023 and ~10 in 2024 (HL volume was
small then), so top 30 and top 50 are identical — the cap never binds. `mode: auto` and
`allowed_coins: auto` remain available for anyone who wants to run it anyway, and the dashboard's
near-breakout list and volume column work in either mode.

### Entries: can failed breakouts be avoided? — tested, nothing adopted yet

About 60% of breakout trades lose; the system pays through the ~20% that run for weeks. So the test
is not "fewer failures" but "more profit", and every idea was checked against that.
`python -m hlg.backtest --entry-study [--interval 4h]`, fixed coin list, 2023-06 → now. Rule written
down first: PF at least +0.10 over the current rule, max drawdown no more than 2pp worse, at least as
good in both halves; a filter must also beat randomly dropping the same number of trades (>95th pct).

| variant | what changes | 1d PF | 4h PF | verdict |
|---|---|---:|---:|---|
| current rule | enter next open, 2 ATR stop, 3 ATR trail, 21d | 1.75 | 1.40 | — |
| `fail_exit2` | exit if a close falls back below the level in the first 2 bars | 1.42 | 1.40 | fail |
| `confirm` | enter only if the next bar also closes above the level | 1.39 | 1.40 | fail |
| `fib382` | limit at the 0.382 retrace of the leg (20-bar low → high), 5 bars | 1.27 | 0.92 | fail |
| `fib50` | same at 0.5 | 1.18 | 1.37 | fail |
| `strong_close` | breakout bar closed in the upper half of its range | 1.60 | 1.47 | fail (4h: 99th pct, but +0.07 and 1d worse) |
| `near_level` | breakout bar closed ≤ 1 ATR past the level | 1.76 | 1.43 | fail (50th / 81st pct) |

What the numbers say:

- **Waiting for a retrace misses exactly the trades that pay.** Of the top-20% winners of the current
  rule, `fib382` never entered 21/24 (1d) and 60/80 (4h); `fib50` 23/24 and 72/80. Retraces to 0.382
  or 0.5 do happen often — that is what you notice on the chart — but they happen mostly on the
  breakouts that were going to be ordinary, while the strongest ones leave without looking back.
  On 4h the 0.382 entry loses money outright (PF 0.92, -60% drawdown).
- **Cutting failures early makes losses smaller but more numerous.** `fail_exit2` shrinks the average
  loss from -1.07% to -0.77% of equity, but many "failed" breakouts dip under the level and then run;
  exiting them hands that back.
- **`strong_close` (no long upper wick) is the one live idea.** On 4h it beats random at the 99th
  percentile, but it misses the +0.10 margin and makes 1d worse. An effect that flips sign between
  timeframes is not something to trade on yet — it is something to measure going forward.

That is what the **signal journal** is for (Technical tab): every breakout signal from now on is
followed under the rule's own mechanics, taken or not, and records its result in R, whether it
failed early (and whether it recovered), and the breakout bar's close location. After ~30 closed
signals that is fresh, out-of-sample evidence on exactly these questions.

### Momentum heads-up

Separately from the breakout rule, the scanner flags plain price momentum: if a coin moves more than
`momentum_alert_pct` (default 8%) within `momentum_window_hours` (default 4h), it sends a heads-up. This is
**not a setup** - no stop, no size, no entry - just a nudge to go look at the chart, for moves fast enough that
by the time 4h confirms them (or 1h, which the backtest above ruled out as too weak to trade on) most of the
move is already over. Alerts re-fire if the move grows past another 5-percentage-point bucket, so one long
continuous spike doesn't get lost after the first alert but also doesn't spam every scan cycle.

### Macro / non-price filters (`hlg.macro`) — tested, and mostly rejected

The breakout rule is pure price action on one coin, so the obvious question is whether anything
*off* the chart improves it: volume, volatility regime, funding, the BTC tape, or the macro backdrop.
`hlg.macro` pulls five daily series from FRED with no API key (HY credit spread, VIX, S&P 500, broad
dollar index, 10y yield), forward-fills them onto the crypto calendar and **shifts everything one day**
so a filter reading bar `T` can only see data published by `T-1`.

Each idea is a one-line predicate in `backtest.FILTERS` and runs as its own variant:

```bash
python -m hlg.backtest --variant breakout_long breakout+squeeze breakout+risk_on
python -m hlg.backtest --filters squeeze cheap_funding    # ad-hoc combination
python -m hlg.macro                                       # print the current backdrop
```

Against the 126-trade `breakout_long` baseline (PF 1.67, 2023-06 → 2026-09), with each filter's PF
compared to **dropping the same number of baseline trades at random** (20k draws — this strategy
earns on ~20% of its trades, so which ones you keep swamps everything):

| filter | what it demands | kept | PF | vs chance |
|---|---|---:|---:|---|
| *(baseline)* | — | 100% | 1.67 | — |
| `squeeze` | ATR in the bottom half of its 100-bar range | 71% | **1.77** | 64th pctile — noise |
| `rs_top_half` | top half of 20d return vs the universe | 100% | 1.69 | no-op: a 20-bar-high breakout is *already* top half |
| `vol_confirm` | breakout bar volume ≥ 1.5× its 20-bar average | 85% | 1.62 | noise |
| `vix_calm` | VIX below its 50d EMA | 82% | 1.61 | noise |
| `cheap_funding` | funding ≤ 30% APR | 85% | 1.59 | noise |
| `btc_bull` | BTC above its EMA200 | 89% | 1.38 | **worse** (3rd pctile) |
| `no_credit_stress` | HY spread below its 50d EMA | 89% | 1.32 | **worse** (1st pctile) |
| `spx_bull` | S&P above its 200d | 97% | 1.26 | **worse** (0th pctile) |
| `risk_on` | no credit stress *and* SPX bullish | 90% | 1.24 | **worse** (0th pctile) |
| `no_dxy_headwind` | dollar below its 50d EMA | 60% | 1.22 | noise |
| `not_extended` | ≤ 2 ATR above EMA20 at the signal | 67% | 1.14 | **worse** (2nd pctile) |

**Nothing earned a place in the entry rule.** The best-looking one (`squeeze`, PF 1.77 and a much
softer -12% max drawdown vs -18%) sits at the 65th percentile of random subsets — indistinguishable
from luck, and it is *worse* out-of-sample than the baseline (OOS PF 2.34 vs 2.71). `rs_top_half`
turned out to be a no-op — it kept all 126 trades, because a coin closing above its 20-bar high is
already in the top half of 20-day returns by construction. Thresholds were
fixed a priori (median splits, or a series against its own moving average) precisely so there was
nothing to tune; with 11 filters tested you would expect ~0.5 of them above the 95th percentile by
chance alone, and none got there.

The interesting result is the failures. Gating on a **risk-on** backdrop is reliably *worse than
random*, and splitting the baseline's own trades by backdrop shows why:

| signal-day backdrop | trades | PF | win rate | share of total profit |
|---|---:|---:|---:|---:|
| credit stress (HY widening) | 27 | **3.74** | 52% | **63%** |
| credit calm | 99 | 1.29 | 31% | 37% |
| S&P below its 200d | 11 | 8.60 | 64% | 46% |
| S&P above its 200d | 115 | 1.38 | 33% | 54% |

Breakouts that fire while credit is deteriorating were a fifth of the trades and most of the money
(permutation test p ≈ 0.03), spread over 10 quarters and 13 coins rather than one lucky cluster.
They also survive losing their three biggest winners (PF 1.97), while the calm-credit trades go
*negative* without theirs (PF 0.90). The plausible reading: a coin strong enough to break out into a
hostile tape is showing real idiosyncratic strength, while breakouts in an everything-rallies tape
are just beta and fail when the tide goes out.

That is **not** wired in as a rule. It is a post-hoc hypothesis on 27 trades, found on the same
sample that generated it, and the p-values ignore that 11 filters were tried first — invert the gate
and you are fitting noise with a good story attached. So the backdrop ships as **context only**: a
line on breakout alerts and a strip on the dashboard, changing no decision, there to be logged
against outcomes until there is enough fresh data to say something honest.

> **`scanner.macro_context` ships off**, because `fred.stlouisfed.org` read-times-out from GitHub
> Actions runners — 10 of 10 requests hit the timeout, adding ~300 s to a job that runs every 15
> minutes and returning nothing (`"macro": null`). It degrades cleanly rather than failing a scan,
> but it is dead weight there. FRED answers normally from a workstation, so set it to `true` if you
> run `python -m hlg.scanner` locally; `python -m hlg.macro` prints the current backdrop, and the
> backtest filters above use the same data. If you want it on the hosted dashboard, the FRED *API*
> host (`api.stlouisfed.org`, needs a free key) is a different endpoint and may not be blocked —
> untested.

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
