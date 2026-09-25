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

from . import account, events, journal, liqmap, universe
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


BAR_MS = {"1d": 86_400_000, "4h": 4 * 3_600_000, "1h": 3_600_000}


def post(inf, body, retries=4):
    """inf.post with backoff on HTTP 429. Scanning ~30 coins shares a per-IP weight budget with every
    other job on the same Actions runner IP, so a rate-limit is an expected event, not a crash."""
    for i in range(retries):
        try:
            return inf.post("/info", body)
        except Exception as e:  # noqa: BLE001 - the SDK raises ClientError; the fakes raise plain errors
            if "429" not in str(e) and getattr(e, "status_code", None) != 429 or i == retries - 1:
                raise
            time.sleep(2 * 2 ** i)


CANDLE_REQUESTS = [0]  # per process; run_once logs the delta so the request budget is visible


def candles(inf, coin, interval, days):
    CANDLE_REQUESTS[0] += 1
    now = int(time.time() * 1000)
    c = post(inf, {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                     "startTime": now - days * 86400_000, "endTime": now}})
    df = pd.DataFrame(c)
    for k in "ohlcv":
        df[k] = df[k].astype(float)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df


def signal_lines(inf, r):
    """The coin's close vs its own 50-week / 200-day SMA at the signal bar's close (hlg.lines). One
    400-day candle request, only when a signal first enters the journal (a few a week). Fail-soft."""
    from . import lines

    try:
        d = candles(inf, r["coin"], "1d", 400).set_index("t")
        return lines.at_signal(d, r["sig_t"], BAR_MS.get(r["tf"], 86_400_000), r["signal_close"])
    except Exception as e:  # noqa: BLE001 - evidence only; never blocks the journal
        log.error("signal lines %s failed: %s", r.get("coin"), e)
        return None


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


def bar_stats(d, tf):
    """Everything the breakout rule needs that only changes when a bar closes. `d` still has the
    live (incomplete) candle as its last row. Plain floats, so it can be cached in the JSON state."""
    live, done = d.iloc[-1], d.iloc[:-1]
    k = done.iloc[-1]
    return {"tf": tf, "bar_t": int(k.t.timestamp() * 1000), "k_c": float(k.c),
            "e20": float(ema(done.c, 20).iloc[-1]), "e50": float(ema(done.c, 50).iloc[-1]),
            "atr": float(atr(done).iloc[-1]),
            "hi20": float(done.h.iloc[-21:-1].max()),       # 20-bar high BEFORE the last completed candle
            "hi20_live": float(done.h.iloc[-20:].max()),    # level the live candle has to close above
            "rsi": float(rsi(done.c).iloc[-1]), "live_c": float(live.c),
            "close_loc": float((k.c - k.l) / (k.h - k.l)) if k.h > k.l else 0.5,
            # the last 40 completed bars, compact, for the dashboard's signal chart
            "spark": [[int(r.t.timestamp() * 1000)] + [float(f"{x:.6g}") for x in (r.o, r.h, r.l, r.c)]
                      for r in done.iloc[-40:].itertuples()]}


def completed_bars(d, n=60):
    """The last n completed bars with their ATR, as plain dicts for the signal journal."""
    done = d.iloc[:-1].copy()
    done["atr"] = atr(done)
    done = done.iloc[-n:]
    return [{"t": int(r.t.timestamp() * 1000), "o": float(r.o), "h": float(r.h), "l": float(r.l), "c": float(r.c),
             "atr": float(r.atr)} for r in done.itertuples()]


def stats_fresh(st, now_ms):
    """A cached bar_stats is good until the live bar it was taken during has closed."""
    return isinstance(st, dict) and "bar_t" in st and now_ms < st["bar_t"] + 2 * BAR_MS.get(st.get("tf"), 86_400_000)


