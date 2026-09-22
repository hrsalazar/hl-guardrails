"""hlg.stablecoin parsing, the lookahead shift, and the entry-filter predicates.

Nothing here touches the network: fetch() is monkeypatched with a canned DefiLlama-shaped payload.
"""
import json

import numpy as np
import pandas as pd
import pytest

from hlg import backtest, stablecoin


def _payload(rows):
    """rows: [(unix_seconds, mcap), ...] -> the shape stablecoins.llama.fi/stablecoincharts/all returns."""
    return json.dumps([{"date": str(t), "totalCirculating": {"peggedUSD": v},
                        "totalCirculatingUSD": {"peggedUSD": v}} for t, v in rows])


class R:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_fetch_parses_the_payload_and_keeps_one_point_per_day(tmp_path, monkeypatch):
    raw = _payload([(1_700_000_000, 100.0), (1_700_000_000 + 3600, 101.0),  # same UTC day, later wins
                    (1_700_086_400, 102.0)])  # next day
    monkeypatch.setattr(stablecoin.requests, "get", lambda *a, **k: R(raw))
    s = stablecoin.fetch(cache_dir=str(tmp_path))
    assert list(s.values) == [101.0, 102.0]


def test_fetch_drops_rows_with_no_usd_reading(tmp_path, monkeypatch):
    raw = json.dumps([{"date": "1700000000", "totalCirculating": {}, "totalCirculatingUSD": {"peggedEUR": 5.0}},
                      {"date": "1700086400", "totalCirculating": {}, "totalCirculatingUSD": {"peggedUSD": 50.0}}])
    monkeypatch.setattr(stablecoin.requests, "get", lambda *a, **k: R(raw))
    s = stablecoin.fetch(cache_dir=str(tmp_path))
    assert list(s.values) == [50.0]


def test_frame_forward_fills_gaps_then_shifts_a_day(tmp_path, monkeypatch):
    idx = pd.to_datetime(["2026-01-02", "2026-01-05"], utc=True)  # a gap: Jan 3-4 missing
    monkeypatch.setattr(stablecoin, "fetch", lambda cache_dir="": pd.Series([10.0, 20.0], index=idx, name="stbl"))
    f = stablecoin.frame(cache_dir=str(tmp_path), end="2026-01-06")
    assert np.isnan(f.loc["2026-01-02"].stbl)   # not readable the day it's published
    assert f.loc["2026-01-03"].stbl == 10.0     # the gap day inherits the last known value
    assert f.loc["2026-01-05"].stbl == 10.0     # still yesterday's number, not today's fresh print
    assert f.loc["2026-01-06"].stbl == 20.0     # only readable the day after


def _exp_level(n, rate):
    """A level series whose daily log-return follows `rate(t)`, so its 30d % change (stbl_chg)
    inherits rate's own monotonicity -- e.g. a linearly increasing rate gives a monotonically
    increasing stbl_chg. Verified empirically, not just reasoned about: a linspec growth-RATE
    (rather than a linspace level, which produces a *decelerating* % growth in the opposite
    direction from what it looks like) is what actually controls stbl_chg's direction here."""
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    t = np.arange(n)
    return pd.Series(100.0 * np.exp(np.cumsum(rate(t))), index=idx)


def test_features_are_relative_to_the_series_own_trend():
    n = 300
    rising_growth = _exp_level(n, lambda t: 0.0005 + 0.0002 * t / n)     # 30d % change itself rises throughout
    falling_growth = _exp_level(n, lambda t: 0.0007 - 0.0002 * t / n)    # ... falls throughout, but stays positive

    f_up = stablecoin.features(pd.DataFrame({"stbl": rising_growth}))
    assert (f_up.stbl_chg.dropna() > 0).all()
    assert bool(f_up.stbl_shrinking.iloc[-1]) is False   # always growing -> never a net outflow
    assert bool(f_up.stbl_below_trend.iloc[-1]) is False  # an accelerating growth rate sits above its own EWM

    f_dn = stablecoin.features(pd.DataFrame({"stbl": falling_growth}))
    assert (f_dn.stbl_chg.dropna() > 0).all()             # still net growth throughout -- not an outflow either
    assert bool(f_dn.stbl_shrinking.iloc[-1]) is False
    assert bool(f_dn.stbl_below_trend.iloc[-1]) is True   # but a decelerating growth rate sits below its own EWM


def test_features_on_empty_frame_is_empty():
    assert stablecoin.features(pd.DataFrame()).empty


def test_a_dead_source_degrades_to_no_context_not_a_crash(monkeypatch):
    monkeypatch.setattr(stablecoin, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("defillama down")))
    assert stablecoin.frame().empty


# ----------------------------------------------------------------- entry filters
class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.mark.parametrize("name,passing,failing", [
    ("no_stbl_growth", {"stbl_below_trend": True}, {"stbl_below_trend": False}),
    ("stbl_outflow", {"stbl_shrinking": True}, {"stbl_shrinking": False}),
])
def test_each_filter_accepts_and_rejects(name, passing, failing):
    assert backtest.FILTERS[name](Row(**passing)) is True
    assert backtest.FILTERS[name](Row(**failing)) is False


def test_missing_stbl_data_never_rejects_a_trade():
    for name in ("no_stbl_growth", "stbl_outflow"):
        assert backtest.FILTERS[name](Row()) is True
        assert backtest.FILTERS[name](Row(stbl_below_trend=np.nan, stbl_shrinking=np.nan)) is True


def test_context_merges_the_stablecoin_columns_and_respects_no_stbl():
    idx = pd.date_range("2025-01-01", periods=5, freq="D", tz="UTC")
    data = {"BTC": pd.DataFrame({"c": [1.0] * 5, "ret20": [0.0] * 5}, index=idx)}
    fund = {"BTC": pd.Series(dtype=float)}
    stbl_f = pd.DataFrame({"stbl_chg": [1.0] * 5, "stbl_below_trend": [True] * 5, "stbl_shrinking": [False] * 5}, index=idx)
    backtest.context(data, fund, stbl_f=stbl_f)
    assert bool(data["BTC"]["stbl_shrinking"].iloc[0]) is False
    empty = {"BTC": pd.DataFrame({"c": [1.0] * 5, "ret20": [0.0] * 5}, index=idx)}
    backtest.context(empty, fund, stbl_f=None)
    assert empty["BTC"]["stbl_chg"].isna().all()
