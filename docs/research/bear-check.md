# Bear-case check — pre-registered, collecting

**Question:** when a model makes the best case *against* a new long breakout signal, using the coin's
recent headlines, funding, open interest and the backdrop, does a "high" flag mark the signals that
lose? It is borrowed from TradingAgents' bull/bear researcher debate, cut down to one call so it can
be tested. Rules fixed 2026-10-14, before any flag was produced.

## Rules

- **Which signals:**
  - every new long signal in the journal, within 12h of being added;
  - one call each, after alerts are pushed and after the daily brief;
  - at most 3 a run;
  - a failed call is recorded and never retried (`hlg/bearcheck.py`).
- **Inputs, public data only:**
  - the signal's close, breakout level, ATR and close location;
  - the coin's 24h move, funding, OI and premium;
  - the market state and Fear & Greed;
  - the day's brief headline and tilt;
  - archived headlines from the last 48h that name the coin.

  The account never goes in.
- **Output:** strict JSON with two parts:
  - **case:** two or three sentences;
  - **flag:** low, medium or high. "High" is reserved for a concrete, specific reason in the data.
- **Never pushed, never gating:** the flag is a journal column and the case its tooltip.
- **Judged:** once 40 flagged longs have closed (stopped or timed out).
  - **The test:** split the 40 at the median signal time. The check becomes one line on the long alert
    only if high-flag trades have PF below 1.0 overall, and below the rest in both halves.
  - **Otherwise:** "no measurable warning", and it stays a tooltip or is switched off.
- **Expectation stated now:** the signal-grade study found that no price feature graded these
  signals, so "no" is the likely outcome. The headlines are the one input that study didn't have.

## Cost

About 1–1.5k tokens in and 100 out per signal. With the brief's model list that's roughly $0.02 a
signal, or about $1–3 a month at the scanner's signal rate.

## Results

Collecting. The running split is shown in the Signal journal ("Bear-case check").
