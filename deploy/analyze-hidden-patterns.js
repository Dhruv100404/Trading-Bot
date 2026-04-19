#!/usr/bin/env bun
// ============================================================================
// HIDDEN PATTERN DISCOVERY — Find what actually predicts 2%+ favorable moves
//
// Approach: Forget the signal engine. For EVERY stock-day:
// 1. Compute 30+ raw features from first 2-5 minutes
// 2. Check what happened AFTER (did price move 0.7%, 1%, 2%+ in our direction?)
// 3. Use reverse engineering to find which feature COMBINATIONS predict winners
// 4. Test patterns the signal engine completely misses
// ============================================================================

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()
const CAPITAL = 50000, PER_TRADE = 10000, MAX_POS = 12

console.log(`\n${"█".repeat(74)}`)
console.log(`  HIDDEN PATTERN DISCOVERY`)
console.log(`  No signal engine. Pure data-driven pattern search.`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)
console.log("Loading every stock-day...")

// ── Load all data with exhaustive feature extraction ──
const dayData = {} // date -> [ stockDay ]
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct: gap, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 10) continue

  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const b1 = sorted.find(b => b.b === 1)
  const b2 = sorted.find(b => b.b === 2)
  const b3 = sorted.find(b => b.b === 3)
  const b4 = sorted.find(b => b.b === 4)
  const b5 = sorted.find(b => b.b === 5)
  if (!b1 || !b2) continue

  // ── Price action features (first 1-3 candles) ──
  const c1Move = (b1.c - dayOpen) / dayOpen * 100
  const c1Range = dayOpen > 0 ? (b1.h - b1.l) / dayOpen * 100 : 0
  const c1Body = b1.br || 0
  const c1Dir = b1.c > b1.o ? 1 : -1 // 1=green, -1=red
  const c1UpperWick = b1.h > 0 ? (b1.h - Math.max(b1.o, b1.c)) / (b1.h - b1.l + 0.001) : 0
  const c1LowerWick = b1.l > 0 ? (Math.min(b1.o, b1.c) - b1.l) / (b1.h - b1.l + 0.001) : 0

  const c2Move = b2 ? (b2.c - dayOpen) / dayOpen * 100 : 0
  const c2Range = b2 && dayOpen > 0 ? (b2.h - b2.l) / dayOpen * 100 : 0
  const c2Body = b2?.br || 0
  const c2Dir = b2 ? (b2.c > b2.o ? 1 : -1) : 0

  // Direction from first 2-3 candles
  const earlyMove = b3 ? (b3.c - dayOpen) / dayOpen * 100 : c2Move
  const dir = earlyMove > 0 ? "BUY" : "SELL"
  const ds = dir === "BUY" ? 1 : -1
  const absEarlyMove = Math.abs(earlyMove)

  // ── Candle pattern features ──
  const sameDir2 = c1Dir === c2Dir ? 1 : 0 // first 2 candles same direction
  const sameDir3 = b3 ? (c1Dir === c2Dir && c2Dir === (b3.c > b3.o ? 1 : -1) ? 1 : 0) : 0
  const reversalPattern = c1Dir !== c2Dir ? 1 : 0 // c1 and c2 opposite = possible reversal
  const insideBar = b2 ? (b2.h <= b1.h && b2.l >= b1.l ? 1 : 0) : 0 // c2 inside c1
  const outsideBar = b2 ? (b2.h > b1.h && b2.l < b1.l ? 1 : 0) : 0 // c2 engulfs c1
  const breakout = b2 ? (dir === "BUY" ? b2.c > b1.h : b2.c < b1.l) ? 1 : 0 : 0 // c2 breaks c1 range

  // ── Volume features ──
  const v1 = b1.v || 0
  const v2 = b2?.v || 0
  const v3 = b3?.v || 0
  const volTotal3 = v1 + v2 + v3
  const volAccel = v1 > 0 ? v2 / v1 : 1 // volume increasing?
  const volAccel23 = v2 > 0 && v3 > 0 ? v3 / v2 : 1
  const vr1 = b1.vr || 0
  const vr2 = b2?.vr || 0

  // ── VWAP features ──
  const vwap2 = b2?.vw || 0
  const vwapDist = vwap2 > 0 ? (b2.c - vwap2) / vwap2 * 100 : 0
  const priceAboveVwap = vwap2 > 0 && b2.c > vwap2 ? 1 : 0
  const priceBelowVwap = vwap2 > 0 && b2.c < vwap2 ? 1 : 0
  const vwapAligned = (dir === "BUY" && priceAboveVwap) || (dir === "SELL" && priceBelowVwap) ? 1 : 0

  // ── Gap features ──
  const absGap = Math.abs(gap)
  const gapDir = gap > 0 ? 1 : gap < 0 ? -1 : 0
  const gapContinuation = (gapDir * ds) > 0 ? 1 : 0 // moving same direction as gap
  const gapReversal = (gapDir * ds) < 0 ? 1 : 0 // moving opposite to gap
  const bigGap = absGap > 2 ? 1 : 0
  const smallGap = absGap < 0.5 ? 1 : 0

  // ── Price features ──
  const ep = b2?.c || b1.c
  const priceRange = ep < 100 ? 0 : ep < 300 ? 1 : ep < 500 ? 2 : ep < 1000 ? 3 : 4

  // ── Momentum quality ──
  const moveVsRange = c1Range > 0 ? Math.abs(c1Move) / c1Range : 0 // how much of range is directional
  const acceleration = b3 ? Math.abs((b3.c - b2.c) / dayOpen * 100) - Math.abs((b2.c - b1.c) / dayOpen * 100) : 0
  const trendy = absEarlyMove > 0.3 && sameDir2 ? 1 : 0

  // ── OUTCOME: What happened after bucket 3? ──
  let mfe = 0, mae = 0, tp03 = false, tp05 = false, tp07 = false, tp10 = false, tp15 = false, tp20 = false
  let tp03Bkt = 0, tp07Bkt = 0, tp10Bkt = 0
  const entryBkt = 3

  for (const b of sorted) {
    if (b.b <= entryBkt) continue
    const fav = ds > 0 ? (b.h - ep) / ep * 100 : (ep - b.l) / ep * 100
    const adv = ds > 0 ? (ep - b.l) / ep * 100 : (b.h - ep) / ep * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv
    if (!tp03 && fav >= 0.3) { tp03 = true; tp03Bkt = b.b }
    if (!tp05 && fav >= 0.5) tp05 = true
    if (!tp07 && fav >= 0.7) { tp07 = true; tp07Bkt = b.b }
    if (!tp10 && fav >= 1.0) { tp10 = true; tp10Bkt = b.b }
    if (!tp15 && fav >= 1.5) tp15 = true
    if (!tp20 && fav >= 2.0) tp20 = true
  }

  const exitBkt = dir === "SELL" ? 71 : 35
  let timeRet = 0
  for (const b of sorted) {
    if (b.b >= exitBkt) {
      timeRet = ds > 0 ? (b.c - ep) / ep * 100 : (ep - b.c) / ep * 100
      break
    }
  }

  if (!dayData[date]) dayData[date] = []
  dayData[date].push({
    sym: symbol, dir, ep, date,
    // Raw features (30+)
    f: {
      absEarlyMove, c1Move: Math.abs(c1Move), c1Range, c1Body, c1Dir: c1Dir * ds, // aligned with trade dir
      c1UpperWick, c1LowerWick,
      c2Range, c2Body, c2Dir: c2Dir * ds,
      sameDir2, sameDir3, reversalPattern, insideBar, outsideBar, breakout,
      v1, v2, v3, volTotal3, volAccel, volAccel23, vr1, vr2,
      vwapDist: vwapDist * ds, vwapAligned,
      absGap, gapContinuation, gapReversal, bigGap, smallGap,
      priceRange, moveVsRange, acceleration: acceleration * ds, trendy,
      gap,
    },
    // Outcomes
    mfe, mae, tp03, tp05, tp07, tp10, tp15, tp20,
    tp03Bkt, tp07Bkt, tp10Bkt, timeRet,
  })

  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayData).sort()
