"""Macro / TradFi backdrop from FRED (no API key needed).

Why bother: the breakout rule is pure price action on a single coin. Crypto trades as a
high-beta risk asset, so the same chart pattern is not worth the same in a credit-stress tape
as in a calm risk-on one. These are the cheapest honest proxies for that backdrop:

  hy   BAMLH0A0HYM2  US high-yield option-adjusted spread -- the cleanest single risk-appetite
                     gauge there is. Widening HY = money leaving the risk end of the curve.
  vix  VIXCLS        equity implied vol.
  spx  SP500         S&P 500 level (risk-on trend).
  dxy  DTWEXBGS      broad dollar index -- dollar strength is a structural crypto headwind.
  y10  DGS10         10y Treasury yield.

Lookahead: FRED publishes some of these with a one-day lag, and a daily HL candle stamped T
closes at 00:00 UTC on T+1. Rather than reason per-series about exactly what was knowable when,
every series is reindexed onto a full calendar, forward-filled (crypto trades weekends, FRED
does not) and then shifted one day. A filter reading row T therefore sees data published no
later than T-1 -- conservative on purpose: a lookahead bug would invalidate every number here.
"""
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import requests

from .common import log

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hl-guardrails/0.1)"}
SERIES = {"hy": "BAMLH0A0HYM2", "vix": "VIXCLS", "spx": "SP500", "dxy": "DTWEXBGS", "y10": "DGS10"}
CACHE_TTL_S = 24 * 3600  # these series print once a business day, so refetching sooner buys nothing
                         # -- and the monitor workflow keys its Actions cache by UTC date to match,
                         # so exactly one run a day pays FRED's ~60s-per-series latency.


def fetch(name, series_id, start="2020-01-01", cache_dir="miner_cache"):
    """One FRED series as a float Series indexed by observation date (NaN on holidays)."""
    p = Path(cache_dir) / f"macro_{series_id}.csv"
    if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
        raw = p.read_text()
    else:
        r = requests.get(FRED, params={"id": series_id, "cosd": start}, headers=UA, timeout=30)
        r.raise_for_status()
        raw = r.text
        p.parent.mkdir(exist_ok=True)
        p.write_text(raw)
    df = pd.read_csv(pd.io.common.StringIO(raw))
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    val_col = [c for c in df.columns if c != date_col][0]
    s = pd.Series(pd.to_numeric(df[val_col], errors="coerce").values,  # FRED writes "." for holidays
                  index=pd.to_datetime(df[date_col], utc=True), name=name)
    return s.dropna()


def frame(start="2020-01-01", cache_dir="miner_cache", end=None):
    """All series on one daily UTC calendar, forward-filled then shifted a day (see module docstring)."""
    cols = {}
    for name, sid in SERIES.items():
        try:
            cols[name] = fetch(name, sid, start, cache_dir)
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            log.error("macro %s (%s) failed: %s", name, sid, e)
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols)
    end = pd.Timestamp(end or dt.datetime.now(dt.timezone.utc).date(), tz="UTC")
    idx = pd.date_range(df.index.min(), end, freq="D", tz="UTC")
    return df.reindex(idx).ffill().shift(1)


def features(df):
    """Backdrop columns the filters read. All comparisons are vs the series' own trend, so they
    stay meaningful across levels that drift over years (a VIX of 17 means something different
    in 2021 than in 2026); nothing here is tuned to a threshold picked off this sample."""
    if df.empty:
        return df
    f = pd.DataFrame(index=df.index)
    f["hy"], f["vix"], f["spx"], f["dxy"], f["y10"] = df.hy, df.vix, df.spx, df.dxy, df.y10
    f["hy_stress"] = df.hy > df.hy.ewm(span=50, adjust=False).mean()      # credit spreads widening vs own trend
    f["vix_calm"] = df.vix < df.vix.ewm(span=50, adjust=False).mean()
    f["spx_bull"] = df.spx > df.spx.rolling(200).mean()
    f["dxy_headwind"] = df.dxy > df.dxy.ewm(span=50, adjust=False).mean()  # dollar strengthening
    f["risk_on"] = (~f.hy_stress) & f.spx_bull                             # both legs of the backdrop agree
    return f


def main():
    from .common import setup_logging

    setup_logging()
    f = features(frame())
    if f.empty:
        print("no macro data")
        return
    cur = f.iloc[-1]
    print(f"macro backdrop as of {f.index[-1].date()} (data shifted 1d, so published <= {(f.index[-1] - pd.Timedelta(days=1)).date()})")
    print(f"  HY spread {cur.hy:.2f}  ({'STRESS - widening vs 50d' if cur.hy_stress else 'calm'})")
    print(f"  VIX       {cur.vix:.2f}  ({'calm' if cur.vix_calm else 'elevated vs 50d'})")
    print(f"  SPX       {cur.spx:,.0f}  ({'above' if cur.spx_bull else 'BELOW'} 200d)")
    print(f"  DXY       {cur.dxy:.2f}  ({'headwind - dollar strengthening' if cur.dxy_headwind else 'neutral/weakening'})")
    print(f"  10y       {cur.y10:.2f}%")
    print(f"  -> risk_on = {bool(cur.risk_on)}")


if __name__ == "__main__":
    main()
