#!/usr/bin/env bun
// ============================================================================
// HONEST ANALYSIS: Why did previous analysis show 100%? What's actually real?
// Capital: Rs 50K, 5x margin = Rs 2.5L buying power, Rs 25K/trade = 10 positions
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
  quantity: cfg.quantity ?? 1,
  buy_gap_min_pct: cfg.buy_gap_min_pct ?? 0, buy_gap_max_pct: cfg.buy_gap_max_pct ?? 100,
  sell_gap_min_pct: cfg.sell_gap_min_pct ?? -100, sell_gap_max_pct: cfg.sell_gap_max_pct ?? 10,
  buy_min_vol_rate: cfg.buy_min_vol_rate ?? 0, sell_min_vol_rate: cfg.sell_min_vol_rate ?? 0,
  direction_filter: cfg.direction_filter ?? "BOTH",
  buy_qty_multiplier: cfg.buy_qty_multiplier ?? 1, sell_qty_multiplier: cfg.sell_qty_multiplier ?? 1,
  min_move_pct: cfg.min_move_pct ?? 0.15,
}

let fnoSet = new Set()
try {
  for (const line of readFileSync("data/candles/scrip-master.csv", "utf-8").split("\n")) {
    const c = line.split(",")
    if (c[0]?.trim() === "NSE" && c[3]?.trim() === "FUTSTK") {
      const s = c[5]?.trim().split("-")[0]; if (s) fnoSet.add(s)
    }
  }
} catch {}

// EXACT signal engine from Backtest.tsx (no VWAP/gap/body scoring)
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

