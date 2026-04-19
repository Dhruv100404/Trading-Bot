#!/usr/bin/env bun
// ============================================================================
// GAP + DAY MOVEMENT → AFTERNOON PATTERN ANALYSIS
//
// For each stock-day, we KNOW at 2PM:
// 1. Gap direction and magnitude (known at 9:15)
// 2. How the stock moved from 9:15 to 2PM (5 hours of data)
// 3. Volume profile through the day
// 4. VWAP position at 2PM
// 5. Whether morning trend continued or reversed
//
// Question: Which combinations PREDICT afternoon (2PM-3:25PM) movement?
// ============================================================================

import { createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()
const CAPITAL = 50000, PER_TRADE = 10000, MAX_POS = 12

const PM2 = 286, PM_EXIT = 371 // 2:00 PM entry, 3:25 PM exit

console.log(`\n${"█".repeat(74)}`)
console.log(`  GAP + DAY MOVEMENT → AFTERNOON PATTERN`)
console.log(`  Everything known at 2PM. No future data.`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)

const dayData = {}
let lc = 0
const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol: sym, date, dayOpen, gapPct: gap, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 50) continue

  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const pm2Snap = sorted.find(b => b.b >= PM2 && b.b <= PM2 + 2)
  if (!pm2Snap) continue
  const pm2Price = pm2Snap.c
  if (pm2Price <= 0) continue

  // ══ ALL FEATURES KNOWN AT 2PM ══

  // Gap
  const gapDir = gap > 0.3 ? "UP" : gap < -0.3 ? "DOWN" : "FLAT"
  const absGap = Math.abs(gap)

  // Full day move by 2PM
  const dayMove = (pm2Price - dayOpen) / dayOpen * 100
  const absDayMove = Math.abs(dayMove)
  const dayDir = dayMove > 0.3 ? "UP" : dayMove < -0.3 ? "DOWN" : "FLAT"

  // Gap vs Day move relationship
  const gapContinued = (gap > 0.3 && dayMove > 0.3) || (gap < -0.3 && dayMove < -0.3)
  const gapReversed = (gap > 0.3 && dayMove < -0.3) || (gap < -0.3 && dayMove > 0.3)
  const gapFilled = (gap > 0.3 && dayMove < 0) || (gap < -0.3 && dayMove > 0)

  // Morning session (9:15-11:30 = bucket 1-135)
  const mornSnaps = sorted.filter(b => b.b >= 1 && b.b <= 135)
  const mornHigh = mornSnaps.reduce((mx, b) => Math.max(mx, b.h), 0)
  const mornLow = mornSnaps.reduce((mn, b) => Math.min(mn, b.l), 99999)
  const mornRange = dayOpen > 0 ? (mornHigh - mornLow) / dayOpen * 100 : 0
  const mornClose = mornSnaps.length > 0 ? mornSnaps[mornSnaps.length - 1].c : dayOpen
  const mornMove = (mornClose - dayOpen) / dayOpen * 100

  // Midday session (11:30-2:00 = bucket 136-285)
  const midSnaps = sorted.filter(b => b.b >= 136 && b.b <= 285)
  const midMove = midSnaps.length > 0 ? (midSnaps[midSnaps.length-1].c - mornClose) / mornClose * 100 : 0
  const midTrendSame = (mornMove > 0 && midMove > 0) || (mornMove < 0 && midMove < 0)

  // Volume profile
  const mornVol = mornSnaps.reduce((s, b) => s + b.v, 0)
  const midVol = midSnaps.reduce((s, b) => s + b.v, 0)
  const volShiftMidMorn = mornVol > 0 ? midVol / mornVol : 1

  // PM2 VWAP position
  const vwap2pm = pm2Snap.vw || 0
  const priceVsVwap = vwap2pm > 0 ? (pm2Price - vwap2pm) / vwap2pm * 100 : 0
  const above2pmVwap = priceVsVwap > 0.1 ? 1 : priceVsVwap < -0.1 ? -1 : 0

  // Price position relative to day's range
  const dayHigh = sorted.filter(b => b.b < PM2).reduce((mx, b) => Math.max(mx, b.h), 0)
  const dayLow = sorted.filter(b => b.b < PM2).reduce((mn, b) => Math.min(mn, b.l), 99999)
  const dayRange = dayHigh - dayLow
  const priceInRange = dayRange > 0 ? (pm2Price - dayLow) / dayRange : 0.5 // 0=at low, 1=at high

  // VR at 2PM
  const vr2pm = pm2Snap.vr || 0

  // ══ AFTERNOON OUTCOME (2PM-3:25PM, strictly after entry) ══
  const pmSnaps = sorted.filter(b => b.b > PM2 && b.b <= PM_EXIT)
  if (pmSnaps.length < 5) continue

  // Test BOTH directions and pick better one (to find which direction to trade)
  let mfeBuy = 0, maeBuy = 0, mfeSell = 0, maeSell = 0
  for (const b of pmSnaps) {
    const up = (b.h - pm2Price) / pm2Price * 100
    const down = (pm2Price - b.l) / pm2Price * 100
    if (up > mfeBuy) mfeBuy = up
    if (down > maeBuy) maeBuy = down
    if (down > mfeSell) mfeSell = down
    if (up > maeSell) maeSell = up
  }

  // Direction for afternoon = continue the day's trend
  const pmDir = dayMove > 0 ? 1 : -1
  const mfe = pmDir > 0 ? mfeBuy : mfeSell
  const mae = pmDir > 0 ? maeBuy : maeSell

  // Exit price at 3:25PM
  const exitSnap = pmSnaps[pmSnaps.length - 1]
  const timeRet = pmDir > 0 ? (exitSnap.c - pm2Price) / pm2Price * 100 : (pm2Price - exitSnap.c) / pm2Price * 100

  if (!dayData[date]) dayData[date] = []
  dayData[date].push({
    sym, ep: pm2Price, date, pmDir,
    f: {
      gap, absGap, gapDir, dayMove, absDayMove, dayDir,
      gapContinued: gapContinued ? 1 : 0,
      gapReversed: gapReversed ? 1 : 0,
      gapFilled: gapFilled ? 1 : 0,
      mornMove, mornRange, midMove,
      midTrendSame: midTrendSame ? 1 : 0,
      volShiftMidMorn, vr2pm,
      priceVsVwap, above2pmVwap,
      priceInRange,
    },
    mfe, mae, timeRet,
    tp03: mfe >= 0.3, tp05: mfe >= 0.5, tp07: mfe >= 0.7,
  })

  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayData).sort()
