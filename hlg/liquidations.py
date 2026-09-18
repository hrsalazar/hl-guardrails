"""Recent liquidations from OKX.

Hyperliquid publishes none: there is no liquidations endpoint (`liquidations`,
`recentLiquidations`, `openInterestHistory` all return 422) and `recentTrades` gives the last ten
trades with no liquidation flag. OKX's public endpoint is free, needs no key, and sends
`Access-Control-Allow-Origin: *`, which also lets the dashboard fetch it directly when this path
returns nothing -- useful because FRED taught us that a third-party host can be unreachable from
a GitHub Actions runner while working fine everywhere else.

Three limits worth knowing before reading the numbers:
  - it is a handful of RECENT events, not a 24h total, so this is long-vs-short pressure right
    now and must never be labelled "24h liquidations";
  - `uly` is mandatory, so it costs one request per coin -- callers cap the list;
  - it is OKX's book, not Hyperliquid's, and the coin has to exist there (HYPE, PENDLE and ZEC
    generally do not). Misses are skipped silently rather than reported as zero, because zero
    liquidations and "not listed on OKX" are different facts.
"""
import time

import requests

from .common import log

OKX = "https://www.okx.com/api/v5/public/liquidation-orders"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hl-guardrails/0.1)"}
TIMEOUT = (3, 5)  # (connect, read): fail fast, this runs on the 15-minute alert path


def parse(payload, coin):
    """OKX response -> one summary row, or None when the coin has no recent liquidations.

    Split from the fetch so it can be tested against a canned payload without a network call."""
    data = (payload or {}).get("data") or []
    longs = shorts = 0.0
    events = 0
    for block in data:
        for d in block.get("details") or []:
            try:
                usd = float(d["sz"]) * float(d["bkPx"])
            except (KeyError, TypeError, ValueError):
                continue
            events += 1
            if d.get("posSide") == "long":
                longs += usd
            else:
                shorts += usd
    if not events:
        return None
    total = longs + shorts
    return {
        "coin": coin,
        "long_usd": longs,
        "short_usd": shorts,
        "events": events,
        # >0 means longs were the ones getting taken out (a flush lower), <0 the reverse
        "skew": (longs - shorts) / total if total else 0.0,
    }


def fetch(coins, budget_s=10.0):
    """Recent liquidations for `coins`, newest OKX window. Returns [] on any failure.

    The whole call is bounded: one request per coin, a hard per-request timeout and a wall-clock
    budget across all of them, so a slow or blocked OKX costs seconds and then gives up rather
    than delaying every alert on the run (which is precisely how FRED cost 300s a run)."""
    out, deadline = [], time.monotonic() + budget_s
    for coin in coins:
        if time.monotonic() > deadline:
            log.warning("liquidations: budget spent, %d coin(s) not checked", len(coins) - len(out))
            break
        try:
            r = requests.get(OKX, params={"instType": "SWAP", "state": "filled", "uly": f"{coin}-USD"},
                             headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            got = parse(r.json(), coin)
        except (requests.RequestException, ValueError) as e:
            log.info("liquidations %s unavailable: %s", coin, e)  # not listed on OKX, or blocked
            continue
        if got:
            out.append(got)
    return out
