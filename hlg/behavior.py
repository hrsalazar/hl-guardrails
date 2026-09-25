"""Your own trading record, as evidence for the aids that target the costly behaviours.

An account's own fills can show where its losses really come from. For the account this was built
for, it wasn't trading too often but adding to losing positions: the averaged-down trades carried
the whole loss, the rest were net positive, and the few worst trades were all ladders of buys below
the average price. That is the disposition effect / loss aversion pattern the behavioural-finance
literature describes (README "Discipline aids"). The figures themselves are private: they live
only in the encrypted state, never in this public repo.

This module rebuilds round trips from the account's public fills (flat -> position -> flat, per
coin) once an hour and keeps, in the encrypted state only:

  stats         averaged-down vs never-averaged-down trades, their counts and net P&L; win rate;
                average win vs average loss; the five worst trades' share
  last_add_under  the last fill that added to a position below its average entry (above, for a
                short) -- the streak "days without adding to a loser" counts from it
  recent        the last 10 closed trades, each marked clean or not (clean = never added while
                underwater, and not a loser held past 7 days)

The P&L figures never go to logs (public on a public repo) and never onto a push's first line
(the lock screen); only the dashboard, decrypted in the browser, shows them.
"""
import requests

from .common import API, log

H_MS, D_MS = 3_600_000, 86_400_000
LOOKBACK_DAYS = 180
REFRESH_HOURS = 1


def fetch_fills(account, now_ms, days=LOOKBACK_DAYS, post=requests.post):
    out, t0 = [], now_ms - days * D_MS
    while True:
        r = post(f"{API}/info", json={"type": "userFillsByTime", "user": account, "startTime": t0, "endTime": now_ms},
                 timeout=30)
        r.raise_for_status()
        batch = r.json() or []
        out += batch
        if len(batch) < 2000:
            return out
        t0 = batch[-1]["time"] + 1


def round_trips(fills):
    """Perp round trips from raw fills. Returns (closed, open). A fill that increases |position|
    below the running average entry (above, for a short) is an 'add under'."""
    seen, rows = set(), []
    for f in fills:
        c = f.get("coin", "")
        if c.startswith("@") or ":" in c:            # spot and other dexes: not perp round trips
            continue
        k = (f.get("hash"), f.get("oid"), f.get("time"), f.get("sz"), f.get("px"))
        if k in seen:
            continue
        seen.add(k)
        rows.append(f)
    rows.sort(key=lambda f: f["time"])
    closed, cur = [], {}
    for f in rows:
        coin, px, sz = f["coin"], float(f["px"]), float(f["sz"])
        sgn = 1 if f["side"] == "B" else -1
        pos0 = float(f["startPosition"])
        pos1 = pos0 + sgn * sz
        t = cur.get(coin)
        if t is None and abs(pos0) < 1e-12:
            t = cur[coin] = {"coin": coin, "open": f["time"], "side": sgn, "pnl": 0.0, "fees": 0.0,
                             "avg": px, "qty": 0.0, "adds_under": 0, "last_add_under": None}
        if t is None:
            continue                                  # position predates the lookback: can't attribute
        t["pnl"] += float(f.get("closedPnl") or 0)
        t["fees"] += float(f.get("fee") or 0)
        if abs(pos1) > abs(pos0) + 1e-12:             # opening / adding
            if t["qty"] > 0 and ((px < t["avg"]) if t["side"] > 0 else (px > t["avg"])):
                t["adds_under"] += 1
                t["last_add_under"] = f["time"]
            t["avg"] = (t["avg"] * t["qty"] + px * sz) / (t["qty"] + sz) if t["qty"] > 0 else px
            t["qty"] += sz
        if abs(pos1) < 1e-12:
            t["close"] = f["time"]
            t["net"] = t["pnl"] - t["fees"]
            closed.append(t)
            del cur[coin]
    open_ = list(cur.values())
    return closed, open_


def clean(t):
    return t["adds_under"] == 0 and not (t["net"] < 0 and t["close"] - t["open"] > 7 * D_MS)


def summarize(closed, open_, now_ms):
    if not closed and not open_:
        return None
    avg = [t for t in closed if t["adds_under"]]
    never = [t for t in closed if not t["adds_under"]]
    wins = [t["net"] for t in closed if t["net"] > 0]
    losses = [t["net"] for t in closed if t["net"] < 0]
    worst5 = sorted(t["net"] for t in closed)[:5]
    last_add = max((t["last_add_under"] for t in closed + open_ if t["last_add_under"]), default=None)
    return {
        "t": now_ms, "days": LOOKBACK_DAYS, "trades": len(closed),
        "net": round(sum(t["net"] for t in closed), 2),
        "avgdown_n": len(avg), "avgdown_net": round(sum(t["net"] for t in avg), 2),
        "never_n": len(never), "never_net": round(sum(t["net"] for t in never), 2),
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
        "worst5_net": round(sum(worst5), 2),
        "last_add_under": last_add,
        "streak_days": round((now_ms - last_add) / D_MS, 1) if last_add else None,
        "recent": [{"coin": t["coin"], "t": t["close"], "net": round(t["net"], 2), "clean": clean(t),
                    "adds_under": t["adds_under"]} for t in sorted(closed, key=lambda t: t["close"])[-10:]],
    }


def refresh(state, account, now_ms, fetch=fetch_fills):
    """Recompute at most once an hour. Fail-soft: the previous record stays."""
    cur = state.get("behavior")
    if isinstance(cur, dict) and now_ms - cur.get("t", 0) < REFRESH_HOURS * H_MS:
        return cur
    try:
        closed, open_ = round_trips(fetch(account, now_ms))
        b = summarize(closed, open_, now_ms)
    except Exception as e:  # noqa: BLE001 - evidence only; never fails the run
        log.error("behavior: fills fetch failed: %s", e)
        return cur
    if b is not None:
        state.set("behavior", b)
        log.info("behavior: %d round trips analysed", b["trades"], extra={"safe": True})
    return b


def evidence(state):
    """The one line that goes under an averaging-down warning (dashboard only, never the push's
    first line). None when there is no record yet."""
    b = state.get("behavior") if state is not None else None
    if not isinstance(b, dict) or not b.get("avgdown_n"):
        return None
    return (f"Your last {b['days']} days: the {b['avgdown_n']} trades you averaged down netted {_usd(b['avgdown_net'])}; "
            f"the {b['never_n']} you never added to while losing netted {_usd(b['never_net'], plus=True)}.")


def _usd(v, plus=False):
    return f"{'-' if v < 0 else '+' if plus else ''}${abs(v):,.0f}"
