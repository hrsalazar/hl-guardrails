"""Live tracking of the 1h -> 4h reversal cascade as an early warning of a 1d breakout.

README "Earlier entries": on history a confirmed cascade was followed by a 1d breakout within 10
days 45.8% of the time vs 31.9% from any bar in the same context -- a 1.44x lift against the 1.5x
bar set in advance, so it is not a notification. This collects the evidence live, with exactly the
study's detector (hlg.reversal.cascade): each confirmed cascade on a scanned coin is recorded, then
resolved from the signal journal -- "breakout" if a 1d breakout signal's entry lands within 10 days,
"no breakout" after that -- with how many days it led by and how much better its price was, in 1d
ATRs. Tracking only: nothing is alerted or advised from it.

Cost: 1h candles once per scanned coin per hour (on the first run after the hour closes) and 1d
candles once per coin per day, run after alerts are pushed so it never delays one.
"""
import pandas as pd

from . import reversal as rv
from .common import log
from .scanner import candles

H_MS, D_MS = 3_600_000, 86_400_000
LEAD_DAYS = rv.LIFT_DAYS
KEEP = 200
BACKTEST = {"hit_rate": 0.458, "base_rate": 0.319, "lift_bar": 1.5}


def _hourly(inf, coin, now_ms):
    d = candles(inf, coin, "1h", 8)
    d = d[d.t.astype("int64") // 1_000_000 + H_MS <= now_ms]      # completed bars only
    return d.set_index("t")[["o", "h", "l", "c", "v"]]


def _daily_rows(inf, coin, now_ms):
    d = candles(inf, coin, "1d", 120)
    d = d[d.t.astype("int64") // 1_000_000 + D_MS <= now_ms].set_index("t")[["o", "h", "l", "c", "v"]]
    x = rv.daily_context(d).iloc[-6:]
    return [[int(t.timestamp() * 1000), bool(r.up), bool(r.broke), float(r.atr)] for t, r in x.iterrows()]


def _ctx_frame(rows):
    return pd.DataFrame({"up": [r[1] for r in rows], "broke": [r[2] for r in rows], "atr": [r[3] for r in rows]},
                        index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True))


def _detect(h1, d):
    h4 = rv.resample(h1, "4h", 4)
    return rv.cascade(h1, h4, d, rv.swings(h1), rv.swings(h4))


def update(inf, coins, state, now_ms, fetch_1h=_hourly, fetch_daily=_daily_rows, detect=_detect):
    """Record new confirmed cascades, then resolve open ones against the journal. Returns True if
    anything changed (so the caller knows to republish state)."""
    cw = dict(state.get("cascade_watch") or {})
    ctx, last_h, events = dict(cw.get("ctx") or {}), dict(cw.get("last_h") or {}), list(cw.get("events") or [])
    day, hour = now_ms // D_MS * D_MS, now_ms // H_MS * H_MS
    known = {e["key"] for e in events}
    changed = False
    for coin in coins:
        try:
            if (ctx.get(coin) or {}).get("day") != day:
                ctx[coin] = {"day": day, "rows": fetch_daily(inf, coin, now_ms)}
            if last_h.get(coin) == hour:
                continue                                   # no new 1h bar since the last look
            h1 = fetch_1h(inf, coin, now_ms)
            last_h[coin] = hour
        except Exception as e:  # noqa: BLE001 - tracking only; never fails the run
            log.error("cascade watch %s: %s", coin, e)
            continue
        if len(h1) < 60:
            continue
        for e in detect(h1, _ctx_frame(ctx[coin]["rows"])):
            t = int(e["t"].timestamp() * 1000)
            key = f"{coin}|{t}"
            if key in known:
                continue
            ref = h1.c[h1.index < e["t"]]
            px = float(ref.iloc[-1]) if len(ref) else None
            # the study's population: a stop within MAX_RISK_ATR daily ATRs (wider is no better entry
            # than the breakout itself), so the live hit rate stays comparable with the backtest's
            if px is None or not 0 < px - e["stop"] <= rv.MAX_RISK_ATR * e["a"]:
                continue
            events.append({"key": key, "coin": coin, "t": t, "px": px,
                           "stop": float(e["stop"]), "atr": float(e["a"]), "status": "watching"})
            known.add(key)
            changed = True
    changed |= resolve(events, state.get("journal") or [], now_ms)
    events = sorted(events, key=lambda e: e["t"])[-KEEP:]
    state.set("cascade_watch", {"ctx": ctx, "last_h": last_h, "events": events})
    return changed


def resolve(events, journal, now_ms):
    """A 1d breakout whose entry (signal bar start + 1 day) falls in (t, t + 10 days] resolves an
    event as a hit, as in the study; past that window with none, a miss."""
    changed = False
    for ev in events:
        if ev["status"] != "watching":
            continue
        hits = sorted((e for e in journal if e.get("coin") == ev["coin"] and e.get("tf") == "1d" and e.get("sig_t")
                       and ev["t"] < e["sig_t"] + D_MS <= ev["t"] + LEAD_DAYS * D_MS), key=lambda e: e["sig_t"])
        if hits:
            b = hits[0]
            px = b.get("entry") or b.get("close")
            ev.update(status="breakout", lead_days=round((b["sig_t"] + D_MS - ev["t"]) / D_MS, 1),
                      better_atr=round((px - ev["px"]) / ev["atr"], 2) if px and ev.get("px") and ev["atr"] else None)
            changed = True
        elif now_ms > ev["t"] + (LEAD_DAYS + 1) * D_MS:   # a day's grace for the journal to record it
            ev["status"] = "no breakout"
            changed = True
    return changed


def summary(state):
    cw = (state.get("cascade_watch") or {}) if state is not None else {}
    ev = cw.get("events") or []
    done = [e for e in ev if e["status"] != "watching"]
    hits = [e for e in done if e["status"] == "breakout"]
    adv = sorted(e["better_atr"] for e in hits if e.get("better_atr") is not None)
    lead = sorted(e["lead_days"] for e in hits)
    return {"resolved": len(done), "hits": len(hits), "hit_rate": len(hits) / len(done) if done else None,
            "median_lead_days": lead[len(lead) // 2] if lead else None,
            "median_better_atr": adv[len(adv) // 2] if adv else None,
            "watching": [{"coin": e["coin"], "t": e["t"]} for e in ev if e["status"] == "watching"][-8:],
            "backtest": BACKTEST}
