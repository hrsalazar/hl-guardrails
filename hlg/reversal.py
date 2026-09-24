"""Early entries into 1d trends: the 1h -> 4h reversal cascade and the failed breakdown.

    python -m hlg.reversal            # research only; writes backtest_out/reversal_study.md

The long strategy enters on a 1d close above the 20-day high. Two ways of getting in earlier, at a
better price, on the same trends -- tested before anything changes in the alerts. Rules fixed
before the first run:

  Idea 1, cascade  (context: last completed 1d bar has EMA20 > EMA50 and no 1d breakout yet)
    1h trigger     the last two confirmed 1h swing highs are falling (a lower high), a higher 1h
                   swing low formed after that lower high, and a 1h close crosses above the lower
                   high -- a mechanical "change of character"
    4h confirm     within 48h, a 4h close crosses above the last confirmed 4h swing high; a 1h low
                   below the higher low first cancels the setup
    trade          entry at the next 4h open; stop = the 1h higher low
  Idea 2, failed breakdown ("spring") on 4h  (context: 1d EMA20 > EMA50)
    a 4h close below the prior 20-bar 4h low, then within the next two 4h bars a close back above
    that level; entry at the next 4h open; stop = the lowest low from the breakdown to the reclaim
  Baseline: the live 1d breakout (close > prior 20-day high with EMA20 > EMA50, stop 2x ATR),
    recomputed on the same data.

Every trade, baseline included, runs through one 1h-bar simulator: trailing stop 3x the 1d ATR at
entry below the highest high, 21-day time stop, a stop gap fills at the open, maker entry / taker
exit fees, one position per coin, a setup skipped if its stop is wider than 2 daily ATRs (then it is
not a better entry). Results are in R net of fees.

Pass, per idea: (1) PF above the 95th percentile of 200 random-entry draws -- same size, same
context, stop distances resampled from the idea's own; (2) PF and average R at least the baseline's;
(3) PF > 1.2 in both halves. For notifications, idea 1 alone: a 1d breakout within 10 days at least
1.5x as likely after a confirmed cascade as from a random bar in the same context.

Data: Binance USDT-M perpetuals, 1h, with 4h and 1d built from it -- one source for every number,
because Hyperliquid serves only ~5000 1h candles (~7 months). Research only; nothing live reads it.
Swing points are 3-bar fractals, known only three bars after they form: nothing looks ahead.
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .backtest import DEF
from .common import log, setup_logging
from .scanner import atr, ema

FAPI = "https://fapi.binance.com/fapi/v1/klines"
H = pd.Timedelta(hours=1)
K = 3                      # fractal half-width
CONFIRM_H = 48             # 4h confirmation window after the 1h trigger
TRAIL_ATR, STOP_ATR, MAX_DAYS, MAX_RISK_ATR = 3.0, 2.0, 21, 2.0
MAKER, TAKER = 0.00015, 0.00045
LIFT_DAYS = 10


# ------------------------------------------------------------------------------------------ data
def klines(coin, cache_dir="miner_cache", start="2023-03-01", get=requests.get):
    """1h OHLCV for COINUSDT perp, cached and topped up incrementally; the live bar is dropped."""
    p = Path(cache_dir) / f"binance_fut_{coin}_1h.csv"
    old = pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else None
    since = int((old.index[-1] + H).timestamp() * 1000) if old is not None and len(old) else \
        int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    rows, now_ms = [], int(time.time() * 1000)
    while since < now_ms - 3_600_000:
        r = get(FAPI, params={"symbol": f"{coin}USDT", "interval": "1h", "startTime": since, "limit": 1000}, timeout=20)
        r.raise_for_status()
        k = r.json()
        if not k:
            break
        rows += k
        since = k[-1][0] + 3_600_000
        time.sleep(0.15)
    new = pd.DataFrame([x[:6] for x in rows], columns=["t", "o", "h", "l", "c", "v"]).astype(float)
    if len(new):
        new.index = pd.to_datetime(new.t.astype("int64"), unit="ms", utc=True)
        new = new.drop(columns="t")
    df = pd.concat([old, new]) if old is not None else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df.index + H <= pd.Timestamp.now(tz="UTC")]   # completed bars only
    p.parent.mkdir(exist_ok=True)
    df.to_csv(p)
    return df


def resample(h1, rule, n):
    """Complete bars only (all n hours present), labelled at the bar's start, UTC-aligned."""
    g = h1.resample(rule)
    out = g.agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"})
    return out[g.size() == n].dropna()


