"""Bear-case check (hlg.bearcheck): once per fresh long signal, public data only, tracked never pushed."""
import json

from hlg import bearcheck as bc
from hlg import digest

from .test_digest import D, Resp
from .test_universe import Mem

H = 3_600_000
NOW = 1_790_000_000_000


def sig(coin="SOL", side="L", added=NOW - H, **kw):
    e = {"key": f"setup_{coin}_LONG_x|1d", "coin": coin, "tf": "1d", "signal_day": "2026-10-13", "sig_t": NOW - 30 * H,
         "added": added, "close": 150.0, "level": 148.0, "atr": 6.0, "close_loc": 0.8, "side": side,
         "status": "pending", "r": None, "failed_early": False}
    return e | kw


def test_headlines_match_the_ticker_or_the_coins_name_and_only_recent_ones():
    arch = {"days": {"d": [["CoinDesk", NOW - 2 * H, "Solana ETF filing advances"],
                           ["X", NOW - 3 * H, "SOL hits a 3-month high"],
                           ["X", NOW - 3 * H, "Consolidation in solar stocks"],          # 'sol' inside a word: no
                           ["X", NOW - 72 * H, "Solana outage"]]}}                     # older than 48h: no
    got = [t for _, _, t in bc.coin_news(arch, [{"src": "Y", "t": NOW - H, "title": "Bitcoin slips"}], "SOL", NOW)]
    assert got == ["Solana ETF filing advances", "SOL hits a 3-month high"]


def test_one_call_per_fresh_long_with_public_data_only_and_the_result_on_the_row():
    sent = []

    def post(url, timeout, headers, json):
        sent.append(json)
        return Resp(js={"content": [{"type": "text", "text": '{"case": "Funding is crowded.", "flag": "high"}'}], "usage": {}})

    jr = [sig(), sig("ETH", side="S"), sig("BTC", added=NOW - 20 * H), sig("DOGE", bear={"flag": "low"})]
    st = Mem({"journal": jr, "account_secret": "0xabc", "behavior": {"net": -1234.5}})
    n = bc.run(st, NOW, D, {"anthropic": "k"}, [{"coin": "SOL", "chg24h_pct": 3.1, "funding_apr": 40.0, "oi_usd": 5e8}],
               ["market state risk-on"], None, post=post)
    assert n == 1 and len(sent) == 1                                     # not the short, the stale one or the done one
    row = st.get("journal")[0]
    assert row["bear"]["flag"] == "high" and row["bear"]["case"] == "Funding is crowded." and row["bear"]["n_news"] == 0
    body = json.dumps(sent[0])
    assert "funding +40% APR" in body and "0xabc" not in body and "1234" not in body
    assert bc.run(st, NOW, D, {"anthropic": "k"}, [], [], None, post=post) == 0   # never twice


def test_a_failed_call_is_recorded_and_not_retried():
    def post(url, timeout, headers, json):
        return Resp(js={"content": [{"type": "text", "text": "no json here"}], "usage": {}})
    st = Mem({"journal": [sig()]})
    bc.run(st, NOW, D, {"anthropic": "k"}, [], [], None, post=post)
    assert st.get("journal")[0]["bear"] == {"t": NOW, "error": "ValueError"}
    assert bc.due(st.get("journal"), NOW) == []


def test_no_key_no_call():
    assert bc.run(Mem({"journal": [sig()]}), NOW, D, {}, [], [], None) == 0


def test_openrouter_gets_the_bear_check_schema():
    sent = {}

    def post(url, timeout, headers, json):
        sent.update(json)
        return Resp(js={"choices": [{"message": {"content": '{"case": "c", "flag": "low"}'}}], "model": "m"})
    bc.run(Mem({"journal": [sig()]}), NOW, D, {"openrouter": "k"}, [], [], None, post=post)
    assert sent["response_format"]["json_schema"]["name"] == "bear_check"
    assert sent["response_format"]["json_schema"]["schema"] == bc.SCHEMA


def _closed(flag, r, i):
    return sig(sig_t=i, status="stopped", r=r, bear={"flag": flag})


def test_the_verdict_waits_for_40_and_needs_high_flags_below_one_in_both_halves():
    few = [_closed("high", -1.0, i) for i in range(10)]
    assert bc.summary(few)["verdict"] == "pending"
    warn = [_closed("high" if i % 4 == 0 else "low", -1.0 if i % 4 == 0 else (2.0 if i % 2 else -1.0), i) for i in range(40)]
    s = bc.summary(warn)
    assert s["n"] == 40 and s["high"]["pf"] == 0.0 and s["rest"]["pf"] > 1.0
    assert s["verdict"] == "adopt as an alert line" and s["flags"]["high"] == 10
    same = [_closed("high" if i % 4 == 0 else "low", 2.0 if i % 3 == 0 else -1.0, i) for i in range(40)]
    assert bc.summary(same)["verdict"] == "no measurable warning"


def test_digest_still_uses_its_own_schema_by_default():
    sent = {}

    def post(url, timeout, headers, json):
        sent.update(json)
        return Resp(js={"choices": [{"message": {"content": "{}"}}]})
    digest.call_openrouter("k", ["m"], "s", "u", post=post)
    assert sent["response_format"]["json_schema"]["name"] == "daily_brief"
