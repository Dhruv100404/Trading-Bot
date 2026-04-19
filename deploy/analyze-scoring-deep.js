#!/usr/bin/env bun
// ============================================================================
// DEEP SCORING ANALYSIS
// Which scoring factors actually predict profitable trades?
// Tests: each factor individually, combinations, weights, and new factors
// ============================================================================

import { readFileSync, createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()

const cfg = JSON.parse(readFileSync("backtest-config.json", "utf-8"))
const C = {
  buy_min_move_pct: cfg.buy_min_move_pct ?? 0.45, sell_min_move_pct: cfg.sell_min_move_pct ?? 0.25,
  buy_min_volume: cfg.buy_min_volume ?? 300, sell_min_volume: cfg.sell_min_volume ?? 450,
  buy_sl_pct: cfg.buy_sl_pct ?? 1.2, sell_sl_pct: cfg.sell_sl_pct ?? 1.8,
  hard_exit_bucket: cfg.hard_exit_bucket ?? 35, sell_hard_exit_bucket: cfg.sell_hard_exit_bucket ?? 71,
  buy_gap_min_pct: cfg.buy_gap_min_pct ?? 0, buy_gap_max_pct: cfg.buy_gap_max_pct ?? 100,
  sell_gap_min_pct: cfg.sell_gap_min_pct ?? -100, sell_gap_max_pct: cfg.sell_gap_max_pct ?? 10,
}

const CAPITAL = 50000, PER_TRADE = 25000, MAX_POS = 10

console.log(`\n${"█".repeat(74)}`)
console.log(`  DEEP SCORING ANALYSIS — Which factors predict winners?`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)
console.log("Loading data...")

// Collect all stock-day data with per-factor scoring
const daySignals = {} // date -> [ signal with individual factor flags ]
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct: gap, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 3) continue

  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  // Entry at bucket 2-3 (widest window)
  const entryBuckets = sorted.filter(b => b.b >= 2 && b.b <= 4)
  if (!entryBuckets.length) continue
  const last = entryBuckets[entryBuckets.length - 1]
  const mp = (last.c - dayOpen) / dayOpen * 100
  if (Math.abs(mp) < 0.15) continue // minimum movement
  const dir = mp > 0 ? "BUY" : "SELL"
  const ds = dir === "BUY" ? 1 : -1

  // Gap filter
  if (dir === "BUY" && gap !== 0 && gap < C.buy_gap_min_pct) continue
  if (dir === "BUY" && gap !== 0 && gap > C.buy_gap_max_pct) continue
  if (dir === "SELL" && gap !== 0 && gap < C.sell_gap_min_pct) continue
  if (dir === "SELL" && gap !== 0 && gap > C.sell_gap_max_pct) continue

  const dMM = dir === "BUY" ? C.buy_min_move_pct : C.sell_min_move_pct
  const dMV = dir === "BUY" ? C.buy_min_volume : C.sell_min_volume
  const volE = entryBuckets.reduce((s, b) => s + b.v, 0)
  const ep = last.c

  // ── Individual scoring factors (boolean flags) ──
  const factors = {
    pm:     Math.abs(mp) >= dMM,                    // price moved enough
    pm2:    Math.abs(mp) >= dMM * 2,                // double move
    vol:    volE >= dMV,                             // volume threshold
    vol2:   volE >= dMV * 2,                         // double volume
    vwap:   entryBuckets.some(b => dir === "BUY" ? b.c > b.vw && b.vw > 0 : b.c < b.vw && b.vw > 0),
    gap:    Math.abs(gap) > 0.3 && (gap * ds) > 0,  // gap continuation
    body:   last.br > 0.6,                           // candle body conviction
    // New experimental factors
    volAccel: entryBuckets.length >= 2 ? entryBuckets[entryBuckets.length-1].v > entryBuckets[0].v * 1.2 : false,
    bigMove:  Math.abs(mp) >= 1.0,                   // strong early move
    lowSpread: last.sp !== undefined ? last.sp < 0.1 : true,
    highVR:   last.vr >= 200,                        // high volume rate
    priceUnder500: ep < 500,
    priceUnder1000: ep < 1000,
    consistentDir: entryBuckets.length >= 2 ? entryBuckets.every((b, i) => {
      if (i === 0) return true
      return dir === "BUY" ? b.c >= entryBuckets[i-1].c : b.c <= entryBuckets[i-1].c
    }) : false,
    gapSmall: Math.abs(gap) < 2.0,                  // not extreme gap
  }

  // Current system score
  let currentScore = 0
  if (factors.pm) currentScore += 2
  if (factors.pm2) currentScore += 2
  if (factors.vol) currentScore += 1
  if (factors.vol2) currentScore += 2
  if (factors.vwap) currentScore += 1
  if (factors.gap) currentScore += 1
  if (factors.body) currentScore += 1

  // Simulate exit for various TPs
  const dSL = dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
  const slPrice = ep * (1 - ds * dSL / 100)
  const exitBkt = dir === "SELL" ? C.sell_hard_exit_bucket : C.hard_exit_bucket

  let mfe = 0, mae = 0
  const tpHits = {} // tp -> boolean
  const tpBuckets = {} // tp -> bucket when hit

  for (const tp of [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]) {
    const tpPrice = ep * (1 + ds * tp / 100)
    tpHits[tp] = false
    for (const b of sorted) {
      if (b.b <= last.b) continue
      if (ds > 0 ? b.c >= tpPrice : b.c <= tpPrice) { tpHits[tp] = true; tpBuckets[tp] = b.b; break }
    }
  }

  // Full exit sim with TP=0.7
  let exitRet = null
  for (const b of sorted) {
    if (b.b <= last.b) continue
    const fav = ds > 0 ? (b.h - ep) / ep * 100 : (ep - b.l) / ep * 100
    const adv = ds > 0 ? (ep - b.l) / ep * 100 : (b.h - ep) / ep * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv
  }
  // Ret without TP (TIME exit)
  const lastBkt = sorted[sorted.length - 1]
  exitRet = ds > 0 ? (lastBkt.c - ep) / ep * 100 : (ep - lastBkt.c) / ep * 100

  if (!daySignals[date]) daySignals[date] = []
  daySignals[date].push({
    sym: symbol, date, dir, ep, mp, gap, volE, vr: last.vr,
    factors, currentScore, mfe, mae, tpHits, tpBuckets, exitRet,
  })

  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(daySignals).sort()
