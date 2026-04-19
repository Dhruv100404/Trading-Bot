#!/usr/bin/env bun
// ============================================================================
// BUCKET WINDOW ANALYSIS: What's the optimal collection window?
// Uses EXACT signal engine from analyze-honest.js (which got 100% win rate)
// Tests: collect signals up to bucket N, then select top K, simulate exits
// ============================================================================

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()

const cfg = JSON.parse(readFileSync("backtest-config.json", "utf-8"))
const C = {
  buy_entry_start: cfg.buy_entry_start ?? 2, buy_entry_end: cfg.buy_entry_end ?? 3,
  sell_entry_start: cfg.sell_entry_start ?? 2, sell_entry_end: cfg.sell_entry_end ?? 4,
  hard_exit_bucket: cfg.hard_exit_bucket ?? 35, sell_hard_exit_bucket: cfg.sell_hard_exit_bucket ?? 71,
  buy_min_move_pct: cfg.buy_min_move_pct ?? 0.45, sell_min_move_pct: cfg.sell_min_move_pct ?? 0.25,
  buy_min_volume: cfg.buy_min_volume ?? 300, sell_min_volume: cfg.sell_min_volume ?? 450,
  buy_min_score: cfg.buy_min_score ?? 4, sell_min_score: cfg.sell_min_score ?? 4,
  buy_tp_pct: cfg.buy_tp_pct ?? 0, buy_sl_pct: cfg.buy_sl_pct ?? 1.2,
  sell_tp_pct: cfg.sell_tp_pct ?? 0, sell_sl_pct: cfg.sell_sl_pct ?? 1.8,
  quantity: cfg.quantity ?? 1, min_move_pct: cfg.min_move_pct ?? 0.15,
  buy_gap_min_pct: cfg.buy_gap_min_pct ?? 0, buy_gap_max_pct: cfg.buy_gap_max_pct ?? 100,
  sell_gap_min_pct: cfg.sell_gap_min_pct ?? -100, sell_gap_max_pct: cfg.sell_gap_max_pct ?? 10,
  buy_min_vol_rate: cfg.buy_min_vol_rate ?? 0, sell_min_vol_rate: cfg.sell_min_vol_rate ?? 0,
  direction_filter: cfg.direction_filter ?? "BOTH",
  buy_qty_multiplier: cfg.buy_qty_multiplier ?? 1, sell_qty_multiplier: cfg.sell_qty_multiplier ?? 1,
}

const CAPITAL = 50000
const PER_TRADE = 25000
const MAX_POS = Math.floor(CAPITAL * 5 / PER_TRADE) // 10
const TEST_BUCKETS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
const TP_LEVELS = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]

// ── EXACT signal engine from analyze-honest.js ──
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

