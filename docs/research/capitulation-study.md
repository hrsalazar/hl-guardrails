# Capitulation long: buy the flush after a liquidation cascade — pre-registration

**Status:** pre-registered 2026-10-16, before any capitulation signal was computed; **run 2026-10-16: neither timeframe passes** (see Results). Results are appended below without editing this section.

## Why

Every adopted rule so far rides momentum: the long breakout in uptrends, and the rally short in downtrends. This one has a different source of profit.

- **The mechanism:** in a long-liquidation cascade, sellers are forced, not choosing to sell. Price overshoots, then partly snaps back as the selling stops.
- **When it fires:** in sharp selloffs, when the breakout is idle. If it pays at all, it should diversify the book rather than compete with it.

## Signal (fixed, candles only, so it can run live on Hyperliquid data)

A completed bar (1d or 4h) is a **capitulation bar** when all of these hold:

| Condition | Rule |
|---|---|
| Big drop | close ≤ previous close − 1.5 × ATR(14) as of the previous bar |
| Wide range | high − low ≥ 2.5 × that ATR |
| Volume spike | volume ≥ 2 × the average of the previous 20 bars |
| Closed off the low | close in the upper 65% of the bar's range (close location ≥ 0.35) |

There's no trend filter: capitulations happen in uptrends and downtrends.

## Trade (fixed)

- **Entry:** long at the next open.
- **Stop:** the capitulation bar's low − 0.5 × ATR.
- **Target:** entry + 1.5 × the risk.
- **Time stop:** 3 days, at the close, on both timeframes. No trail.
- **Sizing and costs:** 1.0% risk; fees and Hyperliquid funding as in every backtest here.

## Data and sets

- **Set S:** the 15 backtest coins, Hyperliquid bars, 2023-06-01 → now. On 4h, from the start of Hyperliquid's 4h history (~2024-07).
- **Set C (confirmation, coins never used for this):** every other main-dex perp, delisted ones included, traded only while it was in the point-in-time liquid universe (top 30 by 30-day median notional, ≥ $10M, ≥ 60 days listed). This is the same universe the rally-short confirmation used.
- **Timeframes:** 1d and 4h are judged separately. Each is its own candidate.

## Adoption bar (per timeframe, all must pass)

1. **≥ 50 trades** in set S.
2. **PF ≥ 1.3** in set S.
3. **PF ≥ 1.1 in both halves** of set S's own trades.
4. **Beats random entries:** in set S, every signal per coin (one position per coin) has a PF above the 95th percentile of 200 random draws. Each draw is the same number of entries after random **down bars** (close < previous close) on the same coins, with stop distances resampled from the signals' own (in ATR), and the same target and time stop. This asks whether the capitulation features matter beyond "buy after a red bar".
5. **Confirms on unseen coins:** set C, ≥ 30 trades and PF ≥ 1.2.
6. **Helps the live book:** the crypto live book (15 coins, 1d + 4h breakouts, 3 slots, 2023-06 → now) plus this rule on **1 slot of its own** must have higher Sharpe than crypto alone, with max drawdown no more than 2 points worse. Hyperliquid nets positions per coin, so a coin already held by the breakout can't take a capitulation entry.

**If a timeframe passes:** it's tracked live in the journal first, then becomes alerts with its own slot and a kill rule fixed before the first live trade. **If neither passes:** it isn't adopted.

## Also reported, with no bar

- **OI confirmation (new data source):** Bybit's daily open-interest history (free, back to 2021) for set S coins on 1d. PF of signals where OI fell ≥ 5% that day vs the rest. A capitulation that also flushed OI is the liquidation footprint the mechanism assumes. If OI helps, a second, pre-registered test would be needed to use it.
- **Correlation:** of the rule's daily P&L with the breakout book's.
- **Exits:** how trades end (target, stop or time) and the average hold.
- **Market state:** signals split by BTC above or below its 200-day EMA.

## Stated in advance

