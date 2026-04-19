#!/usr/bin/env bun
// Download 1-min candles for ALL NSE EQ shares from Dhan scrip master
// Usage: bun deploy/download-candles.js [fromDate] [toDate] [outDir]
//
// - Stock-first: completes all days for stock 1, then stock 2, etc.
// - Multi-day chunks: fetches ~10 trading days per API call (12x fewer requests)
// - 5 tokens round-robin, concurrent stock batches with adaptive throttle
// - Resume-safe: skips already-downloaded files
// - Daily quota aware: stops at 90k, re-run next day

import { mkdir } from "node:fs/promises"
import { existsSync, readFileSync, writeFileSync } from "node:fs"

// 5 tokens — each has its own 100k/day quota, round-robin across them
const TOKENS = [
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NTg2MzgxLCJpYXQiOjE3NzQ0OTk5ODEsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.r1l1iPVphU-OIgMdxaTbnsG5i8-AmzreNRyxNg94Ge3Fy5Oz9Wwav4vQSEoAii_eD-7c-kwZwH2kUq7oKXtZeg",
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NTg2MzY5LCJpYXQiOjE3NzQ0OTk5NjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.1hnt9Qzbp4FRImvIJ1M-0g_IbrTkuLKLjLLQHQfSXWwiZTtVPSW6dPkv_yphkAAlOenR0iY2394OEExuV5hKyA",
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NTg2MzYyLCJpYXQiOjE3NzQ0OTk5NjIsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.ml92xqr-mC2AJ1MJuQHyEGhyUGfmlIJLgeBvkkMmTZM-HXcxTcUOSi5fkWmF7J-tQtfRuOTcZb1SzYokrsEZ6g",
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NTg2MzU1LCJpYXQiOjE3NzQ0OTk5NTUsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.0h-5XN6BNDbOF7X4X7gHpGtg_ExKLqqwCNckiC2MqlRxjWykXPJkIw1ibHZTYpJb4bhX0MCJSsRmNu2aUuNa6Q",
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0NTQ4NjU1LCJpYXQiOjE3NzQ0NjIyNTUsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.gr7Cd3cYke6S2MB2G-yz1FbwWQ96J5d1cWtuJucIOujkOmdVYZKfNkqLUZyVjUGzAV3Wn3lg96zzK87c2iDSQA",
]
const CLIENT_ID = "1100896497"
const SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
const FROM_DATE = process.argv[2] || "2025-12-01"
const TO_DATE = process.argv[3] || "2026-03-25"
const OUT_DIR = process.argv[4] || "data/candles_new"

const MAX_RETRIES = 5
const CHUNK_TRADING_DAYS = 10  // ~10 trading days per API call (safe limit: tested up to 13)
const CONCURRENT_STOCKS = 5   // fetch 5 stocks at once (1 per token)
const DAILY_QUOTA = 90000 * TOKENS.length  // 450k total across all tokens
const QUOTA_FILE = `${OUT_DIR}/.quota.json`

// Adaptive throttle
let batchDelay = 200
const DELAY_MIN = 100
const DELAY_MAX = 2000
const DELAY_ON_SUCCESS = -50
const DELAY_ON_RATE_LIMIT = 500
let tokenIdx = 0  // round-robin counter

// NSE holidays (Dec 2025 – Mar 2026)
const NSE_HOLIDAYS = new Set([
  "2025-12-25", // Christmas
  "2026-01-26", // Republic Day
  "2026-03-03", // Holi
  "2026-03-14", // Holi (Dhuleti)
  "2026-03-30", // Id-ul-Fitr
  "2026-03-31", // Id-ul-Fitr
])

// --- Helpers ---

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

// Chunk trading days into date ranges of up to CHUNK_TRADING_DAYS each
// Returns array of { fromDate, toDate, days: [...tradingDays] }
function chunkTradingDays(tradingDays) {
  const chunks = []
  for (let i = 0; i < tradingDays.length; i += CHUNK_TRADING_DAYS) {
    const days = tradingDays.slice(i, i + CHUNK_TRADING_DAYS)
    chunks.push({
      fromDate: days[0],
      toDate: days[days.length - 1],
      days,
    })
  }
  return chunks
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

function loadQuota() {
  try {
    const data = JSON.parse(readFileSync(QUOTA_FILE, "utf-8"))
    const today = new Date().toISOString().split("T")[0]
    return data.date === today ? data.count : 0
  } catch { return 0 }
}

function saveQuota(count) {
  const today = new Date().toISOString().split("T")[0]
  writeFileSync(QUOTA_FILE, JSON.stringify({ date: today, count }))
}

// Fetch a multi-day chunk — returns { data } or { error }
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

      if (resp.status === 429 || resp.status === 400) {
        const body = await resp.text()
        const isRateLimit = resp.status === 429 || body.includes("DH-905")
        if (isRateLimit) {
          if (attempt < MAX_RETRIES) {
            await sleep(3000 * attempt)
            continue
          }
          return { error: "rate_limit" }
        }
        return { error: "invalid" }
      }

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

function parseCSVLine(line) {
  const fields = []
  let current = ""
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') { inQuotes = !inQuotes; continue }
    if (ch === ',' && !inQuotes) { fields.push(current.trim()); current = ""; continue }
    current += ch
  }
  fields.push(current.trim())
  return fields
}

