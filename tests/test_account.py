"""The account model: which equity base every rule sizes against.

The numbers in the first test are the live account's, taken while HL's own UI showed
"Unified Account Ratio 3.84%" and "Unified Account Leverage 0.77x". Reproducing those exactly is
the acceptance test: if the formula drifts, this fails before a wrong size reaches an alert.
"""
from hlg import account, guardrails
from hlg.common import Notifier, State

from .conftest import (
    NOW_MS,
    FakeInfo,
    base_cfg,
    make_position,
    make_spot,
    make_stop_order,
    make_unified_portfolio,
    make_user_state,
)

LIVE_ST = {
    "marginSummary": {"accountValue": "1208.661276", "totalNtlPos": "12132.15627",
                      "totalRawUsd": "-10923.494994", "totalMarginUsed": "1213.215627"},
    "crossMaintenanceMarginUsed": 606.607813,
    "assetPositions": [],
}
LIVE_SPOT = {"balances": [
    {"coin": "USDC", "token": 0, "total": "15785.06518916"},
    {"coin": "HYPE", "token": 150, "total": "106.68128317"},
    {"coin": "UBTC", "token": 197, "total": "0.0076065371"},
    {"coin": "MAX", "token": 734, "total": "2893333.0002"},
]}


def test_unified_model_reproduces_hls_ratio_and_leverage():
    m = account.summarise(LIVE_ST, "unifiedAccount", LIVE_SPOT)
    assert m["unified"] and m["base_label"] == "USDC collateral"
    assert abs(m["base"] - 15785.065) < 0.01
    assert round(m["ratio_pct"], 2) == 3.84      # HL: Unified Account Ratio 3.84%
    assert round(m["leverage"], 2) == 0.77       # HL: Unified Account Leverage 0.77x
    assert m["perp_account_value"] < 1300        # the number the tool used to size against


def test_classic_account_keeps_perp_equity_as_base():
    """A "disabled" account margins perps from the perp wallet; spot USDC there is not collateral,
    so switching to it would be wrong (one real one had $596k perp equity and $0 spot USDC)."""
    m = account.summarise({"marginSummary": {"accountValue": "596782", "totalNtlPos": "0"}},
                          "disabled", {"balances": [{"coin": "USDC", "token": 0, "total": "50"}]})
    assert not m["unified"] and m["base"] == 596782 and m["base_label"] == "perp equity"
    assert m["spot_units"] == {}  # spot is a separate wallet: not exposure to judge here


def test_unified_without_usdc_falls_back_instead_of_dividing_by_zero():
    m = account.summarise(LIVE_ST, "unifiedAccount", {"balances": [{"coin": "USDC", "token": 0, "total": "0"}]})
    assert m["base_label"] == "perp equity" and m["leverage"] < 20


def test_spot_tokens_map_to_the_perp_coin_they_track():
    m = account.summarise(LIVE_ST, "unifiedAccount", LIVE_SPOT)
    assert m["spot_units"]["HYPE"] > 106 and m["spot_units"]["BTC"] > 0
    assert account.spot_coin("UETH") == "ETH" and account.spot_coin("USOL") == "SOL"


def test_spot_is_valued_at_the_perp_mid_not_its_own_book():
    """Memecoin spot marks are garbage (MAX once priced this account at $23.8M). Only coins with an
    open perp position are valued, at the liquid perp mid."""
    m = account.summarise(LIVE_ST, "unifiedAccount", LIVE_SPOT)
    e = account.coin_exposure(m, [{"coin": "HYPE", "szi": "91.5", "entryPx": "80"}], {"HYPE": "87"})
    assert set(e) == {"HYPE"}                       # MAX never enters the picture
    assert abs(e["HYPE"]["spot_usd"] - 106.68128317 * 87) < 1e-6
    assert abs(e["HYPE"]["net_usd"] - (91.5 + 106.68128317) * 87) < 1e-6


def test_a_perp_short_against_a_spot_bag_nets_down():
    m = account.summarise(LIVE_ST, "unifiedAccount", LIVE_SPOT)
    e = account.coin_exposure(m, [{"coin": "HYPE", "szi": "-100", "entryPx": "87"}], {"HYPE": "87"})
    assert abs(e["HYPE"]["net_usd"]) < 0.07 * 87 * 100  # a hedge, not a bet


def test_load_treats_an_unknown_mode_as_classic():
    """Failing to classic on a unified account sizes off the small perp value -- too conservative,
    never too aggressive, which is the right way for a guardrail to fail."""
    inf = FakeInfo(user_state=make_user_state(1200, []), abstraction=None)
    m, _ = account.load(inf, "0xT")
    assert not m["unified"] and m["base"] == 1200


# --------------------------------------------------------- guardrails end to end
def unified_info(positions, day_pnl=0, week_pnl=0, orders=(), mids=None, perp_value=1200):
    st = make_user_state(perp_value, positions)
    st["marginSummary"]["totalNtlPos"] = str(sum(abs(float(p["positionValue"])) for p in positions))
    st["crossMaintenanceMarginUsed"] = "600"
    return FakeInfo(user_state=st, abstraction="unifiedAccount", spot=make_spot(15800, HYPE=106),
                    portfolio=make_unified_portfolio(day_pnl, week_pnl, 27000),
                    open_orders=list(orders), mids=mids or {})


