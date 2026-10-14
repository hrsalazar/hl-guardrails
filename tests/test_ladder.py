"""Breakout-ladder research (hlg.ladder): first breaks, the 1h lead and the context gate."""
import numpy as np
import pandas as pd

from hlg import ladder as lg

H = pd.Timedelta(hours=1)


def bars(closes, start="2024-01-01", freq="1h", highs=None):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"o": c, "h": highs if highs is not None else c, "l": c - 0.5, "c": c, "v": 1.0}, index=idx)


def test_only_the_first_close_above_the_prior_high_counts():
    df = bars([10.0] * 20 + [11, 12, 13, 9, 14])
    b = lg.breaks(df)
    assert list(df.index[b.values]) == [df.index[20], df.index[24]]    # 21-22 continue the run; 24 is new


def test_a_break_uses_only_prior_bars():
    df = bars([10.0] * 20 + [10.0])
    assert not lg.breaks(df).any()                                      # equal to the high is not above


def _daily(up=True, broke=False, hi=200.0, days=60, start="2024-01-01"):
    idx = pd.date_range(start, periods=days, freq="1D", tz="UTC")
    return pd.DataFrame({"h": hi, "c": 100.0, "atr": 2.0, "up": up, "broke": broke}, index=idx)


def test_a_ladder_needs_a_1h_break_in_the_prior_24h_and_the_context():
    h1 = bars([100.0] * 24 * 40 + [101.0] * 4)                          # 1h break at the last 4h bar
    h4 = bars([100.0] * 6 * 40 + [101.0], freq="4h")
    t = h4.index[-1] + 4 * H
    ev = lg.ladders(h1, h4, _daily(), pd.Timestamp("2024-01-01", tz="UTC"))
    assert [e["t"] for e in ev] == [t] and ev[0]["inctx"] and ev[0]["a"] == 2.0
    assert not lg.ladders(h1, h4, _daily(up=False), pd.Timestamp("2024-01-01", tz="UTC"))[0]["inctx"]
    assert not lg.ladders(h1, h4, _daily(hi=100.5), pd.Timestamp("2024-01-01", tz="UTC"))[0]["inctx"]  # above 20d high
    flat = bars([100.0] * (24 * 40 + 4))                                # no 1h break: no ladder
    assert lg.ladders(flat, h4, _daily(), pd.Timestamp("2024-01-01", tz="UTC")) == []


def test_ladder_trades_skip_out_of_context_and_overlapping_events():
    h1 = bars(np.linspace(100, 120, 24 * 30))
    t0, t1 = h1.index[10], h1.index[20]
    ev = [dict(t=t0, inctx=True, a=1.0), dict(t=t1, inctx=True, a=1.0), dict(t=h1.index[5], inctx=False, a=1.0)]
    tr = lg.ladder_trades(h1, ev)
    assert [x["t"] for x in tr] == [t0] and tr[0]["stop"] == tr[0]["entry"] - 2.0   # second overlaps the first
