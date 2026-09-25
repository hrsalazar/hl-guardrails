"""hlg.backtest.regime_states: the market-state buckets of docs/research/regime-study.md.

Synthetic series only; no network.
"""
import numpy as np
import pandas as pd

from hlg import backtest


def _btc(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({"c": closes}, index=idx)


def _frames(idx, hy, spx, fng):
    m = pd.DataFrame({"hy_stress": pd.array(hy, dtype="boolean"), "spx_bull": pd.array(spx, dtype="boolean")}, index=idx)
    s = pd.DataFrame({"fng": fng}, index=idx, dtype="float64")
    return m, s


def test_trend_uses_the_previous_close_and_waits_for_the_200_day_ema():
    b = _btc(np.r_[np.full(250, 100.0), np.full(10, 200.0)])        # jumps above its EMA on day 250
    S = backtest.regime_states(b, None, None)
    assert S.trend.iloc[:201].isna().all()                             # warm-up, then the one-day shift
    assert S.trend.iloc[250] != "btc_up"                               # the jump's own day isn't known yet
    assert S.trend.iloc[251] == "btc_up"


def test_macro_and_crowd_buckets_and_the_composite():
    b = _btc(np.r_[np.linspace(100, 50, 205), np.full(4, 50.0)])     # falling: btc_down once warmed up
    idx = b.index
    n = len(idx)
    hy = [False] * (n - 4) + [False, True, True, None]
    spx = [True] * (n - 4) + [True, False, True, True]
    fng = [50.0] * (n - 4) + [80, 20, 50, np.nan]
    m, s = _frames(idx, hy, spx, fng)
    S = backtest.regime_states(b, m, s).iloc[-4:]
    assert list(S.macro.iloc[:3]) == ["macro_on", "macro_off", "macro_mixed"]
    assert list(S.crowd.iloc[:3]) == ["greed", "fear", "neutral"] and pd.isna(S.crowd.iloc[3])
    # legs: [down, on, greed] -> 1 off, 2 on -> mixed; [down, off, fear] -> risk_off;
    # [down, mixed, neutral] -> mixed; [down, NA, NA] -> mixed
    assert list(S.composite) == ["mixed", "risk_off", "mixed", "mixed"]


def test_composite_is_risk_on_only_with_no_leg_off():
    b = _btc(np.r_[np.full(205, 100.0), np.linspace(100, 300, 5)])  # rising: btc_up
    idx = b.index
    n = len(idx)
    m, s = _frames(idx, [False] * n, [True] * n, [60.0] * (n - 1) + [30.0])
    S = backtest.regime_states(b, m, s)
    assert S.composite.iloc[-2] == "risk_on"                           # up, macro_on, greed
    assert S.composite.iloc[-1] == "mixed"                             # up, macro_on, fear: one leg off
