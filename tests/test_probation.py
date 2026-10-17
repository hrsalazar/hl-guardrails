"""4h on probation (docs/research/binance-retest.md): the kill rule's arithmetic, that it sticks once
it fires, and that a retired timeframe's signals are still journalled but no longer alerted."""
from hlg import journal, scanner
from hlg.common import Notifier, State

from .conftest import FakeInfo, base_cfg
from .test_scanner import _meta_ctx, make_candles

CFG = {"since": "2026-10-18", "n": 100, "min_pf": 1.0, "early_n": 50, "early_pf": 0.7}
SINCE = 1_792_281_600_000          # 2026-10-18 00:00 UTC


def closed(n, r, tf="4h", added=SINCE + 1, side="L"):
    return [{"tf": tf, "side": side, "added": added, "status": "stopped", "r": r} for _ in range(n)]


def test_only_4h_longs_journalled_since_the_start_count():
    jr = closed(10, 1.0) + closed(5, -1.0, added=SINCE - 1) + closed(5, -1.0, tf="1d") + closed(5, -1.0, side="S")
    s = journal.probation(jr, "4h", CFG)
    assert s["closed"] == 10 and s["pf"] == 99.0 and not s["retired"]


def test_the_early_exit_needs_50_and_pf_below_0_7():
    weak = closed(30, -1.0) + closed(19, 1.5)                      # PF 0.95 at 49: nothing yet
    assert not journal.probation(weak, "4h", CFG)["retired"]
    bad = closed(35, -1.0) + closed(15, 1.5)                       # PF 0.64 at 50: early exit
    s = journal.probation(bad, "4h", CFG)
    assert s["retired"] and s["why"] == "early"
    meh = closed(32, -1.0) + closed(18, 1.5)                       # PF 0.84 at 50: below 1, above 0.7 -> keep going
    assert not journal.probation(meh, "4h", CFG)["retired"]


def test_the_final_check_at_100_retires_below_1_and_passes_otherwise():
    s = journal.probation(closed(62, -1.0) + closed(38, 1.5), "4h", CFG)    # PF 0.92
    assert s["retired"] and s["why"] == "final"
    s = journal.probation(closed(58, -1.0) + closed(42, 1.5), "4h", CFG)    # PF 1.09
    assert not s["retired"] and s["passed"]


def test_retirement_sticks_even_if_the_record_recovers(tmp_path):
    st = State(tmp_path / "s.json")
    st.set("journal", closed(35, -1.0) + closed(15, 1.5))
    S = {"probation": {"4h": CFG}}
    assert scanner.probation_status(st, S, 123)["4h"]["retired"]
    st.set("journal", closed(35, -1.0) + closed(65, 1.5))          # later PF 2.8
    s = scanner.probation_status(st, S, 456)["4h"]
    assert s["retired"] and s["retired_at"] == 123
    S2 = {"probation": {"4h": {**CFG, "since": "2027-01-01"}}}       # a new probation (owner's config change) starts clean
    assert not scanner.probation_status(st, S2, 789)["4h"]["retired"]


def _ramp_4h():
    ramp = [100.0 + i for i in range(59)]
    d4 = make_candles(ramp, step_ms=4 * 3600_000)
    d4.append({"t": d4[-1]["t"] + 4 * 3600_000, "o": ramp[-1], "h": ramp[-1] + 0.4, "l": ramp[-1] - 0.4, "c": ramp[-1], "v": 100})
    return d4


def _run(tmp_path, retired):
    inf = FakeInfo(candles={("BTC", "4h"): _ramp_4h(), ("BTC", "1h"): make_candles([100.0] * 30, step_ms=3600_000)},
                   meta_ctxs=_meta_ctx(["BTC"]), user_state={"marginSummary": {"accountValue": "10000"}, "assetPositions": []})
    cfg = base_cfg()
    cfg["scanner"].update(timeframes=["4h"], coins=["BTC"], watch_coins=[], probation={"4h": CFG})
    st = State(tmp_path / "s.json")
    if retired:
        st.set("probation_retired", {"4h": {"since": CFG["since"], "why": "early", "t": 1}})
    notif = Notifier(cfg)
    rows = scanner.run_once(cfg, inf, notif, st)
    return rows, notif, st


def test_a_live_4h_alert_carries_the_probation_line(tmp_path):
    _, notif, _ = _run(tmp_path, retired=False)
    text = next(v["text"] if isinstance(v, dict) else v for k, v in notif.collected.items() if k.startswith("setup_BTC"))
    assert "probation: 0/100 4h signals closed since 2026-10-18" in text


def test_a_retired_timeframe_is_journalled_but_not_alerted(tmp_path):
    rows, notif, st = _run(tmp_path, retired=True)
    assert not any(k.startswith("setup_BTC") for k in notif.collected)
    assert rows[0]["setup"] is None and rows[0]["rejected"].startswith("4h alerts retired by probation")
    assert any(e["tf"] == "4h" for e in st.get("journal"))          # the evidence keeps accumulating
