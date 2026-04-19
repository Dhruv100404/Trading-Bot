#!/usr/bin/env bun
// ============================================================================
// AFTERNOON MOMENTUM ANALYSIS (2:00 PM - 3:00 PM IST)
//
// The closing hour has unique characteristics:
// - Institutional rebalancing (MF NAV fixing at 3:30)
// - Short covering before close
// - Trend continuation from morning movers
// - Mean reversion of morning overextensions
//
// Buckets: 2PM = bucket 286 (14:00 - 9:15 + 1 = 286), 3PM = bucket 346
// Actually: bucket = (hour*60 + min) - (9*60+15) + 1
// 2:00 PM = 14*60+0 - 555 + 1 = 286
// 2:30 PM = 14*60+30 - 555 + 1 = 316
// 3:00 PM = 15*60+0 - 555 + 1 = 346
// 3:15 PM = 15*60+15 - 555 + 1 = 361
// 3:30 PM = 15*60+30 - 555 + 1 = 376 (close)
// ============================================================================

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()

const CAPITAL = 50000, PER_TRADE = 10000, MAX_POS = 12

// Afternoon time buckets
const PM2 = 286    // 2:00 PM
const PM230 = 316  // 2:30 PM
const PM3 = 346    // 3:00 PM
const PM315 = 361  // 3:15 PM
const PM325 = 371  // 3:25 PM (last entry before close)
const CLOSE = 376  // 3:30 PM

console.log(`\n${"█".repeat(74)}`)
console.log(`  AFTERNOON MOMENTUM ANALYSIS (2:00 PM - 3:30 PM)`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)
console.log("Loading...")

const dayStocks = {} // date -> [ { sym, dayOpen, gap, buckets } ]
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct, buckets } = sd
  if (!dayOpen || dayOpen <= 0) continue
  // Need buckets from morning (for context) and afternoon (for trading)
  if (!buckets.some(b => b.b >= PM2)) continue
  if (!dayStocks[date]) dayStocks[date] = []
  dayStocks[date].push({ sym: symbol, dayOpen, gap: gapPct, buckets })
  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayStocks).sort()
const nd = allDates.length
const totalStockDays = Object.values(dayStocks).reduce((s, v) => s + v.length, 0)
console.log(`${lc} lines → ${totalStockDays} stock-days with afternoon data, ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ============================================================================
// PART 1: RAW AFTERNOON MOVEMENT — How much do stocks move 2PM-3:30PM?
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: RAW AFTERNOON MOVEMENT (2:00 PM - 3:30 PM)`)
console.log(`${"=".repeat(90)}\n`)

let totalMoves = 0
const moveBands = { '0.3': 0, '0.5': 0, '0.7': 0, '1.0': 0, '1.5': 0, '2.0': 0, '3.0': 0 }
const allAfternoon = []

