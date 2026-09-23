"""Daily macro brief (hlg.digest). No network: feeds and the API are faked."""
import datetime as dt
import json

import pytest

from hlg import digest

from .test_universe import Mem

H = 3_600_000
D = digest.settings({})


def ms(*a):
    return int(dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp() * 1000)


RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss><channel>
<item><title>Fed holds rates &amp; signals patience</title><link>https://example.com/fed</link>
<pubDate>Wed, 23 Sep 2026 03:00:00 GMT</pubDate><description>&lt;p&gt;Officials said&lt;/p&gt; inflation is easing.</description></item>
<item><title>Bad link</title><link>javascript:alert(1)</link><pubDate>Wed, 23 Sep 2026 02:00:00 GMT</pubDate></item>
<item><title>No date at all</title><link>https://example.com/x</link></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ETF inflows rise</title>
<link href="https://example.com/etf"/><updated>2026-09-23T01:00:00Z</updated><summary>Big week.</summary></entry></feed>"""


def test_parse_feed_reads_rss_and_atom_strips_html_and_drops_unsafe_links():
    r = digest.parse_feed(RSS, "Fed")
    assert r[0]["title"] == "Fed holds rates & signals patience"
    assert r[0]["summary"] == "Officials said inflation is easing."
    assert r[0]["t"] == ms(2026, 9, 23, 3) and r[0]["url"] == "https://example.com/fed"
    assert r[1]["url"] is None  # javascript: never reaches the dashboard
    a = digest.parse_feed(ATOM, "CoinDesk")
    assert a == [{"src": "CoinDesk", "title": "ETF inflows rise", "url": "https://example.com/etf",
                  "t": ms(2026, 9, 23, 1), "summary": "Big week."}]
    assert digest.parse_feed(b"<not xml", "x") == []


def item(src, title, t):
    return {"src": src, "title": title, "url": None, "t": t, "summary": ""}


def test_select_is_recent_deduplicated_and_balanced_across_sources():
    now = ms(2026, 9, 23, 6)
    items = ([item("Crypto", f"coin story {i}", now - i * 60_000) for i in range(20)]
             + [item("Fed", "Fed speech", now - 5 * H), item("Fed", "FED  speech!", now - 6 * H),
                item("Old", "last week", now - 100 * H), item("NoTime", "undated", None)])
    got = digest.select(items, now - 30 * H, max_items=6, per_source=5)
    srcs = [g["src"] for g in got]
    assert "Fed" in srcs and srcs.count("Fed") == 1          # duplicate title collapsed, Fed not crowded out
    assert "Old" not in srcs and "NoTime" not in srcs
    assert len(got) == 6 and [g["id"] for g in got] == [1, 2, 3, 4, 5, 6]
    assert [g["t"] for g in got] == sorted((g["t"] for g in got), reverse=True)


def test_prompt_frames_headlines_as_untrusted_data_and_carries_the_context():
    now = ms(2026, 9, 23, 6)
    picked = digest.select([item("Fed", "Fed holds", now - H)], now - 30 * H)
    system, user = digest.build_prompt(picked, {"now_ms": now, "fng": {"value": 71, "label": "Greed", "series": [[now - 8 * 86_400_000, 55]]},
                                                "tape": [{"name": "BTC", "chg24h_pct": 1.234}],
                                                "events": [{"t": ms(2026, 9, 24, 12, 30), "titles": ["CPI m/m"]}]})
    assert "untrusted" in system and "No buy/sell" in system
    assert "71 (Greed), 7 days ago 55" in user and "BTC +1.2%" in user and "CPI m/m" in user
    assert "[1] Fed |" in user


def reply(**over):
    d = {"headline": "Fed on hold", "tilt": "neutral", "confidence": "low", "why": "Mixed.",
         "points": [{"text": "Fed held.", "refs": [1, 99, "x"]}], "watch": ["CPI Thursday"]}
    return "Here you go:\n" + json.dumps({**d, **over}) + "\n"


def test_parse_reply_resolves_refs_to_feed_links_and_is_strict():
    items = [{"id": 1, "src": "Fed", "title": "Fed holds", "url": "https://example.com/fed", "t": 1, "summary": ""}]
    b = digest.parse_reply(reply(), items)
    assert b["tilt"] == "neutral" and b["watch"] == ["CPI Thursday"]
    assert b["points"][0]["links"] == [{"src": "Fed", "title": "Fed holds", "url": "https://example.com/fed"}]  # 99 / "x" dropped
    b = digest.parse_reply(reply(points=[{"text": "t", "refs": []}] * 9), items)
    assert len(b["points"]) == 6
    for bad in (reply(tilt="moon"), reply(confidence="certain"), reply(points=[]), "no json here"):
        with pytest.raises(ValueError):
            digest.parse_reply(bad, items)


def test_due_once_per_utc_day_after_the_hour_with_a_retry_gap():
    st = Mem()
    assert not digest.due(st, ms(2026, 9, 23, 5), D)          # before hour_utc
    assert digest.due(st, ms(2026, 9, 23, 7), D)
    st.set("digest_try", ms(2026, 9, 23, 7))
    assert not digest.due(st, ms(2026, 9, 23, 8), D)           # failed an hour ago: wait
    assert digest.due(st, ms(2026, 9, 23, 9, 30), D)
    st.set("digest", {"day": "2026-09-23"})
    assert not digest.due(st, ms(2026, 9, 23, 20), D)          # done for today
    assert digest.due(st, ms(2026, 9, 24, 7), D)


class Resp:
    def __init__(self, content=b"", js=None, status=200):
        self.content, self._js, self.status_code = content, js, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._js


def feeds_get(url, headers, timeout):
    now = ms(2026, 9, 23, 6)
    xs = "".join(f"<item><title>{url} story {i}</title><link>https://example.com/{i}</link>"
                 f"<pubDate>{dt.datetime.fromtimestamp((now - i * H) / 1000, dt.timezone.utc):%a, %d %b %Y %H:%M:%S GMT}</pubDate></item>"
                 for i in range(4))
    return Resp(f"<rss><channel>{xs}</channel></rss>".encode())


def test_run_stores_a_brief_and_sends_only_public_data():
    sent = {}

    def post(url, timeout, headers, json):
        sent.update(headers=headers, body=json)
        return Resp(js={"content": [{"type": "text", "text": reply()}], "usage": {"input_tokens": 900, "output_tokens": 200}})

    st = Mem({"positions_secret": "0xabc"})
    cfg = dict(D, feeds=[["A", "a"], ["B", "b"]])
    b = digest.run(st, ms(2026, 9, 23, 6), cfg, {"fng": None, "tape": [], "events": []}, {"anthropic": "sk-test"}, get=feeds_get, post=post)
    assert b["day"] == "2026-09-23" and st.get("digest") == b and st.get("digest_try") == ms(2026, 9, 23, 6)
    assert b["n_items"] == 8 and b["sources"] == ["A", "B"]
    assert sent["headers"]["x-api-key"] == "sk-test" and sent["body"]["model"] == D["model"]
    assert "0xabc" not in json.dumps(sent["body"])


def test_api_errors_report_status_only_and_still_record_the_attempt():
    def post(url, timeout, headers, json):
        return Resp(js={"error": {"type": "authentication_error", "message": "invalid x-api-key"}}, status=401)

    st = Mem()
    with pytest.raises(RuntimeError) as e:
        digest.run(st, ms(2026, 9, 23, 6), dict(D, feeds=[["A", "a"], ["B", "b"]]), {}, {"anthropic": "sk-secret"}, get=feeds_get, post=post)
    assert str(e.value) == "Claude API HTTP 401 (authentication_error)" and "sk-secret" not in str(e.value)
    assert st.get("digest_try") == ms(2026, 9, 23, 6) and st.get("digest") is None


def test_provider_choice_follows_config_and_the_keys_actually_set():
    both = {"openrouter": "or-key", "anthropic": "an-key"}
    assert digest.pick(D, both) == ("openrouter", "or-key")                       # auto prefers OpenRouter
    assert digest.pick(D, {"openrouter": "  ", "anthropic": "an-key\n"}) == ("anthropic", "an-key")  # blank = unset
    assert digest.pick(dict(D, provider="anthropic"), both) == ("anthropic", "an-key")
    assert digest.pick(dict(D, provider="openrouter"), {"anthropic": "an-key"}) is None
    assert digest.pick(D, {}) is None


def test_openrouter_request_uses_the_fallback_list_and_records_the_model_that_answered():
    sent = {}

    def post(url, timeout, headers, json):
        sent.update(url=url, headers=headers, body=json)
        return Resp(js={"model": "anthropic/claude-sonnet-5",  # the first choice was down: fallback answered
                        "choices": [{"message": {"role": "assistant", "content": reply()}}],
                        "usage": {"prompt_tokens": 5000, "completion_tokens": 700}})

    st = Mem()
    b = digest.run(st, ms(2026, 9, 23, 6), dict(D, feeds=[["A", "a"], ["B", "b"]]), {}, {"openrouter": "or-key"},
                   get=feeds_get, post=post)
    assert sent["url"] == digest.OPENROUTER_URL and sent["headers"]["Authorization"] == "Bearer or-key"
    assert sent["body"]["models"] == D["openrouter_models"] and sent["body"]["models"][0] == "anthropic/claude-opus-5.5"
    assert "model" not in sent["body"]
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user"]
    assert "untrusted" in sent["body"]["messages"][0]["content"]
    assert b["model"] == "anthropic/claude-sonnet-5" and b["provider"] == "openrouter"


def test_openrouter_errors_carry_no_key_including_an_upstream_error_inside_a_200():
    for resp, msg in ((Resp(js={"error": {"code": 402, "message": "Insufficient credits"}}, status=402), "OpenRouter HTTP 402 (402)"),
                      (Resp(js={"error": {"code": 502, "message": "upstream"}}), "OpenRouter upstream error (502)")):
        st = Mem()
        with pytest.raises(RuntimeError) as e:
            digest.run(st, ms(2026, 9, 23, 6), dict(D, feeds=[["A", "a"], ["B", "b"]]), {}, {"openrouter": "or-secret"},
                       get=feeds_get, post=lambda url, timeout, headers, json, r=resp: r)
        assert str(e.value) == msg and "or-secret" not in str(e.value)
        assert st.get("digest_try") == ms(2026, 9, 23, 6)


def test_no_key_means_no_attempt_at_all():
    st = Mem()
    assert digest.run(st, ms(2026, 9, 23, 6), D, {}, {}, get=feeds_get, post=None) is None
    assert st.get("digest_try") is None


def test_too_few_headlines_skips_without_calling_the_api():
    def post(*a, **k):
        raise AssertionError("must not call the API")

    st = Mem()
    assert digest.run(st, ms(2026, 9, 23, 6), dict(D, feeds=[["A", "a"]]), {}, {"anthropic": "k"},
                      get=lambda url, headers, timeout: Resp(RSS), post=post) is None
