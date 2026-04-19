#!/usr/bin/env bun
// Import consolidated NDJSON into ClickHouse trading.snapshots
// This imports ALL buckets (full day, including afternoon)
// Usage: bun deploy/import-ndjson-to-clickhouse.js [data/candles-consolidated.ndjson] [http://localhost:8123]

import { createReadStream } from "node:fs"
import { createInterface } from "node:readline"

const DATA_FILE = process.argv[2] || "data/candles-consolidated.ndjson"
const CH_URL = process.argv[3] || "http://localhost:8123"
const BATCH_SIZE = 5000

console.log(`Importing ${DATA_FILE} → ClickHouse ${CH_URL}`)
const start = Date.now()

let totalInserted = 0, totalErrors = 0, lc = 0
let batch = []

async function flushBatch() {
  if (batch.length === 0) return
  const rows = batch.join(",")
  const sql = `INSERT INTO trading.snapshots (trading_date,symbol,security_id,bucket,ltp,candle_open,candle_high,candle_low,volume_cum,volume_delta,oi_total,oi_delta,bid,ask,bid_qty,ask_qty,spread_pct,vwap,price_velocity,volume_rate,candle_body_ratio) VALUES ${rows}`
  try {
    const resp = await fetch(`${CH_URL}/`, { method: "POST", body: sql })
    if (resp.ok) {
      totalInserted += batch.length
    } else {
      const err = await resp.text()
      totalErrors++
      if (totalErrors <= 3) console.error(`  Error: ${err.substring(0, 150)}`)
    }
  } catch (e) {
    totalErrors++
  }
  batch = []
}

const rl = createInterface({ input: createReadStream(DATA_FILE), crlfDelay: Infinity })

for await (const line of rl) {
  if (!line.trim()) continue
  const sd = JSON.parse(line)
  lc++
  const { symbol, date, buckets } = sd
  if (!buckets?.length) continue

  for (const b of buckets) {
    batch.push(
      `('${date}','${symbol}','','${b.b}',${b.c},${b.o || b.c},${b.h},${b.l},` +
      `${b.vc || 0},${b.v || 0},0,0,0,0,0,0,0,` +
      `${b.vw || 0},0,${b.vr || 0},${b.br || 0})`
    )
    if (batch.length >= BATCH_SIZE) await flushBatch()
  }

  if (lc % 500 === 0) {
    const elapsed = ((Date.now() - start) / 1000).toFixed(1)
    process.stderr.write(`  ${lc} stocks, ${totalInserted} rows, ${elapsed}s\r`)
  }
}

await flushBatch()

console.log(`\nDone: ${lc} stock-days, ${totalInserted} rows inserted, ${totalErrors} errors`)
console.log(`${((Date.now() - start) / 1000).toFixed(1)}s`)

// Verify
const countResp = await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT count(), countDistinct(symbol), countDistinct(trading_date), min(trading_date), max(trading_date), max(bucket) FROM trading.snapshots"
)}`)
console.log(`Snapshots: ${(await countResp.text()).trim()}`)
