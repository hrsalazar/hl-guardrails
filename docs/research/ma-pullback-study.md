# The 9 EMA / 20 SMA / 200 SMA pullback (Emmanuel Malyarovich)

**Status:** pre-registered 2026-10-08, before the first run; run the same day: **not adopted** (below). The results section is added after the run
and the rules above it are not edited.

## Source

"The ONLY 2 Indicators I've Used to Make $843,886 Day Trading", by Emmanuel Malyarovich (YouTube,
2e4WayvjLJo), read from its transcript.
- **His claim:** over $850k made day trading US stocks. It is unverified; the video sells a
  mentorship.
- **His charts:** a 9 EMA, a 20 SMA and a 200 SMA. He trades with the trend, long or short, 95% of
  the time. He says it works the same on "crypto, gold, silver".

## His rules, as stated

1. **Trend:** the 20 SMA is rising and under price (falling and over it, for shorts), at roughly a
   45° slope. Not flat, and not so steep that the trend is "overextended". The 9 EMA is above the
   20.
2. **Entry zone:** a pullback into the area between the 9 EMA and the 20 SMA. He enters above the
   pullback bar (a doji or bottoming tail) and puts the stop below it.
3. **Extension:** the further price is from the 20 SMA, the more overbought. Don't enter up there.
   Skip the **first** pullback after the trend accelerates.
4. **The 200 SMA:** support or resistance. Don't buy with it directly overhead; use it as a target.
5. **A variant he mentions:** in an uptrend, enter when price reclaims the 20 SMA after a
   retracement, with the stop below the retracement low.
6. **Also, discretionary:** catalyst stocks (overnight gappers), a 1–15 minute chart that depends on
   the time of day, alignment across timeframes, and judgment. **These can't be tested here** (see
   Limits).

## The mechanical version (fixed now)

These use native windows on the bar's own timeframe, as on his charts.
- **ATR** is 14 bars.
- **Slope** = (SMA20 − SMA20 five bars ago) / (5 × ATR).
- **Extension** = (close − SMA20) / ATR.

**`ma_pullback` (candidate), long:** a signal at the close of bar *k* when all of these hold:
- EMA9 > SMA20 and slope > 0 (rule 1);
- low ≤ EMA9 and close ≥ SMA20 (the bar dipped into the zone and held the 20) (rule 2);
- extension ≤ 2 ATR (rule 3);
- extension never above 3 ATR in the previous 10 bars, which skips the first pullback after an
  acceleration (rule 3);
- the SMA200 is not between the close and 1 ATR above it (rule 4).

The trade:
- **Entry:** a buy-stop at bar *k*'s high, good for the next bar only. It fills at the higher of
  the bar's open and the trigger.
- **Stop:** bar *k*'s low, but at least 0.5 ATR below the trigger, so a doji can't make a near-zero
  stop and an absurd size.
- **Target:** the SMA200, if it is at least 1 ATR above the trigger at the signal (rule 4).
- **Exit:** the stop, the target, or **a close back below the SMA20**, whichever comes first (the
  trend broke).

**Short:** the exact mirror (EMA9 < SMA20, slope < 0, high ≥ EMA9, close ≤ SMA20, a sell-stop at
the low, and so on).

**`ma_reclaim` (descriptive only, rule 5):**
- the previous close was below the SMA20 and this close is above it;
- SMA20 > its value 20 bars ago, and the close is above the SMA200;
- entry at the next open; stop at the lowest low of the last 5 bars (at least 0.5 ATR away); the
  same exits;
- the mirror for shorts.

**Pessimistic fill:** if a stop-entry bar also touches the stop, the trade is taken as stopped on
that bar. A single bar can't say which came first.

**Engine:** everything else is the same as every other study here. The same 15 coins (`DEF`), 1.5%
risk, 3 slots, Hyperliquid fees (maker in, taker out) and funding, most-liquid first when signals
compete.

## Windows

- **A:** 2023-06-01 → now, on 1d and 4h. The 4h history starts 2024-06.
- **B (holdout):** 1d, 2021-04-01 → 2023-05-31. There's no funding data there.
- **1h:** about 7 months of candles. Too short to judge, so it's **descriptive only**.

## Adoption bar (fixed now)

`ma_pullback` (both sides together, as he trades it) becomes an alert only if **all** of these hold:

