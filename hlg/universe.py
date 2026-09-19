"""Which coins the scanner watches: the liquid part of the market, refreshed once a day.

scanner.universe.mode = fixed -> scanner.coins, as before.
scanner.universe.mode = auto  -> the top_n perps by 30-day median daily notional volume, among
those listed >= min_age_days with open interest >= min_oi_usd and median volume >= min_vol_usd.

This is the same point-in-time rule `python -m hlg.backtest --universe-study` tests. The ranking is
a median of *daily* volumes, so it can only change when a daily candle closes: it is recomputed on
the first run of each UTC day and reused for the rest of it. Within a day the list only grows by
a coin you open a position in (so its breakout exits and alerts keep working).
"""
import datetime as dt
import statistics

DEFAULTS = dict(mode="fixed", top_n=30, min_vol_usd=10_000_000, min_oi_usd=5_000_000, min_age_days=60,
                include=[], exclude=[])


def settings(S):
    return {**DEFAULTS, **(S.get("universe") or {})}


def today(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d")


def prefilter(ctx, U):
    """Cheap first cut from the one asset-contexts call every run already makes: drop the other
    dexes, dead markets and anything far too thin. The bar is a third of min_vol_usd because the
    ranking uses a 30-day median -- a coin having one quiet day must not fall out before it is
    measured properly."""
    out = []
    for name, c in ctx.items():
        if ":" in name or name in U["exclude"]:
            continue
        try:
            vlm = float(c.get("dayNtlVlm") or 0)
            oi = float(c.get("openInterest") or 0) * float(c.get("markPx") or c.get("oraclePx") or 0)
        except (TypeError, ValueError):
            continue
        if vlm >= U["min_vol_usd"] / 3 and oi >= U["min_oi_usd"]:
            out.append(name)
    return out


def median_notional(bars_1d, window=30):
    """Median USD volume of the last `window` completed daily bars ([{"v", "c"}...], oldest first,
    live bar already dropped). None when the coin is younger than the window."""
    if len(bars_1d) < 20:
        return None
    return statistics.median(float(b["v"]) * float(b["c"]) for b in bars_1d[-window:])


def rank(liq, ages, U, open_coins=()):
    """liq: {coin: median notional or None}; ages: {coin: days of history}.
    Returns the coin list: top_n eligible by liquidity, then include/open-position coins appended."""
    ok = {c: v for c, v in liq.items()
          if v is not None and v >= U["min_vol_usd"] and ages.get(c, 0) >= U["min_age_days"] and c not in U["exclude"]}
    coins = sorted(ok, key=lambda c: -ok[c])[: U["top_n"]]
    for c in list(U["include"]) + sorted(open_coins):
        if c not in coins:
            coins.append(c)
    return coins


def current(state, day, open_coins=()):
    """Today's stored list (plus any coin held since it was made), or None if it must be rebuilt."""
    u = state.get("universe") if state is not None else None
    if not isinstance(u, dict) or u.get("day") != day or not isinstance(u.get("coins"), list) or not u["coins"]:
        return None
    coins = list(u["coins"])
    for c in sorted(open_coins):
        if c not in coins:
            coins.append(c)
    return coins
