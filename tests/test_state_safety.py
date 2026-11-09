"""A failed read of the previous published state stops the run instead of overwriting it with an empty one."""
import pytest
import requests

from hlg import report


class R:
    def __init__(self, status, js=None, bad=False):
        self.status_code, self._js, self._bad = status, js, bad
        self.ok = 200 <= status < 300

    def json(self):
        if self._bad:
            raise ValueError("truncated")
        return self._js


def seq(*responses):
    it = iter(responses)

    def get(url, timeout):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return get


def test_never_published_is_none_and_a_good_read_returns_the_body():
    assert report.fetch_prev("https://x", "state.json", get=seq(R(404))) is None
    assert report.fetch_prev("https://x", "state.json", get=seq(R(200, {"a": 1}))) == {"a": 1}
    assert report.fetch_prev("", "state.json") is None


def test_transient_failures_are_retried():
    get = seq(requests.ConnectionError(), R(503), R(200, {"ok": True}))
    assert report.fetch_prev("https://x", "state.json", get=get, pause=0) == {"ok": True}


def test_still_unreachable_raises_instead_of_returning_an_empty_state():
    get = seq(R(503), R(200, bad=True), requests.Timeout())
    with pytest.raises(report.PrevUnavailable):
        report.fetch_prev("https://x", "state.json", get=get, pause=0)


def test_the_run_stops_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUT", tmp_path)
    monkeypatch.setattr(report, "load_config", lambda: {"account": "0xabc"})
    monkeypatch.setattr(report, "require_account", lambda cfg: None)
    monkeypatch.setenv("SITE_URL", "https://example.invalid")

    def boom(site_url, name):
        raise report.PrevUnavailable(name)
    monkeypatch.setattr(report, "fetch_prev", boom)
    assert report.main() == 2
    assert not list(tmp_path.iterdir())                              # nothing written, nothing published


def test_a_restore_run_reads_the_previous_state_from_the_backup_folder(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text('{"journal": [1]}')
    monkeypatch.setenv("HLG_PREV_DIR", str(tmp_path))
    assert report.fetch_prev("https://x", "state.json") == {"journal": [1]}
    assert report.fetch_prev("https://x", "news.json") is None          # not in the backup: none
    (tmp_path / "alerts.json").write_text("{broken")
    with pytest.raises(report.PrevUnavailable):
        report.fetch_prev("https://x", "alerts.json")


NOW = 1_800_000_000_000


def _published(minutes_ago):
    import datetime as dt
    t = dt.datetime.fromtimestamp(NOW / 1000 - minutes_ago * 60, dt.timezone.utc)
    return {"generated": t.isoformat(timespec="seconds")}


def test_a_site_over_an_hour_old_is_flagged_to_the_workflow(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert report.flag_stale_site(_published(161), NOW) is True
    assert "site_stale=true" in out.read_text()


def test_a_normal_gap_or_a_first_run_is_not_flagged(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert report.flag_stale_site(_published(25), NOW) is False         # a 15-minute run plus the CDN's 10
    assert report.flag_stale_site({}, NOW) is False                     # first run: nothing published yet
    assert report.flag_stale_site({"generated": "garbled"}, NOW) is False
    assert not out.exists()
