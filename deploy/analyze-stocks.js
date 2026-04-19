#!/usr/bin/env bun
// DEEP STOCK-LEVEL ANALYSIS: which stocks to trade, which to blacklist
// Goal: curate a watchlist that gives 2-5% daily ROC consistently
// Usage: bun deploy/analyze-stocks.js

import { readFileSync, createReadStream, writeFileSync } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const CONFIG_FILE = process.argv[3] || "backtest-config.json"

let config
try { config = JSON.parse(readFileSync(CONFIG_FILE, "utf-8")) } catch {
  config = { buy_entry_start: 2, buy_entry_end: 3, sell_entry_start: 2, sell_entry_end: 4,
    buy_min_move_pct: 0.45, sell_min_move_pct: 0.25, buy_min_volume: 300, sell_min_volume: 450,
    buy_min_score: 4, sell_min_score: 4, buy_sl_pct: 1.2, sell_sl_pct: 1.8,
    hard_exit_bucket: 35, sell_hard_exit_bucket: 71, buy_qty_multiplier: 2, sell_qty_multiplier: 2,
    gap_filter_min_pct: -100, gap_filter_max_pct: 100, buy_gap_min_pct: 0, buy_gap_max_pct: 100,
    sell_gap_min_pct: -100, sell_gap_max_pct: 10, quantity: 1, min_move_pct: 0.15 }
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

// Signal engine (same as analyze.js)
function computeSignal(buckets, dayOpen, gapPct, cfg) {
  if (buckets.length < 2) return null
  if (gapPct !== 0 && (gapPct < cfg.gap_filter_min_pct || gapPct > cfg.gap_filter_max_pct)) return null
  const op = buckets.find(b => b.b >= 1)?.c || dayOpen
  if (op <= 0) return null
  const wS = Math.min(cfg.buy_entry_start, cfg.sell_entry_start)
  const wE = Math.max(cfg.buy_entry_end, cfg.sell_entry_end)
  const wide = buckets.filter(b => b.b >= wS && b.b <= wE)
  if (!wide.length) return null
  const wL = wide[wide.length - 1]
  const mp = (wL.c - op) / op * 100
  const dir = mp > 0 ? "BUY" : "SELL", ds = dir === "BUY" ? 1 : -1
  const [eS, eE] = dir === "BUY" ? [cfg.buy_entry_start, cfg.buy_entry_end] : [cfg.sell_entry_start, cfg.sell_entry_end]
  const es = buckets.filter(b => b.b >= eS && b.b <= eE)
  if (!es.length) return null
  const last = es[es.length - 1]
  const dm = (last.c - op) / op * 100
  if (Math.abs(dm) < (dir === "BUY" ? cfg.buy_min_move_pct : cfg.sell_min_move_pct)) return null
  if (dir === "SELL" && gapPct !== 0 && gapPct < cfg.sell_gap_min_pct) return null
  if (dir === "BUY" && gapPct !== 0 && gapPct > cfg.buy_gap_max_pct) return null
  if (dir === "BUY" && gapPct !== 0 && gapPct < cfg.buy_gap_min_pct) return null
  if (dir === "SELL" && gapPct !== 0 && gapPct > cfg.sell_gap_max_pct) return null
  const mvr = dir === "BUY" ? (cfg.buy_min_vol_rate || 0) : (cfg.sell_min_vol_rate || 0)
  if (last.vr < mvr) return null
  const volE = es.reduce((s, b) => s + b.v, 0)
  let sc = 0
  if (Math.abs(mp) >= (cfg.min_move_pct || 0.15)) sc += 2
  if (Math.abs(mp) >= (cfg.min_move_pct || 0.15) * 2) sc += 2
  const dv = dir === "BUY" ? cfg.buy_min_volume : cfg.sell_min_volume
  if (volE >= dv) sc += 1; if (volE >= dv * 2) sc += 2
  if (es.some(b => dir === "BUY" ? b.c > b.vw : b.c < b.vw)) sc += 1
  if (Math.abs(gapPct) > 0.3 && gapPct * ds > 0) sc += 1
  if (last.br > 0.6) sc += 1
  if (sc < (dir === "BUY" ? cfg.buy_min_score : cfg.sell_min_score)) return null
  const ep = last.c
  const tp = dir === "BUY" ? (cfg.buy_tp_pct || 0) : (cfg.sell_tp_pct || 0)
  const sl = dir === "BUY" ? (cfg.buy_sl_pct || 0) : (cfg.sell_sl_pct || 0)
  return { dir, sc, ep, eb: last.b, tp: ep*(1+ds*tp/100), sl: ep*(1-ds*sl/100), op, mp: dm, vr: last.vr, vw: last.vw, volE }
}

console.log(`Streaming ${DATA_FILE}...`)
const start = Date.now()

// Per-stock accumulators
const stockData = {} // symbol -> { trades: [...], days, avgPrice, avgVol, isFno }
let lineCount = 0

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lineCount++
  const { symbol, date, dayOpen, gapPct, buckets, f5Range, maxUp45, maxDown45 } = sd
  const isFno = fnoSet.has(symbol)

  if (!stockData[symbol]) stockData[symbol] = { trades: [], days: 0, totalVol: 0, totalPrice: 0, isFno, moveDays: 0, totalMaxMove: 0 }
  const st = stockData[symbol]
  st.days++
  const maxMove = Math.max(maxUp45, maxDown45)
  if (maxMove >= 1) st.moveDays++
  st.totalMaxMove += maxMove
  st.totalPrice += dayOpen
  const lastBucket = buckets[buckets.length - 1]
  if (lastBucket) st.totalVol += lastBucket.vc

  const sig = computeSignal(buckets, dayOpen, gapPct, config)
  if (!sig) continue

  const ds = sig.dir === "BUY" ? 1 : -1
  let mxF = 0, mxA = 0, exitP = null, exitR = null
  const exitLim = sig.dir === "BUY" ? config.hard_exit_bucket : config.sell_hard_exit_bucket

  for (const b of buckets) {
    if (b.b <= sig.eb) continue
    const fav = ds > 0 ? (b.h - sig.ep) / sig.ep * 100 : (sig.ep - b.l) / sig.ep * 100
    const adv = ds > 0 ? (sig.ep - b.l) / sig.ep * 100 : (b.h - sig.ep) / sig.ep * 100
    if (fav > mxF) mxF = fav
    if (adv > mxA) mxA = adv
    if (!exitP) {
      const tpA = Math.abs(sig.tp - sig.ep) > 0.001
      if (tpA && ((ds > 0 && b.c >= sig.tp) || (ds < 0 && b.c <= sig.tp))) { exitP = b.c; exitR = "TP" }
      const slA = Math.abs(sig.sl - sig.ep) > 0.001
      if (!exitP && slA && ((ds > 0 && b.c <= sig.sl) || (ds < 0 && b.c >= sig.sl))) { exitP = b.c; exitR = "SL" }
      if (!exitP && b.b >= exitLim) { exitP = b.c; exitR = "TIME" }
    }
  }
  if (!exitP) { const l = buckets[buckets.length - 1]; exitP = l.c; exitR = "TIME" }
  const ret = ds > 0 ? (exitP - sig.ep) / sig.ep * 100 : (sig.ep - exitP) / sig.ep * 100

  st.trades.push({ dt: date, dir: sig.dir, ret, mxF, mxA, sc: sig.sc, ep: sig.ep, vr: sig.vr, gap: gapPct, volE: sig.volE })

  if (lineCount % 20000 === 0) process.stderr.write(`  ${lineCount} lines...\r`)
}

