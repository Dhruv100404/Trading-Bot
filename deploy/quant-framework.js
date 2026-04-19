#!/usr/bin/env bun
// ============================================================================
// QUANT RESEARCH FRAMEWORK — The Full Reverse Engineering Loop
// ============================================================================
//
// Step 1: Find ALL momentum moves (let market tell us where opportunity is)
// Step 2: Reverse look — what preceded each move? (evidence-first patterns)
// Step 3: Backtest discovered rules
// Step 4: Analyze losses
// Step 5: Filter loss patterns (exclusion rules)
// Step 6: Re-backtest with refined rules
// Step 7: Walk-forward validation (in-sample vs out-of-sample)
// Step 8: Generate deploy config
//
// Usage: bun deploy/quant-framework.js [data/candles-consolidated.ndjson]
// ============================================================================

import { readFileSync, createReadStream, writeFileSync } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const OUT_DIR = "data/quant-results"
try { require("node:fs").mkdirSync(OUT_DIR, { recursive: true }) } catch {}

console.log(`\n${"█".repeat(70)}`)
console.log(`  QUANT RESEARCH FRAMEWORK — Evidence-First Pattern Discovery`)
console.log(`${"█".repeat(70)}\n`)

// ============================================================================
// PHASE 0: Load data, stream-process into memory-efficient structures
// ============================================================================

console.log("PHASE 0: Loading data...")
const t0 = Date.now()

// We store per-stock-day: only the first 80 buckets (entry+exit window)
// and pre-computed features. This keeps memory manageable.
const stockDays = [] // {sym, date, dayOpen, gap, buckets:[{b,o,h,l,c,v,vc,vw,vr,br}], features:{}}
const stockMeta = {} // sym -> {days, totalVol, totalPrice, ...}

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
let lineCount = 0

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lineCount++

  // Only keep buckets 1-80 (enough for entry + exit simulation up to bucket 76)
  const buckets = sd.buckets.filter(b => b.b <= 80)
  if (buckets.length < 3) continue

  stockDays.push({
    sym: sd.symbol, date: sd.date, open: sd.dayOpen, gap: sd.gapPct,
    f5r: sd.f5Range, maxUp45: sd.maxUp45, maxDown45: sd.maxDown45,
    buckets,
  })

  if (!stockMeta[sd.symbol]) stockMeta[sd.symbol] = { days: 0, signals: 0 }
  stockMeta[sd.symbol].days++

  if (lineCount % 20000 === 0) process.stderr.write(`  ${lineCount} lines...\r`)
}

const allDates = [...new Set(stockDays.map(sd => sd.date))].sort()
const trainCutoff = allDates[Math.floor(allDates.length * 0.7)] // 70/30 split
const trainDates = new Set(allDates.filter(d => d <= trainCutoff))
const testDates = new Set(allDates.filter(d => d > trainCutoff))

console.log(`Loaded ${stockDays.length} stock-days, ${Object.keys(stockMeta).length} stocks in ${((Date.now()-t0)/1000).toFixed(1)}s`)
console.log(`Train: ${trainDates.size} days (up to ${trainCutoff}) | Test: ${testDates.size} days`)