const nd = allDates.length
const all = Object.values(dayData).flat()
console.log(`${lc} lines → ${all.length} stock-days, ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

const tp07Rate = all.filter(s => s.tp07).length / all.length * 100
const tp10Rate = all.filter(s => s.tp10).length / all.length * 100
console.log(`Baseline: ${tp07Rate.toFixed(1)}% hit TP=0.7% | ${tp10Rate.toFixed(1)}% hit TP=1.0% | Avg MFE: ${(all.reduce((s,t)=>s+t.mfe,0)/all.length).toFixed(2)}%\n`)

// ============================================================================
// PART 1: WHICH SINGLE FEATURES BEST PREDICT TP=0.7% HIT?
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: SINGLE FEATURE PREDICTIVE POWER (TP=0.7% hit rate)`)
console.log(`${"=".repeat(90)}\n`)

const featureNames = Object.keys(all[0].f)

// For continuous features, find optimal thresholds
const featureResults = []

for (const feat of featureNames) {
  const vals = all.map(s => s.f[feat]).filter(v => !isNaN(v))
  const sorted = [...vals].sort((a, b) => a - b)

  // Test percentile thresholds
  let bestThresh = 0, bestLift = 0, bestTP = 0, bestN = 0
  for (const pct of [10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90]) {
    const thresh = sorted[Math.floor(sorted.length * pct / 100)]
    const above = all.filter(s => s.f[feat] >= thresh)
    const below = all.filter(s => s.f[feat] < thresh)
    if (above.length < 50 || below.length < 50) continue
    const aboveTP = above.filter(s => s.tp07).length / above.length * 100
    const belowTP = below.filter(s => s.tp07).length / below.length * 100
    const lift = aboveTP - belowTP
    if (Math.abs(lift) > Math.abs(bestLift)) {
      bestLift = lift; bestThresh = thresh; bestTP = lift > 0 ? aboveTP : belowTP; bestN = lift > 0 ? above.length : below.length
    }
  }

  const aboveMfe = all.filter(s => s.f[feat] >= bestThresh)
  const belowMfe = all.filter(s => s.f[feat] < bestThresh)
  const aMfe = aboveMfe.length ? aboveMfe.reduce((s,t)=>s+t.mfe,0)/aboveMfe.length : 0
  const bMfe = belowMfe.length ? belowMfe.reduce((s,t)=>s+t.mfe,0)/belowMfe.length : 0

  featureResults.push({ feat, bestThresh, bestLift, bestTP, bestN, aMfe, bMfe })
}

