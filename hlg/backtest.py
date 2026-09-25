"""Daily-bar backtest of the setup fingerprints found by hlg.miner vs the current scanner rules.

    python -m hlg.backtest                # all variants, coins from config [backtest]
    python -m hlg.backtest --variant pullback_long --coins BTC ETH SOL

Signals on the daily close, entry at next open (maker fee), stop checked against the daily low/high
(gap -> filled at open), exits at taker fee. Real hourly funding from the API. Portfolio sizing:
risk_per_trade_pct of current equity per trade, max_positions concurrent, per-position notional
capped at max_lev x equity. Output: backtest_out/report.md + trades_<variant>.csv
"""
import argparse, json, time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import events, macro, market_state, sentiment, stablecoin
from .lines import MA_LINES, ma_lines
from .lines import above as _above
from .common import API, load_config, log, setup_logging
from .scanner import atr, ema, rsi

DEF = dict(
    coins=["BTC", "ETH", "SOL", "HYPE", "XRP", "NEAR", "DOGE", "LINK", "UNI", "AAVE", "SUI", "AVAX", "TON", "PENDLE", "ZEC"],
    start="2023-06-01", equity0=1000.0, risk_pct=1.5, max_positions=3, max_lev=3.0,
    maker_fee=0.00015, taker_fee=0.00045, cache_dir="miner_cache", out_dir="backtest_out",
    interval="1d",  # 1d | 4h | 1h ; indicator horizons stay in *days* (scaled to bars) unless native=True
    native=False,
)
INTERVAL_H = {"1d": 24, "4h": 4, "1h": 1}

VARIANTS = {
    # current scanner rule: trend-following pullback, both sides, target 20d extreme, 7d time stop
    "scanner_current": dict(side="both", trend="with", rsi_max=45, rsi_min=55, level="pullback", stop_atr=1.5,
                            target="range", min_rr=2.0, max_days=7, trail_atr=None),
    # miner finding: long-only pullback near 20d low / below EMA20, neutral-oversold RSI, let it run with trailing stop
    "pullback_long": dict(side="long", trend="any", rsi_max=50, level="pullback", stop_atr=1.5,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "pullback_long_7d": dict(side="long", trend="any", rsi_max=50, level="pullback", stop_atr=1.5,
                             target="range", min_rr=2.0, max_days=7, trail_atr=None),
    "pullback_long_counter": dict(side="long", trend="against", rsi_max=50, level="pullback", stop_atr=1.5,
                                  target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # miner finding: long breakout of 20d high in uptrend, trailing
    "breakout_long": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # sensitivity of the breakout rule (not optimised: one parameter moved at a time)
    "breakout_long_hold45": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                                 target=None, min_rr=0, max_days=45, trail_atr=3.0),
    "breakout_long_trail2": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
                                 target=None, min_rr=0, max_days=21, trail_atr=2.0),
    "breakout_long_stop15": dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=1.5,
                                 target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_long_anytrend": dict(side="long", trend="any", rsi_max=100, level="breakout", stop_atr=2.0,
                                   target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # short side of the breakout rule (close below 20d low in a downtrend) and both sides together
    "breakout_short": dict(side="short", trend="with", rsi_min=0, level="breakout", stop_atr=2.0,
                           target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_short_anytrend": dict(side="short", trend="any", rsi_min=0, level="breakout", stop_atr=2.0,
                                    target=None, min_rr=0, max_days=21, trail_atr=3.0),
    "breakout_both": dict(side="both", trend="with", rsi_max=100, rsi_min=0, level="breakout", stop_atr=2.0,
                          target=None, min_rr=0, max_days=21, trail_atr=3.0),
    # control: mirror of pullback_long on the short side (miner says this should be poor)
    "pullback_short": dict(side="short", trend="any", rsi_min=50, level="pullback", stop_atr=1.5,
                           target=None, min_rr=0, max_days=21, trail_atr=3.0),
}


# ----------------------------------------------------------------------------- data
def post(body, retries=10):
    for i in range(retries):
        r = requests.post(f"{API}/info", json=body, timeout=30)
        if r.status_code == 429:
            time.sleep(5 + 5 * i)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited")


def bars(coin, cache, interval="1d"):
    p = cache / f"bt_{interval}_{coin}.json"
    # a day-old cache would end before freshly fetched coins do, and look like a delisting
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        d = json.loads(p.read_text())
    else:
        d, end = [], int(time.time() * 1000)
        while True:  # API returns the last ~5000 candles before endTime -> page backwards
            b = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": 0, "endTime": end}})
            b = [x for x in b if x["t"] < (d[0]["t"] if d else end + 1)]
            if not b:
                break
            d = b + d
            if len(b) < 4000:
                break
            end = d[0]["t"] - 1
            time.sleep(0.2)
        p.write_text(json.dumps(d))
    if not d:
        return None
    step = f"{INTERVAL_H[interval]}h"
    df = pd.DataFrame(d)
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.floor(step)
    for c in "ohlcv":
        df[c] = df[c].astype(float)
    df = df.set_index("t").sort_index()
    df = df[df.index < pd.Timestamp.utcnow().floor(step)]  # drop the live (incomplete) candle
    return df


def funding(coin, start_ms, cache, interval="1d"):
    """Hourly funding rates -> per-bar sum (fraction of notional paid by longs)."""
    p = cache / f"bt_fund_{coin}.json"
    if p.exists():
        d = json.loads(p.read_text())
    else:
        d, t = [], start_ms
        while True:
            b = post({"type": "fundingHistory", "coin": coin, "startTime": t})
            if not b:
                break
            d += b
            if len(b) < 500:
                break
            t = b[-1]["time"] + 1
            time.sleep(0.15)
        p.write_text(json.dumps(d))
    if not d:
        return pd.Series(dtype=float)
    s = pd.DataFrame(d)
    s["t"] = pd.to_datetime(s.time, unit="ms", utc=True).dt.floor(f"{INTERVAL_H[interval]}h")
    s["r"] = s.fundingRate.astype(float)
    return s.groupby("t").r.sum()


def features(df, k=1):
    """k = bars per day; indicator windows are the daily-system windows x k (k=1 -> native bars)."""
    df = df.copy()
    df["ema20"], df["ema50"], df["rsi"], df["atr"] = ema(df.c, 20 * k), ema(df.c, 50 * k), rsi(df.c, 14 * k), atr(df, 14 * k)
    df["hi20"], df["lo20"] = df.h.rolling(20 * k).max().shift(1), df.l.rolling(20 * k).min().shift(1)
    # inputs for the optional entry filters, all read off the completed signal bar
    df["vol_ratio"] = df.v / df.v.rolling(20 * k).mean()             # breakout conviction
    df["atr_pctile"] = (df.atr / df.c).rolling(100 * k).rank(pct=True)  # where vol sits vs its own recent range
    df["ext_atr"] = (df.c - df.ema20) / df.atr                        # how stretched above the mean, in ATR
    df["ret20"] = df.c.pct_change(20 * k)                             # for the cross-sectional rank
    rng = (df.h - df.l).replace(0, np.nan)
    df["close_loc"] = (df.c - df.l) / rng                              # 1 = closed at the high, 0 = at the low
    df["brk_atr"] = (df.c - df.hi20) / df.atr                          # how far past the level it closed
    return df


def fomc_ahead(index, fomc_ms, hours=24):
    """True for a bar whose next-open fill lands within `hours` before an FOMC decision: signal at
    this bar's close, fill at the next bar's open (= this bar's start + its length), decision in
    (fill, fill + hours]. The meeting schedule is published a year ahead, so this is not lookahead."""
    if len(index) < 2 or not fomc_ms:
        return pd.Series(False, index=index, dtype="boolean")
    step = pd.Series(index).diff().median()
    # explicit unit: pandas 3 builds indexes at us or ns resolution depending on the source, and a
    # hard-coded divisor silently turned ms into seconds here -- every gap then looked huge and the
    # filter never fired (caught by test_fomc_window_flags_only_bars_...)
    fill = (index + step).as_unit("ms").asi8
    t = np.array(sorted(fomc_ms), dtype="int64")
    nxt = np.searchsorted(t, fill, side="right")  # first decision strictly after the fill
    ok = nxt < len(t)
    gap = np.where(ok, t[np.minimum(nxt, len(t) - 1)] - fill, np.iinfo("int64").max)
    return pd.Series(gap <= hours * 3_600_000, index=index, dtype="boolean")


def context(data, fund, macro_f=None, stbl_f=None, sent_f=None, fomc=None):
    """Adds the columns entry filters read that are *not* this coin's own chart: its funding, the
    BTC tape, where it ranks against the rest of the universe, the macro / stablecoin / Fear &
    Greed backdrop, and whether the fill lands just before an FOMC decision. Everything is as-of the
    signal bar (the backdrops already shifted a day in their own modules), so nothing here is
    knowable later than the bar that triggers the trade."""
    btc = data.get("BTC")
    btc_bull = (btc.c > ema(btc.c, 200)) if btc is not None else None
    rs_rank = pd.DataFrame({c: d.ret20 for c, d in data.items()}).rank(axis=1, pct=True)
    for c, d in data.items():
        d["funding_apr"] = fund[c].reindex(d.index).fillna(0.0) * 365 * 100
        d["btc_bull"] = btc_bull.reindex(d.index) if btc_bull is not None else np.nan
        d["rs_rank"] = rs_rank[c].reindex(d.index)
        for col in MACRO_COLS:
            d[col] = macro_f[col].reindex(d.index) if macro_f is not None and not macro_f.empty else np.nan
        for col in STBL_COLS:
            d[col] = stbl_f[col].reindex(d.index) if stbl_f is not None and not stbl_f.empty else np.nan
        for col in SENT_COLS:
            # daily series onto (possibly intraday) bars: the day's value applies to each of its bars
            d[col] = (sent_f[col].reindex(d.index.floor("D")).set_axis(d.index)
                      if sent_f is not None and not sent_f.empty else np.nan)
        d["fomc_next24"] = fomc_ahead(d.index, fomc) if fomc else np.nan
    return data


