# TradFi sleeve: does the breakout rule work on stocks, indices and commodities? — pre-registration

**Status:** pre-registered 2026-10-16, before any of this data was fetched; **run 2026-10-16: no sleeve passes** (see Results). Results are appended below without editing this section.

## Why

When crypto ranges, the scanner goes quiet for weeks. Hyperliquid's xyz exchange now lists 135 TradFi perps, about 30 of them liquid. That includes indices, commodities, FX and large US stocks, and they trade 24/7 on the same account. The question is whether the **same breakout rule, unchanged**, works on them well enough to run as a second sleeve with its own slot.

- **Not tested here:** switching *into* TradFi when crypto ranges. The market-state study found the rule's odds the same in every state, and a switching rule would be easy to fit to hindsight. A sleeve that is always on is the honest version. How much it contributes in crypto-quiet months is reported.
- **Earlier evidence:** PF ~1.2 on xyz:CL, xyz:GOLD and xyz:XYZ100, from ~9 months of xyz bars (README). That is too short to judge.

## Data (fixed)

- **History:** Yahoo Finance daily bars for each instrument's underlying, 2016-01-01 to now. These are split-adjusted, not dividend-adjusted; perps pay no dividends, so this is the closer match. Futures are Yahoo's continuous front month, so roll gaps are included as they are.
- **Funding:** each instrument's mean daily funding, measured from its xyz perp's own history, is charged every day held. An instrument with no xyz funding history is charged its sleeve's mean.
- **Fees:** as in the crypto backtest (maker in, taker out).
- **Gaps:** a stop that a gap opens through fills at the open. The engine already does this.

## Sleeves (lists fixed now)

| Sleeve | Instruments (Yahoo symbol → xyz perp) | Decides adoption? |
|---|---|---|
| **A. Macro** | ^GSPC→SP500, ^NDX→XYZ100, GC=F→GOLD, SI=F→SILVER, CL=F→CL, BZ=F→BRENTOIL, NG=F→NATGAS, HG=F→COPPER, EURUSD=X→EUR, JPY=X→JPY | yes |
| **B. 2016 mega-caps** | the largest US stocks at the start of 2016, not chosen by what they did since: AAPL, GOOGL, MSFT, BRK-B, XOM, AMZN, META, GE, JNJ, WFC, JPM, PG, T, VZ, WMT, PFE, KO, ORCL, CVX, INTC | yes, for the rule on large-cap stocks in general |
| **C. Today's xyz stocks** | NVDA, TSLA, MU, GOOGL, META, AAPL, AMZN, MSFT, INTC, AMD, MSTR, COIN, HOOD, PLTR, TSM, MRVL, DELL, NBIS, RKLB, IREN (at least 2 years of history) | **no**. They're listed because they went up, so the result is shown for information only |

## The rule

`breakout_long` exactly as it runs live:
- **Entry:** a close above the prior 20-day high, with EMA20 above EMA50, entered at the next open.
- **Exits:** a 2 ATR stop, a 3 ATR trail, and a 21-day limit.
- **Risk:** 1.0% per trade.

Each sleeve runs as its own book with 3 slots.

## Adoption bar (per sleeve, all must pass)

1. **≥ 50 trades**, 2016 → now.
2. **PF ≥ 1.3.**
3. **Stable:** PF ≥ 1.1 in both halves of its own trades *and* on the trades opened in the usual holdout window (2021-04-01 to 2023-06-01).
4. **Beats random entry:** the rule's per-instrument PF (every signal, one position per instrument, same exits) is above the 95th percentile of 200 random-entry draws. Each draw uses the same number of entries on random days when that instrument's EMA20 > EMA50.
5. **Helps the live book:** the crypto live book (15 coins, 1d + 4h, 3 slots, 2023-06 → now) plus this sleeve on **1 slot of its own** must have higher Sharpe than crypto alone, with max drawdown no more than 2 points worse.

**If a sleeve passes:** it goes live like the rally shorts: alerts, `max_tradfi: 1`, and a kill rule fixed before the first live trade. **If none pass:** TradFi stays watch-only on the dashboard.

## Also reported, with no bar

- each instrument's trades and PF;
- the sleeve's P&L in months when the crypto book opened no trade;
- the correlation of the sleeve's daily P&L with crypto's;
- funding cost per sleeve;
- **xyz vs Yahoo agreement:** over the year both exist, the share of xyz breakout signals (xyz trades 24/7) that Yahoo's bars also gave within ±1 trading day.

## Stated in advance

