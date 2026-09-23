"""Live signal journal: every breakout signal, followed under the strategy's own rules.

The backtest says what the breakout rule did on history. This records what it does from here on,
on the signals you were actually shown, whether or not you took them:

  entry  the open of the bar after the signal (as backtested)
  stop   signal close - stop_atr x ATR, then trailed trail_atr x ATR below the highest high
  exit   stop touched (at the stop, or the open if it gapped through), or the time stop

Each entry records its result in R (1R = the initial risk), its best excursion, whether it closed
back below the breakout level within the first two bars ("failed early"), and the breakout bar's
close location. That last pair is the live test for the two ideas the entry study could not
settle (README "Entries"): does an early failure mean the trade is dead, and do signals with a long
upper wick do worse?

It also records a *hypothetical* pyramid add, the live half of README "Pyramiding" (which found 4h
adds beating plain bigger sizing, but only in a check chosen after seeing the backtest). Exactly the
backtested rule: a fresh breakout on the same coin and timeframe while the entry is open arms an add;
it fills at the next bar's open only if the trailing stop is by then at or above the first entry;
it shares that stop and the time stop and exits with the entry. Its result is in its own R (its
risk = add price - the shared stop), so "with add" = r + add r, both units risking the same 1.5%.
Nothing is advised from this -- it is evidence being collected.

Pure functions over plain dicts, so the journal lives in the encrypted state like everything else.
"""
MAX_ENTRIES = 300
KEEP_CLOSED_MS = 120 * 86_400_000
EARLY_BARS = 2


def add(journal, row, key, now_ms, S):
    """Start following a new signal. `row` is a scanner row that carries signal_close."""
    if any(e["key"] == key for e in journal):
        return
    risk = S["stop_atr"] * row["atr"]
    journal.append({
        "key": key, "coin": row["coin"], "tf": row["tf"], "signal_day": row["signal_day"],
        "sig_t": row.get("sig_t"), "added": now_ms, "close": row["signal_close"], "level": row["level"],
        "atr": row["atr"], "risk": risk, "close_loc": row.get("close_loc"),
        "liq": row.get("liq"),  # HL liquidation clusters near the price at signal time (hlg.liqmap)
        "entry": None, "stop": row["signal_close"] - risk, "best": None, "bars": 0,
        "status": "pending", "r": None, "max_r": 0.0, "failed_early": False, "last_t": row.get("sig_t"),
    })


def _r(e, px):
    return (px - e["entry"]) / e["risk"] if e["risk"] else 0.0


def arm_add(journal, row):
    """A fresh breakout signal (`row`) on a coin/timeframe whose entry is open arms a hypothetical
    add for the next bar's open. One add per entry (the tested `pyramid1`); a skipped add can be
    re-armed by a later, different signal. Idempotent for the same signal across runs."""
    for e in journal:
        a = e.get("add")
        if (e["coin"] == row["coin"] and e["tf"] == row["tf"] and e["status"] == "open"
                and e["signal_day"] != row["signal_day"]
                and (a is None or (a["status"] == "skipped" and a.get("signal_day") != row["signal_day"]))):
            e["add"] = {"sig_t": row.get("sig_t"), "signal_day": row["signal_day"], "status": "armed"}


def _add_r(a, px):
    return (px - a["entry"]) / a["risk"]


def _end_add(e, px):
    a = e.get("add")
    if not a:
        return
    if a["status"] == "open":
        a.update(status="closed", exit=px, r=_add_r(a, px))
    elif a["status"] == "armed":
        a.update(status="skipped", why="the entry closed before the add could fill")


