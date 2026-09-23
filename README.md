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

The top of the page is the act-now layer: portfolio value with today / this week, an **equity curve** (24h / 7d / 30d / all, from `history.json`, which keeps every run for 3 days, hourly to 90 days, daily after) with the loss-lock level drawn in, and **risk gauges** for the daily and weekly loss limits, account leverage and account ratio - each with an icon and a word, never colour alone. Positions show a **price ladder** (liquidation, stop, entry, mark; a missing stop is called out), an expanded breakout alert shows its **last 40 candles** with the breakout level and stop, the journal draws **cumulative R**, and the Flow tab has a **liquidation map** per coin. Charts are hand-written SVG (no library, nothing loaded from anywhere else), 2px lines and hairline grids, colours validated for colour-blind separation and contrast; dark by default, light when the OS is light.

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

**Liquidation levels (Flow tab)** show where Hyperliquid positions would be force-closed: short
liquidations above the price (forced buying if price gets there), long liquidations below (forced
selling). Aggregators such as CoinGlass *estimate* this for the big exchanges from open interest and
assumed leverage ($699/month for the API tier that has it). Hyperliquid is on-chain, so
`hlg/liqmap.py` reads the real `liquidationPx` of every open position of ~560 accounts: the 200
largest by value plus the 400 most active by weekly volume. Selecting by size alone was measured
and rejected: big accounts mostly run low leverage, so 400 of them put $10M within 5% of BTC and
nothing near ETH, where the active traders put $131M / $85M either side of BTC and $40M / $122M
around ETH. Coverage is shown as a share of each coin's open interest (typically 20-55%). Most of the
near-price leverage sits on the majors; for many alts there is nothing within 5%.

It refreshes every 4 hours, after alerts are published and pushed (~25-30s), so it never delays a
notification. Breakout alerts carry one context line ("shorts liq $4.2M within +5% (largest $1.9M at
+3.1%)"), and the signal journal stores it with each signal. That is the test: clusters are often
read as magnets that price is drawn to, and the journal will show whether breakouts heading into a
short-liquidation cluster actually did better. Until then it changes no rule. Only per-coin
aggregates are kept; the account list is held in the encrypted state and no address is stored
with its positions.

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

**Shorting — tested, not adopted.** The scanner is long-only. The mirror rule (`breakout_short`:
close below the 20-day low in a downtrend) and a combined book (`breakout_both`) exist in
`VARIANTS` and were run on the same coins and window:

| variant | trades | PF | total | Sharpe | IS PF | OOS PF |
|---|---:|---:|---:|---:|---:|---:|
| breakout_long (live) | 126 | 1.67 | +78% | 0.98 | 1.18 | 2.71 |
| breakout_short | 100 | **0.88** | −7% | −0.11 | 0.56 | 1.14 |
| breakout_both | 211 | 1.04 | +13% | 0.28 | 0.76 | 1.44 |

The short side loses money outright — in-sample it's a clear loser (PF 0.56), and even
out-of-sample it barely breaks even. Adding it to the book doesn't diversify the long side, it
drags it: Sharpe falls from 0.98 to 0.28. Consistent with crypto's structural long bias over this
window; `python -m hlg.backtest --variant breakout_short breakout_both` reproduces it.

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

### Adding specific coins (INJ, AVAX, TAO, FET) — tested, not adopted

The liquidity-ranked universe above answers "should the list rank itself by volume". A different
question is whether a handful of specific, named coins are worth adding to the live list by hand.
`python -m hlg.backtest --candidate-study INJ AVAX TAO FET [--interval 4h]` tests that directly:
breakout_long on the live `scanner.coins` (11 coins — see note below) alone, vs. the same list plus
the named candidates always eligible, same-day signals filling most-liquid first as in the universe
study. Adoption rule, fixed before running: combined PF >= 1.4, max DD no worse than the fixed
list's + 5pp, second-half PF > 1.2, **and** the candidates' own net P&L not negative — riding the
other 11 coins' shared drawdown protection without contributing doesn't count as pulling weight.

