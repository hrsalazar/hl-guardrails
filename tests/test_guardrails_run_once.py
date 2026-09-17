"""Every rule in hlg.guardrails.run_once only ever produces advice (a `problems` entry and a
Notifier message) -- it never places, modifies, or cancels an order. These tests assert on that
advice, and on the absence of advice in the clean case, using FakeInfo so nothing touches the
network. NOW_MS anchors the loss-limit portfolio fixtures; state is a real State() writing to a
temp file, and notif is a real Notifier() with telegram disabled (the config default), so
`.collected` is populated without any network call ever being attempted."""
from hlg import guardrails
from hlg.common import Notifier, State

from .conftest import FakeInfo, NOW_MS, base_cfg, make_fill, make_portfolio, make_stop_order, make_user_state, make_position


def run(cfg, info, state):
    notif = Notifier(cfg)
    problems = guardrails.run_once(cfg, info, notif, state)
    return problems, notif


def test_clean_tick_has_no_advice(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    info = FakeInfo(user_state=make_user_state(10000, []), portfolio=make_portfolio(0, 0, 10000))
    problems, notif = run(cfg, info, state)
    assert problems == []
    assert notif.collected == {}


def test_uncovered_stop_under_max_risk_suggests_a_stop_price(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("BTC", sz=1.0, entry=100, upnl=-50)  # risk_usd = 1.5% * 10000 = 150; -50 > -150
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[],
        mids={"BTC": "101"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "NO STOP covering position" in problems[0]
    assert "Required stop @" in problems[0]
    assert "nostop_BTC" in notif.collected


def test_uncovered_stop_past_max_risk_advises_closing(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("BTC", sz=1.0, entry=100, upnl=-200)  # past risk_usd=150
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[],
        mids={"BTC": "90"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "NO STOP and already past max risk" in problems[0]
    assert "Close it." in problems[0]


def test_stop_wider_than_allowed_risk_warns(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    # risk_usd = 150 (1.5% of 10000 equity); allowed up to 1.25x = 187.5. A stop 200 points away
    # on a 1.0-size position risks 200 USD, over the allowance.
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0)
    order = make_stop_order("BTC", "A", 1.0, trigger_px=-100)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "risks" in problems[0] and "Tighten stop or cut size" in problems[0]


def test_covered_stop_within_risk_is_silent(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0)
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)  # risks 1 USD, well inside 150
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
    )
    problems, notif = run(cfg, info, state)
    assert problems == []


def test_daily_loss_limit_breach(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    info = FakeInfo(
        user_state=make_user_state(10000, []),
        portfolio=make_portfolio(day_pnl=-350, week_pnl=0, equity=10000),  # -3.5% > 3.0% limit
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "DAILY LOSS LIMIT hit" in problems[0]
    assert "should be flat" in problems[0]
    assert state.get("lock_until") > NOW_MS


def test_weekly_loss_limit_breach(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    info = FakeInfo(
        user_state=make_user_state(10000, []),
        portfolio=make_portfolio(day_pnl=0, week_pnl=-650, equity=10000),  # -6.5% > 6.0% limit
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "WEEKLY LOSS LIMIT hit" in problems[0]
    assert state.get("lock_until") > NOW_MS


def test_no_loss_limit_breach_below_threshold(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    info = FakeInfo(
        user_state=make_user_state(10000, []),
        portfolio=make_portfolio(day_pnl=-100, week_pnl=-200, equity=10000),  # -1% / -2%, both under limits
    )
    problems, notif = run(cfg, info, state)
    assert problems == []


def test_position_open_while_locked_is_flagged_every_tick(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    # a new ISO week resets lock_until to 0 (see run_once), so pin week_key to the current week
    # first -- otherwise the lock we're about to set gets wiped before run_once ever sees it.
    state.set("week_key", guardrails.week_key(guardrails.utc_now()))
    state.set("lock_until", NOW_MS + 3_600_000)  # locked for another hour, from a prior tick
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0)
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)  # fully stopped, so only the lock fires
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "LOCKED until" in problems[0]
    assert "still open" in problems[0]


def test_leverage_over_cap(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0, leverage=10)  # cap is 5
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "leverage 10x > max 5x" in problems[0]


def test_coin_outside_allowed_list(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("DOGE", sz=1.0, entry=1, upnl=0)  # not in allowed_coins
    order = make_stop_order("DOGE", "A", 1.0, trigger_px=0.99)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"DOGE": "1"},
    )
    problems, notif = run(cfg, info, state)
    assert len(problems) == 1
    assert "DOGE not in allowed list" in problems[0]


def test_max_positions_exceeded(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    positions = [make_position(c, sz=0.1, entry=100, upnl=0) for c in ("BTC", "ETH", "SOL")]
    orders = [make_stop_order(c, "A", 0.1, trigger_px=99) for c in ("BTC", "ETH", "SOL")]
    info = FakeInfo(
        user_state=make_user_state(10000, positions),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=orders,
        mids={c: "100" for c in ("BTC", "ETH", "SOL")},
    )
    # max_positions is 3 in base_cfg; 4 would breach it. Reuse 3 coins + a 4th to cross the cap.
    cfg["rules"]["max_positions"] = 2
    problems, notif = run(cfg, info, state)
    assert any("positions open > max 2" in p for p in problems)


def test_same_direction_cap(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    positions = [make_position(c, sz=0.1, entry=100, upnl=0) for c in ("BTC", "ETH", "SOL")]
    orders = [make_stop_order(c, "A", 0.1, trigger_px=99) for c in ("BTC", "ETH", "SOL")]
    info = FakeInfo(
        user_state=make_user_state(10000, positions),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=orders,
        mids={c: "100" for c in ("BTC", "ETH", "SOL")},
    )
    problems, notif = run(cfg, info, state)  # 3 longs > max_same_direction=2
    assert any("same direction > max 2" in p for p in problems)


def test_gross_exposure_cap(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0, position_value=40000)  # 4x equity > 3x cap
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
    )
    problems, notif = run(cfg, info, state)
    assert any("gross exposure" in p for p in problems)


def test_added_to_a_losing_position_flags_only_the_added_amount(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    state.set("positions", {"BTC": {"sz": 1.0, "entry": 100}})  # prior tick: long 1.0 @ 100
    pos = make_position("BTC", sz=2.0, entry=100, upnl=-100)  # added to 2.0, still entry 100
    order = make_stop_order("BTC", "A", 2.0, trigger_px=95)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "90"},  # mark below entry -> underwater
    )
    problems, notif = run(cfg, info, state)
    assert any("ADDED" in p and "averaging down" in p for p in problems)


def test_no_add_flag_when_price_is_not_underwater(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    state.set("positions", {"BTC": {"sz": 1.0, "entry": 100}})
    pos = make_position("BTC", sz=2.0, entry=100, upnl=100)
    order = make_stop_order("BTC", "A", 2.0, trigger_px=95)
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "110"},  # mark above entry -> not underwater
    )
    problems, notif = run(cfg, info, state)
    assert not any("ADDED" in p for p in problems)


def test_time_stop(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    ten_days_ago = NOW_MS - 10 * 86_400_000  # max_hold_days is 7 in base_cfg
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0)
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)
    fills = [make_fill("BTC", "B", 1.0, ten_days_ago)]
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
        fills=fills,
    )
    problems, notif = run(cfg, info, state)
    assert any("time stop. Close." in p for p in problems)


def test_position_within_hold_window_has_no_time_stop(tmp_path):
    cfg = base_cfg()
    state = State(tmp_path / "state.json")
    two_days_ago = NOW_MS - 2 * 86_400_000
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0)
    order = make_stop_order("BTC", "A", 1.0, trigger_px=99)
    fills = [make_fill("BTC", "B", 1.0, two_days_ago)]
    info = FakeInfo(
        user_state=make_user_state(10000, [pos]),
        portfolio=make_portfolio(0, 0, 10000),
        open_orders=[order],
        mids={"BTC": "100"},
        fills=fills,
    )
    problems, notif = run(cfg, info, state)
    assert problems == []