# ----------------------------------------------------------------------------- long-term lines
def ma_context(data, cache, iv):
    """Adds btc_above_* (BTC's close vs BTC's lines) and coin_above_* (the coin's own) to every
    coin's bars (ma_study)."""
    step = pd.Timedelta(hours=INTERVAL_H[iv])
    btc, btc_daily = data.get("BTC"), bars("BTC", cache, "1d")
    btc_lines = ma_lines(btc_daily) if btc is not None and btc_daily is not None and len(btc_daily) > 1 else None
    for c, d in data.items():
        if btc_lines is not None:
            for ln, flag in _above(pd.DataFrame({"c": btc.c.reindex(d.index)}), btc_lines, step).items():
                d[f"btc_above_{ln}"] = flag
        daily = bars(c, cache, "1d")
        own = _above(d, ma_lines(daily), step) if daily is not None and len(daily) > 1 else {}
        for ln in MA_LINES:
            d[f"coin_above_{ln}"] = own.get(ln, pd.NA)
    return data


# ----------------------------------------------------------------------------- filters
MACRO_COLS = ["hy_stress", "vix_calm", "spx_bull", "dxy_headwind", "risk_on"]
STBL_COLS = ["stbl_chg", "stbl_below_trend", "stbl_shrinking"]
SENT_COLS = ["fng", "fng_extreme_greed", "fng_extreme_fear"]


def _pass(v, test):
    """A filter with no reading has no opinion. Rejecting on NaN would quietly drop the early
    sample (before rolling windows fill) and make filtered variants incomparable to the baseline,
    which would look like an edge and be an artefact. `pd.isna` (not a bare float-NaN check) so
    this also catches pandas' nullable-boolean `pd.NA` -- see hlg.macro.features -- not only a
    missing float reading."""
    if v is None or pd.isna(v):
        return True
    return test(v)


# Thresholds are median splits or structurally motivated, never scanned for the best value on this
# sample: 2 ATR extension is exactly the stop distance, 0.5 is a median, the trend comparisons are
# each series against its own moving average. That is deliberate -- with this little data, a tuned
# threshold is a curve fit with extra steps.
FILTERS = {
    # this coin's own chart, beyond the breakout itself
    "vol_confirm": lambda k: _pass(getattr(k, "vol_ratio", None), lambda v: v >= 1.5),
    "squeeze": lambda k: _pass(getattr(k, "atr_pctile", None), lambda v: v <= 0.5),
    "not_extended": lambda k: _pass(getattr(k, "ext_atr", None), lambda v: v <= 2.0),
    # crypto-native, off this coin's chart
    "cheap_funding": lambda k: _pass(getattr(k, "funding_apr", None), lambda v: v <= 30),
    "btc_bull": lambda k: _pass(getattr(k, "btc_bull", None), bool),
    "rs_top_half": lambda k: _pass(getattr(k, "rs_rank", None), lambda v: v >= 0.5),
    # breakout-bar quality (entry study): no long upper wick / not already stretched past the level
    "strong_close": lambda k: _pass(getattr(k, "close_loc", None), lambda v: v >= 0.5),
    "near_level": lambda k: _pass(getattr(k, "brk_atr", None), lambda v: v <= 1.0),
    # macro / tradfi backdrop (hlg.macro)
    "no_credit_stress": lambda k: _pass(getattr(k, "hy_stress", None), lambda v: not v),
    "vix_calm": lambda k: _pass(getattr(k, "vix_calm", None), bool),
    "spx_bull": lambda k: _pass(getattr(k, "spx_bull", None), bool),
    "no_dxy_headwind": lambda k: _pass(getattr(k, "dxy_headwind", None), lambda v: not v),
    "risk_on": lambda k: _pass(getattr(k, "risk_on", None), bool),
    # stablecoin backdrop (hlg.stablecoin): the "money rotating out of stables = risk-on" hypothesis
    "no_stbl_growth": lambda k: _pass(getattr(k, "stbl_below_trend", None), bool),
    "stbl_outflow": lambda k: _pass(getattr(k, "stbl_shrinking", None), bool),
    # crowd sentiment (hlg.sentiment): the index's own published bands, not tuned cutoffs
    "fng_not_extreme_greed": lambda k: _pass(getattr(k, "fng_extreme_greed", None), lambda v: not v),
    "fng_not_extreme_fear": lambda k: _pass(getattr(k, "fng_extreme_fear", None), lambda v: not v),
    # scheduled event risk (hlg.events): don't open into an FOMC decision
    "no_fomc_entry": lambda k: _pass(getattr(k, "fomc_next24", None), lambda v: not v),
    # long-term lines from daily / weekly bars (ma_study, docs/research/ma-lines-study.md)
    **{f"{who}_above_{ln}": (lambda col: lambda k: _pass(getattr(k, col, None), bool))(f"{who}_above_{ln}")
       for who in ("btc", "coin") for ln in MA_LINES},
}

BASE_BREAKOUT = VARIANTS["breakout_long"]
for _f in FILTERS:
    VARIANTS[f"breakout+{_f}"] = {**BASE_BREAKOUT, "filters": [_f]}


# ----------------------------------------------------------------------------- signals
def signal(k, V):
    """k = completed daily bar. Returns (side, stop, target) or None."""
    if np.isnan(k.ema50) or np.isnan(k.atr) or np.isnan(k.lo20):
        return None
    for f in V.get("filters", ()):
        if not FILTERS[f](k):
            return None
    up = k.ema20 > k.ema50
    px = k.c
    out = []
    if V["side"] in ("long", "both"):
        ok_trend = {"with": up, "against": not up, "any": True}[V["trend"]]
        if V["level"] == "pullback":
            at = px <= k.lo20 * 1.03 or (px <= k.ema20 and px >= k.ema20 * 0.95)
            ok = ok_trend and k.rsi <= V["rsi_max"] and at
        else:  # breakout
            ok = ok_trend and px > k.hi20
        if ok:
            stop = px - V["stop_atr"] * k.atr
            tgt = k.hi20 if V["target"] == "range" else None
            out.append(("L", stop, tgt))
    if V["side"] in ("short", "both"):
        ok_trend = {"with": not up, "against": up, "any": True}[V["trend"]]
        if V["level"] == "pullback":
            at = px >= k.hi20 * 0.97 or (px >= k.ema20 and px <= k.ema20 * 1.05)
            ok = ok_trend and k.rsi >= V["rsi_min"] and at
        else:
            ok = ok_trend and px < k.lo20
        if ok:
            stop = px + V["stop_atr"] * k.atr
            tgt = k.lo20 if V["target"] == "range" else None
            out.append(("S", stop, tgt))
    for side, stop, tgt in out:
        if tgt is not None:
            rr = abs(tgt - px) / max(abs(px - stop), 1e-9)
            if rr < V["min_rr"]:
                continue
        return side, stop, tgt
    return None


# ----------------------------------------------------------------------------- engine
def _pyramid_add(p, px, d, V, P, eq):
    """Add a unit to an open long (README "Pyramiding", rules fixed before running): only while the
    shared trailing stop is at or above the first entry (the original unit can no longer lose), at
    most V["pyramid"] adds, each risking risk_pct to that shared stop, the coin's combined notional
    within max_lev x equity. Returns the entry fee (0.0 when no add is made)."""
    if p is None or p["side"] != "L" or len(p["adds"]) >= V.get("pyramid", 0) or p["stop"] < p["entry"]:
        return 0.0
    dist = px - p["stop"]
    if dist <= 0:  # gapped to or through the stop: the position exits today anyway
        return 0.0
    held = p["sz"] + sum(a["sz"] for a in p["adds"])
    sz = min(eq * P["risk_pct"] / 100 / dist, eq * P["max_lev"] / px - held)
    if sz <= 0:
        return 0.0
    fee = sz * px * P["maker_fee"]
    p["adds"].append(dict(entry=px, sz=sz, fee=fee, fund=0.0, start=d))
    return fee


def _close(p, exit_px, end, why, P, eq):
    """Every unit of a position exits together at one price; one trade row per unit (unit 0 = the
    original entry, 1.. = adds). Returns (rows, equity after)."""
    sgn = 1 if p["side"] == "L" else -1
    rows = []
    for u, unit in enumerate([p] + p.get("adds", [])):
        fee = unit["sz"] * exit_px * P["taker_fee"]
        pnl = sgn * (exit_px - unit["entry"]) * unit["sz"]
        net = pnl - fee - unit["fee"] + unit["fund"]
        eq += pnl - fee + unit["fund"]
        rows.append(dict(coin=p["coin"], side=p["side"], entry=unit["entry"], exit=exit_px, start=unit["start"], end=end,
                         days=(end - unit["start"]).total_seconds() / 86400, why=why, gross=pnl, fees=fee + unit["fee"],
                         fund=unit["fund"], net=net, net_pct=net / (eq - net) * 100, notional=unit["sz"] * unit["entry"],
                         unit=u))
    return rows, eq


