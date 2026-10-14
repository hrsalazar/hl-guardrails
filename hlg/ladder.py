"""Breakout ladder: does a breakout climb from 1h to 4h to 1d?

    python -m hlg.ladder              # research only; writes backtest_out/ladder_study.md

Hypothesis (the owner's): a breakout starts on 1h, carries into 4h, then shows on the daily. That a
1d breakout is preceded by 1h and 4h breakouts is nearly true by construction (price has to get
above the 20-day high hour by hour), so the study asks the forward question instead. Rules fixed
before the first run:

  1h break    a 1h close above the prior 20 1h highs
  4h break    a 4h close above the prior 20 4h highs, the previous 4h close not above its own level
              (the first close above, not every bar of a run)
  ladder      a 4h break with a 1h break closing in the 24h before (inclusive), in context: the last
              completed 1d bar has EMA20 > EMA50 and is no breakout, and the 4h close is still below
              the 20-day high (so the daily breakout has not happened intraday yet)
  trade       entry at the next 4h open, stop 2x the 1d ATR (the live stop), then the shared 1h
              simulator of hlg.reversal: 3x 1d ATR trail, 21-day time stop, fees, one position per coin
  baseline    the live 1d breakout on the same data and simulator (hlg.reversal.baseline)

  A  early warning   P(1d breakout within 10 days | ladder entry) / P(same | any 4h bar in the ladder
                     context) >= 1.5, and > 1 in both halves
  B  early entry     PF above the 95th percentile of 200 random-entry draws in the ladder context;
                     PF and average R >= the baseline's; PF > 1.2 in both halves
  C  confirmation    1d breakouts with a ladder in the 3 days before the breakout day vs those
                     without: higher PF in both halves. The share with a ladder is reported; above
                     90% the filter removes almost nothing and is called moot.

Three tests: about one false pass is plausible by chance, so anything that passes is tracked live
before it is adopted. Data: Binance USDT-M perps, 1h, with 4h and 1d built from it (hlg.reversal).
"""
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import DEF
from .common import log, setup_logging
from .reversal import (H, LIFT_DAYS, MAX_RISK_ATR, STOP_ATR, baseline, ctx_at, daily_context, klines, pf,
                       random_control, resample, simulate, summarize, trades)

N = 20
LEAD_H = 24          # the 1h break must close within this many hours before the 4h close
CONFIRM_DAYS = 3     # test C: ladder within this many days before the 1d breakout day
LIFT_MIN, MOOT = 1.5, 0.90


def breaks(df, n=N):
    """Close times of first closes above the prior n-bar high."""
    lvl = df.h.rolling(n).max().shift(1)
    above = df.c > lvl
    return above & ~above.shift(1, fill_value=False)


def ladders(h1, h4, d, start):
    """All ladder events -> (close time, in-context flag). One per 4h break with a 1h lead."""
    t1 = h1.index[(h1.c > h1.h.rolling(N).max().shift(1)).values] + H        # every 1h break close
    hi20_now = d.h.rolling(20).max()                                         # incl. the last completed day
    out = []
    b4 = breaks(h4)
    for t, c in zip(h4.index[b4.values] + 4 * H, h4.c.values[b4.values]):
        if t < start:
            continue
        i = t1.searchsorted(t, side="right")
        if not (i and t1[i - 1] > t - LEAD_H * H):
            continue
        k = ctx_at(d, t)
        D = t.floor("D") - pd.Timedelta(days=1)
        inctx = bool(k is not None and k.up and not k.broke and not np.isnan(k.atr)
                     and D in hi20_now.index and c < hi20_now.loc[D])
        out.append(dict(t=t, inctx=inctx, a=k.atr if k is not None else np.nan))
    return out


def ladder_trades(h1, events):
    """Trades for in-context ladders; the stop is set from the actual entry (2x 1d ATR below)."""
    pos_i = {t: i for i, t in enumerate(h1.index)}
    res, busy = [], None
    for e in sorted((e for e in events if e["inctx"]), key=lambda e: e["t"]):
        if busy is not None and e["t"] <= busy:
            continue
        i0 = pos_i.get(e["t"])
        if i0 is None:
            continue
        entry = h1.o.values[i0]
        sim = simulate(h1, i0, entry, entry - STOP_ATR * e["a"], e["a"])
        if sim is None:
            continue
        res.append(dict(t=e["t"], entry=entry, stop=entry - STOP_ATR * e["a"], risk_atr=STOP_ATR,
                        r=sim[0], exit_t=sim[1], kind="ladder"))
        busy = sim[1]
    return res


def _soon(br, t):
    return bool(((br > t) & (br <= t + pd.Timedelta(days=LIFT_DAYS))).any())


def lift_halves(books, T, mid):
    """Lift overall and per half: ladder entries vs every 4h bar open in the same context."""
    out = {}
    for name, lo, hi in (("all", None, None), ("h1", None, mid), ("h2", mid, None)):
        def keep(t):
            return (lo is None or t >= lo) and (hi is None or t < hi)
        hit = [_soon(books[c]["breakouts"], t) for c, t in zip(T.coin, T.t) if keep(t)]
        base = [_soon(b["breakouts"], t) for b in books.values() for t in b["ctx_ladder"] if keep(t)]
        p, q = (np.mean(hit) if hit else np.nan), (np.mean(base) if base else np.nan)
        out[name] = dict(n=len(hit), hit=p, base=q, lift=p / q if q else np.nan)
    return out