function replayDay(buckets, dayOpen, gap, sym) {
  if (buckets.length < 3 || dayOpen <= 0) return null
  const sorted = [...buckets].sort((a, b) => a.b - b.b)
  const uniqueBkts = [...new Set(sorted.map(b => b.b))].sort((a, b) => a - b)

  let active = null, fired = false

  for (const bkt of uniqueBkts) {
    const upTo = sorted.filter(b => b.b <= bkt)
    const cur = sorted.find(b => b.b === bkt)
    if (!cur) continue

    if (!fired) {
      const wS = Math.min(C.buy_entry_start, C.sell_entry_start)
      const wE = Math.max(C.buy_entry_end, C.sell_entry_end)
      if (bkt < wS || bkt > wE) continue

      const eSnaps = upTo.filter(b => b.b >= wS && b.b <= bkt)
      if (!eSnaps.length) continue
      const last = eSnaps[eSnaps.length - 1]
      const mp = (last.c - dayOpen) / dayOpen * 100
      const dir = mp > 0 ? "BUY" : "SELL"
      const ds = dir === "BUY" ? 1 : -1

      const dES = dir === "BUY" ? C.buy_entry_start : C.sell_entry_start
      const dEE = dir === "BUY" ? C.buy_entry_end : C.sell_entry_end
      if (bkt < dES || bkt > dEE) continue

      const dSnaps = upTo.filter(b => b.b >= dES && b.b <= bkt)
      if (!dSnaps.length) continue

      const dMM = dir === "BUY" ? C.buy_min_move_pct : C.sell_min_move_pct
      if (Math.abs(mp) < dMM) continue

      // Gap filters
      if (dir === "BUY" && C.buy_gap_min_pct > -100 && gap !== 0 && gap < C.buy_gap_min_pct) continue
      if (dir === "SELL" && C.sell_gap_max_pct < 100 && gap !== 0 && gap > C.sell_gap_max_pct) continue
      if (dir === "SELL" && C.sell_gap_min_pct > -100 && gap !== 0 && gap < C.sell_gap_min_pct) continue
      if (dir === "BUY" && C.buy_gap_max_pct < 100 && gap !== 0 && gap > C.buy_gap_max_pct) continue

      const dMVR = dir === "BUY" ? C.buy_min_vol_rate : C.sell_min_vol_rate
      if (dMVR > 0 && last.vr < dMVR) continue

      // Scoring: ONLY pm + vol (UI removed vwap/gap/body/spread)
      let score = 0
      if (Math.abs(mp) >= dMM) score += 2
      if (Math.abs(mp) >= dMM * 2) score += 2
      const dMV = dir === "BUY" ? C.buy_min_volume : C.sell_min_volume
      const volE = dSnaps.reduce((s, b) => s + b.v, 0)
      if (volE >= dMV) score += 1
      if (volE >= dMV * 2) score += 2
      // OI scores: always 0 in JSON data (no OI)

      const dMS = dir === "BUY" ? C.buy_min_score : C.sell_min_score
      if (score < dMS) continue
      if (C.direction_filter !== "BOTH" && dir !== C.direction_filter) continue

      // Dynamic qty
      const morning = sorted.filter(b => b.b >= 1 && b.b <= bkt).map(b => b.c)
      const mr = morning.length > 0 ? (() => {
        const mx = Math.max(...morning), mn = Math.min(...morning), av = morning.reduce((a,b)=>a+b,0)/morning.length
        return av > 0 ? (mx-mn)/av*100 : 0
      })() : 0
      const dirMult = dir === "BUY" ? C.buy_qty_multiplier : C.sell_qty_multiplier
      const qty = dynamicQty(C.quantity, dirMult, last.c, last.vc, last.vr, mr, mp)
      if (qty === 0) continue

      const ep = last.c
      const dTP = dir === "BUY" ? C.buy_tp_pct : C.sell_tp_pct
      const dSL = dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
      fired = true
      active = { sym, dir, ep, eb: bkt, score, qty, mp, vr: last.vr, vc: last.vc,
        tp: ep*(1+ds*dTP/100), sl: ep*(1-ds*dSL/100), gap }
    }

    if (active && !active.exitR) {
      const dTP = active.dir === "BUY" ? C.buy_tp_pct : C.sell_tp_pct
      const dSL = active.dir === "BUY" ? C.buy_sl_pct : C.sell_sl_pct
      const tpOn = dTP > 0.0001, slOn = dSL > 0.0001
      const tpHit = tpOn && (active.dir === "BUY" ? cur.c >= active.tp : cur.c <= active.tp)
      const slHit = slOn && (active.dir === "BUY" ? cur.c <= active.sl : cur.c >= active.sl)
      const exitBkt = active.dir === "SELL" ? C.sell_hard_exit_bucket : C.hard_exit_bucket
      const timeHit = bkt >= exitBkt
      const reason = tpHit ? "TP" : slHit ? "SL" : timeHit ? "TIME" : null

      if (reason) {
        const ds = active.dir === "BUY" ? 1 : -1
        const ret = active.dir === "BUY" ? (cur.c-active.ep)/active.ep*100 : (active.ep-cur.c)/active.ep*100
        active.xp = cur.c; active.xb = bkt; active.exitR = reason; active.ret = ret
        active.pnl = active.ep * (ret/100) * active.qty
      }
    }
  }

  if (active && !active.exitR) {
    const last = sorted[sorted.length-1]
    const ret = active.dir === "BUY" ? (last.c-active.ep)/active.ep*100 : (active.ep-last.c)/active.ep*100
    active.xp = last.c; active.xb = last.b; active.exitR = "TIME"; active.ret = ret
    active.pnl = active.ep * (ret/100) * active.qty
  }

  // MFE/MAE
  if (active && active.exitR) {
    const ds = active.dir === "BUY" ? 1 : -1
    let mxF = 0, mxA = 0
    for (const b of sorted) {
      if (b.b <= active.eb) continue
      const fav = ds > 0 ? (b.h-active.ep)/active.ep*100 : (active.ep-b.l)/active.ep*100
      const adv = ds > 0 ? (active.ep-b.l)/active.ep*100 : (b.h-active.ep)/active.ep*100
      if (fav > mxF) mxF = fav
      if (adv > mxA) mxA = adv
    }
    active.mxF = mxF; active.mxA = mxA
  }

  return active
}

