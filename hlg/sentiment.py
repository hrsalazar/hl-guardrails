"""Crypto Fear & Greed index (alternative.me): daily, 0-100, free, no key, history back to 2018-02.

The index blends volatility, momentum/volume, social media, BTC dominance and search trends into
one reading of crowd mood. The popular reading is contrarian ("be greedy when others are
fearful"), so it gets the same treatment as every other backdrop here: shown as context on the
dashboard, and tested as an entry filter against random subsetting before it is allowed near a
rule (`python -m hlg.backtest --sentiment-study`, README "Sentiment").

The filter thresholds are the index's own published bands (extreme fear <= 24, extreme greed
>= 76), not values tuned on this sample.

Lookahead: a day's value is published around 00:00 UTC for that day. The same one-day shift as
hlg.macro and hlg.stablecoin applies anyway, so a filter reading row T sees a value published no
later than T-1 -- conservative, and identical across every backdrop.
"""
import time
from pathlib import Path

import pandas as pd
import requests

from .common import log

URL = "https://api.alternative.me/fng/"
CACHE_TTL_S = 12 * 3600
H = 3_600_000
BANDS = [(24, "Extreme fear"), (46, "Fear"), (54, "Neutral"), (75, "Greed"), (100, "Extreme greed")]


def band(v):
    return next(name for hi, name in BANDS if v <= hi)


def parse(js):
    """API payload -> int Series indexed by UTC day, oldest first."""
    rows = (js or {}).get("data") or []
    idx = pd.DatetimeIndex(pd.to_datetime([int(r["timestamp"]) for r in rows], unit="s", utc=True)).floor("D")
    s = pd.Series([int(r["value"]) for r in rows], index=idx, name="fng", dtype="float64")
    return s.sort_index().groupby(level=0).last()


def fetch(cache_dir="miner_cache", get=requests.get):
    p = Path(cache_dir) / "fng_all.json"
    if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
        import json

        return parse(json.loads(p.read_text()))
    r = get(URL, params={"limit": 0, "format": "json"}, timeout=20)
    r.raise_for_status()
    p.parent.mkdir(exist_ok=True)
    p.write_text(r.text)
    return parse(r.json())


def frame(cache_dir="miner_cache", end=None):
    """Daily UTC calendar, forward-filled, shifted a day (see module docstring)."""
    try:
        s = fetch(cache_dir)
    except Exception as e:  # noqa: BLE001 - research input; degrade, never crash
        log.error("fear & greed fetch failed: %s", e)
        return pd.DataFrame()
    if s.empty:
        return pd.DataFrame()
    import datetime as dt

    end = pd.Timestamp(end or dt.datetime.now(dt.timezone.utc).date())
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    idx = pd.date_range(s.index.min(), end, freq="D")
    return s.reindex(idx).ffill().to_frame().shift(1)


def features(df):
    """fng level plus the two published-band flags, nullable so a missing day stays unknown
    (see hlg.macro.features for why a NaN must not read as a confident False)."""
    if df.empty:
        return df
    f = pd.DataFrame(index=df.index)
    f["fng"] = df.fng
    f["fng_extreme_greed"] = (df.fng >= 76).astype("boolean").mask(df.fng.isna())
    f["fng_extreme_fear"] = (df.fng <= 24).astype("boolean").mask(df.fng.isna())
    return f


def refresh(state, now_ms, hours=6, days=90, get=requests.get):
    """Live reading for the dashboard and the daily digest, kept in state. Fail-soft: a failed
    fetch keeps the last reading (its age is shown) and retries in an hour."""
    cur = state.get("fng") if state is not None else None
    if isinstance(cur, dict) and now_ms - cur.get("t", 0) < hours * H:
        return cur
    try:
        r = get(URL, params={"limit": days, "format": "json"}, timeout=10)
        r.raise_for_status()
        s = parse(r.json())
        if s.empty:
            raise ValueError("empty series")
        v = int(s.iloc[-1])
        cur = {"t": now_ms, "value": v, "label": band(v), "as_of": int(s.index[-1].timestamp() * 1000),
               "series": [[int(t.timestamp() * 1000), int(x)] for t, x in s.items()]}
    except Exception as e:  # noqa: BLE001
        log.error("fear & greed refresh failed: %s", e)
        if isinstance(cur, dict):
            cur = {**cur, "t": now_ms - (hours - 1) * H}
        else:
            return None
    state.set("fng", cur)
    return cur
