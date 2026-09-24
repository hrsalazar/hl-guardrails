"""Early-entry research (hlg.reversal): swing points, bars, the simulator and both setups."""
import numpy as np
import pandas as pd

from hlg import reversal as rv

H = pd.Timedelta(hours=1)


def bars(closes, start="2024-01-10", freq="1h", lows=None, highs=None, opens=None):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"o": opens if opens is not None else c, "h": highs if highs is not None else c + 0.5,
                         "l": lows if lows is not None else c - 0.5, "c": c, "v": 1.0}, index=idx)


def test_a_swing_high_is_only_known_three_bars_after_it_forms():
    df = bars([1, 2, 3, 10, 3, 2, 1, 1, 1], highs=[1, 2, 3, 10, 3, 2, 1, 1, 1])
    s = rv.swings(df)
    assert s.sh1.iloc[:6].isna().all()          # the peak at bar 3 is unknowable until bar 6 closes
    assert s.sh1.iloc[6] == 10 and s.sh1_i.iloc[6] == 3


def test_resample_keeps_only_complete_bars():
    h1 = bars(np.arange(30.0))                   # 30 hours: 7 full 4h bars + 2 hours
    h4 = rv.resample(h1, "4h", 4)
    assert len(h4) == 7 and h4.c.iloc[0] == 3 and h4.o.iloc[1] == 4


def test_simulator_trails_and_fills_a_gap_at_the_open():
    closes = [100, 104, 108, 110, 108, 106, 104]
    h1 = bars(closes, highs=[100, 105, 109, 110, 108, 106, 104], lows=[99, 103, 107, 108, 106, 104, 103])
    r, t = rv.simulate(h1, 0, 100.0, 95.0, 1.0, trail=3.0)
    # highest high 110 -> stop 107 from bar 4 on; bar 4 low 106 <= 107, opened at 108 -> filled at 107
    fees = (rv.MAKER * 100 + rv.TAKER * 107) / 5
    assert abs(r - ((107 - 100) / 5 - fees)) < 1e-9 and t == h1.index[4]
    gap = bars([100, 90], opens=[100, 90], lows=[99, 89], highs=[101, 91])
    r, _ = rv.simulate(gap, 0, 100.0, 95.0, 1.0)
    assert abs(r - ((90 - 100) / 5 - (rv.MAKER * 100 + rv.TAKER * 90) / 5)) < 1e-9  # gapped through: the open


def uptrend_day(day, up=True, broke=False):
    return pd.DataFrame({"up": [up], "broke": [broke], "atr": [2.0]}, index=[pd.Timestamp(day, tz="UTC")])


def test_spring_fires_on_a_reclaim_within_two_bars_in_an_uptrend():
    closes = [100.0] * 25 + [98.0, 100.0] + [100.0] * 5
    lows = [99.5] * 25 + [97.0, 99.0] + [99.5] * 5
    h4 = bars(closes, freq="4h", lows=lows)
    d = uptrend_day("2024-01-13")                 # the bar at index 26 closes on 2024-01-14 12:00
    (e,) = rv.spring(h4, d)
    assert e["t"] == h4.index[26] + 4 * H and e["stop"] == 97.0
    assert rv.spring(h4, uptrend_day("2024-01-13", up=False)) == []


def test_cascade_needs_the_1h_change_of_character_then_a_4h_break():
    c = [100, 101, 102, 103, 104, 103, 102, 103, 104, 104.5, 106, 106.5, 107, 107.5, 107.8, 108] + [108] * 10
    h1 = bars(c, start="2024-01-10 00:00", lows=[x - 0.5 for x in c])
    h4 = rv.resample(h1, "4h", 4)
    n = len(h1)
    s1 = pd.DataFrame({"sh1": np.nan, "sh2": np.nan, "sl1": np.nan, "sl2": np.nan, "sh1_i": np.nan, "sl1_i": np.nan},
                      index=h1.index)
    s1.iloc[9:] = [105, 110, 100, 95, 5, 8]      # lower high 105, higher low 100 formed after it
    s4 = pd.DataFrame({"sh1": [107.0] * len(h4)}, index=h4.index)
    d = uptrend_day("2024-01-09")
    (e,) = rv.cascade(h1, h4, d, s1, s4)
    # 1h trigger at bar 10's close (106 > 105); the 4h bar 12:00-16:00 closes 108 > 107 after 106.5
    assert e["t"] == pd.Timestamp("2024-01-10 16:00", tz="UTC") and e["stop"] == 100
    assert rv.cascade(h1, h4, uptrend_day("2024-01-09", broke=True), s1, s4) == []   # 1d already broke out
    lost = h1.copy()
    lost.iloc[12, lost.columns.get_loc("l")] = 99.0                                   # higher low taken out
    assert rv.cascade(lost, h4, d, s1, s4) == []
    assert n == len(s1)
