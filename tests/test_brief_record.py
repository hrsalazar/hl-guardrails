"""The brief settled against the market, its headlines archived, its numbers checked (hlg.digest)."""
import json

from hlg import digest, report, vault

from .test_digest import D, Resp, feeds_get, ms, reply
from .test_universe import Mem

DAY = 86_400_000


def _logged(*calls):
    st = Mem({})
    for i, (tilt, px0) in enumerate(calls):
        digest.log_brief(st, {"day": f"2026-01-{i + 1:02d}", "t": i * DAY, "tilt": tilt, "confidence": "low"}, px0)
    return st


def test_a_tilt_settles_after_five_days_at_the_price_then():
    st = _logged(("risk-on", 100.0), ("risk-off", 100.0), ("neutral", 100.0))
    assert not digest.settle(st, 4 * DAY, 110.0)                         # nothing is 5 days old yet
    assert digest.settle(st, 5 * DAY, 110.0)                             # only the first is
    lg = st.get("brief_log")
    assert lg[0]["hit"] is True and abs(lg[0]["ret"] - 0.10) < 1e-12 and "ret" not in lg[1]
    digest.settle(st, 7 * DAY, 90.0)
    assert lg[1]["hit"] is True and lg[2]["hit"] is None                 # risk-off right; neutral unscored
    assert lg[0]["px1"] == 110.0                                         # settled once, never re-scored


def test_no_price_no_log_and_one_entry_per_day():
    st = _logged(("risk-on", None))
    assert st.get("brief_log") is None
    st = _logged(("risk-on", 100.0))
    digest.log_brief(st, {"day": "2026-01-01", "t": 0, "tilt": "risk-off", "confidence": "high"}, 101.0)
    assert [e["tilt"] for e in st.get("brief_log")] == ["risk-off"]      # a forced re-run replaces the day


def test_the_record_stays_pending_until_60_directional_calls():
    lg = [{"tilt": "risk-on", "ret": 0.01, "hit": True}] * 10 + [{"tilt": "neutral", "ret": 0.01, "hit": None}]
    r = digest.record(lg)
    assert r["n"] == 10 and r["settled"] == 11 and r["hit_rate"] == 1.0 and r["verdict"] == "pending"
    assert digest.record([])["verdict"] == "pending"


def test_a_brief_that_reads_the_market_beats_chance_and_one_that_guesses_does_not():
    good = [{"tilt": "risk-on" if i % 2 else "risk-off", "ret": 0.02 if i % 2 else -0.02} for i in range(80)]
    for e in good:
        e["hit"] = True
    r = digest.record(good)
    assert r["verdict"] == "better than chance" and r["hit_rate"] == 1.0 and r["chance_median"] < 0.6
    blind = [{"tilt": "risk-on", "ret": 0.02 if i % 2 else -0.02} for i in range(80)]
    for e in blind:
        e["hit"] = e["ret"] > 0
    assert digest.record(blind)["verdict"] == "no better than chance"     # always bullish: 50%


def test_a_point_quoting_a_figure_nobody_gave_it_is_dropped():
    items = [{"id": 1, "src": "X", "title": "CPI rose 0.3% in September", "url": None, "t": 1, "summary": ""}]
    known = digest.known_numbers(items, {"tape": [{"name": "BTC", "chg24h_pct": -2.14}]})
    pts = [{"text": "BTC fell 2.1% overnight.", "refs": [1]}, {"text": "CPI rose 0.3 percent.", "refs": [1]},
           {"text": "ETH dropped 7% on the news.", "refs": [1]}, {"text": "The Fed held.", "refs": [1]}]
    dropped = []
    b = digest.parse_reply(reply(points=pts), items, known, dropped)
    assert [p["text"] for p in b["points"]] == ["BTC fell 2.1% overnight.", "CPI rose 0.3 percent.", "The Fed held."]
    assert len(dropped) == 1


def test_run_logs_the_tilt_and_hands_back_the_headlines_for_the_archive():
    def post(url, timeout, headers, json):
        return Resp(js={"content": [{"type": "text", "text": reply(tilt="risk-on")}], "usage": {}})
    st, keep = Mem({}), []
    digest.run(st, ms(2026, 9, 23, 6), dict(D, feeds=[["A", "a"], ["B", "b"]]), {"tape": [], "btc_px": 60000.0}, {"anthropic": "k"},
               get=feeds_get, post=post, keep=keep)
    assert st.get("brief_log")[0] == {"day": "2026-09-23", "t": ms(2026, 9, 23, 6), "tilt": "risk-on", "conf": "low", "px0": 60000.0}
    assert len(keep) == 8 and all("title" in i for i in keep)


def test_the_archive_keeps_titles_only_and_400_days():
    items = [{"src": "A", "t": 5, "title": "T", "summary": "secret summary", "url": "https://x"}]
    arch = None
    for d in range(405):
        arch = digest.archive_add(arch, f"d{d:04d}", items)
    assert len(arch["days"]) == 400 and "d0000" not in arch["days"] and arch["days"]["d0404"] == [["A", 5, "T"]]
    assert digest.unpack(digest.pack(arch)) == arch and "summary" not in json.dumps(arch)


def test_the_archive_is_carried_forward_untouched_and_grows_on_a_brief_day(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUT", tmp_path)
    v = vault.Vault("pw", iterations=1000)
    items = [{"src": "A", "t": 1, "title": "Old"}]
    env = v.seal(digest.pack(digest.archive_add(None, "2026-10-01", items)), report.NEWS)
    report.carry_news(env, v, True)                                       # not a brief day: as it came
    assert json.loads((tmp_path / report.NEWS).read_text()) == env
    report.carry_news(env, v, True, [{"src": "B", "t": 2, "title": "New"}], "2026-10-02")
    got = digest.unpack(v.unseal(json.loads((tmp_path / report.NEWS).read_text()), report.NEWS))
    assert sorted(got["days"]) == ["2026-10-01", "2026-10-02"]
    (tmp_path / report.NEWS).unlink()
    report.carry_news(None, None, True)                                   # nothing to carry: no file
    assert not (tmp_path / report.NEWS).exists()
