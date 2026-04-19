#!/usr/bin/env bun
// Backfill snapshots + daily_ref for a given month from Dhan 1-min candles API.
// Fetches all enabled watchlist symbols, downloads candles, inserts into ClickHouse.
//
// Usage: bun deploy/backfill-month.js 2024-08
//        bun deploy/backfill-month.js 2024-08 2024-10   (multiple months)
//
// - Fetches symbols from ClickHouse watchlist (enabled=1)
// - Downloads 1-min candles from Dhan intraday charts API
// - Converts to snapshot buckets (bucket 1 = 09:15, bucket 375 = 15:29)
// - Batch inserts into trading.snapshots (one INSERT per stock per chunk)
// - Populates trading.daily_ref with day_open, closing_price, prev_close, gap_pct
// - 5 tokens round-robin, concurrent batches, adaptive throttle, resume-safe

const TOKENS = [
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc1MzkxMjMwLCJpYXQiOjE3NzUzMDQ4MzAsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.9jxZFG6xfRWnx4R_YiMllQ0gBGIYVFfuweHU4q8oT9Z4OdjcSdwfhhHDuUwTOb2GAElTb17r2oW6zMk3M1Ixlw",

  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc1MzkxMjI2LCJpYXQiOjE3NzUzMDQ4MjYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.N3x8d45htkf7WEmlBmhaETBNKV8BFXIPrAk0lXEIYCz2NIbgneJelBL546s-R3x5Y5j3qinC_BX9lIdyMwBwPw",

  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc1MzkxMjIwLCJpYXQiOjE3NzUzMDQ4MjAsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.5ayOu9yFjEbGpGVSfD41ipTrNKIbMjwNIx5YMK37Xd_lWj1Cskw8aewlIe2dFbFRXyxXEF4ElfikIr7H46SCYA",

  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc1MzkxMjE2LCJpYXQiOjE3NzUzMDQ4MTYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.aUvDOTmgnyFlC4D-EEUpdGi9KLOtnoseHMw00-70YHC7uP2vRdqMsOLSdlOI4DfFsM67k0lBL_GRLOOkXTdLlg",

  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc1MzkxMjA5LCJpYXQiOjE3NzUzMDQ4MDksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.nV0Pq73p7rPKuPJi4kyq9KeLX2ulGxAorarMYkQq1MBqb-9uEyClW2mqyWL9yhhotVz6k2PtWT8p2Dk8eiL3YQ",
]
const CLIENT_ID = "1100896497"
const CH_URL = process.env.CLICKHOUSE_URL || "http://localhost:8123"

const CHUNK_TRADING_DAYS = 10
const CONCURRENT_STOCKS = 5
const MAX_RETRIES = 5

// Adaptive throttle
let batchDelay = 200
const DELAY_MIN = 100
const DELAY_MAX = 2000
let tokenIdx = 0

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// ─── Date helpers ───────────────────────────────────────────────────────────

// Common NSE holidays (expand as needed)
const NSE_HOLIDAYS = new Set([
  // 2024
  "2024-01-26","2024-03-08","2024-03-25","2024-03-29","2024-04-11","2024-04-14",
  "2024-04-17","2024-04-21","2024-05-20","2024-06-17","2024-07-17","2024-08-15",
  "2024-09-16","2024-10-02","2024-10-12","2024-10-31","2024-11-01","2024-11-15","2024-12-25",
  // 2025
  "2025-01-26","2025-02-26","2025-03-14","2025-03-31","2025-04-10","2025-04-14",
  "2025-04-18","2025-05-01","2025-06-26","2025-07-06","2025-08-15","2025-08-16",
  "2025-08-27","2025-10-02","2025-10-21","2025-10-22","2025-11-05","2025-11-26","2025-12-25",
  // 2026
  "2026-01-26","2026-03-03","2026-03-14","2026-03-30","2026-03-31",
])

function getTradingDays(from, to) {
  const days = []
  const d = new Date(from + "T00:00:00Z")
  const end = new Date(to + "T00:00:00Z")
  while (d <= end) {
    const iso = d.toISOString().split("T")[0]
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6 && !NSE_HOLIDAYS.has(iso)) {
      days.push(iso)
    }
    d.setUTCDate(d.getUTCDate() + 1)
  }
  return days
}

function getMonthRange(monthStr) {
  // "2024-08" → { from: "2024-08-01", to: "2024-08-31" }
  const [y, m] = monthStr.split("-").map(Number)
  const lastDay = new Date(y, m, 0).getDate()
  return {
    from: `${y}-${String(m).padStart(2,"0")}-01`,
    to: `${y}-${String(m).padStart(2,"0")}-${String(lastDay).padStart(2,"0")}`,
  }
}