| interval | universe | trades | PF | total | max DD | OOS PF | candidate trades | candidate net |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1d | fixed | 110 | 1.89 | +90% | -14% | 2.87 | — | — |
| 1d | **fixed + candidates** | 123 | **1.92** | +134% | -16% | 1.96 | 25 | +$179 |
| 4h | fixed | 336 | 1.53 | +262% | -26% | 1.61 | — | — |
| 4h | **fixed + candidates** | 379 | **1.41** | +199% | -32% | 1.50 | 83 | **-$340** |

**Result: not adopted — the two timeframes disagree, and neither pass is convincing on its own.**

- **1d technically clears the bar** (PF 1.92, OOS 1.96, candidates net +$179), but the win is
  fragile: run each candidate alone (no slot competition, same window) and three of the four —
  INJ, AVAX, FET — earned essentially all of their profit in the first half and were flat-to-losing
  out-of-sample (solo OOS PF 0.43 / 0.07 / 0.00); only TAO's *combined* result even involves it
  turning a solo loss (PF 0.63) into a portfolio win, because the shared 3-slot cap mostly filters
  out its weaker signals in favour of better-ranked coins competing for the same slots — a real
  mechanic, but it means the 1d pass is measuring "these coins rarely cost you a slot", not "these
  coins have their own edge".
- **4h fails outright and louder**, the same pattern already seen with the liquidity-ranked
  universe: PF drops (1.53 → 1.41), drawdown worsens by more than the 5pp allowance (-26% → -32%),
  and the candidates lose money outright (-$340 over 83 trades).

Bottom line: none of the four have a standalone edge on this rule: TAO loses money on its own; INJ,
AVAX and FET show a real-looking edge only inside a sample window that has already passed. Not
added to `scanner.coins`.

**Note on which "fixed list" is which.** The backtest's own default research coin list
(`hlg.backtest.DEF["coins"]`, 15 coins — used for the headline PF 1.67/1.75 numbers above) is not
the same as the live `scanner.coins` in `config.yaml` (11 coins): UNI, AAVE, AVAX and TON are in the
research list but not scanned live. Checked here for the first time per-coin: AAVE (PF 0.03) and
UNI (PF 0.43) were the two worst performers of the 15 — reasonably left out. TON is a thin,
roughly-breakeven 3-trade sample. AVAX was actually profitable (PF 1.90, 3 trades) and is *not*
scanned live, while XRP (PF 0.57, a net loser) and NEAR (PF 1.11, barely positive) *are* — so the
live list isn't a clean "keep the backtest's winners" derivation, it predates this per-coin
breakdown. Left as-is rather than changed silently; worth a deliberate decision, not a backtest
side-effect.

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
| `vix_calm` | VIX below its 50d EMA | 82% | 1.58 | noise |
| `cheap_funding` | funding ≤ 30% APR | 85% | 1.59 | noise |
| `btc_bull` | BTC above its EMA200 | 89% | 1.38 | **worse** (3rd pctile) |
| `no_credit_stress` | HY spread below its 50d EMA | 90% | 1.32 | **worse** (1st pctile) |
| `spx_bull` | S&P above its 200d | 99% | 1.48 | **worse** (0th pctile) |
| `risk_on` | no credit stress *and* SPX bullish | 90% | 1.23 | **worse** (0th pctile) |
| `no_dxy_headwind` | dollar below its 50d EMA | 61% | 1.19 | **worse** (just under 5th pctile) |
| `not_extended` | ≤ 2 ATR above EMA20 at the signal | 67% | 1.14 | **worse** (2nd pctile) |

