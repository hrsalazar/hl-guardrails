"""The rally-short entry (docs/research/rally-short-study.md): a downtrend bar that touched EMA20 and
closed back below it in the lower half of its range. Pure signal() checks on synthetic bars."""
import pandas as pd

from hlg import backtest as bt

V = bt.RALLY_STUDY["rally_any"]


def bar(**kw):
    k = dict(ema20=100.0, ema50=110.0, atr=4.0, lo20=90.0, hi20=120.0, rsi=45.0,
             o=97.0, h=101.0, l=95.0, c=96.0, close_loc=(96 - 95) / (101 - 95), funding_apr=10.0, btc_down=True)
    k.update(kw)
    return pd.Series(k)


def test_a_rejected_bounce_in_a_downtrend_is_a_short_with_the_stop_above_its_high():
    side, stop, tgt = bt.signal(bar(), V)
    assert side == "S" and stop == 101 + 0.5 * 4 and tgt is None


def test_no_short_without_the_touch_the_rejection_or_the_downtrend():
    assert bt.signal(bar(h=99.0), V) is None                             # never reached EMA20
    assert bt.signal(bar(c=100.5, close_loc=0.9), V) is None             # closed back above it
    assert bt.signal(bar(close_loc=0.7), V) is None                      # rejected, but closed in the upper half
    assert bt.signal(bar(ema50=95.0), V) is None                         # EMA20 above EMA50: no downtrend


def test_the_gates_btc_downtrend_and_crowded_funding():
    assert bt.signal(bar(btc_down=False), bt.RALLY_STUDY["rally_bear"]) is None
    assert bt.signal(bar(), bt.RALLY_STUDY["rally_bear"])[0] == "S"
    assert bt.signal(bar(funding_apr=12.0), bt.RALLY_STUDY["rally_crowded"]) is None   # ~neutral funding
    assert bt.signal(bar(funding_apr=25.0), bt.RALLY_STUDY["rally_crowded"])[0] == "S"


def test_the_combined_book_keeps_breakout_longs_and_adds_rally_shorts():
    Vb = {**bt.VARIANTS["breakout_both"], "short_level": "rally", "rally_stop_atr": 0.5, "short_filters": ["btc_down"]}
    assert bt.signal(bar(), Vb)[0] == "S"
    up = bar(ema20=100.0, ema50=90.0, c=125.0, h=126.0, l=118.0, close_loc=0.9)   # uptrend, close above the 20-bar high
    assert bt.signal(up, Vb)[0] == "L"
