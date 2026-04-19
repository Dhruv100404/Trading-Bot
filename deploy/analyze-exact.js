#!/usr/bin/env bun
// ============================================================================
// EXACT REPLICA of UI Backtest.tsx logic — matching signal engine, qty, ROC calc
// Then deep analysis on current 205 F&O vs expanding to 2000+ NSE
// ============================================================================

import { readFileSync, createReadStream, writeFileSync } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()

// Load EXACT config from backtest-config.json (same as live)
const cfg = JSON.parse(readFileSync("backtest-config.json", "utf-8"))
// Fill defaults matching configFromApi() in Backtest.tsx
const C = {
  buy_entry_start: cfg.buy_entry_start ?? 2, buy_entry_end: cfg.buy_entry_end ?? 3,
  sell_entry_start: cfg.sell_entry_start ?? 2, sell_entry_end: cfg.sell_entry_end ?? 4,
  hard_exit_bucket: cfg.hard_exit_bucket ?? 35, sell_hard_exit_bucket: cfg.sell_hard_exit_bucket ?? 71,
  min_move_pct: cfg.min_move_pct ?? 0.15,
  buy_min_move_pct: cfg.buy_min_move_pct ?? 0.45, sell_min_move_pct: cfg.sell_min_move_pct ?? 0.25,
  buy_min_volume: cfg.buy_min_volume ?? 300, sell_min_volume: cfg.sell_min_volume ?? 450,
  buy_min_score: cfg.buy_min_score ?? 4, sell_min_score: cfg.sell_min_score ?? 4,
  buy_tp_pct: cfg.buy_tp_pct ?? 0, buy_sl_pct: cfg.buy_sl_pct ?? 1.2,
  sell_tp_pct: cfg.sell_tp_pct ?? 0, sell_sl_pct: cfg.sell_sl_pct ?? 1.8,
  quantity: cfg.quantity ?? 1,
  gap_filter_min_pct: cfg.gap_filter_min_pct ?? -100, gap_filter_max_pct: cfg.gap_filter_max_pct ?? 100,
  buy_gap_min_pct: cfg.buy_gap_min_pct ?? 0, buy_gap_max_pct: cfg.buy_gap_max_pct ?? 100,
  sell_gap_min_pct: cfg.sell_gap_min_pct ?? -100, sell_gap_max_pct: cfg.sell_gap_max_pct ?? 10,
  buy_min_vol_rate: cfg.buy_min_vol_rate ?? 0, sell_min_vol_rate: cfg.sell_min_vol_rate ?? 0,
  direction_filter: cfg.direction_filter ?? "BOTH",
  buy_qty_multiplier: cfg.buy_qty_multiplier ?? 1, sell_qty_multiplier: cfg.sell_qty_multiplier ?? 1,
  capital_per_trade: cfg.capital_per_trade ?? 10000,
}

console.log("Config:", JSON.stringify(C, null, 2))

// F&O symbols
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

// ---- EXACT signal engine from Backtest.tsx ----

function confidenceScore(ep, vc, vr, mr, mp) {
  let s = 0
  if (ep < 1000) s++
  if (vc >= 50000 && vc <= 500000) s++
  if (vc >= 200000 && vc <= 500000) s++
  if (vr >= 500) s++
  if (mr >= 0.5) s++
  if (Math.abs(mp) < 1.0) s++
  return s
}

function dynamicQty(baseQty, dirMult, ep, vc, vr, mr, mp) {
  const sc = confidenceScore(ep, vc, vr, mr, mp)
  if (sc <= 2) return 0
  const cm = sc <= 4 ? 1.0 : sc === 5 ? 1.5 : 2.0
  return Math.max(Math.round(baseQty * cm * dirMult), 1)
}

