#!/usr/bin/env node
// Grid search optimizer: tests hundreds of config combos via Rust backtest API
// Usage: node deploy/optimize-backtest.js

const API = process.env.API_URL || 'http://localhost:8080'
const FROM = '2025-12-01'
const TO = '2026-03-28'

// Base config — ALL SignalConfig fields (Rust requires every field, no defaults)
const BASE = {
  entry_bucket_start: 2,
  entry_bucket_end: 3,
  min_move_pct: 0.15,
  min_volume: 500,
  min_score: 5,
  tp_pct: 0.7,
  sl_pct: 0.6,
  hard_exit_bucket: 35,
  quantity: 1,
  gap_filter_min_pct: -100,
  gap_filter_max_pct: 100,
  sell_gap_min_pct: -100,
  min_vol_rate: 0,
  sell_hard_exit_bucket: 71,
  buy_gap_max_pct: 100,
  direction_filter: 'BOTH',
  capital_per_trade: 100000,
  buy_tp_pct: 0.7,
  buy_sl_pct: 0.5,
  sell_tp_pct: 0.7,
  sell_sl_pct: 0.5,
  buy_min_move_pct: 0.25,
  sell_min_move_pct: 0.25,
  buy_min_vol_rate: 0,
  sell_min_vol_rate: 0,
  buy_capital_per_trade: 10000,
  sell_capital_per_trade: 10000,
  buy_qty_multiplier: 1,
  sell_qty_multiplier: 1,
  buy_entry_start: 2,
  buy_entry_end: 4,
  sell_entry_start: 2,
  sell_entry_end: 4,
  buy_min_volume: 300,
  sell_min_volume: 300,
  buy_min_score: 4,
  sell_min_score: 4,
  buy_gap_min_pct: -100,
  sell_gap_max_pct: 100,
  cherry_pick_enabled: true,
  total_capital: 500000,
  max_positions: 12,
  min_position_value: 5000,
  tp_score_scaling: false,
  max_loss_pct: 0,
  volume_rank_mode: false,
  vr_min_move_pct: 0.3,
  vr_min_vol_rate: 0,
}

// Grid search dimensions
const GRIDS = {
  direction_filter: ['SELL', 'BUY', 'BOTH'],
  tp: [0.5, 0.7, 1.0, 1.5, 2.0],
  sl: [0.3, 0.5, 0.7, 1.0],
  buy_min_move_pct: [0.25, 0.4],
  sell_min_move_pct: [0.15, 0.25],
  buy_min_score: [3, 4, 5],
  sell_min_score: [3, 4, 5],
  tp_score_scaling: [false, true],
  sell_hard_exit_bucket: [46, 71],
  hard_exit_bucket: [25, 35],
  buy_entry_end: [3, 4, 5],
  sell_entry_end: [3, 4, 5],
  buy_gap_min_pct: [-100, 0.5],
  sell_gap_max_pct: [3, 100],
  volume_rank_mode: [false, true],
}