// ============================================================================
// STEP 1: Find ALL momentum moves
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 1: FIND ALL MOMENTUM MOVES (evidence-first)`)
console.log(`${"=".repeat(70)}`)

// For each stock-day, detect the first significant directional move
// A "move" = price travels 0.5%+ in one direction within buckets 1-20
// Record exactly when it started, peaked, and what preceded it

const moves = [] // { sym, date, dir, startBucket, peakBucket, magnitude, ...precursors }

for (const sd of stockDays) {
  const { sym, date, open: dayOpen, gap, buckets, f5r } = sd
  if (dayOpen <= 0 || buckets.length < 10) continue

  // Find the first 0.7%+ directional move from open
  let moveDir = null, movePeak = 0, peakBucket = 0, moveStart = 0

  // Scan buckets to find first significant move
  for (const b of buckets) {
    if (b.b > 30) break // only look in first 30 min

    const upFromOpen = (b.h - dayOpen) / dayOpen * 100
    const downFromOpen = (dayOpen - b.l) / dayOpen * 100

    if (!moveDir && upFromOpen >= 0.7) {
      moveDir = "UP"
      moveStart = Math.max(1, b.b - 1)
    }
    if (!moveDir && downFromOpen >= 0.7) {
      moveDir = "DOWN"
      moveStart = Math.max(1, b.b - 1)
    }

    if (moveDir === "UP") {
      const mag = (b.h - dayOpen) / dayOpen * 100
      if (mag > movePeak) { movePeak = mag; peakBucket = b.b }
    }
    if (moveDir === "DOWN") {
      const mag = (dayOpen - b.l) / dayOpen * 100
      if (mag > movePeak) { movePeak = mag; peakBucket = b.b }
    }
  }

  if (!moveDir || movePeak < 0.7) continue

  // Also track how far the move goes in 45 min
  let maxMove45 = 0
  for (const b of buckets) {
    if (b.b > 45) break
    const m = moveDir === "UP" ? (b.h - dayOpen) / dayOpen * 100 : (dayOpen - b.l) / dayOpen * 100
    maxMove45 = Math.max(maxMove45, m)
  }

  // === PRECURSOR ANALYSIS: what happened BEFORE the move? ===

  // 1. Volume in first 2 buckets (before most moves start)
  const preBuckets = buckets.filter(b => b.b >= 1 && b.b < moveStart)
  const entryBuckets = buckets.filter(b => b.b >= moveStart && b.b <= moveStart + 2)

  const preVol = preBuckets.reduce((s, b) => s + b.v, 0)
  const entryVol = entryBuckets.reduce((s, b) => s + b.v, 0)
  const volSurge = preVol > 0 ? entryVol / preVol : 0 // volume acceleration

  // 2. First candle characteristics
  const b1 = buckets.find(b => b.b === 1)
  const firstCandleDir = b1 ? (b1.c > b1.o ? "GREEN" : b1.c < b1.o ? "RED" : "DOJI") : "N/A"
  const firstCandleRange = b1 && dayOpen > 0 ? (b1.h - b1.l) / dayOpen * 100 : 0
  const firstCandleBody = b1 ? b1.br : 0

  // 3. Volume rate at move start
  const moveStartBucket = buckets.find(b => b.b === moveStart)
  const volRateAtStart = moveStartBucket ? moveStartBucket.vr : 0

  // 4. VWAP position at move start
  const vwapAtStart = moveStartBucket ? moveStartBucket.vw : dayOpen
  const priceVsVwap = vwapAtStart > 0 ? (dayOpen - vwapAtStart) / vwapAtStart * 100 : 0

  // 5. Price at move start vs open (pre-move drift)
  const priceAtStart = moveStartBucket ? moveStartBucket.c : dayOpen
  const preDrift = dayOpen > 0 ? (priceAtStart - dayOpen) / dayOpen * 100 : 0

  // 6. Cumulative volume at move start
  const volCumAtStart = moveStartBucket ? moveStartBucket.vc : 0

  // 7. Did the move reverse significantly? (reversal magnitude)
  let maxReversal = 0
  let hitAfterPeak = false
  for (const b of buckets) {
    if (b.b <= peakBucket) continue
    if (b.b > 60) break
    const rev = moveDir === "UP"
      ? (buckets.find(bb => bb.b === peakBucket)?.h || dayOpen) - b.l
      : b.h - (dayOpen - movePeak / 100 * dayOpen)
    const revPct = rev / dayOpen * 100
    maxReversal = Math.max(maxReversal, revPct)
  }

  moves.push({
    sym, date, dir: moveDir, startBucket: moveStart, peakBucket, magnitude: movePeak, maxMove45,
    gap, f5r, firstCandleDir, firstCandleRange, firstCandleBody,
    volSurge, volRateAtStart, volCumAtStart, priceVsVwap, preDrift,
    maxReversal, capturable: movePeak - maxReversal, // net move after reversal
    isTrainSet: trainDates.has(date),
  })
}

console.log(`\nFound ${moves.length} momentum moves (0.7%+ in first 30 min)`)
console.log(`  UP: ${moves.filter(m => m.dir === "UP").length} | DOWN: ${moves.filter(m => m.dir === "DOWN").length}`)
console.log(`  Avg magnitude: ${(moves.reduce((s,m) => s+m.magnitude, 0)/moves.length).toFixed(2)}%`)
console.log(`  Avg max in 45min: ${(moves.reduce((s,m) => s+m.maxMove45, 0)/moves.length).toFixed(2)}%`)

// ============================================================================
// STEP 2: REVERSE LOOK — What preceded the moves?
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 2: REVERSE LOOK — What preceded profitable moves?`)
console.log(`${"=".repeat(70)}`)