function computeSignal(buckets, dayOpen, gap, currentBucket) {
  if (buckets.length < 3) return null

  const wS = Math.min(C.buy_entry_start, C.sell_entry_start)
  const wE = Math.max(C.buy_entry_end, C.sell_entry_end)
  if (currentBucket < wS || currentBucket > wE) return null

  const upTo = buckets.filter(b => b.b >= wS && b.b <= currentBucket)
  if (!upTo.length) return null
  const last = upTo[upTo.length - 1]
  const mp = (last.c - dayOpen) / dayOpen * 100
  const dir = mp > 0 ? "BUY" : "SELL"
  const ds = dir === "BUY" ? 1 : -1

  const dES = dir === "BUY" ? C.buy_entry_start : C.sell_entry_start
  const dEE = dir === "BUY" ? C.buy_entry_end : C.sell_entry_end
  if (currentBucket < dES || currentBucket > dEE) return null

  const dSnaps = buckets.filter(b => b.b >= dES && b.b <= currentBucket)
  if (!dSnaps.length) return null

  const dMM = dir === "BUY" ? C.buy_min_move_pct : C.sell_min_move_pct
  if (Math.abs(mp) < dMM) return null

  // Gap filters (EXACT match to Backtest.tsx lines 148-155)
  if (dir === "BUY" && C.buy_gap_min_pct > -100 && gap !== 0 && gap < C.buy_gap_min_pct) return null
  if (dir === "SELL" && C.sell_gap_max_pct < 100 && gap !== 0 && gap > C.sell_gap_max_pct) return null
  if (dir === "SELL" && C.sell_gap_min_pct > -100 && gap !== 0 && gap < C.sell_gap_min_pct) return null
  if (dir === "BUY" && C.buy_gap_max_pct < 100 && gap !== 0 && gap > C.buy_gap_max_pct) return null

  const dMVR = dir === "BUY" ? C.buy_min_vol_rate : C.sell_min_vol_rate
  if (dMVR > 0 && last.vr < dMVR) return null

  let score = 0
  const dMV = dir === "BUY" ? C.buy_min_volume : C.sell_min_volume
  if (Math.abs(mp) >= dMM) score += 2
  if (Math.abs(mp) >= dMM * 2) score += 2
  const volE = dSnaps.reduce((s, b) => s + b.v, 0)
  if (volE >= dMV) score += 1
  if (volE >= dMV * 2) score += 2

  // OI-based scores are 0 in JSON data (no OI available)
  // VWAP, gap, body signals removed in UI version (line 190 comment)

  const dMS = dir === "BUY" ? C.buy_min_score : C.sell_min_score
  if (score < dMS) return null

  const ep = last.c
  const dTP = dir === "BUY" ? C.buy_tp_pct : C.sell_tp_pct
  const dSL = dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
  return { dir, score, ep, eb: currentBucket, tp: ep * (1 + ds * dTP / 100), sl: ep * (1 - ds * dSL / 100), mp, vr: last.vr, vc: last.vc }
}

// ---- Replay exactly like replaySymbolDay() ----

