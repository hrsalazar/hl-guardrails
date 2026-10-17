"""hlg.deephistory: the source rule is mechanical (most bars inside the window). No network."""
import pandas as pd

from hlg import deephistory as dh


def frame(start, n):
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.DataFrame({"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}, index=idx)


def test_pick_takes_the_source_with_most_bars_inside_the_window(monkeypatch):
    have = {("bitfinex", "BCH"): frame("2017-08-01", 470),        # ends 2018-11: fewer bars in the window
            ("bitstamp", "BCH"): frame("2017-12-04", 2000),
            ("coinbase", "BCH"): frame("2017-12-20", 2000)}
    monkeypatch.setattr(dh, "bars", lambda src, al, tf: have.get((src, al)))
    label, df = dh.pick(["BCH"], "2017-01-01", "2020-01-01")
    assert label == "bitstamp:BCH"


def test_the_windows_end_before_2020_where_every_earlier_study_starts():
    assert dh.WIN_S[1] == dh.WIN_D[1] == "2020-01-01"