for (const date of allDates) {
  for (const sd of dayStocks[date]) {
    const sorted = sd.buckets.sort((a, b) => a.b - b.b)
    const pm2Snap = sorted.find(b => b.b >= PM2 && b.b <= PM2 + 2)
    if (!pm2Snap) continue

    const pm2Price = pm2Snap.c
    if (pm2Price <= 0) continue

    // Morning context
    const morningSnaps = sorted.filter(b => b.b >= 1 && b.b <= 75) // 9:15 - 10:30
    const morningHigh = morningSnaps.reduce((mx, b) => Math.max(mx, b.h), 0)
    const morningLow = morningSnaps.reduce((mn, b) => Math.min(mn, b.l), 99999)
    const morningMove = sd.dayOpen > 0 ? (morningSnaps[morningSnaps.length-1]?.c - sd.dayOpen) / sd.dayOpen * 100 : 0
    const morningRange = sd.dayOpen > 0 ? (morningHigh - morningLow) / sd.dayOpen * 100 : 0
    const morningVol = morningSnaps.reduce((s, b) => s + b.v, 0)

    // Midday context (11:00 - 2:00)
    const middaySnaps = sorted.filter(b => b.b >= 106 && b.b < PM2)
    const middayMove = middaySnaps.length > 0 && pm2Price > 0 ?
      (pm2Snap.c - (middaySnaps[0]?.c || pm2Price)) / pm2Price * 100 : 0

    // Afternoon data (2:00 PM onwards)
    const pmSnaps = sorted.filter(b => b.b >= PM2)
    if (pmSnaps.length < 5) continue

    let maxUp = 0, maxDown = 0
    for (const b of pmSnaps) {
      const up = (b.h - pm2Price) / pm2Price * 100
      const down = (pm2Price - b.l) / pm2Price * 100
      if (up > maxUp) maxUp = up
      if (down > maxDown) maxDown = down
    }
    const maxMove = Math.max(maxUp, maxDown)
    const netMove = pmSnaps[pmSnaps.length-1] ? (pmSnaps[pmSnaps.length-1].c - pm2Price) / pm2Price * 100 : 0
    const dir = netMove > 0 ? "UP" : "DOWN"
    const absNet = Math.abs(netMove)

    totalMoves++
    for (const [band, _] of Object.entries(moveBands)) {
      if (maxMove >= parseFloat(band)) moveBands[band]++
    }

    // Volume in afternoon
    const pmVol = pmSnaps.reduce((s, b) => s + b.v, 0)
    const pmVolRate = pmSnaps.length > 0 ? pmSnaps.reduce((s, b) => s + (b.vr || 0), 0) / pmSnaps.length : 0

    // VWAP at 2PM
    const vwapAt2 = pm2Snap.vw || 0
    const priceVsVwap = vwapAt2 > 0 ? (pm2Price - vwapAt2) / vwapAt2 * 100 : 0

    // Body ratios in afternoon
    const pmBodyAvg = pmSnaps.reduce((s, b) => s + (b.br || 0), 0) / pmSnaps.length

    // Trend in 2PM-2:15PM (first 15 min)
    const early = pmSnaps.filter(b => b.b >= PM2 && b.b <= PM2 + 15)
    const earlyMove = early.length >= 2 ? (early[early.length-1].c - early[0].c) / early[0].c * 100 : 0

    // Volume acceleration (2PM vol vs morning avg)
    const morningAvgVol = morningSnaps.length > 0 ? morningVol / morningSnaps.length : 1
    const pmAvgVol = pmSnaps.length > 0 ? pmVol / pmSnaps.length : 0
    const volAccelPM = morningAvgVol > 0 ? pmAvgVol / morningAvgVol : 1

    // MFE/MAE for afternoon entries
    // Test different entry times and TPs
    const entryPoints = [
      { name: "2:00PM", bucket: PM2, exitBucket: CLOSE },
      { name: "2:15PM", bucket: PM2 + 15, exitBucket: CLOSE },
      { name: "2:30PM", bucket: PM230, exitBucket: CLOSE },
      { name: "2:45PM", bucket: PM230 + 15, exitBucket: CLOSE },
      { name: "3:00PM", bucket: PM3, exitBucket: CLOSE },
    ]

    const tpResults = {}
    for (const ep of entryPoints) {
      const entrySnap = sorted.find(b => b.b >= ep.bucket && b.b <= ep.bucket + 2)
      if (!entrySnap) continue
      const entryPrice = entrySnap.c
      if (entryPrice <= 0) continue

      // Determine direction from early afternoon trend
      const entryDir = earlyMove > 0.1 ? "BUY" : earlyMove < -0.1 ? "SELL" : null
      if (!entryDir) continue
      const ds = entryDir === "BUY" ? 1 : -1

      let mfe = 0, mae = 0
      for (const b of sorted) {
        if (b.b <= ep.bucket) continue
        if (b.b > ep.exitBucket) break
        const fav = ds > 0 ? (b.h - entryPrice) / entryPrice * 100 : (entryPrice - b.l) / entryPrice * 100
        const adv = ds > 0 ? (entryPrice - b.l) / entryPrice * 100 : (b.h - entryPrice) / entryPrice * 100
        if (fav > mfe) mfe = fav
        if (adv > mae) mae = adv
      }

      // Check TP hits
      for (const tp of [0.3, 0.5, 0.7, 1.0]) {
        const key = `${ep.name}_${tp}`
        if (!tpResults[key]) tpResults[key] = { hits: 0, total: 0, mfeSum: 0, maeSum: 0 }
        tpResults[key].total++
        tpResults[key].mfeSum += mfe
        tpResults[key].maeSum += mae
        if (mfe >= tp) tpResults[key].hits++
      }
    }

    allAfternoon.push({
      sym: sd.sym, date, pm2Price, dir, netMove, absNet, maxUp, maxDown, maxMove,
      morningMove, morningRange, morningVol, middayMove,
      pmVol, pmVolRate, priceVsVwap, pmBodyAvg, earlyMove,
      volAccelPM, gap: sd.gap, dayOpen: sd.dayOpen,
      tpResults,
      // Features for ranking
      f: {
        absEarlyMove: Math.abs(earlyMove),
        earlyDir: earlyMove > 0 ? 1 : -1,
        pmVolRate, volAccelPM,
        priceVsVwap, pmBodyAvg,
        morningMove, morningRange: Math.abs(morningRange),
        absGap: Math.abs(sd.gap),
        middayMove,
        vwapAligned: (earlyMove > 0 && priceVsVwap > 0) || (earlyMove < 0 && priceVsVwap < 0) ? 1 : 0,
        morningTrend: morningMove > 0 ? 1 : -1,
        continueMorning: (morningMove > 0 && earlyMove > 0) || (morningMove < 0 && earlyMove < 0) ? 1 : 0,
        reverseMorning: (morningMove > 0 && earlyMove < 0) || (morningMove < 0 && earlyMove > 0) ? 1 : 0,
        bigMorningMove: Math.abs(morningMove) > 2 ? 1 : 0,
      }
    })
  }
}

