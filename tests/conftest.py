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


def make_position(coin, sz, entry, upnl, position_value=None, leverage=3, margin="cross"):
    return {
        "coin": coin,
        "szi": str(sz),
        "entryPx": str(entry),
        "unrealizedPnl": str(upnl),
        "positionValue": str(position_value if position_value is not None else abs(sz) * entry),
        "leverage": {"value": leverage, "type": margin},
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


def make_spot(usdc, **tokens):
    """A spotClearinghouseState payload: USDC is token 0, anything else is a named holding."""
    bal = [{"coin": "USDC", "token": 0, "total": str(usdc), "hold": "0"}]
    bal += [{"coin": k, "token": 100 + i, "total": str(v), "hold": "0"} for i, (k, v) in enumerate(tokens.items())]
    return {"balances": bal}


def _period_starts(now_ms=None):
    """00:00 UTC today and Monday 00:00 UTC, in ms -- where the monitor's Today / This week begin."""
    import datetime as _dt
    now = _dt.datetime.fromtimestamp((now_ms or NOW_MS) / 1000, _dt.timezone.utc)
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d0.timestamp() * 1000), int((d0 - _dt.timedelta(days=now.weekday())).timestamp() * 1000)


def _series(start_ms, pnl, value):
    """A window's history as HL shapes it: P&L 0 at the window's own start (t=0 here, always
    before any period start), a point exactly at the period start, and the live point at NOW."""
    return {"pnlHistory": [[0, "0"], [start_ms, "0"], [NOW_MS, str(pnl)]],
            "accountValueHistory": [[0, str(value)], [start_ms, str(value)], [NOW_MS, str(value)]]}


def _all_time(value, day_pnl=0.0):
    # the only series with a fixed baseline: the monitor anchors Today / This week on it, so it moves
    # with today's P&L like the real one (5000 = whatever the account made before today)
    return {"pnlHistory": [[0, "0"], [NOW_MS, str(5000 + day_pnl)]], "accountValueHistory": [[0, str(value)], [NOW_MS, str(value)]]}


def make_unified_portfolio(day_pnl, week_pnl, portfolio_value):
    """Unified accounts are judged on the whole-account "day"/"week" series, not perpDay/perpWeek."""
    d0, w0 = _period_starts()
    return [
        ["day", _series(d0, day_pnl, portfolio_value)],
        ["week", _series(w0, week_pnl, portfolio_value)],
        ["allTime", _all_time(portfolio_value, day_pnl)],
        # the perp series of a unified account is tiny and must NOT drive anything
        ["perpDay", _series(d0, -999, 1000)],
        ["perpWeek", _series(w0, -999, 1000)],
        ["perpAllTime", _all_time(1000, -999)],
    ]


def make_portfolio(day_pnl, week_pnl, equity):
    """perpDay/perpWeek histories with a point exactly at 00:00 UTC / Monday 00:00 UTC, so the
    monitor's anchor (hlg.guardrails.pnl_anchors, history case) yields (day_pnl, equity) and
    (week_pnl, equity) whatever the wall clock."""
    d0, w0 = _period_starts()
    return [
        ["perpDay", _series(d0, day_pnl, equity)],
        ["perpWeek", _series(w0, week_pnl, equity)],
        ["perpAllTime", _all_time(equity, day_pnl)],
    ]


class FakeInfo:
    """Stands in for hyperliquid.info.Info: returns canned data, never touches the network."""

    def __init__(self, *, user_state=None, open_orders=None, mids=None, fills=None,
                 ledger=None, portfolio=None, candles=None, meta_ctxs=None, meta=None,
                 abstraction="disabled", spot=None):
        self._user_state = user_state if user_state is not None else make_user_state(10000, [])
        self._open_orders = open_orders if open_orders is not None else []
        self._mids = mids if mids is not None else {}
        self._fills = fills if fills is not None else []
        self._ledger = ledger if ledger is not None else []
        self._portfolio = portfolio if portfolio is not None else make_portfolio(0, 0, 10000)
        self._candles = candles if candles is not None else {}
        self._meta_ctxs = meta_ctxs
        self._meta = meta
        self._abstraction = abstraction  # "disabled" = classic, the most common real value
        self._spot = spot if spot is not None else {"balances": []}

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
        if t == "userFunding":
            return []
        if t == "userNonFundingLedgerUpdates":
            return self._ledger
        if t == "portfolio":
            return self._portfolio
        if t == "candleSnapshot":
            coin, interval = body["req"]["coin"], body["req"]["interval"]
            return self._candles[(coin, interval)]
        if t == "metaAndAssetCtxs":
            return self._meta_ctxs
        if t == "userAbstraction":
            return self._abstraction
        if t == "spotClearinghouseState":
            return self._spot
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
