# Capitulation long, open-interest confirmed, on 2020–2023 — pre-registration

**Status:** pre-registered 2026-10-18, before any 2020–2023 capitulation signal or open-interest change was computed; **run 2026-10-18: fails, and the capitulation idea closes** (see Results). Results are appended below without editing this section.

## Why

The [capitulation study](capitulation-study.md), on Hyperliquid 2023-06 → now, found:
- **The candle-only 1d signal:** PF 1.53 over 52 trades, stable. It failed on random entries (84th percentile) and on too few unseen-coin trades.
- **A lead, found after the fact:** signals where open interest also fell ≥ 5% that day made PF 1.84 (38 trades), against 0.31 for the 8 without.

A falling OI is the footprint of forced liquidation, the mechanism the rule assumes. This retest fixes the OI-confirmed version **now** and judges it on years the first study never touched.

## Rule (fixed)

- **Signal:** the 1d capitulation bar exactly as before (drop ≥ 1.5 ATR, range ≥ 2.5 ATR, volume ≥ 2× the prior 20 bars, close location ≥ 0.35), **plus** open interest down ≥ 5% over that day.
- **OI change:** Bybit's daily OI snapshot at the bar's close against the one at its open. Both are known when the bar closes, so nothing looks ahead.
- **Missing OI:** **no signal.** The rule *is* the OI confirmation. This is a deliberate exception to "missing data never rejects", which is about filters on an existing rule.
- **Trade, unchanged from the first study and not re-tuned:** long at the next open, stop at the bar's low − 0.5 ATR, target entry + 1.5R, 3-day time stop.
- **Costs and sizing:** fees, Binance funding, 1.0% risk.

## Data (fixed)

- **Window:** 2020-01-01 → 2023-05-31. The first study started 2023-06, so this is untouched for this rule.
- **Prices:** Binance USDT-M daily bars and funding. **OI:** Bybit daily history, from each coin's first OI point (BTC 2020-08; most others 2021).
- **Coins, fixed lists made for other reasons:**
  - the 15 backtest coins;
  - the 2018-field coins from the [deep-history test](deep-history-test.md): BCH, ADA, LTC, XEM, XLM, IOTA, TRX, DASH, NEO, XMR, EOS, QTUM, ICX, BTG, LSK, NANO, ETC.

  A coin without Binance futures bars, or without Bybit OI, simply can't signal.

## Adoption bar (all must pass)

1. **≥ 50 trades** (one position per coin).
2. **PF ≥ 1.3.**
3. **PF ≥ 1.1 in both halves** of its own trades.
4. **Beats random entries:** every OI-confirmed signal has a PF above the 95th percentile of 200 random draws. Each draw is the same number of entries after random **down bars** on the same coins, with stop distances resampled from the signals' own, and the same target and time stop.
5. **OI adds something:** the OI-confirmed signals' PF is above that of the candle-only signals the OI condition removed, in the same window.
6. **Helps the book:** the 15-coin breakout book (1d + 4h longs, 3 slots, Binance, 2020-01 → 2023-05) plus this rule on **1 slot of its own** must have higher Sharpe than the book alone, with max drawdown no more than 2 points worse.

**If it passes:** it's tracked live in the journal first (no alerts), then adopted with its own slot and a kill rule. Live use needs a daily OI source: Hyperliquid's own `openInterest` from the scanner's context, snapshotted daily. **If it fails:** the capitulation idea closes.

## Also reported, with no bar

- trades by year, including the 2021 and 2022 crashes;
- per coin;
- exits (target, stop or time);
- the share of candle-only signals that had OI data at all.

## Stated in advance

- **The lead came from after-the-fact splitting of 46 trades.** The likeliest outcome is that it doesn't replicate.
- **The trade shape:** the 1.5R target was rarely reached before. The rule is kept anyway, because changing it now would be fitting.
- **Venue mix:** Bybit's OI stands in for market-wide positioning, while the trades are priced on Binance. That mix is a limitation.

## Results

**Run 2026-10-18** with `python -m hlg.capitulation_oi`. 30 coins had Binance daily bars and 27 had Bybit OI. Full output: `backtest_out/capitulation_oi_retest.md`.

| | Value |
|---|---|
| OI-confirmed trades / win rate | 28 / 25% |
| **PF** / halves | **0.31** / 2.49, 0.14 |
| Candle-only signals the OI condition **removed** | 44, PF **1.08** |
| vs random down-bar entries (median PF 1.06) | **0th percentile** |
| Exits (time / stop / target) | 18 / 9 / 1 |
| By year | 2020: 2 trades; 2021: 17, PF 0.61; 2022: 9, PF 0.00 |
| Book Sharpe with the slot (book alone 0.94) | 0.94, 6 capitulation trades |

| Bar | Result |
|---|---|
| 1. ≥ 50 trades | **fail** (28) |
| 2. PF ≥ 1.3 | **fail** (0.31) |
| 3. ≥ 1.1 in both halves | **fail** (2.49 / 0.14) |
| 4. Beats random down-bar entries | **fail** (0th percentile) |
| 5. OI-confirmed beats the removed signals | **fail** (0.31 vs 1.08) |
| 6. Helps the book | pass on paper: Sharpe unchanged to two decimals |

## Reading

1. **The lead reversed.**
   - **On Hyperliquid 2023+:** OI-confirmed flushes made PF 1.84.
   - **On untouched 2020–2023:** they made 0.31, worse than every random draw of "buy after a red day", and worse than the capitulations they filtered out (1.08).
   - The 2023+ split was 8 vs 38 trades chosen after the fact, and it didn't hold.
2. **Why it may have reversed.** The OI-flush days of 2021–2022 include the May 2021 crash, LUNA and FTX. In those, the liquidation wave was the start of a longer leg down, not the end of one. A capitulation in a bear market isn't a bottom.
3. **The candle-only signal was roughly breakeven here** (PF 1.08 on the 44 the OI condition removed). Across both studies, buying the daily flush hasn't shown an edge beyond buying any red day.

**Verdict: not adopted, and the capitulation idea closes, as fixed in advance.** Nothing live changes.
