"""Guard-rail monitor for a Hyperliquid perp account. Advisory only: it never places, modifies,
or cancels an order, and never touches a private key. Every rule below only ever produces a
warning (console + optional Telegram) for a human to act on.

Rules (see config.yaml):
  1. every position has a reduce-only stop sized so the loss <= risk_per_trade_pct of equity
  2. never add to a losing position
  3. max positions / same-direction / gross exposure / leverage caps
  4. time stop: positions older than max_hold_days
  5. daily and weekly loss limits -> flag "should be flat" + lock the advice until the next period
  6. coin whitelist
"""
import datetime as dt
import math
import time

from .common import Notifier, State, fnum, info, load_config, log, setup_logging

ADDR = None


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def day_key(t):
    return t.strftime("%Y-%m-%d")


def week_key(t):
    y, w, _ = t.isocalendar()
    return f"{y}-W{w:02d}"


def episode_start(fills, coin, cur_sz):
    """Time the current position was opened from flat (walk fills backwards)."""
    sz = cur_sz
    for f in sorted((x for x in fills if x["coin"] == coin), key=lambda x: -x["time"]):
        s = fnum(f["sz"]) if f["side"] == "B" else -fnum(f["sz"])
        sz -= s
        if abs(sz) < 1e-9:
            return f["time"]
    return None


def stop_coverage(open_orders, coin, pos_sz):
    """Return (covered_size, worst_trigger) of reduce-only stop orders protecting the position."""
    cov, trig = 0.0, None
    want_side = "B" if pos_sz < 0 else "A"
    for o in open_orders:
        if o["coin"] != coin or not o.get("reduceOnly") or o.get("side") != want_side:
            continue
        if "Stop" not in o.get("orderType", ""):
            continue
        cov += fnum(o["sz"]) if fnum(o["sz"]) > 0 else abs(pos_sz)
        tp = fnum(o["triggerPx"])
        trig = tp if trig is None else (max(trig, tp) if pos_sz < 0 else min(trig, tp))
    return cov, trig


