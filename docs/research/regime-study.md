# Market-state study: does the live book behave differently in risk-on vs risk-off?

**Status:** pre-registered 2026-09-25, before the first run; run the same day: **no difference** (below). The results section is added after the run
and the rules above it are not edited.

## Why

The lessons on the Now card could adapt their wording to the market. Earlier tests already showed
the backdrop should **not** gate entries (README "Macro", "Sentiment"). This study asks something
different: whether the strategy *behaves* differently by state (how often it signals, how often
breakouts fail fast, how long losing runs get). If it does, a state-specific message can quote
the numbers. If it doesn't, the message says so. Either way, **no signal, size or rule changes.**

## What is tested

The live book: 1d and 4h breakout_long on the 15 backtest coins, sharing 3 slots, one position per
coin, 1.0% risk (`_combined_book`), 2023-06-01 → now. Each trade gets the state of its entry day,
from data known the day before (the macro and Fear & Greed frames are already shifted a day; BTC's
trend is read off the previous daily close).

**States (fixed now):**

| dimension | buckets |
|---|---|
| crypto trend | `btc_up`: BTC's daily close above its 200-day EMA; `btc_down` otherwise |
| macro backdrop | `macro_on`: HY credit spread below its 50-day EMA **and** S&P 500 above its 200-day average; `macro_off`: spread above **and** S&P below; `macro_mixed`: one of each; no reading = excluded |
| crowd | `fear`: Fear & Greed ≤ 44; `neutral`: 45–55; `greed`: ≥ 56 |
| composite (what the app would show) | legs on = {btc_up, macro_on, greed}, legs off = {btc_down, macro_off, fear}. `risk_on`: ≥ 2 legs on and none off; `risk_off`: ≥ 2 legs off; `mixed`: everything else |

**Per bucket:**
- trades;
- entries per 30 days spent in the state;
- win rate, profit factor, average R;
- early-stop rate: a stop exit within 3 bars of the trade's timeframe;
- longest losing run;
- median days held;
- share of days in the state.

R = net P&L / the 1% risked.

## When a difference counts (fixed now)

Tested for PF, win rate, average R and early-stop rate:

1. the bucket has **≥ 30 trades**;
2. its value sits **outside the 5th–95th percentile** of 20,000 random same-size subsets of all trades;
3. its difference from the whole book has the **same sign in both halves** (split by entry date at the
   window's midpoint).

Only a difference that passes all three may be quoted as "in phases like this…". Entries per 30 days
and share of days are descriptive; they're quoted as counts, never as edges.

**Multiple comparisons, said up front:** 4 metrics × 11 buckets = 44 tests at a two-sided 10% level,
so about 4 passes are expected by chance alone even with the halves rule reducing it. The report
states the count of passes next to that expectation. If the passes are not clearly above chance,
the conclusion is "the strategy behaves about the same in every state", and the state messages
carry behavioural context only, not performance claims.

Run: `python -m hlg.backtest --regime-study`

## Results (run 2026-09-25)

`python -m hlg.backtest --regime-study`. 406 trades in the live book (4h history on Hyperliquid starts
2024-06, so the 4h stream covers the second half only; 8 trades lack a reading on one dimension).
Whole book: win rate 36%, PF 1.42, average +0.20R, 17% stopped within 3 bars.

| state | trades | share of days | entries / 30 days | win rate | PF | avg R | early stops | longest losing run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| btc_up | 250 | 66% | 9.4 | 37% | 1.39 | +0.19 | 19% | 18 |
| btc_down | 156 | 34% | 11.3 | 35% | 1.46 | +0.22 | 14% | 12 |
| macro_on | 260 | 59% | 10.9 | 39% | 1.62 | +0.30 | 17% | 15 |
| macro_mixed | 98 | 22% | 11.2 | 29% | 1.04 | −0.03 | 18% | 9 |
| macro_off | 40 | 9% | 10.6 | 40% | 1.48 | +0.31 | 10% | 8 |
| fear | 169 | 37% | 11.3 | 36% | 1.36 | +0.16 | 13% | 12 |
| neutral | 63 | 19% | 8.3 | 43% | 2.46 | +0.59 | 21% | 8 |
| greed | 174 | 44% | 9.8 | 34% | 1.15 | +0.10 | 20% | 9 |
| **risk_on** | 201 | 50% | 9.9 | 37% | 1.33 | +0.18 | 20% | 13 |
| **mixed** | 52 | 17% | 7.7 | 37% | 1.98 | +0.41 | 17% | 7 |
| **risk_off** | 153 | 33% | 11.6 | 35% | 1.36 | +0.17 | 14% | 12 |

**2 of 44 tests passed the pre-registered bar**, which is what chance alone produces (about 2–4). The two passes:
- a lower win rate in `macro_mixed` (3rd percentile);
- fewer early stops in `fear` (2nd percentile).

Neither is backed by a neighbouring bucket: `risk_off`, which contains most fear days, sits at the 5th
percentile and does not pass. The composite states the app would show (risk_on / risk_off) are the
same as the whole book on every metric: PF 1.33 vs 1.36, win rate 37% vs 35%, average R +0.18 vs +0.17.

**Conclusion, by the rule set in advance:** the strategy behaves about the same in every state. No
state message may claim the strategy does better or worse in it.

What *is* stable, and may be quoted as plain counts:
- the book enters **8–12 times a month in every state**;
- **losing runs of 7–18 trades occurred in every state**. A long losing streak is not a sign the
  market has turned against the strategy; it happened in all of them.

What changes with the state is therefore the *pressure on you*, not the strategy's odds. That is
what state messages may address: the pull to buy dips in fear, to chase in greed, and the stops at
risk around releases.