def update(e, bars, S, max_days):
    """Advance one entry over completed bars ([{t, o, h, l, c, atr}], oldest first). Bars at or
    before e["last_t"] were already applied, so calling this again with overlapping bars is safe."""
    if e["status"] in ("stopped", "time"):
        return e
    for b in bars:
        if e["last_t"] is not None and b["t"] <= e["last_t"]:
            continue
        e["last_t"] = b["t"]
        if e["entry"] is None:  # the bar after the signal: fill at its open
            e["entry"], e["best"], e["start_t"], e["status"] = b["o"], b["o"], b["t"], "open"
        a = e.get("add")
        if a and a["status"] == "armed" and (a["sig_t"] is None or b["t"] > a["sig_t"]):
            # the backtested rule, checked at the fill with the stop as it stood before this bar
            if e["stop"] >= e["entry"] and b["o"] > e["stop"]:
                a.update(status="open", entry=b["o"], risk=b["o"] - e["stop"], start_t=b["t"], r=0.0)
            else:
                a.update(status="skipped", why="stop still below the first entry" if e["stop"] < e["entry"]
                         else "opened at or below the stop")
        e["bars"] += 1
        if b["l"] <= e["stop"]:
            px = min(b["o"], e["stop"])
            e.update(status="stopped", exit=px, r=_r(e, px), exit_t=b["t"])
            _end_add(e, px)
            break
        if e["bars"] <= EARLY_BARS and b["c"] < e["level"]:
            e["failed_early"] = True
        e["max_r"] = max(e["max_r"], _r(e, b["h"]))
        if b["t"] - e["start_t"] >= max_days * 86_400_000:
            e.update(status="time", exit=b["c"], r=_r(e, b["c"]), exit_t=b["t"])
            _end_add(e, b["c"])
            break
        e["best"] = max(e["best"], b["h"])
        e["stop"] = max(e["stop"], e["best"] - S["trail_atr"] * b["atr"])
        e["r"] = _r(e, b["c"])  # open trade: marked at the close
        if a and a["status"] == "open":
            a["r"] = _add_r(a, b["c"])
    return e


def prune(journal, now_ms):
    keep = [e for e in journal if e["status"] in ("pending", "open") or now_ms - e.get("exit_t", now_ms) < KEEP_CLOSED_MS]
    return keep[-MAX_ENTRIES:]


def summary(journal):
    """Closed trades only: n, win rate, average R, profit factor in R, and the early-failure split."""
    done = [e for e in journal if e["status"] in ("stopped", "time") and e.get("r") is not None]
    if not done:
        return {"closed": 0, "open": sum(e["status"] == "open" for e in journal), "adds": add_summary(journal)}
    rs = [e["r"] for e in done]
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    fe = [e for e in done if e["failed_early"]]
    return {
        # closed results in exit order, for the dashboard's cumulative-R chart
        "curve": [{"t": e.get("exit_t"), "r": e["r"], "coin": e["coin"], "tf": e["tf"]}
                  for e in sorted(done, key=lambda e: e.get("exit_t") or 0)],
        "closed": len(done), "open": sum(e["status"] == "open" for e in journal),
        "win_rate": sum(r > 0 for r in rs) / len(rs), "avg_r": sum(rs) / len(rs),
        "pf": wins / losses if losses else None,
        "failed_early": len(fe), "failed_early_recovered": sum(e["r"] > 0 for e in fe),
        "adds": add_summary(journal),
    }


def add_summary(journal):
    """Hypothetical adds per timeframe: closed ones in R, plus the entries that carried one, with
    and without it (both units risk the same 1.5%, so R adds up)."""
    out = {}
    for e in journal:
        a = e.get("add") or {}
        s = out.setdefault(e["tf"], {"closed": 0, "open": 0, "skipped": 0, "r_sum": 0.0, "wins": 0.0, "losses": 0.0,
                                     "base_r_sum": 0.0})
        if a.get("status") == "open":
            s["open"] += 1
        elif a.get("status") == "skipped":
            s["skipped"] += 1
        elif a.get("status") == "closed" and e.get("r") is not None:
            s["closed"] += 1
            s["r_sum"] += a["r"]
            s["base_r_sum"] += e["r"]
            s["wins" if a["r"] > 0 else "losses"] += abs(a["r"])
    for s in out.values():
        s["pf"] = s["wins"] / s["losses"] if s["losses"] else None
        s["with_add_r_sum"] = s["base_r_sum"] + s["r_sum"]
        del s["wins"], s["losses"]
    return {tf: s for tf, s in out.items() if s["closed"] or s["open"] or s["skipped"]}
