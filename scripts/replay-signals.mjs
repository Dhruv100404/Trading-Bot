#!/usr/bin/env node
/**
 * replay-signals.mjs
 *
 * Reads historical snapshots from ClickHouse (loaded by backfill-history.mjs)
 * and runs the exact same signal logic as the Rust engine to generate signals.
 * Results are written to trading.signals so they appear in the Dashboard.
 *
 * Usage — single day:
 *   node scripts/replay-signals.mjs --date 2026-03-20 --symbols RELIANCE,HDFCBANK
 *
 * Usage — date range (backtesting):
 *   node scripts/replay-signals.mjs --from 2026-02-20 --to 2026-03-20 --symbols RELIANCE,HDFCBANK
 *
 * Optional env vars:
 *   CLICKHOUSE_URL   default: http://localhost:8123
 */

import { parseArgs } from 'node:util'
import { randomUUID } from 'node:crypto'

// ── Args ──────────────────────────────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    date:     { type: 'string' },
    from:     { type: 'string' },
    to:       { type: 'string' },
    symbols:  { type: 'string' },
    'ch-url': { type: 'string' },
  },
  strict: false,
})

if (!args.symbols || (!args.date && !args.from)) {
  console.error('Usage:')
  console.error('  node scripts/replay-signals.mjs --date YYYY-MM-DD --symbols SYM1,SYM2')
  console.error('  node scripts/replay-signals.mjs --from YYYY-MM-DD --to YYYY-MM-DD --symbols SYM1,SYM2')
  process.exit(1)
}

const FROM_DATE = args.date ?? args.from
const TO_DATE   = args.date ?? args.to ?? args.from
const SYMBOLS   = args.symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
const CH_URL    = args['ch-url'] ?? process.env.CLICKHOUSE_URL ?? 'http://localhost:8123'

// ── Date helpers ──────────────────────────────────────────────────────────────

function weekdaysBetween(from, to) {
  const dates = []
  const cur = new Date(from + 'T00:00:00Z')
  const end = new Date(to   + 'T00:00:00Z')
  while (cur <= end) {
    const dow = cur.getUTCDay()
    if (dow !== 0 && dow !== 6) dates.push(cur.toISOString().slice(0, 10))
    cur.setUTCDate(cur.getUTCDate() + 1)
  }
  return dates
}

const ALL_DATES = weekdaysBetween(FROM_DATE, TO_DATE)

// ── ClickHouse helpers ────────────────────────────────────────────────────────

async function chQuery(sql) {
  const res = await fetch(
    `${CH_URL}/?query=${encodeURIComponent(sql + ' FORMAT JSONEachRow')}`
  )
  if (!res.ok) throw new Error(`ClickHouse error: ${await res.text()}`)
  const text = await res.text()
  return text.trim().split('\n').filter(Boolean).map(l => JSON.parse(l))
}

async function chInsert(table, rows) {
  if (rows.length === 0) return
  const body = rows.map(r => JSON.stringify(r)).join('\n')
  const res = await fetch(
    `${CH_URL}/?query=${encodeURIComponent(`INSERT INTO ${table} FORMAT JSONEachRow`)}`,
    { method: 'POST', body, headers: { 'Content-Type': 'application/x-ndjson' } }
  )
  if (!res.ok) throw new Error(`ClickHouse insert error: ${await res.text()}`)
}

// ── Signal logic (mirrors engine/src/signal_engine.rs) ───────────────────────

