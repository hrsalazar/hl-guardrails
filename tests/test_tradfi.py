"""TradFi sleeve research (hlg.tradfi) and the engine's per-group slots. No network."""
import numpy as np
import pandas as pd

from hlg import backtest as bt
from hlg import tradfi as tf


def frame(closes, start="2024-01-01", highs=None, lows=None, opens=None, atr=1.0):
    idx = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"o": c if opens is None else opens, "h": c + 0.5 if highs is None else highs,
                         "l": c - 0.5 if lows is None else lows, "c": c, "v": 1.0, "atr": atr}, index=idx)


def test_the_simulator_stops_gaps_at_the_open_trails_and_times_out():
    df = frame([100, 90], opens=[100, 88], lows=[99.5, 87])                 # gapped through the 95 stop
    r, j, entry, risk = tf.sim_trade(df, 0, 95.0)
    assert (r, j) == ((88 - 100) / 5, 1)
    up = frame(np.linspace(100, 130, 40))                                     # steady rise: the time stop
    r, j, _, _ = tf.sim_trade(up, 0, 95.0)
    assert up.index[j] - up.index[0] == pd.Timedelta(days=21) and r > 0
    trail = frame([100, 110, 112, 105], highs=[100.5, 110.5, 112.5, 105.5], lows=[99.5, 109.5, 111.5, 104])
    r, j, _, _ = tf.sim_trade(trail, 0, 95.0, None)                            # best 112.5 -> stop 109.5
    assert j == 3 and abs(r - (min(105, 109.5) - 100) / 5) < 1e-12


def test_net_r_charges_fees_and_funding_in_r():
    P = {"maker_fee": 0.001, "taker_fee": 0.001}
    assert abs(tf.net_r(1.0, 100.0, 5.0, 10, 0.0001, P) - (1.0 - 0.2 / 5 - 0.1 / 5)) < 1e-12


def test_group_slots_let_a_sleeve_trade_while_crypto_is_full():
    def stream(n, brk_day):
        c = [100.0] * n
        for i in range(brk_day, n):
            c[i] = 100 + (i - brk_day + 1) * 2
        d = frame(c, start="2023-01-01")
        return bt.features(d.drop(columns="atr"), 1)
    data = {f"C{i}|1d": stream(120, 80) for i in range(3)} | {"GOLD|1d": stream(120, 80)}
    fund = {k: pd.Series(dtype=float) for k in data}
    P = {**bt.DEF, "start": "2023-01-01", "risk_pct": 1.0, "max_positions": 3}
    T, _, _ = bt.run(bt.VARIANTS["breakout_long"], data, fund, P)
    assert len(set(T.coin)) == 3                                               # shared cap: the 4th waits
    group_of = {k: "crypto" for k in data if k.startswith("C")} | {"GOLD|1d": "tradfi"}
    T, _, _ = bt.run(bt.VARIANTS["breakout_long"], data, fund, {**P, "group_max": {"crypto": 3, "tradfi": 1}, "group_of": group_of})
    assert "GOLD|1d" in set(T.coin) and len(set(T.coin)) == 4


def test_concordance_counts_xyz_signals_that_yahoo_also_gave_within_a_bar():
    c = [100.0] * 70 + [101 + i for i in range(10)]
    y = frame(c).drop(columns="atr")
    x = y.copy()
    share, n = tf.concordance(y, x)
    assert n >= 1 and share == 1.0


def test_ema_trend_enters_on_the_cross_and_leaves_when_the_trend_ends():
    c = [100.0] * 60 + [100 + 1.5 * i for i in range(1, 41)] + [160 - 3 * i for i in range(1, 41)]
    d = bt.features(frame(c, start="2020-01-01").drop(columns="atr"), 1)
    sig = [t for t, k in d.iterrows() if bt.signal(k, bt.VARIANTS["ema_trend"])]
    assert len(sig) == 1 and d.loc[sig[0]].ema20 > d.loc[sig[0]].ema50 and not d.loc[sig[0]].up_prev
    T, _, _ = bt.run(bt.VARIANTS["ema_trend"], {"X": d}, {"X": pd.Series(dtype=float)},
                     {**bt.DEF, "start": "2020-01-01", "risk_pct": 1.0, "max_positions": 1})
    assert len(T) == 1 and T.iloc[0].why in ("stop", "trend") and T.iloc[0].days > 21   # no 21-day limit