function chunkArray(arr, size) {
  const chunks = []
  for (let i = 0; i < arr.length; i += size) {
    const days = arr.slice(i, i + size)
    chunks.push({ fromDate: days[0], toDate: days[days.length - 1], days })
  }
  return chunks
}

// ─── Dhan API ───────────────────────────────────────────────────────────────

async function fetchChunk(securityId, fromDate, toDate, token) {
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const resp = await fetch("https://api.dhan.co/v2/charts/intraday", {
        method: "POST",
        headers: {
          "access-token": token,
          "client-id": CLIENT_ID,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          securityId,
          exchangeSegment: "NSE_EQ",
          instrument: "EQUITY",
          interval: "1",
          fromDate,
          toDate,
        }),
      })

      if (resp.status === 429 || (resp.status === 400 && (await resp.clone().text()).includes("DH-905"))) {
        batchDelay = Math.min(DELAY_MAX, batchDelay + 500)
        if (attempt < MAX_RETRIES) { await sleep(3000 * attempt); continue }
        return { error: "rate_limit" }
      }
      if (resp.status === 400) return { error: "invalid" }
      if (!resp.ok) {
        if (attempt < MAX_RETRIES) { await sleep(2000 * attempt); continue }
        return { error: "http" }
      }
      return { data: await resp.json() }
    } catch (e) {
      if (attempt < MAX_RETRIES) { await sleep(2000 * attempt); continue }
      return { error: "network" }
    }
  }
}

// ─── Candle → Snapshot conversion ───────────────────────────────────────────

// Convert 1-min candles to snapshot rows. Bucket 1 = 09:15, bucket 375 = 15:29.
function candlesToSnapshots(symbol, securityId, dateStr, candles) {
  if (!candles || candles.length === 0) return []

  // Sort by timestamp
  candles.sort((a, b) => a.timestamp - b.timestamp)

  // Market open = 09:15 IST. Bucket = minutes since 09:15 + 1
  const rows = []
  let volumeCum = 0

  for (const c of candles) {
    // Convert Unix timestamp to IST minutes
    const d = new Date(c.timestamp * 1000)
    const istHour = (d.getUTCHours() + 5) % 24 + Math.floor((d.getUTCMinutes() + 30) / 60)
    const istMin = (d.getUTCMinutes() + 30) % 60
    const minuteOfDay = istHour * 60 + istMin
    const bucket = minuteOfDay - (9 * 60 + 15) + 1  // 09:15 = bucket 1

    if (bucket < 1 || bucket > 375) continue

    volumeCum += Math.max(0, Math.round(c.volume || 0))
    const range = c.high - c.low
    const bodyRatio = range > 0 ? Math.abs(c.close - c.open) / range : 0.5

    rows.push({
      trading_date: dateStr,
      symbol,
      security_id: securityId,
      bucket,
      ltp: c.close,
      candle_open: c.open,
      candle_high: c.high,
      candle_low: c.low,
      volume_cum: Math.round(volumeCum),
      volume_delta: Math.max(0, Math.round(c.volume || 0)),
      vwap: 0,  // not available from candle API
      volume_rate: 0,
      candle_body_ratio: Math.round(bodyRatio * 1000) / 1000,
    })
  }

  return rows
}

// ─── ClickHouse insert ──────────────────────────────────────────────────────

async function chQuery(sql) {
  const resp = await fetch(CH_URL, { method: "POST", body: sql })
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(`ClickHouse error: ${resp.status} ${body.slice(0, 200)}`)
  }
  return resp
}

async function chFetch(sql) {
  const resp = await fetch(`${CH_URL}/?query=${encodeURIComponent(sql)}`)
  if (!resp.ok) throw new Error(`ClickHouse: ${resp.status}`)
  return await resp.text()
}

// Batch insert snapshots as TSV — much faster than row-by-row
async function insertSnapshots(rows) {
  if (rows.length === 0) return

  const header = "INSERT INTO trading.snapshots (trading_date, symbol, security_id, bucket, ltp, candle_open, candle_high, candle_low, volume_cum, volume_delta, vwap, volume_rate, candle_body_ratio) FORMAT TabSeparated\n"
  const tsv = rows.map(r =>
    `${r.trading_date}\t${r.symbol}\t${r.security_id}\t${r.bucket}\t${r.ltp}\t${r.candle_open}\t${r.candle_high}\t${r.candle_low}\t${r.volume_cum}\t${r.volume_delta}\t${r.vwap}\t${r.volume_rate}\t${r.candle_body_ratio}`
  ).join("\n")

  await chQuery(header + tsv)
}

