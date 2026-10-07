# The MA pullback on momentum movers only

**Status:** pre-registered 2026-10-08, before the first run; run the same day: **not adopted** (below). The results section is added after the run
and the rules above it are not edited.

## Why

The first test of Emmanuel Malyarovich's 9 EMA / 20 SMA / 200 SMA pullback
([ma-pullback-study.md](ma-pullback-study.md)) failed. It ran on a fixed list of 15 large coins
every day. He says the method is for **stocks that are gaining momentum and clearly trending**,
usually with a news catalyst: he picks the day's few big movers out of thousands. The trend part
was already in the test (EMA9 > SMA20, a rising 20 SMA, not extended). What it lacked was **the
selection**. This study adds a crypto version of it.

## What changes (fixed now)

The signal, the entry (a stop order past the bar), the stop, the 200 SMA rules and the exit (a close
back through the 20 SMA) are **identical** to `ma_pullback`. Only the instruments differ:

1. **A wider, point-in-time pool.** Each day, the **top 50** Hyperliquid perps by trailing 30-day
   median daily notional:
   - at least $10M a day;
   - listed for at least 60 days;
   - **delisted coins included** while they traded (`eligibility()`, as in the universe study);
   - the xyz TradFi dex excluded.
2. **Only the movers.** Among that day's eligible coins, rank the 20-bar return (on the bar's own
   timeframe):
   - a **long** needs the coin in the **top 20%** (`mom_rank` ≥ 0.8);
   - a **short** needs it in the **bottom 20%** (`mom_rank` ≤ 0.2).
3. **A catalyst stand-in.** Volume of at least 1.5× its 20-bar average on at least one of the last 5
   bars, signal bar included (`vol_spike`). Something happened.

Every input uses data up to the signal bar only. Liquidity is shifted a day, as in the universe
study.

## Variants (fixed now)

| variant | role |
|---|---|
| `ma_mom` | **candidate**: `ma_pullback`, both sides, with the momentum and volume selection, on the top-50 pool |
| `ma_top50` | `ma_pullback` on the same pool, no selection. Isolates what the selection adds |
| `ma_mom_long`, `ma_mom_short` | each side on its own, reference |
| `breakout_long` on the top-50 pool | reference |

## Windows

- **A:** 2023-06-01 → now, on 1d and 4h. The 4h history starts 2024-06.
- **B (holdout):** 1d, 2021-04-01 → 2023-05-31. Only coins with daily history then take part.
  There's no funding data there.

There is no 1h run: the pool's 1h history is too short. The costs are as before: Hyperliquid fees
and funding, 1.5% risk, 3 slots, most-liquid first.

## Adoption bar (fixed now)

`ma_mom` becomes an alert only if **all** of these hold:

1. **Enough trades:** at least 100 in A-1d and A-4h, and at least 30 in B-1d.
2. **Profit factor:** PF ≥ 1.3 in A-1d, A-4h and B-1d.
3. **Both halves profitable:** PF > 1.0 in both halves of its own trades (median entry date), A-1d and
   A-4h.
4. **The selection adds something:** its PF is above the 95th percentile of 20,000 random same-size
   subsets of `ma_top50`'s trades, in A-1d and A-4h.
5. **Improves the live book:** the live book is the 15-coin 1d + 4h breakout longs at 1.0% risk, 3
   shared slots, window A. Adding `ma_mom` on the top-50 pool to it must leave Sharpe ≥ the live
   book's and max drawdown no more than 2pp worse.

**Nothing else is adoptable from this run,** not the daily long side, not the 1d-only result, not a
different percentile cutoff.

## Limits

- **No overnight gaps.** Crypto trades 24/7, so the "gapped up 30% on earnings" setup has no clean
  equivalent. The volume spike is a stand-in, not the same thing.
- **No minute charts.** His 1–15 minute execution can't be tested on Hyperliquid's candle history.
- **No discretion.** Judgment isn't in a rule.

Run: `python -m hlg.backtest --ma-momentum-study`

## Results (run 2026-10-08)

**Not adopted. All five rules fail**, although rules 2–5 fail on window A alone, without counting
the holdout.

| window | variant | trades | win rate | PF | avg R | both halves PF | max DD |
|---|---|---:|---:|---:|---:|---|---:|
| A, 1d | **ma_mom** | 124 | 0.28 | **1.16** | +0.15 | 1.49 / 0.87 | −36% |
| | ma_top50 (no selection) | 347 | 0.22 | 0.85 | −0.07 | 0.77 / 0.95 | −57% |
| | breakout_long, same pool | 103 | 0.39 | 1.38 | +0.19 | 1.49 / 1.26 | −13% |
| A, 4h | **ma_mom** | 727 | 0.26 | **1.08** | +0.10 | 1.05 / 1.10 | −48% |
| | ma_top50 (no selection) | 1,635 | 0.23 | 0.92 | −0.03 | 0.93 / 0.90 | −89% |
| | breakout_long, same pool | 399 | 0.32 | 1.06 | +0.05 | 1.14 / 1.00 | −36% |

- **The selection effect:** against random same-size subsets of `ma_top50`, `ma_mom` sits at the 90th
  percentile on 1d and the 94th on 4h. Neither reaches the 95th.
- **Each side on its own:** long PF 1.32 on 1d and 0.95 on 4h; short PF 0.96 on 1d and 1.16 on 4h.
  The two timeframes contradict each other, which is what noise looks like.
- **Added to the live book** (407 ma_mom trades): Sharpe went from 1.03 to 1.06, but PF fell from
  1.39 to 1.28. Max drawdown widened from −18.5% to **−28.1%** and the worst day from −4.8% to −8.2%.
  That fails the drawdown bar.

**The holdout couldn't run, and the design should have caught that.** Hyperliquid launched in 2023.
Its backfilled 2021–22 candles carry almost no volume, so only 3 coins ever met the $10M bar there,
and `ma_mom` made 0 trades. Rule 1 therefore failed by construction. No other rule needed B to fail.

**What it says:**
- **You were right that selection matters.** Restricting to momentum movers turned a loser into a
  small winner: PF went from 0.85 to 1.16 on 1d and from 0.92 to 1.08 on 4h. That's close to
  significant, but it isn't, and it isn't enough. It still trails the breakout rule on the same
  pool, with two to three times the drawdown.
- **The pullback entry isn't the edge here.** Even on the movers, a stop at the pullback bar, a 22–28%
  win rate and an exit at the 20 SMA leave very little after costs, at 1d and 4h on crypto perps.
  Whatever makes it work for him (news-gap stocks, 1–15 minute timing, discretion) didn't carry over.

Re-run: `python -m hlg.backtest --ma-momentum-study` (writes `backtest_out/ma_momentum_study.md`;
it fetches the whole perp pool on first run).
