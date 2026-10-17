"""1d breakout on 2013-2019: the live rule on years no study here has used, plus a survivorship check.

    python -m hlg.deephistory         # research only; writes backtest_out/deep_history_test.md

Pre-registered in docs/research/deep-history-test.md (2026-10-18). Spot bars from the free sources in
hlg.sources; a perp-like funding cost (Binance BTC's lifetime mean) charged throughout. Research only.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF, VARIANTS, _own_halves, _pf_of, features, run
from .common import log, setup_logging
from .sources import bars, funding
from .tradfi import random_pf, rule_trades

RULE = VARIANTS["breakout_long"]
SPOT = ("binance_spot", "bitfinex", "bitstamp", "coinbase")
SET_S = {c: [c] for c in ("BTC", "ETH", "XRP", "ZEC", "LINK", "DOGE")}
SET_D = {"BCH": ["BCH", "BAB"], "ADA": ["ADA"], "LTC": ["LTC"], "XEM": ["XEM"], "XLM": ["XLM"], "IOTA": ["IOTA", "IOT"],
         "TRX": ["TRX"], "DASH": ["DASH", "DSH"], "NEO": ["NEO"], "XMR": ["XMR"], "EOS": ["EOS"], "QTUM": ["QTUM", "QTM"],
         "ICX": ["ICX"], "BTG": ["BTG"], "LSK": ["LSK"], "NANO": ["NANO"], "ETC": ["ETC"]}
WIN_S = ("2013-01-01", "2020-01-01")
WIN_D = ("2017-01-01", "2020-01-01")
WARMUP_DAYS = 120


def pick(aliases, a, b):
    """The spot source with the most daily bars inside [a, b); ties to the earlier start. -> (label, frame)."""
    best = (None, None, 0, None)
    for src in SPOT:
        for al in aliases:
            df = bars(src, al, "1d")
            if df is None:
                continue
            inside = df[(df.index >= pd.Timestamp(a, tz="UTC")) & (df.index < pd.Timestamp(b, tz="UTC"))]
            n = len(inside)
            if n and (n > best[2] or (n == best[2] and inside.index[0] < best[3])):
                best = (f"{src}:{al}", df, n, inside.index[0])
            break
    return best[0], best[1]


def judge(T, C, a, b, P):
    t = T[(T.start >= pd.Timestamp(a, tz="UTC")) & (T.start < pd.Timestamp(b, tz="UTC"))]
    h1, h2 = _own_halves(t)
    pf = _pf_of(t.net.values) if len(t) else float("nan")
    rng = np.random.default_rng(0)
    boot = np.array([_pf_of(rng.choice(t.net.values, size=len(t), replace=True)) for _ in range(10_000)]) if len(t) else np.array([np.nan])
    c = C[(C.index >= pd.Timestamp(a, tz="UTC")) & (C.index < pd.Timestamp(b, tz="UTC"))]
    r = c.resample("1D").last().dropna().pct_change().dropna()
    yrs = max((c.index[-1] - c.index[0]).days / 365.25, 1e-9) if len(c) else np.nan
    return t, dict(trades=len(t), coins=t.coin.nunique() if len(t) else 0, win_rate=(t.net > 0).mean() if len(t) else np.nan,
                   pf=pf, half1_pf=h1, half2_pf=h2, boot_p5=float(np.percentile(boot, 5)),
                   cagr_pct=((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1) * 100 if len(c) else np.nan,
                   max_dd_pct=float((c / c.cummax() - 1).min() * 100) if len(c) else np.nan,
                   sharpe=float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0)


def run_set(name, coins, win, fund_d, P):
    data, used = {}, {}
    for coin, aliases in coins.items():
        label, df = pick(aliases, *win)
        if df is None:
            log.info("%s %s: no spot history in the window", name, coin)
            continue
        df = df[df.index < pd.Timestamp(win[1], tz="UTC")]
        df = df[df.index >= pd.Timestamp(win[0], tz="UTC") - pd.Timedelta(days=WARMUP_DAYS)]
        if len(df) < 80:
            continue
        data[coin], used[coin] = features(df, 1), label
    fund = {c: pd.Series(fund_d, index=d.index) for c, d in data.items()}
    Q = {**P, "start": win[0], "risk_pct": 1.0, "max_positions": 3}
    T, C, _ = run(RULE, data, fund, Q)
    t, res = judge(T, C, *win, Q)
    return data, used, t, res


def study(P=None):
    P = {**(P or DEF)}
    out = Path(P["out_dir"])
    out.mkdir(exist_ok=True)
    f = funding("binance", "BTC")
    fund_d = float(f.mean() * 3) if f is not None and len(f) else 0.0       # 8-hourly -> per day
    dS, uS, tS, rS = run_set("S", SET_S, WIN_S, fund_d, P)
    dD, uD, tD, rD = run_set("D", SET_D, WIN_D, fund_d, P)
    vS = (rS["trades"] >= 50 and rS["pf"] >= 1.2 and rS["half1_pf"] > 1 and rS["half2_pf"] > 1 and rS["boot_p5"] > 1)
    vD = (rD["trades"] >= 50 and rD["pf"] >= 1.2 and rD["half1_pf"] > 1 and rD["half2_pf"] > 1)

    def yearly(t):
        return t.groupby(t.start.dt.year).net.agg(trades="size", pf=lambda x: _pf_of(x.values),
                                                    net_pct=lambda x: x.sum() / P["equity0"] * 100).round(2)

    def per_coin(t, used):
        g = t.groupby("coin").net.agg(trades="size", pf=lambda x: _pf_of(x.values)).round(2)
        g["source"] = g.index.map(used)
        return g.sort_values("trades", ascending=False)

    def vs_random(data, win):
        s0 = pd.Timestamp(win[0], tz="UTC")
        rr = [r for c, df in data.items() for r in rule_trades(df, fund_d, P, s0)]
        if not rr:
            return "no signals"
        rnd = random_pf({c: (df, fund_d) for c, df in data.items()}, len(rr), P, s0)
        pf = _pf_of(np.array(rr))
        return f"{len(rr)} signals, PF {pf:.2f} vs random median {np.median(rnd):.2f}, {(rnd < pf).mean() * 100:.0f}th percentile"

    venues = {}
    for src in ("bitstamp", "bitfinex", "coinbase"):
        df = bars(src, "BTC", "1d")
        if df is None:
            continue
        df = df[(df.index < pd.Timestamp(WIN_S[1], tz="UTC"))]
        T, C, _ = run(RULE, {"BTC": features(df, 1)}, {"BTC": pd.Series(fund_d, index=df.index)},
                      {**P, "start": "2016-01-01", "risk_pct": 1.0, "max_positions": 1})
        _, venues[src] = judge(T, C, "2016-01-01", WIN_S[1], P)          # 2016+: all three venues trade
    rep = ["# 1d breakout on 2013-2019 (pre-registered: docs/research/deep-history-test.md)\n",
           f"Funding charged: Binance BTC lifetime mean, {fund_d * 365 * 100:.1f}%/yr. Own book, 3 slots, 1.0% risk.\n",
           pd.DataFrame({f"S: live coins, {WIN_S[0]} -> 2019": rS, f"D: 2018 top-20 field, {WIN_D[0]} -> 2019": rD}).T.astype(float).round(2).to_markdown() + "\n",
           "## Verdicts (fixed before running)\n",
           f"- **Set S: {'confirmed' if vS else 'FAILS'}** (>= 50 trades, PF >= 1.2, both halves > 1.0, bootstrap p5 > 1.0)",
           f"- **Set D: {'confirmed' if vD else 'FAILS'}** (>= 50 trades, PF >= 1.2, both halves > 1.0)\n",
           "## Descriptive (no bar)\n",
           "**Set S by entry year:**\n\n" + yearly(tS).to_markdown() + "\n",
           "**Set D by entry year:**\n\n" + yearly(tD).to_markdown() + "\n",
           "**Set S per coin:**\n\n" + per_coin(tS, uS).to_markdown() + "\n",
           "**Set D per coin:**\n\n" + per_coin(tD, uD).to_markdown() + "\n",
           f"**vs random uptrend entries:** S: {vs_random(dS, WIN_S)}. D: {vs_random(dD, WIN_D)}.\n",
           "**BTC alone on three venues, 2016 -> 2019** (one slot):\n\n" + pd.DataFrame(venues).T.astype(float).round(2).to_markdown() + "\n",
           "Coins with no spot history in their window: "
           + ", ".join(sorted(set(SET_S) - set(uS)) + sorted(set(SET_D) - set(uD))) + ".\n"]
    (out / "deep_history_test.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rS, rD, vS, vD


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