featureResults.sort((a, b) => Math.abs(b.bestLift) - Math.abs(a.bestLift))

console.log(`  ${"Feature".padEnd(18)} ${"Threshold".padStart(10)} ${"Lift".padStart(8)} ${"TP07%".padStart(8)} ${"N".padStart(7)} ${"MFE_above".padStart(10)} ${"MFE_below".padStart(10)} ${"Signal"}`)
console.log(`  ${"-".repeat(85)}`)
for (const r of featureResults) {
  const sig = Math.abs(r.bestLift) > 5 ? "★★★" : Math.abs(r.bestLift) > 3 ? "★★" : Math.abs(r.bestLift) > 1 ? "★" : ""
  console.log(`  ${r.feat.padEnd(18)} ${r.bestThresh.toFixed(2).padStart(10)} ${(r.bestLift>=0?"+":"")+r.bestLift.toFixed(1)+"%".padStart(0).padStart(8)} ${(r.bestTP.toFixed(1)+"%").padStart(8)} ${r.bestN.toString().padStart(7)} ${(r.aMfe.toFixed(2)+"%").padStart(10)} ${(r.bMfe.toFixed(2)+"%").padStart(10)}   ${sig}`)
}

// ============================================================================
// PART 2: EXHAUSTIVE PAIR SEARCH — Which 2-feature combos are best?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 2: BEST 2-FEATURE COMBINATIONS (TP=0.7% hit rate among top-${MAX_POS}/day)`)
console.log(`${"=".repeat(90)}\n`)

// Build binary features at optimal thresholds
const binaryFeatures = {}
for (const r of featureResults) {
  if (Math.abs(r.bestLift) < 1) continue
  const dir = r.bestLift > 0 ? 1 : -1
  binaryFeatures[r.feat] = { thresh: r.bestThresh, dir }
}

// Test all pairs as ranking features for cherry-pick
const pairResults = []
const bfNames = Object.keys(binaryFeatures)

