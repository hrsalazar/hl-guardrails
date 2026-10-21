"""Half risk in BTC downtrends? The live 1d breakout, flat 1.0% vs 0.5% when BTC < its 200-day EMA.

    python -m hlg.regimesize          # research only; writes backtest_out/regime_sizing_study.md

Pre-registered in docs/research/regime-sizing-study.md (2026-10-21): three windows on three sources,
every window must pass. Research only: nothing live reads this module.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF, VARIANTS, _pf_of, bars as hl_bars, features, funding as hl_funding, run
from .common import log, setup_logging
from .deephistory import SET_S, pick
from .retest import funding as bn_funding, klines
from .scanner import ema
from .sources import funding as src_funding

RULE = VARIANTS["breakout_long"]
LOW_RISK = 0.5
W1, W2, W3 = ("2013-01-01", "2020-01-01"), ("2020-01-01", "2023-06-01"), ("2023-06-01", None)


def btc_down(btc_close):
    """True on days BTC closed below its 200-day EMA (the signal day's own close, known at signal time)."""
    e = ema(btc_close, 200)
    return (btc_close < e).where(e.notna())


def with_risk(data, down):
    """Each coin's bars gain risk_x: 0.5 on BTC-downtrend days, 1.0 otherwise (and where BTC's trend is unknown)."""
    out = {}
    for c, df in data.items():
        d = down.reindex(df.index.floor("D")).set_axis(df.index)
        out[c] = df.assign(risk_x=np.where(d == True, LOW_RISK / 1.0, 1.0))  # noqa: E712
    return out


def stats_(T, C, a, b):
    t = T[(T.start >= pd.Timestamp(a, tz="UTC")) & ((T.start < pd.Timestamp(b, tz="UTC")) if b else True)]
    c = C[(C.index >= pd.Timestamp(a, tz="UTC")) & ((C.index < pd.Timestamp(b, tz="UTC")) if b else True)]
    r = c.resample("1D").last().dropna().pct_change().dropna()
    return {"trades": len(t), "pf": _pf_of(t.net.values) if len(t) else np.nan,
            "return_pct": (c.iloc[-1] / c.iloc[0] - 1) * 100, "max_dd_pct": float((c / c.cummax() - 1).min() * 100),
            "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0}


def load_w1(cache):
    data = {}
    for coin, aliases in SET_S.items():
        _, df = pick(aliases, *W1)
        if df is not None:
            df = df[(df.index < pd.Timestamp(W1[1], tz="UTC"))]
            data[coin] = features(df, 1)
    f = src_funding("binance", "BTC")
    fd = float(f.mean() * 3) if f is not None and len(f) else 0.0
    return data, {c: pd.Series(fd, index=d.index) for c, d in data.items()}, data["BTC"].c


def load_w2(cache):
    data, fund = {}, {}
    for c in DEF["coins"]:
        df = klines(c, "1d", cache)
        if df is None or len(df) < 80:
            continue
        df = df[df.index < pd.Timestamp(W2[1], tz="UTC")]
        data[c] = features(df, 1)
        fund[c] = bn_funding(c, "1d", cache, data[c].index)
    return data, fund, data["BTC"].c


def load_w3(cache):
    data, fund = {}, {}
    start_ms = int(pd.Timestamp(W3[0], tz="UTC").timestamp() * 1000)
    for c in DEF["coins"]:
        df = hl_bars(c, cache, "1d")
        if df is None or len(df) < 80:
            continue
        data[c] = features(df, 1)
        fund[c] = hl_funding(c, start_ms, cache, "1d")
    return data, fund, data["BTC"].c


def random_control(data, fund, P, win, share, n_draws=40, seed=0):
    """Max drawdown and Sharpe when the same share of signal days, picked at random, are sized at 0.5%."""
    rng = np.random.default_rng(seed)
    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    out = []
    for _ in range(n_draws):
        pick_ = pd.Series(rng.random(len(days)) < share, index=pd.DatetimeIndex(days))
        dd = {c: df.assign(risk_x=np.where(pick_.reindex(df.index).fillna(False), LOW_RISK, 1.0)) for c, df in data.items()}
        T, C, _ = run(RULE, dd, fund, P)
        s = stats_(T, C, *win)
        out.append((s["max_dd_pct"], s["sharpe"]))
    return np.array(out)


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    out.mkdir(exist_ok=True)
    rows, verdicts, extra = {}, {}, {}
    for name, win, loader in (("W1 2013-2019 (spot)", W1, load_w1), ("W2 2020-2023-05 (Binance)", W2, load_w2),
                              ("W3 2023-06-now (Hyperliquid)", W3, load_w3)):
        data, fund, btc = loader(cache)
        down = btc_down(btc)
        Q = {**P, "start": win[0], "risk_pct": 1.0, "max_positions": 3}
        Tf, Cf, _ = run(RULE, data, fund, Q)
        Tr, Cr, _ = run(RULE, with_risk(data, down), fund, Q)
        f, r = stats_(Tf, Cf, *win), stats_(Tr, Cr, *win)
        tdown = Tf[Tf.start.dt.floor("D").map(lambda d: down.get(d - pd.Timedelta(days=1)) == True)]  # noqa: E712
        share = len(tdown) / max(len(Tf), 1)
        rc = random_control(data, fund, Q, win, share)
        rows[f"{name}: flat 1.0%"] = f
        rows[f"{name}: 0.5% in BTC downtrends"] = r
        dd_gain = 1 - r["max_dd_pct"] / f["max_dd_pct"] if f["max_dd_pct"] else 0.0
        ret_cut = 1 - r["return_pct"] / f["return_pct"] if f["return_pct"] else 0.0
        verdicts[name] = {
            f"1. Sharpe {r['sharpe']:.2f} >= {f['sharpe']:.2f} + 0.05": bool(r["sharpe"] >= f["sharpe"] + 0.05),
            f"2. max DD {r['max_dd_pct']:.1f}% vs {f['max_dd_pct']:.1f}%: {dd_gain:.0%} smaller (>= 15%)": bool(dd_gain >= 0.15),
            f"3. return cut {ret_cut:.0%} <= DD cut {dd_gain:.0%}": bool(ret_cut <= dd_gain),
        }
        extra[name] = {"downtrend_share": share, "downtrend_trades": len(tdown),
                       "downtrend_pf": _pf_of(tdown.net.values) if len(tdown) else np.nan,
                       "uptrend_pf": _pf_of(Tf.drop(tdown.index).net.values) if len(Tf) > len(tdown) else np.nan,
                       "random_dd_median": float(np.median(rc[:, 0])), "random_sharpe_median": float(np.median(rc[:, 1])),
                       "regime_dd": r["max_dd_pct"], "regime_sharpe": r["sharpe"]}
        log.info("%s done", name, extra={"safe": True})
    ok = all(all(v.values()) for v in verdicts.values())
    rep = ["# Half risk in BTC downtrends (pre-registered: docs/research/regime-sizing-study.md)\n",
           "The live 1d breakout, 3 slots. Flat 1.0% vs 0.5% when BTC's close is below its 200-day EMA.\n",
           pd.DataFrame(rows).T.astype(float).round(2).to_markdown() + "\n", "## Adoption bar (every window)\n"]
    for name, v in verdicts.items():
        rep.append(f"**{name}**\n\n" + "\n".join(f"- {k}: **{'pass' if x else 'FAIL'}**" for k, x in v.items()) + "\n")
    rep.append(f"**Regime sizing: {'PASSES in every window' if ok else 'does not pass'}**\n")
    rep.append("## Descriptive (no bar)\n\n" + pd.DataFrame(extra).T.astype(float).round(2).to_markdown() + "\n")
    (out / "regime_sizing_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return verdicts, ok


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