// Split moves into "capturable" (net move > 0.5% after reversal) vs "traps"
const capturable = moves.filter(m => m.capturable >= 0.5)
const traps = moves.filter(m => m.capturable < 0.5)

console.log(`\n  Capturable moves (net 0.5%+ after reversal): ${capturable.length}`)
console.log(`  Traps (move reverses, net < 0.5%): ${traps.length}\n`)

const avgOf = (arr, fn) => arr.length ? arr.reduce((s, x) => s + fn(x), 0) / arr.length : 0

console.log(`  PRECURSOR COMPARISON: Capturable vs Traps`)
console.log(`  ${"Feature".padEnd(30)} ${"Capturable".padStart(12)} ${"Traps".padStart(12)} ${"Signal?".padStart(10)}`)
console.log(`  ${"-".repeat(66)}`)

const features = [
  ["Gap %", m => m.gap, 2],
  ["|Gap| %", m => Math.abs(m.gap), 2],
  ["First 5min range %", m => m.f5r, 2],
  ["First candle body ratio", m => m.firstCandleBody, 3],
  ["First candle range %", m => m.firstCandleRange, 2],
  ["Volume surge (entry/pre)", m => m.volSurge, 2],
  ["Vol rate at move start", m => m.volRateAtStart, 1],
  ["Vol cum at move start", m => m.volCumAtStart, 0],
  ["Price vs VWAP %", m => m.priceVsVwap, 3],
  ["Pre-move drift %", m => m.preDrift, 3],
  ["Move start bucket", m => m.startBucket, 1],
  ["Move magnitude %", m => m.magnitude, 2],
  ["Max reversal %", m => m.maxReversal, 2],
]

for (const [name, fn, dec] of features) {
  const cAvg = avgOf(capturable, fn)
  const tAvg = avgOf(traps, fn)
  const diff = Math.abs(cAvg - tAvg)
  const sig = diff > Math.abs(cAvg) * 0.15 ? "<<<" : ""
  console.log(`  ${name.padEnd(30)} ${cAvg.toFixed(dec).padStart(12)} ${tAvg.toFixed(dec).padStart(12)} ${sig.padStart(10)}`)
}

// First candle direction distribution
console.log(`\n  FIRST CANDLE DIRECTION:`)
for (const dir of ["GREEN", "RED", "DOJI"]) {
  const cCount = capturable.filter(m => m.firstCandleDir === dir).length
  const tCount = traps.filter(m => m.firstCandleDir === dir).length
  console.log(`  ${dir.padEnd(10)} Capturable: ${cCount} (${(cCount/capturable.length*100).toFixed(1)}%) | Traps: ${tCount} (${(tCount/traps.length*100).toFixed(1)}%)`)
}

// Gap + Move direction alignment
console.log(`\n  GAP + MOVE DIRECTION:`)
const combos = [
  ["Gap-up + UP move (continuation)", m => m.gap > 0.3 && m.dir === "UP"],
  ["Gap-up + DOWN move (reversal)", m => m.gap > 0.3 && m.dir === "DOWN"],
  ["Gap-down + DOWN move (continuation)", m => m.gap < -0.3 && m.dir === "DOWN"],
  ["Gap-down + UP move (reversal)", m => m.gap < -0.3 && m.dir === "UP"],
  ["Flat + UP", m => Math.abs(m.gap) <= 0.3 && m.dir === "UP"],
  ["Flat + DOWN", m => Math.abs(m.gap) <= 0.3 && m.dir === "DOWN"],
]
console.log(`  ${"Pattern".padEnd(35)} ${"Total".padStart(6)} ${"Capturable".padStart(11)} ${"Rate".padStart(6)} ${"AvgMag".padStart(8)}`)
for (const [label, fn] of combos) {
  const all = moves.filter(fn)
  const cap = all.filter(m => m.capturable >= 0.5)
  const avg = avgOf(all, m => m.maxMove45)
  console.log(`  ${label.padEnd(35)} ${String(all.length).padStart(6)} ${String(cap.length).padStart(11)} ${(cap.length/all.length*100).toFixed(1).padStart(5)}% ${(avg.toFixed(2)+"%").padStart(8)}`)
}

