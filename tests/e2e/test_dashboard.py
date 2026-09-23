"""Browser-driven tests for pwa/index.html: real Chromium, real WebCrypto, real DOM interaction.

These complement the fast unit suite (tests/test_*.py), which never touches a browser and checks
the *data* hlg/report.py produces. This suite checks what a person actually sees and clicks:
decrypting real ciphertext, the alert list's clear/collapse/persist behaviour, tab and range state
surviving a reload, the SVG charts drawing what the data says, and -- the check a screenshot can't
give you -- that nothing throws in the console while any of that happens.

Not part of the default `pytest -q` (see pytest.ini). Run explicitly:

    pip install -r requirements-e2e.txt && playwright install chromium
    pytest tests/e2e -q
"""
import re

import pytest
from playwright.sync_api import expect

from . import fixtures as F
from .conftest import url

pytestmark = pytest.mark.e2e
expect.set_options(timeout=8000)


# ---------------------------------------------------------------------- the lock gate & crypto
def test_locked_stub_shows_the_configure_message_not_a_bare_error(page, base_url):
    """hlg.report publishes LOCKED_STUB, never plaintext, when no passphrase is configured in CI."""
    page.goto(url(base_url, "stub"))
    expect(page.locator("#gate")).to_be_visible()
    expect(page.locator("#gatemsg")).to_contain_text("DASHBOARD_PASSPHRASE secret is set")
    expect(page.locator("#pass")).to_be_disabled()
    expect(page.locator("#app")).to_be_hidden()


def test_wrong_passphrase_is_rejected_and_nothing_renders(page, base_url):
    page.goto(url(base_url, "crypt"))
    expect(page.locator("#gate")).to_be_visible()
    page.locator("#pass").fill("definitely not it")
    page.locator("#unlockbtn").click()
    expect(page.locator("#unlockmsg")).to_have_text("Wrong passphrase.")
    expect(page.locator("#app")).to_be_hidden()
    expect(page.locator("#hval")).to_have_text("—")  # never populated with the wrong key


def test_correct_passphrase_unlocks_and_the_device_stays_unlocked_after_reload(page, base_url):
    page.goto(url(base_url, "crypt"))
    page.locator("#pass").fill(F.PASSPHRASE)
    page.locator("#unlockbtn").click()
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#gate")).to_be_hidden()
    expect(page.locator("#hval")).to_have_text("$12,133")  # real AES-GCM ciphertext, really decrypted
    expect(page.locator("#lock")).to_be_visible()  # only an encrypted scenario shows Lock

    # the derived key was saved (non-extractable) in IndexedDB, so a reload must NOT re-prompt
    page.reload()
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#gate")).to_be_hidden()
    expect(page.locator("#hval")).to_have_text("$12,133")


def test_lock_forgets_the_key_and_the_gate_returns(page, base_url):
    page.goto(url(base_url, "crypt"))
    page.locator("#pass").fill(F.PASSPHRASE)
    page.locator("#unlockbtn").click()
    expect(page.locator("#app")).to_be_visible()
    page.locator("#lock").click()  # forgetKey() + location.reload()
    expect(page.locator("#gate")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()


def test_a_served_envelope_below_the_iteration_floor_is_refused(page, base_url):
    """The dashboard must not trust a downgraded KDF parameter. Rather than reimplement the check
    here (which would test the test, not the page), intercept the real request and serve the real
    envelope back with `iter` tampered down, then drive the actual gate form with it."""
    page.goto(url(base_url, "crypt"))
    real = page.evaluate("fetch('alerts.json?t=' + Date.now()).then(r => r.json())")
    weak = {**real, "iter": 1000}

    def serve_weak(route):
        import json as _json
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(weak))

    page.route("**/alerts.json*", serve_weak)
    page.reload()
    page.locator("#pass").fill(F.PASSPHRASE)  # the right passphrase -- only the KDF strength is wrong
    page.locator("#unlockbtn").click()
    expect(page.locator("#unlockmsg")).to_have_text("weak KDF parameters refused")
    expect(page.locator("#app")).to_be_hidden()