def run(V, data, fund, P):
    """Portfolio simulation. Keys of `data` are instruments -- normally coins. For a combined book
    (combined_study) they are "COIN|tf" streams: P["coin_of"] maps each to its coin, so a coin held
    on one timeframe blocks an entry on the other (as the live scanner does), and P["mark"] gives
    per-coin closes on the finest grid for marking open positions to market between coarser bars."""
    coin_of, mark = P.get("coin_of") or {}, P.get("mark")
    base = lambda c: coin_of.get(c, c)  # noqa: E731
    # timeframe allocation (priority_study): tf_rank orders same-moment fills, tf_max caps the slots a
    # timeframe may hold, preempt lets a 1d fill close the worst open 4h position when slots are full
    tf_of = lambda c: c.split("|")[1] if "|" in c else None  # noqa: E731
    tf_rank, tf_max, preempt = P.get("tf_rank") or {}, P.get("tf_max") or {}, P.get("preempt", False)

    def px_at(c, d):
        if mark is not None and base(c) in mark and d in mark[base(c)].index:
            return mark[base(c)].at[d]
        return data[c].loc[d].c if d in data[c].index else None

    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    days = [d for d in days if d >= pd.Timestamp(P["start"], tz="UTC")]
    eq, peak, dd = P["equity0"], P["equity0"], 0.0
    open_pos, trades, curve = {}, [], []
    # coin -> {side, stop, tgt, sd, mode, level, atr, leg_lo, leg_hi, bars}. mode: "open" = fill at the
    # next open (the rule as backtested); "confirm" = only after the next bar also closes above the
    # level; "fib" = limit order at a retrace of the breakout leg (entry study, README "Entries").
    pending = {}
    entry = V.get("entry", "open")
    for d in days:
        # 1) fills for pending signals at today's open
        # Same-day signals compete for max_positions slots. Fill the most liquid first (trailing
        # 30d median notional, as of the signal day): fixed in advance, and the order the live
        # scanner lists them in. Dict order used to decide this, i.e. the config file's coin order.
        for coin, q in sorted(pending.items(), key=lambda kv: (-tf_rank.get(tf_of(kv[0]), 0), -_prio(P, kv[0], kv[1]["sd"]))):
            df = data[coin]
            if d not in df.index:
                continue
            k = df.loc[d]
            if q["mode"] == "await":      # confirm: waiting for this bar's close (step 3)
                continue
            if q["mode"] == "fib":
                q["bars"] += 1
                if q["bars"] > V.get("fib_valid", 5):
                    del pending[coin]
                    continue
                # level from the leg as known before this bar: no peeking at today's high
                lim = q["leg_hi"] - V["fib"] * (q["leg_hi"] - q["leg_lo"])
                q["leg_hi"] = max(q["leg_hi"], k.h)
                if k.l > lim:
                    continue
                px, intrabar = min(k.o, lim), True
            else:
                px, intrabar = k.o, False
            del pending[coin]
            if q.get("add"):
                eq -= _pyramid_add(open_pos.get(coin), px, d, V, P, eq)
                continue
            if coin in open_pos:
                continue
            if coin_of and base(coin) in {base(k) for k in open_pos}:
                continue  # held on the other timeframe: no second position in the same coin
            tf = tf_of(coin)
            if tf in tf_max and sum(tf_of(k) == tf for k in open_pos) >= tf_max[tf]:
                continue
            if len(open_pos) >= P["max_positions"]:
                victims = [k for k in open_pos if preempt and tf == "1d" and tf_of(k) == "4h" and d in data[k].index]
                if not victims:
                    continue
                # the 4h position doing worst at this open makes room, closed at that open
                v = min(victims, key=lambda k: data[k].loc[d].o / open_pos[k]["entry"])
                rows, eq = _close(open_pos[v], data[v].loc[d].o, d, "preempted", P, eq)
                trades += rows
                del open_pos[v]
            stop = q["stop"] if entry == "open" else px - V["stop_atr"] * q["atr"]
            dist = abs(px - stop)
            if dist <= 0:
                continue
            sz = eq * P["risk_pct"] / 100 / dist
            sz = min(sz, eq * P["max_lev"] / px)
            fee = sz * px * P["maker_fee"]
            eq -= fee
            open_pos[coin] = dict(coin=coin, side=q["side"], entry=px, sz=sz, stop=stop, tgt=q["tgt"], start=d, fee=fee,
                                  fund=0.0, best=px, sig_day=q["sd"], level=q["level"], bars=0,
                                  intrabar_day=d if intrabar else None, adds=[])
        # 2) manage open positions on today's bar
        for coin, p in list(open_pos.items()):
            df = data[coin]
            if d not in df.index:
                if coin in P.get("delisted", ()) and d > df.index[-1]:  # delisted: close at the last traded price
                    rows, eq = _close(p, df.iloc[-1].c, df.index[-1], "delisted", P, eq)
                    trades += rows
                    del open_pos[coin]
                continue
            k = df.loc[d]
            sgn = 1 if p["side"] == "L" else -1
            fr = fund[coin].get(d, 0.0)
            for unit in [p] + p.get("adds", []):
                unit["fund"] += -sgn * fr * unit["sz"] * k.c  # longs pay positive funding
            exit_px, why = None, None
            if p["side"] == "L":
                if k.l <= p["stop"]:
                    exit_px, why = min(k.o, p["stop"]), "stop"
                elif p["tgt"] and k.h >= p["tgt"]:
                    exit_px, why = max(k.o, p["tgt"]), "target"
            else:
                if k.h >= p["stop"]:
                    exit_px, why = max(k.o, p["stop"]), "stop"
                elif p["tgt"] and k.l <= p["tgt"]:
                    exit_px, why = min(k.o, p["tgt"]), "target"
            p["bars"] = p.get("bars", 0) + 1
            if exit_px is None and V.get("fail_exit_bars") and p["bars"] <= V["fail_exit_bars"] and k.c < p["level"]:
                exit_px, why = k.c, "failed"  # closed back below the breakout level: out now, not at the 2 ATR stop
            if exit_px is None and (d - p["start"]) >= pd.Timedelta(days=V["max_days"]):
                exit_px, why = k.c, "time"
            if exit_px is None and V["trail_atr"] and p.get("intrabar_day") != d:  # today's high may predate the fill
                p["best"] = max(p["best"], k.h) if sgn > 0 else min(p["best"], k.l)
                new_stop = p["best"] - sgn * V["trail_atr"] * k.atr
                p["stop"] = max(p["stop"], new_stop) if sgn > 0 else min(p["stop"], new_stop)
            if exit_px is not None:
                rows, eq = _close(p, exit_px, d, why, P, eq)
                trades += rows
                del open_pos[coin]
        # 3) mark to market + signals at close
        mtm = eq + sum((1 if p["side"] == "L" else -1) * (px_at(c, d) - u["entry"]) * u["sz"]
                       for c, p in open_pos.items() if px_at(c, d) is not None for u in [p] + p.get("adds", []))
        peak = max(peak, mtm)
        dd = min(dd, mtm / peak - 1)
        curve.append((d, mtm))
        # confirm: the bar after the breakout has closed -- keep the entry only if it held the level
        for coin, q in list(pending.items()):
            if q["mode"] == "await" and d in data[coin].index and d > q["sd"]:
                if data[coin].loc[d].c > q["level"]:
                    q["mode"] = "open"
                else:
                    del pending[coin]
        elig = P.get("elig_by_day")
        for coin in (elig.get(d.floor("D"), ()) if elig is not None else data):
            df = data[coin]
            if d not in df.index:
                continue
            if coin in open_pos:
                # pyramiding: a fresh breakout on a coin already held becomes an add order for the
                # next open; _pyramid_add decides at the fill whether the rules allow it
                if V.get("pyramid") and entry == "open":
                    s = signal(df.loc[d], V)
                    if s and s[0] == "L":
                        pending[coin] = dict(side="L", stop=s[1], tgt=None, sd=d, level=df.loc[d].hi20,
                                             atr=df.loc[d].atr, mode="open", add=True)
                continue
            if coin in pending and pending[coin]["mode"] in ("fib", "await"):
                continue  # a retrace order or a confirmation is already working
            k = df.loc[d]
            s = signal(k, V)
            if s:
                side, stop, tgt = s
                pending[coin] = dict(side=side, stop=stop, tgt=tgt, sd=d, level=k.hi20, atr=k.atr, leg_lo=k.lo20,
                                     leg_hi=k.h, bars=0, mode={"open": "open", "confirm": "await", "fib": "fib"}[entry])
    T = pd.DataFrame(trades)
    C = pd.Series(dict(curve))
    return T, C, dd


def stats(T, C, dd, P):
    if T.empty:
        return dict(trades=0)
    yrs = (C.index[-1] - C.index[0]).days / 365.25
    g = T.net[T.net > 0].sum()
    l = -T.net[T.net < 0].sum()
    daily_ret = C.resample("1D").last().dropna().pct_change().dropna()
    return dict(trades=len(T), win_rate=(T.net > 0).mean(), pf=g / max(l, 1e-9),
                avg_win_pct=T.net_pct[T.net > 0].mean(), avg_loss_pct=T.net_pct[T.net < 0].mean(),
                net=C.iloc[-1] - P["equity0"], total_return_pct=(C.iloc[-1] / P["equity0"] - 1) * 100,
                cagr_pct=((C.iloc[-1] / P["equity0"]) ** (1 / yrs) - 1) * 100, max_dd_pct=dd * 100,
                sharpe=daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else 0,
                fees=T.fees.sum(), funding=T.fund.sum(), avg_days=T.days.mean(),
                exits=T.why.value_counts().to_dict())


def by(T, key):
    g = T.groupby(key).net
    return pd.DataFrame(dict(trades=g.size(), net=g.sum().round(0), wr=g.apply(lambda x: (x > 0).mean()).round(2),
                             pf=g.apply(lambda x: min(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 99)).round(2)))


def bootstrap_pf(base_net, n, actual_pf, n_boot=20_000, seed=0):
    """Where does a filtered subset's PF sit against randomly dropping the same number of trades?

    A filter that keeps 70% of trades will change PF just by luck -- this strategy makes its money
    on ~20% of trades, so which ones you happen to keep dominates everything. Returns the percentile
    of `actual_pf` in that null distribution. Anything between 5 and 95 is indistinguishable from
    picking at random, no matter how good the rationale sounded."""
    rng = np.random.default_rng(seed)
    if n <= 0 or n > len(base_net):
        return float("nan")
    draws = np.array([_pf_of(rng.choice(base_net, size=n, replace=False)) for _ in range(n_boot)])
    return float((draws < actual_pf).mean() * 100)


def _pf_of(net):
    g, l = net[net > 0].sum(), -net[net < 0].sum()
    return min(g / max(l, 1e-9), 99)


def _prio(P, coin, day):
    m = P.get("liq_med")
    if m is None:
        return 0.0
    try:
        v = m.at[day.floor("D"), coin]
    except KeyError:
        return 0.0
    return 0.0 if pd.isna(v) else float(v)


# ----------------------------------------------------------------------------- universe
def daily_notional(df):
    """USD traded per UTC day, from the coin's own candles (base volume x close)."""
    return (df.v * df.c).resample("1D").sum()


def liquidity(data, window=30):
    """Trailing median daily notional, day x coin, shifted a day: the value on day T only uses days
    up to T-1, so ranking on it cannot see the day it is used on."""
    vol = pd.DataFrame({c: daily_notional(df) for c, df in data.items()})
    return vol, vol.rolling(window, min_periods=20).median().shift(1)


def eligibility(data, U, window=30):
    """Point-in-time universe: on each day, coins listed >= min_age_days, with a trailing median
    daily notional >= min_vol_usd, and in the top_n by it. Delisted coins take part while they
    traded -- dropping them would test only the survivors. Returns (mask day x coin, liquidity)."""
    vol, med = liquidity(data, window)
    first = pd.Series({c: df.index[0].floor("D") for c, df in data.items()})
    age = pd.DataFrame({c: (vol.index - first[c]).days for c in vol.columns}, index=vol.index)
    ok = (med >= U["min_vol_usd"]) & (age >= U["min_age_days"]) & vol.notna()
    if U.get("survivors"):
        # the biased control: today's top_n, applied to all of history
        last = med.iloc[-1].where(vol.iloc[-5:].notna().all())
        keep = set(last[last >= U["min_vol_usd"]].nlargest(U["top_n"]).index)
        mask = vol.notna() & pd.DataFrame({c: c in keep for c in vol.columns}, index=vol.index)
        return mask, med
    rank = med.where(ok).rank(axis=1, ascending=False, method="first")
    return rank <= U["top_n"], med


