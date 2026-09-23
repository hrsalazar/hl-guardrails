"""Pyramiding in the backtest engine (hlg.backtest._pyramid_add / _close)."""
import pandas as pd

from hlg.backtest import _close, _pyramid_add

P = dict(risk_pct=1.5, max_lev=3.0, maker_fee=0.0, taker_fee=0.0)
V = dict(pyramid=1)
D1, D2, D3 = (pd.Timestamp(f"2026-01-0{i}", tz="UTC") for i in (1, 2, 3))


def pos(entry=100.0, stop=100.0, sz=1.0):
    return dict(coin="SOL", side="L", entry=entry, sz=sz, stop=stop, start=D1, fee=0.0, fund=0.0, adds=[])


def test_no_add_while_the_first_unit_can_still_lose():
    p = pos(stop=95.0)
    assert _pyramid_add(p, 110.0, D2, V, P, 1000.0) == 0.0 and p["adds"] == []


def test_add_risks_risk_pct_to_the_shared_stop():
    p = pos(stop=100.0)  # trailed to breakeven
    _pyramid_add(p, 110.0, D2, V, P, 1000.0)
    (a,) = p["adds"]
    assert abs(a["sz"] * (110.0 - 100.0) - 15.0) < 1e-9  # 1.5% of 1000 lost if the shared stop is hit


def test_add_count_notional_cap_and_gap_through_stop():
    p = pos(stop=100.0)
    _pyramid_add(p, 110.0, D2, V, P, 1000.0)
    assert _pyramid_add(p, 120.0, D3, V, P, 1000.0) == 0.0 and len(p["adds"]) == 1   # pyramid=1: one add only
    big = pos(stop=100.0, sz=27.0)                                                     # 27 x 110 already ~3x of 1000
    _pyramid_add(big, 110.0, D2, dict(pyramid=2), P, 1000.0)
    held = big["sz"] + sum(a["sz"] for a in big["adds"])
    assert held * 110.0 <= 3.0 * 1000.0 + 1e-6
    assert _pyramid_add(pos(stop=100.0), 99.0, D2, V, P, 1000.0) == 0.0               # opened below the stop


def test_all_units_exit_together_one_row_each():
    p = pos(stop=100.0)
    _pyramid_add(p, 110.0, D2, V, P, 1000.0)
    rows, eq = _close(p, 105.0, D3, "stop", P, 1000.0)
    assert [r["unit"] for r in rows] == [0, 1] and {r["exit"] for r in rows} == {105.0}
    assert rows[0]["net"] == 5.0 and abs(rows[1]["net"] + 1.5 * 5) < 1e-9  # base +5, add -(1.5 units x 5)
    assert abs(eq - (1000.0 + 5.0 - 7.5)) < 1e-9