> **A correction, found while answering "are we using all the data points FRED has?"** The FRED
> series behind `hy_stress`/`vix_calm`/`spx_bull`/`dxy_headwind`/`risk_on` used to collapse a
> genuinely missing reading into a confident `False` (`nan > x` evaluates to `False` in plain
> pandas), rather than the "no opinion, let it through" the design intended — two real gaps hit
> this window: this FRED mirror's HY series only starts 2023-09-22, inside the backtest's
> 2023-06-01 start, and `spx_bull`'s 200-day rolling mean has no warmup buffer at all when fetched
> from that same start, so it reads as a confident "bearish" for its first ~200 days regardless of
> where SPX actually was. `spx_bull` moved the most (97% kept/PF 1.26 → 99%/1.48); `no_dxy_headwind`
> flipped from "noise" to "worse" (borderline, just under the 5th percentile); everything else is
> unchanged or within ordinary data-revision drift. `hlg/macro.py` now uses pandas' nullable
> `"boolean"` dtype so a missing reading, from either cause, stays genuinely unknown. See
> `docs/architecture.md` / the commit history for the fix and its regression tests.

**Nothing earned a place in the entry rule, before or after the fix.** The best-looking one
(`squeeze`, PF 1.77 and a much softer -12% max drawdown vs -18%) sits at the 65th percentile of
random subsets — indistinguishable from luck, and it is *worse* out-of-sample than the baseline
(OOS PF 2.34 vs 2.71). `rs_top_half` turned out to be a no-op — it kept all 126 trades, because a
coin closing above its 20-bar high is already in the top half of 20-day returns by construction.
Thresholds were fixed a priori (median splits, or a series against its own moving average)
precisely so there was nothing to tune; with 11 filters tested you would expect ~0.5 of them above
the 95th percentile by chance alone, and none got there.

The interesting result is the failures. Gating on a **risk-on** backdrop is reliably *worse than
random*, and splitting the baseline's own trades by backdrop shows why (corrected numbers; the
"unknown" rows below are the two data gaps above, now kept separate instead of silently folded
into "calm"/"below 200d"):

| signal-day backdrop | trades | PF | win rate | net |
|---|---:|---:|---:|---:|
| credit stress (HY widening) | 22 | **3.39** | 50% | +$344 |
| credit calm | 97 | 1.51 | 34% | +$395 |
| credit **unknown** (no HY reading existed yet) | 7 | 0.20 | 14% | −$71 |
| S&P below its 200d | 4 | 6.24 | 75% | +$113 |
| S&P above its 200d | 98 | 1.65 | 37% | +$497 |
| S&P **unknown** (200d window not warmed up) | 24 | 1.27 | 25% | +$58 |

