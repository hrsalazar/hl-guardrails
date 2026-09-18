"""One-shot run for GitHub Actions: guardrails + scanner -> encrypted site/*.json, Web Push.

Env:
  SITE_URL              public Pages URL (to fetch the previous state/alerts for de-dup)
  DASHBOARD_PASSPHRASE  encrypts everything published (hlg.vault). In CI, without it nothing
                        with account data is published at all -- see `publish`
  HLG_ACCOUNT           the wallet address, kept out of the public repo
  HLG_REDACT            keep account data out of the (public) Actions log -- see common._redact
  VAPID_PRIVATE_KEY     (optional) base64url VAPID private key -> Web Push
  VAPID_SUBJECT         (optional) mailto:you@example.com
  PUSH_SUBSCRIPTIONS    (optional) JSON list of PushSubscription objects (or a single object)
  HLG_TEST_PUSH         "true" -> also push a test notification (the workflow's manual `test_push`)

Alerts used to be appended to a GitHub Issue as well. On a public repo that published every
position to anyone, logged in or not, so that channel is gone; Web Push is end-to-end encrypted
to your device and stays.
"""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests
from cryptography.exceptions import InvalidTag

from . import account, guardrails, liquidations, market, scanner, vault
from .common import (
    Notifier,
    State,
    info,
    load_config,
    log,
    require_account,
    setup_logging,
)

OUT = Path("site")
FILES = ("state.json", "alerts.json", "history.json")
LOCKED_STUB = {"locked": "encryption not configured: set the DASHBOARD_PASSPHRASE secret"}


def fetch_prev(site_url, name):
    if not site_url:
        return None
    try:
        r = requests.get(f"{site_url.rstrip('/')}/{name}", timeout=15)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def load_prev(raw, name, vlt):
    """Previous published file -> its plain content, or None. Accepts plaintext too, so the first
    encrypted run can carry forward state published before encryption existed."""
    if raw is None or raw == LOCKED_STUB or (isinstance(raw, dict) and "locked" in raw):
        return None
    if vault.is_envelope(raw):
        if vlt is None:
            return None
        try:
            return vlt.unseal(raw, name)
        except InvalidTag:
            log.error("cannot decrypt previous %s (passphrase changed?) - starting it fresh", name)
            return None
    return raw


def publish(name, obj, vlt, in_ci):
    """Write one site file. Encrypted when a passphrase is set. In CI without one, write a stub
    instead of the data: failing closed means a missing secret hides the dashboard rather than
    publishing the account in plaintext. Locally, plaintext is fine -- site/ is gitignored."""
    if vlt is not None:
        body = vlt.seal(obj, name)
    elif in_ci:
        body = LOCKED_STUB
    else:
        body = obj
    (OUT / name).write_text(json.dumps(body, indent=None if vlt else 1))


def web_push(new_alerts, cfg):
    key, subs = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip(), os.environ.get("PUSH_SUBSCRIPTIONS")
    if not (key and subs and new_alerts):
        return
    from pywebpush import WebPushException, webpush

    subs = json.loads(subs)
    if isinstance(subs, dict):
        subs = [subs]
    title = f"HL guardrails: {len(new_alerts)} new alert(s)"
    body = "\n".join(a["text"].split("\n")[0] for a in new_alerts)[:400]
    sent = 0
    for s in subs:
        try:
            webpush(
                subscription_info=s,
                data=json.dumps({"title": title, "body": body, "url": os.environ.get("SITE_URL", "")}),
                vapid_private_key=key,
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:alerts@example.com")},
                ttl=3600,
            )
            sent += 1
        except WebPushException as e:
            # status only: the exception text carries the subscription endpoint, and this log is public
            code = getattr(e.response, "status_code", None)
            hint = " (subscription expired: re-subscribe and update PUSH_SUBSCRIPTIONS)" if code in (404, 410) else ""
            log.error("webpush failed: HTTP %s%s", code, hint)
    log.info("push: %d/%d device(s)", sent, len(subs), extra={"safe": True})


