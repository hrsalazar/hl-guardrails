"""Signal grade study: can a breakout be rated Low / Neutral / High before entry?

    python -m hlg.signal_grade

Pre-registered in docs/research/signal-grade-study.md (signals, outcomes, features, model, walk-forward
and the adoption bar were written down before the first run). Research only: nothing here runs in
the monitor. Every signal is simulated on its own under the live rule, so the sample is every
breakout, not only the ones a 3-slot book would have taken.
"""
import numpy as np
import pandas as pd
from pathlib import Path

from . import backtest as bt
from . import macro, sentiment
from .common import log, setup_logging

START = "2023-06-01"
BARS_21D = {"1d": 21, "4h": 126}
TRAIN_MONTHS = {"1d": 12, "4h": 6}
FEES = (0.00015, 0.00045)
FEATURES = ["close_loc", "brk_atr", "vol_ratio", "atr_pctile", "ext_atr", "rsi", "funding_apr", "rs_rank",
            "above_50w", "market", "prior_fails"]


# ----------------------------------------------------------------------------- outcomes
def simulate(o, h, l, c, atr, i, max_bars, stop_atr=2.0, trail_atr=3.0):
    """One trade from a signal at bar i (arrays). Returns (exit_index, R after fees, hit1R) or None
    when it is still open at the end of the data. Same-bar stop and +1R: the stop counts first."""
    if i + 1 >= len(o):
        return None
    entry = o[i + 1]
    stop = c[i] - stop_atr * atr[i]
    risk = entry - stop
    if risk <= 0:
        return None
    target, best, hit1 = entry + risk, entry, False
    last = min(i + max_bars, len(o) - 1)
    for j in range(i + 1, last + 1):
        if l[j] <= stop:
            px, end = min(o[j], stop), j
            break
        hit1 = hit1 or h[j] >= target
        best = max(best, h[j])
        stop = max(stop, best - trail_atr * atr[j])
    else:
        if last < i + max_bars:            # data ended before the time stop: unresolved
            return None
        px, end = c[last], last
    r = (px - entry) / risk - (entry * FEES[0] + px * FEES[1]) / risk
    return end, r, hit1


def signals(df, coin, iv, start, market):
    """Every rule signal on one coin/timeframe, one simulated trade at a time, with its features."""
    V = bt.VARIANTS["breakout_long"]
    o, h, l, c, a = (df[k].to_numpy(float) for k in ("o", "h", "l", "c", "atr"))
    step = pd.Timedelta(hours=bt.INTERVAL_H[iv])
    rows, busy_until = [], -1
    for i, k in enumerate(df.itertuples()):
        if i <= busy_until or k.Index < start or not bt.signal(k, V):
            continue
        res = simulate(o, h, l, c, a, i, BARS_21D[iv])
        t = k.Index + step                                        # the signal is known at the bar's close
        if res is None:
            busy_until = len(df)                                  # still open at the end: nothing after it
            continue
        end, r, hit1 = res
        busy_until = end
        f = getattr(k, "coin_above_sma50w", pd.NA)
        rows.append({"coin": coin, "tf": iv, "t": t, "resolved": df.index[end] + step, "R": r, "hit1": bool(hit1),
                     "close_loc": k.close_loc, "brk_atr": k.brk_atr, "vol_ratio": k.vol_ratio, "atr_pctile": k.atr_pctile,
                     "ext_atr": k.ext_atr, "rsi": k.rsi, "funding_apr": k.funding_apr, "rs_rank": k.rs_rank,
                     "above_50w": np.nan if pd.isna(f) else float(bool(f)),
                     "market": market.get(k.Index.floor("D"), np.nan)})
    return rows


def add_prior_fails(S, days=60):
    """Per coin and timeframe: earlier signals in the last `days` that missed +1R, counted only once
    they had resolved (their outcome was known) by this signal's time."""
    S = S.sort_values("t").reset_index(drop=True)
    pf = np.zeros(len(S))
    for _, g in S.groupby(["coin", "tf"]):
        for idx in g.index:
            t = S.at[idx, "t"]
            prev = g[(g.t < t) & (g.t >= t - pd.Timedelta(days=days)) & (g.resolved <= t)]
            pf[idx] = (~prev.hit1).sum()
    S["prior_fails"] = pf
    return S


