"""Capitulation long, open-interest confirmed, on 2020-2023 (untouched for this rule).

    python -m hlg.capitulation_oi     # research only; writes backtest_out/capitulation_oi_retest.md

Pre-registered in docs/research/capitulation-oi-retest.md (2026-10-18). Binance daily bars and funding,
Bybit daily open interest. Research only: nothing live reads this module.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF, VARIANTS, _own_halves, _pf_of, features, run, signal
from .capitulation import random_pf, sim
from .common import log, setup_logging
from .retest import funding as bn_funding, judge, klines, load
from .sources import open_interest

CAND = VARIANTS["capitulation"]
RULE = {**CAND, "cap_oi_max": -0.05}
WIN = ("2020-01-01", "2023-06-01")
FIELD_2018 = ("BCH", "ADA", "LTC", "XEM", "XLM", "IOTA", "TRX", "DASH", "NEO", "XMR", "EOS", "QTUM", "ICX", "BTG",
              "LSK", "NANO", "ETC")


def oi_change(df, oi):
    """OI over each daily bar: the snapshot at its close (next day 00:00) vs at its open; NaN if either is missing."""
    if oi is None:
        return pd.Series(np.nan, index=df.index)
    a = oi.reindex(df.index)
    b = oi.reindex(df.index + pd.Timedelta(days=1))
    return pd.Series(b.values / a.values - 1, index=df.index)


def signals(df, V, fees, start, end):
    """Every signal, one position per coin: [(R after fees, stop distance in ATR, entry time, exit why)]."""
    out, busy = [], None
    o, h, l, c = (df[x].values for x in "ohlc")
    for i in range(1, len(df)):
        t = df.index[i]
        if t < start or t >= end or (busy is not None and t <= busy):
            continue
        s = signal(df.iloc[i - 1], V)
        if not s:
            continue
        r = sim(df, i, s[1], s[2], fees)
        if r is None:
            continue
        k = df.iloc[i - 1]
        out.append((r, (k.c - s[1]) / k.atr_prev, t))
        busy = t + pd.Timedelta(days=V["max_days"])
    return out


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    out.mkdir(exist_ok=True)
    s0, s1 = (pd.Timestamp(x, tz="UTC") for x in WIN)
    fees = P["maker_fee"] + P["taker_fee"]
    coins = list(DEF["coins"]) + [c for c in FIELD_2018 if c not in DEF["coins"]]
    data, fund, oi_cov = {}, {}, {}
    for c in coins:
        try:
            df = klines(c, "1d", cache)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", c, type(e).__name__)
            continue
        if df is None or len(df) < 80:
            continue
        f = features(df, 1)
        oi = open_interest(c)
        f["oi_chg"] = oi_change(f, oi)
        data[c], fund[c] = f, bn_funding(c, "1d", cache, f.index)
        oi_cov[c] = oi.index[0].date().isoformat() if oi is not None and len(oi) else None
    log.info("coins with Binance 1d bars: %d, with Bybit OI: %d", len(data), sum(v is not None for v in oi_cov.values()),
             extra={"safe": True})

    # bars 1-3: the engine, one position per coin
    Q = {**P, "start": WIN[0], "risk_pct": 1.0, "max_positions": len(data)}
    T, C, dd = run(RULE, data, fund, Q)
    t = T[(T.start >= s0) & (T.start < s1)]
    h1, h2 = _own_halves(t)
    pf = _pf_of(t.net.values) if len(t) else float("nan")
    # bars 4-5: per-signal, OI-confirmed vs candle-only
    sig_oi = [x for c, df in data.items() for x in signals(df, RULE, fees, s0, s1)]
    sig_all = [x for c, df in data.items() for x in signals(df, CAND, fees, s0, s1)]
    oi_times = {(round(r, 9), tt) for r, _, tt in sig_oi}
    removed = [x for x in sig_all if (round(x[0], 9), x[2]) not in oi_times]
    pf_sig = _pf_of(np.array([r for r, _, _ in sig_oi])) if sig_oi else float("nan")
    pf_removed = _pf_of(np.array([r for r, _, _ in removed])) if removed else float("nan")
    rnd = random_pf({c: df[df.index < s1] for c, df in data.items()}, len(sig_oi),
                    np.array([d for _, d, _ in sig_oi]), fees, s0) if sig_oi else np.array([np.nan])
    pct = float((rnd < pf_sig).mean() * 100)
    # bar 6: the 15-coin breakout book (1d + 4h, 3 slots) with and without a capitulation slot
    d1, f1 = load("1d", cache)
    d4, f4 = load("4h", cache)
    bdata, bfund, coin_of, mark = {}, {}, {}, {}
    for tf, dd_, ff in (("1d", d1, f1), ("4h", d4, f4)):
        for c in dd_:
            k = f"{c}|{tf}"
            bdata[k], bfund[k], coin_of[k] = dd_[c], ff[c], c
            if tf == "4h":
                mark[c] = dd_[c].c
    Qb = {**P, "start": WIN[0], "risk_pct": 1.0, "coin_of": coin_of, "mark": mark}
    Tb, Cb, _ = run(VARIANTS["breakout_long"], bdata, bfund, {**Qb, "max_positions": 3})
    book = judge(Tb, Cb, P, *WIN)
    cap_keys = {f"{c}|1d|cap": c for c in DEF["coins"] if c in data}
    md = {**bdata, **{k: data[c] for k, c in cap_keys.items()}}
    mf = {**bfund, **{k: fund[c] for k, c in cap_keys.items()}}
    group_of = {k: "crypto" for k in bdata} | {k: "cap" for k in cap_keys}
    Tm, Cm, _ = run(VARIANTS["breakout_long"], md, mf, {**Qb, "coin_of": {**coin_of, **cap_keys}, "V_of": {k: RULE for k in cap_keys},
                                                         "group_max": {"crypto": 3, "cap": 1}, "group_of": group_of})
    both = judge(Tm, Cm, P, *WIN)
    both["cap_trades"] = int(Tm[(Tm.start >= s0) & (Tm.start < s1)].coin.map(group_of.get).eq("cap").sum())

    rules = {
        f"1. >= 50 trades ({len(t)})": len(t) >= 50,
        f"2. PF >= 1.3 ({pf:.2f})": bool(pf >= 1.3),
        f"3. PF >= 1.1 in both halves ({h1:.2f} / {h2:.2f})": bool(h1 >= 1.1 and h2 >= 1.1),
        f"4. above the 95th percentile of random down-bar entries ({pct:.0f}th)": pct > 95,
        f"5. OI-confirmed PF {pf_sig:.2f} > the removed candle-only signals' {pf_removed:.2f}": bool(pf_sig > pf_removed),
        f"6. book Sharpe {both['sharpe']:.2f} > {book['sharpe']:.2f}, DD {both['max_dd_pct']:.1f}% within 2pp of {book['max_dd_pct']:.1f}%":
            bool(both["sharpe"] > book["sharpe"] and both["max_dd_pct"] >= book["max_dd_pct"] - 2),
    }
    ok = all(rules.values())
    by_year = t.groupby(t.start.dt.year).net.agg(trades="size", pf=lambda x: _pf_of(x.values)).round(2) if len(t) else pd.DataFrame()
    per_coin = t.groupby("coin").net.agg(trades="size", pf=lambda x: _pf_of(x.values)).round(2).sort_values("trades", ascending=False) if len(t) else pd.DataFrame()
    with_oi = sum(1 for c, df in data.items() for _, _, tt in signals(df, CAND, fees, s0, s1) if not pd.isna(df.oi_chg.get(tt - pd.Timedelta(days=1))))
    rep = ["# Capitulation long, OI-confirmed, 2020-01 -> 2023-05 (pre-registered: docs/research/capitulation-oi-retest.md)\n",
           f"Coins with Binance 1d bars: {len(data)}; with Bybit OI: {sum(v is not None for v in oi_cov.values())}. "
           f"One position per coin, 1.0% risk, Binance funding.\n",
           "| | value |\n|---|---|\n"
           + "\n".join(f"| {k} | {v} |" for k, v in {
               "trades": len(t), "win rate": f"{(t.net > 0).mean():.0%}" if len(t) else "—", "PF": f"{pf:.2f}",
               "halves": f"{h1:.2f} / {h2:.2f}", "max DD": f"{dd * 100:.1f}%",
               "OI-confirmed signals (uncapped) / PF": f"{len(sig_oi)} / {pf_sig:.2f}",
               "candle-only signals the OI condition removed / PF": f"{len(removed)} / {pf_removed:.2f}",
               "candle-only signals with OI data that day": f"{with_oi} of {len(sig_all)}",
               "random down-bar entries: median PF / percentile": f"{np.median(rnd):.2f} / {pct:.0f}th",
               "exits": t.why.value_counts().to_dict() if len(t) else {}}.items()) + "\n",
           "## Book (15 coins, 1d + 4h breakouts, 3 slots, Binance)\n\n"
           + pd.DataFrame({"book": book, "book + OI capitulation slot": both}).T.reindex(
               columns=["trades", "cap_trades", "pf", "return_pct", "max_dd_pct", "sharpe"]).astype(float).round(2).to_markdown() + "\n",
           "## Adoption bar (fixed before running)\n\n" + "\n".join(f"- {k}: **{'pass' if v else 'FAIL'}**" for k, v in rules.items())
           + f"\n\n**OI-confirmed capitulation: {'PASSES' if ok else 'does not pass'}**\n",
           "## By entry year\n\n" + (by_year.to_markdown() if len(by_year) else "no trades") + "\n",
           "## Per coin\n\n" + (per_coin.to_markdown() if len(per_coin) else "no trades") + "\n",
           "Bybit OI starts: " + ", ".join(f"{c} {v}" for c, v in oi_cov.items() if v) + ".\n"]
    (out / "capitulation_oi_retest.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rules, ok


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
