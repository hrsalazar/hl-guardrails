"""Synthetic dashboard payloads for the Playwright suite (tests/e2e).

Each `build_*` function returns a plain dict matching the real `alerts.json` schema
(`hlg/report.py`'s `out`), plus a `history()` helper for `history.json`. Values are chosen
deliberately, not randomly, so each dashboard branch is hit on purpose:

  - one gauge in each state (good / warn / crit), computed backwards from the thresholds in
    pwa/index.html's `band()` so a threshold change here would need a matching test change
  - a position with a full stop, one with none, one with partial coverage
  - a breakout alert with a chart (scan row carries `bars`) and one without (no matching row)
  - a liquidation cluster inside the 5% window and one outside it (the "Nothing within 5%" line)
  - a classic (non-unified) account in `build_empty`, alongside every "flat"/empty-state message

Reusing `hlg.vault` for the encrypted scenario keeps it byte-compatible with the real pipeline,
so the browser's WebCrypto path is exercised against real ciphertext, not a stand-in.
"""
import datetime as dt
import json
import random

from hlg import vault

H = 3_600_000
D = 24 * H


def now_ms():
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec="seconds")


def candles(n=40, start=0.50, drift=0.0025, vol=0.016, seed=1, breakout=True):
    rng = random.Random(seed)
    bars, p, t0 = [], start, now_ms() - (n + 1) * 4 * H
    for i in range(n):
        o = p
        c = o * (1 + rng.gauss(drift, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append([t0 + i * 4 * H, round(o, 5), round(h, 5), round(l, 5), round(c, 5)])
        p = c
    if breakout:
        level = max(b[2] for b in bars[-21:-1])
        bars[-1][4] = round(level * 1.018, 5)
        bars[-1][2] = round(level * 1.025, 5)
    return bars


def history(n=250, end_pv=12_133.0, unified=True, seed=3):
    """~2.6 days of 15-min points (thin_history keeps every run that recent, so nothing here is
    collapsed) -- enough span for the 24h range to be a strict subset of 7d."""
    rng = random.Random(seed)
    pv, out = end_pv, []
    for i in range(n - 1, -1, -1):
        t = now_ms() - i * 15 * 60_000
        pv *= 1 + rng.gauss(0.00004, 0.0021)
        row = {"t": iso(t), "equity": round(pv * 0.86, 2), "n_alerts": 0, "new": []}
        if unified:
            row["pv"] = round(pv, 2)
        out.append(row)
    out[-1]["pv" if unified else "equity"] = end_pv
    return out


def build_full(*, age_min=2, journal_closed=8):
    gen = now_ms() - age_min * 60_000
    pv = 12_133.0
    # Gauges, one in each state -- see band()/renderGauges in pwa/index.html:
    #   daily:  dayUsed  = max(0,-day_pnl)  / (day_start_equity  * 3% ) -> want >= .85  (crit)
    #   weekly: weekUsed = max(0,-week_pnl) / (week_start_equity * 6% ) -> want in [.5, .85) (warn)
    #   leverage: leverage/cap -> want < .66 (good);  ratio: ratio_pct -> want >= 50 (crit)
    day_start, week_start = 12_463.0, 12_563.0
    day_pnl, week_pnl = pv - day_start, pv - week_start
    assert 0.85 <= max(0, -day_pnl) / (day_start * 0.03)
    assert 0.5 <= max(0, -week_pnl) / (week_start * 0.06) < 0.85

    ena_bars = candles(seed=11)
    level = max(b[2] for b in ena_bars[-21:-1])

    scan = [
        {"coin": "ENA", "tf": "4h", "px": ena_bars[-1][4] * 1.004, "rsi4h": 66.4, "trend": "UP",
         "funding_apr": 11.2, "setup": "LONG", "stop": ena_bars[-1][4] - 2 * 0.012, "target": None, "rr": None,
         "hi20": level, "atr": 0.012, "urank": 13, "vlm24h": 5.1e7,
         "signal_close": ena_bars[-1][4], "level": level, "bars": ena_bars},
        # UNI: a live entry with no matching `bars` in this run's scan (e.g. its own bar hasn't
        # closed yet this cycle) -- signalChartFor must fall back to no chart, not throw.
        {"coin": "UNI", "tf": "1d", "px": 9.14, "rsi4h": 58.0, "trend": "UP", "funding_apr": 9.0,
         "setup": "LONG", "stop": 8.25, "target": None, "rr": None, "hi20": 9.40, "atr": 0.35,
         "urank": 8, "vlm24h": 8.6e7},
        {"coin": "TAO", "tf": "1d", "px": 420.0, "rsi4h": 58, "trend": "UP", "funding_apr": 14.0,
         "setup": None, "hi20": 428.0, "atr": 6.1, "near_atr": 0.22, "urank": 15, "vlm24h": 3.2e7},
        {"coin": "BTC", "tf": "1d", "px": 98_000.0, "rsi4h": 45, "trend": "DOWN", "funding_apr": 10.0,
         "setup": None, "rejected": "trend down", "hi20": 104_000.0, "atr": 2500.0, "urank": 1, "vlm24h": 3.2e9},
        {"coin": "xyz:CL", "tf": "1d", "px": 71.2, "rsi4h": 50, "trend": "UP", "funding_apr": 0,
         "setup": None, "watch": True, "hi20": 73.0, "atr": 1.1, "vlm24h": 4e6},
    ]

    alerts = [
        {"kind": "guardrail", "cat": "position", "key": "lev_ETH", "push": True, "new": True,
         "first_seen": gen - 20 * 60_000, "status": "live",
         "text": "ETH LONG 4 @ 3050 (uPnL -60): leverage 6x > max 5x. Lower it.",
         "summary": "ETH LONG 4 @ 3050 (uPnL -60): leverage 6x > max 5x. Lower it."},
        {"kind": "scanner", "cat": "entry", "key": "setup_ENA_LONG_2026-09-19 16:00", "push": True,
         "new": True, "first_seen": gen - 45 * 60_000, "status": "live",
         "valid_until": gen + 3 * H + 10 * 60_000, "meta": {"coin": "ENA", "tf": "4h"},
         "summary": f"BREAKOUT LONG ENA 4h · size 4200 (~2,600 USD) · stop {ena_bars[-1][4] - 0.024:.5g}",
         "text": (f"BREAKOUT LONG ENA @ {ena_bars[-1][4]:.5g}\n"
                  f"  4h close {ena_bars[-1][4]:.5g} on 2026-09-19 16:00 > 20-bar high | trend UP | 4h RSI 64\n"
                  f"  stop {ena_bars[-1][4] - 0.024:.5g} (2.0x ATR) | trail 3.0x ATR (0.036) below highest high | time stop day 21\n"
                  f"  size 4200 ENA (~2,600 USD) keeps loss at 150 USD = 1.5% of 9,800 USDC collateral\n"
                  f"  rules: limit entry near open (maker), stop placed BEFORE entry, no adds if red, no target - let the trail work\n"
                  f"  HL liq levels: shorts liq $131.2M within +5% (largest $74.3M at +3.6%) | longs liq $84.6M within -5% (largest $52.2M at -2.5%) - context only")},
        # window already closed -- exercises left()'s "window closing" branch
        {"kind": "scanner", "cat": "entry", "key": "setup_UNI_LONG_2026-09-18", "push": True,
         "new": False, "first_seen": gen - 9 * H, "status": "live", "valid_until": gen - 5 * 60_000,
         "meta": {"coin": "UNI", "tf": "1d"}, "summary": "BREAKOUT LONG UNI 1d · size 180 (~1,640 USD) · stop 8.25",
         "text": "BREAKOUT LONG UNI @ 9.1\n  1d close 9.14 on 2026-09-18 > 20-bar high | trend UP | 1d RSI 58\n  stop 8.25 (2.0x ATR)"},
        {"kind": "scanner", "cat": "heads_up", "key": "mom_TAO_1", "push": True, "new": False,
         "first_seen": gen - 1 * H, "status": "live", "valid_until": gen + 3 * H,
         "summary": "MOMENTUM TAO +9.4% in 4h", "meta": {"coin": "TAO"},
         "text": "MOMENTUM TAO up +9.4% in 4h - no setup here (not backtested at this speed), just a heads-up to go look"},
        {"kind": "scanner", "cat": "info", "key": "fund_ZEC_4", "push": False, "new": False,
         "first_seen": gen - 5 * H, "status": "live", "meta": {"coin": "ZEC"},
         "summary": "Funding ZEC +42% APR (longs crowded)",
         "text": ("FUNDING ZEC: +42% APR - longs are paying shorts, about $12/day per $10,000 held\n"
                   "  read: funding this high usually means the crowd is heavily long - a cost if "
                   "you're long here, more a caution flag than a bearish call\n"
                   "  the yield is capturable risk-free: hold spot + short an equal perp (delta-"
                   "neutral) to collect it without taking a market view\n"
                   "  context only - not backtested, not part of the breakout rule, never pushed to your phone")},
        {"kind": "scanner", "cat": "info", "key": "setup_BTC_LONG_2026-09-18", "push": False, "new": False,
         "first_seen": gen - 7 * H, "status": "live", "meta": {"coin": "BTC"},
         "summary": "BTC 1d breakout - already held, don't add", "text": "BREAKOUT LONG BTC [already in a position - do NOT add]"},
    ]
    ended = [
        {"kind": "scanner", "cat": "entry", "key": "setup_ARB_LONG_2026-09-19 08:00", "push": True,
         "first_seen": gen - 10 * H, "status": "missed", "why": "ran 7.2% past the signal close",
         "ended_at": gen - 2 * H, "summary": "BREAKOUT LONG ARB 4h · size 3000 · stop 0.41",
         "text": "BREAKOUT LONG ARB ..."},
        {"kind": "scanner", "cat": "entry", "key": "setup_XMR_LONG_2026-09-18", "push": True,
         "first_seen": gen - 30 * H, "status": "failed", "why": "back below the breakout level 322",
         "ended_at": gen - 5 * H, "summary": "BREAKOUT LONG XMR 1d · size 4 · stop 310",
         "text": "BREAKOUT LONG XMR ..."},
    ]

    positions = [
        {"coin": "SOL", "szi": "40", "entryPx": "108.5", "positionValue": "4468", "unrealizedPnl": "128",
         "liquidationPx": "61.2", "leverage": 3, "margin_type": "cross",
         "stop_px": 103.9, "stop_cov": 1.0, "risk_stop_px": 104.8},
        {"coin": "HYPE", "szi": "-60", "entryPx": "91.2", "positionValue": "5645", "unrealizedPnl": "-117",
         "liquidationPx": None, "leverage": 5, "margin_type": "cross",
         "stop_px": None, "stop_cov": 0.0, "risk_stop_px": 93.65},
        {"coin": "ETH", "szi": "4", "entryPx": "3050", "positionValue": "12507", "unrealizedPnl": "-60",
         "liquidationPx": "2610.0", "leverage": 6, "margin_type": "cross",
         "stop_px": 2990.0, "stop_cov": 0.4, "risk_stop_px": 2980.0},
    ]

    def liq_coin(px, above, below, coverage, extra=None):
        clusters = list(extra or [])
        if above:
            clusters.append({"side": "short", "px": px * (1 + above[0] / 100), "usd": above[1], "n": above[2], "pct": above[0]})
        if below:
            clusters.append({"side": "long", "px": px * (1 + below[0] / 100), "usd": below[1], "n": below[2], "pct": below[0]})
        near_above = {"usd": above[1] if above and 0 < above[0] <= 5 else 0.0,
                      "top_px": px * (1 + above[0] / 100) if above and 0 < above[0] <= 5 else None,
                      "top_pct": above[0] if above and 0 < above[0] <= 5 else None,
                      "top_usd": above[1] if above and 0 < above[0] <= 5 else None}
        near_below = {"usd": below[1] if below and -5 <= below[0] < 0 else 0.0,
                      "top_px": px * (1 + below[0] / 100) if below and -5 <= below[0] < 0 else None,
                      "top_pct": below[0] if below and -5 <= below[0] < 0 else None,
                      "top_usd": below[1] if below and -5 <= below[0] < 0 else None}
        return {"px": px, "coverage": coverage, "clusters": clusters, "near": {"above": near_above, "below": near_below}}

    liqmap = {
        "t": gen, "accounts": 564, "positions": 1936,
        "coins": {
            # BTC's within-5% total (95M+60M=155M) is kept clearly above ETH's (31.9M+100.5M=132.4M)
            # so the "most liquidation $ near price" default-selection test has an unambiguous answer.
            "BTC": liq_coin(81_611.0, (3.6, 95_000_000, 40), (-2.5, 60_000_000, 25),
                             0.45, extra=[{"side": "short", "px": 81_611 * 1.093, "usd": 104_900_000, "n": 60, "pct": 9.3}]),
            "ETH": liq_coin(2655.0, (2.3, 31_900_000, 20), (-4.4, 100_500_000, 55), 0.55),
            "NEAR": liq_coin(4.2652, None, (-4.0, 593_000, 3), 0.36),
            "HYPE": liq_coin(94.08, (3.1, 528_000, 2), None, 0.38),
            # entirely outside the 5% window -> exercises the "Nothing within 5%" line while still
            # showing up as a selectable coin (it does have a cluster, just a distant one)
            "ZEC": liq_coin(410.0, (12.0, 9_000_000, 6), None, 0.30),
        },
    }

    out = {
        "generated": iso(gen), "account": "0x" + "ab" * 20,
        "equity": 9800.0,
        "acct": {"unified": True, "base": 9800.0, "portfolio_value": pv, "ratio_pct": 61.2, "leverage": 1.1,
                 "maint_margin": 1117.0, "notional": 18620.0},
        "locked_until": 0, "day_start_equity": day_start, "week_start_equity": week_start,
        "day_pnl": day_pnl, "week_pnl": week_pnl,
        "positions": positions, "alerts": alerts, "ended": ended, "scan": scan,
        "macro": {"as_of": "2026-09-21", "hy": 3.21, "vix": 16.4, "hy_stress": False, "spx_bull": True, "risk_on": True},
        "market": [{"coin": "BTC", "chg24h_pct": 1.2, "vlm24h": 3.2e9, "oi_usd": 9.1e9, "vol_oi": 0.35, "premium_bps": 1.1},
                   {"coin": "ENA", "chg24h_pct": -2.4, "vlm24h": 5.1e7, "oi_usd": 6.2e7, "vol_oi": 0.82, "premium_bps": -3.0}],
        "tradfi": {"rows": [{"coin": "xyz:SP500", "px": 6812.4, "chg24h_pct": 0.4, "vlm24h": 2.1e8, "oi_usd": 4.4e8},
                            {"coin": "xyz:GOLD", "px": 4102.5, "chg24h_pct": -0.2, "vlm24h": 9.8e7, "oi_usd": 2.1e8}],
                   "dropped": 4},
        "liquidations": {"src": "baked", "rows": [{"coin": "BTC", "long_usd": 37.1e6, "short_usd": 627.5e6, "events": 100, "skew": -0.89},
                                                   {"coin": "ETH", "long_usd": 20.2e6, "short_usd": 489.2e6, "events": 56, "skew": -0.92}]},
        "rules": {"max_positions": 3, "max_gross_exposure_x": 3, "daily_loss_limit_pct": 3.0,
                  "weekly_loss_limit_pct": 6.0, "allowed_coins": ["BTC", "ETH", "SOL", "HYPE", "ENA", "UNI", "TAO", "ZEC"]},
        "journal": {
            "summary": {
                "closed": journal_closed, "open": 2, "win_rate": 3 / 8, "avg_r": 0.34, "pf": 1.59,
                "failed_early": 3, "failed_early_recovered": 1,
                "curve": [{"t": gen - (journal_closed - i) * 3 * D, "r": r, "coin": "SOL", "tf": "1d"}
                          for i, r in enumerate([-1, -1, 2.4, -0.6, -1, 4.1, -1, 0.8][:journal_closed])],
            },
            "recent": [
                {"coin": "ENA", "tf": "4h", "signal_day": "2026-09-19 16:00", "status": "open", "r": 0.8,
                 "max_r": 1.4, "failed_early": False, "close_loc": 0.86},
                {"coin": "SOL", "tf": "1d", "signal_day": "2026-09-12", "status": "stopped", "r": -1.0,
                 "max_r": 0.3, "failed_early": True, "close_loc": 0.41},
                {"coin": "ZEC", "tf": "1d", "signal_day": "2026-09-02", "status": "time", "r": 3.9,
                 "max_r": 5.2, "failed_early": False, "close_loc": 0.92},
                {"coin": "NEAR", "tf": "1d", "signal_day": "2026-09-20", "status": "pending", "r": None,
                 "max_r": 0, "failed_early": False, "close_loc": 0.70},
            ],
        },
        "universe": {"mode": "auto", "coins": 30, "day": "2026-09-22"},
        "liqmap": liqmap,
    }
    return out


def build_empty(*, age_min=95):
    """Flat, classic (non-unified) account, nothing anywhere -- every empty-state message and the
    classic-account labels ("Perp equity" / "Margin ratio"), plus the stale (>40min) indicator."""
    gen = now_ms() - age_min * 60_000
    return {
        "generated": iso(gen), "account": "0x" + "cd" * 20, "equity": 10_000.0,
        "acct": {"unified": False},
        "locked_until": 0, "day_start_equity": None, "week_start_equity": None,
        "day_pnl": 0.0, "week_pnl": 0.0,
        "positions": [], "alerts": [], "ended": [], "scan": [],
        "macro": None, "market": [], "tradfi": {"rows": [], "dropped": 0}, "liquidations": None,
        "rules": {"max_positions": 3, "max_gross_exposure_x": 3, "daily_loss_limit_pct": 3.0,
                  "weekly_loss_limit_pct": 6.0, "allowed_coins": ["BTC", "ETH", "SOL"]},
        "journal": {"summary": {"closed": 0, "open": 0}, "recent": []},
        "universe": {"mode": "fixed", "coins": 3, "day": None},
        "liqmap": None,
    }


LOCKED_STUB = {"locked": "encryption not configured: set the DASHBOARD_PASSPHRASE secret"}
PASSPHRASE = "correct horse battery staple e2e"


def build_encrypted_files(passphrase=PASSPHRASE):
    """(alerts_env, history_env) sealed with hlg.vault -- the real production format, so the
    browser's WebCrypto path is tested against real ciphertext, not a JS-side stand-in."""
    payload = build_full()
    hist = history(n=40, end_pv=payload["acct"]["portfolio_value"])
    v = vault.Vault(passphrase)
    return v.seal(payload, "alerts.json"), v.seal(hist, "history.json")


def write_scenario(dir_, alerts_obj, history_obj=None):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "alerts.json").write_text(json.dumps(alerts_obj))
    if history_obj is not None:
        (dir_ / "history.json").write_text(json.dumps(history_obj))
