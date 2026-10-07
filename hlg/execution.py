"""Execution gap: your real trades against the signals the journal follows.

The journal records what each breakout signal did under the rule's own mechanics; your fills record
what you actually did. The difference is where an account's results leave the backtest -- for the
account this was built for, its losses came from execution (adding to losers), not from the rule.
Hourly, from public fills and the order history, kept in the encrypted state only:

  taken / skipped   a signal counts as taken when a long on its coin opened from flat between the
                    signal close and two bars after it; skipped signals keep their journal R, so
                    "what skipping cost (or saved)" is a number
  slippage          your first-hour average entry vs the journal's entry (the next open), in ATR
  stop              when the first reduce-only stop on the coin went in: before the entry, minutes
                    after it, or never while the position was open (unknown if the order history,
                    capped at 2,000 orders, doesn't reach back)
  size              your first-hour quantity vs the rule's size at the current equity base (approx.)
  off-plan          positions opened since tracking began that match no signal, and their net P&L

Never logged beyond counts; P&L stays in the encrypted payload.
"""
from . import behavior
from .common import API, fnum, log

H_MS = 3_600_000
D_MS = 86_400_000
BAR_MS = {"1d": 86_400_000, "4h": 4 * H_MS, "1h": H_MS}
WINDOW_BARS = 2          # a fill up to two bars after the signal close still counts as taking it
LOOKBACK_DAYS = 60
REFRESH_HOURS = 1
EPS = 1e-12


def positions_from_fills(fills):
    """Positions opened from flat, per coin: start time, side, first-hour average price and quantity,
    end time (None while open) and net P&L (closed P&L less fees) over the position's life."""
    out, cur = [], {}
    seen = set()
    rows = []
    for f in fills:
        k = (f.get("hash"), f.get("oid"), f.get("time"), f.get("sz"), f.get("px"))
        if k in seen or str(f.get("coin", "")).startswith("@"):
            continue
        seen.add(k)
        rows.append(f)
    for f in sorted(rows, key=lambda f: f["time"]):
        coin, px, sz = f["coin"], fnum(f["px"]), fnum(f["sz"])
        sgn = 1 if f["side"] == "B" else -1
        pos0 = fnum(f["startPosition"])
        pos1 = pos0 + sgn * sz
        p = cur.get(coin)
        if p is None and abs(pos0) < EPS:
            p = cur[coin] = {"coin": coin, "t0": f["time"], "side": sgn, "qty": 0.0, "cost": 0.0,
                             "t_end": None, "net": 0.0}
        if p is None:
            continue                                     # opened before the lookback: can't attribute
        p["net"] += fnum(f.get("closedPnl")) - fnum(f.get("fee"))
        if abs(pos1) > abs(pos0) and f["time"] - p["t0"] <= H_MS:
            p["qty"] += sz
            p["cost"] += sz * px
        if abs(pos1) < EPS:
            p["t_end"] = f["time"]
            out.append(p)
            del cur[coin]
    out += list(cur.values())
    for p in out:
        p["px"] = p["cost"] / p["qty"] if p["qty"] else None
    return out


def first_stop(orders, coin, side, t0, t_end):
    """Minutes from the entry to the first reduce-only stop on the coin (<= 0: placed before it), None
    if none went in while the position was open, 'unknown' if the order history doesn't reach back."""
    if orders and len(orders) >= 2000 and min(o["order"]["timestamp"] for o in orders) > t0:
        return "unknown"
    want = "A" if side > 0 else "B"
    ts = [o["order"]["timestamp"] for o in orders
          if o["order"].get("coin") == coin and o["order"].get("reduceOnly") and o["order"].get("side") == want
          and "Stop" in (o["order"].get("orderType") or "")
          and o["order"]["timestamp"] >= t0 - 10 * 60_000 and (t_end is None or o["order"]["timestamp"] <= t_end)]
    if not ts:
        return None
    return round((min(ts) - t0) / 60_000, 1)


