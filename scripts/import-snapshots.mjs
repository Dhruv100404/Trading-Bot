#!/usr/bin/env node
/**
 * import-snapshots.mjs
 *
 * Imports candles-consolidated*.ndjson files from data/ into trading.snapshots.
 * Each NDJSON line contains a symbol+date with an array of 10-minute buckets.
 * Each bucket becomes one row in trading.snapshots.
 *
 * Features:
 *   - Retry with exponential backoff on network errors
 *   - Checkpoint file (.import-checkpoint.json) so crashes are resumable
 *   - Deduplication: skips rows already present in DB before inserting
 *
 * Market opens at 9:15 AM IST. Bucket timing:
 *   bucket 1 → 09:15, bucket 2 → 09:25, bucket 3 → 09:35, ...
 *
 * Usage:
 *   node scripts/import-snapshots.mjs
 *   node scripts/import-snapshots.mjs --file data/candles-consolidated_new.ndjson
 *   node scripts/import-snapshots.mjs --batch-size 2000
 *   node scripts/import-snapshots.mjs --reset-checkpoint   (clear saved progress)
 *
 * Optional env vars:
 *   CLICKHOUSE_URL   default: http://localhost:8123
 */

import { createReadStream, existsSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { parseArgs } from 'node:util'
import { resolve } from 'node:path'

// ── Args ──────────────────────────────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    file:               { type: 'string' },
    'batch-size':       { type: 'string', default: '2000' },
    'reset-checkpoint': { type: 'boolean', default: false },
  },
  allowPositionals: false,
})

const CLICKHOUSE_URL    = process.env.CLICKHOUSE_URL ?? 'http://localhost:8123'
const BATCH_SIZE        = parseInt(args['batch-size'], 10)
const CHECKPOINT_FILE   = resolve('scripts/.import-checkpoint.json')

const FILES = args.file
  ? [args.file]
  : [
      'data/candles-consolidated_new.ndjson',
      'data/candles-consolidated.ndjson',
    ]

// ── Checkpoint ────────────────────────────────────────────────────────────────

function loadCheckpoint() {
  if (args['reset-checkpoint']) {
    if (existsSync(CHECKPOINT_FILE)) {
      unlinkSync(CHECKPOINT_FILE)
      console.log('Checkpoint cleared.')
    }
    return {}
  }
  if (existsSync(CHECKPOINT_FILE)) {
    try {
      const cp = JSON.parse(readFileSync(CHECKPOINT_FILE, 'utf8'))
      console.log(`Resuming from checkpoint: file=${cp.file} line=${cp.lineNum} rows=${cp.total}`)
      return cp
    } catch {
      return {}
    }
  }
  return {}
}

function saveCheckpoint(file, lineNum, total) {
  writeFileSync(CHECKPOINT_FILE, JSON.stringify({ file, lineNum, total }), 'utf8')
}

function clearCheckpoint() {
  if (existsSync(CHECKPOINT_FILE)) unlinkSync(CHECKPOINT_FILE)
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function bucketToTs(date, bucketNum) {
  const baseMinutes = 9 * 60 + 15 + (bucketNum - 1) * 10
  const hh = String(Math.floor(baseMinutes / 60)).padStart(2, '0')
  const mm = String(baseMinutes % 60).padStart(2, '0')
  return `${date} ${hh}:${mm}:00`
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * Retry wrapper with exponential backoff.
 * Retries on ECONNABORTED, ECONNRESET, ECONNREFUSED, ETIMEDOUT, and 5xx errors.
 */
async function withRetry(fn, label, maxAttempts = 6) {
  let delay = 1000
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn()
    } catch (err) {
      const isRetryable =
        err?.cause?.code === 'ECONNABORTED' ||
        err?.cause?.code === 'ECONNRESET'   ||
        err?.cause?.code === 'ECONNREFUSED' ||
        err?.cause?.code === 'ETIMEDOUT'    ||
        err?.message?.includes('ECONN')     ||
        err?.message?.startsWith('Insert error: Code: 5') // CH server errors

      if (!isRetryable || attempt === maxAttempts) throw err

      console.warn(`\n  [${label}] attempt ${attempt} failed (${err?.cause?.code ?? err?.message?.slice(0, 60)}), retrying in ${delay / 1000}s...`)
      await sleep(delay)
      delay = Math.min(delay * 2, 30000)
    }
  }
}

async function chQuery(sql) {
  const res = await withRetry(async () => {
    const r = await fetch(`${CLICKHOUSE_URL}/?query=${encodeURIComponent(sql)}`)
    if (!r.ok) throw new Error(`ClickHouse error: ${await r.text()}`)
    return r
  }, 'query')
  return res.text()
}

/**
 * For a batch of rows, query ClickHouse to find which (trading_date, symbol, bucket)
 * combos already exist, then return only the rows that are not yet in the DB.
 */
async function filterExisting(rows) {
  if (rows.length === 0) return rows

  // Collect unique (trading_date, symbol) pairs in this batch
  const pairs = [...new Set(rows.map(r => `('${r.trading_date}','${r.symbol}')`))]

  const sql = `
    SELECT trading_date, symbol, bucket
    FROM trading.snapshots
    WHERE (trading_date, symbol) IN (${pairs.join(',')})
    FORMAT TabSeparated
  `
  let raw = ''
  try {
    raw = await withRetry(async () => {
      const r = await fetch(`${CLICKHOUSE_URL}/?query=${encodeURIComponent(sql)}`)
      if (!r.ok) throw new Error(`ClickHouse error: ${await r.text()}`)
      return r.text()
    }, 'dedup-check')
  } catch (err) {
    // If dedup check fails, warn but proceed — better to have dupes than lose data
    console.warn(`  Dedup check failed (${err.message}), inserting without dedup check`)
    return rows
  }

  // Build set of existing keys
  const existing = new Set()
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    const [date, sym, bkt] = line.split('\t')
    existing.add(`${date}|${sym}|${bkt}`)
  }

  if (existing.size === 0) return rows

  const filtered = rows.filter(r => !existing.has(`${r.trading_date}|${r.symbol}|${r.bucket}`))
  const skipped  = rows.length - filtered.length
  if (skipped > 0) process.stdout.write(`  [dedup] skipped ${skipped} already-existing rows\n`)
  return filtered
}