for (let i = 0; i < bfNames.length; i++) {
  for (let j = i + 1; j < bfNames.length; j++) {
    const f1 = bfNames[i], f2 = bfNames[j]
    const bf1 = binaryFeatures[f1], bf2 = binaryFeatures[f2]

    // Score = f1_match * 2 + f2_match * 2 + absMove (tiebreaker)
    const scoreFn = (s) => {
      let sc = 0
      if (bf1.dir > 0 ? s.f[f1] >= bf1.thresh : s.f[f1] < bf1.thresh) sc += 2
      if (bf2.dir > 0 ? s.f[f2] >= bf2.thresh : s.f[f2] < bf2.thresh) sc += 2
      sc += s.f.absEarlyMove // tiebreaker
      return sc
    }

    // Simulate cherry-pick
    let wins = 0, trades = 0, pnlSum = 0, mfeSum = 0
    for (const date of allDates) {
      const sigs = dayData[date].map(s => ({ ...s, sc: scoreFn(s) }))
      sigs.sort((a, b) => b.sc - a.sc)
      for (const s of sigs.slice(0, MAX_POS)) {
        const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
        const ret = s.tp07 ? 0.7 : s.timeRet
        pnlSum += s.ep * (ret / 100) * qty
        trades++
        if (ret > 0) wins++
        mfeSum += s.mfe
      }
    }
    const wr = trades > 0 ? wins/trades*100 : 0
    const roc = (pnlSum/nd)/CAPITAL*100
    const avgMfe = trades > 0 ? mfeSum / trades : 0
    pairResults.push({ f1, f2, wr, roc, avgMfe, trades })
  }
}

pairResults.sort((a, b) => b.roc - a.roc)
console.log(`  ${"Pair".padEnd(36)} ${"Win%".padStart(7)} ${"ROC%".padStart(7)} ${"AvgMFE".padStart(8)}`)
console.log(`  ${"-".repeat(62)}`)
for (const r of pairResults.slice(0, 25)) {
  console.log(`  ${(r.f1+" + "+r.f2).padEnd(36)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.avgMfe.toFixed(2)+"%").padStart(8)}`)
}

// ============================================================================
// PART 3: THE GOLDEN PATTERNS — What do the TOP 5% of trades look like?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 3: REVERSE ENGINEERING THE TOP 5% (highest MFE trades)`)
console.log(`${"=".repeat(90)}\n`)

const sortedByMfe = [...all].sort((a, b) => b.mfe - a.mfe)
const top5pct = sortedByMfe.slice(0, Math.floor(all.length * 0.05))
const bottom50pct = sortedByMfe.slice(Math.floor(all.length * 0.5))

console.log(`  Top 5% (${top5pct.length} trades): Avg MFE = ${(top5pct.reduce((s,t)=>s+t.mfe,0)/top5pct.length).toFixed(2)}%`)
console.log(`  Bottom 50% (${bottom50pct.length} trades): Avg MFE = ${(bottom50pct.reduce((s,t)=>s+t.mfe,0)/bottom50pct.length).toFixed(2)}%\n`)

console.log(`  ${"Feature".padEnd(18)} ${"Top 5%".padStart(10)} ${"Bot 50%".padStart(10)} ${"Diff".padStart(8)} ${"Signal"}`)
console.log(`  ${"-".repeat(60)}`)

const topVsBottom = []
for (const feat of featureNames) {
  const topAvg = top5pct.reduce((s, t) => s + t.f[feat], 0) / top5pct.length
  const botAvg = bottom50pct.reduce((s, t) => s + t.f[feat], 0) / bottom50pct.length
  const diff = topAvg - botAvg
  const pctDiff = botAvg !== 0 ? Math.abs(diff / botAvg * 100) : 0
  topVsBottom.push({ feat, topAvg, botAvg, diff, pctDiff })
}
topVsBottom.sort((a, b) => b.pctDiff - a.pctDiff)

for (const r of topVsBottom) {
  const sig = r.pctDiff > 30 ? "★★★" : r.pctDiff > 15 ? "★★" : r.pctDiff > 5 ? "★" : ""
  if (r.pctDiff < 3) continue
  console.log(`  ${r.feat.padEnd(18)} ${r.topAvg.toFixed(3).padStart(10)} ${r.botAvg.toFixed(3).padStart(10)} ${(r.diff>=0?"+":"")+r.diff.toFixed(3).padStart(0).padStart(8)}   ${sig} (${r.pctDiff.toFixed(0)}%)`)
}

