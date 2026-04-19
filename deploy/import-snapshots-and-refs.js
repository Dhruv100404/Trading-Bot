#!/usr/bin/env bun
// Import consolidated NDJSON candle data into trading.snapshots + trading.daily_ref
// Sources (chronological order):
//   data/candles-consolidated_new.ndjson  → Dec 2025 & Jan 2026
//   data/candles-consolidated.ndjson      → Feb 2026 & Mar 2026
//
// Usage: bun deploy/import-snapshots-and-refs.js [--refs-only] [--snapshots-only]

import { createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const CH_URL = process.env.CH_URL || "http://localhost:8123"
const MAX_BUCKET = 105 // 9:15 + 105 min = 11:00 AM

const args = new Set(process.argv.slice(2))
const REFS_ONLY = args.has("--refs-only")
const SNAPSHOTS_ONLY = args.has("--snapshots-only")

const NDJSON_FILES = [
  "data/candles-consolidated_new.ndjson", // Dec-Jan
  "data/candles-consolidated.ndjson",     // Feb-Mar
]

console.log(`Import → ClickHouse ${CH_URL}`)
if (REFS_ONLY) console.log("  refs-only mode")
if (SNAPSHOTS_ONLY) console.log("  snapshots-only mode")
const start = Date.now()

// ── Load watchlist ──
const wlResp = await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled = 1 FORMAT JSONEachRow"
)}`)
const wlLines = (await wlResp.text()).trim().split("\n").filter(Boolean)
const symbolToSecId = {}
for (const line of wlLines) {
  const r = JSON.parse(line)
  symbolToSecId[r.symbol] = r.security_id
}
console.log(`Watchlist: ${Object.keys(symbolToSecId).length} symbols\n`)

// ── ClickHouse insert helper ──
async function chInsert(sql) {
  const resp = await fetch(`${CH_URL}/`, { method: "POST", body: sql })
  if (!resp.ok) {
    const err = await resp.text()
    if (!err.includes("duplicate")) throw new Error(err.substring(0, 200))
  }
}

// Track prev_close per symbol across files for daily_ref
const prevDay = new Map() // symbol → { close, high, low }

let snapInserted = 0, refInserted = 0, skipped = 0, snapErrors = 0, refErrors = 0
let lineCount = 0

// Batch inserts for speed
const BATCH_FLUSH = 5000 // rows per INSERT
let snapBatch = []
let refBatch = []

async function flushSnapBatch() {
  if (snapBatch.length === 0) return
  const sql = `INSERT INTO trading.snapshots (trading_date,symbol,security_id,bucket,ltp,candle_open,candle_high,candle_low,volume_cum,volume_delta,oi_total,oi_delta,bid,ask,bid_qty,ask_qty,spread_pct,vwap,price_velocity,volume_rate,candle_body_ratio) VALUES ${snapBatch.join(",")}`
  snapBatch = []
  try {
    await chInsert(sql)
    snapInserted++
  } catch (e) {
    snapErrors++
    if (snapErrors <= 5) console.error(`  SNAP batch error: ${e.message}`)
  }
}

async function flushRefBatch() {
  if (refBatch.length === 0) return
  const sql = `INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES ${refBatch.join(",")}`
  refBatch = []
  try {
    await chInsert(sql)
    refInserted++
  } catch (e) {
    refErrors++
    if (refErrors <= 5) console.error(`  REF batch error: ${e.message}`)
  }
}

for (const file of NDJSON_FILES) {
  console.log(`Processing ${file}...`)

  const rl = createInterface({ input: createReadStream(file), crlfDelay: Infinity })

  for await (const line of rl) {
    if (!line) continue
    lineCount++

    let rec
    try { rec = JSON.parse(line) } catch { continue }

    const { symbol, security_id, date, dayOpen, gapPct, buckets } = rec
    const secId = security_id || symbolToSecId[symbol]
    if (!secId || !buckets || buckets.length === 0) { skipped++; continue }

    // Filter buckets to <= MAX_BUCKET (11:00 AM)
    const filteredBuckets = buckets.filter(b => b.b <= MAX_BUCKET)
    if (filteredBuckets.length === 0) continue

    // ── Build snapshot rows ──
    if (!REFS_ONLY) {
      for (const b of filteredBuckets) {
        const volDelta = b.v
        const priceVelocity = 0 // not in NDJSON
        snapBatch.push(
          `('${date}','${symbol}','${secId}',${b.b},${b.c},${b.o},${b.h},${b.l},` +
          `${b.vc},${volDelta},0,0,0,0,0,0,0,` +
          `${b.vw},${priceVelocity},${b.vr},${b.br})`
        )
      }
      if (snapBatch.length >= BATCH_FLUSH) await flushSnapBatch()
    }

    // ── Insert daily_ref ──
    if (!SNAPSHOTS_ONLY) {
      const prev = prevDay.get(symbol) || { close: 0, high: 0, low: 0 }
      const closingPrice = filteredBuckets[filteredBuckets.length - 1].c
      const dayHigh = Math.max(...filteredBuckets.map(b => b.h))
      const dayLow = Math.min(...filteredBuckets.map(b => b.l))

      // Recompute gap from tracked prev_close (more accurate across files)
      const computedGap = prev.close > 0
        ? Math.round((dayOpen - prev.close) / prev.close * 10000) / 100
        : gapPct

      refBatch.push(`('${date}','${symbol}','${secId}',${prev.close},${dayOpen},${dayOpen},${computedGap},${prev.high},${prev.low},${closingPrice})`)
      if (refBatch.length >= BATCH_FLUSH) await flushRefBatch()

      prevDay.set(symbol, { close: closingPrice, high: dayHigh, low: dayLow })
    }

    if (lineCount % 5000 === 0) {
      const elapsed = ((Date.now() - start) / 1000).toFixed(1)
      console.log(`  ${lineCount} lines — ${elapsed}s — snaps=${snapInserted} refs=${refInserted} err=${snapErrors + refErrors}`)
    }
  }
}

// Flush remaining batches
await flushSnapBatch()
await flushRefBatch()

console.log(`\nDone in ${((Date.now() - start) / 1000).toFixed(1)}s`)
console.log(`  Lines processed: ${lineCount}`)
console.log(`  Snapshots inserted: ${snapInserted} batches (${snapErrors} errors)`)
console.log(`  Daily refs inserted: ${refInserted} (${refErrors} errors)`)
console.log(`  Skipped: ${skipped}`)

// ── Verify ──
const snapCount = await (await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT count(), countDistinct(symbol), min(trading_date), max(trading_date) FROM trading.snapshots"
)}`)).text()
console.log(`\nSnapshots total: ${snapCount.trim()}`)

const refCount = await (await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT count(), countDistinct(symbol), min(trading_date), max(trading_date) FROM trading.daily_ref FINAL"
)}`)).text()
console.log(`Daily refs total: ${refCount.trim()}`)