const symbols = Object.keys(stockData)
console.log(`\nProcessed ${lineCount} stock-days, ${symbols.length} stocks in ${((Date.now()-start)/1000).toFixed(1)}s`)

// ==========================================================================
// SCORING EACH STOCK
// ==========================================================================

const TP = 1.0 // simulate TP

const stockScores = symbols.map(sym => {
  const d = stockData[sym]
  const trades = d.trades
  const n = trades.length
  const avgPrice = d.days > 0 ? d.totalPrice / d.days : 0
  const avgDailyVol = d.days > 0 ? d.totalVol / d.days : 0
  const moveFreq = d.days > 0 ? d.moveDays / d.days * 100 : 0
  const avgMaxMove = d.days > 0 ? d.totalMaxMove / d.days : 0

  if (n < 3) return null // not enough trades to evaluate

  // Simulate with TP
  let wins = 0, totalRet = 0, totalMFE = 0, totalMAE = 0
  const dailyRets = {} // date -> simulated return

  for (const t of trades) {
    const simRet = t.mxF >= TP ? TP : t.ret
    if (simRet > 0) wins++
    totalRet += simRet
    totalMFE += t.mxF
    totalMAE += t.mxA
    dailyRets[t.dt] = (dailyRets[t.dt] || 0) + simRet
  }

  const winRate = wins / n * 100
  const avgRet = totalRet / n
  const avgMFE = totalMFE / n
  const avgMAE = totalMAE / n
  const mfeMaeRatio = avgMAE > 0 ? avgMFE / avgMAE : 0

  // Consistency: how many trading days were positive?
  const tradingDates = Object.keys(dailyRets)
  const posDays = tradingDates.filter(d => dailyRets[d] > 0).length
  const consistency = tradingDates.length > 0 ? posDays / tradingDates.length * 100 : 0

  // Per-trade PnL with Rs 25K position
  const avgQty = avgPrice > 0 ? Math.floor(25000 / avgPrice) : 1
  const avgPnlPerTrade = avgPrice * (avgRet / 100) * avgQty
  const totalPnl = trades.reduce((s, t) => {
    const r = t.mxF >= TP ? TP : t.ret
    const q = t.ep > 0 ? Math.floor(25000 / t.ep) : 1
    return s + t.ep * (r / 100) * q
  }, 0)

  // Direction split
  const buyTrades = trades.filter(t => t.dir === "BUY")
  const sellTrades = trades.filter(t => t.dir === "SELL")
  const buyWinRate = buyTrades.length >= 2 ? buyTrades.filter(t => (t.mxF >= TP ? TP : t.ret) > 0).length / buyTrades.length * 100 : -1
  const sellWinRate = sellTrades.length >= 2 ? sellTrades.filter(t => (t.mxF >= TP ? TP : t.ret) > 0).length / sellTrades.length * 100 : -1

  // Max consecutive losses
  let maxLossStreak = 0, curStreak = 0
  for (const t of trades) {
    if ((t.mxF >= TP ? TP : t.ret) <= 0) { curStreak++; maxLossStreak = Math.max(maxLossStreak, curStreak) }
    else curStreak = 0
  }

  // Composite score (higher = better stock to trade)
  // Weight: win_rate(30%) + consistency(20%) + avg_return(20%) + mfe_mae(15%) + move_freq(15%)
  const compositeScore =
    (winRate / 100) * 30 +
    (consistency / 100) * 20 +
    (Math.min(avgRet, 1.0) / 1.0) * 20 +  // cap at 1% for scoring
    (Math.min(mfeMaeRatio, 2.0) / 2.0) * 15 +
    (moveFreq / 100) * 15

  return {
    sym, n, days: d.days, avgPrice: Math.round(avgPrice), avgDailyVol: Math.round(avgDailyVol),
    moveFreq, avgMaxMove, isFno: d.isFno,
    winRate, avgRet, avgMFE, avgMAE, mfeMaeRatio,
    consistency, posDays, tradingDates: tradingDates.length,
    avgPnlPerTrade, totalPnl,
    buyN: buyTrades.length, sellN: sellTrades.length, buyWR: buyWinRate, sellWR: sellWinRate,
    maxLossStreak, compositeScore,
    // Per-day returns for simulation
    dailyRets, trades,
  }
}).filter(Boolean)

