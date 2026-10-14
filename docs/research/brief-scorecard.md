# Daily brief scorecard — pre-registered, collecting

**Question:** does the daily brief's risk-on / risk-off tilt say anything about BTC over the next
five days? Until now it was labelled "reading material" because there was no way to test it. This
fixes the test before any result is seen. Rules fixed 2026-10-14, before the first settled call.

## Rules

- **Logged:** each brief's day, time, tilt and confidence, with the BTC mid price at the run that
  wrote it (`brief_log` in the encrypted state; `hlg/digest.py` `log_brief`). One per UTC day; a
  forced re-run replaces that day.
- **Settled:** at the first run at least 5 days later (within ~15 minutes of it), against the BTC mid
  then. Risk-on is right if BTC is higher, risk-off if lower. Neutral claims no direction and isn't
  scored, the way TradingAgents doesn't score "Hold".
- **Compared with:**
  - **chance:** the same tilts shuffled across the same windows, 2,000 times;
  - **naive:** always calling the direction BTC moved more often. That is only knowable in hindsight,
    so it's a generous bar, shown for context.
- **Judged:** not before 60 directional calls have settled.
  - **Better than chance:** only if the hit rate is above the shuffle's 95th percentile.
  - **Otherwise:** "no better than chance", and the brief stays reading material.
  - **If it passes:** it becomes a candidate line in the Now card, with its own adoption decision. It
    still would not be a trade signal.
- **Caveat stated now:** daily briefs with 5-day windows overlap, so neighbouring calls aren't
  independent and the shuffle flatters a little. The 60-call minimum is set partly for that.

## Point-in-time archive

From the same day, the brief's input headlines are archived in `news.json`:
- titles, source and time only;
- 400 days;
- encrypted, gzipped, and never read by the page.

Any later study of the news (a sentiment filter, Polymarket odds next to headlines, a different
model on the same inputs) can then use only what was public on each day.

## Results

Collecting. The record is shown under the brief in the Macro tab.
