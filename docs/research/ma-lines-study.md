# Long-term moving averages (50-week, 200-day) as entry filters

**Status:** pre-registered 2026-09-25, before the first run; run the same day: **nothing adopted** (below). The results section is added after the run
and the rules above it are not edited.

## Why

Benjamin Cowen's market commentary leans on a few long-term lines: price against the **50-week**
moving average and the **200-day** moving average, simple or exponential. The usual reading is that
above them the market is in a bull phase and dips get bought, and below them rallies fade. If that
holds for this strategy, breakouts taken below the lines should do worse than those taken above.

One version was tested before: `btc_bull`, BTC above its 200-period EMA. It made results worse
(README "Macro", 3rd percentile vs random drops). But on 4h bars that EMA spanned 200 × 4h ≈ 33
days, not 200 days. This study computes every line from **daily and weekly bars** on both intervals.

## Lines (fixed now)

From daily closes (`bars(..., "1d")`):
- `sma200d` and `ema200d`: 200-day simple / exponential average of the daily close;
- `sma50w` and `ema50w`: 50-week simple / exponential average of the **weekly close** (the week ends
  with Sunday's daily bar, i.e. Monday 00:00 UTC).

**No lookahead:** a signal bar is compared with the line as of the last daily or weekly close at or
before that bar's own close. The comparison is the signal bar's close against that value. An EMA is
not used until it has had as many bars as its span (200 days, 50 weeks); before that it reads "no
opinion" and the filter lets the trade through, like every other filter here.

## Filters (fixed now)

| filter | passes when | role |
|---|---|---|
| `btc_above_sma50w` | BTC's close above its 50-week SMA | candidate |
| `btc_above_ema50w` | BTC's close above its 50-week EMA | candidate |
| `btc_above_sma200d` | BTC's close above its 200-day SMA | candidate |
| `btc_above_ema200d` | BTC's close above its 200-day EMA | candidate (a clean re-run of `btc_bull`) |
| `coin_above_sma50w` | the coin's own close above its 50-week SMA | candidate |
| `coin_above_sma200d` | the coin's own close above its 200-day SMA | candidate |

## Test and adoption bar (fixed now)

- **Engine:** `breakout_long` on the 15 backtest coins, 2023-06-01 → now, run separately on **1d** and
  **4h** (`_entry_filter_test`).
- **The bar every entry filter has met:**
  - PF ≥ base + 0.10;
  - max drawdown no more than 2 pp worse;
  - PF in each half ≥ the base's;
  - above the 95th percentile of randomly dropping the same number of trades.
- **Multiple comparisons:** 6 filters × 2 intervals = 12 tests, so a single pass is expected by
  chance about half the time. A filter is **adopted only if it passes on both 1d and 4h**.
- **Descriptive, not a verdict:** the base trades split by each line (above / below) with trades, win
  rate and PF, to show whether "below the line" breakouts are really worse.

**If adopted:** the filter joins the scanner, and signals below the line become dashboard-only notes.
**If not:** the lines may still be shown as context on the dashboard, labelled as not an edge for this
strategy, with the numbers from this study.

Run: `python -m hlg.backtest --ma-study`

## Results (run 2026-09-25)

`python -m hlg.backtest --ma-study` (5.5 min; 4h bars on Hyperliquid start 2024-06).

**Entry-filter test.** PF, with the random-subset percentile in brackets:

| filter | 1d (base 126 trades, PF 1.67) | 4h (base 405 trades, PF 1.39) | verdict |
|---|---|---|---|
| `btc_above_sma50w` | 1.44 (8th) | 1.27 (22nd) | fail, worse on both |
| `btc_above_ema50w` | 1.50 (14th) | 1.26 (17th) | fail, worse on both |
| `btc_above_sma200d` | 1.48 (15th) | 1.44 (62nd) | fail |
| `btc_above_ema200d` | 1.38 (3rd) | 1.30 (25th) | fail, worse on both |
| `coin_above_sma50w` | 1.68 (51st) | **1.61 (95th), DD −29.9% vs −40.4%, both halves better** | pass on 4h only, so **not adopted** |
| `coin_above_sma200d` | 1.65 (35th) | 1.48 (72nd), DD −29.7% | fail |

**Adopted: none** (the rule needed a pass on both 1d and 4h).

**The BTC lines work backwards for this strategy.** Breakouts taken while BTC was *below* its line did
better on every line and both timeframes:
- 1d below: PF 2.07–3.08 on 20–26 trades, vs 1.40–1.57 above;
- 4h below: PF 1.54–1.63 on 133–174 trades, vs 1.16–1.20 above.

That matches the earlier findings: breakouts in credit stress made 63% of the profit, and higher Fear &
Greed went with better, not worse, forward returns. A coin breaking out to a 20-bar high while BTC is
still under its long-term lines is showing relative strength, and those were among the best trades.

**The coin's own lines are the one lead.** On 4h, breakouts on coins above their own 50-week SMA did
better than those below:
- 50-week SMA: PF 1.67 on 201 trades above, vs 1.14 on 163 below;
- 200-day SMA: PF 1.63 above vs 1.10 below.

Why it isn't adopted:
- the filter cleared the random-drop bar by the narrowest margin (95th percentile);
- it does nothing on 1d (PF 1.68 vs 1.67);
- 1 pass in 12 tests is roughly what chance produces.

It is a hypothesis to watch, not a rule.
