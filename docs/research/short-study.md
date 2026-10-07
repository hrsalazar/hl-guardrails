# Breakdown shorts in a BTC downtrend

**Status:** pre-registered 2026-10-07, before the first run; run the same day: **not adopted** (below). The results section is added after the run
and the rules above it are not edited.

## Why

The scanner is long-only. The mirror short (`breakout_short`: close below the 20-bar low while
EMA20 < EMA50) was tested before and lost money, PF 0.88 (README "Shorting"). But it ran on every
day of a window that was mostly a bull market. The question now is narrower: **does the breakdown
short work when the market as a whole is in a downtrend?** If shorts only pay in bear phases, an
unconditional test would bury that edge under the bull-phase losses.

## The regime (fixed now)

`btc_down`: BTC's **daily** close below its **200-day EMA**, as of the previous daily close. It's the
same reading as the `trend` leg of the live market state (`hlg.market_state`, `regime_states`). It's
mapped to each signal bar by its UTC day, so a 4h bar uses the daily state known before that day
opened. There's no lookahead, and it reads "no opinion" before the EMA has 200 days.

## Variants (fixed now)

| variant | rule | role |
|---|---|---|
| `short_bear` | `breakout_short` (close < 20-bar low, EMA20 < EMA50, 2 ATR stop, 3 ATR trail, 21-day limit), only when `btc_down` | **candidate** |
| `breakout_short` | the same short on every day | reference (the earlier test, re-run) |
| `long_bear` | `breakout_long` only when `btc_down` | descriptive: what the live long rule does in the same phases |

The coins are the 15 used by every other backtest study (`DEF["coins"]`). The risk is 1.5%, as in
the single-timeframe studies, with 3 slots, Hyperliquid fees and funding (shorts receive positive
funding). Survivors only: coins that died aren't in the list, and dead coins are where shorts make
the most. So this setup is biased **against** shorts, and that is stated rather than corrected.

## Windows (fixed now)

- **A, the live window:** 2023-06-01 to now, on 1d and 4h. The 4h history starts 2024-06, which is
  the cap on Hyperliquid's candles.
- **B, the holdout:** 2021-04-01 to 2023-05-31, 1d only. Neither the long rule nor this study was
  designed on it, and it contains the 2022 bear market. Trades count in B by their entry date.
  Hyperliquid funding history doesn't reach most of B, so funding there is zero. That is a stated
  approximation: in the 2022 bear, funding mostly ran slightly negative, which costs shorts.

## Adoption bar (fixed now)

`short_bear` is adopted **as an alert only** (the same as the longs: you decide, nothing trades by
itself). That needs **all** of the following:

1. **Enough trades:** at least 30 trades in each of A-1d, A-4h and B-1d.
2. **Profit factor:** PF ≥ 1.3 in each of A-1d, A-4h and B-1d.
3. **Both halves profitable:** PF > 1.0 in both halves of its own trades, split at the median entry
   date, in A-1d and A-4h. Bear phases cluster at the end of window A, so a calendar midpoint
   would leave one half nearly empty.
4. **Better than chance:** in A-1d and A-4h, its PF sits above the 95th percentile of 20,000
   random same-size subsets of the unconditional `breakout_short` trades. That's the test of
   whether the regime adds anything.
5. **Improves the live book:** the live book is 1d and 4h longs sharing 3 slots at 1.0% risk.
   Adding `short_bear` shorts to it, same slots, window A, must leave Sharpe **≥** the live
   book's, max drawdown no more than 2pp worse, and PF no more than 0.05 lower.

If any of these fails, the scanner stays long-only. In a downtrend, the long rule's own answer is
fewer signals; the market-state study measured the rate.

Run: `python -m hlg.backtest --short-study`

## Results (run 2026-10-07)

**Not adopted: rules 2, 3, 4 and 5 fail.** The scanner stays long-only.

| window | variant | trades | win rate | PF | avg R | both halves PF |
|---|---|---:|---:|---:|---:|---|
| A, 1d (2023-06 → now) | **short_bear** | 51 | 0.33 | **0.74** | −0.12 | 0.17 / 1.43 |
| | breakout_short | 100 | 0.35 | 0.88 | −0.04 | 0.70 / 1.03 |
| | long_bear | 25 | 0.52 | 2.82 | +0.58 | 1.37 / 4.27 |
| A, 4h (2024-06 → now) | **short_bear** | 188 | 0.32 | **0.68** | −0.14 | 0.86 / 0.54 |
| | breakout_short | 367 | 0.34 | 0.91 | −0.04 | 0.85 / 0.96 |
| | long_bear | 164 | 0.37 | 1.59 | +0.28 | 1.26 / 1.84 |
| B, 1d holdout (2021-04 → 2023-05) | **short_bear** | 46 | 0.48 | **1.51** | +0.20 | 3.66 / 0.63 |
| | breakout_short | 56 | 0.57 | 1.89 | +0.28 | 3.51 / 1.02 |
| | long_bear | 17 | 0.35 | 1.69 | +0.38 | 0.68 / 2.44 |

BTC was below its 200-day EMA on 413 of 1,224 days in window A and on 468 of 791 in B.
Against random same-size subsets of the unconditional short, short_bear sits at the **25th
percentile on 1d and the 3rd on 4h.** Gating shorts on the downtrend made them *worse* than
shorting at random times.

**The live book with these shorts added** (1d + 4h, 1.0% risk, 3 slots, window A): 161 shorts were
taken. PF fell from 1.39 to 1.16, Sharpe from 1.03 to 0.58, the total return from +110% to +47%,
and the max drawdown widened from −18.5% to −27.4%.

**What it says:**
- **Breakdown shorts only paid in 2021–22.** In the 2022 bear (capitulations, LUNA, FTX),
  breakdowns kept going: PF 1.5–1.9. The last half of B was already 0.63. Since 2023, including
  the long 2025-11 → 2026-08 downtrend, breakdowns below the 20-bar low have mostly snapped back.
  Trend-following short entries were bought into, squeezed, and stopped out.
- **The long rule kept working in downtrends.** long_bear made PF 2.82 (1d) and 1.59 (4h). There
  are fewer signals, but the ones that fire are no worse. This matches the market-state study:
  in a downtrend the right response is the rule's own, fewer longs, not a short book.
- **Caveats:** survivors only (biased against shorts), and no funding in B. Neither changes the
  verdict on window A, where the funding *paid* the shorts (+0.3% and +1.1% of equity).

Re-run: `python -m hlg.backtest --short-study` (writes `backtest_out/short_study.md`).