# ---------------------------------------------------------------------- hero, gauges
def test_hero_shows_portfolio_value_pnl_chips_and_trading_state(page, base_url):
    page.goto(url(base_url, "full"))
    expect(page.locator("#hval")).to_have_text("$12,133")
    expect(page.locator("#hlabel")).to_have_text("Portfolio")
    expect(page.locator("#hcollat")).to_contain_text("$9,800 USDC collateral")
    chips = page.locator("#hchips")
    expect(chips).to_contain_text("Today")
    expect(chips).to_contain_text("This week")
    expect(chips).to_contain_text("Positions")
    expect(chips).to_contain_text("3")  # 3 open positions
    expect(chips).to_contain_text("/ 3")  # max_positions
    expect(page.locator("#hstate")).to_contain_text("Trading allowed")


def test_classic_account_uses_perp_labels_not_unified_ones(page, base_url):
    page.goto(url(base_url, "empty"))
    expect(page.locator("#hlabel")).to_have_text("Perp equity")
    expect(page.locator("#hval")).to_have_text("$10,000")


def test_stale_data_turns_the_header_dot_red_and_says_stale(page, base_url):
    page.goto(url(base_url, "empty"))  # built with age_min=95
    expect(page.locator("#fresh")).to_have_class(re.compile(r"\bneg\b"))
    expect(page.locator("#gen")).to_contain_text("— stale")


def test_gauges_show_the_right_state_for_each_threshold_crossed(page, base_url):
    """full's numbers are chosen (and self-checked at fixture build time) to land exactly one
    gauge in each state, so the icon+word pairing -- never colour alone -- is what's asserted."""
    page.goto(url(base_url, "full"))
    gauges = page.locator("#gauges .gauge")
    expect(gauges).to_have_count(4)

    def state_of(label):
        g = page.locator(f"#gauges .gauge:has(.gl:has-text('{label}'))")
        expect(g).to_be_visible()
        return g

    expect(state_of("Daily loss limit").locator(".gs")).to_have_class(re.compile("st-crit"))
    expect(state_of("Daily loss limit").locator(".gs")).to_contain_text("at limit")
    expect(state_of("Weekly loss limit").locator(".gs")).to_have_class(re.compile("st-warn"))
    expect(state_of("Weekly loss limit").locator(".gs")).to_contain_text("near limit")
    expect(state_of("Account leverage").locator(".gs")).to_have_class(re.compile("st-good"))
    expect(state_of("Account leverage").locator(".gs")).to_contain_text(" ok")
    expect(state_of("Account ratio").locator(".gs")).to_have_class(re.compile("st-crit"))
    expect(page.locator("#gauges .gv")).to_have_count(4)
    for g in page.locator("#gauges .gv").all():
        expect(g).to_have_text(re.compile(r"[\d.]+[%×]"))  # every gauge shows a real number


# ---------------------------------------------------------------------- equity curve
def test_equity_chart_draws_a_line_and_range_buttons_change_it(page, base_url):
    page.goto(url(base_url, "full"))
    chart = page.locator("#eqchart svg")
    expect(chart).to_be_visible()
    path_7d = chart.locator("path").first.get_attribute("d")
    assert path_7d and len(path_7d) > 20

    day_btn = page.locator('#eqrange button[data-r="1"]')
    day_btn.click()
    expect(day_btn).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#eqrange button[data-r="7"]')).to_have_attribute("aria-pressed", "false")
    path_1d = page.locator("#eqchart svg path").first.get_attribute("d")
    assert path_1d != path_7d  # a narrower range actually redraws with fewer/different points

    # the choice survives a reload (localStorage), and the loss-lock reference line only appears
    # at the range it applies to (day lock only drawn within the 24h view)
    expect(page.locator("#eqlegend")).to_contain_text("loss lock")
    page.reload()
    expect(page.locator('#eqrange button[data-r="1"]')).to_have_attribute("aria-pressed", "true")


