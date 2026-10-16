"""Capitulation long: buy the flush after a liquidation cascade.

    python -m hlg.capitulation        # research only; writes backtest_out/capitulation_study.md

Pre-registered in docs/research/capitulation-study.md (2026-10-16): signal, trade, sets and the
adoption bar are fixed there. Hyperliquid bars throughout (so the rule could run live as tested);
Bybit's free daily open-interest history is read for one descriptive split. Research only.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .backtest import (DEF, VARIANTS, _combined_book, _combined_run, _own_halves, _pf_of, bars, by_day, candidate_pool,
                       eligibility, features, funding, regime_states, run, signal)
from .common import log, setup_logging

RULE = VARIANTS["capitulation"]
TFS = ("1d", "4h")
MIN_TRADES, MIN_PF, MIN_HALF, MIN_C_TRADES, MIN_C_PF, DD_SLACK, N_RANDOM = 50, 1.3, 1.1, 30, 1.2, 2.0, 200
BYBIT_OI = "https://api.bybit.com/v5/market/open-interest"


# --------------------------------------------------------------------- per-signal simulation (bar 4)
def sim(df, i, stop, tgt, fees):
    """Long from the open of bar i: stop checked first on each bar (a gap fills at the open), then the
    target, then the 3-day time stop at the close. -> R after fees, or None if still open."""
    o, h, l, c = (df[x].values for x in "ohlc")
    entry = o[i]
    risk = entry - stop
    if not risk > 0:
        return None
    t0 = df.index[i]
    for j in range(i, len(df)):
        if l[j] <= stop:
            px = min(o[j], stop)
        elif h[j] >= tgt:
            px = max(o[j], tgt)
        elif df.index[j] - t0 >= pd.Timedelta(days=RULE["max_days"]):
            px = c[j]
        else:
            continue
        return (px - entry) / risk - fees * entry / risk
    return None


def rule_rs(df, fees, start):
    out, busy_until = [], None
    for i in range(1, len(df)):
        if df.index[i] < start or (busy_until is not None and df.index[i] <= busy_until):
            continue
        s = signal(df.iloc[i - 1], RULE)
        if not s:
            continue
        r = sim(df, i, s[1], s[2], fees)
        if r is None:
            continue
        k = df.iloc[i - 1]
        out.append((r, (k.c - s[1]) / k.atr_prev))
        busy_until = df.index[i] + pd.Timedelta(days=RULE["max_days"])
    return out


def random_pf(data, n, dists, fees, start, n_draws=N_RANDOM, seed=0):
    """n entries after random down bars (close < previous close), stop distance resampled from the
    signals' own (in ATR, below the bar's close), the same 1.5R target and time stop."""
    rng = np.random.default_rng(seed)
    pool = [(k, i) for k, df in data.items() for i in range(21, len(df) - 1)
            if df.index[i] >= start and df.c.iloc[i - 1] < df.c.iloc[i - 2] and df.atr_prev.iloc[i - 1] > 0]
    out = []
    for _ in range(n_draws):
        rs = []
        for idx in rng.choice(len(pool), size=min(n, len(pool)), replace=False):
            k, i = pool[idx]
            df = data[k]
            prev = df.iloc[i - 1]
            stop = prev.c - rng.choice(dists) * prev.atr_prev
            r = sim(df, i, stop, prev.c + RULE["cap_target_r"] * (prev.c - stop), fees)
            if r is not None:
                rs.append(r)
        out.append(_pf_of(np.array(rs)))
    return np.array(out)


# ---------------------------------------------------------------------------- Bybit open interest
def bybit_oi(coin, cache, start_ms, get=requests.get):
    """Daily open interest (contracts) from Bybit's public API, cached for a day. None if not listed."""
    p = cache / f"bybit_oi_{coin}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, end = [], int(time.time() * 1000)
        while end > start_ms:
            r = get(BYBIT_OI, params={"category": "linear", "symbol": f"{coin}USDT", "intervalTime": "1d",
                                      "startTime": start_ms, "endTime": end, "limit": 200}, timeout=20)
            js = r.json()
            rows = (js.get("result") or {}).get("list") or []
            if js.get("retCode") != 0 or not rows:
                break
            d += rows
            end = min(int(x["timestamp"]) for x in rows) - 1
            if len(rows) < 200:
                break
            time.sleep(0.2)
        p.write_text(json.dumps(d))
    if not d:
        return None
    s = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="ms", tz="UTC"): float(x["openInterest"]) for x in d}).sort_index()
    return s[~s.index.duplicated()]


