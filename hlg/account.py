"""What the account is actually worth, and against what base to size risk.

Hyperliquid has two account models, and they need different bases:

  classic ("disabled" / "default" from `userAbstraction`)
      Perps are margined by the perp account itself, so `marginSummary.accountValue` IS the
      equity. Spot is a separate wallet.

  unified ("unifiedAccount")
      Spot balances collateralise perps. The perp `accountValue` is then only the slice of USDC
      the perps are currently drawing -- an order of magnitude below the real collateral on a
      typical book -- and it moves as collateral is allocated internally, with no ledger transfer
      to show for it. Sizing off it understates capacity by that same factor and makes every
      exposure check fire.

For a unified account the base is the spot USDC balance. That is not a judgement call: HL's own
"Unified Account Ratio" and "Unified Account Leverage" are exactly

    ratio    = perp maintenance margin / USDC
    leverage = perp notional / USDC

which were checked against the exchange's own account summary to its displayed precision, so this
module matches what HL shows you.

Spot tokens other than USDC are deliberately NOT part of the sizing base. They are exposure, not
spare capacity: counting a HYPE bag as room to open more HYPE risk double-counts it. They are
instead folded into per-coin exposure (`spot_units`), valued at the liquid perp mid rather than
at their own spot marks, since illiquid spot pairs carry absurd marks (valuing every token off its
own book once put a small memecoin balance in the tens of millions).
"""
from .common import fnum, log

UNIFIED = "unifiedAccount"


def spot_coin(token_name):
    """Map a spot token to the perp coin it tracks, or None. Native listings share the name
    (HYPE -> HYPE); Unit-bridged majors carry a U prefix (UBTC -> BTC, UETH -> ETH, USOL -> SOL).
    Anything else (memecoins, wrapped oddities) is not treated as exposure to a perp coin."""
    if token_name in {"UBTC", "UETH", "USOL"}:
        return token_name[1:]
    return token_name


def summarise(st, mode=None, spot=None, portfolio_value=None):
    """Pure: raw API payloads -> the account model. No network, so it tests against fixtures."""
    ms = st.get("marginSummary") or {}
    perp_av = fnum(ms.get("accountValue", 0))
    maint = fnum(st.get("crossMaintenanceMarginUsed", 0) or 0)
    # HL's own total when present (it is what their leverage figure uses); otherwise the sum of
    # position values, which equals it on live data but survives a sparser payload.
    notional = (fnum(ms["totalNtlPos"]) if ms.get("totalNtlPos") is not None
                else sum(abs(fnum(p["position"]["positionValue"])) for p in st.get("assetPositions") or []))
    unified = mode == UNIFIED

    usdc, units = None, {}
    for b in (spot or {}).get("balances") or []:
        if b.get("token") == 0:
            usdc = fnum(b.get("total", 0))
        elif fnum(b.get("total", 0)) > 0:
            units[spot_coin(b.get("coin", ""))] = fnum(b["total"])

    # Unified with no USDC to speak of would make every ratio divide by ~0; fall back to the perp
    # value there rather than print a 10,000x leverage.
    on_usdc = bool(unified and usdc and usdc > 0)
    base = usdc if on_usdc else perp_av
    return {
        "mode": mode or "unknown",
        "unified": unified,
        "base": base,
        "base_label": "USDC collateral" if on_usdc else "perp equity",
        "perp_account_value": perp_av,
        "usdc": usdc,
        "maint_margin": maint,
        "notional": notional,
        "ratio_pct": maint / base * 100 if base else None,
        "leverage": notional / base if base else None,
        "buffer_usd": base - maint,
        "portfolio_value": portfolio_value,
        "spot_units": units if unified else {},
    }


def load(inf, acct, port=None):
    """Fetch and summarise. `port` is the already-fetched `portfolio` payload when the caller has
    one (guardrails does), to avoid a second request.

    Every extra call degrades rather than raises. If `userAbstraction` fails the account is
    treated as classic -- which on a unified account means sizing off the small perp value, i.e.
    too conservative, never too aggressive. That is the right direction to fail for a guardrail."""
    st = inf.user_state(acct)
    mode = spot = None
    try:
        mode = inf.post("/info", {"type": "userAbstraction", "user": acct})
    except Exception as e:  # noqa: BLE001
        log.warning("userAbstraction unavailable (%s); assuming a classic account", e)
    if mode == UNIFIED:
        try:
            spot = inf.post("/info", {"type": "spotClearinghouseState", "user": acct})
        except Exception as e:  # noqa: BLE001
            log.warning("spot state unavailable (%s); sizing off perp equity this run", e)
    pv = None
    try:
        port = dict(port if port is not None else inf.post("/info", {"type": "portfolio", "user": acct}))
        # "day" is the whole-account series (spot included); "perpDay" is perps only
        pv = fnum(port["day" if mode == UNIFIED else "perpDay"]["accountValueHistory"][-1][1])
    except Exception as e:  # noqa: BLE001
        log.info("portfolio value unavailable: %s", e)
    return summarise(st, mode, spot, pv), st


def coin_exposure(model, positions, mids):
    """Net USD exposure per coin with an open perp position: signed perp notional plus any spot
    holding of the same coin, valued at the perp mid. A perp short against a spot bag nets down
    (a hedge); a perp long on top of one stacks up (one concentrated bet)."""
    out = {}
    for p in positions:
        coin = p["coin"]
        sz = fnum(p["szi"])
        mark = fnum(mids.get(coin, 0)) or fnum(p.get("entryPx", 0))
        perp = sz * mark
        spot_usd = model["spot_units"].get(coin, 0.0) * mark
        out[coin] = {"perp_usd": perp, "spot_usd": spot_usd, "net_usd": perp + spot_usd}
    return out
