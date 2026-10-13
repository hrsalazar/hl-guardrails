# Rally shorts on daily bars, ungated: a confirmation on unseen coins

**Status:** pre-registered 2026-10-12, before the first run; run the same day: **passed all five rules, adopted as a 1d alert** (below). The results section is added after the run
and the rules above it are not edited.

## Why, and why on other coins

In the rally-short study (docs/research/rally-short-study.md), the **baseline** `rally_any`, a rally
short with no BTC gate, did better on 1d than both candidates did:
- PF 1.30 on 187 trades, 2023-06 → now, PF 1.28 / 1.31 in the two halves;
- PF 1.44 in the 2021–23 holdout.

It lost on 4h (PF 0.96). It was a reference line, not a candidate, so adopting it on those numbers
would be choosing after the fact. A confirmation needs data the rule hasn't been judged on:
- **time after today doesn't exist yet;**
- **the 2021–23 holdout is spent** (we've seen 1.44 there).

What is unseen is **other coins**. Every short study so far used the same 15. So the confirmation
runs the identical rule on the rest of Hyperliquid's market.

## The rule (fixed now, unchanged)

`rally_any`, exactly as in the rally-short study: a coin in a downtrend (EMA20 < EMA50) prints a
**closed 1d bar whose high touches EMA20 and which closes below it, in the lower half of its
range**. Then:
- **entry:** short at the next open;
- **stop:** the bar's high + 0.5 ATR;
- **exit:** trailed 3 ATR above the lowest low, a 21-day limit, no target.

No BTC gate, no other filter, 1d only.

## Test set C: the coins never used (fixed now)

- **Which coins:** every main-exchange Hyperliquid perp **except the 15 backtest coins**, delisted
  coins included (dropping them would bias against shorts).
- **When each coin counts:** only on days it is in the point-in-time liquid universe, judged from
  data up to the day before:
  - listed ≥ 60 days;
  - trailing 30-day median daily notional ≥ $10M;
  - among the top 30 non-backtest coins by that median.

  These are the universe study's `auto_top30` thresholds (`eligibility`).
- **Window:** 2023-06-01 → now.
- **Book:** 1.5% risk, 3 slots, Hyperliquid fees and funding (shorts receive positive funding).

## Adoption bar (fixed now)

**All** of the following must hold:
1. **Enough trades:** ≥ 50 trades in C.
2. **Profit factor:** PF ≥ 1.3 in C.
3. **Both halves profitable:** PF > 1.0 in both halves of its own trades, split at the median entry
   date.
4. **Not luck:** in a 10,000-draw bootstrap of C's trades, the 5th percentile of PF is > 1.0.
5. **Improves the live book:** the live book is 1d + 4h longs, 3 shared slots, 1.0% risk, the 15
   coins, 2023-06 → now. With `rally_any` shorts added **on the 1d streams only**, it must keep Sharpe
   ≥ the live book's, max drawdown no more than 2 pp worse, and PF no more than 0.05 lower.

**If it passes,** it's adopted as an **alert only** (you decide; nothing trades), 1d, on the scanned
coins. It is followed live in the signal journal under a **kill rule fixed now**: after 30 closed
live rally shorts, it is retired if their PF is below 1.0.

**If it fails,** the short question is closed for this market phase: three short entries tested,
none adopted.

Run: `python -m hlg.backtest --rally-confirm`

## Results (run 2026-10-12)

**All five rules pass.** `python -m hlg.backtest --rally-confirm` (raw: `backtest_out/rally_confirm.md`).

| set C: the coins never tested, 2023-06 → now | value | bar |
|---|---|---|
| coins ever eligible / of them delisted | 61 / 7 | — |
| trades / coins traded | **104** / 35 | ≥ 50 ✓ |
| win rate, average R | 50%, +0.31R | — |
| PF | **1.67** | ≥ 1.3 ✓ |
| PF in each half of its own trades | 1.54 / 1.77 | > 1.0 ✓ |
| bootstrap PF, 5th / 50th / 95th percentile | **1.14** / 1.68 / 2.41 | 5th > 1.0 ✓ |
| max drawdown | −12.1% | — |
| funding received | +1.6% of equity | — |

**The live book with 1d rally shorts added** (15 coins, 1d + 4h longs, 3 shared slots, 1.0% risk):

| | trades | shorts | PF | total return | max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| live book (longs) | 407 | 0 | 1.42 | +121% | −18.5% | 1.09 |
| + rally_any 1d | 415 | 152 | **1.64** | **+200%** | **−15.0%** | **1.54** |

Rule 5 passes (✓).

**Read with care:**
- **Rules 1–4 are the out-of-sample evidence**, on coins never used. Rule 5 runs on the 15 coins
  where the rule was already seen (PF 1.30), so it shows the shorts fit the book; it is not
  independent evidence.
- **No slippage is modelled.** Small-cap shorts can fill worse, so the 1.67 is somewhat optimistic.
- **Results vary widely by coin.** The most-traded, FARTCOIN, was about flat (13 trades, PF 0.94), and a
  handful of big winners carry much of the profit, which is normal for a trailing exit.

**Adopted as an alert only, 1d, on the scanned coins.** The kill rule is fixed above: after 30 closed
live rally shorts in the signal journal, it is retired if their PF is below 1.0.

## Live configuration (2026-10-13): its own slot

Shorts go live with **their own single slot**, separate from the three long slots. That's the
owner's choice, to start small while the rule proves itself live. The confirmation judged shorts
sharing the 3 slots, so the live configuration was re-run as well. This is descriptive, not a new
gate (`python -m hlg.backtest --rally-book`):

| live book, 2023-06 → now, 1.0% risk | shorts | PF | long PF | short PF | return | max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| longs only (3 slots) | — | 1.41 | — | — | +118% | −18.5% | 1.07 |
| + shorts sharing the 3 slots | 152 | 1.62 | 1.63 | 1.61 | +197% | −15.0% | 1.52 |
| **+ shorts in their own slot (3 L + 1 S)** | **68** | **1.45** | 1.42 | **1.66** | **+158%** | **−17.8%** | **1.33** |

The separate slot takes fewer shorts and gains less than sharing would, but it still raises the
book's Sharpe from 1.07 to 1.33 without a deeper drawdown.

**Live:**
- **Scanner:** a `RALLY SHORT` alert on the 1d close (`hlg.scanner.rally_short`), with entry, stop
  (the rejected high + 0.5 ATR), size at 1R, and the odds line.
- **Guardrails:** longs limited to `max_positions` (3), shorts to `max_shorts` (1).
- **Journal:** follows each short (trail above the lowest low).
- **Kill rule:** applied by the scanner from the journal's own record (`journal.side_summary`).
- **Dashboard:** a "Rally shorts, live" tracker counting toward the 30.