# ------------------------------------------------------------------------------------------ study
def _load(coins, cache, tf, start):
    data, fund = {}, {}
    for c in coins:
        try:
            df = bars(c, cache, tf)
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s: bars failed (%s)", c, tf, type(e).__name__)
            continue
        if df is None or len(df) < 60:
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, int(start.timestamp() * 1000), cache, tf)
    return data, fund


def study(P=None):
    P = {**(P or DEF)}
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(P["start"], tz="UTC")
    fees = P["maker_fee"] + P["taker_fee"]
    S = list(DEF["coins"])
    cdata, cfund, extra, mid = _combined_book(P)
    Qc = {**P, "risk_pct": 1.0, **extra}
    _, crypto = _combined_run(cdata, cfund, Qc, mid)
    _, Ccr, _ = run(VARIANTS["breakout_long"], cdata, cfund, Qc)
    btc = regime_states(bars("BTC", cache, "1d"), None, None).trend

    # set C pool: main-dex perps outside the 15, point-in-time liquid universe (as rally_confirm)
    pool, delisted = candidate_pool()
    c1d = {}
    for c in pool:
        if c in S:
            continue
        try:
            df = bars(c, cache, "1d")
        except Exception:  # noqa: BLE001
            continue
        if df is not None and len(df) >= 60:
            c1d[c] = features(df, 1)
    mask, med = eligibility(c1d, dict(top_n=30, min_vol_usd=10e6, min_age_days=60))
    ever = sorted(mask.columns[mask[mask.index >= start].any()])
    elig = by_day(mask.loc[mask.index >= start - pd.Timedelta(days=1)])
    log.info("set C: %d coins ever eligible", len(ever), extra={"safe": True})

    rows, rules, extras = {}, {}, {}
    for tf in TFS:
        data, fund = _load(S, cache, tf, start)
        Q = {**P, "risk_pct": 1.0, "max_positions": len(data)}     # one position per coin, no shared cap
        T, C, dd = run(RULE, data, fund, Q)
        T.to_csv(out / f"capitulation_{tf}_trades.csv", index=False)
        h1, h2 = _own_halves(T)
        pf = _pf_of(T.net.values) if len(T) else float("nan")
        # bar 4
        sig = [x for k in data for x in rule_rs(data[k], fees, start)]
        rule_pf = _pf_of(np.array([r for r, _ in sig])) if sig else float("nan")
        rnd = random_pf(data, len(sig), np.array([d for _, d in sig]), fees, start) if sig else np.array([np.nan])
        pct = float((rnd < rule_pf).mean() * 100)
        # bar 5: unseen coins
        if tf == "1d":
            cd, cf = {c: c1d[c] for c in ever}, {c: funding(c, int(start.timestamp() * 1000), cache, "1d") for c in ever}
        else:
            cd, cf = _load(ever, cache, "4h", start)
        Tc, _, _ = run(RULE, cd, cf, {**P, "risk_pct": 1.0, "max_positions": len(cd), "delisted": delisted,
                                      "elig_by_day": elig, "liq_med": med})
        pf_c = _pf_of(Tc.net.values) if len(Tc) else float("nan")
        # bar 6: the live book + this rule on its own slot
        keys = {f"{k}|{tf}|cap": k for k in data}
        md = {**cdata, **{s: data[k] for s, k in keys.items()}}
        mf = {**cfund, **{s: fund[k] for s, k in keys.items()}}
        group_of = {k: "crypto" for k in cdata} | {s: "cap" for s in keys}
        Qm = {**Qc, "coin_of": {**extra["coin_of"], **keys}, "V_of": {s: RULE for s in keys},
              "group_max": {"crypto": 3, "cap": 1}, "group_of": group_of}
        Tm, book = _combined_run(md, mf, Qm, mid)
        book["cap_trades"] = int(Tm.coin.map(group_of.get).eq("cap").sum()) if len(Tm) else 0
        # descriptive
        _, Cs, _ = run(RULE, data, fund, {**Q, "max_positions": 1})
        rc = Ccr.resample("1D").last().dropna().pct_change().dropna()
        rs = Cs.resample("1D").last().dropna().pct_change().dropna().reindex(rc.index).fillna(0.0)
        down = T.start.dt.floor("D").map(lambda d: btc.get(d - pd.Timedelta(days=1)) == "btc_down") if len(T) else pd.Series(dtype=bool)
        extras[tf] = {"exits": T.why.value_counts().to_dict() if len(T) else {}, "avg_days": T.days.mean() if len(T) else np.nan,
                      "corr": float(rc.corr(rs)), "btc_down": (int(down.sum()), _pf_of(T.net[down.values].values) if down.any() else np.nan),
                      "btc_up": (int((~down).sum()), _pf_of(T.net[~down.values].values) if (~down).any() else np.nan)}
        if tf == "1d" and len(T):
            oi_rows = []
            for c in S:
                oi = bybit_oi(c, cache, int(start.timestamp() * 1000))
                if oi is None:
                    continue
                for _, t in T[T.coin == c].iterrows():
                    D = t.start.floor("D") - pd.Timedelta(days=1)          # the capitulation bar's day
                    a, b = oi.get(D), oi.get(D + pd.Timedelta(days=1))
                    if a and b:
                        oi_rows.append((b / a - 1, t.net))
            o = pd.DataFrame(oi_rows, columns=["oi_chg", "net"])
            flush = o[o.oi_chg <= -0.05]
            rest = o[o.oi_chg > -0.05]
            extras[tf]["oi"] = {"matched": len(o), "flush_n": len(flush), "flush_pf": _pf_of(flush.net.values) if len(flush) else np.nan,
                                "rest_n": len(rest), "rest_pf": _pf_of(rest.net.values) if len(rest) else np.nan}
        rows[tf] = dict(trades=len(T), win_rate=(T.net > 0).mean() if len(T) else np.nan, pf=pf, half1_pf=h1, half2_pf=h2,
                        avg_r=(T.net_pct / 1.0).mean() if len(T) else np.nan, max_dd_pct=dd * 100,
                        signal_pf_uncapped=rule_pf, random_pf_median=float(np.median(rnd)), random_pctile=pct,
                        unseen_coins=len(cd), unseen_trades=len(Tc), unseen_pf=pf_c,
                        book_sharpe=book["sharpe"], book_dd=book["max_dd_pct"], book_pf=book["pf"], book_cap_trades=book["cap_trades"])
        rules[tf] = {
            f"1. >= {MIN_TRADES} trades in S ({len(T)})": len(T) >= MIN_TRADES,
            f"2. PF >= {MIN_PF} in S ({pf:.2f})": bool(pf >= MIN_PF),
            f"3. PF >= {MIN_HALF} in both halves ({h1:.2f} / {h2:.2f})": bool(h1 >= MIN_HALF and h2 >= MIN_HALF),
            f"4. above the 95th percentile of random down-bar entries ({pct:.0f}th)": pct > 95,
            f"5. unseen coins: >= {MIN_C_TRADES} trades and PF >= {MIN_C_PF} ({len(Tc)}, {pf_c:.2f})": bool(len(Tc) >= MIN_C_TRADES and pf_c >= MIN_C_PF),
            f"6. book Sharpe {book['sharpe']:.2f} > crypto {crypto['sharpe']:.2f}, DD {book['max_dd_pct']:.1f}% within {DD_SLACK:g}pp of "
            f"{crypto['max_dd_pct']:.1f}%": bool(book["sharpe"] > crypto["sharpe"] and book["max_dd_pct"] >= crypto["max_dd_pct"] - DD_SLACK),
        }
        log.info("%s done: %d trades", tf, len(T), extra={"safe": True})

    R = pd.DataFrame(rows)
    rep = ["# Capitulation long (pre-registered: docs/research/capitulation-study.md)\n",
           f"Set S: the 15 backtest coins, Hyperliquid bars, {P['start']} -> now, one position per coin, 1.0% risk. "
           f"Set C: {len(ever)} other perps, point-in-time top 30. Crypto live book for bar 6: Sharpe {crypto['sharpe']:.2f}, "
           f"max DD {crypto['max_dd_pct']:.1f}%.\n",
           R.astype(float).round(2).to_markdown() + "\n", "## Adoption bar (fixed before running)\n"]
    for tf, v in rules.items():
        rep.append(f"### {tf}\n\n" + "\n".join(f"- {k}: **{'pass' if x else 'FAIL'}**" for k, x in v.items())
                   + f"\n\n**{tf}: {'PASSES' if all(v.values()) else 'does not pass'}**\n")
    rep.append("## Descriptive (no bar)\n")
    for tf, e in extras.items():
        rep.append(f"- **{tf}:** exits {e['exits']}, average hold {e['avg_days']:.1f} days; correlation with the breakout book {e['corr']:.2f}; "
                   f"BTC below its 200-day EMA: {e['btc_down'][0]} trades, PF {e['btc_down'][1]:.2f}; above: {e['btc_up'][0]}, PF {e['btc_up'][1]:.2f}")
        if "oi" in e:
            o = e["oi"]
            rep.append(f"  - Bybit OI on the capitulation day (matched {o['matched']}): OI fell >= 5%: {o['flush_n']} trades, PF "
                       f"{o['flush_pf']:.2f}; otherwise: {o['rest_n']}, PF {o['rest_pf']:.2f}")
    (out / "capitulation_study.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("\n".join(rep))
    return R, rules


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    study()


if __name__ == "__main__":
    main()
