"""Data behind the dashboard charts: history thinning, stop levels for the position ladder."""
import datetime as dt

from hlg import report

from .conftest import FakeInfo, base_cfg, make_stop_order

H, D = 3_600_000, 86_400_000
NOW = int(dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc).timestamp() * 1000)


def pt(ms):
    return {"t": dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec="seconds"), "equity": 1}


def test_recent_points_are_kept_older_ones_thinned_hourly_then_daily():
    runs = [NOW - i * 15 * 60_000 for i in range(0, 4 * 24 * 200)]  # every 15 min for 200 days
    out = report.thin_history([pt(m) for m in reversed(runs)], NOW)
    ts = [int(dt.datetime.fromisoformat(h["t"]).timestamp() * 1000) for h in out]
    assert ts == sorted(ts)
    recent = [t for t in ts if NOW - t <= 3 * D]
    assert len(recent) == 3 * 24 * 4 + 1                      # every run of the last 3 days
    mid = [t for t in ts if 3 * D < NOW - t <= 90 * D]
    assert abs(len(mid) - 87 * 24) <= 2                        # ~one per hour
    old = [t for t in ts if NOW - t > 90 * D]
    assert abs(len(old) - 110) <= 2                            # ~one per day
    assert len(out) < 3000


def test_ladder_gets_the_resting_stop_and_the_risk_budget_stop():
    cfg = base_cfg()
    inf = FakeInfo(open_orders=[make_stop_order("SOL", "A", 10, 140)])
    pos = [{"coin": "SOL", "szi": "10", "entryPx": "150"}, {"coin": "ETH", "szi": "-2", "entryPx": "3000"}]
    report.add_stops(pos, inf, cfg, {"base": 10_000})
    sol, eth = pos
    assert sol["stop_px"] == 140 and sol["stop_cov"] == 1.0 and sol["risk_stop_px"] == 150 - 150 / 10
    assert eth["stop_px"] is None and eth["stop_cov"] == 0 and eth["risk_stop_px"] == 3000 + 150 / 2
