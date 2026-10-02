"""hlg.signal_grade: the per-signal trade simulation, the outcome labels and the walk-forward model
(docs/research/signal-grade-study.md). Synthetic bars; no network."""
import numpy as np
import pandas as pd

from hlg import signal_grade as sg


def arrs(rows):
    """rows of (o, h, l, c); ATR fixed at 1."""
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(a))


def test_entry_next_open_stop_two_atr_below_the_signal_close():
    # signal close 100 -> stop 98; entry 100.5 -> risk 2.5; bar 2 dips to 97.9: stopped at 98
    o, h, l, c, a = arrs([(99, 100, 98, 100), (100.5, 101, 99.5, 100.8), (100, 100.2, 97.9, 98.2)] + [(98, 99, 97, 98)] * 30)
    end, r, hit1 = sg.simulate(o, h, l, c, a, 0, 21)
    assert end == 2 and not hit1
    assert abs(r - ((98 - 100.5) / 2.5 - (100.5 * 0.00015 + 98 * 0.00045) / 2.5)) < 1e-12


def test_reaching_plus_one_r_before_the_stop_is_a_hit_and_same_bar_counts_as_the_stop():
    up = [(99, 100, 98, 100), (100.5, 103.1, 100, 103)] + [(103, 103.5, 102.5, 103)] * 30   # +1R = 103
    _, _, hit1 = sg.simulate(*arrs(up), 0, 21)
    assert hit1
    both = [(99, 100, 98, 100), (100.5, 103.5, 97.5, 99)] + [(99, 99.5, 98.5, 99)] * 30     # touches both in one bar
    _, _, hit1 = sg.simulate(*arrs(both), 0, 21)
    assert not hit1


def test_the_stop_trails_three_atr_below_the_high_and_time_stop_exits_at_the_close():
    rows = [(99, 100, 98, 100), (100.5, 110, 100.5, 109)] + [(109, 109.5, 108, 109)] * 25
    end, r, hit1 = sg.simulate(*arrs(rows), 0, 5)
    assert end == 5 and hit1                                    # never fell to 110 - 3 = 107
    assert abs(r - ((109 - 100.5) / 2.5 - (100.5 * 0.00015 + 109 * 0.00045) / 2.5)) < 1e-12


def test_a_trade_still_open_at_the_end_of_the_data_is_unresolved():
    rows = [(99, 100, 98, 100), (100.5, 101, 100, 101), (101, 102, 100.5, 101.5)]
    assert sg.simulate(*arrs(rows), 0, 21) is None


def test_prior_fails_counts_only_outcomes_known_at_the_signal():
    t = pd.Timestamp("2025-01-01", tz="UTC")
    D = pd.Timedelta(days=1)
    S = pd.DataFrame({"coin": ["A"] * 3, "tf": ["1d"] * 3, "t": [t, t + 10 * D, t + 12 * D],
                      "resolved": [t + 5 * D, t + 20 * D, t + 15 * D], "hit1": [False, False, True]})
    out = sg.add_prior_fails(S)
    assert list(out.prior_fails) == [0, 1, 1]                   # the 2nd hadn't resolved by the 3rd's time


def test_logit_recovers_a_planted_effect_and_walk_forward_never_trains_on_the_future():
    rng = np.random.default_rng(0)
    n = 600
    t = pd.Timestamp("2023-06-01", tz="UTC") + pd.to_timedelta(np.sort(rng.uniform(0, 900, n)), unit="D")
    x = rng.normal(size=n)
    hit = rng.uniform(size=n) < 1 / (1 + np.exp(-1.5 * x))
    S = pd.DataFrame({f: 0.0 for f in sg.FEATURES}, index=range(n))
    S["close_loc"] = x
    S = S.assign(t=t, resolved=t + pd.Timedelta(days=5), hit1=hit, R=np.where(hit, 1.0, -1.0), coin="A", tf="1d")
    g = sg.Grader(S)
    assert g.w[1 + sg.FEATURES.index("close_loc")] > 0.8          # planted effect found
    O = sg.walk_forward(S, "1d")
    assert O.t.min() >= S.t.min() + pd.DateOffset(months=12)
    by = O.groupby("grade").R.mean()
    assert by["High"] > by["Low"]


def test_breakout_alerts_carry_the_base_rates_since_no_grade_passed():
    from hlg import scanner
    n = scanner.base_rate_note("4h")
    assert n.startswith("\n  odds: of 906 past 4h breakouts 35% won and 50% never reached +1R")
    assert "No setup feature predicted" in n
    assert scanner.base_rate_note("1h") == ""
