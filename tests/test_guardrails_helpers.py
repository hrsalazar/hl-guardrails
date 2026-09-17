import datetime as dt

from hlg import guardrails
from .conftest import make_fill, make_stop_order


def test_day_key():
    t = dt.datetime(2026, 9, 17, 13, 45, tzinfo=dt.timezone.utc)
    assert guardrails.day_key(t) == "2026-09-17"


def test_week_key_matches_isocalendar():
    t = dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc)
    y, w, _ = t.isocalendar()
    assert guardrails.week_key(t) == f"{y}-W{w:02d}"


def test_week_key_stable_within_the_same_iso_week():
    monday = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
    sunday = dt.datetime(2026, 9, 20, 23, 59, tzinfo=dt.timezone.utc)
    assert guardrails.week_key(monday) == guardrails.week_key(sunday)


def test_episode_start_simple_open():
    fills = [make_fill("BTC", "B", 5, 1_000)]
    assert guardrails.episode_start(fills, "BTC", 5) == 1_000


def test_episode_start_walks_through_an_add():
    fills = [
        make_fill("BTC", "B", 3, 1_000),  # opened long 3
        make_fill("BTC", "B", 2, 2_000),  # added 2 -> long 5
    ]
    # current size is 5; the episode began at the original open, not the add
    assert guardrails.episode_start(fills, "BTC", 5) == 1_000


def test_episode_start_ignores_a_fully_closed_and_reopened_episode():
    fills = [
        make_fill("BTC", "B", 3, 1_000),  # opened long 3
        make_fill("BTC", "A", 3, 2_000),  # closed flat
        make_fill("BTC", "B", 4, 3_000),  # reopened long 4
    ]
    assert guardrails.episode_start(fills, "BTC", 4) == 3_000


def test_episode_start_ignores_other_coins():
    fills = [make_fill("ETH", "B", 5, 1_000), make_fill("BTC", "B", 2, 2_000)]
    assert guardrails.episode_start(fills, "BTC", 2) == 2_000


def test_stop_coverage_fully_covered_long():
    orders = [make_stop_order("BTC", "A", 1.0, 90)]
    cov, trig = guardrails.stop_coverage(orders, "BTC", 1.0)
    assert cov == 1.0
    assert trig == 90


def test_stop_coverage_partially_covered_short():
    orders = [make_stop_order("BTC", "B", 1.0, 105)]
    cov, trig = guardrails.stop_coverage(orders, "BTC", -2.0)
    assert cov == 1.0
    assert cov < abs(-2.0) * 0.95
    assert trig == 105


def test_stop_coverage_no_orders():
    cov, trig = guardrails.stop_coverage([], "BTC", 1.0)
    assert cov == 0.0
    assert trig is None


def test_stop_coverage_ignores_non_reduce_only_and_wrong_side():
    orders = [
        {"coin": "BTC", "reduceOnly": False, "side": "A", "orderType": "Stop Market", "sz": "1", "triggerPx": "90"},
        {"coin": "BTC", "reduceOnly": True, "side": "B", "orderType": "Stop Market", "sz": "1", "triggerPx": "90"},
        {"coin": "ETH", "reduceOnly": True, "side": "A", "orderType": "Stop Market", "sz": "1", "triggerPx": "90"},
        {"coin": "BTC", "reduceOnly": True, "side": "A", "orderType": "Limit", "sz": "1", "triggerPx": "90"},
    ]
    cov, trig = guardrails.stop_coverage(orders, "BTC", 1.0)
    assert cov == 0.0
    assert trig is None


def test_stop_coverage_zero_size_order_means_full_remaining_size():
    orders = [make_stop_order("BTC", "A", 0, 90)]
    cov, _ = guardrails.stop_coverage(orders, "BTC", 3.0)
    assert cov == 3.0


def test_stop_coverage_picks_the_loosest_trigger_for_a_long():
    # for a long, the "worst" stop is the one furthest below entry -> the lower trigger
    orders = [make_stop_order("BTC", "A", 0.5, 95), make_stop_order("BTC", "A", 0.5, 90)]
    cov, trig = guardrails.stop_coverage(orders, "BTC", 1.0)
    assert cov == 1.0
    assert trig == 90


def test_stop_coverage_picks_the_loosest_trigger_for_a_short():
    # for a short, the "worst" stop is the one furthest above entry -> the higher trigger
    orders = [make_stop_order("BTC", "B", 0.5, 105), make_stop_order("BTC", "B", 0.5, 110)]
    cov, trig = guardrails.stop_coverage(orders, "BTC", -1.0)
    assert cov == 1.0
    assert trig == 110
