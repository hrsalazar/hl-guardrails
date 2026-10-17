"""hlg.sources: each exchange's raw layout ends up as the same frame. Fake HTTP; no network."""
import pandas as pd

from hlg import sources as s

D = 86_400_000


class R:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def json(self):
        return self._js


def fake(pages):
    """A get() that serves the given JSON pages in order, then empty lists."""
    it = iter(pages)

    def get(url, params=None, headers=None, timeout=None):
        return R(next(it, []))
    return get


def test_frame_dedupes_sorts_and_types():
    df = s._frame([(2 * D, "2", "3", "1", "2.5", "10"), (D, 1, 2, 0.5, 1.5, 5), (2 * D, 2, 3, 1, 2.5, 10)])
    assert list(df.index) == [pd.Timestamp(D, unit="ms", tz="UTC"), pd.Timestamp(2 * D, unit="ms", tz="UTC")]
    assert df.dtypes.eq(float).all() and df.iloc[1].c == 2.5


def test_bitfinex_and_coinbase_columns_are_reordered_to_ohlc():
    s._last[0] = 0
    bf = s._bitfinex("BTC", "1d", 0, fake([[[D, 10, 12, 13, 9, 100]]]))     # [mts, open, close, high, low, vol]
    assert bf == [(D, 10, 13, 9, 12, 100)]
    cb = s._coinbase("BTC", "1d", 1_700_000_000_000, fake([[[1_700_000_000, 9, 13, 10, 12, 100]]]))   # [t, low, high, open, close, vol]
    assert cb[0] == (1_700_000_000_000, 10, 13, 9, 12, 100)


def test_bitstamp_skips_forward_until_the_coin_is_listed():
    page = {"data": {"ohlc": [{"timestamp": str(10 * 86400), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3}]}}
    rows = s._bitstamp("ETH", "1d", 0, fake([{"data": {"ohlc": []}}, page, {"data": {"ohlc": []}}]))
    assert rows[0][0] == 10 * D


def test_complete_drops_the_bar_still_forming():
    now = pd.Timestamp.now(tz="UTC").floor("D")
    df = pd.DataFrame({"o": [1.0, 1.0], "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}, index=[now - pd.Timedelta(days=1), now])
    assert list(s._complete(df, "1d").index) == [now - pd.Timedelta(days=1)]


def test_bars_caches_an_unlisted_coin_as_empty(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(s.ADAPTERS, "dummy", lambda coin, tf, start, get: calls.append(coin) or [])
    assert s.bars("dummy", "NOPE", "1d", cache=tmp_path) is None
    assert s.bars("dummy", "NOPE", "1d", cache=tmp_path) is None and calls == ["NOPE"]      # second call from cache
