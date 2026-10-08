# Changelog

Dates are UTC. For the details and reasoning, see the commit messages.

## 2026-10-08

- **Fix: the service worker was empty.** Commit 9148150 emptied `pwa/sw.js`, which receives and displays push notifications and keeps the offline shell, so background notifications stopped working once phones picked it up. It is restored from the previous version, with a cache bump (hlg-v35) so devices update.
- **MA momentum study** (`--ma-momentum-study`, pre-registered in docs/research/ma-momentum-study.md): the MA pullback on the day's movers only. That means a top-50 point-in-time pool (delisted coins included), top or bottom 20% by 20-bar return, and a recent volume spike. Not adopted.
  - **The selection helped:** PF went from 0.85 to 1.16 on 1d and from 0.92 to 1.08 on 4h, at the 90th and 94th percentiles. That's not significant.
  - **Still behind the breakout:** it trails the breakout rule on the same pool.
  - **In the live book:** drawdown widened from −18.5% to −28%.
  - **The 2021–22 holdout couldn't run:** HL's backfilled candles carry no volume.

  New in the backtester: `momentum_context` and the `mom_top`, `mom_bottom` and `vol_spike` filters, plus side-specific filters for the MA rule.
- **MA pullback study** (`--ma-pullback-study`, pre-registered in docs/research/ma-pullback-study.md): Emmanuel Malyarovich's 9 EMA / 20 SMA / 200 SMA trend pullback, long and short, made mechanical. Not adopted.
  - **By timeframe:** PF 1.56 on 1d, 0.94 on 4h, 1.16 on the 1d holdout and 0.93 on 1h.
  - **In the live book:** Sharpe fell from 1.03 to 0.76 and drawdown widened from −18.5% to −47%.
  - **One lead:** the daily long side, which would need a fresh test.

  New in the backtester: `_ma_signal`, a one-bar stop-entry mode, an exit on a close back through the 20 SMA (`ma_exit`), and per-stream rules in `run()` (`P["V_of"]`) so a combined book can mix strategies.

## 2026-10-08

- **Wallet check** (`pwa/report.html`), a public page: paste a Hyperliquid address and see how the last 90 days of losses happened. It shows:
  - the worst 5% of positions' share of losses;
  - losses from averaged-down and unstopped positions;
  - liquidations;
  - the biggest losers, each tagged with what happened;

  all next to the wallet study's 145 traders.
  - **Private by design:** it runs entirely in the browser against Hyperliquid's public API. No server, nothing stored, and the address goes nowhere else (a test checks every request).
  - **Same analysis as the study:** a port of the study's position rebuild, and a test checks that the JavaScript and the Python agree.

- **Reinforcing the process, not the trading** (Now card; the evidence is in docs/research/behaviour-change.md):
  - **Your why:** your own line under the headline.
  - **Plan kept:** a 30-day dot strip plus an all-time count. A slip day shows as a ring and never resets the count. The slip days come from new `behavior.slip_days` and `execution.slip_days`, which record dates only.
  - **A slip note:** a recent slip gets a self-compassionate line.
  - **Process milestones:** urges waited out, days on plan and weekly reviews, each with one quiet note when reached.
  - **Looks today vs your usual.**
  - **The charting urge:** "Look at charts, project the price" is a new urge reason, answered with your swap plan and a 15-minute charting window.

  Nothing rewards a trade, a win or the P&L.
- **Short study** (`--short-study`, pre-registered in docs/research/short-study.md): do breakdown shorts pay while BTC is below its 200-day EMA? No, and not adopted.
  - **Worse than unconditional:** PF 0.74 (1d) and 0.68 (4h) since 2023-06, at the 25th and 3rd percentiles of random short subsets.
  - **Hurts the live book:** adding the shorts cut its Sharpe from 1.03 to 0.58.
  - **Only paid in 2021–22:** PF 1.51 on the 1d holdout.
  - **Longs held up:** the long rule in the same downtrends made PF 2.82 (1d) and 1.59 (4h).

  New in the backtester: a `btc_down` filter and `short_filters` (filters that apply only to the short side).