def swings(df, k=K):
    """Per bar, the swing state known at that bar's close: last and previous confirmed fractal
    highs/lows and when the last ones formed. A fractal at i is only known at i + k."""
    h, l = df.h.values, df.l.values
    n = len(df)
    cols = {c: np.full(n, np.nan) for c in ("sh1", "sh2", "sl1", "sl2", "sh1_i", "sl1_i")}
    sh1 = sh2 = sl1 = sl2 = np.nan
    sh1_i = sl1_i = np.nan
    for j in range(n):
        i = j - k
        if i >= k:
            if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:j + 1].max():
                sh2, sh1, sh1_i = sh1, h[i], i
            if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:j + 1].min():
                sl2, sl1, sl1_i = sl1, l[i], i
        for c, v in (("sh1", sh1), ("sh2", sh2), ("sl1", sl1), ("sl2", sl2), ("sh1_i", sh1_i), ("sl1_i", sl1_i)):
            cols[c][j] = v
    return pd.DataFrame(cols, index=df.index)


def daily_context(d1):
    d = d1.copy()
    d["ema20"], d["ema50"], d["atr"] = ema(d.c, 20), ema(d.c, 50), atr(d, 14)
    d["hi20"] = d.h.rolling(20).max().shift(1)
    d["up"] = d.ema20 > d.ema50
    d["broke"] = d.c > d.hi20
    return d


def ctx_at(d, t_close):
    """The last 1d bar completed by time t_close (a bar starting D completes at D + 1 day)."""
    D = t_close.floor("D") - pd.Timedelta(days=1)
    return d.loc[D] if D in d.index else None


# ------------------------------------------------------------------------------------- simulator
def simulate(h1, i0, entry, stop, a1d, max_days=MAX_DAYS, trail=TRAIL_ATR):
    """One long from the open of 1h bar i0. Stop checked on each bar's low with the stop as it stood
    before the bar (a gap through it fills at the open), then trailed trail x a1d below the highest
    high so far; time stop at the first close >= max_days after entry. -> (R net of fees, exit_t)."""
    risk = entry - stop
    if risk <= 0:
        return None
    n = min(len(h1) - i0, max_days * 24 + 1)
    if n <= 0:
        return None
    o, h, l, c = (h1[x].values[i0:i0 + n] for x in ("o", "h", "l", "c"))
    prior_hi = np.maximum.accumulate(np.concatenate([[entry], h[:-1]]))
    stop_before = np.maximum(stop, prior_hi - trail * a1d)
    hit = np.nonzero(l <= stop_before)[0]
    last = n - 1
    if hit.size and hit[0] <= last:
        j = hit[0]
        px = min(o[j], stop_before[j])
    else:
        j, px = last, c[last]
    r = (px - entry) / risk - (MAKER * entry + TAKER * px) / risk
    return r, h1.index[i0 + j]


# ----------------------------------------------------------------------------------- the setups
def baseline(h1, d):
    """Live 1d breakout on the same data: entries at the next day's 00:00 open."""
    out = []
    for D, k in d[d.up & d.broke & d.atr.notna()].iterrows():
        t = D + pd.Timedelta(days=1)
        out.append(dict(t=t, stop=k.c - STOP_ATR * k.atr, a=k.atr, kind="base"))
    return out


def cascade(h1, h4, d, s1, s4):
    """Idea 1. Returns entries (t = entry bar start) with stop and the 1d ATR."""
    out, armed = [], None
    c1, l1 = h1.c.values, h1.l.values
    sh1, sh2, sl1, sl2, sh1_i, sl1_i = (s1[x].values for x in ("sh1", "sh2", "sl1", "sl2", "sh1_i", "sl1_i"))
    s4_by_close = {t + 4 * H: row for t, row in s4.iterrows()}
    c4 = dict(zip(h4.index + 4 * H, h4.c.values))
    prev4 = dict(zip(h4.index + 4 * H, h4.c.shift(1).values))
    for j in range(1, len(h1)):
        t_close = h1.index[j] + H
        if armed is not None:
            if l1[j] < armed["stop"]:
                armed = None                      # lost the higher low: setup void
            elif t_close > armed["expires"]:
                armed = None
            elif t_close in c4:
                row = s4_by_close[t_close]
                k = ctx_at(d, t_close)
                if (k is not None and k.up and not k.broke and not np.isnan(row.sh1)
                        and c4[t_close] > row.sh1 and prev4[t_close] <= row.sh1):
                    out.append(dict(t=t_close, stop=armed["stop"], a=k.atr, kind="cascade"))
                    armed = None
                    continue
        if np.isnan(sh2[j]) or np.isnan(sl2[j]):
            continue
        if (sh1[j] < sh2[j] and sl1[j] > sl2[j] and sl1_i[j] > sh1_i[j]
                and c1[j] > sh1[j] and c1[j - 1] <= sh1[j]):
            k = ctx_at(d, t_close)
            if k is not None and k.up and not k.broke:
                armed = {"stop": sl1[j], "expires": t_close + CONFIRM_H * H}
    return out


