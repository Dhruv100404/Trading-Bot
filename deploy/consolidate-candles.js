#!/usr/bin/env bun
// Step 1: Merge all JSON candle files into a single compact binary for fast analysis
// Output: data/candles.bin — one read, all data in memory
// Usage: bun deploy/consolidate-candles.js

import { readdirSync, readFileSync, writeFileSync, existsSync } from "node:fs"

const DATA_DIR = process.argv[2] || "data/candles_new"
const OUT_FILE = process.argv[3] || "data/candles-consolidated_new.ndjson"

console.log(`Consolidating ${DATA_DIR} → ${OUT_FILE}`)
const start = Date.now()

const stockDirs = readdirSync(DATA_DIR, { withFileTypes: true })
  .filter(d => d.isDirectory())
  .map(d => d.name)
  .sort()

console.log(`${stockDirs.length} stock directories`)

// IST offset
const IST_OFFSET = 5 * 3600 + 30 * 60
const MARKET_OPEN_MIN = 9 * 60 + 15
const MARKET_CLOSE_MIN = 15 * 60 + 30

function tsBucket(ts) {
  const istSecs = ts + IST_OFFSET
  const dayMins = Math.floor(((istSecs % 86400) + 86400) % 86400 / 60)
  if (dayMins < MARKET_OPEN_MIN || dayMins >= MARKET_CLOSE_MIN) return 0
  return dayMins - MARKET_OPEN_MIN + 1
}

// Process all stocks, output one record per stock-day with pre-computed features
// Stream NDJSON — one line per stock-day, never holds everything in memory
const fd = Bun.file(OUT_FILE).writer()
let totalDays = 0

for (let si = 0; si < stockDirs.length; si++) {
  const symbol = stockDirs[si]
  const dir = `${DATA_DIR}/${symbol}`

  const files = readdirSync(dir)
    .filter(f => f.endsWith(".json"))
    .sort()

  let prevClose = null

  for (const file of files) {
    const date = file.replace(".json", "")
    const path = `${dir}/${file}`

    let cf
    try {
      cf = JSON.parse(readFileSync(path, "utf-8"))
    } catch { continue }

    if (!cf.candles || cf.candles.length === 0) { continue }

    const dayOpen = cf.candles[0].open
    const gapPct = prevClose && prevClose > 0 ? (dayOpen - prevClose) / prevClose * 100 : 0
    prevClose = cf.candles[cf.candles.length - 1].close

    // Compute per-bucket data
    let volCum = 0, prevLtp = 0, vwapNum = 0, vwapDen = 0
    const buckets = []

    for (const c of cf.candles) {
      const bucket = tsBucket(c.timestamp)
      if (bucket === 0) continue

      const vol = c.volume
      volCum += vol

      const ltp = c.close
      const volForVwap = prevLtp === 0 ? volCum : vol
      vwapNum += ltp * volForVwap
      vwapDen += volForVwap
      const vwap = vwapDen > 0 ? vwapNum / vwapDen : ltp
      const volRate = vol / 60
      const range = c.high - c.low
      const bodyRatio = range > 0 ? Math.abs(ltp - c.open) / range : 0

      buckets.push({
        b: bucket, o: c.open, h: c.high, l: c.low, c: ltp,
        v: vol, vc: volCum, vw: Math.round(vwap * 100) / 100,
        vr: Math.round(volRate * 100) / 100,
        br: Math.round(bodyRatio * 1000) / 1000,
      })
      prevLtp = ltp
    }

    if (buckets.length < 3) continue

    // Pre-compute features
    const f5 = buckets.filter(b => b.b >= 1 && b.b <= 5)
    const f5Hi = f5.length ? Math.max(...f5.map(b => b.h)) : dayOpen
    const f5Lo = f5.length ? Math.min(...f5.map(b => b.l)) : dayOpen
    const f5Range = dayOpen > 0 ? (f5Hi - f5Lo) / dayOpen * 100 : 0
    const f5Vol = f5.length ? f5[f5.length - 1].vc : 0

    let maxUp20 = 0, maxDown20 = 0, maxUp45 = 0, maxDown45 = 0, maxUp60 = 0, maxDown60 = 0
    for (const b of buckets) {
      const up = (b.h - dayOpen) / dayOpen * 100
      const down = (dayOpen - b.l) / dayOpen * 100
      if (b.b <= 20) { maxUp20 = Math.max(maxUp20, up); maxDown20 = Math.max(maxDown20, down) }
      if (b.b <= 45) { maxUp45 = Math.max(maxUp45, up); maxDown45 = Math.max(maxDown45, down) }
      if (b.b <= 60) { maxUp60 = Math.max(maxUp60, up); maxDown60 = Math.max(maxDown60, down) }
    }

    // Write one NDJSON line (streamed, no OOM)
    fd.write(JSON.stringify({
      symbol, security_id: cf.security_id, date, dayOpen, gapPct: Math.round(gapPct * 100) / 100,
      f5Range: Math.round(f5Range * 100) / 100, f5Vol,
      maxUp20: Math.round(maxUp20 * 100) / 100, maxDown20: Math.round(maxDown20 * 100) / 100,
      maxUp45: Math.round(maxUp45 * 100) / 100, maxDown45: Math.round(maxDown45 * 100) / 100,
      maxUp60: Math.round(maxUp60 * 100) / 100, maxDown60: Math.round(maxDown60 * 100) / 100,
      buckets,
    }) + "\n")
    totalDays++
  }

  if ((si + 1) % 200 === 0 || si === stockDirs.length - 1) {
    const elapsed = ((Date.now() - start) / 1000).toFixed(1)
    console.log(`[${si + 1}/${stockDirs.length}] ${elapsed}s — ${totalDays} stock-days`)
  }
}

fd.flush()
fd.end()
console.log(`Done: ${totalDays} stock-days, ${((Date.now() - start) / 1000).toFixed(1)}s`)
