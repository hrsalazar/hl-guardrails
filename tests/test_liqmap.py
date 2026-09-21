"""Hyperliquid liquidation levels (hlg.liqmap)."""
from hlg import liqmap, report

from .test_universe import Mem

L = liqmap.settings({})


def test_shorts_cluster_above_and_longs_below():
    pos = [("SOL", -10, 103.5, 50_000), ("SOL", -5, 103.9, 20_000),   # shorts liquidate at +3.5% / +3.9%
           ("SOL", 20, 95.2, 80_000),                                   # a long liquidates at -4.8%
           ("SOL", 20, 130.0, 1_000),                                   # long "liq" above price: already past, ignored
           ("SOL", -3, 150.0, 9_000),                                   # beyond range_pct: ignored
           ("BTC", -1, 101.0, 999_999)]                                 # another coin
    m = liqmap.build(pos, {"SOL": 100.0}, ["SOL"], {"SOL": 1_600_000}, band_pct=1.0, range_pct=15)
    cl = m["SOL"]["clusters"]
    shorts = [c for c in cl if c["side"] == "short"]
    longs = [c for c in cl if c["side"] == "long"]
    assert len(shorts) == 1 and shorts[0]["usd"] == 70_000 and shorts[0]["n"] == 2
    assert 103.5 < shorts[0]["px"] < 103.9  # notional-weighted price inside the 3-4% band
    assert longs == [{"side": "long", "px": 95.2, "usd": 80_000, "n": 1}]
    assert m["SOL"]["coverage"] == (50_000 + 20_000 + 80_000 + 1_000 + 9_000) / 1_600_000


def test_near_sums_both_sides_within_the_window_against_a_newer_price():
    e = {"px": 100.0, "clusters": [{"side": "short", "px": 103.0, "usd": 3e6, "n": 4},
                                   {"side": "short", "px": 104.5, "usd": 1e6, "n": 1},
                                   {"side": "short", "px": 109.0, "usd": 9e6, "n": 9},
                                   {"side": "long", "px": 97.0, "usd": 2e6, "n": 2}]}
    s = liqmap.near(e, 100.0, near_pct=5)
    assert s["above"]["usd"] == 4e6 and s["above"]["top_px"] == 103.0 and abs(s["above"]["top_pct"] - 3.0) < 1e-9
    assert s["below"]["usd"] == 2e6
    moved = liqmap.near(e, 105.0, near_pct=5)  # price rallied through: the 103 / 104.5 shorts are behind it now
    assert moved["above"]["usd"] == 9e6 and moved["below"]["usd"] == 0  # 97 is now -7.6%: outside 5%


def test_describe_reads_like_an_alert_line():
    s = {"above": {"usd": 4.2e6, "top_px": 103.1, "top_pct": 3.1, "top_usd": 1.9e6},
         "below": {"usd": 0.0, "top_px": None, "top_pct": None, "top_usd": None}}
    assert liqmap.describe(s) == "shorts liq $4.2M within +5% (largest $1.9M at +3.1%) | no longs liq within -5%"


def test_fetch_skips_positions_without_a_liquidation_price_and_bad_accounts():
    def fetch(a):
        if a == "bad":
            raise RuntimeError("boom")
        return {"assetPositions": [{"position": {"coin": "ETH", "szi": "-2", "liquidationPx": "4000", "positionValue": "7000"}},
                                   {"position": {"coin": "ETH", "szi": "5", "liquidationPx": None, "positionValue": "9"}}]}
    got, done = liqmap.fetch_positions(["a", "bad", "b"], threads=2, budget_s=10, fetch=fetch)
    assert done == 2 and got == [("ETH", -2.0, 4000.0, 7000.0)] * 2


def test_refresh_caches_the_account_list_for_the_day_and_stores_only_aggregates():
    calls = []

    class Resp:
        def json(self):
            return {"leaderboardRows": [{"ethAddress": f"0x{i}", "accountValue": str(i),
                                         "windowPerformances": [["week", {"vlm": str(100 - 10 * i)}]]} for i in range(10)]}

    def get(url, timeout):
        calls.append(url)
        return Resp()

    fetch = lambda a: {"assetPositions": [{"position": {"coin": "SOL", "szi": "-1", "liquidationPx": "104", "positionValue": "1000"}}]}
    st, cfg = Mem(), dict(L, accounts_by_value=2, accounts_by_volume=3)
    m = liqmap.refresh(st, {"SOL": 100.0}, ["SOL"], {"SOL": 1e6}, cfg, 0, "2026-09-21", get=get, fetch=fetch)
    # largest by value (9, 8) plus most active by weekly volume (0, 1, 2), deduplicated
    assert st.get("liqmap_accounts")["addrs"] == ["0x9", "0x8", "0x0", "0x1", "0x2"]
    assert m["accounts"] == 5 and m["coins"]["SOL"]["clusters"][0]["usd"] == 5000
    assert "0x9" not in str(st.get("liqmap"))  # no address is kept next to positions
    liqmap.refresh(st, {"SOL": 100.0}, ["SOL"], {"SOL": 1e6}, cfg, 1, "2026-09-21", get=get, fetch=fetch)
    assert len(calls) == 1  # same day: no second leaderboard download


def test_stale_after_refresh_hours():
    st = Mem({"liqmap": {"t": 0}})
    assert not liqmap.stale(st, 3 * 3_600_000, 4)
    assert liqmap.stale(st, 4 * 3_600_000, 4)
    assert liqmap.stale(Mem(), 0, 4)


def test_payload_recomputes_distances_against_the_current_price():
    m = {"t": 1, "accounts": 3, "positions": 5,
         "coins": {"SOL": {"px": 100.0, "coverage": 0.2, "clusters": [{"side": "short", "px": 110.0, "usd": 1e6, "n": 1}]}}}
    p = report.liqmap_payload(m, {"SOL": 105.0})
    assert abs(p["coins"]["SOL"]["clusters"][0]["pct"] - (110 / 105 - 1) * 100) < 1e-9
    assert report.liqmap_payload(None, {}) is None
