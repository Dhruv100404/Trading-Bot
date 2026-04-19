#!/usr/bin/env bun
// ============================================================================
// ANALYSIS 1: TRAP/REVERSAL PATTERN DETECTION
// Can we detect when a move is about to reverse? → exit early or flip position
//
// ANALYSIS 2: COMBINED STOCK-LEVEL + QUANT FRAMEWORK
// Take best stocks from stock analysis, validate through quant framework
// ============================================================================

import { readFileSync, createReadStream, writeFileSync } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
console.log(`\n${"█".repeat(70)}`)
console.log(`  TRAP FORENSICS + COMBINED UNIVERSE ANALYSIS`)
console.log(`${"█".repeat(70)}\n`)

const t0 = Date.now()

// Load F&O set
let fnoSet = new Set()
try {
  for (const line of readFileSync("data/candles/scrip-master.csv", "utf-8").split("\n")) {
    const c = line.split(",")
    if (c[0]?.trim() === "NSE" && c[3]?.trim() === "FUTSTK") {
      const s = c[5]?.trim().split("-")[0]; if (s) fnoSet.add(s)
    }
  }
} catch {}

// Load recommended watchlist from previous analysis
let tier1Stocks = new Set(), blacklistStocks = new Set()
try {
  const wl = JSON.parse(readFileSync("data/recommended-watchlist.json", "utf-8"))
  tier1Stocks = new Set(wl.tier1 || [])
  blacklistStocks = new Set(wl.blacklist || [])
  console.log(`Loaded watchlist: Tier1=${tier1Stocks.size}, Blacklist=${blacklistStocks.size}`)
} catch { console.log("No watchlist found, proceeding without") }

// ============================================================================
// Stream and process
// ============================================================================

console.log(`Streaming ${DATA_FILE}...`)

// For each stock-day, we track the FULL move lifecycle:
// entry → initial move → peak → reversal → secondary move
// This gives us the "anatomy" of every momentum event