// ============================================================================
// Stream
// ============================================================================

console.log(`Streaming...`)
const allTrades = []
let lc = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const t = replayDay(sd.buckets, sd.dayOpen, sd.gapPct, sd.symbol)
  if (t) { t.isFno = fnoSet.has(sd.symbol); t.date = sd.date; allTrades.push(t) }
  if (lc % 20000 === 0) process.stderr.write(`  ${lc}...\r`)
}
console.log(`${lc} stock-days → ${allTrades.length} trades in ${((Date.now()-t0)/1000).toFixed(1)}s`)

const fno = allTrades.filter(t => t.isFno)
const allDates = [...new Set(allTrades.map(t => t.date))].sort()
const nd = allDates.length

// ============================================================================
// PART 1: WHY DID PREVIOUS ANALYSIS SHOW 100%?
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  PART 1: WHY 100% WIN RATE WAS WRONG — HONEST BREAKDOWN`)
console.log(`${"=".repeat(74)}`)

console.log(`
  The previous analyses had THREE errors that inflated results:

  ERROR 1: SURVIVORSHIP BIAS IN QUANT FRAMEWORK
  ─────────────────────────────────────────────
  quant-framework.js searched for "moves where price went 0.7%+" then asked
  "does 0.7% TP get hit?" — CIRCULAR LOGIC. If you select moves that went
  0.7%+, of course 0.7% TP hits 100%. That's how they were selected!

  ERROR 2: DIFFERENT SIGNAL ENGINE
  ────────────────────────────────
  analyze.js and analyze-stocks.js used a signal engine with VWAP cross,
  gap continuation, and candle body ratio as scoring factors.
  BUT your UI Backtest.tsx removed these (line 190):
    "Note: vwap, gap, body, spread signals removed — matching EarlySignals baseline"
  This means the previous analyses generated DIFFERENT signals than your real system.

  ERROR 3: NO POSITION CAP
  ────────────────────────
  Previous analysis simulated unlimited positions. Your real capital is Rs 50K
  with 5x margin = Rs 2.5L buying power = max 10 positions at Rs 25K each.
