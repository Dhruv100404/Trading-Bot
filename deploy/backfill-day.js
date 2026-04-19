#!/usr/bin/env bun
// Backfill 1-min candles for all F&O stocks for a given date
// Usage: bun deploy/backfill-day.js 2026-03-25

const TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NjQyNTM5LCJpYXQiOjE3NzQ1NTYxMzksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.9AqLMlOCG7e9d4C-oJv0QdIWoUwPHheQEn--VE--8Wo2S40KztNs5hFpyBIdCK236k7DIcbHoI2U9m5ih6XuCw"
const CH_URL = process.env.CH_URL || "http://localhost:8123"
const DATE = process.argv[2] || new Date().toISOString().split('T')[0]

console.log(`Backfilling ${DATE}...`)

// Get F&O stocks from ClickHouse
const stocksResp = await fetch(`${CH_URL}/?query=${encodeURIComponent(
  "SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled = 1 AND has(tiers, 'F&O') FORMAT JSONEachRow"
)}`)
const stockLines = (await stocksResp.text()).trim().split('\n').filter(Boolean)
const stocks = stockLines.map(l => JSON.parse(l))
console.log(`Found ${stocks.length} F&O stocks`)

let inserted = 0, errors = 0, skipped = 0

for (let i = 0; i < stocks.length; i++) {
  const { security_id, symbol } = stocks[i]

  // Rate limit: 1 req/sec
  if (i > 0 && i % 5 === 0) await Bun.sleep(1000)

  try {
    const resp = await fetch("https://api.dhan.co/v2/charts/intraday", {
      method: "POST",
      headers: { "access-token": TOKEN, "client-id": "1100896497", "Content-Type": "application/json" },
      body: JSON.stringify({ securityId: security_id, exchangeSegment: "NSE_EQ", instrument: "EQUITY", interval: "1", fromDate: DATE, toDate: DATE })
    })

    const data = await resp.json()
    if (!data.open || data.open.length === 0) { skipped++; continue }

    const candles = data.open.length

    // Build INSERT values
    const rows = []
    let prevVol = 0
    for (let j = 0; j < candles; j++) {
      const ts = data.timestamp[j]
      const dt = new Date(ts * 1000)
      const istH = (dt.getUTCHours() + 5) % 24 + Math.floor((dt.getUTCMinutes() + 30) / 60)
      const istM = (dt.getUTCMinutes() + 30) % 60
      const bucket = (istH * 60 + istM) - (9 * 60 + 15) + 1
      if (bucket < 1 || bucket > 105) continue

      const open = data.open[j]
      const high = data.high[j]
      const low = data.low[j]
      const close = data.close[j]
      const vol = data.volume[j]
      const volDelta = j === 0 ? vol : vol
      const volCum = j === 0 ? vol : (rows.length > 0 ? rows[rows.length - 1].volCum + vol : vol)
      const vwap = (open + high + low + close) / 4
      const bodyRatio = high !== low ? Math.abs(close - open) / (high - low) : 0
      const volRate = vol / 60

      rows.push({
        trading_date: DATE, symbol, security_id, bucket,
        ltp: close, candle_open: open, candle_high: high, candle_low: low,
        volume_cum: volCum, volume_delta: vol,
        oi_total: 0, oi_delta: 0,
        bid: 0, ask: 0, bid_qty: 0, ask_qty: 0,
        spread_pct: 0, vwap: Math.round(vwap * 100) / 100,
        price_velocity: 0, volume_rate: Math.round(volRate * 100) / 100,
        candle_body_ratio: Math.round(bodyRatio * 10000) / 10000,
        volCum
      })
    }

    if (rows.length === 0) { skipped++; continue }

    // Batch insert via ClickHouse HTTP
    const values = rows.map(r =>
      `('${r.trading_date}','${r.symbol}','${r.security_id}',${r.bucket},${r.ltp},${r.candle_open},${r.candle_high},${r.candle_low},${r.volume_cum},${r.volume_delta},${r.oi_total},${r.oi_delta},${r.bid},${r.ask},${r.bid_qty},${r.ask_qty},${r.spread_pct},${r.vwap},${r.price_velocity},${r.volume_rate},${r.candle_body_ratio})`
    ).join(',')

    const insertSQL = `INSERT INTO trading.snapshots (trading_date,symbol,security_id,bucket,ltp,candle_open,candle_high,candle_low,volume_cum,volume_delta,oi_total,oi_delta,bid,ask,bid_qty,ask_qty,spread_pct,vwap,price_velocity,volume_rate,candle_body_ratio) VALUES ${values}`

    const insertResp = await fetch(`${CH_URL}/`, { method: "POST", body: insertSQL })
    if (insertResp.ok) {
      inserted++
      if ((i + 1) % 20 === 0 || i === stocks.length - 1) {
        console.log(`  [${i+1}/${stocks.length}] ${symbol}: ${rows.length} candles ✓ (${inserted} done, ${skipped} skipped, ${errors} errors)`)
      }
    } else {
      const err = await insertResp.text()
      console.error(`  ${symbol}: INSERT failed: ${err.substring(0, 100)}`)
      errors++
    }
  } catch (e) {
    console.error(`  ${symbol}: ${e.message}`)
    errors++
  }
}

console.log(`\nDone! Inserted: ${inserted}, Skipped: ${skipped}, Errors: ${errors}`)

// Verify
const count = await (await fetch(`${CH_URL}/?query=${encodeURIComponent(`SELECT count(), countDistinct(symbol) FROM trading.snapshots WHERE trading_date = '${DATE}'`)}`)).text()
console.log(`Snapshots for ${DATE}: ${count.trim()}`)
