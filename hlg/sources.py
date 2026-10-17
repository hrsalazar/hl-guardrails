"""Free market data beyond Hyperliquid: one bar format from many exchanges, plus derivatives and on-chain series.

    python -m hlg.sources inventory           # probe every source x the 15 coins; writes docs/research/data-inventory.md
    python -m hlg.sources bars bitfinex BTC 1d   # fetch one series into the cache and print its span

Research only: nothing live reads this module. Every source here is free and needs no key (checked 2026-10-16).
Bars come back as a DataFrame indexed by the bar's UTC open time, columns o h l c v (v in the base coin),
completed bars only, cached in miner_cache/sources/ for a day.

  perps   binance, bybit, okx, gate, bitget, deribit (BTC/ETH and a few linear perps), hyperliquid
  spot    binance_spot (2017+), coinbase (2015+), bitstamp (2011+), bitfinex (2013+)
  other   funding history (binance, bybit, okx, gate), open interest (bybit, daily since 2021),
          CoinMetrics community metrics, blockchain.com charts, DefiLlama stablecoins / chain TVL,
          alternative.me Fear & Greed

Not here, and why: CryptoCompare and CoinPaprika history need a key or a paid plan; Kraken serves only the
last 720 bars; KuCoin and MEXC futures keep only recent history; Gate's open-interest history is capped at
180 days; Coinalyze (liquidations) and Glassnode need keys.
"""
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from .backtest import DEF
from .common import log, setup_logging

UA = {"User-Agent": "Mozilla/5.0 (hl-guardrails research)"}
MS = {"1d": 86_400_000, "4h": 14_400_000}
START = "2010-01-01"
CACHE = Path(DEF["cache_dir"]) / "sources"
_last = [0.0]


def _get(url, params=None, pause=0.2, get=None):
    """GET with a polite gap between calls and retries on 429/5xx; returns parsed JSON or None."""
    get = get or requests.get
    for attempt in range(4):
        wait = pause - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        try:
            r = get(url, params=params, headers=UA, timeout=30)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def _frame(rows, unit="ms"):
    """[(t, o, h, l, c, v), ...] -> the standard frame, deduplicated, sorted."""
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    df = df.dropna(subset=["t", "o", "h", "l", "c"])
    df.index = pd.to_datetime(df.t.astype("int64"), unit=unit, utc=True)
    df = df.drop(columns="t").astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df if len(df) else None


def _complete(df, tf):
    return df[df.index + pd.Timedelta(milliseconds=MS[tf]) <= pd.Timestamp.now(tz="UTC")] if df is not None else None


def _ms(ts):
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)


def _now_ms():
    return int(time.time() * 1000)


# ------------------------------------------------------------------------------------------- adapters
# Each takes (coin, tf, start_ms, get) and returns raw rows [(t_ms, o, h, l, c, v)], oldest first or any order.

def _binance(coin, tf, start, get, base="https://fapi.binance.com/fapi/v1/klines", sym="{c}USDT"):
    rows, t = [], start
    while t < _now_ms():
        b = _get(base, {"symbol": sym.format(c=coin), "interval": tf, "startTime": t, "limit": 1000}, 0.15, get)
        if not b:
            break
        rows += [x[:6] for x in b]
        if len(b) < 1000:
            break
        t = b[-1][0] + MS[tf]
    return rows


def _binance_spot(coin, tf, start, get):
    return _binance(coin, tf, start, get, "https://api.binance.com/api/v3/klines")


def _bybit(coin, tf, start, get):
    rows, end = [], _now_ms()
    while end > start:
        js = _get("https://api.bybit.com/v5/market/kline", {"category": "linear", "symbol": f"{coin}USDT",
                  "interval": {"1d": "D", "4h": "240"}[tf], "start": start, "end": end, "limit": 1000}, 0.15, get)
        lst = ((js or {}).get("result") or {}).get("list") or []
        if not lst:
            break
        rows += [x[:6] for x in lst]
        end = min(int(x[0]) for x in lst) - 1
        if len(lst) < 1000:
            break
    return rows