1. **Enough trades:** at least 100 trades in each of A-1d and A-4h, and at least 30 in B-1d.
2. **Profit factor:** PF ≥ 1.3 after fees and funding in A-1d, A-4h and B-1d.
3. **Both halves profitable:** PF > 1.0 in both halves of its own trades (split at the median entry
   date), in A-1d and A-4h.
4. **Improves the live book:** added to the live book (1d + 4h breakout longs, 3 shared slots, 1.0%
   risk, window A), it leaves Sharpe ≥ the live book's and max drawdown no more than 2pp worse.

**For reference only:** the long and short sides separately, `ma_reclaim`, and the current
`breakout_long` on the same data. None of these is adopted from this run, whatever it shows.

## Limits, stated up front

His edge, if there is one, may sit in what this can't test:
- **Choosing catalyst gappers.** Crypto perps on a fixed list have no equivalent.
- **Intraday timing**, on 1–15 minute charts in the US session.
- **Discretion:** "pick the moments where you want to be aggressive".

Hyperliquid's candle history is too short below 1h to test those timeframes. So a fail here means
"the mechanical core doesn't work on crypto perps at 1h–1d", not "he's wrong about his own
trading". And a pass would still need to survive live journaling like any other rule here.

Run: `python -m hlg.backtest --ma-pullback-study`

## Results (run 2026-10-08)

**Not adopted. Rules 2, 3 and 4 fail.** The strategy as he trades it, long and short:

| window | trades | win rate | PF | avg R | both halves PF | max DD |
|---|---:|---:|---:|---:|---|---:|
| A, 1d (2023-06 → now) | 394 | 0.27 | 1.56 | +0.62 | 1.07 / 1.70 | −32% |
| A, 4h (2024-06 → now) | 1,749 | 0.23 | **0.94** | +0.01 | 0.96 / 0.89 | −91% |
| B, 1d holdout (2021-04 → 2023-05) | 253 | 0.25 | **1.16** | +0.32 | 2.04 / **0.47** | −66% |
| 1h (2026-03 → now, descriptive) | 1,323 | 0.24 | 0.93 | +0.01 | 1.01 / 0.83 | −83% |
| *breakout_long, A-1d, for reference* | 128 | 0.36 | 1.66 | +0.29 | 1.11 / 2.29 | −18% |
| *breakout_long, A-4h* | 410 | 0.36 | 1.36 | +0.19 | 1.13 / 1.50 | −40% |

**Added to the live book**, the 1d + 4h breakout longs at 1.0% risk (779 MA trades): PF fell from
1.39 to 1.13, Sharpe from 1.03 to 0.76, and max drawdown widened from −18.5% to **−47.4%**. The
worst single day went from −4.8% to **−19.3%**.

**What it says:**
- **Faster bars, worse results.** On 4h and 1h it loses money after costs: 4h fees came to 56% of
  starting equity. Win rates of 22–27% mean losing runs that look like a broken system even when
  it isn't one. He trades this on 1–15 minute charts, where the costs per trade would be heavier
  still.
- **The short side adds nothing** (PF 1.06 on 1d, 0.87 on 4h, 0.76 on 1h), which is consistent
  with both short studies.
- **Tight stops plus the 3× leverage cap make bad days:** the worst day was −19%. A stop at the bar's
  low is close, so the size is large. Then a gap through the stop costs several R.
- **The one lead is the daily long side:** `ma_pullback_long` made PF 1.97 and +0.96R a trade on A-1d
  (243 trades). But its max drawdown was −46% at 1.5% risk. It made PF 1.29 in the holdout, and the
  second half of B was 0.67. It wasn't the pre-registered candidate. Picking it now, after seeing
  the table, would be the garden of forking paths. If it's worth anything, it needs its own fresh
  test: a forward window it has never seen, or a live paper journal. And it would have to clear the
  drawdown bar too, which it doesn't today.
- **`ma_reclaim`** (his secondary idea): PF 0.70 on 1d and 1.27 on 4h. It's inconsistent across
  timeframes, which looks like noise.

**Limits:** see above. This tests the mechanical core on crypto perps at 1h–1d. It doesn't test
his stock selection (catalyst gappers), his intraday timing, or his discretion.

Re-run: `python -m hlg.backtest --ma-pullback-study` (writes `backtest_out/ma_pullback_study.md`).
