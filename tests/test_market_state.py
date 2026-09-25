"""hlg.market_state: the live risk-on / mixed / risk-off label (docs/research/regime-study.md)."""
import pytest

from hlg import market_state as ms
from hlg.common import State

from .conftest import FakeInfo

D = ms.D_MS


@pytest.mark.parametrize("legs,want", [
    (("btc_up", "macro_on", "greed"), "risk_on"),
    (("btc_up", "macro_on", "neutral"), "risk_on"),
    (("btc_up", "macro_on", "fear"), "mixed"),           # one leg off is enough to block risk_on
    (("btc_down", "macro_off", "greed"), "risk_off"),
    (("btc_down", None, "fear"), "risk_off"),
    (("btc_down", "macro_mixed", "neutral"), "mixed"),
    ((None, None, None), "mixed"),
])
def test_composite(legs, want):
    assert ms.composite(*legs) == want


def test_leg_thresholds():
    assert [ms.crowd_of(v) for v in (44, 45, 55, 56, None)] == ["fear", "neutral", "neutral", "greed", None]
    assert ms.macro_of(False, True) == "macro_on" and ms.macro_of(True, False) == "macro_off"
    assert ms.macro_of(True, True) == "macro_mixed" and ms.macro_of(None, True) is None


def _bars(closes, day0):
    return [{"t": (day0 + i) * D, "o": c, "h": c, "l": c, "c": c, "v": 1} for i, c in enumerate(closes)]


def test_btc_trend_reads_closed_bars_once_a_day_and_keeps_yesterday_on_failure(tmp_path):
    today = 20_000
    closes = [100.0] * 300 + [50.0]                                   # the live bar dips; closed bars don't
    inf = FakeInfo(candles={("BTC", "1d"): _bars(closes, today - 300)})
    st = State(tmp_path / "s.json")
    t = ms.btc_trend(inf, st, today * D + 5)
    assert t["day"] == today and t["close"] == 100.0                  # the unfinished bar is dropped
    inf._candles = {}                                                  # a later run the same day: cached, no request
    assert ms.btc_trend(inf, st, today * D + 3_600_000) == t
    kept = ms.btc_trend(inf, st, (today + 1) * D + 5)                 # next day, fetch fails: yesterday stays
    assert kept == t


def test_build_payload_and_none_without_any_leg():
    assert ms.build(None, None, None) is None
    b = ms.build({"up": False}, {"hy_stress": True, "spx_bull": False}, {"value": 22})
    assert b["state"] == "risk_off" and (b["trend"], b["macro"], b["crowd"], b["fng"]) == ("btc_down", "macro_off", "fear", 22)
    assert b["study"]["losing_run"] == [7, 18]                         # the only numbers a message may quote