def by_day(mask):
    return {d: [c for c in mask.columns[row]] for d, row in zip(mask.index, mask.values)}


def candidate_pool():
    """Every perp on the main dex, delisted included."""
    meta = post({"type": "meta"})
    return [u["name"] for u in meta["universe"]], {u["name"] for u in meta["universe"] if u.get("isDelisted")}


def halves(T, mid):
    """PF on each half of the sample, split by signal date. One shared portfolio path, not two
    independent sims, so this answers "did the edge persist" -- not "what would a fresh book have
    made in the second half". A filter that only works in the first half is a fitted filter."""
    out = {}
    for label, t in [("IS", T[T.start < mid]), ("OOS", T[T.start >= mid])]:
        g, l = t.net[t.net > 0].sum(), -t.net[t.net < 0].sum()
        out[f"{label}_n"] = len(t)
        out[f"{label}_pf"] = round(min(g / max(l, 1e-9), 99), 2) if len(t) else 0.0
        out[f"{label}_net"] = round(t.net.sum()) if len(t) else 0
    return out


UNIVERSE_STUDY = {
    # pre-registered before running: auto_top30 is the candidate, the rest are sensitivity/controls
    "auto_top30": dict(top_n=30, min_vol_usd=10e6, min_age_days=60),
    "auto_top15": dict(top_n=15, min_vol_usd=10e6, min_age_days=60),
    "auto_top50": dict(top_n=50, min_vol_usd=10e6, min_age_days=60),
    "auto_top30_survivors": dict(top_n=30, min_vol_usd=10e6, min_age_days=60, survivors=True),
}


