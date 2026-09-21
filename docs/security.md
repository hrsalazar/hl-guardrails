# Security model

The repository is public. The account's numbers are meant to be visible to its owner only. This
document says what protects them, what doesn't, and why.

## What is protected, and from whom

| Asset | Protected against | By |
|---|---|---|
| Positions, PnL, balances, alerts | anyone browsing the repo, Pages, or Actions | encryption before publishing; log redaction |
| The wallet address | casual discovery via this repo | kept out of committed files; stored in a secret, masked in logs |
| Funds | anyone at all, including someone who takes over the repo | there are no keys: the tool is read-only by construction |
| Alert content in transit to devices | the push service (Apple, Google, Mozilla) | Web Push payload encryption (RFC 8291) |

**Not protected, by design or by nature:**

- **The chain is public.** Anyone who knows the address can see its positions on Hyperliquid
  itself. Nothing here can change that.
- **Git history.** Early commits contain the address and some real figures. History was
  deliberately not rewritten. Full separation needs a wallet that was never published.
- **The lock screen.** Push notifications show alert text on the device.
- **Someone who has your passphrase or an unlocked device.**

**Third-party data held:** the liquidation map keeps a list of ~560 public leaderboard addresses in
the encrypted state (to know whose positions to read) and only per-coin aggregates of their
positions. No address is stored or published alongside its positions.

## Controls

### 1. Encryption of published data (`hlg/vault.py`, `pwa/index.html`)

- **AES-256-GCM**, authenticated: a wrong key or any modified byte fails outright rather than
  decrypting to garbage.
- **Key derivation:** PBKDF2-HMAC-SHA256, 600,000 iterations (the OWASP 2023 recommendation), with
  a 16-byte random salt.
- **Fresh 96-bit random IV** for every encryption.
- **The file name is associated data**, so `state.json`'s ciphertext won't decrypt as `alerts.json`.
- **The salt carries forward** from the previous envelope, so the key is stable and a device
  unlocks once.
- **The browser refuses envelopes with fewer than 600k iterations.** This blocks a
  parameter-downgrade trick meant to make offline guessing cheaper.
- **The browser stores only a non-extractable `CryptoKey`** in IndexedDB, never the passphrase.
  Page JavaScript can use the key but can't read it. *Lock* deletes it.

The passphrase exists in exactly two places: the `DASHBOARD_PASSPHRASE` Actions secret and the
owner's memory or password manager. **The strength of the passphrase is the whole defence**:
ciphertext on Pages can be downloaded and attacked offline. Use four or more random words, or 16+
random characters.

### 2. Fail closed

- No passphrase in CI means a `{"locked": ...}` stub is published instead of data, and no push is
  sent.
- If the previous state can't be decrypted (for example, after a passphrase change), it is treated
  as absent. That is logged as an error, and the run continues.
- A missing or malformed account stops the monitor rather than watching the wrong thing.

### 3. Public log redaction (`hlg/common.py::_redact`)

With `HLG_REDACT` set, which the workflow always does, only `ERROR` records and records logged
with `extra={"safe": True}` reach the log. It is an allowlist: a new log line is hidden unless
someone deliberately marks it safe. A typical run's log is three lines of timings and counts. Web
Push errors log the HTTP status only, because the exception text contains the device's push
endpoint.

### 4. Least privilege

- The workflow has `contents: read`, `pages: write` and `id-token: write`. It can't write to
  the repo or issues.
- The external scheduler's token should be fine-grained: this repo only, **Actions: read and
  write**, nothing else. If it leaks, it can start this read-only workflow and do nothing more.
- No exchange API keys exist anywhere.

### 5. Supply chain

Runtime dependencies are pinned to exact versions in `requirements.txt`, so an unattended
upgrade can't change behaviour. GitHub Actions are pinned to major versions.

## Secrets inventory

| Name | Kind | Sensitivity | If leaked |
|---|---|---|---|
| `DASHBOARD_PASSPHRASE` | secret | **high**: decrypts everything | rotate it (below); all devices re-unlock |
| `HLG_ACCOUNT` | secret | medium: links the repo to a wallet | can't be undone; consider a fresh wallet |
| `VAPID_PRIVATE_KEY` | secret | medium: lets someone send notifications to subscribed devices | generate new keys; every device re-subscribes |
| `VAPID_SUBJECT` | secret | low | – |
| `PUSH_SUBSCRIPTIONS` | secret | medium: device push endpoints | re-subscribe the devices |
| `VAPID_PUBLIC_KEY` | variable | public by design | – |
| scheduler token | external | low: can only trigger the workflow | revoke it in GitHub settings |

**Secret names are not secret.** Anyone with admin access sees them, and so does tooling output.
Never type a value into the *Name* field.

## Procedures

**Rotate the dashboard passphrase:** set a new `DASHBOARD_PASSPHRASE` value, then run the
workflow. That run can't read the previous state, so the day and week baselines restart, and
every current alert is pushed once because none of them look already-seen. Every device shows the
gate again.

**Rotate the push keys:** `python -m hlg.vapid`, then update `VAPID_PRIVATE_KEY` and
`VAPID_PUBLIC_KEY`. On each device, clear the site's data, *Enable push* again, and replace
`PUSH_SUBSCRIPTIONS`.

**Retire a device:** remove its entry from `PUSH_SUBSCRIPTIONS`. If the device might be
compromised, also rotate the passphrase, since it held an unlocked key.

## Known limitations

- PBKDF2 isn't memory-hard. Argon2 would resist GPU guessing better, but WebCrypto doesn't
  provide it. A strong passphrase makes this moot.
- One passphrase covers all devices; there are no per-device keys or revocation.
- Push payloads include alert text, not only a "check the dashboard" ping.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private reporting (*Security → Report a
vulnerability*). The owner must first enable it under *Settings → Code security → Private
vulnerability reporting*; it is currently off.
