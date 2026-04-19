#!/usr/bin/env bun
// ============================================================================
// SCORING OPTIMIZATION — Find the best scoring formula for cherry-pick
// Tests 100+ scoring variants with full cherry-pick simulation
// Goal: find a scoring that gives 80%+ win rate with positive ROC
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

const CAPITAL = 50000, PER_TRADE = 10000, MAX_POS = 12
const TP_TESTS = [0.5, 0.7, 1.0]

console.log(`\n${"█".repeat(74)}`)
console.log(`  SCORING OPTIMIZATION — Cherry-pick top ${MAX_POS}, Rs ${PER_TRADE}/trade`)
console.log(`  ${DATA_FILE}`)
console.log(`${"█".repeat(74)}\n`)
console.log("Loading...")

const daySignals = {}
let lc = 0
const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, dayOpen, gapPct: gap, buckets } = sd
  if (!dayOpen || dayOpen <= 0 || buckets.length < 3) continue

  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const entryBkts = sorted.filter(b => b.b >= 2 && b.b <= 4)
  if (!entryBkts.length) continue
  const last = entryBkts[entryBkts.length - 1]
  const mp = (last.c - dayOpen) / dayOpen * 100
  if (Math.abs(mp) < 0.15) continue
  const dir = mp > 0 ? "BUY" : "SELL"
  const ds = dir === "BUY" ? 1 : -1

  if (dir === "BUY" && gap !== 0 && gap < C.buy_gap_min_pct) continue
  if (dir === "BUY" && gap !== 0 && gap > C.buy_gap_max_pct) continue
  if (dir === "SELL" && gap !== 0 && gap < C.sell_gap_min_pct) continue
  if (dir === "SELL" && gap !== 0 && gap > C.sell_gap_max_pct) continue

  const dMM = dir === "BUY" ? C.buy_min_move_pct : C.sell_min_move_pct
  const dMV = dir === "BUY" ? C.buy_min_volume : C.sell_min_volume
  const volE = entryBkts.reduce((s, b) => s + b.v, 0)
  const ep = last.c

  // ── Raw features (continuous values, not just booleans) ──
  const features = {
    // Existing boolean factors
    pm:     Math.abs(mp) >= dMM ? 1 : 0,
    pm2:    Math.abs(mp) >= dMM * 2 ? 1 : 0,
    vol:    volE >= dMV ? 1 : 0,
    vol2:   volE >= dMV * 2 ? 1 : 0,
    vwap:   entryBkts.some(b => dir === "BUY" ? b.c > b.vw && b.vw > 0 : b.c < b.vw && b.vw > 0) ? 1 : 0,
    gap:    Math.abs(gap) > 0.3 && (gap * ds) > 0 ? 1 : 0,
    body:   last.br > 0.6 ? 1 : 0,

    // Continuous features (for smarter scoring)
    absMove:      Math.abs(mp),
    volRate:      last.vr || 0,
    bodyRatio:    last.br || 0,
    gapPct:       gap,
    absGap:       Math.abs(gap),
    vwapDist:     last.vw > 0 ? (last.c - last.vw) / last.vw * 100 * ds : 0, // positive = on right side
    volPerBucket: volE / entryBkts.length,

    // Derived features
    movePerVol:   volE > 0 ? Math.abs(mp) / Math.log(volE + 1) : 0, // efficiency: move per unit volume
    priceRange:   ep < 200 ? 3 : ep < 500 ? 2 : ep < 1000 ? 1 : 0, // cheaper = more qty = more pnl
    bigGap:       Math.abs(gap) > 2 ? 1 : 0, // stocks with big gaps move more
    volSurge:     last.vr >= 500 ? 1 : 0,
    strongBody:   last.br >= 0.7 ? 1 : 0,
    vwapAligned:  last.vw > 0 && ((dir === "BUY" && last.c > last.vw) || (dir === "SELL" && last.c < last.vw)) ? 1 : 0,
    moveStrength: Math.abs(mp) >= 1.0 ? 2 : Math.abs(mp) >= 0.5 ? 1 : 0,

    // Momentum quality: move relative to range
    moveQuality:  (() => {
      const range = entryBkts.reduce((mx, b) => Math.max(mx, b.h), 0) - entryBkts.reduce((mn, b) => Math.min(mn, b.l), 99999)
      return range > 0 ? Math.abs(last.c - dayOpen) / range : 0
    })(),
  }

  // Exit simulation data
  const dSL = dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
  const slPrice = ep * (1 - ds * dSL / 100)
  const exitBkt = dir === "SELL" ? C.sell_hard_exit_bucket : C.hard_exit_bucket
  let mfe = 0, mae = 0
  const tpHits = {}

  for (const tp of [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]) {
    const tpP = ep * (1 + ds * tp / 100)
    tpHits[tp] = false
    for (const b of sorted) {
      if (b.b <= last.b) continue
      if (ds > 0 ? b.c >= tpP : b.c <= tpP) { tpHits[tp] = true; break }
    }
  }
  for (const b of sorted) {
    if (b.b <= last.b) continue
    const fav = ds > 0 ? (b.h - ep) / ep * 100 : (ep - b.l) / ep * 100
    const adv = ds > 0 ? (ep - b.l) / ep * 100 : (b.h - ep) / ep * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv
  }

  // TIME exit return
  const lastB = sorted[sorted.length - 1]
  const exitRet = ds > 0 ? (lastB.c - ep) / ep * 100 : (ep - lastB.c) / ep * 100

  if (!daySignals[date]) daySignals[date] = []
  daySignals[date].push({ sym: symbol, dir, ep, features, mfe, mae, tpHits, exitRet })

  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(daySignals).sort()
