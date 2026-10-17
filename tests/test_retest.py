"""Binance retest (hlg.retest): windows and the bar arithmetic. No network."""
import numpy as np
import pandas as pd

from hlg import backtest as bt
from hlg import retest as rt


def test_window_cuts_by_entry_date_and_judge_reports_halves_and_bootstrap():
    days = pd.date_range("2020-01-01", periods=40, freq="7D", tz="UTC")
    T = pd.DataFrame({"start": days, "net": [10.0 if i % 3 else -5.0 for i in range(40)], "coin": "BTC"})
    C = pd.Series(np.linspace(1000, 1200, 40), index=days)
    assert len(rt.window(T, "2020-03-01", "2020-06-01")) == len(T[(T.start >= "2020-03-01") & (T.start < "2020-06-01")])
    j = rt.judge(T, C, bt.DEF, "2020-01-01", None)
    assert j["trades"] == 40 and j["pf"] == bt._pf_of(T.net.values) and j["boot_p5"] <= j["pf"]
    assert j["half1_pf"] > 1 and j["half2_pf"] > 1 and j["max_dd_pct"] == 0.0


def test_the_untouched_windows_are_the_pre_registered_ones():
    assert rt.R1 == ("2020-01-01", "2024-07-01") and rt.R2 == ("2020-01-01", "2021-04-01")
    assert pd.Timestamp(rt.R2[1]) <= pd.Timestamp(bt.HOLDOUT[0])          # stops where the spent holdout starts
