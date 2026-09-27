"""Do other Hyperliquid traders lose money the way the guardrails assume? A behavior study of random wallets.

Samples active wallets from the public leaderboard, rebuilds every perp position (flat -> flat) from
their fills over the last 90 days, and measures how averaging down, missing stops and liquidations relate
to losses. Findings and caveats: docs/research/wallet-behavior-study.md.

    python scripts/wallet_behavior_study.py fetch     # ~1-2 h for 200 wallets (HL rate limit); resumable
    python scripts/wallet_behavior_study.py analyze   # prints and writes backtest_out/wallet_behavior_study.md

Run from the repo root. Raw data (other people's addresses and trades) stays in miner_cache/wallet_study/,
which is gitignored: the repo is public, so only aggregates go into docs/.
"""
import json
import random
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "miner_cache" / "wallet_study"
WALLETS = CACHE / "wallets"
LEADERBOARD = CACHE / "leaderboard.json"
OUT = ROOT / "backtest_out" / "wallet_behavior_study.md"

N_WALLETS = 200
SEED = 42
DAYS = 90
MIN_MONTH_VLM, MAX_MONTH_VLM, MAX_ACCOUNT = 2e4, 2e7, 1e6
AD_THRESHOLD = 0.005            # an add >=0.5% worse than the average entry counts as averaging down
BOTLIKE_POSITIONS_PER_DAY = 20
MIN_POSITIONS = 5
WEIGHT_PER_MIN = 900            # HL allows 1200/min per IP
EPS = 1e-9
PERP_DIRS = {"Open Long", "Close Long", "Open Short", "Close Short", "Long > Short", "Short > Long"}


# ---------- fetch ----------
_spent = []


def _throttle(weight):
    while True:
        now = time.time()
        while _spent and now - _spent[0][0] > 60:
            _spent.pop(0)
        if sum(w for _, w in _spent) + weight <= WEIGHT_PER_MIN:
            _spent.append((now, weight))
            return
        time.sleep(1)


def _info(body, weight):
    for attempt in range(6):
        _throttle(weight)
        try:
            req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("rate limited")


def fetch():
    WALLETS.mkdir(parents=True, exist_ok=True)
    if not LEADERBOARD.exists():
        urllib.request.urlretrieve("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard", LEADERBOARD)
    rows = json.loads(LEADERBOARD.read_text(encoding="utf-8"))["leaderboardRows"]

    def month_vlm(r):
        return float(dict(r["windowPerformances"])["month"]["vlm"])
    # Near-zero accounts stay in: dropping blown-up wallets would hide the damage being measured.
    pool = [r for r in rows if float(r["accountValue"]) <= MAX_ACCOUNT and MIN_MONTH_VLM <= month_vlm(r) <= MAX_MONTH_VLM]
    sample = random.Random(SEED).sample(pool, N_WALLETS)
    print(f"pool {len(pool)}, sampling {N_WALLETS}", flush=True)

    start = int((time.time() - DAYS * 86400) * 1000)
    for i, r in enumerate(sample, 1):
        out = WALLETS / f"{r['ethAddress']}.json"
        if out.exists():
            continue
        fills, t = [], start
        while True:
            page = _info({"type": "userFillsByTime", "user": r["ethAddress"], "startTime": t, "aggregateByTime": True}, 120)
            fills += page
            if len(page) < 2000 or len(fills) >= 10000:   # HL serves at most the latest 10k fills
                break
            t = page[-1]["time"] + 1
        orders = _info({"type": "historicalOrders", "user": r["ethAddress"]}, 20)
        out.write_text(json.dumps({"addr": r["ethAddress"], "lb": r, "fills": fills, "orders": orders}))
        print(f"{i}/{N_WALLETS} fills={len(fills)} orders={len(orders)}", flush=True)


# ---------- positions ----------
def _new_position(coin, f, after, px, pnl, fee_taker):
    return {"coin": coin, "side": 1 if after > 0 else -1, "start": f["time"], "size": abs(after),
            "cost": abs(after) * px, "pnl": pnl, "ad_adds": 0, "adds": 0, "liq": False, "fills": 1 if fee_taker else 0}