const nd = allDates.length
const allSignals = Object.values(daySignals).flat()
console.log(`${lc} lines → ${allSignals.length} signals across ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ============================================================================
// PART 1: INDIVIDUAL FACTOR PREDICTIVE POWER
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: INDIVIDUAL FACTOR PREDICTIVE POWER`)
console.log(`  For each factor: what's the MFE, TP hit rate, and win rate when factor is ON vs OFF?`)
console.log(`${"=".repeat(90)}\n`)

const factorNames = Object.keys(allSignals[0].factors)

console.log(`  ${"Factor".padEnd(18)} ${"ON_N".padStart(7)} ${"ON_MFE".padStart(8)} ${"ON_MAE".padStart(8)} ${"ON_Ratio".padStart(9)} ${"ON_TP07".padStart(8)} ${"OFF_N".padStart(7)} ${"OFF_MFE".padStart(8)} ${"OFF_MAE".padStart(8)} ${"OFF_Ratio".padStart(9)} ${"OFF_TP07".padStart(8)} ${"Lift".padStart(7)}`)
console.log(`  ${"-".repeat(108)}`)

const factorLift = {}

for (const factor of factorNames) {
  const on = allSignals.filter(s => s.factors[factor])
  const off = allSignals.filter(s => !s.factors[factor])

  const onMFE = on.length > 0 ? on.reduce((s, t) => s + t.mfe, 0) / on.length : 0
  const onMAE = on.length > 0 ? on.reduce((s, t) => s + t.mae, 0) / on.length : 0
  const onRatio = onMAE > 0 ? onMFE / onMAE : 0
  const onTP07 = on.length > 0 ? on.filter(s => s.tpHits[0.7]).length / on.length * 100 : 0

  const offMFE = off.length > 0 ? off.reduce((s, t) => s + t.mfe, 0) / off.length : 0
  const offMAE = off.length > 0 ? off.reduce((s, t) => s + t.mae, 0) / off.length : 0
  const offRatio = offMAE > 0 ? offMFE / offMAE : 0
  const offTP07 = off.length > 0 ? off.filter(s => s.tpHits[0.7]).length / off.length * 100 : 0

  const lift = offTP07 > 0 ? (onTP07 / offTP07 - 1) * 100 : 0
  factorLift[factor] = { lift, onTP07, offTP07, onRatio, offRatio }

  const marker = lift > 5 ? " <<<" : lift < -5 ? " !!!" : ""
  console.log(`  ${factor.padEnd(18)} ${on.length.toString().padStart(7)} ${(onMFE.toFixed(2)+"%").padStart(8)} ${(onMAE.toFixed(2)+"%").padStart(8)} ${onRatio.toFixed(2).padStart(9)} ${(onTP07.toFixed(1)+"%").padStart(8)} ${off.length.toString().padStart(7)} ${(offMFE.toFixed(2)+"%").padStart(8)} ${(offMAE.toFixed(2)+"%").padStart(8)} ${offRatio.toFixed(2).padStart(9)} ${(offTP07.toFixed(1)+"%").padStart(8)} ${(lift>=0?"+":"")+lift.toFixed(1)+"%".padStart(0).padStart(7)}${marker}`)
}

