"""hlg.execution: your real trades matched against the journal's signals. Synthetic fills; no network."""
from hlg import execution as ex

H = 3_600_000
D = 24 * H
T = 1_790_000_000_000 - (1_790_000_000_000 % D)          # a UTC midnight


def fill(t, coin, side, sz, px, start, closed=0.0, fee=0.1):
    return {"time": t, "coin": coin, "side": side, "sz": str(sz), "px": str(px), "startPosition": str(start),
            "closedPnl": str(closed), "fee": str(fee), "hash": f"h{t}{coin}", "oid": t}


def entry(coin="SOL", tf="4h", sig_t=T, entry_px=100.0, atr=2.0, r=None, status="open"):
    return {"key": f"setup_{coin}_LONG_x|{tf}", "coin": coin, "tf": tf, "signal_day": "x", "sig_t": sig_t,
            "added": sig_t + 4 * H, "entry": entry_px, "atr": atr, "risk": 2 * atr, "r": r, "status": status}


def stop_order(t, coin="SOL", side="A"):
    return {"order": {"coin": coin, "timestamp": t, "orderType": "Stop Market", "reduceOnly": True, "side": side}}


def test_a_taken_signal_reports_slippage_stop_timing_and_size():
    close = T + 4 * H                                    # the 4h signal bar closes here
    fills = [fill(close + 10 * 60_000, "SOL", "B", 20, 100.4, 0), fill(close + 20 * 60_000, "SOL", "B", 5, 100.6, 20),
             fill(close + 30 * H, "SOL", "A", 25, 104, 25, closed=90)]
    x = ex.evaluate([entry()], fills, [stop_order(close + 13 * 60_000)], equity=10_000, risk_pct=1.0, now_ms=close + 40 * H)
    row = x["rows"][0]
    assert x["taken"] == 1 and x["skipped"] == 0 and row["taken"]
    assert row["slip_atr"] == round((100.44 - 100.0) / 2.0, 2)          # first-hour average vs the next open
    assert row["stop_min"] == 3.0                                        # stop 3 minutes after the first fill
    assert row["size_x"] == round(25 / (100 / 4.0), 2)                   # 25 units vs the rule's 25 -> 1.0


def test_skipped_signals_keep_their_journal_r_and_wait_for_the_window():
    e_done = entry(coin="ETH", sig_t=T, r=-1.0, status="stopped")
    e_live = entry(coin="BTC", sig_t=T + 20 * H)                        # window still open at now
    x = ex.evaluate([e_done, e_live], [], [], 10_000, 1.0, now_ms=T + 26 * H)
    assert x["signals"] == 1 and x["skipped"] == 1 and x["skipped_r"] == -1.0


def test_no_stop_and_off_plan_trades_are_counted():
    close = T + 4 * H
    fills = [fill(close + 60_000, "SOL", "B", 10, 100, 0), fill(close + 5 * H, "SOL", "A", 10, 95, 10, closed=-50),
             fill(close + 6 * H, "DOGE", "B", 1000, 0.2, 0), fill(close + 9 * H, "DOGE", "A", 1000, 0.18, 1000, closed=-20)]
    x = ex.evaluate([entry()], fills, [], 10_000, 1.0, now_ms=close + 20 * H)
    assert x["no_stop"] == 1                                             # never placed while the position was open
    assert x["offplan"] == 1 and x["offplan_net"] == round(-20 - 0.2, 2)  # DOGE matched no signal


def test_a_stop_placed_before_the_entry_counts_as_before_and_short_trades_never_take_a_long_signal():
    close = T + 4 * H
    fills = [fill(close + 60_000, "SOL", "A", 10, 100, 0)]               # a SHORT on the signal's coin
    x = ex.evaluate([entry()], fills, [stop_order(close)], 10_000, 1.0, now_ms=close + 20 * H)
    assert x["taken"] == 0 and x["offplan"] == 1
    fills = [fill(close + 60_000, "SOL", "B", 10, 100, 0)]
    x = ex.evaluate([entry()], fills, [stop_order(close - 5 * 60_000)], 10_000, 1.0, now_ms=close + 20 * H)
    assert x["stop_before"] == 1 and x["rows"][0]["stop_min"] <= 0


def test_refresh_is_hourly_and_fail_soft(tmp_path):
    from hlg.common import State
    st = State(tmp_path / "s.json")
    st.set("journal", [entry()])
    calls = []
    x = ex.refresh(st, "0xA", T + 40 * H, 10_000, 1.0, fetch=lambda a, n, days: calls.append(1) or [], orders_fetch=lambda a: [])
    assert x["signals"] == 1 and calls == [1]
    assert ex.refresh(st, "0xA", T + 40 * H + 60_000, 10_000, 1.0, fetch=lambda *a, **k: 1 / 0) == x   # cached
    boom = ex.refresh(st, "0xA", T + 42 * H, 10_000, 1.0, fetch=lambda *a, **k: 1 / 0, orders_fetch=lambda a: [])
    assert boom == x                                                     # failure keeps the last summary


def test_weekly_counts_split_at_monday_utc():
    import datetime as dt
    mon = int(dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc).timestamp() * 1000)   # a Monday
    e_last = entry(coin="ETH", sig_t=mon - 2 * D, r=-1.0, status="stopped")         # skipped, last week
    e_this = entry(coin="SOL", sig_t=mon + 4 * H)                                    # taken, this week, no stop
    close = mon + 8 * H
    fills = [fill(close + 60_000, "SOL", "B", 10, 100, 0), fill(mon + 30 * H, "DOGE", "B", 100, 0.2, 0),
             fill(mon + 31 * H, "DOGE", "A", 100, 0.19, 100, closed=-1)]
    x = ex.evaluate([e_last, e_this], fills, [], 10_000, 1.0, now_ms=mon + 3 * D)
    w = x["weeks"]
    assert w["this"]["start"] == mon and w["last"]["start"] == mon - 7 * D
    assert (w["this"]["signals"], w["this"]["taken"], w["this"]["no_stop"]) == (1, 1, 1)
    assert (w["last"]["signals"], w["last"]["skipped"], w["last"]["skipped_r"]) == (1, 1, -1.0)
    assert w["this"]["offplan"] == 1 and w["this"]["offplan_net"] == round(-1 - 0.2, 2)


def test_your_r_on_taken_signals_against_the_rule_on_the_same_signals():
    close = T + 4 * H
    fills = [fill(close + 60_000, "SOL", "B", 25, 100.5, 0, fee=0.0),
             fill(close + 9 * H, "SOL", "A", 25, 96, 25, closed=-112.5, fee=0.0)]
    e = entry(r=-1.0, status="stopped")
    x = ex.evaluate([e], fills, [], 10_000, 1.0, now_ms=close + 20 * H)
    assert x["rows"][0]["your_r"] == -1.12                    # -112.5 USD on a 100 USD 1R
    assert x["same"] == {"n": 1, "rule_r": -1.0, "your_r": -1.12}