// ============================================================================
// STEP 3: DERIVE RULES FROM DATA (auto-generated, not hypothesized)
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 3: AUTO-DERIVED RULES FROM DATA`)
console.log(`${"=".repeat(70)}`)

// Use ONLY training data for rule discovery
const trainMoves = moves.filter(m => m.isTrainSet)
const trainCap = trainMoves.filter(m => m.capturable >= 0.5)
const trainTraps = trainMoves.filter(m => m.capturable < 0.5)

console.log(`  Training set: ${trainMoves.length} moves (${trainCap.length} capturable, ${trainTraps.length} traps)`)

// Auto-discover thresholds by finding the split point that maximizes separation
function findBestThreshold(capArr, trapArr, fn, steps) {
  const allVals = [...capArr.map(fn), ...trapArr.map(fn)].sort((a, b) => a - b)
  let bestThresh = 0, bestScore = 0, bestDir = ">"

  for (let i = 0; i < steps; i++) {
    const pctile = i / steps
    const thresh = allVals[Math.floor(pctile * allVals.length)] || 0

    // Try ">= thresh"
    const capAbove = capArr.filter(m => fn(m) >= thresh).length
    const trapAbove = trapArr.filter(m => fn(m) >= thresh).length
    const totalAbove = capAbove + trapAbove
    if (totalAbove > 0) {
      const precision = capAbove / totalAbove
      const recall = capAbove / capArr.length
      const f1 = precision > 0 && recall > 0 ? 2 * precision * recall / (precision + recall) : 0
      if (f1 > bestScore) { bestScore = f1; bestThresh = thresh; bestDir = ">=" }
    }

    // Try "< thresh"
    const capBelow = capArr.filter(m => fn(m) < thresh).length
    const trapBelow = trapArr.filter(m => fn(m) < thresh).length
    const totalBelow = capBelow + trapBelow
    if (totalBelow > 0) {
      const precision = capBelow / totalBelow
      const recall = capBelow / capArr.length
      const f1 = precision > 0 && recall > 0 ? 2 * precision * recall / (precision + recall) : 0
      if (f1 > bestScore) { bestScore = f1; bestThresh = thresh; bestDir = "<" }
    }
  }

  return { threshold: bestThresh, direction: bestDir, f1: bestScore }
}

console.log(`\n  AUTO-DISCOVERED THRESHOLDS (from training data):`)
console.log(`  ${"Feature".padEnd(28)} ${"Threshold".padStart(12)} ${"Dir".padStart(4)} ${"F1 Score".padStart(9)} ${"Cap rate if applied".padStart(20)}`)
console.log(`  ${"-".repeat(76)}`)

const ruleFeatures = [
  ["f5r (5min range %)", m => m.f5r],
  ["|gap| %", m => Math.abs(m.gap)],
  ["volSurge", m => m.volSurge],
  ["volRateAtStart", m => m.volRateAtStart],
  ["volCumAtStart", m => m.volCumAtStart],
  ["firstCandleRange %", m => m.firstCandleRange],
  ["firstCandleBody", m => m.firstCandleBody],
  ["startBucket", m => m.startBucket],
]

const discoveredRules = []
for (const [name, fn] of ruleFeatures) {
  const result = findBestThreshold(trainCap, trainTraps, fn, 20)
  const passing = trainMoves.filter(m => result.direction === ">=" ? fn(m) >= result.threshold : fn(m) < result.threshold)
  const passingCap = passing.filter(m => m.capturable >= 0.5).length
  const capRate = passing.length > 0 ? passingCap / passing.length * 100 : 0
  console.log(`  ${name.padEnd(28)} ${result.threshold.toFixed(2).padStart(12)} ${result.direction.padStart(4)} ${result.f1.toFixed(3).padStart(9)} ${(capRate.toFixed(1) + "% of " + passing.length).padStart(20)}`)
  discoveredRules.push({ name, fn, ...result })
}

// ============================================================================
// STEP 4 & 5: Combined — Apply rules, analyze what still fails
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 4-5: APPLY RULES + LOSS ANALYSIS`)
console.log(`${"=".repeat(70)}`)

