# Operations guide

Setting it up from nothing, running it day to day, and fixing what goes wrong. Every problem in
the troubleshooting section actually happened while building this.

## Setup from scratch (about 20 minutes)

You need a GitHub account, the wallet address to watch, and a phone or browser for the dashboard.
No exchange keys are ever needed.

### 1. The repository

1. Fork, or use as a template, and keep the name `hl-guardrails` or pick your own. Public is fine,
   because everything published is encrypted.
2. *Settings → Pages → Build and deployment → Source: **GitHub Actions***.
3. *Settings → Actions → General → Workflow permissions*: the default (read) is enough. The
   workflow declares its own permissions.

### 2. Secrets

*Settings → Secrets and variables → Actions*. Type the **name** exactly as shown and put the
value in the **Secret** box.

| Name | Where | Value |
|---|---|---|
| `DASHBOARD_PASSPHRASE` | Secrets | a strong passphrase; store it in a password manager |
| `HLG_ACCOUNT` | Secrets | the `0x…` wallet address |
| `VAPID_PRIVATE_KEY` | Secrets | from `python -m hlg.vapid` |
| `VAPID_SUBJECT` | Secrets | `mailto:you@example.com` or `https://<you>.github.io` (host only, no path) |
| `VAPID_PUBLIC_KEY` | **Variables** tab | from `python -m hlg.vapid` |
| `PUSH_SUBSCRIPTIONS` | Secrets | added in step 5 |
| `OPENROUTER_API_KEY` | Secrets | optional, for the daily brief: openrouter.ai → *Keys* → *Create key* (buy a few dollars of credit first; ~$0.05 a day). Without it (or the next one) the brief is skipped and everything else works |
| `ANTHROPIC_API_KEY` | Secrets | optional alternative: a key from console.anthropic.com (API credits are separate from a Claude Pro plan). Used only if no OpenRouter key is set |

To set them from a terminal, use the GitHub CLI; it prompts for the value without showing it:

```bash
gh secret set DASHBOARD_PASSPHRASE --repo <you>/hl-guardrails
```

On Windows, if `gh` "is not recognised", the terminal was opened before the CLI was installed.
Open a new one, or call `& "C:\Program Files\GitHub CLI\gh.exe"`. Never pass a secret with
`echo … |`: the trailing newline becomes part of the value.

### 3. Rules

Edit `config.yaml` → `rules` (risk per trade, leverage caps, loss limits, allowed coins) and
`scanner` (coins, timeframes). The address never goes in this file; for local runs put it in
`config.local.yaml`, which is gitignored.

### 4. Cadence

GitHub's own cron is unreliable at short intervals, so use an external trigger:

1. Create a fine-grained token: this repository only, *Actions: Read and write*, nothing else.
2. In a free scheduler (cron-job.org or similar), create a job running **every 15 minutes**:
   - `POST https://api.github.com/repos/<you>/hl-guardrails/actions/workflows/monitor.yml/dispatches`
   - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`
   - Body: `{"ref":"main"}`
3. *Actions → monitor* should now show `workflow_dispatch` runs about every 15 minutes.

### 5. Devices

For each phone or computer:

1. **iPhone:** open `https://<you>.github.io/hl-guardrails/` in **Safari** → Share → *Add to Home
   Screen*, then open it **from the icon**. iOS only allows notifications for installed web apps.
   Android and desktop work in the browser.
2. Enter the passphrase. The device stays unlocked until you press *Lock*.
3. *Enable notifications* → allow, then *Enable push* → *Copy*.
4. Save the copied text as the `PUSH_SUBSCRIPTIONS` secret. For several devices, merge them into
   one list: `[{…phone…},{…laptop…}]`.
5. Check it: *Actions → monitor → Run workflow*, tick **"Also send a test notification"**, then
   *Run*. The device should buzz within a minute, and the run log shows `push: N/N device(s)`.

## Day to day

- **Dashboard header:** the timestamp turns amber after 20 minutes and red after 40. Red means
  runs have stopped; check the scheduler.
- **Notifications** fire only for alerts that weren't in the previous run, and only for guardrail
  breaches, breakout entries, momentum and **high-impact releases (FOMC, CPI, jobs…) 24h ahead**,
  once per release. Funding notes and breakouts on coins you already hold stay on the dashboard.