// ============================================================================
// PART 2: FACTOR COMBINATIONS — Which pairs boost TP hit rate most?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 2: BEST FACTOR COMBINATIONS (TP=0.7% hit rate)`)
console.log(`${"=".repeat(90)}\n`)

const combos = []
const coreFactors = ["pm", "pm2", "vol", "vol2", "vwap", "gap", "body", "volAccel", "bigMove", "highVR", "consistentDir", "gapSmall"]

for (let i = 0; i < coreFactors.length; i++) {
  for (let j = i + 1; j < coreFactors.length; j++) {
    const f1 = coreFactors[i], f2 = coreFactors[j]
    const both = allSignals.filter(s => s.factors[f1] && s.factors[f2])
    if (both.length < 50) continue
    const tp07 = both.filter(s => s.tpHits[0.7]).length / both.length * 100
    const mfe = both.reduce((s, t) => s + t.mfe, 0) / both.length
    const mae = both.reduce((s, t) => s + t.mae, 0) / both.length
    combos.push({ label: `${f1}+${f2}`, n: both.length, tp07, mfe, mae, ratio: mae > 0 ? mfe/mae : 0 })
  }
}

combos.sort((a, b) => b.tp07 - a.tp07)
console.log(`  ${"Combination".padEnd(28)} ${"N".padStart(7)} ${"TP0.7%".padStart(8)} ${"MFE%".padStart(7)} ${"MAE%".padStart(7)} ${"Ratio".padStart(6)}`)
console.log(`  ${"-".repeat(68)}`)
for (const c of combos.slice(0, 25)) {
  console.log(`  ${c.label.padEnd(28)} ${c.n.toString().padStart(7)} ${(c.tp07.toFixed(1)+"%").padStart(8)} ${(c.mfe.toFixed(2)+"%").padStart(7)} ${(c.mae.toFixed(2)+"%").padStart(7)} ${c.ratio.toFixed(2).padStart(6)}`)
}

// ============================================================================
// PART 3: SCORE THRESHOLD ANALYSIS — What minimum score works best?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 3: CURRENT SCORE THRESHOLD — Cherry-pick top ${MAX_POS} by score`)
console.log(`${"=".repeat(90)}\n`)

for (const tp of [0.7, 1.0, 1.5]) {
  console.log(`  TP=${tp}%:`)
  console.log(`  ${"MinScore".padEnd(10)} ${"Cands/d".padStart(8)} ${"Sel/d".padStart(6)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"MFE/MAE".padStart(8)} ${"PosDays".padStart(8)}`)
  console.log(`  ${"-".repeat(62)}`)

  for (let minSc = 0; minSc <= 10; minSc++) {
    let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
    const dailyPnls = []

    for (const date of allDates) {
      const sigs = daySignals[date].filter(s => s.currentScore >= minSc)
      sigs.sort((a, b) => b.currentScore - a.currentScore || a.ep - b.ep)
      const sel = sigs.slice(0, MAX_POS)

      let dayPnl = 0
      for (const s of sel) {
        const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
        const ret = s.tpHits[tp] ? tp : s.exitRet
        const pnl = s.ep * (ret / 100) * qty
        dayPnl += pnl; pnlSum += pnl; trades++
        if (ret > 0) wins++
        mfeS += s.mfe; maeS += s.mae
      }
      dailyPnls.push(dayPnl)
    }

    const wr = trades > 0 ? wins/trades*100 : 0
    const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
    const pos = dailyPnls.filter(p => p > 0).length
    const ratio = maeS > 0 ? mfeS/maeS : 0
    const fill = trades / nd

    console.log(`  sc>=${String(minSc).padEnd(7)} ${(daySignals[allDates[0]]?.filter(s=>s.currentScore>=minSc).length || 0).toString().padStart(8)} ${fill.toFixed(0).padStart(6)} ${(wr.toFixed(1)+"%").padStart(7)} ${("Rs "+avgD.toFixed(0)).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${ratio.toFixed(2).padStart(8)} ${(pos+"/"+nd).padStart(8)}`)
  }
  console.log()
}

