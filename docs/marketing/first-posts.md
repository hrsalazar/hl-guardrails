# First posts: drafts

**Status: drafts, not to be published yet.** Figures in `[brackets]` are the **replicated** values: the [pre-registered replication](../research/wallet-behavior-replication.md) passed H1–H5 on 2026-10-14, on 200 new wallets with 138 analysed. Don't add any profitability claim: in both samples, wallets that kept the habits were *less* often profitable (see the replication's "What this means for the product").

**Rules for every post:**
- no returns, PF, or win-rate claims for the scanner (hypothetical performance);
- no "signals" framing;
- no individual wallets named;
- end with the free wallet check link.

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

## 3. Averaging down (H5)

> "I'll add here, it's a better price."
>
> In [two-thirds] of the Hyperliquid wallets we studied that average down, those positions caused [at least 2×] their share of losses.
>
> Adding to a loser is how a small mistake becomes the big one.
>
> <link>

## 4. Building in public: what didn't work

> Things I tested for my trading tool and **didn't** ship, because the data said no:
>
> - grading each setup Low / Neutral / High from 11 features: it didn't separate winners from losers out of sample;
> - the 50-week and 200-day lines as entry filters: didn't hold on both timeframes;
> - the 20-week "bull market support band": same;
> - trading differently in risk-on vs risk-off markets: no difference;
> - shorting failed bounces only when BTC is falling: failed its test.
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