stockScores.sort((a, b) => b.compositeScore - a.compositeScore)

const allDates = [...new Set(stockScores.flatMap(s => Object.keys(s.dailyRets)))].sort()
const numDays = allDates.length

console.log(`\n${"=".repeat(80)}`)
console.log(`  STOCK-LEVEL DEEP ANALYSIS — ${stockScores.length} stocks with 3+ trades, TP=${TP}%`)
console.log(`${"=".repeat(80)}`)

// --- 1. TOP 50 STOCKS (by composite score) ---
console.log(`\n  --- TOP 50 STOCKS (by composite score) ---`)
console.log(`  ${"Sym".padEnd(18)} ${"Tr".padStart(4)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Cons%".padStart(6)} ${"MFE/MAE".padStart(7)} ${"PnL".padStart(8)} ${"AvgPx".padStart(7)} ${"DlyVol".padStart(8)} ${"Score".padStart(6)} ${"F&O".padStart(4)}`)
console.log(`  ${"-".repeat(90)}`)
for (const s of stockScores.slice(0, 50)) {
  console.log(`  ${s.sym.padEnd(18)} ${String(s.n).padStart(4)} ${(s.winRate.toFixed(0)+"%").padStart(6)} ${("+"+s.avgRet.toFixed(2)+"%").padStart(7)} ${(s.consistency.toFixed(0)+"%").padStart(6)} ${s.mfeMaeRatio.toFixed(2).padStart(7)} ${s.totalPnl.toFixed(0).padStart(8)} ${String(s.avgPrice).padStart(7)} ${(s.avgDailyVol>1e6?(s.avgDailyVol/1e6).toFixed(1)+"M":s.avgDailyVol>1e3?(s.avgDailyVol/1e3).toFixed(0)+"K":String(s.avgDailyVol)).padStart(8)} ${s.compositeScore.toFixed(1).padStart(6)} ${s.isFno?"yes":"".padStart(4)}`)
}