const moveProfiles = []  // detailed move anatomy
const stockStats = {}    // per-stock accumulator
let lineCount = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lineCount++
  const { symbol: sym, date, dayOpen, gapPct: gap, buckets, f5Range: f5r, maxUp45, maxDown45 } = sd
  if (dayOpen <= 0 || buckets.length < 10) continue

  const isFno = fnoSet.has(sym)
  const isTier1 = tier1Stocks.has(sym)
  const isBlacklist = blacklistStocks.has(sym)

  // ---- MOVE ANATOMY: track the full lifecycle ----
  // Phase 1: Initial direction (buckets 1-5)
  // Phase 2: Primary move (until peak)
  // Phase 3: Reversal (from peak)
  // Phase 4: Secondary move (post-reversal)

  const b1 = buckets.find(b => b.b === 1)
  if (!b1) continue

  // Detect primary direction from first 3-5 buckets
  const earlyBuckets = buckets.filter(b => b.b >= 1 && b.b <= 5)
  if (earlyBuckets.length < 3) continue

  const earlyLast = earlyBuckets[earlyBuckets.length - 1]
  const earlyMove = (earlyLast.c - dayOpen) / dayOpen * 100
  const primaryDir = earlyMove > 0.2 ? "UP" : earlyMove < -0.2 ? "DOWN" : null
  if (!primaryDir) continue // no clear early direction

  const ds = primaryDir === "UP" ? 1 : -1

  // Track move through all buckets (up to 75 for exit simulation)
  let peakPrice = dayOpen, peakBucket = 1
  let troughAfterPeak = null, troughBucket = null
  let phase = "MOVING" // MOVING → PEAKED → REVERSING → SECONDARY

  const bucketData = [] // [{bucket, favorable%, adverse%, vol, volRate, bodyRatio, phase}]
  let hitTP07 = false, hitTP10 = false, hitTP15 = false
  let tp07Bucket = 0, tp10Bucket = 0, tp15Bucket = 0
  let maxFav = 0, maxAdv = 0, peakBkt = 1

  // Volume tracking for reversal detection
  let moveVolTotal = 0, moveBuckets = 0
  let reversalVolTotal = 0, reversalBuckets = 0

  for (const b of buckets) {
    if (b.b > 75) break

    const fav = ds > 0 ? (b.h - dayOpen) / dayOpen * 100 : (dayOpen - b.l) / dayOpen * 100
    const adv = ds > 0 ? (dayOpen - b.l) / dayOpen * 100 : (b.h - dayOpen) / dayOpen * 100
    const curFav = ds > 0 ? (b.c - dayOpen) / dayOpen * 100 : (dayOpen - b.c) / dayOpen * 100

    if (fav > maxFav) { maxFav = fav; peakBkt = b.b; peakPrice = ds > 0 ? b.h : b.l }
    if (adv > maxAdv) maxAdv = adv

    if (!hitTP07 && maxFav >= 0.7) { hitTP07 = true; tp07Bucket = b.b }
    if (!hitTP10 && maxFav >= 1.0) { hitTP10 = true; tp10Bucket = b.b }
    if (!hitTP15 && maxFav >= 1.5) { hitTP15 = true; tp15Bucket = b.b }

    // Detect phase transitions
    if (phase === "MOVING" && b.b > 3) {
      // Check if we've peaked: favorable move started declining
      if (fav < maxFav * 0.7 && maxFav > 0.5) {
        phase = "REVERSING"
        troughAfterPeak = curFav
        troughBucket = b.b
      }
    }

    if (phase === "MOVING") { moveVolTotal += b.v; moveBuckets++ }
    if (phase === "REVERSING") { reversalVolTotal += b.v; reversalBuckets++ }

    // Reversal signals at each bucket
    const volAccel = b.b >= 2 ? b.v / (buckets.find(bb => bb.b === b.b - 1)?.v || b.v || 1) : 1
    const priceVsVwap = b.vw > 0 ? (b.c - b.vw) / b.vw * 100 : 0

    bucketData.push({
      b: b.b, fav, adv, curFav, v: b.v, vr: b.vr, br: b.br,
      volAccel, priceVsVwap, phase,
    })
  }

  // Compute reversal characteristics
  const reversalMag = maxFav - (troughAfterPeak || maxFav)
  const moveAvgVol = moveBuckets > 0 ? moveVolTotal / moveBuckets : 0
  const revAvgVol = reversalBuckets > 0 ? reversalVolTotal / reversalBuckets : 0
  const volShift = moveAvgVol > 0 ? revAvgVol / moveAvgVol : 1

  // Body ratio change: at peak vs 2 buckets before peak
  const peakBd = bucketData.find(d => d.b === peakBkt)
  const prePeakBd = bucketData.find(d => d.b === Math.max(1, peakBkt - 2))
  const bodyRatioShift = peakBd && prePeakBd ? peakBd.br - prePeakBd.br : 0

  // VWAP cross near peak: was price diverging from VWAP?
  const vwapAtPeak = peakBd ? peakBd.priceVsVwap : 0

  // Volume spike at peak bucket
  const volSpikePeak = peakBd && peakBd.b >= 2
    ? peakBd.v / (bucketData.find(d => d.b === peakBd.b - 1)?.v || peakBd.v || 1)
    : 1

  // Did the move reverse past entry? (full reversal)
  const fullReversal = maxAdv > maxFav * 0.5

  // How fast was the reversal?
  const reversalSpeed = peakBkt > 0 && troughBucket ? (troughBucket - peakBkt) : 99

  // Classify the move
  const isCapturable = maxFav >= 1.0 && (maxFav - maxAdv) > 0
  const isTrap = maxFav >= 0.7 && maxAdv > maxFav

  moveProfiles.push({
    sym, date, dir: primaryDir, gap, f5r, isFno, isTier1, isBlacklist,
    maxFav, maxAdv, peakBkt, isCapturable, isTrap,
    hitTP07, tp07Bucket, hitTP10, tp10Bucket, hitTP15, tp15Bucket,
    // Reversal detection features
    reversalMag, reversalSpeed, volShift, bodyRatioShift,
    vwapAtPeak, volSpikePeak, fullReversal,
    // Move characteristics
    earlyMove: Math.abs(earlyMove),
    firstCandleBody: b1.br,
    firstCandleDir: b1.c > b1.o ? "GREEN" : "RED",
    moveAvgVol, revAvgVol,
    // Price at peak vs VWAP
    peakVsVwap: vwapAtPeak,
  })

  // Per-stock tracking
  if (!stockStats[sym]) stockStats[sym] = { moves: 0, traps: 0, capturable: 0, tier1: isTier1, blacklist: isBlacklist }
  stockStats[sym].moves++
  if (isTrap) stockStats[sym].traps++
  if (isCapturable) stockStats[sym].capturable++

  if (lineCount % 20000 === 0) process.stderr.write(`  ${lineCount} lines...\r`)
}