console.log(`  ${totalMoves} stocks have afternoon data\n`)
console.log(`  Max move (2PM-3:30PM):`)
for (const [band, count] of Object.entries(moveBands)) {
  console.log(`    >= ${band}%: ${count} (${(count/totalMoves*100).toFixed(1)}%)`)
}

const avgNet = allAfternoon.reduce((s, t) => s + t.absNet, 0) / allAfternoon.length
const avgMaxMove = allAfternoon.reduce((s, t) => s + t.maxMove, 0) / allAfternoon.length
console.log(`\n  Avg |net move|: ${avgNet.toFixed(2)}% | Avg max move: ${avgMaxMove.toFixed(2)}%`)
console.log(`  UP days: ${allAfternoon.filter(t => t.dir === "UP").length} | DOWN: ${allAfternoon.filter(t => t.dir === "DOWN").length}`)

// ============================================================================
// PART 2: ENTRY TIME ANALYSIS — When to enter for best TP hit rate?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 2: OPTIMAL ENTRY TIME (TP hit rate by entry time)`)
console.log(`${"=".repeat(90)}\n`)

// Aggregate tpResults
const entryTimeStats = {}
for (const sd of allAfternoon) {
  for (const [key, val] of Object.entries(sd.tpResults)) {
    if (!entryTimeStats[key]) entryTimeStats[key] = { hits: 0, total: 0, mfeSum: 0, maeSum: 0 }
    entryTimeStats[key].hits += val.hits
    entryTimeStats[key].total += val.total
    entryTimeStats[key].mfeSum += val.mfeSum
    entryTimeStats[key].maeSum += val.maeSum
  }
}