`)

// ============================================================================
// PART 2: THE REAL NUMBERS (exact signal engine, your capital)
// ============================================================================

console.log(`${"=".repeat(74)}`)
console.log(`  PART 2: REAL NUMBERS — Rs 50K capital, 5x margin, Rs 25K/trade, max 10 pos`)
console.log(`${"=".repeat(74)}`)

const CAPITAL = 50000
const MARGIN = 5
const BUYING_POWER = CAPITAL * MARGIN  // 2.5L
const PER_TRADE = 25000
const MAX_POS = Math.floor(BUYING_POWER / PER_TRADE) // 10

// Simulate with different TP levels
console.log(`\n  --- TP SWEEP (exact signal engine, capped at ${MAX_POS} positions/day) ---\n`)
console.log(`  ${"TP%".padEnd(8)} ${"Trades".padStart(7)} ${"Win%".padStart(7)} ${"AvgRet%".padStart(9)} ${"TotPnL".padStart(10)} ${"Rs/day".padStart(8)} ${"ROC%/day".padStart(9)} ${"PosDays".padStart(8)}`)
console.log(`  ${"-".repeat(70)}`)

for (const simTP of [0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]) {
  const dailyResults = []

  for (const date of allDates) {
    const dayTrades = allTrades.filter(t => t.date === date)
      .sort((a, b) => b.score - a.score || (b.mxF||0) - (a.mxF||0))
      .slice(0, MAX_POS)

    let dayPnl = 0, dayWins = 0, dayTrades2 = 0
    for (const t of dayTrades) {
      const qty = Math.max(Math.floor(PER_TRADE / t.ep), 1)
      let ret
      if (simTP > 0 && t.mxF >= simTP) {
        ret = simTP
      } else {
        ret = t.ret
      }
      const pnl = t.ep * (ret / 100) * qty
      dayPnl += pnl
      if (ret > 0) dayWins++
      dayTrades2++
    }
    dailyResults.push({ date, pnl: dayPnl, wins: dayWins, trades: dayTrades2 })
  }

  const totalPnl = dailyResults.reduce((s, d) => s + d.pnl, 0)
  const totalTrades = dailyResults.reduce((s, d) => s + d.trades, 0)
  const totalWins = dailyResults.reduce((s, d) => s + d.wins, 0)
  const avgDaily = totalPnl / nd
  const posDays = dailyResults.filter(d => d.pnl > 0).length
  const avgRet = totalTrades > 0 ? dailyResults.reduce((s, d) => {
    return s + d.trades // placeholder
  }, 0) : 0
  const winRate = totalTrades > 0 ? totalWins / totalTrades * 100 : 0
  const roc = avgDaily / CAPITAL * 100

  const label = simTP === 0 ? "0 (now)" : simTP.toString()
  console.log(`  ${label.padEnd(8)} ${String(totalTrades).padStart(7)} ${(winRate.toFixed(1)+"%").padStart(7)} ${("—").padStart(9)} ${("Rs "+totalPnl.toFixed(0)).padStart(10)} ${("Rs "+avgDaily.toFixed(0)).padStart(8)} ${(roc.toFixed(2)+"%").padStart(9)} ${(posDays+"/"+nd).padStart(8)}`)

  // Print daily breakdown for best TP
  if (simTP === 0.7) {
    console.log(`\n    Daily breakdown for TP=0.7%:`)
    console.log(`    ${"Date".padEnd(12)} ${"Trades".padStart(7)} ${"Wins".padStart(5)} ${"PnL".padStart(10)} ${"Cum PnL".padStart(10)}`)
    let cum = 0
    for (const d of dailyResults) {
      cum += d.pnl
      const mark = d.pnl < 0 ? " ✗" : ""
      console.log(`    ${d.date.padEnd(12)} ${String(d.trades).padStart(7)} ${String(d.wins).padStart(5)} ${("Rs "+d.pnl.toFixed(0)).padStart(10)} ${("Rs "+cum.toFixed(0)).padStart(10)}${mark}`)
    }
    console.log()
  }
}

// ============================================================================
// PART 3: WHAT NEEDS TO CHANGE TO GET 90%+ WIN RATE
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  PART 3: WHAT NEEDS TO CHANGE FOR 90%+ WIN RATE`)
console.log(`${"=".repeat(74)}`)

// Test: pick ONLY trades where MFE >= 0.7% (i.e., the move does reach our TP)
// What filters predict MFE >= 0.7%?
const hitters = allTrades.filter(t => t.mxF >= 0.7)
const missers = allTrades.filter(t => t.mxF < 0.7)

console.log(`\n  Trades where 0.7% TP WOULD be hit: ${hitters.length}/${allTrades.length} (${(hitters.length/allTrades.length*100).toFixed(1)}%)`)
console.log(`  Trades where 0.7% TP would NOT be hit: ${missers.length}/${allTrades.length} (${(missers.length/allTrades.length*100).toFixed(1)}%)\n`)

const avg = (arr, fn) => arr.length ? arr.reduce((s, x) => s + fn(x), 0) / arr.length : 0

console.log(`  WHAT PREDICTS TP HIT vs MISS?`)
console.log(`  ${"Feature".padEnd(30)} ${"TP Hitters".padStart(12)} ${"TP Missers".padStart(12)} ${"Signal?".padStart(8)}`)
console.log(`  ${"-".repeat(65)}`)