// --- 2. BOTTOM 50 STOCKS (blacklist) ---
const blacklistCandidates = stockScores.filter(s => s.n >= 5).sort((a, b) => a.compositeScore - b.compositeScore)
console.log(`\n  --- BOTTOM 50 STOCKS (BLACKLIST) ---`)
console.log(`  ${"Sym".padEnd(18)} ${"Tr".padStart(4)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Cons%".padStart(6)} ${"MaxLoss".padStart(7)} ${"PnL".padStart(8)} ${"Score".padStart(6)}`)
console.log(`  ${"-".repeat(65)}`)
for (const s of blacklistCandidates.slice(0, 50)) {
  console.log(`  ${s.sym.padEnd(18)} ${String(s.n).padStart(4)} ${(s.winRate.toFixed(0)+"%").padStart(6)} ${((s.avgRet>=0?"+":"")+s.avgRet.toFixed(2)+"%").padStart(7)} ${(s.consistency.toFixed(0)+"%").padStart(6)} ${String(s.maxLossStreak).padStart(7)} ${s.totalPnl.toFixed(0).padStart(8)} ${s.compositeScore.toFixed(1).padStart(6)}`)
}

// --- 3. DIRECTION BIAS: stocks that ONLY work in one direction ---
console.log(`\n  --- STOCKS WITH STRONG DIRECTIONAL BIAS ---`)
console.log(`  (Buy WR vs Sell WR differ by 25%+, min 3 trades each direction)\n`)
const biased = stockScores
  .filter(s => s.buyN >= 3 && s.sellN >= 3 && s.buyWR >= 0 && s.sellWR >= 0)
  .filter(s => Math.abs(s.buyWR - s.sellWR) >= 25)
  .sort((a, b) => Math.abs(b.buyWR - b.sellWR) - Math.abs(a.buyWR - a.sellWR))