console.log(`\nProcessed ${lineCount} stock-days → ${moveProfiles.length} moves in ${((Date.now()-t0)/1000).toFixed(1)}s`)

const avgOf = (arr, fn) => arr.length ? arr.reduce((s, x) => s + fn(x), 0) / arr.length : 0
const allDates = [...new Set(moveProfiles.map(m => m.date))].sort()
const nd = allDates.length

// ============================================================================
// ANALYSIS 1: TRAP FORENSICS — Can we detect reversals?
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  ANALYSIS 1: TRAP/REVERSAL FORENSICS`)
console.log(`${"=".repeat(74)}`)

const traps = moveProfiles.filter(m => m.isTrap)
const capturables = moveProfiles.filter(m => m.isCapturable)
const neutrals = moveProfiles.filter(m => !m.isTrap && !m.isCapturable)

console.log(`\n  Move classification:`)
console.log(`  Capturable (maxFav >= 1%, net positive): ${capturables.length} (${(capturables.length/moveProfiles.length*100).toFixed(1)}%)`)
console.log(`  Traps (maxFav >= 0.7%, reversal > move):  ${traps.length} (${(traps.length/moveProfiles.length*100).toFixed(1)}%)`)
console.log(`  Neutral (small moves):                    ${neutrals.length}`)

// --- 1A. What REVERSAL looks like vs sustained move ---
console.log(`\n  --- 1A. ANATOMY: Trap vs Capturable at the moment of peak ---`)
console.log(`  ${"Feature".padEnd(35)} ${"Capturable".padStart(12)} ${"Trap".padStart(12)} ${"Delta".padStart(12)} ${"Signal?".padStart(8)}`)
console.log(`  ${"-".repeat(82)}`)

const comparisons = [
  ["Peak bucket (when peak happens)", m => m.peakBkt],
  ["Max favorable move %", m => m.maxFav],
  ["Max adverse move %", m => m.maxAdv],
  ["Reversal magnitude %", m => m.reversalMag],
  ["Reversal speed (buckets)", m => m.reversalSpeed],
  ["Volume shift (reversal/move)", m => m.volShift],
  ["Body ratio shift at peak", m => m.bodyRatioShift],
  ["VWAP distance at peak %", m => m.vwapAtPeak],
  ["Volume spike at peak (ratio)", m => m.volSpikePeak],
  ["First candle body ratio", m => m.firstCandleBody],
  ["Early move % (first 5 bkts)", m => m.earlyMove],
  ["|Gap| %", m => Math.abs(m.gap)],
  ["First 5-min range %", m => m.f5r],
  ["Move avg volume", m => m.moveAvgVol],
  ["Reversal avg volume", m => m.revAvgVol],
]

const signals = []
for (const [name, fn] of comparisons) {
  const cVal = avgOf(capturables, fn)
  const tVal = avgOf(traps, fn)
  const delta = tVal - cVal
  const pctDelta = cVal !== 0 ? Math.abs(delta / cVal * 100) : 0
  const isSig = pctDelta > 15
  if (isSig) signals.push({ name, cVal, tVal, delta })
  console.log(`  ${name.padEnd(35)} ${cVal.toFixed(3).padStart(12)} ${tVal.toFixed(3).padStart(12)} ${((delta>=0?"+":"")+delta.toFixed(3)).padStart(12)} ${isSig ? "<<<" : ""}`.padEnd(8))
}

// --- 1B. REVERSAL DETECTION RULES ---
console.log(`\n  --- 1B. REVERSAL DETECTION RULES (from evidence) ---`)
console.log(`  These signals appear AT OR NEAR the peak, before the reversal completes:\n`)

// Test each signal as a reversal detector
const detectors = [
  ["Volume spike > 2x at peak", m => m.volSpikePeak > 2.0],
  ["Volume spike > 1.5x at peak", m => m.volSpikePeak > 1.5],
  ["VWAP divergence > 0.5% at peak", m => Math.abs(m.vwapAtPeak) > 0.5],
  ["VWAP divergence > 0.3% at peak", m => Math.abs(m.vwapAtPeak) > 0.3],
  ["Body ratio drops (shift < -0.2)", m => m.bodyRatioShift < -0.2],
  ["Body ratio drops (shift < -0.1)", m => m.bodyRatioShift < -0.1],
  ["Volume shift > 1.5 (reversal louder)", m => m.volShift > 1.5],
  ["Volume shift > 1.2 (reversal louder)", m => m.volShift > 1.2],
  ["Peak early (bucket <= 3)", m => m.peakBkt <= 3],
  ["Peak early (bucket <= 5)", m => m.peakBkt <= 5],
  ["Large early move (> 1%)", m => m.earlyMove > 1.0],
  ["Large |gap| > 2%", m => Math.abs(m.gap) > 2.0],
  ["First candle RED", m => m.firstCandleDir === "RED"],
  ["First candle weak body (< 0.3)", m => m.firstCandleBody < 0.3],
]

console.log(`  ${"Detector".padEnd(40)} ${"Traps".padStart(8)} ${"Capt".padStart(8)} ${"Precision".padStart(10)} ${"Recall".padStart(8)} ${"Useful?".padStart(8)}`)
console.log(`  ${"-".repeat(75)}`)

for (const [name, fn] of detectors) {
  const trapFlagged = traps.filter(fn).length
  const capFlagged = capturables.filter(fn).length
  const total = trapFlagged + capFlagged
  const precision = total > 0 ? trapFlagged / total * 100 : 0  // % of flagged that are actually traps
  const recall = traps.length > 0 ? trapFlagged / traps.length * 100 : 0  // % of traps we catch
  const useful = precision > 55 && recall > 20 ? "YES" : precision > 60 ? "MAYBE" : ""
  console.log(`  ${name.padEnd(40)} ${String(trapFlagged).padStart(8)} ${String(capFlagged).padStart(8)} ${(precision.toFixed(1)+"%").padStart(10)} ${(recall.toFixed(1)+"%").padStart(8)} ${useful.padStart(8)}`)
}

// --- 1C. COMBINED REVERSAL DETECTOR ---
console.log(`\n  --- 1C. COMBINED REVERSAL DETECTORS ---`)

function reversalScore(m) {
  let sc = 0
  if (m.volSpikePeak > 1.5) sc += 1
  if (Math.abs(m.vwapAtPeak) > 0.3) sc += 1
  if (m.bodyRatioShift < -0.1) sc += 1
  if (m.volShift > 1.2) sc += 1
  if (m.peakBkt <= 5) sc += 1
  if (Math.abs(m.gap) > 2.0) sc += 1
  return sc
}

console.log(`  ${"Rev Score".padEnd(12)} ${"Total".padStart(7)} ${"Traps".padStart(7)} ${"Capt".padStart(7)} ${"TrapRate".padStart(9)} ${"Action".padStart(20)}`)
for (let s = 0; s <= 6; s++) {
  const matching = moveProfiles.filter(m => reversalScore(m) >= s)
  const mTraps = matching.filter(m => m.isTrap).length
  const mCap = matching.filter(m => m.isCapturable).length
  const trapRate = matching.length > 0 ? mTraps / (mTraps + mCap) * 100 : 0
  const action = trapRate > 65 ? "EXIT / FLIP" : trapRate > 55 ? "TIGHTEN TP" : "HOLD"
  console.log(`  ${(">="+s).padEnd(12)} ${String(matching.length).padStart(7)} ${String(mTraps).padStart(7)} ${String(mCap).padStart(7)} ${(trapRate.toFixed(1)+"%").padStart(9)} ${action.padStart(20)}`)
}

// --- 1D. THE FLIP STRATEGY: when reversal detected, take opposite position ---
console.log(`\n  --- 1D. THE FLIP STRATEGY ---`)
console.log(`  When reversal score >= 4, EXIT original position and take OPPOSITE\n`)

let flipWins = 0, flipLosses = 0, flipTotalRet = 0, flipCount = 0

for (const m of moveProfiles) {
  if (reversalScore(m) < 4) continue
  if (!m.hitTP07) continue // must have initial move to flip

  // The flip: after TP hit at 0.7%, if reversal detected, enter opposite
  // Opposite return = maxAdv (how far it went against original direction)
  // But cap at reasonable TP for the flip too
  const flipReturn = Math.min(m.maxAdv, 1.0) - 0.3 // net after 0.3% cost/slippage
  if (flipReturn > 0) flipWins++
  else flipLosses++
  flipTotalRet += flipReturn
  flipCount++
}

if (flipCount > 0) {
  console.log(`  Flip trades: ${flipCount}`)
  console.log(`  Win rate: ${(flipWins/flipCount*100).toFixed(1)}%`)
  console.log(`  Avg flip return: ${(flipTotalRet/flipCount).toFixed(3)}%`)
  console.log(`  Total flip return: ${flipTotalRet.toFixed(1)}%`)
}

// --- 1E. TP TIMING: when does 0.7% TP hit? Can we detect "about to reverse"? ---
console.log(`\n  --- 1E. TP TIMING ANALYSIS ---`)
console.log(`  When does TP=0.7% get hit? Is there a warning window before reversal?\n`)

const tp07Moves = moveProfiles.filter(m => m.hitTP07)
console.log(`  Moves hitting TP=0.7%: ${tp07Moves.length} / ${moveProfiles.length} (${(tp07Moves.length/moveProfiles.length*100).toFixed(1)}%)`)

const tpTimeDist = {}
for (const m of tp07Moves) {
  const label = m.tp07Bucket <= 2 ? "bucket 1-2" : m.tp07Bucket <= 4 ? "bucket 3-4" : m.tp07Bucket <= 6 ? "bucket 5-6" : m.tp07Bucket <= 10 ? "bucket 7-10" : m.tp07Bucket <= 15 ? "bucket 11-15" : "bucket 16+"
  if (!tpTimeDist[label]) tpTimeDist[label] = { total: 0, trap: 0, cap: 0 }
  tpTimeDist[label].total++
  if (m.isTrap) tpTimeDist[label].trap++
  if (m.isCapturable) tpTimeDist[label].cap++
}

console.log(`  ${"TP hit at".padEnd(16)} ${"Count".padStart(7)} ${"Capturable".padStart(11)} ${"Trap".padStart(6)} ${"TrapRate".padStart(9)}`)
const tpOrder = ["bucket 1-2", "bucket 3-4", "bucket 5-6", "bucket 7-10", "bucket 11-15", "bucket 16+"]
for (const label of tpOrder) {
  const d = tpTimeDist[label]
  if (!d) continue
  const tr = (d.trap + d.cap) > 0 ? d.trap / (d.trap + d.cap) * 100 : 0
  console.log(`  ${label.padEnd(16)} ${String(d.total).padStart(7)} ${String(d.cap).padStart(11)} ${String(d.trap).padStart(6)} ${(tr.toFixed(1)+"%").padStart(9)}`)
}

console.log(`\n  Average bucket when TP=0.7% hit:`)
console.log(`    Capturable moves: bucket ${avgOf(capturables.filter(m=>m.hitTP07), m=>m.tp07Bucket).toFixed(1)}`)
console.log(`    Trap moves:       bucket ${avgOf(traps.filter(m=>m.hitTP07), m=>m.tp07Bucket).toFixed(1)}`)

// ============================================================================
// ANALYSIS 2: COMBINED STOCK-LEVEL + QUANT FRAMEWORK
// ============================================================================

console.log(`\n\n${"=".repeat(74)}`)
console.log(`  ANALYSIS 2: COMBINED TIER1 STOCKS + QUANT FRAMEWORK`)
console.log(`${"=".repeat(74)}`)

// Compare Tier1 vs Blacklist vs All through the quant lens
const groups = [
  ["ALL STOCKS", moveProfiles],
  ["TIER 1 ONLY", moveProfiles.filter(m => m.isTier1)],
  ["BLACKLIST ONLY", moveProfiles.filter(m => m.isBlacklist)],
  ["TIER 1 + F&O", moveProfiles.filter(m => m.isTier1 || m.isFno)],
  ["NON-TIER1, NON-BLACKLIST", moveProfiles.filter(m => !m.isTier1 && !m.isBlacklist)],
]

console.log(`\n  --- 2A. QUANT METRICS BY STOCK UNIVERSE ---\n`)
console.log(`  ${"Universe".padEnd(28)} ${"Moves".padStart(7)} ${"Cap%".padStart(6)} ${"Trap%".padStart(6)} ${"AvgMFE".padStart(8)} ${"AvgMAE".padStart(8)} ${"MFE/MAE".padStart(8)} ${"TP0.7%".padStart(8)} ${"TP1%".padStart(8)}`)
console.log(`  ${"-".repeat(88)}`)

for (const [label, grp] of groups) {
  if (grp.length < 10) continue
  const cap = grp.filter(m => m.isCapturable).length
  const trap = grp.filter(m => m.isTrap).length
  const tp07 = grp.filter(m => m.hitTP07).length
  const tp10 = grp.filter(m => m.hitTP10).length
  console.log(`  ${label.padEnd(28)} ${String(grp.length).padStart(7)} ${(cap/grp.length*100).toFixed(1).padStart(5)}% ${(trap/grp.length*100).toFixed(1).padStart(5)}% ${avgOf(grp,m=>m.maxFav).toFixed(2).padStart(7)}% ${avgOf(grp,m=>m.maxAdv).toFixed(2).padStart(7)}% ${(avgOf(grp,m=>m.maxAdv)>0?(avgOf(grp,m=>m.maxFav)/avgOf(grp,m=>m.maxAdv)).toFixed(2):"0").padStart(8)} ${(tp07/grp.length*100).toFixed(1).padStart(7)}% ${(tp10/grp.length*100).toFixed(1).padStart(7)}%`)
}

// --- 2B. TIER1 + QUANT RULES combined performance ---
console.log(`\n  --- 2B. TIER1 + QUANT RULES COMBINED PERFORMANCE ---`)
console.log(`  (Tier1 stocks + reversal score < 4 + TP=0.7%)\n`)

function simTrades(movesArr, tp, label) {
  const dailyPnl = {}
  let wins = 0, total = 0, totalRet = 0

  for (const m of movesArr) {
    if (!m.hitTP07 && tp <= 0.7) continue
    if (tp > 0.7 && !m.hitTP10 && tp <= 1.0) continue

    const ret = m.maxFav >= tp ? tp : -(Math.min(m.maxAdv, 1.5))
    const qty = Math.floor(25000 / 200) || 1
    const pnl = 200 * (ret / 100) * qty

    if (!dailyPnl[m.date]) dailyPnl[m.date] = { pnl: 0, n: 0 }
    if (dailyPnl[m.date].n < 20) { // max 20 positions
      dailyPnl[m.date].pnl += pnl
      dailyPnl[m.date].n++
      total++
      if (ret > 0) wins++
      totalRet += ret
    }
  }

  const days = Object.values(dailyPnl)
  const totalPnl = days.reduce((s, d) => s + d.pnl, 0)
  const avgDaily = totalPnl / nd
  const posDays = days.filter(d => d.pnl > 0).length
  const roc = avgDaily / 100000 * 100

  console.log(`  ${label.padEnd(45)} ${String(total).padStart(6)} ${(wins/total*100||0).toFixed(1).padStart(5)}% ${((totalRet/total||0)>=0?"+":"")+(totalRet/total||0).toFixed(2).padStart(0)}% Rs ${avgDaily.toFixed(0).padStart(6)}/day ${(roc.toFixed(2)+"%").padStart(7)} ROC ${posDays}/${nd} pos`)
}

console.log(`  ${"Strategy".padEnd(45)} ${"Trades".padStart(6)} ${"Win%".padStart(6)} ${"Avg%".padStart(6)} ${"Daily PnL".padStart(12)} ${"ROC".padStart(8)} ${"Days".padStart(8)}`)
console.log(`  ${"-".repeat(95)}`)

// All stocks, no filter
simTrades(moveProfiles.filter(m => m.hitTP07), 0.7, "ALL stocks, TP=0.7%, no filter")

// Tier1 only
simTrades(moveProfiles.filter(m => m.isTier1 && m.hitTP07), 0.7, "TIER1 only, TP=0.7%")

// Tier1 + low reversal risk
simTrades(moveProfiles.filter(m => m.isTier1 && reversalScore(m) < 4), 0.7, "TIER1 + reversal score < 4, TP=0.7%")

// Tier1 + low reversal + early TP hit
simTrades(moveProfiles.filter(m => m.isTier1 && reversalScore(m) < 3 && m.tp07Bucket <= 10), 0.7, "TIER1 + rev<3 + early TP, TP=0.7%")

// Tier1, TP=1%
simTrades(moveProfiles.filter(m => m.isTier1 && m.hitTP10), 1.0, "TIER1 only, TP=1.0%")

// Tier1 + low reversal, TP=1%
simTrades(moveProfiles.filter(m => m.isTier1 && reversalScore(m) < 4 && m.hitTP10), 1.0, "TIER1 + reversal score < 4, TP=1.0%")

// Blacklist for comparison
simTrades(moveProfiles.filter(m => m.isBlacklist && m.hitTP07), 0.7, "BLACKLIST only, TP=0.7% (don't do this)")

// --- 2C. TOP STOCKS: consistent across both frameworks ---
console.log(`\n  --- 2C. STOCKS THAT ARE GREAT IN BOTH FRAMEWORKS ---`)
console.log(`  (Tier1 from stock analysis + high capturable rate from quant framework)\n`)

const crossValidated = Object.entries(stockStats)
  .filter(([sym, d]) => d.tier1 && d.moves >= 5)
  .map(([sym, d]) => ({
    sym, moves: d.moves, traps: d.traps, capturable: d.capturable,
    capRate: d.capturable / d.moves * 100,
    trapRate: d.traps / d.moves * 100,
    netQuality: (d.capturable - d.traps) / d.moves * 100,
  }))
  .sort((a, b) => b.netQuality - a.netQuality)

const eliteStocks = crossValidated.filter(s => s.capRate >= 40 && s.trapRate <= 40)
const dangerStocks = crossValidated.filter(s => s.trapRate > 50)

console.log(`  Cross-validated Tier1 stocks: ${crossValidated.length}`)
console.log(`  ELITE (capRate >= 40%, trapRate <= 40%): ${eliteStocks.length}`)
console.log(`  DANGER (trapRate > 50% despite being Tier1): ${dangerStocks.length}`)

console.log(`\n  ELITE STOCKS (best of both worlds):`)
console.log(`  ${"Sym".padEnd(18)} ${"Moves".padStart(6)} ${"Cap%".padStart(6)} ${"Trap%".padStart(7)} ${"NetQ%".padStart(7)}`)
console.log(`  ${"-".repeat(47)}`)
for (const s of eliteStocks.slice(0, 40)) {
  console.log(`  ${s.sym.padEnd(18)} ${String(s.moves).padStart(6)} ${(s.capRate.toFixed(0)+"%").padStart(6)} ${(s.trapRate.toFixed(0)+"%").padStart(7)} ${((s.netQuality>=0?"+":"")+s.netQuality.toFixed(0)+"%").padStart(7)}`)
}

if (dangerStocks.length > 0) {
  console.log(`\n  DANGER STOCKS (Tier1 by returns, but high trap rate — fragile):`)
  for (const s of dangerStocks.slice(0, 20)) {
    console.log(`  ${s.sym.padEnd(18)} ${String(s.moves).padStart(6)} ${(s.capRate.toFixed(0)+"%").padStart(6)} ${(s.trapRate.toFixed(0)+"%").padStart(7)} — REMOVE FROM TIER1`)
  }
}

// --- 2D. SIMULATE ELITE-ONLY UNIVERSE ---
console.log(`\n  --- 2D. ELITE UNIVERSE SIMULATION ---`)
const eliteSet = new Set(eliteStocks.map(s => s.sym))
const eliteMoves = moveProfiles.filter(m => eliteSet.has(m.sym))
console.log(`  Elite universe: ${eliteSet.size} stocks, ${eliteMoves.length} moves\n`)

console.log(`  ${"Strategy".padEnd(45)} ${"Trades".padStart(6)} ${"Win%".padStart(6)} ${"Avg%".padStart(6)} ${"Daily PnL".padStart(12)} ${"ROC".padStart(8)} ${"Days".padStart(8)}`)
console.log(`  ${"-".repeat(95)}`)
simTrades(eliteMoves.filter(m => m.hitTP07), 0.7, "ELITE stocks, TP=0.7%")
simTrades(eliteMoves.filter(m => m.hitTP10), 1.0, "ELITE stocks, TP=1.0%")
simTrades(eliteMoves.filter(m => reversalScore(m) < 3 && m.hitTP07), 0.7, "ELITE + low reversal, TP=0.7%")
simTrades(eliteMoves.filter(m => reversalScore(m) < 3 && m.hitTP10), 1.0, "ELITE + low reversal, TP=1.0%")

// Save elite list
const output = {
  generatedAt: new Date().toISOString(),
  eliteStocks: eliteStocks.map(s => s.sym),
  dangerStocks: dangerStocks.map(s => s.sym),
  reversalRules: {
    volSpikePeakThreshold: 1.5,
    vwapDivergenceThreshold: 0.3,
    bodyRatioShiftThreshold: -0.1,
    volShiftThreshold: 1.2,
    peakBucketThreshold: 5,
    gapThreshold: 2.0,
    exitFlipScoreThreshold: 4,
  }
}
writeFileSync("data/quant-results/elite-stocks.json", JSON.stringify(output, null, 2))
console.log(`\n  Results saved to data/quant-results/elite-stocks.json`)
console.log(`  Elite: ${eliteStocks.length} stocks | Danger: ${dangerStocks.length} stocks`)
console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
