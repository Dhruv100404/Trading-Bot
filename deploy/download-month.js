#!/usr/bin/env node
/**
 * Download intraday data from Dhan API for a full month and insert into ClickHouse.
 * Usage: node deploy/download-month.js 2024-08 [--all]
 *   --all = all watchlist stocks (default = enabled only)
 *
 * Dhan Data API limits: 5 req/sec, 100K/day
 * Strategy: fetch 5 symbols in parallel, 1 request per symbol covers full month
 */

const CH = process.env.CLICKHOUSE_URL || 'http://localhost:8123';
const TOKEN = process.env.DHAN_TOKEN || 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0ODczNzQ2LCJpYXQiOjE3NzQ3ODczNDYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.VC7PSPXWqqn32ZPBFugPzdyjBQGgQZ4c_VUIArOoSr-9qK0qDVdLbs_PlUB0C7DComisPUq46YPTCWZNo3Bcdg';
const CLIENT_ID = process.env.DHAN_CLIENT_ID || '1100896497';
const CONCURRENT = 4;       // parallel requests (stay under 5/sec)
const SNAP_BATCH = 50000;   // rows per INSERT (large = fewer merges)
const REF_BATCH = 5000;

async function chQuery(sql) {
  const r = await fetch(CH, { method: 'POST', body: sql });
  if (!r.ok) throw new Error(`CH: ${await r.text()}`);
  return r.text();
}

async function dhanIntraday(secId, from, to) {
  const r = await fetch('https://api.dhan.co/v2/charts/intraday', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'access-token': TOKEN, 'client-id': CLIENT_ID },
    body: JSON.stringify({ securityId: secId, exchangeSegment: 'NSE_EQ', instrument: 'EQUITY', expiryCode: 0, fromDate: from, toDate: to }),
  });
  if (r.status === 429) return null; // rate limited
  if (!r.ok) return null;
  return r.json();
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Convert unix timestamp to IST date string and bucket
function tsToBucket(ts) {
  // IST = UTC + 5:30
  const istMs = (ts * 1000) + (5.5 * 3600 * 1000);
  const d = new Date(istMs);
  const h = d.getUTCHours(), m = d.getUTCMinutes();
  const totalMin = h * 60 + m;
  const openMin = 9 * 60 + 15; // 9:15
  const bucket = totalMin - openMin + 1;
  const dateStr = d.toISOString().split('T')[0];
  return { dateStr, bucket, h, m };
}

