#!/usr/bin/env bun
// ============================================================================
// NO-LOOKAHEAD PATTERN DISCOVERY
// STRICTLY: Every feature computed from data BEFORE entry (bucket 1-3)
// Outcome: What happens AFTER entry (bucket 4+)
// Goal: Find what CAUSES price to move, not what CORRELATES with future price
// ============================================================================

import { createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const t0 = Date.now()
const CAPITAL = 50000, PER_TRADE = 10000, MAX_POS = 12

console.log(`\n${"█".repeat(74)}`)
console.log(`  NO-LOOKAHEAD PATTERN DISCOVERY`)
console.log(`  All features from bucket 1-3, outcomes from bucket 4+`)
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
  if (!dayOpen || dayOpen <= 0 || buckets.length < 10) continue

  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const b1 = sorted.find(b => b.b === 1)
  const b2 = sorted.find(b => b.b === 2)
  const b3 = sorted.find(b => b.b === 3)
  if (!b1 || !b2 || !b3) continue

  // ══ FEATURES (all known at bucket 3 = 9:17 IST) ══
  const ep = b3.c // entry price
  if (ep <= 0) continue
  const movePct = (ep - dayOpen) / dayOpen * 100
  if (Math.abs(movePct) < 0.1) continue // need some movement

  const dir = movePct > 0 ? 1 : -1 // 1=BUY, -1=SELL
  const absMove = Math.abs(movePct)

  // Candle patterns
  const c1Body = b1.br || 0
  const c1Range = dayOpen > 0 ? (b1.h - b1.l) / dayOpen * 100 : 0
  const c1Green = b1.c > b1.o ? 1 : 0
  const c1DirAligned = ((b1.c > b1.o && dir > 0) || (b1.c < b1.o && dir < 0)) ? 1 : 0

  const c2Body = b2.br || 0
  const c3Body = b3.br || 0
  const avgBody = (c1Body + c2Body + c3Body) / 3

  // Volume
  const v1 = b1.v || 0, v2 = b2.v || 0, v3 = b3.v || 0
  const totalVol = v1 + v2 + v3
  const volAccel12 = v1 > 0 ? v2 / v1 : 1
  const volAccel23 = v2 > 0 ? v3 / v2 : 1
  const volIncreasing = (v2 > v1 && v3 > v2) ? 1 : 0
  const volDecreasing = (v2 < v1 && v3 < v2) ? 1 : 0
  const vr3 = b3.vr || 0

  // VWAP
  const vwap3 = b3.vw || 0
  const vwapDist = vwap3 > 0 ? (ep - vwap3) / vwap3 * 100 * dir : 0 // positive = right side
  const vwapAligned = vwap3 > 0 && ((dir > 0 && ep > vwap3) || (dir < 0 && ep < vwap3)) ? 1 : 0

  // Spread (liquidity)
  const spread = b3.sp || 0
  const tightSpread = spread < 0.1 ? 1 : 0

  // Bid/Ask imbalance
  const bidQty = b3.bq || 0
  const askQty = b3.aq || 0
  const orderImbalance = (bidQty + askQty) > 0 ? (bidQty - askQty) / (bidQty + askQty) : 0
  const imbalanceAligned = ((dir > 0 && orderImbalance > 0.2) || (dir < 0 && orderImbalance < -0.2)) ? 1 : 0

  // Gap
  const absGap = Math.abs(gap)
  const gapAligned = ((gap > 0.3 && dir > 0) || (gap < -0.3 && dir < 0)) ? 1 : 0 // continuation
  const gapReverse = ((gap > 0.3 && dir < 0) || (gap < -0.3 && dir > 0)) ? 1 : 0 // reversal

  // Momentum consistency
  const sameDir3 = ((b1.c > b1.o) === (b2.c > b2.o) && (b2.c > b2.o) === (b3.c > b3.o)) ? 1 : 0
  const priceAccel = sorted.filter(b => b.b >= 1 && b.b <= 3).length >= 3 ?
    Math.abs((b3.c - b2.c) / dayOpen * 100) > Math.abs((b2.c - b1.c) / dayOpen * 100) ? 1 : 0 : 0

  // Wick analysis (rejection)
  const upperWick1 = (b1.h - b1.l) > 0 ? (b1.h - Math.max(b1.o, b1.c)) / (b1.h - b1.l) : 0
  const lowerWick1 = (b1.h - b1.l) > 0 ? (Math.min(b1.o, b1.c) - b1.l) / (b1.h - b1.l) : 0

  // Volume-Price divergence: price moving but volume dropping = weak
  const volPriceDiverge = (absMove > 0.3 && volDecreasing) ? 1 : 0
  const volPriceConfirm = (absMove > 0.3 && volIncreasing) ? 1 : 0

  // ══ OUTCOME (strictly from bucket 4 onwards) ══
  let mfe = 0, mae = 0
  const afterEntry = sorted.filter(b => b.b >= 4 && b.b <= 71) // up to ~10:25 for SELL exit
  for (const b of afterEntry) {
    const fav = dir > 0 ? (b.h - ep) / ep * 100 : (ep - b.l) / ep * 100
    const adv = dir > 0 ? (ep - b.l) / ep * 100 : (b.h - ep) / ep * 100
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv
  }

  // TIME exit return
  const exitSnap = sorted.find(b => b.b >= (dir < 0 ? 71 : 35))
  const timeRet = exitSnap ? (dir > 0 ? (exitSnap.c - ep) / ep * 100 : (ep - exitSnap.c) / ep * 100) : 0

  if (!dayData[date]) dayData[date] = []
  dayData[date].push({
    sym, ep, dir, date,
    f: { absMove, c1Body, c1Range, c1Green, c1DirAligned, c2Body, c3Body, avgBody,
      v1, v2, v3, totalVol, volAccel12, volAccel23, volIncreasing, volDecreasing, vr3,
      vwapDist, vwapAligned, spread, tightSpread,
      orderImbalance, imbalanceAligned,
      absGap, gapAligned, gapReverse,
      sameDir3, priceAccel, upperWick1, lowerWick1,
      volPriceDiverge, volPriceConfirm },
    mfe, mae, timeRet,
    tp03: mfe >= 0.3, tp05: mfe >= 0.5, tp07: mfe >= 0.7, tp10: mfe >= 1.0,
  })

  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}

