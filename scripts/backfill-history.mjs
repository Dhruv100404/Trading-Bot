#!/usr/bin/env node
/**
 * backfill-history.mjs
 *
 * Fetches 1-minute historical OHLCV from Dhan API and loads into ClickHouse
 * (trading.snapshots + trading.daily_ref) so you can backtest signal logic.
 *
 * Usage — single day:
 *   node scripts/backfill-history.mjs --date 2026-03-20 --symbols RELIANCE,INFY
 *
 * Usage — date range (last month of data):
 *   node scripts/backfill-history.mjs --from 2026-02-20 --to 2026-03-20 --symbols RELIANCE,INFY
 *
 * Dhan intraday API allows max 5 trading days per request.
 * This script automatically chunks the range into ≤5-day windows.
 *
 * Required env vars:
 *   DHAN_ACCESS_TOKEN
 *   DHAN_CLIENT_ID
 *
 * Optional env vars:
 *   CLICKHOUSE_URL   default: http://localhost:8123
 *   DHAN_BASE_URL    default: https://api.dhan.co/v2
 */

import { parseArgs } from 'node:util'

// ── Args ──────────────────────────────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    date:     { type: 'string' },   // single day shorthand
    from:     { type: 'string' },   // range start (inclusive)
    to:       { type: 'string' },   // range end   (inclusive)
    symbols:  { type: 'string' },
    'ch-url': { type: 'string' },
    clear:    { type: 'boolean' },  // truncate snapshots + daily_ref before inserting
  },
  strict: false,
})

if (!args.symbols || (!args.date && !args.from)) {
  console.error('Usage:')
  console.error('  node scripts/backfill-history.mjs --date YYYY-MM-DD --symbols SYM1,SYM2')
  console.error('  node scripts/backfill-history.mjs --from YYYY-MM-DD --to YYYY-MM-DD --symbols SYM1,SYM2')
  console.error('  Add --clear to truncate existing snapshots + daily_ref before inserting (prevents duplicates)')
  process.exit(1)
}

const FROM_DATE    = args.date ?? args.from
const TO_DATE      = args.date ?? args.to ?? args.from
const SYMBOLS      = args.symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
const CH_URL       = args['ch-url'] ?? process.env.CLICKHOUSE_URL ?? 'http://localhost:8123'
const DHAN_URL     = process.env.DHAN_BASE_URL    ?? 'https://api.dhan.co/v2'
const ACCESS_TOKEN = process.env.DHAN_ACCESS_TOKEN ?? ''
const CLIENT_ID    = process.env.DHAN_CLIENT_ID    ?? ''

if (!ACCESS_TOKEN || !CLIENT_ID) {
  console.error('Error: DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID must be set as env vars')
  process.exit(1)
}

// ── Date helpers ──────────────────────────────────────────────────────────────

/** Generate all weekday dates (Mon–Fri) between from and to, inclusive */
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

/** Split an array into chunks of at most n */
function chunks(arr, n) {
  const result = []
  for (let i = 0; i < arr.length; i += n) result.push(arr.slice(i, i + n))
  return result
}

// Dhan intraday API: max 5 trading days per call
const DATE_CHUNKS = chunks(weekdaysBetween(FROM_DATE, TO_DATE), 5)

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

// ── Dhan API helpers ──────────────────────────────────────────────────────────

const DHAN_HEADERS = {
  'Content-Type': 'application/json',
  'access-token': ACCESS_TOKEN,
  'client-id':    CLIENT_ID,
}

async function fetchCandles(securityId, fromDate, toDate, retries = 3) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    const res = await fetch(`${DHAN_URL}/charts/intraday`, {
      method: 'POST',
      headers: DHAN_HEADERS,
      body: JSON.stringify({
        securityId:      String(securityId),
        exchangeSegment: 'NSE_EQ',
        instrument:      'EQUITY',
        interval:        '1',
        fromDate,
        toDate,
      }),
    })
    if (res.status === 429) {
      const wait = attempt * 3000
      process.stdout.write(`  [rate-limit] waiting ${wait/1000}s...`)
      await new Promise(r => setTimeout(r, wait))
      process.stdout.write(' retrying\n')
      continue
    }
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Dhan API ${res.status}: ${text}`)
    }
    const data = await res.json()
    if (!data.open || !data.timestamp) {
      // No data for this period (holiday week / no trades) — return empty
      return []
    }
    return data.timestamp.map((ts, i) => ({
      ts:     ts,
      open:   data.open[i],
      high:   data.high[i],
      low:    data.low[i],
      close:  data.close[i],
      volume: data.volume?.[i] ?? 0,
    }))
  }
  throw new Error(`Dhan API rate-limited after ${retries} retries`)
}

// ── Time / bucket helpers ─────────────────────────────────────────────────────

function toIST(unixSec) {
  return new Date((unixSec + 330 * 60) * 1000)
}

function getBucket(unixSec) {
  const d = toIST(unixSec)
  const minuteOfDay = d.getUTCHours() * 60 + d.getUTCMinutes()
  const offset = minuteOfDay - (9 * 60 + 15)
  if (offset < 0 || offset > 374) return null
  return offset + 1  // 1-minute buckets: bucket 1 = 9:15, bucket 46 = 10:00
}

function fmtDT(unixSec) {
  const d = toIST(unixSec)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ` +
         `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}

function tradingDate(unixSec) {
  const d = toIST(unixSec)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}`
}