const feats = [
  ["Score", t => t.score],
  ["|Move %| at entry", t => Math.abs(t.mp)],
  ["Volume rate", t => t.vr],
  ["Volume cum at entry", t => t.vc],
  ["|Gap| %", t => Math.abs(t.gap)],
  ["Entry price (Rs)", t => t.ep],
  ["Entry bucket", t => t.eb],
]
for (const [name, fn] of feats) {
  const hAvg = avg(hitters, fn), mAvg = avg(missers, fn)
  const diff = Math.abs(hAvg - mAvg) / (Math.abs(hAvg) || 1) * 100
  console.log(`  ${name.padEnd(30)} ${hAvg.toFixed(2).padStart(12)} ${mAvg.toFixed(2).padStart(12)} ${diff > 20 ? "<<<" : ""}`.padEnd(8))
}

// Direction split
console.log(`\n  By direction:`)
for (const dir of ["BUY", "SELL"]) {
  const dt = allTrades.filter(t => t.dir === dir)
  const h = dt.filter(t => t.mxF >= 0.7).length
  console.log(`  ${dir}: ${h}/${dt.length} hit 0.7% TP (${(h/dt.length*100).toFixed(1)}%)`)
}

// Score filter
console.log(`\n  By score (higher score = better signal):`)
for (let sc = 4; sc <= 10; sc++) {
  const st = allTrades.filter(t => t.score >= sc)
  if (st.length < 10) continue
  const h = st.filter(t => t.mxF >= 0.7).length
  console.log(`  score >= ${sc}: ${h}/${st.length} hit 0.7% TP (${(h/st.length*100).toFixed(1)}%)`)
}

// Volume rate filter
console.log(`\n  By volume rate at entry:`)
for (const [label, lo, hi] of [["vr < 10", 0, 10], ["vr 10-50", 10, 50], ["vr 50-200", 50, 200], ["vr 200-500", 200, 500], ["vr > 500", 500, 1e9]]) {
  const vt = allTrades.filter(t => t.vr >= lo && t.vr < hi)
  if (vt.length < 10) continue
  const h = vt.filter(t => t.mxF >= 0.7).length
  console.log(`  ${label.padEnd(14)} ${h}/${vt.length} hit 0.7% TP (${(h/vt.length*100).toFixed(1)}%)`)
}

// Move at entry
console.log(`\n  By |move %| at entry:`)
for (const [label, lo, hi] of [["|mp| < 0.5", 0, 0.5], ["0.5-1.0", 0.5, 1.0], ["1.0-1.5", 1.0, 1.5], ["> 1.5", 1.5, 100]]) {
  const mt = allTrades.filter(t => Math.abs(t.mp) >= lo && Math.abs(t.mp) < hi)
  if (mt.length < 10) continue
  const h = mt.filter(t => t.mxF >= 0.7).length
  console.log(`  ${label.padEnd(14)} ${h}/${mt.length} hit 0.7% TP (${(h/mt.length*100).toFixed(1)}%)`)
}

