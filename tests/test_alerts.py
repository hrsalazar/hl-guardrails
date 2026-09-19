"""Alert lifecycle (hlg.alerts) and what reaches the phone."""
import numpy as np

from hlg import alerts as lc
from hlg import report
from hlg.common import Notifier

H = 3600_000
T0 = 1_760_000_000_000


def notifier():
    return Notifier({"telegram": {"enabled": False}})


def entry(n, coin="ENA", day="2026-09-18", **kw):
    meta = dict(cat="entry", summary=f"BREAKOUT LONG {coin}", valid_until=T0 + 20 * H, coin=coin, tf="1d",
                level=1.00, signal_close=1.05, atr=0.05)
    meta.update(kw)
    n.send(f"BREAKOUT LONG {coin} @ 1.05\n  details", key=f"setup_{coin}_LONG_{day}", **meta)


def test_first_seen_is_carried_across_runs_and_new_only_once():
    n = notifier()
    entry(n)
    a1, _ = lc.build([("scanner", n)], {}, T0)
    assert a1[0]["new"] and a1[0]["first_seen"] == T0
    a2, _ = lc.build([("scanner", n)], {"alerts": a1}, T0 + H)
    assert not a2[0]["new"] and a2[0]["first_seen"] == T0


def test_momentum_valid_until_counts_from_first_seen():
    n = notifier()
    n.send("MOMENTUM SOL up +9%", key="mom_SOL_1", cat="heads_up", valid_for_ms=4 * H, coin="SOL")
    a1, _ = lc.build([("scanner", n)], {}, T0)
    a2, _ = lc.build([("scanner", n)], {"alerts": a1}, T0 + 2 * H)
    assert a2[0]["valid_until"] == T0 + 4 * H


def test_a_breakout_that_ran_away_ends_as_missed():
    n = notifier()
    entry(n)
    live, _ = lc.build([("scanner", n)], {}, T0)
    _, ended = lc.build([("scanner", notifier())], {"alerts": live}, T0 + H, {"ENA": 1.20})
    assert ended[0]["status"] == "missed" and "past the signal close" in ended[0]["why"]


def test_a_breakout_back_below_its_level_ends_as_failed():
    n = notifier()
    entry(n)
    live, _ = lc.build([("scanner", n)], {}, T0)
    _, ended = lc.build([("scanner", notifier())], {"alerts": live}, T0 + H, {"ENA": 0.95})
    assert ended[0]["status"] == "failed"


def test_otherwise_it_expired_and_ended_items_drop_after_24h():
    n = notifier()
    entry(n)
    live, _ = lc.build([("scanner", n)], {}, T0)
    _, ended = lc.build([("scanner", notifier())], {"alerts": live}, T0 + H, {"ENA": 1.06})
    assert ended[0]["status"] == "expired"
    _, still = lc.build([("scanner", notifier())], {"alerts": [], "ended": ended}, T0 + 20 * H)
    assert len(still) == 1
    _, gone = lc.build([("scanner", notifier())], {"alerts": [], "ended": ended}, T0 + 26 * H)
    assert gone == []


def test_cleared_guardrail_breaches_just_disappear():
    g = notifier()
    g.send("BTC LONG: NO STOP covering position", key="nostop_BTC")
    live, _ = lc.build([("guardrail", g)], {}, T0)
    assert live[0]["cat"] == "position" and live[0]["push"]
    _, ended = lc.build([("guardrail", notifier())], {"alerts": live}, T0 + H)
    assert ended == []


def test_a_signal_that_comes_back_leaves_the_ended_list():
    n = notifier()
    entry(n)
    live, _ = lc.build([("scanner", n)], {}, T0)
    _, ended = lc.build([("scanner", notifier())], {"alerts": live}, T0 + H, {"ENA": 1.06})
    back, ended2 = lc.build([("scanner", n)], {"alerts": [], "ended": ended}, T0 + 2 * H)
    assert len(back) == 1 and ended2 == []


def test_order_is_positions_then_entries_then_heads_up_then_info():
    g, s = notifier(), notifier()
    s.send("FUNDING ZEC +40% APR", key="fund_ZEC_4", push=False, cat="info")
    s.send("MOMENTUM SOL", key="mom_SOL_1", cat="heads_up")
    entry(s)
    g.send("leverage 7x > max 5x", key="lev_BTC")
    live, _ = lc.build([("guardrail", g), ("scanner", s)], {}, T0)
    assert [a["cat"] for a in live] == ["position", "entry", "heads_up", "info"]


def test_numpy_metadata_is_made_json_safe():
    import json

    n = notifier()
    entry(n, level=np.float64(1.0), atr=np.float64(0.05))
    live, _ = lc.build([("scanner", n)], {}, T0)
    json.dumps(live)


def test_long_first_lines_get_a_short_summary():
    assert len(lc.summary_of("x" * 300)) == 110


# ---------------------------------------------------------------- push routing
def test_funding_and_already_held_breakouts_are_dashboard_only():
    s = notifier()
    s.send("FUNDING ZEC +40% APR", key="fund_ZEC_4", push=False, cat="info")
    s.send("BREAKOUT LONG BTC [already in a position - do NOT add]", key="setup_BTC_LONG_x", push=False, cat="info")
    entry(s)
    live, _ = lc.build([("scanner", s)], {}, T0)
    pushed = [a["key"] for a in live if a["new"] and a["push"]]
    assert pushed == ["setup_ENA_LONG_2026-09-18"]


def test_web_push_only_gets_what_main_selects(monkeypatch):
    """report.main passes `new and push` alerts; web_push itself sends whatever it is given."""
    got = []
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
    monkeypatch.setenv("PUSH_SUBSCRIPTIONS", "[{\"endpoint\": \"https://push.example/x\"}]")
    import sys
    import types

    monkeypatch.setitem(sys.modules, "pywebpush", types.SimpleNamespace(
        webpush=lambda subscription_info, data, **kw: got.append(data), WebPushException=Exception))
    report.web_push([{"key": "a", "text": "BREAKOUT LONG ENA\nmore"}], {})
    assert len(got) == 1 and "BREAKOUT LONG ENA" in got[0] and "more" not in got[0]


def test_telegram_is_skipped_for_dashboard_only_alerts(monkeypatch):
    sent = []
    n = Notifier({"telegram": {"enabled": True}})
    n.token, n.chat = "t", "c"
    monkeypatch.setattr("hlg.common.requests.post", lambda *a, **k: sent.append(1))
    n.send("FUNDING ZEC", key="fund_ZEC_4", push=False)
    n.send("BREAKOUT LONG ENA", key="setup_ENA")
    assert len(sent) == 1
