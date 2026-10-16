"""Macro trend sleeve: an EMA20/50 trend rule on indices, metals and oil, as diversification.

    python -m hlg.macrotrend          # research only; writes backtest_out/macro_trend_study.md

Pre-registered in docs/research/macro-trend-study.md (2026-10-16). The idea came from the TradFi
study's 2016 -> now data, so the verdict rests on 2001-2015, which no study here had used; 2016 -> now
only confirms. Research only: nothing live reads this module.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF, VARIANTS, _combined_book, _combined_run, _own_halves, _pf_of, features, run, stats
from .common import log, setup_logging
from .tradfi import xyz_funding_daily, yahoo

RULE = VARIANTS["ema_trend"]
INSTRUMENTS = {"^GSPC": "SP500", "^NDX": "XYZ100", "GC=F": "GOLD", "SI=F": "SILVER", "HG=F": "COPPER",
               "CL=F": "CL", "BZ=F": "BRENTOIL"}
FETCH_FROM = "2000-01-01"
PRIMARY = ("2001-01-01", "2016-01-01")
CONFIRM_FROM = "2016-01-01"
MIN_TRADES, MIN_PF, MIN_HALF, MIN_CONFIRM, DD_SLACK = 60, 1.3, 1.1, 1.2, 2.0


def _window(T, a, b=None):
    m = T.start >= pd.Timestamp(a, tz="UTC")
    if b:
        m &= T.start < pd.Timestamp(b, tz="UTC")
    return T[m]


def buy_hold(data, a, b=None):
    """Equal-weight buy-and-hold of the instruments over [a, b): annualised return and max drawdown."""
    rets = []
    for df in data.values():
        c = df.c[(df.index >= pd.Timestamp(a, tz="UTC")) & ((df.index < pd.Timestamp(b, tz="UTC")) if b else True)]
        if len(c) > 20:
            rets.append(c.pct_change().rename(None))
    r = pd.concat(rets, axis=1, sort=True).mean(axis=1).fillna(0.0)
    eq = (1 + r).cumprod()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return {"cagr_pct": (eq.iloc[-1] ** (1 / yrs) - 1) * 100, "max_dd_pct": (eq / eq.cummax() - 1).min() * 100}


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    data, fund_d = {}, {}
    for sym, perp in INSTRUMENTS.items():
        df = yahoo(sym, cache, start=FETCH_FROM)
        data[sym] = features(df, 1)
        try:
            fund_d[sym] = xyz_funding_daily(perp, cache) or 0.0
        except Exception as e:  # noqa: BLE001
            log.warning("xyz:%s funding: %s", perp, type(e).__name__)
            fund_d[sym] = 0.0
        log.info("%s: %d bars from %s", sym, len(df), df.index[0].date())
    fund = {k: pd.Series(fund_d[k], index=data[k].index) for k in data}

    # its own book, 3 slots, over the whole span; windows are cut by entry date
    Q = {**P, "start": PRIMARY[0], "risk_pct": 1.0, "max_positions": 3}
    T, C, dd = run(RULE, data, fund, Q)
    T.to_csv(out / "macro_trend_trades.csv", index=False)
    Tp, Tc = _window(T, *PRIMARY), _window(T, CONFIRM_FROM)
    h1, h2 = _own_halves(Tp)
    pf_p, pf_c = _pf_of(Tp.net.values), _pf_of(Tc.net.values)
    Cp = C[(C.index >= pd.Timestamp(PRIMARY[0], tz="UTC")) & (C.index < pd.Timestamp(PRIMARY[1], tz="UTC"))]
    Cc = C[C.index >= pd.Timestamp(CONFIRM_FROM, tz="UTC")]

    def curve_stats(c):
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        r = c.resample("1D").last().dropna().pct_change().dropna()
        return {"cagr_pct": ((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1) * 100,
                "max_dd_pct": (c / c.cummax() - 1).min() * 100,
                "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0}

    # rule 5: the crypto live book + the sleeve on its own slot(s), 2023-06 -> now
    cdata, cfund, extra, mid = _combined_book(P)
    Qc = {**P, "risk_pct": 1.0, **extra}
    _, crypto = _combined_run(cdata, cfund, Qc, mid)
    books = {}
    for slots in (1, 2):
        md = {**cdata, **{f"{k}|1d": data[k] for k in data}}
        mf = {**cfund, **{f"{k}|1d": fund[k] for k in data}}
        group_of = {k: "crypto" for k in cdata} | {f"{k}|1d": "macro" for k in data}
        V_of = {f"{k}|1d": RULE for k in data}
        Qm = {**Qc, "coin_of": {**extra["coin_of"], **{f"{k}|1d": k for k in data}}, "V_of": V_of,
              "group_max": {"crypto": 3, "macro": slots}, "group_of": group_of}
        Tm, books[slots] = _combined_run(md, mf, Qm, mid)
        books[slots]["macro_trades"] = int(Tm.coin.map(group_of.get).eq("macro").sum())
    b1 = books[1]
    # sleeve alone (1 slot) over the live window, for the correlation with crypto
    _, Cs, _ = run(RULE, {f"{k}|1d": data[k] for k in data}, {f"{k}|1d": fund[k] for k in data},
                   {**P, "risk_pct": 1.0, "max_positions": 1})
    _, Ccr, _ = run(VARIANTS["breakout_long"], cdata, cfund, Qc)
    rc = Ccr.resample("1D").last().dropna().pct_change().dropna()
    rs = Cs.resample("1D").last().dropna().pct_change().dropna().reindex(rc.index).fillna(0.0)
    corr = float(rc.corr(rs))

    rules = {
        f"1. 2001-2015: >= {MIN_TRADES} trades ({len(Tp)})": len(Tp) >= MIN_TRADES,
        f"2. 2001-2015: PF >= {MIN_PF} ({pf_p:.2f})": bool(pf_p >= MIN_PF),
        f"3. 2001-2015: PF >= {MIN_HALF} in both halves ({h1:.2f} / {h2:.2f})": bool(h1 >= MIN_HALF and h2 >= MIN_HALF),
        f"4. 2016 -> now: PF >= {MIN_CONFIRM} ({pf_c:.2f})": bool(pf_c >= MIN_CONFIRM),
        f"5. book Sharpe {b1['sharpe']:.2f} > crypto {crypto['sharpe']:.2f}, DD {b1['max_dd_pct']:.1f}% within {DD_SLACK:g}pp "
        f"of {crypto['max_dd_pct']:.1f}%": bool(b1["sharpe"] > crypto["sharpe"] and b1["max_dd_pct"] >= crypto["max_dd_pct"] - DD_SLACK),
    }
    ok = all(rules.values())

    def win(t, c, bh):
        s = curve_stats(c)
        return {"trades": len(t), "win_rate": (t.net > 0).mean() if len(t) else np.nan, "pf": _pf_of(t.net.values),
                "avg_days": t.days.mean() if len(t) else np.nan, **s,
                "buyhold_cagr_pct": bh["cagr_pct"], "buyhold_max_dd_pct": bh["max_dd_pct"],
                "funding_net_pct": t.fund.sum() / Q["equity0"] * 100}
    W = pd.DataFrame({"2001-2015 (primary)": win(Tp, Cp, buy_hold(data, *PRIMARY)),
                      "2016 -> now (confirm)": win(Tc, Cc, buy_hold(data, CONFIRM_FROM))}).T
    per = (T.assign(window=np.where(T.start < pd.Timestamp(PRIMARY[1], tz="UTC"), "2001-2015", "2016-now"))
           .groupby(["coin", "window"]).net.agg(trades="size", pf=lambda x: _pf_of(x.values)).unstack("window").round(2))
    yearly = (T.assign(year=T.end.dt.year).groupby("year").net.sum() / Q["equity0"] * 100).round(1)
    days_in = sum(((T.end - T.start).dt.days).clip(lower=0)) / max(1, (C.index[-1] - C.index[0]).days)
    rep = ["# Macro trend sleeve (pre-registered: docs/research/macro-trend-study.md)\n",
           "Rule: EMA20 crosses above EMA50 -> long at the next open; 2 ATR stop, 3 ATR trail, out when EMA20 < EMA50; "
           "1.0% risk, its own book with 3 slots, xyz funding charged throughout.\n",
           W.astype(float).round(2).to_markdown() + "\n",
           f"Slot-days used / calendar days: {days_in:.2f} (3 slots). Correlation of daily P&L with the crypto book "
           f"(2023-06 -> now, sleeve alone on 1 slot): {corr:.2f}.\n",
           "## Combined with the crypto live book (2023-06 -> now)\n\n"
           + pd.DataFrame({"crypto alone": crypto, "+ macro trend, 1 slot": books[1], "+ macro trend, 2 slots": books[2]}).T
           .reindex(columns=["trades", "macro_trades", "pf", "total_return_pct", "max_dd_pct", "sharpe", "worst_day_pct"])
           .astype(float).round(2).to_markdown() + "\n",
           "## Adoption bar (fixed before running)\n\n" + "\n".join(f"- {k}: **{'pass' if v else 'FAIL'}**" for k, v in rules.items())
           + f"\n\n**Macro trend sleeve: {'PASSES' if ok else 'does not pass'}**\n",
           "## Per instrument (trades, PF)\n\n" + per.to_markdown() + "\n",
           "## P&L by exit year (% of starting equity)\n\n" + yearly.to_frame("net_pct").T.to_markdown() + "\n"]
    (out / "macro_trend_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return W, rules, ok


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
