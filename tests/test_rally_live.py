"""Live 1d rally shorts (docs/research/rally-any-confirmation.md): the signal on cached bar stats, the
alert, the journal following a short, the kill rule, the alert lifecycle and the separate short slot."""
import pandas as pd

from hlg import alerts, guardrails, journal, scanner
from hlg.common import Notifier, State

from .conftest import FakeInfo, base_cfg, make_position, make_user_state
from .test_scanner import _meta_ctx, _NullNotifier, make_candles

D = 86_400_000
S = {"rally_shorts": True, "rally_stop_atr": 0.5, "stop_atr": 2.0, "trail_atr": 3.0}


def st(**kw):
    s = {"tf": "1d", "bar_t": 100 * D, "k_c": 96.0, "k_h": 101.0, "e20": 100.0, "e50": 110.0, "atr": 4.0,
         "hi20": 130.0, "hi20_live": 130.0, "rsi": 40.0, "live_c": 96.5, "close_loc": 0.2, "spark": []}
    s.update(kw)
    return s


def test_a_rejected_rally_on_a_downtrend_daily_bar_is_a_short_setup():
    a = scanner.classify(st(), 96.5, S, "SOL", 5.0)
    assert a["setup"] == "SHORT" and a["side"] == "S" and a["stop"] == 101 + 0.5 * 4 and a["level"] == 101
    assert a["rejected"] is None and a["signal_day"] == str(pd.Timestamp(100 * D, unit="ms").date())


def test_no_short_on_4h_without_the_touch_or_the_rejection_or_when_switched_off():
    assert scanner.classify(st(tf="4h"), 96.5, S, "SOL", 0)["setup"] is None
    assert scanner.classify(st(k_h=99.0), 96.5, S, "SOL", 0)["setup"] is None           # never reached EMA20
    assert scanner.classify(st(close_loc=0.8), 96.5, S, "SOL", 0)["setup"] is None      # closed in the upper half
    assert scanner.classify(st(), 96.5, {**S, "rally_shorts": False}, "SOL", 0)["setup"] is None
    a = scanner.classify(st(), 91.0, S, "SOL", 0)                                         # fell > 1 ATR already
    assert a["setup"] is None and a["rejected"].startswith("fell ")


def test_a_cached_stats_entry_without_the_bar_high_waits_for_the_next_bar():
    s = st()
    del s["k_h"]
    assert scanner.classify(s, 96.5, S, "SOL", 0)["setup"] is None


def _downtrend_with_a_rejected_rally():
    closes = [200.0 - i * 1.5 for i in range(70)]                       # steady downtrend
    rows = make_candles(closes)
    e20 = float(scanner.ema(pd.Series(closes), 20).iloc[-1])
    last = rows[-1]                                                       # the bar to close: touches EMA20, closes low
    last.update(o=closes[-1], h=e20 + 0.5, l=closes[-1] - 2.0, c=closes[-1] - 1.5)
    rows.append({"t": last["t"] + D, "o": last["c"], "h": last["c"] + 0.2, "l": last["c"] - 0.2, "c": last["c"], "v": 100})
    return rows


def test_run_once_sends_a_rally_short_alert_and_journals_it_as_a_short(tmp_path):
    inf = FakeInfo(candles={("SOL", "1d"): _downtrend_with_a_rejected_rally(), ("SOL", "1h"): make_candles([100.0] * 30, step_ms=3600_000)},
                   meta_ctxs=_meta_ctx(["SOL"]), user_state=make_user_state(10000, []))
    cfg = base_cfg()
    cfg["scanner"].update(timeframes=["1d"], coins=["SOL"], watch_coins=[], rally_shorts=True, rally_stop_atr=0.5)
    cfg["rules"]["max_shorts"] = 1
    notif = Notifier(cfg)
    stt = State(tmp_path / "s.json")
    scanner.run_once(cfg, inf, notif, stt)
    key = next(k for k in notif.collected if "_SHORT_" in k)
    text = notif.collected[key]["text"] if isinstance(notif.collected[key], dict) else notif.collected[key]
    assert text.startswith("RALLY SHORT SOL") and "touched EMA20" in text and "Kill rule" in text
    e = next(x for x in stt.get("journal") if x["side"] == "S")
    assert e["stop"] > e["close"] and abs(e["risk"] - (e["stop"] - e["close"])) < 1e-9


