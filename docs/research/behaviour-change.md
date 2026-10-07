# Behaviour change: building habits that help while you wait

**Status:** a literature review written 2026-10-07, and the design it led to (shipped the same day).
It is not a backtest. The claims come from published studies, listed at the end. It isn't
treatment, either. For a compulsion that is costing sleep, money or relationships, the evidence
points to CBT with a professional (see "Where this stops").

## The problem

Waiting is most of this strategy: a signal arrives every week or so, and between signals the plan
says do nothing. For someone who has traded compulsively, that empty time is when the old habit
returns: staring at charts, projecting fib levels and wave counts, and inventing trades. The
account this app was built for knows the cost. Oversized risk, averaging down and lost sleep.
Smaller size, the guardrails and fewer trades have already cut the stress. The goal now is to make
the new habits hold, and to make keeping the plan feel like progress rather than an absence.

**The owner's own framing,** which is now on the Now card: *"I trade for side income, not to be
right."* Most of what follows comes back to that line. Being right about the market is the reward
the charting chases. The plan pays out in money, over a series of trades.

## What the evidence says

### 1. Excessive trading behaves like a gambling problem, and heavy market monitoring is one of its markers

- **Australia (n = 9,245):** problem gambling was markedly more common among day traders
  (7.6%) than among non-traders.
- **Systematic review (12 studies):** problem gambling was generally more common among traders,
  ranging from 1.4% to 47.2%.
- **Latent-class analysis:** about 10% of traders fit a "disordered trading" class, marked by
  higher trading frequency, **more intensive market monitoring** and more problem gambling.

The charting habit isn't a quirk. It is one of the behaviours that separates disordered trading
from ordinary trading.

### 2. Gamified trading apps increase trading, so reinforcement must never reward a trade

In the FCA's trading-app experiment (more than 9,000 participants), push notifications raised the
number of trades by 11%, and points with prize draws by 12%. Other studies found gamified apps
also raise risk-taking, most among people with less financial literacy. Confetti on a fill is
exactly the wrong design for this user.

**Rule:** nothing in this app celebrates a trade, a win or the P&L. What gets reinforced is
keeping the plan: days without a slip, urges waited out, reviews done.

### 3. Habits build from cumulative repetition; a missed day doesn't undo them. All-or-nothing streaks backfire

- **Lally et al. (2010):** automaticity took 66 days on average, with a wide range. Missing one
  opportunity did not significantly affect habit formation. Only repeated inconsistency did.
- **The abstinence-violation effect (Marlatt):** after a single lapse from a total-abstinence goal,
  people tend to continue the prohibited behaviour, driven by guilt, shame and "I've blown it
  anyway". A streak counter that resets to zero recreates that trap.

**Rule:** counts are cumulative and forgiving. A slip day stays visible as a ring, and the count
of days kept never resets.

### 4. Self-compassion after a slip increases the motivation to improve; self-criticism doesn't

In four experiments, Breines & Chen (2012) found that people who met a failure or weakness with
self-compassion were more motivated to improve. In one of them, they studied 25% longer after a
failed test, compared with people prompted to boost their self-esteem.

**Rule:** a slip is met with a calm line, never a red X: "information, not a verdict", followed by
a question about what set it off.

### 5. If-then plans work, and work best when they are your own and rehearsed

- **Gollwitzer & Sheeran (2006), 94 tests:** if-then plans (implementation intentions) had a
  medium-to-large effect on reaching goals, d = 0.65.
- **A later meta-analysis, 642 tests:** effects of d = 0.27 to 0.66. They were stronger for
  contingent if-then wording, motivated people and rehearsed plans.

**Rule:** the user writes their own swap ("When I want to open a chart, I will…"). The app shows it
at the moment of the urge.

### 6. Urges can be ridden out rather than obeyed

- **Urge surfing** (Marlatt; part of mindfulness-based relapse prevention) means watching a craving
  rise and fall instead of acting on it. It may not reduce urges at first, but it changes the
  response to them.
- **Veterans with gambling disorder** who completed MBRP reported gambling less, with fewer and
  less intense cravings.
- **CBT for gambling disorder** (meta-analysis) reduced urges, g = −0.76.

The app's urge check already runs this sequence: name the urge, a timed pause, a check against
the plan.

### 7. Personalised feedback and pre-commitment reduce gambling, and the person's own numbers carry weight

Players who got personalised feedback on their own play, for example when nearing a monthly loss
limit they had set, reduced what they gambled in real-world studies (Auer & Griffiths; Norsk
Tipping). There's a caveat: people with a gambling problem breach self-set limits more often, so
limits work best alongside other tools, not alone.

**Rule:** your own numbers, never a comparison with other people. That covers the loss limits
already enforced, how often you looked today against your usual, and your record against the
rule's.

### 8. Postponing beats prohibiting

This one is a design inference from 5 and 6, not a separate study. A flat "never chart" leaves the
urge nowhere to go, and a single breach becomes the abstinence-violation spiral. A scheduled
**charting window** turns "no" into "not now, at 11". The strategy only acts on bar closes, so a
window after the daily close loses nothing.

