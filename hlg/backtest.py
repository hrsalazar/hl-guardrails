"""Daily-bar backtest of the setup fingerprints found by hlg.miner vs the current scanner rules.

    python -m hlg.backtest                # all variants, coins from config [backtest]
    python -m hlg.backtest --variant pullback_long --coins BTC ETH SOL

Signals on the daily close, entry at next open (maker fee), stop checked against the daily low/high
(gap -> filled at open), exits at taker fee. Real hourly funding from the API. Portfolio sizing:
risk_per_trade_pct of current equity per trade, max_positions concurrent, per-position notional
capped at max_lev x equity. Output: backtest_out/report.md + trades_<variant>.csv
"""
import argparse, json, time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import macro
from .common import API, load_config, log, setup_logging
from .scanner import atr, ema, rsi

DEF = dict(
    coins=["BTC", "ETH", "SOL", "HYPE", "XRP", "NEAR", "DOGE", "LINK", "UNI", "AAVE", "SUI", "AVAX", "TON", "PENDLE", "ZEC"],
    start="2023-06-01", equity0=1000.0, risk_pct=1.5, max_positions=3, max_lev=3.0,
    maker_fee=0.00015, taker_fee=0.00045, cache_dir="miner_cache", out_dir="backtest_out",
    interval="1d",  # 1d | 4h | 1h ; indicator horizons stay in *days* (scaled to bars) unless native=True
    native=False,
)
INTERVAL_H = {"1d": 24, "4h": 4, "1h": 1}