def spring(h4, d):
    """Idea 2 on 4h bars."""
    out = []
    lo20 = h4.l.rolling(20).min().shift(1).values
    c, l = h4.c.values, h4.l.values
    for b in range(21, len(h4) - 3):
        if not c[b] < lo20[b]:
            continue
        for r in (b + 1, b + 2):
            if c[r] > lo20[b]:
                t_close = h4.index[r] + 4 * H
                k = ctx_at(d, t_close)
                if k is not None and k.up:
                    out.append(dict(t=t_close, stop=l[b:r + 1].min(), a=k.atr, kind="spring"))
                break
    return out


def trades(h1, entries):
    """Simulate a coin's entries in time order, one position at a time."""
    pos_i = {t: i for i, t in enumerate(h1.index)}
    res, busy_until = [], None
    for e in sorted(entries, key=lambda e: e["t"]):
        if busy_until is not None and e["t"] <= busy_until:
            continue
        i0 = pos_i.get(e["t"])
        if i0 is None or np.isnan(e["a"]):
            continue
        entry = h1.o.values[i0]
        if entry - e["stop"] <= 0 or entry - e["stop"] > MAX_RISK_ATR * e["a"]:
            continue
        sim = simulate(h1, i0, entry, e["stop"], e["a"])
        if sim is None:
            continue
        res.append(dict(t=e["t"], entry=entry, stop=e["stop"], risk_atr=(entry - e["stop"]) / e["a"],
                        r=sim[0], exit_t=sim[1], kind=e["kind"]))
        busy_until = sim[1]
    return res


# ------------------------------------------------------------------------------------ the study
def pf(rs):
    rs = np.asarray(rs)
    g, l = rs[rs > 0].sum(), -rs[rs < 0].sum()
    return g / l if l > 0 else float("inf")


def summarize(T, mid):
    if T.empty:
        return dict(trades=0)
    a, b = T[T.t < mid], T[T.t >= mid]
    return dict(trades=len(T), win=(T.r > 0).mean(), pf=pf(T.r), avg_r=T.r.mean(), total_r=T.r.sum(),
                pf_h1=pf(a.r) if len(a) else np.nan, pf_h2=pf(b.r) if len(b) else np.nan,
                med_risk_atr=T.risk_atr.median())


def random_control(books, T, context, n_draws=200, seed=0):
    """PFs of random entries: same count as T, drawn from 4h bar opens where `context` held, stops
    resampled from T's own stop distances (in 1d ATRs)."""
    rng = np.random.default_rng(seed)
    pool = [(coin, t) for coin, b in books.items() for t in b["ctx_" + context]]
    dists = T.risk_atr.values
    out = []
    for _ in range(n_draws):
        rs = []
        for idx in rng.choice(len(pool), size=len(T), replace=False):
            coin, t = pool[idx]
            b = books[coin]
            i0 = b["pos"].get(t)
            k = ctx_at(b["d"], t)
            if i0 is None or k is None or np.isnan(k.atr):
                continue
            entry = b["h1"].o.values[i0]
            sim = simulate(b["h1"], i0, entry, entry - rng.choice(dists) * k.atr, k.atr)
            if sim:
                rs.append(sim[0])
        out.append(pf(rs))
    return np.array(out)