# ----------------------------------------------------------------------------- model
def fit_logit(X, y, lam=1.0, iters=100):
    """L2 logistic regression by Newton steps; intercept unpenalised. Returns weights incl. intercept."""
    n, p = X.shape
    Xb = np.c_[np.ones(n), X]
    w = np.zeros(p + 1)
    pen = np.r_[0.0, np.full(p, lam)]
    for _ in range(iters):
        pr = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (pr - y) + pen * w
        H = Xb.T @ (Xb * (pr * (1 - pr))[:, None]) + np.diag(pen) + 1e-9 * np.eye(p + 1)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


class Grader:
    """Median-imputed, standardised features -> P(hit1R); tercile cut points from its training set."""

    def __init__(self, train):
        X = train[FEATURES].astype(float)
        self.med = X.median().fillna(0.0)
        X = X.fillna(self.med)
        self.mu, self.sd = X.mean(), X.std(ddof=0).replace(0, 1.0)
        self.w = fit_logit(((X - self.mu) / self.sd).to_numpy(), train.hit1.to_numpy(float))
        p = self.prob(train)
        self.cuts = np.quantile(p, [1 / 3, 2 / 3])

    def prob(self, df):
        X = ((df[FEATURES].astype(float).fillna(self.med) - self.mu) / self.sd).to_numpy()
        return 1 / (1 + np.exp(-np.clip(np.c_[np.ones(len(X)), X] @ self.w, -30, 30)))

    def grade(self, p):
        return np.where(p <= self.cuts[0], "Low", np.where(p <= self.cuts[1], "Neutral", "High"))