// ============================================================================
// PART 4: PATTERN TEMPLATES — Named patterns and their performance
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 4: NAMED PATTERNS — Does the pattern work for cherry-pick?`)
console.log(`${"=".repeat(90)}\n`)

const patterns = {
  // Volume breakout: high volume + price breaks previous candle range
  "Vol Breakout":        s => s.f.breakout && s.f.v2 > s.f.v1 * 1.5,
  // Trend continuation: 2-3 candles same direction + increasing volume
  "Trend Continue":      s => s.f.sameDir2 && s.f.volAccel >= 1.2 && s.f.absEarlyMove >= 0.3,
  // Strong 3-bar trend
  "3-Bar Trend":         s => s.f.sameDir3 && s.f.absEarlyMove >= 0.5,
  // VWAP + Volume: price on right side of VWAP with high volume
  "VWAP + Volume":       s => s.f.vwapAligned && s.f.vr2 >= 200,
  // Big body candle: first candle has body > 70%
  "Big Body C1":         s => s.f.c1Body >= 0.7 && s.f.absEarlyMove >= 0.3,
  // Gap reversal: stock gapped one way, now moving the other
  "Gap Reversal":        s => s.f.gapReversal && s.f.absEarlyMove >= 0.3,
  // Gap continuation with volume
  "Gap + Vol Continue":  s => s.f.gapContinuation && s.f.volTotal3 >= 1000 && s.f.absEarlyMove >= 0.3,
  // Engulfing pattern (outside bar)
  "Outside Bar":         s => s.f.outsideBar && s.f.absEarlyMove >= 0.3,
  // Breakout + VWAP aligned
  "Breakout + VWAP":     s => s.f.breakout && s.f.vwapAligned,
  // Volume spike (c2 volume > 2× c1)
  "Vol Spike":           s => s.f.volAccel >= 2.0 && s.f.absEarlyMove >= 0.3,
  // Quiet then move (low c1 range, big c2 move)
  "Quiet→Explode":      s => s.f.c1Range < 0.5 && s.f.c2Range >= 0.5,
  // Strong move + strong body + VWAP aligned
  "Triple Confirm":      s => s.f.absEarlyMove >= 0.5 && s.f.c2Body >= 0.6 && s.f.vwapAligned,
  // Big gap + continuation
  "BigGap Continue":     s => s.f.bigGap && s.f.gapContinuation && s.f.absEarlyMove >= 0.5,
  // BigGap + reversal (analysis showed this is highest capturable rate)
  "BigGap Reversal":     s => s.f.bigGap && s.f.gapReversal && s.f.absEarlyMove >= 0.5,
  // Momentum quality: directional move relative to range
  "High MoveQuality":    s => s.f.moveVsRange >= 0.7 && s.f.absEarlyMove >= 0.3,
  // Volume + Body + Move (triple strength)
  "V+B+M Strong":        s => s.f.vr2 >= 100 && s.f.c2Body >= 0.5 && s.f.absEarlyMove >= 0.5,
  // Acceleration: c3 move > c2 move (momentum building)
  "Accelerating":        s => s.f.acceleration > 0.1 && s.f.absEarlyMove >= 0.3,
  // Everything aligned
  "Full Alignment":      s => s.f.vwapAligned && s.f.sameDir2 && s.f.c2Body >= 0.5 && s.f.absEarlyMove >= 0.3 && s.f.volAccel >= 1.0,
  // Just pick the biggest movers (no filters)
  "Biggest Movers":      s => s.f.absEarlyMove >= 0.8,
  // Cheap + moving (more qty = more absolute PnL)
  "Cheap + Moving":      s => s.f.priceRange <= 1 && s.f.absEarlyMove >= 0.5,
}

console.log(`  ${"Pattern".padEnd(22)} ${"Matches/d".padStart(10)} ${"TP07%".padStart(7)} ${"TP10%".padStart(7)} ${"AvgMFE".padStart(8)} ${"MFE/MAE".padStart(8)} → ${"CP_Win%".padStart(8)} ${"CP_ROC%".padStart(8)} ${"PosDays".padStart(8)}`)
console.log(`  ${"-".repeat(100)}`)

for (const [name, filterFn] of Object.entries(patterns)) {
  const matching = all.filter(filterFn)
  if (matching.length < nd * 3) continue // need at least 3/day average

  const tp07 = matching.filter(s => s.tp07).length / matching.length * 100
  const tp10 = matching.filter(s => s.tp10).length / matching.length * 100
  const avgMfe = matching.reduce((s, t) => s + t.mfe, 0) / matching.length
  const avgMae = matching.reduce((s, t) => s + t.mae, 0) / matching.length
  const ratio = avgMae > 0 ? avgMfe / avgMae : 0
  const perDay = matching.length / nd

  // Cherry-pick sim: select top MAX_POS by absEarlyMove from matched
  let wins = 0, trades = 0, pnlSum = 0
  const dailyPnls = []
  for (const date of allDates) {
    const daySigs = (dayData[date] || []).filter(filterFn)
    daySigs.sort((a, b) => b.f.absEarlyMove - a.f.absEarlyMove)
    let dayPnl = 0
    for (const s of daySigs.slice(0, MAX_POS)) {
      const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
      const ret = s.tp07 ? 0.7 : s.timeRet
      const pnl = s.ep * (ret / 100) * qty
      dayPnl += pnl; pnlSum += pnl; trades++
      if (ret > 0) wins++
    }
    dailyPnls.push(dayPnl)
  }
  const wr = trades > 0 ? wins/trades*100 : 0
  const roc = (pnlSum/nd)/CAPITAL*100
  const pos = dailyPnls.filter(p => p > 0).length

  console.log(`  ${name.padEnd(22)} ${perDay.toFixed(0).padStart(10)} ${(tp07.toFixed(0)+"%").padStart(7)} ${(tp10.toFixed(0)+"%").padStart(7)} ${(avgMfe.toFixed(2)+"%").padStart(8)} ${ratio.toFixed(2).padStart(8)} → ${(wr.toFixed(1)+"%").padStart(8)} ${(roc.toFixed(2)+"%").padStart(8)} ${(pos+"/"+nd).padStart(8)}`)
}

// ============================================================================
// PART 5: THE FILTER QUESTION — What if we REMOVE filters?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 5: WHAT IF WE REMOVE FILTERS? (more candidates = better cherry-pick)`)
console.log(`${"=".repeat(90)}\n`)