const nd = allDates.length
const all = Object.values(dayData).flat()
console.log(`${lc} → ${all.length} signals, ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s`)
console.log(`Baseline: tp05=${(all.filter(s=>s.tp05).length/all.length*100).toFixed(1)}% tp07=${(all.filter(s=>s.tp07).length/all.length*100).toFixed(1)}%\n`)

// ════════════════════════════════════════════════════════════════
// PART 1: GAP DIRECTION + DAY MOVE DIRECTION → AFTERNOON
// ════════════════════════════════════════════════════════════════

console.log(`${"=".repeat(80)}`)
console.log(`  PART 1: GAP + DAY MOVE → AFTERNOON TP HIT RATE`)
console.log(`${"=".repeat(80)}\n`)

const combos = {}
for (const s of all) {
  const key = `Gap:${s.f.gapDir} Day:${s.f.dayDir}`
  if (!combos[key]) combos[key] = { n: 0, tp03: 0, tp05: 0, tp07: 0, mfeS: 0, maeS: 0, retS: 0 }
  combos[key].n++
  if (s.tp03) combos[key].tp03++
  if (s.tp05) combos[key].tp05++
  if (s.tp07) combos[key].tp07++
  combos[key].mfeS += s.mfe
  combos[key].maeS += s.mae
  combos[key].retS += s.timeRet
}

console.log(`  ${"Pattern".padEnd(25)} ${"N".padStart(7)} ${"TP03%".padStart(7)} ${"TP05%".padStart(7)} ${"TP07%".padStart(7)} ${"AvgMFE".padStart(8)} ${"AvgMAE".padStart(8)} ${"Ratio".padStart(6)} ${"AvgRet".padStart(8)}`)
console.log(`  ${"-".repeat(85)}`)

