"""Setup scanner.

scanner.strategy = breakout (default, backtested: see hlg.backtest / README):
  LONG only: EMA20 > EMA50 and the last COMPLETED close > prior 20-bar high, on each of scanner.timeframes
  (default [1d]; both 1d and 4h are backtested, see README "Timeframe" -- each timeframe alerts independently).
  stop = close - stop_atr*ATR14d, then trail trail_atr*ATR below the highest high; time stop max_hold_days.
  "forming" = price above the 20d high intraday but the candle has not closed yet (do not chase).
scanner.strategy = pullback (legacy, tested negative in the backtest):
  LONG : daily EMA20 > EMA50, 4h RSI14 <= rsi_long_max, price within level_proximity_pct of 20d low or daily EMA20,
         stop = min(4h swing low, px - 1.5*ATR14d), target = 20d high, RR >= min_rr
  SHORT: mirror. Preferred when funding is positive (short side collects funding).
Also alerts on funding-carry opportunities (|annualised funding| > funding_carry_alert_apr), and on plain
momentum -- price moved > momentum_alert_pct in momentum_window_hours -- for moves too fast for 1d/4h breakout
levels to catch in time. Momentum alerts are a heads-up only: no backtested edge, so no stop/size/target.
Every setup alert includes the position size that keeps the stop loss at risk_per_trade_pct of equity.
"""
import time

import numpy as np
import pandas as pd

from .common import Notifier, State, fnum, info, load_config, log, setup_logging