- **The daily brief** (Macro tab) appears on the first run after 06:00 UTC. It's reading material:
  never pushed, not backtested. Its source links let you check any point before relying on it.
  To regenerate it now (one paid call): *Actions → monitor → Run workflow*, tick **"Regenerate the
  daily brief now"**.
- **The alert list** groups by your positions, entries, heads-up, info and recently ended. Tap a
  line to expand it; ✕ or *Clear all* hides alerts on that device (position warnings cannot be
  cleared). A signal that passed shows for 24h as missed, failed or expired.
- **Loss lock:** after a daily or weekly limit is hit, the tool keeps advising "be flat" until the
  next UTC day or ISO week.
- **Changing rules:** edit `config.yaml` and push. The push itself triggers a run.
- **Universe study, weekly:** `universe-study` runs every Monday and commits its result to
  `docs/research/universe-study.md` (a trend table) if the number changed. It only ever writes that
  file — it never touches `config.yaml`, so a passing week is something to go read, not something
  that changes what gets scanned by itself.

## Health checks

| Check | How | Healthy |
|---|---|---|
| runs are happening | `gh run list --workflow monitor.yml` | a success every ~15 min |
| data is encrypted | open `https://<you>.github.io/hl-guardrails/alerts.json` | `"alg": "AES-256-GCM"`, no readable numbers |
| logs are quiet | open any run's *Run guardrails + scanner* step | ~3 lines: market, liquidations, `run ok: published encrypted` |
| push works | manual run with the test box ticked | notification received; `push: N/N` |
| daily brief works | the first run after 06:00 UTC | a `digest: N headlines from M/11 feeds via openrouter (model), … tokens` line; the Macro tab says "today" |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard says "encryption not configured" | `DASHBOARD_PASSPHRASE` missing, or saved under another name | check *Secrets*; the name must match exactly |
| A secret's **name** looks like your passphrase | the value went into the Name field | delete that secret; pick a **new** passphrase (the old one has been displayed); save it properly |
| Passphrase rejected on a device | wrong passphrase, or it was rotated | enter the current one; a rotation requires re-unlocking everywhere |
| Run fails with "no account configured" | `HLG_ACCOUNT` missing, or it carried whitespace before the fix | re-set it with `gh secret set … --body 0x…` or the web form |
| Local run can't read `config.local.yaml` | file saved as UTF-16 or with a BOM (PowerShell `>`, Notepad) | handled since the encoding fix; otherwise re-save as UTF-8 |
| No notifications at all | `PUSH_SUBSCRIPTIONS` not set, or the iOS app isn't installed to the Home Screen | complete *Devices*; run the test push |
| Log shows `webpush failed: HTTP 410` or `404` | the device's subscription expired, or site data was cleared | *Enable push* on that device again and update the secret |
| Updates only every few hours | the external scheduler stopped or its token expired | check the scheduler's history; renew the token |
| Day/week PnL restarted | the passphrase changed, so previous state was unreadable | expected once; it recovers the next day or week |
| `gh` "not recognised as a cmdlet" | PATH not refreshed in that terminal | open a new terminal, or use the full path |
| Stale UI after an update | service-worker cache | reload once; `sw.js` bumps its cache version on UI changes |
| Macro tab: "No brief yet" | no `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`, or before 06:00 UTC | set a secret; the next run after 06:00 UTC writes one |
| Log: `digest failed: OpenRouter HTTP 402` | out of OpenRouter credit | top up at openrouter.ai; it retries two hours later |
| Log: `digest failed: OpenRouter HTTP 401` / `Claude API HTTP 401` | the key is wrong or revoked | re-set the secret |
| Log: `digest failed: … HTTP 400` / `404` | a model id in `digest.openrouter_models` (or `digest.model`) doesn't exist | check the id on openrouter.ai/models; it retries two hours later, not every run |
| Log: `events: calendar fetch failed: HTTP 429` | the weekly calendar feed rate-limits shared IPs | nothing: the last week's list is kept and it retries in an hour |

## Local use

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
echo "account: '0x…'" > config.local.yaml
pytest -q                          # 246 tests, no network
python -m hlg.guardrails           # continuous, console / Telegram
python -m hlg.scanner
python -m hlg.report               # one CI-style run; writes plaintext site/*.json (gitignored)
python -m hlg.backtest             # research
```