// Replay a symbol-day but STOP at a given collection bucket (don't simulate exit yet)
function replayForEntry(buckets, dayOpen, gap, sym, maxBucket) {
  if (buckets.length < 3 || dayOpen <= 0) return null
  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const uniqueBkts = [...new Set(sorted.map(b => b.b))].sort((a, b) => a - b)

  for (const bkt of uniqueBkts) {
    if (bkt > maxBucket) break
    const upTo = sorted.filter(b => b.b <= bkt)
    const wS = Math.min(C.buy_entry_start, C.sell_entry_start)
    const wE = Math.min(maxBucket, Math.max(C.buy_entry_end, C.sell_entry_end))
    if (bkt < wS || bkt > wE) continue

    const eSnaps = upTo.filter(b => b.b >= wS && b.b <= bkt)
    if (!eSnaps.length) continue
    const last = eSnaps[eSnaps.length - 1]
    const mp = (last.c - dayOpen) / dayOpen * 100
    const dir = mp > 0 ? "BUY" : "SELL"
    const ds = dir === "BUY" ? 1 : -1

    const dES = dir === "BUY" ? C.buy_entry_start : C.sell_entry_start
    const dEE = dir === "BUY" ? Math.min(C.buy_entry_end, maxBucket) : Math.min(C.sell_entry_end, maxBucket)
    if (bkt < dES || bkt > dEE) continue

    const dSnaps = upTo.filter(b => b.b >= dES && b.b <= bkt)
    if (!dSnaps.length) continue

    const dMM = dir === "BUY" ? C.buy_min_move_pct : C.sell_min_move_pct
    if (Math.abs(mp) < dMM) continue

    if (dir === "BUY" && gap !== 0 && gap < C.buy_gap_min_pct) continue
    if (dir === "SELL" && gap !== 0 && gap > C.sell_gap_max_pct) continue
    if (dir === "SELL" && gap !== 0 && gap < C.sell_gap_min_pct) continue
    if (dir === "BUY" && gap !== 0 && gap > C.buy_gap_max_pct) continue

    const dMVR = dir === "BUY" ? C.buy_min_vol_rate : C.sell_min_vol_rate
    if (dMVR > 0 && last.vr < dMVR) continue

    // Scoring (with VWAP/gap/body)
    let score = 0
    if (Math.abs(mp) >= dMM) score += 2
    if (Math.abs(mp) >= dMM * 2) score += 2
    const dMV = dir === "BUY" ? C.buy_min_volume : C.sell_min_volume
    const volE = dSnaps.reduce((s, b) => s + b.v, 0)
    if (volE >= dMV) score += 1
    if (volE >= dMV * 2) score += 2
    if (dSnaps.some(b => dir === "BUY" ? b.c > b.vw && b.vw > 0 : b.c < b.vw && b.vw > 0)) score += 1
    if (Math.abs(gap) > 0.3 && (gap * ds) > 0) score += 1
    if (last.br > 0.6) score += 1

    const dMS = dir === "BUY" ? C.buy_min_score : C.sell_min_score
    if (score < dMS) continue
    if (C.direction_filter !== "BOTH" && dir !== C.direction_filter) continue

    // Dynamic qty
    const morning = sorted.filter(b => b.b >= 1 && b.b <= bkt).map(b => b.c)
    const mr = morning.length > 0 ? (() => {
      const mx = Math.max(...morning), mn = Math.min(...morning), av = morning.reduce((a, b) => a + b, 0) / morning.length
      return av > 0 ? (mx - mn) / av * 100 : 0
    })() : 0
    const dirMult = dir === "BUY" ? C.buy_qty_multiplier : C.sell_qty_multiplier
    const qty = dynamicQty(C.quantity, dirMult, last.c, last.vc, last.vr, mr, mp)
    if (qty === 0) continue

    const ep = last.c
    const dSL = dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
    return { sym, dir, ep, eb: bkt, score, qty, mp, vr: last.vr, gap, sl: ep * (1 - ds * dSL / 100) }
  }
  return null
}

// Simulate exit from entry point using remaining buckets
function simulateExit(sig, allBuckets, tpPct) {
  const ds = sig.dir === "BUY" ? 1 : -1
  const tpPrice = tpPct > 0 ? sig.ep * (1 + ds * tpPct / 100) : 0
  const exitBkt = sig.dir === "SELL" ? C.sell_hard_exit_bucket : C.hard_exit_bucket
  const sorted = [...allBuckets].sort((a, b) => a.b - b.b)
  let mfe = 0, mae = 0

  for (const b of sorted) {
    if (b.b <= sig.eb) continue
    const fav = ds > 0 ? (b.h - sig.ep) / sig.ep * 100 : (sig.ep - b.l) / sig.ep * 100
    const adv = ds > 0 ? (sig.ep - b.l) / sig.ep * 100 : (b.h - sig.ep) / sig.ep * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv

    const tpHit = tpPct > 0.001 && (ds > 0 ? b.c >= tpPrice : b.c <= tpPrice)
    const slHit = Math.abs(sig.sl - sig.ep) > 0.001 && (ds > 0 ? b.c <= sig.sl : b.c >= sig.sl)
    const timeHit = b.b >= exitBkt
    if (tpHit) return { xr: "TP", ret: tpPct, xb: b.b, mfe, mae }
    if (slHit) { const r = ds > 0 ? (b.c-sig.ep)/sig.ep*100 : (sig.ep-b.c)/sig.ep*100; return { xr: "SL", ret: r, xb: b.b, mfe, mae } }
    if (timeHit) { const r = ds > 0 ? (b.c-sig.ep)/sig.ep*100 : (sig.ep-b.c)/sig.ep*100; return { xr: "TIME", ret: r, xb: b.b, mfe, mae } }
  }
  const last = sorted[sorted.length - 1]
  const r = ds > 0 ? (last.c-sig.ep)/sig.ep*100 : (sig.ep-last.c)/sig.ep*100
  return { xr: "TIME", ret: r, xb: last.b, mfe, mae }
}

