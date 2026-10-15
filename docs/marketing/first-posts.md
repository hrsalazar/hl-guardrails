# First posts: drafts

**Status: drafts, not to be published yet.** Figures in `[brackets]` come from pre-registered studies; each post's footnote says which test and whether it had a pass mark:
- **Single-sample figures:** the [replication](../research/wallet-behavior-replication.md), H1–H5 passed 2026-10-14, 138 wallets analysed.
- **Pooled figures:** the [style study](../research/wallet-style-study.md), run 2026-10-15 on both samples (283 wallets).

**Why no profitability claim:** in both samples, wallets that kept the habits were *less* often profitable. The style study found why for averaging down: the profitable wallets trade in a different style (they hold longer and use limit orders). For stops, style explains only part of it.

**Rules for every post:**
- no returns, PF, or win-rate claims for the scanner (hypothetical performance);
- no "signals" framing;
- no individual wallets named;
- end with the free wallet check link;
- never say or imply that stops or not averaging down make a trader profitable;
- never say "traders who average down lose more". Across wallets the opposite holds, because of style. Only the within-trader comparison (post 3) is supported.

---

## 1. Liquidations (H1, H2)

> We rebuilt every position of [138] random active Hyperliquid traders over 90 days.
>
> [48%] were liquidated at least once.
> [90%] of liquidated positions had no stop order.
>
> A stop doesn't make you profitable. It decides whether one bad trade ends the account.
>
> Check your own wallet (read-only, runs in your browser): <link>

## 2. A few positions do the damage (H3)

> Most traders don't lose money slowly. They lose it in a handful of positions.
>
> Across [138] Hyperliquid wallets, the worst 5% of each trader's positions caused a median [47%] of their losses.
>
> Your edge isn't the next entry. It's not letting one position get that big.
>
> See your worst 5%: <link>

## 3. Averaging down (H5, style study T3)

> "I'll add here, it's a better price."
>
> We compared each trader's averaged-down positions with their *own* other positions they added to.
>
> In [2 of 3] Hyperliquid wallets, the averaged-down ones won less often. And in [two-thirds] of the wallets that average down, those positions caused [at least 2×] their share of losses.
>
> Same trader, same habit of adding. The difference is adding below your average entry.
>
> <link>

*Sources: "2 of 3" is the style study's size-free robustness check of T3, which itself passed. It covers 119 of 176 wallets, p ≈ 2×10⁻⁶. The 74% dollar version is larger, but partly reflects position size, so don't use it. "Two-thirds" is H5 from the replication.*

## 4. Building in public: what didn't work

> Things I tested for my trading tool and **didn't** ship, because the data said no:
>
> - grading each setup Low / Neutral / High from 11 features: it didn't separate winners from losers out of sample;
> - the 50-week and 200-day lines as entry filters: didn't hold on both timeframes;
> - the 20-week "bull market support band": same;
> - trading differently in risk-on vs risk-off markets: no difference;
> - shorting failed bounces only when BTC is falling: failed its test;
> - waiting for a breakout to "climb" from the 1h to the 4h to the daily chart: no better than entering at random in the same uptrends.
>
> Every rule and pass mark was written down before running. (Sources: docs/research.)
>
> What did make it in: warnings when I break my own rules. <link>

## 5. The behaviour, not the trade

> The losses in my own account didn't come from bad entries. They came from three habits:
> - no stop;
> - adding to a loser;
> - holding past my own time limit.
>
> So I built something that watches my account and buzzes my phone when I break my rules. It can't trade; it never sees a key.
>
> Is it the same for you? Check your last 90 days: <link>

## 6. What I got wrong about averaging down (style study T1)

> In our wallet data, traders who averaged down were *more* often profitable than those who didn't. I expected the opposite.
>
> So I checked what else they had in common. The profitable wallets held positions for [~11 hours] at the median, against [under 4] for the rest. [57%] of their fills were limit orders resting on the book, against [22%].
>
> Account for that, and averaging down stops predicting anything. Patient, limit-order traders scale in, sometimes below their average. Averaging down didn't make them profitable.
>
> And for any one trader, their averaged-down adds still did worse than their other adds (post 3).
>
> I'd rather publish what the data says than what sells. <link>

*Sources: style study, 283 wallets. T1 "explained by style" was a pre-registered verdict. The hold and maker figures were reported with no pass mark: median hold 10.8h vs 3.6h, maker share 57% vs 22%. Averaging-down coefficient +0.45 alone, +0.13 with style (CI includes 0). These are associations: don't present holding longer or using limit orders as advice that makes money.*
