#!/usr/bin/env bun
// ============================================================================
// OBSERVE → PICK ANALYSIS
//
// Phase 1 (bucket 1 to X): Just WATCH all stocks. Collect:
//   - Price movement from open
//   - Volume accumulation
//   - VWAP position
//   - Candle body ratios
//   - Momentum consistency (how many green/red candles)
//   - Volume acceleration
//   - Move magnitude and direction
//
// Phase 2 (at bucket X): RANK all stocks using observed data, pick top N
//
// Phase 3 (bucket X+1 onwards): Enter selected stocks, simulate TP/SL/TIME exits
//
// Tests X = 2,3,4,5,6,7,8,9,10,11,12,13
// Tests different ranking/filter strategies
// ============================================================================

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()

const CAPITAL = 50000
const PER_TRADE = 25000
const MAX_POS = Math.floor(CAPITAL * 5 / PER_TRADE) // 10
const OBSERVE_BUCKETS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15]
const TP_LEVELS = [0.5, 0.7, 1.0, 1.5, 2.0]
const SL_PCT = 1.5 // universal SL for this analysis

console.log(`\n${"█".repeat(74)}`)
console.log(`  OBSERVE → PICK ANALYSIS`)
console.log(`  Watch stocks for X buckets, then pick best, then trade`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)

// ── Stream all stock-day data into memory ──
console.log("Loading data...")
const dayStocks = {} // date -> [ { sym, dayOpen, gap, buckets } ]
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 5) continue
  if (!dayStocks[date]) dayStocks[date] = []
  dayStocks[date].push({ sym: symbol, dayOpen, gap: gapPct, buckets })
  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayStocks).sort()