def walk_forward(S, iv):
    """Calendar quarters; each scored by a model trained only on trades closed before it began."""
    first = S.t.min() + pd.DateOffset(months=TRAIN_MONTHS[iv])
    q = pd.Timestamp(year=first.year, month=3 * ((first.month - 1) // 3) + 1, day=1, tz="UTC")
    if q < first:
        q += pd.DateOffset(months=3)
    out = []
    while q <= S.t.max():
        nxt = q + pd.DateOffset(months=3)
        train, test = S[S.resolved < q], S[(S.t >= q) & (S.t < nxt)]
        if len(train) >= 30 and len(test):
            gr = Grader(train)
            p = gr.prob(test)
            out.append(test.assign(p=p, grade=gr.grade(p)))
        q = nxt
    return pd.concat(out) if out else pd.DataFrame()


def week_bootstrap_diff(O, n=10_000, seed=0):
    """95% CI of avg R(High) - avg R(Low), resampling whole weeks (signals in a week are correlated)."""
    rng = np.random.default_rng(seed)
    wk = O.t.dt.to_period("W").astype(str)
    groups = {w: g for w, g in O.groupby(wk)}
    keys = list(groups)
    diffs = []
    for _ in range(n):
        s = pd.concat([groups[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        hi, lo = s[s.grade == "High"].R, s[s.grade == "Low"].R
        if len(hi) and len(lo):
            diffs.append(hi.mean() - lo.mean())
    return np.percentile(diffs, [2.5, 97.5])


def verdict(O):
    """The pre-registered bar, on one timeframe's out-of-sample quarters."""
    by = O.groupby("grade").agg(n=("R", "size"), avg_R=("R", "mean"), hit1=("hit1", "mean"), pred=("p", "mean"),
                                win=("R", lambda r: (r > 0).mean())).reindex(["Low", "Neutral", "High"])
    diff = by.at["High", "avg_R"] - by.at["Low", "avg_R"]
    lo, hi = week_bootstrap_diff(O)
    mid = O.t.sort_values().iloc[len(O) // 2]
    halves = []
    for part in (O[O.t < mid], O[O.t >= mid]):
        a, b = part[part.grade == "High"].R, part[part.grade == "Low"].R
        halves.append(a.mean() - b.mean() if len(a) and len(b) else np.nan)
    checks = {
        "separation >= 0.30R, CI excludes 0": bool(diff >= 0.30 and lo > 0),
        "each half >= 0.15R": bool(all(x >= 0.15 for x in halves if not np.isnan(x)) and not any(np.isnan(halves))),
        "calibration within 10pp": bool(((by.pred - by.hit1).abs() <= 0.10).all()),
        "order High >= Neutral >= Low": bool(by.at["High", "hit1"] >= by.at["Neutral", "hit1"] >= by.at["Low", "hit1"]),
    }
    return by, diff, (lo, hi), halves, checks


def base_rates(S):
    r = S.R.sort_values(ascending=False)
    top = r.iloc[:max(1, len(r) // 5)].sum()
    return {"signals": len(S), "win rate": (S.R > 0).mean(), "reached +1R before the stop": S.hit1.mean(),
            "avg R": S.R.mean(), "profit from the top 20% of trades": top / r[r > 0].sum() if (r > 0).any() else np.nan,
            "net R of the top 20% vs all": f"{top:+.1f}R of {r.sum():+.1f}R"}


# ----------------------------------------------------------------------------- run
def load(iv, cache):
    start = pd.Timestamp(START, tz="UTC")
    data, fund = {}, {}
    for c in bt.DEF["coins"]:
        df = bt.bars(c, cache, iv)
        if df is None or len(df) < 80:
            continue
        data[c] = bt.features(df, 1)
        fund[c] = bt.funding(c, int(start.timestamp() * 1000), cache, iv)
    bt.context(data, fund)
    bt.ma_context(data, cache, iv)
    S = bt.regime_states(bt.bars("BTC", cache, "1d"), macro.features(macro.frame(start="2022-01-01", cache_dir=str(cache))),
                         sentiment.features(sentiment.frame(cache_dir=str(cache))))
    legs = ((S.trend == "btc_up").astype(int) + (S.macro == "macro_on").astype(int) + (S.crowd == "greed").astype(int)
            - (S.trend == "btc_down").astype(int) - (S.macro == "macro_off").astype(int) - (S.crowd == "fear").astype(int))
    market = legs.to_dict()
    rows = [r for c, df in data.items() for r in signals(df, c, iv, start, market)]
    return add_prior_fails(pd.DataFrame(rows))


def main():
    setup_logging()
    cache, out = Path(bt.DEF["cache_dir"]), Path(bt.DEF["out_dir"])
    out.mkdir(exist_ok=True)
    rep = [f"# Signal grade study ({START} -> now)\n", "Pre-registered: docs/research/signal-grade-study.md\n"]
    passed = {}
    for iv in ("1d", "4h"):
        S = load(iv, cache)
        S.to_csv(out / f"signal_grade_{iv}.csv", index=False)
        log.info("%s: %d signals", iv, len(S), extra={"safe": True})
        rep.append(f"\n## {iv}: {len(S)} signals\n")
        rep.append("**Base rates (all signals):** " + "; ".join(f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}"
                                                                for k, v in base_rates(S).items()) + "\n")
        full = Grader(S)
        rep.append("Coefficients (all data, standardised; descriptive only): "
                   + ", ".join(f"{f} {w:+.2f}" for f, w in zip(FEATURES, full.w[1:])) + "\n")
        O = walk_forward(S, iv)
        if O.empty:
            rep.append("Not enough data for an out-of-sample quarter.\n")
            passed[iv] = False
            continue
        by, diff, ci, halves, checks = verdict(O)
        rep.append(f"Out-of-sample: {len(O)} signals, {O.t.min():%Y-%m-%d} -> {O.t.max():%Y-%m-%d}\n")
        rep.append(by.round(3).to_markdown() + "\n")
        rep.append(f"High - Low avg R: {diff:+.2f}R, 95% CI {ci[0]:+.2f} .. {ci[1]:+.2f}; halves {halves[0]:+.2f} / {halves[1]:+.2f}\n")
        rep.append("Checks: " + "; ".join(f"{k}: {'PASS' if v else 'fail'}" for k, v in checks.items()) + "\n")
        passed[iv] = all(checks.values())
        rep.append(f"**{iv}: {'ADOPTED' if passed[iv] else 'not adopted'}**\n")
    (out / "signal_grade_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return passed


if __name__ == "__main__":
    main()