for (const [key, v] of Object.entries(combos).sort((a, b) => b[1].tp05 / b[1].n - a[1].tp05 / a[1].n)) {
  const r = v.maeS > 0 ? v.mfeS / v.maeS : 0
  console.log(`  ${key.padEnd(25)} ${v.n.toString().padStart(7)} ${(v.tp03/v.n*100).toFixed(0).padStart(6)}% ${(v.tp05/v.n*100).toFixed(0).padStart(6)}% ${(v.tp07/v.n*100).toFixed(0).padStart(6)}% ${(v.mfeS/v.n).toFixed(2).padStart(7)}% ${(v.maeS/v.n).toFixed(2).padStart(7)}% ${r.toFixed(2).padStart(6)} ${(v.retS/v.n>=0?"+":"")+(v.retS/v.n).toFixed(2).padStart(0).padStart(8)}%`)
}

// ════════════════════════════════════════════════════════════════
// PART 2: DETAILED GAP MAGNITUDE BUCKETS
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 2: GAP MAGNITUDE + DAY MOVE MAGNITUDE → AFTERNOON`)
console.log(`${"=".repeat(80)}\n`)

const gapBuckets = [
  ["Gap DN >3%", s => s.f.gap < -3],
  ["Gap DN 1-3%", s => s.f.gap >= -3 && s.f.gap < -1],
  ["Gap DN 0.3-1%", s => s.f.gap >= -1 && s.f.gap < -0.3],
  ["Flat gap", s => Math.abs(s.f.gap) <= 0.3],
  ["Gap UP 0.3-1%", s => s.f.gap > 0.3 && s.f.gap <= 1],
  ["Gap UP 1-3%", s => s.f.gap > 1 && s.f.gap <= 3],
  ["Gap UP >3%", s => s.f.gap > 3],
]

const dayMoveBuckets = [
  ["Day DN >3%", s => s.f.dayMove < -3],
  ["Day DN 1-3%", s => s.f.dayMove >= -3 && s.f.dayMove < -1],
  ["Day DN 0.3-1%", s => s.f.dayMove >= -1 && s.f.dayMove < -0.3],
  ["Day Flat", s => Math.abs(s.f.dayMove) <= 0.3],
  ["Day UP 0.3-1%", s => s.f.dayMove > 0.3 && s.f.dayMove <= 1],
  ["Day UP 1-3%", s => s.f.dayMove > 1 && s.f.dayMove <= 3],
  ["Day UP >3%", s => s.f.dayMove > 3],
]

console.log(`  ${"Gap × DayMove".padEnd(30)} ${"N".padStart(6)} ${"TP05".padStart(6)} ${"TP07".padStart(6)} ${"MFE".padStart(6)} ${"MAE".padStart(6)} ${"Ratio".padStart(6)} ${"NetRet".padStart(7)}`)
console.log(`  ${"-".repeat(75)}`)

for (const [gLabel, gFn] of gapBuckets) {
  for (const [dLabel, dFn] of dayMoveBuckets) {
    const grp = all.filter(s => gFn(s) && dFn(s))
    if (grp.length < 50) continue
    const tp05 = grp.filter(s => s.tp05).length / grp.length * 100
    const tp07 = grp.filter(s => s.tp07).length / grp.length * 100
    const avgMfe = grp.reduce((s, t) => s + t.mfe, 0) / grp.length
    const avgMae = grp.reduce((s, t) => s + t.mae, 0) / grp.length
    const avgRet = grp.reduce((s, t) => s + t.timeRet, 0) / grp.length
    const ratio = avgMae > 0 ? avgMfe / avgMae : 0
    const mark = tp07 > 55 ? " <<<" : tp07 < 30 ? " !!!" : ""
    console.log(`  ${(gLabel+" + "+dLabel).padEnd(30)} ${grp.length.toString().padStart(6)} ${(tp05.toFixed(0)+"%").padStart(6)} ${(tp07.toFixed(0)+"%").padStart(6)} ${avgMfe.toFixed(2).padStart(6)} ${avgMae.toFixed(2).padStart(6)} ${ratio.toFixed(2).padStart(6)} ${(avgRet>=0?"+":"")+avgRet.toFixed(2)+"%".padStart(0).padStart(7)}${mark}`)
  }
}

// ════════════════════════════════════════════════════════════════
// PART 3: GAP CONTINUATION vs REVERSAL vs FILL
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 3: GAP BEHAVIOUR BY 2PM → AFTERNOON PATTERN`)
console.log(`${"=".repeat(80)}\n`)

