# Macro trend sleeve — pre-registration

**Status:** pre-registered 2026-10-16, before any 2001–2015 data was fetched; **run 2026-10-16: does not pass** (see Results). Results are appended below without editing this section.

## Why, and the problem with asking

The [TradFi study](tradfi-study.md) found that on macro instruments being long in an uptrend paid (random uptrend entries made PF ~1.26), but the breakout trigger added nothing. A macro sleeve on its own slot also lifted the live book's Sharpe from 1.07 to 1.18, with almost no correlation to crypto (0.02). So the question now is whether a plain **trend** sleeve on macro works well enough to add to the book as **diversification**.

**The problem:** this question comes from data already seen (2016 → now on these instruments). Testing it on the same years would confirm what prompted it. So:

- **Primary test: 2001–2015.** No study here has used these years. The verdict rests on them.
- **Confirmation: 2016 → now**, which has already been seen. It must not contradict the primary test, but it can't carry the verdict.
- **Fixed in advance:** a textbook rule, not tuned, and the instrument list below.

## Instruments (fixed)

Yahoo daily bars, split-adjusted:

- **Indices:** ^GSPC → xyz:SP500, ^NDX → xyz:XYZ100.
- **Metals:** GC=F → GOLD, SI=F → SILVER, HG=F → COPPER.
- **Oil:** CL=F → CL, BZ=F → BRENTOIL. Brent's Yahoo history starts in 2007, so it joins the test from then.

These are "indices, metals and oil" as proposed before this study. **Left out, for reasons other than their P&L:**
- **NATGAS:** its xyz perp charges ~45%/yr funding and gave the same signal as its underlying only 12% of the time.
- **EUR and JPY:** FX wasn't part of the proposal.

## Rule (fixed, not tuned)

- **Entry:** EMA20 crosses above EMA50 at a daily close; buy at the next open.
- **Stops:** initial stop 2 ATR below the signal close (as live), then a 3 ATR trailing stop under the highest high.
- **Exit:** at the close when EMA20 is back below EMA50, or by the stop. **No time limit.**
- **Re-entry:** only on the next cross up. A stop-out in a still-rising trend stays out.
- **Sizing and costs:** 1.0% risk per trade, the crypto book's fees, and each instrument's mean xyz funding (measured in the TradFi study) charged every day held, including in years before xyz existed.
- **Book:** its own book has 3 slots. In the combined book it gets **1 slot of its own** next to crypto's 3.

## Adoption bar (all must pass)

| # | Window | Bar |
|---|---|---|
| 1 | 2001–2015 | ≥ 60 trades |
| 2 | 2001–2015 | PF ≥ 1.3 |
| 3 | 2001–2015 | PF ≥ 1.1 in both halves of its own trades |
| 4 | 2016 → now | PF ≥ 1.2 (confirmation) |
| 5 | 2023-06 → now | crypto live book + this sleeve on 1 slot: Sharpe above crypto alone, max drawdown no more than 2 points worse |

There's **no random-entry bar**, deliberately. The sleeve's job is diversification: exposure to trends in markets that don't move with crypto. The previous study already established that timing doesn't beat random uptrend entries on these instruments. What's asked is whether trend exposure itself pays, out of sample, and whether it helps the book.

**If it passes:** a live trend sleeve with `max_tradfi: 1`, alerts on crosses, and a kill rule fixed before the first live trade. **If it fails:** TradFi stays watch-only, and this line of inquiry closes.

## Also reported, with no bar

- **Comparison:** an equal-weight buy-and-hold of the same instruments, over the same window.
- **Per instrument:** trades and PF.
- **Exposure:** time in the market.
- **Correlation:** with crypto's daily P&L.
- **Slots:** the 2-slot version of rule 5.
- **Bad years:** each year's P&L, including 2008 and 2022.

## Stated in advance

- This is the third TradFi-related test in two studies; a pass is weaker evidence than a first test would be.
- Trend-following on futures has a long published record, so a pass would be unsurprising. A fail on 2001–2015 would be informative.

## Results

**Run 2026-10-16** with `python -m hlg.macrotrend`. Full output: `backtest_out/macro_trend_study.md`.

| Window | Trades | Win | PF | CAGR | Max DD | Sharpe | Buy-and-hold CAGR / DD |
|---|---:|---:|---:|---:|---:|---:|---|
| **2001–2015 (primary, unseen)** | 193 | 40% | **1.16** | 1.0% | −18.0% | 0.19 | 8.1% / −51.9% |
| 2016 → now (confirmation, seen) | 156 | 47% | 1.95 | 5.2% | −7.0% | 0.66 | 16.7% / −37.5% |

| With the crypto live book, 2023-06 → now | Trades (macro) | PF | Return | Max DD | Sharpe |
|---|---:|---:|---:|---:|---:|
| crypto alone | 409 | 1.40 | +117% | −18.5% | 1.07 |
| + macro trend, **1 slot** (the bar) | 429 (20) | 1.37 | +112% | −19.3% | **1.03** |
| + macro trend, 2 slots (no bar) | 440 (31) | 1.44 | +146% | −18.9% | 1.16 |

| Bar | Result | |
|---|---|---|
| 1. 2001–2015: ≥ 60 trades | 193 | pass |
| 2. 2001–2015: PF ≥ 1.3 | 1.16 | **fail** |
| 3. 2001–2015: PF ≥ 1.1 in both halves | 1.30 / 1.05 | **fail** |
| 4. 2016 → now: PF ≥ 1.2 | 1.95 | pass |
| 5. Book Sharpe above crypto alone, DD within 2pp | 1.03 vs 1.07 | **fail** |

**Not adopted.** TradFi stays watch-only, and this line of inquiry closes, as fixed in advance.

## Reading

1. **The untouched years said no.**
   - **2001–2015:** PF 1.16, about 1% a year, Sharpe 0.19, and the second half only 1.05.
   - **2016 → now:** PF 1.95, but these are the years that prompted the idea.
   - The gap between the two is the hindsight effect this design was built to catch. Testing on 2016 → now alone would have passed it.
2. **Per instrument, the swings are large.**
   - **Silver:** 0.30 in 2001–2015 against 1.65 since.
   - **Copper:** 1.36 against 0.72.
   - **Good in both windows:** oil, Brent and the S&P 500 (1.50–4.57). The Nasdaq-100 and gold were weak in 2001–2015 (1.23, 1.16) and strong since.

   Choosing just those now would be picking winners after the fact.
3. **It also didn't help the book on its pre-set slot.**
   - **1 slot:** Sharpe dropped from 1.07 to 1.03.
   - **2 slots:** 1.16. That configuration had no bar, so it can't rescue the result.
4. **What it does well is avoid crashes.**
   - **Drawdown:** −18% in 2001–2015, where buy-and-hold fell −52% (2008).
   - **Correlation with crypto:** 0.01.

   But at ~1% a year over 15 years, that protection costs most of the return. It isn't worth a slot next to a crypto book making far more.