async function insertBatch(rows) {
  if (rows.length === 0) return

  const newRows = await filterExisting(rows)
  if (newRows.length === 0) return

  const tsv = newRows.map(r => [
    r.trading_date,
    r.symbol,
    r.security_id,
    r.snapshot_ts,
    r.bucket,
    r.ltp,
    r.candle_open,
    r.candle_high,
    r.candle_low,
    Math.round(r.volume_cum),
    Math.round(r.volume_delta),
    0,   // trade_count  (not in source data)
    0,   // oi_total
    0,   // oi_delta
    0,   // bid
    0,   // ask
    0,   // bid_qty
    0,   // ask_qty
    0,   // spread_pct
    r.vwap,
    0,   // price_velocity
    r.volume_rate,
    r.candle_body_ratio,
  ].join('\t')).join('\n')

  const sql = `INSERT INTO trading.snapshots (
    trading_date, symbol, security_id, snapshot_ts, bucket,
    ltp, candle_open, candle_high, candle_low,
    volume_cum, volume_delta, trade_count,
    oi_total, oi_delta, bid, ask, bid_qty, ask_qty, spread_pct,
    vwap, price_velocity, volume_rate, candle_body_ratio
  ) FORMAT TabSeparated`

  await withRetry(async () => {
    const res = await fetch(`${CLICKHOUSE_URL}/?query=${encodeURIComponent(sql)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: tsv,
    })
    if (!res.ok) throw new Error(`Insert error: ${await res.text()}`)
  }, 'insert')
}

// ── Main ──────────────────────────────────────────────────────────────────────

/**
 * Async generator that yields lines from a file without using readline.
 * readline buffers lines internally and crashes on very long lines (RangeError).
 * This version accumulates raw chunks and splits on \n manually, so there is no
 * internal string-length ceiling imposed by the readline state machine.
 */
async function* readLines(filePath) {
  const stream = createReadStream(filePath, { encoding: 'utf8', highWaterMark: 256 * 1024 })
  let remainder = ''
  for await (const chunk of stream) {
    remainder += chunk
    let idx
    while ((idx = remainder.indexOf('\n')) !== -1) {
      yield remainder.slice(0, idx)
      remainder = remainder.slice(idx + 1)
    }
  }
  if (remainder.length > 0) yield remainder
}

async function importFile(filePath, checkpoint) {
  const absPath    = resolve(filePath)
  const resumeLine = checkpoint?.file === filePath ? (checkpoint.lineNum ?? 0) : 0
  const resumeTotal = checkpoint?.file === filePath ? (checkpoint.total ?? 0) : 0

  console.log(`\nImporting: ${absPath}`)
  if (resumeLine > 0) console.log(`  Resuming from line ${resumeLine} (${resumeTotal} rows already inserted)`)

  let batch   = []
  let total   = resumeTotal
  let lineNum = 0

  for await (const line of readLines(absPath)) {
    lineNum++
    if (lineNum <= resumeLine) continue   // skip already-processed lines
    if (!line.trim()) continue

    let record
    try {
      record = JSON.parse(line)
    } catch {
      console.warn(`  Line ${lineNum}: invalid JSON, skipping`)
      continue
    }

    const { symbol, security_id, date, buckets } = record
    if (!Array.isArray(buckets)) continue

    for (const b of buckets) {
      if (b.b < 1 || b.b > 105) continue
      batch.push({
        trading_date:      date,
        symbol,
        security_id,
        snapshot_ts:       bucketToTs(date, b.b),
        bucket:            b.b,
        ltp:               b.c,
        candle_open:       b.o,
        candle_high:       b.h,
        candle_low:        b.l,
        volume_cum:        b.vc,
        volume_delta:      b.v,
        vwap:              b.vw,
        volume_rate:       b.vr,
        candle_body_ratio: b.br,
      })
    }

    if (batch.length >= BATCH_SIZE) {
      await insertBatch(batch)
      total += batch.length
      saveCheckpoint(filePath, lineNum, total)
      process.stdout.write(`  Inserted ${total} rows (line ${lineNum})\r`)
      batch = []
    }
  }

  if (batch.length > 0) {
    await insertBatch(batch)
    total += batch.length
    saveCheckpoint(filePath, lineNum, total)
  }

  console.log(`  Done: ${total} rows from ${lineNum} lines        `)
  return total
}

async function main() {
  const checkpoint = loadCheckpoint()

  try {
    await withRetry(() => chQuery('SELECT 1'), 'ping', 3)
  } catch {
    console.error(`Cannot reach ClickHouse at ${CLICKHOUSE_URL}`)
    console.error('Make sure docker-compose is running, then retry.')
    process.exit(1)
  }

  let grandTotal = 0
  for (const f of FILES) {
    grandTotal += await importFile(f, checkpoint)
  }

  clearCheckpoint()
  console.log(`\nAll done. Total rows inserted: ${grandTotal}`)
}

main().catch(err => { console.error(err); process.exit(1) })