function replayDay(buckets, dayOpen, gap, sym, date) {
  if (buckets.length < 3) return null
  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const uniqueBuckets = [...new Set(sorted.map(b => b.b))].sort((a, b) => a - b)

  let active = null, fired = false

  for (const bkt of uniqueBuckets) {
    const upTo = sorted.filter(b => b.b <= bkt)
    const current = sorted.find(b => b.b === bkt)
    if (!current) continue

    if (!fired && bkt >= Math.min(C.buy_entry_start, C.sell_entry_start) && bkt <= Math.max(C.buy_entry_end, C.sell_entry_end)) {
      const sig = computeSignal(upTo, dayOpen, gap, bkt)
      if (sig) {
        if (C.direction_filter !== "BOTH" && sig.dir !== C.direction_filter) continue

        // Dynamic quantity — EXACT match
        const morning = sorted.filter(b => b.b >= 1 && b.b <= bkt).map(b => b.c)
        const mr = morning.length > 0 ? (() => {
          const mx = Math.max(...morning), mn = Math.min(...morning), av = morning.reduce((a, b) => a + b, 0) / morning.length
          return av > 0 ? (mx - mn) / av * 100 : 0
        })() : 0

        const dirMult = sig.dir === "BUY" ? C.buy_qty_multiplier : C.sell_qty_multiplier
        const qty = dynamicQty(C.quantity, dirMult, sig.ep, sig.vc, sig.vr, mr, sig.mp)
        if (qty === 0) continue

        fired = true
        active = { sym, date, dir: sig.dir, ep: sig.ep, eb: bkt, score: sig.score, tp: sig.tp, sl: sig.sl, qty, mp: sig.mp, vr: sig.vr, gap }
      }
    }

    if (active && !active.exitR) {
      const dTP = active.dir === "BUY" ? C.buy_tp_pct : C.sell_tp_pct
      const dSL = active.dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
      const ds = active.dir === "BUY" ? 1 : -1
      const tpOn = dTP > 0.0001
      const slOn = dSL > 0.0001
      const tpHit = tpOn && (active.dir === "BUY" ? current.c >= active.tp : current.c <= active.tp)
      const slHit = slOn && (active.dir === "BUY" ? current.c <= active.sl : current.c >= active.sl)
      const exitBkt = active.dir === "SELL" ? C.sell_hard_exit_bucket : C.hard_exit_bucket
      const timeHit = bkt >= exitBkt
      const reason = tpHit ? "TP" : slHit ? "SL" : timeHit ? "TIME" : null

      if (reason) {
        const ret = active.dir === "BUY" ? (current.c - active.ep) / active.ep * 100 : (active.ep - current.c) / active.ep * 100
        active.xp = current.c; active.xb = bkt; active.exitR = reason
        active.ret = Math.round(ret * 100) / 100
        active.pnl = Math.round(active.ep * (ret / 100) * active.qty * 100) / 100
      }
    }
  }

  // Force close if still open
  if (active && !active.exitR) {
    const last = sorted[sorted.length - 1]
    const ret = active.dir === "BUY" ? (last.c - active.ep) / active.ep * 100 : (active.ep - last.c) / active.ep * 100
    active.xp = last.c; active.xb = last.b; active.exitR = "TIME"
    active.ret = Math.round(ret * 100) / 100
    active.pnl = Math.round(active.ep * (ret / 100) * active.qty * 100) / 100
  }

  // MFE/MAE tracking
  if (active && active.exitR) {
    const ds = active.dir === "BUY" ? 1 : -1
    let mxF = 0, mxA = 0
    for (const b of sorted) {
      if (b.b <= active.eb) continue
      const fav = ds > 0 ? (b.h - active.ep) / active.ep * 100 : (active.ep - b.l) / active.ep * 100
      const adv = ds > 0 ? (active.ep - b.l) / active.ep * 100 : (b.h - active.ep) / active.ep * 100
      if (fav > mxF) mxF = fav
      if (adv > mxA) mxA = adv
    }
    active.mxF = mxF; active.mxA = mxA
  }

  return active
}

// ============================================================================
// Stream and process
// ============================================================================

console.log(`\nStreaming ${DATA_FILE}...`)

const allTrades = []  // all trades (F&O + non-F&O)
let lineCount = 0, totalStockDays = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lineCount++
  totalStockDays++

  const { symbol: sym, date, dayOpen, gapPct: gap, buckets } = sd
  const isFno = fnoSet.has(sym)

  const trade = replayDay(buckets, dayOpen, gap, sym, date)
  if (trade) {
    trade.isFno = isFno
    allTrades.push(trade)
  }

  if (lineCount % 20000 === 0) process.stderr.write(`  ${lineCount} lines...\r`)
}

console.log(`\nProcessed ${totalStockDays} stock-days → ${allTrades.length} trades in ${((Date.now()-t0)/1000).toFixed(1)}s`)

// ============================================================================
// EXACT ROC CALCULATION (matching perfFromSignals in Backtest.tsx)
// ============================================================================