function computeSignal(allSnaps, cfg, gapPct, openPrice, currentBucket) {
  if (allSnaps.length < 3) return null
  if (currentBucket < cfg.entry_bucket_start || currentBucket > cfg.entry_bucket_end) return null

  const entrySnaps = allSnaps.filter(
    s => s.bucket >= cfg.entry_bucket_start && s.bucket <= currentBucket
  )
  if (entrySnaps.length === 0) return null

  const last = entrySnaps[entrySnaps.length - 1]
  const movePct = (last.ltp - openPrice) / openPrice * 100
  if (Math.abs(movePct) < cfg.min_move_pct) return null

  const direction = movePct > 0 ? 'BUY' : 'SELL'
  const dirSign   = direction === 'BUY' ? 1 : -1

  let score = 0
  const fired = []

  if (Math.abs(movePct) >= cfg.min_move_pct)     { score += 2; fired.push('pm✓') }
  if (Math.abs(movePct) >= cfg.min_move_pct * 2) { score += 2; fired.push('pm2✓') }

  const volEntry = entrySnaps.reduce((s, snap) => s + snap.volume_delta, 0)
  if (volEntry >= cfg.min_volume)     { score += 1; fired.push('vol✓') }
  if (volEntry >= cfg.min_volume * 2) { score += 2; fired.push('vol2✓') }

  const oiCum = entrySnaps.reduce((s, snap) => s + Number(snap.oi_delta), 0)
  if ((oiCum * dirSign) > 0) { score += 1; fired.push('oiDir✓') }

  const preEntrySnaps = allSnaps.filter(s => s.bucket < cfg.entry_bucket_start)
  const avgPreOi = preEntrySnaps.length > 0
    ? preEntrySnaps.reduce((s, snap) => s + Math.abs(Number(snap.oi_delta)), 0) / preEntrySnaps.length
    : 0
  if (avgPreOi > 0 && Math.abs(Number(last.oi_delta)) > avgPreOi * 3) {
    score += 1; fired.push('oiSpike✓')
  }

  if (entrySnaps.length >= 3) {
    const n = entrySnaps.length
    const xs = entrySnaps.map((_, i) => i)
    const ys = entrySnaps.map(s => Number(s.oi_delta))
    const xMean = xs.reduce((a, b) => a + b, 0) / n
    const yMean = ys.reduce((a, b) => a + b, 0) / n
    const num = xs.reduce((s, x, i) => s + (x - xMean) * (ys[i] - yMean), 0)
    const den = xs.reduce((s, x) => s + (x - xMean) ** 2, 0)
    const slope = den !== 0 ? num / den : 0
    if (slope * dirSign > 0) { score += 1; fired.push('oiAcc✓') }
  }

  const vwapCross = entrySnaps.some(s =>
    direction === 'BUY' ? s.ltp > s.vwap && s.vwap > 0
                        : s.ltp < s.vwap && s.vwap > 0
  )
  if (vwapCross) { score += 1; fired.push('vwap✓') }

  if (Math.abs(gapPct) > 0.3 && (gapPct * dirSign) > 0) { score += 1; fired.push('gap✓') }
  if (last.candle_body_ratio > 0.6)                       { score += 1; fired.push('body✓') }
  if (last.spread_pct > 0.15)                             { score -= 2; fired.push('spread✗') }

  score = Math.max(0, score)
  if (score < cfg.min_score) return null

  const entryPrice = last.ltp
  const tpPrice    = entryPrice * (1 + dirSign * cfg.tp_pct / 100)
  const slPrice    = entryPrice * (1 - dirSign * cfg.sl_pct / 100)

  return { direction, score, fired, entryPrice, tpPrice, slPrice, entryBucket: currentBucket }
}

function checkExit(signal, currentLtp, currentBucket, hardExitBucket) {
  const tpHit   = signal.direction === 'BUY' ? currentLtp >= signal.tpPrice : currentLtp <= signal.tpPrice
  const slHit   = signal.direction === 'BUY' ? currentLtp <= signal.slPrice : currentLtp >= signal.slPrice
  const timeHit = currentBucket >= hardExitBucket

  let reason = null
  if (tpHit) reason = 'TP'
  else if (slHit) reason = 'SL'
  else if (timeHit) reason = 'TIME'
  if (!reason) return null

  const returnPct = signal.direction === 'BUY'
    ? (currentLtp - signal.entryPrice) / signal.entryPrice * 100
    : (signal.entryPrice - currentLtp) / signal.entryPrice * 100

  return {
    reason,
    exitPrice:  currentLtp,
    exitBucket: currentBucket,
    returnPct,
    pnlRupees:  signal.entryPrice * (returnPct / 100) * signal.quantity,
  }
}

// ── Per-symbol-per-day replay ─────────────────────────────────────────────────

function replaySymbolDay(snaps, dailyRef, cfg) {
  if (!snaps.length) return null

  const gapPct    = Number(dailyRef.gap_pct)
  const openPrice = snaps[0].ltp

  let activeSignal = null
  let signalFired  = false

  const buckets = [...new Set(snaps.map(s => s.bucket))].sort((a, b) => a - b)

  for (const bucket of buckets) {
    const snapsUpTo = snaps.filter(s => s.bucket <= bucket)
    const current   = snaps.find(s => s.bucket === bucket)
    if (!current) continue

    if (!signalFired && bucket >= cfg.entry_bucket_start && bucket <= cfg.entry_bucket_end) {
      const result = computeSignal(snapsUpTo, cfg, gapPct, openPrice, bucket)
      if (result) {
        signalFired  = true
        activeSignal = {
          id:          randomUUID(),
          symbol:      dailyRef.symbol,
          securityId:  dailyRef.security_id,
          direction:   result.direction,
          entryPrice:  result.entryPrice,
          entryBucket: result.entryBucket,
          tpPrice:     result.tpPrice,
          slPrice:     result.slPrice,
          score:       result.score,
          fired:       result.fired,
          quantity:    cfg.quantity,
        }
      }
    }

    if (activeSignal && bucket > activeSignal.entryBucket) {
      const exit = checkExit(activeSignal, current.ltp, bucket, cfg.hard_exit_bucket)
      if (exit) {
        return buildSignalRow(activeSignal, exit, dailyRef.trading_date, cfg)
      }
    }
  }

  // Force TIME exit at last bucket if still open
  if (activeSignal) {
    const last = snaps[snaps.length - 1]
    const returnPct = activeSignal.direction === 'BUY'
      ? (last.ltp - activeSignal.entryPrice) / activeSignal.entryPrice * 100
      : (activeSignal.entryPrice - last.ltp) / activeSignal.entryPrice * 100
    const exit = {
      reason: 'TIME', exitPrice: last.ltp, exitBucket: last.bucket,
      returnPct, pnlRupees: activeSignal.entryPrice * (returnPct / 100) * activeSignal.quantity,
    }
    return buildSignalRow(activeSignal, exit, dailyRef.trading_date, cfg)
  }

  return null
}

