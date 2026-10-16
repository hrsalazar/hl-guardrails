"""TradFi sleeve study: the live breakout rule on indices, commodities, FX and US stocks.

    python -m hlg.tradfi              # research only; writes backtest_out/tradfi_study.md

Pre-registered in docs/research/tradfi-study.md (2026-10-16): sleeves, rule, data and the adoption
bar are fixed there. History comes from Yahoo Finance's daily bars for each underlying (2016 -> now),
because Hyperliquid's xyz perps only go back to late 2025; the overlapping year is used to check that
the two give the same signals, and the xyz perps' own funding is charged. Research only: nothing
live reads this module.
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .backtest import (DEF, HOLDOUT, VARIANTS, _combined_book, _combined_run, _own_halves, _pf_of, features, post,
                       run, signal, stats)
from .common import log, setup_logging

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = {"User-Agent": "Mozilla/5.0 (hl-guardrails research)"}
FETCH_FROM = "2015-03-01"     # warm-up for EMA50 / ATR before the 2016 start
START = "2016-01-01"
RULE = VARIANTS["breakout_long"]

SLEEVES = {  # Yahoo symbol -> xyz perp; fixed in the pre-registration
    "A": {"^GSPC": "SP500", "^NDX": "XYZ100", "GC=F": "GOLD", "SI=F": "SILVER", "CL=F": "CL", "BZ=F": "BRENTOIL",
          "NG=F": "NATGAS", "HG=F": "COPPER", "EURUSD=X": "EUR", "JPY=X": "JPY"},
    "B": {s: None for s in ("AAPL", "GOOGL", "MSFT", "BRK-B", "XOM", "AMZN", "META", "GE", "JNJ", "WFC", "JPM", "PG", "T",
                            "VZ", "WMT", "PFE", "KO", "ORCL", "CVX", "INTC")},
    "C": {s: s for s in ("NVDA", "TSLA", "MU", "GOOGL", "META", "AAPL", "AMZN", "MSFT", "INTC", "AMD", "MSTR", "COIN",
                         "HOOD", "PLTR", "TSM", "MRVL", "DELL", "NBIS", "RKLB", "IREN")},
}
# B names that are also xyz perps (for funding); the rest are charged the sleeve's mean
for _s in ("AAPL", "GOOGL", "MSFT", "AMZN", "META", "INTC"):
    SLEEVES["B"][_s] = _s
DECISIVE = ("A", "B")
MIN_TRADES, MIN_PF, MIN_STABLE, N_RANDOM, DD_SLACK = 50, 1.3, 1.1, 200, 2.0


def _safe(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def yahoo(sym, cache, get=requests.get):
    """Daily OHLCV for a Yahoo symbol, cached for a day. Index: the bar's exchange-local date, as UTC midnight."""
    p = cache / f"yahoo_{_safe(sym)}.csv"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.index = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
        return df
    p1 = int(pd.Timestamp(FETCH_FROM, tz="UTC").timestamp())
    r = get(YAHOO.format(sym=sym), params={"period1": p1, "period2": int(time.time()), "interval": "1d"}, headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    off = res["meta"].get("gmtoffset", 0)
    idx = pd.to_datetime(np.array(res["timestamp"]) + off, unit="s", utc=True).floor("D")
    df = pd.DataFrame({"o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"], "v": q["volume"]}, index=idx).astype(float)
    df = df.dropna(subset=["o", "h", "l", "c"])
    df = df[(df.h >= df.l) & (df.c > 0)]
    df["v"] = df.v.fillna(0.0)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df.index < pd.Timestamp.now(tz="UTC").floor("D")]      # today's bar may be incomplete
    p.parent.mkdir(exist_ok=True)
    df.to_csv(p)
    time.sleep(0.3)
    return df


def xyz_bars(name, cache):
    """Daily candles of an xyz perp from Hyperliquid (its whole short history)."""
    p = cache / f"xyz_1d_{_safe(name)}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d = post({"type": "candleSnapshot", "req": {"coin": f"xyz:{name}", "interval": "1d", "startTime": 0,
                                                     "endTime": int(time.time() * 1000)}}) or []
        p.write_text(json.dumps(d))
    if not d:
        return None
    df = pd.DataFrame(d)
    df.index = pd.to_datetime(df.t, unit="ms", utc=True).dt.floor("D")
    df = df[["o", "h", "l", "c", "v"]].astype(float).sort_index()
    return df[df.index < pd.Timestamp.now(tz="UTC").floor("D")]