// ─── Main ───────────────────────────────────────────────────────────────────

const args = process.argv.slice(2)
if (args.length === 0) {
  console.log("Usage: bun deploy/backfill-month.js 2024-08")
  console.log("       bun deploy/backfill-month.js 2024-08 2024-10  (range)")
  process.exit(1)
}

// Parse month range
let fromMonth = args[0]
let toMonth = args[1] || args[0]

// Build full date range across months
const fromRange = getMonthRange(fromMonth)
const toRange = getMonthRange(toMonth)
// Extend from by 5 days for prev_day lookback
const lookbackFrom = new Date(fromRange.from + "T00:00:00Z")
lookbackFrom.setUTCDate(lookbackFrom.getUTCDate() - 7)
const lookbackFromStr = lookbackFrom.toISOString().split("T")[0]

const tradingDays = getTradingDays(lookbackFromStr, toRange.to)
const targetDays = getTradingDays(fromRange.from, toRange.to)
const chunks = chunkArray(tradingDays, CHUNK_TRADING_DAYS)

console.log(`\nBackfill: ${fromMonth} to ${toMonth}`)
console.log(`  Target days: ${targetDays.length} | With lookback: ${tradingDays.length} | Chunks: ${chunks.length}`)

// 1. Fetch watchlist from ClickHouse
console.log("\nFetching watchlist from ClickHouse...")
const watchlistRaw = await chFetch("SELECT symbol, security_id FROM trading.watchlist FINAL WHERE enabled = 1 FORMAT TabSeparated")
const stocks = watchlistRaw.trim().split("\n").filter(Boolean).map(line => {
  const [symbol, security_id] = line.split("\t")
  return { symbol, security_id }
})
console.log(`  ${stocks.length} symbols from watchlist`)

// 2. Check what's already in snapshots (resume-safe)
console.log("Checking existing data...")
const existingRaw = await chFetch(
  `SELECT DISTINCT toString(trading_date), symbol FROM trading.snapshots WHERE trading_date >= '${lookbackFromStr}' AND trading_date <= '${toRange.to}' FORMAT TabSeparated`
)
const existingSet = new Set(existingRaw.trim().split("\n").filter(Boolean).map(l => l.replace("\t", "|")))
console.log(`  ${existingSet.size} existing (date, symbol) pairs — will skip these`)

// 3. Process stocks in concurrent batches
const startTime = Date.now()
let totalInserted = 0, totalSkipped = 0, totalEmpty = 0, totalErrors = 0, reqCount = 0
let snapshotBuffer = []  // accumulate rows for batch insert
const BUFFER_FLUSH_SIZE = 50000  // flush every 50k rows to reduce ClickHouse parts

async function flushBuffer() {
  if (snapshotBuffer.length === 0) return
  const batch = snapshotBuffer
  snapshotBuffer = []
  await insertSnapshots(batch)
}

console.log(`\nStarting download... (${CONCURRENT_STOCKS} concurrent, ${CHUNK_TRADING_DAYS} days/req)\n`)

