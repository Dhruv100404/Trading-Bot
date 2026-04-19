#!/usr/bin/env bun
// Deep pattern analysis — streaming, processes line by line, never holds all data
// Usage: bun deploy/analyze.js [data/candles-consolidated.ndjson] [config.json]

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const CONFIG_FILE = process.argv[3] || "backtest-config.json"

// Load config
let config
try { config = JSON.parse(readFileSync(CONFIG_FILE, "utf-8")) } catch {
  config = { buy_entry_start: 2, buy_entry_end: 3, sell_entry_start: 2, sell_entry_end: 4,
    buy_min_move_pct: 0.45, sell_min_move_pct: 0.25, buy_min_volume: 300, sell_min_volume: 450,
    buy_min_score: 4, sell_min_score: 4, buy_sl_pct: 1.2, sell_sl_pct: 1.8,
    hard_exit_bucket: 35, sell_hard_exit_bucket: 71, buy_qty_multiplier: 2, sell_qty_multiplier: 2,
    gap_filter_min_pct: -100, gap_filter_max_pct: 100, buy_gap_min_pct: 0, buy_gap_max_pct: 100,
    sell_gap_min_pct: -100, sell_gap_max_pct: 10, quantity: 1, min_move_pct: 0.15 }
}
console.log("Config loaded")

// F&O
let fnoSet = new Set()
try {
  for (const line of readFileSync("data/candles/scrip-master.csv", "utf-8").split("\n")) {
    const c = line.split(",")
    if (c[0]?.trim() === "NSE" && c[3]?.trim() === "FUTSTK") {
      const s = c[5]?.trim().split("-")[0]; if (s) fnoSet.add(s)
    }
  }
} catch {}
console.log(`F&O: ${fnoSet.size} symbols`)

// Signal engine (matching Rust)
function computeSignal(buckets, dayOpen, gapPct, cfg) {
  if (buckets.length < 2) return null
  if (gapPct !== 0 && (gapPct < cfg.gap_filter_min_pct || gapPct > cfg.gap_filter_max_pct)) return null
  const openPrice = buckets.find(b => b.b >= 1)?.c || dayOpen
  if (openPrice <= 0) return null
  const wS = Math.min(cfg.buy_entry_start, cfg.sell_entry_start)
  const wE = Math.max(cfg.buy_entry_end, cfg.sell_entry_end)
  const wide = buckets.filter(b => b.b >= wS && b.b <= wE)
  if (!wide.length) return null
  const wL = wide[wide.length - 1]
  const movePct = (wL.c - openPrice) / openPrice * 100
  const dir = movePct > 0 ? "BUY" : "SELL"
  const ds = dir === "BUY" ? 1 : -1
  const [eS, eE] = dir === "BUY" ? [cfg.buy_entry_start, cfg.buy_entry_end] : [cfg.sell_entry_start, cfg.sell_entry_end]
  const es = buckets.filter(b => b.b >= eS && b.b <= eE)
  if (!es.length) return null
  const last = es[es.length - 1]
  const dm = (last.c - openPrice) / openPrice * 100
  const mm = dir === "BUY" ? cfg.buy_min_move_pct : cfg.sell_min_move_pct
  if (Math.abs(dm) < mm) return null
  if (dir === "SELL" && gapPct !== 0 && gapPct < cfg.sell_gap_min_pct) return null
  if (dir === "BUY" && gapPct !== 0 && gapPct > cfg.buy_gap_max_pct) return null
  if (dir === "BUY" && gapPct !== 0 && gapPct < cfg.buy_gap_min_pct) return null
  if (dir === "SELL" && gapPct !== 0 && gapPct > cfg.sell_gap_max_pct) return null
  const mvr = dir === "BUY" ? (cfg.buy_min_vol_rate || 0) : (cfg.sell_min_vol_rate || 0)
  if (last.vr < mvr) return null
  const volE = es.reduce((s, b) => s + b.v, 0)
  let sc = 0
  if (Math.abs(movePct) >= (cfg.min_move_pct || 0.15)) sc += 2
  if (Math.abs(movePct) >= (cfg.min_move_pct || 0.15) * 2) sc += 2
  const dv = dir === "BUY" ? cfg.buy_min_volume : cfg.sell_min_volume
  if (volE >= dv) sc += 1
  if (volE >= dv * 2) sc += 2
  if (es.some(b => dir === "BUY" ? b.c > b.vw : b.c < b.vw)) sc += 1
  if (Math.abs(gapPct) > 0.3 && gapPct * ds > 0) sc += 1
  if (last.br > 0.6) sc += 1
  const ms = dir === "BUY" ? cfg.buy_min_score : cfg.sell_min_score
  if (sc < ms) return null
  const ep = last.c
  const tp = dir === "BUY" ? (cfg.buy_tp_pct || 0) : (cfg.sell_tp_pct || 0)
  const sl = dir === "BUY" ? (cfg.buy_sl_pct || 0) : (cfg.sell_sl_pct || 0)
  return { dir, sc, ep, eb: last.b, tp: ep * (1 + ds * tp / 100), sl: ep * (1 - ds * sl / 100), op: openPrice, mp: dm, vr: last.vr, vw: last.vw, br: last.br }
}

