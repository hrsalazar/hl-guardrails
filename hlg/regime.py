"""Regime analysis: does pullback ever beat breakout, and in which market state?

    python -m hlg.regime

Regime is tagged per day from BTC daily bars + breadth across the backtest coins:
  trend   : BTC close > EMA200 -> bull, else bear
  state   : BTC 20d high-low span < 5 ATR -> range, else trending
  breadth : share of coins with EMA20 > EMA50 (>0.6 broad-up, <0.4 broad-down, else mixed)
  dd      : BTC drawdown from 90d high (<-15% = deep)
Each trade of each variant is attributed to the regime on its signal day; a regime-switch rule
(pullback in regime X, breakout otherwise) is fitted on the first half and tested on the second.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF, VARIANTS, bars, by, features, funding, run, stats
from .common import load_config, log, setup_logging
from .scanner import atr, ema

CMP = ["breakout_long", "pullback_long", "pullback_long_counter", "scanner_current", "pullback_long_7d"]


def regimes(data, btc):
    b = btc.copy()
    b["ema200"], b["ema50"], b["atr"] = ema(b.c, 200), ema(b.c, 50), atr(b)
    b["rng20"] = b.h.rolling(20).max() - b.l.rolling(20).min()
    b["dd90"] = b.c / b.c.rolling(90).max() - 1
    trend = np.where(b.c > b.ema200, "bull", "bear")
    state = np.where(b.rng20 < 5 * b.atr, "range", "trending")  # 20d high-low span under 5 ATR = compressed / choppy
    br = pd.concat([(d.ema20 > d.ema50).astype(float) for d in data.values()], axis=1).mean(axis=1).reindex(b.index)
    breadth = pd.cut(br, [-0.01, 0.4, 0.6, 1.01], labels=["broad-down", "mixed", "broad-up"]).astype(str)
    dd = np.where(b.dd90 < -0.15, "deep-dd", "shallow")
    R = pd.DataFrame(dict(trend=trend, state=state, breadth=breadth.values, dd=dd, btc_dd90=b.dd90, breadth_pct=br.values), index=b.index)
    R["combo"] = R.trend + "/" + R.state
    return R


def tag(T, R):
    T = T.copy()
    sig = pd.DatetimeIndex(T.start) - pd.Timedelta(days=1)  # regime on the signal day (fill is next open)
    for c in ["trend", "state", "breadth", "dd", "combo"]:
        T[c] = R[c].reindex(sig).values
    return T


def pf(x):
    return min(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 99)


def main():
    setup_logging()
    P = {**DEF, **(load_config("config.yaml").get("backtest", {}) if Path("config.yaml").exists() else {})}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    start_ms = int(pd.Timestamp(P["start"], tz="UTC").timestamp() * 1000)
    data, fund = {}, {}
    for c in P["coins"]:
        df = bars(c, cache)
        if df is None or len(df) < 80:
            continue
        data[c], fund[c] = features(df), funding(c, start_ms, cache)
    R = regimes(data, bars("BTC", cache))
    R = R[R.index >= pd.Timestamp(P["start"], tz="UTC")]
    R.to_csv(out / "regimes.csv")

    res, curves = {}, {}
    for v in CMP:
        T, C, dd = run(VARIANTS[v], data, fund, P)
        res[v], curves[v] = tag(T, R), C
        log.info("%s: %s", v, {k: round(x, 2) for k, x in stats(T, C, dd, P).items() if isinstance(x, float)})

    out_md = ["# Regime report\n", f"Coins: {', '.join(data)}. {P['start']} -> {R.index[-1].date()}. Regime from BTC daily bars + breadth; "
              "each trade attributed to the regime on its signal day.\n"]
    # 1) PF / net per regime dimension, variants side by side
    for dim in ["trend", "state", "combo", "breadth", "dd"]:
        rows = {}
        for v, T in res.items():
            g = T.groupby(dim).net
            rows[(v, "n")], rows[(v, "pf")], rows[(v, "net")] = g.size(), g.apply(pf).round(2), g.sum().round(0)
        tab = pd.DataFrame(rows).fillna(0)
        days = R[dim].value_counts()
        tab.insert(0, ("regime", "days"), days.reindex(tab.index).fillna(0).astype(int))
        tab.columns = [f"{v} {k}" for v, k in tab.columns]
        out_md.append(f"\n## By {dim}\n\n" + tab.to_markdown() + "\n")
    # 2) rolling quarter comparison
    q = {}
    for v, T in res.items():
        g = T.groupby(T.end.dt.to_period("Q")).net
        q[(v, "n")], q[(v, "pf")], q[(v, "net")] = g.size(), g.apply(pf).round(2), g.sum().round(0)
    Q = pd.DataFrame(q).fillna(0)
    btc = bars("BTC", cache).c.resample("QE").last()
    Q.insert(0, ("BTC", "ret%"), (btc.pct_change() * 100).round(0).set_axis(btc.index.to_period("Q")).reindex(Q.index))
    Q.columns = [f"{v} {k}" for v, k in Q.columns]
    out_md.append("\n## By quarter\n\n" + Q.to_markdown() + "\n")
    # 3) current regime
    cur = R.iloc[-1]
    out_md.append(f"\n## Current regime ({R.index[-1].date()})\n\n{cur.trend}/{cur.state}, breadth {cur.breadth} ({cur.breadth_pct:.0%} coins EMA20>EMA50), "
                  f"BTC {cur.btc_dd90:+.1%} from 90d high ({cur.dd}). Last 30 days: {R.combo.iloc[-30:].value_counts().to_dict()}\n")
    # 4) regime switch, out of sample: choose per-combo best variant on first half, apply on second half
    mid = R.index[len(R) // 2]
    out_md.append(f"\n## Regime-switch test (fit < {mid.date()}, test >= {mid.date()})\n")
    best = {}
    for combo in R.combo.unique():
        sc = {v: T[(T.combo == combo) & (T.start < mid)].net.sum() for v, T in res.items()}
        best[combo] = max(sc, key=sc.get)
    out_md.append(f"in-sample best per regime: {best}\n")
    rows = []
    for v, T in res.items():
        t = T[T.start >= mid]
        rows.append(dict(rule=f"always {v}", n=len(t), pf=round(pf(t.net), 2), net=round(t.net.sum())))
    sw = pd.concat([T[(T.start >= mid) & (T.combo == combo)] for combo, v in best.items() for T in [res[v]]])
    rows.append(dict(rule="switch (in-sample best per regime)", n=len(sw), pf=round(pf(sw.net), 2), net=round(sw.net.sum())))
    out_md.append(pd.DataFrame(rows).to_markdown(index=False) + "\n")
    out_md.append("\nNote: the switch test sums trades of the chosen variant per regime (not a joint portfolio simulation), so its "
                  "net is indicative. Net figures are on the $1k compounding book of each variant and not directly additive.\n")
    (out / "regime_report.md").write_text("\n".join(out_md))
    print("\n".join(out_md))


if __name__ == "__main__":
    main()
