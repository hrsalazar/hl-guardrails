# 1d breakout on 2013–2019 — pre-registration

**Status:** pre-registered 2026-10-18, before any breakout signal on these years was computed; **run 2026-10-18: set S confirmed, set D fails** (see Results). Data availability was checked (first and last bar per source), but not prices or results. Results are appended below without editing this section.

## Why

The 1d long breakout has held in every window tested so far:
- **Main window:** Hyperliquid, 2023-06 → now.
- **Holdout:** 2021-04 → 2023-05.
- **Binance retest:** 2020-01 → 2021-03.

Every one of those is 2020 or later. Free spot history (see [data-inventory.md](data-inventory.md)) reaches back to 2011–2019, covering two full crypto cycles, including the 2014–15 and 2018 bear markets. **No study here has used any bar before 2020-01-01.**

## Sets (fixed)

| Set | Coins | Window | Purpose |
|---|---|---|---|
| **S. Live coins** | the backtest coins with pre-2020 spot history: BTC, ETH, XRP, ZEC, LINK, DOGE | 2013-01-01 → 2019-12-31 (each from its first bar) | does the live rule hold on its own coins, years earlier? |
| **D. The 2018 field** | the top 20 by market cap on 2018-01-01, excluding set S and stablecoins: BCH, ADA, LTC, XEM, XLM, IOTA, TRX, DASH, NEO, XMR, EOS, QTUM, ICX, BTG, LSK, NANO, ETC | 2017-01-01 → 2019-12-31 | **survivorship check**: the coins that mattered then, many of which faded or were delisted later |

**Source per coin:** the spot source with the most daily bars inside the window, with ties going to the earlier start. The candidates are Binance spot, Bitfinex, Bitstamp and Coinbase. The rule is mechanical and blind to results. XEM and LSK have no free history before 2020, so they don't take part.

## Rule and costs (unchanged from live)

- **Rule:** `breakout_long`, exactly as it runs live.
- **Book:** its own, with 3 slots, at 1.0% risk.
- **Fees:** as in every backtest here.
- **Funding:** perps didn't exist for most of this period, so a perp-like funding cost is charged every day held: the **mean of Binance's BTC funding over its whole history (2019 → now)**. Spot would have no such cost, so this is conservative.

## Bars

**Each set is confirmed if all hold:**

1. ≥ 50 trades;
2. PF ≥ 1.2;
3. PF > 1.0 in both halves of its own trades;
4. set S only: bootstrap 5th percentile of PF > 1.0.

**If both are confirmed:** the daily rule's evidence spans 2013 → now. **If either fails:** I'll propose a review of the 1d risk setting. You decide; nothing changes automatically.

## Also reported, with no bar

- results by year, with the bear years 2014, 2015 and 2018 shown separately;
- per-coin trades and PF;
- the rule vs random uptrend entries (same test as before);
- BTC on three venues (Bitstamp, Bitfinex, Coinbase). If they disagree a lot, the result depends on the source.

## Stated in advance

- **Survivorship:** set S is survivors by construction. Set D was chosen to counter that, but it is still coins that reached the top 20, not the whole market.
- **Liquidity:** early markets were thin, with wider spreads than today's fees assume. That flatters any rule on 2013–2016.

## Results

**Run 2026-10-18** with `python -m hlg.deephistory`. Funding charged: 11.5%/yr (Binance BTC's lifetime mean). Full output: `backtest_out/deep_history_test.md`.

| Set | Window | Trades | Win | PF | Halves | Bootstrap p5 | CAGR | Max DD | Verdict |
|---|---|---:|---:|---:|---|---:|---:|---:|---|
| **S. Live coins** (BTC, ETH, XRP, ZEC, LINK; DOGE had no trades) | 2013 → 2019 | 104 | 53% | **2.58** | 7.12 / 1.21 | 1.75 | 12.9% | −12.9% | **confirmed** |
| **D. The 2018 field** (14 of 17 coins traded) | 2017 → 2019 | 87 | 41% | 1.59 | 2.67 / **0.86** | 0.99 | 9.0% | −9.4% | **fails** (2nd half < 1.0) |

**By entry year:**

| | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S: trades / PF | 12 / 33.0 | 2 / 0.00 | 5 / 9.26 | 13 / 4.28 | 44 / 2.85 | 9 / **0.08** | 19 / 1.83 |
| D: trades / PF | — | — | — | — | 44 / 2.70 | 15 / **0.69** | 28 / **0.97** |

**Descriptive (no bar):**
- **vs random uptrend entries:** S at the 78th percentile (PF 2.81 vs 2.32); D at the 56th (1.72 vs 1.64).
- **BTC on three venues, 2016–2019:** PF 2.34 (Coinbase), 2.40 (Bitstamp), 3.85 (Bitfinex). The result doesn't hinge on the venue.
- **Per coin:**
  - **Set S:** BTC 3.36 (53 trades), ETH 2.76, XRP 1.94, LINK 3.37 (5); ZEC 0.81.
  - **Set D:** LTC 2.10 (17), XMR 2.19, ETC 1.54, DASH 1.34; BTG 0.84, XLM, ICX and NANO lost (2–5 trades each).

## Reading

1. **On the coins it trades live, the 1d rule held back to 2013.**
   - PF 2.58, bootstrap 5th percentile 1.75, consistent across venues.
   - With this test, it has been positive in every window from 2013 to now.
2. **But its profit comes from bull markets.**
   - **Bull years:** 2013, 2016, 2017 and 2019 made the money.
   - **Bear years:** 2014 and 2018 lost (PF 0 and 0.08, on few trades).
   - **Why bears stay small:** in a bear market, the uptrend condition keeps it mostly out (9 trades in all of 2018), so the damage stays small (−14% of equity at the worst) rather than ruinous.
3. **On the wider 2018 field, the edge didn't survive the bear.**
   - **2017's bubble:** PF 2.70.
   - **2018–2019:** 0.69, then 0.97.
   - **The coins that later faded** gave their breakouts back. That's the survivorship the live coin list hides: on today's coins the rule looks better than it would have looked choosing coins at the time.
   - **Random entries:** set D beat random uptrend entries only at the 56th percentile. On that field, the gain was the bull market, not the trigger.
4. **What this doesn't say.** It doesn't say the rule is broken. It says its returns depend heavily on bull phases and on trading coins that survive. Both are already partly built in: the live list is re-checked by the weekly universe study, and the 3-slot cap keeps the bear-market bleed small.

## Review of the 1d risk setting (the pre-registered consequence of a fail)

**Proposed: no change to the 1d rule or its 1.0% risk.** The failure is the 2018–19 bleed on faded coins, and it was small:
- **Set D's worst drawdown was −9.4%**, below the live book's backtested −18.5%.
- **The kill-switch-style protections already in place:** the 3-slot cap and the 2 ATR stop.

**What it does argue for:** keeping the coin list liquid and current, which the weekly universe study already does. It also means setting expectations: a bear year is likely to be flat to slightly negative, not profitable. Your call, as fixed.