// ── Derived field computation ─────────────────────────────────────────────────

// Max bucket to store in snapshots: bucket 76 = 11:00 AM IST
// Full-day candles (up to 15:30) are still used for prev_close computation in daily_ref.
const MAX_SNAPSHOT_BUCKET = 76

/**
 * Group candles by trading date, then build snapshot rows per day.
 * Only stores candles up to 11:00 AM (bucket ≤ 76).
 * VWAP and volume_delta reset at day boundary.
 */
function buildSnapshotRows(allCandles, securityId, symbol) {
  // Group by date — include all market candles so VWAP/cumulative volume is correct
  const byDate = {}
  for (const c of allCandles) {
    const bucket = getBucket(c.ts)
    if (bucket === null) continue
    const d = tradingDate(c.ts)
    if (!byDate[d]) byDate[d] = []
    byDate[d].push({ ...c, bucket })
  }

  const rows = []
  for (const [date, candles] of Object.entries(byDate)) {
    candles.sort((a, b) => a.ts - b.ts)
    let prevLtp = 0, prevVolume = 0, vwapNum = 0, vwapDen = 0, first = true

    for (const c of candles) {
      // Accumulate all candles for correct running VWAP/volume state,
      // but only emit rows up to 11:00 AM (bucket ≤ MAX_SNAPSHOT_BUCKET)
      const ltp       = c.close
      const volDelta  = first ? c.volume : Math.max(0, c.volume - prevVolume)
      const velocity  = first ? 0 : (ltp - prevLtp) / 60
      const volRate   = volDelta / 60
      const range     = c.high - c.low
      const bodyRatio = range > 0 ? Math.abs(ltp - c.open) / range : 0
      const weight    = first ? c.volume : volDelta
      vwapNum += ltp * weight
      vwapDen += weight
      const vwap = vwapDen > 0 ? vwapNum / vwapDen : ltp

      prevLtp = ltp; prevVolume = c.volume; first = false

      // Only store candles up to 11:00 AM
      if (c.bucket > MAX_SNAPSHOT_BUCKET) continue

      rows.push({
        trading_date:      date,
        symbol,
        security_id:       String(securityId),
        snapshot_ts:       fmtDT(c.ts),
        bucket:            c.bucket,
        ltp:               round(ltp, 2),
        candle_open:       round(c.open, 2),
        candle_high:       round(c.high, 2),
        candle_low:        round(c.low, 2),
        volume_cum:        c.volume,
        volume_delta:      volDelta,
        trade_count:       0,
        oi_total:          0,
        oi_delta:          0,
        bid:               0,
        ask:               0,
        bid_qty:           0,
        ask_qty:           0,
        spread_pct:        0,
        vwap:              round(vwap, 2),
        price_velocity:    round(velocity, 6),
        volume_rate:       round(volRate, 2),
        candle_body_ratio: round(bodyRatio, 4),
      })
    }
  }
  return rows
}

/**
 * Build daily_ref rows (one per trading date) from candles.
 * Receives full-day market candles (9:15–15:30) so EOD closing prices are correct.
 * prev_close and gap_pct are computed from consecutive days in the loaded range.
 * For the first day in the range, prev_close is fetched from daily_ref.closing_price
 * so incremental runs also get correct gap_pct.
 * closing_price = actual EOD close (~15:29) stored for gap calculations in future runs.
 */
async function buildDailyRefRows(allCandles, securityId, symbol) {
  const byDate = {}
  for (const c of allCandles) {
    const bucket = getBucket(c.ts)
    if (bucket === null) continue
    const d = tradingDate(c.ts)
    if (!byDate[d]) byDate[d] = []
    byDate[d].push(c)
  }

  const sortedDates = Object.keys(byDate).sort()
  if (sortedDates.length === 0) return []

  // Build a map of date → actual EOD close price from full-day candles
  const eodClose = {}
  for (const date of sortedDates) {
    const sorted = byDate[date].slice().sort((a, b) => a.ts - b.ts)
    eodClose[date] = sorted[sorted.length - 1].close
  }

  // For the first date in range, try to get prev_close from daily_ref.closing_price
  // (covers single-day runs and first-day-of-range where no prior candle exists)
  const firstDate = sortedDates[0]
  let prevCloseForFirst = 0
  try {
    const rows = await chQuery(
      `SELECT closing_price FROM trading.daily_ref FINAL ` +
      `WHERE symbol = '${symbol}' AND trading_date < '${firstDate}' AND closing_price > 0 ` +
      `ORDER BY trading_date DESC LIMIT 1`
    )
    if (rows.length > 0 && rows[0].closing_price > 0) {
      prevCloseForFirst = rows[0].closing_price
    }
  } catch (_) { /* fallback to 0 */ }

  // Map date → prev_close (= EOD close of previous trading day)
  const prevCloseMap = {}
  prevCloseMap[firstDate] = prevCloseForFirst
  for (let i = 1; i < sortedDates.length; i++) {
    prevCloseMap[sortedDates[i]] = eodClose[sortedDates[i - 1]]
  }

  const rows = []
  for (const date of sortedDates) {
    const candles  = byDate[date].slice().sort((a, b) => a.ts - b.ts)
    const dayOpen  = candles[0].open
    const pc       = prevCloseMap[date] ?? 0
    const gapPct   = pc > 0 ? round((dayOpen - pc) / pc * 100, 4) : 0
    rows.push({
      trading_date:   date,
      symbol,
      security_id:    String(securityId),
      prev_close:     round(pc, 2),
      pre_open_price: 0,
      day_open:       round(dayOpen, 2),
      gap_pct:        gapPct,
      prev_day_high:  0,
      prev_day_low:   0,
      closing_price:  round(eodClose[date], 2),
    })
  }
  return rows
}