// --- Streaming process ---
console.log(`\nStreaming ${DATA_FILE}...`)
const start = Date.now()

// Accumulators (keep trades small — only store what's needed for analysis)
const trades = []   // will be ~3-10K items, fine for memory
const oppStats = { total: 0, m05: 0, m1: 0, m15: 0, m2: 0 }
const stockMoveCount = {} // symbol -> {days, moves}
let lineCount = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lineCount++
  const { symbol, date, dayOpen, gapPct, buckets, f5Range, f5Vol, maxUp45, maxDown45, maxUp20, maxDown20, maxUp60, maxDown60 } = sd
  const isFno = fnoSet.has(symbol)
  const maxMove45 = Math.max(maxUp45, maxDown45)

  // Opportunity stats
  oppStats.total++
  if (maxMove45 >= 0.5) oppStats.m05++
  if (maxMove45 >= 1.0) oppStats.m1++
  if (maxMove45 >= 1.5) oppStats.m15++
  if (maxMove45 >= 2.0) oppStats.m2++

  // Stock move tracking
  if (!stockMoveCount[symbol]) stockMoveCount[symbol] = { days: 0, moves: 0, totalMax: 0, isFno }
  stockMoveCount[symbol].days++
  if (maxMove45 >= 1) stockMoveCount[symbol].moves++
  stockMoveCount[symbol].totalMax += maxMove45

  // Signal
  const sig = computeSignal(buckets, dayOpen, gapPct, config)
  if (!sig) continue

  const ds = sig.dir === "BUY" ? 1 : -1
  let mxF = 0, mxA = 0, favB = sig.eb, exitP = null, exitB = null, exitR = null
  const exitLim = sig.dir === "BUY" ? config.hard_exit_bucket : config.sell_hard_exit_bucket

  for (const b of buckets) {
    if (b.b <= sig.eb) continue
    const fav = ds > 0 ? (b.h - sig.ep) / sig.ep * 100 : (sig.ep - b.l) / sig.ep * 100
    const adv = ds > 0 ? (sig.ep - b.l) / sig.ep * 100 : (b.h - sig.ep) / sig.ep * 100
    if (fav > mxF) { mxF = fav; favB = b.b }
    if (adv > mxA) mxA = adv
    if (!exitP) {
      const tpA = Math.abs(sig.tp - sig.ep) > 0.001
      if (tpA && ((ds > 0 && b.c >= sig.tp) || (ds < 0 && b.c <= sig.tp))) { exitP = b.c; exitB = b.b; exitR = "TP" }
      const slA = Math.abs(sig.sl - sig.ep) > 0.001
      if (!exitP && slA && ((ds > 0 && b.c <= sig.sl) || (ds < 0 && b.c >= sig.sl))) { exitP = b.c; exitB = b.b; exitR = "SL" }
      if (!exitP && b.b >= exitLim) { exitP = b.c; exitB = b.b; exitR = "TIME" }
    }
  }
  if (!exitP) { const l = buckets[buckets.length - 1]; exitP = l.c; exitB = l.b; exitR = "TIME" }

  const ret = ds > 0 ? (exitP - sig.ep) / sig.ep * 100 : (sig.ep - exitP) / sig.ep * 100
  const qty = config.quantity || 1

  trades.push({
    sym: symbol, dt: date, dir: sig.dir, eb: sig.eb, ep: sig.ep,
    xb: exitB, xp: exitP, xr: exitR, ret, pnl: sig.ep * (ret / 100) * qty,
    qty, sc: sig.sc, fno: isFno, gap: gapPct, mp: sig.mp, vr: sig.vr,
    mr: 0, vd: sig.ep > 0 ? (sig.ep - sig.vw) / sig.vw * 100 : 0, br: sig.br,
    f5r: f5Range, mxF, mxA, favB, dow: new Date(date).getDay(),
    px: sig.ep < 500 ? 0 : sig.ep < 1000 ? 1 : sig.ep < 2000 ? 2 : 3,
  })

  if (lineCount % 20000 === 0) process.stderr.write(`  ${lineCount} lines...\r`)
}