def positions(wallet):
    """Closed perp positions (flat -> flat) with averaging-down, stop, liquidation and P&L (after fees)."""
    addr = wallet["addr"].lower()
    fills = sorted((f for f in wallet["fills"] if f["dir"] in PERP_DIRS or "liquidation" in f),
                   key=lambda f: (f["time"], f.get("tid", 0)))
    open_pos, done = {}, []
    for f in fills:
        coin, px, sz = f["coin"], float(f["px"]), float(f["sz"])
        before = float(f["startPosition"])
        after = before + (sz if f["side"] == "B" else -sz)
        fee = float(f.get("fee", 0)) + float(f.get("builderFee", 0) or 0)
        liq = "liquidation" in f and (f["liquidation"] or {}).get("liquidatedUser", "").lower() == addr
        p = open_pos.get(coin)

        if abs(before) < EPS:
            open_pos[coin] = _new_position(coin, f, after, px, -fee, True)
            continue
        if p is None:            # position opened before the window; skip until it is flat
            continue
        p["pnl"] += float(f["closedPnl"]) - fee
        p["fills"] += 1
        p["liq"] |= liq
        same_side = (after > 0) == (before > 0) and abs(after) > EPS
        if same_side and abs(after) > abs(before):                  # adding
            avg = p["cost"] / p["size"]
            worse = px < avg * (1 - AD_THRESHOLD) if p["side"] > 0 else px > avg * (1 + AD_THRESHOLD)
            p["adds"] += 1
            p["ad_adds"] += int(worse)
            p["cost"] += sz * px
            p["size"] += sz
        elif same_side:                                             # partial close: average entry unchanged
            p["cost"] *= abs(after) / p["size"]
            p["size"] = abs(after)
        else:                                                       # flat, or flipped through zero
            p["end"] = f["time"]
            done.append(p)
            del open_pos[coin]
            if abs(after) > EPS:
                open_pos[coin] = _new_position(coin, f, after, px, 0.0, False)

    # Stops: reduce-only stop orders in the order history, which HL caps at the latest 2000 orders.
    orders = wallet["orders"]
    covered_from = min((o["order"]["timestamp"] for o in orders), default=None) if len(orders) >= 2000 else 0
    stops = [(o["order"]["coin"], o["order"]["timestamp"]) for o in orders
             if o["order"].get("orderType", "").startswith("Stop") and o["order"].get("reduceOnly")]
    for p in done:
        p["hold_h"] = (p["end"] - p["start"]) / 3.6e6
        p["ad"] = p["ad_adds"] > 0
        p["stop"] = (any(c == p["coin"] and p["start"] - 60_000 <= t <= p["end"] for c, t in stops)
                     if covered_from is not None and p["start"] >= covered_from else None)
    return done


# ---------- analyze ----------
def _pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def _med(x):
    x = list(x)
    return st.median(x) if x else float("nan")


def _loss(ps):
    return -sum(p["pnl"] for p in ps if p["pnl"] < 0)


