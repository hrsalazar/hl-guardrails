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

from . import account
from .common import (
    Notifier,
    State,
    fnum,
    info,
    load_config,
    log,
    require_account,
    setup_logging,
)

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
    port = dict(inf.post("/info", {"type": "portfolio", "user": acct}))
    # `equity` is the base every rule sizes against: the perp account value on a classic account,
    # the USDC collateral on a unified one (see hlg.account for why those differ ~13x).
    model, st = account.load(inf, acct, port)
    equity = model["base"]
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

    # A loss-limit lock is only meaningful against the base it was computed on. Locks normally
    # persist for the period even if PnL recovers -- deliberately -- but one measured against a
    # different definition of equity was never a real breach: a lock fired while the tool read
    # equity as the small perp-margin slice instead of the USDC collateral, with the whole account
    # up on the day. So a change of basis (new account model, or an account
    # switching between classic and unified) voids an outstanding lock instead of inheriting it.
    # A lock with no recorded basis predates this check, and that code always sized on perp equity
    # -- so it is inferred rather than assumed different. A genuine lock on a classic account, whose
    # base never changed, must survive the upgrade; only a basis that really moved voids one.
    basis, prev = model["base_label"], state.get("lock_basis") or "perp equity"
    if prev != basis:
        if state.get("lock_until", 0) > now_ms:
            log.warning("voiding loss-limit lock computed on %r; equity base is now %r", prev, basis)
        state.set("lock_until", 0)
    state.set("lock_basis", basis)

    # HL's own PnL series (already net of deposits/withdrawals/spot<->perp transfers, including
    # flows the ledger endpoint does not report) differenced at the period start. On a unified
    # account that is the whole-account series ("day"/"week"): spot is collateral there, so a bad
    # day in the spot book is a bad day. On a classic account spot is a separate wallet and the
    # perp-only series stays the right measure.
    dwin, wwin = ("day", "week") if model["unified"] else ("perpDay", "perpWeek")
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - dt.timedelta(days=now.weekday())

    def period_pnl(win, t0):
        t0_ms = int(t0.timestamp() * 1000)
        pnl, av = port[win]["pnlHistory"], port[win]["accountValueHistory"]
        base = [(float(p), float(v)) for (t, p), (_, v) in zip(pnl, av) if t <= t0_ms]
        p0, v0 = base[-1] if base else (float(pnl[0][1]), float(av[0][1]))
        return float(pnl[-1][1]) - p0, v0

    try:
        day_pnl, day_base = period_pnl(dwin, day0)
        week_pnl, week_base = period_pnl(wwin, week0)
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
    # Same definition as HL's "Unified Account Leverage" on a unified account (notional / USDC),
    # and plain gross / equity on a classic one.
    lev_x = model["leverage"]
    if lev_x is not None and lev_x > R["max_gross_exposure_x"]:
        breach(f"account leverage {lev_x:.2f}x ({model['notional']:,.0f} notional / {equity:,.0f} {model['base_label']}) "
               f"> {R['max_gross_exposure_x']}x", "gross")

    # A perp long stacked on a spot bag in the same coin is one bet, not two; the checks above
    # only ever saw the perp half. Only coins where spot is actually involved are judged here.
    cap = R.get("max_coin_exposure_x")
    if cap and equity > 0:
        for coin, e in account.coin_exposure(model, positions, mids).items():
            if e["spot_usd"] and abs(e["net_usd"]) > cap * equity:
                breach(f"{coin}: perp {e['perp_usd']:+,.0f} + spot {e['spot_usd']:,.0f} = {e['net_usd']:+,.0f} USD net "
                       f"({abs(e['net_usd']) / equity:.2f}x {model['base_label']}) > {cap}x - one concentrated bet across "
                       f"spot and perp", f"coinexp_{coin}")

    # ---- per-position checks ----
    prev = state.get("positions", {})
    snap = {}
    risk_usd = equity * R["risk_per_trade_pct"] / 100
    # allowed_coins: auto = the scanner's liquidity universe for today (hlg.universe), so a coin the
    # scanner can alert on is never also flagged as not allowed. Before the first universe exists,
    # fall back to scanner.coins.
    allowed = R["allowed_coins"]
    if allowed == "auto":
        u = state.get("universe") if state is not None else None
        allowed = (u or {}).get("coins") or cfg.get("scanner", {}).get("coins", [])
    for p in positions:
        coin, sz = p["coin"], fnum(p["szi"])
        entry, upnl = fnum(p["entryPx"]), fnum(p["unrealizedPnl"])
        mark = fnum(mids.get(coin, entry))
        lev = p["leverage"]["value"]
        snap[coin] = {"sz": sz, "entry": entry}
        side = "LONG" if sz > 0 else "SHORT"
        tag = f"{coin} {side} {abs(sz)} @ {entry} (uPnL {upnl:+.0f})"

        if coin not in allowed:
            what = "today's liquid universe (allowed_coins: auto)" if R["allowed_coins"] == "auto" else f"allowed list {R['allowed_coins']}"
            breach(f"{tag}: {coin} not in {what}", f"coin_{coin}")
        # Isolated only. On cross margin the per-position setting just decides how much margin is
        # reserved: the loss at the stop and the liquidation point are account-level, and the
        # account's real leverage is already capped by max_gross_exposure_x. Warning about a 10x
        # *setting* on a cross position at 1.4x account leverage is a false alarm.
        if p["leverage"].get("type") == "isolated" and lev > R["max_leverage"]:
            breach(f"{tag}: isolated leverage {lev}x > max {R['max_leverage']}x - it sets where this position liquidates. Lower it.", f"lev_{coin}")

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
            # Loss if the stop fills, measured from entry and signed by direction: a long's stop above
            # entry (or a short's below) locks in profit and risks nothing. The old abs() distance
            # read those as risk and told you to tighten stops that were already protecting gains.
            worst = max(0.0, (entry - trig) * sz)
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
        "%s %.0f (%s) | ratio %s lev %s | day %+.0f (%.1f%%) week %+.0f (%.1f%%) | %d pos gross %.0f | %d issue(s)%s",
        model["base_label"], equity, model["mode"],
        f"{model['ratio_pct']:.2f}%" if model["ratio_pct"] is not None else "-",
        f"{model['leverage']:.2f}x" if model["leverage"] is not None else "-",
        day_pnl, day_pnl / day_base * 100, week_pnl, week_pnl / week_base * 100,
        len(positions), gross, len(problems), " | LOCKED" if locked else "",
    )
    return problems


def main():
    setup_logging()
    cfg = load_config()
    require_account(cfg)
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
