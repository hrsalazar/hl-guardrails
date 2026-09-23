"""Scheduled high-impact releases (hlg.events)."""
import datetime as dt

from hlg import alerts as lifecycle
from hlg import events
from hlg.common import Notifier

from .test_universe import Mem

E = events.settings({})
H = 3_600_000


def ms(*a):
    return int(dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp() * 1000)


FOMC_HTML = """
<h4><a id="1">2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>January</strong></div><div class="fomc-meeting__date col">27-28</div>
<div class="fomc-meeting__month col"><strong>March</strong></div><div class="fomc-meeting__date col">17-18*</div>
<div class="fomc-meeting__month col"><strong>Apr/May</strong></div><div class="fomc-meeting__date col">30-1</div>
<div class="fomc-meeting__month col"><strong>August</strong></div><div class="fomc-meeting__date col">22 (notation vote)</div>
<h4><a id="2">2025 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col"><strong>December</strong></div><div class="fomc-meeting__date col">9-10*</div>
"""


def test_fomc_decision_is_14h_new_york_on_the_last_day_in_utc():
    got = events.parse_fomc(FOMC_HTML)
    assert ms(2026, 1, 28, 19) in got   # winter: EST, 14:00 = 19:00 UTC
    assert ms(2026, 3, 18, 18) in got   # after the March DST switch: EDT, 18:00 UTC
    assert ms(2026, 5, 1, 18) in got    # "Apr/May 30-1": decided on May 1st
    assert ms(2025, 12, 10, 19) in got  # the year comes from the section, not the page's first header
    assert len(got) == 4                # the notation vote is not a meeting with a statement


def test_ff_rows_are_filtered_and_timezone_aware():
    rows = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.4%"},
            {"title": "Retail Sales", "country": "USD", "date": "2026-10-15T08:30:00-04:00", "impact": "Medium"},
            {"title": "ECB Rate", "country": "EUR", "date": "2026-10-15T08:15:00-04:00", "impact": "High"},
            {"title": "broken", "country": "USD", "date": "not a date", "impact": "High"}]
    got = events.parse_ff(rows, ["USD"], ["High"])
    assert [g["title"] for g in got] == ["CPI m/m"]
    assert got[0]["t"] == ms(2026, 10, 14, 12, 30) and got[0]["forecast"] == "0.3%"


class Resp:
    def __init__(self, js=None, text="", status=200):
        self._js, self.text, self.status_code = js, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._js