def xyz_funding_daily(name, cache):
    """Mean daily funding (fraction of notional, paid by longs when positive) over the perp's history."""
    p = cache / f"xyz_fund_{_safe(name)}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, t = [], 0
        while True:
            b = post({"type": "fundingHistory", "coin": f"xyz:{name}", "startTime": t})
            if not b:
                break
            d += b
            if len(b) < 500:
                break
            t = b[-1]["time"] + 1
            time.sleep(0.15)
        p.write_text(json.dumps(d))
    if not d:
        return None
    r = np.array([float(x["fundingRate"]) for x in d])
    return float(r.mean() * 24)      # hourly rates


# ------------------------------------------------------------------- per-instrument (uncapped) simulation
def sim_trade(df, i, stop0, a_entry=None):
    """One long from the open of bar i with initial stop stop0; the engine's exits on daily bars: stop
    checked on the low (a gap fills at the open), then the 3 ATR trail from the highest high, 21-day
    time stop at the close. -> (R before costs, exit index, entry, risk)."""
    o, h, l, c, a = (df[x].values for x in ("o", "h", "l", "c", "atr"))
    entry = o[i]
    risk = entry - stop0
    if not risk > 0:
        return None
    stop, best, t0 = stop0, entry, df.index[i]
    for j in range(i, len(df)):
        if l[j] <= stop:
            return (min(o[j], stop) - entry) / risk, j, entry, risk
        if df.index[j] - t0 >= pd.Timedelta(days=RULE["max_days"]):
            return (c[j] - entry) / risk, j, entry, risk
        best = max(best, h[j])
        if not np.isnan(a[j]):
            stop = max(stop, best - RULE["trail_atr"] * a[j])
    return None                                     # still open at the end of the data


def net_r(r, entry, risk, days, fund_d, P):
    """R after fees (maker in, taker out) and funding over the days held."""
    fees = (P["maker_fee"] + P["taker_fee"]) * entry / risk
    return r - fees - fund_d * days * entry / risk


def rule_trades(df, fund_d, P, start):
    out, busy = [], -1
    for i in range(1, len(df)):
        if i <= busy or df.index[i] < start:
            continue
        s = signal(df.iloc[i - 1], RULE)
        if not s:
            continue
        t = sim_trade(df, i, s[1])
        if t is None:
            continue
        r, j, entry, risk = t
        out.append(net_r(r, entry, risk, (df.index[j] - df.index[i]).days, fund_d, P))
        busy = j
    return out


def random_pf(books, n, P, start, n_draws=N_RANDOM, seed=0):
    """PF of n random entries on days the instrument was in an uptrend (EMA20 > EMA50 on the prior
    bar), stop 2 ATR below the prior close, same exits and costs."""
    rng = np.random.default_rng(seed)
    pool = [(k, i) for k, (df, _) in books.items() for i in range(51, len(df) - 1)
            if df.index[i] >= start and df.ema20.iloc[i - 1] > df.ema50.iloc[i - 1] and not np.isnan(df.atr.iloc[i - 1])]
    out = []
    for _ in range(n_draws):
        rs = []
        for idx in rng.choice(len(pool), size=min(n, len(pool)), replace=False):
            k, i = pool[idx]
            df, fund_d = books[k]
            prev = df.iloc[i - 1]
            t = sim_trade(df, i, prev.c - RULE["stop_atr"] * prev.atr)
            if t:
                r, j, entry, risk = t
                rs.append(net_r(r, entry, risk, (df.index[j] - df.index[i]).days, fund_d, P))
        out.append(_pf_of(np.array(rs)))
    return np.array(out)