def lift(books, T, context):
    """P(1d breakout within LIFT_DAYS | an idea's entry) vs the same from any 4h bar in context."""
    def soon(coin, t):
        br = books[coin]["breakouts"]
        return bool(((br > t) & (br <= t + pd.Timedelta(days=LIFT_DAYS))).any())
    hit = np.mean([soon(c, t) for c, t in zip(T.coin, T.t)]) if len(T) else np.nan
    base = np.mean([soon(c, t) for c, b in books.items() for t in b["ctx_" + context]])
    return hit, base


def study(P=None, coins=None):
    P = P or DEF
    start = pd.Timestamp(P["start"], tz="UTC")
    out_dir = Path(P["out_dir"])
    out_dir.mkdir(exist_ok=True)
    books, rows = {}, []
    for coin in coins or DEF["coins"]:
        try:
            h1 = klines(coin, P["cache_dir"])
        except Exception as e:  # noqa: BLE001
            log.warning("%s: no Binance data (%s), skipped", coin, e)
            continue
        h4, d1 = resample(h1, "4h", 4), resample(h1, "1D", 24)
        if len(d1) < 80:
            continue
        d = daily_context(d1)
        s1, s4 = swings(h1), swings(h4)
        ents = baseline(h1, d) + cascade(h1, h4, d, s1, s4) + spring(h4, d)
        ents = [e for e in ents if e["t"] >= start]
        T = []
        for kind in ("base", "cascade", "spring"):
            T += [dict(x, coin=coin) for x in trades(h1, [e for e in ents if e["kind"] == kind])]
        rows += T
        # context pools for the random control and the lift base rate: 4h bar opens after start
        opens = [t + 4 * H for t in h4.index if t + 4 * H >= start]
        cks = {t: ctx_at(d, t) for t in opens}
        books[coin] = dict(h1=h1, d=d, pos={t: i for i, t in enumerate(h1.index)},
                           ctx_cascade=[t for t, k in cks.items() if k is not None and k.up and not k.broke],
                           ctx_spring=[t for t, k in cks.items() if k is not None and k.up],
                           breakouts=pd.DatetimeIndex([D + pd.Timedelta(days=1) for D in d.index[d.up & d.broke]]))
        log.info("%s: %d base, %d cascade, %d spring trades", coin, *(sum(r["kind"] == k for r in T)
                 for k in ("base", "cascade", "spring")))
    T = pd.DataFrame(rows)
    T.to_csv(out_dir / "reversal_trades.csv", index=False)
    mid = T.t.sort_values().iloc[len(T) // 2]
    S = {k: summarize(T[T.kind == k], mid) for k in ("base", "cascade", "spring")}
    b = S["base"]
    rep = ["# Early entries into 1d trends (Binance USDT-M perps, 1h, "
           f"{P['start']} -> now, {len(books)} coins)\n",
           pd.DataFrame(S).T.astype(float).round(2).to_markdown() + "\n"]
    verdicts = {}
    for kind in ("cascade", "spring"):
        t = T[T.kind == kind]
        s = S[kind]
        if not len(t):
            verdicts[kind] = "no trades"
            continue
        rnd = random_control(books, t, kind)
        pct = (rnd < s["pf"]).mean() * 100
        ok = [pct > 95, s["pf"] >= b["pf"] and s["avg_r"] >= b["avg_r"], s["pf_h1"] > 1.2 and s["pf_h2"] > 1.2]
        hit, base = lift(books, t, kind)
        verdicts[kind] = dict(random_pctile=round(pct, 1), random_pf_median=round(float(np.median(rnd)), 2),
                              beats_random=ok[0], matches_baseline=ok[1], stable=ok[2],
                              verdict="PASS" if all(ok) else "fail",
                              breakout_within_10d=round(hit, 3), base_rate=round(base, 3),
                              lift=round(hit / base, 2) if base else np.nan)
    rep.append("## Verdicts (rules fixed before running)\n\n" + pd.DataFrame(verdicts).T.to_markdown() + "\n")
    if "cascade" in verdicts and isinstance(verdicts["cascade"], dict):
        v = verdicts["cascade"]
        rep.append(f"Notification test, cascade -> 1d breakout within {LIFT_DAYS}d: {v['breakout_within_10d']:.1%} vs "
                   f"base rate {v['base_rate']:.1%}, lift {v['lift']} (needs >= 1.5): "
                   f"**{'PASS' if v['lift'] >= 1.5 else 'fail'}**\n")
    (out_dir / "reversal_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return T, S, verdicts


def main():
    setup_logging()
    study()


if __name__ == "__main__":
    main()
