"""Alert lifecycle: what is live, what just ended and why, and what reaches the phone.

Every run rebuilds the alert list from whatever conditions hold right now. Without a lifecycle
that list only grows in meaning-free ways: a breakout from yesterday's close and one from an hour
ago look the same, and a signal that ran away or failed simply vanishes. This module carries each
alert across runs by key (first_seen), gives signals an expiry, and keeps the ones that ended for
24h with the reason, so the dashboard can show "live", "missed", "failed" or "expired".

Categories, in the order the dashboard shows them:
  position  guardrail breach on the account or an open position (always pushed, never clearable)
  entry     live breakout entry signal (pushed)
  heads_up  momentum spike (pushed)
  info      funding carry, breakout on a coin already held (dashboard only)
"""
ENDED_KEEP_MS = 24 * 3600_000
CATS = ("position", "entry", "heads_up", "info")


def summary_of(text, limit=110):
    first = text.split("\n", 1)[0]
    return first if len(first) <= limit else first[: limit - 1] + "…"


def ended_status(a, px):
    """Why a signal left the live list. `px` is the coin's current price, if known."""
    m = a.get("meta") or {}
    if a.get("cat") != "entry" or px is None:
        return "expired", None
    if m.get("signal_close") is not None and m.get("atr") and px > m["signal_close"] + m["atr"]:
        return "missed", f"ran {(px / m['signal_close'] - 1) * 100:.1f}% past the signal close"
    if m.get("level") is not None and px < m["level"]:
        return "failed", f"back below the breakout level {m['level']:.5g}"
    return "expired", "entry window passed"


def build(kind_notifiers, prev, now_ms, px_by_coin=None):
    """kind_notifiers: [("guardrail", Notifier), ("scanner", Notifier)].
    prev: the previous decrypted alerts payload (dict with "alerts" and optional "ended"), or {}.
    Returns (alerts, ended). New = key not live in the previous run."""
    px_by_coin = px_by_coin or {}
    prev_live = {a["key"]: a for a in (prev or {}).get("alerts", [])}
    alerts = []
    for kind, n in kind_notifiers:
        for key, text in n.collected.items():
            # numpy scalars from the scanner would not survive json.dumps
            meta = {k: (v.item() if hasattr(v, "item") else v) for k, v in getattr(n, "meta", {}).get(key, {}).items()}
            cat = meta.pop("cat", "position" if kind == "guardrail" else "info")
            push = meta.pop("push", True)
            summary = meta.pop("summary", None) or summary_of(text)
            valid_for = meta.pop("valid_for_ms", None)
            before = prev_live.get(key)
            first = (before or {}).get("first_seen") or now_ms
            a = {"kind": kind, "cat": cat, "key": key, "text": text, "summary": summary, "push": bool(push),
                 "new": before is None, "first_seen": first, "status": "live"}
            valid_until = meta.pop("valid_until", None) or (first + valid_for if valid_for else None)
            if valid_until:
                a["valid_until"] = valid_until
            if meta:
                a["meta"] = meta
            alerts.append(a)
    live_keys = {a["key"] for a in alerts}
    ended = [e for e in (prev or {}).get("ended", []) if now_ms - e.get("ended_at", 0) < ENDED_KEEP_MS
             and e["key"] not in live_keys]
    for key, a in prev_live.items():
        # guardrail breaches that clear just disappear: "no stop" fixed is good news, not history
        if key in live_keys or a.get("cat") in (None, "position"):
            continue
        coin = (a.get("meta") or {}).get("coin")
        status, why = ended_status(a, px_by_coin.get(coin))
        ended.append({**a, "status": status, "why": why, "ended_at": now_ms, "new": False})
    alerts.sort(key=lambda a: (CATS.index(a["cat"]) if a["cat"] in CATS else 9, -a["first_seen"]))
    ended.sort(key=lambda e: -e["ended_at"])
    return alerts, ended