def candles(inf, coin, interval, days):
    now = int(time.time() * 1000)
    c = inf.post("/info", {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                             "startTime": now - days * 86400_000, "endTime": now}})
    df = pd.DataFrame(c)
    for k in "ohlcv":
        df[k] = df[k].astype(float)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n=14):
    tr = pd.concat([df.h - df.l, (df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def analyse_breakout(inf, coin, S, ctx, tf=None):
    tf = tf or S.get("timeframe", "1d")  # 1d (backtested default) or 4h (faster, 20/50-bar windows on 4h bars)
    d = candles(inf, coin, tf, 120 if tf == "1d" else 30)
    if len(d) < 55:
        return None
    live, done = d.iloc[-1], d.iloc[:-1]  # last row is the open (incomplete) candle
    k = done.iloc[-1]
    e20, e50 = ema(done.c, 20).iloc[-1], ema(done.c, 50).iloc[-1]
    a = atr(done).iloc[-1]
    hi20 = done.h.iloc[-21:-1].max()  # 20-bar high BEFORE the last completed candle
    hi20_live = done.h.iloc[-20:].max()  # level the live candle has to close above
    r = rsi(done.c).iloc[-1]
    funding_apr = fnum(ctx["funding"]) * 24 * 365 * 100
    px = live.c
    out = {"coin": coin, "px": px, "rsi4h": r, "trend": "UP" if e20 > e50 else "DOWN", "funding_apr": funding_apr,
           "setup": None, "hi20": hi20_live, "atr": a, "tf": tf}
    if e20 <= e50:
        out["rejected"] = "trend down"
        return out
    if k.c > hi20:
        stop = k.c - S["stop_atr"] * a
        sig = str(k.t.date()) if tf == "1d" else k.t.strftime("%Y-%m-%d %H:%M")
        out.update(setup="LONG", stop=stop, target=None, rr=None, signal_close=k.c, signal_day=sig)
        # entry is the open after the signal close; if price already ran > 1 ATR beyond it, don't chase
        if px > k.c + a:
            out["rejected"] = f"ran {((px / k.c) - 1) * 100:.1f}% since signal close, wait for next setup"
            out["setup"] = None
    elif px > hi20_live:
        out["rejected"] = f"forming: above 20-bar high {hi20_live:.5g}, needs {tf} close"
    return out


def macro_context(S):
    """Current macro backdrop as a short label for alerts, or None if unavailable/disabled.

    Deliberately NOT a filter. Gating breakouts on a risk-on backdrop was backtested and is worse
    than dropping the same number of trades at random (README "Macro"): breakouts that fire while
    credit is deteriorating were 27 of 126 trades but 63% of the profit. Inverting the gate to only
    trade those is a 27-trade, post-hoc rule, which is how you overfit. So it rides along as context
    you can log against outcomes, and changes no decision by itself."""
    if not S.get("macro_context", True):
        return None
    try:
        from . import macro

        f = macro.features(macro.frame(start="2023-01-01"))
        if f.empty:
            return None
        c = f.iloc[-1]
        return {"as_of": str(f.index[-1].date()), "hy": float(c.hy), "vix": float(c.vix),
                "hy_stress": bool(c.hy_stress), "spx_bull": bool(c.spx_bull), "risk_on": bool(c.risk_on),
                "label": ("credit stress" if c.hy_stress else "credit calm") + (", SPX above 200d" if c.spx_bull else ", SPX below 200d")}
    except Exception as e:  # noqa: BLE001
        log.error("macro context failed: %s", e)
        return None


def momentum_pct(inf, coin, S):
    """Plain % price change over the last momentum_window_hours -- not a backtested setup, just
    a fast heads-up for moves the 1d/4h breakout timeframes are too slow to confirm in time."""
    window = S.get("momentum_window_hours", 4)
    d = candles(inf, coin, "1h", 2)
    if len(d) <= window:
        return None
    now_px, ref_px = d.c.iloc[-1], d.c.iloc[-(window + 1)]
    if ref_px == 0:
        return None
    return (now_px / ref_px - 1) * 100


def analyse(inf, coin, S, ctx, tf=None):
    if S.get("strategy", "breakout") == "breakout":
        return analyse_breakout(inf, coin, S, ctx, tf)
    d = candles(inf, coin, "1d", 90)
    h4 = candles(inf, coin, "4h", 30)
    if len(d) < 55 or len(h4) < 30:
        return None
    px = h4.c.iloc[-1]
    e20, e50 = ema(d.c, 20).iloc[-1], ema(d.c, 50).iloc[-1]
    a = atr(d).iloc[-1]
    r = rsi(h4.c).iloc[-1]
    hi20, lo20 = d.h.iloc[-21:-1].max(), d.l.iloc[-21:-1].min()
    swing_lo, swing_hi = h4.l.iloc[-12:].min(), h4.h.iloc[-12:].max()
    funding_apr = fnum(ctx["funding"]) * 24 * 365 * 100
    prox = S["level_proximity_pct"] / 100
    out = {"coin": coin, "px": px, "rsi4h": r, "trend": "UP" if e20 > e50 else "DOWN", "funding_apr": funding_apr, "setup": None}

    if e20 > e50 and r <= S["rsi_long_max"] and (px <= lo20 * (1 + prox) or abs(px - e20) / px <= prox):
        stop = min(swing_lo, px - 1.5 * a)
        rr = (hi20 - px) / max(px - stop, 1e-9)
        out.update(setup="LONG", stop=stop, target=hi20, rr=rr)
    elif e20 < e50 and r >= S["rsi_short_min"] and (px >= hi20 * (1 - prox) or abs(px - e20) / px <= prox):
        stop = max(swing_hi, px + 1.5 * a)
        rr = (px - lo20) / max(stop - px, 1e-9)
        out.update(setup="SHORT", stop=stop, target=lo20, rr=rr)
    if out["setup"] and out["rr"] < S["min_rr"]:
        out["rejected"] = f"RR {out['rr']:.1f} < {S['min_rr']}"
        out["setup"] = None
    return out


def ctx_map(inf, coins):
    """Per-coin asset contexts for `coins`, pulling in any extra dex they name (a prefixed coin
    like "xyz:CL" lives on the "xyz" dex, and its meta returns the name already prefixed, so the
    keys line up with the config). Callers read `funding` from this; the same payload also carries
    openInterest / dayNtlVlm / prevDayPx / premium / impactPxs, which hlg.market turns into the
    dashboard's market-structure rows."""
    meta, ctxs = inf.meta_and_asset_ctxs()
    ctx = {u["name"]: c for u, c in zip(meta["universe"], ctxs)}
    for dex in {c.split(":")[0] for c in coins if ":" in c}:
        try:
            m2, c2 = inf.post("/info", {"type": "metaAndAssetCtxs", "dex": dex})
            ctx.update({u["name"]: c for u, c in zip(m2["universe"], c2)})
        except Exception as e:  # noqa: BLE001
            log.error("dex %s ctx failed: %s", dex, e)
    return ctx


def run_once(cfg, inf, notif, state):
    S, R = cfg["scanner"], cfg["rules"]
    watch = S.get("watch_coins", [])
    ctx = ctx_map(inf, list(watch) + list(S.get("tradfi_coins", [])))
    st = inf.user_state(cfg["account"])
    equity = fnum(st["marginSummary"]["accountValue"])
    risk_usd = equity * R["risk_per_trade_pct"] / 100
    open_coins = {p["position"]["coin"] for p in st["assetPositions"]}
    tfs = S.get("timeframes") or [S.get("timeframe", "1d")]
    breakout_tfs = tfs if S.get("strategy", "breakout") == "breakout" else [None]  # pullback ignores tf, one pass
    mac = macro_context(S)
    rows = []
    for coin in list(S["coins"]) + list(watch):
        c_ctx = ctx.get(coin, {"funding": 0})
        for tf in breakout_tfs:
            try:
                a = analyse(inf, coin, S, c_ctx, tf)
            except Exception as e:  # noqa: BLE001
                log.error("%s (%s) scan failed: %s", coin, tf or S.get("strategy", "breakout"), e)
                continue
            if not a:
                continue
            if coin in watch:  # informational only: shown on the dashboard, never alerted
                a["watch"] = True
                if a["setup"]:
                    a["rejected"] = f"watch-only: breakout signal (stop {a['stop']:.5g})"
                    a["setup"] = None
                rows.append(a)
                continue
            rows.append(a)
            if a["setup"] and a.get("rr") is None:  # breakout strategy
                dist = abs(a["px"] - a["stop"])
                size = min(risk_usd / dist, equity * R["max_leverage"] / a["px"])
                note = f" (paying {a['funding_apr']:.0f}% APR funding)" if a["funding_apr"] > 15 else ""
                if coin in open_coins:
                    note += " [already in a position - do NOT add]"
                msg = (
                    f"BREAKOUT LONG {coin} @ {a['px']:.5g}{note}\n"
                    f"  {a['tf']} close {a['signal_close']:.5g} on {a['signal_day']} > 20-bar high | trend UP | {a['tf']} RSI {a['rsi4h']:.0f}\n"
                    f"  stop {a['stop']:.5g} ({S['stop_atr']}x ATR) | trail {S['trail_atr']}x ATR ({S['trail_atr'] * a['atr']:.5g}) below highest high | time stop day {R['max_hold_days']}\n"
                    f"  size {size:.4g} {coin} (~{size * a['px']:,.0f} USD) keeps loss at {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% equity\n"
                    f"  rules: limit entry near open (maker), stop placed BEFORE entry, no adds if red, no target - let the trail work"
                )
                if mac:
                    msg += f"\n  macro: {mac['label']} (HY {mac['hy']:.2f}, VIX {mac['vix']:.1f}) - context only, not part of the rule"
                notif.send(msg, key=f"setup_{coin}_LONG_{a['signal_day']}", cooldown_s=24 * 3600 if a["tf"] == "1d" else 4 * 3600)
            elif a["setup"]:
                dist = abs(a["px"] - a["stop"])
                size = risk_usd / dist
                note = ""
                if a["setup"] == "SHORT" and a["funding_apr"] > 0 and S["prefer_short_when_funding_positive"]:
                    note = f" (+funding {a['funding_apr']:.0f}% APR in your favour)"
                if a["setup"] == "LONG" and a["funding_apr"] > 15:
                    note = f" (careful: paying {a['funding_apr']:.0f}% APR funding)"
                if coin in open_coins:
                    note += " [already in a position - do NOT add]"
                msg = (
                    f"SETUP {a['setup']} {coin} @ {a['px']:.5g}{note}\n"
                    f"  trend {a['trend']} | 4h RSI {a['rsi4h']:.0f}\n"
                    f"  stop {a['stop']:.5g} | target {a['target']:.5g} | RR {a['rr']:.1f}\n"
                    f"  size {size:.4g} {coin} (~{size * a['px']:,.0f} USD) keeps loss at {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% equity\n"
                    f"  rules: limit entry (maker), stop placed BEFORE entry, no adds if red, close by day 7"
                )
                notif.send(msg, key=f"setup_{coin}_{a['setup']}", cooldown_s=12 * 3600)
        if coin in watch:
            continue
        funding_apr = fnum(c_ctx.get("funding", 0)) * 24 * 365 * 100
        if abs(funding_apr) > S["funding_carry_alert_apr"]:
            side = "short perp / long spot" if funding_apr > 0 else "long perp / short spot"
            notif.send(f"FUNDING CARRY {coin}: {funding_apr:+.0f}% APR -> {side}", key=f"fund_{coin}_{int(funding_apr // 10)}", cooldown_s=6 * 3600)
        try:
            mom = momentum_pct(inf, coin, S)
        except Exception as e:  # noqa: BLE001
            log.error("%s momentum check failed: %s", coin, e)
            mom = None
        mom_threshold = S.get("momentum_alert_pct", 8)
        if mom is not None and abs(mom) >= mom_threshold:
            window = S.get("momentum_window_hours", 4)
            direction = "up" if mom > 0 else "down"
            notif.send(
                f"MOMENTUM {coin} {direction} {mom:+.1f}% in {window}h - no setup here (not backtested at this speed), just a heads-up to go look",
                key=f"mom_{coin}_{int(abs(mom) // 5)}", cooldown_s=2 * 3600,
            )
    tbl = " | ".join(
        f"{r['coin']} {r['px']:.5g} {r['trend']} rsi{r['rsi4h']:.0f} f{r['funding_apr']:+.0f}%"
        + (f" **{r['setup']}**" if r["setup"] else (f" ({r['rejected']})" if r.get("rejected") else ""))
        for r in rows
    )
    log.info("scan: %s", tbl)
    return rows


def main():
    setup_logging()
    cfg = load_config()
    inf = info()
    notif = Notifier(cfg)
    state = State(cfg["state_file"])
    while True:
        try:
            run_once(cfg, inf, notif, state)
        except Exception as e:  # noqa: BLE001
            log.exception("scan loop error: %s", e)
        time.sleep(cfg["scanner"]["interval_minutes"] * 60)


if __name__ == "__main__":
    main()
