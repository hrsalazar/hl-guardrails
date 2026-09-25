"""Today's market state, for the lessons and the Now card (README "Discipline aids").

The same three legs and the same composite as the pre-registered market-state study
(docs/research/regime-study.md; `backtest.regime_states` calls the functions below, so the live
label and the tested one can't drift apart):

  trend   BTC's last closed daily bar above its 200-day EMA -> btc_up, else btc_down
  macro   HY credit spread below its 50-day EMA and S&P 500 above its 200-day -> macro_on;
          both the other way -> macro_off; one of each -> macro_mixed (hlg.macro)
  crowd   Fear & Greed <= 44 fear, >= 56 greed, else neutral (hlg.sentiment)

  risk_off  >= 2 legs off;  risk_on  >= 2 legs on and none off;  mixed  otherwise

The study found the strategy behaves the same in every state (2 of 44 tests passed, the number
chance produces). So the state is context for *you*, never a signal: messages built on it talk about
the pressure the market puts on the trader and quote only what held in every state (STUDY).
"""
import pandas as pd

from .common import log
from .scanner import candles, ema

D_MS = 86_400_000

# docs/research/regime-study.md, results of 2026-09-25 -- the only numbers a state message may quote
STUDY = {"pf": {"risk_on": 1.33, "mixed": 1.98, "risk_off": 1.36},
         "win_rate": {"risk_on": 0.37, "mixed": 0.37, "risk_off": 0.35},
         "entries_month": [8, 12], "losing_run": [7, 18], "trades": 406}


def crowd_of(fng):
    if fng is None:
        return None
    return "fear" if fng <= 44 else "greed" if fng >= 56 else "neutral"


def macro_of(hy_stress, spx_bull):
    if hy_stress is None or spx_bull is None:
        return None
    if not hy_stress and spx_bull:
        return "macro_on"
    if hy_stress and not spx_bull:
        return "macro_off"
    return "macro_mixed"


def composite(trend, macro, crowd):
    on = (trend == "btc_up") + (macro == "macro_on") + (crowd == "greed")
    off = (trend == "btc_down") + (macro == "macro_off") + (crowd == "fear")
    return "risk_off" if off >= 2 else "risk_on" if on >= 2 and off == 0 else "mixed"


def btc_trend(inf, state, now_ms):
    """BTC vs its 200-day EMA on the last closed daily bar: one candle request per UTC day, kept in
    the state. Fail-soft: yesterday's reading stays (and says its date)."""
    day = now_ms // D_MS
    cur = state.get("btc_trend")
    if isinstance(cur, dict) and cur.get("day") == day:
        return cur
    try:
        df = candles(inf, "BTC", "1d", 420)
        df = df[df.t < pd.Timestamp(day * D_MS, unit="ms", tz="UTC")]  # closed bars only
        if len(df) < 201:
            raise ValueError(f"only {len(df)} daily bars")
        e = ema(df.c, 200)
        cur = {"day": day, "up": bool(df.c.iloc[-1] > e.iloc[-1]), "close": float(df.c.iloc[-1]), "ema200": float(e.iloc[-1])}
        state.set("btc_trend", cur)
    except Exception as e:  # noqa: BLE001 - context only; never fails the run
        log.error("btc trend failed: %s", e)
    return cur if isinstance(cur, dict) else None


def build(trend, macro_ctx, fng):
    """The dashboard's `market_state`, or None with no leg at all."""
    t = None if not trend else ("btc_up" if trend["up"] else "btc_down")
    m = macro_of(macro_ctx.get("hy_stress"), macro_ctx.get("spx_bull")) if macro_ctx else None
    c = crowd_of(fng.get("value")) if isinstance(fng, dict) else None
    if t is None and m is None and c is None:
        return None
    return {"state": composite(t, m, c), "trend": t, "macro": m, "crowd": c,
            "fng": fng.get("value") if isinstance(fng, dict) else None, "study": STUDY}
