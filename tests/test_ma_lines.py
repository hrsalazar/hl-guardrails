"""hlg.backtest.ma_lines / _above: 50-week and 200-day lines without lookahead (ma_study)."""
import numpy as np
import pandas as pd

from hlg import backtest as bt


def _daily(closes, start="2024-01-01"):                     # 2024-01-01 is a Monday
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({"c": np.asarray(closes, float)}, index=idx)


def test_weekly_line_is_known_at_mondays_open_from_sunday_closes_only():
    d = _daily(np.arange(1, 400 + 1))                         # close = day number
    L = bt.ma_lines(d)
    w = L["sma50w"].dropna()
    t = w.index[0]
    assert t.dayofweek == 0 and t.hour == 0                   # known at Monday 00:00
    sundays = d.c[d.index.dayofweek == 6]
    assert w.iloc[0] == sundays.iloc[:50].mean()              # the first 50 Sunday closes, nothing later


def test_emas_have_no_opinion_until_warm():
    L = bt.ma_lines(_daily(np.full(260, 100.0)))
    assert L["ema200d"].iloc[:199].isna().all() and L["ema200d"].notna().iloc[199]
    assert L["ema50w"].isna().all()                           # 37 weeks: never warm


def test_a_4h_bar_sees_yesterdays_daily_line_not_todays():
    d = _daily(np.r_[np.full(200, 100.0), [1000.0]])          # day 201 closes far higher
    L = {"sma200d": bt.ma_lines(d)["sma200d"]}
    day201 = d.index[-1]
    bars4h = pd.DataFrame({"c": [101.0, 101.0]}, index=[day201 + pd.Timedelta(hours=8), day201 + pd.Timedelta(hours=16)])
    flag = bt._above(bars4h, L, pd.Timedelta(hours=4))["sma200d"]
    assert list(flag) == [True, True]                          # vs 100 (known), not the 104.5 day 201 will print
    daily_flag = bt._above(d.iloc[[-1]], L, pd.Timedelta(days=1))["sma200d"]
    assert daily_flag.iloc[0]                                  # the 1d bar's own close is part of its line


def test_filters_let_trades_through_without_a_reading():
    k = pd.Series({"btc_above_sma50w": pd.NA, "coin_above_sma200d": False})
    assert bt.FILTERS["btc_above_sma50w"](k) is True
    assert not bt.FILTERS["coin_above_sma200d"](k)
