# Product notes

Working notes on turning hl-guardrails into something others pay for: what it is, what already
works, what's missing, and which questions need a professional answer before selling anything.
**This is a planning document, not legal or financial advice.** Items marked ⚖️ need a lawyer
in the relevant jurisdiction.

## What it is, in one paragraph

A risk co-pilot for self-directed Hyperliquid traders. It watches the account around the clock
and warns, on the phone even with the app closed, when the trader breaks their own rules: no
stop, too much size, too much leverage, averaging down, holding too long, or past the daily or
weekly loss limit. It also surfaces a backtested breakout setup with a suggested size. It
never trades and never needs keys, which is the core trust argument.

## Who it's for

- Discretionary perp traders on Hyperliquid with roughly $500–$50k accounts, who know their
  rules and break them under pressure. The tool was built from exactly that pattern: losses came
  from unstopped, averaged-down positions held for more than 7 days.
- Traders on HL **unified accounts**. The tool reproduces HL's own unified ratio and leverage,
  which most third-party tools get wrong because they size off perp `accountValue`.

## Why someone would choose it

| Differentiator | Evidence in the repo |
|---|---|
| No keys, can't trade | read-only by construction; no order code exists |
| Private by default | end-to-end encrypted dashboard and push; see [security.md](security.md) |
| Correct unified-account math | matches HL's summary; tests in `tests/test_account.py` |
| Honest research | backtests with fees and funding; filters compared against random baselines; rejected ideas documented in the README |
| Phone-first | installable PWA with background push |
| Near-zero running cost | GitHub Actions + Pages free tier; no server |

## Current state

**Works today, for one person:** guardrails, scanner (1d/4h), momentum alerts, tabbed dashboard
(Technical / Macro / Flow), encryption, push, 266 tests, and CI.

**It is single-tenant.** One repository watches one account for one owner. Configuration is a
YAML file and secrets are set by hand. Setup takes about 20 minutes and requires a GitHub
account, which excludes most non-technical traders.

## Ways to sell it

| Model | What the customer gets | What must be built | Pros | Cons |
|---|---|---|---|---|
| **A. Self-hosted template** | a private copy of the repo plus the setup guide; they run it on their own GitHub | a setup wizard or script; a licence; support docs (mostly exist: [operations.md](operations.md)) | cheapest to build; you never hold customer data | a technical audience only; hard to enforce the licence; one-off revenue |
| **B. Hosted service (SaaS)** | sign up, paste an address, install the app | a backend (scheduler, per-user state, push), user accounts, billing, a settings UI, monitoring | a real market; recurring revenue | infrastructure and support costs; you now hold users' data (privacy law) |
| **C. Setup as a service** | you configure option A for them, plus rule coaching | nothing new | revenue now; validates demand | doesn't scale |

A sensible order is **C → A → B**. C tests whether people pay, A packages what they paid for,
and B is only worth building once A shows demand.

### What option B would need

- **Multi-tenant runner:** one job looping over users, or a queue. The core (`guardrails.run_once`,
  `scanner.run_once`) already takes `cfg`, `info` and `state` as arguments, so it's close to
  reusable. `report.main` is the single-user glue.
- **Per-user state store** in place of the Pages files. The vault design still applies:
  encrypting with a key derived from the user's passphrase means you as operator can't read their
  data, which is a strong selling point.
- **Auth and billing:** a hosted provider (e.g. Stripe for payments). The tool needs no
  wallet-connect, only an address, and ownership of the address could be proven by a signed
  message.
- **Rate limits:** Hyperliquid's public API is shared. At scale, batch requests and cache market
  data once per cycle rather than per user.
- **Observability:** per-user run success, push delivery, staleness alarms.

## Questions to settle before selling

1. **⚖️ Investment-advice regulation.** Guardrail warnings about the user's *own* rules are
   probably tool-like. The scanner is different: it names coins with entry, stop and **suggested
   size**, which may count as personalised investment advice or a signals service in some
   jurisdictions. Options include selling the guardrails only, making the scanner generic (no
   sizing), or getting licensed or legal cover. This is the most important open question.
2. **⚖️ Crypto derivatives access.** Hyperliquid perps aren't available to residents of some
   countries. Selling a tool for them may carry marketing restrictions there.
3. **⚖️ Performance claims.** The backtest figures (PF 1.67, etc.) are hypothetical,
   in-sample-heavy, and come without modelled slippage. Many regulators restrict advertising
   hypothetical performance. Marketing should use the guardrails, not returns.
4. **Data licensing.** Check the Hyperliquid, OKX and FRED terms for commercial use and
   redistribution of their data before a paid launch. Personal use differs from reselling. The
   macro tab adds more to check: the FairEconomy calendar is an unofficial feed, publishers' RSS
   terms usually allow personal reading but not redistribution of summaries to paying users, and
   alternative.me asks for attribution. A hosted product would need licensed news and calendar
   data, and the Claude API cost scales per user.
5. **Licence for this code.** The repo currently has **no LICENSE file**, which by default means
   all rights are reserved: people may view the code but not reuse it. That protects a future
   product. Choose deliberately: keep it proprietary, go source-available (e.g. BSL or
   PolyForm), or open-source part of it. Before accepting outside contributions, a contributor
   agreement keeps the ownership clean.
6. **Git history** contains the owner's address and some early figures. For a public product
   repo, consider starting a fresh repository without that history.
7. **Liability.** Clear terms: no guarantee of delivery (push and schedulers can fail), not advice,
   the user remains responsible for their stops. The dashboard's staleness indicator helps by
   being honest when the tool is behind.

## Product gaps (roadmap candidates)

| Area | Gap | Effort |
|---|---|---|
| Onboarding | a web-based setup; no YAML or secrets by hand | M (A) / part of B |
| Settings UI | edit rules from the dashboard | M |
| Multi-account | several wallets per user | S–M |
| Channels | Telegram exists locally; add it to CI, plus email and Discord | S |
| Journal | log the outcome of every alert, to measure whether following it helped; also the honest way to validate the scanner live | M |
| Explanations | a "why" link on each alert | S |
| Alerts | quiet hours; per-rule mute | S |
| Market | more exchanges, or HL sub-accounts and vaults | M–L |
| Trust | independent security review of the vault and PWA | external |
| Risk | stop-hit odds for open positions (volatility model; Kronos as a candidate) - **parked**, see [proposals/kronos-stop-odds.md](proposals/kronos-stop-odds.md) | M |

## Pricing notes (to validate, not decided)

Comparable trader tools are typically priced monthly. Test willingness to pay with option C before
building B. The honest value claim is "fewer rule-breaking losses", which the journal feature
would let each user measure for themselves.