const allDates = Object.keys(dayData).sort()
const nd = allDates.length
const all = Object.values(dayData).flat()
console.log(`${lc} → ${all.length} signals, ${nd} days in ${((Date.now()-t0)/1000).toFixed(1)}s`)
console.log(`Baseline: tp07=${(all.filter(s=>s.tp07).length/all.length*100).toFixed(1)}% | avgMFE=${(all.reduce((s,t)=>s+t.mfe,0)/all.length).toFixed(2)}%\n`)

// ════════════════════════════════════════════════════════════════
// ANALYSIS 1: Single feature importance for TP=0.7% hit
// ════════════════════════════════════════════════════════════════

console.log(`${"=".repeat(80)}`)
console.log(`  PART 1: SINGLE FEATURE → TP=0.7% HIT RATE`)
console.log(`${"=".repeat(80)}\n`)

const fNames = Object.keys(all[0].f)
const results = []

for (const feat of fNames) {
  const vals = all.map(s => s.f[feat]).sort((a, b) => a - b)
  let bestLift = 0, bestThresh = 0, bestDir = '>=', bestRate = 0, bestN = 0

  for (const pct of [10, 20, 30, 40, 50, 60, 70, 80, 90]) {
    const thresh = vals[Math.floor(vals.length * pct / 100)]
    // Test >=
    const above = all.filter(s => s.f[feat] >= thresh)
    const below = all.filter(s => s.f[feat] < thresh)
    if (above.length > 100 && below.length > 100) {
      const aRate = above.filter(s => s.tp07).length / above.length * 100
      const bRate = below.filter(s => s.tp07).length / below.length * 100
      if (aRate - bRate > bestLift) { bestLift = aRate - bRate; bestThresh = thresh; bestDir = '>='; bestRate = aRate; bestN = above.length }
      if (bRate - aRate > bestLift) { bestLift = bRate - aRate; bestThresh = thresh; bestDir = '<'; bestRate = bRate; bestN = below.length }
    }
  }
  results.push({ feat, bestThresh, bestDir, bestLift, bestRate, bestN })
}