VARIANTS = {
    # current scanner rule: trend-following pullback, both sides, target 20d extreme, 7d time stop
    "scanner_current": dict(side="both", trend="with", rsi_max=45, rsi_min=55, level="pullback", stop_atr=1.5,
                            target="range", min_rr=2.0, max_days=7, trail_atr=None),
    # miner finding: long-only pullback near 20d low / below EMA20, neutral-oversold RSI, let it run with trailing stop
    "pullback_long": dict(side="long", trend="any", rsi_max=50, level="pullback", stop_atr=1.5,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "pullback_long_7d": dict(side="long", trend="any", rsi_max=50, level="pullback", stop_atr=1.5,
                             target="range", min_rr=2.0, max_days=7, trail_atr=None),
    "pullback_long_counter": dict(side="long", trend="against", rsi_max=50, level="pullback", stop_atr=1.5,
                                  target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # miner finding: long breakout of 20d high in uptrend, trailing
    "breakout_long": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # sensitivity of the breakout rule (not optimised: one parameter moved at a time)
    "breakout_long_hold45": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                                 target=None, min_rr=0, max_days=45, trail_atr=3.0),
    "breakout_long_trail2": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                                 target=None, min_rr=0, max_days=21, trail_atr=2.0),
    "breakout_long_stop15": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=1.5,
                                 target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_long_anytrend": dict(side="long", trend="any", rsi_max=100, level="breakout", stop_atr=2.0,
                                   target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # short side of the breakout rule (close below 20d low in a downtrend) and both sides together
    "breakout_short": dict(side="short", trend="with", rsi_min=0, level="breakout", stop_atr=2.0,
                           target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_short_anytrend": dict(side="short", trend="any", rsi_min=0, level="breakout", stop_atr=2.0,
                                    target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_both": dict(side="both", trend="with", rsi_max=100, rsi_min=0, level="breakout", stop_atr=2.0,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # control: mirror of pullback_long on the short side (miner says this should be poor)
    "pullback_short": dict(side="short", trend="any", rsi_min=50, level="pullback", stop_atr=1.5,
                           target=None, min_rr=0, max_days=21, trail_atr=3.0),
}


# ----------------------------------------------------------------------------- data
def post(body, retries=10):
    for i in range(retries):
        r = requests.post(f"{API}/info", json=body, timeout=30)
        if r.status_code == 429:
            time.sleep(5 + 5 * i)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited")


def bars(coin, cache, interval="1d"):
    p = cache / f"bt_{interval}_{coin}.json"
    # a day-old cache would end before freshly fetched coins do, and look like a delisting
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, end = [], int(time.time() * 1000)
        while True:  # API returns the last ~5000 candles before endTime -> page backwards
            b = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": 0, "endTime": end}})
            b = [x for x in b if x["t"] < (d[0]["t"] if d else end + 1)]
            if not b:
                break
            d = b + d
            if len(b) < 4000:
                break
            end = d[0]["t"] - 1
            time.sleep(0.2)
        p.write_text(json.dumps(d))
    if not d:
        return None
    step = f"{INTERVAL_H[interval]}h"
    df = pd.DataFrame(d)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.floor(step)
    for c in "ohlcv":
        df[c] = df[c].astype(float)
    df = df.set_index("t").sort_index()
    df = df[df.index < pd.Timestamp.utcnow().floor(step)]  # drop the live (incomplete) candle
    return df


def funding(coin, start_ms, cache, interval="1d"):
    """Hourly funding rates -> per-bar sum (fraction of notional paid by longs)."""
    p = cache / f"bt_fund_{coin}.json"
    if p.exists():
        d = json.loads(p.read_text())
    else:
        d, t = [], start_ms
        while True:
            b = post({"type": "fundingHistory", "coin": coin, "startTime": t})
            if not b:
                break
            d += b
            if len(b) < 500:
                break
            t = b[-1]["time"] + 1
            time.sleep(0.15)
        p.write_text(json.dumps(d))
    if not d:
        return pd.Series(dtype=float)
    s = pd.DataFrame(d)
    s["t"] = pd.to_datetime(s.time, unit="ms", utc=True).dt.floor(f"{INTERVAL_H[interval]}h")
    s["r"] = s.fundingRate.astype(float)
    return s.groupby("t").r.sum()


def features(df, k=1):
    """k = bars per day; indicator windows are the daily-system windows x k (k=1 -> native bars)."""
    df = df.copy()
    df["ema20"], df["ema50"], df["rsi"], df["atr"] = ema(df.c, 20 * k), ema(df.c, 50 * k), rsi(df.c, 14 * k), atr(df, 14 * k)
    df["hi20"], df["lo20"] = df.h.rolling(20 * k).max().shift(1), df.l.rolling(20 * k).min().shift(1)
    # inputs for the optional entry filters, all read off the completed signal bar
    df["vol_ratio"] = df.v / df.v.rolling(20 * k).mean()             # breakout conviction
    df["atr_pctile"] = (df.atr / df.c).rolling(100 * k).rank(pct=True)  # where vol sits vs its own recent range
    df["ext_atr"] = (df.c - df.ema20) / df.atr                        # how stretched above the mean, in ATR
    df["ret20"] = df.c.pct_change(20 * k)                             # for the cross-sectional rank
    rng = (df.h - df.l).replace(0, np.nan)
    df["close_loc"] = (df.c - df.l) / rng                              # 1 = closed at the high, 0 = at the low
    df["brk_atr"] = (df.c - df.hi20) / df.atr                          # how far past the level it closed
    return df


def context(data, fund, macro_f=None):
    """Adds the columns entry filters read that are *not* this coin's own chart: its funding, the
    BTC tape, where it ranks against the rest of the universe, and the macro backdrop. Everything
    is as-of the signal bar (macro already shifted a day in hlg.macro), so nothing here is knowable
    later than the bar that triggers the trade."""
    btc = data.get("BTC")
    btc_bull = (btc.c > ema(btc.c, 200)) if btc is not None else None
    rs_rank = pd.DataFrame({c: d.ret20 for c, d in data.items()}).rank(axis=1, pct=True)
    for c, d in data.items():
        d["funding_apr"] = fund[c].reindex(d.index).fillna(0.0) * 365 * 100
        d["btc_bull"] = btc_bull.reindex(d.index) if btc_bull is not None else np.nan
        d["rs_rank"] = rs_rank[c].reindex(d.index)
        for col in MACRO_COLS:
            d[col] = macro_f[col].reindex(d.index) if macro_f is not None and not macro_f.empty else np.nan
    return data


# ----------------------------------------------------------------------------- filters
MACRO_COLS = ["hy_stress", "vix_calm", "spx_bull", "dxy_headwind", "risk_on"]


def _pass(v, test):
    """A filter with no reading has no opinion. Rejecting on NaN would quietly drop the early
    sample (before rolling windows fill) and make filtered variants incomparable to the baseline,
    which would look like an edge and be an artefact."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return True
    return test(v)


# Thresholds are median splits or structurally motivated, never scanned for the best value on this
# sample: 2 ATR extension is exactly the stop distance, 0.5 is a median, the trend comparisons are
# each series against its own moving average. That is deliberate -- with this little data, a tuned
# threshold is a curve fit with extra steps.
FILTERS = {
    # this coin's own chart, beyond the breakout itself
    "vol_confirm": lambda k: _pass(getattr(k, "vol_ratio", None), lambda v: v >= 1.5),
    "squeeze": lambda k: _pass(getattr(k, "atr_pctile", None), lambda v: v <= 0.5),
    "not_extended": lambda k: _pass(getattr(k, "ext_atr", None), lambda v: v <= 2.0),
    # crypto-native, off this coin's chart
    "cheap_funding": lambda k: _pass(getattr(k, "funding_apr", None), lambda v: v <= 30),
    "btc_bull": lambda k: _pass(getattr(k, "btc_bull", None), bool),
    "rs_top_half": lambda k: _pass(getattr(k, "rs_rank", None), lambda v: v >= 0.5),
    # breakout-bar quality (entry study): no long upper wick / not already stretched past the level
    "strong_close": lambda k: _pass(getattr(k, "close_loc", None), lambda v: v >= 0.5),
    "near_level": lambda k: _pass(getattr(k, "brk_atr", None), lambda v: v <= 1.0),
    # macro / tradfi backdrop (hlg.macro)
    "no_credit_stress": lambda k: _pass(getattr(k, "hy_stress", None), lambda v: not v),
    "vix_calm": lambda k: _pass(getattr(k, "vix_calm", None), bool),
    "spx_bull": lambda k: _pass(getattr(k, "spx_bull", None), bool),
    "no_dxy_headwind": lambda k: _pass(getattr(k, "dxy_headwind", None), lambda v: not v),
    "risk_on": lambda k: _pass(getattr(k, "risk_on", None), bool),
}

BASE_BREAKOUT = VARIANTS["breakout_long"]
for _f in FILTERS:
    VARIANTS[f"breakout+{_f}"] = {**BASE_BREAKOUT, "filters": [_f]}


# ----------------------------------------------------------------------------- signals
def signal(k, V):
    """k = completed daily bar. Returns (side, stop, target) or None."""
    if np.isnan(k.ema50) or np.isnan(k.atr) or np.isnan(k.lo20):
        return None
    for f in V.get("filters", ()):
        if not FILTERS[f](k):
            return None
    up = k.ema20 > k.ema50
    px = k.c
    out = []
    if V["side"] in ("long", "both"):
        ok_trend = {"with": up, "against": not up, "any": True}[V["trend"]]
        if V["level"] == "pullback":
            at = px <= k.lo20 * 1.03 or (px <= k.ema20 and px >= k.ema20 * 0.95)
            ok = ok_trend and k.rsi <= V["rsi_max"] and at
        else:  # breakout
            ok = ok_trend and px > k.hi20
        if ok:
            stop = px - V["stop_atr"] * k.atr
            tgt = k.hi20 if V["target"] == "range" else None
            out.append(("L", stop, tgt))
    if V["side"] in ("short", "both"):
        ok_trend = {"with": not up, "against": up, "any": True}[V["trend"]]
        if V["level"] == "pullback":
            at = px >= k.hi20 * 0.97 or (px >= k.ema20 and px <= k.ema20 * 1.05)
            ok = ok_trend and k.rsi >= V["rsi_min"] and at
        else:
            ok = ok_trend and px < k.lo20
        if ok:
            stop = px + V["stop_atr"] * k.atr
            tgt = k.lo20 if V["target"] == "range" else None
            out.append(("S", stop, tgt))
    for side, stop, tgt in out:
        if tgt is not None:
            rr = abs(tgt - px) / max(abs(px - stop), 1e-9)
            if rr < V["min_rr"]:
                continue
        return side, stop, tgt
    return None


# ----------------------------------------------------------------------------- engine
def run(V, data, fund, P):
    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    days = [d for d in days if d >= pd.Timestamp(P["start"], tz="UTC")]
    eq, peak, dd = P["equity0"], P["equity0"], 0.0
    open_pos, trades, curve = {}, [], []
    # coin -> {side, stop, tgt, sd, mode, level, atr, leg_lo, leg_hi, bars}. mode: "open" = fill at the
    # next open (the rule as backtested); "confirm" = only after the next bar also closes above the
    # level; "fib" = limit order at a retrace of the breakout leg (entry study, README "Entries").
    pending = {}
    entry = V.get("entry", "open")
    for d in days:
        # 1) fills for pending signals at today's open
        # Same-day signals compete for max_positions slots. Fill the most liquid first (trailing
        # 30d median notional, as of the signal day): fixed in advance, and the order the live
        # scanner lists them in. Dict order used to decide this, i.e. the config file's coin order.
        for coin, q in sorted(pending.items(), key=lambda kv: -_prio(P, kv[0], kv[1]["sd"])):
            df = data[coin]
            if d not in df.index:
                continue
            k = df.loc[d]
            if q["mode"] == "await":      # confirm: waiting for this bar's close (step 3)
                continue
            if q["mode"] == "fib":
                q["bars"] += 1
                if q["bars"] > V.get("fib_valid", 5):
                    del pending[coin]
                    continue
                # level from the leg as known before this bar: no peeking at today's high
                lim = q["leg_hi"] - V["fib"] * (q["leg_hi"] - q["leg_lo"])
                q["leg_hi"] = max(q["leg_hi"], k.h)
                if k.l > lim:
                    continue
                px, intrabar = min(k.o, lim), True
            else:
                px, intrabar = k.o, False
            del pending[coin]
            if coin in open_pos or len(open_pos) >= P["max_positions"]:
                continue
            stop = q["stop"] if entry == "open" else px - V["stop_atr"] * q["atr"]
            dist = abs(px - stop)
            if dist <= 0:
                continue
            sz = eq * P["risk_pct"] / 100 / dist
            sz = min(sz, eq * P["max_lev"] / px)
            fee = sz * px * P["maker_fee"]
            eq -= fee
            open_pos[coin] = dict(coin=coin, side=q["side"], entry=px, sz=sz, stop=stop, tgt=q["tgt"], start=d, fee=fee,
                                  fund=0.0, best=px, sig_day=q["sd"], level=q["level"], bars=0,
                                  intrabar_day=d if intrabar else None)
        # 2) manage open positions on today's bar
        for coin, p in list(open_pos.items()):
            df = data[coin]
            if d not in df.index:
                if coin in P.get("delisted", ()) and d > df.index[-1]:  # delisted: close at the last traded price
                    k = df.iloc[-1]
                    sgn = 1 if p["side"] == "L" else -1
                    exit_px = k.c
                    fee = p["sz"] * exit_px * P["taker_fee"]
                    pnl = sgn * (exit_px - p["entry"]) * p["sz"]
                    net = pnl - fee - p["fee"] + p["fund"]
                    eq += pnl - fee + p["fund"]
                    trades.append(dict(coin=coin, side=p["side"], entry=p["entry"], exit=exit_px, start=p["start"], end=df.index[-1],
                                       days=(df.index[-1] - p["start"]).total_seconds() / 86400, why="delisted", gross=pnl,
                                       fees=fee + p["fee"], fund=p["fund"], net=net, net_pct=net / (eq - net) * 100,
                                       notional=p["sz"] * p["entry"]))
                    del open_pos[coin]
                continue
            k = df.loc[d]
            sgn = 1 if p["side"] == "L" else -1
            fr = fund[coin].get(d, 0.0)
            p["fund"] += -sgn * fr * p["sz"] * k.c  # longs pay positive funding
            exit_px, why = None, None
            if p["side"] == "L":
                if k.l <= p["stop"]:
                    exit_px, why = min(k.o, p["stop"]), "stop"
                elif p["tgt"] and k.h >= p["tgt"]:
                    exit_px, why = max(k.o, p["tgt"]), "target"
            else:
                if k.h >= p["stop"]:
                    exit_px, why = max(k.o, p["stop"]), "stop"
                elif p["tgt"] and k.l <= p["tgt"]:
                    exit_px, why = min(k.o, p["tgt"]), "target"
            p["bars"] = p.get("bars", 0) + 1
            if exit_px is None and V.get("fail_exit_bars") and p["bars"] <= V["fail_exit_bars"] and k.c < p["level"]:
                exit_px, why = k.c, "failed"  # closed back below the breakout level: out now, not at the 2 ATR stop
            if exit_px is None and (d - p["start"]) >= pd.Timedelta(days=V["max_days"]):
                exit_px, why = k.c, "time"
            if exit_px is None and V["trail_atr"] and p.get("intrabar_day") != d:  # today's high may predate the fill
                p["best"] = max(p["best"], k.h) if sgn > 0 else min(p["best"], k.l)
                new_stop = p["best"] - sgn * V["trail_atr"] * k.atr
                p["stop"] = max(p["stop"], new_stop) if sgn > 0 else min(p["stop"], new_stop)
            if exit_px is not None:
                fee = p["sz"] * exit_px * P["taker_fee"]
                pnl = sgn * (exit_px - p["entry"]) * p["sz"]
                net = pnl - fee - p["fee"] + p["fund"]
                eq += pnl - fee + p["fund"]
                trades.append(dict(coin=coin, side=p["side"], entry=p["entry"], exit=exit_px, start=p["start"], end=d,
                                   days=(d - p["start"]).total_seconds() / 86400, why=why, gross=pnl, fees=fee + p["fee"], fund=p["fund"],
                                   net=net, net_pct=net / (eq - net) * 100, notional=p["sz"] * p["entry"]))
                del open_pos[coin]
        # 3) mark to market + signals at close
        mtm = eq + sum((1 if p["side"] == "L" else -1) * (data[c].loc[d].c - p["entry"]) * p["sz"]
                       for c, p in open_pos.items() if d in data[c].index)
        peak = max(peak, mtm)
        dd = min(dd, mtm / peak - 1)
        curve.append((d, mtm))
        # confirm: the bar after the breakout has closed -- keep the entry only if it held the level
        for coin, q in list(pending.items()):
            if q["mode"] == "await" and d in data[coin].index and d > q["sd"]:
                if data[coin].loc[d].c > q["level"]:
                    q["mode"] = "open"
                else:
                    del pending[coin]
        elig = P.get("elig_by_day")
        for coin in (elig.get(d.floor("D"), ()) if elig is not None else data):
            df = data[coin]
            if d not in df.index or coin in open_pos:
                continue
            if coin in pending and pending[coin]["mode"] in ("fib", "await"):
                continue  # a retrace order or a confirmation is already working
            k = df.loc[d]
            s = signal(k, V)
            if s:
                side, stop, tgt = s
                pending[coin] = dict(side=side, stop=stop, tgt=tgt, sd=d, level=k.hi20, atr=k.atr, leg_lo=k.lo20,
                                     leg_hi=k.h, bars=0, mode={"open": "open", "confirm": "await", "fib": "fib"}[entry])
    T = pd.DataFrame(trades)
    C = pd.Series(dict(curve))
    return T, C, dd


def stats(T, C, dd, P):
    if T.empty:
        return dict(trades=0)
    yrs = (C.index[-1] - C.index[0]).days / 365.25
    g = T.net[T.net > 0].sum()
    l = -T.net[T.net < 0].sum()
    daily_ret = C.resample("1D").last().dropna().pct_change().dropna()
    return dict(trades=len(T), win_rate=(T.net > 0).mean(), pf=g / max(l, 1e-9),
                avg_win_pct=T.net_pct[T.net > 0].mean(), avg_loss_pct=T.net_pct[T.net < 0].mean(),
                net=C.iloc[-1] - P["equity0"], total_return_pct=(C.iloc[-1] / P["equity0"] - 1) * 100,
                cagr_pct=((C.iloc[-1] / P["equity0"]) ** (1 / yrs) - 1) * 100, max_dd_pct=dd * 100,
                sharpe=daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0,
                fees=T.fees.sum(), funding=T.fund.sum(), avg_days=T.days.mean(),
                exits=T.why.value_counts().to_dict())


def by(T, key):
    g = T.groupby(key).net
    return pd.DataFrame(dict(trades=g.size(), net=g.sum().round(0), wr=g.apply(lambda x: (x > 0).mean()).round(2),
                             pf=g.apply(lambda x: min(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 99)).round(2)))


def bootstrap_pf(base_net, n, actual_pf, n_boot=20_000, seed=0):
    """Where does a filtered subset's PF sit against randomly dropping the same number of trades?

    A filter that keeps 70% of trades will change PF just by luck -- this strategy makes its money
    on ~20% of trades, so which ones you happen to keep dominates everything. Returns the percentile
    of `actual_pf` in that null distribution. Anything between 5 and 95 is indistinguishable from
    picking at random, no matter how good the rationale sounded."""
    rng = np.random.default_rng(seed)
    if n <= 0 or n > len(base_net):
        return float("nan")
    draws = np.array([_pf_of(rng.choice(base_net, size=n, replace=False)) for _ in range(n_boot)])
    return float((draws < actual_pf).mean() * 100)


def _pf_of(net):
    g, l = net[net > 0].sum(), -net[net < 0].sum()
    return min(g / max(l, 1e-9), 99)


def _prio(P, coin, day):
    m = P.get("liq_med")
    if m is None:
        return 0.0
    try:
        v = m.at[day.floor("D"), coin]
    except KeyError:
        return 0.0
    return 0.0 if pd.isna(v) else float(v)


# ----------------------------------------------------------------------------- universe
def daily_notional(df):
    """USD traded per UTC day, from the coin's own candles (base volume x close)."""
    return (df.v * df.c).resample("1D").sum()


def liquidity(data, window=30):
    """Trailing median daily notional, day x coin, shifted a day: the value on day T only uses days
    up to T-1, so ranking on it cannot see the day it is used on."""
    vol = pd.DataFrame({c: daily_notional(df) for c, df in data.items()})
    return vol, vol.rolling(window, min_periods=20).median().shift(1)


def eligibility(data, U, window=30):
    """Point-in-time universe: on each day, coins listed >= min_age_days, with a trailing median
    daily notional >= min_vol_usd, and in the top_n by it. Delisted coins take part while they
    traded -- dropping them would test only the survivors. Returns (mask day x coin, liquidity)."""
    vol, med = liquidity(data, window)
    first = pd.Series({c: df.index[0].floor("D") for c, df in data.items()})
    age = pd.DataFrame({c: (vol.index - first[c]).days for c in vol.columns}, index=vol.index)
    ok = (med >= U["min_vol_usd"]) & (age >= U["min_age_days"]) & vol.notna()
    if U.get("survivors"):
        # the biased control: today's top_n, applied to all of history
        last = med.iloc[-1].where(vol.iloc[-5:].notna().all())
        keep = set(last[last >= U["min_vol_usd"]].nlargest(U["top_n"]).index)
        mask = vol.notna() & pd.DataFrame({c: c in keep for c in vol.columns}, index=vol.index)
        return mask, med
    rank = med.where(ok).rank(axis=1, ascending=False, method="first")
    return rank <= U["top_n"], med


def by_day(mask):
    return {d: [c for c in mask.columns[row]] for d, row in zip(mask.index, mask.values)}


def candidate_pool():
    """Every perp on the main dex, delisted included."""
    meta = post({"type": "meta"})
    return [u["name"] for u in meta["universe"]], {u["name"] for u in meta["universe"] if u.get("isDelisted")}


def halves(T, mid):
    """PF on each half of the sample, split by signal date. One shared portfolio path, not two
    independent sims, so this answers "did the edge persist" -- not "what would a fresh book have
    made in the second half". A filter that only works in the first half is a fitted filter."""
    out = {}
    for label, t in [("IS", T[T.start < mid]), ("OOS", T[T.start >= mid])]:
        g, l = t.net[t.net > 0].sum(), -t.net[t.net < 0].sum()
        out[f"{label}_n"] = len(t)
        out[f"{label}_pf"] = round(min(g / max(l, 1e-9), 99), 2) if len(t) else 0.0
        out[f"{label}_net"] = round(t.net.sum()) if len(t) else 0
    return out


UNIVERSE_STUDY = {
    # pre-registered before running: auto_top30 is the candidate, the rest are sensitivity/controls
    "auto_top30": dict(top_n=30, min_vol_usd=10e6, min_age_days=60),
    "auto_top15": dict(top_n=15, min_vol_usd=10e6, min_age_days=60),
    "auto_top50": dict(top_n=50, min_vol_usd=10e6, min_age_days=60),
    "auto_top30_survivors": dict(top_n=30, min_vol_usd=10e6, min_age_days=60, survivors=True),
}


def universe_study(P, iv):
    """python -m hlg.backtest --universe-study [--interval 4h]

    Does the breakout edge survive scanning the liquid market instead of a hand-picked list? Runs
    breakout_long on the fixed list and on point-in-time liquidity universes over the whole HL perp
    history, delisted coins included. Adoption rule, fixed before the first run: auto_top30 ships if
    PF >= 1.4, max DD no worse than the fixed list's + 5pp, and second-half PF > 1.2."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    pool, delisted = candidate_pool()
    log.info("candidate pool: %d perps (%d delisted)", len(pool), len(delisted))
    data = {}
    for c in pool:
        try:
            df = bars(c, cache, iv)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: bars failed (%s), skipped", c, e)
            continue
        if df is not None and len(df) >= 60:
            data[c] = features(df, 1)
    V = VARIANTS["breakout_long"]
    start = pd.Timestamp(P["start"], tz="UTC")
    fixed = [c for c in DEF["coins"] if c in data]
    masks = {name: eligibility(data, U) for name, U in UNIVERSE_STUDY.items()}
    liq_med = next(iter(masks.values()))[1]
    ever = set(fixed)
    for m, _ in masks.values():
        ever |= set(m.columns[m[m.index >= start].any()])
    start_ms = int(start.timestamp() * 1000)
    fund = {}
    for i, c in enumerate(sorted(ever)):
        fund[c] = funding(c, start_ms, cache, iv)
        if i % 10 == 0:
            log.info("funding %d/%d", i + 1, len(ever))
    fund = {c: fund.get(c, pd.Series(dtype=float)) for c in data}
    runs = {"fixed": (fixed, None)} | {n: (sorted(ever), by_day(m.loc[m.index >= start - pd.Timedelta(days=1)])) for n, (m, _) in masks.items()}
    rows, notes = {}, []
    for name, (coins, elig) in runs.items():
        sub = {c: data[c] for c in coins}
        Q = {**P, "liq_med": liq_med, "elig_by_day": elig, "delisted": delisted}
        T, C, dd = run(V, sub, {c: fund[c] for c in coins}, Q)
        s = stats(T, C, dd, Q)
        days = sorted(set().union(*[set(d.index) for d in sub.values()]))
        days = [d for d in days if d >= start]
        s |= halves(T, days[len(days) // 2])
        months = max((days[-1] - days[0]).days / 30.44, 1)
        s["signals_per_month"] = len(T) / months
        s["profit_share_new_coins"] = (T[~T.coin.isin(DEF["coins"])].net.sum() / T.net.sum()) if len(T) and T.net.sum() else float("nan")
        s["delisted_exits"] = int((T.why == "delisted").sum()) if len(T) else 0
        if elig is not None:
            m = masks[name][0]
            per_year = m[m.index >= start].sum(axis=1).groupby(m[m.index >= start].index.year).mean().round(1).to_dict()
            notes.append(f"- `{name}`: mean eligible coins per day by year {per_year}")
        rows[name] = s
        T.to_csv(out / f"universe_{iv}_{name}.csv", index=False)
        log.info("%s: trades %s pf %.2f dd %.1f", name, s.get("trades"), s.get("pf", 0), s.get("max_dd_pct", 0))
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "sharpe", "IS_pf", "OOS_pf",
            "signals_per_month", "profit_share_new_coins", "delisted_exits"]
    base, cand = rows["fixed"], rows.get("auto_top30", {})
    passed = bool(cand.get("trades")) and cand["pf"] >= 1.4 and cand["max_dd_pct"] >= base["max_dd_pct"] - 5 and cand["OOS_pf"] > 1.2
    rep = [f"# Universe study ({iv}, breakout_long, {P['start']} -> now)\n",
           f"Pool: {len(data)} perps with history, {len(delisted)} delisted in meta. Fixed list: {', '.join(fixed)}. "
           "Same-day signals fill most-liquid first in every run.\n",
           S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n",
           "Eligibility per day:\n" + "\n".join(notes) + "\n",
           f"Adoption rule (fixed before running): auto_top30 PF >= 1.4, max DD >= fixed - 5pp, OOS PF > 1.2 -> **{'PASS' if passed else 'FAIL'}**\n"]
    (out / f"universe_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


ENTRY_STUDY = {
    # pre-registered before running (README "Entries"); every variant is breakout_long plus one change
    "base": {},
    "fail_exit2": {"fail_exit_bars": 2},
    "confirm": {"entry": "confirm"},
    "fib382": {"entry": "fib", "fib": 0.382, "fib_valid": 5},
    "fib50": {"entry": "fib", "fib": 0.5, "fib_valid": 5},
    "strong_close": {"filters": ["strong_close"]},
    "near_level": {"filters": ["near_level"]},
}


def entry_study(P, iv):
    """python -m hlg.backtest --entry-study [--interval 4h]

    Can failed breakouts be avoided or made cheaper? Rule fixed before running: an entry/exit variant
    passes if PF >= base + 0.10, max DD no worse than base - 2pp, and PF in each half >= the base's in
    that half; a filter must additionally sit above the 95th percentile of randomly dropping the
    same number of base trades."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(P["start"], tz="UTC")
    data, fund = {}, {}
    for c in DEF["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80:
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, int(start.timestamp() * 1000), cache, iv)
    _, liq_med = liquidity(data)
    Q = {**P, "liq_med": liq_med}
    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    days = [d for d in days if d >= start]
    mid = days[len(days) // 2]
    rows, trades = {}, {}
    for name, extra in ENTRY_STUDY.items():
        V = {**VARIANTS["breakout_long"], **extra}
        T, C, dd = run(V, data, fund, Q)
        s = stats(T, C, dd, Q) | halves(T, mid)
        s["failed_exits"] = int((T.why == "failed").sum()) if len(T) else 0
        rows[name], trades[name] = s, T
        T.to_csv(out / f"entry_{iv}_{name}.csv", index=False)
        log.info("%s: trades %s pf %.2f dd %.1f", name, s.get("trades"), s.get("pf", 0), s.get("max_dd_pct", 0))
    b = rows["base"]
    base_net = trades["base"].net.values
    verdict = {}
    for name, s in rows.items():
        if name == "base":
            continue
        ok = (s["pf"] >= b["pf"] + 0.10 and s["max_dd_pct"] >= b["max_dd_pct"] - 2
              and s["IS_pf"] >= b["IS_pf"] and s["OOS_pf"] >= b["OOS_pf"])
        pct = None
        if ENTRY_STUDY[name].get("filters"):
            pct = bootstrap_pf(base_net, s["trades"], s["pf"]) if s["trades"] < len(base_net) else float("nan")
            ok = ok and pct is not None and pct > 95
        verdict[name] = ("PASS" if ok else "fail") + (f" (random-subset pctile {pct:.0f})" if pct is not None else "")
    # the cost side of waiting for a retrace: base winners the variant never traded
    big = trades["base"].nlargest(max(1, len(trades["base"]) // 5), "net") if len(trades["base"]) else trades["base"]
    missed = {}
    for name in ("confirm", "fib382", "fib50"):
        T = trades[name]
        have = set(zip(T.coin, T.start.dt.floor("D"))) if len(T) else set()
        # a variant trade on the same coin within 6 days of the base entry counts as "caught it"
        caught = sum(any(c == bc and 0 <= (t - bs.floor("D")).days <= 6 for c, t in have) for bc, bs in zip(big.coin, big.start))
        missed[name] = f"{len(big) - caught}/{len(big)}"
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "avg_loss_pct", "avg_win_pct", "IS_pf", "OOS_pf", "failed_exits"]
    rep = [f"# Entry study ({iv}, breakout_long on the fixed list, {P['start']} -> now)\n",
           S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n",
           "Verdicts (rule fixed before running): " + "; ".join(f"`{k}` {v}" for k, v in verdict.items()) + "\n",
           "Top-20% base winners the retrace/confirm variants never entered: "
           + ", ".join(f"`{k}` {v}" for k, v in missed.items()) + "\n"]
    (out / f"entry_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--variant", nargs="*")
    ap.add_argument("--coins", nargs="*")
    ap.add_argument("--interval", choices=list(INTERVAL_H))
    ap.add_argument("--native", action="store_true", help="use 20/50/14-bar windows on the chosen interval instead of day-equivalent windows")
    ap.add_argument("--filters", nargs="*", help=f"ad-hoc entry filters on the breakout rule: {', '.join(FILTERS)}")
    ap.add_argument("--no-macro", action="store_true", help="skip the FRED fetch (macro filters then never reject)")
    ap.add_argument("--universe-study", action="store_true", help="breakout on fixed list vs point-in-time liquidity universes")
    ap.add_argument("--entry-study", action="store_true", help="failed-breakout study: fast exit, confirmation, fib retrace entries, bar-quality filters")
    a = ap.parse_args()
    cfg = load_config(a.config) if Path(a.config).exists() else {}
    P = {**DEF, **cfg.get("backtest", {})}
    if a.coins:
        P["coins"] = a.coins
    if a.interval:
        P["interval"] = a.interval
    if a.native:
        P["native"] = True
    iv = P["interval"]
    if a.universe_study:
        return universe_study(P, iv)
    if a.entry_study:
        return entry_study(P, iv)
    k = 1 if P["native"] else 24 // INTERVAL_H[iv]
    tag = f"{iv}{'_native' if P['native'] and iv != '1d' else ''}"
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    if iv != "1d":
        out = out / tag
        out.mkdir(exist_ok=True)
    start_ms = int(pd.Timestamp(P["start"], tz="UTC").timestamp() * 1000)

    data, fund = {}, {}
    for c in P["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80 * k:
            log.warning("%s: no/short history, skipped", c)
            continue
        data[c] = features(df, k)
        fund[c] = funding(c, start_ms, cache, iv)
        log.info("%s: %d %s bars from %s, %d funding bars", c, len(df), iv, df.index[0].date(), len(fund[c]))

    macro_f = None
    if not a.no_macro:
        macro_f = macro.features(macro.frame(start=P["start"], cache_dir=str(cache)))
        log.info("macro: %d rows%s", len(macro_f), "" if len(macro_f) else " (filters will not reject)")
    context(data, fund, macro_f)
    if a.filters:
        VARIANTS[f"breakout+{'+'.join(a.filters)}"] = {**BASE_BREAKOUT, "filters": list(a.filters)}
        a.variant = [f"breakout+{'+'.join(a.filters)}"]

    R = [f"# Backtest report\n\nCoins: {', '.join(data)}. Start {P['start']}, equity ${P['equity0']:,.0f}, risk {P['risk_pct']}%/trade, "
         f"max {P['max_positions']} positions, notional cap {P['max_lev']}x equity, maker entry {P['maker_fee']*100:.3f}% / taker exit "
         f"{P['taker_fee']*100:.3f}%, real hourly funding. {iv} bars (indicator windows {'native' if k == 1 else f'x{k} = day-equivalent'}): "
         f"signal at close, fill next open, stop on low/high (gap -> open).\n"]
    all_days = sorted(set().union(*[set(d.index) for d in data.values()]))
    all_days = [d for d in all_days if d >= pd.Timestamp(P["start"], tz="UTC")]
    mid = all_days[len(all_days) // 2]
    summ = {}
    for name, V in VARIANTS.items():
        if a.variant and name not in a.variant:
            continue
        T, C, dd = run(V, data, fund, P)
        s = stats(T, C, dd, P)
        if not T.empty:
            s |= halves(T, mid)
        summ[name] = s
        if not T.empty:
            T.to_csv(out / f"trades_{name}.csv", index=False)
        log.info("%s: %s", name, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()})
        R.append(f"\n## {name}\n\n`{json.dumps(V)}`\n")
        if T.empty:
            R.append("no trades\n")
            continue
        R.append(f"trades {s['trades']} | WR {s['win_rate']:.0%} | PF {s['pf']:.2f} | avg win {s['avg_win_pct']:+.2f}% / avg loss {s['avg_loss_pct']:+.2f}% of equity | "
                 f"total {s['total_return_pct']:+.0f}% (CAGR {s['cagr_pct']:+.0f}%) | max DD {s['max_dd_pct']:.0f}% | Sharpe {s['sharpe']:.2f} | "
                 f"fees ${s['fees']:.0f} funding ${s['funding']:+.0f} | avg hold {s['avg_days']:.1f}d | exits {s['exits']}\n")
        R.append("### By year\n" + by(T.assign(year=T.end.dt.year), "year").to_markdown() + "\n")
        R.append("### By coin\n" + by(T, "coin").sort_values("net", ascending=False).to_markdown() + "\n")
        R.append("### By exit\n" + by(T, "why").to_markdown() + "\n")
    S = pd.DataFrame(summ).T
    if not S.empty:
        cols = [c for c in ["trades", "win_rate", "pf", "total_return_pct", "cagr_pct", "max_dd_pct", "sharpe", "avg_days",
                            "IS_n", "IS_pf", "OOS_n", "OOS_pf"] if c in S]
        R.insert(1, "## Summary\n\n" + S[cols].astype(float).round(2).to_markdown() + "\n"
                 + f"\nIS/OOS split at {mid.date()} by signal date (shared portfolio path, see `halves`).\n")
    # Only filtered variants are comparable this way: they select a subset of the baseline's own
    # signals, so "dropping trades at random" is the right null. A different rule (pullback, short,
    # a wider stop) trades on its own signals and is not a subset -- the test would be meaningless.
    if "breakout_long" in summ and any(VARIANTS[n].get("filters") for n in summ if n in VARIANTS):
        base_net = pd.read_csv(out / "trades_breakout_long.csv").net.values
        rows = []
        for name in summ:
            if not VARIANTS.get(name, {}).get("filters") or not summ[name].get("trades"):
                continue
            n, p = summ[name]["trades"], summ[name]["pf"]
            if n >= len(base_net):
                verdict, pct = "no-op: kept every trade, nothing to test", float("nan")
            else:
                pct = bootstrap_pf(base_net, n, p)
                verdict = "noise" if 5 <= pct <= 95 else ("better" if pct > 95 else "WORSE")
            rows.append(dict(variant=name, kept=f"{n / len(base_net):.0%}", pf=round(p, 2),
                             vs_base=round(p - _pf_of(base_net), 2),
                             pctile="-" if np.isnan(pct) else round(pct, 1), verdict=verdict))
        if rows:
            R.append("\n## Against chance\n\nPercentile of each filtered variant's PF in the distribution from dropping the same "
                     "number of baseline trades at random (20k draws). 5-95 = indistinguishable from luck. Only filters appear here: "
                     "they select a subset of the baseline's signals, so random subsetting is the right null; a different rule is not "
                     f"a subset and is not comparable this way. Testing many filters inflates this -- with {len(rows)}, expect "
                     f"~{0.05 * len(rows):.1f} above the 95th by chance alone.\n\n"
                     + pd.DataFrame(rows).to_markdown(index=False) + "\n")
    R.append("\nCaveats: bar-based (intra-bar stop-outs that recovered by the close are counted as stops only if the low touched — "
             "realistic — but entries are next-day open, not intraday); no slippage; HL history only (most alts start 2023-2024); "
             "parameters were chosen from the miner, not optimised on this data, but the coin list overlaps with the miner sample.")
    (out / "report.md").write_text("\n".join(R))
    print("\n".join(R[:2]))


if __name__ == "__main__":
    main()