console.log(`  ${"Sym".padEnd(18)} ${"BuyN".padStart(5)} ${"BuyWR".padStart(6)} ${"SellN".padStart(6)} ${"SellWR".padStart(7)} ${"Bias".padStart(12)}`)
for (const s of biased.slice(0, 30)) {
  const bias = s.buyWR > s.sellWR ? "BUY-only" : "SELL-only"
  console.log(`  ${s.sym.padEnd(18)} ${String(s.buyN).padStart(5)} ${(s.buyWR.toFixed(0)+"%").padStart(6)} ${String(s.sellN).padStart(6)} ${(s.sellWR.toFixed(0)+"%").padStart(7)} ${bias.padStart(12)}`)
}

// --- 4. UNIVERSE SIZE OPTIMIZATION ---
console.log(`\n  --- UNIVERSE SIZE vs PERFORMANCE ---`)
console.log(`  (Simulate: only trade top-N stocks by composite score, TP=${TP}%, Rs 25K/trade, 5x on Rs 1L)\n`)

const capital = 100000
console.log(`  ${"Universe".padEnd(10)} ${"Signals".padStart(8)} ${"Sig/day".padStart(8)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Rs/day".padStart(8)} ${"ROC%".padStart(7)} ${"PosDays".padStart(8)} ${"MaxDD".padStart(8)}`)
console.log(`  ${"-".repeat(75)}`)

for (const topN of [20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, stockScores.length]) {
  const universe = new Set(stockScores.slice(0, topN).map(s => s.sym))
  const uTrades = stockScores.filter(s => universe.has(s.sym)).flatMap(s => s.trades.map(t => ({ ...t, sym: s.sym, ep: t.ep })))

  if (uTrades.length < 10) continue

  // Daily PnL simulation
  const dailyPnls = []
  for (const date of allDates) {
    const dayT = uTrades.filter(t => t.dt === date).sort((a, b) => b.sc - a.sc).slice(0, 20) // max 20 positions
    let pnl = 0
    for (const t of dayT) {
      const qty = Math.floor(25000 / t.ep) || 1
      const ret = t.mxF >= TP ? TP : t.ret
      pnl += t.ep * (ret / 100) * qty
    }
    dailyPnls.push(pnl)
  }

  const totalPnl = dailyPnls.reduce((s, p) => s + p, 0)
  const avgDaily = totalPnl / numDays
  const posDays = dailyPnls.filter(p => p > 0).length
  const roc = avgDaily / capital * 100

  // Max drawdown
  let peak = 0, maxDD = 0, equity = 0
  for (const p of dailyPnls) { equity += p; if (equity > peak) peak = equity; maxDD = Math.max(maxDD, peak - equity) }

  const uWins = uTrades.filter(t => (t.mxF >= TP ? TP : t.ret) > 0).length
  const uAvg = uTrades.reduce((s, t) => s + (t.mxF >= TP ? TP : t.ret), 0) / uTrades.length

  const label = topN >= stockScores.length ? `ALL(${stockScores.length})` : `Top-${topN}`
  console.log(`  ${label.padEnd(10)} ${String(uTrades.length).padStart(8)} ${(uTrades.length/numDays).toFixed(1).padStart(8)} ${(uWins/uTrades.length*100).toFixed(1).padStart(5)}% ${("+"+uAvg.toFixed(2)+"%").padStart(7)} ${avgDaily.toFixed(0).padStart(8)} ${(roc.toFixed(2)+"%").padStart(7)} ${(posDays+"/"+numDays).padStart(8)} ${maxDD.toFixed(0).padStart(8)}`)
}

// --- 5. STOCK CHARACTERISTICS OF TOP vs BOTTOM ---
console.log(`\n  --- WHAT MAKES A STOCK PROFITABLE? (Top 100 vs Bottom 100) ---`)
const top100 = stockScores.slice(0, 100)
const bot100 = stockScores.slice(-100)
const avgOf = (arr, fn) => arr.reduce((s, x) => s + fn(x), 0) / arr.length