def classify(st, px, S, coin, funding_apr):
    """The breakout rule on cached bar stats and a live price."""
    tf, a, k_c = st["tf"], st["atr"], st["k_c"]
    out = {"coin": coin, "px": px, "rsi4h": st["rsi"], "trend": "UP" if st["e20"] > st["e50"] else "DOWN",
           "funding_apr": funding_apr, "setup": None, "hi20": st["hi20_live"], "atr": a, "tf": tf}
    if st["e20"] <= st["e50"]:
        out["rejected"] = "trend down"
        return out
    if k_c > st["hi20"]:
        stop = k_c - S["stop_atr"] * a
        k_t = pd.Timestamp(st["bar_t"], unit="ms", tz="UTC")
        sig = str(k_t.date()) if tf == "1d" else k_t.strftime("%Y-%m-%d %H:%M")
        # entry is the next bar's open; once that bar has closed too, the signal is spent
        out.update(setup="LONG", stop=stop, target=None, rr=None, signal_close=k_c, signal_day=sig, level=st["hi20"],
                   valid_until=st["bar_t"] + 2 * BAR_MS.get(tf, 86_400_000), sig_t=st["bar_t"],
                   close_loc=st.get("close_loc"), bars=st.get("spark"))
        # entry is the open after the signal close; if price already ran > 1 ATR beyond it, don't chase
        if px > k_c + a:
            out["rejected"] = f"ran {((px / k_c) - 1) * 100:.1f}% since signal close, wait for next setup"
            out["setup"] = None
    elif px > st["hi20_live"]:
        out["rejected"] = f"forming: above 20-bar high {st['hi20_live']:.5g}, needs {tf} close"
    elif a > 0 and st["hi20_live"] - px <= 0.5 * a:
        # context for the dashboard's "near breakout" list; not a signal and never alerted
        out["near_atr"] = (st["hi20_live"] - px) / a
    return out


def analyse_breakout(inf, coin, S, ctx, tf=None, cache=None, px=None, now_ms=None, bars_out=None):
    """cache: {"coin|tf": bar_stats} reused until the next bar closes, so a 15-minute run only pulls
    candles when there is a new bar to evaluate. px: live price (allMids) -- otherwise the live
    candle's close from the fetch."""
    tf = tf or S.get("timeframe", "1d")  # 1d (backtested default) or 4h (faster, 20/50-bar windows on 4h bars)
    now_ms = now_ms or int(time.time() * 1000)
    key = f"{coin}|{tf}"
    st = cache.get(key) if cache is not None else None
    if not stats_fresh(st, now_ms):
        d = candles(inf, coin, tf, 120 if tf == "1d" else 30)
        if len(d) < 55:
            return None
        st = bar_stats(d, tf)
        if cache is not None:
            cache[key] = st
        if bars_out is not None:  # fresh bars for the signal journal (only when a bar has closed)
            bars_out[key] = completed_bars(d)
    funding_apr = fnum(ctx.get("funding", 0)) * 24 * 365 * 100
    return classify(st, px if px is not None else st["live_c"], S, coin, funding_apr)


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


def analyse(inf, coin, S, ctx, tf=None, **kw):
    if S.get("strategy", "breakout") == "breakout":
        return analyse_breakout(inf, coin, S, ctx, tf, **kw)
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


def resolve_coins(inf, S, U, ctx, state, open_coins, cache):
    """The coins to scan this run. fixed: scanner.coins. auto: today's liquidity universe, rebuilt
    on the first run of each UTC day (hlg.universe) and stored in state; the 1d candles fetched to
    rank it also seed the 1d bar-stats cache, so ranking costs no extra requests for scanned coins."""
    if U["mode"] != "auto":
        return list(S["coins"])
    day = universe.today()
    coins = universe.current(state, day, open_coins)
    if coins is None:
        liq, ages, t0, n = {}, {}, time.monotonic(), 0
        for c in universe.prefilter(ctx, U):
            try:
                d = candles(inf, c, "1d", 120)
            except Exception as e:  # noqa: BLE001
                log.error("%s: universe candles failed: %s", c, e)
                continue
            n += 1
            done = d.iloc[:-1]
            liq[c] = universe.median_notional(done[["v", "c"]].to_dict("records"))
            ages[c] = (d.t.iloc[-1] - d.t.iloc[0]).days
            if cache is not None and len(d) >= 55:
                cache[f"{c}|1d"] = bar_stats(d, "1d")
        coins = universe.rank(liq, ages, U, open_coins) or list(S["coins"])
        if state is not None:
            state.set("universe", {"day": day, "coins": coins, "liq": {c: liq[c] for c in coins if liq.get(c)}})
        log.info("universe: %d coins from %d candidates (%d requests) in %.1fs", len(coins), len(liq), n,
                 time.monotonic() - t0, extra={"safe": True})
    # a listing that died intraday drops out; a coin you hold never does
    return [c for c in coins if c in ctx or c in open_coins]