def main():
    started = time.monotonic()
    setup_logging()
    cfg = load_config()
    require_account(cfg)
    site_url = os.environ.get("SITE_URL", "")
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    OUT.mkdir(exist_ok=True)

    raw = {n: fetch_prev(site_url, n) for n in FILES}
    pw = os.environ.get("DASHBOARD_PASSPHRASE")
    # reuse the published salt so the key is stable across runs (a device that unlocked stays so)
    salt = next((vault.salt_of(r) for r in raw.values() if vault.is_envelope(r)), None)
    vlt = vault.Vault(pw, salt) if pw else None
    if in_ci and vlt is None:
        log.error("DASHBOARD_PASSPHRASE is not set: publishing a locked stub, not account data")
    prev_state, prev, prev_hist = (load_prev(raw[n], n, vlt) for n in FILES)

    state_path = OUT / "state.json"
    if prev_state and not state_path.exists():
        state_path.write_text(json.dumps(prev_state))
    state = State(state_path)
    prev = prev or {}
    prev_keys = {a["key"] for a in prev.get("alerts", [])}

    inf = info()
    gn, sn = Notifier(cfg), Notifier(cfg)
    guardrails.run_once(cfg, inf, gn, state)
    rows = scanner.run_once(cfg, inf, sn, state)

    acct, st = account.load(inf, cfg["account"])
    positions = [
        {k: p["position"][k] for k in ("coin", "szi", "entryPx", "positionValue", "unrealizedPnl", "liquidationPx")}
        | {"leverage": p["position"]["leverage"]["value"], "margin_type": p["position"]["leverage"].get("type")}
        for p in st["assetPositions"]
    ]
    alerts = [{"kind": "guardrail", "key": k, "text": t} for k, t in gn.collected.items()]
    alerts += [{"kind": "scanner", "key": k, "text": t} for k, t in sn.collected.items()]
    new = [a for a in alerts if a["key"] not in prev_keys]
    for a in alerts:
        a["new"] = a["key"] not in prev_keys

    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "item"):
                r[k] = v.item()

    # Market structure and TradFi come off one extra asset-ctx call (~0.3s); liquidations are the
    # only genuinely external hop here and are hard time-boxed. Each is logged with its own elapsed
    # time so a repeat of the 300s FRED regression shows up in the run log, not as alert lag.
    S, F = cfg["scanner"], cfg.get("flow") or {}
    tradfi_coins = S.get("tradfi_coins") or []
    t0 = time.monotonic()
    ctx = scanner.ctx_map(inf, tradfi_coins)
    market_rows = market.ctx_rows(ctx, list(S["coins"]))
    tradfi_rows, tradfi_dropped = market.tradfi_rows(ctx, tradfi_coins)
    log.info("market: %d coins, %d tradfi (%d stale hidden) in %.1fs",
             len(market_rows), len(tradfi_rows), tradfi_dropped, time.monotonic() - t0, extra={"safe": True})

    liq = None
    if F.get("enabled", True):
        t1 = time.monotonic()
        got = liquidations.fetch(F.get("liquidation_coins") or ["BTC", "ETH", "SOL"],
                                 budget_s=F.get("budget_s", 10))
        log.info("liquidations: %d coin(s) in %.1fs", len(got), time.monotonic() - t1, extra={"safe": True})
        # Absent rather than empty when the source gave nothing: the dashboard then fetches OKX
        # itself, which works even if OKX is blocked from Actions the way FRED is.
        liq = {"src": "baked", "rows": got} if got else None
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "account": cfg["account"],
        # the sizing base (USDC collateral on a unified account); `acct` carries the rest. Not
        # "account": that key already holds the address string the dashboard header slices.
        "equity": acct["base"],
        "acct": acct,
        "locked_until": state.get("lock_until", 0),
        "day_start_equity": state.get("day_start_equity"),
        "week_start_equity": state.get("week_start_equity"),
        "day_pnl": state.get("day_pnl"),
        "week_pnl": state.get("week_pnl"),
        "positions": positions,
        "alerts": alerts,
        "scan": rows,
        "macro": scanner.macro_context(cfg["scanner"]),  # cached on disk by hlg.macro, so no second fetch
        "market": market_rows,
        "tradfi": {"rows": tradfi_rows, "dropped": tradfi_dropped},
        "liquidations": liq,
        "rules": cfg["rules"],
    }
    hist = prev_hist if isinstance(prev_hist, list) else []
    hist.append({"t": out["generated"], "equity": out["equity"], "n_alerts": len(alerts), "new": [a["key"] for a in new]})
    # state.json last: State wrote it in plaintext as it went, and this overwrites that
    publish("alerts.json", out, vlt, in_ci)
    publish("history.json", hist[-2000:], vlt, in_ci)
    publish("state.json", state.d, vlt, in_ci)
    log.info("%d alerts (%d new), %d positions", len(alerts), len(new), len(positions))

    # Without the passphrase every alert looks new each run (the previous file can't be read), so
    # pushing would spam every 15 minutes until the secret is set.
    if vlt is not None or not in_ci:
        try:
            web_push(new, cfg)
            if os.environ.get("HLG_TEST_PUSH") == "true":
                web_push([{"key": "test", "text": "Test notification: background alerts reach this device."}], cfg)
        except Exception as e:  # noqa: BLE001
            log.error("push error: %s", e)
    log.info("run ok: published %s in %.1fs", "encrypted" if vlt else ("locked stub" if in_ci else "plaintext (local)"),
             time.monotonic() - started, extra={"safe": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