console.log(`  ${"Feature".padEnd(30)} ${"Top 100".padStart(12)} ${"Bottom 100".padStart(12)}`)
console.log(`  ${"-".repeat(56)}`)
console.log(`  ${"Avg price".padEnd(30)} ${"Rs "+avgOf(top100, s=>s.avgPrice).toFixed(0).padStart(9)} ${"Rs "+avgOf(bot100, s=>s.avgPrice).toFixed(0).padStart(9)}`)
console.log(`  ${"Avg daily volume".padEnd(30)} ${avgOf(top100, s=>s.avgDailyVol).toFixed(0).padStart(12)} ${avgOf(bot100, s=>s.avgDailyVol).toFixed(0).padStart(12)}`)
console.log(`  ${"Move frequency (1%+ days)".padEnd(30)} ${(avgOf(top100, s=>s.moveFreq).toFixed(1)+"%").padStart(12)} ${(avgOf(bot100, s=>s.moveFreq).toFixed(1)+"%").padStart(12)}`)
console.log(`  ${"Avg max move".padEnd(30)} ${(avgOf(top100, s=>s.avgMaxMove).toFixed(2)+"%").padStart(12)} ${(avgOf(bot100, s=>s.avgMaxMove).toFixed(2)+"%").padStart(12)}`)
console.log(`  ${"Avg MFE (max favorable)".padEnd(30)} ${(avgOf(top100, s=>s.avgMFE).toFixed(2)+"%").padStart(12)} ${(avgOf(bot100, s=>s.avgMFE).toFixed(2)+"%").padStart(12)}`)
console.log(`  ${"Avg MAE (max adverse)".padEnd(30)} ${(avgOf(top100, s=>s.avgMAE).toFixed(2)+"%").padStart(12)} ${(avgOf(bot100, s=>s.avgMAE).toFixed(2)+"%").padStart(12)}`)
console.log(`  ${"MFE/MAE ratio".padEnd(30)} ${avgOf(top100, s=>s.mfeMaeRatio).toFixed(2).padStart(12)} ${avgOf(bot100, s=>s.mfeMaeRatio).toFixed(2).padStart(12)}`)
console.log(`  ${"Avg score".padEnd(30)} ${avgOf(top100, s=>s.trades.reduce((a,t)=>a+t.sc,0)/s.n).toFixed(1).padStart(12)} ${avgOf(bot100, s=>s.trades.reduce((a,t)=>a+t.sc,0)/s.n).toFixed(1).padStart(12)}`)
console.log(`  ${"F&O %".padEnd(30)} ${(top100.filter(s=>s.isFno).length+"%").padStart(12)} ${(bot100.filter(s=>s.isFno).length+"%").padStart(12)}`)
console.log(`  ${"Max loss streak".padEnd(30)} ${avgOf(top100, s=>s.maxLossStreak).toFixed(1).padStart(12)} ${avgOf(bot100, s=>s.maxLossStreak).toFixed(1).padStart(12)}`)

// --- 6. PRICE RANGE ANALYSIS ---
console.log(`\n  --- PRICE RANGE: which price bands are most profitable? ---`)
const priceBands = [["<100", 0, 100], ["100-300", 100, 300], ["300-500", 300, 500], ["500-1000", 500, 1000], ["1K-2K", 1000, 2000], [">2K", 2000, 1e6]]
console.log(`  ${"Band".padEnd(12)} ${"Stocks".padStart(7)} ${"Trades".padStart(7)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"MFE/MAE".padStart(8)} ${"ProfitStocks".padStart(13)}`)
for (const [label, lo, hi] of priceBands) {
  const grp = stockScores.filter(s => s.avgPrice >= lo && s.avgPrice < hi)
  if (grp.length < 3) continue
  const allT = grp.flatMap(s => s.trades)
  const w = allT.filter(t => (t.mxF >= TP ? TP : t.ret) > 0).length
  const avg = allT.reduce((s, t) => s + (t.mxF >= TP ? TP : t.ret), 0) / allT.length
  const mfe = allT.reduce((s, t) => s + t.mxF, 0) / allT.length
  const mae = allT.reduce((s, t) => s + t.mxA, 0) / allT.length
  const prof = grp.filter(s => s.totalPnl > 0).length
  console.log(`  ${label.padEnd(12)} ${String(grp.length).padStart(7)} ${String(allT.length).padStart(7)} ${(w/allT.length*100).toFixed(1).padStart(5)}% ${("+"+avg.toFixed(2)+"%").padStart(7)} ${(mae>0?(mfe/mae).toFixed(2):"0").padStart(8)} ${(prof+"/"+grp.length+" ("+(prof/grp.length*100).toFixed(0)+"%)").padStart(13)}`)
}