const behaviors = [
  ["Gap continued (same dir by 2PM)", s => s.f.gapContinued, "Continue gap dir"],
  ["Gap reversed (opposite by 2PM)", s => s.f.gapReversed, "Continue reversal"],
  ["Gap filled (moved toward open)", s => s.f.gapFilled, "Continue fill dir"],
  ["Midday continued morning", s => s.f.midTrendSame, "Continue full-day trend"],
  ["Midday reversed morning", s => !s.f.midTrendSame && Math.abs(s.f.mornMove) > 0.3, "Continue mid reversal"],
]

console.log(`  ${"Behavior".padEnd(40)} ${"N".padStart(7)} ${"TP05%".padStart(7)} ${"TP07%".padStart(7)} ${"MFE".padStart(7)} ${"MAE".padStart(7)} ${"Ratio".padStart(6)} ${"Direction"}`)
console.log(`  ${"-".repeat(90)}`)

for (const [label, fn, tradDir] of behaviors) {
  const grp = all.filter(fn)
  if (grp.length < 100) continue
  const tp05 = grp.filter(s => s.tp05).length / grp.length * 100
  const tp07 = grp.filter(s => s.tp07).length / grp.length * 100
  const avgMfe = grp.reduce((s, t) => s + t.mfe, 0) / grp.length
  const avgMae = grp.reduce((s, t) => s + t.mae, 0) / grp.length
  const ratio = avgMae > 0 ? avgMfe / avgMae : 0
  console.log(`  ${label.padEnd(40)} ${grp.length.toString().padStart(7)} ${(tp05.toFixed(0)+"%").padStart(7)} ${(tp07.toFixed(0)+"%").padStart(7)} ${avgMfe.toFixed(2).padStart(7)} ${avgMae.toFixed(2).padStart(7)} ${ratio.toFixed(2).padStart(6)}   ${tradDir}`)
}