const nd = allDates.length
console.log(`${lc} lines → ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ── Observe a stock during buckets 1..X, return features ──
function observeStock(buckets, dayOpen, gap, observeUntil) {
  const sorted = buckets.filter(b => b.b >= 1 && b.b <= observeUntil).sort((a, b) => a.b - b.b)
  if (sorted.length < 2 || dayOpen <= 0) return null

  const first = sorted[0]
  const last = sorted[sorted.length - 1]

  // Price movement
  const movePct = (last.c - dayOpen) / dayOpen * 100
  const absMove = Math.abs(movePct)
  const dir = movePct > 0 ? "BUY" : "SELL"

  // High/low during observation
  let maxHigh = 0, minLow = 99999
  for (const b of sorted) {
    if (b.h > maxHigh) maxHigh = b.h
    if (b.l < minLow) minLow = b.l
  }
  const rangePct = dayOpen > 0 ? (maxHigh - minLow) / dayOpen * 100 : 0

  // Volume
  const totalVol = sorted.reduce((s, b) => s + b.v, 0)
  const avgVolRate = sorted.reduce((s, b) => s + (b.vr || 0), 0) / sorted.length
  // Volume trend: is volume increasing?
  const halfIdx = Math.floor(sorted.length / 2)
  const firstHalfVol = sorted.slice(0, halfIdx).reduce((s, b) => s + b.v, 0)
  const secondHalfVol = sorted.slice(halfIdx).reduce((s, b) => s + b.v, 0)
  const volAccel = firstHalfVol > 0 ? secondHalfVol / firstHalfVol : 1

  // VWAP position
  const vwapPct = last.vw > 0 ? (last.c - last.vw) / last.vw * 100 : 0
  const aboveVwap = dir === "BUY" ? vwapPct > 0 : vwapPct < 0 // price on right side of VWAP

  // Momentum consistency: how many buckets moved in the signal direction?
  let consistentBuckets = 0
  for (let i = 1; i < sorted.length; i++) {
    const change = sorted[i].c - sorted[i-1].c
    if ((dir === "BUY" && change > 0) || (dir === "SELL" && change < 0)) consistentBuckets++
  }
  const consistency = sorted.length > 1 ? consistentBuckets / (sorted.length - 1) : 0

  // Body ratio average (conviction)
  const avgBody = sorted.reduce((s, b) => s + (b.br || 0), 0) / sorted.length

  // Move speed: move per bucket
  const moveSpeed = absMove / sorted.length

  // Last candle direction alignment
  const lastCandleAligned = (dir === "BUY" && last.c > last.o) || (dir === "SELL" && last.c < last.o)

  // Entry price = last observed close
  const entryPrice = last.c

  // Composite score (higher = better candidate)
  let score = 0
  if (absMove >= 0.3) score += 1
  if (absMove >= 0.6) score += 1
  if (absMove >= 1.0) score += 1
  if (totalVol >= 500) score += 1
  if (totalVol >= 2000) score += 1
  if (avgVolRate >= 50) score += 1
  if (aboveVwap) score += 1
  if (consistency >= 0.5) score += 1
  if (consistency >= 0.7) score += 1
  if (avgBody >= 0.4) score += 1
  if (avgBody >= 0.6) score += 1
  if (lastCandleAligned) score += 1
  if (volAccel >= 1.2) score += 1
  if (moveSpeed >= 0.15) score += 1
  if (Math.abs(gap) < 3) score += 1 // not extreme gap

  return {
    dir, movePct, absMove, rangePct, totalVol, avgVolRate, volAccel,
    vwapPct, aboveVwap, consistency, avgBody, moveSpeed, lastCandleAligned,
    entryPrice, entryBucket: observeUntil, score, gap,
  }
}

// ── Simulate exit from entry point ──
function simulateExit(entryPrice, dir, entryBucket, allBuckets, tpPct, slPct, hardExitBuy, hardExitSell) {
  const ds = dir === "BUY" ? 1 : -1
  const tpPrice = tpPct > 0 ? entryPrice * (1 + ds * tpPct / 100) : 0
  const slPrice = slPct > 0 ? entryPrice * (1 - ds * slPct / 100) : 0
  const exitBkt = dir === "SELL" ? hardExitSell : hardExitBuy
  const sorted = allBuckets.sort((a, b) => a.b - b.b)
  let mfe = 0, mae = 0

  for (const b of sorted) {
    if (b.b <= entryBucket) continue
    const fav = ds > 0 ? (b.h - entryPrice) / entryPrice * 100 : (entryPrice - b.l) / entryPrice * 100
    const adv = ds > 0 ? (entryPrice - b.l) / entryPrice * 100 : (b.h - entryPrice) / entryPrice * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv

    const tpHit = tpPct > 0.001 && (ds > 0 ? b.c >= tpPrice : b.c <= tpPrice)
    const slHit = slPct > 0.001 && (ds > 0 ? b.c <= slPrice : b.c >= slPrice)
    const timeHit = b.b >= exitBkt
    if (tpHit) return { xr: "TP", ret: tpPct, xb: b.b, mfe, mae }
    if (slHit) { const r = ds>0?(b.c-entryPrice)/entryPrice*100:(entryPrice-b.c)/entryPrice*100; return { xr: "SL", ret: r, xb: b.b, mfe, mae } }
    if (timeHit) { const r = ds>0?(b.c-entryPrice)/entryPrice*100:(entryPrice-b.c)/entryPrice*100; return { xr: "TIME", ret: r, xb: b.b, mfe, mae } }
  }
  const last = sorted[sorted.length - 1]
  const r = ds>0?(last.c-entryPrice)/entryPrice*100:(entryPrice-last.c)/entryPrice*100
  return { xr: "TIME", ret: r, xb: last?.b || 0, mfe, mae }
}

// ── Different ranking strategies ──
const STRATEGIES = {
  "score": (a, b) => b.score - a.score || b.absMove - a.absMove,
  "move%": (a, b) => b.absMove - a.absMove,
  "momentum": (a, b) => (b.consistency * b.absMove) - (a.consistency * a.absMove),
  "vol+move": (a, b) => (b.absMove * Math.log(b.totalVol + 1)) - (a.absMove * Math.log(a.totalVol + 1)),
  "speed": (a, b) => b.moveSpeed - a.moveSpeed,
  "vwap+move": (a, b) => {
    const aScore = a.absMove * (a.aboveVwap ? 1.5 : 0.8) * (a.consistency + 0.3)
    const bScore = b.absMove * (b.aboveVwap ? 1.5 : 0.8) * (b.consistency + 0.3)
    return bScore - aScore
  },
  "composite": (a, b) => {
    // Best of all: move × consistency × volume × vwap alignment × body
    const aS = a.absMove * (0.5 + a.consistency) * Math.log(a.totalVol + 10) * (a.aboveVwap ? 1.3 : 0.7) * (0.5 + a.avgBody)
    const bS = b.absMove * (0.5 + b.consistency) * Math.log(b.totalVol + 10) * (b.aboveVwap ? 1.3 : 0.7) * (0.5 + b.avgBody)
    return bS - aS
  },
}

// ============================================================================
// PART 1: Test each observe window × TP × ranking strategy
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: OBSERVE WINDOW × TP × RANKING STRATEGY`)
console.log(`  Capital: Rs ${CAPITAL} | Per trade: Rs ${PER_TRADE} | Max: ${MAX_POS} | SL: ${SL_PCT}%`)
console.log(`${"=".repeat(90)}\n`)

