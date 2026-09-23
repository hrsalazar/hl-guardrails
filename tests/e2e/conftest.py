"""Playwright fixtures for the dashboard UI suite.

One static file server for the whole session, rooted at a temp directory holding one subfolder
per data scenario -- each a full copy of pwa/ (so relative asset paths like `sw.js`/`config.js`
resolve exactly as they do in production) plus that scenario's own `alerts.json`/`history.json`.
Tests navigate to `{base_url}/{scenario}/index.html`.

Requires the browsers Playwright installs itself (`playwright install chromium`) -- see
requirements-e2e.txt. Not part of the default `pytest -q` run (see pytest.ini); run explicitly:

    pytest tests/e2e -q
"""
import http.server
import re
import shutil
import threading
from pathlib import Path

import pytest

from . import fixtures as F

PWA = Path(__file__).resolve().parents[2] / "pwa"
SCENARIOS = {
    "full": lambda: (F.build_full(), F.history()),
    "mature": lambda: (F.build_full(journal_closed=34), F.history()),
    "empty": lambda: (F.build_empty(), None),
    "briefpending": lambda: (F.build_brief_pending(), F.history()),
    "stub": lambda: (F.LOCKED_STUB, None),
}


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    root = tmp_path_factory.mktemp("hlg_e2e_site")
    for name, build in SCENARIOS.items():
        d = root / name
        shutil.copytree(PWA, d)
        alerts, hist = build()
        F.write_scenario(d, alerts, hist)
    crypt = root / "crypt"
    shutil.copytree(PWA, crypt)
    alerts_env, hist_env = F.build_encrypted_files()
    F.write_scenario(crypt, alerts_env, hist_env)

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **kw)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def url(base_url, scenario):
    return f"{base_url}/{scenario}/index.html"


@pytest.fixture(autouse=True)
def block_third_party(page):
    """The dashboard falls back to calling OKX directly from the browser when the baked
    liquidations snapshot is empty (pwa/index.html::liveLiq). Real UI tests should not depend on
    a third party's uptime or reachability from the test host, so this is stubbed for every test."""
    page.route(re.compile(r"^https://www\.okx\.com/"),
               lambda route: route.fulfill(status=200, content_type="application/json", body='{"data":[]}'))


@pytest.fixture
def console_errors(page):
    """Collect console errors and uncaught exceptions for the life of the test. Assert on this
    explicitly (`assert not console_errors`) rather than relying on an autouse fixture, so a test
    that expects a handled error (there are none here, but future ones might) can inspect first.

    "Failed to load resource" is filtered out: it's Chromium's own diagnostic for any non-2xx HTTP
    response, not something app code raises or can suppress, and the `empty`/`stub` scenarios
    deliberately serve no history.json (a fresh deployment before the first run) specifically to
    exercise fetchJson's `r.ok` fallback -- that 404 is the scenario working as intended, not a bug."""
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    return errs
