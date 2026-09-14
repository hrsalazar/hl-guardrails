"""Setup miner: find discretionary, similar-capital winners on the Hyperliquid
leaderboard, reconstruct their round trips and report which setup fingerprints
are profitable across MANY wallets (not just one).

    python -m hlg.miner                 # uses config.yaml [miner]
    python -m hlg.miner --wallets 60    # smaller sample, faster

Output: miner_out/wallets.csv, miner_out/trades.csv, miner_out/report.md
"""
import argparse, json, sys, time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import API, load_config, log, setup_logging
from .scanner import ema, rsi

LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DEF = dict(
    equity_min=500, equity_max=50_000,
    windows_positive=["month", "allTime"],
    min_roi_90d=0.10,
    wallets=120,
    min_trades=40,
    max_trades_per_day=15,
    max_maker_share=0.6,
    min_median_hold_h=2,
    max_open_coins_delta_neutral=0.5,
    lookback_days=180,
    cache_dir="miner_cache",
    out_dir="miner_out",
    min_wallets_per_setup=5,
)


# ----------------------------------------------------------------------------- data
def post(body, retries=5):
    for i in range(retries):
        r = requests.post(f"{API}/info", json=body, timeout=30)
        if r.status_code == 429:
            time.sleep(2 + 2 * i)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited")


def leaderboard():
    return requests.get(LB, timeout=60).json()["leaderboardRows"]


def fills_by_time(addr, start_ms, cache):
    p = cache / f"{addr}.json"
    if p.exists():
        return json.loads(p.read_text())
    out, t = [], start_ms
    while True:
        b = post({"type": "userFillsByTime", "user": addr, "startTime": t, "aggregateByTime": True})
        if not b:
            break
        out += b
        if len(b) < 2000:
            break
        t = b[-1]["time"] + 1
        time.sleep(0.2)
    p.write_text(json.dumps(out))
    return out


def candles(coin, days, cache):
    p = cache / f"candles_{coin}.json"
    if p.exists():
        d = json.loads(p.read_text())
    else:
        end = int(time.time() * 1000)
        d = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d",
                                                    "startTime": end - (days + 60) * 86400_000, "endTime": end}})
        p.write_text(json.dumps(d))
        time.sleep(0.2)
    if not d:
        return None
    df = pd.DataFrame(d)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.floor("D")
    for c in "ohlcv":
        df[c] = df[c].astype(float)
    df = df.set_index("t").sort_index()
    df["ema20"], df["ema50"], df["rsi"] = ema(df.c, 20), ema(df.c, 50), rsi(df.c)
    df["hi20"], df["lo20"] = df.h.rolling(20).max().shift(1), df.l.rolling(20).min().shift(1)
    df["ret5"] = df.c.pct_change(5)
    return df


# ----------------------------------------------------------------------------- wallet filters
def candidates(rows, P):
    out = []
    for r in rows:
        eq = float(r["accountValue"])
        if not P["equity_min"] <= eq <= P["equity_max"]:
            continue
        w = {k: v for k, v in r["windowPerformances"]}
        if any(float(w[x]["pnl"]) <= 0 for x in P["windows_positive"]):
            continue
        roi_m = float(w["month"]["roi"])
        vlm_m = float(w["month"]["vlm"])
        if vlm_m <= 0 or vlm_m / eq > 2000:  # >2000x equity/month = MM / wash
            continue
        out.append(dict(addr=r["ethAddress"], equity=eq, roi_m=roi_m, pnl_m=float(w["month"]["pnl"]),
                        pnl_all=float(w["allTime"]["pnl"]), vlm_m=vlm_m))
    df = pd.DataFrame(out)
    df["score"] = df.roi_m.clip(upper=3) * np.log1p(df.pnl_all.clip(lower=0))
    return df.sort_values("score", ascending=False)