def _okx(coin, tf, start, get):
    rows, after = [], _now_ms()
    while after > start:
        js = _get("https://www.okx.com/api/v5/market/history-candles", {"instId": f"{coin}-USDT-SWAP",
                  "bar": {"1d": "1Dutc", "4h": "4H"}[tf], "after": after, "limit": 100}, 0.12, get)
        d = (js or {}).get("data") or []
        if not d:
            break
        rows += [x[:6] for x in d]
        after = min(int(x[0]) for x in d)
        if len(d) < 100:
            break
    return rows


def _gate(coin, tf, start, get):
    rows, t, step = [], start // 1000, MS[tf] // 1000
    while t < _now_ms() // 1000:
        to = min(t + 1999 * step, _now_ms() // 1000)
        d = _get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", {"contract": f"{coin}_USDT", "interval": tf,
                 "from": t, "to": to}, 0.15, get)
        if isinstance(d, list) and d:
            rows += [(int(x["t"]) * 1000, x["o"], x["h"], x["l"], x["c"], x.get("v", 0)) for x in d]
        elif d is None:
            break
        t = to + step
    return rows


def _bitget(coin, tf, start, get):
    rows, end = [], _now_ms()
    while end > start:
        js = _get("https://api.bitget.com/api/v2/mix/market/history-candles", {"symbol": f"{coin}USDT", "productType": "usdt-futures",
                  "granularity": {"1d": "1D", "4h": "4H"}[tf], "endTime": end, "limit": 200}, 0.12, get)
        d = (js or {}).get("data") or []
        if not d:
            break
        rows += [x[:6] for x in d]
        nxt = min(int(x[0]) for x in d) - 1
        if nxt >= end:
            break
        end = nxt                      # pages are capped at ~90 days, so keep paging until empty
    return rows


DERIBIT = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL", "SOL": "SOL_USDC-PERPETUAL", "XRP": "XRP_USDC-PERPETUAL",
           "DOGE": "DOGE_USDC-PERPETUAL", "AVAX": "AVAX_USDC-PERPETUAL", "LINK": "LINK_USDC-PERPETUAL"}