def analyze():
    wallets = [json.loads(p.read_text()) for p in sorted(WALLETS.glob("*.json"))]
    rows, allp, botlike = [], [], 0
    for w in wallets:
        ps = positions(w)
        if len(ps) < MIN_POSITIONS:
            continue
        if len(ps) / DAYS > BOTLIKE_POSITIONS_PER_DAY:
            botlike += 1
            continue
        allp += ps
        loss = _loss(ps)
        known = [p for p in ps if p["stop"] is not None]
        rows.append({
            "pnl": sum(p["pnl"] for p in ps),
            "ad_rate": sum(p["ad"] for p in ps) / len(ps),
            "ad_loss_share": _loss([p for p in ps if p["ad"]]) / loss if loss else None,
            "stop_rate": sum(p["stop"] for p in known) / len(known) if known else None,
            "nostop_rate": sum(not p["stop"] for p in known) / len(known) if known else None,
            "nostop_loss_share": _loss([p for p in known if not p["stop"]]) / _loss(known) if _loss(known) else None,
            "liqs": sum(p["liq"] for p in ps),
            "liq_loss_share": _loss([p for p in ps if p["liq"]]) / loss if loss else None,
            "worst5_loss_share": _loss(sorted(ps, key=lambda p: p["pnl"])[:max(1, len(ps) // 20)]) / loss if loss else None,
        })

    L = [f"# Wallet behavior study: raw results ({len(rows)} wallets, last {DAYS} days)\n",
         f"{len(wallets)} fetched; {botlike} excluded as bot-like (>{BOTLIKE_POSITIONS_PER_DAY} positions/day); "
         f"wallets with <{MIN_POSITIONS} closed positions skipped. {len(allp):,} closed positions.\n"]
    ad, nad = [p for p in allp if p["ad"]], [p for p in allp if not p["ad"]]
    worst = sorted(allp, key=lambda p: p["pnl"])[:max(1, len(allp) // 100)]
    s = [r for r in rows if r["ad_loss_share"] is not None and r["ad_rate"] > 0]
    L += ["## Averaging down",
          f"- Wallets that averaged down at least once: {_pct(sum(r['ad_rate'] > 0 for r in rows), len(rows))}",
          f"- Positions averaged down: {_pct(len(ad), len(allp))}",
          f"- Win rate: averaged-down {_pct(sum(p['pnl'] > 0 for p in ad), len(ad))} vs not {_pct(sum(p['pnl'] > 0 for p in nad), len(nad))}",
          f"- Median P&L per position: averaged-down ${_med(p['pnl'] for p in ad):,.2f} vs not ${_med(p['pnl'] for p in nad):,.2f}",
          f"- Per wallet (those who average down): median {_med(r['ad_rate'] for r in s) * 100:.0f}% of positions, "
          f"{_med(r['ad_loss_share'] for r in s) * 100:.0f}% of losses",
          f"- Worst 1% of positions: {_pct(sum(p['ad'] for p in worst), len(worst))} averaged down"]
    known = [p for p in allp if p["stop"] is not None]
    kw = sorted(known, key=lambda p: p["pnl"])[:max(1, len(known) // 100)]
    s2 = [r for r in rows if r["nostop_loss_share"] is not None]
    L += ["\n## Stops",
          f"- Positions with stop coverage known: {len(known):,}; had a reduce-only stop: {_pct(sum(p['stop'] for p in known), len(known))}",
          f"- Win rate: with stop {_pct(sum(p['pnl'] > 0 for p in known if p['stop']), sum(p['stop'] for p in known))} "
          f"vs without {_pct(sum(p['pnl'] > 0 for p in known if not p['stop']), sum(not p['stop'] for p in known))}",
          f"- Worst 1% of positions: {_pct(sum(not p['stop'] for p in kw), len(kw))} had no stop",
          f"- Per wallet: unstopped = median {_med(r['nostop_rate'] for r in s2) * 100:.0f}% of positions, "
          f"{_med(r['nostop_loss_share'] for r in s2) * 100:.0f}% of losses"]
    liq = [p for p in allp if p["liq"]]
    L += ["\n## Liquidations",
          f"- Wallets liquidated at least once: {_pct(sum(r['liqs'] > 0 for r in rows), len(rows))}; liquidated positions: {len(liq)}",
          f"- Share of all losses: {_pct(_loss(liq), _loss(allp))}; among liquidated wallets, median "
          f"{_med(r['liq_loss_share'] for r in rows if r['liqs']) * 100:.0f}% of their losses",
          f"- Liquidated positions averaged down: {_pct(sum(p['ad'] for p in liq), len(liq))}; "
          f"no stop (where known): {_pct(sum(p['stop'] is False for p in liq), sum(p['stop'] is not None for p in liq))}"]
    L += ["\n## Concentration",
          f"- Per wallet: worst 5% of positions = median {_med(r['worst5_loss_share'] for r in rows if r['worst5_loss_share'] is not None) * 100:.0f}% of losses",
          f"- Worst 1% of positions flagged by at least one rule (averaged down, liquidated, or no stop): "
          f"{_pct(sum(p['ad'] or p['liq'] or p['stop'] is False for p in worst), len(worst))}"]

    L.append("\n## Wallet outcomes by habit (90-day net P&L after fees)")
    for name, sel in [("Rarely average down (<5%)", lambda r: r["ad_rate"] < 0.05),
                      ("Sometimes (5-20%)", lambda r: 0.05 <= r["ad_rate"] < 0.20),
                      ("Often (>=20%)", lambda r: r["ad_rate"] >= 0.20),
                      ("Stops on most positions (>=50%)", lambda r: r["stop_rate"] is not None and r["stop_rate"] >= 0.5),
                      ("Stops rarely (<10%)", lambda r: r["stop_rate"] is not None and r["stop_rate"] < 0.10),
                      ("All wallets", lambda r: True)]:
        g = [r for r in rows if sel(r)]
        if g:
            L.append(f"- {name}: {len(g)} wallets, profitable {_pct(sum(r['pnl'] > 0 for r in g), len(g))}, "
                     f"median ${_med(r['pnl'] for r in g):,.0f}")
    short = [p for p in allp if p["hold_h"] < 24]
    L += ["\n## Holding time",
          f"- Closed within 24h: {_pct(len(short), len(allp))}; win rate <24h {_pct(sum(p['pnl'] > 0 for p in short), len(short))} "
          f"vs >=24h {_pct(sum(p['pnl'] > 0 for p in allp if p['hold_h'] >= 24), len(allp) - len(short))}"]

    text = "\n".join(L) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("fetch", "analyze"):
        sys.exit(__doc__)
    fetch() if cmd == "fetch" else analyze()
