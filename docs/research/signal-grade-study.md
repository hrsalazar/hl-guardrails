# Signal grade: can a breakout be rated Low / Neutral / High before entry?

**Status:** pre-registered 2026-10-02, before the first run; run the same day: **not adopted on either timeframe** (below). The results section is added after the run
and the rules above it are not edited.

## Why

About 60% of breakouts lose, and the edge comes from the ~20% that run for weeks. What hurts in
practice is the entry that pulls back straight into the stop. The entry study
(README "Entries") tested single bar-quality filters and adopted none. This study asks whether
**several features together** can grade a signal well enough to show as context on the alert. It
never filters signals.

## Signals and outcomes (fixed now)

- **Signals:** the live rule (`breakout_long`: EMA20 > EMA50 and a close above the 20-bar high) on the
  15 backtest coins, **1d and 4h separately**, 2023-06-01 → now.
- **One trade at a time per coin and timeframe:** a signal while the previous simulated trade on the
  same coin and timeframe is still open is skipped, as the journal does.
- **Every signal is simulated on its own**, whether or not the 3-slot book would have had room:
  - entry at the next bar's open;
  - initial stop at the signal close − 2 ATR;
  - stop trailed 3 ATR below the highest high, updated after each bar;
  - time stop after 21 days;
  - fees 0.015% at entry and 0.045% at exit.
- **Outcomes:**
  - `R`: the trade's result in units of the initial risk, after fees;
  - `hit1R`: price reaches entry + 1R *before* the stop. If both happen in the same bar, the stop
    counts first (conservative). A miss is the "pulled back into my stop" entry.

## Features: all known at the signal close (fixed now)

| feature | meaning |
|---|---|
| `close_loc` | where the breakout bar closed in its range (1 = at the high) |
| `brk_atr` | how far past the 20-bar high it closed, in ATR |
| `vol_ratio` | bar volume vs its 20-bar average |
| `atr_pctile` | volatility vs its own last 100 bars (low = a squeeze) |
| `ext_atr` | close above EMA20, in ATR |
| `rsi` | RSI14 |
| `funding_apr` | funding at the signal |
| `rs_rank` | 20-bar return rank across the 15 coins |
| `above_50w` | coin's close above its own 50-week SMA (1/0; the journal's tracked lead) |
| `market` | market-state legs: on − off, −3…+3 (BTC trend, credit and S&P, Fear & Greed; `hlg.market_state`) |
| `prior_fails` | this coin and timeframe's signals in the previous 60 days that missed +1R, counted only once resolved |

## Model and evaluation (fixed now)

- **Model:** logistic regression for `hit1R`, L2 penalty λ = 1 on standardised features, intercept not
  penalised. Missing values are filled with the training fold's median. Written in numpy, no new
  dependency.
- **Walk-forward, calendar quarters:** each quarter is scored by a model trained only on signals whose
  trades **closed before that quarter began**, so no outcome leaks. Testing starts once there are 12
  months of data (1d) or 6 months (4h).
- **Grades:** terciles of the predicted probability, with cut points from the training fold. Low =
  bottom third, Neutral = middle, High = top.

**A grade is adopted for a timeframe only if all of these hold on its out-of-sample quarters:**
1. **Separation:** average R of High minus Low ≥ **0.30R**, and the 95% CI (bootstrap by week,
   10,000 draws) excludes 0.
2. **Stability:** the High − Low difference is ≥ 0.15R in **each half** of the out-of-sample period.
3. **Calibration:** for every grade, the average predicted `hit1R` is within **10 pp** of the observed rate.
4. **Order:** the observed `hit1R` rate is High ≥ Neutral ≥ Low.

It ships only for timeframes that pass, as context on the alert, e.g. "High: similar setups reached
+1R first 61% of the time". It never gates a signal.

**If neither timeframe passes**, the alerts get the honest base rates instead: win rate, the share
that reached +1R before the stop, average R, and the share of profit from the top 20% of trades.

**Expect thin samples.** The out-of-sample periods have a few hundred signals on 4h and fewer on 1d.
The CI rule is there because of that.

Run: `python -m hlg.signal_grade`

## Results (run 2026-10-02)

`python -m hlg.signal_grade` (3.5 min). Raw output: `backtest_out/signal_grade_study.md`.

**Base rates, every signal simulated on its own:**

| | 1d | 4h |
|---|---:|---:|
| signals | 246 | 906 |
| won | 38% | 35% |
| reached +1R before the stop | 56% | 50% |
| average R per trade | +0.35 | +0.18 |
| net R of the top 20% of trades vs all trades | +176.7R vs +86.2R | +550.2R vs +163.5R |

The top fifth of trades made more than all the profit; the other 80% lost money together.

**Out-of-sample grades (walk-forward by quarter):**

| | 1d Low | 1d Neutral | 1d High | 4h Low | 4h Neutral | 4h High |
|---|---:|---:|---:|---:|---:|---:|
| signals | 60 | 32 | 57 | 187 | 277 | 331 |
| average R | +0.31 | +0.02 | +0.80 | +0.14 | +0.18 | +0.16 |
| reached +1R first | **63%** | 59% | **58%** | 46% | 52% | 47% |
| predicted | 39% | 51% | 69% | 46% | 54% | 64% |

| check | 1d | 4h |
|---|---|---|
| High − Low ≥ 0.30R, CI excludes 0 | +0.48R, CI −0.60 to +1.49: **fail** | +0.02R, CI −0.38 to +0.40: **fail** |
| each half ≥ 0.15R | −0.36 / +0.84: **fail** | −0.01 / +0.18: **fail** |
| calibration within 10pp | High predicted 69%, got 58%: **fail** | High predicted 64%, got 47%: **fail** |
| order High ≥ Neutral ≥ Low (+1R rate) | backwards: **fail** | **fail** |

**Not adopted on either timeframe.**
- **1d:** the High grade's higher average R comes from a few big winners in the second half. Its
  confidence interval is wide and crosses zero. It reached +1R *less* often than Low.
- **4h:** with 795 out-of-sample signals, every grade averages the same, about +0.15R.
- **The model is overconfident.** It promised 64–69% for High and got 47–58%.

The coefficients fitted on all the data are small and change sign between timeframes. For
example, breakout size: +0.17 on 1d, −0.03 on 4h; above the 50-week line: −0.17 on 1d, +0.07 on 4h.
That's what features without a stable relation to the outcome look like.

**What ships instead, as pre-registered:** every breakout alert carries a line with the base rates.
On 4h, for example: *"odds: of 906 past 4h breakouts 35% won and 50% never reached +1R before the
stop; avg +0.18R a trade, all of it (and more) from the top 20% that ran. No setup feature predicted
which one this is: take it as planned; the stop is the cost of the winners."*

The honest answer to "will this one pull back into my stop?" is "about half do, and nothing visible
at the signal tells which half." The plan already accounts for that: 1R risk, a stop placed before
entry, no adds.