const loadTime = ((Date.now() - start) / 1000).toFixed(1)
console.log(`Processed ${lineCount} stock-days → ${trades.length} trades in ${loadTime}s`)

// ==========================================================================
// ANALYSIS
// ==========================================================================

const days = [...new Set(trades.map(t => t.dt))]
const nd = days.length || 1
const N = trades.length
const pxLabel = ["<500", "500-1K", "1K-2K", ">2K"]

function grp(arr) {
  const n = arr.length; if (!n) return { n: 0, w: 0, a: 0, p: 0 }
  const w = arr.filter(t => t.ret > 0).length
  return { n, w: w / n * 100, a: arr.reduce((s, t) => s + t.ret, 0) / n, p: arr.reduce((s, t) => s + t.pnl, 0) }
}

function tbl(name, keyFn, order) {
  const b = {}; for (const t of trades) { const k = keyFn(t); (b[k] = b[k] || []).push(t) }
  let e = Object.entries(b).map(([k, v]) => [k, grp(v)])
  if (order) e.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
  else e.sort((a, b) => b[1].a - a[1].a)
  console.log(`\n  --- ${name} ---`)
  console.log(`  ${"Bucket".padEnd(24)} ${"N".padStart(6)} ${"Win%".padStart(7)} ${"Avg%".padStart(8)} ${"PnL".padStart(9)}`)
  for (const [k, s] of e) {
    if (s.n < 5) continue
    const m = s.w >= 57 && s.a > 0.15 ? " <<<" : s.w < 42 ? " !!!" : ""
    console.log(`  ${k.padEnd(24)} ${String(s.n).padStart(6)} ${(s.w.toFixed(1)+"%").padStart(7)} ${((s.a>=0?"+":"")+s.a.toFixed(3)+"%").padStart(8)} ${s.p.toFixed(0).padStart(9)}${m}`)
  }
}

// --- SUMMARY ---
const s = grp(trades)
const buy = grp(trades.filter(t => t.dir === "BUY"))
const sell = grp(trades.filter(t => t.dir === "SELL"))
console.log(`\n${"=".repeat(74)}`)
console.log(`SUMMARY: ${N} trades over ${nd} days | ${s.w.toFixed(1)}% win | ${s.a>=0?"+":""}${s.a.toFixed(2)}% avg | Rs ${s.p.toFixed(0)}`)
console.log(`  BUY:  ${buy.n} (${buy.w.toFixed(1)}% win, ${buy.a>=0?"+":""}${buy.a.toFixed(2)}%) | SELL: ${sell.n} (${sell.w.toFixed(1)}% win, ${sell.a>=0?"+":""}${sell.a.toFixed(2)}%)`)
console.log(`  TP=${trades.filter(t=>t.xr==="TP").length} SL=${trades.filter(t=>t.xr==="SL").length} TIME=${trades.filter(t=>t.xr==="TIME").length}`)

// --- OPPORTUNITY ---
console.log(`\n${"=".repeat(74)}`)
console.log(`OPPORTUNITY: ${oppStats.total} stock-days`)
console.log(`  0.5%+ in 45m: ${oppStats.m05} (${(oppStats.m05/oppStats.total*100).toFixed(1)}%)`)
console.log(`  1.0%+ in 45m: ${oppStats.m1} (${(oppStats.m1/oppStats.total*100).toFixed(1)}%)`)
console.log(`  1.5%+ in 45m: ${oppStats.m15} (${(oppStats.m15/oppStats.total*100).toFixed(1)}%)`)
console.log(`  2.0%+ in 45m: ${oppStats.m2} (${(oppStats.m2/oppStats.total*100).toFixed(1)}%)`)

// Top movers
const movers = Object.entries(stockMoveCount).filter(([,v]) => v.days >= 5)
  .map(([sym, v]) => ({ sym, ...v, freq: v.moves / v.days * 100, avgMax: v.totalMax / v.days }))
  .sort((a, b) => b.freq - a.freq)
