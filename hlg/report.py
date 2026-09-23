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

from . import account, digest, events, guardrails, journal, liqmap, liquidations, market, scanner, sentiment, universe, vault
from . import alerts as lifecycle
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


def add_stops(positions, inf, cfg, acct):
    """Stop levels for the dashboard's position ladder: the protective stop actually resting on the
    book (guardrails.stop_coverage, the same reading the NO STOP rule uses) and the stop that would
    cap the loss at risk_per_trade_pct. One extra request; a failure just leaves the ladder without
    them."""
    try:
        oo = inf.post("/info", {"type": "frontendOpenOrders", "user": cfg["account"]})
    except Exception as e:  # noqa: BLE001
        log.error("open orders for the ladder failed: %s", e)
        return
    risk_usd = acct["base"] * cfg["rules"]["risk_per_trade_pct"] / 100
    for p in positions:
        sz, entry = float(p["szi"]), float(p["entryPx"])
        cov, trig = guardrails.stop_coverage(oo, p["coin"], sz)
        p["stop_px"], p["stop_cov"] = trig, (cov / abs(sz)) if sz else 0.0
        p["risk_stop_px"] = entry - risk_usd / abs(sz) if sz > 0 else entry + risk_usd / abs(sz)


def thin_history(hist, now_ms, full_days=3, hourly_days=90):
    """Every run for the last few days, one point per hour back to 90 days, one per day before
    that: months of equity curve in a file that stays small."""
    def ms(h):
        try:
            return int(dt.datetime.fromisoformat(h["t"]).timestamp() * 1000)
        except (KeyError, TypeError, ValueError):
            return 0
    out, seen = [], set()
    for h in reversed(hist):
        age = now_ms - ms(h)
        if age <= full_days * 86_400_000:
            out.append(h)
            continue
        bucket = ("h", ms(h) // 3_600_000) if age <= hourly_days * 86_400_000 else ("d", ms(h) // 86_400_000)
        if bucket not in seen:  # newest point in each bucket wins
            seen.add(bucket)
            out.append(h)
    return list(reversed(out))[-4000:]


def liqmap_payload(m, px_now):
    """The dashboard's view of the stored map: clusters with distances against the current price."""
    if not isinstance(m, dict) or not m.get("coins"):
        return None
    coins = {}
    for c, e in m["coins"].items():
        px = px_now.get(c) or e["px"]
        coins[c] = {"px": px, "coverage": e.get("coverage"),
                    "clusters": [dict(k, pct=(k["px"] / px - 1) * 100) for k in e["clusters"]],
                    "near": liqmap.near(e, px)}
    return {"t": m["t"], "accounts": m.get("accounts"), "positions": m.get("positions"), "coins": coins}


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
    try:  # a leftover envelope from an earlier encrypted local run is not state: start clean
        if state_path.exists() and vault.is_envelope(json.loads(state_path.read_text())):
            state_path.unlink()
    except ValueError:
        state_path.unlink()
    if prev_state and not state_path.exists():
        state_path.write_text(json.dumps(prev_state))
    state = State(state_path)
    prev = prev or {}

    inf = info()
    now_ms = int(time.time() * 1000)
    # Scheduled releases first: the scanner reads them to flag entries opened into one. Cached in
    # state and refreshed every few hours, so most runs make no request here.
    E = events.settings(cfg)
    if E["enabled"]:
        try:
            events.refresh(state, now_ms, E)
        except Exception as e:  # noqa: BLE001 - context data; never fails the run
            log.error("events failed: %s", e)
    gn, sn, en = Notifier(cfg), Notifier(cfg), Notifier(cfg)
    guardrails.run_once(cfg, inf, gn, state)
    rows = scanner.run_once(cfg, inf, sn, state)

    acct, st = account.load(inf, cfg["account"])
    positions = [
        {k: p["position"][k] for k in ("coin", "szi", "entryPx", "positionValue", "unrealizedPnl", "liquidationPx")}
        | {"leverage": p["position"]["leverage"]["value"], "margin_type": p["position"]["leverage"].get("type")}
        for p in st["assetPositions"]
    ]
    add_stops(positions, inf, cfg, acct)
    if E["enabled"]:
        events.alerts(en, state.get("events"), now_ms, E, held={p["coin"] for p in positions})
    px_by_coin = {r["coin"]: r["px"] for r in rows if r.get("px") is not None}
    alerts, ended = lifecycle.build([("guardrail", gn), ("scanner", sn), ("event", en)], prev, now_ms, px_by_coin)
    # push = the alert's own flag (funding carry and already-held breakouts are dashboard-only)
    new = [a for a in alerts if a["new"] and a["push"]]

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
    # the coins actually scanned this run: scanner.coins, or today's liquidity universe (hlg.universe)
    scanned = list(dict.fromkeys(r["coin"] for r in rows if not r.get("watch"))) or list(S["coins"])
    market_rows = market.ctx_rows(ctx, scanned)
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
    try:
        sentiment.refresh(state, now_ms)  # one small request every 6h
    except Exception as e:  # noqa: BLE001
        log.error("fear & greed failed: %s", e)
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
        "ended": ended,  # signals that expired, ran away or failed in the last 24h (hlg.alerts)
        "scan": rows,
        "macro": scanner.macro_context(cfg["scanner"]),  # cached on disk by hlg.macro, so no second fetch
        "market": market_rows,
        "tradfi": {"rows": tradfi_rows, "dropped": tradfi_dropped},
        "liquidations": liq,
        "rules": cfg["rules"],
        # live record of every breakout signal under the strategy's rules (hlg.journal)
        "journal": {"summary": journal.summary(state.get("journal") or []),
                    "recent": list(reversed((state.get("journal") or [])[-30:]))},
        "universe": {"mode": universe.settings(S)["mode"], "coins": len(scanned),
                     "day": (state.get("universe") or {}).get("day")},
        "liqmap": liqmap_payload(state.get("liqmap"), {r["coin"]: r["px"] for r in market_rows if r.get("px")}),
        "events": events.payload(state.get("events"), now_ms) if E["enabled"] else None,
        "fng": state.get("fng"),
        "digest": state.get("digest"),
    }
    hist = prev_hist if isinstance(prev_hist, list) else []
    hist.append({"t": out["generated"], "equity": out["equity"], "pv": acct.get("portfolio_value"),
                 "n_alerts": len(alerts), "new": [a["key"] for a in new]})
    hist = thin_history(hist, now_ms)
    # state.json last: State wrote it in plaintext as it went, and this overwrites that
    publish("alerts.json", out, vlt, in_ci)
    publish("history.json", hist, vlt, in_ci)
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
    # Liquidation levels from the largest HL accounts (hlg.liqmap): ~25s, so only every few hours,
    # and only after alerts are published and pushed -- a refresh can never delay a notification.
    L = liqmap.settings(cfg)
    if L["enabled"] and liqmap.stale(state, now_ms, L["refresh_hours"]):
        try:
            coins = sorted(set(scanned) | {p["coin"] for p in positions})
            mids = {r["coin"]: r["px"] for r in market_rows if r.get("px")}
            for c in coins:
                if c not in mids and ctx.get(c, {}).get("markPx"):
                    mids[c] = float(ctx[c]["markPx"])
            oi = {c: float(ctx[c].get("openInterest") or 0) * mids[c] for c in coins if c in ctx and c in mids}
            m = liqmap.refresh(state, mids, coins, oi, L, now_ms, universe.today())
            if m is not None:
                out["liqmap"] = liqmap_payload(m, mids)
                publish("alerts.json", out, vlt, in_ci)
                publish("state.json", state.d, vlt, in_ci)
        except Exception as e:  # noqa: BLE001 - context data; never fails the run
            log.error("liqmap failed: %s", e)
    # Daily brief (hlg.digest): once per UTC day, last, for the same reason as liqmap. Only public
    # data goes into the prompt -- the tape, the calendar, the index, headlines; nothing from the
    # account. The attempt time is published whatever happens, so a failure (or a paid call whose
    # reply didn't parse) retries hours later, not on every 15-minute run.
    D = digest.settings(cfg)
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if D["enabled"] and api_key and digest.due(state, now_ms, D):
        try:
            tape = ([{"name": r["coin"], "chg24h_pct": r.get("chg24h_pct")} for r in market_rows if r["coin"] in ("BTC", "ETH", "SOL")]
                    + [{"name": r["coin"].replace("xyz:", ""), "chg24h_pct": r.get("chg24h_pct")} for r in tradfi_rows])
            brief = digest.run(state, now_ms, D, {"fng": state.get("fng"), "tape": tape,
                                                  "events": events.groups(state.get("events"), now_ms, now_ms + 7 * 86_400_000)},
                               api_key)
            if brief:
                out["digest"] = brief
                publish("alerts.json", out, vlt, in_ci)
        except Exception as e:  # noqa: BLE001 - reading material; never fails the run
            log.error("digest failed: %s", e)
        finally:
            publish("state.json", state.d, vlt, in_ci)
    log.info("run ok: published %s in %.1fs", "encrypted" if vlt else ("locked stub" if in_ci else "plaintext (local)"),
             time.monotonic() - started, extra={"safe": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
