# Wallet style study: why are averaging-down wallets more often profitable? — pre-registration

**Status:** pre-registered 2026-10-15, before any of the measures below were computed; **run 2026-10-15** (see Results). The results
are appended underneath without editing this section.

## Why

In both wallet samples ([study](wallet-behavior-study.md), [replication](wallet-behavior-replication.md)), wallets that
averaged down *more* often were *more* often profitable. Wallets that used stops on most positions
were *less* often profitable. At the same time, averaged-down positions carried at least twice their
share of losses in about two-thirds of the wallets that average down. Both can be true if the habits
mark a **trading style** rather than cause the result. The guardrails' public claim depends on which it
is.

## Data (fixed)

- **Samples:** both existing samples, pooled (seed 42 and seed 2026, 283 analysed wallets), with
  each also reported on its own. No new fetch.
- **Rules:** the study's own position rebuild, filters and thresholds (`scripts/wallet_behavior_study.py`),
  unchanged.
- **Outcome:** a wallet is profitable if its 90-day P&L after fees is > 0.

## Style measures (per wallet, fixed)

| Measure | Definition |
|---|---|
| `ad_rate` | share of closed positions averaged down (the study's definition) |
| `add_rate` | share of closed positions with at least one add, at any price |
| `stop_rate` | share with a reduce-only stop, where coverage is known |
| `activity` | log(closed positions per day) |
| `hold` | log(median holding time, hours) |
| `maker` | share of fills that were maker (`crossed` false) |

## Tests and bars

**T1. Does style explain the averaging-down link?**
- **Method:** logistic regression of profitable on `ad_rate` alone (β), then with `add_rate`,
  `activity`, `hold` and `maker` added (β′). All inputs standardised; 1,000 bootstrap resamples of
  wallets.
- **Explained by style** if |β′| ≤ 0.5·|β| *and* the 95% CI of β′ includes 0.
- **Averaging down itself goes with profit** if the CI of β′ is entirely above 0.
- **Otherwise:** partly explained.

**T2. Does style explain the stop link?** The same test, with `stop_rate` in place of `ad_rate`, on
wallets with stop coverage.

**T3. Within the same style, does averaging down still hurt?**
- **Population:** positions with at least one add. Compare averaged-down positions with positions
  added to *without* averaging down, within each wallet that has ≥ 3 of each.
- **Hurts within style** if per-wallet mean P&L of averaged-down positions is below the other added
  positions in a significant majority of wallets (one-sided sign test, p < 0.05).

**T4. Do averaging-down wallets carry more tail risk?**
- **Groups:** the top and bottom thirds of wallets by `ad_rate`.
- **Supported** if, in *both* samples, the top third has a higher liquidation rate *and* a higher
  median share of losses from its worst 5% of positions.

## Also reported, with no bar

- each style measure's correlation with profitability;
- profitability by `add_rate` third;
- the same models on each sample separately.

## Known limits (accepted in advance)

- **Survivorship:** only wallets still trading are sampled. Traders who averaged down into a wipe-out
  and left are missing, which flatters averaging down. This can't be tested with this data.
- **Association, not cause:** none of this proves what a given trader should do.
- **Small n:** 283 wallets, which makes these coarse tests.

## Results

**Run 2026-10-15** with `python scripts/wallet_style_study.py`, on 283 wallets (145 + 138). Aggregates only.

| Test | Result | Verdict |
|---|---|---|
| **T1** averaging down vs profitable | β +0.45 alone → **+0.13 with style** (95% CI −0.20 to +0.49). Same in each sample (+0.28 → +0.08; +0.62 → +0.16) | **explained by style** |
| **T2** stops vs profitable | β −0.31 alone → −0.18 with style (CI −0.49 to +0.09). Smaller, but by less than half | **partly explained** |
| **T3** averaging down within the same wallet | In **132 of 179 wallets (74%)**, averaged-down positions made less on average than the same wallet's other positions that were added to; p ≈ 8×10⁻¹¹ | **hurts within style** |
| **T4** tail risk of high averagers | Original: the top third was liquidated more (54% vs 40%) and its worst 5% held more of its losses (56% vs 38%). Replication: liquidated *less* (39% vs 54%), worst-5% share higher (63% vs 38%) | **not supported** (both samples needed) |

**What the style is** (pooled; no bar):

| Measure | Profitable wallets (median) | Unprofitable (median) | Spearman |
|---|---:|---:|---:|
| Median holding time | **10.8 h** | 3.6 h | +0.28 |
| Maker share of fills | **57%** | 22% | +0.22 |
| Share of positions added to (any price) | 60% | 46% | +0.21 |
| Averaged down | 15% | 7% | +0.26 |
| Stops (where known) | 6% | 25% | −0.16 |
| Positions a day | 0.51 | 0.66 | −0.10 |

In the averaging-down model with style included, holding time (+0.41) and maker share (+0.26) carry most of the weight.

**T3 robustness (added after the run, not a pre-registered test).** T3 compares mean P&L in dollars. Averaged-down positions had about twice as many adds (median 8.7 vs 4.2 a position), so they were likely larger, and part of the dollar gap is size. Win rate doesn't depend on size. On win rate, averaged-down positions won less often than the same wallet's other added-to positions in **119 of 176 wallets (68%)**, p ≈ 2×10⁻⁶ (wallets with tied rates left out). The direction holds; the dollar figure overstates it somewhat.

## Reading

1. **The paradox comes from style, not from averaging down.** The profitable wallets hold for hours instead of minutes and mostly use resting (maker) orders, scaling in and out with limits. That style also adds to positions often, including below the average, so "averages down" ends up marking the style. Once style is accounted for, averaging down no longer goes with profit in either sample.
2. **Within a trader, averaging down still hurts.** Comparing a wallet's averaged-down positions with its other added-to positions, the averaged-down ones made less in three wallets out of four, and won less often in two out of three. This is the cleanest test in either study, and it supports the guardrail directly: for the same trader, adding below the average entry is the worse trade.
3. **Stops are only partly explained.** Stop users tend to be short-hold takers, the losing style, and that accounts for some of the link but not all. A stop realises a loss that might have recovered, which wouldn't show up as style. As before, there's no evidence that stops make a trader profitable. Their case is the liquidation data: 90–96% of liquidated positions were unstopped.
4. **"Averaging down blows up accounts" is not shown at wallet level.** The heavy averagers' tail was worse in both samples (63% vs 38% and 56% vs 38% of losses from the worst 5%), but their liquidation rate went opposite ways in the two samples.

## What this means

- **For the guardrails:** the averaging-down warning stands on T3. It compares like with like, a trader against their own other adds. No change.
- **For public content:** a claim like "in about 2 of 3 wallets, a trader's averaged-down positions won less often than their other adds" is now supported. That uses the size-free version. "Traders who average down lose more" is not; the opposite association holds, and it comes from style.
- **For the product:** the traders who are profitable here are patient, maker-heavy and scale in. The guardrails' audience is the other group: short-hold takers without a plan, who are also the ones being liquidated.
