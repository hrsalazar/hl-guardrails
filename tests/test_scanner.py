import pandas as pd

from hlg import scanner
from .conftest import FakeInfo, base_cfg


def make_candles(closes, *, start_ms=0, step_ms=86_400_000, spread=0.4):
    rows = []
    t = start_ms
    for c in closes:
        rows.append({"t": t, "o": c, "h": c + spread, "l": c - spread, "c": c, "v": 100})
        t += step_ms
    return rows


def test_ema_of_a_constant_series_equals_the_constant():
    s = pd.Series([5.0] * 10)
    assert (scanner.ema(s, 3) == 5.0).all()


def test_rsi_almost_all_gains_is_near_100():
    # A single tiny down-tick keeps the loss EWM from being exactly zero throughout, which
    # would otherwise divide by a value rsi() replaces with NaN (a real edge case in the
    # formula for a literally loss-free series -- avoided here, not asserted on).
    closes = [float(i) for i in range(1, 30)]
    closes[10] = closes[9] - 0.01
    assert scanner.rsi(pd.Series(closes), n=14).iloc[-1] > 95


def test_rsi_all_losses_is_near_0():
    s = pd.Series([float(30 - i) for i in range(30)])  # strictly decreasing, no gains
    assert scanner.rsi(s, n=14).iloc[-1] < 5


def test_atr_of_constant_true_range():
    n = 20
    df = pd.DataFrame({"h": [101.0] * n, "l": [99.0] * n, "c": [100.0] * n})
    assert abs(scanner.atr(df, n=14).iloc[-1] - 2.0) < 1e-6


def _S(**overrides):
    S = dict(base_cfg()["scanner"])
    S.update(overrides)
    return S


def test_analyse_breakout_rejects_a_downtrend():
    closes = [200 - i for i in range(60)]  # steadily falling
    inf = FakeInfo(candles={("BTC", "1d"): make_candles(closes)})
    out = scanner.analyse_breakout(inf, "BTC", _S(), {"funding": 0})
    assert out["trend"] == "DOWN"
    assert out["rejected"] == "trend down"
    assert out["setup"] is None


def test_analyse_breakout_detects_a_genuine_breakout():
    ramp = [100.0 + i for i in range(59)]  # rows 0..58, each close a new high
    rows = make_candles(ramp)
    rows.append({"t": rows[-1]["t"] + 86_400_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    inf = FakeInfo(candles={("BTC", "1d"): rows})
    out = scanner.analyse_breakout(inf, "BTC", _S(), {"funding": 0})
    assert out["trend"] == "UP"
    assert out["setup"] == "LONG"
    assert out.get("rejected") is None
    assert out["stop"] < out["signal_close"]


def test_analyse_breakout_rejects_chasing_a_big_runup():
    ramp = [100.0 + i for i in range(59)]
    rows = make_candles(ramp)
    chase_close = ramp[-1] + 20  # far beyond 1 ATR past the signal close
    rows.append({"t": rows[-1]["t"] + 86_400_000, "o": chase_close, "h": chase_close + 0.4, "l": chase_close - 0.4, "c": chase_close, "v": 100})
    inf = FakeInfo(candles={("BTC", "1d"): rows})
    out = scanner.analyse_breakout(inf, "BTC", _S(), {"funding": 0})
    assert out["setup"] is None
    assert "wait for next setup" in out["rejected"]


def test_analyse_breakout_forming_does_not_chase_an_unclosed_candle():
    ramp = [100.0 + i for i in range(58)]  # rows 0..57, up to 157
    rows = make_candles(ramp)
    flat_close = ramp[-1]  # row 58: flat, not a new completed-candle high
    rows.append({"t": rows[-1]["t"] + 86_400_000, "o": flat_close, "h": flat_close + 0.4, "l": flat_close - 0.4, "c": flat_close, "v": 100})
    forming_close = ramp[-1] + 3  # row 59 (live, unclosed): trading above the 20-bar high intraday
    rows.append({"t": rows[-1]["t"] + 86_400_000, "o": forming_close, "h": forming_close + 0.4, "l": forming_close - 0.4, "c": forming_close, "v": 100})
    inf = FakeInfo(candles={("BTC", "1d"): rows})
    out = scanner.analyse_breakout(inf, "BTC", _S(), {"funding": 0})
    assert out["setup"] is None
    assert "forming" in out["rejected"]


def test_analyse_dispatches_to_breakout_by_default():
    closes = [200 - i for i in range(60)]
    inf = FakeInfo(candles={("BTC", "1d"): make_candles(closes)})
    out = scanner.analyse(inf, "BTC", _S(), {"funding": 0})
    assert out["rejected"] == "trend down"