def evaluate(journal, fills, orders, equity, risk_pct, now_ms):
    """The execution summary for the dashboard (pure, so it tests against fixtures)."""
    pos = positions_from_fills(fills)
    since = min((e.get("added") or now_ms for e in journal), default=now_ms)
    used, rows = set(), []
    risk_usd = equity * risk_pct / 100 if equity else None
    for e in sorted(journal, key=lambda e: e.get("sig_t") or 0):
        bar = BAR_MS.get(e.get("tf"), BAR_MS["1d"])
        tc = (e.get("sig_t") or 0) + bar
        end = tc + WINDOW_BARS * bar
        match = next((i for i, p in enumerate(pos) if i not in used and p["coin"] == e["coin"] and p["side"] > 0
                      and tc - 5 * 60_000 <= p["t0"] <= end), None)
        if match is None and now_ms < end:
            continue                                     # still inside the window: no verdict yet
        row = {"coin": e["coin"], "tf": e["tf"], "signal_day": e["signal_day"], "r": e.get("r"),
               "status": e.get("status"), "taken": match is not None, "tc": tc}
        if match is not None:
            used.add(match)
            p = pos[match]
            atr = e.get("atr") or 0
            row["slip_atr"] = round((p["px"] - e["entry"]) / atr, 2) if p["px"] and e.get("entry") and atr else None
            row["stop_min"] = first_stop(orders, p["coin"], p["side"], p["t0"], p["t_end"])
            plan_qty = risk_usd / e["risk"] if risk_usd and e.get("risk") else None
            row["size_x"] = round(p["qty"] / plan_qty, 2) if plan_qty and p["qty"] else None
            # your realised result on this signal in R (1R = the rule's risk at the current equity base)
            row["your_r"] = round(p["net"] / risk_usd, 2) if risk_usd and p["t_end"] is not None else None
        rows.append(row)
    off = [p for i, p in enumerate(pos) if i not in used and p["t0"] >= since]
    taken = [r for r in rows if r["taken"]]
    skipped = [r for r in rows if not r["taken"]]
    med = lambda xs: (sorted(xs)[len(xs) // 2] if xs else None)  # noqa: E731
    both = [r for r in taken if r.get("your_r") is not None and r["status"] in ("stopped", "time") and r["r"] is not None]
    # the weekly review: this week and last, Monday 00:00 UTC (the weekly loss limit's week)
    w0 = now_ms - ((now_ms // D_MS + 3) % 7) * D_MS - now_ms % D_MS      # 1970-01-01 was a Thursday
    weeks = {}
    for name, a, b in (("this", w0, now_ms + 1), ("last", w0 - 7 * D_MS, w0)):
        wr = [r for r in rows if a <= r["tc"] < b]
        wo = [p for p in off if a <= p["t0"] < b]
        wt = [r for r in wr if r["taken"]]
        weeks[name] = {"start": a, "signals": len(wr), "taken": len(wt), "skipped": len(wr) - len(wt),
                       "no_stop": sum(r.get("stop_min") is None for r in wt),
                       "skipped_r": round(sum(r["r"] or 0 for r in wr if not r["taken"] and r["status"] in ("stopped", "time")), 2),
                       "offplan": len(wo), "offplan_net": round(sum(p["net"] for p in wo if p["t_end"] is not None), 2)}
    stops = [r["stop_min"] for r in taken]
    return {
        "t": now_ms, "since": since, "signals": len(rows), "taken": len(taken), "skipped": len(skipped),
        "taken_r": round(sum(r["r"] or 0 for r in taken), 2),
        "skipped_r": round(sum(r["r"] or 0 for r in skipped if r["status"] in ("stopped", "time")), 2),
        "slip_atr_med": med([r["slip_atr"] for r in taken if r.get("slip_atr") is not None]),
        "stop_before": sum(isinstance(s, (int, float)) and s <= 1 for s in stops),
        "stop_delay_med": med([s for s in stops if isinstance(s, (int, float)) and s > 1]),
        "no_stop": sum(s is None for s in stops), "stop_unknown": sum(s == "unknown" for s in stops),
        "size_x_med": med([r["size_x"] for r in taken if r.get("size_x") is not None]),
        "offplan": len(off), "offplan_net": round(sum(p["net"] for p in off if p["t_end"] is not None), 2),
        "offplan_open": sum(p["t_end"] is None for p in off),
        # the same signals, both closed: what the rule made vs what you made (the execution cost in R)
        "same": {"n": len(both), "rule_r": round(sum(r["r"] for r in both), 2), "your_r": round(sum(r["your_r"] for r in both), 2)},
        "rows": rows[-10:], "weeks": weeks,
    }


def fetch_orders(account, post):
    r = post(f"{API}/info", json={"type": "historicalOrders", "user": account}, timeout=30)
    r.raise_for_status()
    return r.json() or []


def refresh(state, account, now_ms, equity, risk_pct, fetch=behavior.fetch_fills, orders_fetch=None):
    """At most hourly. Fail-soft: the previous summary stays."""
    cur = state.get("execution")
    if isinstance(cur, dict) and now_ms - cur.get("t", 0) < REFRESH_HOURS * H_MS:
        return cur
    try:
        import requests

        fills = fetch(account, now_ms, days=LOOKBACK_DAYS)
        orders = (orders_fetch or (lambda a: fetch_orders(a, requests.post)))(account)
        x = evaluate(state.get("journal") or [], fills, orders, equity, risk_pct, now_ms)
    except Exception as e:  # noqa: BLE001 - evidence only; never fails the run
        log.error("execution: fills/orders fetch failed: %s", type(e).__name__)
        return cur
    state.set("execution", x)
    log.info("execution: %d signals, %d taken, %d off-plan", x["signals"], x["taken"], x["offplan"], extra={"safe": True})
    return x