function calcPerf(trades, label) {
  const n = trades.length; if (!n) return
  const wins = trades.filter(t => t.pnl > 0).length
  const losses = trades.filter(t => t.pnl <= 0).length
  const netPnl = trades.reduce((s, t) => s + t.pnl, 0)
  const capital = trades.reduce((s, t) => s + t.ep * t.qty, 0)
  const marginCapital = capital / 5  // 5x margin — EXACT match to line 368
  const roc = capital > 0 ? (netPnl / marginCapital) * 100 : 0  // line 369

  const days = [...new Set(trades.map(t => t.date))]
  const dailyPnl = {}
  for (const t of trades) { dailyPnl[t.date] = (dailyPnl[t.date] || 0) + t.pnl }
  const posDays = Object.values(dailyPnl).filter(p => p > 0).length

  console.log(`\n  ${label}`)
  console.log(`  Trades: ${n} | Wins: ${wins} (${(wins/n*100).toFixed(1)}%) | Losses: ${losses}`)
  console.log(`  Net PnL: Rs ${netPnl.toFixed(0)} | Capital deployed: Rs ${capital.toFixed(0)}`)
  console.log(`  Margin capital (capital/5): Rs ${marginCapital.toFixed(0)}`)
  console.log(`  ROC (PnL/marginCapital): ${roc.toFixed(2)}%`)
  console.log(`  TP=${trades.filter(t=>t.exitR==="TP").length} SL=${trades.filter(t=>t.exitR==="SL").length} TIME=${trades.filter(t=>t.exitR==="TIME").length}`)
  console.log(`  BUY: ${trades.filter(t=>t.dir==="BUY").length} | SELL: ${trades.filter(t=>t.dir==="SELL").length}`)
  console.log(`  Positive days: ${posDays}/${days.length}`)
  console.log(`  Daily PnL: Rs ${(netPnl/days.length).toFixed(0)}/day`)

  // Qty analysis
  const avgQty = trades.reduce((s, t) => s + t.qty, 0) / n
  const avgPrice = trades.reduce((s, t) => s + t.ep, 0) / n
  console.log(`  Avg quantity: ${avgQty.toFixed(1)} | Avg price: Rs ${avgPrice.toFixed(0)} | Avg position: Rs ${(avgQty * avgPrice).toFixed(0)}`)

  return { n, wins, netPnl, capital, marginCapital, roc, posDays, days: days.length }
}