def study(P=None, coins=None):
    P = P or DEF
    start = pd.Timestamp(P["start"], tz="UTC")
    out_dir = Path(P["out_dir"])
    out_dir.mkdir(exist_ok=True)
    books, rows, stats = {}, [], dict(b4=0, b4_lead=0)
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
        ev = ladders(h1, h4, d, start)
        stats["b4"] += int(sum(t + 4 * H >= start for t in h4.index[breaks(h4).values]))
        stats["b4_lead"] += len(ev)
        base = [dict(x, coin=coin) for x in trades(h1, [e for e in baseline(h1, d) if e["t"] >= start])]
        ev_t = pd.DatetimeIndex([e["t"] for e in ev])
        for x in base:                       # test C: a ladder (any context) in the 3 days before the breakout day
            D = x["t"] - pd.Timedelta(days=1)
            x["laddered"] = bool(((ev_t >= D - pd.Timedelta(days=CONFIRM_DAYS)) & (ev_t <= D)).any())
        lad = [dict(x, coin=coin) for x in ladder_trades(h1, ev)]
        rows += base + lad
        hi20_now = d.h.rolling(20).max()
        ctx = []
        for t in h4.index:
            t_open = t + 4 * H                 # the next 4h open, after this bar's close
            if t_open < start:
                continue
            k = ctx_at(d, t_open)
            D = t_open.floor("D") - pd.Timedelta(days=1)
            if (k is not None and k.up and not k.broke and not np.isnan(k.atr) and D in hi20_now.index
                    and h4.c.loc[t] < hi20_now.loc[D]):
                ctx.append(t_open)
        books[coin] = dict(h1=h1, d=d, pos={t: i for i, t in enumerate(h1.index)}, ctx_ladder=ctx,
                           breakouts=pd.DatetimeIndex([D + pd.Timedelta(days=1) for D in d.index[d.up & d.broke]]))
        log.info("%s: %d base, %d ladder trades, %d ladder events (%d in context)", coin, len(base), len(lad),
                 len(ev), sum(e["inctx"] for e in ev))
    T = pd.DataFrame(rows)
    T.to_csv(out_dir / "ladder_trades.csv", index=False)
    mid = T.t.sort_values().iloc[len(T) // 2]
    B, L = T[T.kind == "base"], T[T.kind == "ladder"]
    S = {"1d breakout (reference)": summarize(B, mid), "ladder entry": summarize(L, mid),
         "1d breakout, laddered": summarize(B[B.laddered == True], mid),          # noqa: E712
         "1d breakout, not laddered": summarize(B[B.laddered == False], mid)}     # noqa: E712
    b, s = S["1d breakout (reference)"], S["ladder entry"]

    lf = lift_halves(books, L, mid)
    A = lf["all"]["lift"] >= LIFT_MIN and lf["h1"]["lift"] > 1 and lf["h2"]["lift"] > 1
    rnd = random_control(books, L, "ladder")
    pct = (rnd < s["pf"]).mean() * 100
    okB = [pct > 95, s["pf"] >= b["pf"] and s["avg_r"] >= b["avg_r"], s["pf_h1"] > 1.2 and s["pf_h2"] > 1.2]
    share = B.laddered.mean()
    y, n = S["1d breakout, laddered"], S["1d breakout, not laddered"]
    okC = n.get("trades", 0) > 0 and y["pf_h1"] > n.get("pf_h1", np.nan) and y["pf_h2"] > n.get("pf_h2", np.nan)
    C = "moot" if share > MOOT else ("PASS" if okC else "fail")

    rep = [f"# Breakout ladder 1h -> 4h -> 1d (Binance USDT-M perps, 1h, {P['start']} -> now, {len(books)} coins)\n",
           f"4h first-breaks: {stats['b4']}; with a 1h break in the prior {LEAD_H}h: {stats['b4_lead']} "
           f"({stats['b4_lead'] / max(stats['b4'], 1):.0%}).\n",
           pd.DataFrame(S).T.astype(float).round(2).to_markdown() + "\n",
           "## Verdicts (rules fixed before running)\n",
           f"**A, early warning:** 1d breakout within {LIFT_DAYS}d after a ladder entry "
           f"{lf['all']['hit']:.1%} vs {lf['all']['base']:.1%} from any 4h bar in context, lift "
           f"{lf['all']['lift']:.2f} (halves {lf['h1']['lift']:.2f} / {lf['h2']['lift']:.2f}; needs >= {LIFT_MIN} and "
           f"> 1 in both): **{'PASS' if A else 'fail'}**\n",
           f"**B, early entry:** PF {s['pf']:.2f} vs random median {np.median(rnd):.2f}, {pct:.0f}th percentile "
           f"(needs > 95: {okB[0]}); vs baseline PF {b['pf']:.2f} / avg R {b['avg_r']:+.2f} ({okB[1]}); halves "
           f"{s['pf_h1']:.2f} / {s['pf_h2']:.2f} (> 1.2 both: {okB[2]}): **{'PASS' if all(okB) else 'fail'}**\n",
           f"**C, confirmation:** {share:.0%} of 1d breakouts had a ladder in the {CONFIRM_DAYS} days before; "
           f"laddered PF {y['pf_h1']:.2f} / {y['pf_h2']:.2f} vs not {n.get('pf_h1', np.nan):.2f} / "
           f"{n.get('pf_h2', np.nan):.2f} by half: **{C}**\n"]
    (out_dir / "ladder_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return T, S, dict(A=A, B=all(okB), C=C, lift=lf, rnd_pct=pct, share=share)


def main():
    setup_logging()
    study()


if __name__ == "__main__":
    main()