- **Plan vs reality card** (top of the Technical tab):
  - **The rule vs its backtest,** per timeframe (from the journal's new `by_tf` summary): win rate, reached +1R first, average R.
  - **You vs the rule** on the signals you took: your realised R (new in `hlg/execution.py`) against the rule's, i.e. the execution cost per trade, plus off-plan positions.
  - **The verdict,** fixed in advance: not before 30 closed trades; then *on track* within 2 standard errors of the backtest's average R, *below* / *above* outside it.

  Small tables can now opt out of the phone card layout (`.nocard`).
- **Weekly review card** (top of the Technical tab): this week or last, graded on the process, not the P&L.
  - **Grade:** A (every rule kept) / B (one slip) / C, listing each slip: rule-breaking trades, off-plan positions, signals taken without a stop.
  - **The week's numbers:** clean trades, signals taken / skipped, strategy trades closed in R, urges waited out, and predictions scored.
  - **The cue:** on Sundays and Mondays a *Weekly review due* button sits on the Now card until last week is marked reviewed.

  `hlg/execution.py` now adds per-week counts (Monday 00:00 UTC).
- **Execution gap** (`hlg/execution.py`, Signal journal → *Your execution*). Your real fills are matched to the journal's signals hourly:
  - signals taken vs skipped, and what the skipped ones made;
  - entry vs the next open, in ATR;
  - when the first stop went in (before the entry / minutes after / never);
  - size vs the rule's;
  - off-plan positions that matched no signal, with their net.

  P&L stays in the encrypted payload.
- **Prediction log** (Signal journal → *Your read vs the rule*): one tap on a live entry alert, before it resolves: does it reach +1R before the stop? It's scored from the journal against the rule's own rate (~50% / 56%) with a Brier score, judged at 50, and kept on the device.
- **TradFi positions are tracked.** Hyperliquid's TradFi perps (`xyz:SP500`, `xyz:GOLD`, …) trade on the separate `xyz` exchange, which the monitor never read. A TradFi position was invisible to every rule and to the dashboard. Positions, open orders (so a stop there counts) and prices now include `xyz`, and the position counts toward account leverage. If `xyz` is unreachable, the monitor falls back to the main account. `allowed_coins` gains the TradFi perps.
- **Macro backdrop in production.** It was off on GitHub because FRED's keyless download times out from Actions runners, which also left the market state without its credit / S&P leg. With the new optional `FRED_API_KEY` secret, it reads FRED's official API instead:
  - refreshed every 6 hours, keeping the last good reading through an outage;
  - the key never reaches the public log, because errors are reduced to type / status.
- **Fix: a stuck run can no longer freeze the dashboard.** A monitor run sat "waiting" on the `github-pages` environment for 4.5 hours, with nothing to approve (a GitHub-side glitch). It held the one-at-a-time slot while every queued run was cancelled, so nothing was published. A new run now replaces the one in progress (`cancel-in-progress: true`), which clears a stuck run within 15 minutes.
- **TradingView and OpenMarket versions of the rule:** `tradingview/hlg_breakout.pine` (Pine v6: status table, sizing, labels) and `tradingview/hlg_breakout.wrun.ts` (wrun: the simplified rule and two alerts). Both decide on closed bars only.

## 2026-10-06

- **Now card, readable at a glance.** It's organised in layers:
  - the state and one line;
  - three tiles: *Next close* (countdown and bar), *Market* and *Last signal*;
  - one line for a big release within 24h;
  - the two actions.

  The record (no-adds streak, clean trades, urges), the strategy's series, the market explanation and your plan move into a fold, closed by default and remembered.
- **Scanner groups fold** from their heading (Daily, 4-hour, TradFi), remembered on the device. A folded group keeps its signal / breaking-out / near counts in the heading. TradFi starts folded.
- **Fewer failed monitor runs.** Two GitHub-side causes of the occasional failure emails:
  - **Deploy timeouts:** Pages deploys timing out on GitHub's token service. The deploy step now retries once, which also prevents the duplicate pushes an undeployed run caused.
  - **Runner congestion:** runs queued with no runner were cancelled when the next one arrived. That is unchanged, because the next run does the job. Runs are now capped at 10 minutes so a hung one can't hold the slot.
- **GitHub Actions on Node 24:** checkout v7, setup-python v7, configure-pages v6, upload-pages-artifact v5 and deploy-pages v5, in every workflow. The v4/v5 releases targeted the deprecated Node 20.

## 2026-10-02

- **Signal grade study** (`hlg/signal_grade.py`, pre-registered in docs/research/signal-grade-study.md). Can a breakout be graded Low / Neutral / High before entry? Eleven setup features fed a walk-forward logistic regression for "reaches +1R before the stop" across 246 (1d) and 906 (4h) signals.
  - **Not adopted on either timeframe:** on 4h every grade averaged ~+0.15R; on 1d the gap's CI crossed zero and the order was backwards; the model was overconfident (promised 64–69%, got 47–58%).
  - **Breakout alerts now carry the honest base rates instead:** 35–38% win, and about half never reach +1R before the stop. The top 20% of trades make more than all the profit.
- **Loss limits: measured on the money actually in the account, with a realised / open split.**
  - **Percent base:** the limits' % now divides by the period's starting value plus net deposits and withdrawals, i.e. the value now minus the P&L. The old base, the starting value alone, stayed sized to withdrawn money: after a 60% mid-week withdrawal, the week read −7.8% instead of −13.7%.
  - **Total kept:** the limits still use the total P&L, open positions included. A limit on realised losses alone rewards holding losers.
  - **The split:** the hero card and the limit alert's second line now show what the total is made of:
    - realised: closes less fees, plus funding;
    - the change in open positions;
    - spot tokens.
  - **Precision:** the perps-only part is anchored like the total, so the split is exact. Spot is the total minus perps.

## 2026-09-29

- **Notifications: prompt, and no replays when the app opens.**
  - **Replays on open:** opening the app replayed alerts as notifications. It notified for every alert the device hadn't displayed yet, including ones pushed hours before, so notifications seemed to come only when the app was opened. Local notifications are now only a fallback for a device *without* Web Push, and only for alerts that appear while the app is open.
  - **Delivery delay:** pushes carried no urgency, so Android (Doze) and iOS could hold them until the phone woke. They now go out as `Urgency: high`.
  - **Measured on the phone:** each push carries its send time, and the service worker records when it arrived. A small badge in the Alerts title shows it (`push 4 s`), in red when a push took over 5 minutes. The send and arrival times and the median are in its tooltip.

## 2026-09-28

- **Fix: Today / This week started up to ~2h20m early.**
  - **Cause:** Hyperliquid's day/week P&L series have a point only every ~2h20m, and the monitor took the last point *before* 00:00 UTC. A live check found −$162 of Sunday evening counted in Monday's Today. The daily and weekly loss limits measure from the same start.
  - **Fix:** the start is now anchored on HL's all-time series (the only one with a fixed baseline). It is interpolated between the monitor's runs either side of midnight (~15 minutes apart), or between HL's history points when there was no run just before, then kept for the period.
  - **Verified:** against an independent check (account value minus deposits and withdrawals), which agreed to the cent.
  - **Labels:** the chips now say where they start ("from 02:00", your time), with a note that Hyperliquid's app shows rolling 24h / 7d. The gauges say "since 00:00 UTC".
- **Push notifications, easier to set up and harder to miss.**
  - Every alert now buzzes (`renotify`). Before, a new alert that replaced one still sitting in the tray arrived silently.
  - The *Push* button no longer waits for the bell button: it asks for permission itself, which matters after a reinstall resets it.
  - The subscription code opens at the top of the page. It used to appear below the footer, off-screen on a phone.
  - Any failure is explained in the box: blocked permission, not installed on iPhone, or the background worker not starting.
- **Wallet behavior study** (`scripts/wallet_behavior_study.py`, docs/research/wallet-behavior-study.md; exploratory, not pre-registered): 200 random Hyperliquid wallets, 20,138 closed positions over 90 days.
  - Losses concentrate in a few positions (worst 5% = half of a wallet's losses), and 83% of the worst 1% broke a guardrail rule (averaged down, liquidated, or no stop).
  - 48% of wallets were liquidated at least once, and 96% of those liquidations had no stop.
  - The habits did not predict which wallets were profitable, so the guardrails are protection, not an edge.
  - Raw data stays gitignored; only aggregates are committed. The figures were reproduced from the cache, and the position rebuild gained 6 unit tests.

## 2026-09-27

- **Scanner: see at a glance what to watch.** Every row gets one status, shown as a word and a coloured mark:
  - *Signal*: a live entry.
  - *Breaking out*: above its trigger, needs the bar to close; shows the time left.
  - *Near*: within 0.5 ATR.
  - *Uptrend*: further off.
  - *Ran past* and *Downtrend*: in muted text.

  Rows sort by status, then by distance to the trigger. Each timeframe heading counts signals, breakouts and near ones. The Near breakout card becomes **Watch closely**, which also lists signals and breakouts in progress. TradFi perps were drawn at 55% opacity, which read as disabled; they now sit at full strength in their own group, labelled *watch only* with the reason.

## 2026-09-25

- **Wallet behavior study** (`scripts/wallet_behavior_study.py`, exploratory and not pre-registered; docs/research/wallet-behavior-study.md): 200 random active Hyperliquid wallets over 90 days, 145 of them analyzed (20,138 closed positions). Losses sit in the tail: in each wallet the worst 5% of positions are half the losses. Averaged-down positions are 14% of positions but 45% of losses, and 83% of the worst 1% broke a guardrail rule. 48% of wallets were liquidated, 96% of those positions with no stop. Wallet-level habits did not predict profitability (stop users were profitable less often, 31% vs 50%), so this supports the guardrails as tail protection, not as an edge. Nothing adopted. Raw data stays in the gitignored `miner_cache/`.
- **A calmer, friendlier look.** New app icon matching the header's blue-to-indigo shield, drawn bolder so it reads at 16px. Adds a maskable Android icon and a proper Apple touch icon. Each card title gets a small line icon on a soft tinted badge, with one quiet hue per area: Technical blue, Macro violet, Flow teal, alerts amber. Alert groups get icons, tabs show their icon on desktop too, and panes fade in with short colour transitions. All motion is off under reduced motion; icons are decorative (aria-hidden) next to text that says the same.
- **Bull market support band study** (`--bmsb-study`, pre-registered in docs/research/bmsb-study.md): 20-week SMA / 21-week EMA vs the 50-week SMA as breakout filters. Nothing adopted, and neither line is clearly more relevant. BTC's band is neutral (4h PF 1.36 above = 1.36 below), unlike its harmful 50-week line. For the coin itself, the band and the 50-week measure the same long-term uptrend (4h ~1.6 vs ~1.1 PF), but only the 50-week held up as a filter.
- **Signal journal: the 50-week lead, tracked live.** Each new signal records whether the coin closed above its own 50-week and 200-day SMA (`hlg/lines.py`, one daily-candle request per new signal). The journal card shows closed 4h signals above vs below the 50-week line. The rule is fixed before any live data: judged at 25 per side; the lead holds if PF above ≥ PF below + 0.3 and ≥ 1.2, which earns a fresh backtest, not adoption.
- **Long-term lines study** (`--ma-study`, pre-registered in docs/research/ma-lines-study.md): 50-week and 200-day SMA/EMA (Benjamin Cowen's lines), for BTC and each coin's own chart, as breakout entry filters on 1d and 4h. Nothing adopted: the BTC lines made results worse on both, and breakouts taken with BTC *below* its lines did better (4h PF 1.54–1.63 vs 1.16–1.20). One lead, not adopted: the coin's own 50-week SMA on 4h (PF 1.61 vs 1.39, drawdown −30% vs −40%, 95th percentile) did nothing on 1d.
- **Market today** on the Now card, in every lesson and in the urge check: risk-on / mixed / risk-off (`hlg/market_state.py`, the market-state study's own classifier) and any high-impact release within 24h. Since the study found the same odds in every state, it names the pull on you (fear: buy the dip; greed: chase and size up), never a trade signal, and quotes only the facts that held everywhere. Lessons have per-state wording; a new lesson, *Stops go on before the news*, comes up when a release is near and positions are open.
- **Market-state study** (`--regime-study`, pre-registered in docs/research/regime-study.md): does the live book behave differently in risk-on vs risk-off, BTC up/down trend, credit/S&P backdrop or Fear & Greed band? No: 2 of 44 tests passed, the number expected by chance. Risk-on vs risk-off: PF 1.33 vs 1.36, win rate 37% vs 35%. Every state entered 8–12 times a month and had losing runs of 7–18 trades. State-aware messaging may therefore address the pressure on the trader, not the strategy's odds.
- **Today's lesson** on the Now card: a 1-minute lesson picked by what your data shows today (e.g. a position under water → averaging down; a losing day → the break-even effect). Each is one idea from the research (Douglas, Tversky & Kahneman, Odean, Barber & Odean, Tharp, Thaler & Johnson, Duke, regret theory, this repo's Fear & Greed study), your own numbers for it, what it means today, and one if-then plan. The urge check's "Why wait?" opens the lesson matching the urge. Built in the browser from the decrypted data: nothing goes to a model.
- **Next decision point** on the Now card: a live countdown to the next 4h/1d close and a bar through the current candle. The strategy only acts on closed bars, so between closes no entry can signal; the card says so.
- Phone tab bar: no longer a scroll container, centred without a transform, bottom offset that doesn't move with Safari's toolbar (WebKit mis-placed it after scrolling).

- **Discipline aids** (README "Discipline aids"), after rebuilding the account's own fills into round
  trips showed the losses came from adding to losing positions, not from trading often:
  - **resting-add warning**: open orders that would add to a losing position, or below its entry,
    are flagged (pushed) before they fill, with the account's own record on the line under the push;
    the existing "added while underwater" warning gets the same evidence line;
  - **Now card** at the top of the app: Wait / Setup live / Manage / Locked, "nothing new since your
    last look", days since the last signal, the streak without adding to a loser, clean-trade dots,
    the strategy's recent series, and an if-then plan (`config.yaml` -> `discipline`);
  - **urge check**: name it, a timed pause, then a checklist from live data; waited-out urges counted.
  - `hlg/behavior.py` rebuilds round trips hourly from public fills; P&L stays in the encrypted state
    and never reaches logs or a push's first line (lock screen).
  Grounded in Tversky & Kahneman (1992), Odean (1998), Douglas (Trading in the Zone), Gollwitzer &
  Sheeran (2006), Bowen & Marlatt (2009), Oulasvirta et al. (2012).

- **Dashboard, phone-first pass** (audited at 390px in both themes, before and after):
  - the freshness line ("updated N min ago") was cut off on phones; the header now uses icon buttons
    (labels kept for screen readers) and folds the address away instead;
  - Technical / Macro / Flow become a fixed bottom tab bar on phones; a tap brings the tab into view;
  - six wide tables were clipped (Funding, Setup, Longs...): on phones every table turns into
    labelled cards, generically, so new tables get it for free;
  - long card explanations fold to two lines (tap to open); 40px+ touch targets on touch screens;
    focus rings; a loading skeleton while data fetches and decrypts;
  - equity axis showed "$12k" on every line: labels now carry the digits the tick step needs.
- **New visual aids** (each encodes something, no decoration): distance-to-trigger bar on every
  scanner row (in ATR), R-multiple bars in the journal, a fear-to-greed scale with today's marker,
  and a bullet chart for the cascade tracker (live hit rate vs base rate, backtest and alert bar),
  plus a progress bar toward 30 closed hypothetical adds.

## 2026-09-24

- **Cascade early warning, tracked live** (`hlg/cascade_watch.py`): the study's detector runs hourly
  on each scanned coin after the push (12 candle requests an hour, 12 a day for 1d context), records
  confirmed cascades with a stop within 2 daily ATRs, and resolves each from the signal journal
  within 10 days. Journal card: hit rate vs the backtest's 45.8% / 31.9%, lead days, entry advantage.
  Tracking only - no alert.

- **Earlier entries into 1d trends: tested, neither adopted** (`python -m hlg.reversal`, README
  "Earlier entries"). Binance 1h data since 2023-06, 15 coins, one simulator for every entry, rules
  fixed before running. The 1h->4h reversal **cascade** does worse than random entries in the same
  uptrends (PF 0.90, 9th percentile); the **failed breakdown** makes PF 1.28 but random entries with
  the same stops make ~1.2 (75th percentile). The cascade raises the odds of a 1d breakout within 10
  days to 45.8% from 31.9% (lift 1.44, bar was 1.5): not enough for a notification. Same verdicts
  with an engine-like daily trail.
- Found along the way: the breakout's headline PF 1.67 is the capped 3-slot book; on every signal it
  is 1.33 (R). Documented in README.

- **Risk rules revised** (README "Position sizing"), after testing the defaults for the first time:
  - `risk_per_trade_pct` 1.5 -> **1.0**: risk % doesn't change the edge (1d PF 1.66-1.67 from 0.75% to
    2%), only drawdown; and 3 stops on one correlated day were 4.5%, past the 3% daily limit.
  - `max_same_direction` 2 -> **3**: the long-only strategy with 3 slots tripped it when followed exactly.
  - `max_leverage` now checks **isolated** positions only: on cross margin the per-position setting just
    reserves margin, and account leverage is already capped by `max_gross_exposure_x`.
  - `max_positions` stays 3: 3-4 is the plateau on both timeframes.
- **The live book measured** (`--combined-study`): 1d + 4h sharing 3 slots, one position per coin.
  At 1.5%: max DD -26.5%, 44 days below -3%. At 1.0%: -18.5%, 8 days. 4h signals take ~80% of the
  slots, so live behaves mostly like the 4h strategy (PF 1.41, not 1d's 1.67).
- Engine: instruments can be coin|timeframe streams sharing coins (`coin_of`) and marked on a finer
  grid (`mark`); single-timeframe results unchanged.
- **1d priority over 4h: tested, not adopted** (`--priority-study`, README "Position sizing").
  Candidate `reserve1` (4h at most 2 of 3 slots) fails: PF 1.42 -> 1.44, DD -18.5% -> -20.5%, weaker
  second half. Filling 1d first at the same moment changes nothing (1d fills only at 00:00 UTC).
  Preempting a 4h trade for a 1d signal is clearly worse (return -40%, Sharpe 0.83). Recorded as a
  lead, not a result: `reserve2` (4h gets 1 slot) scored PF 1.62, Sharpe 1.29, DD -15.8% for ~10%
  less return, but it was one of six variants, not the candidate.

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