def episodes(f):
    """Reconstruct round trips per coin from fills (same logic as the wallet report)."""
    eps = []
    for coin, d in f.groupby("coin"):
        pos, ep = 0.0, None
        for r in d.sort_values("time").itertuples():
            signed = r.sz if r.side == "B" else -r.sz
            if ep is None:
                ep = dict(coin=coin, start=r.t, side="L" if signed > 0 else "S", pnl=0.0, fee=0.0,
                          maxntl=0.0, n=1, adds=0, adds_under=0, entry_px=r.px, avg=r.px, maker=0)
            else:
                ep["n"] += 1
                same_dir = (signed > 0) == (ep["side"] == "L")
                if same_dir and abs(pos) > 1e-12:
                    ep["adds"] += 1
                    under = (r.px < ep["avg"]) if ep["side"] == "L" else (r.px > ep["avg"])
                    ep["adds_under"] += int(under)
                    ep["avg"] = (ep["avg"] * abs(pos) + r.px * abs(signed)) / (abs(pos) + abs(signed))
            ep["maker"] += int(not r.crossed)
            pos += signed
            ep["pnl"] += r.closedPnl
            ep["fee"] += r.fee
            ep["maxntl"] = max(ep["maxntl"], abs(pos) * r.px)
            if abs(pos) < 1e-9:
                ep["end"], ep["exit_px"] = r.t, r.px
                eps.append(ep)
                ep, pos = None, 0.0
    E = pd.DataFrame(eps)
    if E.empty:
        return E
    E["net"] = E.pnl - E.fee
    E["hold_h"] = (E.end - E.start).dt.total_seconds() / 3600
    E["maker_share"] = E.maker / E.n
    return E


def wallet_style(f, E, days):
    """Classify a wallet; returns (keep: bool, reason, stats)."""
    st = dict(fills=len(f), trades=len(E), trades_per_day=len(E) / max(days, 1),
              maker_share=float((~f.crossed).mean()), median_hold_h=float(E.hold_h.median()),
              net=float(E.net.sum()), pf=float(E.net[E.net > 0].sum() / max(-E.net[E.net < 0].sum(), 1e-9)),
              wr=float((E.net > 0).mean()), coins=int(E.coin.nunique()),
              twap_share=float(f.twapId.notna().mean()) if "twapId" in f else 0.0)
    if st["trades"] < P_["min_trades"]:
        return False, "too_few_trades", st
    if st["trades_per_day"] > P_["max_trades_per_day"]:
        return False, "hft", st
    if st["maker_share"] > P_["max_maker_share"]:
        return False, "market_maker", st
    if st["median_hold_h"] < P_["min_median_hold_h"]:
        return False, "scalper", st
    if st["twap_share"] > 0.5:
        return False, "twap_bot", st
    # delta-neutral / hedged farmers: many simultaneous L and S episodes on same coin overlapping
    ov = 0
    for coin, d in E.groupby("coin"):
        L, S = d[d.side == "L"], d[d.side == "S"]
        if len(L) and len(S):
            ov += ((L.start.values[:, None] < S.end.values) & (L.end.values[:, None] > S.start.values)).any(axis=1).sum()
    if len(E) and ov / len(E) > P_["max_open_coins_delta_neutral"]:
        return False, "hedged_farmer", st
    if st["pf"] < 1.2:
        return False, "not_profitable_in_window", st
    return True, "ok", st