results.sort((a, b) => b.bestLift - a.bestLift)
console.log(`  ${"Feature".padEnd(20)} ${"Threshold".padStart(10)} ${"Dir".padStart(4)} ${"Lift".padStart(8)} ${"TP07%".padStart(8)} ${"N".padStart(8)} ${"Signal"}`)
console.log(`  ${"-".repeat(65)}`)
for (const r of results) {
  const sig = r.bestLift > 5 ? "★★★" : r.bestLift > 3 ? "★★" : r.bestLift > 1 ? "★" : ""
  console.log(`  ${r.feat.padEnd(20)} ${r.bestThresh.toFixed(2).padStart(10)} ${r.bestDir.padStart(4)} ${("+"+r.bestLift.toFixed(1)+"%").padStart(8)} ${(r.bestRate.toFixed(1)+"%").padStart(8)} ${r.bestN.toString().padStart(8)}   ${sig}`)
}

// ════════════════════════════════════════════════════════════════
// ANALYSIS 2: CHERRY-PICK with each feature as ranker
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 2: CHERRY-PICK TOP-${MAX_POS} BY EACH FEATURE (TP=0.7%)`)
console.log(`${"=".repeat(80)}\n`)

function simCherryPick(rankFn, filterFn, tp) {
  let wins = 0, trades = 0, pnlSum = 0, mfeS = 0, maeS = 0
  const dailyPnls = []
  for (const date of allDates) {
    const cands = (dayData[date] || []).filter(filterFn)
    cands.sort(rankFn)
    let dayPnl = 0
    for (const s of cands.slice(0, MAX_POS)) {
      const qty = Math.max(Math.floor(PER_TRADE / s.ep), 1)
      const ret = s.mfe >= tp ? tp : s.timeRet
      const pnl = s.ep * (ret / 100) * qty
      dayPnl += pnl; pnlSum += pnl; trades++; if (ret > 0) wins++
      mfeS += s.mfe; maeS += s.mae
    }
    dailyPnls.push(dayPnl)
  }
  const wr = trades > 0 ? wins/trades*100 : 0
  const roc = (pnlSum/nd)/CAPITAL*100
  const pos = dailyPnls.filter(p => p > 0).length
  const ratio = maeS > 0 ? mfeS/maeS : 0
  return { wr, roc, pos, ratio, trades }
}

const rankers = {
  "volRate (vr3)":           (a, b) => b.f.vr3 - a.f.vr3,
  "absMove":                 (a, b) => b.f.absMove - a.f.absMove,
  "totalVol":                (a, b) => b.f.totalVol - a.f.totalVol,
  "volPriceConfirm+move":   (a, b) => (b.f.volPriceConfirm * b.f.absMove) - (a.f.volPriceConfirm * a.f.absMove),
  "vwapDist":                (a, b) => b.f.vwapDist - a.f.vwapDist,
  "imbalance+move":          (a, b) => (b.f.imbalanceAligned * b.f.absMove) - (a.f.imbalanceAligned * a.f.absMove),
  "body*move*vwap":          (a, b) => (b.f.avgBody*b.f.absMove*(b.f.vwapAligned+0.5)) - (a.f.avgBody*a.f.absMove*(a.f.vwapAligned+0.5)),
  "vol*move*body":           (a, b) => (b.f.totalVol*b.f.absMove*b.f.avgBody) - (a.f.totalVol*a.f.absMove*a.f.avgBody),
  "volAccel*move":           (a, b) => (b.f.volAccel23*b.f.absMove) - (a.f.volAccel23*a.f.absMove),
  "sameDir3*move*vol":       (a, b) => (b.f.sameDir3*b.f.absMove*Math.log(b.f.totalVol+1)) - (a.f.sameDir3*a.f.absMove*Math.log(a.f.totalVol+1)),
  "priceAccel*move":         (a, b) => (b.f.priceAccel*b.f.absMove) - (a.f.priceAccel*a.f.absMove),
  "COMPOSITE(all)":          (a, b) => {
    const sc = s => s.f.absMove*2 + s.f.vwapAligned*1.5 + s.f.avgBody + s.f.volPriceConfirm*2 +
      s.f.sameDir3*1.5 + s.f.priceAccel + Math.log(s.f.vr3+1)*0.5 + s.f.imbalanceAligned +
      s.f.c1DirAligned*0.5 - s.f.volPriceDiverge*2
    return sc(b) - sc(a)
  },
}

console.log(`  ${"Ranker".padEnd(28)} ${"Win%".padStart(7)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(60)}`)

for (const [name, fn] of Object.entries(rankers)) {
  const r = simCherryPick(fn, s => s.f.absMove >= 0.15, 0.7)
  console.log(`  ${name.padEnd(28)} ${(r.wr.toFixed(1)+"%").padStart(7)} ${(r.roc.toFixed(2)+"%").padStart(7)} ${(r.pos+"/"+nd).padStart(6)} ${r.ratio.toFixed(2).padStart(8)}`)
}

// ════════════════════════════════════════════════════════════════
// ANALYSIS 3: WHAT DO THE TOP 5% MFE TRADES LOOK LIKE?
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 3: TOP 5% MFE vs BOTTOM 50% — What's DIFFERENT?`)
console.log(`${"=".repeat(80)}\n`)

