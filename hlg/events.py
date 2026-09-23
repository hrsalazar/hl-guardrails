"""Scheduled high-impact economic events: FOMC decisions, CPI, jobs, PCE and the like.

Scheduled US data moves crypto more reliably than any headline, and unlike a headline it is known
in advance. So this is the one part of "macro news" that can warn *before* the move: a heads-up
(pushed) when a high-impact release is within `warn_hours`, the countdown on the dashboard, and a
note on any breakout entry that would be opened into one.

Two free, keyless sources:

  FairEconomy weekly calendar (the Forex Factory feed)  every scheduled release with an impact
      rating and exact time, but only for the current week. Fetched every `refresh_hours`; items
      are kept in state so the week's list survives a failed fetch.
  federalreserve.gov FOMC calendar  the official meeting schedule, years ahead. Covers what the
      weekly feed can't: "next FOMC in 23 days", and the historical dates the backtest uses.

BLS's own release schedule would be the authoritative source for CPI and payrolls, but it
answers automated requests with HTTP 403 (checked with a browser user agent too), so the weekly
feed stands in for it.

Times are UTC milliseconds throughout. The Fed publishes meeting dates only; the statement comes
at 14:00 New York time on the last day, which is 18:00 UTC in summer and 19:00 in winter, hence
zoneinfo rather than a fixed offset.
"""
import datetime as dt
import re
import zoneinfo

import requests

from .common import log

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
UA = {"User-Agent": "Mozilla/5.0 (hl-guardrails; github.com/hrsalazar/hl-guardrails)"}
NY = zoneinfo.ZoneInfo("America/New_York")
H = 3_600_000
DEFAULTS = dict(enabled=True, warn_hours=24, countries=["USD"], impacts=["High"], refresh_hours=6,
                fomc_refresh_hours=24, keep_days=30)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], start=1)}


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("events") or {})}


def _ms(t):
    return int(t.timestamp() * 1000)