def _short_entry():
    jr = []
    journal.add(jr, {"coin": "SOL", "tf": "1d", "signal_day": "d", "sig_t": 0, "signal_close": 96.0, "level": 101.0,
                     "atr": 4.0, "stop": 103.0, "side": "S"}, "k", 0, S)
    return jr[0]


def bar(i, o, h, l, c, atr=4.0):
    return {"t": i * D, "o": o, "h": h, "l": l, "c": c, "atr": atr}


def test_the_journal_follows_a_short_trailing_above_the_lowest_low():
    e = _short_entry()
    assert e["stop"] == 103.0 and e["risk"] == 7.0
    journal.update(e, [bar(1, 95, 96, 90, 91), bar(2, 91, 92, 80, 81)], S, 21)
    assert e["entry"] == 95 and e["status"] == "open"
    assert e["stop"] == 80 + 3 * 4                                       # trailed: lowest low 80 + 3 ATR
    assert abs(e["r"] - (95 - 81) / 7) < 1e-9                            # a short gains as price falls
    journal.update(e, [bar(3, 85, 93, 84, 92)], S, 21)                   # high 93 >= stop 92: out at 92
    assert e["status"] == "stopped" and e["exit"] == 92 and abs(e["r"] - (95 - 92) / 7) < 1e-9


def test_failed_early_for_a_short_is_a_close_back_above_the_rejected_high():
    e = _short_entry()
    journal.update(e, [bar(1, 95, 102.5, 94, 101.5)], S, 21)
    assert e["failed_early"] is True and e["status"] == "open"


def test_the_kill_rule_retires_shorts_after_30_closed_with_pf_below_one():
    jr = [{"side": "S", "status": "stopped", "r": -1.0} for _ in range(20)] + [{"side": "S", "status": "stopped", "r": 1.5} for _ in range(10)]
    s = journal.side_summary(jr, "S")
    assert s["closed"] == 30 and s["pf"] == 15 / 20 and s["retired"] is True
    assert journal.side_summary(jr[:29], "S")["retired"] is False        # not before 30
    st_ = type("St", (), {"get": lambda self, k, d=None: jr if k == "journal" else d})()
    assert scanner.shorts_retired(st_) is True


def test_a_short_alert_ends_missed_when_price_fell_away_and_failed_above_the_high():
    a = {"cat": "entry", "meta": {"side": "S", "signal_close": 96.0, "atr": 4.0, "level": 101.0}}
    assert alerts.ended_status(a, 91.0)[0] == "missed"
    assert alerts.ended_status(a, 102.0)[0] == "failed"
    assert alerts.ended_status(a, 97.0)[0] == "expired"


def test_longs_and_shorts_have_separate_slots(tmp_path):
    pos = [make_position(c, 1, 100, 0) for c in ("BTC", "ETH", "SOL")] + [make_position("HYPE", -1, 100, 0)]
    cfg = base_cfg()
    cfg["rules"].update(max_positions=3, max_shorts=1, max_same_direction=3, allowed_coins=["BTC", "ETH", "SOL", "HYPE"])
    problems = guardrails.run_once(cfg, FakeInfo(user_state=make_user_state(10000, pos)), Notifier(cfg), State(tmp_path / "a.json"))
    assert not any("positions open > max" in p for p in problems)        # 3 longs + 1 short: fine
    pos.append(make_position("XRP", -1, 100, 0))
    cfg["rules"]["allowed_coins"].append("XRP")
    problems = guardrails.run_once(cfg, FakeInfo(user_state=make_user_state(10000, pos)), Notifier(cfg), State(tmp_path / "b.json"))
    assert any("2 short positions open > max 1" in p for p in problems)