// --- 7. VOLUME ANALYSIS ---
console.log(`\n  --- DAILY VOLUME: which liquidity bands work? ---`)
const volBands = [["<10K", 0, 10000], ["10K-50K", 10000, 50000], ["50K-200K", 50000, 200000], ["200K-1M", 200000, 1e6], ["1M-10M", 1e6, 1e7], [">10M", 1e7, 1e12]]
console.log(`  ${"Band".padEnd(12)} ${"Stocks".padStart(7)} ${"Trades".padStart(7)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"MFE/MAE".padStart(8)} ${"ProfitStocks".padStart(13)}`)
for (const [label, lo, hi] of volBands) {
  const grp = stockScores.filter(s => s.avgDailyVol >= lo && s.avgDailyVol < hi)
  if (grp.length < 3) continue
  const allT = grp.flatMap(s => s.trades)
  const w = allT.filter(t => (t.mxF >= TP ? TP : t.ret) > 0).length
  const avg = allT.reduce((s, t) => s + (t.mxF >= TP ? TP : t.ret), 0) / allT.length
  const mfe = allT.reduce((s, t) => s + t.mxF, 0) / allT.length
  const mae = allT.reduce((s, t) => s + t.mxA, 0) / allT.length
  const prof = grp.filter(s => s.totalPnl > 0).length
  console.log(`  ${label.padEnd(12)} ${String(grp.length).padStart(7)} ${String(allT.length).padStart(7)} ${(w/allT.length*100).toFixed(1).padStart(5)}% ${("+"+avg.toFixed(2)+"%").padStart(7)} ${(mae>0?(mfe/mae).toFixed(2):"0").padStart(8)} ${(prof+"/"+grp.length+" ("+(prof/grp.length*100).toFixed(0)+"%)").padStart(13)}`)
}

// --- 8. FINAL RECOMMENDED WATCHLIST ---
console.log(`\n${"=".repeat(80)}`)
console.log(`  FINAL RECOMMENDATIONS`)
console.log(`${"=".repeat(80)}`)

// Tier 1: stocks with winRate >= 70%, consistency >= 60%, 5+ trades
const tier1 = stockScores.filter(s => s.winRate >= 70 && s.consistency >= 60 && s.n >= 5)
  .sort((a, b) => b.totalPnl - a.totalPnl)
console.log(`\n  TIER 1 — HIGH CONVICTION (WR>=70%, Consistency>=60%, 5+ trades): ${tier1.length} stocks`)
console.log(`  ${"Sym".padEnd(18)} ${"Tr".padStart(4)} ${"Win%".padStart(6)} ${"Avg%".padStart(7)} ${"Cons%".padStart(6)} ${"PnL".padStart(8)} ${"AvgPx".padStart(7)} ${"DlyVol".padStart(8)} ${"F&O".padStart(4)}`)
for (const s of tier1.slice(0, 40))
  console.log(`  ${s.sym.padEnd(18)} ${String(s.n).padStart(4)} ${(s.winRate.toFixed(0)+"%").padStart(6)} ${("+"+s.avgRet.toFixed(2)+"%").padStart(7)} ${(s.consistency.toFixed(0)+"%").padStart(6)} ${s.totalPnl.toFixed(0).padStart(8)} ${String(s.avgPrice).padStart(7)} ${(s.avgDailyVol>1e6?(s.avgDailyVol/1e6).toFixed(1)+"M":s.avgDailyVol>1e3?(s.avgDailyVol/1e3).toFixed(0)+"K":String(Math.round(s.avgDailyVol))).padStart(8)} ${s.isFno?"yes":"".padStart(4)}`)