def test_thin_history_gives_a_usable_chart_over_a_long_span(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('#eqrange button[data-r="0"]').click()  # "All"
    expect(page.locator("#eqchart svg")).to_be_visible()
    expect(page.locator("#eqchart .empty")).to_have_count(0)


# ---------------------------------------------------------------------- alerts: grouping, expand, clear
def test_alert_groups_appear_in_priority_order_with_correct_counts(page, base_url):
    page.goto(url(base_url, "full"))
    groups = page.locator(".grp")
    expect(groups).to_have_count(5)
    order = groups.evaluate_all("els => els.map(e => e.dataset.cat)")
    assert order == ["position", "entry", "heads_up", "info", "ended"]
    expect(page.locator('.grp[data-cat="position"] .n')).to_have_text("1")
    expect(page.locator('.grp[data-cat="entry"] .n')).to_have_text("2")
    expect(page.locator("#ncount")).to_have_text("3 need attention")  # 1 position + 2 entries


def test_a_guardrail_breach_has_no_clear_button_and_shows_new(page, base_url):
    page.goto(url(base_url, "full"))
    breach = page.locator('.al.position[data-key="lev_ETH"]')
    expect(breach).to_be_visible()
    expect(breach.locator('[data-act="clear"]')).to_have_count(0)
    expect(breach.locator(".new")).to_have_text("NEW")


def test_expanding_a_breakout_alert_shows_its_candle_chart(page, base_url):
    page.goto(url(base_url, "full"))
    ena = page.locator('.al[data-key^="setup_ENA_LONG"]')
    ena.locator(".sum").click()
    expect(ena.locator("pre")).to_contain_text("BREAKOUT LONG ENA")
    candles = ena.locator("svg g[data-tip]")
    expect(candles).to_have_count(40)  # one <g> per candle bar
    expect(ena.locator("svg text", has_text="breakout level")).to_be_visible()
    expect(ena.locator("svg text", has_text="stop")).to_be_visible()
    # collapsing hides the detail again
    ena.locator(".sum").click()
    expect(ena.locator("pre")).to_have_count(0)


def test_an_entry_alert_with_no_matching_scan_bars_shows_text_but_no_chart(page, base_url):
    """UNI's scan row deliberately has no `bars` this run -- the chart must degrade to nothing,
    not throw, and the alert's own text still expands."""
    page.goto(url(base_url, "full"))
    uni = page.locator('.al[data-key^="setup_UNI_LONG"]')
    uni.locator(".sum").click()
    expect(uni.locator("pre")).to_contain_text("BREAKOUT LONG UNI")
    expect(uni.locator("svg")).to_have_count(0)
    expect(uni.locator(".sub")).to_contain_text("window closing")  # its valid_until is already past


def test_info_alerts_are_dashboard_only_and_say_so(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.gh[data-act="collapse"]', has_text="Info").click()
    funding = page.locator('.al[data-key="fund_ZEC_4"]')
    expect(funding).to_be_visible()
    expect(funding).to_have_class(re.compile(r"\bquiet\b"))
    expect(funding.locator(".sub")).to_contain_text("dashboard only")


def test_clearing_an_alert_hides_it_and_survives_reload_then_restores(page, base_url):
    """Heads-up starts *expanded* by default (only info/ended start collapsed -- see `collapsed`'s
    initial value in pwa/index.html), so no collapse click is needed to see the momentum alert."""
    page.goto(url(base_url, "full"))
    momentum = page.locator('.al[data-key="mom_TAO_1"]')
    expect(momentum).to_be_visible()
    momentum.locator('[data-act="clear"]').click()
    expect(momentum).to_have_count(0)

    page.reload()
    expect(page.locator('.al[data-key="mom_TAO_1"]')).to_have_count(0)  # the clear survived the reload
    expect(page.locator("#alerts")).to_contain_text("1 cleared on this device")
    page.locator('[data-act="restore"]').click()
    expect(page.locator('.al[data-key="mom_TAO_1"]')).to_be_visible()


def test_clear_all_hides_every_alert_in_its_group_only(page, base_url):
    page.goto(url(base_url, "full"))
    entries = page.locator('.grp[data-cat="entry"] .al')
    expect(entries).to_have_count(2)
    page.locator('.grp[data-cat="entry"] [data-act="clearall"]').click()
    expect(entries).to_have_count(0)
    # guardrail breach (a different group) is untouched
    expect(page.locator('.al.position[data-key="lev_ETH"]')).to_be_visible()


def test_ended_signals_show_why_they_ended(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.gh[data-act="collapse"]', has_text="Recently ended").click()
    missed = page.locator('.al.ended[data-key*="ARB"]')
    expect(missed.locator(".st.missed")).to_have_text("missed")
    expect(missed).to_contain_text("ran 7.2% past the signal close")
    failed = page.locator('.al.ended[data-key*="XMR"]')
    expect(failed.locator(".st.failed")).to_have_text("failed")


def test_flat_account_shows_every_empty_state_message(page, base_url):
    page.goto(url(base_url, "empty"))
    expect(page.locator("#alerts")).to_contain_text("All rules satisfied. No live signals.")
    expect(page.locator("#positions")).to_contain_text("Flat — no open positions.")
    expect(page.locator("#journal")).to_contain_text("No breakout signals recorded yet.")
    page.locator('.tab[data-tab="macro"]').click()
    expect(page.locator("#tradfi")).to_contain_text("No live TradFi markets.")
    page.locator('.tab[data-tab="flow"]').click()
    expect(page.locator("#liqrisk")).to_contain_text("Flat — nothing to liquidate.")
    expect(page.locator("#market")).to_contain_text("No market data.")
    expect(page.locator("#lmcard")).to_be_hidden()  # no liqmap at all in this scenario
    expect(page.locator("#liq")).to_contain_text("No recent liquidations.")  # OKX stub returned []


# ---------------------------------------------------------------------- positions ladder
def test_position_without_a_stop_warns_and_shows_the_risk_based_stop(page, base_url):
    page.goto(url(base_url, "full"))
    hype = page.locator(".pcard", has_text="HYPE")
    expect(hype.locator(".warnchip")).to_have_text("! No stop on the book")
    expect(hype).to_contain_text("1.5% risk stop would be 93.65")
    expect(hype.locator("svg text", has_text="1.5% risk")).to_be_visible()


def test_position_with_partial_stop_coverage_warns_with_the_percentage(page, base_url):
    page.goto(url(base_url, "full"))
    eth = page.locator(".pcard", has_text="ETH")
    expect(eth.locator(".warnchip")).to_have_text("! Stop covers 40% of the size")


def test_a_fully_covered_position_shows_no_warning(page, base_url):
    page.goto(url(base_url, "full"))
    sol = page.locator(".pcard", has_text="SOL")
    expect(sol.locator(".warnchip")).to_have_count(0)
    expect(sol.locator(".pill.long")).to_contain_text("LONG")
    expect(sol.locator("svg")).to_be_visible()


# ---------------------------------------------------------------------- scanner / near-breakout
def test_near_breakout_list_is_sorted_closest_first(page, base_url):
    page.goto(url(base_url, "full"))
    expect(page.locator("#nearcard")).to_be_visible()
    coins = page.locator("#near b").all_text_contents()
    assert coins == ["TAO"]  # only non-watch coin with near_atr in this fixture


def test_scanner_table_groups_by_timeframe_and_shows_the_setup(page, base_url):
    page.goto(url(base_url, "full"))
    expect(page.locator(".tfhead")).to_have_count(2)  # 1d and 4h present
    ena_row = page.locator("#scan tr", has_text="ENA")
    expect(ena_row.locator(".pill.long")).to_have_text("LONG")
    btc_row = page.locator("#scan tr", has_text="BTC")
    expect(btc_row).to_contain_text("trend down")
    watch_row = page.locator("#scan tr", has_text="xyz:CL")
    expect(watch_row).to_have_attribute("style", re.compile("opacity"))


# ---------------------------------------------------------------------- journal
def test_journal_shows_stats_chart_and_the_too_few_disclaimer_under_30(page, base_url):
    page.goto(url(base_url, "full"))  # journal_closed=8
    chips = page.locator("#journal .chips")
    expect(chips).to_contain_text("Win rate")
    expect(chips).to_contain_text("PF")
    expect(page.locator("#jchart svg")).to_be_visible()
    expect(page.locator("#journal")).to_contain_text("8 closed so far — too few to judge")


def test_journal_disclaimer_disappears_once_enough_signals_have_closed(page, base_url):
    page.goto(url(base_url, "mature"))  # journal_closed=34
    expect(page.locator("#journal")).not_to_contain_text("too few to judge")
    expect(page.locator("#jchart svg")).to_be_visible()


# ---------------------------------------------------------------------- liquidation map
def test_liquidation_map_switches_coin_and_the_choice_persists(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="flow"]').click()
    expect(page.locator("#lmcard")).to_be_visible()
    expect(page.locator('#lmcoins button[aria-pressed="true"]')).to_have_text("BTC")  # most liquidation $ near price
    before = page.locator("#lmchart svg").inner_html()

    eth_btn = page.locator('#lmcoins button[data-c="ETH"]')
    eth_btn.click()
    expect(eth_btn).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#lmcoins button[data-c="BTC"]')).to_have_attribute("aria-pressed", "false")
    after = page.locator("#lmchart svg").inner_html()
    assert before != after

    page.reload()
    page.locator('.tab[data-tab="flow"]').click()
    expect(page.locator('#lmcoins button[data-c="ETH"]')).to_have_attribute("aria-pressed", "true")


def test_a_coin_with_only_distant_clusters_is_listed_as_nothing_within_5pct(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="flow"]').click()
    # ZEC has a real cluster (so it's selectable) but only at +12%, outside the 5% summary window
    expect(page.locator('#lmcoins button[data-c="ZEC"]')).to_be_visible()
    expect(page.locator("#lm")).to_contain_text("Nothing within 5%: ZEC")


def test_liquidation_bars_render_one_per_cluster_plus_the_now_marker(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="flow"]').click()
    page.locator('#lmcoins button[data-c="BTC"]').click()
    svg = page.locator("#lmchart svg")
    expect(svg).to_contain_text("now 81,611")
    expect(svg.locator("g[data-tip]")).to_have_count(3)  # BTC has 3 clusters in the fixture


# ---------------------------------------------------------------------- tabs
def test_tab_selection_persists_across_reload(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    expect(page.locator("#pane-macro")).to_be_visible()
    expect(page.locator("#pane-tech")).to_be_hidden()
    expect(page.locator('.tab[data-tab="macro"]')).to_have_attribute("aria-selected", "true")
    page.reload()
    expect(page.locator("#pane-macro")).to_be_visible()
    expect(page.locator('.tab[data-tab="macro"]')).to_have_attribute("aria-selected", "true")


def test_macro_backdrop_card_only_shows_when_macro_data_exists(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    expect(page.locator("#macrocard")).to_be_visible()
    expect(page.locator("#macro")).to_contain_text("context only, not part of any rule")


def test_daily_brief_shows_tilt_points_and_source_links(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    brief = page.locator("#brief")
    expect(brief.locator(".tilt")).to_have_text("▼ Risk-off")  # icon + word, not colour alone
    expect(brief).to_contain_text("medium confidence")
    expect(brief.locator(".brief-h")).to_have_text("Hot inflation print risk and ETF outflows weigh on crypto into CPI")
    expect(brief.locator("li")).to_have_count(2)
    expect(brief.locator('a[href="https://example.com/etf"]')).to_have_attribute("rel", "noopener noreferrer")
    expect(brief.locator(".chip")).to_have_text(["CPI Wednesday 12:30 UTC", "ETF flows"])
    expect(brief).to_contain_text("Reading material, not a signal")
    expect(brief).to_contain_text("1 unreachable")
    expect(brief).to_contain_text("Written by anthropic/claude-opus-5.5 via OpenRouter")


def test_hostile_text_in_the_brief_is_inert(page, base_url):
    """The brief is model output built from third-party headlines: markup must render as text, and
    a javascript: or quote-injected link must never become a live link."""
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    brief = page.locator("#brief")
    expect(brief).to_contain_text('<img src=x onerror="window.__pwned=1"> Fed speakers stay cautious.')
    expect(brief).to_contain_text("Evil: <b>click</b>")
    assert brief.locator("img").count() == 0 and brief.locator("b").count() == 0
    hrefs = [a.get_attribute("href") for a in brief.locator("a").all()]
    assert hrefs == ["https://example.com/etf"], hrefs  # the injected one fails safeUrl and stays text
    brief.hover()
    for a in brief.locator("li").all():
        a.hover()
    assert page.evaluate("window.__pwned") is None


def test_calendar_lists_releases_with_countdowns_and_next_fomc(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    rows = page.locator("#cal .ev")
    expect(rows).to_have_count(3)
    expect(rows.nth(0)).to_have_class(re.compile(r"\bpast\b"))
    expect(rows.nth(0).locator(".cd")).to_have_text("released")
    expect(rows.nth(1)).to_have_class(re.compile(r"\bsoon\b"))  # within 24h: highlighted
    expect(rows.nth(1)).to_contain_text("CPI m/m, Core CPI m/m")
    expect(rows.nth(1)).to_contain_text("forecast 0.3%, prev 0.4%")
    expect(rows.nth(1).locator(".cd")).to_have_text(re.compile(r"^in 1[34]h \d\dm$"))
    expect(rows.nth(2).locator(".cd")).to_have_text(re.compile(r"^in [23]d \d+h$"))
    expect(page.locator("#calnext")).to_have_text(re.compile(r"^next FOMC in 3[45]d \d+h$"))


def test_event_alert_counts_down_instead_of_saying_valid(page, base_url):
    page.goto(url(base_url, "full"))
    ev = page.locator('.al[data-key^="event_USD_"]')
    expect(ev).to_be_visible()  # heads-up group starts expanded
    expect(ev.locator(".sub")).to_contain_text(re.compile(r"in 1[34]h"))
    expect(ev.locator(".sub")).not_to_contain_text("valid")


def test_fear_and_greed_card_shows_value_band_and_chart(page, base_url):
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="macro"]').click()
    card = page.locator("#fng")
    expect(card.locator(".fngv")).to_have_text("71")
    expect(card).to_contain_text("Greed")
    expect(card).to_contain_text("a week ago")
    svg = card.locator("#fngchart svg")
    expect(svg).to_be_visible()
    expect(svg).to_contain_text("extreme greed")
    assert float(svg.get_attribute("viewBox").split()[2]) >= 260  # measured on the visible tab, not a hidden 0px one


def test_macro_tab_empty_states(page, base_url):
    page.goto(url(base_url, "empty"))
    page.locator('.tab[data-tab="macro"]').click()
    expect(page.locator("#brief")).to_contain_text("OPENROUTER_API_KEY or ANTHROPIC_API_KEY")
    expect(page.locator("#calcard")).to_be_hidden()
    expect(page.locator("#fngcard")).to_be_hidden()


# ---------------------------------------------------------------------- notifications / push
def test_push_stays_disabled_without_a_configured_vapid_key(page, base_url):
    page.goto(url(base_url, "full"))  # scenario's config.js has no VAPID_PUBLIC_KEY
    expect(page.locator("#push")).to_be_disabled()


def test_enabling_notifications_updates_the_button(page, base_url, context):
    context.grant_permissions(["notifications"])
    page.goto(url(base_url, "full"))
    page.locator("#notif").click()
    expect(page.locator("#notif")).to_have_text(re.compile("Alerts on|Blocked"))


# ---------------------------------------------------------------------- cross-cutting: errors, layout, themes
@pytest.mark.parametrize("scenario", ["full", "empty", "mature"])
def test_no_console_errors_through_a_realistic_session(page, base_url, console_errors, scenario):
    """The single check a screenshot can't give you: walk through the tabs, expand an alert, switch
    liqmap coins and equity ranges, and make sure nothing threw along the way."""
    page.goto(url(base_url, scenario))
    page.locator('.tab[data-tab="macro"]').click()
    page.locator('.tab[data-tab="flow"]').click()
    for btn in page.locator("#lmcoins button").all():
        btn.click()
    page.locator('.tab[data-tab="tech"]').click()
    for al in page.locator(".al .sum").all():
        al.click()
    for r in page.locator("#eqrange button").all():
        r.click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(150)  # let the debounced resize handler (150ms) fire and re-render
    assert console_errors == []


@pytest.mark.parametrize("width", [375, 820, 1280])
def test_no_horizontal_overflow_at_any_width(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url(base_url, "full"))
    page.locator('.tab[data-tab="flow"]').click()
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 1, f"page is {overflow}px wider than the viewport at {width}px"


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_renders_without_error_in_both_colour_schemes(page, base_url, console_errors, scheme):
    page.emulate_media(color_scheme=scheme)
    page.goto(url(base_url, "full"))
    expect(page.locator("#hval")).to_have_text("$12,133")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg  # a background was actually computed, not the UA default
    assert console_errors == []


def test_static_assets_are_served_and_the_manifest_is_valid_json(page, base_url):
    for path in ("full/sw.js", "full/manifest.webmanifest", "full/icon-192.png", "full/config.js"):
        r = page.request.get(f"{base_url}/{path}")
        assert r.ok, f"{path} -> {r.status}"
    manifest = page.request.get(f"{base_url}/full/manifest.webmanifest").json()
    assert manifest["name"] and manifest["icons"]