// ============================================================================
// PART 4: ALTERNATIVE SCORING — Test different weights
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 4: ALTERNATIVE SCORING SYSTEMS`)
console.log(`${"=".repeat(90)}\n`)

function altScore(s, weights) {
  let sc = 0
  if (s.factors.pm) sc += weights.pm || 0
  if (s.factors.pm2) sc += weights.pm2 || 0
  if (s.factors.vol) sc += weights.vol || 0
  if (s.factors.vol2) sc += weights.vol2 || 0
  if (s.factors.vwap) sc += weights.vwap || 0
  if (s.factors.gap) sc += weights.gap || 0
  if (s.factors.body) sc += weights.body || 0
  if (s.factors.volAccel) sc += weights.volAccel || 0
  if (s.factors.bigMove) sc += weights.bigMove || 0
  if (s.factors.highVR) sc += weights.highVR || 0
  if (s.factors.consistentDir) sc += weights.consistentDir || 0
  if (s.factors.gapSmall) sc += weights.gapSmall || 0
  return sc
}

const scoringSystems = {
  "Current (pm2+2,vol2+2,vwap,gap,body)": { pm: 2, pm2: 2, vol: 1, vol2: 2, vwap: 1, gap: 1, body: 1 },
  "Volume-heavy (vol×3)":                  { pm: 1, pm2: 1, vol: 2, vol2: 3, vwap: 1, gap: 1, body: 1 },
  "Move-heavy (pm×3)":                     { pm: 3, pm2: 3, vol: 1, vol2: 1, vwap: 1, gap: 1, body: 1 },
  "VWAP-heavy (vwap×3)":                   { pm: 1, pm2: 1, vol: 1, vol2: 1, vwap: 3, gap: 1, body: 1 },
  "Body-heavy (body×3)":                   { pm: 1, pm2: 1, vol: 1, vol2: 1, vwap: 1, gap: 1, body: 3 },
  "Consistency-focused":                   { pm: 1, pm2: 1, vol: 1, vol2: 1, vwap: 2, body: 2, consistentDir: 3, gapSmall: 1 },
  "MFE-optimized (from Part 1 lifts)":     { pm: 2, pm2: 2, vol: 1, vol2: 2, vwap: 2, gap: 1, body: 2, highVR: 1, consistentDir: 2 },
  "Minimal (pm+vol only)":                 { pm: 3, vol: 2, vol2: 3 },
  "Everything equal":                      { pm: 1, pm2: 1, vol: 1, vol2: 1, vwap: 1, gap: 1, body: 1, volAccel: 1, bigMove: 1, highVR: 1, consistentDir: 1, gapSmall: 1 },
  "Anti-gap (penalize gap)":               { pm: 2, pm2: 2, vol: 1, vol2: 2, vwap: 1, gap: -1, body: 1, gapSmall: 2 },
}

console.log(`  ${"Scoring System".padEnd(42)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"MFE/MAE".padStart(8)} ${"PosDays".padStart(8)}`)
console.log(`  ${"-".repeat(82)}`)

for (const [name, weights] of Object.entries(scoringSystems)) {
  let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
  const dailyPnls = []

  for (const date of allDates) {
    const sigs = daySignals[date].map(s => ({ ...s, altSc: altScore(s, weights) }))
    sigs.sort((a, b) => b.altSc - a.altSc || a.ep - b.ep)
    const sel = sigs.slice(0, MAX_POS)

    let dayPnl = 0
    for (const s of sel) {
      const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
      const ret = s.tpHits[0.7] ? 0.7 : s.exitRet
      const pnl = s.ep * (ret / 100) * qty
      dayPnl += pnl; pnlSum += pnl; trades++
      if (ret > 0) wins++
      mfeS += s.mfe; maeS += s.mae
    }
    dailyPnls.push(dayPnl)
  }

  const wr = trades > 0 ? wins/trades*100 : 0
  const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
  const pos = dailyPnls.filter(p => p > 0).length
  const ratio = maeS > 0 ? mfeS/maeS : 0
  const best = roc === Math.max(...Object.values(scoringSystems).map(() => roc))
  console.log(`  ${name.padEnd(42)} ${(wr.toFixed(1)+"%").padStart(7)} ${("Rs "+avgD.toFixed(0)).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${ratio.toFixed(2).padStart(8)} ${(pos+"/"+nd).padStart(8)}`)
}

// ============================================================================
// PART 5: FACTOR IMPACT ON MFE — Which factors predict HIGH MFE?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 5: WHICH FACTORS PREDICT HIGH MFE (>= 0.7%)?`)
console.log(`${"=".repeat(90)}\n`)

