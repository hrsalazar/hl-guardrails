# Out-of-sample retest of the live rules on Binance 2020–2024 — pre-registration

**Status:** pre-registered 2026-10-16, before any Binance data for these windows was fetched; **run 2026-10-16: R1 fails, R2 confirmed, R3 inconclusive** (see Results). Results are appended below without editing this section.

## Why

The live rules (1d and 4h long breakouts, 1d rally shorts) were built and judged on Hyperliquid history. Two stretches of that history are spent:

- **1d, 2023-06 → now:** the main test window.
- **1d, 2021-04 → 2023-05:** the holdout. The short and rally-short studies, among others, already reported results there.

Binance's USDT-M perps reach further back. These windows have **never been used by any study here**:

| Rule | Untouched window | Why it's untouched |
|---|---|---|
| **4h long breakout** | **2020-01-01 → 2024-06-30** | Hyperliquid's 4h history starts 2024-07-05 |
| 1d long breakout | 2020-01-01 → 2021-03-31 | the 1d holdout starts 2021-04 |
| 1d rally short | 2020-01-01 → 2021-03-31 | same |

## Data (fixed)

- **Source:** Binance USDT-M perpetuals, daily and 4h bars from the public API, with Binance's own funding history charged.
- **Coins:** the 15 backtest coins, each traded only once it was listed on Binance futures. Coins listed later simply join later.
- **Survivorship caveat (stated now):** the 15 were chosen in 2023+, so in 2020 they are survivors. That flatters the longs.
- **Rules and engine:** unchanged (`breakout_long`; the rally short as live, with stop above the rejected high + 0.5 ATR), with fees as in every backtest. Each rule runs in its own book: 3 long slots, or 1 short slot, at 1.0% risk.

## Bars (fixed)

This is a confirmation of rules that are already live, so the bars ask whether they **hold up**. That's a lower bar than adoption.

| Rule | Window | Confirmed if |
|---|---|---|
| **R1. 4h long breakout** | 2020-01 → 2024-06 | ≥ 100 trades, PF ≥ 1.2, PF > 1.0 in both halves of its own trades, bootstrap 5th percentile of PF > 1.0 |
| **R2. 1d long breakout** | 2020-01 → 2021-03 | ≥ 30 trades, PF ≥ 1.2, PF > 1.0 in both halves. Fewer than 30 trades = **inconclusive** |
| **R3. 1d rally short** | 2020-01 → 2021-03 | ≥ 20 trades and PF ≥ 1.0 (its own kill-rule line). Fewer than 20 = **inconclusive** |

**If a rule fails:** I'll propose demoting it, for example 4h signals to watch-only until its live journal shows PF ≥ 1.0 over 30 closed trades. You decide; nothing changes automatically. **Inconclusive** changes nothing.

## Also reported, with no bar

- R1 by calendar year, including 2022's bear market;
- R1 against random uptrend entries, with the same test the TradFi study used;
- the full live book (1d + 4h longs on 3 slots, plus the rally-short slot) over 2020-01 → 2021-03, the only window where every rule is untouched;
- Binance vs Hyperliquid: R1 over 2024-07 → now on both sources. If the rule's PF differs a lot between them, the gap is attributed to the data source, not the period.

## Results

**Run 2026-10-16** with `python -m hlg.retest`. All 15 coins were on Binance futures (each from its listing). Full output: `backtest_out/binance_retest.md`.

| Rule (untouched window) | Trades | Win | PF | Halves | Bootstrap p5 | Max DD | Verdict |
|---|---:|---:|---:|---|---:|---:|---|
| **R1. 4h breakout** (2020-01 → 2024-06) | 753 | 35% | **1.17** | 1.24 / 1.10 | **0.97** | −43% | **fails** (PF < 1.2, p5 < 1.0) |
| **R2. 1d breakout** (2020-01 → 2021-03) | 51 | 45% | **2.04** | 2.38 / 1.78 | 1.18 | −9% | **confirmed** |
| **R3. 1d rally short** (2020-01 → 2021-03) | 18 | 17% | 0.08 | 0.07 / 0.08 | 0.01 | −12% | **inconclusive** (< 20 trades) |

### Descriptive (no bar)

**R1 by entry year:**

| Year | 2020 | 2021 | 2022 | 2023 | 2024 H1 |
|---|---:|---:|---:|---:|---:|
| Trades | 144 | 215 | 117 | 174 | 103 |
| PF | 1.98 | 1.00 | 0.94 | 1.20 | 1.00 |

**R1 vs random uptrend entries:** PF 1.22 against a median of 1.15 for random entries; 76th percentile.

**Data source isn't the cause.** Over the same window (2024-07 → now), the 4h rule made PF 1.50 on Binance and 1.37 on Hyperliquid. Binance prices don't make the rule look worse.

**The full live book** (1d + 4h longs on 3 slots, plus the rally-short slot), 2020-01 → 2021-03: PF 1.58, Sharpe 1.79, max DD −23%. This is mostly a bull market.

**Book with and without the 4h stream** (added after the run, to inform the decision; no bar):

| Book | Window | Trades | PF | Return | Max DD | Sharpe |
|---|---|---:|---:|---:|---:|---:|
| 1d only | Binance 2020-01 → 2024-06 | 156 | 1.49 | +52% | −12% | 0.79 |
| 1d + 4h (live shape) | Binance 2020-01 → 2024-06 | 670 | 1.28 | +201% | −32% | 1.05 |
| 1d only | Hyperliquid 2023-06 → now | 131 | 1.67 | +45% | −13% | 0.88 |
| 1d + 4h (live shape) | Hyperliquid 2023-06 → now | 409 | 1.40 | +117% | −19% | 1.07 |

## Reading

1. **The 1d breakout holds up.**
   - On fifteen untouched months: PF 2.04, both halves well above 1, and a bootstrap 5th percentile of 1.18.
   - Together with the earlier holdout (2021–23) and the main window, the daily rule has now held in every window tested.
2. **The 4h breakout doesn't, on its own.**
   - Over four and a half untouched years it made PF 1.17, short of the 1.2 bar, and its bootstrap 5th percentile is under 1.0.
   - Most of the profit came from 2020. From 2021 to mid-2024 it ran close to breakeven (PF 0.94–1.20), with a −43% drawdown as a standalone book.
   - It does beat random uptrend entries (76th percentile), so it isn't nothing. It's thin.
3. **Yet the 4h stream raises the book's Sharpe in both periods.**
   - It does so through more trades and more return, at the cost of a deeper drawdown (−12% → −32% on Binance).
   - The verdict fixed in advance was about the rule standing on its own, and on that it fails.
   - The book result is information only, and it's the reason the next step is your decision, not automatic.
4. **Rally shorts: 18 trades, three winners, PF 0.08 in the 2020–21 bull market.**
   - Under 20 trades is "inconclusive", as fixed.
   - The pattern is clear, though: short-lived downtrends inside a bull market crushed them.
   - Their earlier holdout PF of 1.44 came from 2022's bear market.
   - The live kill rule (30 closed trades, PF < 1.0) is the safeguard. This result makes it more likely to trigger if 2026 turns bullish.

## Decision (yours, per the pre-registration)

The pre-registered consequence of R1 failing is a **proposal** to demote 4h signals. Nothing changes automatically. The options and trade-offs are in the README entry for this retest.
