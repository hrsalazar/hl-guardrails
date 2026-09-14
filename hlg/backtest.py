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
    if p.exists():
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
    return df


# ----------------------------------------------------------------------------- signals
def signal(k, V):
    """k = completed daily bar. Returns (side, stop, target) or None."""
    if np.isnan(k.ema50) or np.isnan(k.atr) or np.isnan(k.lo20):
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
    pending = {}  # coin -> (side, stop, tgt, signal_day)
    for d in days:
        # 1) fills for pending signals at today's open
        for coin, (side, stop, tgt, sd) in list(pending.items()):
            del pending[coin]
            df = data[coin]
            if d not in df.index or coin in open_pos or len(open_pos) >= P["max_positions"]:
                continue
            o = df.loc[d].o
            dist = abs(o - stop)
            if dist <= 0:
                continue
            sz = eq * P["risk_pct"] / 100 / dist
            sz = min(sz, eq * P["max_lev"] / o)
            fee = sz * o * P["maker_fee"]
            eq -= fee
            open_pos[coin] = dict(coin=coin, side=side, entry=o, sz=sz, stop=stop, tgt=tgt, start=d, fee=fee, fund=0.0,
                                  best=o, sig_day=sd)
        # 2) manage open positions on today's bar
        for coin, p in list(open_pos.items()):
            df = data[coin]
            if d not in df.index:
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
            if exit_px is None and (d - p["start"]) >= pd.Timedelta(days=V["max_days"]):
                exit_px, why = k.c, "time"
            if exit_px is None and V["trail_atr"]:
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
        for coin, df in data.items():
            if d not in df.index or coin in open_pos:
                continue
            s = signal(df.loc[d], V)
            if s:
                pending[coin] = (*s, d)
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


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--variant", nargs="*")
    ap.add_argument("--coins", nargs="*")
    ap.add_argument("--interval", choices=list(INTERVAL_H))
    ap.add_argument("--native", action="store_true", help="use 20/50/14-bar windows on the chosen interval instead of day-equivalent windows")
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

    R = [f"# Backtest report\n\nCoins: {', '.join(data)}. Start {P['start']}, equity ${P['equity0']:,.0f}, risk {P['risk_pct']}%/trade, "
         f"max {P['max_positions']} positions, notional cap {P['max_lev']}x equity, maker entry {P['maker_fee']*100:.3f}% / taker exit "
         f"{P['taker_fee']*100:.3f}%, real hourly funding. {iv} bars (indicator windows {'native' if k == 1 else f'x{k} = day-equivalent'}): "
         f"signal at close, fill next open, stop on low/high (gap -> open).\n"]
    summ = {}
    for name, V in VARIANTS.items():
        if a.variant and name not in a.variant:
            continue
        T, C, dd = run(V, data, fund, P)
        s = stats(T, C, dd, P)
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
        cols = [c for c in ["trades", "win_rate", "pf", "total_return_pct", "cagr_pct", "max_dd_pct", "sharpe", "avg_days"] if c in S]
        R.insert(1, "## Summary\n\n" + S[cols].astype(float).round(2).to_markdown() + "\n")
    R.append("\nCaveats: bar-based (intra-bar stop-outs that recovered by the close are counted as stops only if the low touched — "
             "realistic — but entries are next-day open, not intraday); no slippage; HL history only (most alts start 2023-2024); "
             "parameters were chosen from the miner, not optimised on this data, but the coin list overlaps with the miner sample.")
    (out / "report.md").write_text("\n".join(R))
    print("\n".join(R[:2]))


if __name__ == "__main__":
    main()