// ============================================================================
// PART 4: COMBINED FILTER OPTIMIZATION for your capital
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  PART 4: BEST ACHIEVABLE WITH Rs 50K CAPITAL`)
console.log(`${"=".repeat(74)}`)

console.log(`\n  Testing filter combos: TP=0.7%, max ${MAX_POS} positions/day, Rs ${PER_TRADE/1000}K each\n`)

const filterCombos = []

for (const tp of [0.5, 0.7, 1.0]) {
  for (const minSc of [4, 5, 7]) {
    for (const dirFilter of ["BOTH", "SELL"]) {
      for (const vrMin of [0, 10, 50]) {
        for (const fnoOnly of [false, true]) {
          const filtered = allTrades.filter(t => {
            if (t.score < minSc) return false
            if (dirFilter !== "BOTH" && t.dir !== dirFilter) return false
            if (t.vr < vrMin) return false
            if (fnoOnly && !t.isFno) return false
            return true
          })

          if (filtered.length < 30) continue

          // Simulate daily with position cap
          const dailyPnls = []
          let totalW = 0, totalT = 0

          for (const date of allDates) {
            const day = filtered.filter(t => t.date === date)
              .sort((a, b) => b.score - a.score)
              .slice(0, MAX_POS)

            let pnl = 0
            for (const t of day) {
              const qty = Math.max(Math.floor(PER_TRADE / t.ep), 1)
              const ret = t.mxF >= tp ? tp : t.ret
              pnl += t.ep * (ret / 100) * qty
              if (ret > 0) totalW++
              totalT++
            }
            dailyPnls.push(pnl)
          }

          const totPnl = dailyPnls.reduce((s, p) => s + p, 0)
          const avgD = totPnl / nd
          const posDays = dailyPnls.filter(p => p > 0).length
          const wr = totalT > 0 ? totalW / totalT * 100 : 0
          const roc = avgD / CAPITAL * 100
          const avgTradesDay = totalT / nd

          filterCombos.push({
            label: `TP=${tp}% sc>=${minSc} ${dirFilter} vr>=${vrMin} ${fnoOnly?"F&O":"all"}`,
            totalT, wr, totPnl, avgD, roc, posDays, avgTradesDay,
          })
        }
      }
    }
  }
}

// Sort by ROC
filterCombos.sort((a, b) => b.roc - a.roc)

console.log(`  ${"Filter".padEnd(42)} ${"Tr/day".padStart(6)} ${"Win%".padStart(6)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"PosDays".padStart(8)}`)
console.log(`  ${"-".repeat(80)}`)
for (const f of filterCombos.slice(0, 25)) {
  console.log(`  ${f.label.padEnd(42)} ${f.avgTradesDay.toFixed(1).padStart(6)} ${(f.wr.toFixed(1)+"%").padStart(6)} ${("Rs "+f.avgD.toFixed(0)).padStart(8)} ${(f.roc.toFixed(2)+"%").padStart(7)} ${(f.posDays+"/"+nd).padStart(8)}`)
}

if (filterCombos.length > 0) {
  const best = filterCombos[0]
  console.log(`\n  ${"=".repeat(55)}`)
  console.log(`  BEST SETUP FOR Rs 50K CAPITAL:`)
  console.log(`  ${best.label}`)
  console.log(`  ${best.avgTradesDay.toFixed(1)} trades/day | ${best.wr.toFixed(1)}% win`)
  console.log(`  Rs ${best.avgD.toFixed(0)}/day | ${best.roc.toFixed(2)}% daily ROC`)
  console.log(`  ${best.posDays}/${nd} positive days`)
  console.log(`  Monthly: ~Rs ${(best.avgD * 22).toFixed(0)} (~${(best.roc * 22).toFixed(1)}% ROC)`)
  console.log(`  ${"=".repeat(55)}`)
}

// ============================================================================
// PART 5: MFE ANALYSIS — what % of YOUR signals reach each TP level?
// ============================================================================

console.log(`\n${"=".repeat(74)}`)
console.log(`  PART 5: MFE DISTRIBUTION OF YOUR ACTUAL SIGNALS`)
console.log(`${"=".repeat(74)}`)

console.log(`\n  What % of your signals reach each favorable level BEFORE reversing?`)
console.log(`  (This is the TRUE TP hit rate — not survivorship bias)\n`)

for (const [label, subset] of [["ALL signals", allTrades], ["F&O only", fno]]) {
  console.log(`  ${label} (${subset.length} trades):`)
  for (const threshold of [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]) {
    const hits = subset.filter(t => t.mxF >= threshold).length
    console.log(`    MFE >= ${threshold.toFixed(1)}%: ${hits}/${subset.length} (${(hits/subset.length*100).toFixed(1)}%)`)
  }
  console.log()
}

console.log(`Done in ${((Date.now()-t0)/1000).toFixed(1)}s`)
