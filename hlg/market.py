"""Market structure from the asset contexts the scanner already fetches.

`metaAndAssetCtxs` returns a lot more than the funding rate the scanner reads: open interest,
24h notional volume, yesterday's price, the mark-vs-oracle premium and impact prices. All of it
arrives on every scan and was being discarded. These are pure dict->dict transforms over that
payload -- no client, no network, nothing to time out.

None of this gates a rule. It is context for the dashboard's Flow and Macro sections, in the same
spirit as the macro backdrop: shown so you can read it, never wired into an entry decision.

Dead markets are the trap here. Hyperliquid lists TradFi instruments that nobody trades -- DXY,
VIX, CORN, WHEAT, URANIUM, NIFTY all sit at openInterest 0, dayNtlVlm 0 and markPx == prevDayPx,
so their "price" is a frozen number that looks live. DXY and VIX are exactly the two series a
macro panel most wants, which is how you end up publishing fiction. `tradfi_rows` filters them
out and reports how many it dropped, so an empty section is distinguishable from a broken one.
"""
MIN_VLM_USD = 1_000_000  # a TradFi listing below these is untraded, not merely quiet: its mark
MIN_OI_USD = 250_000     # price stops moving and the row becomes a frozen number that reads live


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def row(c, coin):
    """One coin's market structure. Missing fields degrade to 0/None rather than raising -- the
    perp and TradFi dexes do not return identical shapes and a new listing can be sparse."""
    mark = _f(c.get("markPx")) or _f(c.get("midPx")) or _f(c.get("oraclePx"))
    prev, oi = _f(c.get("prevDayPx")), _f(c.get("openInterest"))
    vlm, imp = _f(c.get("dayNtlVlm")), c.get("impactPxs") or []
    oi_usd = oi * mark
    spread_bps = None
    if len(imp) == 2 and mark:
        bid, ask = _f(imp[0]), _f(imp[1])
        if bid and ask:
            spread_bps = (ask - bid) / mark * 10_000
    return {
        "coin": coin,
        "px": mark,
        "chg24h_pct": (mark / prev - 1) * 100 if prev else None,
        "vlm24h": vlm,
        "oi_usd": oi_usd,
        "vol_oi": vlm / oi_usd if oi_usd else None,  # turnover: how fast the open book churns
        "premium_bps": _f(c.get("premium")) * 10_000,  # mark over oracle = crowded side paying up
        "spread_bps": spread_bps,
        "funding_apr": _f(c.get("funding")) * 24 * 365 * 100,
        "stale": bool(prev) and mark == prev,
    }


def ctx_rows(ctx, coins):
    """Market structure for the coins actually being scanned. Deliberately not the whole 234-coin
    universe: this tool is scoped to scanner.coins, and a universe-wide leaderboard is a different
    product that would also triple the alerts.json the browser refetches every 5 minutes."""
    return [row(ctx[c], c) for c in coins if c in ctx]


def tradfi_rows(ctx, coins):
    """TradFi instruments with the dead ones removed. Returns (rows, n_dropped) -- the count is
    shown in the UI so "nothing here" never gets confused with "this broke"."""
    kept, dropped = [], 0
    for c in coins:
        if c not in ctx:
            dropped += 1
            continue
        r = row(ctx[c], c)
        if r["stale"] or r["vlm24h"] < MIN_VLM_USD or r["oi_usd"] < MIN_OI_USD:
            dropped += 1
            continue
        kept.append(r)
    kept.sort(key=lambda r: -r["vlm24h"])
    return kept, dropped
