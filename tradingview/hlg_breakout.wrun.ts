// HL Guardrails · Breakout: the live rule, simplified for wrun (OpenMarket).
// Same maths as hlg/scanner.py and tradingview/hlg_breakout.pine:
//   trend   EMA20 > EMA50 of the close
//   signal  a CLOSED bar closes above the highest high of the 20 bars before it
//   entry   the next bar's open, skipped if it opens more than 1 ATR above the signal close
//   stop    signal close - 2 x ATR14 (Wilder), then 3 x ATR below the highest high, only ever raised
//   time    out after 21 days
// Decided on closed bars only: the forming bar draws the lines but never marks a signal, so nothing
// repaints. Use it on the 1D and 4H charts. Size, the 3-position cap and the loss locks stay in the app.

param.int("fast", 20, { min: 1, max: 200, description: "Trend EMA fast" });
param.int("slow", 50, { min: 2, max: 400, description: "Trend EMA slow" });
param.int("lookback", 20, { min: 2, max: 200, description: "Breakout: close above the high of the previous N bars" });
param.int("atr_len", 14, { min: 1, max: 100, description: "ATR length (Wilder)" });
param.number("stop_atr", 2.0, { min: 0.5, max: 5, step: 0.1, description: "Initial stop: ATR below the signal close" });
param.number("trail_atr", 3.0, { min: 0.5, max: 8, step: 0.1, description: "Trailing stop: ATR below the highest high" });
param.number("chase_atr", 1.0, { min: 0, max: 5, step: 0.1, description: "Don't chase: skip if the entry opens more than N ATR above the signal close" });
param.int("max_days", 21, { min: 1, max: 120, description: "Time stop (days)" });

output("ema_fast", line, overlay, { color: "#2563eb", width: 1, description: "EMA20" });
output("ema_slow", line, overlay, { color: "#7c3aed", width: 1, description: "EMA50" });
output("level", line, overlay, { color: "#0ea5e9", width: 2, step: true, description: "Breakout level: the high of the previous 20 bars" });
output("stop", line, overlay, { color: "#dc2626", width: 2, step: true, description: "Stop, then the 3-ATR trail, while in a trade" });
output("breakout", shape, overlay, { shape: "triangle_up", location: "below_bar", color: "#16a34a", shape_where: "is_breakout", description: "Breakout on a closed bar: enter at the next open" });
output("exit_mark", shape, overlay, { shape: "triangle_down", location: "above_bar", color: "#dc2626", shape_where: "is_exit", description: "Stop, trail or time stop hit" });
const isBreakout = output("is_breakout", none);
const isExit = output("is_exit", none);
output("plan_stop", none);   // the stop for the signalled entry (signal close - 2 ATR), read by the alert text

alert("breakout", { when: isBreakout, title: "HLG breakout", description: "A bar closed above the 20-bar high in an uptrend",
  message: "{{symbol}}: breakout closed at {{close}} above the 20-bar high. Enter at the next open, stop {{plan_stop}}. No adds." });
alert("exit", { when: isExit, title: "HLG exit", description: "The stop, trail or time stop was hit",
  message: "{{symbol}}: stop / trail / time exit near {{close}}." });

let emaF = new Ema(20);
let emaS = new Ema(50);
let atr = new Atr(14);
let hi = new Highest(20);
let prevHi: f64 = NaN;     // the lookback high as of the previous bar
let st: i32 = 0;           // 0 flat · 1 signal closed, entry at the next open · 2 in a trade
let sigClose: f64 = NaN;
let sigAtr: f64 = NaN;
let planStop: f64 = NaN;
let stopV: f64 = NaN;
let best: f64 = NaN;
let entryT: f64 = NaN;

function onStart(): void {
  emaF = new Ema(i32(p_fast()));
  emaS = new Ema(i32(p_slow()));
  atr = new Atr(i32(p_atr_len()));
  hi = new Highest(i32(p_lookback()));
}

function onBar(): void {
  const o = bar.open(), h = bar.high(), l = bar.low(), c = bar.close();
  const f = emaF.update(c);
  const s = emaS.update(c);
  const a = atr.update(h, l, c);
  const level = prevHi;    // the lookback high BEFORE this bar
  prevHi = hi.update(h);
  let breakout = 0.0;
  let exited = 0.0;

  if (!bar.isLast()) {     // closed bars only: the forming bar decides nothing
    // 1) the bar after a signal: fill at its open, or skip a runaway open
    if (st == 1) {
      if (o > sigClose + p_chase_atr() * sigAtr) {
        st = 0;
      } else {
        stopV = sigClose - p_stop_atr() * sigAtr;
        best = o;
        entryT = bar.time();
        st = 2;
      }
    }
    // 2) the open trade: stop first, then the time stop, then raise the trail
    if (st == 2) {
      if (l <= stopV) {
        exited = 1.0;
        st = 0;
      } else if (bar.time() - entryT >= p_max_days() * 86400.0) {
        exited = 1.0;
        st = 0;
      } else {
        best = Math.max(best, h);
        stopV = Math.max(stopV, best - p_trail_atr() * a);
      }
    }
    // 3) a new signal (one trade at a time: none while one is open or pending)
    if (st == 0 && !isNaN(level) && !isNaN(s) && !isNaN(a) && f > s && c > level) {
      breakout = 1.0;
      sigClose = c;
      sigAtr = a;
      planStop = c - p_stop_atr() * a;
      st = 1;
    }
  }

  if (isNaN(s)) return;    // warming up
  out_ema_fast(f);
  out_ema_slow(s);
  out_level(level);
  out_stop(st == 2 ? stopV : NaN);
  out_breakout(l);
  out_is_breakout(breakout);
  out_exit_mark(h);
  out_is_exit(exited);
  out_plan_stop(planStop);
}