const byMfe = [...all].sort((a, b) => b.mfe - a.mfe)
const top5 = byMfe.slice(0, Math.floor(all.length * 0.05))
const bot50 = byMfe.slice(Math.floor(all.length * 0.5))

console.log(`  Top 5%: avgMFE=${(top5.reduce((s,t)=>s+t.mfe,0)/top5.length).toFixed(2)}%`)
console.log(`  Bot 50%: avgMFE=${(bot50.reduce((s,t)=>s+t.mfe,0)/bot50.length).toFixed(2)}%\n`)

console.log(`  ${"Feature".padEnd(20)} ${"Top 5%".padStart(10)} ${"Bot 50%".padStart(10)} ${"Diff%".padStart(8)} ${"Signal"}`)
console.log(`  ${"-".repeat(55)}`)

const diffs = []
for (const feat of fNames) {
  const topAvg = top5.reduce((s, t) => s + t.f[feat], 0) / top5.length
  const botAvg = bot50.reduce((s, t) => s + t.f[feat], 0) / bot50.length
  const pctDiff = botAvg !== 0 ? Math.abs((topAvg - botAvg) / botAvg * 100) : (topAvg > 0 ? 100 : 0)
  diffs.push({ feat, topAvg, botAvg, pctDiff })
}
diffs.sort((a, b) => b.pctDiff - a.pctDiff)
for (const d of diffs.filter(d => d.pctDiff > 3)) {
  const sig = d.pctDiff > 50 ? "★★★" : d.pctDiff > 20 ? "★★" : "★"
  console.log(`  ${d.feat.padEnd(20)} ${d.topAvg.toFixed(3).padStart(10)} ${d.botAvg.toFixed(3).padStart(10)} ${(d.pctDiff.toFixed(0)+"%").padStart(8)}   ${sig}`)
}