// Generate smart combos (not full cartesian — prioritize impactful variables)
function* generateCombos() {
  // Phase 1: TP/SL ratio × direction (most impactful)
  for (const dir of GRIDS.direction_filter) {
    for (const tp of GRIDS.tp) {
      for (const sl of GRIDS.sl) {
        if (tp <= sl * 0.5) continue // skip obviously bad ratios
        for (const scaling of GRIDS.tp_score_scaling) {
          yield {
            label: `${dir} TP=${tp} SL=${sl} scl=${scaling ? 'Y' : 'N'}`,
            direction_filter: dir,
            buy_tp_pct: tp, sell_tp_pct: tp,
            buy_sl_pct: sl, sell_sl_pct: sl,
            tp_score_scaling: scaling,
            buy_min_move_pct: 0.4, sell_min_move_pct: 0.25,
            buy_min_score: 4, sell_min_score: 4,
            hard_exit_bucket: 35, sell_hard_exit_bucket: 71,
            buy_entry_start: 2, buy_entry_end: 4,
            sell_entry_start: 2, sell_entry_end: 4,
            buy_gap_min_pct: 0.5, sell_gap_max_pct: 3,
            buy_min_volume: 300, sell_min_volume: 450,
          }
        }
      }
    }
  }

  // Phase 2: Volume rank mode variations
  for (const dir of ['SELL', 'BOTH']) {
    for (const tp of [0.7, 1.0, 1.5]) {
      for (const sl of [0.3, 0.5, 0.7]) {
        yield {
          label: `VR ${dir} TP=${tp} SL=${sl}`,
          direction_filter: dir,
          buy_tp_pct: tp, sell_tp_pct: tp,
          buy_sl_pct: sl, sell_sl_pct: sl,
          tp_score_scaling: false,
          volume_rank_mode: true,
          vr_min_move_pct: 0.3, vr_min_vol_rate: 0,
          buy_min_move_pct: 0.3, sell_min_move_pct: 0.3,
          buy_min_score: 3, sell_min_score: 3,
          hard_exit_bucket: 35, sell_hard_exit_bucket: 71,
          buy_entry_start: 2, buy_entry_end: 4,
          sell_entry_start: 2, sell_entry_end: 4,
          buy_gap_min_pct: -100, sell_gap_max_pct: 100,
          buy_min_volume: 100, sell_min_volume: 100,
        }
      }
    }
  }

  // Phase 3: Entry window + exit bucket variations on best TP/SL ratios
  for (const dir of ['SELL', 'BOTH']) {
    for (const exitB of [35, 46, 61, 71]) {
      for (const entryEnd of [3, 4, 5]) {
        yield {
          label: `${dir} exit=${exitB} ent=2-${entryEnd} TP=1 SL=0.5`,
          direction_filter: dir,
          buy_tp_pct: 1.0, sell_tp_pct: 1.0,
          buy_sl_pct: 0.5, sell_sl_pct: 0.5,
          tp_score_scaling: false,
          buy_min_move_pct: 0.25, sell_min_move_pct: 0.25,
          buy_min_score: 4, sell_min_score: 4,
          hard_exit_bucket: exitB, sell_hard_exit_bucket: exitB,
          buy_entry_start: 2, buy_entry_end: entryEnd,
          sell_entry_start: 2, sell_entry_end: entryEnd,
          buy_gap_min_pct: -100, sell_gap_max_pct: 100,
          buy_min_volume: 300, sell_min_volume: 300,
        }
      }
    }
  }

  // Phase 4: Min score + move filter variations
  for (const dir of ['SELL', 'BOTH']) {
    for (const score of [3, 4, 5, 6]) {
      for (const move_pct of [0.15, 0.25, 0.4]) {
        yield {
          label: `${dir} sc>=${score} mv>=${move_pct} TP=1 SL=0.5`,
          direction_filter: dir,
          buy_tp_pct: 1.0, sell_tp_pct: 1.0,
          buy_sl_pct: 0.5, sell_sl_pct: 0.5,
          tp_score_scaling: false,
          buy_min_move_pct: move_pct, sell_min_move_pct: move_pct,
          buy_min_score: score, sell_min_score: score,
          hard_exit_bucket: 46, sell_hard_exit_bucket: 71,
          buy_entry_start: 2, buy_entry_end: 4,
          sell_entry_start: 2, sell_entry_end: 4,
          buy_gap_min_pct: -100, sell_gap_max_pct: 100,
          buy_min_volume: 300, sell_min_volume: 300,
        }
      }
    }
  }
}