def run_once(cfg, inf, notif, state):
    R = cfg["rules"]
    acct = cfg["account"]
    st = inf.user_state(acct)
    equity = fnum(st["marginSummary"]["accountValue"])
    oo = inf.post("/info", {"type": "frontendOpenOrders", "user": acct})
    mids = inf.all_mids()
    now = utc_now()
    now_ms = int(time.time() * 1000)
    fills = inf.post(
        "/info",
        {"type": "userFillsByTime", "user": acct, "startTime": now_ms - 45 * 86400_000, "endTime": now_ms},
    )

    # ---- loss limits (equity change adjusted for transfers) ----
    ledger = inf.post(
        "/info",
        {"type": "userNonFundingLedgerUpdates", "user": acct, "startTime": now_ms - 8 * 86400_000, "endTime": now_ms},
    )

    def flows_since(ts_ms):
        tot = 0.0
        for x in ledger:
            if x["time"] < ts_ms:
                continue
            d = x["delta"]
            if d["type"] == "deposit":
                tot += fnum(d["usdc"])
            elif d["type"] == "withdraw":
                tot -= fnum(d["usdc"])
            elif d["type"] == "accountClassTransfer":
                tot += fnum(d["usdc"]) * (1 if d["toPerp"] else -1)
        return tot

    dk, wk = day_key(now), week_key(now)
    if state.get("week_key") != wk:
        state.set("week_key", wk)
        state.set("lock_until", 0)
    state.set("day_key", dk)

    # HL's own perp PnL series (already net of deposits/withdrawals/spot<->perp transfers,
    # including flows the ledger endpoint does not report) differenced at the period start.
    port = dict(inf.post("/info", {"type": "portfolio", "user": acct}))
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - dt.timedelta(days=now.weekday())

    def period_pnl(win, t0):
        t0_ms = int(t0.timestamp() * 1000)
        pnl, av = port[win]["pnlHistory"], port[win]["accountValueHistory"]
        base = [(float(p), float(v)) for (t, p), (_, v) in zip(pnl, av) if t <= t0_ms]
        p0, v0 = base[-1] if base else (float(pnl[0][1]), float(av[0][1]))
        return float(pnl[-1][1]) - p0, v0

    try:
        day_pnl, day_base = period_pnl("perpDay", day0)
        week_pnl, week_base = period_pnl("perpWeek", week0)
    except (KeyError, IndexError) as e:
        log.error("portfolio pnl unavailable (%s); falling back to equity snapshot", e)
        if state.get("day_start_equity") is None or state.get("day_start_day") != dk:
            state.set("day_start_equity", equity), state.set("day_start_ts", now_ms), state.set("day_start_day", dk)
        if state.get("week_start_equity") is None or state.get("week_start_week") != wk:
            state.set("week_start_equity", equity), state.set("week_start_ts", now_ms), state.set("week_start_week", wk)
        day_pnl = equity - state.get("day_start_equity") - flows_since(state.get("day_start_ts"))
        week_pnl = equity - state.get("week_start_equity") - flows_since(state.get("week_start_ts"))
        day_base, week_base = state.get("day_start_equity"), state.get("week_start_equity")
    day_base, week_base = max(day_base, 1e-9), max(week_base, 1e-9)
    state.set("day_start_equity", day_base)
    state.set("week_start_equity", week_base)
    state.set("day_pnl", day_pnl)
    state.set("week_pnl", week_pnl)

    positions = [p["position"] for p in st["assetPositions"]]
    problems = []
    lock_until = state.get("lock_until", 0)
    locked = now_ms < lock_until

    def breach(msg, key):
        problems.append(msg)
        notif.send(msg, key=key)

    if day_pnl / day_base * 100 <= -R["daily_loss_limit_pct"]:
        nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        state.set("lock_until", int(nxt.timestamp() * 1000))
        locked = True
        breach(
            f"DAILY LOSS LIMIT hit: {day_pnl:+.0f} USD ({day_pnl / day_base * 100:+.1f}%). You should be flat; no trading advised until {nxt:%Y-%m-%d %H:%M} UTC.",
            "daily_limit",
        )
    if week_pnl / week_base * 100 <= -R["weekly_loss_limit_pct"]:
        nxt = (now + dt.timedelta(days=7 - now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        state.set("lock_until", int(nxt.timestamp() * 1000))
        locked = True
        breach(
            f"WEEKLY LOSS LIMIT hit: {week_pnl:+.0f} USD ({week_pnl / week_base * 100:+.1f}%). You should be flat; no trading advised until {nxt:%Y-%m-%d} UTC.",
            "weekly_limit",
        )
    if locked and positions:
        breach(
            f"LOCKED until {dt.datetime.fromtimestamp(state.get('lock_until') / 1000, dt.timezone.utc):%Y-%m-%d %H:%M} UTC but {len(positions)} position(s) still open. Close them yourself.",
            "locked_open",
        )

    # ---- portfolio-level caps ----
    gross = sum(fnum(p["positionValue"]) for p in positions)
    if len(positions) > R["max_positions"]:
        breach(f"{len(positions)} positions open > max {R['max_positions']}", "max_pos")
    longs = sum(1 for p in positions if fnum(p["szi"]) > 0)
    shorts = len(positions) - longs
    if max(longs, shorts) > R["max_same_direction"]:
        breach(f"{max(longs, shorts)} positions same direction > max {R['max_same_direction']} (correlated-bet risk)", "same_dir")
    if equity > 0 and gross / equity > R["max_gross_exposure_x"]:
        breach(f"gross exposure {gross:,.0f} = {gross / equity:.1f}x equity > {R['max_gross_exposure_x']}x", "gross")

    # ---- per-position checks ----
    prev = state.get("positions", {})
    snap = {}
    risk_usd = equity * R["risk_per_trade_pct"] / 100
    for p in positions:
        coin, sz = p["coin"], fnum(p["szi"])
        entry, upnl = fnum(p["entryPx"]), fnum(p["unrealizedPnl"])
        mark = fnum(mids.get(coin, entry))
        lev = p["leverage"]["value"]
        snap[coin] = {"sz": sz, "entry": entry}
        side = "LONG" if sz > 0 else "SHORT"
        tag = f"{coin} {side} {abs(sz)} @ {entry} (uPnL {upnl:+.0f})"

        if coin not in R["allowed_coins"]:
            breach(f"{tag}: {coin} not in allowed list {R['allowed_coins']}", f"coin_{coin}")
        if lev > R["max_leverage"]:
            breach(f"{tag}: leverage {lev}x > max {R['max_leverage']}x. Lower it.", f"lev_{coin}")

        # stop-loss coverage
        cov, trig = stop_coverage(oo, coin, sz)
        want_stop = entry - risk_usd / abs(sz) if sz > 0 else entry + risk_usd / abs(sz)
        if cov < abs(sz) * 0.95:
            if upnl < -risk_usd:
                breach(
                    f"{tag}: NO STOP and already past max risk ({upnl:+.0f} < -{risk_usd:.0f}). Close it.",
                    f"nostop_{coin}",
                )
            else:
                breach(
                    f"{tag}: NO STOP covering position. Required stop @ {want_stop:.5g} (risk {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% equity)",
                    f"nostop_{coin}",
                )
        else:
            worst = abs(trig - entry) * abs(sz)
            if worst > risk_usd * 1.25:
                breach(f"{tag}: stop @ {trig} risks {worst:.0f} USD > allowed {risk_usd:.0f}. Tighten stop or cut size.", f"widestop_{coin}")

        # no adds underwater
        if R["no_add_underwater"] and coin in prev:
            psz = prev[coin]["sz"]
            if abs(sz) > abs(psz) * 1.001 and math.copysign(1, sz) == math.copysign(1, psz):
                underwater = (mark < prev[coin]["entry"]) if sz > 0 else (mark > prev[coin]["entry"])
                if underwater:
                    added = abs(sz) - abs(psz)
                    breach(
                        f"{tag}: ADDED {added:.4g} while underwater (averaging down). Trim back.",
                        f"add_{coin}_{int(abs(sz) * 1e6)}",
                    )

        # time stop
        start = episode_start(fills, coin, sz)
        if start:
            age_d = (now_ms - start) / 86400_000
            if age_d > R["max_hold_days"]:
                breach(f"{tag}: open {age_d:.1f} days > {R['max_hold_days']}d time stop. Close.", f"age_{coin}")

    state.set("positions", snap)
    log.info(
        "equity %.0f | day %+.0f (%.1f%%) week %+.0f (%.1f%%) | %d pos gross %.0f | %d issue(s)%s",
        equity, day_pnl, day_pnl / day_base * 100, week_pnl, week_pnl / week_base * 100,
        len(positions), gross, len(problems), " | LOCKED" if locked else "",
    )
    return problems


def main():
    setup_logging()
    cfg = load_config()
    inf = info()
    notif = Notifier(cfg)
    state = State(cfg["state_file"])
    log.info("advisory mode: read-only, no orders are ever placed")
    while True:
        try:
            run_once(cfg, inf, notif, state)
        except Exception as e:  # noqa: BLE001
            log.exception("loop error: %s", e)
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
