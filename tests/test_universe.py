"""The liquidity universe (hlg.universe), the bar-close cache and snapshot momentum (hlg.scanner),
and the point-in-time eligibility used to backtest them (hlg.backtest)."""
import numpy as np
import pandas as pd

from hlg import backtest, scanner, universe
from hlg.common import Notifier

from .conftest import FakeInfo, base_cfg
from .test_scanner import make_candles

U = universe.settings({"universe": {"mode": "auto", "top_n": 2, "min_vol_usd": 10e6, "min_oi_usd": 5e6, "min_age_days": 60}})


class Mem:
    """A State stand-in: get/set on a dict."""

    def __init__(self, d=None):
        self.d = d or {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


# ---------------------------------------------------------------- universe.rank / prefilter
def test_rank_takes_the_top_n_liquid_coins_old_enough():
    liq = {"A": 50e6, "B": 30e6, "C": 20e6, "THIN": 5e6, "YOUNG": 90e6, "NEW": None}
    ages = {"A": 400, "B": 400, "C": 400, "THIN": 400, "YOUNG": 20, "NEW": 5}
    assert universe.rank(liq, ages, U) == ["A", "B"]


def test_rank_keeps_open_positions_and_includes_and_honours_exclude():
    liq, ages = {"A": 50e6, "B": 30e6, "C": 20e6}, {"A": 400, "B": 400, "C": 400}
    got = universe.rank(liq, ages, {**U, "include": ["PENDLE"], "exclude": ["A"]}, open_coins={"DOGE"})
    assert got == ["B", "C", "PENDLE", "DOGE"]


def test_prefilter_drops_other_dexes_thin_and_low_oi_markets():
    ctx = {"BTC": {"dayNtlVlm": "3e9", "openInterest": "40000", "markPx": "100000"},
           "QUIET": {"dayNtlVlm": "4e6", "openInterest": "1e6", "markPx": "10"},   # >= 1/3 of min vol, OI 10M
           "DEAD": {"dayNtlVlm": "1e5", "openInterest": "1e6", "markPx": "10"},
           "LOWOI": {"dayNtlVlm": "5e7", "openInterest": "1", "markPx": "10"},
           "xyz:GOLD": {"dayNtlVlm": "5e8", "openInterest": "1e6", "markPx": "2000"}}
    assert universe.prefilter(ctx, U) == ["BTC", "QUIET"]


def test_median_notional_needs_enough_history():
    bars = [{"v": 1000, "c": 20} for _ in range(40)]
    assert universe.median_notional(bars) == 20000
    assert universe.median_notional(bars[:10]) is None


# ---------------------------------------------------------------- daily refresh
def test_the_stored_list_is_reused_the_same_day_only():
    st = Mem({"universe": {"day": "2026-09-19", "coins": ["BTC", "ETH"]}})
    assert universe.current(st, "2026-09-19") == ["BTC", "ETH"]
    assert universe.current(st, "2026-09-20") is None


def test_a_coin_opened_intraday_joins_the_list():
    st = Mem({"universe": {"day": "2026-09-19", "coins": ["BTC"]}})
    assert universe.current(st, "2026-09-19", open_coins={"DOGE"}) == ["BTC", "DOGE"]


def test_a_missing_or_corrupt_stored_list_forces_a_rebuild():
    assert universe.current(Mem(), "2026-09-19") is None
    assert universe.current(Mem({"universe": "garbage"}), "2026-09-19") is None
    assert universe.current(Mem({"universe": {"day": "2026-09-19", "coins": []}}), "2026-09-19") is None
    assert universe.current(None, "2026-09-19") is None


def _ctx(**coins):
    return {c: {"dayNtlVlm": str(v), "openInterest": "1e7", "markPx": "1", "funding": "0"} for c, v in coins.items()}


class CountingInfo(FakeInfo):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.candle_calls = 0

    def post(self, path, body):
        if body.get("type") == "candleSnapshot":
            self.candle_calls += 1
        return super().post(path, body)


def _bars(n, notional, step=86_400_000, start=None):
    start = start if start is not None else int(pd.Timestamp.now("UTC").timestamp() * 1000) - n * step
    return [{"t": start + i * step, "o": 1, "h": 1.01, "l": 0.99, "c": 1, "v": notional} for i in range(n)]


def test_resolve_coins_ranks_once_then_reuses_for_the_day():
    inf = CountingInfo(candles={("AAA", "1d"): _bars(100, 50e6), ("BBB", "1d"): _bars(100, 20e6),
                                ("CCC", "1d"): _bars(100, 12e6)})
    ctx, st, cache = _ctx(AAA=5e7, BBB=2e7, CCC=1.2e7), Mem(), {}
    S = {"coins": ["BTC"], "universe": U}
    assert scanner.resolve_coins(inf, S, U, ctx, st, set(), cache) == ["AAA", "BBB"]
    assert inf.candle_calls == 3 and st.get("universe")["coins"] == ["AAA", "BBB"]
    assert "AAA|1d" in cache  # the ranking fetch seeds the 1d bar-stats cache
    scanner.resolve_coins(inf, S, U, ctx, st, set(), cache)
    assert inf.candle_calls == 3  # same UTC day: no new requests


def test_fixed_mode_is_unchanged():
    S = {"coins": ["BTC", "ETH"]}
    assert scanner.resolve_coins(FakeInfo(), S, universe.settings(S), {}, Mem(), set(), {}) == ["BTC", "ETH"]


# ---------------------------------------------------------------- bar-close cache
def _live_series(n=60, step=86_400_000):
    """n closed ramping bars ending just before now, plus the live bar."""
    now = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    start = (now // step) * step - (n) * step
    rows = make_candles([100.0 + i for i in range(n)], start_ms=start, step_ms=step)
    rows.append({"t": start + n * step, "o": 159, "h": 159.4, "l": 158.6, "c": 159, "v": 100})
    return rows


def test_no_candle_request_until_a_new_bar_closes():
    inf = CountingInfo(candles={("BTC", "1d"): _live_series()})
    cache, S = {}, dict(base_cfg()["scanner"])
    scanner.analyse_breakout(inf, "BTC", S, {"funding": 0}, "1d", cache=cache)
    scanner.analyse_breakout(inf, "BTC", S, {"funding": 0}, "1d", cache=cache, px=160.0)
    assert inf.candle_calls == 1
    later = cache["BTC|1d"]["bar_t"] + 2 * 86_400_000 + 1  # the live bar has now closed
    scanner.analyse_breakout(inf, "BTC", S, {"funding": 0}, "1d", cache=cache, now_ms=later)
    assert inf.candle_calls == 2


def test_a_corrupt_cache_entry_is_recomputed():
    inf = CountingInfo(candles={("BTC", "1d"): _live_series()})
    cache = {"BTC|1d": {"nonsense": True}}
    out = scanner.analyse_breakout(inf, "BTC", dict(base_cfg()["scanner"]), {"funding": 0}, "1d", cache=cache)
    assert inf.candle_calls == 1 and out["trend"] == "UP" and "bar_t" in cache["BTC|1d"]


def test_live_price_decides_chasing_without_refetching():
    inf = CountingInfo(candles={("BTC", "1d"): _live_series()})
    cache, S = {}, dict(base_cfg()["scanner"])
    first = scanner.analyse_breakout(inf, "BTC", S, {"funding": 0}, "1d", cache=cache)
    assert first["setup"] == "LONG"
    ran = scanner.analyse_breakout(inf, "BTC", S, {"funding": 0}, "1d", cache=cache, px=first["signal_close"] + 5 * first["atr"])
    assert ran["setup"] is None and "wait for next setup" in ran["rejected"] and inf.candle_calls == 1


def test_near_breakout_is_flagged_as_context():
    st = {"tf": "1d", "bar_t": 0, "k_c": 99.0, "e20": 2, "e50": 1, "atr": 2.0, "hi20": 100.0, "hi20_live": 100.0,
          "rsi": 60, "live_c": 99.5}
    out = scanner.classify(st, 99.5, dict(base_cfg()["scanner"]), "X", 0.0)
    assert out["setup"] is None and abs(out["near_atr"] - 0.25) < 1e-9


# ---------------------------------------------------------------- snapshot momentum
H = 3_600_000


def test_momentum_pairs_with_the_snapshot_closest_to_the_window():
    now = 100 * H
    snaps = [[now - 5.5 * H, {"SOL": 50.0}], [now - 4.1 * H, {"SOL": 100.0}], [now - 3.6 * H, {"SOL": 90.0}]]
    got = scanner.momentum_from_snaps(snaps, {"SOL": 110.0}, now, 4)
    assert abs(got["SOL"] - 10.0) < 1e-9


def test_momentum_is_skipped_without_a_snapshot_in_range():
    now = 100 * H
    assert scanner.momentum_from_snaps([[now - 1 * H, {"SOL": 100.0}]], {"SOL": 120.0}, now, 4) == {}
    assert scanner.momentum_from_snaps([[now - 6 * H, {"SOL": 100.0}]], {"SOL": 120.0}, now, 4) == {}


def test_momentum_alerts_from_stored_snapshots_and_keeps_the_buckets():
    now = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    st = Mem({"mid_snaps": [[now - 4 * H, {"SOL": 100.0, "BTC": 100.0}]]})
    n = Notifier({"telegram": {"enabled": False}})
    S = dict(base_cfg()["scanner"], momentum_window_hours=4, momentum_alert_pct=8)
    scanner.momentum(FakeInfo(), S, universe.settings(S), {}, ["SOL", "BTC"], {"SOL": 112.0, "BTC": 101.0}, st, n, now)
    assert list(n.collected) == ["mom_SOL_2"]  # +12% -> bucket 2, same key scheme as before
    assert st.get("mid_snaps")[-1][0] == now and len(st.get("mid_snaps")) == 2


def test_auto_mode_watches_every_liquid_perp_for_momentum():
    now = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    st = Mem({"mid_snaps": [[now - 4 * H, {"OUTSIDE": 10.0}]]})
    n = Notifier({"telegram": {"enabled": False}})
    S = dict(base_cfg()["scanner"], universe=dict(U))
    scanner.momentum(FakeInfo(), S, universe.settings(S), _ctx(OUTSIDE=2e7), ["BTC"], {"OUTSIDE": 12.0}, st, n, now)
    assert any(k.startswith("mom_OUTSIDE_") for k in n.collected)


# ---------------------------------------------------------------- allowed_coins: auto
def test_allowed_coins_auto_uses_todays_universe():
    from hlg import guardrails

    from .conftest import make_position, make_user_state

    cfg = base_cfg()
    cfg["rules"]["allowed_coins"] = "auto"
    inf = FakeInfo(user_state=make_user_state(10000, [make_position("ENA", 100, 1.0, 0)]), mids={"ENA": "1.0"})
    st = Mem({"universe": {"day": "x", "coins": ["BTC", "UNI"]}})
    n = Notifier(cfg)
    guardrails.run_once(cfg, inf, n, st)
    assert "coin_ENA" in n.collected and "liquid universe" in n.collected["coin_ENA"]
    st2, n2 = Mem({"universe": {"day": "x", "coins": ["ENA"]}}), Notifier(cfg)
    guardrails.run_once(cfg, inf, n2, st2)
    assert "coin_ENA" not in n2.collected


# ---------------------------------------------------------------- backtest eligibility
def _df(start, n, notional):
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.DataFrame({"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": float(notional)}, index=idx)


def test_eligibility_never_sees_the_day_it_is_used_on():
    df = _df("2024-01-01", 120, 20e6)
    df.loc[df.index[100]:, "v"] = 1.0   # volume collapses on day 100
    mask, med = backtest.eligibility({"A": df}, dict(top_n=5, min_vol_usd=10e6, min_age_days=30))
    day = df.index[100]
    assert mask.at[day, "A"]  # on day 100 it only knows days <= 99, all liquid
    assert not mask.at[df.index[119], "A"]  # a few weeks later the median has caught up


def test_a_delisted_coin_is_eligible_while_it_traded():
    alive = _df("2024-01-01", 200, 20e6)
    dead = _df("2024-01-01", 100, 20e6)  # delisted after 100 days
    mask, _ = backtest.eligibility({"ALIVE": alive, "DEAD": dead}, dict(top_n=5, min_vol_usd=10e6, min_age_days=30))
    assert mask.at[dead.index[60], "DEAD"] and not mask.at[alive.index[150], "DEAD"]


def test_top_n_and_age_filter():
    data = {"BIG": _df("2024-01-01", 120, 90e6), "MID": _df("2024-01-01", 120, 50e6),
            "SMALL": _df("2024-01-01", 120, 20e6), "NEW": _df("2024-04-01", 30, 500e6)}
    mask, _ = backtest.eligibility(data, dict(top_n=2, min_vol_usd=10e6, min_age_days=60))
    last = pd.Timestamp("2024-04-20", tz="UTC")  # NEW is 19 days old here, the others 110
    assert list(mask.columns[mask.loc[last].fillna(False).astype(bool)]) == ["BIG", "MID"]


def test_same_day_signals_fill_most_liquid_first():
    med = pd.DataFrame({"A": [1e6], "B": [9e6]}, index=pd.DatetimeIndex(["2024-01-01"], tz="UTC"))
    P = {"liq_med": med}
    day = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    order = sorted({"A": (0, 0, 0, day), "B": (0, 0, 0, day)}.items(), key=lambda kv: -backtest._prio(P, kv[0], kv[1][3]))
    assert [c for c, _ in order] == ["B", "A"]
    assert np.isclose(backtest._prio({}, "A", day), 0.0)