function buildSignalRow(sig, exit, date, cfg) {
  return {
    id:                sig.id,
    trading_date:      date,
    symbol:            sig.symbol,
    security_id:       sig.securityId,
    direction:         sig.direction,
    entry_price:       round(sig.entryPrice, 2),
    entry_bucket:      sig.entryBucket,
    entry_ts:          bucketToTs(date, sig.entryBucket),
    score:             sig.score,
    signals_fired:     sig.fired,
    tp_price:          round(sig.tpPrice, 2),
    sl_price:          round(sig.slPrice, 2),
    quantity:          sig.quantity,
    exit_price:        round(exit.exitPrice, 2),
    exit_bucket:       exit.exitBucket,
    exit_reason:       exit.reason,
    actual_return_pct: round(exit.returnPct, 4),
    pnl_rupees:        round(exit.pnlRupees, 2),
    cfg_entry_start:      cfg.entry_bucket_start,
    cfg_entry_end:        cfg.entry_bucket_end,
    cfg_min_move_pct:     cfg.min_move_pct,
    cfg_min_volume:       cfg.min_volume,
    cfg_min_score:        cfg.min_score,
    cfg_tp_pct:           cfg.tp_pct,
    cfg_sl_pct:           cfg.sl_pct,
    cfg_hard_exit_bucket: cfg.hard_exit_bucket,
    cfg_quantity:         cfg.quantity,
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  const rangeLabel = FROM_DATE === TO_DATE ? FROM_DATE : `${FROM_DATE} → ${TO_DATE}`
  console.log(`\n╔═══════════════════════════════════════════╗`)
  console.log(`║  dhan-trader signal replay                ║`)
  console.log(`╚═══════════════════════════════════════════╝`)
  console.log(`  Range:   ${rangeLabel}  (${ALL_DATES.length} days)`)
  console.log(`  Symbols: ${SYMBOLS.length} total`)
  console.log(`  CH URL:  ${CH_URL}\n`)

  // Load config
  let cfgRows
  try {
    cfgRows = await chQuery('SELECT * FROM trading.config FINAL ORDER BY inserted_at DESC LIMIT 1')
  } catch (e) {
    console.error('Could not load config:', e.message); process.exit(1)
  }
  if (cfgRows.length === 0) {
    console.error('No config row — run the engine at least once to seed defaults.'); process.exit(1)
  }
  const cfg = {
    entry_bucket_start: Number(cfgRows[0].entry_bucket_start),
    entry_bucket_end:   Number(cfgRows[0].entry_bucket_end),
    min_move_pct:       Number(cfgRows[0].min_move_pct),
    min_volume:         Number(cfgRows[0].min_volume),
    min_score:          Number(cfgRows[0].min_score),
    tp_pct:             Number(cfgRows[0].tp_pct),
    sl_pct:             Number(cfgRows[0].sl_pct),
    hard_exit_bucket:   Number(cfgRows[0].hard_exit_bucket),
    quantity:           Number(cfgRows[0].quantity),
  }
  console.log(`Config: entry ${cfg.entry_bucket_start}–${cfg.entry_bucket_end}  ` +
    `move≥${cfg.min_move_pct}%  vol≥${cfg.min_volume}  score≥${cfg.min_score}  ` +
    `tp=${cfg.tp_pct}%  sl=${cfg.sl_pct}%  exit@b${cfg.hard_exit_bucket}\n`)

  // Load ALL snapshots for all symbols+dates in one query (much faster than per-symbol queries)
  console.log('Loading snapshots from ClickHouse...')
  const symbolList = SYMBOLS.map(s => `'${s}'`).join(',')
  const allSnapsRaw = await chQuery(
    `SELECT trading_date, symbol, security_id, bucket, ltp, candle_open, candle_high, candle_low, ` +
    `volume_cum, volume_delta, oi_total, oi_delta, bid, ask, spread_pct, vwap, ` +
    `price_velocity, volume_rate, candle_body_ratio ` +
    `FROM trading.snapshots ` +
    `WHERE trading_date >= toDate('${FROM_DATE}') AND trading_date <= toDate('${TO_DATE}') ` +
    `AND symbol IN (${symbolList}) ` +
    `ORDER BY trading_date, symbol, bucket`
  )
  console.log(`  Loaded ${allSnapsRaw.length.toLocaleString()} snapshot rows`)

  const allDailyRefRaw = await chQuery(
    `SELECT trading_date, symbol, security_id, gap_pct, day_open ` +
    `FROM trading.daily_ref FINAL ` +
    `WHERE trading_date >= toDate('${FROM_DATE}') AND trading_date <= toDate('${TO_DATE}') ` +
    `AND symbol IN (${symbolList})`
  )
  console.log(`  Loaded ${allDailyRefRaw.length.toLocaleString()} daily_ref rows\n`)

  // Index by date+symbol for fast lookup
  const snapIndex = {}
  for (const r of allSnapsRaw) {
    const key = `${r.trading_date}|${r.symbol}`
    if (!snapIndex[key]) snapIndex[key] = []
    snapIndex[key].push({
      bucket:            Number(r.bucket),
      ltp:               Number(r.ltp),
      candle_open:       Number(r.candle_open),
      candle_high:       Number(r.candle_high),
      candle_low:        Number(r.candle_low),
      volume_cum:        Number(r.volume_cum),
      volume_delta:      Number(r.volume_delta),
      oi_delta:          Number(r.oi_delta),
      spread_pct:        Number(r.spread_pct),
      vwap:              Number(r.vwap),
      candle_body_ratio: Number(r.candle_body_ratio),
    })
  }

  const refIndex = {}
  for (const r of allDailyRefRaw) {
    refIndex[`${r.trading_date}|${r.symbol}`] = r
  }

  // Replay all combinations
  const allSignals = []
  const stats = { tp: 0, sl: 0, time: 0, noSignal: 0, noData: 0 }

  console.log('Replaying...')
  for (const date of ALL_DATES) {
    let dateSignals = 0
    for (const symbol of SYMBOLS) {
      const key   = `${date}|${symbol}`
      const snaps = snapIndex[key]
      const ref   = refIndex[key]

      if (!snaps || !ref) { stats.noData++; continue }

      const signal = replaySymbolDay(snaps, { ...ref, trading_date: date, symbol }, cfg)
      if (signal) {
        allSignals.push(signal)
        dateSignals++
        stats[signal.exit_reason.toLowerCase()]++
      } else {
        stats.noSignal++
      }
    }
    process.stdout.write(`  ${date}: ${dateSignals} signals\n`)
  }

  // Batch insert all signals
  if (allSignals.length > 0) {
    // Insert in batches of 1000 to avoid large payloads
    for (let i = 0; i < allSignals.length; i += 1000) {
      await chInsert('trading.signals', allSignals.slice(i, i + 1000))
    }
  }

  const totalPnl   = allSignals.reduce((s, r) => s + r.pnl_rupees, 0)
  const profitable = allSignals.filter(r => r.actual_return_pct > 0).length
  const winRate    = allSignals.length > 0 ? (profitable / allSignals.length * 100).toFixed(1) : 0

  console.log(`\n═══════════════════════════════════════════════`)
  console.log(`Backtest complete  (${FROM_DATE} → ${TO_DATE})`)
  console.log(`  Signals fired  : ${allSignals.length}  (${(allSignals.length / (ALL_DATES.length * SYMBOLS.length) * 100).toFixed(1)}% hit rate)`)
  console.log(`  TP / SL / TIME : ${stats.tp} / ${stats.sl} / ${stats.time}`)
  console.log(`  Win rate       : ${winRate}%  (${profitable}/${allSignals.length})`)
  console.log(`  Net P&L        : ₹${totalPnl.toFixed(2)}`)
  console.log(`  No signal      : ${stats.noSignal}  |  No data : ${stats.noData}`)
  console.log(`═══════════════════════════════════════════════`)
  console.log(`\nOpen http://localhost:3000 → Dashboard → pick any date to explore\n`)
}

function round(n, decimals) {
  const f = 10 ** decimals
  return Math.round(n * f) / f
}

function bucketToTs(date, bucket) {
  const totalMinutes = 9 * 60 + 15 + (bucket - 1)
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  const pad = n => String(n).padStart(2, '0')
  return `${date} ${pad(h)}:${pad(m)}:00`
}

main().catch(err => {
  console.error('\nFatal error:', err.message)
  process.exit(1)
})
