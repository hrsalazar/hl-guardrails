# Breakout ladder: 1h → 4h → 1d — tested, not adopted

**Hypothesis (the owner's):** a breakout starts on 1h, carries into 4h, then shows on the daily.
If that's true, the 1h and 4h legs should warn early of a daily breakout, give a better entry, or
mark the better daily breakouts.

`python -m hlg.ladder` (research only; module docstring has the rules). Run 2026-10-14.

## What was asked, and why not the obvious count

That a daily breakout comes after 1h and 4h breakouts is almost true by construction: price has to
cross the 20-day high hour by hour. The data confirm it: **99% of 4h breakouts had a 1h breakout in
the 24h before** (3,643 of 3,681). Counting that would "confirm" the idea without saying anything. So
the study asks the forward question: once 1h and 4h have broken out, but the daily hasn't yet, does
that tell us anything?

## Rules (fixed before running)

- **1h / 4h break:** a close above the prior 20-bar high. For 4h, only the first close above counts,
  not every bar of the run.
- **Ladder:** a 4h break with a 1h break in the 24h before. It only counts in context: the daily trend
  is up (EMA20 > EMA50), the last daily bar was not a breakout, and price is still below the 20-day
  high.
- **Trade:** enter at the next 4h open with the live stop (2 daily ATR), then the shared 1h simulator
  from the cascade study (3 ATR trail, 21 days, fees, one position per coin). Results are in R.
- **Data:** Binance USDT-M perps, 1h since 2023-06, with 4h and 1d built from it; the same 15 coins
  as the cascade study, so the numbers compare.

| Test | Pass bar |
|---|---|
| **A. Early warning** | A 1d breakout within 10 days is ≥ 1.5× as likely after a ladder as from any 4h bar in the same context, and > 1× in both halves |
| **B. Early entry** | PF above the 95th percentile of 200 random entries in the same context; PF and avg R ≥ the 1d breakout's; PF > 1.2 in both halves |
| **C. Confirmation** | 1d breakouts with a ladder in the 3 days before beat those without, in both halves. If over 90% have one, the filter is moot |

Three tests, so about one false pass was plausible by chance. Anything passing would have gone to the
live journal first.

## Results

| entry | trades | win | PF (R) | avg R | 1st / 2nd half PF |
|---|---:|---:|---:|---:|---:|
| 1d breakout (reference) | 216 | 34% | 1.04 | +0.02 | 1.01 / 1.08 |
| ladder entry | 392 | 39% | 1.22 | +0.10 | 1.47 / 1.03 |
| 1d breakout, with a ladder before | 177 | 34% | 1.02 | +0.01 | 1.06 / 0.98 |
| 1d breakout, without | 39 | 36% | 1.13 | +0.07 | 0.87 / 1.95 |

- **A, early warning — fail.** A daily breakout followed within 10 days 39.5% of the time after a
  ladder vs 29.9% from any 4h bar in the same context: a **1.32× lift** (1.31 / 1.34 by half). Real
  and stable, but under the 1.5× bar, and weaker than the 1h→4h reversal cascade's 1.44×.
- **B, early entry — fail.** The ladder entry made PF 1.22 against the daily breakout's 1.04, but
  **random entries in the same context made a median PF of 1.14**; the ladder sits at the 68th
  percentile, not above the 95th. All of the edge came in the first half (1.47 vs 1.03). As with the
  spring, what pays is being long while the daily trend is up; the ladder adds nothing measurable.
- **C, confirmation — fail.** 82% of daily breakouts had a ladder in the 3 days before. Those did
  *not* do better (1.06 / 0.98 by half) than the 39 without (0.87 / 1.95); the halves disagree, and 39
  trades is too few to read anything into.

## Verdict

**Not adopted, and not tracked live.** The idea describes how breakouts look, but it doesn't predict
them better than being in an uptrend already does. The live cascade watch (`hlg/cascade_watch.py`) is
the closer early warning and stays the one being tracked.

**Why the reference breakout is PF 1.04 here, not 1.67:** the same reasons as in the cascade study
(README): no 3-slot cap, Binance prices, and a harsher 1h trail. The comparisons are like-for-like.