console.log(`  ${"Entry Time".padEnd(12)} ${"TP%".padEnd(5)} ${"Hit Rate".padStart(9)} ${"AvgMFE".padStart(8)} ${"AvgMAE".padStart(8)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(55)}`)
for (const entry of ["2:00PM", "2:15PM", "2:30PM", "2:45PM", "3:00PM"]) {
  for (const tp of [0.3, 0.5, 0.7, 1.0]) {
    const key = `${entry}_${tp}`
    const s = entryTimeStats[key]
    if (!s || s.total === 0) continue
    const hitRate = s.hits / s.total * 100
    const avgMfe = s.mfeSum / s.total
    const avgMae = s.maeSum / s.total
    const ratio = avgMae > 0 ? avgMfe / avgMae : 0
    console.log(`  ${entry.padEnd(12)} ${tp.toFixed(1).padEnd(5)} ${(hitRate.toFixed(1)+"%").padStart(9)} ${(avgMfe.toFixed(2)+"%").padStart(8)} ${(avgMae.toFixed(2)+"%").padStart(8)} ${ratio.toFixed(2).padStart(8)}`)
  }
  console.log()
}

// ============================================================================
// PART 3: WHAT PREDICTS AFTERNOON MOVEMENT?
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 3: WHAT PREDICTS AFTERNOON MOVEMENT? (features of top movers)`)
console.log(`${"=".repeat(90)}\n`)

const sortedByMove = [...allAfternoon].sort((a, b) => b.maxMove - a.maxMove)
const top10pct = sortedByMove.slice(0, Math.floor(allAfternoon.length * 0.1))
const bottom50pct = sortedByMove.slice(Math.floor(allAfternoon.length * 0.5))

console.log(`  Top 10% movers: avg max move = ${(top10pct.reduce((s,t)=>s+t.maxMove,0)/top10pct.length).toFixed(2)}%`)
console.log(`  Bottom 50%: avg max move = ${(bottom50pct.reduce((s,t)=>s+t.maxMove,0)/bottom50pct.length).toFixed(2)}%\n`)

const featureNames = Object.keys(allAfternoon[0].f)
console.log(`  ${"Feature".padEnd(22)} ${"Top 10%".padStart(10)} ${"Bot 50%".padStart(10)} ${"Diff%".padStart(8)} ${"Signal"}`)
console.log(`  ${"-".repeat(60)}`)

for (const feat of featureNames) {
  const topAvg = top10pct.reduce((s, t) => s + t.f[feat], 0) / top10pct.length
  const botAvg = bottom50pct.reduce((s, t) => s + t.f[feat], 0) / bottom50pct.length
  const diff = topAvg - botAvg
  const pctDiff = botAvg !== 0 ? Math.abs(diff / botAvg * 100) : 0
  const sig = pctDiff > 30 ? "★★★" : pctDiff > 15 ? "★★" : pctDiff > 5 ? "★" : ""
  if (pctDiff < 3) continue
  console.log(`  ${feat.padEnd(22)} ${topAvg.toFixed(3).padStart(10)} ${botAvg.toFixed(3).padStart(10)} ${(pctDiff.toFixed(0)+"%").padStart(8)}   ${sig}`)
}

// ============================================================================
// PART 4: AFTERNOON PATTERNS — Named strategies
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 4: AFTERNOON TRADING PATTERNS`)
console.log(`${"=".repeat(90)}\n`)

const patterns = {
  "Morning continuation":  s => s.f.continueMorning && s.f.absEarlyMove >= 0.2,
  "Morning reversal":      s => s.f.reverseMorning && s.f.absEarlyMove >= 0.2,
  "VWAP aligned + moving": s => s.f.vwapAligned && s.f.absEarlyMove >= 0.15,
  "High PM volume":        s => s.f.pmVolRate >= 100,
  "Vol acceleration":      s => s.f.volAccelPM >= 1.5,
  "Big morning → continue":s => s.f.bigMorningMove && s.f.continueMorning,
  "Big morning → reverse": s => s.f.bigMorningMove && s.f.reverseMorning,
  "Early PM trend":        s => s.f.absEarlyMove >= 0.3,
  "Strong PM trend":       s => s.f.absEarlyMove >= 0.5,
  "VWAP + vol + move":     s => s.f.vwapAligned && s.f.pmVolRate >= 50 && s.f.absEarlyMove >= 0.2,
  "Gap + afternoon cont":  s => s.f.absGap > 1 && s.f.continueMorning && s.f.absEarlyMove >= 0.2,
  "Flat morning → PM move":s => Math.abs(s.morningMove) < 0.5 && s.f.absEarlyMove >= 0.3,
}