# ----------------------------------------------------------------------------- fingerprints
def fingerprint(E, cdl, equity):
    """Attach market context at entry and bucketed features to each episode."""
    rows = []
    for r in E.itertuples():
        c = cdl.get(r.coin)
        if c is None:
            continue
        day = r.start.floor("D") - pd.Timedelta(days=1)  # last completed candle before entry
        if day not in c.index:
            continue
        k = c.loc[day]
        if np.isnan(k.ema50) or np.isnan(k.rsi):
            continue
        trend = "up" if k.ema20 > k.ema50 else "down"
        aligned = (trend == "up") == (r.side == "L")
        dist_hi = (k.hi20 - r.entry_px) / r.entry_px * 100
        dist_lo = (r.entry_px - k.lo20) / r.entry_px * 100
        near = "breakout" if (r.side == "L" and dist_hi < 0) or (r.side == "S" and dist_lo < 0) else \
               "at_level" if min(abs(dist_hi), abs(dist_lo)) < 3 else "mid_range"
        rsi_b = "oversold" if k.rsi < 35 else "overbought" if k.rsi > 65 else "neutral"
        hold = pd.cut([r.hold_h], [0, 6, 24, 72, 168, 1e9], labels=["<6h", "6-24h", "1-3d", "3-7d", ">7d"])[0]
        size = pd.cut([r.maxntl / max(equity, 1)], [0, 0.5, 1.5, 3, 6, 1e9],
                      labels=["<0.5x", "0.5-1.5x", "1.5-3x", "3-6x", ">6x"])[0]
        rows.append(dict(addr=r.addr, coin=r.coin, side=r.side, net=r.net, hold=str(hold), size=str(size),
                         trend=trend, aligned=aligned, level=near, rsi=rsi_b,
                         adds="none" if r.adds == 0 else ("underwater" if r.adds_under else "pyramid"),
                         mom5="up" if k.ret5 > 0.03 else "down" if k.ret5 < -0.03 else "flat",
                         major=r.coin in ("BTC", "ETH", "SOL"), start=r.start))
    return pd.DataFrame(rows)


def agg(T, by, min_w, min_t=1):
    g = T.groupby(by)
    out = pd.DataFrame(dict(
        trades=g.size(), wallets=g.addr.nunique(), net=g.net.sum(), wr=g.net.apply(lambda x: (x > 0).mean()),
        pf=g.net.apply(lambda x: min(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 99.0)),
        # share of wallets for which this setup is net positive -> robustness across wallets
        w_pos=g.apply(lambda d: (d.groupby("addr").net.sum() > 0).mean(), include_groups=False),
    ))
    return out[(out.wallets >= min_w) & (out.trades >= min_t)].sort_values("pf", ascending=False)


def fmt(df, n=15):
    d = df.head(n).copy()
    d["net"] = d.net.round(0).astype(int)
    for c in ("wr", "pf", "w_pos"):
        d[c] = d[c].round(2)
    return d.to_markdown()


# ----------------------------------------------------------------------------- main
P_ = dict(DEF)