// Split candles by date from a multi-day response
function splitByDate(apiData) {
  const byDate = new Map()
  if (!apiData || !apiData.open || apiData.open.length === 0) return byDate

  for (let j = 0; j < apiData.open.length; j++) {
    const ts = apiData.timestamp[j]
    const date = new Date(ts * 1000).toISOString().split("T")[0]
    if (!byDate.has(date)) byDate.set(date, [])
    byDate.get(date).push({
      timestamp: ts,
      open: apiData.open[j],
      high: apiData.high[j],
      low: apiData.low[j],
      close: apiData.close[j],
      volume: apiData.volume[j],
    })
  }
  return byDate
}

// --- Main ---

console.log(`\n📥 Download 1-min candles: ${FROM_DATE} → ${TO_DATE}`)
console.log(`   Output: ${OUT_DIR}`)
console.log(`   Tokens: ${TOKENS.length} | Chunk: ${CHUNK_TRADING_DAYS} days/req | Concurrent: ${CONCURRENT_STOCKS} stocks | Quota: ${DAILY_QUOTA.toLocaleString()}/day\n`)

// 1. Scrip master
console.log("Fetching Dhan scrip master...")
const scripResp = await fetch(SCRIP_MASTER_URL)
if (!scripResp.ok) { console.error("Failed:", scripResp.status); process.exit(1) }
const csvLines = (await scripResp.text()).split("\n").filter(Boolean)
const header = parseCSVLine(csvLines[0])

const colExch = header.indexOf("SEM_EXM_EXCH_ID")
const colSecId = header.indexOf("SEM_SMST_SECURITY_ID")
const colSymbol = header.indexOf("SEM_TRADING_SYMBOL")
const colSeries = header.indexOf("SEM_SERIES")
if ([colExch, colSecId, colSymbol, colSeries].includes(-1)) {
  console.error("CSV format changed!"); process.exit(1)
}

const stocks = []
const seen = new Set()
for (let i = 1; i < csvLines.length; i++) {
  const c = parseCSVLine(csvLines[i])
  if (c[colExch] !== "NSE" || c[colSeries] !== "EQ") continue
  const symbol = c[colSymbol], security_id = c[colSecId]
  if (!symbol || !security_id || seen.has(symbol)) continue
  if (symbol.includes("NSETEST")) continue
  seen.add(symbol)
  stocks.push({ security_id, symbol })
}

const tradingDays = getTradingDays(FROM_DATE, TO_DATE)
const chunks = chunkTradingDays(tradingDays)
console.log(`Stocks: ${stocks.length} | Days: ${tradingDays.length} | Chunks: ${chunks.length} (${CHUNK_TRADING_DAYS} days each)`)
console.log(`API calls needed: ~${(stocks.length * chunks.length).toLocaleString()} (was ~${(stocks.length * tradingDays.length).toLocaleString()} with 1-day requests)\n`)

await mkdir(OUT_DIR, { recursive: true })

// 2. State
let reqCount = loadQuota()
if (reqCount > 0) console.log(`Resuming — ${reqCount.toLocaleString()} requests used today\n`)

let totalFiles = 0, totalSkipped = 0, totalEmpty = 0, totalErrors = 0
const invalidStocks = new Set()
const startTime = Date.now()
let quotaHit = false