# ----------------------------------------------------------------------------------------- checks
def concordance(ydf, xdf):
    """Share of xyz breakout signals that Yahoo's bars also gave within one Yahoo trading bar."""
    xf, yf = features(xdf, 1), features(ydf, 1)
    xs = [d for d, k in xf.iterrows() if signal(k, RULE)]
    ys = {i for i, (d, k) in enumerate(yf.iterrows()) if d >= xf.index[0] and signal(k, RULE)}
    if not xs:
        return None, 0
    hit = 0
    for d in xs:
        i = int(min(yf.index.searchsorted(d), len(yf) - 1))
        hit += any(j in ys for j in (i - 1, i, i + 1))
    return hit / len(xs), len(xs)


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(START, tz="UTC")
    data, fund_rate, xyz_ok, conc = {}, {}, {}, {}
    for sl, syms in SLEEVES.items():
        for sym, perp in syms.items():
            key = f"{sl}:{sym}"
            try:
                df = yahoo(sym, cache)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: Yahoo failed (%s), skipped", sym, type(e).__name__)
                continue
            if sl == "C" and (len(df) < 2 * 252 or df.index[0] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=730)):
                log.info("%s: under 2 years of history, left out of C", sym)
                continue
            data[key] = features(df, 1)
            fund_rate[key] = None
            if perp:
                try:
                    fund_rate[key] = xyz_funding_daily(perp, cache)
                    xdf = xyz_bars(perp, cache)
                    if xdf is not None and len(xdf) > 60:
                        conc[key] = concordance(df, xdf)
                except Exception as e:  # noqa: BLE001
                    log.warning("xyz:%s: %s", perp, type(e).__name__)
            xyz_ok[key] = perp is not None
    for sl in SLEEVES:     # an instrument with no funding history pays its sleeve's mean
        known = [v for k, v in fund_rate.items() if k.startswith(sl + ":") and v is not None]
        m = float(np.mean(known)) if known else 0.0
        for k in fund_rate:
            if k.startswith(sl + ":") and fund_rate[k] is None:
                fund_rate[k] = m

    Q = {**P, "start": START, "risk_pct": 1.0, "max_positions": 3}
    rows, inst, verdicts, Ts = {}, [], {}, {}
    cdata, cfund, extra, mid = _combined_book(P)
    Qc = {**P, "risk_pct": 1.0, **extra}
    _, crypto = _combined_run(cdata, cfund, Qc, mid)
    Tc_only, Cc, _ = run(RULE, cdata, cfund, Qc)
    crypto_daily = Cc.resample("1D").last().dropna().pct_change().dropna()
    for sl in SLEEVES:
        keys = [k for k in data if k.startswith(sl + ":")]
        if not keys:
            continue
        sd = {k: data[k] for k in keys}
        sf = {k: pd.Series(fund_rate[k], index=data[k].index) for k in keys}
        T, C, dd = run(RULE, sd, sf, Q)
        Ts[sl] = T
        s = stats(T, C, dd, Q)
        h1, h2 = _own_halves(T)
        ho = T[(T.start >= pd.Timestamp(HOLDOUT[0], tz="UTC")) & (T.start < pd.Timestamp(HOLDOUT[1], tz="UTC"))]
        ho_pf = _pf_of(ho.net.values) if len(ho) else float("nan")
        # rule 4: every signal per instrument (uncapped) vs random uptrend entries, in R after costs
        books = {k: (data[k], fund_rate[k]) for k in keys}
        rr = [r for k in keys for r in rule_trades(data[k], fund_rate[k], P, start)]
        rule_pf = _pf_of(np.array(rr))
        rnd = random_pf(books, len(rr), P, start)
        pct = float((rnd < rule_pf).mean() * 100)
        # rule 5: the crypto live book + this sleeve on one slot of its own, 2023-06 -> now
        md = {**cdata, **{f"{k}|1d": data[k] for k in keys}}
        mf = {**cfund, **{f"{k}|1d": sf[k] for k in keys}}
        group_of = {k: "crypto" for k in cdata} | {f"{k}|1d": "tradfi" for k in keys}
        Qm = {**Qc, "coin_of": {**extra["coin_of"], **{f"{k}|1d": k for k in keys}},
              "group_max": {"crypto": 3, "tradfi": 1}, "group_of": group_of}
        Tm, both = _combined_run(md, mf, Qm, mid)
        Ts_alone, Cs, _ = run(RULE, {f"{k}|1d": data[k] for k in keys}, {f"{k}|1d": sf[k] for k in keys},
                              {**P, "risk_pct": 1.0, "max_positions": 1})
        sd_daily = Cs.resample("1D").last().dropna().pct_change().dropna()
        corr = float(crypto_daily.corr(sd_daily.reindex(crypto_daily.index).fillna(0.0)))
        # P&L of the sleeve in months the crypto book opened nothing (in the combined run)
        tm = Tm.assign(month=Tm.start.dt.tz_localize(None).dt.to_period("M"), grp=Tm.coin.map(lambda c: group_of.get(c)))
        cm = set(tm[tm.grp == "crypto"].month)
        months = pd.period_range(pd.Timestamp(P["start"]), pd.Timestamp.now(), freq="M")
        quiet = [m for m in months if m not in cm]
        tq = tm[(tm.grp == "tradfi") & tm.month.isin(quiet)]
        row = dict(instruments=len(keys), trades=len(T), win_rate=(T.net > 0).mean() if len(T) else np.nan,
                   pf=s.get("pf", np.nan), total_return_pct=s.get("total_return_pct", np.nan), cagr_pct=s.get("cagr_pct", np.nan),
                   max_dd_pct=s.get("max_dd_pct", np.nan), sharpe=s.get("sharpe", np.nan), half1_pf=h1, half2_pf=h2,
                   holdout_n=len(ho), holdout_pf=ho_pf, rule_pf_uncapped=rule_pf, random_pf_median=float(np.median(rnd)),
                   random_pctile=pct, funding_pct_yr=np.mean([fund_rate[k] for k in keys]) * 365 * 100,
                   book_sharpe=both["sharpe"], book_dd=both["max_dd_pct"], book_pf=both["pf"],
                   tradfi_trades_in_book=int((tm.grp == "tradfi").sum()), corr_with_crypto=corr,
                   quiet_months=len(quiet), quiet_month_trades=len(tq), quiet_month_net_pct=tq.net.sum() / Qc["equity0"] * 100)
        rows[sl] = row
        verdicts[sl] = {
            f"1. >= {MIN_TRADES} trades": len(T) >= MIN_TRADES,
            f"2. PF >= {MIN_PF}": bool(row["pf"] >= MIN_PF),
            f"3. PF >= {MIN_STABLE} in both halves and the holdout": bool(h1 >= MIN_STABLE and h2 >= MIN_STABLE and ho_pf >= MIN_STABLE),
            "4. above the 95th percentile of random uptrend entries": pct > 95,
            f"5. book Sharpe above crypto alone ({crypto['sharpe']:.2f}), DD within {DD_SLACK:g}pp ({crypto['max_dd_pct']:.1f}%)":
                bool(both["sharpe"] > crypto["sharpe"] and both["max_dd_pct"] >= crypto["max_dd_pct"] - DD_SLACK),
        }
        for k in keys:
            t = T[T.coin == k]
            inst.append(dict(sleeve=sl, symbol=k.split(":", 1)[1], trades=len(t), pf=_pf_of(t.net.values) if len(t) else np.nan,
                             net_pct=t.net.sum() / Q["equity0"] * 100 if len(t) else 0.0,
                             funding_pct_yr=fund_rate[k] * 365 * 100,
                             xyz_agree=(conc.get(k) or (None, 0))[0], xyz_signals=(conc.get(k) or (None, 0))[1]))
    pd.concat([T.assign(sleeve=s) for s, T in Ts.items()]).to_csv(out / "tradfi_trades.csv", index=False)
    R = pd.DataFrame(rows).T
    rep = ["# TradFi sleeve study (pre-registered: docs/research/tradfi-study.md)\n",
           f"Yahoo daily bars {START} -> now; each sleeve its own book (3 slots, 1.0% risk), xyz funding charged. "
           f"Crypto live book for rule 5: {P['start']} -> now, Sharpe {crypto['sharpe']:.2f}, max DD {crypto['max_dd_pct']:.1f}%, "
           f"PF {crypto['pf']:.2f}, {crypto['trades']} trades.\n",
           R.astype(float).round(2).T.to_markdown() + "\n", "## Adoption bar (fixed before running)\n"]
    for sl, v in verdicts.items():
        tag = "decisive" if sl in DECISIVE else "information only (hindsight-selected)"
        ok = all(v.values())
        rep.append(f"### Sleeve {sl} ({tag})\n\n" + "\n".join(f"- {k}: **{'pass' if x else 'FAIL'}**" for k, x in v.items())
                   + f"\n\n**Sleeve {sl}: {'PASSES' if ok else 'does not pass'}**" + ("" if sl in DECISIVE else " (cannot be adopted on its own)") + "\n")
    rep.append("## Per instrument\n\n" + pd.DataFrame(inst).round(2).to_markdown(index=False) + "\n")
    (out / "tradfi_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return R, verdicts


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
