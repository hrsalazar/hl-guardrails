"""The 9 EMA / 20 SMA / 200 SMA pullback rules and the stop-entry / close-through-the-20 mechanics
(docs/research/ma-pullback-study.md). Synthetic bars only; no network."""
import numpy as np
import pandas as pd

from hlg import backtest

V = backtest.VARIANTS["ma_pullback"]


def _k(**kw):
    base = dict(o=101.0, h=102.0, l=99.5, c=101.0, atr=2.0, ema9=100.0, sma20=99.0, sma200=np.nan, ma_slope=0.2,
                ext20=1.0, ext_max10=2.0, ext_min10=0.0)
    return pd.Series({**base, **kw})


def test_a_dip_into_the_zone_in_an_uptrend_signals_long_with_a_floored_stop():
    side, stop, tgt = backtest.signal(_k(), V)
    assert side == "L" and stop == 99.5 and tgt is None               # bar low, already >= 0.5 ATR under the high
    _, stop, _ = backtest.signal(_k(l=99.9, h=100.2), V)
    assert stop == 99.2                                               # a doji: stop floored at trigger - 0.5 ATR


def test_flat_extended_or_just_accelerated_trends_are_skipped():
    assert backtest.signal(_k(ma_slope=-0.01), V) is None
    assert backtest.signal(_k(ext20=2.5), V) is None
    assert backtest.signal(_k(ext_max10=3.4), V) is None               # first pullback after an acceleration
    assert backtest.signal(_k(l=100.5), V) is None                     # never reached the 9 EMA


def test_the_200_sma_blocks_a_long_just_overhead_and_is_the_target_further_up():
    assert backtest.signal(_k(sma200=102.5), V) is None               # within 1 ATR above the close
    assert backtest.signal(_k(sma200=110.0), V)[2] == 110.0


def test_the_short_side_mirrors_it():
    k = _k(o=99.0, h=100.5, l=98.0, c=99.0, ema9=100.0, sma20=101.0, ma_slope=-0.2, ext20=-1.0, ext_min10=-2.0, ext_max10=0.0)
    side, stop, _ = backtest.signal(k, V)
    assert side == "S" and stop == 100.5


def _run(bars, sig_on):
    idx = pd.date_range("2025-01-01", periods=len(bars), freq="D", tz="UTC")
    df = pd.DataFrame(bars, index=idx)
    df["atr"], df["hi20"], df["lo20"] = 2.0, np.nan, np.nan
    P = dict(start="2025-01-01", equity0=1000.0, risk_pct=1.0, max_lev=3.0, maker_fee=0.0, taker_fee=0.0, max_positions=3)
    orig = backtest.signal
    backtest.signal = lambda k, V_: ("L", 98.0, None) if k.name == idx[sig_on] else None
    try:
        T, _, _ = backtest.run(V, {"X": df}, {"X": pd.Series(dtype=float)}, P)
    finally:
        backtest.signal = orig
    return T


def test_a_buy_stop_fills_at_the_trigger_and_exits_on_a_close_below_the_20():
    T = _run([dict(o=99, h=100, l=98.5, c=99.5, sma20=97), dict(o=99.5, h=101, l=99.2, c=100.5, sma20=97),
              dict(o=100.5, h=103, l=100, c=102, sma20=98), dict(o=102, h=102, l=99, c=98.5, sma20=99)], sig_on=0)
    (t,) = T.itertuples()
    assert t.entry == 100 and t.why == "ma" and t.exit == 98.5


def test_a_buy_stop_that_is_never_reached_lapses_after_one_bar():
    T = _run([dict(o=99, h=100, l=98.5, c=99.5, sma20=97), dict(o=99.5, h=99.8, l=99, c=99.4, sma20=97),
              dict(o=99.4, h=104, l=99.3, c=103, sma20=98)], sig_on=0)
    assert T.empty


def test_momentum_selection_gates_each_side_on_its_own_filters():
    M = backtest.VARIANTS["ma_mom"]
    assert backtest.signal(_k(mom_rank=0.9, vol_spike=True), M)[0] == "L"
    assert backtest.signal(_k(mom_rank=0.5, vol_spike=True), M) is None          # not a mover
    assert backtest.signal(_k(mom_rank=0.9, vol_spike=False), M) is None         # nothing happened
    short = dict(o=99.0, h=100.5, l=98.0, c=99.0, ema9=100.0, sma20=101.0, ma_slope=-0.2, ext20=-1.0,
                 ext_min10=-2.0, ext_max10=0.0, vol_spike=True)
    assert backtest.signal(_k(**short, mom_rank=0.1), M)[0] == "S"
    assert backtest.signal(_k(**short, mom_rank=0.9), M) is None                 # a leader is no short


def test_momentum_rank_is_among_the_coins_eligible_that_day_only():
    idx = pd.date_range("2025-01-01", periods=6, freq="D", tz="UTC")
    data = {c: pd.DataFrame({"ret20": [r] * 6, "vol_ratio": v}, index=idx)
            for c, r, v in (("A", 0.1, [1, 1, 1, 1, 1, 2]), ("B", 0.5, [1] * 6), ("C", 0.9, [1] * 6))}
    mask = pd.DataFrame({"A": [True] * 6, "B": [True] * 6, "C": [False] + [True] * 5}, index=idx)
    backtest.momentum_context(data, mask)
    assert data["B"].mom_rank.iloc[0] == 1.0                                     # C not eligible yet: B leads
    assert data["B"].mom_rank.iloc[1] == 2 / 3 and pd.isna(data["C"].mom_rank.iloc[0])
    assert list(data["A"].vol_spike)[-2:] == [False, True]                    # a 5-bar look-back