def momentum_from_snaps(snaps, mids, now_ms, window_h):
    """% change per coin from the stored allMids snapshot closest to window_h ago, or {} if no
    snapshot is within [window_h - 0.5h, window_h + 1h] (e.g. after a gap in runs)."""
    H = 3_600_000
    lo, hi, target = now_ms - (window_h + 1) * H, now_ms - (window_h - 0.5) * H, now_ms - window_h * H
    near = [s for s in snaps if lo <= s[0] <= hi]
    if not near:
        return {}
    _, then = min(near, key=lambda s: abs(s[0] - target))
    return {c: (px / then[c] - 1) * 100 for c, px in mids.items() if then.get(c)}


def momentum(inf, S, U, ctx, coins, mids, state, notif, now_ms):
    """Heads-up for moves too fast for 1d/4h breakouts. With state, from allMids snapshots (one
    request for every coin, stored each run); without it, from 1h candles per coin, as before.
    In auto mode it watches every liquid perp, not only the scanned top_n, so a sudden move just
    outside the list still alerts."""
    window = S.get("momentum_window_hours", 4)
    thr = S.get("momentum_alert_pct", 8)
    watch_set = list(coins)
    if U["mode"] == "auto":
        watch_set += [c for c, x in ctx.items() if ":" not in c and c not in watch_set
                      and fnum(x.get("dayNtlVlm") or 0) >= U["min_vol_usd"]]
    if state is not None and mids:
        snaps = [s for s in (state.get("mid_snaps") or []) if now_ms - s[0] <= (window + 1.5) * 3_600_000]
        moves = momentum_from_snaps(snaps, {c: mids[c] for c in watch_set if c in mids}, now_ms, window)
        snaps.append([now_ms, {c: mids[c] for c in watch_set if c in mids}])
        state.set("mid_snaps", snaps)
    else:
        moves = {}
        for c in watch_set:
            try:
                m = momentum_pct(inf, c, S)
            except Exception as e:  # noqa: BLE001
                log.error("%s momentum check failed: %s", c, e)
                continue
            if m is not None:
                moves[c] = m
    for coin, mom in moves.items():
        if abs(mom) < thr:
            continue
        direction = "up" if mom > 0 else "down"
        notif.send(
            f"MOMENTUM {coin} {direction} {mom:+.1f}% in {window}h - no setup here (not backtested at this speed), just a heads-up to go look",
            key=f"mom_{coin}_{int(abs(mom) // 5)}", cooldown_s=2 * 3600,
            cat="heads_up", summary=f"MOMENTUM {coin} {mom:+.1f}% in {window}h", valid_for_ms=window * 3600_000, coin=coin,
        )