// Tier 2: WR >= 60%, consistency >= 50%, 5+ trades
const tier2 = stockScores.filter(s => s.winRate >= 60 && s.winRate < 70 && s.consistency >= 50 && s.n >= 5)
  .sort((a, b) => b.totalPnl - a.totalPnl)
console.log(`\n  TIER 2 — GOOD (WR 60-70%, Consistency>=50%): ${tier2.length} stocks`)

// Blacklist: WR < 45% OR consistency < 30% OR avgRet < -0.2%
const blacklist = stockScores.filter(s => s.n >= 5 && (s.winRate < 45 || s.consistency < 30 || s.avgRet < -0.2))
  .sort((a, b) => a.totalPnl - b.totalPnl)
console.log(`\n  BLACKLIST (WR<45% OR Cons<30% OR Avg<-0.2%): ${blacklist.length} stocks`)
console.log(`  Top 30 worst:`)
for (const s of blacklist.slice(0, 30))
  console.log(`  ${s.sym.padEnd(18)} ${String(s.n).padStart(4)} ${(s.winRate.toFixed(0)+"%").padStart(6)} ${((s.avgRet>=0?"+":"")+s.avgRet.toFixed(2)+"%").padStart(7)} ${(s.consistency.toFixed(0)+"%").padStart(6)} ${s.totalPnl.toFixed(0).padStart(8)}`)

// --- 9. Simulate TIER1 only ---
console.log(`\n  --- TIER 1 ONLY SIMULATION (5x margin, Rs 1L, Rs 25K/trade) ---`)
const t1Set = new Set(tier1.map(s => s.sym))
const t1Trades = stockScores.filter(s => t1Set.has(s.sym)).flatMap(s => s.trades.map(t => ({ ...t, sym: s.sym })))
const t1Daily = []
for (const date of allDates) {
  const dt = t1Trades.filter(t => t.dt === date).sort((a, b) => b.sc - a.sc).slice(0, 20)
  let pnl = 0
  for (const t of dt) { const q = Math.floor(25000 / t.ep) || 1; pnl += t.ep * ((t.mxF >= TP ? TP : t.ret) / 100) * q }
  t1Daily.push({ date, pnl, n: dt.length })
}
const t1Total = t1Daily.reduce((s, d) => s + d.pnl, 0)
const t1Pos = t1Daily.filter(d => d.pnl > 0).length
const t1Avg = t1Total / numDays
const t1AvgTrades = t1Daily.reduce((s, d) => s + d.n, 0) / numDays
console.log(`  ${tier1.length} stocks | ${t1Trades.length} trades (${t1AvgTrades.toFixed(1)}/day)`)
console.log(`  Rs ${t1Avg.toFixed(0)}/day | ${(t1Avg/capital*100).toFixed(2)}% ROC | ${t1Pos}/${numDays} positive days`)
console.log(`  Total: Rs ${t1Total.toFixed(0)} over ${numDays} days`)

// Save watchlists
const watchlist = { tier1: tier1.map(s => s.sym), tier2: tier2.map(s => s.sym), blacklist: blacklist.map(s => s.sym) }
writeFileSync("data/recommended-watchlist.json", JSON.stringify(watchlist, null, 2))
console.log(`\n  Watchlists saved to data/recommended-watchlist.json`)
console.log(`  Tier 1: ${tier1.length} | Tier 2: ${tier2.length} | Blacklist: ${blacklist.length}`)

console.log(`\nDone in ${((Date.now()-start)/1000).toFixed(1)}s`)