def universe_study(P, iv):
    """python -m hlg.backtest --universe-study [--interval 4h]

    Does the breakout edge survive scanning the liquid market instead of a hand-picked list? Runs
    breakout_long on the fixed list and on point-in-time liquidity universes over the whole HL perp
    history, delisted coins included. Adoption rule, fixed before the first run: auto_top30 ships if
    PF >= 1.4, max DD no worse than the fixed list's + 5pp, and second-half PF > 1.2."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    pool, delisted = candidate_pool()
    log.info("candidate pool: %d perps (%d delisted)", len(pool), len(delisted))
    data = {}
    for c in pool:
        try:
            df = bars(c, cache, iv)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: bars failed (%s), skipped", c, e)
            continue
        if df is not None and len(df) >= 60:
            data[c] = features(df, 1)
    V = VARIANTS["breakout_long"]
    start = pd.Timestamp(P["start"], tz="UTC")
    fixed = [c for c in DEF["coins"] if c in data]
    masks = {name: eligibility(data, U) for name, U in UNIVERSE_STUDY.items()}
    liq_med = next(iter(masks.values()))[1]
    ever = set(fixed)
    for m, _ in masks.values():
        ever |= set(m.columns[m[m.index >= start].any()])
    start_ms = int(start.timestamp() * 1000)
    fund = {}
    for i, c in enumerate(sorted(ever)):
        fund[c] = funding(c, start_ms, cache, iv)
        if i % 10 == 0:
            log.info("funding %d/%d", i + 1, len(ever))
    fund = {c: fund.get(c, pd.Series(dtype=float)) for c in data}
    runs = {"fixed": (fixed, None)} | {n: (sorted(ever), by_day(m.loc[m.index >= start - pd.Timedelta(days=1)])) for n, (m, _) in masks.items()}
    rows, notes = {}, []
    for name, (coins, elig) in runs.items():
        sub = {c: data[c] for c in coins}
        Q = {**P, "liq_med": liq_med, "elig_by_day": elig, "delisted": delisted}
        T, C, dd = run(V, sub, {c: fund[c] for c in coins}, Q)
        s = stats(T, C, dd, Q)
        days = sorted(set().union(*[set(d.index) for d in sub.values()]))
        days = [d for d in days if d >= start]
        s |= halves(T, days[len(days) // 2])
        months = max((days[-1] - days[0]).days / 30.44, 1)
        s["signals_per_month"] = len(T) / months
        s["profit_share_new_coins"] = (T[~T.coin.isin(DEF["coins"])].net.sum() / T.net.sum()) if len(T) and T.net.sum() else float("nan")
        s["delisted_exits"] = int((T.why == "delisted").sum()) if len(T) else 0
        if elig is not None:
            m = masks[name][0]
            per_year = m[m.index >= start].sum(axis=1).groupby(m[m.index >= start].index.year).mean().round(1).to_dict()
            notes.append(f"- `{name}`: mean eligible coins per day by year {per_year}")
        rows[name] = s
        T.to_csv(out / f"universe_{iv}_{name}.csv", index=False)
        log.info("%s: trades %s pf %.2f dd %.1f", name, s.get("trades"), s.get("pf", 0), s.get("max_dd_pct", 0))
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "sharpe", "IS_pf", "OOS_pf",
            "signals_per_month", "profit_share_new_coins", "delisted_exits"]
    base, cand = rows["fixed"], rows.get("auto_top30", {})
    passed = bool(cand.get("trades")) and cand["pf"] >= 1.4 and cand["max_dd_pct"] >= base["max_dd_pct"] - 5 and cand["OOS_pf"] > 1.2
    rep = [f"# Universe study ({iv}, breakout_long, {P['start']} -> now)\n",
           f"Pool: {len(data)} perps with history, {len(delisted)} delisted in meta. Fixed list: {', '.join(fixed)}. "
           "Same-day signals fill most-liquid first in every run.\n",
           S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n",
           "Eligibility per day:\n" + "\n".join(notes) + "\n",
           f"Adoption rule (fixed before running): auto_top30 PF >= 1.4, max DD >= fixed - 5pp, OOS PF > 1.2 -> **{'PASS' if passed else 'FAIL'}**\n"]
    (out / f"universe_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rows, passed


def candidate_study(P, iv, fixed, candidates):
    """python -m hlg.backtest --candidate-study INJ AVAX TAO FET [--interval 4h]

    Does adding specific named coins to the live scanner list pay for itself? Not the liquidity-
    ranked `--universe-study` (which asks "should the list rank itself"): this asks "should these
    particular coins be on it", always eligible once listed -- the question actually being asked
    when someone names a coin. Runs breakout_long on `fixed` (the live `scanner.coins`) alone and
    on `fixed + candidates`, same window, same-day signals filling most-liquid first, same
    adoption-rule discipline as the universe study. Adoption rule, fixed before running: the
    candidate list ships only if combined PF >= 1.4, max DD no worse than the fixed list's + 5pp,
    second-half (OOS) PF > 1.2, AND the candidates' own net P&L is not negative -- a coin that only
    looks good riding the other 11's shared drawdown protection is not actually pulling weight.
    """
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(P["start"], tz="UTC")
    start_ms = int(start.timestamp() * 1000)
    data, fund = {}, {}
    for c in dict.fromkeys(fixed + candidates):
        df = bars(c, cache, iv)
        if df is None or len(df) < 60:
            log.warning("%s: no/short history, skipped", c)
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, start_ms, cache, iv)
    fixed = [c for c in fixed if c in data]
    candidates = [c for c in candidates if c in data]
    V = VARIANTS["breakout_long"]
    liq_med = liquidity(data)[1]
    rows = {}
    for name, coins in [("fixed", fixed), ("fixed_plus_candidates", fixed + candidates)]:
        sub = {c: data[c] for c in coins}
        Q = {**P, "liq_med": liq_med, "elig_by_day": None, "delisted": set()}
        T, C, dd = run(V, sub, {c: fund[c] for c in coins}, Q)
        s = stats(T, C, dd, Q)
        days = sorted(set().union(*[set(d.index) for d in sub.values()]))
        days = [d for d in days if d >= start]
        if days and not T.empty:
            s |= halves(T, days[len(days) // 2])
        cand_t = T[T.coin.isin(candidates)] if len(T) else T
        s["candidate_trades"] = len(cand_t)
        s["candidate_net"] = round(cand_t.net.sum(), 2) if len(cand_t) else 0.0
        rows[name] = s
        T.to_csv(out / f"candidates_{iv}_{name}.csv", index=False)
        log.info("%s: trades %s pf %.2f dd %.1f", name, s.get("trades"), s.get("pf", 0), s.get("max_dd_pct", 0))
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "sharpe", "IS_pf", "OOS_pf",
            "candidate_trades", "candidate_net"]
    base, cand = rows["fixed"], rows["fixed_plus_candidates"]
    passed = (bool(cand.get("trades")) and cand["pf"] >= 1.4 and cand["max_dd_pct"] >= base["max_dd_pct"] - 5
              and cand.get("OOS_pf", 0) > 1.2 and cand.get("candidate_net", -1) >= 0)
    rep = [f"# Candidate study ({iv}, breakout_long, {P['start']} -> now)\n",
           f"Fixed (live scanner.coins): {', '.join(fixed)}. Candidates tested: {', '.join(candidates)}. "
           "Same-day signals fill most-liquid first.\n",
           S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n",
           "Adoption rule (fixed before running): combined PF >= 1.4, max DD >= fixed - 5pp, OOS PF > 1.2, "
           f"candidates' own net P&L >= 0 -> **{'PASS' if passed else 'FAIL'}**\n"]
    (out / f"candidates_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return passed


ENTRY_STUDY = {
    # pre-registered before running (README "Entries"); every variant is breakout_long plus one change
    "base": {},
    "fail_exit2": {"fail_exit_bars": 2},
    "confirm": {"entry": "confirm"},
    "fib382": {"entry": "fib", "fib": 0.382, "fib_valid": 5},
    "fib50": {"entry": "fib", "fib": 0.5, "fib_valid": 5},
    "strong_close": {"filters": ["strong_close"]},
    "near_level": {"filters": ["near_level"]},
}


def entry_study(P, iv):
    """python -m hlg.backtest --entry-study [--interval 4h]

    Can failed breakouts be avoided or made cheaper? Rule fixed before running: an entry/exit variant
    passes if PF >= base + 0.10, max DD no worse than base - 2pp, and PF in each half >= the base's in
    that half; a filter must additionally sit above the 95th percentile of randomly dropping the
    same number of base trades."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(P["start"], tz="UTC")
    data, fund = {}, {}
    for c in DEF["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80:
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, int(start.timestamp() * 1000), cache, iv)
    _, liq_med = liquidity(data)
    Q = {**P, "liq_med": liq_med}
    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    days = [d for d in days if d >= start]
    mid = days[len(days) // 2]
    rows, trades = {}, {}
    for name, extra in ENTRY_STUDY.items():
        V = {**VARIANTS["breakout_long"], **extra}
        T, C, dd = run(V, data, fund, Q)
        s = stats(T, C, dd, Q) | halves(T, mid)
        s["failed_exits"] = int((T.why == "failed").sum()) if len(T) else 0
        rows[name], trades[name] = s, T
        T.to_csv(out / f"entry_{iv}_{name}.csv", index=False)
        log.info("%s: trades %s pf %.2f dd %.1f", name, s.get("trades"), s.get("pf", 0), s.get("max_dd_pct", 0))
    b = rows["base"]
    base_net = trades["base"].net.values
    verdict = {}
    for name, s in rows.items():
        if name == "base":
            continue
        ok = (s["pf"] >= b["pf"] + 0.10 and s["max_dd_pct"] >= b["max_dd_pct"] - 2
              and s["IS_pf"] >= b["IS_pf"] and s["OOS_pf"] >= b["OOS_pf"])
        pct = None
        if ENTRY_STUDY[name].get("filters"):
            pct = bootstrap_pf(base_net, s["trades"], s["pf"]) if s["trades"] < len(base_net) else float("nan")
            ok = ok and pct is not None and pct > 95
        verdict[name] = ("PASS" if ok else "fail") + (f" (random-subset pctile {pct:.0f})" if pct is not None else "")
    # the cost side of waiting for a retrace: base winners the variant never traded
    big = trades["base"].nlargest(max(1, len(trades["base"]) // 5), "net") if len(trades["base"]) else trades["base"]
    missed = {}
    for name in ("confirm", "fib382", "fib50"):
        T = trades[name]
        have = set(zip(T.coin, T.start.dt.floor("D"))) if len(T) else set()
        # a variant trade on the same coin within 6 days of the base entry counts as "caught it"
        caught = sum(any(c == bc and 0 <= (t - bs.floor("D")).days <= 6 for c, t in have) for bc, bs in zip(big.coin, big.start))
        missed[name] = f"{len(big) - caught}/{len(big)}"
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "avg_loss_pct", "avg_win_pct", "IS_pf", "OOS_pf", "failed_exits"]
    rep = [f"# Entry study ({iv}, breakout_long on the fixed list, {P['start']} -> now)\n",
           S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n",
           "Verdicts (rule fixed before running): " + "; ".join(f"`{k}` {v}" for k, v in verdict.items()) + "\n",
           "Top-20% base winners the retrace/confirm variants never entered: "
           + ", ".join(f"`{k}` {v}" for k, v in missed.items()) + "\n"]
    (out / f"entry_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def _full_history(P, iv, cache):
    """Every DEF coin's full cached history (not capped to P["start"]) plus an equal-weight,
    NaN-before-listing basket built from it -- shared by every backdrop-vs-forward-returns study,
    so BTC/basket construction can't quietly drift between them."""
    full_data = {}
    for c in DEF["coins"]:
        df = bars(c, cache, iv)
        if df is not None and len(df) >= 200:
            full_data[c] = df
    ret1d = pd.DataFrame({c: d.c.pct_change() for c, d in full_data.items()})
    basket = (1 + ret1d).cumprod().mean(axis=1, skipna=True)
    return full_data, basket


def _corr_and_quintiles(rep, chg, chg_name, full_data, basket, windows=(7, 30, 90), quintile_at=30):
    """Appends a correlation table (chg vs forward return, several horizons) and, at
    `quintile_at`, a quintile breakdown per series -- a single correlation number can hide a
    non-monotonic or flat relationship a quintile table won't."""
    corr_rows, quint_rows = [], []
    for name, level in (("BTC", full_data["BTC"].c), ("basket (equal-weight, all coins)", basket)):
        for n in windows:
            fwd = level.shift(-n) / level - 1
            x = chg.reindex(fwd.index)
            both = pd.concat([x, fwd], axis=1, keys=["x", "fwd"]).dropna()
            if len(both) < 30:
                continue
            corr_rows.append(dict(series=name, fwd_days=n, n=len(both), corr=round(both.x.corr(both.fwd), 3)))
            if n == quintile_at:
                q = pd.qcut(both.x, 5, labels=["Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest"])
                g = both.groupby(q, observed=True).fwd
                quint_rows.append((name, pd.DataFrame({"n": g.size(), "mean_fwd_pct": (g.mean() * 100).round(2),
                                                        "win_rate": g.apply(lambda s: (s > 0).mean()).round(2)})))
    rep.append(f"## Correlation with forward returns\n\n{chg_name} (signal-day, already 1d-lagged) vs forward return.\n\n"
               + pd.DataFrame(corr_rows).to_markdown(index=False) + "\n")
    for name, q in quint_rows:
        rep.append(f"### {name}, by {quintile_at}d-{chg_name} quintile on the signal day\n\n" + q.to_markdown() + "\n")


def _entry_filter_test(rep, P, iv, cache, filters, context_kw, tag, extra=None):
    """Part 2 of a backdrop study: does gating breakout_long on `filters` help, at the same
    adoption bar --entry-study uses. Shared so every backdrop study is held to one bar."""
    start = pd.Timestamp(P["start"], tz="UTC")
    data, fund = {}, {}
    for c in DEF["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80:
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, int(start.timestamp() * 1000), cache, iv)
    context(data, fund, **context_kw)
    if extra is not None:
        extra(data, cache, iv)
    days = sorted(set().union(*[set(d.index) for d in data.values()]))
    days = [d for d in days if d >= start]
    mid = days[len(days) // 2]
    rows, trades = {}, {}
    for name in ("base", *filters):
        V = VARIANTS["breakout_long"] if name == "base" else {**VARIANTS["breakout_long"], "filters": [name]}
        T, C, dd = run(V, data, fund, P)
        s = stats(T, C, dd, P) | (halves(T, mid) if len(T) else {})
        rows[name], trades[name] = s, T
        log.info("%s: trades %s pf %.2f", name, s.get("trades"), s.get("pf", 0))
    b = rows["base"]
    base_net = trades["base"].net.values
    verdict = {}
    for name in filters:
        s = rows[name]
        if not s.get("trades"):
            verdict[name] = "fail (no trades)"
            continue
        pct = bootstrap_pf(base_net, s["trades"], s["pf"]) if s["trades"] < len(base_net) else float("nan")
        ok = (s["pf"] >= b["pf"] + 0.10 and s["max_dd_pct"] >= b["max_dd_pct"] - 2
              and s.get("IS_pf", 0) >= b.get("IS_pf", 0) and s.get("OOS_pf", 0) >= b.get("OOS_pf", 0)
              and not np.isnan(pct) and pct > 95)
        verdict[name] = ("PASS" if ok else "fail") + f" (random-subset pctile {pct:.0f})"
    S = pd.DataFrame(rows).T
    cols = ["trades", "win_rate", "pf", "total_return_pct", "max_dd_pct", "sharpe", "IS_pf", "OOS_pf"]
    rep.append(f"\n## Entry-filter test ({P['start']} -> now, same bar as --entry-study)\n\n"
               + S[[c for c in cols if c in S]].astype(float).round(2).to_markdown() + "\n")
    rep.append("Verdicts (rule fixed before running): " + "; ".join(f"`{k}` {v}" for k, v in verdict.items()) + "\n")
    return verdict, trades["base"], data


def stbl_study(P, iv):
    """python -m hlg.backtest --stbl-study

    Tests the "stablecoin dominance and crypto move inversely" claim two ways.

    Part 1: the hypothesis as stated -- does stablecoin market-cap growth predict forward BTC (and
    a crypto-basket) returns, on the *full* available history (BTC's cached range, 2020-08 -> now:
    much more data than the 2023-06 backtest window, since this is a pure data question, not tied
    to the live trading rule). stbl_chg is already shifted a day (hlg.stablecoin), so this reads
    only what was known at T; the forward return is deliberately forward-looking -- that is the
    thing being tested.

    Part 2: does it help the live entry rule? Same 2023-06 -> now window and adoption bar as
    --entry-study (fixed before running): PF >= base + 0.10, max DD no worse than base - 2pp, PF
    in each half >= the base's, and (since both candidates are subsets of the baseline's own
    signals) above the 95th percentile of randomly dropping the same number of trades."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    stbl_f = stablecoin.features(stablecoin.frame(cache_dir=str(cache)))
    if stbl_f.empty:
        log.error("stbl_study: no stablecoin data, aborting")
        return
    rep = [f"# Stablecoins vs crypto returns ({stbl_f.index[0].date()} -> {stbl_f.index[-1].date()})\n"]
    full_data, basket = _full_history(P, iv, cache)
    _corr_and_quintiles(rep, stbl_f.stbl_chg, "stbl_chg", full_data, basket)
    _entry_filter_test(rep, P, iv, cache, ("no_stbl_growth", "stbl_outflow"), dict(stbl_f=stbl_f), "stbl")
    (out / "stbl_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def dxy_study(P, iv):
    """python -m hlg.backtest --dxy-study

    The same two-part test as --stbl-study, for the dollar-strength version of the "risk-off
    parking" claim: does the broad trade-weighted dollar index (DTWEXBGS, via hlg.macro/FRED)
    predict forward BTC/basket returns, and does gating breakout_long on it help? `no_dxy_headwind`
    already existed as an entry filter (README "Macro"); this adds Part 1 -- the direct
    correlation the filter test alone can't show -- and re-runs Part 2 for a like-for-like report
    alongside --stbl-study, at the same adoption bar."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    # Part 1 wants the longest overlap with BTC's own cached history (FRED's DXY series runs back
    # decades, unlike DefiLlama's stablecoin series it is not the limiting factor) -- fetched
    # separately from Part 2's macro_f, which must stay anchored to P["start"] to match the backtest
    # window exactly. Using P["start"] for both here would quietly halve Part 1's sample for no reason.
    macro_long = macro.features(macro.frame(start="2015-01-01", cache_dir=str(cache)))
    if macro_long.empty or macro_long.dxy.dropna().empty:
        log.error("dxy_study: no DXY data, aborting")
        return
    dxy_chg = macro_long.dxy.pct_change(30) * 100
    rep = [f"# Dollar index (DXY) vs crypto returns ({macro_long.index[0].date()} -> {macro_long.index[-1].date()})\n"]
    full_data, basket = _full_history(P, iv, cache)
    _corr_and_quintiles(rep, dxy_chg, "dxy_chg", full_data, basket)
    macro_f = macro.features(macro.frame(start=P["start"], cache_dir=str(cache)))
    _entry_filter_test(rep, P, iv, cache, ("no_dxy_headwind",), dict(macro_f=macro_f), "dxy")
    (out / "dxy_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def sentiment_study(P, iv):
    """python -m hlg.backtest --sentiment-study [--interval 4h]

    The Crypto Fear & Greed index, tested the same two ways as --stbl-study.

    Part 1: does the level predict forward BTC / basket returns (the contrarian claim says low
    readings precede better returns)? Full overlap with cached price history.

    Part 2: two entry filters on breakout_long, thresholds = the index's own published bands,
    written down before the first run: `fng_not_extreme_greed` (skip entries when the index is >=
    76 -- "the crowd is euphoric, tops form there") and `fng_not_extreme_fear` (skip <= 24 --
    "breakouts in panic fail"). Adoption bar, fixed before running and identical to --entry-study /
    --stbl-study: PF >= base + 0.10, max DD no worse than base - 2pp, PF in each half >= the base's,
    and above the 95th percentile of randomly dropping the same number of trades."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    sent_f = sentiment.features(sentiment.frame(cache_dir=str(cache)))
    if sent_f.empty:
        log.error("sentiment_study: no Fear & Greed data, aborting")
        return
    rep = [f"# Fear & Greed vs crypto returns ({sent_f.fng.first_valid_index().date()} -> {sent_f.index[-1].date()}, {iv})\n"]
    full_data, basket = _full_history(P, iv, cache)
    _corr_and_quintiles(rep, sent_f.fng, "fng", full_data, basket)
    share = lambda s: f"{s.mean() * 100:.0f}%"  # noqa: E731
    days = sent_f[sent_f.index >= pd.Timestamp(P["start"], tz="UTC")]
    rep.append(f"Days in the backtest window: extreme greed {share(days.fng_extreme_greed.astype(float))}, "
               f"extreme fear {share(days.fng_extreme_fear.astype(float))}.\n")
    _entry_filter_test(rep, P, iv, cache, ("fng_not_extreme_greed", "fng_not_extreme_fear"), dict(sent_f=sent_f), "fng")
    (out / f"sentiment_study_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def event_study(P, iv):
    """python -m hlg.backtest --event-study [--interval 4h]

    Should breakout entries avoid opening into an FOMC decision? `no_fomc_entry` skips a signal
    whose fill lands within 24h before a decision (same window as the live heads-up), from the
    Fed's own published schedule. Same adoption bar as every entry filter, fixed before running.

    Only FOMC is testable: BLS (CPI, payrolls) refuses automated requests and there is no other
    free source of past release dates, so those events get the live heads-up without a backtest.
    Expect a small sample -- 8 meetings a year -- which the random-subset percentile accounts for."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    r = requests.get(events.FOMC_URL, headers=events.UA, timeout=20)
    r.raise_for_status()
    fomc = events.parse_fomc(r.text)
    start = pd.Timestamp(P["start"], tz="UTC")
    in_window = [t for t in fomc if pd.Timestamp(t, unit="ms", tz="UTC") >= start and t < time.time() * 1000]
    rep = [f"# FOMC decisions vs breakout entries ({iv}, {P['start']} -> now)\n",
           f"{len(in_window)} scheduled FOMC decisions in the window (federalreserve.gov).\n"]
    _entry_filter_test(rep, P, iv, cache, ("no_fomc_entry",), dict(fomc=fomc), "fomc")
    (out / f"event_study_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


def ma_study(P):
    """python -m hlg.backtest --ma-study

    Benjamin Cowen's long-term lines -- 50-week and 200-day SMA/EMA -- as breakout entry filters,
    for BTC and for each coin's own chart, on 1d and 4h. Pre-registered in
    docs/research/ma-lines-study.md: the usual entry-filter bar, and a filter is adopted only if it
    passes on BOTH intervals (12 tests; one lone pass is expected by chance)."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    filters = tuple(f"{w}_above_{ln}" for w in ("btc", "coin") for ln in MA_LINES if not (w == "coin" and ln.startswith("ema")))
    rep = [f"# Long-term moving averages as entry filters ({P['start']} -> now)\n"]
    passed = {}
    for iv in ("1d", "4h"):
        rep.append(f"\n# {iv}\n")
        verdict, T, data = _entry_filter_test(rep, P, iv, cache, filters, {}, "ma", extra=ma_context)
        for f, v in verdict.items():
            passed.setdefault(f, []).append(v.startswith("PASS"))
        # descriptive: the base trades split by each line, read off the signal bar (the bar before entry)
        split = []
        for f in filters:
            flag = []
            for _, t in T.iterrows():
                d = data[t.coin]
                i = d.index.get_indexer([t.start])[0]
                flag.append(d[f].iloc[i - 1] if i > 0 else pd.NA)
            fl = pd.Series(flag, index=T.index, dtype="object")
            for side, m in (("above", fl == True), ("below", fl == False), ("unknown", fl.isna())):  # noqa: E712
                n = T.net[m.fillna(False).astype(bool)]
                if len(n):
                    split.append(dict(filter=f, side=side, trades=len(n), win_rate=round((n > 0).mean(), 2),
                                      pf=round(min(n[n > 0].sum() / max(-n[n < 0].sum(), 1e-9), 99), 2), net=round(n.sum())))
        rep.append(f"\n### {iv}: base trades by side of each line (signal bar)\n\n" + pd.DataFrame(split).to_markdown(index=False) + "\n")
    adopted = [f for f, v in passed.items() if all(v) and len(v) == 2]
    rep.append(f"\n**Adopted (passes on both 1d and 4h):** {', '.join(adopted) if adopted else 'none'}\n")
    (out / "ma_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return passed


PYRAMID_STUDY = {"base": 0, "pyramid1": 1, "pyramid2": 2}  # pyramid1 is the candidate; pyramid2 sensitivity only


def pyramid_study(P, iv):
    """python -m hlg.backtest --pyramid-study [--interval 4h]

    Should a fresh breakout on a coin already held add to the position? Rules fixed before running
    (README "Pyramiding"): add only while the shared trailing stop is at or above the first entry,
    the add risks risk_pct to that shared stop, all units exit together, the coin's notional stays
    within max_lev x equity, an add takes no new slot. Candidate `pyramid1` (one add) must beat the
    base by the --entry-study bar -- PF >= base + 0.10, max DD no worse than base - 2pp, PF in each
    half >= the base's -- on BOTH 1d and 4h. (No random-subset control: adds create trades rather
    than select from the baseline's, so the second timeframe is the stand-in for a second test.)"""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    start = pd.Timestamp(P["start"], tz="UTC")
    data, fund = {}, {}
    for c in DEF["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80:
            continue
        data[c] = features(df, 1)
        fund[c] = funding(c, int(start.timestamp() * 1000), cache, iv)
    days = [d for d in sorted(set().union(*[set(d.index) for d in data.values()])) if d >= start]
    mid = days[len(days) // 2]
    rows = {}
    for name, n in PYRAMID_STUDY.items():
        V = {**VARIANTS["breakout_long"], "pyramid": n}
        T, C, dd = run(V, data, fund, P)
        s = stats(T, C, dd, P) | halves(T, mid)
        adds = T[T.unit > 0]
        g, l = adds.net[adds.net > 0].sum(), -adds.net[adds.net < 0].sum()
        s |= {"positions": int((T.unit == 0).sum()), "adds": len(adds), "adds_net": round(adds.net.sum(), 1),
              "adds_pf": round(g / l, 2) if l else (float("inf") if g else 0.0)}
        rows[name] = s
        T.to_csv(out / f"pyramid_{iv}_{name}.csv", index=False)
        log.info("%s: positions %d adds %d pf %.2f dd %.1f", name, s["positions"], s["adds"], s["pf"], s["max_dd_pct"])
    b, c = rows["base"], rows["pyramid1"]
    ok = (c["pf"] >= b["pf"] + 0.10 and c["max_dd_pct"] >= b["max_dd_pct"] - 2
          and c["IS_pf"] >= b["IS_pf"] and c["OOS_pf"] >= b["OOS_pf"])
    S = pd.DataFrame(rows).T
    cols = ["positions", "adds", "trades", "pf", "total_return_pct", "max_dd_pct", "sharpe", "IS_pf", "OOS_pf", "adds_pf", "adds_net"]
    rep = [f"# Pyramiding study ({iv}, breakout_long, {P['start']} -> now)\n",
           S[cols].astype(float).round(2).to_markdown() + "\n",
           f"pyramid1 vs base on this interval (rule fixed before running): **{'PASS' if ok else 'FAIL'}** "
           "(adoption needs a pass on both 1d and 4h)\n"]
    (out / f"pyramid_{iv}.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rows, ok


def _combined_book(P):
    """1d and 4h streams per DEF coin, keyed "COIN|tf"; returns (data, fund, run extras, mid-date)."""
    cache = Path(P["cache_dir"])
    start = pd.Timestamp(P["start"], tz="UTC")
    data, fund, coin_of, mark = {}, {}, {}, {}
    for c in DEF["coins"]:
        for iv in ("1d", "4h"):
            df = bars(c, cache, iv)
            if df is None or len(df) < 80:
                continue
            k = f"{c}|{iv}"
            data[k], coin_of[k] = features(df, 1), c
            fund[k] = funding(c, int(start.timestamp() * 1000), cache, iv)
            if iv == "4h":
                mark[c] = df.c
    days = [d for d in sorted(set().union(*[set(d.index) for d in data.values()])) if d >= start]
    return data, fund, {"coin_of": coin_of, "mark": mark}, days[len(days) // 2]


def _combined_run(data, fund, Q, mid):
    T, C, dd = run(VARIANTS["breakout_long"], data, fund, Q)
    s = stats(T, C, dd, Q) | halves(T, mid)
    daily = C.resample("1D").last().dropna().pct_change().dropna() * 100
    tf = T.coin.str.split("|").str[1]
    return T, dict(trades=len(T), trades_1d=int((tf == "1d").sum()), trades_4h=int((tf == "4h").sum()),
                   pf=s["pf"], total_return_pct=s["total_return_pct"], max_dd_pct=s["max_dd_pct"], sharpe=s["sharpe"],
                   IS_pf=s["IS_pf"], OOS_pf=s["OOS_pf"], worst_day_pct=daily.min(),
                   days_below_3pct=int((daily < -3).sum()), days_below_4_5pct=int((daily < -4.5).sum()),
                   preempted=int((T.why == "preempted").sum()) if len(T) else 0)


PRIORITY_STUDY = {
    # fixed before running: reserve1 is the candidate; the rest are sensitivity / reference only
    "shared (live)": {},
    "reserve1": {"tf_max": {"4h": 2}},
    "same_bar": {"tf_rank": {"1d": 1}},
    "reserve2": {"tf_max": {"4h": 1}},
    "preempt": {"tf_rank": {"1d": 1}, "preempt": True},
    "1d only": {"tf_max": {"4h": 0}},
    "4h only": {"tf_max": {"1d": 0}},
}


def priority_study(P, risk=1.0):
    """python -m hlg.backtest --priority-study

    Should 1d signals get priority over 4h in the live book? In the combined book 4h signals take
    ~80% of the slots simply by firing more often, while 1d is the stronger rule (PF 1.67 vs 1.38
    alone). Candidate, fixed before running: `reserve1` -- 4h may hold at most 2 of the 3 slots.
    Passes on the --entry-study bar against the live shared book: PF >= base + 0.10, max DD no worse
    than base - 2pp, PF in each half >= the base's. Run at the live 1.0% risk."""
    out = Path(P["out_dir"])
    data, fund, extra, mid = _combined_book(P)
    rows = {}
    for name, cfg in PRIORITY_STUDY.items():
        T, s = _combined_run(data, fund, {**P, "risk_pct": risk, **extra, **cfg}, mid)
        rows[name] = s
        T.to_csv(out / f"priority_{name.split()[0]}.csv", index=False)
        log.info("%s: trades %d (1d %d / 4h %d) pf %.2f dd %.1f", name, s["trades"], s["trades_1d"], s["trades_4h"],
                 s["pf"], s["max_dd_pct"])
    b, c = rows["shared (live)"], rows["reserve1"]
    ok = (c["pf"] >= b["pf"] + 0.10 and c["max_dd_pct"] >= b["max_dd_pct"] - 2
          and c["IS_pf"] >= b["IS_pf"] and c["OOS_pf"] >= b["OOS_pf"])
    S = pd.DataFrame(rows).T.astype(float).round(2)
    rep = [f"# 1d vs 4h slot priority (combined book, {risk}% risk, max {P['max_positions']} positions, {P['start']} -> now)\n",
           S.to_markdown() + "\n",
           f"reserve1 vs the live shared book (rule fixed before running): **{'PASS' if ok else 'FAIL'}**\n"]
    (out / "priority_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rows, ok


def combined_study(P, risks=(1.5, 1.0)):
    """python -m hlg.backtest --combined-study

    The book as it actually runs live: 1d and 4h breakout signals on the same coins, sharing
    max_positions slots, one position per coin whichever timeframe opened it. Every other study
    runs one timeframe alone. Descriptive (no adoption rule): it measures the drawdown and the
    daily-loss exposure of the live configuration at each risk level.

    Approximation, stated rather than hidden: a 1d stream is simulated once a day at 00:00 UTC with
    its whole daily bar, 4h streams every 4h; open positions are marked on 4h closes. Slot contention
    within a day is therefore approximate for 1d stops and fills."""
    out = Path(P["out_dir"])
    data, fund, extra, mid = _combined_book(P)
    rows = {}
    for r in risks:
        T, s = _combined_run(data, fund, {**P, "risk_pct": r, **extra}, mid)
        rows[f"combined @ {r}%"] = s
        T.to_csv(out / f"combined_{r}.csv", index=False)
    S = pd.DataFrame(rows).T.astype(float).round(2)
    rep = [f"# Combined 1d + 4h book (live configuration), {P['start']} -> now, max {P['max_positions']} positions\n",
           S.to_markdown() + "\n"]
    (out / "combined_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return rows


REGIME_METRICS = ("pf", "win_rate", "avg_r", "early_stop")
BAR_DAYS = {"1d": 1.0, "4h": 4 / 24, "1h": 1 / 24}


def regime_states(btc_1d, macro_f, sent_f):
    """Market state per UTC day, as known before that day's open (docs/research/regime-study.md).
    BTC's trend from the previous daily close; the macro and Fear & Greed frames are already shifted
    a day in their own modules. A dimension with no reading stays NA and counts as neither leg."""
    up = (btc_1d.c > ema(btc_1d.c, 200)).astype("boolean")
    up.iloc[:200] = pd.NA                                              # the 200-day EMA hasn't warmed up
    up = up.shift(1)
    idx = up.index
    S = pd.DataFrame(index=idx)
    S["trend"] = up.map({True: "btc_up", False: "btc_down"}, na_action="ignore")
    if macro_f is not None and not macro_f.empty:
        hy, spx = macro_f.hy_stress.reindex(idx), macro_f.spx_bull.reindex(idx)
        m = pd.Series(pd.NA, index=idx, dtype="object")
        m[(hy == False) & (spx == True)] = "macro_on"                  # noqa: E712 - nullable booleans
        m[(hy == True) & (spx == False)] = "macro_off"                 # noqa: E712
        m[((hy == True) & (spx == True)) | ((hy == False) & (spx == False))] = "macro_mixed"  # noqa: E712
        S["macro"] = m
    else:
        S["macro"] = pd.NA
    if sent_f is not None and not sent_f.empty:
        f = sent_f.fng.reindex(idx)
        S["crowd"] = [None if pd.isna(v) else market_state.crowd_of(v) for v in f]
    else:
        S["crowd"] = None
    # one classifier for the study and the live label (hlg.market_state), so they can't drift apart
    nz = lambda v: None if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)) else v  # noqa: E731
    S["composite"] = [market_state.composite(nz(t), nz(m), nz(c)) for t, m, c in zip(S.trend, S.macro, S.crowd)]
    return S


def _regime_metric(name, net, r, early, ix):
    if name == "pf":
        w, l = net[ix][net[ix] > 0].sum(), -net[ix][net[ix] < 0].sum()
        return min(w / max(l, 1e-9), 99)
    if name == "win_rate":
        return float((net[ix] > 0).mean())
    if name == "avg_r":
        return float(r[ix].mean())
    return float(early[ix].mean())


def regime_study(P, risk=1.0, n_boot=20_000, seed=0):
    """python -m hlg.backtest --regime-study

    Does the live book behave differently by market state? Messaging only: pre-registered in
    docs/research/regime-study.md (states, metrics and the bar for "different" fixed before the
    first run). Nothing here changes a signal, a size or a rule."""
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    out.mkdir(exist_ok=True)
    data, fund, extra, mid = _combined_book(P)
    Q = {**P, "risk_pct": risk, **extra}
    T, C, dd = run(VARIANTS["breakout_long"], data, fund, Q)
    macro_f = macro.features(macro.frame(start="2022-01-01", cache_dir=str(cache)))
    sent_f = sentiment.features(sentiment.frame(cache_dir=str(cache)))
    S = regime_states(bars("BTC", cache, "1d"), macro_f, sent_f)
    T = T.copy()
    T["tf"] = T.coin.str.split("|").str[1]
    T["day"] = T.start.dt.floor("D")
    for dim in ("trend", "macro", "crowd", "composite"):
        T[dim] = T.day.map(S[dim])
    T["r"] = T.net_pct / risk
    T["early"] = (T.why == "stop") & (T.days <= 3 * T.tf.map(BAR_DAYS))
    T = T.sort_values("start").reset_index(drop=True)
    net, r, early = T.net.values, T.r.values, T.early.values.astype(float)
    first, second = (T.start < mid).values, (T.start >= mid).values
    window = S[(S.index >= pd.Timestamp(P["start"], tz="UTC")) & (S.index <= T.end.max())]
    rng = np.random.default_rng(seed)
    allix = np.arange(len(T))
    overall = {m: _regime_metric(m, net, r, early, allix) for m in REGIME_METRICS}
    rows, tests = [], []
    for dim in ("trend", "macro", "crowd", "composite"):
        for b in sorted(T[dim].dropna().unique()):
            ix = np.flatnonzero((T[dim] == b).values)
            days_in = int((window[dim] == b).sum())
            run_, longest = 0, 0
            for v in net[ix]:
                run_ = run_ + 1 if v <= 0 else 0
                longest = max(longest, run_)
            row = dict(dimension=dim, state=b, trades=len(ix), share_of_days=days_in / max(len(window), 1),
                       entries_per_30d=len(ix) / days_in * 30 if days_in else np.nan,
                       longest_losing_run=longest, median_days_held=float(np.median(T.days.values[ix])))
            for m in REGIME_METRICS:
                v = _regime_metric(m, net, r, early, ix)
                row[m] = v
                if len(ix) < 30:
                    row[f"{m}_verdict"] = "n<30"
                    continue
                draws = np.array([_regime_metric(m, net, r, early, rng.choice(allix, size=len(ix), replace=False))
                                  for _ in range(n_boot)])
                pctile = float((draws < v).mean() * 100)
                signs = []
                for h in (first, second):
                    hb, ha = np.flatnonzero((T[dim] == b).values & h), np.flatnonzero(h)
                    signs.append(np.sign(_regime_metric(m, net, r, early, hb) - _regime_metric(m, net, r, early, ha))
                                 if len(hb) else 0)
                ok = (pctile < 5 or pctile > 95) and signs[0] == signs[1] != 0
                row[f"{m}_verdict"] = f"{'DIFFERENT' if ok else 'same'} (p{pctile:.0f}, halves {signs[0]:+.0f}/{signs[1]:+.0f})"
                tests.append(ok)
            rows.append(row)
            log.info("%s %s: %d trades", dim, b, len(ix), extra={"safe": True})
    R = pd.DataFrame(rows)
    T.to_csv(out / "regime_trades.csv", index=False)
    num = ["trades", "share_of_days", "entries_per_30d", "win_rate", "pf", "avg_r", "early_stop",
           "longest_losing_run", "median_days_held"]
    rep = [f"# Market-state study: live book (1d + 4h, {risk}% risk, max {P['max_positions']} positions), "
           f"{P['start']} -> now\n",
           f"Whole book: {len(T)} trades, win rate {overall['win_rate']:.2f}, PF {overall['pf']:.2f}, "
           f"avg R {overall['avg_r']:+.2f}, early-stop rate {overall['early_stop']:.2f}. "
           f"{int(T[['trend', 'macro', 'crowd']].isna().any(axis=1).sum())} trades lack a reading on at least one dimension.\n",
           R[["dimension", "state"] + num].round(2).to_markdown(index=False) + "\n",
           "## Verdicts (rule fixed before running)\n",
           R[["dimension", "state"] + [f"{m}_verdict" for m in REGIME_METRICS]].to_markdown(index=False) + "\n",
           f"**{sum(tests)} of {len(tests)} tests passed**; about {len(tests) * 0.1 * 0.5:.0f}-{len(tests) * 0.1:.0f} "
           "would pass by chance (10% two-sided, fewer after the halves rule).\n"]
    (out / "regime_study.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    return R, T


def main():
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--variant", nargs="*")
    ap.add_argument("--coins", nargs="*")
    ap.add_argument("--interval", choices=list(INTERVAL_H))
    ap.add_argument("--native", action="store_true", help="use 20/50/14-bar windows on the chosen interval instead of day-equivalent windows")
    ap.add_argument("--filters", nargs="*", help=f"ad-hoc entry filters on the breakout rule: {', '.join(FILTERS)}")
    ap.add_argument("--no-macro", action="store_true", help="skip the FRED fetch (macro filters then never reject)")
    ap.add_argument("--no-stbl", action="store_true", help="skip the DefiLlama fetch (stablecoin filters then never reject)")
    ap.add_argument("--stbl-study", action="store_true", help="stablecoin-mcap vs forward returns correlation, plus the two backdrop filters")
    ap.add_argument("--dxy-study", action="store_true", help="dollar-index vs forward returns correlation, plus the no_dxy_headwind filter")
    ap.add_argument("--universe-study", action="store_true", help="breakout on fixed list vs point-in-time liquidity universes")
    ap.add_argument("--entry-study", action="store_true", help="failed-breakout study: fast exit, confirmation, fib retrace entries, bar-quality filters")
    ap.add_argument("--sentiment-study", action="store_true", help="Fear & Greed vs forward returns, plus the two extreme-band entry filters")
    ap.add_argument("--event-study", action="store_true", help="skip breakout entries that fill within 24h before an FOMC decision?")
    ap.add_argument("--pyramid-study", action="store_true", help="add to a held coin on a fresh breakout once its stop is at breakeven?")
    ap.add_argument("--combined-study", action="store_true", help="the live book: 1d + 4h signals sharing the position slots, at 1.5% and 1.0% risk")
    ap.add_argument("--priority-study", action="store_true", help="should 1d signals get slot priority over 4h in the live book?")
    ap.add_argument("--ma-study", action="store_true", help="50-week / 200-day SMA/EMA (BTC and each coin) as breakout entry filters")
    ap.add_argument("--regime-study", action="store_true", help="does the live book behave differently by market state (risk-on/off)? messaging only")
    ap.add_argument("--candidate-study", nargs="+", metavar="COIN",
                     help="does adding these specific coins to the live scanner.coins list pay for itself?")
    a = ap.parse_args()
    cfg = load_config(a.config) if Path(a.config).exists() else {}
    P = {**DEF, **cfg.get("backtest", {})}
    if a.coins:
        P["coins"] = a.coins
    if a.interval:
        P["interval"] = a.interval
    if a.native:
        P["native"] = True
    iv = P["interval"]
    if a.universe_study:
        return universe_study(P, iv)
    if a.candidate_study:
        fixed = cfg.get("scanner", {}).get("coins") or P["coins"]
        return candidate_study(P, iv, list(fixed), list(a.candidate_study))
    if a.entry_study:
        return entry_study(P, iv)
    if a.stbl_study:
        return stbl_study(P, iv)
    if a.dxy_study:
        return dxy_study(P, iv)
    if a.sentiment_study:
        return sentiment_study(P, iv)
    if a.event_study:
        return event_study(P, iv)
    if a.pyramid_study:
        return pyramid_study(P, iv)
    if a.combined_study:
        return combined_study(P)
    if a.priority_study:
        return priority_study(P)
    if a.regime_study:
        return regime_study(P)
    if a.ma_study:
        return ma_study(P)
    k = 1 if P["native"] else 24 // INTERVAL_H[iv]
    tag = f"{iv}{'_native' if P['native'] and iv != '1d' else ''}"
    cache, out = Path(P["cache_dir"]), Path(P["out_dir"])
    cache.mkdir(exist_ok=True), out.mkdir(exist_ok=True)
    if iv != "1d":
        out = out / tag
        out.mkdir(exist_ok=True)
    start_ms = int(pd.Timestamp(P["start"], tz="UTC").timestamp() * 1000)

    data, fund = {}, {}
    for c in P["coins"]:
        df = bars(c, cache, iv)
        if df is None or len(df) < 80 * k:
            log.warning("%s: no/short history, skipped", c)
            continue
        data[c] = features(df, k)
        fund[c] = funding(c, start_ms, cache, iv)
        log.info("%s: %d %s bars from %s, %d funding bars", c, len(df), iv, df.index[0].date(), len(fund[c]))

    macro_f = None
    if not a.no_macro:
        macro_f = macro.features(macro.frame(start=P["start"], cache_dir=str(cache)))
        log.info("macro: %d rows%s", len(macro_f), "" if len(macro_f) else " (filters will not reject)")
    stbl_f = None
    if not a.no_stbl:
        stbl_f = stablecoin.features(stablecoin.frame(cache_dir=str(cache)))
        log.info("stablecoin: %d rows%s", len(stbl_f), "" if len(stbl_f) else " (filters will not reject)")
    context(data, fund, macro_f, stbl_f)
    if a.filters:
        VARIANTS[f"breakout+{'+'.join(a.filters)}"] = {**BASE_BREAKOUT, "filters": list(a.filters)}
        a.variant = [f"breakout+{'+'.join(a.filters)}"]

    R = [f"# Backtest report\n\nCoins: {', '.join(data)}. Start {P['start']}, equity ${P['equity0']:,.0f}, risk {P['risk_pct']}%/trade, "
         f"max {P['max_positions']} positions, notional cap {P['max_lev']}x equity, maker entry {P['maker_fee']*100:.3f}% / taker exit "
         f"{P['taker_fee']*100:.3f}%, real hourly funding. {iv} bars (indicator windows {'native' if k == 1 else f'x{k} = day-equivalent'}): "
         f"signal at close, fill next open, stop on low/high (gap -> open).\n"]
    all_days = sorted(set().union(*[set(d.index) for d in data.values()]))
    all_days = [d for d in all_days if d >= pd.Timestamp(P["start"], tz="UTC")]
    mid = all_days[len(all_days) // 2]
    summ = {}
    for name, V in VARIANTS.items():
        if a.variant and name not in a.variant:
            continue
        T, C, dd = run(V, data, fund, P)
        s = stats(T, C, dd, P)
        if not T.empty:
            s |= halves(T, mid)
        summ[name] = s
        if not T.empty:
            T.to_csv(out / f"trades_{name}.csv", index=False)
        log.info("%s: %s", name, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()})
        R.append(f"\n## {name}\n\n`{json.dumps(V)}`\n")
        if T.empty:
            R.append("no trades\n")
            continue
        R.append(f"trades {s['trades']} | WR {s['win_rate']:.0%} | PF {s['pf']:.2f} | avg win {s['avg_win_pct']:+.2f}% / avg loss {s['avg_loss_pct']:+.2f}% of equity | "
                 f"total {s['total_return_pct']:+.0f}% (CAGR {s['cagr_pct']:+.0f}%) | max DD {s['max_dd_pct']:.0f}% | Sharpe {s['sharpe']:.2f} | "
                 f"fees ${s['fees']:.0f} funding ${s['funding']:+.0f} | avg hold {s['avg_days']:.1f}d | exits {s['exits']}\n")
        R.append("### By year\n" + by(T.assign(year=T.end.dt.year), "year").to_markdown() + "\n")
        R.append("### By coin\n" + by(T, "coin").sort_values("net", ascending=False).to_markdown() + "\n")
        R.append("### By exit\n" + by(T, "why").to_markdown() + "\n")
    S = pd.DataFrame(summ).T
    if not S.empty:
        cols = [c for c in ["trades", "win_rate", "pf", "total_return_pct", "cagr_pct", "max_dd_pct", "sharpe", "avg_days",
                            "IS_n", "IS_pf", "OOS_n", "OOS_pf"] if c in S]
        R.insert(1, "## Summary\n\n" + S[cols].astype(float).round(2).to_markdown() + "\n"
                 + f"\nIS/OOS split at {mid.date()} by signal date (shared portfolio path, see `halves`).\n")
    # Only filtered variants are comparable this way: they select a subset of the baseline's own
    # signals, so "dropping trades at random" is the right null. A different rule (pullback, short,
    # a wider stop) trades on its own signals and is not a subset -- the test would be meaningless.
    if "breakout_long" in summ and any(VARIANTS[n].get("filters") for n in summ if n in VARIANTS):
        base_net = pd.read_csv(out / "trades_breakout_long.csv").net.values
        rows = []
        for name in summ:
            if not VARIANTS.get(name, {}).get("filters") or not summ[name].get("trades"):
                continue
            n, p = summ[name]["trades"], summ[name]["pf"]
            if n >= len(base_net):
                verdict, pct = "no-op: kept every trade, nothing to test", float("nan")
            else:
                pct = bootstrap_pf(base_net, n, p)
                verdict = "noise" if 5 <= pct <= 95 else ("better" if pct > 95 else "WORSE")
            rows.append(dict(variant=name, kept=f"{n / len(base_net):.0%}", pf=round(p, 2),
                             vs_base=round(p - _pf_of(base_net), 2),
                             pctile="-" if np.isnan(pct) else round(pct, 1), verdict=verdict))
        if rows:
            R.append("\n## Against chance\n\nPercentile of each filtered variant's PF in the distribution from dropping the same "
                     "number of baseline trades at random (20k draws). 5-95 = indistinguishable from luck. Only filters appear here: "
                     "they select a subset of the baseline's signals, so random subsetting is the right null; a different rule is not "
                     f"a subset and is not comparable this way. Testing many filters inflates this -- with {len(rows)}, expect "
                     f"~{0.05 * len(rows):.1f} above the 95th by chance alone.\n\n"
                     + pd.DataFrame(rows).to_markdown(index=False) + "\n")
    R.append("\nCaveats: bar-based (intra-bar stop-outs that recovered by the close are counted as stops only if the low touched — "
             "realistic — but entries are next-day open, not intraday); no slippage; HL history only (most alts start 2023-2024); "
             "parameters were chosen from the miner, not optimised on this data, but the coin list overlaps with the miner sample.")
    (out / "report.md").write_text("\n".join(R))
    print("\n".join(R[:2]))


if __name__ == "__main__":
    main()
