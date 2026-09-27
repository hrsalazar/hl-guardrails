"""scripts/wallet_behavior_study.py: rebuilding other wallets' positions (docs/research/wallet-behavior-study.md).

The study's numbers rest on positions(): flat -> flat, averaging down, partial closes, flips, liquidations
and stop coverage. Synthetic fills only; no network.
"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("wallet_study", Path(__file__).resolve().parent.parent / "scripts" / "wallet_behavior_study.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

ADDR = "0xabc"
H = 3_600_000


def fill(t, side, sz, px, start, closed=0.0, fee=0.0, coin="SOL", dir_="Open Long", **kw):
    return {"time": t, "tid": t, "coin": coin, "side": side, "sz": str(sz), "px": str(px),
            "startPosition": str(start), "closedPnl": str(closed), "fee": str(fee), "dir": dir_, **kw}


def wallet(fills, orders=()):
    return {"addr": ADDR, "fills": list(fills), "orders": list(orders)}


def test_an_add_below_the_average_is_averaging_down_and_pnl_is_after_fees():
    ps = ws.positions(wallet([
        fill(0, "B", 1, 100, 0, fee=0.1),
        fill(H, "B", 1, 99.6, 1),                       # 0.4% worse: an add, not averaging down
        fill(2 * H, "B", 2, 95, 2),                     # > 0.5% below the average: averaging down
        fill(3 * H, "A", 4, 97, 4, closed=-1.2, fee=0.2, dir_="Close Long"),
    ]))
    assert len(ps) == 1
    p = ps[0]
    assert p["adds"] == 2 and p["ad_adds"] == 1 and p["ad"] is True
    assert abs(p["pnl"] - (-1.2 - 0.3)) < 1e-9 and p["hold_h"] == 3


def test_a_partial_close_keeps_the_average_and_a_flip_opens_the_other_side():
    ps = ws.positions(wallet([
        fill(0, "B", 2, 100, 0),
        fill(H, "A", 1, 110, 2, closed=10, dir_="Close Long"),        # average stays 100
        fill(2 * H, "B", 1, 99.6, 1),                                   # 0.4% under 100: not averaging down
        fill(3 * H, "A", 4, 105, 2, closed=10, dir_="Long > Short"),    # closes the long, opens a 2-lot short
        fill(4 * H, "B", 2, 100, -2, closed=10, dir_="Close Short"),
    ]))
    assert [p["side"] for p in ps] == [1, -1]
    assert ps[0]["ad"] is False and ps[0]["pnl"] == 20
    assert ps[1]["start"] == 3 * H and ps[1]["pnl"] == 10


def test_a_position_opened_before_the_window_is_skipped_until_it_is_flat():
    ps = ws.positions(wallet([
        fill(0, "A", 1, 100, 1, closed=5, dir_="Close Long"),          # was already long: unknown entry
        fill(H, "B", 1, 100, 0),
        fill(2 * H, "A", 1, 101, 1, closed=1, dir_="Close Long"),
    ]))
    assert len(ps) == 1 and ps[0]["start"] == H


def test_liquidation_counts_only_when_this_wallet_is_the_liquidated_one():
    liq = lambda who: {"liquidatedUser": who, "markPx": "90", "method": "market"}  # noqa: E731
    ps = ws.positions(wallet([
        fill(0, "B", 1, 100, 0),
        fill(H, "A", 1, 80, 1, closed=-20, dir_="Close Long", liquidation=liq(ADDR.upper())),
        fill(2 * H, "B", 1, 100, 0),
        fill(3 * H, "A", 1, 80, 1, closed=-20, dir_="Close Long", liquidation=liq("0xsomeoneelse")),
    ]))
    assert [p["liq"] for p in ps] == [True, False]


def _stop(coin, t):
    return {"order": {"coin": coin, "timestamp": t, "orderType": "Stop Market", "reduceOnly": True}}


def test_a_stop_counts_from_a_minute_before_the_open_to_the_close_on_the_same_coin():
    fills = [fill(10 * H, "B", 1, 100, 0), fill(12 * H, "A", 1, 99, 1, closed=-1, dir_="Close Long")]
    assert ws.positions(wallet(fills, [_stop("SOL", 10 * H - 30_000)]))[0]["stop"] is True
    assert ws.positions(wallet(fills, [_stop("SOL", 10 * H - 120_000)]))[0]["stop"] is False   # too early
    assert ws.positions(wallet(fills, [_stop("ETH", 11 * H)]))[0]["stop"] is False            # other coin
    entry_order = {"order": {"coin": "SOL", "timestamp": 11 * H, "orderType": "Limit", "reduceOnly": True}}
    assert ws.positions(wallet(fills, [entry_order]))[0]["stop"] is False                     # not a stop


def test_stop_coverage_is_unknown_when_the_capped_order_history_starts_after_the_open():
    fills = [fill(10 * H, "B", 1, 100, 0), fill(12 * H, "A", 1, 99, 1, closed=-1, dir_="Close Long")]
    capped = [{"order": {"coin": "BTC", "timestamp": 11 * H + i, "orderType": "Limit", "reduceOnly": False}}
              for i in range(2000)]                                     # HL's 2,000-order cap, all after the open
    assert ws.positions(wallet(fills, capped))[0]["stop"] is None