def main():
    global P_
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--wallets", type=int)
    ap.add_argument("--days", type=int)
    a = ap.parse_args()
    cfg = load_config(a.config) if Path(a.config).exists() else {}
    P_ = {**DEF, **cfg.get("miner", {})}
    if a.wallets:
        P_["wallets"] = a.wallets
    if a.days:
        P_["lookback_days"] = a.days
    cache, out = Path(P_["cache_dir"]), Path(P_["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)

    rows = leaderboard()
    C = candidates(rows, P_)
    log.info("leaderboard %d rows -> %d similar-capital profitable candidates", len(rows), len(C))
    start_ms = int((time.time() - P_["lookback_days"] * 86400) * 1000)

    kept, rejected, allE = [], {}, []
    for w in C.head(P_["wallets"] * 3).itertuples():  # over-fetch: many get rejected
        if len(kept) >= P_["wallets"]:
            break
        try:
            raw = fills_by_time(w.addr, start_ms, cache)
        except Exception as e:
            log.warning("%s fetch failed: %s", w.addr, e)
            continue
        if not raw:
            rejected["no_fills"] = rejected.get("no_fills", 0) + 1
            continue
        f = pd.DataFrame(raw)
        for c in ("px", "sz", "closedPnl", "fee"):
            f[c] = f[c].astype(float)
        f["t"] = pd.to_datetime(f.time, unit="ms", utc=True)
        f = f[~f.coin.str.startswith("@")]  # drop spot
        if f.empty:
            rejected["spot_only"] = rejected.get("spot_only", 0) + 1
            continue
        E = episodes(f)
        if E.empty:
            continue
        ok, why, st = wallet_style(f, E, P_["lookback_days"])
        if not ok:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        E["addr"] = w.addr
        allE.append(E)
        kept.append(dict(addr=w.addr, equity=w.equity, roi_month=w.roi_m, pnl_all=w.pnl_all, **st))
        log.info("keep %s eq=%.0f trades=%d pf=%.2f hold=%.0fh", w.addr[:10], w.equity, st["trades"], st["pf"], st["median_hold_h"])

    if not kept:
        log.error("no wallets passed the filters; loosen [miner] in config.yaml")
        sys.exit(1)
    W = pd.DataFrame(kept)
    W.to_csv(out / "wallets.csv", index=False)
    E = pd.concat(allE, ignore_index=True)
    eq = dict(zip(W.addr, W.equity))

    coins = E.coin.value_counts()
    coins = coins[coins >= 10].index
    cdl = {c: candles(c, P_["lookback_days"], cache) for c in coins}
    E["equity"] = E.addr.map(eq)
    T = pd.concat([fingerprint(E[E.addr == a_], cdl, eq[a_]) for a_ in W.addr], ignore_index=True)
    T.to_csv(out / "trades.csv", index=False)

    mw = P_["min_wallets_per_setup"]
    R = [f"# Setup miner report\n\n{len(rows)} leaderboard wallets -> {len(C)} with ${P_['equity_min']:,}-${P_['equity_max']:,} equity and positive "
         f"{'/'.join(P_['windows_positive'])} PnL -> **{len(W)} discretionary winners kept** after removing: "
         + ", ".join(f"{k} {v}" for k, v in sorted(rejected.items(), key=lambda x: -x[1]))
         + f".\nLookback {P_['lookback_days']}d, {len(T)} round trips with market context.\n",
         "Columns: `pf` profit factor, `wr` win rate, `w_pos` share of wallets for which the setup is net positive "
         "(robustness — trust rows with w_pos >= 0.6 and wallets >= %d).\n" % mw,
         "## Kept wallets\n", W.assign(net=W.net.round(0), pf=W.pf.round(2), median_hold_h=W.median_hold_h.round(0),
                                        maker_share=W.maker_share.round(2), equity=W.equity.round(0))
         [["addr", "equity", "trades", "pf", "wr", "median_hold_h", "maker_share", "coins", "net"]].to_markdown(index=False),
         "\n## By holding period\n", fmt(agg(T, "hold", mw)),
         "\n## By size (max notional / equity)\n", fmt(agg(T, "size", mw)),
         "\n## By side x trend alignment\n", fmt(agg(T, ["side", "aligned"], mw)),
         "\n## By entry location vs 20d range\n", fmt(agg(T, ["side", "level"], mw)),
         "\n## By daily RSI at entry\n", fmt(agg(T, ["side", "rsi"], mw)),
         "\n## By 5-day momentum at entry\n", fmt(agg(T, ["side", "mom5"], mw)),
         "\n## Adding behaviour\n", fmt(agg(T, "adds", mw)),
         "\n## Majors vs alts\n", fmt(agg(T, ["major", "side"], mw)),
         "\n## Top combined fingerprints (side, trend-aligned, level, hold, size)\n",
         fmt(agg(T, ["side", "aligned", "level", "hold", "size"], mw, 15), 20),
         "\n## Worst combined fingerprints\n",
         fmt(agg(T, ["side", "aligned", "level", "hold", "size"], mw, 15).sort_values("pf"), 10),
         "\n## By coin\n", fmt(agg(T, "coin", mw), 20),
         "\nCaveats: survivorship (winners only, past 6 months), leaderboard PnL includes unrealized, context uses daily candles "
         "only. Treat this as hypothesis generation; backtest before trading."]
    (out / "report.md").write_text("\n".join(R))
    log.info("wrote %s", out / "report.md")
    print("\n".join(R[:3]))
    print(R[6])


if __name__ == "__main__":
    main()
