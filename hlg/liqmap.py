"""Liquidation levels on Hyperliquid, from the positions of its largest and most active accounts.

Where would leveraged positions be force-closed? Aggregators such as CoinGlass *estimate* this for
the big exchanges from open interest and assumed leverage, because those exchanges publish no
positions. Hyperliquid is on-chain: every account's positions, with the exchange's own
`liquidationPx`, are public. So this reads them directly for the largest and the most active accounts on
the leaderboard and groups them per coin:

  above the price   short positions' liquidation prices -> forced BUYING if price gets there
  below the price   long positions' liquidation prices  -> forced SELLING if price gets there

The common reading is that big clusters act as magnets (price is drawn to where forced orders sit,
and stalls once they are cleared). That is a hypothesis, not a finding: this is context only, and
the signal journal records the clusters at every breakout so it can be tested on live signals.

Coverage is partial by design -- a few hundred accounts, not every wallet -- and is reported as a
share of each coin's open interest. Positions with no liquidation price (enough collateral that no
price liquidates them) are skipped. Only per-coin aggregates are kept; no address is stored with its
positions.
"""
import concurrent.futures as cf
import time

import requests

from .common import API, log

LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DEFAULTS = dict(enabled=True, accounts_by_value=200, accounts_by_volume=400, refresh_hours=4, band_pct=1.0,
                range_pct=15.0, threads=4, budget_s=60)


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("liqmap") or {})}


def _week_vlm(r):
    for w, v in r.get("windowPerformances") or []:
        if w == "week":
            return float((v or {}).get("vlm") or 0)
    return 0.0


def top_accounts(by_value, by_volume, get=requests.get):
    """The largest accounts by value plus the most active by weekly volume, deduplicated.

    Measured on the live leaderboard: the largest accounts alone mostly run low leverage, so their
    liquidation prices sit far from the price or do not exist -- 400 of them put $10M within 5% of
    BTC and nothing near ETH. The 400 most active by weekly volume put $131M / $85M either side of
    BTC and $40M / $131M around ETH. The leaderboard is a ~40 MB download (4-70s), so callers
    cache this for a day."""
    rows = get(LEADERBOARD, timeout=120).json()["leaderboardRows"]
    val = sorted(rows, key=lambda r: -float(r.get("accountValue") or 0))[:by_value]
    act = sorted(rows, key=lambda r: -_week_vlm(r))[:by_volume]
    return list(dict.fromkeys(r["ethAddress"] for r in val + act))


def _clearinghouse(session, addr, retries=3):
    for i in range(retries):
        r = session.post(f"{API}/info", json={"type": "clearinghouseState", "user": addr}, timeout=20)
        if r.status_code == 429:
            time.sleep(2 * 2 ** i)
            continue
        r.raise_for_status()
        return r.json()
    return None


def fetch_positions(addrs, threads=4, budget_s=60, fetch=None):
    """[(coin, szi, liquidationPx, positionValue)] across the accounts, within a time budget: a
    slow API costs coverage, never the monitor run."""
    session = requests.Session()
    fetch = fetch or (lambda a: _clearinghouse(session, a))
    out, t0, done = [], time.monotonic(), 0
    ex = cf.ThreadPoolExecutor(threads)
    try:
        futs = [ex.submit(fetch, a) for a in addrs]
        for f in futs:
            left = budget_s - (time.monotonic() - t0)
            if left <= 0:
                break
            try:
                st = f.result(timeout=left)
            except Exception:  # noqa: BLE001 - one bad account must not sink the map
                continue
            done += 1
            for ap in (st or {}).get("assetPositions", []):
                p = ap.get("position", {})
                if p.get("liquidationPx") is None:
                    continue
                try:
                    out.append((p["coin"], float(p["szi"]), float(p["liquidationPx"]), abs(float(p["positionValue"]))))
                except (KeyError, TypeError, ValueError):
                    continue
    finally:
        # past the budget, queued accounts are dropped rather than waited for
        ex.shutdown(wait=False, cancel_futures=True)
    return out, done


