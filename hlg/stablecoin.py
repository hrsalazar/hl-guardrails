"""Total USD-pegged stablecoin market cap, from DefiLlama (no API key needed).

Why: the common trading claim is that stablecoin dominance and crypto prices move inversely --
money parks in stables when risk appetite falls, and stablecoin supply grows as risk-off capital
sits on the sidelines; supply shrinking (or growing more slowly than usual) is read as capital
rotating back into risk assets. hlg/backtest.py --stbl-study tests this directly (README
"Stablecoins").

DefiLlama's aggregated stablecoin series (`stablecoincharts/all`) is the data source, not a
reconstructed "total crypto market cap" ratio: CoinGecko's only free historical endpoint for that
is capped at roughly a year on the anonymous tier (verified: `days=1000` -> 401, `days=30` -> 200),
which would force either a thin one-year sample or an approximated denominator (circulating
supply back-computed from issuance schedules). DefiLlama gives the numerator alone -- total
circulating USD-stablecoin market cap -- as a clean daily series back to 2017, unauthenticated, in
one request. That is a real, exact quantity, not an approximation, and it is what the "supply
growth = dry powder" version of the hypothesis actually needs; the filters below compare it to its
own trend rather than fabricating a dominance percentage from an inexact total.

Lookahead: DefiLlama timestamps a day's close, but the API can be queried intraday, so the same
discipline as hlg.macro applies -- reindex onto the full calendar, forward-fill, then shift one
day. A filter reading row T sees stablecoin data published no later than T-1.
"""
import time
from pathlib import Path

import pandas as pd
import requests

from .common import log

URL = "https://stablecoins.llama.fi/stablecoincharts/all"
CACHE_TTL_S = 24 * 3600  # this prints once a day; the monitor's Actions cache is keyed by UTC date to match


def fetch(cache_dir="miner_cache"):
    """Total USD-pegged stablecoin market cap as a float Series indexed by UTC date."""
    p = Path(cache_dir) / "stablecoins_all.json"
    if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
        raw = p.read_text()
    else:
        r = requests.get(URL, timeout=20)
        r.raise_for_status()
        raw = r.text
        p.parent.mkdir(exist_ok=True)
        p.write_text(raw)
    rows = pd.read_json(pd.io.common.StringIO(raw))
    mcap = rows["totalCirculatingUSD"].apply(lambda d: (d or {}).get("peggedUSD"))
    idx = pd.DatetimeIndex(pd.to_datetime(rows["date"].astype("int64"), unit="s", utc=True)).floor("D")
    s = pd.Series(mcap.values, index=idx, name="stbl")
    return s.dropna().sort_index().groupby(level=0).last()  # one point per UTC day: the latest read wins


def frame(cache_dir="miner_cache", end=None):
    """Daily UTC calendar, forward-filled then shifted a day (see module docstring)."""
    try:
        s = fetch(cache_dir)
    except Exception as e:  # noqa: BLE001 - one source, called unattended; must degrade, never crash a run
        log.error("stablecoin fetch failed: %s", e)
        return pd.DataFrame()
    if s.empty:
        return pd.DataFrame()
    import datetime as dt

    end = pd.Timestamp(end or dt.datetime.now(dt.timezone.utc).date(), tz="UTC")
    idx = pd.date_range(s.index.min().floor("D"), end, freq="D", tz="UTC")
    df = s.reindex(idx).ffill().to_frame()
    return df.shift(1)


def features(df, chg_days=30):
    """Level plus relative-trend signals -- compared to the series' own recent history, never a
    tuned absolute cutoff, same discipline as hlg.macro.features. `stbl_chg` is the raw N-day %
    change (the literal "supply growth" reading); `stbl_below_trend` and `stbl_shrinking` are the
    two filter candidates -- one structural (vs its own EWM), one a sign test (net outflow)."""
    if df.empty:
        return df
    f = pd.DataFrame(index=df.index)
    f["stbl"] = df.stbl
    f["stbl_chg"] = df.stbl.pct_change(chg_days) * 100
    # nullable "boolean" dtype, not plain bool: a NaN comparison (before the series has chg_days of
    # history) evaluates to False, which a filter would then read as a confident, real "not
    # shrinking" rather than "unknown" -- see hlg.macro.features, which has the same fix and the
    # full reasoning. No trade in the current backtest window is actually affected (DefiLlama's
    # series starts 2017, years before any coin's own cached history), but the guarantee should
    # hold regardless of what data happens to line up today.
    f["stbl_below_trend"] = (f.stbl_chg < f.stbl_chg.ewm(span=50, adjust=False).mean()).astype("boolean").mask(f.stbl_chg.isna())
    f["stbl_shrinking"] = (f.stbl_chg < 0).astype("boolean").mask(f.stbl_chg.isna())
    return f


def main():
    from .common import setup_logging

    setup_logging()
    f = features(frame())
    if f.empty:
        print("no stablecoin data")
        return
    cur = f.iloc[-1]
    print(f"stablecoin backdrop as of {f.index[-1].date()} (shifted 1d, so published <= {(f.index[-1] - pd.Timedelta(days=1)).date()})")
    print(f"  total USD stablecoin mcap  ${cur.stbl:,.0f}")
    print(f"  30d change                 {cur.stbl_chg:+.2f}%  "
          f"({'below its own trend' if cur.stbl_below_trend else 'above its own trend'}, "
          f"{'net outflow' if cur.stbl_shrinking else 'net inflow'})")


if __name__ == "__main__":
    main()