function round(n, decimals) {
  const f = 10 ** decimals
  return Math.round(n * f) / f
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function chExec(sql) {
  const res = await fetch(`${CH_URL}/?query=${encodeURIComponent(sql)}`, { method: 'POST' })
  if (!res.ok) throw new Error(`ClickHouse error: ${await res.text()}`)
}

async function main() {
  const totalDays = weekdaysBetween(FROM_DATE, TO_DATE).length
  console.log(`\n╔═══════════════════════════════════════════╗`)
  console.log(`║  dhan-trader historical backfill          ║`)
  console.log(`╚═══════════════════════════════════════════╝`)
  console.log(`  Range:   ${FROM_DATE} → ${TO_DATE}  (${totalDays} weekdays, ${DATE_CHUNKS.length} API chunks)`)
  console.log(`  Symbols: ${SYMBOLS.length} total`)
  console.log(`  CH URL:  ${CH_URL}`)

  // Ensure closing_price column exists (idempotent — safe to run on every invocation)
  await chExec('ALTER TABLE trading.daily_ref ADD COLUMN IF NOT EXISTS closing_price Float32 DEFAULT 0')

  if (args.clear) {
    console.log(`  ⚠  --clear: truncating trading.snapshots and trading.daily_ref...`)
    await chExec('TRUNCATE TABLE trading.snapshots')
    await chExec('TRUNCATE TABLE trading.daily_ref')
    console.log(`  ✓  Tables cleared\n`)
  } else {
    console.log(`  (use --clear to truncate existing data before inserting)\n`)
  }

  let totalSnapshots = 0
  let symbolsDone = 0
  let symbolsSkipped = 0

  for (const symbol of SYMBOLS) {
    process.stdout.write(`[${++symbolsDone}/${SYMBOLS.length}] ${symbol} `)

    // 1. Look up security_id
    let securityId
    try {
      const rows = await chQuery(
        `SELECT security_id FROM trading.watchlist FINAL WHERE symbol = '${symbol}' LIMIT 1`
      )
      if (rows.length === 0) {
        console.log(`✗ not in watchlist`)
        symbolsSkipped++
        continue
      }
      securityId = rows[0].security_id
    } catch (e) {
      console.log(`✗ CH lookup failed: ${e.message}`)
      symbolsSkipped++
      continue
    }

    // 2. Fetch all chunks and accumulate candles
    let allCandles = []
    let chunksFailed = 0
    for (const chunk of DATE_CHUNKS) {
      const from = chunk[0]
      const to   = chunk[chunk.length - 1]
      await new Promise(r => setTimeout(r, 600))  // throttle: ~1.5 req/sec
      try {
        const candles = await fetchCandles(securityId, from, to)
        allCandles = allCandles.concat(candles)
      } catch (e) {
        chunksFailed++
      }
    }

    const marketCandles = allCandles.filter(c => getBucket(c.ts) !== null)
    const datesLoaded   = new Set(marketCandles.map(c => tradingDate(c.ts))).size

    if (marketCandles.length === 0) {
      console.log(`✗ no data`)
      symbolsSkipped++
      continue
    }

    // 3. Insert snapshots + daily_ref
    const snapRows    = buildSnapshotRows(marketCandles, securityId, symbol)
    const dailyRows   = await buildDailyRefRows(marketCandles, securityId, symbol)

    await chInsert('trading.snapshots', snapRows)
    await chInsert('trading.daily_ref', dailyRows)

    totalSnapshots += snapRows.length
    const failNote = chunksFailed > 0 ? `  (${chunksFailed} chunk(s) failed)` : ''
    console.log(`✓  ${datesLoaded} days  ${snapRows.length} rows${failNote}`)
  }

  console.log(`\n═══════════════════════════════════════════════`)
  console.log(`Done.`)
  console.log(`  Symbols processed : ${symbolsDone - symbolsSkipped} / ${SYMBOLS.length}`)
  console.log(`  Total snapshots   : ${totalSnapshots.toLocaleString()}`)
  console.log(`  Date range        : ${FROM_DATE} → ${TO_DATE}`)
  console.log(`═══════════════════════════════════════════════\n`)
}

main().catch(err => {
  console.error('\nFatal error:', err.message)
  process.exit(1)
})