// First, find the best strategy for TP=0.7% across all buckets
console.log(`  --- Finding best ranking strategy (TP=0.7%) ---\n`)
console.log(`  ${"Strategy".padEnd(14)} ${"B2".padStart(8)} ${"B3".padStart(8)} ${"B4".padStart(8)} ${"B5".padStart(8)} ${"B7".padStart(8)} ${"B10".padStart(8)} ${"B13".padStart(8)}`)
console.log(`  ${"-".repeat(72)}`)

const stratResults = {}

for (const [stratName, sortFn] of Object.entries(STRATEGIES)) {
  const rowVals = []
  for (const obsEnd of [2, 3, 4, 5, 7, 10, 13]) {
    let wins = 0, trades = 0, pnlSum = 0

    for (const date of allDates) {
      const stocks = dayStocks[date]
      const candidates = []
      for (const sd of stocks) {
        const obs = observeStock(sd.buckets, sd.dayOpen, sd.gap, obsEnd)
        if (obs && obs.absMove >= 0.15) candidates.push({ ...obs, _buckets: sd.buckets, sym: sd.sym })
      }
      candidates.sort(sortFn)
      const selected = candidates.slice(0, MAX_POS)

      for (const c of selected) {
        const qty = Math.max(Math.floor(PER_TRADE / c.entryPrice), 1)
        const exit = simulateExit(c.entryPrice, c.dir, c.entryBucket, c._buckets, 0.7, SL_PCT, 35, 71)
        const pnl = c.entryPrice * (exit.ret / 100) * qty
        pnlSum += pnl; trades++
        if (exit.ret > 0) wins++
      }
    }
    const roc = (pnlSum / nd) / CAPITAL * 100
    rowVals.push(roc)
    stratResults[`${stratName}_B${obsEnd}_0.7`] = { wins, trades, pnlSum, roc, wr: trades > 0 ? wins/trades*100 : 0 }
  }
  console.log(`  ${stratName.padEnd(14)} ${rowVals.map(r => (r >= 0 ? "+" : "") + r.toFixed(2) + "%").map(s => s.padStart(8)).join("")}`)
}

// Find best strategy
let bestStrat = "composite", bestStratROC = -999
for (const [stratName] of Object.entries(STRATEGIES)) {
  const avgROC = [2,3,4,5,7,10,13].reduce((s, b) => s + (stratResults[`${stratName}_B${b}_0.7`]?.roc || -999), 0) / 7
  if (avgROC > bestStratROC) { bestStratROC = avgROC; bestStrat = stratName }
}
console.log(`\n  Best strategy: "${bestStrat}" (avg ROC across buckets: ${bestStratROC.toFixed(2)}%)\n`)

