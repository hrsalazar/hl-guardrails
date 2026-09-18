"""Market-structure transforms and the OKX liquidation parser.

Everything here is pure over canned payloads -- conftest's autouse fixture blocks the network,
which is the point: these modules must never be the reason a scan gets slow.
"""
from hlg import liquidations, market


def ctx(**over):
    """An asset context shaped like a live Hyperliquid one."""
    c = {"funding": "0.0000125", "openInterest": "1000.0", "prevDayPx": "100.0",
         "dayNtlVlm": "50000000.0", "premium": "0.0005", "oraclePx": "110.0",
         "markPx": "110.0", "midPx": "110.0", "impactPxs": ["109.9", "110.1"]}
    c.update(over)
    return c


def test_row_derives_the_structure_fields():
    r = market.row(ctx(), "BTC")
    assert r["px"] == 110.0
    assert abs(r["chg24h_pct"] - 10.0) < 1e-9        # 100 -> 110
    assert r["oi_usd"] == 1000.0 * 110.0
    assert abs(r["vol_oi"] - 50_000_000 / 110_000) < 1e-9
    assert abs(r["premium_bps"] - 5.0) < 1e-9        # 0.0005 -> 5 bps
    assert abs(r["spread_bps"] - (0.2 / 110.0 * 10_000)) < 1e-9
    assert r["stale"] is False


def test_row_survives_a_sparse_context():
    """A new or thin listing can omit fields; that must degrade, not raise."""
    r = market.row({"funding": "0"}, "NEW")
    assert r["px"] == 0.0
    assert r["chg24h_pct"] is None and r["vol_oi"] is None and r["spread_bps"] is None


def test_row_falls_back_when_markpx_is_absent():
    r = market.row(ctx(markPx=None), "BTC")
    assert r["px"] == 110.0  # midPx, then oraclePx


def test_row_flags_a_frozen_market():
    assert market.row(ctx(markPx="100.0", prevDayPx="100.0"), "xyz:DXY")["stale"] is True


def test_ctx_rows_covers_only_requested_coins_and_skips_unknowns():
    c = {"BTC": ctx(), "ETH": ctx()}
    rows = market.ctx_rows(c, ["BTC", "ETH", "NOTLISTED"])
    assert [r["coin"] for r in rows] == ["BTC", "ETH"]


def test_tradfi_rows_drops_dead_markets():
    """The real failure this guards: xyz:DXY and xyz:VIX are listed but untraded, so their frozen
    marks would render as live macro data."""
    c = {
        "xyz:SP500": ctx(markPx="7630.0", prevDayPx="7596.0", dayNtlVlm="330000000.0", openInterest="61000.0"),
        "xyz:DXY": ctx(markPx="97.15", prevDayPx="97.15", dayNtlVlm="0.0", openInterest="0.0"),
        "xyz:VIX": ctx(markPx="20.0", prevDayPx="20.0", dayNtlVlm="0.0", openInterest="0.0"),
    }
    rows, dropped = market.tradfi_rows(c, ["xyz:SP500", "xyz:DXY", "xyz:VIX"])
    assert [r["coin"] for r in rows] == ["xyz:SP500"]
    assert dropped == 2


def test_tradfi_rows_drops_thin_but_moving_markets():
    c = {"xyz:THIN": ctx(markPx="5.0", prevDayPx="4.9", dayNtlVlm="1000.0", openInterest="10.0")}
    rows, dropped = market.tradfi_rows(c, ["xyz:THIN"])
    assert rows == [] and dropped == 1


def test_tradfi_rows_counts_missing_instruments_as_dropped():
    rows, dropped = market.tradfi_rows({}, ["xyz:SP500", "xyz:GOLD"])
    assert rows == [] and dropped == 2


def test_tradfi_rows_sorts_by_volume():
    c = {"xyz:A": ctx(dayNtlVlm="2000000.0", markPx="10.0", prevDayPx="9.0", openInterest="100000.0"),
         "xyz:B": ctx(dayNtlVlm="9000000.0", markPx="10.0", prevDayPx="9.0", openInterest="100000.0")}
    rows, _ = market.tradfi_rows(c, ["xyz:A", "xyz:B"])
    assert [r["coin"] for r in rows] == ["xyz:B", "xyz:A"]


# ------------------------------------------------------------------ liquidations
def okx(details):
    return {"code": "0", "data": [{"instId": "BTC-USD-SWAP", "details": details}]}


def test_parse_splits_long_and_short_liquidations():
    r = liquidations.parse(okx([
        {"posSide": "long", "sz": "10", "bkPx": "100", "ts": "1"},
        {"posSide": "short", "sz": "5", "bkPx": "100", "ts": "2"},
    ]), "BTC")
    assert r["long_usd"] == 1000.0 and r["short_usd"] == 500.0
    assert r["events"] == 2
    assert abs(r["skew"] - (500 / 1500)) < 1e-9  # positive = longs being flushed


def test_parse_returns_none_when_nothing_recent():
    assert liquidations.parse(okx([]), "BTC") is None
    assert liquidations.parse({"code": "0", "data": []}, "BTC") is None
    assert liquidations.parse(None, "BTC") is None


def test_parse_skips_malformed_details_without_raising():
    r = liquidations.parse(okx([
        {"posSide": "long", "sz": "10", "bkPx": "100"},
        {"posSide": "long", "sz": "oops", "bkPx": "100"},
        {"posSide": "short"},
    ]), "BTC")
    assert r["events"] == 1 and r["long_usd"] == 1000.0


def test_fetch_degrades_to_empty_when_the_source_is_dead(monkeypatch):
    """OKX being unreachable must cost a scan nothing but time-boxed seconds -- the FRED lesson."""
    import requests as rq

    def boom(*a, **k):
        raise rq.RequestException("blocked")

    monkeypatch.setattr(liquidations.requests, "get", boom)
    assert liquidations.fetch(["BTC", "ETH"]) == []


def test_fetch_stops_when_the_budget_is_spent(monkeypatch):
    calls = []

    def slow(*a, **k):
        calls.append(k["params"]["uly"])
        raise rq.RequestException("slow")

    import requests as rq

    monkeypatch.setattr(liquidations.requests, "get", slow)
    liquidations.fetch(["BTC", "ETH", "SOL"], budget_s=-1)  # already over budget
    assert calls == []