Two things worth noting precisely: the 7 credit-unknown trades were the worst-performing group in
the entire sample (PF 0.20) — under the old bug they were silently counted as "calm", which is
exactly why the old "credit calm" PF (1.29) read lower than the corrected one (1.51). And "S&P
below its 200d" shrinks from a previously-reported 11 trades to a genuine 4 — too few to read
anything into; most of what looked like an 11-trade bearish-SPX sample was actually 20-odd
warmup-affected trades with no real signal either way. The core, weaker claim survives the
correction — credit-stress signal days still show a meaningfully higher PF (3.39 vs 1.51) — but the
stronger, more precise version previously published here (a specific permutation p-value, "10
quarters and 13 coins") was computed on the pre-fix, contaminated buckets and has not been
re-verified; it is not repeated here rather than restated on data now known to be wrong.
Both buckets survive losing their three biggest winners — stress stays profitable (PF 1.50) and,
corrected, so does calm (PF 1.08), which is a real weakening of what was claimed here before ("calm
goes negative without them, PF 0.90" doesn't reproduce; calm's edge is thinner than stress's, not
absent). The credit-stress trades span 9 of the window's 13 quarters and 12 different coins, so it
isn't one lucky cluster — but with the bucket now smaller (22, not 27) and the contrast with calm
less dramatic, this reads as a real but modest tilt, not the stronger asymmetry previously described.
The plausible reading, unchanged: a coin strong enough to break out into a hostile tape may be
showing real idiosyncratic strength, while breakouts in an everything-rallies tape are more likely
just beta.

That is **not** wired in as a rule. It is a post-hoc hypothesis on a couple dozen trades, found on
the same sample that generated it, and the earlier p-value estimate here ignored that 11 filters
were tried first — invert the gate and you are fitting noise with a good story attached. So the
backdrop ships as **context only**: a line on breakout alerts and a strip on the dashboard, changing
no decision, there to be logged against outcomes until there is enough fresh data to say something
honest.

> **`scanner.macro_context` ships off**, because `fred.stlouisfed.org` read-times-out from GitHub
> Actions runners — 10 of 10 requests hit the timeout, adding ~300 s to a job that runs every 15
> minutes and returning nothing (`"macro": null`). It degrades cleanly rather than failing a scan,
> but it is dead weight there. FRED answers normally from a workstation, so set it to `true` if you
> run `python -m hlg.scanner` locally; `python -m hlg.macro` prints the current backdrop, and the
> backtest filters above use the same data. If you want it on the hosted dashboard, the FRED *API*
> host (`api.stlouisfed.org`, needs a free key) is a different endpoint and may not be blocked —
> untested.

### Stablecoins: does supply growth predict returns? — tested, and the opposite of the claim

The common trading claim is that stablecoin dominance and crypto prices move inversely: money
parks in stablecoins when risk appetite falls, so shrinking stablecoin supply (or supply growing
slower than its own trend) should mark a good time to be long. `python -m hlg.backtest
--stbl-study` tests it, using total USD-pegged stablecoin market cap from
[DefiLlama](https://stablecoins.llama.fi/stablecoincharts/all) (free, no key, daily back to
2017 — chosen over reconstructing a CoinGecko-based dominance *percentage*, whose only free
historical endpoint is capped at roughly a year anonymously and would need an approximated
total-market-cap denominator; the raw stablecoin-supply series is exact, not approximate).

**Part 1 — the hypothesis as stated: does 30-day stablecoin supply growth predict forward
returns?** BTC and an equal-weight basket of `backtest.coins`, full history (2020-08 → now,
~2,200 daily observations), stablecoin data lagged a day so nothing is knowable early:

| series | forward window | n | correlation |
|---|---:|---:|---:|
| BTC | 7d | 2,218 | +0.042 |
| BTC | 30d | 2,195 | **+0.113** |
| BTC | 90d | 2,135 | **+0.136** |
| basket (equal-weight, all coins) | 7d | 2,217 | +0.172 |
| basket | 30d | 2,194 | **+0.265** |
| basket | 90d | 2,134 | **+0.378** |

Every correlation is **positive** — the opposite sign from the claim. By quintile of 30-day
stablecoin growth on the signal day, BTC's forward 30-day return: Q1 (shrinking most) +2.8%, Q2
+0.1%, Q3 +6.0%, Q4 +4.1%, **Q5 (growing most) +8.8%**, roughly monotonic and backed by ~440
observations per bucket, not a handful of outliers. Stablecoin supply growing fastest — not
shrinking — led the best forward returns.

**Part 2 — does gating the live entry rule on it help?** Same 2023-06 → now window and adoption
bar as the entry study (PF ≥ base + 0.10, drawdown no worse than base − 2pp, PF holding up in both
halves, and above the 95th percentile of randomly dropping the same number of trades):

| variant | trades | PF | Sharpe | verdict |
|---|---:|---:|---:|---|
| breakout_long (base) | 126 | 1.67 | 0.98 | — |
| only enter while supply growth is below its own trend | 63 | 1.58 | 0.69 | fail (41st pctile) |
| only enter during a stablecoin outflow | 21 | 1.10 | 0.12 | fail (25th pctile) |

Both fail, and `stbl_outflow` fails badly — worse than picking the same number of trades at
random. Consistent with Part 1: the periods the hypothesis says should be *best* for going long
(shrinking stablecoin supply) tested worst.

Not adopted as a filter, and not shown on the dashboard as context, since the data says the
opposite of the framing it would be shown under. One plausible reading, offered as interpretation
rather than a finding: stablecoins are typically minted *to buy* crypto, not parked defensively —
so net minting may be more a coincident symptom of bullish demand than a leading contrarian
signal. That's post-hoc and untested; it isn't a reason to invert the filter and trade on it
either, for the same overfitting reason the macro "credit stress" finding above was left as
context only.

**Does the dollar index change the answer?** `python -m hlg.backtest --dxy-study` runs the same
two-part test for the other "risk-off parking" framing — dollar strength, not stablecoin supply —
using `hlg.macro`'s FRED broad dollar index (DTWEXBGS), full history (2015 → now, matched to BTC's
own cached range, same ~2,200 observations as the stablecoin test above):