def test_refresh_is_cached_and_a_rate_limited_feed_keeps_its_items_and_backs_off():
    calls = []
    cpi = {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High"}

    def ok(url, headers, timeout):
        calls.append(url)
        return Resp([cpi]) if url == events.FF_URL else Resp(text=FOMC_HTML * 2)

    st, now = Mem(), ms(2026, 10, 12)
    events.refresh(st, now, dict(E, keep_days=365), get=ok)
    assert len(st.get("events")["items"]) == 1 and len(calls) == 2
    events.refresh(st, now + H, dict(E, keep_days=365), get=ok)
    assert len(calls) == 2  # inside refresh_hours: no request at all

    def limited(url, headers, timeout):
        calls.append(url)
        return Resp(status=429)

    later = now + 7 * H
    events.refresh(st, later, dict(E, keep_days=365), get=limited)
    assert len(st.get("events")["items"]) == 1          # last good data survives
    n = len(calls)
    events.refresh(st, later + 15 * 60_000, dict(E, keep_days=365), get=limited)
    assert len(calls) == n                              # no retry on the next 15-minute run
    events.refresh(st, later + H, dict(E, keep_days=365), get=limited)
    assert len(calls) == n + 1                          # retried after an hour


def test_same_minute_releases_merge_and_fomc_is_not_listed_twice():
    t = ms(2026, 10, 14, 12, 30)
    f = ms(2026, 10, 28, 18)
    ev = {"items": [{"id": "a", "title": "CPI m/m", "t": t, "country": "USD", "impact": "High", "forecast": "", "previous": ""},
                    {"id": "b", "title": "Core CPI m/m", "t": t, "country": "USD", "impact": "High", "forecast": "", "previous": ""},
                    {"id": "c", "title": "Federal Funds Rate", "t": f, "country": "USD", "impact": "High", "forecast": "", "previous": ""}],
          "fomc": [f, ms(2026, 12, 9, 19)]}
    g = events.groups(ev, ms(2026, 10, 1), ms(2026, 12, 31))
    assert [x["titles"] for x in g] == [["CPI m/m", "Core CPI m/m"], ["Federal Funds Rate"], ["FOMC rate decision"]]
    assert events.next_fomc(ev, ms(2026, 10, 29)) == ms(2026, 12, 9, 19)


def test_heads_up_within_warn_hours_names_positions_and_is_pushed_once_then_vanishes():
    t = ms(2026, 10, 14, 12, 30)
    ev = {"items": [{"id": "a", "title": "CPI m/m", "t": t, "country": "USD", "impact": "High", "forecast": "0.3%", "previous": "0.4%"}]}
    far = Notifier({})
    events.alerts(far, ev, t - 30 * H, E, held={"BTC"})
    assert not far.collected  # 30h out: not yet

    n = Notifier({})
    events.alerts(n, ev, t - 14 * H, E, held={"SOL", "BTC"})
    (key, text), = n.collected.items()
    assert "in 14h 00m" in text and "2 positions (BTC, SOL)" in text and "forecast 0.3%" in text
    live, _ = lifecycle.build([("event", n)], {}, t - 14 * H)
    assert live[0]["cat"] == "heads_up" and live[0]["push"] and live[0]["new"]

    n2 = Notifier({})
    events.alerts(n2, ev, t - 13 * H, E, held=set())
    again, _ = lifecycle.build([("event", n2)], {"alerts": live}, t - 13 * H)
    assert not again[0]["new"] and "No open positions" in again[0]["text"]  # same key: not re-pushed

    after, ended = lifecycle.build([("event", Notifier({}))], {"alerts": again}, t + H)
    assert after == [] and ended == []  # released: gone, not "expired"
    assert "2.6x" not in text and "volatility usually jumps" in text  # the measured figure is FOMC's only

    f = Notifier({})
    events.alerts(f, {"fomc": [t]}, t - 5 * H, E)
    (_, ftext), = f.collected.items()
    assert ftext.startswith("EVENT USD FOMC rate decision") and "2.6x a normal hour (27 decisions" in ftext


def test_fomc_window_flags_only_bars_whose_fill_lands_in_the_24h_before_a_decision():
    import pandas as pd

    from hlg.backtest import fomc_ahead

    decision = ms(2026, 3, 18, 18)
    daily = pd.date_range("2026-03-15", periods=6, freq="D", tz="UTC")
    got = dict(zip(daily.strftime("%m-%d"), fomc_ahead(daily, [decision])))
    # signal on the 17th fills at 18th 00:00, 18h before the decision; the 16th's fill is 42h out;
    # the 18th's own bar fills on the 19th, after the decision
    assert got == {"03-15": False, "03-16": False, "03-17": True, "03-18": False, "03-19": False, "03-20": False}

    h4 = pd.date_range("2026-03-18 04:00", periods=4, freq="4h", tz="UTC")  # fills 08,12,16,20h
    assert list(fomc_ahead(h4, [decision])) == [True, True, True, False]
    assert not fomc_ahead(daily, []).any()


def test_breakout_note_only_when_a_release_is_close():
    t = ms(2026, 10, 14, 12, 30)
    ev = {"items": [{"id": "a", "title": "CPI m/m", "t": t, "country": "USD", "impact": "High", "forecast": "", "previous": ""}]}
    assert events.note_for_entry(ev, t - 9 * H) == "high-impact USD CPI m/m in 9h 00m - gap risk"
    assert events.note_for_entry(ev, t - 30 * H) == ""
    assert events.note_for_entry(None, t) == ""