// ── Stream and collect all stock-day data ──
console.log(`\n${"█".repeat(70)}`)
console.log(`  BUCKET WINDOW DEEP ANALYSIS`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(70)}\n`)
console.log(`Streaming...`)

// Store per-day, per-symbol: { buckets, dayOpen, gap }
const dayStocks = {} // date -> [ { sym, dayOpen, gap, buckets } ]
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 3) continue
  if (!dayStocks[date]) dayStocks[date] = []
  dayStocks[date].push({ sym: symbol, dayOpen, gap: gapPct, buckets })
  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayStocks).sort()
const nd = allDates.length
const totalStockDays = Object.values(dayStocks).reduce((s, v) => s + v.length, 0)
console.log(`${lc} lines → ${totalStockDays} stock-days across ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ============================================================================
// For each collection bucket endpoint, replay all signals and simulate
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: COLLECTION WINDOW vs PERFORMANCE`)
console.log(`  For each "collect until bucket N": generate signals, select top ${MAX_POS}/day, simulate exits`)
console.log(`  Capital: Rs ${CAPITAL} | Per trade: Rs ${PER_TRADE} | Max positions: ${MAX_POS}`)
console.log(`${"=".repeat(90)}\n`)

const allResults = {} // key: `B${bkt}_TP${tp}` -> stats

for (const collectEnd of TEST_BUCKETS) {
  // Generate all signals for this collection window
  const signalsByDate = {} // date -> [ signal + buckets ref ]
  let totalSigs = 0

  for (const date of allDates) {
    const stocks = dayStocks[date]
    const daySigs = []
    for (const sd of stocks) {
      const sig = replayForEntry(sd.buckets, sd.dayOpen, sd.gap, sd.sym, collectEnd)
      if (sig) {
        daySigs.push({ ...sig, _buckets: sd.buckets })
        totalSigs++
      }
    }
    signalsByDate[date] = daySigs
  }

  const avgSigs = totalSigs / nd

  for (const tp of TP_LEVELS) {
    let wins = 0, trades = 0, pnlSum = 0, mfeSum = 0, maeSum = 0, tpHits = 0, slHits = 0
    let exitBktSum = 0
    const dailyPnls = []
    let losersWithMfe03 = 0

    for (const date of allDates) {
      const daySigs = signalsByDate[date]
      // Cherry-pick: sort by score DESC, price ASC, take top MAX_POS
      daySigs.sort((a, b) => b.score - a.score || a.ep - b.ep)
      const selected = daySigs.slice(0, MAX_POS)

      let dayPnl = 0
      for (const sig of selected) {
        const qty = Math.max(Math.floor(PER_TRADE / sig.ep), 1)
        const exit = simulateExit(sig, sig._buckets, tp)
        const pnl = sig.ep * (exit.ret / 100) * qty
        dayPnl += pnl
        pnlSum += pnl
        trades++
        if (exit.ret > 0) wins++
        if (exit.xr === "TP") tpHits++
        if (exit.xr === "SL") slHits++
        mfeSum += exit.mfe
        maeSum += exit.mae
        exitBktSum += exit.xb
        if (exit.ret <= 0 && exit.mfe >= 0.3) losersWithMfe03++
      }
      dailyPnls.push(dayPnl)
    }

    const wr = trades > 0 ? wins / trades * 100 : 0
    const avgDaily = pnlSum / nd
    const roc = avgDaily / CAPITAL * 100
    const posDays = dailyPnls.filter(p => p > 0).length
    const avgMfe = trades > 0 ? mfeSum / trades : 0
    const avgMae = trades > 0 ? maeSum / trades : 0
    const ratio = avgMae > 0 ? avgMfe / avgMae : 0
    const tpRate = trades > 0 ? tpHits / trades * 100 : 0
    const avgExBkt = trades > 0 ? exitBktSum / trades : 0
    const avgFill = trades / nd

    // Max consecutive red
    let maxRed = 0, streak = 0
    for (const p of dailyPnls) { if (p <= 0) { streak++; if (streak > maxRed) maxRed = streak } else streak = 0 }

    allResults[`B${collectEnd}_TP${tp}`] = {
      collectEnd, tp, avgSigs, avgFill, wr, avgDaily, roc, posDays, avgMfe, avgMae, ratio,
      tpRate, avgExBkt, trades, tpHits, slHits, maxRed, losersWithMfe03, dailyPnls,
    }
  }
}

// Print main table
console.log(`  ${"Coll".padEnd(5)} ${"TP%".padEnd(4)} ${"Sigs/d".padStart(7)} ${"Fill".padStart(5)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE%".padStart(7)} ${"MAE%".padStart(7)} ${"Ratio".padStart(6)} ${"TPHit".padStart(6)} ${"ExBkt".padStart(6)} ${"MaxRd".padStart(6)}`)
console.log(`  ${"-".repeat(95)}`)

for (const collectEnd of TEST_BUCKETS) {
  for (const tp of TP_LEVELS) {
    const r = allResults[`B${collectEnd}_TP${tp}`]
    const best = TP_LEVELS.every(t => {
      const other = allResults[`B${collectEnd}_TP${t}`]
      return !other || r.roc >= other.roc
    })
    const marker = best ? " <<<" : ""
    console.log(`  B${String(collectEnd).padEnd(3)} ${tp.toFixed(1).padEnd(4)} ${r.avgSigs.toFixed(0).padStart(7)} ${r.avgFill.toFixed(0).padStart(5)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${("Rs "+r.avgDaily.toFixed(0)).padStart(8)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.posDays+"/"+nd).padStart(6)} ${(r.avgMfe.toFixed(2)+"%").padStart(7)} ${(r.avgMae.toFixed(2)+"%").padStart(7)} ${r.ratio.toFixed(2).padStart(6)} ${(r.tpRate.toFixed(0)+"%").padStart(6)} ${r.avgExBkt.toFixed(0).padStart(6)} ${(r.maxRed+"d").padStart(6)}${marker}`)
  }
  console.log()
}

// ============================================================================
// PART 2: BEST CONFIG PER BUCKET
// ============================================================================
console.log(`${"=".repeat(90)}`)
console.log(`  PART 2: BEST TP% PER COLLECTION BUCKET`)
console.log(`${"=".repeat(90)}\n`)

for (const collectEnd of TEST_BUCKETS) {
  let bestTP = 0, bestROC = -999
  for (const tp of TP_LEVELS) {
    const r = allResults[`B${collectEnd}_TP${tp}`]
    if (r.roc > bestROC) { bestROC = r.roc; bestTP = tp }
  }
  const r = allResults[`B${collectEnd}_TP${bestTP}`]
  console.log(`  Bucket ${String(collectEnd).padEnd(2)} → Best TP=${bestTP}% | ${r.wr.toFixed(1)}% win | Rs ${r.avgDaily.toFixed(0)}/day | ${r.roc.toFixed(2)}% ROC | ${r.posDays}/${nd} pos | MFE/MAE=${r.ratio.toFixed(2)} | ${r.avgSigs.toFixed(0)} candidates/day`)
}

// ============================================================================
// PART 3: BEST BUCKET PER TP
// ============================================================================
console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 3: BEST COLLECTION BUCKET PER TP LEVEL`)
console.log(`${"=".repeat(90)}\n`)

for (const tp of TP_LEVELS) {
  let bestBkt = 2, bestROC = -999
  for (const bkt of TEST_BUCKETS) {
    const r = allResults[`B${bkt}_TP${tp}`]
    if (r.roc > bestROC) { bestROC = r.roc; bestBkt = bkt }
  }
  const r = allResults[`B${bestBkt}_TP${tp}`]
  console.log(`  TP=${tp.toFixed(1)}% → Best at Bucket ${bestBkt} | ${r.wr.toFixed(1)}% win | Rs ${r.avgDaily.toFixed(0)}/day | ${r.roc.toFixed(2)}% ROC | ${r.posDays}/${nd} pos | ${r.avgSigs.toFixed(0)} cands/day → ${r.avgFill.toFixed(0)} selected`)
}

// ============================================================================
// PART 4: LOSERS THAT HAD PROFIT FIRST (by bucket)
// ============================================================================
console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 4: LOSERS THAT HAD 0.3%+ MFE (missed exit opportunity)`)
console.log(`${"=".repeat(90)}\n`)

