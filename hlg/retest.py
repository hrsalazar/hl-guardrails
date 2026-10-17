"""Out-of-sample retest of the live rules on Binance 2020-2024 (never used by any study here).

    python -m hlg.retest              # research only; writes backtest_out/binance_retest.md

Pre-registered in docs/research/binance-retest.md (2026-10-16). Binance USDT-M daily and 4h bars with
Binance's own funding, the live rules unchanged. Research only: nothing live reads this module.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .backtest import (DEF, RALLY_STUDY, VARIANTS, _combined_book, _combined_run, _own_halves, _pf_of, features, run,
                       stats)
from .common import log, setup_logging
from .tradfi import random_pf, rule_trades

FAPI = "https://fapi.binance.com/fapi/v1"
FROM = "2019-09-01"          # warm-up before the 2020-01 start
STEP = {"1d": 86_400_000, "4h": 14_400_000}
R1 = ("2020-01-01", "2024-07-01")      # 4h breakout, untouched
R2 = ("2020-01-01", "2021-04-01")      # 1d breakout and rally short, untouched
OVERLAP = "2024-07-05"                 # Hyperliquid 4h history starts here
LONG, SHORT = VARIANTS["breakout_long"], RALLY_STUDY["rally_any"]


def klines(coin, tf, cache, get=requests.get):
    """Binance USDT-M bars from FROM, cached for a day; the live bar dropped. None if not listed."""
    p = cache / f"binance_{tf}_{coin}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, t = [], int(pd.Timestamp(FROM, tz="UTC").timestamp() * 1000)
        while True:
            r = get(f"{FAPI}/klines", params={"symbol": f"{coin}USDT", "interval": tf, "startTime": t, "limit": 1500}, timeout=20)
            if r.status_code == 400:      # not listed
                break
            r.raise_for_status()
            b = r.json()
            if not b:
                break
            d += [x[:6] for x in b]
            if len(b) < 1500:
                break
            t = b[-1][0] + STEP[tf]
            time.sleep(0.15)
        p.write_text(json.dumps(d))
    if not d:
        return None
    df = pd.DataFrame(d, columns=["t", "o", "h", "l", "c", "v"]).astype(float)
    df.index = pd.to_datetime(df.t.astype("int64"), unit="ms", utc=True)
    df = df.drop(columns="t")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[df.index + pd.Timedelta(milliseconds=STEP[tf]) <= pd.Timestamp.now(tz="UTC")]


def funding(coin, tf, cache, index, get=requests.get):
    """Binance funding (8-hourly) summed per bar; a fraction of notional paid by longs."""
    p = cache / f"binance_fund_{coin}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, t = [], int(pd.Timestamp(FROM, tz="UTC").timestamp() * 1000)
        while True:
            r = get(f"{FAPI}/fundingRate", params={"symbol": f"{coin}USDT", "startTime": t, "limit": 1000}, timeout=20)
            if r.status_code == 400:
                break
            r.raise_for_status()
            b = r.json()
            if not b:
                break
            d += [[x["fundingTime"], float(x["fundingRate"])] for x in b]
            if len(b) < 1000:
                break
            t = b[-1]["fundingTime"] + 1
            time.sleep(0.15)
        p.write_text(json.dumps(d))
    if not d:
        return pd.Series(0.0, index=index)
    s = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC").floor("4h" if tf == "4h" else "1D"): r for t, r in d})
    return s.groupby(level=0).sum().reindex(index, fill_value=0.0)


def load(tf, cache):
    data, fund = {}, {}
    for c in DEF["coins"]:
        try:
            df = klines(c, tf, cache)
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s: %s", c, tf, type(e).__name__)
            continue
        if df is None or len(df) < 80:
            log.info("%s: not on Binance futures (%s)", c, tf)
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, tf, cache, data[c].index)
    return data, fund


def window(T, a, b=None):
    m = T.start >= pd.Timestamp(a, tz="UTC")
    if b:
        m &= T.start < pd.Timestamp(b, tz="UTC")
    return T[m]


def judge(T, C, P, a, b):
    t = window(T, a, b)
    h1, h2 = _own_halves(t)
    pf = _pf_of(t.net.values) if len(t) else float("nan")
    rng = np.random.default_rng(0)
    boot = np.array([_pf_of(rng.choice(t.net.values, size=len(t), replace=True)) for _ in range(10_000)]) if len(t) else np.array([np.nan])
    c = C[(C.index >= pd.Timestamp(a, tz="UTC")) & (C.index < pd.Timestamp(b, tz="UTC"))] if b else C[C.index >= pd.Timestamp(a, tz="UTC")]
    r = c.resample("1D").last().dropna().pct_change().dropna()
    return dict(trades=len(t), coins=t.coin.nunique() if len(t) else 0, win_rate=(t.net > 0).mean() if len(t) else np.nan,
                pf=pf, half1_pf=h1, half2_pf=h2, boot_p5=float(np.percentile(boot, 5)),
                return_pct=(c.iloc[-1] / c.iloc[0] - 1) * 100 if len(c) else np.nan,
                max_dd_pct=float((c / c.cummax() - 1).min() * 100) if len(c) else np.nan,
                sharpe=float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0)


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    d1, f1 = load("1d", cache)
    d4, f4 = load("4h", cache)
    Q = {**P, "risk_pct": 1.0, "max_positions": 3}
    res, verdict = {}, {}

    T4, C4, _ = run(LONG, d4, f4, {**Q, "start": R1[0]})
    res["R1 4h breakout, 2020-01 -> 2024-06"] = r1 = judge(T4, C4, P, *R1)
    verdict["R1"] = ("confirmed" if r1["trades"] >= 100 and r1["pf"] >= 1.2 and r1["half1_pf"] > 1 and r1["half2_pf"] > 1
                     and r1["boot_p5"] > 1 else "FAILS")
    T1, C1, _ = run(LONG, d1, f1, {**Q, "start": R2[0]})
    res["R2 1d breakout, 2020-01 -> 2021-03"] = r2 = judge(T1, C1, P, *R2)
    verdict["R2"] = ("inconclusive" if r2["trades"] < 30 else
                     "confirmed" if r2["pf"] >= 1.2 and r2["half1_pf"] > 1 and r2["half2_pf"] > 1 else "FAILS")
    Ts, Cs, _ = run(SHORT, d1, f1, {**Q, "start": R2[0], "max_positions": 1})
    res["R3 1d rally short, 2020-01 -> 2021-03"] = r3 = judge(Ts, Cs, P, *R2)
    verdict["R3"] = "inconclusive" if r3["trades"] < 20 else ("confirmed" if r3["pf"] >= 1.0 else "FAILS")

    # descriptive: R1 by year, vs random uptrend entries, the live book where every rule is untouched,
    # and Binance vs Hyperliquid over the same 4h window
    t4 = window(T4, *R1)
    by_year = t4.groupby(t4.start.dt.year).net.agg(trades="size", pf=lambda x: _pf_of(x.values), net_pct=lambda x: x.sum() / Q["equity0"] * 100)
    s0 = pd.Timestamp(R1[0], tz="UTC")
    fees_P = {**P}
    rr = []
    for c, df in d4.items():
        sub = df[df.index < pd.Timestamp(R1[1], tz="UTC")]
        fd = float(f4[c].mean()) if len(f4[c]) else 0.0     # per-bar funding -> per-day for net_r
        rr += rule_trades(sub, fd * 6, fees_P, s0)
    rnd = random_pf({c: (df[df.index < pd.Timestamp(R1[1], tz="UTC")], float(f4[c].mean()) * 6) for c, df in d4.items()},
                    len(rr), fees_P, s0)
    rand = {"signals_uncapped": len(rr), "rule_pf": _pf_of(np.array(rr)), "random_median": float(np.median(rnd)),
            "pctile": float((rnd < _pf_of(np.array(rr))).mean() * 100)}
    # live book 2020-01 -> 2021-03: 1d + 4h longs on 3 slots + the 1d rally short on its own slot
    data, fund, coin_of, mark = {}, {}, {}, {}
    for tf, dd_, ff in (("1d", d1, f1), ("4h", d4, f4)):
        for c in dd_:
            k = f"{c}|{tf}"
            data[k], fund[k], coin_of[k] = dd_[c].assign(is_1d=tf == "1d"), ff[c], c
            if tf == "4h":
                mark[c] = dd_[c].c
    Vb = {**VARIANTS["breakout_both"], "short_level": "rally", "rally_stop_atr": 0.5, "short_filters": ["stream_1d"]}
    Tb, Cb, ddb = run(Vb, data, fund, {**Q, "start": R2[0], "coin_of": coin_of, "mark": mark, "side_max": {"L": 3, "S": 1}})
    book = judge(Tb, Cb, P, *R2)
    book["shorts"] = int((window(Tb, *R2).side == "S").sum())
    # same 4h rule on Hyperliquid vs Binance, 2024-07-05 -> now
    cdata, cfund, extra, _ = _combined_book(P)
    hl4 = {k.split("|")[0]: v for k, v in cdata.items() if k.endswith("|4h")}
    hlf = {k.split("|")[0]: v for k, v in cfund.items() if k.endswith("|4h")}
    Th, Ch, _ = run(LONG, hl4, hlf, {**Q, "start": OVERLAP})
    Tbn, Cbn, _ = run(LONG, d4, f4, {**Q, "start": OVERLAP})
    src = {"Hyperliquid 4h": judge(Th, Ch, P, OVERLAP, None), "Binance 4h": judge(Tbn, Cbn, P, OVERLAP, None)}

    R = pd.DataFrame(res).T
    rep = ["# Out-of-sample retest of the live rules on Binance (pre-registered: docs/research/binance-retest.md)\n",
           f"Coins on Binance futures: 1d {len(d1)}, 4h {len(d4)} of 15. Own books, 1.0% risk, Binance funding charged.\n",
           R.astype(float).round(2).to_markdown() + "\n",
           "## Verdicts (fixed before running)\n\n" + "\n".join(f"- **{k}: {v}**" for k, v in verdict.items()) + "\n",
           "## Descriptive (no bar)\n",
           "**R1 by entry year:**\n\n" + by_year.round(2).to_markdown() + "\n",
           f"**R1 vs random uptrend entries** (every signal, one position per coin): {rand['signals_uncapped']} signals, PF "
           f"{rand['rule_pf']:.2f} vs random median {rand['random_median']:.2f}, {rand['pctile']:.0f}th percentile.\n",
           "**Live book, 2020-01 -> 2021-03** (1d + 4h longs on 3 slots + rally-short slot):\n\n"
           + pd.DataFrame({"live book": book}).T.astype(float).round(2).to_markdown() + "\n",
           f"**Same 4h rule, same window ({OVERLAP} -> now), two sources:**\n\n" + pd.DataFrame(src).T.astype(float).round(2).to_markdown() + "\n"]
    (out / "binance_retest.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return R, verdict


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