| series | 7d corr | 30d corr | 90d corr |
|---|---:|---:|---:|
| BTC | +0.005 | +0.012 | **−0.231** |
| basket | −0.020 | +0.032 | **−0.158** |

Unlike stablecoins, this is **not** uniform: 7d and 30d are both essentially zero (BTC's 30d
quintile table is flat and non-monotonic — Q1 +7.1%, Q3 +1.8%, Q5 +6.7% — no usable pattern), but
at 90 days the correlation turns moderately negative for both series, in the direction the
"dollar strength is a crypto headwind" claim predicts. So the answer does change by which "USD"
question is asked: stablecoin *supply* shows no support for the inverse-correlation story at any
horizon tested; dollar *strength* shows a real, if modest, long-horizon one.

It still doesn't help the live rule. `no_dxy_headwind` (dollar below its own 50-day trend) already
existed as a filter — the compact table above called it "noise" without a number; re-run here
alongside the stablecoin work, it sits at the **5th percentile** of randomly dropping the same
number of trades, with in-sample PF collapsing to 0.47 and a worse drawdown (−25% vs −18%) despite
fewer trades. Borderline-to-worse-than-random, not the edge the 90-day correlation might suggest —
consistent with this project's other finding that gating entries on a calm-backdrop condition
tends to remove the rule's best trades along with the bad ones, not just the bad ones.

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

### Dashboard: a real-browser test suite (`tests/e2e`)

The unit suite never touches `pwa/index.html` — it checks the *data* `hlg/report.py` produces, not
what a person sees or clicks. `tests/e2e` does, in real Chromium via [Playwright](https://playwright.dev/python/):

```bash
pip install -r requirements-e2e.txt
playwright install chromium
pytest tests/e2e -q
```

43 tests against four synthetic data scenarios (`tests/e2e/fixtures.py`) served from a local static
server (`tests/e2e/conftest.py`) — a full account with one gauge deliberately landing in each of its
good/warn/crit states, a flat classic-account, the unconfigured-passphrase stub, and a **real
AES-256-GCM envelope** sealed with `hlg.vault` (so the browser's WebCrypto path is exercised against
real ciphertext, not a JS-side stand-in: wrong passphrase, correct passphrase, the key surviving a
reload via IndexedDB, *Lock*, and a served envelope with a downgraded KDF iteration count being
refused). It also clicks through the alert list's clear/collapse/restore and persistence, tab and
equity-range state surviving a reload, the SVG charts (candles, price ladder, liquidation bars)
rendering real marks with real tooltips, three viewport widths with no horizontal overflow, both
colour schemes, and — the check a screenshot can't give you — that nothing throws in the console
across a full click-through session. It is what caught a real bug: `renderLiqMap`'s chart-width
formula computed `clientWidth - 34` *before* falling back on a zero width, so on every page load
where the Flow tab isn't the active one (`#lmcard`'s hidden ancestor makes `clientWidth` read 0),
the liquidation chart was briefly built with a negative SVG viewBox. Fixed with a floor, not a
special case, since the same shape of bug existed at two other call sites (see the `Math.max`
wrapping `clientWidth` in `pwa/index.html`).

Excluded from the default `pytest -q` (needs a downloaded browser and takes ~20s; see `pytest.ini`)
and not run on every push — `.github/workflows/e2e.yml` runs it on demand (`workflow_dispatch`) or
when `pwa/` changes on a pull request.
