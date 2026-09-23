"""Crypto Fear & Greed index (hlg.sentiment)."""
import pandas as pd

from hlg import sentiment

from .test_universe import Mem

DAY = 86_400


def payload(values, start=1_700_000_000 // DAY * DAY):
    # the API lists newest first
    return {"data": [{"value": str(v), "value_classification": "x", "timestamp": str(start + i * DAY)}
                     for i, v in reversed(list(enumerate(values)))]}


def test_parse_orders_oldest_first_on_utc_days():
    s = sentiment.parse(payload([10, 50, 90]))
    assert list(s) == [10, 50, 90] and s.index.is_monotonic_increasing and str(s.index.tz) == "UTC"


def test_bands_follow_the_index_s_own_published_cutoffs():
    assert [sentiment.band(v) for v in (0, 24, 25, 46, 47, 54, 55, 75, 76, 100)] == [
        "Extreme fear", "Extreme fear", "Fear", "Fear", "Neutral", "Neutral", "Greed", "Greed",
        "Extreme greed", "Extreme greed"]


def test_features_flags_extremes_and_a_missing_day_stays_unknown():
    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    f = sentiment.features(pd.DataFrame({"fng": [80.0, 20.0, None, 50.0]}, index=idx))
    assert list(f.fng_extreme_greed.iloc[[0, 1, 3]]) == [True, False, False]
    assert list(f.fng_extreme_fear.iloc[[0, 1, 3]]) == [False, True, False]
    assert pd.isna(f.fng_extreme_greed.iloc[2]) and pd.isna(f.fng_extreme_fear.iloc[2])


def test_frame_is_shifted_a_day(tmp_path, monkeypatch):
    s = sentiment.parse(payload([10, 20, 30]))
    monkeypatch.setattr(sentiment, "fetch", lambda cache_dir: s)
    df = sentiment.frame(str(tmp_path), end=s.index[-1])
    assert pd.isna(df.fng.iloc[0]) and list(df.fng.iloc[1:]) == [10, 20]


class Resp:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("down")

    def json(self):
        return self._js


def test_refresh_caches_and_keeps_the_last_reading_when_the_api_fails():
    st, calls = Mem(), []

    def ok(url, params, timeout):
        calls.append(1)
        return Resp(payload([40, 71]))

    cur = sentiment.refresh(st, 0, get=ok)
    assert cur["value"] == 71 and cur["label"] == "Greed" and len(cur["series"]) == 2
    sentiment.refresh(st, 3_600_000, get=ok)
    assert len(calls) == 1  # cached for 6h

    bad = sentiment.refresh(st, 7 * 3_600_000, get=lambda url, params, timeout: Resp(None, 503))
    assert bad["value"] == 71  # last reading kept
    assert sentiment.refresh(Mem(), 0, get=lambda url, params, timeout: Resp(None, 503)) is None