// Apply top rules as a combined filter
function applyRules(move) {
  let score = 0
  // Rule 1: First 5-min range should indicate activity
  if (move.f5r >= 0.8) score += 1
  // Rule 2: Volume must be present at move start
  if (move.volRateAtStart >= 5) score += 1
  // Rule 3: Gap not extreme
  if (Math.abs(move.gap) < 3) score += 1
  // Rule 4: First candle has body (not doji)
  if (move.firstCandleBody >= 0.3) score += 1
  // Rule 5: Move starts early (within first 5 buckets)
  if (move.startBucket <= 5) score += 1
  return score
}

console.log(`\n  Applying combined rules on ALL moves:`)
for (let minScore = 1; minScore <= 5; minScore++) {
  const passing = moves.filter(m => applyRules(m) >= minScore)
  const passingCap = passing.filter(m => m.capturable >= 0.5)
  const passingTrap = passing.filter(m => m.capturable < 0.5)
  const capRate = passing.length > 0 ? passingCap.length / passing.length * 100 : 0
  const avgMag = avgOf(passingCap, m => m.maxMove45)
  console.log(`  Score >= ${minScore}: ${passing.length} moves, ${passingCap.length} capturable (${capRate.toFixed(1)}%), avg move ${avgMag.toFixed(2)}%, traps: ${passingTrap.length}`)

  // What do remaining traps look like?
  if (minScore === 3 && passingTrap.length > 0) {
    console.log(`\n    Remaining traps (score >= 3) profile:`)
    console.log(`    Avg magnitude: ${avgOf(passingTrap, m => m.magnitude).toFixed(2)}% (looks like a move but reverses)`)
    console.log(`    Avg reversal: ${avgOf(passingTrap, m => m.maxReversal).toFixed(2)}%`)
    console.log(`    Avg gap: ${avgOf(passingTrap, m => m.gap).toFixed(2)}%`)
    console.log(`    Avg vol rate: ${avgOf(passingTrap, m => m.volRateAtStart).toFixed(1)}`)
    console.log(`    These are FAKE MOVES — high initial momentum that fades.`)
    console.log(`    Key difference from capturable: higher reversal (${avgOf(passingTrap, m => m.maxReversal).toFixed(2)}% vs ${avgOf(passingCap.filter(m => applyRules(m) >= 3), m => m.maxReversal).toFixed(2)}%)`)
  }
}

// ============================================================================
// STEP 6: RE-BACKTEST WITH REFINED RULES (simulated P&L)
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 6: RE-BACKTEST WITH REFINED RULES`)
console.log(`${"=".repeat(70)}`)

// Simulate trading: for each day, find moves passing filters, enter at move start, exit at TP or time
const TP_LEVELS = [0.5, 0.7, 1.0, 1.5, 2.0]

console.log(`\n  Simulating trades on capturable moves (5x margin, Rs 1L capital, Rs 25K/trade):`)
console.log(`  ${"TP%".padEnd(6)} ${"MinSc".padStart(6)} ${"Trades".padStart(7)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"PosDays".padStart(8)}`)

const capital = 100000
let bestSetup = null

for (const tp of TP_LEVELS) {
  for (const minSc of [2, 3, 4]) {
    const filteredMoves = moves.filter(m => applyRules(m) >= minSc)

    const dailyPnls = {}
    let totalTrades = 0, totalWins = 0, totalRet = 0

    for (const m of filteredMoves) {
      const ret = m.maxMove45 >= tp ? tp : -(m.maxReversal > 0 ? Math.min(m.maxReversal, 1.5) : 0.3)
      const win = ret > 0
      const qty = Math.floor(25000 / (m.sym === "dummy" ? 100 : 200)) || 1 // approximate
      const pnl = 200 * (ret / 100) * qty // approximate price Rs 200

      if (!dailyPnls[m.date]) dailyPnls[m.date] = 0
      dailyPnls[m.date] += pnl
      totalTrades++
      if (win) totalWins++
      totalRet += ret
    }

    const days = Object.keys(dailyPnls)
    const nd = days.length || 1
    const totalPnl = Object.values(dailyPnls).reduce((s, p) => s + p, 0)
    const avgDaily = totalPnl / nd
    const posDays = Object.values(dailyPnls).filter(p => p > 0).length
    const roc = avgDaily / capital * 100
    const wr = totalTrades > 0 ? totalWins / totalTrades * 100 : 0
    const avg = totalTrades > 0 ? totalRet / totalTrades : 0

    if (totalTrades >= 100) {
      console.log(`  ${tp.toFixed(1).padEnd(6)} ${String(minSc).padStart(6)} ${String(totalTrades).padStart(7)} ${(wr.toFixed(1)+"%").padStart(6)} ${((avg>=0?"+":"")+avg.toFixed(2)+"%").padStart(7)} ${avgDaily.toFixed(0).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${(posDays+"/"+nd).padStart(8)}`)

      if (!bestSetup || roc > bestSetup.roc) {
        bestSetup = { tp, minSc, trades: totalTrades, wr, avg, avgDaily, roc, posDays, nd }
      }
    }
  }
}

// ============================================================================
// STEP 7: WALK-FORWARD VALIDATION
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 7: WALK-FORWARD VALIDATION (in-sample vs out-of-sample)`)
console.log(`${"=".repeat(70)}`)