console.log(`\n  TOP 20 CONSISTENT MOVERS:`)
console.log(`  ${"Symbol".padEnd(20)} ${"Days".padStart(5)} ${"1%+".padStart(5)} ${"Freq%".padStart(7)} ${"AvgMax%".padStart(8)}`)
for (const m of movers.slice(0, 20))
  console.log(`  ${m.sym.padEnd(20)} ${String(m.days).padStart(5)} ${String(m.moves).padStart(5)} ${(m.freq.toFixed(1)+"%").padStart(7)} ${(m.avgMax.toFixed(2)+"%").padStart(8)}`)

// --- PATTERNS ---
console.log(`\n${"=".repeat(74)}`)
tbl("DIRECTION", t => t.dir, ["BUY", "SELL"])
tbl("SCORE", t => `sc=${t.sc}`)
tbl("GAP", t => { const g=t.gap; return g<-2?"<-2%":g<-1?"-2to-1":g<0?"-1to0":g<1?"0to+1":g<2?"+1to+2":">+2%" })
tbl("VOL RATE", t => { const v=t.vr; return v<10?"<10":v<50?"10-50":v<200?"50-200":v<500?"200-500":">500" })
tbl("PRICE", t => pxLabel[t.px])
tbl("DAY", t => ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][t.dow])
tbl("5MIN RANGE", t => { const r=t.f5r; return r<0.5?"<0.5%":r<1?"0.5-1%":r<2?"1-2%":r<3?"2-3%":">3%" })
tbl("F&O", t => t.fno ? "F&O" : "Non-F&O")
tbl("DIR+GAP", t => `${t.dir} ${t.gap>0.5?"gapUp":t.gap<-0.5?"gapDn":"flat"}`)
tbl("DIR+VOLRATE", t => `${t.dir} ${t.vr<50?"loVol":t.vr<200?"midVol":"hiVol"}`)

// --- LOSS AUTOPSY ---
console.log(`\n${"=".repeat(74)}`)
const L = trades.filter(t => t.ret < 0), W = trades.filter(t => t.ret > 0)
const av = (a, f) => a.length ? a.reduce((s, t) => s + f(t), 0) / a.length : 0
console.log(`LOSS AUTOPSY: ${L.length} losers vs ${W.length} winners`)
console.log(`  ${"".padEnd(30)} ${"Winners".padStart(10)} ${"Losers".padStart(10)}`)
console.log(`  ${"Avg max favorable%".padEnd(30)} ${(av(W,t=>t.mxF).toFixed(2)+"%").padStart(10)} ${(av(L,t=>t.mxF).toFixed(2)+"%").padStart(10)}`)
console.log(`  ${"Avg max adverse%".padEnd(30)} ${(av(W,t=>t.mxA).toFixed(2)+"%").padStart(10)} ${(av(L,t=>t.mxA).toFixed(2)+"%").padStart(10)}`)
console.log(`  ${"Avg vol_rate".padEnd(30)} ${av(W,t=>t.vr).toFixed(1).padStart(10)} ${av(L,t=>t.vr).toFixed(1).padStart(10)}`)
const neverMoved = L.filter(t => t.mxF < 0.05).length
const goodReversed = L.filter(t => t.mxF >= 0.3).length
console.log(`  Never moved: ${neverMoved} (${(neverMoved/L.length*100).toFixed(1)}%) | Good then reversed: ${goodReversed} (${(goodReversed/L.length*100).toFixed(1)}%)`)

// --- TP SWEEP ---
console.log(`\n${"=".repeat(74)}`)
console.log(`TP OPTIMIZATION:`)
console.log(`  ${"TP%".padEnd(6)} ${"Win%".padStart(7)} ${"Avg%".padStart(8)} ${"PnL".padStart(10)} ${"Rs/day".padStart(9)} ${"Saved".padStart(8)}`)
for (const tp of [0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]) {
  let w = 0, tr = 0, tp_pnl = 0, saved = 0
  for (const t of trades) {
    const r = t.mxF >= tp ? tp : t.ret
    if (r > 0) w++
    if (t.ret < 0 && t.mxF >= tp) saved++
    tr += r; tp_pnl += t.ep * (r / 100) * t.qty
  }
  console.log(`  ${tp.toFixed(1).padEnd(6)} ${(w/N*100).toFixed(1).padStart(6)}% ${((tr/N>=0?"+":"")+(tr/N).toFixed(3)+"%").padStart(8)} ${tp_pnl.toFixed(0).padStart(10)} ${(tp_pnl/nd).toFixed(0).padStart(9)} ${(saved+"/"+L.length).padStart(8)}`)
}

