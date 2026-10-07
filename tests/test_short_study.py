"""hlg.backtest short-study plumbing: the btc_down filter and short-only filters
(docs/research/short-study.md). Synthetic bars only; no network."""
import numpy as np
import pandas as pd

from hlg import backtest


def _bar(c, lo20, hi20, ema20, ema50, btc_down):
    return pd.Series(dict(c=c, lo20=lo20, hi20=hi20, ema20=ema20, ema50=ema50, atr=1.0, rsi=50.0, btc_down=btc_down))


def test_btc_down_gates_shorts_and_reads_no_opinion_without_a_reading():
    V = backtest.SHORT_STUDY["short_bear"]
    k = _bar(c=90, lo20=95, hi20=110, ema20=100, ema50=105, btc_down=True)   # breakdown in a downtrend
    side, stop, _ = backtest.signal(k, V)
    assert side == "S" and stop == 92.0                                   # 2 ATR above the close
    assert backtest.signal(_bar(90, 95, 110, 100, 105, False), V) is None   # BTC above its 200-day EMA
    assert backtest.signal(_bar(90, 95, 110, 100, 105, np.nan), V)[0] == "S"


def test_short_filters_leave_the_long_side_alone():
    V = {**backtest.VARIANTS["breakout_both"], "short_filters": ["btc_down"]}
    up = _bar(c=120, lo20=95, hi20=110, ema20=105, ema50=100, btc_down=False)
    assert backtest.signal(up, V)[0] == "L"                               # longs ungated
    down = _bar(c=90, lo20=95, hi20=110, ema20=100, ema50=105, btc_down=False)
    assert backtest.signal(down, V) is None                               # shorts gated
    assert backtest.signal(_bar(90, 95, 110, 100, 105, True), V)[0] == "S"


def test_bear_context_maps_the_daily_state_onto_4h_bars(tmp_path, monkeypatch):
    days = pd.date_range("2024-01-01", periods=260, freq="D", tz="UTC")
    btc = pd.DataFrame({"c": np.r_[np.linspace(50, 100, 250), np.full(10, 50.0)]}, index=days)  # rising, falls on day 250
    monkeypatch.setattr(backtest, "bars", lambda coin, cache, iv="1d": btc)
    idx = pd.date_range(days[249], periods=18, freq="4h")
    data = backtest.bear_context({"X|4h": pd.DataFrame({"c": 1.0}, index=idx)}, tmp_path)
    flag = data["X|4h"].btc_down
    assert (flag.iloc[:12] == False).all()                                # noqa: E712 - fall not known on its own day
    assert (flag.iloc[12:] == True).all()                                 # noqa: E712 - from the next day's open


def test_own_halves_splits_at_the_median_entry_date():
    t = pd.DataFrame({"start": pd.date_range("2025-01-01", periods=4, freq="D"), "net": [-1.0, 2.0, 3.0, -1.0]})
    assert backtest._own_halves(t) == (2.0, 3.0)
