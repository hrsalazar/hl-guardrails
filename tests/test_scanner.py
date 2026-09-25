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


def test_analyse_breakout_honours_an_explicit_tf_over_the_config_default():
    ramp = [100.0 + i for i in range(59)]
    rows = make_candles(ramp, step_ms=4 * 3600_000)
    rows.append({"t": rows[-1]["t"] + 4 * 3600_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    inf = FakeInfo(candles={("BTC", "4h"): rows})
    # config still says "1d", but an explicit tf argument should win (this is how run_once scans
    # several configured timeframes without mutating S for each one)
    out = scanner.analyse_breakout(inf, "BTC", _S(timeframe="1d"), {"funding": 0}, tf="4h")
    assert out["tf"] == "4h"
    assert out["setup"] == "LONG"


def _meta_ctx(coins, funding="0.0"):
    return {"universe": [{"name": c} for c in coins]}, [{"funding": funding} for _ in coins]


class _NullNotifier:
    """Minimal Notifier stand-in for run_once tests that don't care about alert content, only rows."""

    def __init__(self):
        self.collected = {}

    def send(self, text, key=None, cooldown_s=3600, push=True, **meta):
        self.collected[key or text] = text


def test_run_once_scans_every_configured_timeframe_independently():
    ramp = [100.0 + i for i in range(59)]
    d1 = make_candles(ramp)  # 1d bars: closes to a new high
    d1.append({"t": d1[-1]["t"] + 86_400_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    d4 = make_candles(ramp, step_ms=4 * 3600_000)  # 4h bars: same shape, different clock
    d4.append({"t": d4[-1]["t"] + 4 * 3600_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    flat_1h = make_candles([100.0] * 30, step_ms=3600_000)  # no momentum alert wanted in this test
    inf = FakeInfo(
        candles={("BTC", "1d"): d1, ("BTC", "4h"): d4, ("BTC", "1h"): flat_1h},
        meta_ctxs=_meta_ctx(["BTC"]),
        user_state={"marginSummary": {"accountValue": "10000"}, "assetPositions": []},
    )
    S = _S(timeframes=["1d", "4h"], coins=["BTC"], watch_coins=[])
    rows = scanner.run_once({"account": "0xTEST", "scanner": S, "rules": base_cfg()["rules"]}, inf, _NullNotifier(), None)
    tfs_seen = sorted(r["tf"] for r in rows)
    assert tfs_seen == ["1d", "4h"]
    assert all(r["setup"] == "LONG" for r in rows)


def test_run_once_sends_a_momentum_alert_for_a_fast_move_daily_scan_misses():
    flat = [100.0 - 0.01 * i for i in range(59)]  # gently declining: no 1d/4h breakout signal
    d1, d4 = make_candles(flat), make_candles(flat, step_ms=4 * 3600_000)
    h1 = make_candles([100.0] * 20, step_ms=3600_000)
    h1.append({"t": h1[-1]["t"] + 3600_000, "o": 100, "h": 112, "l": 100, "c": 110, "v": 100})  # sharp 1h pop
    inf = FakeInfo(
        candles={("BTC", "1d"): d1, ("BTC", "4h"): d4, ("BTC", "1h"): h1},
        meta_ctxs=_meta_ctx(["BTC"]),
        user_state={"marginSummary": {"accountValue": "10000"}, "assetPositions": []},
    )
    S = _S(timeframes=["1d", "4h"], coins=["BTC"], watch_coins=[], momentum_window_hours=4, momentum_alert_pct=8)
    notif = _NullNotifier()
    rows = scanner.run_once({"account": "0xTEST", "scanner": S, "rules": base_cfg()["rules"]}, inf, notif, None)
    assert all(r["setup"] is None for r in rows)  # confirms 1d/4h genuinely missed it
    assert any(k.startswith("mom_BTC_") for k in notif.collected)


def test_momentum_pct_detects_a_sharp_recent_move():
    flat = [100.0] * 20
    candles_1h = make_candles(flat, step_ms=3600_000)
    candles_1h.append({"t": candles_1h[-1]["t"] + 3600_000, "o": 100, "h": 112, "l": 100, "c": 110, "v": 100})
    inf = FakeInfo(candles={("BTC", "1h"): candles_1h})
    pct = scanner.momentum_pct(inf, "BTC", _S(momentum_window_hours=4))
    assert pct > 8  # ~10% pop inside the window


def test_momentum_pct_is_near_zero_for_a_flat_market():
    flat = [100.0] * 30
    inf = FakeInfo(candles={("BTC", "1h"): make_candles(flat, step_ms=3600_000)})
    pct = scanner.momentum_pct(inf, "BTC", _S(momentum_window_hours=4))
    assert abs(pct) < 0.5


def test_a_new_journal_entry_records_its_lines_once(tmp_path, monkeypatch):
    """The coin's side of its own 50-week / 200-day SMA is looked up when a signal first enters the
    journal (docs/research/ma-lines-study.md), not again on later runs."""
    from hlg.common import State

    ramp = [100.0 + i for i in range(59)]
    d1 = make_candles(ramp)
    d1.append({"t": d1[-1]["t"] + 86_400_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    inf = FakeInfo(candles={("BTC", "1d"): d1, ("BTC", "1h"): make_candles([100.0] * 30, step_ms=3600_000)},
                   meta_ctxs=_meta_ctx(["BTC"]), user_state={"marginSummary": {"accountValue": "10000"}, "assetPositions": []})
    calls = []
    real = scanner.signal_lines
    monkeypatch.setattr(scanner, "signal_lines", lambda inf, r: calls.append(r["coin"]) or real(inf, r))
    st = State(tmp_path / "s.json")
    cfg = {"account": "0xTEST", "scanner": _S(timeframes=["1d"], coins=["BTC"], watch_coins=[]), "rules": base_cfg()["rules"]}
    scanner.run_once(cfg, inf, _NullNotifier(), st)
    scanner.run_once(cfg, inf, _NullNotifier(), st)
    jr = st.get("journal")
    assert len(jr) == 1 and calls == ["BTC"]
    assert jr[0]["lines"] == {"sma50w": None, "sma200d": None}      # 60 days of history: too young for either
