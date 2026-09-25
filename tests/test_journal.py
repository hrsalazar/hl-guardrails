"""Live signal journal (hlg.journal): each signal followed under the strategy's own rules."""
from hlg import journal

D = 86_400_000
S = {"stop_atr": 2.0, "trail_atr": 3.0}


def row(**kw):
    r = dict(coin="SOL", tf="1d", signal_day="2026-09-01", sig_t=0, signal_close=100.0, level=98.0, atr=2.0, close_loc=0.8)
    r.update(kw)
    return r


def bar(i, o, h, l, c, atr=2.0):
    return {"t": i * D, "o": o, "h": h, "l": l, "c": c, "atr": atr}


def new_entry(**kw):
    jr = []
    journal.add(jr, row(**kw), "k", 0, S)
    return jr


def test_fills_at_the_next_open_with_the_rule_stop():
    jr = new_entry()
    e = journal.update(jr[0], [bar(0, 99, 101, 97, 100), bar(1, 101, 103, 100, 102)], S, 21)
    assert e["entry"] == 101 and e["status"] == "open" and e["bars"] == 1  # bar 0 is the signal bar itself
    assert e["risk"] == 4.0 and abs(e["r"] - 0.25) < 1e-9


def test_a_stop_touch_closes_at_the_stop_or_the_gap_open():
    e = new_entry()[0]
    journal.update(e, [bar(1, 101, 102, 95, 96)], S, 21)       # stop 96 touched intrabar
    assert e["status"] == "stopped" and e["exit"] == 96 and abs(e["r"] - (-1.25)) < 1e-9
    g = new_entry()[0]
    journal.update(g, [bar(1, 101, 102, 100, 101), bar(2, 90, 92, 89, 91)], S, 21)  # gapped through
    assert g["exit"] == 90


def test_the_stop_trails_three_atr_below_the_highest_high():
    e = new_entry()[0]
    journal.update(e, [bar(1, 101, 110, 100, 109), bar(2, 109, 120, 108, 119)], S, 21)
    assert e["stop"] == 120 - 6 and abs(e["max_r"] - (120 - 101) / 4) < 1e-9


def test_time_stop_after_max_days():
    e = new_entry()[0]
    bars = [bar(i, 101 + i * 0.1, 101.5 + i * 0.1, 100.9 + i * 0.1, 101.2 + i * 0.1) for i in range(1, 25)]
    journal.update(e, bars, S, 21)
    assert e["status"] == "time" and e["bars"] == 22


def test_failed_early_is_flagged_and_can_still_recover():
    e = new_entry()[0]
    journal.update(e, [bar(1, 101, 101.5, 97, 97.5), bar(2, 97.5, 110, 97.4, 109)], S, 21)
    assert e["failed_early"] and e["status"] == "open" and e["r"] > 0


def test_replaying_overlapping_bars_is_idempotent():
    e = new_entry()[0]
    bars = [bar(1, 101, 103, 100, 102), bar(2, 102, 104, 101, 103)]
    journal.update(e, bars, S, 21)
    snap = dict(e)
    journal.update(e, bars, S, 21)
    assert e == snap


def test_the_same_signal_is_journaled_once():
    jr = new_entry()
    journal.add(jr, row(), "k", 5, S)
    assert len(jr) == 1


def test_summary_and_prune():
    jr = []
    for i, (lo, c) in enumerate([(95, 96), (100, 130)]):  # one loser, one winner at the time stop
        journal.add(jr, row(), f"k{i}", 0, S)
        journal.update(jr[-1], [bar(1, 101, 131, lo, c)] + [bar(j, c, c + 1, c - 1, c) for j in range(2, 25)], S, 21)
    s = journal.summary(jr)
    assert s["closed"] == 2 and s["win_rate"] == 0.5 and s["pf"] > 1
    assert journal.prune(jr, 24 * D + journal.KEEP_CLOSED_MS + 1) == []


# ---- hypothetical pyramid adds (README "Pyramiding": the live half of the test)
def _winner():
    """Entry 101 (risk 4, stop 96), trailing up to 104 by bar 3: the first unit can no longer lose."""
    jr = new_entry()
    e = journal.update(jr[0], [bar(0, 99, 101, 97, 100), bar(1, 101, 103, 100, 102),
                               bar(2, 102, 108, 101, 107), bar(3, 107, 110, 106, 109)], S, 21)
    assert e["stop"] == 104 and e["entry"] == 101
    return jr, e


