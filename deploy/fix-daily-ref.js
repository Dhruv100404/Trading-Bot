#!/usr/bin/env node
/**
 * Fix daily_ref prev_close using snapshot data (bucket 375 = 3:30 PM close).
 * Re-inserts all rows with correct prev_close, day_open (bucket 1), gap_pct, closing_price.
 * ReplacingMergeTree deduplicates on FINAL reads — no need to truncate.
 */
const CH = process.env.CLICKHOUSE_URL || 'http://localhost:8123';

async function query(sql) {
  const r = await fetch(CH, { method: 'POST', body: sql });
  if (!r.ok) throw new Error(`CH error: ${await r.text()}`);
  return r.text();
}

async function main() {
  console.log('='.repeat(80));
  console.log('  FIX DAILY_REF — recompute prev_close from snapshot bucket 375');
  console.log('='.repeat(80));

  // 1. Get all trading dates from snapshots
  const datesRaw = await query(`
    SELECT DISTINCT trading_date FROM trading.snapshots ORDER BY trading_date FORMAT TabSeparated
  `);
  const dates = datesRaw.trim().split('\n').filter(Boolean);
  console.log(`Found ${dates.length} trading dates: ${dates[0]} to ${dates[dates.length - 1]}`);

  // 2. For each date, compute day_open (bucket 1) and closing_price (last bucket)
  //    Then pair with previous date's closing_price for prev_close and gap_pct
  console.log('Computing per-date open/close from snapshots...');
  const t0 = Date.now();

  // Single query: get open + close for ALL dates at once
  const ocRaw = await query(`
    SELECT
      toString(trading_date) as dt,
      symbol,
      security_id,
      argMin(ltp, bucket) as day_open,
      argMax(ltp, bucket) as closing_price,
      max(candle_high) as day_high,
      min(candle_low) as day_low
    FROM trading.snapshots
    WHERE bucket >= 1
    GROUP BY trading_date, symbol, security_id
    ORDER BY trading_date, symbol
    FORMAT TabSeparated
  `);

  const lines = ocRaw.trim().split('\n').filter(Boolean);
  console.log(`  ${lines.length} (date, symbol) pairs in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  // Build map: date|symbol -> { day_open, closing_price, security_id, day_high, day_low }
  const data = new Map();
  const dateSymbols = new Map(); // date -> [symbols]
  for (const line of lines) {
    const [dt, sym, secId, dopen, close, hi, lo] = line.split('\t');
    const key = `${dt}|${sym}`;
    data.set(key, {
      dt, sym, secId,
      day_open: parseFloat(dopen),
      closing_price: parseFloat(close),
      day_high: parseFloat(hi),
      day_low: parseFloat(lo),
    });
    if (!dateSymbols.has(dt)) dateSymbols.set(dt, []);
    dateSymbols.get(dt).push(sym);
  }

  // 3. Build daily_ref rows: prev_close = previous trading date's closing_price
  console.log('Building daily_ref rows with correct prev_close...');
  const sortedDates = [...dateSymbols.keys()].sort();
  let totalRows = 0;
  let batchValues = [];
  const BATCH_SIZE = 5000;
  let inserted = 0;

  for (let i = 0; i < sortedDates.length; i++) {
    const dt = sortedDates[i];
    const prevDt = i > 0 ? sortedDates[i - 1] : null;
    const symbols = dateSymbols.get(dt);

    for (const sym of symbols) {
      const cur = data.get(`${dt}|${sym}`);
      if (!cur) continue;

      let prev_close = 0;
      let prev_high = 0;
      let prev_low = 0;
      if (prevDt) {
        const prev = data.get(`${prevDt}|${sym}`);
        if (prev) {
          prev_close = prev.closing_price;
          prev_high = prev.day_high;
          prev_low = prev.day_low;
        }
      }

      const gap_pct = prev_close > 0
        ? Math.round((cur.day_open - prev_close) / prev_close * 10000) / 100
        : 0;

      batchValues.push(
        `('${dt}','${sym}','${cur.secId}',${prev_close},${cur.day_open},${cur.day_open},${gap_pct},${prev_high},${prev_low},${cur.closing_price})`
      );
      totalRows++;

      if (batchValues.length >= BATCH_SIZE) {
        await query(`INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES ${batchValues.join(',')}`);
        inserted += batchValues.length;
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
        process.stdout.write(`  ${inserted}/${totalRows} inserted (${elapsed}s)\r`);
        batchValues = [];
      }
    }
  }

  // Flush remaining
  if (batchValues.length > 0) {
    await query(`INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES ${batchValues.join(',')}`);
    inserted += batchValues.length;
  }

  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`\n  ✅ Inserted ${inserted} rows in ${elapsed}s`);

  // 4. Verify
  console.log('\nVerification:');
  const verify = await query(`
    SELECT
      count() as total,
      countIf(prev_close > 0) as has_pc,
      countIf(abs(gap_pct) > 0.001) as has_gap,
      min(trading_date) as from_dt,
      max(trading_date) as to_dt,
      uniqExact(trading_date) as dates
    FROM trading.daily_ref FINAL
    FORMAT TabSeparated
  `);
  const [total, hasPC, hasGap, fromDt, toDt, numDates] = verify.trim().split('\t');
  console.log(`  Total: ${total} | prev_close>0: ${hasPC} | has_gap: ${hasGap} | ${fromDt} to ${toDt} (${numDates} dates)`);

  // Spot check HDFCBANK Jan 5->6
  const spot = await query(`
    SELECT symbol, round(prev_close,2), round(day_open,2), round(gap_pct,2), round(closing_price,2)
    FROM trading.daily_ref FINAL
    WHERE trading_date = '2026-01-06' AND symbol IN ('HDFCBANK','RELIANCE')
    FORMAT TabSeparated
  `);
  console.log('\n  Spot check (Jan 6):');
  for (const line of spot.trim().split('\n')) {
    const [sym, pc, dopen, gap, close] = line.split('\t');
    console.log(`    ${sym}: prev_close=${pc} day_open=${dopen} gap=${gap}% closing=${close}`);
  }

  console.log('\n' + '='.repeat(80));
  console.log('  DONE');
  console.log('='.repeat(80));
}

main().catch(e => { console.error('FATAL:', e); process.exit(1); });