const filterTests = {
  "All stocks (no filter)":          s => true,
  "Only move > 0.15%":               s => s.f.absEarlyMove >= 0.15,
  "Only move > 0.3%":                s => s.f.absEarlyMove >= 0.3,
  "Only move > 0.5%":                s => s.f.absEarlyMove >= 0.5,
  "Move>0.15 + VWAP aligned":        s => s.f.absEarlyMove >= 0.15 && s.f.vwapAligned,
  "Move>0.3 + body>0.4":             s => s.f.absEarlyMove >= 0.3 && s.f.c2Body >= 0.4,
  "Move>0.3 + volAccel>1":           s => s.f.absEarlyMove >= 0.3 && s.f.volAccel >= 1.0,
  "Move>0.3 + sameDir":              s => s.f.absEarlyMove >= 0.3 && s.f.sameDir2,
  "SELL only + move>0.3":            s => s.dir === "SELL" && s.f.absEarlyMove >= 0.3,
  "SELL + vwap + move>0.3":          s => s.dir === "SELL" && s.f.vwapAligned && s.f.absEarlyMove >= 0.3,
  "SELL + bigGap reversal":          s => s.dir === "SELL" && s.f.bigGap && s.f.gapReversal,
}

// Test each filter with different ranking methods
const rankMethods = {
  "by absMove":    (a, b) => b.f.absEarlyMove - a.f.absEarlyMove,
  "by volRate":    (a, b) => b.f.vr2 - a.f.vr2,
  "by moveQual":   (a, b) => (b.f.moveVsRange * b.f.absEarlyMove) - (a.f.moveVsRange * a.f.absEarlyMove),
  "by composite":  (a, b) => (b.f.absEarlyMove * (b.f.c2Body+0.3) * (b.f.vwapAligned+0.5)) - (a.f.absEarlyMove * (a.f.c2Body+0.3) * (a.f.vwapAligned+0.5)),
}

