import json
import logging
import os
import time
from pathlib import Path

import requests
import yaml
from hyperliquid.info import Info

log = logging.getLogger("hlg")
API = "https://api.hyperliquid.xyz"


def load_config(path="config.yaml"):
    """config.yaml, overlaid with an optional gitignored config.local.yaml, and the account taken
    from $HLG_ACCOUNT when set. The repo is public, so the wallet address lives in a secret (CI) or
    the local overlay (your machine) rather than in the committed file."""
    # Bytes, not text: PyYAML then detects the encoding itself. The local overlay is hand-made, and
    # on Windows that means a UTF-8 BOM (Notepad, PowerShell's -Encoding utf8) or UTF-16 (PowerShell's
    # `>`), either of which breaks a text read in the locale's default codepage.
    cfg = yaml.safe_load(Path(path).read_bytes())
    local = Path(path).with_name("config.local.yaml")
    if local.exists():
        cfg.update(yaml.safe_load(local.read_bytes()) or {})
    # strip: a secret set by piping or pasting routinely carries a trailing newline, which once
    # turned a valid address into a rejected one and stopped the monitor
    cfg["account"] = (os.environ.get("HLG_ACCOUNT") or "").strip() or cfg.get("account")
    return cfg


def require_account(cfg):
    """Only the monitors need an account; backtest/regime/miner load the same config without one."""
    a = str(cfg.get("account") or "")
    if not (a.startswith("0x") and len(a) == 42):
        raise SystemExit("no account configured: set HLG_ACCOUNT, or `account:` in config.local.yaml")
    return a


def _redact(record):
    """Actions logs on a public repo are readable by anyone with a GitHub account, and the monitor
    used to print equity, PnL and every position into them every 15 minutes. Fail closed: only
    errors and lines explicitly marked safe (extra={"safe": True}) get through, so a log call added
    later cannot leak account data by default -- it has to be opted in."""
    return record.levelno >= logging.ERROR or getattr(record, "safe", False)


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.environ.get("HLG_REDACT"):
        for h in logging.getLogger().handlers:
            h.addFilter(_redact)


class Notifier:
    def __init__(self, cfg):
        self.enabled = cfg.get("telegram", {}).get("enabled", False)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID")
        self._last = {}
        self.collected = {}
        self.meta = {}

    def send(self, text, key=None, cooldown_s=3600, push=True, **meta):
        """Send an alert; identical `key` is suppressed for cooldown_s. All alerts are kept in `collected`.

        push=False keeps an alert on the dashboard only: no Telegram here, no Web Push in hlg.report.
        Extra keyword arguments (cat, summary, valid_until, ...) ride along for hlg.alerts."""
        k = key or text
        self.collected[k] = text
        self.meta[k] = {"push": push, **meta}
        now = time.time()
        if key and now - self._last.get(key, 0) < cooldown_s:
            return
        if key:
            self._last[key] = now
        log.warning("ALERT %s", text.replace("\n", " | "))
        if push and self.enabled and self.token and self.chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat, "text": text},
                    timeout=10,
                )
            except requests.RequestException as e:
                log.error("telegram failed: %s", e)


class State:
    def __init__(self, path):
        self.path = Path(path)
        self.d = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v
        self.path.write_text(json.dumps(self.d, indent=1))


def info():
    return Info(API, skip_ws=True)


def fnum(x):
    return float(x)