def test_add_fills_at_the_next_open_shares_the_stop_and_exits_with_the_entry():
    jr, e = _winner()
    journal.arm_add(jr, row(signal_day="2026-09-04", sig_t=3 * D))
    journal.update(e, [bar(4, 110, 113, 109, 112)], S, 21)
    a = e["add"]
    assert a["status"] == "open" and a["entry"] == 110 and a["risk"] == 6  # 110 - the shared stop (104)
    assert abs(a["r"] - 2 / 6) < 1e-9                                      # marked at the close
    journal.update(e, [bar(5, 111, 111, 106, 107)], S, 21)                  # trail is now 107: stopped
    assert e["status"] == "stopped" and abs(e["r"] - 1.5) < 1e-9
    assert a["status"] == "closed" and abs(a["r"] + 0.5) < 1e-9             # (107 - 110) / 6
    s = journal.summary(jr)["adds"]["1d"]
    assert s["closed"] == 1 and abs(s["r_sum"] + 0.5) < 1e-9 and abs(s["with_add_r_sum"] - 1.0) < 1e-9


def test_no_add_while_the_stop_is_below_the_entry_and_a_skip_can_be_rearmed_by_a_new_signal():
    jr = new_entry()
    e = journal.update(jr[0], [bar(0, 99, 101, 97, 100), bar(1, 101, 103, 100, 102)], S, 21)  # stop 97 < 101
    journal.arm_add(jr, row(signal_day="2026-09-02", sig_t=1 * D))
    journal.update(e, [bar(2, 102, 108, 101, 107)], S, 21)
    assert e["add"]["status"] == "skipped" and "below the first entry" in e["add"]["why"]
    journal.arm_add(jr, row(signal_day="2026-09-02", sig_t=1 * D))  # same signal again (next run): no re-arm
    assert e["add"]["status"] == "skipped"
    journal.arm_add(jr, row(signal_day="2026-09-03", sig_t=2 * D))  # a later signal: stop is now 102 >= 101
    journal.update(e, [bar(3, 107, 110, 106, 109)], S, 21)
    assert e["add"]["status"] == "open" and e["add"]["entry"] == 107


def test_arming_is_idempotent_and_scoped_to_coin_and_timeframe():
    jr, e = _winner()
    journal.arm_add(jr, row(signal_day="2026-09-04", sig_t=3 * D))
    first = dict(e["add"])
    journal.arm_add(jr, row(signal_day="2026-09-05", sig_t=4 * D))  # one add per entry
    journal.arm_add(jr, row(signal_day="2026-09-01", sig_t=0))      # its own signal never arms itself
    assert e["add"] == first
    other = new_entry(tf="4h")
    journal.arm_add(other, row(signal_day="2026-09-04", sig_t=3 * D))
    assert "add" not in other[0]  # still pending (not open): nothing to add to


def test_an_armed_add_is_skipped_if_the_entry_closes_first():
    jr, e = _winner()
    journal.arm_add(jr, row(signal_day="2026-09-04", sig_t=4 * D))      # signal on bar 4: fills at bar 5's open
    journal.update(e, [bar(4, 104, 105, 103, 104)], S, 21)                # stopped at 104 on bar 4
    assert e["status"] == "stopped" and e["add"]["status"] == "skipped"


# ---- the ma-lines lead, tracked live (docs/research/ma-lines-study.md)
def test_lines_are_kept_and_closed_entries_are_split_by_side():
    jr = []
    for i, (lines, lo, c) in enumerate([({"sma50w": True, "sma200d": True}, 100, 130),     # winner above
                                        ({"sma50w": False, "sma200d": None}, 95, 96),      # loser below
                                        ({"sma50w": True, "sma200d": False}, 95, 96)]):    # loser above
        journal.add(jr, row(tf="4h", lines=lines), f"k{i}", 0, S)
        journal.update(jr[-1], [bar(1, 101, 131, lo, c)] + [bar(j, c, c + 1, c - 1, c) for j in range(2, 25)], S, 21)
    assert jr[1]["lines"] == {"sma50w": False, "sma200d": None}
    L = journal.summary(jr)["lines"]["4h"]
    assert L["sma50w"]["above"]["n"] == 2 and L["sma50w"]["below"]["n"] == 1
    assert L["sma50w"]["above"]["win_rate"] == 0.5 and L["sma50w"]["below"]["win_rate"] == 0
    assert "below" in L["sma200d"] and L["sma200d"]["below"]["n"] == 1 and L["sma200d"]["above"]["n"] == 1  # None skipped