- **The likely failure:** bar 4. "Buy after a red bar" may do as well, as random entries did in the spring and ladder studies.
- **The fragile part:** the 1.5R target and 3-day hold are a guess at the shape of a snap-back, not a tuned value. If the rule fails, it isn't re-tuned and retried on this data.
- **Multiple comparisons:** two timeframes, so roughly one false pass in ten is plausible. That's why there's the unseen-coin confirmation and a kill rule.

## Results

**Run 2026-10-16** with `python -m hlg.capitulation`. Full output: `backtest_out/capitulation_study.md`.

| | 1d | 4h |
|---|---:|---:|
| Trades in S (15 coins) / win rate | 52 / 67% | 126 / 49% |
| PF / halves | **1.53** / 1.30, 1.79 | 0.91 / 1.45, 0.49 |
| Every signal vs random down-bar entries | 1.53 vs 1.01: **84th pctile** | 0.94 vs 0.98: 43rd |
| Unseen coins (61, point-in-time top 30) | **24 trades**, PF 1.31 | 74 trades, PF 1.69 |
| Book Sharpe on its own slot (crypto alone 1.07) | 1.07 (+0.00) | 0.90 |
| Correlation with the breakout book | 0.02 | 0.08 |
| Exits (time / stop / target), average hold | 44 / 6 / 2, 2.8 days | 71 / 40 / 15, 2.1 days |

| Bar | 1d | 4h |
|---|---|---|
| 1. ≥ 50 trades | pass (52) | pass |
| 2. PF ≥ 1.3 | pass | **fail** |
| 3. ≥ 1.1 in both halves | pass | **fail** |
| 4. Beats random down-bar entries | **fail** (84th) | **fail** |
| 5. Unseen coins: ≥ 30 trades, PF ≥ 1.2 | **fail** (24 trades) | pass |
| 6. Improves the book | pass, by under 0.01 Sharpe | **fail** |
| **Verdict** | **not adopted** | **not adopted** |

**Descriptive, information only:** the Bybit open interest on the 1d capitulation day, matched for 46 of the 52 trades.

| | Trades | PF |
|---|---:|---:|
| OI fell ≥ 5% that day | 38 | **1.84** |
| Otherwise | 8 | 0.31 |

## Reading

1. **The daily version is the most promising idea in a while, but it didn't clear the bar.**
   - **In its favour:** PF 1.53, better in the second half (1.79), a 67% win rate, a −4% drawdown, and almost no correlation with the breakout book (0.02).
   - **Against it:**
     - It beat "buy after any red day" only at the 84th percentile.
     - The unseen coins gave only 24 trades (PF 1.31), short of the 30 needed.
     - Its gain to the book's Sharpe was under 0.01.
   - **Sample size:** 52 trades in 3.3 years is too few to tell a real edge from luck at this bar.
2. **The 4h version is noise.** PF 0.91, collapsing in the second half (0.49), and below random. Intraday flushes on 4h are too frequent and too shallow to be forced selling.
3. **The trade shape was off.** The 1.5R target was hit twice in 52 trades. Almost everything ended on the 3-day time stop, so this was "hold for 3 days after a flush", not "catch the snap-back to a target". As stated in advance, it isn't re-tuned on this data.
4. **Open interest is the interesting lead, and only a lead.** Capitulations that also flushed open interest (the liquidation footprint) made PF 1.84. The 8 that didn't made 0.31. That fits the mechanism, but 8 trades is nothing, and the split was looked at after the fact.

## What would settle it

More capitulation events. Two free sources could supply them (see README "Data sources"):

- **Binance's bulk archive (data.binance.vision):** daily bars and 5-minute open interest for these coins back to 2020. The events of 2020–2023 have never been looked at, so they'd be an unseen test period.
- **Coinalyze's liquidation history:** this needs a free API key.

A second pre-registered study could then test a fixed version: the 1d signal, OI down ≥ 5% on the day, and the same trade, all on 2020–2023 data. Nothing is adopted from this run.