// --- 5x MARGIN ---
console.log(`\n${"=".repeat(74)}`)
console.log(`5x MARGIN (Rs 1L capital = Rs 5L buying power):`)
const cap = 100000
for (const tp of [0.5, 1.0, 1.5, 2.0]) {
  for (const pt of [10000, 25000, 50000]) {
    const mx = Math.floor(cap * 5 / pt)
    const dp = []
    for (const d of days) {
      const dt = trades.filter(t => t.dt === d).sort((a, b) => b.sc - a.sc).slice(0, mx)
      let p = 0
      for (const t of dt) { const q = Math.floor(pt / t.ep) || 1; p += t.ep * ((t.mxF >= tp ? tp : t.ret) / 100) * q }
      dp.push(p)
    }
    const tot = dp.reduce((s, p) => s + p, 0), avg = tot / nd, pos = dp.filter(p => p > 0).length, roc = avg / cap * 100
    if (roc > 0.3 || pt === 25000)
      console.log(`  TP=${tp}% Rs${(pt/1000).toFixed(0)}K×${mx}: Rs ${avg.toFixed(0)}/day | ${(pos/nd*100).toFixed(0)}% pos | ROC ${roc.toFixed(2)}%/day (${(roc*22).toFixed(1)}%/mo)`)
  }
}

// --- GOLDEN FILTER ---
console.log(`\n${"=".repeat(74)}`)
console.log(`GOLDEN FILTER SEARCH (TP=1%, Rs 25K/trade, 5x on Rs 1L):`)
const G = []
for (const d of ["ANY","SELL","BUY"]) {
  for (const ms of [4,7,9]) {
    for (const vf of ["any","vr10-500","vr>50"]) {
      for (const gf of ["any","gap<0","gap-2to1"]) {
        for (const pf of ["any","px<500","px<1K"]) {
          const f = trades.filter(t => {
            if (d !== "ANY" && t.dir !== d) return false
            if (t.sc < ms) return false
            if (vf === "vr10-500" && (t.vr < 10 || t.vr >= 500)) return false
            if (vf === "vr>50" && t.vr < 50) return false
            if (gf === "gap<0" && t.gap >= 0) return false
            if (gf === "gap-2to1" && (t.gap < -2 || t.gap >= 1)) return false
            if (pf === "px<500" && t.px !== 0) return false
            if (pf === "px<1K" && t.px > 1) return false
            return true
          })
          const n = f.length; if (n < 30) continue
          let w = 0, tr = 0, mfe = 0, mae = 0
          for (const t of f) { const r = t.mxF >= 1.0 ? 1.0 : t.ret; if (r > 0) w++; tr += r; mfe += t.mxF; mae += t.mxA }
          const wr = w / n * 100, avg = tr / n, mm = mae > 0 ? mfe / mae : 0
          const conc = Math.min(n / nd, 20), dp = avg / 100 * 25000 * conc, roc = dp / cap * 100
          if (wr >= 58 && avg > 0.15) G.push({ l: `${d} sc>=${ms} ${vf} ${gf} ${pf}`, n, wr, avg, dp, roc, mm })
        }
      }
    }
  }
}
G.sort((a, b) => b.roc - a.roc)
console.log(`\n  ${"Filter".padEnd(42)} ${"N".padStart(5)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Rs/d".padStart(7)} ${"ROC%".padStart(6)} ${"MFE/MAE".padStart(7)}`)
console.log(`  ${"-".repeat(78)}`)
for (const g of G.slice(0, 25))
  console.log(`  ${g.l.padEnd(42)} ${String(g.n).padStart(5)} ${(g.wr.toFixed(1)+"%").padStart(6)} ${("+"+g.avg.toFixed(2)+"%").padStart(7)} ${g.dp.toFixed(0).padStart(7)} ${(g.roc.toFixed(2)+"%").padStart(6)} ${g.mm.toFixed(2).padStart(7)}`)

if (G.length) {
  const b = G[0]
  console.log(`\n  ${"=".repeat(55)}`)
  console.log(`  BEST: ${b.l}`)
  console.log(`  ${b.n} trades (${(b.n/nd).toFixed(1)}/day) | ${b.wr.toFixed(1)}% win | +${b.avg.toFixed(2)}%`)
  console.log(`  Rs ${b.dp.toFixed(0)}/day | ${b.roc.toFixed(2)}%/day | ~${(b.roc*22).toFixed(1)}%/month`)
}

console.log(`\nDone in ${((Date.now() - start) / 1000).toFixed(1)}s`)
