#!/usr/bin/env bun
// Import downloaded JSON candle data into ClickHouse trading.snapshots
// So the UI backtest can use all 2462 stocks
// Usage: bun deploy/import-candles-to-clickhouse.js [data/candles] [http://localhost:8123]

import { readdirSync, readFileSync } from "node:fs"

const DATA_DIR = process.argv[2] || "data/candles"
const CH_URL = process.argv[3] || "http://localhost:8123"

const IST_OFFSET = 5 * 3600 + 30 * 60
const MARKET_OPEN_MIN = 9 * 60 + 15

console.log(`Importing ${DATA_DIR} → ClickHouse ${CH_URL}`)
const start = Date.now()

const stockDirs = readdirSync(DATA_DIR, { withFileTypes: true })
  .filter(d => d.isDirectory())
  .map(d => d.name).sort()

console.log(`${stockDirs.length} stock directories`)

// Get security_id → symbol mapping from watchlist
const wlResp = await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled = 1 FORMAT JSONEachRow"
)}`)
const wlLines = (await wlResp.text()).trim().split("\n").filter(Boolean)
const symbolToSecId = {}
for (const line of wlLines) {
  const r = JSON.parse(line)
  symbolToSecId[r.symbol] = r.security_id
}
console.log(`Watchlist: ${Object.keys(symbolToSecId).length} symbols`)

let totalInserted = 0, totalSkipped = 0, totalErrors = 0

for (let si = 0; si < stockDirs.length; si++) {
  const symbol = stockDirs[si]
  const secId = symbolToSecId[symbol]
  if (!secId) { totalSkipped++; continue }

  const dir = `${DATA_DIR}/${symbol}`
  const files = readdirSync(dir).filter(f => f.endsWith(".json")).sort()

  for (const file of files) {
    const date = file.replace(".json", "")
    const path = `${dir}/${file}`

    let cf
    try { cf = JSON.parse(readFileSync(path, "utf-8")) } catch { continue }
    if (!cf.candles || cf.candles.length === 0) continue

    // Build snapshot rows
    let volCum = 0, prevLtp = 0, prevVolCum = 0, vwapNum = 0, vwapDen = 0
    const rows = []

    for (const c of cf.candles) {
      const istSecs = c.timestamp + IST_OFFSET
      const dayMins = Math.floor(((istSecs % 86400) + 86400) % 86400 / 60)
      if (dayMins < MARKET_OPEN_MIN || dayMins >= 15 * 60 + 30) continue
      const bucket = dayMins - MARKET_OPEN_MIN + 1

      volCum += c.volume
      const ltp = c.close
      const volDelta = volCum - prevVolCum
      const volForVwap = prevVolCum === 0 ? volCum : volDelta
      vwapNum += ltp * volForVwap
      vwapDen += volForVwap
      const vwap = vwapDen > 0 ? vwapNum / vwapDen : ltp
      const volRate = volDelta / 60
      const range = c.high - c.low
      const bodyRatio = range > 0 ? Math.abs(ltp - c.open) / range : 0
      const priceVelocity = prevLtp > 0 ? (ltp - prevLtp) / 60 : 0

      rows.push(
        `('${date}','${symbol}','${secId}',${bucket},${ltp},${c.open},${c.high},${c.low},` +
        `${volCum},${Math.round(volDelta)},0,0,0,0,0,0,0,` +
        `${Math.round(vwap * 100) / 100},${Math.round(priceVelocity * 10000) / 10000},` +
        `${Math.round(volRate * 100) / 100},${Math.round(bodyRatio * 10000) / 10000})`
      )
      prevLtp = ltp
      prevVolCum = volCum
    }

    if (rows.length === 0) continue

    // Batch insert
    const sql = `INSERT INTO trading.snapshots (trading_date,symbol,security_id,bucket,ltp,candle_open,candle_high,candle_low,volume_cum,volume_delta,oi_total,oi_delta,bid,ask,bid_qty,ask_qty,spread_pct,vwap,price_velocity,volume_rate,candle_body_ratio) VALUES ${rows.join(",")}`

    try {
      const resp = await fetch(`${CH_URL}/`, { method: "POST", body: sql })
      if (resp.ok) {
        totalInserted++
      } else {
        const err = await resp.text()
        if (!err.includes("duplicate")) {
          totalErrors++
          if (totalErrors <= 5) console.error(`  ${symbol}/${date}: ${err.substring(0, 100)}`)
        }
      }
    } catch (e) {
      totalErrors++
    }

    // Rate limit: don't overwhelm ClickHouse
    if (totalInserted % 100 === 0 && totalInserted > 0) {
      await new Promise(r => setTimeout(r, 50))
    }
  }

  if ((si + 1) % 100 === 0 || si === stockDirs.length - 1) {
    const elapsed = ((Date.now() - start) / 1000).toFixed(1)
    console.log(`[${si + 1}/${stockDirs.length}] ${elapsed}s — inserted=${totalInserted} skipped=${totalSkipped} errors=${totalErrors}`)
  }
}

console.log(`\nDone: ${totalInserted} stock-days inserted, ${totalSkipped} skipped (no secId), ${totalErrors} errors`)
console.log(`${((Date.now() - start) / 1000).toFixed(1)}s`)

// Verify
const countResp = await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT count(), countDistinct(symbol), min(trading_date), max(trading_date) FROM trading.snapshots"
)}`)
console.log(`Snapshots total: ${(await countResp.text()).trim()}`)
