"""Guard-rail monitor for a Hyperliquid perp account.

Rules (see config.yaml):
  1. every position has a reduce-only stop sized so the loss <= risk_per_trade_pct of equity
  2. never add to a losing position
  3. max positions / same-direction / gross exposure / leverage caps
  4. time stop: positions older than max_hold_days
  5. daily and weekly loss limits -> flatten + lock
  6. coin whitelist

mode=alert only reports; mode=enforce also acts (needs HL_PRIVATE_KEY of the wallet or an API/agent wallet).
"""
import datetime as dt
import math
import os
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


class Exec:
    """Thin wrapper around the SDK Exchange; None when running in alert mode."""

    def __init__(self, account, meta):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from .common import API

        key = os.environ["HL_PRIVATE_KEY"]
        self.ex = Exchange(Account.from_key(key), API, account_address=account)
        self.sz_dec = {u["name"]: u["szDecimals"] for u in meta["universe"]}

    def round_px(self, coin, px):
        dec = 6 - self.sz_dec[coin]
        px = float(f"{px:.5g}")
        return round(px, dec)

    def round_sz(self, coin, sz):
        return round(sz, self.sz_dec[coin])

    def place_stop(self, coin, pos_sz, trig_px, mark):
        is_buy = pos_sz < 0
        trig = self.round_px(coin, trig_px)
        limit = self.round_px(coin, trig * (1.03 if is_buy else 0.97))
        ot = {"trigger": {"triggerPx": trig, "isMarket": True, "tpsl": "sl"}}
        r = self.ex.order(coin, is_buy, self.round_sz(coin, abs(pos_sz)), limit, ot, reduce_only=True)
        log.info("placed stop %s %s @%s -> %s", coin, pos_sz, trig, r)
        return r

    def close(self, coin, sz=None):
        r = self.ex.market_close(coin, sz=self.round_sz(coin, sz) if sz else None)
        log.info("market_close %s %s -> %s", coin, sz, r)
        return r

    def cancel_all(self, open_orders):
        for o in open_orders:
            try:
                self.ex.cancel(o["coin"], o["oid"])
            except Exception as e:  # noqa: BLE001
                log.error("cancel %s failed: %s", o["oid"], e)

    def set_leverage(self, coin, lev):
        try:
            r = self.ex.update_leverage(lev, coin, True)
            log.info("update_leverage %s %sx -> %s", coin, lev, r)
        except Exception as e:  # noqa: BLE001
            log.error("update_leverage failed: %s", e)


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


def run_once(cfg, inf, notif, state, ex):
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
    if state.get("day_key") != dk:
        state.set("day_key", dk)
        state.set("day_start_equity", equity)
        state.set("day_start_ts", now_ms)
    if state.get("week_key") != wk:
        state.set("week_key", wk)
        state.set("week_start_equity", equity)
        state.set("week_start_ts", now_ms)
        state.set("lock_until", 0)
    day_pnl = equity - state.get("day_start_equity") - flows_since(state.get("day_start_ts"))
    week_pnl = equity - state.get("week_start_equity") - flows_since(state.get("week_start_ts"))
    day_base = max(state.get("day_start_equity"), 1e-9)
    week_base = max(state.get("week_start_equity"), 1e-9)

    positions = [p["position"] for p in st["assetPositions"]]
    problems = []
    lock_until = state.get("lock_until", 0)
    locked = now_ms < lock_until

    def breach(msg, key, action=None):
        problems.append(msg)
        notif.send(msg, key=key)
        if ex and action:
            try:
                action()
            except Exception as e:  # noqa: BLE001
                log.error("enforce action failed: %s", e)

    if day_pnl / day_base * 100 <= -R["daily_loss_limit_pct"]:
        nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        state.set("lock_until", int(nxt.timestamp() * 1000))
        locked = True
        breach(
            f"DAILY LOSS LIMIT hit: {day_pnl:+.0f} USD ({day_pnl / day_base * 100:+.1f}%). Flatten; no trading until {nxt:%Y-%m-%d %H:%M} UTC.",
            "daily_limit",
            lambda: (ex.cancel_all(oo), [ex.close(p["coin"]) for p in positions]),
        )
    if week_pnl / week_base * 100 <= -R["weekly_loss_limit_pct"]:
        nxt = (now + dt.timedelta(days=7 - now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        state.set("lock_until", int(nxt.timestamp() * 1000))
        locked = True
        breach(
            f"WEEKLY LOSS LIMIT hit: {week_pnl:+.0f} USD ({week_pnl / week_base * 100:+.1f}%). Flatten; no trading until {nxt:%Y-%m-%d} UTC.",
            "weekly_limit",
            lambda: (ex.cancel_all(oo), [ex.close(p["coin"]) for p in positions]),
        )
    if locked and positions:
        breach(
            f"LOCKED until {dt.datetime.fromtimestamp(state.get('lock_until') / 1000, dt.timezone.utc):%Y-%m-%d %H:%M} UTC but {len(positions)} position(s) open.",
            "locked_open",
            lambda: [ex.close(p["coin"]) for p in positions],
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
            breach(f"{tag}: leverage {lev}x > max {R['max_leverage']}x", f"lev_{coin}", lambda c=coin: ex.set_leverage(c, R["max_leverage"]))

        # stop-loss coverage
        cov, trig = stop_coverage(oo, coin, sz)
        want_stop = entry - risk_usd / abs(sz) if sz > 0 else entry + risk_usd / abs(sz)
        if cov < abs(sz) * 0.95:
            if upnl < -risk_usd:
                breach(
                    f"{tag}: NO STOP and already past max risk ({upnl:+.0f} < -{risk_usd:.0f}). Close it.",
                    f"nostop_{coin}",
                    lambda c=coin: ex.close(c),
                )
            else:
                breach(
                    f"{tag}: NO STOP covering position. Required stop @ {want_stop:.5g} (risk {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% equity)",
                    f"nostop_{coin}",
                    lambda c=coin, s=sz, w=want_stop, m=mark: ex.place_stop(c, s, w, m),
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
                        lambda c=coin, a=added: ex.close(c, a),
                    )

        # time stop
        start = episode_start(fills, coin, sz)
        if start:
            age_d = (now_ms - start) / 86400_000
            if age_d > R["max_hold_days"]:
                breach(f"{tag}: open {age_d:.1f} days > {R['max_hold_days']}d time stop. Close.", f"age_{coin}", lambda c=coin: ex.close(c))

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
    ex = None
    if cfg["mode"] == "enforce":
        if not os.environ.get("HL_PRIVATE_KEY"):
            raise SystemExit("mode=enforce requires HL_PRIVATE_KEY")
        ex = Exec(cfg["account"], inf.meta())
        log.info("ENFORCE mode: orders will be placed")
    else:
        log.info("ALERT mode: read-only")
    while True:
        try:
            run_once(cfg, inf, notif, state, ex)
        except Exception as e:  # noqa: BLE001
            log.exception("loop error: %s", e)
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
