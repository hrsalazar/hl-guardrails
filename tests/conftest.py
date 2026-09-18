"""Shared fixtures and fakes for the hl-guardrails test suite.

Nothing here ever touches the network: FakeInfo returns canned responses in place of the
Hyperliquid Info client, and Notifier is exercised with telegram disabled (the config default),
so `.send()` only ever appends to `.collected` / logs -- it never calls out.
"""
import time

import pytest


NOW_MS = int(time.time() * 1000)


def base_cfg():
    return {
        "account": "0xTEST00000000000000000000000000000000000",
        "poll_seconds": 60,
        "state_file": "state.json",
        "rules": {
            "risk_per_trade_pct": 1.5,
            "max_positions": 3,
            "max_same_direction": 2,
            "max_gross_exposure_x": 3.0,
            "max_leverage": 5,
            "max_hold_days": 7,
            "daily_loss_limit_pct": 3.0,
            "weekly_loss_limit_pct": 6.0,
            "allowed_coins": ["BTC", "ETH", "SOL"],
            "no_add_underwater": True,
        },
        "scanner": {
            "strategy": "breakout",
            "coins": ["BTC", "ETH", "SOL"],
            "watch_coins": [],
            "interval_minutes": 30,
            "timeframe": "1d",
            "stop_atr": 2.0,
            "trail_atr": 3.0,
            "rsi_long_max": 35,
            "rsi_short_min": 65,
            "level_proximity_pct": 3.0,
            "min_rr": 2.0,
            "prefer_short_when_funding_positive": True,
            "funding_carry_alert_apr": 20,
            # off by default in tests: scanner.run_once would otherwise fetch FRED over the network,
            # which breaks the no-network rule above and made CI 250x slower than local. The enabled
            # path is covered directly in test_macro.py with a stubbed fetch.
            "macro_context": False,
        },
        "telegram": {"enabled": False},
    }


def make_position(coin, sz, entry, upnl, position_value=None, leverage=3):
    return {
        "coin": coin,
        "szi": str(sz),
        "entryPx": str(entry),
        "unrealizedPnl": str(upnl),
        "positionValue": str(position_value if position_value is not None else abs(sz) * entry),
        "leverage": {"value": leverage},
    }


def make_user_state(equity, positions):
    return {
        "marginSummary": {"accountValue": str(equity)},
        "assetPositions": [{"position": p} for p in positions],
    }


def make_stop_order(coin, side, sz, trigger_px):
    return {"coin": coin, "reduceOnly": True, "side": side, "orderType": "Stop Market", "sz": str(sz), "triggerPx": str(trigger_px)}


def make_fill(coin, side, sz, time_ms):
    return {"coin": coin, "side": side, "sz": str(sz), "time": time_ms}


def make_portfolio(day_pnl, week_pnl, equity):
    """A perpDay/perpWeek history with one baseline entry far in the past (t=0, always inside
    any real day/week window) and one entry equal to `now` -- period_pnl() diffs the series'
    very last element against the last baseline entry at-or-before the period start, so this
    deterministically yields (day_pnl, equity) and (week_pnl, equity) regardless of wall clock."""
    return [
        ["perpDay", {
            "pnlHistory": [[0, "0"], [NOW_MS, str(day_pnl)]],
            "accountValueHistory": [[0, str(equity)], [NOW_MS, str(equity)]],
        }],
        ["perpWeek", {
            "pnlHistory": [[0, "0"], [NOW_MS, str(week_pnl)]],
            "accountValueHistory": [[0, str(equity)], [NOW_MS, str(equity)]],
        }],
    ]


class FakeInfo:
    """Stands in for hyperliquid.info.Info: returns canned data, never touches the network."""

    def __init__(self, *, user_state=None, open_orders=None, mids=None, fills=None,
                 ledger=None, portfolio=None, candles=None, meta_ctxs=None, meta=None):
        self._user_state = user_state if user_state is not None else make_user_state(10000, [])
        self._open_orders = open_orders if open_orders is not None else []
        self._mids = mids if mids is not None else {}
        self._fills = fills if fills is not None else []
        self._ledger = ledger if ledger is not None else []
        self._portfolio = portfolio if portfolio is not None else make_portfolio(0, 0, 10000)
        self._candles = candles if candles is not None else {}
        self._meta_ctxs = meta_ctxs
        self._meta = meta

    def user_state(self, acct):
        return self._user_state

    def all_mids(self):
        return self._mids

    def meta(self):
        return self._meta

    def meta_and_asset_ctxs(self):
        return self._meta_ctxs

    def post(self, path, body):
        t = body.get("type")
        if t == "frontendOpenOrders":
            return self._open_orders
        if t == "userFillsByTime":
            return self._fills
        if t == "userNonFundingLedgerUpdates":
            return self._ledger
        if t == "portfolio":
            return self._portfolio
        if t == "candleSnapshot":
            coin, interval = body["req"]["coin"], body["req"]["interval"]
            return self._candles[(coin, interval)]
        if t == "metaAndAssetCtxs":
            return self._meta_ctxs
        raise ValueError(f"FakeInfo: unhandled /info type {t!r}")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Enforce the promise at the top of this file. A test that reaches the network is slow, flaky
    and dependent on someone else's uptime -- one such slip took CI from 1s to 302s. Tests that
    need a canned response patch the specific call (see test_macro.py); anything else fails loudly
    here instead of quietly dialling out."""
    def blocked(*a, **k):
        raise AssertionError(f"test tried to reach the network: {a[:2]}")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)


@pytest.fixture
def cfg():
    return base_cfg()
