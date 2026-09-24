"""Live cascade early-warning tracking (hlg.cascade_watch). No network: fetchers are stubbed."""
import numpy as np
import pandas as pd

from hlg import cascade_watch as cw

from .test_universe import Mem

H, D = cw.H_MS, cw.D_MS
NOW = int(pd.Timestamp("2026-09-24 10:20", tz="UTC").timestamp() * 1000)


def h1_frame(n=100):
    idx = pd.date_range(end=pd.Timestamp(NOW // H * H - H, unit="ms", tz="UTC"), periods=n, freq="1h")
    c = np.linspace(100, 110, n)
    return pd.DataFrame({"o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 1.0}, index=idx)


class Fetch:
    def __init__(self):
        self.h, self.d = 0, 0

    def hourly(self, inf, coin, now_ms):
        self.h += 1
        return h1_frame()

    def daily(self, inf, coin, now_ms):
        self.d += 1
        return [[NOW // D * D - D, True, False, 2.0]]


def one_event(h1, d):
    return [{"t": h1.index[-10] + pd.Timedelta(hours=1), "stop": 106.0, "a": 2.0, "kind": "cascade"}]  # ~1.5 ATR


def test_records_once_and_fetches_1h_only_once_per_hour_and_1d_once_per_day():
    st, f = Mem(), Fetch()
    assert cw.update(None, ["SOL"], st, NOW, f.hourly, f.daily, one_event)
    (ev,) = st.get("cascade_watch")["events"]
    assert ev["coin"] == "SOL" and ev["status"] == "watching" and ev["atr"] == 2.0
    assert ev["px"] == h1_frame().c.iloc[-10]                      # the close the confirmation printed
    assert not cw.update(None, ["SOL"], st, NOW + 10 * 60_000, f.hourly, f.daily, one_event)
    assert (f.h, f.d) == (1, 1)                                     # same hour: no refetch, no duplicate
    cw.update(None, ["SOL"], st, NOW + H, f.hourly, f.daily, one_event)
    assert (f.h, f.d) == (2, 1) and len(st.get("cascade_watch")["events"]) == 1


def test_only_the_studys_population_is_recorded_stop_within_two_daily_atrs():
    def wide(h1, d):
        return [{"t": h1.index[-10] + pd.Timedelta(hours=1), "stop": 90.0, "a": 2.0, "kind": "cascade"}]
    st, f = Mem(), Fetch()
    cw.update(None, ["SOL"], st, NOW, f.hourly, f.daily, wide)    # ~19 below the price = ~9.5 ATR
    assert st.get("cascade_watch")["events"] == []


def test_resolves_a_hit_from_the_journal_with_lead_and_price_advantage():
    t = NOW - 3 * D
    ev = [{"key": "k", "coin": "SOL", "t": t, "px": 100.0, "atr": 2.0, "status": "watching"}]
    sig_t = t // D * D + D                                          # 1d signal the next day -> entry a day later
    journal = [{"coin": "SOL", "tf": "1d", "sig_t": sig_t, "entry": 104.0, "close": 103.5},
               {"coin": "SOL", "tf": "4h", "sig_t": t + H, "entry": 101.0}]   # 4h signals don't count
    assert cw.resolve(ev, journal, NOW)
    assert ev[0]["status"] == "breakout" and ev[0]["better_atr"] == 2.0
    assert ev[0]["lead_days"] == round((sig_t + D - t) / D, 1)


def test_a_miss_only_after_the_window_and_a_day_of_grace():
    t = NOW - 10.5 * D
    ev = [{"key": "k", "coin": "SOL", "t": int(t), "px": 100.0, "atr": 2.0, "status": "watching"}]
    assert not cw.resolve(ev, [], NOW) and ev[0]["status"] == "watching"
    assert cw.resolve(ev, [], NOW + D) and ev[0]["status"] == "no breakout"


def test_summary_and_a_failing_fetch_is_contained():
    st = Mem({"cascade_watch": {"events": [
        {"coin": "A", "t": 1, "status": "breakout", "lead_days": 3.0, "better_atr": 1.5},
        {"coin": "B", "t": 2, "status": "no breakout"},
        {"coin": "C", "t": 3, "status": "watching"}]}})
    s = cw.summary(st)
    assert (s["resolved"], s["hits"], s["hit_rate"]) == (2, 1, 0.5) and s["watching"] == [{"coin": "C", "t": 3}]
    assert s["backtest"]["hit_rate"] == 0.458

    def boom(*a):
        raise RuntimeError("HL down")
    st2 = Mem()
    assert not cw.update(None, ["SOL"], st2, NOW, boom, boom, one_event)
    assert st2.get("cascade_watch")["events"] == []
