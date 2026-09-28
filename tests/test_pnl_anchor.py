"""hlg.guardrails.pnl_anchors: where Today and This week start (the loss limits' baseline).

Hyperliquid's day/week P&L series have a point every ~2h20m and restart at 0 at their own rolling
window start; the old anchor took the last point at or before 00:00 UTC, up to ~2h20m early, so the
previous evening's P&L landed in Today. Synthetic series; no network.
"""
import datetime as dt

from hlg import guardrails as g
from hlg.common import State

M = 60_000
T0 = int(dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc).timestamp() * 1000)   # Monday 00:00 UTC
STARTS = {"day": dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc), "week": dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc)}


def port(now, all_pnl, window_pts, value=1000.0):
    """window_pts: [(t, pnl)] of the day/week series, which must end at `now`."""
    ser = {"pnlHistory": [[t, str(p)] for t, p in window_pts], "accountValueHistory": [[t, str(value)] for t, _ in window_pts]}
    return {"day": ser, "week": ser,
            "allTime": {"pnlHistory": [[0, "0"], [now, str(all_pnl)]], "accountValueHistory": [[0, "0"], [now, str(value)]]}}


def test_first_run_after_midnight_interpolates_between_the_two_runs_around_it(tmp_path):
    st = State(tmp_path / "s.json")
    st.set("pnl_live", {"t": T0 - 10 * M, "pnl": 100.0, "av": 1000.0})           # the 23:50 run
    now = T0 + 5 * M
    live, a = g.pnl_anchors(port(now, 130.0, [(T0 - 140 * M, 0), (now, 50)]), "day", "week", st, STARTS)
    assert a["day"]["how"] == "runs" and abs(a["day"]["pnl"] - 120.0) < 1e-9         # 10 of 15 minutes
    pnl, base = g.period_since(live, a["day"])
    assert abs(pnl - 10.0) < 1e-9 and base == 1000.0
    assert st.get("pnl_live")["t"] == now


def test_without_a_run_just_before_it_interpolates_in_hls_history_not_the_earlier_point(tmp_path):
    st = State(tmp_path / "s.json")
    now = T0 + 150 * M
    # points at 22:40 (0) and 01:00 (+40); live +100 at 02:30. At 00:00: 0 + 40 * 80/140
    live, a = g.pnl_anchors(port(now, 5100.0, [(T0 - 80 * M, 0), (T0 + 60 * M, 40), (now, 100)]), "day", "week", st, STARTS)
    assert a["day"]["how"] == "history"
    pnl, _ = g.period_since(live, a["day"])
    assert abs(pnl - (100 - 40 * 80 / 140)) < 1e-9                                  # 77.14, not the old 100


def test_a_stale_previous_run_is_not_used(tmp_path):
    st = State(tmp_path / "s.json")
    st.set("pnl_live", {"t": T0 - 2 * 3_600_000, "pnl": -999.0, "av": 1000.0})       # 2h before: too far
    now = T0 + 5 * M
    _, a = g.pnl_anchors(port(now, 5000.0, [(T0 - 60 * M, 0), (now, 10)]), "day", "week", st, STARTS)
    assert a["day"]["how"] == "history"


def test_the_anchor_is_kept_for_the_period_and_moves_on_at_the_next(tmp_path):
    st = State(tmp_path / "s.json")
    st.set("pnl_live", {"t": T0 - 5 * M, "pnl": 100.0, "av": 1000.0})
    _, a1 = g.pnl_anchors(port(T0 + 10 * M, 115.0, [(T0 - 60 * M, 0), (T0 + 10 * M, 5)]), "day", "week", st, STARTS)
    later = T0 + 10 * 3_600_000                                                       # 10:00, the window has rolled
    live, a2 = g.pnl_anchors(port(later, 160.0, [(later - 1440 * M, 0), (later, 999)]), "day", "week", st, STARTS)
    assert a2["day"] == a1["day"]                                                      # not recomputed from rolled data
    assert abs(g.period_since(live, a2["day"])[0] - 55.0) < 1e-9                       # 160 - 105
    tue = {"day": STARTS["day"] + dt.timedelta(days=1), "week": STARTS["week"]}
    _, a3 = g.pnl_anchors(port(T0 + 1440 * M + 5 * M, 170.0, [(T0 + 1440 * M - 30 * M, 0), (T0 + 1440 * M + 5 * M, 1)]),
                          "day", "week", st, tue)
    assert a3["day"]["t0"] == T0 + 1440 * M and a3["week"] == a1["week"]               # new day, same week