async function runBacktest(overrides) {
  const body = { from: FROM, to: TO, ...BASE, ...overrides }
  const resp = await fetch(`${API}/api/backtest/compute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

function analyze(result) {
  const sigs = result.signals || []
  if (sigs.length === 0) return null
  const closed = sigs.filter(s => s.exit_reason)
  const wins = closed.filter(s => (s.pnl_rupees || 0) > 0).length
  const netPnl = closed.reduce((s, sig) => s + (sig.pnl_rupees || 0), 0)

  // Per-day metrics
  const byDate = {}
  for (const s of sigs) {
    if (!byDate[s.trading_date]) byDate[s.trading_date] = []
    byDate[s.trading_date].push(s)
  }
  const dates = Object.keys(byDate).sort()
  const dailyRocs = []
  let greenDays = 0
  for (const d of dates) {
    const ds = byDate[d]
    const cap = ds.reduce((s, sig) => s + sig.entry_price * sig.quantity, 0)
    const pnl = ds.reduce((s, sig) => s + (sig.pnl_rupees || 0), 0)
    const margin = cap / 5
    dailyRocs.push(margin > 0 ? (pnl / margin) * 100 : 0)
    if (pnl > 0) greenDays++
  }
  const avgRoc = dailyRocs.length > 0 ? dailyRocs.reduce((a, b) => a + b, 0) / dailyRocs.length : 0
  const tp = closed.filter(s => s.exit_reason === 'TP').length
  const sl = closed.filter(s => s.exit_reason === 'SL').length

  return {
    signals: sigs.length,
    winRate: closed.length > 0 ? (wins / closed.length * 100) : 0,
    avgRoc,
    netPnl,
    greenDays,
    totalDays: dates.length,
    greenPct: dates.length > 0 ? (greenDays / dates.length * 100) : 0,
    tp, sl,
    time: closed.length - tp - sl,
    candidates: result.total_candidates || 0,
  }
}

async function main() {
  const combos = [...generateCombos()]
  console.log(`\n${'='.repeat(100)}`)
  console.log(`  BACKTEST OPTIMIZER — ${combos.length} combos × ${FROM} to ${TO}`)
  console.log(`${'='.repeat(100)}\n`)

  const results = []
  let i = 0

  for (const combo of combos) {
    i++
    const { label, ...overrides } = combo
    process.stderr.write(`  [${i}/${combos.length}] ${label}\r`)
    try {
      const r = await runBacktest(overrides)
      const a = analyze(r)
      if (a && a.signals > 0) {
        results.push({ label, ...a, elapsed: r.elapsed_ms })
      }
    } catch (e) {
      // skip
    }
  }

  // Sort by avgRoc DESC
  results.sort((a, b) => b.avgRoc - a.avgRoc)

  console.log(`\n${'='.repeat(120)}`)
  console.log(`  TOP 50 CONFIGS BY AVG DAILY ROC`)
  console.log(`${'='.repeat(120)}`)
  console.log(`  ${'Label'.padEnd(50)} Sigs Win%  AvgROC  NetPnl   Grn/Tot  TP   SL  TIME  ms`)
  console.log(`  ${'-'.repeat(115)}`)

  for (const r of results.slice(0, 50)) {
    const roc = r.avgRoc >= 0 ? `+${r.avgRoc.toFixed(2)}%` : `${r.avgRoc.toFixed(2)}%`
    const pnl = r.netPnl >= 0 ? `+${Math.round(r.netPnl)}` : `${Math.round(r.netPnl)}`
    console.log(
      `  ${r.label.padEnd(50)} ${String(r.signals).padStart(4)} ${r.winRate.toFixed(0).padStart(3)}%  ${roc.padStart(7)}  ${pnl.padStart(8)}  ${r.greenDays}/${r.totalDays} (${r.greenPct.toFixed(0)}%)  ${String(r.tp).padStart(3)} ${String(r.sl).padStart(4)} ${String(r.time).padStart(5)}  ${r.elapsed}`)
  }

  console.log(`\n${'='.repeat(120)}`)
  console.log(`  WORST 10 CONFIGS (avoid these)`)
  console.log(`${'='.repeat(120)}`)
  for (const r of results.slice(-10).reverse()) {
    const roc = r.avgRoc >= 0 ? `+${r.avgRoc.toFixed(2)}%` : `${r.avgRoc.toFixed(2)}%`
    const pnl = r.netPnl >= 0 ? `+${Math.round(r.netPnl)}` : `${Math.round(r.netPnl)}`
    console.log(
      `  ${r.label.padEnd(50)} ${String(r.signals).padStart(4)} ${r.winRate.toFixed(0).padStart(3)}%  ${roc.padStart(7)}  ${pnl.padStart(8)}  ${r.greenDays}/${r.totalDays}`)
  }

  console.log(`\nDone. Tested ${results.length} valid combos.`)
}

main().catch(console.error)
