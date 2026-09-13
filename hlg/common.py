import json, logging, os, time
from pathlib import Path

import requests
import yaml
from hyperliquid.info import Info

log = logging.getLogger("hlg")
API = "https://api.hyperliquid.xyz"


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class Notifier:
    def __init__(self, cfg):
        self.enabled = cfg.get("telegram", {}).get("enabled", False)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat = os.environ.get("TELEGRAM_CHAT_ID")
        self._last = {}

    def send(self, text, key=None, cooldown_s=3600):
        """Send an alert; identical `key` is suppressed for cooldown_s."""
        now = time.time()
        if key and now - self._last.get(key, 0) < cooldown_s:
            return
        if key:
            self._last[key] = now
        log.warning("ALERT %s", text.replace("\n", " | "))
        if self.enabled and self.token and self.chat:
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
