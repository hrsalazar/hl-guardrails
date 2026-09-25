"""Discipline aids, server side: your record from fills (hlg.behavior) and the resting-add guardrail."""
from hlg import behavior, guardrails
from hlg.common import Notifier, State

from .conftest import FakeInfo, base_cfg, make_portfolio, make_position, make_stop_order, make_user_state
from .test_universe import Mem

D = behavior.D_MS


def fill(coin, side, px, sz, start, t, pnl=0.0, fee=0.0, crossed=False, oid=None):
    return {"coin": coin, "side": side, "px": str(px), "sz": str(sz), "startPosition": str(start), "time": t,
            "closedPnl": str(pnl), "fee": str(fee), "crossed": crossed, "hash": f"h{t}", "oid": oid or t}


def test_round_trips_mark_adds_below_the_average_and_ignore_spot_and_duplicates():
    fills = [
        fill("SOL", "B", 100, 1, 0, 1 * D),                   # open long 1 @ 100
        fill("SOL", "B", 90, 1, 1, 2 * D),                    # add below the average: averaging down
        fill("SOL", "B", 90, 1, 1, 2 * D),                    # exact duplicate row: ignored
        fill("SOL", "A", 97, 2, 2, 3 * D, pnl=4.0, fee=0.5),  # flat: 2 x (97 - 95)
        fill("BTC", "B", 50, 1, 0, 4 * D),
        fill("BTC", "B", 55, 1, 1, 5 * D),                    # adding ABOVE the average: not averaging down
        fill("BTC", "A", 60, 2, 2, 6 * D, pnl=15.0),
        fill("@107", "B", 1, 10, 0, 7 * D),                   # spot: not a perp round trip
        fill("ETH", "A", 10, 1, 2, 8 * D),                    # started before the lookback: unattributable
        fill("ZEC", "A", 300, 1, 0, 9 * D),                   # still open short, added above the average
        fill("ZEC", "A", 320, 1, -1, 10 * D),
    ]
    closed, open_ = behavior.round_trips(fills)
    by = {t["coin"]: t for t in closed}
    assert set(by) == {"SOL", "BTC"}
    assert by["SOL"]["adds_under"] == 1 and by["SOL"]["net"] == 3.5
    assert by["BTC"]["adds_under"] == 0
    (z,) = open_
    assert z["coin"] == "ZEC" and z["adds_under"] == 1 and z["last_add_under"] == 10 * D   # short: above = worse


def test_summary_splits_averaged_down_from_never_and_counts_the_streak_from_the_last_add():
    now = 30 * D
    trades = [dict(coin="A", open=0, close=1 * D, net=-500.0, adds_under=3, last_add_under=int(0.5 * D)),
              dict(coin="B", open=2 * D, close=3 * D, net=200.0, adds_under=0, last_add_under=None),
              dict(coin="C", open=4 * D, close=20 * D, net=-50.0, adds_under=0, last_add_under=None)]  # loser held 16d
    s = behavior.summarize(trades, [], now)
    assert (s["avgdown_n"], s["avgdown_net"], s["never_n"], s["never_net"]) == (1, -500.0, 2, 150.0)
    assert s["streak_days"] == 29.5
    assert [t["clean"] for t in s["recent"]] == [False, True, False]   # C: held a loser > 7 days
    assert behavior.summarize([], [], now) is None


def test_refresh_is_hourly_fail_soft_and_logs_no_money():
    calls = []

    def fetch(acct, now_ms):
        calls.append(now_ms)
        return [fill("SOL", "B", 100, 1, 0, 1 * D), fill("SOL", "B", 90, 1, 1, 2 * D), fill("SOL", "A", 80, 2, 2, 3 * D, pnl=-30)]

    st = Mem()
    b = behavior.refresh(st, "0x", 10 * D, fetch)
    assert b["avgdown_n"] == 1 and st.get("behavior") is b
    behavior.refresh(st, "0x", 10 * D + 30 * 60_000, fetch)
    assert len(calls) == 1                                         # within the hour: no refetch

    def boom(acct, now_ms):
        raise RuntimeError("HL down")
    assert behavior.refresh(st, "0x", 12 * D, boom) is b          # previous record kept
    ev = behavior.evidence(st)
    assert ev.startswith("Your last 180 days: the 1 trades you averaged down netted -$30")
    assert behavior.evidence(Mem()) is None


def limit(coin, side, px, sz, reduce_only=False):
    return {"coin": coin, "side": side, "limitPx": str(px), "sz": str(sz), "reduceOnly": reduce_only,
            "isTrigger": False, "orderType": "Limit"}


def run_with(tmp_path, pos, orders, mid, behavior_record=None):
    state = State(tmp_path / "s.json")
    if behavior_record:
        state.set("behavior", behavior_record)
    info = FakeInfo(user_state=make_user_state(10000, [pos]), portfolio=make_portfolio(0, 0, 10000),
                    open_orders=orders, mids={pos["coin"]: str(mid)})
    notif = Notifier(base_cfg())
    return guardrails.run_once(base_cfg(), info, notif, state), notif


REC = {"days": 180, "avgdown_n": 61, "avgdown_net": -12340.0, "never_n": 47, "never_net": 1905.0}  # made up


def test_resting_buys_that_would_add_to_a_losing_long_are_flagged_before_they_fill(tmp_path):
    pos = make_position("SOL", sz=10, entry=100, upnl=-50)
    stop = make_stop_order("SOL", "A", 10, 94)
    problems, notif = run_with(tmp_path, pos, [stop, limit("SOL", "B", 93, 5), limit("SOL", "B", 90, 5)], 95, REC)
    (msg,) = [p for p in problems if "resting" in p]
    first, second = msg.split("\n")
    assert "2 resting buy order(s), 10 SOL at 90, 93, would add to this losing position below its entry" in first
    assert "$" not in first                                           # no P&L on the push line (lock screen)
    assert "61 trades you averaged down netted -$12,340" in second and "never added to while losing netted +$1,905" in second
    assert any(k.startswith("addorders_SOL_2_") for k in notif.collected)


def test_a_planned_average_down_is_flagged_even_while_winning_but_adding_above_entry_is_not(tmp_path):
    pos = make_position("SOL", sz=10, entry=100, upnl=+50)
    stop = make_stop_order("SOL", "A", 10, 94)
    below, _ = run_with(tmp_path, pos, [stop, limit("SOL", "B", 97, 5)], 105)
    assert any("would add to this position below its entry" in p for p in below)
    above, _ = run_with(tmp_path, pos, [stop, limit("SOL", "B", 104, 5), limit("SOL", "A", 120, 10, reduce_only=True)], 105)
    assert not any("resting" in p for p in above)                     # adding to a winner / a take-profit


def test_short_mirror(tmp_path):
    pos = make_position("ETH", sz=-2, entry=3000, upnl=-40)
    stop = make_stop_order("ETH", "B", 2, 3100)
    problems, _ = run_with(tmp_path, pos, [stop, limit("ETH", "A", 3050, 1)], 3020)
    assert any("1 resting sell order(s), 1 ETH at 3050, would add to this losing position above its entry" in p for p in problems)