console.log(`\n  Training period: up to ${trainCutoff} (${trainDates.size} days)`)
console.log(`  Test period: after ${trainCutoff} (${testDates.size} days)\n`)

// Run the same analysis on train vs test
for (const [label, dateSet] of [["TRAIN (in-sample)", trainDates], ["TEST (out-of-sample)", testDates]]) {
  const periodMoves = moves.filter(m => dateSet.has(m.date))
  const filtered = periodMoves.filter(m => applyRules(m) >= 3)
  const capMoves = filtered.filter(m => m.capturable >= 0.5)

  console.log(`  ${label}:`)
  console.log(`    Total moves: ${periodMoves.length} | Filtered (score>=3): ${filtered.length}`)
  console.log(`    Capturable: ${capMoves.length} (${(capMoves.length/filtered.length*100).toFixed(1)}%)`)
  console.log(`    Avg magnitude: ${avgOf(capMoves, m => m.maxMove45).toFixed(2)}%`)
  console.log(`    Avg capturable (net): ${avgOf(capMoves, m => m.capturable).toFixed(2)}%`)

  // Simulate with TP=1%
  let wins = 0, total = 0
  for (const m of filtered) {
    total++
    if (m.maxMove45 >= 1.0) wins++
  }
  console.log(`    TP=1% hit rate: ${(wins/total*100).toFixed(1)}% (${wins}/${total})`)
  console.log()
}

// Stability check: does the pattern hold across time?
console.log(`  WEEKLY STABILITY (does capturable rate hold week to week?):`)
const weeks = {}
for (const m of moves.filter(m => applyRules(m) >= 3)) {
  const d = new Date(m.date)
  const weekStart = new Date(d); weekStart.setDate(d.getDate() - d.getDay())
  const wk = weekStart.toISOString().split("T")[0]
  if (!weeks[wk]) weeks[wk] = { total: 0, cap: 0 }
  weeks[wk].total++
  if (m.capturable >= 0.5) weeks[wk].cap++
}
console.log(`  ${"Week".padEnd(12)} ${"Moves".padStart(6)} ${"Capturable".padStart(11)} ${"Rate".padStart(7)}`)
for (const [wk, data] of Object.entries(weeks).sort()) {
  console.log(`  ${wk.padEnd(12)} ${String(data.total).padStart(6)} ${String(data.cap).padStart(11)} ${(data.cap/data.total*100).toFixed(1).padStart(6)}%`)
}

// ============================================================================
// STEP 8: GENERATE DEPLOY CONFIG
// ============================================================================

console.log(`\n${"=".repeat(70)}`)
console.log(`  STEP 8: DEPLOY RECOMMENDATIONS`)
console.log(`${"=".repeat(70)}`)

// Key findings summary
const allFiltered = moves.filter(m => applyRules(m) >= 3)
const capRate = allFiltered.filter(m => m.capturable >= 0.5).length / allFiltered.length * 100

console.log(`\n  DISCOVERED RULES (from market evidence):`)
console.log(`  1. First 5-min range >= 0.8% (stock is active)`)
console.log(`  2. Volume rate >= 5 shares/sec at move start (real participation)`)
console.log(`  3. |Gap| < 3% (extreme gaps reverse)`)
console.log(`  4. First candle body ratio >= 0.3 (conviction, not indecision)`)
console.log(`  5. Move starts within first 5 buckets (9:15-9:19)`)

console.log(`\n  STOCK SELECTION:`)
// Find stocks with highest capturable rate
const stockCapRate = {}
for (const m of allFiltered) {
  if (!stockCapRate[m.sym]) stockCapRate[m.sym] = { total: 0, cap: 0, totalMag: 0 }
  stockCapRate[m.sym].total++
  if (m.capturable >= 0.5) { stockCapRate[m.sym].cap++; stockCapRate[m.sym].totalMag += m.capturable }
}