def parse_ff(rows, countries, impacts):
    """The weekly feed -> [{id, title, t, country, impact, forecast, previous}], filtered."""
    out = []
    for r in rows or []:
        if r.get("country") not in countries or r.get("impact") not in impacts:
            continue
        try:
            t = _ms(dt.datetime.fromisoformat(r["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"id": f"ff:{r['country']}:{r['title']}:{t}", "title": r["title"], "t": t,
                    "country": r["country"], "impact": r["impact"],
                    "forecast": r.get("forecast") or "", "previous": r.get("previous") or ""})
    return out


def parse_fomc(html):
    """Decision times (UTC ms) from the Fed's calendar page: the last day of each scheduled
    meeting, 14:00 New York. Notation votes are not meetings with a statement and are skipped."""
    out = []
    heads = list(re.finditer(r"(\d{4}) FOMC Meetings", html))
    for i, h in enumerate(heads):
        year = int(h.group(1))
        section = html[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>', section)
        dates = re.findall(r'fomc-meeting__date[^>]*>([^<]+)<', section)
        for mon, day in zip(months, dates):
            if "notation" in day.lower():
                continue
            nums = [int(n) for n in re.findall(r"\d+", day)]
            names = [m.strip()[:3].lower() for m in mon.split("/")]
            if not nums or names[0] not in MONTHS:
                continue
            # "Apr/May 30-1": the decision is on the second month's day
            name = names[-1] if len(names) > 1 and len(nums) > 1 and nums[-1] < nums[0] else names[0]
            if name not in MONTHS:
                continue
            try:
                local = dt.datetime(year, MONTHS[name], nums[-1], 14, 0, tzinfo=NY)
            except ValueError:
                continue
            out.append(_ms(local))
    return sorted(set(out))


def refresh(state, now_ms, E, get=requests.get):
    """Update state["events"] if stale. Every fetch is independent and fail-soft: a dead source
    keeps its last good data rather than emptying the calendar. A failure retries after an hour,
    not on the next 15-minute run: the weekly feed rate-limits hard (HTTP 429 after a handful of
    requests in a few minutes, measured), so hammering it would only extend the block."""
    ev = dict(state.get("events") or {})
    items = {i["id"]: i for i in ev.get("items") or []}
    if now_ms - ev.get("ff_t", 0) >= E["refresh_hours"] * H:
        try:
            r = get(FF_URL, headers=UA, timeout=10)
            r.raise_for_status()
            for i in parse_ff(r.json(), E["countries"], E["impacts"]):
                items[i["id"]] = i
            ev["ff_t"] = ev["ff_ok"] = now_ms
        except Exception as e:  # noqa: BLE001
            log.error("events: calendar fetch failed: %s", e)
            ev["ff_t"] = now_ms - (E["refresh_hours"] - 1) * H
    if now_ms - ev.get("fomc_t", 0) >= E["fomc_refresh_hours"] * H:
        try:
            r = get(FOMC_URL, headers=UA, timeout=15)
            r.raise_for_status()
            fomc = parse_fomc(r.text)
            if len(fomc) >= 6:  # a page layout change that parses to nothing must not wipe the list
                ev["fomc"] = fomc
            else:
                log.error("events: FOMC page parsed to %d meetings, keeping the previous list", len(fomc))
            ev["fomc_t"] = now_ms
        except Exception as e:  # noqa: BLE001
            log.error("events: FOMC calendar fetch failed: %s", e)
            ev["fomc_t"] = now_ms - (E["fomc_refresh_hours"] - 1) * H
    keep = now_ms - E["keep_days"] * 86_400_000
    ev["items"] = sorted((i for i in items.values() if i["t"] >= keep), key=lambda i: i["t"])[-300:]
    state.set("events", ev)
    return ev


def _fomc_covered(t, items):
    return any(abs(i["t"] - t) <= 2 * H and ("FOMC" in i["title"] or "Federal Funds" in i["title"]) for i in items)


def groups(ev, lo_ms, hi_ms):
    """Events in [lo, hi], releases at the same minute merged into one ("CPI m/m, Core CPI m/m"),
    with FOMC decisions from the Fed's calendar added where the weekly feed doesn't have them."""
    ev = ev or {}
    items = [i for i in ev.get("items") or [] if lo_ms <= i["t"] <= hi_ms]
    for t in ev.get("fomc") or []:
        if lo_ms <= t <= hi_ms and not _fomc_covered(t, items):
            items.append({"id": f"fomc:{t}", "title": "FOMC rate decision", "t": t, "country": "USD",
                          "impact": "High", "forecast": "", "previous": ""})
    by = {}
    for i in sorted(items, key=lambda i: (i["t"], i["title"])):
        by.setdefault((i["country"], i["t"]), []).append(i)
    return [{"key": f"event_{c}_{t}", "t": t, "country": c, "titles": [i["title"] for i in g],
             "items": g} for (c, t), g in sorted(by.items(), key=lambda kv: kv[0][1])]


def next_fomc(ev, now_ms):
    return next((t for t in (ev or {}).get("fomc") or [] if t > now_ms), None)


def until(t, now_ms):
    m = max(0, (t - now_ms) // 60_000)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def label(g):
    names = g["titles"]
    return (", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")) or "event"


def alerts(notif, ev, now_ms, E, held=()):
    """One heads-up per release time within warn_hours, pushed once (the key carries the time).
    Mentions open positions, because that's what an event can hurt: stops can gap."""
    for g in groups(ev, now_ms, now_ms + E["warn_hours"] * H):
        at = dt.datetime.fromtimestamp(g["t"] / 1000, dt.timezone.utc).strftime("%a %H:%M UTC")
        fc = [f"{i['title']}: forecast {i['forecast']}, previous {i['previous']}"
              for i in g["items"] if i["forecast"] or i["previous"]]
        pos = (f"You hold {len(held)} position{'s' if len(held) != 1 else ''} ({', '.join(sorted(held))}): "
               "check every stop is placed - a release can gap price through a stop that isn't on the book."
               if held else "No open positions.")
        fomc = any("FOMC" in n or "Federal Funds" in n for n in g["titles"])
        # measured, not folklore: BTCUSDT 1h, the 27 decisions 2023-06 -> 2026-09 (README "Events")
        why = ("BTC's high-low range in the hour after an FOMC decision has run 2.6x a normal hour (27 decisions since 2023)"
               if fomc else "high-impact scheduled release: volatility usually jumps at the print")
        text = (f"EVENT {g['country']} {label(g)} at {at} (in {until(g['t'], now_ms)})\n"
                f"  {why}\n"
                + "".join(f"  {x}\n" for x in fc)
                + f"  {pos}\n"
                f"  context, not a rule: skipping entries before FOMC tested as no effect (they almost never coincide)")
        notif.send(text, key=g["key"], cooldown_s=int(E["warn_hours"] * 3600), push=True, cat="heads_up",
                   summary=f"{label(g)} in {until(g['t'], now_ms)}", valid_until=g["t"], event_t=g["t"])


def note_for_entry(ev, now_ms, hours=24):
    """' | US CPI m/m in 9h' for a breakout alert, or ''."""
    g = groups(ev, now_ms, now_ms + hours * H)
    if not g:
        return ""
    first = g[0]
    return f"high-impact {first['country']} {label(first)} in {until(first['t'], now_ms)} - gap risk"


def payload(ev, now_ms, days=7):
    """What the dashboard shows: the next week's releases and the next FOMC decision."""
    if not ev:
        return None
    return {"upcoming": [{"t": g["t"], "country": g["country"], "titles": g["titles"],
                          "forecast": [i["forecast"] for i in g["items"]],
                          "previous": [i["previous"] for i in g["items"]]}
                         for g in groups(ev, now_ms - 2 * H, now_ms + days * 86_400_000)],
            "next_fomc": next_fomc(ev, now_ms), "fetched": ev.get("ff_ok")}