def build(positions, mids, coins, oi_usd, band_pct=1.0, range_pct=15.0):
    """Per coin: liquidation notional binned in band_pct steps of the current price, within
    range_pct either side. Bins keep absolute prices so distances can be recomputed later against
    a newer price."""
    out = {}
    for coin in coins:
        px = mids.get(coin)
        if not px:
            continue
        bins, total = {}, 0.0
        for c, szi, liq, val in positions:
            if c != coin:
                continue
            total += val
            d = (liq / px - 1) * 100
            # a short liquidates above the price, a long below; anything else is already past
            if (szi < 0 and d <= 0) or (szi > 0 and d >= 0) or abs(d) > range_pct:
                continue
            b = int(d // band_pct) if d > 0 else -int(-d // band_pct) - 1
            side = "short" if szi < 0 else "long"
            k = (side, b)
            usd, n, w = bins.get(k, (0.0, 0, 0.0))
            bins[k] = (usd + val, n + 1, w + liq * val)
        clusters = [{"side": s, "px": w / usd, "usd": usd, "n": n} for (s, _), (usd, n, w) in bins.items()]
        out[coin] = {"px": px, "clusters": sorted(clusters, key=lambda c: c["px"]),
                     "coverage": (total / oi_usd[coin]) if oi_usd.get(coin) else None}
    return out


def near(entry, px, near_pct=5.0):
    """Summary around the current price: short-liquidation notional within +near_pct above and
    long-liquidation notional within near_pct below, plus the largest cluster on each side."""
    if not entry or not px:
        return None
    res = {}
    for side, sign in (("short", 1), ("long", -1)):
        cs = [dict(c, pct=(c["px"] / px - 1) * 100) for c in entry["clusters"] if c["side"] == side]
        cs = [c for c in cs if 0 < sign * c["pct"] <= near_pct]
        top = max(cs, key=lambda c: c["usd"], default=None)
        res["above" if side == "short" else "below"] = {
            "usd": sum(c["usd"] for c in cs),
            "top_px": top["px"] if top else None, "top_pct": top["pct"] if top else None,
            "top_usd": top["usd"] if top else None}
    return res


def describe(summary, near_pct=5.0):
    """One line for an alert, e.g. 'shorts liq $4.2M within +5% (largest $1.9M at +3.1%)'."""
    if not summary:
        return None

    def m(x):
        return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"

    parts = []
    for key, who in (("above", "shorts"), ("below", "longs")):
        s = summary[key]
        if s["usd"] <= 0:
            parts.append(f"no {who} liq within {'+' if key == 'above' else '-'}{near_pct:g}%")
        else:
            parts.append(f"{who} liq {m(s['usd'])} within {'+' if key == 'above' else '-'}{near_pct:g}% "
                         f"(largest {m(s['top_usd'])} at {s['top_pct']:+.1f}%)")
    return " | ".join(parts)


def stale(state, now_ms, refresh_hours):
    m = state.get("liqmap") if state is not None else None
    return not isinstance(m, dict) or now_ms - m.get("t", 0) >= refresh_hours * 3_600_000


def refresh(state, mids, coins, oi_usd, L, now_ms, day, get=requests.get, fetch=None):
    """Rebuild the map (and, once a day, the account list) into state["liqmap"]."""
    t0 = time.monotonic()
    acc = state.get("liqmap_accounts") or {}
    want = L["accounts_by_value"] + L["accounts_by_volume"]
    if acc.get("day") != day or len(acc.get("addrs") or []) < want // 3:
        try:
            acc = {"day": day, "addrs": top_accounts(L["accounts_by_value"], L["accounts_by_volume"], get=get)}
            state.set("liqmap_accounts", acc)
        except Exception as e:  # noqa: BLE001
            log.error("liqmap: leaderboard failed: %s", e)
            if not acc.get("addrs"):
                return None
    positions, done = fetch_positions(acc["addrs"], L["threads"], L["budget_s"], fetch=fetch)
    m = {"t": now_ms, "accounts": done, "positions": len(positions),
         "coins": build(positions, mids, coins, oi_usd, L["band_pct"], L["range_pct"])}
    state.set("liqmap", m)
    log.info("liqmap: %d accounts, %d positions, %d coins in %.1fs", done, len(positions), len(m["coins"]),
             time.monotonic() - t0, extra={"safe": True})
    return m