async function main() {
  const monthArg = process.argv[2] || '2024-08';
  const useAll = process.argv.includes('--all');

  const [year, month] = monthArg.split('-').map(Number);
  const fromDate = `${year}-${String(month).padStart(2,'0')}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const toDate = `${year}-${String(month).padStart(2,'0')}-${lastDay}`;

  console.log('='.repeat(80));
  console.log(`  DOWNLOAD ${fromDate} to ${toDate} from Dhan → ClickHouse`);
  console.log('='.repeat(80));

  // 1. Get symbols
  const filter = useAll ? '' : 'WHERE enabled=1';
  const symRaw = await chQuery(`SELECT security_id, symbol FROM trading.watchlist FINAL ${filter} ORDER BY symbol FORMAT TabSeparated`);
  const symbols = symRaw.trim().split('\n').filter(Boolean).map(l => {
    const [secId, sym] = l.split('\t');
    return { secId, sym };
  });
  console.log(`Symbols: ${symbols.length} (${useAll ? 'all' : 'enabled only'})`);

  // 2. Check existing data
  const existing = await chQuery(`SELECT uniqExact(symbol) FROM trading.snapshots WHERE trading_date >= '${fromDate}' AND trading_date <= '${toDate}' FORMAT TabSeparated`);
  console.log(`Existing snapshot symbols for this period: ${existing.trim()}`);

  const t0 = Date.now();
  let downloaded = 0, failed = 0, totalCandles = 0;
  let snapBatch = [], refBatch = [];
  let snapInserted = 0, refInserted = 0;
  // Track daily data per symbol for daily_ref
  const dailyData = new Map(); // "date|symbol" -> { open, close, high, low, secId }

  async function flushSnaps() {
    if (snapBatch.length === 0) return;
    const sql = `INSERT INTO trading.snapshots (trading_date,symbol,security_id,bucket,ltp,candle_open,candle_high,candle_low,volume_cum,volume_delta,vwap,volume_rate,candle_body_ratio) VALUES ${snapBatch.join(',')}`;
    await chQuery(sql);
    snapInserted += snapBatch.length;
    snapBatch = [];
  }

  async function flushRefs() {
    if (refBatch.length === 0) return;
    const sql = `INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES ${refBatch.join(',')}`;
    await chQuery(sql);
    refInserted += refBatch.length;
    refBatch = [];
  }

  // 3. Download in parallel batches
  for (let i = 0; i < symbols.length; i += CONCURRENT) {
    const batch = symbols.slice(i, i + CONCURRENT);
    const results = await Promise.all(batch.map(async ({ secId, sym }) => {
      for (let retry = 0; retry < 3; retry++) {
        const data = await dhanIntraday(secId, fromDate, toDate);
        if (data && data.open && data.open.length > 0) return { secId, sym, data };
        if (data === null) await sleep(2000); // rate limited, wait
      }
      return { secId, sym, data: null };
    }));

    for (const { secId, sym, data } of results) {
      if (!data || !data.open) { failed++; continue; }
      downloaded++;
      totalCandles += data.open.length;

      // Process candles
      for (let j = 0; j < data.open.length; j++) {
        const ts = data.timestamp[j];
        const { dateStr, bucket } = tsToBucket(ts);
        if (bucket < 0 || bucket > 400) continue; // skip invalid

        const o = data.open[j], h = data.high[j], l = data.low[j], c = data.close[j];
        const v = data.volume ? data.volume[j] : 0;
        const vDelta = v; // per-minute volume = delta
        const bodyRatio = (h - l) > 0 ? Math.abs(c - o) / (h - l) : 0;

        snapBatch.push(`('${dateStr}','${sym}','${secId}',${bucket},${c},${o},${h},${l},${v},${vDelta},0,0,${bodyRatio.toFixed(4)})`);

        // Track daily OHLC
        const key = `${dateStr}|${sym}`;
        if (!dailyData.has(key)) {
          dailyData.set(key, { secId, open: o, close: c, high: h, low: l, firstBucket: bucket });
        } else {
          const dd = dailyData.get(key);
          if (bucket < dd.firstBucket) { dd.open = o; dd.firstBucket = bucket; }
          dd.close = c; // last candle's close
          dd.high = Math.max(dd.high, h);
          dd.low = Math.min(dd.low, l);
        }
      }

      if (snapBatch.length >= SNAP_BATCH) await flushSnaps();
    }

    // Rate limit: ~1 batch per second (4 concurrent = 4 req/sec, under 5/sec limit)
    await sleep(1000);

    const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
    const pct = ((i + CONCURRENT) / symbols.length * 100).toFixed(0);
    process.stdout.write(`  ${downloaded}/${symbols.length} (${pct}%) | ${totalCandles} candles | ${snapInserted} snaps | ${failed} failed | ${elapsed}s\r`);
  }

  // Flush remaining snapshots
  await flushSnaps();
  console.log(`\n  Download done: ${downloaded} symbols, ${totalCandles} candles, ${snapInserted} snapshots in ${((Date.now()-t0)/1000).toFixed(0)}s`);

  // 4. Build daily_ref from tracked data
  console.log(`\n  Building daily_ref from ${dailyData.size} (date,symbol) pairs...`);
  const sortedKeys = [...dailyData.keys()].sort();

  // Track prev day close per symbol
  const prevClose = new Map(); // symbol -> close

  for (const key of sortedKeys) {
    const [date, sym] = key.split('|');
    const dd = dailyData.get(key);
    const pc = prevClose.get(sym) || 0;
    const gap = pc > 0 ? Math.round((dd.open - pc) / pc * 10000) / 100 : 0;
    const prevHigh = 0, prevLow = 0; // not tracked across days here

    refBatch.push(`('${date}','${sym}','${dd.secId}',${pc},${dd.open},${dd.open},${gap},${prevHigh},${prevLow},${dd.close})`);
    prevClose.set(sym, dd.close);

    if (refBatch.length >= REF_BATCH) await flushRefs();
  }
  await flushRefs();
  console.log(`  daily_ref: ${refInserted} rows inserted`);

  // 5. Verify
  console.log('\n  Verification:');
  const snapCount = await chQuery(`SELECT count(), uniqExact(symbol), uniqExact(trading_date) FROM trading.snapshots WHERE trading_date >= '${fromDate}' AND trading_date <= '${toDate}' FORMAT TabSeparated`);
  console.log(`  Snapshots: ${snapCount.trim()}`);
  const refCount = await chQuery(`SELECT count(), uniqExact(symbol), uniqExact(trading_date) FROM trading.daily_ref FINAL WHERE trading_date >= '${fromDate}' AND trading_date <= '${toDate}' FORMAT TabSeparated`);
  console.log(`  Daily_ref: ${refCount.trim()}`);

  console.log('\n' + '='.repeat(80));
  console.log(`  DONE in ${((Date.now()-t0)/1000).toFixed(0)}s`);
  console.log('='.repeat(80));
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