// 3. Process stocks in batches of CONCURRENT_STOCKS
for (let si = 0; si < stocks.length; si += CONCURRENT_STOCKS) {
  if (quotaHit) break

  const stockBatch = stocks.slice(si, si + CONCURRENT_STOCKS)

  // Process all stocks in this batch concurrently
  const batchResults = await Promise.all(stockBatch.map(async (stock, batchIdx) => {
    const { security_id, symbol } = stock
    const stockDir = `${OUT_DIR}/${symbol}`
    let stockFiles = 0, stockSkipped = 0, stockEmpty = 0, stockErrors = 0
    let isInvalid = false

    // Figure out which chunks still have pending days
    const pendingChunks = []
    for (const chunk of chunks) {
      const pending = chunk.days.filter(d => !existsSync(`${stockDir}/${d}.json`))
      const skipped = chunk.days.length - pending.length
      stockSkipped += skipped
      if (pending.length > 0) {
        pendingChunks.push({ ...chunk, pendingDays: pending })
      }
    }

    if (pendingChunks.length === 0) {
      return { symbol, stockFiles, stockSkipped, stockEmpty, stockErrors, isInvalid, reqs: 0 }
    }

    // Create folder
    if (!existsSync(stockDir)) await mkdir(stockDir, { recursive: true })

    let reqs = 0

    for (const chunk of pendingChunks) {
      if (isInvalid || quotaHit) break

      // Quota check
      if (reqCount >= DAILY_QUOTA) {
        quotaHit = true
        break
      }

      // Throttle between chunks
      if (reqs > 0) await sleep(batchDelay)

      const token = TOKENS[(tokenIdx + batchIdx) % TOKENS.length]
      const result = await fetchChunk(security_id, chunk.fromDate, chunk.toDate, token)
      reqs++
      reqCount++

      if (result.error) {
        if (result.error === "invalid") {
          isInvalid = true
          invalidStocks.add(symbol)
        } else if (result.error === "rate_limit") {
          batchDelay = Math.min(DELAY_MAX, batchDelay + DELAY_ON_RATE_LIMIT)
          await sleep(3000)
        }
        stockErrors += chunk.pendingDays.length
        totalErrors += chunk.pendingDays.length
        continue
      }

      // Split response by date and write individual day files
      const byDate = splitByDate(result.data)

      for (const day of chunk.pendingDays) {
        const candles = byDate.get(day)
        if (!candles || candles.length === 0) {
          stockEmpty++
          totalEmpty++
          continue
        }

        await Bun.write(
          `${stockDir}/${day}.json`,
          JSON.stringify({ symbol, security_id, date: day, candles }, null, 2)
        )
        stockFiles++
        totalFiles++
      }

      // Adaptive throttle on success
      batchDelay = Math.max(DELAY_MIN, batchDelay + DELAY_ON_SUCCESS)

      // Save quota periodically
      if (reqCount % 50 < CONCURRENT_STOCKS) saveQuota(reqCount)
    }

    return { symbol, stockFiles, stockSkipped, stockEmpty, stockErrors, isInvalid, reqs }
  }))

  // Advance token index
  tokenIdx = (tokenIdx + stockBatch.length) % TOKENS.length

  // Log results
  for (let i = 0; i < batchResults.length; i++) {
    const r = batchResults[i]
    totalSkipped += r.stockSkipped
    const globalIdx = si + i + 1
    const elapsed = ((Date.now() - startTime) / 1000 / 60).toFixed(1)
    const pct = (globalIdx / stocks.length * 100).toFixed(1)

    // Only log if there was actual work or every 100th stock
    if (r.reqs > 0 || globalIdx % 100 === 0) {
      const status = r.isInvalid
        ? "INVALID"
        : r.reqs === 0
        ? `all ${tradingDays.length} days cached`
        : `ok=${r.stockFiles} skip=${r.stockSkipped} empty=${r.stockEmpty} err=${r.stockErrors} (${r.reqs} reqs)`
      console.log(
        `[${globalIdx}/${stocks.length}] (${pct}%) ${r.symbol.padEnd(20)} ` +
        `${status}  total_reqs=${reqCount.toLocaleString()}  delay=${batchDelay}ms  ${elapsed}m`
      )
    }
  }

  if (quotaHit) {
    console.log(`\n⚠️  Daily quota (${reqCount.toLocaleString()}). Re-run tomorrow.`)
  }
}

saveQuota(reqCount)

const totalMin = ((Date.now() - startTime) / 1000 / 60).toFixed(1)
console.log(`\n--- Summary (${totalMin}m) ---`)
console.log(`Files written  : ${totalFiles.toLocaleString()}`)
console.log(`Skipped        : ${totalSkipped.toLocaleString()}`)
console.log(`Empty (no data): ${totalEmpty.toLocaleString()}`)
console.log(`Errors         : ${totalErrors.toLocaleString()}`)
console.log(`API requests   : ${reqCount.toLocaleString()} / ${DAILY_QUOTA.toLocaleString()}`)
console.log(`Invalid stocks : ${invalidStocks.size}`)
if (invalidStocks.size > 0) {
  console.log(`  ${[...invalidStocks].slice(0, 30).join(", ")}${invalidStocks.size > 30 ? "..." : ""}`)
}
if (quotaHit) {
  console.log(`\n👉 Re-run same command tomorrow to continue.`)
}