for (let si = 0; si < stocks.length; si += CONCURRENT_STOCKS) {
  const stockBatch = stocks.slice(si, si + CONCURRENT_STOCKS)

  const batchResults = await Promise.all(stockBatch.map(async (stock, batchIdx) => {
    const { symbol, security_id } = stock
    let inserted = 0, skipped = 0, empty = 0, errors = 0, reqs = 0

    for (const chunk of chunks) {
      // Check which days need downloading
      const pendingDays = chunk.days.filter(d => !existingSet.has(`${d}|${symbol}`))
      skipped += chunk.days.length - pendingDays.length
      if (pendingDays.length === 0) continue

      // Throttle
      if (reqs > 0) await sleep(batchDelay)

      const token = TOKENS[(tokenIdx + batchIdx) % TOKENS.length]
      const result = await fetchChunk(security_id, chunk.fromDate, chunk.toDate, token)
      reqs++
      reqCount++

      if (result.error) {
        if (result.error === "rate_limit") {
          batchDelay = Math.min(DELAY_MAX, batchDelay + 500)
          await sleep(3000)
        }
        errors += pendingDays.length
        continue
      }

      // Split candles by date
      if (!result.data || !result.data.open || result.data.open.length === 0) {
        empty += pendingDays.length
        continue
      }

      // Parse candles from Dhan format
      const byDate = new Map()
      for (let j = 0; j < result.data.open.length; j++) {
        const ts = result.data.timestamp[j]
        const date = new Date(ts * 1000).toISOString().split("T")[0]
        if (!byDate.has(date)) byDate.set(date, [])
        byDate.get(date).push({
          timestamp: ts,
          open: result.data.open[j],
          high: result.data.high[j],
          low: result.data.low[j],
          close: result.data.close[j],
          volume: result.data.volume?.[j] || 0,
        })
      }

      // Convert to snapshots for each pending day
      for (const day of pendingDays) {
        const candles = byDate.get(day)
        if (!candles || candles.length === 0) { empty++; continue }

        const rows = candlesToSnapshots(symbol, security_id, day, candles)
        if (rows.length > 0) {
          snapshotBuffer.push(...rows)
          inserted += rows.length
          existingSet.add(`${day}|${symbol}`)  // mark as done
        }
      }

      // Adaptive throttle on success
      batchDelay = Math.max(DELAY_MIN, batchDelay - 50)

      // Flush buffer if large enough
      if (snapshotBuffer.length >= BUFFER_FLUSH_SIZE) {
        await flushBuffer()
      }
    }

    return { symbol, inserted, skipped, empty, errors, reqs }
  }))

  tokenIdx = (tokenIdx + stockBatch.length) % TOKENS.length

  for (let i = 0; i < batchResults.length; i++) {
    const r = batchResults[i]
    totalInserted += r.inserted
    totalSkipped += r.skipped
    totalEmpty += r.empty
    totalErrors += r.errors
    const globalIdx = si + i + 1
    const elapsed = ((Date.now() - startTime) / 1000 / 60).toFixed(1)
    const pct = (globalIdx / stocks.length * 100).toFixed(1)

    if (r.reqs > 0 || globalIdx % 100 === 0) {
      console.log(
        `[${globalIdx}/${stocks.length}] (${pct}%) ${r.symbol.padEnd(20)} ` +
        `rows=${r.inserted} skip=${r.skipped} empty=${r.empty} err=${r.errors} ` +
        `reqs=${reqCount} delay=${batchDelay}ms ${elapsed}m`
      )
    }
  }
}

// Flush remaining buffer
await flushBuffer()

// 4. Populate daily_ref from the new snapshot data
console.log("\nPopulating daily_ref...")
try {
  await chQuery(`
    INSERT INTO trading.daily_ref (trading_date, symbol, security_id, day_open, closing_price, prev_close, gap_pct, prev_day_high, prev_day_low)
    WITH daily AS (
        SELECT trading_date, symbol, any(security_id) AS security_id,
            argMin(candle_open, bucket) AS day_open,
            argMax(ltp, bucket) AS closing_price,
            max(candle_high) AS day_high,
            minIf(candle_low, candle_low > 0) AS day_low
        FROM trading.snapshots
        WHERE trading_date >= '${lookbackFromStr}' AND trading_date <= '${toRange.to}'
        GROUP BY trading_date, symbol
    ),
    with_prev AS (
        SELECT trading_date, symbol, security_id, day_open, closing_price, day_high, day_low,
            lagInFrame(closing_price) OVER (PARTITION BY symbol ORDER BY trading_date) AS prev_close,
            lagInFrame(day_high) OVER (PARTITION BY symbol ORDER BY trading_date) AS prev_day_high,
            lagInFrame(day_low) OVER (PARTITION BY symbol ORDER BY trading_date) AS prev_day_low
        FROM daily
    )
    SELECT trading_date, symbol, security_id, day_open, closing_price,
        coalesce(prev_close, 0),
        if(prev_close > 0, (day_open - prev_close) / prev_close * 100, 0),
        coalesce(prev_day_high, 0), coalesce(prev_day_low, 0)
    FROM with_prev
    WHERE trading_date >= '${fromRange.from}' AND trading_date <= '${toRange.to}'
  `)
  // Verify
  const drCount = await chFetch(
    `SELECT count() FROM trading.daily_ref FINAL WHERE trading_date >= '${fromRange.from}' AND trading_date <= '${toRange.to}' FORMAT TabSeparated`
  )
  console.log(`  daily_ref: ${drCount.trim()} rows inserted`)
} catch (e) {
  console.error("  daily_ref error:", e.message)
}

// 5. Summary
const totalMin = ((Date.now() - startTime) / 1000 / 60).toFixed(1)
console.log(`\n--- Summary (${totalMin}m) ---`)
console.log(`Snapshot rows : ${totalInserted.toLocaleString()}`)
console.log(`Skipped       : ${totalSkipped.toLocaleString()} (already in CH)`)
console.log(`Empty         : ${totalEmpty.toLocaleString()} (no data from Dhan)`)
console.log(`Errors        : ${totalErrors.toLocaleString()}`)
console.log(`API requests  : ${reqCount.toLocaleString()}`)
console.log(`Month range   : ${fromMonth} to ${toMonth}`)