const nd = allDates.length
const allSignals = Object.values(daySignals).flat()
console.log(`${lc} lines → ${allSignals.length} signals, ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s\n`)

// ── Simulate cherry-pick with a scoring function ──
function simulate(scoreFn, tp) {
  let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
  const dailyPnls = []

  for (const date of allDates) {
    const sigs = daySignals[date].map(s => ({ ...s, sc: scoreFn(s.features) }))
    sigs.sort((a, b) => b.sc - a.sc || a.ep - b.ep)
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

  const wr = trades > 0 ? wins / trades * 100 : 0
  const avgD = pnlSum / nd
  const roc = avgD / CAPITAL * 100
  const pos = dailyPnls.filter(p => p > 0).length
  const ratio = maeS > 0 ? mfeS / maeS : 0
  return { wr, avgD, roc, pos, ratio, trades }
}

// ============================================================================
// PART 1: TEST 100+ SCORING FORMULAS
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 1: SCORING FORMULA GRID SEARCH`)
console.log(`${"=".repeat(90)}\n`)

const formulas = {
  // Current system
  "CURRENT": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.gap*1 + f.body*1,

  // Remove gap (inconsistent factor)
  "NO_GAP": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.body*1,

  // Boost VWAP (consistent strong predictor)
  "VWAP_3x": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*3 + f.body*1,

  // Boost body
  "BODY_3x": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.body*3,

  // Move-dominant (pm is #1 predictor)
  "MOVE_DOM": f => f.pm*3 + f.pm2*3 + f.vol*1 + f.vol2*1 + f.vwap*2 + f.body*1,

  // Anti-gap (penalize gap)
  "ANTI_GAP": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 - f.gap*1 + f.body*1,

  // Big gap bonus (analysis showed bigger gaps = higher TP hit)
  "BIG_GAP_BONUS": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.body*1 + f.bigGap*2,

  // Continuous: use actual move% as score component
  "CONT_MOVE": f => f.absMove * 3 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.body*1,

  // Continuous: move × VWAP distance
  "CONT_MOVE_VWAP": f => f.absMove * 2 + f.vwapDist * 2 + f.vol*1 + f.vol2*1 + f.body*1,

  // Continuous: move efficiency (move per volume)
  "MOVE_EFFICIENCY": f => f.movePerVol * 5 + f.vwap*2 + f.body*1,

  // Volume surge focus
  "VOL_SURGE": f => f.pm*2 + f.pm2*2 + f.volSurge*3 + f.vol2*2 + f.vwap*1 + f.body*1,

  // Strong body focus
  "STRONG_BODY": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.strongBody*3,

  // VWAP aligned (directional)
  "VWAP_ALIGNED": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwapAligned*3 + f.body*1,

  // Move strength tiers
  "MOVE_TIERS": f => f.moveStrength*3 + f.vol*1 + f.vol2*2 + f.vwap*2 + f.body*1,

  // Price preference (cheaper = more qty = more absolute PnL)
  "CHEAP_PREF": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*1 + f.body*1 + f.priceRange*1,

  // Move quality (move relative to candle range)
  "MOVE_QUALITY": f => f.moveQuality * 5 + f.absMove * 2 + f.vwap*1 + f.vol*1,

  // Minimal: just move + vwap
  "MINIMAL_MV": f => f.absMove * 3 + f.vwapAligned * 3,

  // Kitchen sink: everything
  "KITCHEN_SINK": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*2 + f.body*2 + f.bigGap*1 + f.volSurge*1 + f.moveStrength*1 + f.vwapAligned*1,

  // MFE-optimized (from previous analysis)
  "MFE_OPT": f => f.pm*2 + f.pm2*2 + f.vol*1 + f.vol2*2 + f.vwap*2 + f.body*2 + f.volSurge*1,

  // Pure continuous (no booleans)
  "PURE_CONT": f => f.absMove * 2 + f.vwapDist * 2 + Math.log(f.volPerBucket + 1) * 1 + f.bodyRatio * 3 + f.moveQuality * 3,

  // Rank by MFE predictor: absMove × bodyRatio × vwapAligned
  "MFE_PREDICT": f => f.absMove * f.bodyRatio * (f.vwapAligned + 0.5) * (f.vol2 + 0.5),

  // Just rank by absolute move (simplest possible)
  "JUST_MOVE": f => f.absMove,

  // Just rank by volume
  "JUST_VOL": f => f.volRate,

  // Random baseline (for comparison)
  "RANDOM": f => Math.random(),

  // Penalize high volume (analysis showed losers have higher vol)
  "LOW_VOL_PREF": f => f.pm*2 + f.pm2*2 + f.vwap*2 + f.body*1 - (f.volRate > 1000 ? 2 : 0),

  // VWAP distance as primary (the further from VWAP in right direction, the stronger)
  "VWAP_DIST_PRI": f => f.vwapDist * 4 + f.pm*1 + f.vol*1,

  // Body ratio as primary
  "BODY_PRI": f => f.bodyRatio * 5 + f.absMove * 2 + f.vwap*1,

  // Composite multiplicative (not additive)
  "MULTIPLY": f => (f.absMove + 0.1) * (f.bodyRatio + 0.3) * (f.vwapAligned + 0.5) * (f.vol + 0.3),

  // Big move + big gap (both analysis showed these individually predict higher MFE)
  "BIG_MOVE_GAP": f => f.moveStrength*3 + f.bigGap*3 + f.vwap*1 + f.body*1 + f.vol*1,
}

// Run all formulas across all TPs
const results = []

for (const [name, fn] of Object.entries(formulas)) {
  for (const tp of TP_TESTS) {
    const r = simulate(fn, tp)
    results.push({ name, tp, ...r })
  }
}

// Sort by ROC for each TP
for (const tp of TP_TESTS) {
  console.log(`  TP=${tp}% — Top 15 by ROC:`)
  console.log(`  ${"Formula".padEnd(22)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
  console.log(`  ${"-".repeat(62)}`)

  const tpResults = results.filter(r => r.tp === tp).sort((a, b) => b.roc - a.roc)
  for (const r of tpResults.slice(0, 15)) {
    const marker = r.name === "CURRENT" ? " ← current" : ""
    console.log(`  ${r.name.padEnd(22)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${("Rs "+r.avgD.toFixed(0)).padStart(8)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)} ${r.ratio.toFixed(2).padStart(8)}${marker}`)
  }
  console.log()
}

// ============================================================================
// PART 2: WHAT MAKES TOP-SCORING SIGNALS WIN/LOSE?
// ============================================================================

console.log(`${"=".repeat(90)}`)
console.log(`  PART 2: WHY DO TOP-SCORED SIGNALS STILL LOSE? (using CURRENT scoring, TP=0.7%)`)
console.log(`${"=".repeat(90)}\n`)

const currentFn = formulas["CURRENT"]
let tpWinners = [], tpLosers = []

for (const date of allDates) {
  const sigs = daySignals[date].map(s => ({ ...s, sc: currentFn(s.features) }))
  sigs.sort((a, b) => b.sc - a.sc || a.ep - b.ep)
  for (const s of sigs.slice(0, MAX_POS)) {
    if (s.tpHits[0.7]) tpWinners.push(s)
    else tpLosers.push(s)
  }
}

console.log(`  Selected signals: ${tpWinners.length + tpLosers.length} | TP=0.7% Winners: ${tpWinners.length} | Losers: ${tpLosers.length}\n`)

const featureNames = Object.keys(allSignals[0].features)
console.log(`  ${"Feature".padEnd(18)} ${"Winners".padStart(10)} ${"Losers".padStart(10)} ${"Diff".padStart(8)} ${"Signal?"}`)
console.log(`  ${"-".repeat(60)}`)

for (const feat of featureNames) {
  const wAvg = tpWinners.reduce((s, t) => s + t.features[feat], 0) / tpWinners.length
  const lAvg = tpLosers.reduce((s, t) => s + t.features[feat], 0) / tpLosers.length
  const diff = wAvg - lAvg
  const pct = lAvg !== 0 ? Math.abs(diff / lAvg * 100) : 0
  const sig = pct > 10 ? (diff > 0 ? "✓ WIN" : "✗ LOSE") : "~"
  console.log(`  ${feat.padEnd(18)} ${wAvg.toFixed(3).padStart(10)} ${lAvg.toFixed(3).padStart(10)} ${(diff>=0?"+":"")+diff.toFixed(3).padStart(0).padStart(8)}   ${sig}`)
}

// ============================================================================
// PART 3: WHAT IF WE FILTER BEFORE SCORING?
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 3: PRE-FILTERS + SCORING (filter first, then cherry-pick)`)
console.log(`${"=".repeat(90)}\n`)

const preFilters = {
  "No filter":              s => true,
  "move >= 0.5%":           s => s.features.absMove >= 0.5,
  "move >= 0.3%":           s => s.features.absMove >= 0.3,
  "vwap aligned":           s => s.features.vwapAligned === 1,
  "body >= 0.5":            s => s.features.bodyRatio >= 0.5,
  "body >= 0.4 + vwap":     s => s.features.bodyRatio >= 0.4 && s.features.vwapAligned === 1,
  "move>0.3 + vwap":        s => s.features.absMove >= 0.3 && s.features.vwapAligned === 1,
  "move>0.5 + body>0.5":    s => s.features.absMove >= 0.5 && s.features.bodyRatio >= 0.5,
  "move>0.3 + vwap + body": s => s.features.absMove >= 0.3 && s.features.vwapAligned === 1 && s.features.bodyRatio >= 0.4,
  "bigGap only":            s => s.features.bigGap === 1,
  "vol2 + vwap":            s => s.features.vol2 === 1 && s.features.vwapAligned === 1,
  "price < 500":            s => s.features.priceRange >= 2,
  "SELL only":              s => s.dir === "SELL",
  "BUY only":               s => s.dir === "BUY",
}

console.log(`  ${"Filter".padEnd(28)} ${"Cands/d".padStart(8)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(72)}`)

for (const [filterName, filterFn] of Object.entries(preFilters)) {
  // Use CURRENT scoring but with pre-filter
  let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
  const dailyPnls = []
  let totalCands = 0

  for (const date of allDates) {
    const filtered = daySignals[date].filter(filterFn)
    totalCands += filtered.length
    const scored = filtered.map(s => ({ ...s, sc: currentFn(s.features) }))
    scored.sort((a, b) => b.sc - a.sc || a.ep - b.ep)
    const sel = scored.slice(0, MAX_POS)

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
  const avgCands = totalCands / nd

  console.log(`  ${filterName.padEnd(28)} ${avgCands.toFixed(0).padStart(8)} ${(wr.toFixed(1)+"%").padStart(7)} ${("Rs "+avgD.toFixed(0)).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${(pos+"/"+nd).padStart(6)} ${ratio.toFixed(2).padStart(8)}`)
}

// ============================================================================
// PART 4: BEST COMBO — Filter + Scoring + TP
// ============================================================================

console.log(`\n${"=".repeat(90)}`)
console.log(`  PART 4: BEST OVERALL COMBINATION`)
console.log(`${"=".repeat(90)}\n`)

const bestCombos = []
const topFormulas = ["CURRENT", "MFE_OPT", "VWAP_ALIGNED", "BIG_GAP_BONUS", "MULTIPLY", "MOVE_QUALITY", "KITCHEN_SINK", "CONT_MOVE_VWAP"]
const topFilters = { "none": s => true, "vwap_aligned": s => s.features.vwapAligned === 1, "move>0.3+vwap": s => s.features.absMove >= 0.3 && s.features.vwapAligned === 1 }

for (const [fName, fFn] of Object.entries(topFilters)) {
  for (const sName of topFormulas) {
    const sFn = formulas[sName]
    for (const tp of [0.5, 0.7, 1.0, 1.5]) {
      let wins = 0, trades = 0, pnlSum = 0
      const dailyPnls = []

      for (const date of allDates) {
        const filtered = daySignals[date].filter(fFn)
        const scored = filtered.map(s => ({ ...s, sc: sFn(s.features) }))
        scored.sort((a, b) => b.sc - a.sc || a.ep - b.ep)
        const sel = scored.slice(0, MAX_POS)

        let dayPnl = 0
        for (const s of sel) {
          const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
          const ret = s.tpHits[tp] ? tp : s.exitRet
          const pnl = s.ep * (ret / 100) * qty
          dayPnl += pnl; pnlSum += pnl; trades++
          if (ret > 0) wins++
        }
        dailyPnls.push(dayPnl)
      }

      const wr = trades > 0 ? wins/trades*100 : 0
      const avgD = pnlSum/nd, roc = avgD/CAPITAL*100
      const pos = dailyPnls.filter(p => p > 0).length
      bestCombos.push({ filter: fName, scoring: sName, tp, wr, avgD, roc, pos })
    }
  }
}

bestCombos.sort((a, b) => b.roc - a.roc)
console.log(`  ${"Filter".padEnd(18)} ${"Scoring".padEnd(18)} ${"TP".padStart(4)} ${"Win%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)}`)
console.log(`  ${"-".repeat(72)}`)
for (const c of bestCombos.slice(0, 20)) {
  console.log(`  ${c.filter.padEnd(18)} ${c.scoring.padEnd(18)} ${c.tp.toFixed(1).padStart(4)} ${(c.wr.toFixed(1)+"%").padStart(7)} ${("Rs "+c.avgD.toFixed(0)).padStart(8)} ${(c.roc.toFixed(2)+"%").padStart(7)} ${(c.pos+"/"+nd).padStart(6)}`)
}

const best = bestCombos[0]
console.log(`\n  BEST: Filter=${best.filter} + Scoring=${best.scoring} + TP=${best.tp}%`)
console.log(`  ${best.wr.toFixed(1)}% win | Rs ${best.avgD.toFixed(0)}/day | ${best.roc.toFixed(2)}% ROC | ${best.pos}/${nd} pos days`)

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