def run_once(cfg, inf, notif, state):
    S, R = cfg["scanner"], cfg["rules"]
    watch = S.get("watch_coins", [])
    ctx = ctx_map(inf, list(watch) + list(S.get("tradfi_coins", [])))
    model, st = account.load(inf, cfg["account"])
    equity = model["base"]  # USDC collateral on a unified account, perp equity otherwise
    risk_usd = equity * R["risk_per_trade_pct"] / 100
    open_coins = {p["position"]["coin"] for p in st["assetPositions"]}
    tfs = S.get("timeframes") or [S.get("timeframe", "1d")]
    breakout_tfs = tfs if S.get("strategy", "breakout") == "breakout" else [None]  # pullback ignores tf, one pass
    mac = macro_context(S)
    now_ms = int(time.time() * 1000)
    req0 = CANDLE_REQUESTS[0]
    # bar stats per coin|tf, reused until that bar closes (see analyse_breakout); lives in the
    # encrypted state between runs. None (tests, one-off calls) = always fetch.
    cache = dict(state.get("scan_cache") or {}) if state is not None else None
    try:
        mids = {k: float(v) for k, v in (inf.all_mids() or {}).items()}
    except Exception as e:  # noqa: BLE001
        log.error("allMids failed: %s", e)
        mids = {}
    U = universe.settings(S)
    coins = resolve_coins(inf, S, U, ctx, state, open_coins, cache)
    urank = {c: i + 1 for i, c in enumerate(coins)} if U["mode"] == "auto" else {}
    rows, bars_out = [], {}
    for coin in coins + [w for w in watch if w not in coins]:
        c_ctx = ctx.get(coin, {"funding": 0})
        for tf in breakout_tfs:
            try:
                a = analyse(inf, coin, S, c_ctx, tf, cache=cache, px=mids.get(coin), now_ms=now_ms, bars_out=bars_out)
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
            a["vlm24h"] = fnum(c_ctx.get("dayNtlVlm") or 0)
            lm = ((state.get("liqmap") or {}).get("coins") or {}).get(coin) if state is not None else None
            if lm and a.get("signal_close") is not None:
                a["liq"] = liqmap.near(lm, a["px"])
            if coin in urank:
                a["urank"] = urank[coin]
            rows.append(a)
            if a["setup"] and a.get("rr") is None:  # breakout strategy
                dist = abs(a["px"] - a["stop"])
                size = min(risk_usd / dist, equity * R["max_leverage"] / a["px"])
                note = f" (paying {a['funding_apr']:.0f}% APR funding)" if a["funding_apr"] > 15 else ""
                held = coin in open_coins
                if held:
                    note += " [already in a position - do NOT add]"
                elif len(open_coins) >= R["max_positions"]:
                    note += f" [{len(open_coins)} of {R['max_positions']} positions open: skip, or replace a weaker one]"
                msg = (
                    f"BREAKOUT LONG {coin} @ {a['px']:.5g}{note}\n"
                    f"  {a['tf']} close {a['signal_close']:.5g} on {a['signal_day']} > 20-bar high | trend UP | {a['tf']} RSI {a['rsi4h']:.0f}\n"
                    f"  stop {a['stop']:.5g} ({S['stop_atr']}x ATR) | trail {S['trail_atr']}x ATR ({S['trail_atr'] * a['atr']:.5g}) below highest high | time stop day {R['max_hold_days']}\n"
                    f"  size {size:.4g} {coin} (~{size * a['px']:,.0f} USD) keeps loss at {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% of {equity:,.0f} {model['base_label']}\n"
                    f"  rules: limit entry near open (maker), stop placed BEFORE entry, no adds if red, no target - let the trail work"
                )
                if mac:
                    msg += f"\n  macro: {mac['label']} (HY {mac['hy']:.2f}, VIX {mac['vix']:.1f}) - context only, not part of the rule"
                if a.get("liq"):
                    msg += f"\n  HL liq levels: {liqmap.describe(a['liq'])} - context only"
                ev_note = events.note_for_entry(state.get("events"), now_ms) if state is not None else ""
                if ev_note:
                    msg += f"\n  calendar: {ev_note} - context only"
                fg = (state.get("fng") or {}) if state is not None else {}
                if a["tf"] == "4h" and fg.get("value") is not None and fg["value"] <= 24:
                    # README "Sentiment": on 4h (not 1d) extreme-fear entries lost money, but almost all
                    # in one stretch -- shown so you can weigh it, deliberately not made a filter
                    msg += (f"\n  sentiment: Fear & Greed {fg['value']} (extreme fear) - 4h breakouts entered in extreme fear "
                            "made PF 0.79 over 82 backtest trades, nearly all in the 2025-26 fear stretch; context, not a rule")
                # A breakout on a coin you already hold is not an entry (no adds), so it stays on the
                # dashboard instead of buzzing the phone.
                notif.send(msg, key=f"setup_{coin}_LONG_{a['signal_day']}", cooldown_s=24 * 3600 if a["tf"] == "1d" else 4 * 3600,
                           push=not held, cat="info" if held else "entry",
                           summary=(f"{coin} {a['tf']} breakout - already held, don't add" if held else
                                    f"BREAKOUT LONG {coin} {a['tf']} · size {size:.4g} (~{size * a['px']:,.0f} USD) · stop {a['stop']:.5g}"),
                           valid_until=a["valid_until"], coin=coin, tf=a["tf"], level=a["level"],
                           signal_close=a["signal_close"], atr=a["atr"])
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
                    f"  size {size:.4g} {coin} (~{size * a['px']:,.0f} USD) keeps loss at {risk_usd:.0f} USD = {R['risk_per_trade_pct']}% of {equity:,.0f} {model['base_label']}\n"
                    f"  rules: limit entry (maker), stop placed BEFORE entry, no adds if red, close by day 7"
                )
                notif.send(msg, key=f"setup_{coin}_{a['setup']}", cooldown_s=12 * 3600, push=coin not in open_coins,
                           cat="info" if coin in open_coins else "entry", coin=coin)
        if coin in watch:
            continue
        funding_apr = fnum(c_ctx.get("funding", 0)) * 24 * 365 * 100
        if abs(funding_apr) > S["funding_carry_alert_apr"]:
            # Dashboard only: never backtested and not part of the breakout strategy. Only the
            # positive-funding side is a hedge you can actually hold on HL (long spot, short perp);
            # the mirror needs a spot short, which HL does not offer. The text below is written to
            # answer "why does this matter" (funding as a crowding signal) and "what could I do
            # about it" (the one side that's actually capturable here), not just report a number.
            daily_per_10k = 10_000 * funding_apr / 36_500
            if funding_apr > 0:
                txt = (
                    f"FUNDING {coin}: {funding_apr:+.0f}% APR - longs are paying shorts, "
                    f"about ${daily_per_10k:,.0f}/day per $10,000 held\n"
                    f"  read: funding this high usually means the crowd is heavily long - a cost if "
                    f"you're long here, more a caution flag than a bearish call\n"
                    f"  the yield is capturable risk-free: hold spot + short an equal perp (delta-"
                    f"neutral) to collect it without taking a market view\n"
                    f"  context only - not backtested, not part of the breakout rule, never pushed to your phone"
                )
            else:
                txt = (
                    f"FUNDING {coin}: {funding_apr:+.0f}% APR - shorts are paying longs, "
                    f"about ${-daily_per_10k:,.0f}/day per $10,000 held\n"
                    f"  read: funding this negative usually means the crowd is heavily short - a cost "
                    f"if you're short here, more a caution flag than a bullish call\n"
                    f"  no clean hedge on Hyperliquid (spot can't be shorted), so there's no risk-free "
                    f"way to collect this side\n"
                    f"  context only - not backtested, not part of the breakout rule, never pushed to your phone"
                )
            notif.send(txt, key=f"fund_{coin}_{int(funding_apr // 10)}", cooldown_s=6 * 3600,
                       push=False, cat="info", summary=f"Funding {coin} {funding_apr:+.0f}% APR "
                       f"({'longs' if funding_apr > 0 else 'shorts'} crowded)", coin=coin)
    momentum(inf, S, U, ctx, coins, mids, state, notif, now_ms)
    if state is not None:
        # follow every breakout signal under the strategy's rules, taken or not (hlg.journal)
        jr = list(state.get("journal") or [])
        for r in rows:
            if r.get("signal_close") is not None and not r.get("watch"):
                journal.arm_add(jr, r)  # a fresh breakout on an open entry: hypothetical add (README "Pyramiding")
                key = f"setup_{r['coin']}_LONG_{r['signal_day']}|{r['tf']}"
                if not any(e["key"] == key for e in jr):
                    r["lines"] = signal_lines(inf, r)
                journal.add(jr, r, key, now_ms, S)
        for e in jr:
            b = bars_out.get(f"{e['coin']}|{e['tf']}")
            if b and e["status"] in ("pending", "open"):
                journal.update(e, b, S, R["max_hold_days"])
        state.set("journal", journal.prune(jr, now_ms))
    if state is not None:
        # keep only what is still in play, so the state file cannot grow without bound
        keep = set(coins) | set(watch)
        state.set("scan_cache", {k: v for k, v in cache.items() if k.split("|")[0] in keep})
    log.info("scan: %d coins x %d timeframes, %d candle requests", len(coins), len(breakout_tfs),
             CANDLE_REQUESTS[0] - req0, extra={"safe": True})
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
    require_account(cfg)
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