console.log(`  ${"Pattern".padEnd(24)} ${"Matches/d".padStart(10)} ${"TP03%".padStart(7)} ${"TP05%".padStart(7)} ${"TP07%".padStart(7)} ${"AvgMFE".padStart(8)} ${"AvgMAE".padStart(8)} ${"Ratio".padStart(6)}`)
console.log(`  ${"-".repeat(82)}`)

for (const [name, filterFn] of Object.entries(patterns)) {
  const matching = allAfternoon.filter(filterFn)
  if (matching.length < nd) continue

  const perDay = matching.length / nd
  // Calculate MFE/MAE for 2:15PM entry (gives 15 min to confirm direction)
  let mfeSum = 0, maeSum = 0, tp03 = 0, tp05 = 0, tp07 = 0, count = 0

  for (const sd of matching) {
    const sorted = sd.buckets || []
    const entryBkt = PM2 + 15
    const entrySnap = sorted.find(b => b.b >= entryBkt && b.b <= entryBkt + 2)
    if (!entrySnap) continue
    const ep = entrySnap.c
    if (ep <= 0) continue
    const ds = sd.earlyMove > 0 ? 1 : -1

    let mfe = 0, mae = 0
    for (const b of sorted) {
      if (b.b <= entryBkt) continue
      if (b.b > CLOSE) break
      const fav = ds > 0 ? (b.h - ep) / ep * 100 : (ep - b.l) / ep * 100
      const adv = ds > 0 ? (ep - b.l) / ep * 100 : (b.h - ep) / ep * 100
      if (fav > mfe) mfe = fav
      if (adv > mae) mae = adv
    }
    mfeSum += mfe; maeSum += mae; count++
    if (mfe >= 0.3) tp03++
    if (mfe >= 0.5) tp05++
    if (mfe >= 0.7) tp07++
  }

  if (count === 0) continue
  const avgMfe = mfeSum / count
  const avgMae = maeSum / count
  const ratio = avgMae > 0 ? avgMfe / avgMae : 0

  console.log(`  ${name.padEnd(24)} ${perDay.toFixed(0).padStart(10)} ${(tp03/count*100).toFixed(0).padStart(6)}% ${(tp05/count*100).toFixed(0).padStart(6)}% ${(tp07/count*100).toFixed(0).padStart(6)}% ${(avgMfe.toFixed(2)+"%").padStart(8)} ${(avgMae.toFixed(2)+"%").padStart(8)} ${ratio.toFixed(2).padStart(6)}`)
}