## What shipped (Now card)

| Feature | Evidence | Notes |
|---|---|---|
| **Your why**, in your own words, under the headline | §5, §8 | editable, stored on this device; default "I trade for side income, not to be right." |
| **Plan kept, 30-day dot strip**, plus an all-time count | §3, §2 | a UTC day is kept with no add to a loser, no off-plan position, no signal taken without a stop (from fills: `behavior.slip_days`, `execution.slip_days`). A slip is a ring, never a reset; today is in progress |
| **Slip note** for a slip in the last two days | §4 | "One slip is information, not a verdict…" |
| **Process milestones**: urges waited out (5 → 500), days on plan (7 → 365), weekly reviews (1 → 52), with progress to the next | §2, §3 | one quiet note when reached, said once; no confetti; never for trades or P&L |
| **New urge reason**, "Look at charts, project the price" | §1, §6 | the check shows your charting window and your swap plan; its lesson is the slot-machine one |
| **Charting window**, 15 minutes, by default at the daily close in your time | §8 | editable |
| **Looks today vs your usual**, after a week of data | §1, §7 | "calmer than usual", or a pointer to the urge button; never shaming |

## Deliberately not built

- **Confetti, sounds or celebrations** on any trade, fill or profit (§2).
- **Streaks that reset to zero** (§3). The existing "No adds to a loser N days" chip is a fact about
  the last add, and it sits beside the cumulative record, not instead of it.
- **Leaderboards or comparisons with other traders.** In the FCA study leaderboards had no
  significant effect on trade counts, and social comparison is the wrong frame for this goal.
- **Blocking.** The app can't stop an order on the exchange, and pretending otherwise would
  undermine trust. The friction it adds (the pause, the check, the window) is honest about being
  optional.

## Where this stops

This is self-help for someone already doing better. Signs that it isn't enough:
- trading or charting that keeps going despite losses;
- hiding it;
- restlessness when you can't check;
- chasing losses;
- lost sleep.

Those call for a professional. CBT is the best-evidenced treatment for gambling disorder. In
Australia, Gambling Help Online is free, confidential and open 24/7 (1800 858 858,
gamblinghelponline.org.au), and it covers trading.

**If this grows into a tool for others,** it should carry a helpline line in the app (left out of the personal
version for now), and the same rules hold, plus two:
- the data stays on the person's device or encrypted, as here;
- the app never earns from trading volume. An app that profits when its users trade more is the
  FCA study's design, not this one.

## Sources

- Trading and problem gambling (Australia, n = 9,245; and the latent-class analysis): [PMC11815345](https://pmc.ncbi.nlm.nih.gov/articles/PMC11815345/), [Frontiers in Psychiatry 2025](https://www.frontiersin.org/journals/psychiatry/articles/10.3389/fpsyt.2025.1505012/full)
- FCA, digital engagement practices, trading-app experiment: [research note](https://www.fca.org.uk/publications/research-notes/research-note-digital-engagement-practices-trading-apps-experiment)
- Gamified trading and risk-taking: [Investment Executive summary](https://www.investmentexecutive.com/?p=50195), [SSE working paper](https://research.hhs.se/esploro/outputs/workingPaper/Does-Gamified-Trading-Stimulate-Risk-Taking/991001555899406056)
- Lally et al., habit formation and missed days: [BPS Research Digest](https://bps.org.uk/research-digest/seven-ways-be-good-1-learn-healthier-habits), [summary](https://www.spring.org.uk/2023/01/form-a-habit.php)
- The abstinence-violation effect: [NCJRS abstract](https://www.ncjrs.gov/App/Publications/abstract.aspx?ID=237820); streak counters and dependency: [Northumbria](https://researchportal.northumbria.ac.uk/en/publications/dont-kick-the-habit-the-role-of-dependency-in-habit-formation-app/)
- Breines & Chen (2012), self-compassion and self-improvement motivation: [PubMed 22645164](https://pubmed.ncbi.nlm.nih.gov/22645164/)
- Gollwitzer & Sheeran, implementation intentions: [summary of both meta-analyses](https://goalsandprogress.com/implementation-intentions-research/)
- Urge surfing and MBRP in gambling: [NY problem gambling presentation](https://nyproblemgambling.org/wp-content/uploads/2020/10/Parente-Presentation.pdf), [MBI in gambling disorder](https://link.springer.com/article/10.1186/2195-3007-4-2)
- CBT and cognitive distortions in gambling disorder: [PMC9292938](https://pmc.ncbi.nlm.nih.gov/articles/PMC9292938/)
- Personalised feedback and limit setting: [J Gambling Studies](https://link.springer.com/article/10.1007/s10899-022-10155-1), [Auer & Griffiths](https://irep.ntu.ac.uk/29197/), [Frontiers 2019](https://www.frontiersin.org/articles/10.3389/fpsyg.2019.00639/pdf)