def run(info, tmp_path, **rule_overrides):
    cfg = base_cfg()
    cfg["rules"].update(rule_overrides)
    notif = Notifier(cfg)
    return guardrails.run_once(cfg, info, notif, State(tmp_path / "s.json")), notif


def test_book_that_is_10x_perp_equity_but_small_vs_collateral_is_not_flagged(tmp_path):
    """The exact false alarm the dashboard was raising: 12k notional read as 10x a 1.2k 'equity'."""
    pos = make_position("BTC", sz=0.1, entry=100000, upnl=0, position_value=12000)
    stop = make_stop_order("BTC", "A", 0.1, trigger_px=97700)  # 230 risk vs 1.5% of 15.8k = 237
    problems, _ = run(unified_info([pos], orders=[stop], mids={"BTC": "100000"}), tmp_path,
                      allowed_coins=["BTC"])
    assert not any("leverage" in p for p in problems)
    assert not any("risks" in p for p in problems)  # the old 18 USD budget would call this stop too wide


def test_risk_budget_is_sized_off_usdc_collateral(tmp_path):
    pos = make_position("BTC", sz=1.0, entry=100, upnl=0, position_value=100)
    problems, _ = run(unified_info([pos], mids={"BTC": "100"}), tmp_path, allowed_coins=["BTC"])
    nostop = next(p for p in problems if "NO STOP" in p)
    assert "risk 237 USD" in nostop  # 1.5% of 15,800 USDC -- not 18 USD off the 1.2k perp value


def test_unified_loss_limits_use_the_whole_account_series(tmp_path):
    # perpDay in the fixture reads -999 on a 1000 base (-99.9%): it must be ignored
    problems, _ = run(unified_info([], day_pnl=-100, week_pnl=-100), tmp_path)
    assert not any("LOSS LIMIT" in p for p in problems)
    # -900 of 27,000 = -3.3% of the whole account: over the 3% daily limit
    problems, _ = run(unified_info([], day_pnl=-900, week_pnl=-900), tmp_path)
    assert any("DAILY LOSS LIMIT" in p for p in problems)


def test_a_lock_set_under_the_old_equity_base_is_voided(tmp_path):
    """The live case: a daily lock fired when equity was read as $872 of perp margin, while the
    whole account was up 8%. Under the corrected base it is not a breach and must not linger."""
    state = State(tmp_path / "s.json")
    state.set("lock_until", NOW_MS + 6 * 3600_000)  # left behind by the old code: no lock_basis
    # same week, or the unrelated week-rollover reset clears the lock and this passes for nothing
    state.set("week_key", guardrails.week_key(guardrails.utc_now()))
    cfg = base_cfg()
    guardrails.run_once(cfg, unified_info([], day_pnl=2000, week_pnl=3000), Notifier(cfg), state)
    assert state.get("lock_until") == 0
    assert state.get("lock_basis") == "USDC collateral"


def test_a_legacy_lock_on_a_classic_account_survives_the_upgrade(tmp_path):
    """Old code always sized on perp equity, which is still a classic account's base -- so a real
    lock left behind on one must not be voided just because no basis was recorded."""
    from .conftest import make_portfolio

    state = State(tmp_path / "s.json")
    state.set("lock_until", NOW_MS + 6 * 3600_000)
    state.set("week_key", guardrails.week_key(guardrails.utc_now()))
    cfg = base_cfg()
    inf = FakeInfo(user_state=make_user_state(10000, []), portfolio=make_portfolio(0, 0, 10000))
    guardrails.run_once(cfg, inf, Notifier(cfg), state)
    assert state.get("lock_until") == NOW_MS + 6 * 3600_000


def test_a_real_lock_survives_under_an_unchanged_basis(tmp_path):
    """Locks are meant to persist for the day even if PnL recovers -- the basis guard must not
    turn into a way of clearing a genuine one."""
    state = State(tmp_path / "s.json")
    cfg = base_cfg()
    guardrails.run_once(cfg, unified_info([], day_pnl=-900), Notifier(cfg), state)  # -3.3%: locks
    locked = state.get("lock_until")
    assert locked > NOW_MS
    guardrails.run_once(cfg, unified_info([], day_pnl=500), Notifier(cfg), state)   # recovered
    assert state.get("lock_until") == locked


def test_perp_long_stacked_on_a_spot_bag_is_flagged_as_one_bet(tmp_path):
    pos = make_position("HYPE", sz=91.5, entry=80, upnl=600, position_value=7960, leverage=3)
    stop = make_stop_order("HYPE", "A", 91.5, trigger_px=85)
    problems, _ = run(unified_info([pos], orders=[stop], mids={"HYPE": "87"}), tmp_path,
                      allowed_coins=["HYPE"], max_coin_exposure_x=1.0)
    hit = [p for p in problems if "concentrated bet" in p]
    assert len(hit) == 1 and "HYPE" in hit[0] and "spot" in hit[0]


def test_coin_exposure_rule_is_off_when_not_configured(tmp_path):
    pos = make_position("HYPE", sz=91.5, entry=80, upnl=600, position_value=7960, leverage=3)
    stop = make_stop_order("HYPE", "A", 91.5, trigger_px=85)
    problems, _ = run(unified_info([pos], orders=[stop], mids={"HYPE": "87"}), tmp_path, allowed_coins=["HYPE"])
    assert not any("concentrated bet" in p for p in problems)