// ============================================================================
// PART 5: CHERRY-PICK SIMULATION — Afternoon session
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 5: CHERRY-PICK SIMULATION (Enter 2:15PM, Exit 3:25PM)`)
console.log(`${"=".repeat(90)}\n`)

const entryBucket = PM2 + 15  // 2:15 PM
const exitBucket = PM325       // 3:25 PM

const rankMethods = {
  "by pmVolRate":   (a, b) => b.f.pmVolRate - a.f.pmVolRate,
  "by earlyMove":   (a, b) => b.f.absEarlyMove - a.f.absEarlyMove,
  "by volAccel":    (a, b) => b.f.volAccelPM - a.f.volAccelPM,
  "by composite":   (a, b) => (b.f.absEarlyMove * (b.f.vwapAligned+0.5) * (b.f.pmVolRate+1)) - (a.f.absEarlyMove * (a.f.vwapAligned+0.5) * (a.f.pmVolRate+1)),
}

const filters = {
  "All (move>0.15%)":       s => s.f.absEarlyMove >= 0.15,
  "VWAP aligned":           s => s.f.vwapAligned && s.f.absEarlyMove >= 0.15,
  "Morning cont + move":    s => s.f.continueMorning && s.f.absEarlyMove >= 0.2,
  "SELL only + move":       s => s.earlyMove < -0.15,
  "High vol + move":        s => s.f.pmVolRate >= 50 && s.f.absEarlyMove >= 0.2,
  "VWAP + vol + move":      s => s.f.vwapAligned && s.f.pmVolRate >= 30 && s.f.absEarlyMove >= 0.15,
}

console.log(`  ${"Filter + Rank".padEnd(50)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(90)}`)

const allResults = []

for (const [filterName, filterFn] of Object.entries(filters)) {
  for (const [rankName, rankFn] of Object.entries(rankMethods)) {
    for (const tp of [0.3, 0.5, 0.7]) {
      let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
      const dailyPnls = []

      for (const date of allDates) {
        const stocks = (dayStocks[date] || [])
        const candidates = []

        for (const sd of stocks) {
          const sorted = sd.buckets.sort((a, b) => a.b - b.b)
          const pm2Snap = sorted.find(b => b.b >= PM2 && b.b <= PM2 + 2)
          if (!pm2Snap) continue

          const early = sorted.filter(b => b.b >= PM2 && b.b <= entryBucket)
          if (early.length < 2) continue
          const earlyMove = (early[early.length-1].c - early[0].c) / early[0].c * 100
          if (Math.abs(earlyMove) < 0.1) continue

          // Build features for this stock
          const morningSnaps = sorted.filter(b => b.b >= 1 && b.b <= 75)
          const morningMove = morningSnaps.length > 0 && sd.dayOpen > 0 ? (morningSnaps[morningSnaps.length-1].c - sd.dayOpen) / sd.dayOpen * 100 : 0
          const pmSnaps = sorted.filter(b => b.b >= PM2)
          const pmVolRate = pmSnaps.reduce((s, b) => s + (b.vr || 0), 0) / (pmSnaps.length || 1)
          const vwap = pm2Snap.vw || 0
          const priceVsVwap = vwap > 0 ? (pm2Snap.c - vwap) / vwap * 100 : 0
          const morningVol = morningSnaps.reduce((s, b) => s + b.v, 0)
          const morningAvgVol = morningSnaps.length > 0 ? morningVol / morningSnaps.length : 1
          const pmAvgVol = pmSnaps.length > 0 ? pmSnaps.reduce((s, b) => s + b.v, 0) / pmSnaps.length : 0
          const volAccelPM = morningAvgVol > 0 ? pmAvgVol / morningAvgVol : 1

          const feat = {
            absEarlyMove: Math.abs(earlyMove),
            earlyMove,
            pmVolRate, volAccelPM, priceVsVwap,
            vwapAligned: (earlyMove > 0 && priceVsVwap > 0) || (earlyMove < 0 && priceVsVwap < 0) ? 1 : 0,
            continueMorning: (morningMove > 0 && earlyMove > 0) || (morningMove < 0 && earlyMove < 0) ? 1 : 0,
            pmBodyAvg: pmSnaps.reduce((s, b) => s + (b.br || 0), 0) / (pmSnaps.length || 1),
          }

          const obj = { sym: sd.sym, f: feat, earlyMove, buckets: sd.buckets, ep: early[early.length-1].c }
          if (filterFn(obj)) candidates.push(obj)
        }

        candidates.sort(rankFn)
        const selected = candidates.slice(0, MAX_POS)

        let dayPnl = 0
        for (const c of selected) {
          const sorted = c.buckets.sort((a, b) => a.b - b.b)
          const ds = c.earlyMove > 0 ? 1 : -1
          const qty = Math.max(Math.floor(PER_TRADE / c.ep), 1)

          let mfe = 0, mae = 0, tpHit = false
          for (const b of sorted) {
            if (b.b <= entryBucket) continue
            if (b.b > exitBucket) break
            const fav = ds > 0 ? (b.h - c.ep) / c.ep * 100 : (c.ep - b.l) / c.ep * 100
            const adv = ds > 0 ? (c.ep - b.l) / c.ep * 100 : (b.h - c.ep) / c.ep * 100
            if (fav > mfe) mfe = fav
            if (adv > mae) mae = adv
            if (!tpHit && fav >= tp) tpHit = true
          }

          // Exit at 3:25 if TP not hit
          const exitSnap = sorted.find(b => b.b >= exitBucket)
          const exitPrice = exitSnap ? exitSnap.c : c.ep
          const timeRet = ds > 0 ? (exitPrice - c.ep) / c.ep * 100 : (c.ep - exitPrice) / c.ep * 100
          const ret = tpHit ? tp : timeRet

          const pnl = c.ep * (ret / 100) * qty
          dayPnl += pnl; pnlSum += pnl; trades++
          if (ret > 0) wins++
          mfeS += mfe; maeS += mae
        }
        dailyPnls.push(dayPnl)
      }

      const wr = trades > 0 ? wins/trades*100 : 0
      const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
      const pos = dailyPnls.filter(p => p > 0).length
      const ratio = maeS > 0 ? mfeS/maeS : 0

      allResults.push({ filterName, rankName, tp, wr, avgD, roc, pos, ratio })
    }
  }
}

allResults.sort((a, b) => b.roc - a.roc)
for (const r of allResults.slice(0, 25)) {
  console.log(`  ${(r.filterName+" "+r.rankName+" tp="+r.tp).padEnd(50)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${("Rs "+r.avgD.toFixed(0)).padStart(8)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)} ${r.ratio.toFixed(2).padStart(8)}`)
}

// ============================================================================
// PART 6: MORNING + AFTERNOON COMBINED — Can we double-dip?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 6: COMBINED SESSION — Morning (9:16-10:25) + Afternoon (2:15-3:25)`)
console.log(`  If morning cherry-pick earns X and afternoon earns Y, total = X + Y`)
console.log(`${"=".repeat(90)}\n`)

