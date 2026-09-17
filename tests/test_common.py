import json

from hlg.common import Notifier, State


def test_state_round_trips_through_a_file(tmp_path):
    path = tmp_path / "state.json"
    s1 = State(path)
    s1.set("lock_until", 12345)
    s1.set("positions", {"BTC": {"sz": 1.0, "entry": 100}})

    s2 = State(path)  # fresh instance, same file
    assert s2.get("lock_until") == 12345
    assert s2.get("positions") == {"BTC": {"sz": 1.0, "entry": 100}}
    assert json.loads(path.read_text()) == s1.d


def test_state_missing_file_starts_empty(tmp_path):
    s = State(tmp_path / "does_not_exist.json")
    assert s.get("anything") is None
    assert s.get("anything", "default") == "default"


def test_notifier_disabled_by_default_never_touches_network(monkeypatch):
    calls = []
    monkeypatch.setattr("hlg.common.requests.post", lambda *a, **k: calls.append((a, k)))
    notif = Notifier({"telegram": {"enabled": False}})
    notif.send("hello", key="k1")
    assert calls == []
    assert notif.collected == {"k1": "hello"}


def test_notifier_collects_every_message_even_during_cooldown():
    notif = Notifier({"telegram": {"enabled": False}})
    notif.send("first", key="dup", cooldown_s=3600)
    notif.send("second", key="dup", cooldown_s=3600)
    # collected always reflects the latest text for that key
    assert notif.collected == {"dup": "second"}


def test_notifier_cooldown_suppresses_repeat_within_window(monkeypatch):
    # A settable fake clock, not a finite iterator: `hlg.common.time` is the real `time` module
    # (shared process-wide), so patching `.time` here also affects the stdlib `logging` module's
    # own internal time.time() call when send() -> log.warning() creates a LogRecord. That call
    # happens or doesn't depending on ambient logging config, so the fake must tolerate being
    # called any number of times rather than assuming exactly one call per send().
    clock = {"now": 1_700_000_000.0}  # realistic epoch seconds, so "now - 0" comfortably clears cooldown_s
    monkeypatch.setattr("hlg.common.time.time", lambda: clock["now"])
    notif = Notifier({"telegram": {"enabled": False}})

    notif.send("a", key="k", cooldown_s=3600)
    assert notif._last["k"] == clock["now"]

    clock["now"] += 0.5
    notif.send("b", key="k", cooldown_s=3600)  # within cooldown, should not refresh _last
    assert notif._last["k"] == 1_700_000_000.0

    clock["now"] += 5000.0
    notif.send("c", key="k", cooldown_s=3600)  # well past cooldown
    assert notif._last["k"] == clock["now"]
