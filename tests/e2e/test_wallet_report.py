"""The public wallet check (pwa/report.html + pwa/wallet-report.js).

The analysis must match the study's Python exactly (scripts/wallet_behavior_study.py), so a trader's
own report means what the study's numbers mean. Hyperliquid's API is stubbed: no network.
"""
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

spec = importlib.util.spec_from_file_location("wallet_study", Path(__file__).resolve().parents[2] / "scripts" / "wallet_behavior_study.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

ADDR = "0x" + "ab" * 20
H = 3_600_000
NOW = int(time.time() * 1000)
T = NOW - 30 * 24 * H                                   # inside the 90-day window


def fill(t, side, sz, px, start, closed=0.0, fee=0.0, coin="SOL", dir_="Open Long", **kw):
    return {"time": t, "tid": t, "coin": coin, "side": side, "sz": str(sz), "px": str(px),
            "startPosition": str(start), "closedPnl": str(closed), "fee": str(fee), "dir": dir_, **kw}


def stop(coin, t):
    return {"order": {"coin": coin, "timestamp": t, "orderType": "Stop Market", "reduceOnly": True}}


# A wallet with one of everything: a clean winner, an averaged-down loser without a stop, a liquidated
# short, a partial close, a flip, a position opened before the window, and a stopped small loser.
FILLS = [
    fill(T - 50 * H, "B", 1, 50, 2, closed=10, dir_="Close Long", coin="ETH"),               # opened before the window
    fill(T, "B", 1, 100, 0, fee=0.1),                                                          # SOL long
    fill(T + H, "B", 1, 95, 1, fee=0.1),                                                       # averaged down
    fill(T + 2 * H, "A", 2, 90, 2, closed=-15, fee=0.2, dir_="Close Long"),
    fill(T + 3 * H, "B", 2, 10, 0, coin="HYPE"),                                               # clean winner, partial close
    fill(T + 4 * H, "A", 1, 12, 2, closed=2, coin="HYPE", dir_="Close Long"),
    fill(T + 5 * H, "A", 1, 13, 1, closed=3, coin="HYPE", dir_="Close Long"),
    fill(T + 6 * H, "A", 1, 2000, 0, coin="BTC", dir_="Open Short"),                           # liquidated short
    fill(T + 7 * H, "B", 1, 2400, -1, closed=-400, coin="BTC", dir_="Close Short",
         liquidation={"liquidatedUser": ADDR.upper(), "markPx": "2400", "method": "market"}),
    fill(T + 8 * H, "B", 1, 50, 0, coin="LINK"),                                               # flip long -> short
    fill(T + 9 * H, "A", 2, 55, 1, closed=5, coin="LINK", dir_="Long > Short"),
    fill(T + 10 * H, "B", 1, 52, -1, closed=3, coin="LINK", dir_="Close Short"),
    fill(T + 11 * H, "B", 1, 20, 0, coin="AVAX"),                                              # stopped small loser
    fill(T + 12 * H, "A", 1, 19, 1, closed=-1, coin="AVAX", dir_="Close Long"),
]
ORDERS = [stop("AVAX", T + 11 * H + 1000)]


def _js_positions(page, fills, orders):
    return page.evaluate("([a,f,o])=>WalletReport.positions(a,f,o).map(p=>({coin:p.coin,side:p.side,start:p.start,end:p.end,"
                         "pnl:Math.round(p.pnl*1e6)/1e6,ad:p.ad,liq:p.liq,stop:p.stop}))", [ADDR, fills, orders])


def test_the_browser_analysis_matches_the_studys_python(page, base_url):
    page.goto(f"{base_url}/full/report.html")
    for orders in (ORDERS, ORDERS + [{"order": {"coin": "X", "timestamp": T + 20 * H, "orderType": "Limit",
                                                "reduceOnly": False}}] * 2000):   # capped history: stops unknown early
        py = [{"coin": p["coin"], "side": p["side"], "start": p["start"], "end": p["end"], "pnl": round(p["pnl"], 6),
               "ad": p["ad"], "liq": p["liq"], "stop": p["stop"]}
              for p in ws.positions({"addr": ADDR, "fills": json.loads(json.dumps(FILLS)), "orders": orders})]
        assert _js_positions(page, FILLS, orders) == py
    assert len(py) == 6 and {p["coin"] for p in py} == {"SOL", "HYPE", "BTC", "LINK", "AVAX"}


def _stub_hl(page, fills, orders, seen):
    def handle(route):
        body = json.loads(route.request.post_data)
        seen.append(body)
        if body["type"] == "userFillsByTime":
            page_ = [f for f in fills if f["time"] >= body["startTime"]][:2000]
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(page_))
        if body["type"] == "historicalOrders":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(orders))
        route.fulfill(status=400, body="{}")
    page.route("https://api.hyperliquid.xyz/info", handle)


def test_a_trader_sees_how_their_losses_happened_next_to_the_study(page, base_url):
    seen, other_hosts = [], []
    page.on("request", lambda r: other_hosts.append(r.url) if not (r.url.startswith(base_url) or r.url.startswith("https://api.hyperliquid.xyz")) else None)
    _stub_hl(page, FILLS, ORDERS, seen)
    page.goto(f"{base_url}/full/report.html")
    page.locator("#addr").fill(ADDR)
    page.locator("#go").click()
    res = page.locator("#results")
    expect(res).to_contain_text("caused", timeout=8000)
    expect(res).to_contain_text("You were liquidated 1 time")
    expect(res).to_contain_text("You vs 145 other Hyperliquid traders")
    tbl = res.locator("table")
    expect(tbl.locator("tr", has_text="BTC")).to_contain_text("liquidated")
    expect(tbl.locator("tr", has_text="SOL")).to_contain_text("averaged down")
    expect(tbl.locator("tr", has_text="SOL")).to_contain_text("no stop")
    expect(tbl.locator("tr", has_text="AVAX")).to_contain_text("a planned loss")             # had a stop
    assert {b["type"] for b in seen} == {"userFillsByTime", "historicalOrders"} and all(b["user"] == ADDR for b in seen)
    assert other_hosts == []                                                                 # the address goes nowhere else
    assert ADDR not in page.url                                                              # nor into the URL


def test_fills_are_paged_and_bad_addresses_are_refused(page, base_url):
    many = [fill(T + i * 1000, "B" if i % 2 == 0 else "A", 1, 100, 0 if i % 2 == 0 else 1,
                 dir_="Open Long" if i % 2 == 0 else "Close Long") for i in range(2100)]
    seen = []
    _stub_hl(page, many, [], seen)
    page.goto(f"{base_url}/full/report.html")
    page.locator("#addr").fill("0x1234")
    page.locator("#go").click()
    expect(page.locator("#status")).to_contain_text("isn't a Hyperliquid address")
    assert seen == []
    page.locator("#addr").fill(ADDR)
    page.locator("#go").click()
    expect(page.locator("#results")).to_contain_text("1050 closed positions", timeout=8000)
    starts = [b["startTime"] for b in seen if b["type"] == "userFillsByTime"]
    assert len(starts) == 2 and starts[1] == many[1999]["time"] + 1                          # second page after the first
