#!/usr/bin/env node
/**
 * Fix daily_ref using Dhan Historical API (actual exchange settlement closes).
 * Usage: node deploy/fix-daily-ref-from-dhan.js [fromDate] [toDate]
 *
 * Dhan Data API: 5 req/sec, 100K/day
 * 1 request per symbol covers entire date range → ~1062 requests total
 */

const CH = process.env.CLICKHOUSE_URL || 'http://localhost:8123';
const TOKEN = process.env.DHAN_TOKEN || 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0ODczNzQ2LCJpYXQiOjE3NzQ3ODczNDYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.VC7PSPXWqqn32ZPBFugPzdyjBQGgQZ4c_VUIArOoSr-9qK0qDVdLbs_PlUB0C7DComisPUq46YPTCWZNo3Bcdg';
const CID = process.env.DHAN_CLIENT_ID || '1100896497';
const FROM = process.argv[2] || '2025-12-01';
const TO = process.argv[3] || '2026-03-30';
const CONCURRENT = 4;
const BATCH = 5000;

async function chq(sql) {
  const r = await fetch(CH, { method: 'POST', body: sql });
  if (!r.ok) throw new Error(`CH: ${(await r.text()).slice(0, 200)}`);
  return r.text();
}

async function dhanDaily(secId) {
  for (let retry = 0; retry < 3; retry++) {
    try {
      const r = await fetch('https://api.dhan.co/v2/charts/historical', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'access-token': TOKEN, 'client-id': CID },
        body: JSON.stringify({ securityId: secId, exchangeSegment: 'NSE_EQ', instrument: 'EQUITY', expiryCode: 0, fromDate: FROM, toDate: TO }),
      });
      if (r.status === 429) { await new Promise(r => setTimeout(r, 3000 * (retry + 1))); continue; }
      if (!r.ok) return null;
      return await r.json();
    } catch { await new Promise(r => setTimeout(r, 2000)); }
  }
  return null;
}

async function main() {
  console.log('='.repeat(80));
  console.log(`  FIX DAILY_REF — Dhan Historical API settlement closes`);
  console.log(`  ${FROM} to ${TO}`);
  console.log('='.repeat(80));

  // Get all enabled symbols
  const raw = await chq('SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled=1 ORDER BY symbol FORMAT TabSeparated');
  const syms = raw.trim().split('\n').map(l => { const [id, sym] = l.split('\t'); return { id, sym }; });
  console.log(`Symbols: ${syms.length}`);

  const t0 = Date.now();
  let downloaded = 0, failed = 0, totalDays = 0;
  let refBatch = [], refInserted = 0;

  // Per-symbol: collect daily OHLC from Dhan, then build daily_ref
  // Track prev close across days per symbol
  const allData = new Map(); // "date|symbol" -> { secId, open, close, high, low }

  async function flushRef() {
    if (!refBatch.length) return;
    await chq(`INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES ${refBatch.join(',')}`);
    refInserted += refBatch.length;
    refBatch = [];
  }

  for (let i = 0; i < syms.length; i += CONCURRENT) {
    const batch = syms.slice(i, i + CONCURRENT);
    const results = await Promise.all(batch.map(async ({ id, sym }) => {
      const data = await dhanDaily(id);
      if (!data || !data.open || data.open.length === 0) return { sym, id, days: [] };

      const days = [];
      for (let j = 0; j < data.open.length; j++) {
        const ts = data.timestamp[j];
        // Dhan historical timestamps are IST midnight as unix seconds
        const dt = new Date(ts * 1000);
        // Handle timezone: add 5.5h to get IST date
        const istDate = new Date(dt.getTime() + 5.5 * 3600 * 1000);
        const dateStr = istDate.toISOString().split('T')[0];
        days.push({
          date: dateStr,
          open: data.open[j],
          high: data.high[j],
          low: data.low[j],
          close: data.close[j],
        });
      }
      return { sym, id, days };
    }));

    for (const { sym, id, days } of results) {
      if (days.length === 0) { failed++; continue; }
      downloaded++;
      totalDays += days.length;

      // Sort by date
      days.sort((a, b) => a.date.localeCompare(b.date));

      let prevClose = 0, prevHigh = 0, prevLow = 0;
      for (const d of days) {
        const gap = prevClose > 0 ? Math.round((d.open - prevClose) / prevClose * 10000) / 100 : 0;

        refBatch.push(`('${d.date}','${sym}','${id}',${prevClose},${d.open},${d.open},${gap},${prevHigh},${prevLow},${d.close})`);

        prevClose = d.close;
        prevHigh = d.high;
        prevLow = d.low;
      }

      if (refBatch.length >= BATCH) await flushRef();
    }

    await new Promise(r => setTimeout(r, 1000)); // rate limit: ~4 req/sec

    const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
    const pct = Math.min(100, ((i + CONCURRENT) / syms.length * 100)).toFixed(0);
    process.stdout.write(`  ${downloaded}/${syms.length} (${pct}%) | ${totalDays} days | ${refInserted} inserted | ${failed} failed | ${elapsed}s\r`);
  }

  await flushRef();

  const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
  console.log(`\n  Done: ${downloaded} symbols, ${totalDays} days, ${refInserted} rows in ${elapsed}s`);

  // Verify
  console.log('\nVerification:');
  const v1 = await chq(`SELECT count(), countIf(prev_close>0), countIf(abs(gap_pct)>0.001), min(trading_date), max(trading_date) FROM trading.daily_ref FINAL WHERE trading_date>=toDate('${FROM}') AND trading_date<=toDate('${TO}') FORMAT TabSeparated`);
  console.log(`  daily_ref: ${v1.trim()}`);

  // Spot check
  const spot = await chq(`SELECT symbol, round(prev_close,2), round(day_open,2), round(gap_pct,2), round(closing_price,2) FROM trading.daily_ref FINAL WHERE trading_date='2026-01-06' AND symbol IN ('HDFCBANK','RELIANCE') FORMAT TabSeparated`);
  console.log('\n  Spot check (Jan 6):');
  for (const l of spot.trim().split('\n')) {
    const [sym, pc, o, g, c] = l.split('\t');
    console.log(`    ${sym}: prev_close=${pc} open=${o} gap=${g}% close=${c}`);
  }

  console.log('\n' + '='.repeat(80));
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