// ════════════════════════════════════════════════════════════════
// PART 4: PRICE POSITION IN DAY'S RANGE AT 2PM
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 4: PRICE POSITION AT 2PM (relative to day's high/low)`)
console.log(`${"=".repeat(80)}\n`)

const positionBuckets = [
  ["Near day low (0-20%)", s => s.f.priceInRange <= 0.2],
  ["Low-mid (20-40%)", s => s.f.priceInRange > 0.2 && s.f.priceInRange <= 0.4],
  ["Middle (40-60%)", s => s.f.priceInRange > 0.4 && s.f.priceInRange <= 0.6],
  ["High-mid (60-80%)", s => s.f.priceInRange > 0.6 && s.f.priceInRange <= 0.8],
  ["Near day high (80-100%)", s => s.f.priceInRange > 0.8],
]

console.log(`  ${"Position".padEnd(28)} ${"N".padStart(7)} ${"TP05%".padStart(7)} ${"TP07%".padStart(7)} ${"MFE(BUY)".padStart(9)} ${"MFE(SELL)".padStart(10)} ${"Best Dir"}`)
console.log(`  ${"-".repeat(75)}`)

for (const [label, fn] of positionBuckets) {
  const grp = all.filter(fn)
  if (grp.length < 100) continue
  // Test BUY vs SELL from this position
  const buyMfe = grp.reduce((s, t) => s + (t.pmDir > 0 ? t.mfe : 0), 0) / grp.filter(t => t.pmDir > 0).length || 0
  const sellMfe = grp.reduce((s, t) => s + (t.pmDir < 0 ? t.mfe : 0), 0) / grp.filter(t => t.pmDir < 0).length || 0
  // For TP, use day direction
  const tp05 = grp.filter(s => s.tp05).length / grp.length * 100
  const tp07 = grp.filter(s => s.tp07).length / grp.length * 100
  const bestDir = buyMfe > sellMfe ? "BUY ↑" : "SELL ↓"
  console.log(`  ${label.padEnd(28)} ${grp.length.toString().padStart(7)} ${(tp05.toFixed(0)+"%").padStart(7)} ${(tp07.toFixed(0)+"%").padStart(7)} ${buyMfe.toFixed(2).padStart(8)}% ${sellMfe.toFixed(2).padStart(9)}%   ${bestDir}`)
}

// ════════════════════════════════════════════════════════════════
// PART 5: CHERRY-PICK SIMULATION — Best afternoon combos
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 5: CHERRY-PICK SIMULATION (Enter 2PM, Exit 3:25PM)`)
console.log(`${"=".repeat(80)}\n`)

function sim(filterFn, rankFn, tp) {
  let wins = 0, trades = 0, pnlSum = 0
  const dailyPnls = []
  for (const date of allDates) {
    const cands = (dayData[date] || []).filter(filterFn)
    cands.sort(rankFn)
    let dayPnl = 0
    for (const s of cands.slice(0, MAX_POS)) {
      const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
      const ret = s.mfe >= tp ? tp : s.timeRet
      dayPnl += s.ep * (ret / 100) * qty
      trades++; if (ret > 0) wins++
    }
    dailyPnls.push(dayPnl)
    pnlSum += dayPnl
  }
  return { wr: trades > 0 ? wins/trades*100 : 0, roc: (pnlSum/nd)/CAPITAL*100, pos: dailyPnls.filter(p=>p>0).length }
}

const byVR = (a, b) => b.f.vr2pm - a.f.vr2pm
const byDayMove = (a, b) => b.f.absDayMove - a.f.absDayMove
const byComp = (a, b) => (b.f.absDayMove * (1 + b.f.above2pmVwap * 0.5) * Math.log(b.f.vr2pm + 1)) - (a.f.absDayMove * (1 + a.f.above2pmVwap * 0.5) * Math.log(a.f.vr2pm + 1))

const setups = [
  ["All, dayMove>0.5%, byVR", s => s.f.absDayMove >= 0.5, byVR],
  ["All, dayMove>1%, byVR", s => s.f.absDayMove >= 1, byVR],
  ["All, dayMove>2%, byVR", s => s.f.absDayMove >= 2, byVR],
  ["All, dayMove>3%, byVR", s => s.f.absDayMove >= 3, byVR],
  ["GapCont + dayMove>1%, byVR", s => s.f.gapContinued && s.f.absDayMove >= 1, byVR],
  ["GapRev + dayMove>1%, byVR", s => s.f.gapReversed && s.f.absDayMove >= 1, byVR],
  ["BigGap + bigMove, byVR", s => s.f.absGap >= 2 && s.f.absDayMove >= 2, byVR],
  ["SELL + dayDn>1%, byVR", s => s.pmDir < 0 && s.f.dayMove < -1, byVR],
  ["SELL + dayDn>2%, byVR", s => s.pmDir < 0 && s.f.dayMove < -2, byVR],
  ["GapUP + daySELL, byVR", s => s.f.gap > 0.5 && s.f.dayMove < -0.5, byVR],
  ["GapUP + daySELL>2%, byVR", s => s.f.gap > 0.5 && s.f.dayMove < -2, byVR],
  ["NearDayHigh + BUY, byVR", s => s.f.priceInRange > 0.8 && s.pmDir > 0, byVR],
  ["NearDayLow + SELL, byVR", s => s.f.priceInRange < 0.2 && s.pmDir < 0, byVR],
  ["MidContinued + bigMove, byVR", s => s.f.midTrendSame && s.f.absDayMove >= 1.5, byVR],
  ["All, dayMove>1%, byComp", s => s.f.absDayMove >= 1, byComp],
  ["All, dayMove>2%, byComp", s => s.f.absDayMove >= 2, byComp],
  ["VWAP aligned + dayMove>1%, byVR", s => s.f.above2pmVwap !== 0 && ((s.pmDir > 0 && s.f.above2pmVwap > 0) || (s.pmDir < 0 && s.f.above2pmVwap < 0)) && s.f.absDayMove >= 1, byVR],
]

console.log(`  ${"Setup".padEnd(42)} ${"TP".padStart(4)} ${"Win%".padStart(7)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)}`)
console.log(`  ${"-".repeat(70)}`)

const results = []
for (const [name, filterFn, rankFn] of setups) {
  for (const tp of [0.3, 0.5, 0.7]) {
    const r = sim(filterFn, rankFn, tp)
    results.push({ name, tp, ...r })
    if (r.roc > 0) {
      console.log(`  ${name.padEnd(42)} ${tp.toFixed(1).padStart(4)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)}`)
    }
  }
}

results.sort((a, b) => b.roc - a.roc)
console.log(`\n  TOP 10 SETUPS:`)
console.log(`  ${"Setup".padEnd(42)} ${"TP".padStart(4)} ${"Win%".padStart(7)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)}`)
console.log(`  ${"-".repeat(70)}`)
for (const r of results.slice(0, 10)) {
  console.log(`  ${r.name.padEnd(42)} ${r.tp.toFixed(1).padStart(4)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)}`)
}

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