console.log(`  ${"Filter + Rank".padEnd(42)} ${"Cands".padStart(6)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(88)}`)

const finalResults = []

for (const [filterName, filterFn] of Object.entries(filterTests)) {
  for (const [rankName, rankFn] of Object.entries(rankMethods)) {
    for (const tp of [0.5, 0.7, 1.0]) {
      let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
      const dailyPnls = []
      let totalCands = 0

      for (const date of allDates) {
        const filtered = (dayData[date] || []).filter(filterFn)
        totalCands += filtered.length
        filtered.sort(rankFn)
        let dayPnl = 0
        for (const s of filtered.slice(0, MAX_POS)) {
          const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
          const ret = s.tpHits?.[tp] ?? (s.mfe >= tp ? tp : s.timeRet)
          // More accurate: check if tp was hit
          const hit = tp === 0.5 ? s.tp05 : tp === 0.7 ? s.tp07 : s.tp10
          const actualRet = hit ? tp : s.timeRet
          const pnl = s.ep * (actualRet / 100) * qty
          dayPnl += pnl; pnlSum += pnl; trades++
          if (actualRet > 0) wins++
          mfeS += s.mfe; maeS += s.mae
        }
        dailyPnls.push(dayPnl)
      }

      const wr = trades > 0 ? wins/trades*100 : 0
      const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
      const pos = dailyPnls.filter(p => p > 0).length
      const ratio = maeS > 0 ? mfeS/maeS : 0
      const avgCands = totalCands / nd

      finalResults.push({ filterName, rankName, tp, wr, avgD, roc, pos, ratio, avgCands })
    }
  }
}

// Show top 30 by ROC
finalResults.sort((a, b) => b.roc - a.roc)
for (const r of finalResults.slice(0, 30)) {
  console.log(`  ${(r.filterName+" "+r.rankName+" tp="+r.tp).padEnd(52)} ${r.avgCands.toFixed(0).padStart(6)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${("Rs "+r.avgD.toFixed(0)).padStart(8)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)} ${r.ratio.toFixed(2).padStart(8)}`)
}

// ============================================================================
// PART 6: THE ULTIMATE QUESTION — What if we had PERFECT selection?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 6: PERFECT HINDSIGHT — What's theoretically possible?`)
console.log(`${"=".repeat(90)}\n`)

for (const tp of [0.5, 0.7, 1.0, 1.5, 2.0]) {
  let totalPnl = 0
  const dailyPnls = []
  let totalWins = 0, totalTrades = 0

  for (const date of allDates) {
    const sigs = dayData[date]
    // Perfect selection: pick the top MAX_POS stocks by actual MFE (hindsight)
    sigs.sort((a, b) => b.mfe - a.mfe)
    let dayPnl = 0
    for (const s of sigs.slice(0, MAX_POS)) {
      const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
      const hit = tp === 0.5 ? s.tp05 : tp === 0.7 ? s.tp07 : tp === 1.0 ? s.tp10 : tp === 1.5 ? s.tp15 : s.tp20
      const ret = hit ? tp : s.timeRet
      const pnl = s.ep * (ret / 100) * qty
      dayPnl += pnl; totalPnl += pnl; totalTrades++
      if (ret > 0) totalWins++
    }
    dailyPnls.push(dayPnl)
  }

  const wr = totalTrades > 0 ? totalWins/totalTrades*100 : 0
  const roc = (totalPnl/nd)/CAPITAL*100
  const pos = dailyPnls.filter(p => p > 0).length
  console.log(`  TP=${tp}%: ${wr.toFixed(1)}% win | Rs ${(totalPnl/nd).toFixed(0)}/day | ${roc.toFixed(2)}% ROC | ${pos}/${nd} pos days ← PERFECT SELECTION (impossible in real-time)`)
}

const best = finalResults[0]
console.log(`\n${"=".repeat(90)}`)
console.log(`  BEST FOUND: ${best.filterName} + ${best.rankName} + TP=${best.tp}%`)
console.log(`  ${best.wr.toFixed(1)}% win | Rs ${best.avgD.toFixed(0)}/day | ${best.roc.toFixed(2)}% ROC | ${best.pos}/${nd} pos | MFE/MAE=${best.ratio.toFixed(2)}`)
console.log(`${"=".repeat(90)}`)

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