// ============================================================================
// PART 2: Full grid with best strategy
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 2: FULL RESULTS — Strategy: "${bestStrat}"`)
console.log(`${"=".repeat(90)}\n`)

const sortFn = STRATEGIES[bestStrat]
const fullResults = {}

console.log(`  ${"Obs".padEnd(4)} ${"TP%".padEnd(4)} ${"Cands".padStart(6)} ${"Sel".padStart(4)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE%".padStart(7)} ${"MAE%".padStart(7)} ${"Ratio".padStart(6)} ${"TPHit".padStart(6)} ${"ExBkt".padStart(6)} ${"Streak".padStart(7)}`)
console.log(`  ${"-".repeat(100)}`)

for (const obsEnd of OBSERVE_BUCKETS) {
  for (const tp of TP_LEVELS) {
    let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0, tpH = 0, exBkt = 0
    const dailyPnls = []
    let totalCands = 0

    for (const date of allDates) {
      const stocks = dayStocks[date]
      const candidates = []
      for (const sd of stocks) {
        const obs = observeStock(sd.buckets, sd.dayOpen, sd.gap, obsEnd)
        if (obs && obs.absMove >= 0.15) candidates.push({ ...obs, _buckets: sd.buckets, sym: sd.sym })
      }
      totalCands += candidates.length
      candidates.sort(sortFn)
      const selected = candidates.slice(0, MAX_POS)

      let dayPnl = 0
      for (const c of selected) {
        const qty = Math.max(Math.floor(PER_TRADE / c.entryPrice), 1)
        const exit = simulateExit(c.entryPrice, c.dir, c.entryBucket, c._buckets, tp, SL_PCT, 35, 71)
        const pnl = c.entryPrice * (exit.ret / 100) * qty
        dayPnl += pnl; pnlSum += pnl; trades++
        if (exit.ret > 0) wins++
        if (exit.xr === "TP") tpH++
        mfeS += exit.mfe; maeS += exit.mae; exBkt += exit.xb
      }
      dailyPnls.push(dayPnl)
    }

    const wr = trades > 0 ? wins/trades*100 : 0
    const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
    const pos = dailyPnls.filter(p => p > 0).length
    const avgMfe = trades>0?mfeS/trades:0, avgMae = trades>0?maeS/trades:0
    const ratio = avgMae>0?avgMfe/avgMae:0
    const tpRate = trades>0?tpH/trades*100:0
    const avgEx = trades>0?exBkt/trades:0
    let maxRed = 0, streak = 0
    for (const p of dailyPnls) { if (p<=0){streak++;if(streak>maxRed)maxRed=streak}else streak=0 }
    const avgCands = totalCands / nd

    fullResults[`B${obsEnd}_${tp}`] = { obsEnd, tp, avgCands, wr, avgD, roc, pos, avgMfe, avgMae, ratio, tpRate, avgEx, maxRed, trades, dailyPnls }

    const best = TP_LEVELS.every(t => !fullResults[`B${obsEnd}_${t}`] || roc >= fullResults[`B${obsEnd}_${t}`].roc)
    console.log(`  B${String(obsEnd).padEnd(2)} ${tp.toFixed(1).padEnd(4)} ${avgCands.toFixed(0).padStart(6)} ${(trades/nd).toFixed(0).padStart(4)} ${(wr.toFixed(1)+"%").padStart(7)} ${("Rs "+avgD.toFixed(0)).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${(pos+"/"+nd).padStart(6)} ${(avgMfe.toFixed(2)+"%").padStart(7)} ${(avgMae.toFixed(2)+"%").padStart(7)} ${ratio.toFixed(2).padStart(6)} ${(tpRate.toFixed(0)+"%").padStart(6)} ${avgEx.toFixed(0).padStart(6)} ${(maxRed+"d").padStart(7)}${best?" <<<":""}`)
  }
  console.log()
}

// ============================================================================
// PART 3: WHAT THE TOP-10 LOOK LIKE AT EACH BUCKET
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 3: CHARACTERISTICS OF TOP-10 PICKS AT EACH OBSERVATION WINDOW`)
console.log(`${"=".repeat(90)}\n`)

console.log(`  ${"Obs".padEnd(4)} ${"AvgMove%".padStart(9)} ${"AvgVol".padStart(8)} ${"AvgVR".padStart(7)} ${"Consist".padStart(8)} ${"AbvVWAP".padStart(8)} ${"AvgBody".padStart(8)} ${"Speed".padStart(7)} ${"BUY%".padStart(6)} ${"AvgPrice".padStart(9)} ${"AvgScore".padStart(9)}`)
console.log(`  ${"-".repeat(90)}`)

for (const obsEnd of OBSERVE_BUCKETS) {
  let sumMove=0, sumVol=0, sumVR=0, sumCons=0, sumVwap=0, sumBody=0, sumSpeed=0, buys=0, sumPrice=0, sumScore=0, n=0

  for (const date of allDates) {
    const stocks = dayStocks[date]
    const candidates = []
    for (const sd of stocks) {
      const obs = observeStock(sd.buckets, sd.dayOpen, sd.gap, obsEnd)
      if (obs && obs.absMove >= 0.15) candidates.push(obs)
    }
    candidates.sort(sortFn)
    for (const c of candidates.slice(0, MAX_POS)) {
      sumMove += c.absMove; sumVol += c.totalVol; sumVR += c.avgVolRate
      sumCons += c.consistency; sumVwap += c.aboveVwap ? 1 : 0
      sumBody += c.avgBody; sumSpeed += c.moveSpeed
      if (c.dir === "BUY") buys++
      sumPrice += c.entryPrice; sumScore += c.score; n++
    }
  }

  if (n === 0) continue
  console.log(`  B${String(obsEnd).padEnd(2)} ${(sumMove/n).toFixed(2).padStart(8)}% ${(sumVol/n).toFixed(0).padStart(8)} ${(sumVR/n).toFixed(0).padStart(7)} ${(sumCons/n*100).toFixed(0).padStart(7)}% ${(sumVwap/n*100).toFixed(0).padStart(7)}% ${(sumBody/n).toFixed(2).padStart(8)} ${(sumSpeed/n).toFixed(3).padStart(7)} ${(buys/n*100).toFixed(0).padStart(5)}% ${("Rs "+(sumPrice/n).toFixed(0)).padStart(9)} ${(sumScore/n).toFixed(1).padStart(9)}`)
}

// ============================================================================
// PART 4: BEST BUCKET PER TP
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 4: BEST OBSERVATION WINDOW PER TP`)
console.log(`${"=".repeat(90)}\n`)

