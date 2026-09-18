"""hlg.macro parsing, the lookahead shift, and the entry-filter predicates.

Nothing here touches the network: fetch() is monkeypatched with canned FRED CSV text.
"""
import numpy as np
import pandas as pd
import pytest

from hlg import backtest, macro, scanner


def _csv(series_id, rows):
    body = "\n".join(f"{d},{v}" for d, v in rows)
    return f"observation_date,{series_id}\n{body}\n"


def test_fetch_parses_fred_csv_and_drops_holiday_dots(tmp_path, monkeypatch):
    raw = _csv("VIXCLS", [("2026-01-02", "17.2"), ("2026-01-05", "."), ("2026-01-06", "18.4")])

    class R:
        text = raw
        def raise_for_status(self):
            pass

    monkeypatch.setattr(macro.requests, "get", lambda *a, **k: R())
    s = macro.fetch("vix", "VIXCLS", cache_dir=str(tmp_path))
    assert list(s.values) == [17.2, 18.4]  # the "." holiday row is dropped, not parsed as 0
    assert str(s.index[0].date()) == "2026-01-02"


def test_frame_forward_fills_weekends_then_shifts_a_day(tmp_path, monkeypatch):
    # Fri 2026-01-02 then Mon 2026-01-05: the weekend must inherit Friday, and everything shifts +1d
    rows = [("2026-01-02", "10"), ("2026-01-05", "20")]
    monkeypatch.setattr(macro, "fetch", lambda name, sid, start="2020-01-01", cache_dir="": pd.Series(
        [float(v) for _, v in rows], index=pd.to_datetime([d for d, _ in rows], utc=True), name=name))
    f = macro.frame(cache_dir=str(tmp_path), end="2026-01-07")
    # Friday's value must not be readable on Friday itself -- only from Saturday on
    assert np.isnan(f.loc["2026-01-02"].hy)
    assert f.loc["2026-01-03"].hy == 10      # Saturday sees Friday
    assert f.loc["2026-01-05"].hy == 10      # Monday still sees Friday, not Monday's own print
    assert f.loc["2026-01-06"].hy == 20      # Monday's value only readable Tuesday


def test_features_flag_stress_relative_to_own_trend(tmp_path, monkeypatch):
    n = 300
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    rising = pd.Series(np.linspace(3.0, 6.0, n), index=idx)   # spreads widening the whole way
    falling = pd.Series(np.linspace(6.0, 3.0, n), index=idx)
    df = pd.DataFrame({"hy": rising, "vix": falling, "spx": pd.Series(np.linspace(100, 200, n), index=idx),
                       "dxy": falling, "y10": rising})
    f = macro.features(df)
    assert bool(f.hy_stress.iloc[-1]) is True     # rising series sits above its own EMA
    assert bool(f.vix_calm.iloc[-1]) is True      # falling VIX sits below its own EMA
    assert bool(f.spx_bull.iloc[-1]) is True      # rising SPX above its 200d
    assert bool(f.dxy_headwind.iloc[-1]) is False
    assert bool(f.risk_on.iloc[-1]) is False      # hy_stress vetoes risk_on even with SPX bullish


def test_features_on_empty_frame_is_empty():
    assert macro.features(pd.DataFrame()).empty


# ----------------------------------------------------------------- entry filters
class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.mark.parametrize("name,passing,failing", [
    ("vol_confirm", {"vol_ratio": 2.0}, {"vol_ratio": 0.9}),
    ("squeeze", {"atr_pctile": 0.2}, {"atr_pctile": 0.9}),
    ("not_extended", {"ext_atr": 1.0}, {"ext_atr": 3.0}),
    ("cheap_funding", {"funding_apr": 10.0}, {"funding_apr": 80.0}),
    ("btc_bull", {"btc_bull": True}, {"btc_bull": False}),
    ("rs_top_half", {"rs_rank": 0.8}, {"rs_rank": 0.2}),
    ("no_credit_stress", {"hy_stress": False}, {"hy_stress": True}),
    ("spx_bull", {"spx_bull": True}, {"spx_bull": False}),
    ("risk_on", {"risk_on": True}, {"risk_on": False}),
])
def test_each_filter_accepts_and_rejects(name, passing, failing):
    assert backtest.FILTERS[name](Row(**passing)) is True
    assert backtest.FILTERS[name](Row(**failing)) is False


def test_missing_data_never_rejects_a_trade():
    """A NaN reading must pass, or a filter would silently shrink the early sample (before rolling
    windows fill) and look like an edge it does not have."""
    for name, fn in backtest.FILTERS.items():
        assert fn(Row()) is True, f"{name} rejected a row with no data at all"
        assert fn(Row(vol_ratio=np.nan, atr_pctile=np.nan, ext_atr=np.nan, funding_apr=np.nan,
                      btc_bull=np.nan, rs_rank=np.nan, hy_stress=np.nan, vix_calm=np.nan,
                      spx_bull=np.nan, dxy_headwind=np.nan, risk_on=np.nan)) is True, f"{name} rejected NaN"


def test_signal_applies_configured_filters():
    k = Row(ema50=1.0, atr=1.0, lo20=1.0, ema20=2.0, c=100.0, hi20=50.0, rsi=50.0, vol_ratio=0.1)
    V = dict(side="long", trend="with", rsi_max=100, level="breakout", stop_atr=2.0,
             target=None, min_rr=0, max_days=21, trail_atr=3.0)
    assert backtest.signal(k, V) is not None                        # breakout fires without filters
    assert backtest.signal(k, {**V, "filters": ["vol_confirm"]}) is None  # ... and is vetoed with one


def test_macro_context_disabled_makes_no_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("macro_context must not fetch when disabled")

    monkeypatch.setattr(macro.requests, "get", boom)
    assert scanner.macro_context({"macro_context": False}) is None


def test_macro_context_labels_the_current_backdrop(monkeypatch):
    idx = pd.date_range("2025-01-01", periods=300, freq="D", tz="UTC")
    rising = pd.Series(np.linspace(3.0, 6.0, 300), index=idx)
    monkeypatch.setattr(macro, "frame", lambda **k: pd.DataFrame(
        {"hy": rising, "vix": rising, "spx": rising, "dxy": rising, "y10": rising}))
    c = scanner.macro_context({"macro_context": True})
    assert c["hy_stress"] is True and c["risk_on"] is False
    assert "credit stress" in c["label"]


def test_macro_context_survives_a_dead_data_source(monkeypatch):
    """A FRED outage must degrade to "no context", never break a scan -- this runs against a live
    account every 15 minutes."""
    monkeypatch.setattr(macro, "frame", lambda **k: (_ for _ in ()).throw(RuntimeError("FRED down")))
    assert scanner.macro_context({"macro_context": True}) is None


def test_bootstrap_flags_a_random_subset_as_noise():
    rng = np.random.default_rng(1)
    base = rng.normal(2, 40, 400)
    sub = rng.choice(base, size=250, replace=False)
    pct = backtest.bootstrap_pf(base, len(sub), backtest._pf_of(sub), n_boot=2000)
    assert 1 < pct < 99  # a genuinely random subset should not look special

    cherry = np.sort(base)[-250:]  # the 250 best trades: must land at the top
    assert backtest.bootstrap_pf(base, 250, backtest._pf_of(cherry), n_boot=2000) > 99