def _deribit(coin, tf, start, get):
    inst = DERIBIT.get(coin)
    if not inst:
        return []
    rows, t, span = [], start, 4000 * MS[tf]
    while t < _now_ms():
        js = _get("https://www.deribit.com/api/v2/public/get_tradingview_chart_data", {"instrument_name": inst,
                  "resolution": {"1d": "1D", "4h": "240"}[tf], "start_timestamp": t, "end_timestamp": t + span}, 0.15, get)
        r = (js or {}).get("result") or {}
        if r.get("status") == "ok" and r.get("ticks"):
            rows += list(zip(r["ticks"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
        elif js is None:
            break
        t += span
    return rows


def _coinbase(coin, tf, start, get):
    if tf != "1d":
        return []                        # Coinbase has 6h, not 4h
    rows, t = [], start // 1000
    while t < _now_ms() // 1000:
        to = t + 299 * 86400
        d = _get(f"https://api.exchange.coinbase.com/products/{coin}-USD/candles", {"granularity": 86400,
                 "start": pd.Timestamp(t, unit="s", tz="UTC").isoformat(), "end": pd.Timestamp(to, unit="s", tz="UTC").isoformat()}, 0.35, get)
        if d is None:
            break
        rows += [(x[0] * 1000, x[3], x[2], x[1], x[4], x[5]) for x in d]   # [time, low, high, open, close, volume]
        t = to + 86400
    return rows


def _bitstamp(coin, tf, start, get):
    rows, t, step = [], start // 1000, MS[tf] // 1000
    while t < _now_ms() // 1000:
        js = _get(f"https://www.bitstamp.net/api/v2/ohlc/{coin.lower()}usd/", {"step": step, "limit": 1000, "start": t}, 0.2, get)
        d = ((js or {}).get("data") or {}).get("ohlc") or []
        if js is None:
            break
        if not d:                      # nothing yet at t (listed later): jump a page forward
            t += 1000 * step
            continue
        rows += [(int(x["timestamp"]) * 1000, x["open"], x["high"], x["low"], x["close"], x["volume"]) for x in d]
        nxt = int(d[-1]["timestamp"]) + step
        if nxt <= t:
            break
        t = nxt
    return rows


def _bitfinex(coin, tf, start, get):
    for sym in (f"t{coin}USD", f"t{coin}:USD"):
        rows, t = [], start
        while t < _now_ms():
            d = _get(f"https://api-pub.bitfinex.com/v2/candles/trade:{ {'1d': '1D', '4h': '4h'}[tf] }:{sym}/hist",
                     {"start": t, "limit": 10000, "sort": 1}, 1.2, get)
            if not d or not isinstance(d, list):
                break
            rows += [(x[0], x[1], x[3], x[4], x[2], x[5]) for x in d]          # [mts, open, close, high, low, volume]
            if len(d) < 10000:
                break
            t = d[-1][0] + MS[tf]
        if rows:
            return rows
    return []


def _hyperliquid(coin, tf, start, get):
    from .backtest import bars
    df = bars(coin, Path(DEF["cache_dir"]), tf)
    if df is None:
        return []
    return [(int(t.timestamp() * 1000), r.o, r.h, r.l, r.c, r.v) for t, r in df.iterrows()]


ADAPTERS = {"hyperliquid": _hyperliquid, "binance": _binance, "bybit": _bybit, "okx": _okx, "gate": _gate,
            "bitget": _bitget, "deribit": _deribit, "binance_spot": _binance_spot, "coinbase": _coinbase,
            "bitstamp": _bitstamp, "bitfinex": _bitfinex}
KIND = {"hyperliquid": "perp", "binance": "perp", "bybit": "perp", "okx": "perp", "gate": "perp", "bitget": "perp",
        "deribit": "perp", "binance_spot": "spot", "coinbase": "spot", "bitstamp": "spot", "bitfinex": "spot"}


def bars(source, coin, tf="1d", start=START, cache=None, get=None):
    """Standard bars for one coin from one source, or None if the source doesn't list it."""
    cache = Path(cache or CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / f"{source}_{tf}_{coin}.csv"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        if p.stat().st_size < 10:
            return None
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df if len(df) else None
    try:
        rows = ADAPTERS[source](coin, tf, _ms(start), get)
    except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
        log.warning("%s %s %s: %s", source, coin, tf, type(e).__name__)
        rows = []
    df = _complete(_frame(rows), tf)
    if df is None or not len(df):
        p.write_text("")
        return None
    df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    df.to_csv(p)
    return df


def longest(coin, tf="1d", sources=None, kind=None, **kw):
    """The source with the longest history for a coin -> (source, frame), or (None, None)."""
    best = (None, None)
    for s in sources or ADAPTERS:
        if kind and KIND[s] != kind:
            continue
        df = bars(s, coin, tf, **kw)
        if df is not None and (best[1] is None or df.index[0] < best[1].index[0]):
            best = (s, df)
    return best


# ------------------------------------------------------------------------------ derivatives / other series
def funding(source, coin, start=START, get=None):
    """Funding-rate history (fraction per funding interval) as a Series indexed by time, or None."""
    rows = []
    if source == "binance":
        t = _ms(start)
        while True:
            b = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": f"{coin}USDT", "startTime": t, "limit": 1000}, 0.15, get)
            if not b:
                break
            rows += [(x["fundingTime"], float(x["fundingRate"])) for x in b]
            if len(b) < 1000:
                break
            t = b[-1]["fundingTime"] + 1
    elif source == "bybit":
        end = _now_ms()
        while end > _ms(start):
            js = _get("https://api.bybit.com/v5/market/funding/history", {"category": "linear", "symbol": f"{coin}USDT",
                      "startTime": _ms(start), "endTime": end, "limit": 200}, 0.15, get)
            lst = ((js or {}).get("result") or {}).get("list") or []
            if not lst:
                break
            rows += [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in lst]
            end = min(int(x["fundingRateTimestamp"]) for x in lst) - 1
            if len(lst) < 200:
                break
    elif source == "okx":
        after = _now_ms()
        while True:
            js = _get("https://www.okx.com/api/v5/public/funding-rate-history", {"instId": f"{coin}-USDT-SWAP", "after": after, "limit": 100}, 0.2, get)
            d = (js or {}).get("data") or []
            if not d:
                break
            rows += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in d]
            after = min(int(x["fundingTime"]) for x in d)
    elif source == "gate":
        d = _get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate", {"contract": f"{coin}_USDT", "limit": 1000}, 0.15, get)
        rows = [(int(x["t"]) * 1000, float(x["r"])) for x in d or []]
    if not rows:
        return None
    s = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC"): r for t, r in rows}).sort_index()
    return s[~s.index.duplicated()]


def open_interest(coin, start="2020-01-01", get=None):
    """Bybit daily open interest (contracts), the longest free history found (2021+)."""
    rows, end = [], _now_ms()
    while end > _ms(start):
        js = _get("https://api.bybit.com/v5/market/open-interest", {"category": "linear", "symbol": f"{coin}USDT",
                  "intervalTime": "1d", "startTime": _ms(start), "endTime": end, "limit": 200}, 0.2, get)
        lst = ((js or {}).get("result") or {}).get("list") or []
        if not lst:
            break
        rows += [(int(x["timestamp"]), float(x["openInterest"])) for x in lst]
        end = min(int(x["timestamp"]) for x in lst) - 1
        if len(lst) < 200:
            break
    if not rows:
        return None
    s = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC"): v for t, v in rows}).sort_index()
    return s[~s.index.duplicated()]


def coinmetrics(asset, metrics, start=START, get=None):
    """CoinMetrics community API (free, no key): daily metrics for one asset as a DataFrame."""
    url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    params = {"assets": asset, "metrics": ",".join(metrics), "start_time": start[:10], "page_size": 10000, "frequency": "1d"}
    rows = []
    while url:
        js = _get(url, params, 0.7, get)
        if not js:
            break
        rows += js.get("data") or []
        url, params = js.get("next_page_url"), None
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.time, utc=True)
    return df.drop(columns=["asset", "time"]).apply(pd.to_numeric, errors="coerce")


def blockchain_chart(name, get=None):
    """blockchain.com charts (BTC network): e.g. hash-rate, n-transactions, miners-revenue, difficulty."""
    js = _get(f"https://api.blockchain.info/charts/{name}", {"timespan": "all", "format": "json", "sampled": "false"}, 0.3, get)
    if not js or not js.get("values"):
        return None
    return pd.Series({pd.Timestamp(v["x"], unit="s", tz="UTC"): v["y"] for v in js["values"]}).sort_index()


def defillama_stablecoins(get=None):
    d = _get("https://stablecoins.llama.fi/stablecoincharts/all", None, 0.3, get)
    if not d:
        return None
    return pd.Series({pd.Timestamp(int(x["date"]), unit="s", tz="UTC"): (x.get("totalCirculatingUSD") or {}).get("peggedUSD") for x in d}).sort_index()


def defillama_tvl(get=None):
    d = _get("https://api.llama.fi/v2/historicalChainTvl", None, 0.3, get)
    if not d:
        return None
    return pd.Series({pd.Timestamp(int(x["date"]), unit="s", tz="UTC"): x["tvl"] for x in d}).sort_index()


def fear_greed(get=None):
    js = _get("https://api.alternative.me/fng/", {"limit": 0, "format": "json"}, 0.3, get)
    if not js or not js.get("data"):
        return None
    return pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s", tz="UTC"): int(x["value"]) for x in js["data"]}).sort_index()


# ---------------------------------------------------------------------------------------- inventory
def inventory(coins=None, out_doc=Path("docs/research/data-inventory.md")):
    """Probe every bar source x coin (1d and 4h), plus the other series; write the inventory doc."""
    coins = coins or DEF["coins"]
    rows = []
    for coin in coins:
        for s in ADAPTERS:
            for tf in ("1d", "4h"):
                if s == "coinbase" and tf == "4h":
                    continue
                df = bars(s, coin, tf)
                rows.append({"coin": coin, "source": s, "kind": KIND[s], "tf": tf,
                             "first": df.index[0].date().isoformat() if df is not None else None, "bars": len(df) if df is not None else 0})
        log.info("inventory: %s done", coin, extra={"safe": True})
    inv = pd.DataFrame(rows)
    inv.to_csv(CACHE / "inventory.csv", index=False)
    other = {}
    for name, fn in (("Funding, Binance (BTC)", lambda: funding("binance", "BTC")), ("Funding, Bybit (BTC)", lambda: funding("bybit", "BTC")),
                     ("Funding, OKX (BTC)", lambda: funding("okx", "BTC")), ("Funding, Gate (BTC)", lambda: funding("gate", "BTC")),
                     ("Open interest, Bybit (BTC, daily)", lambda: open_interest("BTC")),
                     ("CoinMetrics BTC (price, cap, active addresses, tx count)", lambda: coinmetrics("btc", ["PriceUSD", "CapMrktCurUSD", "AdrActCnt", "TxCnt"])),
                     ("blockchain.com hash rate", lambda: blockchain_chart("hash-rate")),
                     ("DefiLlama stablecoin supply", defillama_stablecoins), ("DefiLlama total chain TVL", defillama_tvl),
                     ("Fear & Greed", fear_greed)):
        try:
            x = fn()
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", name, type(e).__name__)
            x = None
        other[name] = (x.index[0].date().isoformat(), len(x)) if x is not None and len(x) else (None, 0)

    d1 = inv[inv.tf == "1d"]
    first = d1.pivot(index="coin", columns="source", values="first").reindex(index=coins, columns=list(ADAPTERS))
    nbars = d1.pivot(index="coin", columns="source", values="bars").reindex(index=coins, columns=list(ADAPTERS))
    best = d1[d1.bars > 0].sort_values("first").groupby("coin").first().reindex(coins)
    hl = d1[d1.source == "hyperliquid"].set_index("coin").reindex(coins)
    d4 = inv[(inv.tf == "4h") & (inv.bars > 0)].sort_values("first").groupby("coin").first().reindex(coins)
    total_hl = int(inv[inv.source == "hyperliquid"].bars.sum())
    total = int(inv.bars.sum())
    L = ["# Free market data inventory\n",
         f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d} by `python -m hlg.sources inventory`: every free, keyless source probed live "
         "for the 15 backtest coins. Cached series live in the gitignored `miner_cache/sources/`. Aggregates only.\n",
         f"**Daily + 4h bars available: {total:,}** across {inv[inv.bars > 0].source.nunique()} sources, against "
         f"{total_hl:,} from Hyperliquid alone ({total / max(total_hl, 1):.1f}x).\n",
         "## Longest daily history per coin\n",
         "| coin | longest source | from | Hyperliquid from | gain (years) | longest 4h source | 4h from |\n|---|---|---|---|---:|---|---|"]
    for c in coins:
        b, h, f4 = best.loc[c], hl.loc[c], d4.loc[c]
        gain = ((pd.Timestamp(h["first"]) - pd.Timestamp(b["first"])).days / 365.25) if isinstance(h["first"], str) and isinstance(b["first"], str) else float("nan")
        L.append(f"| {c} | {b['source'] if isinstance(b['source'], str) else '—'} | {b['first'] or '—'} | {h['first'] or '—'} | "
                 f"{gain:.1f} | {f4['source'] if isinstance(f4['source'], str) else '—'} | {f4['first'] or '—'} |")
    L += ["\n## Daily bars: first date by source\n", first.fillna("—").to_markdown() + "\n",
          "## Daily bars: count by source\n", nbars.fillna(0).astype(int).to_markdown() + "\n",
          "## Other series (BTC where per-coin)\n", "| series | from | points |\n|---|---|---:|"]
    L += [f"| {k} | {v[0] or 'not available'} | {v[1]:,} |" for k, v in other.items()]
    L += ["\n## Not included\n",
          "- **Need a key or a paid plan:** CryptoCompare, CoinPaprika (history), Coinalyze (liquidations; free key), Glassnode, CoinGlass.",
          "- **Limited history:** Kraken (last 720 bars), KuCoin and MEXC futures (recent only), Gate open interest (180 days), Binance futures API ratios (30 days).",
          "- **Bulk archives, usable but heavy:** data.binance.vision (5-minute OI and long/short since 2020, one zip per coin per day), Hyperliquid's S3 archive (AWS requester-pays).",
          "- **Blocked from scripts:** Stooq (browser check)."]
    out_doc.parent.mkdir(parents=True, exist_ok=True)
    out_doc.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return inv, other


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "inventory":
        inventory()
    elif cmd == "bars" and len(sys.argv) >= 4:
        df = bars(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "1d")
        print("not listed" if df is None else f"{len(df)} bars, {df.index[0]} -> {df.index[-1]}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
