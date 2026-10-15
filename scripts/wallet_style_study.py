"""Why are averaging-down wallets more often profitable? Pre-registered in docs/research/wallet-style-study.md.

    python scripts/wallet_style_study.py

Both cached wallet samples, the study's own position rebuild (scripts/wallet_behavior_study.py,
unchanged). Prints aggregates only; raw data stays in the gitignored miner_cache/.
"""
import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wallet_behavior_study as w  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {"original (seed 42)": ROOT / "miner_cache" / "wallet_study",
           "replication (seed 2026)": ROOT / "miner_cache" / "wallet_study_rep"}
STYLE = ["add_rate", "activity", "hold", "maker"]
N_BOOT = 1000


def wallet_rows(cache, sample):
    rows, added = [], []
    for p in sorted((cache / "wallets").glob("*.json")):
        wl = json.loads(p.read_text())
        ps = w.positions(wl)
        if len(ps) < w.MIN_POSITIONS or len(ps) / w.DAYS > w.BOTLIKE_POSITIONS_PER_DAY:
            continue
        perp = [f for f in wl["fills"] if f["dir"] in w.PERP_DIRS]
        known = [q for q in ps if q["stop"] is not None]
        loss = w._loss(ps)
        rows.append({
            "sample": sample, "profitable": sum(q["pnl"] for q in ps) > 0,
            "ad_rate": sum(q["ad"] for q in ps) / len(ps),
            "add_rate": sum(q["adds"] > 0 for q in ps) / len(ps),
            "stop_rate": sum(q["stop"] for q in known) / len(known) if known else None,
            "activity": math.log(len(ps) / w.DAYS),
            "hold": math.log(max(st.median(q["hold_h"] for q in ps), 1 / 60)),
            "maker": sum(not f.get("crossed", True) for f in perp) / len(perp) if perp else 0.0,
            "liq": any(q["liq"] for q in ps),
            "worst5": w._loss(sorted(ps, key=lambda q: q["pnl"])[:max(1, len(ps) // 20)]) / loss if loss else None,
        })
        ad = [q["pnl"] for q in ps if q["adds"] and q["ad"]]
        other = [q["pnl"] for q in ps if q["adds"] and not q["ad"]]
        if len(ad) >= 3 and len(other) >= 3:
            added.append(np.mean(ad) < np.mean(other))
    return rows, added


def logit(X, y, iters=50, ridge=1e-6):
    """Newton-Raphson logistic regression with an intercept; a tiny ridge keeps it finite."""
    X = np.column_stack([np.ones(len(X)), X])
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        g = X.T @ (y - p) - ridge * b
        H = -(X.T * (p * (1 - p))) @ X - ridge * np.eye(len(b))
        step = np.linalg.solve(H, g)
        b -= step
        if np.abs(step).max() < 1e-8:
            break
    return b[1:]


def z(a):
    a = np.asarray(a, float)
    return (a - a.mean()) / (a.std() or 1)


def coef_test(rows, key, seed=0):
    rows = [r for r in rows if r[key] is not None]
    y = np.array([r["profitable"] for r in rows], float)
    x = z([r[key] for r in rows])
    S = np.column_stack([z([r[k] for r in rows]) for k in STYLE])
    b0 = logit(x[:, None], y)[0]
    b1 = logit(np.column_stack([x, S]), y)[0]
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(y), len(y))
        boot.append(logit(np.column_stack([x[i], S[i]]), y[i])[0])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    if abs(b1) <= 0.5 * abs(b0) and lo <= 0 <= hi:
        v = "explained by style"
    elif lo > 0:
        v = "goes with profit beyond style"
    elif hi < 0:
        v = "goes with losses beyond style"
    else:
        v = "partly explained"
    return {"n": len(y), "b": b0, "b_style": b1, "ci": (lo, hi), "verdict": v,
            "style_coefs": dict(zip(STYLE, logit(np.column_stack([x, S]), y)[1:]))}


def sign_test(flags):
    n, k = len(flags), sum(flags)
    p = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else 1.0
    return n, k, p


def thirds(rows):
    s = sorted(rows, key=lambda r: r["ad_rate"])
    k = len(s) // 3
    return s[:k], s[-k:]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")       # the Windows console defaults to cp1252
    rows, added = [], []
    for name, cache in SAMPLES.items():
        r, a = wallet_rows(cache, name)
        rows += r
        added += a
    by = {n: [r for r in rows if r["sample"] == n] for n in SAMPLES}
    pc = lambda x: f"{100 * x:.0f}%"  # noqa: E731
    L = [f"# Wallet style study ({len(rows)} wallets: " + ", ".join(f"{n} {len(v)}" for n, v in by.items()) + ")\n"]

    L.append("## T1 / T2: does style explain the link? (logit, standardised; 95% bootstrap CI)\n")
    L.append("| test | sample | n | β alone | β with style | 95% CI | verdict |\n|---|---|---:|---:|---:|---|---|")
    res = {}
    for key, t in (("ad_rate", "T1 averaging down"), ("stop_rate", "T2 stops")):
        for name, rs in [("pooled", rows)] + list(by.items()):
            c = coef_test(rs, key)
            res[(key, name)] = c
            L.append(f"| {t} | {name} | {c['n']} | {c['b']:+.2f} | {c['b_style']:+.2f} | {c['ci'][0]:+.2f} to {c['ci'][1]:+.2f} | "
                     f"{'**' + c['verdict'] + '**' if name == 'pooled' else c['verdict']} |")
    L.append("\nStyle coefficients in the pooled averaging-down model: " +
             ", ".join(f"{k} {v:+.2f}" for k, v in res[("ad_rate", "pooled")]["style_coefs"].items()) + "\n")

    n, k, p = sign_test(added)
    L.append("## T3: within the same style, does averaging down still hurt?\n")
    L.append(f"Wallets with >= 3 averaged-down and >= 3 other added positions: {n}. Averaged-down positions had the lower mean "
             f"P&L in {k} ({pc(k / n) if n else 'n/a'}); one-sided sign test p = {p:.3g}: "
             f"**{'hurts within style' if p < 0.05 else 'not shown'}**\n")

    L.append("## T4: do averaging-down wallets carry more tail risk?\n")
    L.append("| sample | third | wallets | profitable | liquidated | median worst-5% loss share |\n|---|---|---:|---:|---:|---:|")
    ok = []
    for name, rs in by.items():
        lo, hi = thirds(rs)
        for lab, g in (("lowest ad_rate", lo), ("highest ad_rate", hi)):
            L.append(f"| {name} | {lab} | {len(g)} | {pc(np.mean([r['profitable'] for r in g]))} | {pc(np.mean([r['liq'] for r in g]))} | "
                     f"{pc(st.median(r['worst5'] for r in g if r['worst5'] is not None))} |")
        ok.append(np.mean([r["liq"] for r in hi]) > np.mean([r["liq"] for r in lo])
                  and st.median(r["worst5"] for r in hi if r["worst5"] is not None)
                  > st.median(r["worst5"] for r in lo if r["worst5"] is not None))
    L.append(f"\n**T4: {'supported' if all(ok) else 'not supported'}** (needs both samples)\n")

    L.append("## No bar: each measure vs profitability (pooled)\n")
    L.append("| measure | Spearman with profitable | median, profitable | median, not |\n|---|---:|---:|---:|")
    for key in ["ad_rate", "add_rate", "stop_rate", "activity", "hold", "maker"]:
        rs = [r for r in rows if r[key] is not None]
        a = np.array([r[key] for r in rs])
        y = np.array([r["profitable"] for r in rs], float)
        rho = np.corrcoef(np.argsort(np.argsort(a)), y)[0, 1]
        fmt = (lambda v: f"{math.exp(v):.2f}") if key in ("activity", "hold") else (lambda v: f"{v:.2f}")
        L.append(f"| {key}{' (pos/day)' if key == 'activity' else ' (hours)' if key == 'hold' else ''} | {rho:+.2f} | "
                 f"{fmt(np.median(a[y == 1]))} | {fmt(np.median(a[y == 0]))} |")
    L.append("\n## No bar: profitability by add_rate third (pooled)\n")
    s = sorted(rows, key=lambda r: r["add_rate"])
    k3 = len(s) // 3
    for lab, g in (("lowest", s[:k3]), ("middle", s[k3:-k3]), ("highest", s[-k3:])):
        L.append(f"- {lab} add_rate (median {np.median([r['add_rate'] for r in g]):.2f}): {len(g)} wallets, profitable "
                 f"{pc(np.mean([r['profitable'] for r in g]))}")
    text = "\n".join(L) + "\n"
    out = ROOT / "backtest_out" / "wallet_style_study.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