for (const tp of TP_LEVELS) {
  let bestB = 2, bestROC = -999
  for (const b of OBSERVE_BUCKETS) {
    const r = fullResults[`B${b}_${tp}`]
    if (r && r.roc > bestROC) { bestROC = r.roc; bestB = b }
  }
  const r = fullResults[`B${bestB}_${tp}`]
  if (!r) continue
  console.log(`  TP=${tp.toFixed(1)}% → Best at Bucket ${bestB} (observe ${bestB} min) | ${r.wr.toFixed(1)}% win | Rs ${r.avgD.toFixed(0)}/day | ${r.roc.toFixed(2)}% ROC | ${r.pos}/${nd} pos | MFE/MAE=${r.ratio.toFixed(2)}`)
}

// ============================================================================
// PART 5: DAILY CONSISTENCY
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 5: DAILY CONSISTENCY (best TP per bucket)`)
console.log(`${"=".repeat(90)}\n`)

console.log(`  ${"Obs".padEnd(4)} ${"BestTP".padStart(7)} ${"Green".padStart(6)} ${"Red".padStart(5)} ${"G%".padStart(5)} ${"AvgGrn".padStart(9)} ${"AvgRed".padStart(9)} ${"MaxRed".padStart(8)} ${"Streak".padStart(7)} ${"Sharpe".padStart(7)}`)
console.log(`  ${"-".repeat(70)}`)

for (const obsEnd of OBSERVE_BUCKETS) {
  let bestTP = 0.7, bestROC = -999
  for (const tp of TP_LEVELS) {
    const r = fullResults[`B${obsEnd}_${tp}`]
    if (r && r.roc > bestROC) { bestROC = r.roc; bestTP = tp }
  }
  const r = fullResults[`B${obsEnd}_${bestTP}`]
  if (!r) continue
  const dp = r.dailyPnls
  const green = dp.filter(p => p > 0), red = dp.filter(p => p <= 0)
  const avgG = green.length ? green.reduce((s,p)=>s+p,0)/green.length : 0
  const avgR = red.length ? red.reduce((s,p)=>s+p,0)/red.length : 0
  const maxR = red.length ? Math.min(...red) : 0
  const mean = dp.reduce((s,p)=>s+p,0)/dp.length
  const std = Math.sqrt(dp.reduce((s,p)=>s+(p-mean)**2,0)/dp.length)
  const sharpe = std > 0 ? mean / std : 0

  console.log(`  B${String(obsEnd).padEnd(2)} ${(bestTP+"%").padStart(7)} ${green.length.toString().padStart(6)} ${red.length.toString().padStart(5)} ${(green.length/nd*100).toFixed(0).padStart(4)}% ${("Rs "+avgG.toFixed(0)).padStart(9)} ${("Rs "+avgR.toFixed(0)).padStart(9)} ${("Rs "+maxR.toFixed(0)).padStart(8)} ${(r.maxRed+"d").padStart(7)} ${sharpe.toFixed(2).padStart(7)}`)
}

// ============================================================================
// PART 6: OVERALL RECOMMENDATION
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  RECOMMENDATION`)
console.log(`${"=".repeat(90)}\n`)

let bestKey = "", bestROC = -999
for (const k of Object.keys(fullResults)) {
  if (fullResults[k].roc > bestROC) { bestROC = fullResults[k].roc; bestKey = k }
}
const best = fullResults[bestKey]
if (best) {
  console.log(`  BEST SETUP: Observe ${best.obsEnd} minutes (bucket ${best.obsEnd}), then pick top ${MAX_POS}, TP=${best.tp}%`)
  console.log(`  Strategy: "${bestStrat}" ranking`)
  console.log(`  ${best.wr.toFixed(1)}% win | Rs ${best.avgD.toFixed(0)}/day | ${best.roc.toFixed(2)}% daily ROC`)
  console.log(`  ${best.pos}/${nd} positive days | MFE/MAE: ${best.ratio.toFixed(2)} | Max red streak: ${best.maxRed}d`)
  console.log(`  ${best.avgCands.toFixed(0)} candidates observed/day → ${(best.trades/nd).toFixed(0)} selected`)
  console.log(`  Monthly: Rs ${(best.avgD*22).toFixed(0)} (~${(best.roc*22).toFixed(1)}% monthly)`)
}

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