// Simulate morning session (bucket 2 entry, exit at 35/71)
// + afternoon session (bucket 286+15 entry, exit at 371)
// Both use cherry-pick top 12 by volume rate

for (const tp of [0.3, 0.5, 0.7]) {
  let mornWins = 0, mornTrades = 0, mornPnl = 0
  let pmWins = 0, pmTrades = 0, pmPnl = 0
  const dailyCombo = []

  for (const date of allDates) {
    const stocks = dayStocks[date] || []
    let dayMornPnl = 0, dayPmPnl = 0

    // ── Morning session ──
    const mornCandidates = []
    for (const sd of stocks) {
      const sorted = sd.buckets.sort((a, b) => a.b - b.b)
      const entrySnaps = sorted.filter(b => b.b >= 2 && b.b <= 4)
      if (!entrySnaps.length) continue
      const last = entrySnaps[entrySnaps.length - 1]
      const mp = (last.c - sd.dayOpen) / sd.dayOpen * 100
      if (Math.abs(mp) < 0.15) continue
      mornCandidates.push({ sym: sd.sym, ep: last.c, dir: mp > 0 ? 1 : -1, vr: last.vr || 0, buckets: sorted })
    }
    mornCandidates.sort((a, b) => b.vr - a.vr)
    for (const c of mornCandidates.slice(0, MAX_POS)) {
      const exitBkt = c.dir > 0 ? 35 : 71
      let mfe = 0
      for (const b of c.buckets) {
        if (b.b <= 4) continue
        if (b.b > exitBkt) break
        const fav = c.dir > 0 ? (b.h - c.ep) / c.ep * 100 : (c.ep - b.l) / c.ep * 100
        if (fav > mfe) mfe = fav
      }
      const exitSnap = c.buckets.find(b => b.b >= exitBkt)
      const timeRet = exitSnap ? (c.dir > 0 ? (exitSnap.c - c.ep) / c.ep * 100 : (c.ep - exitSnap.c) / c.ep * 100) : 0
      const ret = mfe >= tp ? tp : timeRet
      const qty = Math.max(Math.floor(PER_TRADE / c.ep), 1)
      dayMornPnl += c.ep * (ret / 100) * qty
      mornTrades++
      if (ret > 0) mornWins++
    }

    // ── Afternoon session ──
    const pmCandidates = []
    for (const sd of stocks) {
      const sorted = sd.buckets.sort((a, b) => a.b - b.b)
      const early = sorted.filter(b => b.b >= PM2 && b.b <= entryBucket)
      if (early.length < 2) continue
      const earlyMove = (early[early.length-1].c - early[0].c) / early[0].c * 100
      if (Math.abs(earlyMove) < 0.15) continue
      const pmSnaps = sorted.filter(b => b.b >= PM2)
      const pmVR = pmSnaps.reduce((s, b) => s + (b.vr || 0), 0) / (pmSnaps.length || 1)
      pmCandidates.push({ sym: sd.sym, ep: early[early.length-1].c, dir: earlyMove > 0 ? 1 : -1, vr: pmVR, buckets: sorted })
    }
    pmCandidates.sort((a, b) => b.vr - a.vr)
    for (const c of pmCandidates.slice(0, MAX_POS)) {
      let mfe = 0
      for (const b of c.buckets) {
        if (b.b <= entryBucket) continue
        if (b.b > PM325) break
        const fav = c.dir > 0 ? (b.h - c.ep) / c.ep * 100 : (c.ep - b.l) / c.ep * 100
        if (fav > mfe) mfe = fav
      }
      const exitSnap = c.buckets.find(b => b.b >= PM325)
      const timeRet = exitSnap ? (c.dir > 0 ? (exitSnap.c - c.ep) / c.ep * 100 : (c.ep - exitSnap.c) / c.ep * 100) : 0
      const ret = mfe >= tp ? tp : timeRet
      const qty = Math.max(Math.floor(PER_TRADE / c.ep), 1)
      dayPmPnl += c.ep * (ret / 100) * qty
      pmTrades++
      if (ret > 0) pmWins++
    }

    mornPnl += dayMornPnl
    pmPnl += dayPmPnl
    dailyCombo.push(dayMornPnl + dayPmPnl)
  }

  const mornROC = (mornPnl / nd) / CAPITAL * 100
  const pmROC = (pmPnl / nd) / CAPITAL * 100
  const comboROC = mornROC + pmROC
  const comboPos = dailyCombo.filter(p => p > 0).length

  console.log(`  TP=${tp}%:`)
  console.log(`    Morning:   ${(mornWins/mornTrades*100).toFixed(1)}% win | Rs ${(mornPnl/nd).toFixed(0)}/day | ${mornROC.toFixed(2)}% ROC`)
  console.log(`    Afternoon: ${(pmWins/pmTrades*100).toFixed(1)}% win | Rs ${(pmPnl/nd).toFixed(0)}/day | ${pmROC.toFixed(2)}% ROC`)
  console.log(`    COMBINED:  Rs ${((mornPnl+pmPnl)/nd).toFixed(0)}/day | ${comboROC.toFixed(2)}% ROC | ${comboPos}/${nd} positive days`)
  console.log()
}

const best = allResults[0]
console.log(`${"=".repeat(90)}`)
console.log(`  BEST AFTERNOON SETUP: ${best.filterName} + ${best.rankName} + TP=${best.tp}%`)
console.log(`  ${best.wr.toFixed(1)}% win | Rs ${best.avgD.toFixed(0)}/day | ${best.roc.toFixed(2)}% ROC | ${best.pos}/${nd} pos | MFE/MAE=${best.ratio.toFixed(2)}`)
console.log(`${"=".repeat(90)}`)

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