console.log(`  ${"Bucket".padEnd(8)} ${"TP=0.5%".padStart(10)} ${"TP=0.7%".padStart(10)} ${"TP=1.0%".padStart(10)} ${"TP=1.5%".padStart(10)}`)
console.log(`  ${"-".repeat(52)}`)
for (const bkt of TEST_BUCKETS) {
  const vals = [0.5, 0.7, 1.0, 1.5].map(tp => {
    const r = allResults[`B${bkt}_TP${tp}`]
    const losses = r.trades - Math.round(r.wr * r.trades / 100)
    return losses > 0 ? `${r.losersWithMfe03}/${losses}` : "0/0"
  })
  console.log(`  B${String(bkt).padEnd(6)} ${vals.map(v => v.padStart(10)).join("")}`)
}

// ============================================================================
// PART 5: DAILY CONSISTENCY DEEP DIVE
// ============================================================================
console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 5: DAILY CONSISTENCY (TP=0.7%)`)
console.log(`${"=".repeat(90)}\n`)

console.log(`  ${"Bucket".padEnd(8)} ${"Green".padStart(6)} ${"Red".padStart(5)} ${"G%".padStart(5)} ${"AvgGrn".padStart(8)} ${"AvgRed".padStart(8)} ${"MaxGrn".padStart(8)} ${"MaxRed".padStart(8)} ${"Streak".padStart(7)} ${"Sharpe".padStart(7)}`)
console.log(`  ${"-".repeat(75)}`)

for (const bkt of TEST_BUCKETS) {
  const r = allResults[`B${bkt}_TP0.7`]
  const dp = r.dailyPnls
  const green = dp.filter(p => p > 0), red = dp.filter(p => p <= 0)
  const avgG = green.length ? green.reduce((s,p)=>s+p,0)/green.length : 0
  const avgR = red.length ? red.reduce((s,p)=>s+p,0)/red.length : 0
  const maxG = green.length ? Math.max(...green) : 0
  const maxR = red.length ? Math.min(...red) : 0
  const mean = dp.reduce((s,p)=>s+p,0)/dp.length
  const std = Math.sqrt(dp.reduce((s,p)=>s+(p-mean)**2,0)/dp.length)
  const sharpe = std > 0 ? mean / std : 0

  console.log(`  B${String(bkt).padEnd(6)} ${green.length.toString().padStart(6)} ${red.length.toString().padStart(5)} ${(green.length/nd*100).toFixed(0).padStart(4)}% ${("Rs "+avgG.toFixed(0)).padStart(8)} ${("Rs "+avgR.toFixed(0)).padStart(8)} ${("Rs "+maxG.toFixed(0)).padStart(8)} ${("Rs "+maxR.toFixed(0)).padStart(8)} ${(r.maxRed+"d").padStart(7)} ${sharpe.toFixed(2).padStart(7)}`)
}

// ============================================================================
// PART 6: OVERALL RECOMMENDATION
// ============================================================================
console.log(`\n${"=".repeat(90)}`)
console.log(`  RECOMMENDATION`)
console.log(`${"=".repeat(90)}\n`)

// Find absolute best combo
let bestKey = "", bestROC = -999
for (const k of Object.keys(allResults)) {
  if (allResults[k].roc > bestROC) { bestROC = allResults[k].roc; bestKey = k }
}
const best = allResults[bestKey]
if (best) {
  console.log(`  ABSOLUTE BEST: Collect until Bucket ${best.collectEnd}, TP=${best.tp}%`)
  console.log(`  ${best.wr.toFixed(1)}% win rate | Rs ${best.avgDaily.toFixed(0)}/day | ${best.roc.toFixed(2)}% daily ROC`)
  console.log(`  ${best.posDays}/${nd} positive days | MFE/MAE: ${best.ratio.toFixed(2)} | Max red streak: ${best.maxRed}d`)
  console.log(`  ${best.avgSigs.toFixed(0)} candidates/day → ${best.avgFill.toFixed(0)} selected | TP hit rate: ${best.tpRate.toFixed(0)}%`)
  console.log(`  Monthly estimate: Rs ${(best.avgDaily * 22).toFixed(0)} (~${(best.roc * 22).toFixed(1)}% monthly ROC)`)
}

console.log(`\nDone in ${((Date.now() - t0) / 1000).toFixed(1)}s`)
