# Half risk in BTC downtrends? — pre-registration

**Status:** pre-registered 2026-10-21, before any regime-sized book was run; **run 2026-10-21: fails in every window, sizing stays flat** (see Results). Results are appended below without editing this section.

## Why, and the problem with asking

The [deep-history test](deep-history-test.md) showed the 1d breakout makes its money in bull years and gives a little back in bear years (2014, 2018). An obvious response is to **risk less while BTC is below its 200-day EMA**.

Two warnings, stated before running:

- **Hindsight:** the idea comes from those results, so testing it on the same years partly confirms itself.
- **Earlier evidence points the other way:** the [market-state study](regime-study.md) found the rule's odds the *same* in risk-on and risk-off states (2023+). If the odds don't change, cutting size in bear phases lowers drawdown and return roughly in proportion, and buys nothing risk-adjusted.

So the bar asks for a real risk-adjusted gain, in **every** window, on three different data sources.

## Rule (fixed)

- **The trade:** the live 1d breakout, unchanged.
- **Risk per trade:** **0.5%** when BTC's daily close on the signal day is below its 200-day EMA, otherwise **1.0%**. BTC comes from the same source as the window.
- **Comparison:** the same book at a flat 1.0%.
- **Book:** 3 slots, fees, and funding as in each window's earlier study.

## Windows (each its own source, as already used)

| Window | Data | Coins |
|---|---|---|
| W1 2013-01 → 2019-12 | free spot history (`hlg.deephistory` source rule), perp-like funding 11.5%/yr | BTC, ETH, XRP, ZEC, LINK, DOGE |
| W2 2020-01 → 2023-05 | Binance USDT-M perps and funding | the 15 backtest coins |
| W3 2023-06 → now | Hyperliquid, as live | the 15 backtest coins |

## Adoption bar (every window must pass)

1. **Sharpe** of the regime-sized book ≥ the flat book's + 0.05.
2. **Max drawdown** at least 15% smaller in relative terms (e.g. −20% → −17% or better).
3. **Return** isn't cut by more than the drawdown is, in relative terms (otherwise it's just less exposure).

**If all pass in all windows:** propose `rules.risk_in_btc_downtrend: 0.5` for the live sizing. You decide; nothing changes automatically. **Otherwise:** sizing stays flat at 1.0%.

## Also reported, with no bar

- The share of trades taken in BTC downtrends, and their PF, per window.
- **Random control:** the same number of trades picked at random, sized at 0.5%. If that improves drawdown just as much, the regime adds nothing over simply trading smaller on some trades.

## Results

**Run 2026-10-21** with `python -m hlg.regimesize`. Full output: `backtest_out/regime_sizing_study.md`.

| Window | Book | Trades | PF | Return | Max DD | Sharpe |
|---|---|---:|---:|---:|---:|---:|
| W1 2013–2019 (spot) | flat 1.0% | 104 | 2.58 | +134% | −12.9% | 1.37 |
| | 0.5% in BTC downtrends | 104 | 2.76 | +131% | −12.2% | 1.40 |
| W2 2020–2023-05 (Binance) | flat 1.0% | 103 | 1.78 | +52% | −9.3% | 1.03 |
| | 0.5% in BTC downtrends | 103 | 1.79 | +47% | −9.3% | 1.01 |
| W3 2023-06 → now (Hyperliquid) | flat 1.0% | 131 | 1.67 | +45% | −12.7% | 0.88 |
| | 0.5% in BTC downtrends | 131 | 1.55 | +34% | −12.4% | 0.75 |

| Bar | W1 | W2 | W3 |
|---|---|---|---|
| 1. Sharpe ≥ flat + 0.05 | **fail** (+0.03) | **fail** (−0.02) | **fail** (−0.13) |
| 2. Max DD ≥ 15% smaller | **fail** (5%) | **fail** (0%) | **fail** (2%) |
| 3. Return cut ≤ DD cut | pass | **fail** | **fail** |

**Descriptive:** the trades taken in BTC downtrends, against the rest.

| | Share of trades | Downtrend PF | Uptrend PF |
|---|---:|---:|---:|
| W1 | 12% | 1.02 | 2.96 |
| W2 | 17% | 1.80 | 1.78 |
| W3 | 18% | **3.14** | 1.43 |

**Random control:** halving the same share of trades at random gave drawdowns no worse than the regime rule's (median −12.2%, −9.0%, −11.5%).

## Reading

1. **The book's drawdowns don't come from BTC-downtrend trades.** Those are only 12–18% of trades, so halving them barely moves the drawdown (0–5% smaller). Halving any random 12–18% does about as well.
2. **In two of three windows those trades were as good or better.** W2's were 1.80 vs 1.78, and W3's 3.14 vs 1.43. The uptrend condition already keeps the rule mostly out of bear markets, and what it does take there pays. Cutting them only cost return: −25% in W3.
3. **This agrees with the market-state study:** the odds are the same in every state, so sizing by state buys nothing. The bear-year losses in the deep-history test came from 2014 and 2018 specifically, not from BTC-downtrend days in general.

**Verdict: not adopted.** Sizing stays flat at 1.0%, and the idea closes.