const highMFE = allSignals.filter(s => s.mfe >= 0.7)
const lowMFE = allSignals.filter(s => s.mfe < 0.7)

console.log(`  High MFE (>=0.7%): ${highMFE.length} (${(highMFE.length/allSignals.length*100).toFixed(1)}%)`)
console.log(`  Low MFE (<0.7%):   ${lowMFE.length} (${(lowMFE.length/allSignals.length*100).toFixed(1)}%)\n`)

console.log(`  ${"Factor".padEnd(18)} ${"HighMFE%".padStart(9)} ${"LowMFE%".padStart(9)} ${"Diff".padStart(7)} ${"Predictive?"}`)
console.log(`  ${"-".repeat(60)}`)

for (const factor of factorNames) {
  const highOn = highMFE.filter(s => s.factors[factor]).length / highMFE.length * 100
  const lowOn = lowMFE.filter(s => s.factors[factor]).length / lowMFE.length * 100
  const diff = highOn - lowOn
  const pred = Math.abs(diff) > 5 ? (diff > 0 ? "YES ✓" : "ANTI ✗") : "weak"
  console.log(`  ${factor.padEnd(18)} ${(highOn.toFixed(1)+"%").padStart(9)} ${(lowOn.toFixed(1)+"%").padStart(9)} ${(diff>=0?"+":"")+diff.toFixed(1)+"%".padStart(0).padStart(7)}   ${pred}`)
}

// ============================================================================
// PART 6: WINNERS vs LOSERS — Factor frequency
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 6: WINNERS vs LOSERS — Factor frequency (with TP=0.7%)`)
console.log(`${"=".repeat(90)}\n`)

const winners = allSignals.filter(s => s.tpHits[0.7])
const losers = allSignals.filter(s => !s.tpHits[0.7])

console.log(`  Winners (TP hit): ${winners.length} | Losers (TP miss): ${losers.length}\n`)
console.log(`  ${"Factor".padEnd(18)} ${"Winners%".padStart(9)} ${"Losers%".padStart(9)} ${"WinLift".padStart(8)} ${"Signal?"}`)
console.log(`  ${"-".repeat(55)}`)

for (const factor of factorNames) {
  const wOn = winners.filter(s => s.factors[factor]).length / winners.length * 100
  const lOn = losers.filter(s => s.factors[factor]).length / losers.length * 100
  const lift = wOn - lOn
  const sig = Math.abs(lift) > 3 ? (lift > 0 ? "GOOD ✓" : "BAD ✗") : "~"
  console.log(`  ${factor.padEnd(18)} ${(wOn.toFixed(1)+"%").padStart(9)} ${(lOn.toFixed(1)+"%").padStart(9)} ${(lift>=0?"+":"")+lift.toFixed(1)+"%".padStart(0).padStart(8)}   ${sig}`)
}

// ============================================================================
// PART 7: RECOMMENDATION
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  RECOMMENDATION: SCORING IMPROVEMENTS`)
console.log(`${"=".repeat(90)}\n`)

// Sort factors by lift
const sorted = Object.entries(factorLift).sort((a, b) => b[1].lift - a[1].lift)
console.log("  Factors ranked by TP=0.7% hit rate lift (ON vs OFF):\n")
for (const [f, v] of sorted) {
  const emoji = v.lift > 5 ? "🟢" : v.lift > 0 ? "🟡" : "🔴"
  console.log(`  ${emoji} ${f.padEnd(18)} ON: ${v.onTP07.toFixed(1)}% | OFF: ${v.offTP07.toFixed(1)}% | Lift: ${v.lift>=0?"+":""}${v.lift.toFixed(1)}% | MFE/MAE ON: ${v.onRatio.toFixed(2)} OFF: ${v.offRatio.toFixed(2)}`)
}

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
