# Wallet behavior replication: pre-registration

**Status:** pre-registered 2026-10-14, **run 2026-10-14: H1–H5 all pass** (see Results). This file fixes the hypotheses and bars before any data is fetched. Results will be appended below without editing this section.

## Why

The [wallet behavior study](wallet-behavior-study.md) was exploratory: one sample (seed 42), with cut-offs chosen while writing the analysis. Its figures are the evidence the product plan rests on ([product.md](../product.md), "Validation plan"), and they would be quoted in public content. A figure may be quoted publicly **only if it passes its bar here**. When quoted, it's the replication's value, not the original's.

## Method (fixed)

The method is the same as the original study, with three changes:

| | Original | Replication |
|---|---|---|
| Seed | 42 | **2026** |
| Sample | 200 wallets | 200 wallets, **excluding the original 200** |
| Window | 90 days to 2026-09-25 | 90 days to the run date |

- **Unchanged:**
  - pool filters: monthly volume $20k–$20M, account value ≤ $1M;
  - position rebuild, and the 0.5% averaging-down threshold;
  - stop detection, liquidation detection, and the bot filter;
  - wallets need ≥ 5 closed positions.
- **Script changes allowed:** only parameters (the seed, an exclusion list, and the output path). The analysis code is not touched. The raw data stays in the gitignored `miner_cache/`.
- **Window overlap:** if the run happens before 2026-12-24, the window overlaps the original's. The new wallets make it an independent sample of traders, not of time. This will be stated with the results.

## Hypotheses and bars

| # | Claim it would support | Measure | Bar to pass |
|---|---|---|---|
| H1 | "A large share of active HL traders get liquidated" | wallets liquidated at least once in 90 days | ≥ 35% |
| H2 | "Liquidations happen to unstopped positions" | liquidated positions with no stop (where known) | ≥ 85% |
| H3 | "A few positions do most of the damage" | per-wallet median share of losses from the worst 5% of positions | ≥ 40% |
| H4 | "Most traders lose money" | wallets unprofitable over 90 days after fees | ≥ 50% |
| H5 | "Averaging down is where losses concentrate" | wallets where averaged-down positions' share of losses is ≥ 2× their share of positions | > 50% of wallets with ≥ 1 averaged-down position |

- **Each hypothesis passes or fails on its own.** A failed one isn't re-tested on another sample to rescue it.
- **Also reported, with no bar:** the original study's remaining table rows, so the two runs can be compared.

## Known limits (accepted in advance)

- **Survivorship.** The pool filter requires recent volume, so traders who blew up and quit are under-sampled. That biases H1 and H4 *downwards*, so a pass is conservative.
- **Stop coverage.** Order history is capped at 2,000, so H2 is judged only on positions where coverage reaches the open.
- **Generalisation.** One exchange, one period. The results describe HL traders in 2026, not traders in general.

## Results

**Run 2026-10-14.** Seed 2026, 200 new wallets from a pool of 13,745 (the original 200 excluded), 90 days to 2026-10-14. 138 analysed: 61 had fewer than 5 closed positions and 1 was bot-like; 15,622 closed positions. The window overlaps the original's (it was run before 2026-12-24), so this is an independent sample of **traders**, not of time.

Scripts: `scripts/wallet_behavior_study.py` with parameters only (`--seed`, `--cache`, `--exclude`, `--out`; analysis unchanged), and `scripts/wallet_replication_check.py`, which scores H1–H5 with the study's own position rebuild. Run on the original sample, the checker reproduces its published figures exactly. Raw data stays in the gitignored `miner_cache/wallet_study_rep/`.

| # | Measure | Bar | Replication | Original | Verdict |
|---|---|---|---:|---:|---|
| H1 | Wallets liquidated at least once in 90 days | ≥ 35% | **48%** | 48% | **pass** |
| H2 | Liquidated positions with no stop (321 with coverage) | ≥ 85% | **90%** | 96% | **pass** |
| H3 | Per-wallet median share of losses from the worst 5% of positions | ≥ 40% | **47%** | 48% | **pass** |
| H4 | Wallets unprofitable over 90 days after fees | ≥ 50% | **51%** | 60% | **pass, barely** |
| H5 | Wallets whose averaged-down positions carry ≥ 2× their share of losses (113 wallets) | > 50% | **65%** | 61% | **pass** |

**Quotable**, at the replication's values: about half of active HL wallets were liquidated in 90 days; 90% of liquidated positions had no stop; the worst 5% of a wallet's positions carried a median 47% of its losses; about half of wallets lost money after fees; in two-thirds of the wallets that average down, those positions carry at least twice their share of losses.

**Say carefully:** H4 cleared its bar by one point (51%). "About half" is honest; "most traders lose" is not.

### The other rows (no bar)

| Row | Replication | Original |
|---|---|---|
| Wallets that averaged down at least once | 83% | — |
| Positions averaged down / their share of losses (per-wallet median) | 14% / 45% | 14% / 45% |
| Worst 1% of positions flagged by at least one rule | 83% | 83% |
| Worst 1% of positions with no stop | 76% | 77% |
| Liquidations' share of all losses | 32% | — |
| Positions closed within 24h | 85% (win 45% vs 55% ≥ 24h) | 87% (47% vs 53%) |

**The habit paradox replicates, and more strongly:**

| Group | Wallets | Profitable | Median 90-day P&L |
|---|---|---|---|
| Average down on < 5% of positions | 40 | 20% | −$1,090 |
| On 5–20% | 54 | 52% | +$656 |
| On ≥ 20% | 44 | 70% | +$2,048 |
| Stops on ≥ 50% of positions | 42 | 36% | −$811 |
| Stops on < 10% | 58 | 55% | +$711 |

In both samples, wallets that used stops or rarely averaged down were *less* often profitable. The original study's caveats apply (trading style, survivorship, stops realising drawdowns, small groups), and none is tested. Two samples now agree, so it can't be put down to one unlucky draw.

## What this means for the product

- **The protection claim holds.** Liquidations are common, they are almost always unstopped, and a few positions do most of the damage. "Know before a position ends your account" rests on replicated figures.
- **A profitability claim would not hold.** Twice now, following the habits has *not* gone with being profitable. Public content must not say or imply that stops or not averaging down make a trader profitable. The draft posts in `docs/marketing/` should quote only H1–H3 and H5, and H4 as "about half".
- **Worth a later study, not assumed:** why averaging-down wallets are more often profitable. Candidates are market-making or grid styles, survivorship, and sizing. A pre-registered look at style, for example holding time and position count, would tell the guardrails' audience apart from traders the rules don't fit.
