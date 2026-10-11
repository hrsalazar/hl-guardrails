# Rally shorts: sell the failed bounce in a downtrend

**Status:** pre-registered 2026-10-12, before the first run; run the same day: **not adopted** (below). The results section is added after the run
and the rules above it are not edited.

## Why

The breakdown-short study (docs/research/short-study.md) found that since 2023, closes below the
20-bar low mostly snapped back. Breakdown shorts lost money even when BTC was in a downtrend (PF
0.74 on 1d, 0.68 on 4h). The opposite entry is the natural next question: **short the bounce that
fails**, not the breakdown. In a downtrend, price rallies into resistance, gets rejected, and turns
back down. Entering there puts the stop just above the rally's high instead of 2 ATR above a
breakdown that is about to snap back.

## The rule (fixed now)

**Rally short** (`short_level: rally`), on a closed bar:
- **Trend:** the coin is in a downtrend, EMA20 < EMA50.
- **The bounce reached resistance:** the bar's **high touched or crossed EMA20**.
- **And was rejected:** the bar **closed below EMA20**, in the **lower half of its range**
  (close location ≤ 0.5).
- **Entry:** short at the next bar's open.
- **Stop:** the signal bar's high + 0.5 ATR.
- **Exit:** trailed 3 ATR above the lowest low, a 21-day limit, no target. These are the same
  mechanics as the longs and the earlier shorts.

## Variants (fixed now)

| variant | gate | role |
|---|---|---|
| `rally_bear` | BTC's daily close below its 200-day EMA (`btc_down`, as in the short study) | **candidate 1** |
| `rally_crowded` | `btc_down` **and** funding ≥ 20% APR at the signal (longs paying to stay in; Hyperliquid's neutral rate is ~11%) | **candidate 2** |
| `rally_any` | none: the rally short on every day | reference: the random-subset baseline |
| `long_bear` | the live long rule when `btc_down` | descriptive, as in the short study |

The setup is the same as the short study: the 15 backtest coins, 1.5% risk, 3 slots, Hyperliquid fees
and funding (shorts receive positive funding), and survivors only (biased against shorts).

## Windows (fixed now)

The same as the short study:
- **A:** 2023-06-01 → now, on 1d and 4h (4h history starts 2024-06).
- **B, the holdout:** 2021-04-01 → 2023-05-31, 1d only, no funding data. `rally_crowded` can't be
  judged in B without funding, so it is reported there, but B counts for `rally_bear` only.

## Adoption bar (fixed now)

A candidate is adopted **as an alert only** (like the longs: you decide; nothing trades by itself)
if **all** of the following hold:
1. **Enough trades:** ≥ 30 trades in A-1d, A-4h and (`rally_bear` only) B-1d.
2. **Profit factor:** PF ≥ 1.3 in the same windows.
3. **Both halves profitable:** PF > 1.0 in both halves of its own trades (split at the median entry
   date), in A-1d and A-4h.
4. **Better than chance:** in A-1d and A-4h, its PF sits above the **97.5th** percentile of 20,000
   random same-size subsets of `rally_any`. That's stricter than the usual 95th, because two
   candidates are tested.
5. **Improves the live book:** the live book is 1d + 4h longs, 3 shared slots, 1.0% risk. With the
   candidate's shorts added in window A, it must keep Sharpe ≥ the live book's, max drawdown no
   more than 2 pp worse, and PF no more than 0.05 lower.

**If neither passes, the scanner stays long-only.** The short study's finding then stands: in a
downtrend, the edge is fewer, better longs.

Run: `python -m hlg.backtest --rally-short-study`

## Results (run 2026-10-12)

**Neither candidate adopted. The scanner stays long-only.**

| window | variant | trades | win rate | PF | avg R | both halves PF |
|---|---|---:|---:|---:|---:|---|
| A, 1d (2023-06 → now) | **rally_bear** | 82 | 0.41 | **1.07** | +0.06 | 0.73 / 1.42 |
| | rally_crowded | 2 | 0.50 | 1.74 | +0.46 | — |
| | rally_any (baseline) | 187 | 0.41 | 1.30 | +0.16 | 1.28 / 1.31 |
| | long_bear | 25 | 0.52 | 2.82 | +0.58 | 1.37 / 4.27 |
| A, 4h (2024-06 → now) | **rally_bear** | 358 | 0.27 | **0.66** | −0.15 | 0.60 / 0.74 |
| | rally_crowded | 0 | — | — | — | — |
| | rally_any (baseline) | 667 | 0.30 | 0.96 | −0.00 | 0.86 / 1.05 |
| | long_bear | 164 | 0.37 | 1.59 | +0.28 | 1.25 / 1.84 |
| B, 1d holdout (2021-04 → 2023-05) | **rally_bear** | 77 | 0.43 | **1.42** | +0.26 | 3.15 / 0.69 |
| | rally_any (baseline) | 100 | 0.43 | 1.44 | +0.25 | 2.53 / 0.87 |
| | long_bear | 17 | 0.35 | 1.69 | +0.38 | 0.68 / 2.44 |

Against random same-size subsets of `rally_any`, `rally_bear` sits at the **19th percentile on 1d and
the 0th on 4h**. As with breakdown shorts, the BTC-downtrend gate made the shorts worse, not better.

**The live book with these shorts added** (1d + 4h, 1.0% risk, 3 slots, window A):

| | trades | shorts | PF | total return | max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| live book (longs) | 407 | 0 | 1.42 | +120% | −18.5% | 1.08 |
| + rally_bear | 563 | 214 | 1.06 | +18% | −40.9% | 0.32 |
| + rally_crowded | 407 | 2 | 1.43 | +124% | −18.5% | 1.11 |

**Verdicts:**
- `rally_bear` fails rules 2–5.
- `rally_crowded` fails rules 1–4. With 2 trades on 1d and none on 4h, it can't be judged, but it
  isn't evidence of an edge either: funding almost never reaches 20% APR while BTC is in a
  downtrend, because longs aren't crowded in a falling market.

**What it says:**
- **Two different short entries, both lost on the live window.** Breakdowns (short-study) and failed
  bounces (here) both lost, while the long rule kept working in the same downtrends: long_bear PF 2.82
  on 1d, 1.59 on 4h. Since 2023, crypto downtrends here have been choppy and squeeze-prone, not
  steady. The edge in them is fewer, better longs.
- **Noted, not adopted:** `rally_any`, the baseline with no BTC gate, made PF 1.30 on 1d with both
  halves above 1.0 and 1.44 in the holdout, but lost on 4h (0.96). It wasn't a candidate, so choosing
  it now would be selecting after the fact. If it's worth pursuing, it needs its own pre-registered
  test: 1d only, judged on data after 2026-10-12, or on a stricter bar.

Re-run: `python -m hlg.backtest --rally-short-study` (writes `backtest_out/rally_short_study.md`).