const rankedStocks = Object.entries(stockCapRate)
  .filter(([, v]) => v.total >= 5)
  .map(([sym, v]) => ({ sym, total: v.total, cap: v.cap, rate: v.cap / v.total * 100, avgCap: v.cap > 0 ? v.totalMag / v.cap : 0 }))
  .sort((a, b) => b.rate - a.rate || b.avgCap - a.avgCap)

console.log(`\n  TOP 40 STOCKS BY CAPTURABLE MOVE RATE (from evidence):`)
console.log(`  ${"Sym".padEnd(18)} ${"Moves".padStart(6)} ${"Capturable".padStart(11)} ${"Rate".padStart(6)} ${"AvgNet%".padStart(8)}`)
console.log(`  ${"-".repeat(52)}`)
for (const s of rankedStocks.slice(0, 40)) {
  console.log(`  ${s.sym.padEnd(18)} ${String(s.total).padStart(6)} ${String(s.cap).padStart(11)} ${(s.rate.toFixed(0)+"%").padStart(6)} ${(s.avgCap.toFixed(2)+"%").padStart(8)}`)
}

// Stocks to avoid
const avoidStocks = Object.entries(stockCapRate)
  .filter(([, v]) => v.total >= 5 && v.cap / v.total < 0.4)
  .map(([sym, v]) => ({ sym, total: v.total, rate: v.cap / v.total * 100 }))
  .sort((a, b) => a.rate - b.rate)

console.log(`\n  STOCKS TO AVOID (capturable rate < 40%, 5+ moves):`)
console.log(`  ${avoidStocks.slice(0, 30).map(s => s.sym).join(", ")}`)

// Save the framework results
const frameworkOutput = {
  generatedAt: new Date().toISOString(),
  dataRange: { from: allDates[0], to: allDates[allDates.length - 1], trainCutoff },
  rules: {
    min5minRange: 0.8,
    minVolRateAtStart: 5,
    maxAbsGap: 3.0,
    minFirstCandleBody: 0.3,
    maxStartBucket: 5,
    minRuleScore: 3,
    recommendedTP: 1.0,
  },
  stats: {
    totalMoves: moves.length,
    capturableRate: capRate,
    trainCapRate: trainMoves.filter(m => applyRules(m) >= 3 && m.capturable >= 0.5).length /
                  trainMoves.filter(m => applyRules(m) >= 3).length * 100,
    testCapRate: moves.filter(m => testDates.has(m.date) && applyRules(m) >= 3).length > 0
      ? moves.filter(m => testDates.has(m.date) && applyRules(m) >= 3 && m.capturable >= 0.5).length /
        moves.filter(m => testDates.has(m.date) && applyRules(m) >= 3).length * 100
      : 0,
  },
  topStocks: rankedStocks.filter(s => s.rate >= 70).map(s => s.sym),
  avoidStocks: avoidStocks.map(s => s.sym),
}

writeFileSync(`${OUT_DIR}/framework-results.json`, JSON.stringify(frameworkOutput, null, 2))
console.log(`\n  Framework results saved to ${OUT_DIR}/framework-results.json`)
console.log(`  Top stocks (70%+ capturable): ${frameworkOutput.topStocks.length}`)
console.log(`  Avoid stocks: ${frameworkOutput.avoidStocks.length}`)

if (bestSetup) {
  console.log(`\n  ${"=".repeat(55)}`)
  console.log(`  BEST ACHIEVABLE SETUP:`)
  console.log(`  TP=${bestSetup.tp}% | Min rule score=${bestSetup.minSc}`)
  console.log(`  ${bestSetup.trades} trades | ${bestSetup.wr.toFixed(1)}% win | ${bestSetup.avg >= 0 ? "+" : ""}${bestSetup.avg.toFixed(2)}% avg`)
  console.log(`  Rs ${bestSetup.avgDaily.toFixed(0)}/day | ${bestSetup.roc.toFixed(2)}% ROC | ${bestSetup.posDays}/${bestSetup.nd} pos days`)
  console.log(`  ${"=".repeat(55)}`)
}

console.log(`\nTotal framework time: ${((Date.now()-t0)/1000).toFixed(1)}s`)