// ════════════════════════════════════════════════════════════════
// ANALYSIS 4: THE KEY QUESTION — Volume-Price Confirmation
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 4: VOLUME-PRICE RELATIONSHIP (the most honest signal)`)
console.log(`${"=".repeat(80)}\n`)

const vpGroups = {
  "Price UP + Vol UP (confirm)":   all.filter(s => s.dir > 0 && s.f.volIncreasing),
  "Price UP + Vol DOWN (diverge)": all.filter(s => s.dir > 0 && s.f.volDecreasing),
  "Price DN + Vol UP (confirm)":   all.filter(s => s.dir < 0 && s.f.volIncreasing),
  "Price DN + Vol DOWN (diverge)": all.filter(s => s.dir < 0 && s.f.volDecreasing),
  "Vol increasing (any dir)":      all.filter(s => s.f.volIncreasing),
  "Vol decreasing (any dir)":      all.filter(s => s.f.volDecreasing),
  "VWAP aligned + vol confirm":    all.filter(s => s.f.vwapAligned && s.f.volPriceConfirm),
  "All 3 aligned (dir+vwap+vol)":  all.filter(s => s.f.vwapAligned && s.f.volPriceConfirm && s.f.sameDir3),
  "Order imbalance aligned":       all.filter(s => s.f.imbalanceAligned),
  "Everything aligned":            all.filter(s => s.f.vwapAligned && s.f.volPriceConfirm && s.f.sameDir3 && s.f.c1DirAligned && s.f.priceAccel),
}

console.log(`  ${"Pattern".padEnd(35)} ${"N".padStart(7)} ${"TP07%".padStart(7)} ${"TP10%".padStart(7)} ${"AvgMFE".padStart(8)} ${"AvgMAE".padStart(8)} ${"Ratio".padStart(6)}`)
console.log(`  ${"-".repeat(80)}`)

for (const [name, grp] of Object.entries(vpGroups)) {
  if (grp.length < 100) continue
  const tp07 = grp.filter(s => s.tp07).length / grp.length * 100
  const tp10 = grp.filter(s => s.tp10).length / grp.length * 100
  const avgMfe = grp.reduce((s, t) => s + t.mfe, 0) / grp.length
  const avgMae = grp.reduce((s, t) => s + t.mae, 0) / grp.length
  const ratio = avgMae > 0 ? avgMfe / avgMae : 0
  console.log(`  ${name.padEnd(35)} ${grp.length.toString().padStart(7)} ${(tp07.toFixed(1)+"%").padStart(7)} ${(tp10.toFixed(1)+"%").padStart(7)} ${(avgMfe.toFixed(2)+"%").padStart(8)} ${(avgMae.toFixed(2)+"%").padStart(8)} ${ratio.toFixed(2).padStart(6)}`)
}

// ════════════════════════════════════════════════════════════════
// ANALYSIS 5: BEST COMBO — Filter + Rank for cherry-pick
// ════════════════════════════════════════════════════════════════

console.log(`\n${"=".repeat(80)}`)
console.log(`  PART 5: BEST FILTER + RANK COMBO`)
console.log(`${"=".repeat(80)}\n`)

const filters = {
  "all (move>0.15)":           s => true,
  "volConfirm":                s => s.f.volPriceConfirm,
  "vwapAligned":               s => s.f.vwapAligned,
  "vwap+volConfirm":           s => s.f.vwapAligned && s.f.volPriceConfirm,
  "sameDir3":                  s => s.f.sameDir3,
  "sameDir3+vwap":             s => s.f.sameDir3 && s.f.vwapAligned,
  "allAligned":                s => s.f.vwapAligned && s.f.volPriceConfirm && s.f.sameDir3,
  "imbalance":                 s => s.f.imbalanceAligned,
  "SELL only":                 s => s.dir < 0,
  "SELL+vwap+vol":             s => s.dir < 0 && s.f.vwapAligned && s.f.volPriceConfirm,
}

const bestRankers = {
  "volRate":    (a, b) => b.f.vr3 - a.f.vr3,
  "absMove":   (a, b) => b.f.absMove - a.f.absMove,
  "COMPOSITE": (a, b) => {
    const sc = s => s.f.absMove*2 + s.f.vwapAligned*1.5 + s.f.avgBody + s.f.volPriceConfirm*2 +
      s.f.sameDir3*1.5 + s.f.priceAccel + Math.log(s.f.vr3+1)*0.5 + s.f.imbalanceAligned - s.f.volPriceDiverge*2
    return sc(b) - sc(a)
  },
}

const combos = []
for (const [fName, fFn] of Object.entries(filters)) {
  for (const [rName, rFn] of Object.entries(bestRankers)) {
    for (const tp of [0.5, 0.7, 1.0]) {
      const r = simCherryPick(rFn, s => s.f.absMove >= 0.15 && fFn(s), tp)
      combos.push({ label: `${fName} × ${rName} tp=${tp}`, ...r })
    }
  }
}

combos.sort((a, b) => b.roc - a.roc)
console.log(`  ${"Combo".padEnd(45)} ${"Win%".padStart(7)} ${"ROC%".padStart(7)} ${"Pos".padStart(6)} ${"MFE/MAE".padStart(8)}`)
console.log(`  ${"-".repeat(76)}`)
for (const c of combos.slice(0, 25)) {
  console.log(`  ${c.label.padEnd(45)} ${(c.wr.toFixed(1)+"%").padStart(7)} ${(c.roc.toFixed(2)+"%").padStart(7)} ${(c.pos+"/"+nd).padStart(6)} ${c.ratio.toFixed(2).padStart(8)}`)
}

const best = combos[0]
console.log(`\n  BEST: ${best.label}`)
console.log(`  ${best.wr.toFixed(1)}% win | ${best.roc.toFixed(2)}% ROC | ${best.pos}/${nd} pos | MFE/MAE=${best.ratio.toFixed(2)}`)

console.log(`\nDone in ${((Date.now()-t0)/1000).toFixed(1)}s`)
