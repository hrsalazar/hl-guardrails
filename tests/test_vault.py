"""The privacy layer: what gets published, what gets logged.

The repo is public and GitHub Pages is static, so these are the only things standing between the
account and anyone with the URL. Each test pins one way it could quietly fail open.
"""
import json
import logging

import pytest
from cryptography.exceptions import InvalidTag

from hlg import common, report, vault

FAST = 1_000  # PBKDF2 iterations for tests; production uses vault.ITER (checked separately)
SECRET = {"account": "0x1111111111111111111111111111111111111111", "equity": 12345.67,
          "alerts": [{"key": "lev_BTC", "text": "BTC LONG 0.5 @ 80000 leverage 40x"}]}


def v(pw="correct horse", salt=None):
    return vault.Vault(pw, salt, iterations=FAST)


def test_round_trip():
    x = v()
    assert x.unseal(x.seal(SECRET, "alerts.json"), "alerts.json") == SECRET


def test_ciphertext_contains_none_of_the_plaintext():
    env = json.dumps(v().seal(SECRET, "alerts.json"))
    for leak in ("0x1111", "12345", "BTC", "lev_", "equity", "alerts"):
        assert leak not in env


def test_wrong_passphrase_fails_loudly():
    env = v("right").seal(SECRET, "alerts.json")
    with pytest.raises(InvalidTag):
        v("wrong", vault.salt_of(env)).unseal(env, "alerts.json")


def test_tampering_is_detected():
    x = v()
    env = x.seal(SECRET, "alerts.json")
    ct = bytearray(__import__("base64").b64decode(env["ct"]))
    ct[0] ^= 1
    env["ct"] = __import__("base64").b64encode(bytes(ct)).decode()
    with pytest.raises(InvalidTag):
        x.unseal(env, "alerts.json")


def test_a_file_cannot_be_served_as_another():
    """The file name is authenticated data: state.json's ciphertext won't open as alerts.json."""
    x = v()
    with pytest.raises(InvalidTag):
        x.unseal(x.seal(SECRET, "state.json"), "alerts.json")


def test_the_salt_carries_forward_so_the_key_is_stable_across_runs():
    first = v().seal(SECRET, "alerts.json")
    second_run = v(salt=vault.salt_of(first))  # what report.main does with the previous envelope
    assert second_run.unseal(first, "alerts.json") == SECRET
    assert second_run.seal(SECRET, "alerts.json")["salt"] == first["salt"]


def test_every_encryption_uses_a_fresh_iv():
    x = v()
    assert len({x.seal(SECRET, "alerts.json")["iv"] for _ in range(20)}) == 20


def test_production_kdf_strength():
    env = vault.Vault("pw").seal({}, "alerts.json")
    assert env["iter"] == vault.ITER >= 600_000 and env["kdf"] == "PBKDF2-SHA256"


def test_empty_passphrase_is_refused():
    with pytest.raises(ValueError):
        vault.Vault("")


# ---------------------------------------------------------------- publishing fails closed
@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUT", tmp_path)
    return tmp_path


def test_ci_without_a_passphrase_publishes_a_stub_not_the_data(site):
    report.publish("alerts.json", SECRET, None, in_ci=True)
    body = (site / "alerts.json").read_text()
    assert json.loads(body) == report.LOCKED_STUB
    assert "0x1111" not in body and "12345" not in body


def test_with_a_passphrase_only_ciphertext_is_published(site):
    x = v()
    report.publish("alerts.json", SECRET, x, in_ci=True)
    env = json.loads((site / "alerts.json").read_text())
    assert vault.is_envelope(env) and x.unseal(env, "alerts.json") == SECRET


def test_local_runs_without_a_passphrase_stay_plaintext(site):
    report.publish("alerts.json", SECRET, None, in_ci=False)  # site/ is gitignored locally
    assert json.loads((site / "alerts.json").read_text()) == SECRET


def test_load_prev_handles_every_shape_without_raising():
    x = v()
    env = x.seal(SECRET, "state.json")
    assert report.load_prev(env, "state.json", x) == SECRET
    assert report.load_prev(env, "state.json", None) is None          # can't read it: start fresh
    assert report.load_prev(env, "state.json", v("other", vault.salt_of(env))) is None  # rotated
    assert report.load_prev(report.LOCKED_STUB, "state.json", x) is None
    assert report.load_prev({"lock_until": 5}, "state.json", x) == {"lock_until": 5}  # pre-encryption
    assert report.load_prev(None, "state.json", x) is None


# ---------------------------------------------------------------- the public Actions log
def record(level, msg, **extra):
    r = logging.LogRecord("hlg", level, __file__, 1, msg, None, None)
    r.__dict__.update(extra)
    return r


def test_redaction_drops_account_lines_by_default():
    assert not common._redact(record(logging.WARNING, "ALERT ETH perp +1,000 + spot 2,000"))
    assert not common._redact(record(logging.INFO, "USDC collateral 10000 | day +100"))


def test_redaction_keeps_errors_and_opted_in_lines():
    assert common._redact(record(logging.ERROR, "webpush failed"))
    assert common._redact(record(logging.INFO, "market: 11 coins in 0.5s", safe=True))


def test_redaction_is_installed_only_when_asked(monkeypatch):
    root = logging.getLogger()
    before = {h: list(h.filters) for h in root.handlers}
    try:
        monkeypatch.setenv("HLG_REDACT", "1")
        common.setup_logging()
        assert root.handlers and all(common._redact in h.filters for h in root.handlers)
    finally:
        for h in root.handlers:
            h.filters[:] = before.get(h, [])


# ---------------------------------------------------------------- the address stays out of the repo
def test_account_comes_from_the_environment(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("account: null\nrules: {}\n")
    monkeypatch.setenv("HLG_ACCOUNT", "0x" + "ab" * 20)
    cfg = common.load_config(str(tmp_path / "config.yaml"))
    assert common.require_account(cfg) == "0x" + "ab" * 20


def test_account_from_a_gitignored_local_overlay(tmp_path, monkeypatch):
    monkeypatch.delenv("HLG_ACCOUNT", raising=False)
    (tmp_path / "config.yaml").write_text("account: null\nrules: {}\n")
    (tmp_path / "config.local.yaml").write_text("account: '0x" + "cd" * 20 + "'\n")
    assert common.require_account(common.load_config(str(tmp_path / "config.yaml"))) == "0x" + "cd" * 20


def test_missing_account_stops_the_monitors_but_not_the_research_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("HLG_ACCOUNT", raising=False)
    (tmp_path / "config.yaml").write_text("account: null\nbacktest: {start: '2024-01-01'}\n")
    cfg = common.load_config(str(tmp_path / "config.yaml"))  # backtest/miner/regime load this fine
    assert cfg["backtest"]["start"] == "2024-01-01"
    with pytest.raises(SystemExit):
        common.require_account(cfg)
