// Wallet report: the analysis, in the browser. A line-for-line port of positions() in
// scripts/wallet_behavior_study.py (tests/e2e checks the two agree), so a trader's own report uses
// exactly the method behind the study's numbers (docs/research/wallet-behavior-study.md).
// Pure functions over Hyperliquid's public API payloads: no network here, nothing stored.
(function (root) {
  "use strict";
  const PERP_DIRS = new Set(["Open Long", "Close Long", "Open Short", "Close Short", "Long > Short", "Short > Long"]);
  const AD = 0.005;        // an add >= 0.5% worse than the average entry counts as averaging down
  const EPS = 1e-9;
  const DAYS = 90;

  // The study's benchmarks: 145 random active Hyperliquid wallets, 20,138 closed positions, 90 days
  // to 2026-09-25 (exploratory, not pre-registered). Medians are per wallet.
  const BENCH = {
    wallets: 145, positions: 20138,
    ad_rate: 0.14, ad_loss_share: 0.45,           // averaged-down share of positions -> of losses
    stop_rate: 0.25, nostop_loss_share: 0.92,     // positions with a stop; unstopped share of losses
    liquidated_wallets: 0.48, liq_nostop: 0.96,   // wallets liquidated in 90d; liquidations without a stop
    worst5_loss_share: 0.48,                      // the worst 5% of positions' share of losses
    profitable: 0.40, median_pnl: -943,
  };

  function newPos(coin, f, after, px, pnl) {
    return { coin, side: after > 0 ? 1 : -1, start: f.time, size: Math.abs(after), cost: Math.abs(after) * px,
             pnl, adAdds: 0, adds: 0, liq: false };
  }

  // Closed perp positions (flat -> flat): averaging down, liquidation, stop coverage, P&L after fees.
  function positions(addr, fills, orders) {
    addr = (addr || "").toLowerCase();
    const fs = (fills || []).filter(f => PERP_DIRS.has(f.dir) || ("liquidation" in f))
      .slice().sort((a, b) => a.time - b.time || (a.tid || 0) - (b.tid || 0));
    const open = {}, done = [];
    for (const f of fs) {
      const coin = f.coin, px = +f.px, sz = +f.sz, before = +f.startPosition;
      const after = before + (f.side === "B" ? sz : -sz);
      const fee = (+f.fee || 0) + (+(f.builderFee || 0) || 0);
      const liq = !!(f.liquidation && (f.liquidation.liquidatedUser || "").toLowerCase() === addr);
      const p = open[coin];
      if (Math.abs(before) < EPS) { open[coin] = newPos(coin, f, after, px, -fee); continue; }
      if (!p) continue;                                   // opened before the window: skip until flat
      p.pnl += (+f.closedPnl || 0) - fee;
      p.liq = p.liq || liq;
      const same = (after > 0) === (before > 0) && Math.abs(after) > EPS;
      if (same && Math.abs(after) > Math.abs(before)) {   // adding
        const avg = p.cost / p.size;
        const worse = p.side > 0 ? px < avg * (1 - AD) : px > avg * (1 + AD);
        p.adds += 1; p.adAdds += worse ? 1 : 0; p.cost += sz * px; p.size += sz;
      } else if (same) {                                  // partial close: average entry unchanged
        p.cost *= Math.abs(after) / p.size; p.size = Math.abs(after);
      } else {                                            // flat, or flipped through zero
        p.end = f.time; done.push(p); delete open[coin];
        if (Math.abs(after) > EPS) open[coin] = newPos(coin, f, after, px, 0);
      }
    }
    // Stops: reduce-only stop orders in the order history, which HL caps at the latest 2,000 orders.
    const os = orders || [];
    const coveredFrom = os.length >= 2000 ? Math.min(...os.map(o => o.order.timestamp)) : 0;
    const stops = os.filter(o => (o.order.orderType || "").startsWith("Stop") && o.order.reduceOnly)
      .map(o => [o.order.coin, o.order.timestamp]);
    for (const p of done) {
      p.hold_h = (p.end - p.start) / 3.6e6;
      p.ad = p.adAdds > 0;
      p.stop = p.start >= coveredFrom ? stops.some(([c, t]) => c === p.coin && p.start - 60000 <= t && t <= p.end) : null;
    }
    return done;
  }

  const loss = ps => -ps.reduce((s, p) => s + (p.pnl < 0 ? p.pnl : 0), 0);

  // One wallet's numbers, defined exactly as the study's per-wallet rows.
  function summarize(ps) {
    const n = ps.length, L = loss(ps);
    const known = ps.filter(p => p.stop !== null);
    const ad = ps.filter(p => p.ad), liq = ps.filter(p => p.liq);
    const worst = ps.slice().sort((a, b) => a.pnl - b.pnl);
    const w5 = worst.slice(0, Math.max(1, Math.floor(n / 20)));
    const share = (part, whole) => whole ? part / whole : null;
    return {
      n, pnl: ps.reduce((s, p) => s + p.pnl, 0), losses: L,
      win_rate: share(ps.filter(p => p.pnl > 0).length, n),
      ad_n: ad.length, ad_rate: share(ad.length, n), ad_loss_share: share(loss(ad), L),
      known_n: known.length, stop_rate: share(known.filter(p => p.stop).length, known.length),
      nostop_loss_share: share(loss(known.filter(p => !p.stop)), loss(known)),
      liq_n: liq.length, liq_loss_share: share(loss(liq), L),
      liq_nostop: share(liq.filter(p => p.stop === false).length, liq.filter(p => p.stop !== null).length),
      worst5_n: w5.length, worst5_loss_share: share(loss(w5), L),
      short_share: share(ps.filter(p => p.hold_h < 24).length, n),
      worst: worst.slice(0, 5).filter(p => p.pnl < 0),
    };
  }

  root.WalletReport = { positions, summarize, BENCH, DAYS, PERP_DIRS };
})(typeof window !== "undefined" ? window : globalThis);
