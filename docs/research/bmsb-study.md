# Bull market support band (20-week SMA / 21-week EMA) as an entry filter, vs the 50-week SMA

**Status:** pre-registered 2026-09-25, before the first run; run the same day: **nothing adopted, neither line clearly more relevant** (below). The results section is added after the run
and the rules above it are not edited.

## Why

Benjamin Cowen's "bull market support band" (BMSB) is the zone between the **20-week SMA** and the
**21-week EMA**. The reading: above it, the bull trend is intact; losing it is a warning. The previous
study (docs/research/ma-lines-study.md) tested the 50-week and 200-day lines. The BTC lines made
results worse. The coin's own 50-week SMA passed on 4h only, so it is being tracked live. This study
asks the same question of the faster band, and which of the two lines is more relevant here.

## Lines (fixed now)

Same construction and no-lookahead rule as the ma-lines study (`hlg/lines.py`):
- weekly closes end with Sunday's daily bar (known Monday 00:00 UTC);
- a signal bar is compared with the line as of its own close;
- an EMA reads "no opinion" until warm (21 weeks).

- `sma20w`: 20-week SMA of the weekly close; `ema21w`: 21-week EMA.
- **Above the band** = the close is above *both* lines (above the band's upper edge).

## Filters (fixed now)

| filter | passes when | role |
|---|---|---|
| `btc_above_bmsb` | BTC's close above both band lines | candidate |
| `coin_above_bmsb` | the coin's own close above both of its band lines | candidate |
| `btc_above_sma20w` | BTC's close above its 20-week SMA | sensitivity (one line alone) |
| `coin_above_sma20w` | the coin's close above its 20-week SMA | sensitivity |
| `coin_above_sma50w`, `btc_above_sma50w` | as in the ma-lines study | reference only, for the comparison; not re-judged |

## Test and adoption bar (fixed now)

- **The test:** `breakout_long`, 15 backtest coins, 2023-06-01 → now, on 1d and 4h.
- **The usual entry-filter bar:**
  - PF ≥ base + 0.10;
  - drawdown no more than 2 pp worse;
  - each half ≥ the base's PF;
  - above the 95th percentile of randomly dropping the same number of trades.
- **Adoption needs a pass on both 1d and 4h.** Only the two candidates can be adopted (4 tests). The
  sensitivity rows are shown, not judged.
- **"Which is more relevant"** is answered descriptively, and only among lines that were *not*
  worse than baseline. The measure is the split of the base trades above vs below each line (trades,
  PF) and how much of the book each line would remove. A line is called "more relevant" only if its
  above/below PF gap is larger *on both intervals*. Otherwise the answer is "neither is clearly more
  relevant".

Run: `python -m hlg.backtest --bmsb-study`

## Results (run 2026-09-25)

`python -m hlg.backtest --bmsb-study`.

**Filter test.** PF, with the random-subset percentile in brackets:

| filter | 1d (base PF 1.67) | 4h (base PF 1.39) | verdict |
|---|---|---|---|
| `btc_above_bmsb` | 1.82 (80th) | 1.38 (49th) | fail |
| `coin_above_bmsb` | 1.70 (66th) | 1.40 (52nd), DD −31% vs −40% | fail |
| `btc_above_sma20w` | 1.76 (69th) | 1.53 (79th) | not judged (sensitivity) |
| `coin_above_sma20w` | 1.68 (50th) | 1.49 (77th) | not judged |
| `btc_above_sma50w` | 1.44 (8th) | 1.27 (22nd) | reference: worse on both |
| `coin_above_sma50w` | 1.68 (51st) | 1.61 (95th) | reference: the tracked lead |

**Adopted: none.**

**Base trades split by side of each line** (PF above vs below; trades below in brackets):

| line | 1d | 4h |
|---|---|---|
| BTC vs its band | 1.67 vs 1.66 (27) | 1.36 vs 1.36 (187) |
| BTC vs its 50-week SMA | 1.51 vs 2.46 (20) | 1.20 vs 1.54 (148) |
| coin vs its band | 1.58 vs 2.88 (9) | 1.62 vs 1.10 (165) |
| coin vs its 50-week SMA | 1.49 vs 3.46 (15) | 1.67 vs 1.14 (163) |

**Which is more relevant, by the rule set in advance:** neither, clearly. Only the coin-level lines
were not worse than baseline. Their gap on 4h is the same (+0.52 band, +0.53 50-week), and on 1d
both reverse on too few trades (9 and 15). What the numbers do say:
- **For BTC, the band carries no information, and that's still better than the 50-week line.**
  Breakouts did the same with BTC above or below its band (4h PF 1.36 vs 1.36). Gating on BTC's
  50-week SMA was actively harmful.
- **For the coin itself, the band and the 50-week SMA are one signal.** Both say "this coin is in a
  long-term uptrend", and on 4h both split breakouts the same way (~1.6 vs ~1.1).
- **The split doesn't survive as a filter for the band.** Filtering on the coin's band left 4h PF at
  1.40 (52nd percentile), even though the trades above it did better. Removing entries frees slots
  for other, often weaker, signals, so a split that looks good doesn't guarantee a filter that
  works. Only the 50-week version held up as a filter, and only just (95th percentile).
