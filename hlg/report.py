"""One-shot run for GitHub Actions: guardrails + scanner -> site/alerts.json, Web Push, GitHub Issue.

Env:
  SITE_URL              public Pages URL (to fetch previous state/alerts for de-dup)
  VAPID_PRIVATE_KEY     (optional) base64url VAPID private key -> Web Push
  VAPID_SUBJECT         (optional) mailto:you@example.com
  PUSH_SUBSCRIPTIONS    (optional) JSON list of PushSubscription objects (or a single object)
  GITHUB_TOKEN + GITHUB_REPOSITORY (set by Actions) -> issue comments
"""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

from . import guardrails, liquidations, market, scanner
from .common import Notifier, State, fnum, info, load_config, log, setup_logging

OUT = Path("site")


def fetch_prev(site_url, name):
    if not site_url:
        return None
    try:
        r = requests.get(f"{site_url.rstrip('/')}/{name}", timeout=15)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def web_push(new_alerts, cfg):
    key, subs = os.environ.get("VAPID_PRIVATE_KEY"), os.environ.get("PUSH_SUBSCRIPTIONS")
    if not (key and subs and new_alerts):
        return
    from pywebpush import WebPushException, webpush

    subs = json.loads(subs)
    if isinstance(subs, dict):
        subs = [subs]
    title = f"HL guardrails: {len(new_alerts)} new alert(s)"
    body = "\n".join(a["text"].split("\n")[0] for a in new_alerts)[:400]
    for s in subs:
        try:
            webpush(
                subscription_info=s,
                data=json.dumps({"title": title, "body": body, "url": os.environ.get("SITE_URL", "")}),
                vapid_private_key=key,
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:alerts@example.com")},
                ttl=3600,
            )
        except WebPushException as e:
            log.error("webpush failed: %s", e)


def github_issue(new_alerts):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo and new_alerts):
        return
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/issues"
    r = requests.get(api, headers=h, params={"labels": "alerts", "state": "open"}, timeout=15)
    r.raise_for_status()
    body = "\n\n".join(f"**{a['kind'].upper()}** — `{a['key']}`\n```\n{a['text']}\n```" for a in new_alerts)
    body += f"\n\n_{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC_"
    if r.json():
        num = r.json()[0]["number"]
        requests.post(f"{api}/{num}/comments", headers=h, json={"body": body}, timeout=15).raise_for_status()
    else:
        requests.post(api, headers=h, json={"title": "HL guardrails alerts", "body": body, "labels": ["alerts"]}, timeout=15).raise_for_status()


def main():
    setup_logging()
    cfg = load_config()
    site_url = os.environ.get("SITE_URL", "")
    OUT.mkdir(exist_ok=True)

    state_path = OUT / "state.json"
    prev_state = fetch_prev(site_url, "state.json")
    if prev_state and not state_path.exists():
        state_path.write_text(json.dumps(prev_state))
    state = State(state_path)
    prev = fetch_prev(site_url, "alerts.json") or {}
    prev_keys = {a["key"] for a in prev.get("alerts", [])}

    inf = info()
    gn, sn = Notifier(cfg), Notifier(cfg)
    guardrails.run_once(cfg, inf, gn, state)
    rows = scanner.run_once(cfg, inf, sn, state)

    st = inf.user_state(cfg["account"])
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
             len(market_rows), len(tradfi_rows), tradfi_dropped, time.monotonic() - t0)

    liq = None
    if F.get("enabled", True):
        t1 = time.monotonic()
        got = liquidations.fetch(F.get("liquidation_coins") or ["BTC", "ETH", "SOL"],
                                 budget_s=F.get("budget_s", 10))
        log.info("liquidations: %d coin(s) in %.1fs", len(got), time.monotonic() - t1)
        # Absent rather than empty when the source gave nothing: the dashboard then fetches OKX
        # itself, which works even if OKX is blocked from Actions the way FRED is.
        liq = {"src": "baked", "rows": got} if got else None
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "account": cfg["account"],
        "equity": fnum(st["marginSummary"]["accountValue"]),
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
        "margin": market.margin_health(st),
        "tradfi": {"rows": tradfi_rows, "dropped": tradfi_dropped},
        "liquidations": liq,
        "rules": cfg["rules"],
    }
    (OUT / "alerts.json").write_text(json.dumps(out, indent=1))
    hist = fetch_prev(site_url, "history.json") or []
    hist.append({"t": out["generated"], "equity": out["equity"], "n_alerts": len(alerts), "new": [a["key"] for a in new]})
    (OUT / "history.json").write_text(json.dumps(hist[-2000:]))
    log.info("%d alerts (%d new), %d positions", len(alerts), len(new), len(positions))

    try:
        web_push(new, cfg)
    except Exception as e:  # noqa: BLE001
        log.error("push error: %s", e)
    try:
        github_issue(new)
    except Exception as e:  # noqa: BLE001
        log.error("issue error: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