- **Expected outcomes:** sleeve A is the most likely to pass (trend-following on commodities and indices has a long public record), B is marginal, and C will look best for the wrong reason.
- **Multiple tests:** two decisive sleeves, so about one false pass in ten is plausible. Any adoption carries a kill rule.

## Results

**Run 2026-10-16** with `python -m hlg.tradfi`. Full output: `backtest_out/tradfi_study.md`.

| | A. Macro | B. 2016 mega-caps | C. Today's xyz stocks *(info only)* |
|---|---:|---:|---:|
| Instruments / trades | 10 / 458 | 20 / 566 | 19 / 493 |
| PF (own book, 3 slots) | **1.39** | 1.23 | 1.72 |
| CAGR / max DD / Sharpe | 7.2% / −15.4% / 0.74 | 6.6% / −13.1% / 0.77 | 14.1% / −16.9% / 1.39 |
| PF 1st / 2nd half / holdout | 1.50 / 1.31 / 1.41 | 1.44 / 1.11 / 1.28 | 1.78 / 1.69 / 1.31 |
| Every signal (uncapped) vs random uptrend entries | 1.24 vs 1.26: **45th pctile** | 1.03 vs 1.01: 59th | 1.44 vs 1.36: 77th |
| Funding, average | 3.9%/yr | 8.3%/yr | 9.7%/yr |
| Crypto book + sleeve on 1 slot: Sharpe / DD (crypto alone 1.07 / −18.5%) | **1.18 / −19.3%** | 0.99 / −20.5% | 1.25 / −18.5% |
| Daily P&L correlation with crypto | 0.02 | 0.06 | 0.11 |

| Bar | A | B | C |
|---|---|---|---|
| 1. ≥ 50 trades | pass | pass | pass |
| 2. PF ≥ 1.3 | pass | **fail** | pass |
| 3. ≥ 1.1 in both halves and the holdout | pass | pass | pass |
| 4. Beats random uptrend entries (95th pctile) | **fail** | **fail** | **fail** |
| 5. Improves the live book | pass | **fail** | pass |
| **Verdict** | **not adopted** | **not adopted** | (can't be adopted) |

## Reading

1. **On macro, being long in an uptrend pays; the breakout trigger adds nothing.**
   - Sleeve A made PF 1.39 and held up in both halves and the holdout.
   - Random entries on the same instruments, on any day they were in an uptrend, did just as well (PF 1.26 vs the rule's 1.24, uncapped).
   - This is the spring study's finding again, now on TradFi.
   - **Comparison, information only:** run through the identical test, the live crypto breakout sits at the 86th percentile since 2023-06 and the 96.5th since 2020. On crypto the trigger adds something; on macro it doesn't.
2. **A macro sleeve would still have helped the book.**
   - Sharpe went 1.07 → 1.18, with drawdown 0.8 points deeper.
   - Its daily P&L is almost uncorrelated with crypto's (0.02).
   - That's diversification, not breakout skill, and the bar fixed in advance asked for both.
   - A trend sleeve on macro (long while the uptrend holds, no breakout trigger) would be a **different rule** needing its own pre-registration. It isn't adopted from this run.
3. **Mega-caps (B) don't work with this rule.** PF 1.23 overall and 1.11 in the second half, with no edge over random entries, and it *lowers* the book's Sharpe. Stocks pay ~8%/yr in xyz funding, which eats most of a slow trend.
4. **Today's xyz stocks (C) look best, as expected.** PF 1.72, but they were picked because they went up, and even they don't beat random uptrend entries (77th percentile).
5. **The premise that crypto goes quiet is rarer than it feels.** From 2023-06 to now, the live crypto book (15 coins, 1d + 4h) opened at least one trade in **every month but one**. Quiet weeks happen; quiet months almost never.
6. **Practical findings from the overlap year:**
   - **Index and stock perps track their underlyings well:** xyz and Yahoo gave the same breakout within a bar for 79–95% of signals (S&P 500, Nasdaq-100, AAPL, MSFT, NVDA, AMD, INTC).
   - **Commodities agree less:** 45–67% for gold, silver and oil.
   - **NATGAS and EUR barely match:** 12% and 20%. NATGAS also charges ~45%/yr funding; avoid it.
   - **Oil and Brent pay longs:** funding was −17% and −25% a year.

## Verdict

**No sleeve adopted. TradFi stays watch-only.** The macro result is the one worth coming back to: as diversification, not as a breakout edge. That needs a new study, pre-registered, with a trend rule.
