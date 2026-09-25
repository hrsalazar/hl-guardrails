"""Long-term moving-average lines (50-week, 200-day) from daily bars, without lookahead.

Shared by the backtest's `--ma-study` (docs/research/ma-lines-study.md) and the live signal journal,
which records where each new signal's coin stood against its own lines. The study's one lead, "a
coin above its own 50-week SMA breaks out better on 4h", is being tracked live rather than adopted.
"""
import numpy as np
import pandas as pd

from .scanner import ema

# the ma-lines study's four, then the bull market support band (docs/research/bmsb-study.md)
MA_LINES = ("sma50w", "ema50w", "sma200d", "ema200d", "sma20w", "ema21w", "bmsb")


def ma_lines(daily):
    """50-week and 200-day averages from daily bars (index = bar open, UTC), each indexed by the time
    its value became known: the close of the bar it includes. The week ends with Sunday's daily bar
    (closes Monday 00:00 UTC). An EMA reads NA until it has had `span` bars."""
    day_close = daily.c.copy()
    day_close.index = daily.index + pd.Timedelta(days=1)             # known at the daily close
    wk = daily.c.resample("W-SUN", label="right", closed="right").last().dropna()
    wk.index = wk.index + pd.Timedelta(days=1)                       # Sunday's bar closes Monday 00:00
    wk = wk[wk.index <= day_close.index[-1]]                          # drop a week still in progress
    ema_w = ema(wk, 50).where(np.arange(len(wk)) >= 49)
    ema_d = ema(day_close, 200).where(np.arange(len(day_close)) >= 199)
    sma20w, ema21w = wk.rolling(20).mean(), ema(wk, 21).where(np.arange(len(wk)) >= 20)
    return {"sma50w": wk.rolling(50).mean(), "ema50w": ema_w,
            "sma200d": day_close.rolling(200).mean(), "ema200d": ema_d,
            # bull market support band: "above the band" = above both lines, i.e. its upper edge
            "sma20w": sma20w, "ema21w": ema21w, "bmsb": pd.concat([sma20w, ema21w], axis=1).max(axis=1, skipna=False)}


def above(bars_df, lines, step):
    """Per bar of `bars_df` (any interval): is its close above each line, as of the bar's own close?"""
    close_t = bars_df.index + step
    out = {}
    for ln, ser in lines.items():
        s = ser.dropna()
        v = pd.Series(s.values, index=s.index).reindex(close_t, method="ffill") if len(s) else pd.Series(np.nan, index=close_t)
        flag = pd.Series(bars_df.c.values > v.values, index=bars_df.index).astype("boolean")
        out[ln] = flag.mask(pd.Series(v.values, index=bars_df.index).isna() | bars_df.c.isna())
    return out


def at_signal(daily, sig_t_ms, bar_ms, close, which=("sma50w", "sma200d")):
    """Live: the signal close against the coin's own lines as of the signal bar's close.
    {line: True | False | None}; None = not enough history (a young coin)."""
    if daily is None or len(daily) < 2:
        return {ln: None for ln in which}
    L = ma_lines(daily)
    t = pd.Timestamp(sig_t_ms + bar_ms, unit="ms", tz="UTC")
    out = {}
    for ln in which:
        s = L[ln].dropna()
        s = s[s.index <= t]
        out[ln] = bool(close > s.iloc[-1]) if len(s) else None
    return out
