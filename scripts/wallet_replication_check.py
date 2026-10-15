"""Score the wallet-behavior replication against its pre-registered bars (H1-H5).

    python scripts/wallet_replication_check.py [CACHE_DIR ...]

Defaults to the replication sample (miner_cache/wallet_study_rep) and, for comparison only, the
original (miner_cache/wallet_study). Uses the study's own position rebuild and filters unchanged
(scripts/wallet_behavior_study.py); this file only computes the five measures and compares them with
the bars in docs/research/wallet-behavior-replication.md. Prints aggregates only.
"""
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wallet_behavior_study as w  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BARS = {  # id: (claim, threshold, comparison)
    "H1": ("wallets liquidated at least once in 90 days", 0.35, ">="),
    "H2": ("liquidated positions with no stop (where known)", 0.85, ">="),
    "H3": ("per-wallet median share of losses from the worst 5% of positions", 0.40, ">="),
    "H4": ("wallets unprofitable over 90 days after fees", 0.50, ">="),
    "H5": ("wallets where averaged-down positions' loss share >= 2x their position share", 0.50, ">"),
}


def measures(cache):
    wallets = [json.loads(p.read_text()) for p in sorted((cache / "wallets").glob("*.json"))]
    rows, liq_known, liq_nostop, bot, few = [], 0, 0, 0, 0
    for wl in wallets:
        ps = w.positions(wl)
        if len(ps) < w.MIN_POSITIONS:
            few += 1
            continue
        if len(ps) / w.DAYS > w.BOTLIKE_POSITIONS_PER_DAY:
            bot += 1
            continue
        loss = w._loss(ps)
        ad = [p for p in ps if p["ad"]]
        for p in ps:
            if p["liq"] and p["stop"] is not None:
                liq_known += 1
                liq_nostop += p["stop"] is False
        rows.append({"liq": any(p["liq"] for p in ps), "pnl": sum(p["pnl"] for p in ps),
                     "worst5": w._loss(sorted(ps, key=lambda p: p["pnl"])[:max(1, len(ps) // 20)]) / loss if loss else None,
                     "ad_rate": len(ad) / len(ps), "ad_loss": w._loss(ad) / loss if loss else None})
    h5 = [r for r in rows if r["ad_rate"] > 0 and r["ad_loss"] is not None]
    return {"fetched": len(wallets), "analysed": len(rows), "bot": bot, "few": few, "liq_known": liq_known, "n_h5": len(h5),
            "H1": sum(r["liq"] for r in rows) / len(rows),
            "H2": liq_nostop / liq_known if liq_known else None,
            "H3": st.median(r["worst5"] for r in rows if r["worst5"] is not None),
            "H4": sum(r["pnl"] <= 0 for r in rows) / len(rows),
            "H5": sum(r["ad_loss"] >= 2 * r["ad_rate"] for r in h5) / len(h5) if h5 else None}


def verdict(h, v):
    if v is None:
        return "n/a"
    t, op = BARS[h][1], BARS[h][2]
    return "PASS" if (v >= t if op == ">=" else v > t) else "fail"


def main():
    dirs = [ROOT / d for d in sys.argv[1:]] or [ROOT / "miner_cache" / "wallet_study_rep", ROOT / "miner_cache" / "wallet_study"]
    res = {d.name: measures(d) for d in dirs}
    names = list(res)
    print("| | claim | bar | " + " | ".join(names) + " | verdict (first) |")
    print("|---|---|---|" + "---|" * len(names) + "---|")
    for h, (claim, t, op) in BARS.items():
        vals = " | ".join("n/a" if res[n][h] is None else f"{res[n][h]:.0%}" for n in names)
        print(f"| {h} | {claim} | {op} {t:.0%} | {vals} | **{verdict(h, res[names[0]][h])}** |")
    for n in names:
        r = res[n]
        print(f"\n{n}: {r['fetched']} fetched, {r['analysed']} analysed ({r['bot']} bot-like, {r['few']} with < "
              f"{w.MIN_POSITIONS} positions); H2 on {r['liq_known']} liquidated positions with stop coverage; "
              f"H5 on {r['n_h5']} wallets that averaged down")


if __name__ == "__main__":
    main()
