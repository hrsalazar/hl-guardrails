"""Capitulation long (hlg.capitulation): the signal is exactly the pre-registered conditions. No network."""
import numpy as np
import pandas as pd

from hlg import backtest as bt
from hlg import capitulation as cap

V = bt.VARIANTS["capitulation"]


def frame(n=60, last=None):
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    c = np.full(n, 100.0)
    df = pd.DataFrame({"o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1000.0}, index=idx)
    if last:
        for k, v in last.items():
            df.iloc[-1, df.columns.get_loc(k)] = v
    return bt.features(df, 1)


def flush(**over):
    # ATR ~2 before the bar: drop 5 (>= 1.5 ATR), range 8 (>= 2.5 ATR), 3x volume, closed at 0.5 of the range
    bar = {"o": 100.0, "h": 100.5, "l": 92.5, "c": 96.5, "v": 3000.0} | over
    return frame(last=bar).iloc[-1]


def test_a_flush_that_closed_off_its_low_is_a_signal_with_stop_and_target():
    k = flush()
    side, stop, tgt = bt.signal(k, V)
    assert side == "L" and abs(stop - (92.5 - 0.5 * k.atr_prev)) < 1e-9
    assert abs(tgt - (96.5 + 1.5 * (96.5 - stop))) < 1e-9


def test_each_condition_is_required():
    assert bt.signal(flush(v=1500.0), V) is None              # no volume spike
    assert bt.signal(flush(c=93.0), V) is None                # closed near the low (close location < 0.35)
    assert bt.signal(flush(l=97.0, c=98.5), V) is None        # neither a big drop nor a wide range


def test_the_simulator_takes_the_stop_before_the_target_and_times_out_after_three_days():
    idx = pd.date_range("2024-01-01", periods=6, freq="1D", tz="UTC")
    df = pd.DataFrame({"o": [100, 100, 100, 100, 100, 100], "h": [101, 120, 101, 101, 101, 101],
                       "l": [99, 80, 99, 99, 99, 99], "c": [100, 100, 100, 100, 100, 101]}, index=idx, dtype=float)
    assert cap.sim(df, 0, 95.0, 107.5, 0.0) == -1.0           # bar 1 hits both: the stop counts
    calm = df.assign(h=101.0, l=99.0)
    assert cap.sim(calm, 0, 95.0, 107.5, 0.0) == 0.0          # out at the close 3 days after entry