// ============================================================================
// ANALYSIS
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  CURRENT SYSTEM — EXACT MATCH TO UI BACKTEST`)
console.log(`${"=".repeat(74)}`)

const fnoTrades = allTrades.filter(t => t.isFno)
const nonFnoTrades = allTrades.filter(t => !t.isFno)

console.log(`\n--- CURRENT: F&O only (what you run today) ---`)
const fnoPerf = calcPerf(fnoTrades, "F&O STOCKS ONLY (205 stocks)")

console.log(`\n--- EXPANSION: All NSE stocks ---`)
const allPerf = calcPerf(allTrades, "ALL NSE STOCKS (~2400 stocks)")

console.log(`\n--- NON-F&O only (the new stocks) ---`)
const nonFnoPerf = calcPerf(nonFnoTrades, "NON-F&O STOCKS ONLY (~2200 stocks)")

// ============================================================================
// WHAT HAPPENS WHEN YOU ENABLE TP?
// ============================================================================

console.log(`\n\n${"=".repeat(74)}`)
console.log(`  IMPACT OF ENABLING TP (currently buy_tp_pct=0, sell_tp_pct=0)`)
console.log(`${"=".repeat(74)}`)

for (const tpVal of [0.3, 0.5, 0.7, 1.0, 1.5]) {
  for (const [label, subset] of [["F&O", fnoTrades], ["ALL", allTrades]]) {
    let wins = 0, netPnl = 0, capital = 0
    for (const t of subset) {
      const simRet = t.mxF >= tpVal ? tpVal : t.ret
      const simPnl = t.ep * (simRet / 100) * t.qty
      if (simRet > 0) wins++
      netPnl += simPnl
      capital += t.ep * t.qty
    }
    const mc = capital / 5
    const roc = mc > 0 ? (netPnl / mc) * 100 : 0
    const n = subset.length
    console.log(`  TP=${tpVal}% ${label.padEnd(4)} | ${n} trades | ${(wins/n*100).toFixed(1)}% win | Rs ${netPnl.toFixed(0)} PnL | ROC: ${roc.toFixed(2)}% (margin Rs ${mc.toFixed(0)})`)
  }
  console.log()
}

// ============================================================================
// PER-DAY BREAKDOWN — what does a typical day look like?
// ============================================================================

console.log(`${"=".repeat(74)}`)
console.log(`  DAILY BREAKDOWN`)
console.log(`${"=".repeat(74)}`)

const allDates = [...new Set(allTrades.map(t => t.date))].sort()

console.log(`\n  ${"Date".padEnd(12)} ${"FnO_Tr".padStart(7)} ${"FnO_PnL".padStart(9)} ${"All_Tr".padStart(7)} ${"All_PnL".padStart(9)} ${"New_Tr".padStart(7)} ${"New_PnL".padStart(9)} ${"FnO_Cap".padStart(10)} ${"All_Cap".padStart(10)}`)
console.log(`  ${"-".repeat(82)}`)

let fnoTotalCap = 0, allTotalCap = 0

for (const d of allDates) {
  const fD = fnoTrades.filter(t => t.date === d)
  const aD = allTrades.filter(t => t.date === d)
  const nD = nonFnoTrades.filter(t => t.date === d)
  const fPnl = fD.reduce((s, t) => s + t.pnl, 0)
  const aPnl = aD.reduce((s, t) => s + t.pnl, 0)
  const nPnl = nD.reduce((s, t) => s + t.pnl, 0)
  const fCap = fD.reduce((s, t) => s + t.ep * t.qty, 0)
  const aCap = aD.reduce((s, t) => s + t.ep * t.qty, 0)
  fnoTotalCap += fCap; allTotalCap += aCap

  console.log(`  ${d.padEnd(12)} ${String(fD.length).padStart(7)} ${("Rs "+fPnl.toFixed(0)).padStart(9)} ${String(aD.length).padStart(7)} ${("Rs "+aPnl.toFixed(0)).padStart(9)} ${String(nD.length).padStart(7)} ${("Rs "+nPnl.toFixed(0)).padStart(9)} ${("Rs "+fCap.toFixed(0)).padStart(10)} ${("Rs "+aCap.toFixed(0)).padStart(10)}`)
}

// ============================================================================
// THE CRITICAL QUESTION: qty × price analysis
// ============================================================================

console.log(`\n\n${"=".repeat(74)}`)
console.log(`  QTY × PRICE ANALYSIS — Where does PnL actually come from?`)
console.log(`${"=".repeat(74)}`)

// Group by qty value
const qtyDist = {}
for (const t of allTrades) {
  const k = `qty=${t.qty}`
  if (!qtyDist[k]) qtyDist[k] = { n: 0, wins: 0, pnl: 0, capital: 0 }
  qtyDist[k].n++
  if (t.pnl > 0) qtyDist[k].wins++
  qtyDist[k].pnl += t.pnl
  qtyDist[k].capital += t.ep * t.qty
}

console.log(`\n  ${"Qty".padEnd(10)} ${"Trades".padStart(7)} ${"Win%".padStart(7)} ${"PnL".padStart(10)} ${"Capital".padStart(12)} ${"ROC(5x)".padStart(9)} ${"AvgPnl/trade".padStart(13)}`)
for (const [k, d] of Object.entries(qtyDist).sort((a, b) => b[1].capital - a[1].capital)) {
  const roc = d.capital > 0 ? (d.pnl / (d.capital / 5)) * 100 : 0
  console.log(`  ${k.padEnd(10)} ${String(d.n).padStart(7)} ${(d.wins/d.n*100).toFixed(1).padStart(6)}% ${("Rs "+d.pnl.toFixed(0)).padStart(10)} ${("Rs "+d.capital.toFixed(0)).padStart(12)} ${(roc.toFixed(2)+"%").padStart(9)} ${("Rs "+(d.pnl/d.n).toFixed(1)).padStart(13)}`)
}

// Per-trade PnL distribution
console.log(`\n  PER-TRADE PnL DISTRIBUTION:`)
const pnlBands = [["< -10", -1e9, -10], ["-10 to -5", -10, -5], ["-5 to -1", -5, -1], ["-1 to 0", -1, 0],
  ["0 to +1", 0, 1], ["+1 to +5", 1, 5], ["+5 to +10", 5, 10], ["> +10", 10, 1e9]]
for (const [label, lo, hi] of pnlBands) {
  const grp = allTrades.filter(t => t.pnl >= lo && t.pnl < hi)
  if (grp.length > 0) console.log(`  ${label.padEnd(12)} ${String(grp.length).padStart(6)} trades (${(grp.length/allTrades.length*100).toFixed(1)}%)`)
}

// Why PnL is small: qty=1 on most trades
const avgPosition = allTrades.reduce((s, t) => s + t.ep * t.qty, 0) / allTrades.length
console.log(`\n  Average position size: Rs ${avgPosition.toFixed(0)}`)
console.log(`  With qty=1 and avg price Rs ${(allTrades.reduce((s,t)=>s+t.ep,0)/allTrades.length).toFixed(0)}: avg position = Rs ${avgPosition.toFixed(0)}`)
console.log(`  A 0.5% move on Rs ${avgPosition.toFixed(0)} = Rs ${(avgPosition * 0.005).toFixed(2)} PnL per trade`)

// ============================================================================
// WHAT IF: Capital-based quantity instead of qty=1
// ============================================================================

console.log(`\n\n${"=".repeat(74)}`)
console.log(`  WHAT IF: CAPITAL-BASED QUANTITY`)
console.log(`${"=".repeat(74)}`)

for (const capPerTrade of [10000, 25000, 50000]) {
  for (const [label, subset] of [["F&O", fnoTrades], ["ALL", allTrades]]) {
    let netPnl = 0, totalCap = 0, wins = 0
    for (const t of subset) {
      const qty = Math.floor(capPerTrade / t.ep) || 1
      const pnl = t.ep * (t.ret / 100) * qty
      netPnl += pnl
      totalCap += t.ep * qty
      if (pnl > 0) wins++
    }
    const mc = totalCap / 5
    const roc = mc > 0 ? (netPnl / mc) * 100 : 0
    const nd = [...new Set(subset.map(t => t.date))].length
    console.log(`  Rs ${(capPerTrade/1000).toFixed(0)}K/trade ${label.padEnd(4)} | ${subset.length} trades | Win ${(wins/subset.length*100).toFixed(1)}% | PnL Rs ${netPnl.toFixed(0)} | Cap Rs ${totalCap.toFixed(0)} | MarginCap Rs ${mc.toFixed(0)} | ROC ${roc.toFixed(2)}% | Rs ${(netPnl/nd).toFixed(0)}/day`)
  }
  console.log()
}

// ============================================================================
// WHAT IF: Capital-based + TP enabled
// ============================================================================

console.log(`${"=".repeat(74)}`)
console.log(`  WHAT IF: CAPITAL-BASED QTY + TP ENABLED`)
console.log(`${"=".repeat(74)}`)

for (const tp of [0.5, 0.7, 1.0]) {
  for (const capPerTrade of [10000, 25000, 50000]) {
    for (const [label, subset] of [["F&O", fnoTrades], ["ALL", allTrades]]) {
      let netPnl = 0, totalCap = 0, wins = 0
      const dailyPnl = {}
      for (const t of subset) {
        const qty = Math.floor(capPerTrade / t.ep) || 1
        const ret = t.mxF >= tp ? tp : t.ret
        const pnl = t.ep * (ret / 100) * qty
        netPnl += pnl; totalCap += t.ep * qty
        if (pnl > 0) wins++
        dailyPnl[t.date] = (dailyPnl[t.date] || 0) + pnl
      }
      const mc = totalCap / 5
      const roc = mc > 0 ? (netPnl / mc) * 100 : 0
      const nd = Object.keys(dailyPnl).length
      const posDays = Object.values(dailyPnl).filter(p => p > 0).length
      if (capPerTrade === 25000 || (roc > 5 && capPerTrade === 50000)) {
        console.log(`  TP=${tp}% Rs${(capPerTrade/1000).toFixed(0)}K ${label.padEnd(4)} | ${subset.length}tr | ${(wins/subset.length*100).toFixed(1)}%win | Rs${netPnl.toFixed(0)} | ROC ${roc.toFixed(2)}% | Rs${(netPnl/nd).toFixed(0)}/day | ${posDays}/${nd} pos`)
      }
    }
  }
  console.log()
}

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
