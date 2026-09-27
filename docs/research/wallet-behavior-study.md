# Wallet behavior study: do other Hyperliquid traders lose money the way the guardrails assume?

**Status:** exploratory, run 2026-09-25. **Not pre-registered:** the questions and cut-offs were chosen while writing the analysis, unlike the backtest studies. Treat it as descriptive; nothing is adopted. A claim needs a pre-registered replication on a fresh sample (Follow-up).

## Why

The discipline aids came from this account's own fills: the losses were in positions added to while losing, not in trading often (README "Discipline aids"). If other traders lose the same way, the guardrails solve a common problem rather than one account's. That is what decides whether they are worth building out, e.g. for other users or with optional enforcement.

## Method

`python scripts/wallet_behavior_study.py fetch` then `analyze` (raw output: `backtest_out/wallet_behavior_study.md`).

- **Sample:** 200 random wallets (seed 42) from the public leaderboard (46,831 accounts), filtered to:
  - monthly volume $20k–$20M;
  - account value ≤ $1M.
  - Near-zero accounts are kept on purpose: dropping blown-up wallets would hide the damage being measured.
- **Data:** the last 90 days of fills (`userFillsByTime`, at most the latest 10k) and the latest ≤ 2,000 orders (`historicalOrders`), per wallet. The raw data stays in `miner_cache/wallet_study/`, which is gitignored (other people's addresses and trades). Only aggregates are here.
- **Positions:** each perp position is rebuilt from flat to flat. P&L is realized P&L minus fees, including builder fees. The positions analyzed are:
  - positions opened inside the window and already closed;
  - from wallets with ≥ 5 such positions (145 wallets, 20,138 positions).
  - Wallets with > 20 positions/day would count as bot-like and be excluded; none were.
- **Averaged down:** at least one add at a price ≥ 0.5% worse than the position's average entry at that moment.
- **Stop:** a reduce-only `Stop …` order on the coin was placed between the position's open (−60 s) and its close. It's only judged where the order history reaches back to the open (12,562 positions, 62%).
- **Liquidated:** a fill that names the wallet as the liquidated user.

## Results

| | |
|---|---|
| Wallets that averaged down at least once | **81%** |
| Positions averaged down | 12% |
| Per wallet: averaged-down share of positions → of losses | **14% → 45%** (medians) |
| Positions with a stop | 25% |
| Per wallet: unstopped share of positions → of losses | 86% → 92% (medians) |
| Wallets liquidated at least once in 90 days | **48%** |
| Liquidated positions with no stop (where known) / averaged down | **96%** / 26% |
| Liquidations' share of all losses | 17% (median 25% within liquidated wallets) |
| Per wallet: worst 5% of positions' share of losses | **48%** (median) |
| Worst 1% of positions that broke at least one rule (averaged down, liquidated, no stop) | **83%** |
| Wallets profitable over 90 days after fees | 40% (median −$943) |
| Positions closed within 24h | 87% (win rate 47% vs 53% for ≥ 24h) |

**By habit (wallet level):**

| Group | Wallets | Profitable | Median 90-day P&L |
|---|---|---|---|
| Average down on < 5% of positions | 47 | 26% | −$1,956 |
| On 5–20% | 52 | 46% | −$348 |
| On ≥ 20% | 46 | 48% | −$349 |
| Stops on ≥ 50% of positions | 36 | 31% | −$2,697 |
| Stops on < 10% | 68 | 50% | +$50 |

## Reading

1. **Losses are concentrated in a few positions, and those positions break the rules the guardrails enforce.** In each wallet, half of the losses come from the worst 5% of positions. 83% of the worst 1% of positions were averaged down, liquidated or unstopped. Averaged-down positions carry about 3× their share of losses (14% of positions, 45% of losses). Nearly half of the wallets were liquidated within 90 days, almost always without a stop. This matches the account's own finding: the damage is in the tail, and the tail has recognizable habits.
2. **The habits do not predict which wallets end up profitable.** Wallets that used stops, or rarely averaged down, were *less* often profitable, not more. Likely reasons, none tested here:
   - **Trading style:** stop users and non-averagers may be short-term traders paying more fees and spread.
   - **Survivorship:** the sample is still-active wallets, so unstopped traders who blew up and quit are missing.
   - **Stops realize losses:** stops turn drawdowns that might have recovered into realized losses.
   - **Small groups:** 36 and 47 wallets.
   
   So this is **not** evidence that the rules make a trader profitable.
3. **What it supports:** the guardrails as protection against the account-ending position, not as an edge. That's consistent with how the README frames them. The stop rule looks at least as important as the averaging-down rule: unstopped positions were 96% of liquidations. That's where optional enforcement (for example, placing a missing stop) would act.

**Reproduced 2026-09-28** from the cached raw data: `analyze` gives every figure above unchanged. Two more
figures from the raw output: 47% of the worst 1% of positions were averaged down, and 77% (where known) had
no stop. `tests/test_wallet_study.py` covers the position rebuild: averaging down, partial closes, flips,
positions opened before the window, liquidations and stop coverage.

## Limitations

- **Short window and caps:** 90 days, fills capped at 10k, and orders at 2,000, so stop coverage is known for only 62% of positions.
- **What isn't counted:** open positions (unrealized P&L), and stops managed off-exchange or by manual closes.
- **One sample:** one seed. The averaging-down threshold (0.5%) and the bot filter are judgment calls.
- **Correlation, not cause.** Not pre-registered.

## Follow-up (not done)

- **Pre-registered replication:** fix the hypotheses and bars first, e.g. "averaged-down positions' loss share ≥ 2× their position share in most wallets". Then run on a new seed and window.
- **Survivorship:** sample wallets active 90–180 days ago, including those that have since stopped trading.
