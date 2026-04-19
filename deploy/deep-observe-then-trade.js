#!/usr/bin/env node
// OBSERVE-THEN-TRADE: Watch for N minutes, aggregate signals, then execute top picks
// No future data. All observation from bucket 1-N, execution from bucket N+1 onwards.

const CH = process.env.CH_URL || 'http://localhost:8123'
const FROM = '2025-12-01'
const TO = '2026-03-28'

async function q(sql) {
  const r = await fetch(CH, { method: 'POST', body: sql })
  return r.text()
}
async function qj(sql) {
  const txt = await q(sql + ' FORMAT JSONEachRow')
  return txt.trim().split('\n').filter(Boolean).map(l => JSON.parse(l))
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 1: Build observation matrix with rich features from bucket 1-N
// ═══════════════════════════════════════════════════════════════════════════════

async function buildMatrix(obsEnd) {
  const sql = `
    WITH
      obs AS (
        SELECT trading_date, symbol,
          -- Price action
          argMin(ltp, bucket) as open_ltp,
          argMax(ltp, bucket) as obs_end_ltp,
          min(candle_low) as obs_low,
          max(candle_high) as obs_high,
          -- Volume profile
          sum(volume_delta) as obs_vol,
          avg(volume_rate) as avg_vr,
          max(volume_rate) as max_vr,
          -- Volume acceleration (early vs late in observation)
          sumIf(volume_delta, bucket <= ${Math.ceil(obsEnd/2)}) as vol_first_half,
          sumIf(volume_delta, bucket > ${Math.ceil(obsEnd/2)}) as vol_second_half,
          -- Candle quality
          avg(candle_body_ratio) as avg_body,
          -- VWAP
          avg(vwap) as obs_vwap,
          -- Bucket-level momentum: is price consistently moving one direction?
          -- Count how many buckets closed higher than previous
          count() as obs_bars,
          -- Price at specific observation points
          argMinIf(ltp, bucket, bucket >= 1) as ltp_b1,
          argMinIf(ltp, bucket, bucket >= 2) as ltp_b2,
          argMinIf(ltp, bucket, bucket >= 3) as ltp_b3,
          argMinIf(ltp, bucket, bucket >= ${obsEnd}) as ltp_end,
          -- Volume at specific points
          sumIf(volume_delta, bucket = 1) as vol_b1,
          sumIf(volume_delta, bucket = 2) as vol_b2,
          sumIf(volume_delta, bucket = 3) as vol_b3
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= ${obsEnd}
        GROUP BY trading_date, symbol
        HAVING obs_bars >= ${Math.max(2, obsEnd - 1)} AND open_ltp > 0 AND obs_end_ltp > 0
      ),
      -- Post-observation outcomes at various exit windows
      post_short AS (
        SELECT trading_date, symbol,
          max(candle_high) as ph, min(candle_low) as pl, argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket > ${obsEnd} AND bucket <= ${obsEnd + 20}
        GROUP BY trading_date, symbol
      ),
      post_med AS (
        SELECT trading_date, symbol,
          max(candle_high) as ph, min(candle_low) as pl, argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket > ${obsEnd} AND bucket <= ${obsEnd + 40}
        GROUP BY trading_date, symbol
      ),
      post_long AS (
        SELECT trading_date, symbol,
          max(candle_high) as ph, min(candle_low) as pl, argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket > ${obsEnd} AND bucket <= ${obsEnd + 65}
        GROUP BY trading_date, symbol
      ),
      gaps AS (
        SELECT toString(t.trading_date) as td, t.symbol as sym,
          toFloat32(if(p.dc > 0, (t.do - p.dc) / p.dc * 100, 0)) as gap_pct
        FROM (
          SELECT trading_date, symbol, argMin(ltp, bucket) as do
          FROM trading.snapshots WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          GROUP BY trading_date, symbol
        ) t
        ASOF LEFT JOIN (
          SELECT trading_date, symbol, argMax(ltp, bucket) as dc
          FROM trading.snapshots WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          GROUP BY trading_date, symbol
        ) p ON t.symbol = p.symbol AND t.trading_date > p.trading_date
      ),
      prev_day AS (
        SELECT trading_date, symbol,
          if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev_dir,
          (argMax(ltp, bucket) - argMin(ltp, bucket)) / argMin(ltp, bucket) * 100 as prev_range
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 80
        GROUP BY trading_date, symbol
      ),
      prev2 AS (
        SELECT trading_date, symbol,
          if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev2_dir
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') - 15 AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 80
        GROUP BY trading_date, symbol
      )
    SELECT
      toString(o.trading_date) as trading_date, o.symbol,
      o.obs_end_ltp as entry_price,
      -- Observation features (ALL known at entry time)
      (o.obs_end_ltp - o.open_ltp) / o.open_ltp * 100 as move_pct,
      (o.obs_high - o.obs_low) / o.open_ltp * 100 as range_pct,
      o.obs_vol, o.avg_vr, o.max_vr, o.avg_body,
      if(o.obs_vwap > 0, (o.obs_end_ltp - o.obs_vwap) / o.obs_vwap * 100, 0) as vwap_dist,
      -- Volume acceleration
      if(o.vol_first_half > 0, o.vol_second_half / o.vol_first_half, 0) as vol_accel,
      -- Price consistency: did price move steadily or whipsaw?
      -- Measured as: net move / total range (1.0 = perfectly consistent, 0 = whipsaw)
      if(o.obs_high > o.obs_low, abs(o.obs_end_ltp - o.open_ltp) / (o.obs_high - o.obs_low), 0) as price_consistency,
      -- Trend strength: move per unit of volume
      if(o.obs_vol > 0, abs(o.obs_end_ltp - o.open_ltp) / o.open_ltp * 100 / (o.obs_vol / 10000.0), 0) as move_per_vol,
      -- Gap and multi-day
      g.gap_pct,
      pd.prev_dir, pd.prev_range,
      p2.prev2_dir,
      -- Direction
      if(o.obs_end_ltp > o.open_ltp, 'BUY', 'SELL') as direction,
      -- Outcomes (NOT used for selection, only for evaluation)
      -- SELL outcomes (inverted: entry - post for profit)
      -- BUY outcomes
      if(o.obs_end_ltp > o.open_ltp, (ps.ph - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - ps.pl) / o.obs_end_ltp * 100) as mfe_s,
      if(o.obs_end_ltp > o.open_ltp, (o.obs_end_ltp - ps.pl) / o.obs_end_ltp * 100, (ps.ph - o.obs_end_ltp) / o.obs_end_ltp * 100) as mae_s,
      if(o.obs_end_ltp > o.open_ltp, (ps.last_ltp - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - ps.last_ltp) / o.obs_end_ltp * 100) as ret_s,
      if(o.obs_end_ltp > o.open_ltp, (pm.ph - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - pm.pl) / o.obs_end_ltp * 100) as mfe_m,
      if(o.obs_end_ltp > o.open_ltp, (o.obs_end_ltp - pm.pl) / o.obs_end_ltp * 100, (pm.ph - o.obs_end_ltp) / o.obs_end_ltp * 100) as mae_m,
      if(o.obs_end_ltp > o.open_ltp, (pm.last_ltp - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - pm.last_ltp) / o.obs_end_ltp * 100) as ret_m,
      if(o.obs_end_ltp > o.open_ltp, (pl.ph - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - pl.pl) / o.obs_end_ltp * 100) as mfe_l,
      if(o.obs_end_ltp > o.open_ltp, (o.obs_end_ltp - pl.pl) / o.obs_end_ltp * 100, (pl.ph - o.obs_end_ltp) / o.obs_end_ltp * 100) as mae_l,
      if(o.obs_end_ltp > o.open_ltp, (pl.last_ltp - o.obs_end_ltp) / o.obs_end_ltp * 100, (o.obs_end_ltp - pl.last_ltp) / o.obs_end_ltp * 100) as ret_l
    FROM obs o
    JOIN post_short ps ON o.trading_date = ps.trading_date AND o.symbol = ps.symbol
    JOIN post_med pm ON o.trading_date = pm.trading_date AND o.symbol = pm.symbol
    JOIN post_long pl ON o.trading_date = pl.trading_date AND o.symbol = pl.symbol
    LEFT JOIN gaps g ON toString(o.trading_date) = g.td AND o.symbol = g.sym
    LEFT JOIN prev_day pd ON o.trading_date - 1 = pd.trading_date AND o.symbol = pd.symbol
    LEFT JOIN prev2 p2 ON o.trading_date - 2 = p2.trading_date AND o.symbol = p2.symbol
    WHERE abs((o.obs_end_ltp - o.open_ltp) / o.open_ltp * 100) >= 0.05
  `
  return qj(sql)
}

// ═══════════════════════════════════════════════════════════════════════════════
// SIMULATION
// ═══════════════════════════════════════════════════════════════════════════════

function simulate(candidates, tp, sl, maxPos, exitKey) {
  const byDate = {}
  for (const c of candidates) {
    if (!byDate[c.trading_date]) byDate[c.trading_date] = []
    byDate[c.trading_date].push(c)
  }
  const dates = Object.keys(byDate).sort()
  let totalPnl = 0, sigs = 0, wins = 0, losses = 0
  const rocs = []
  let green = 0

  const mK = 'mfe_' + exitKey, aK = 'mae_' + exitKey, rK = 'ret_' + exitKey

  for (const dt of dates) {
    const sel = byDate[dt].slice(0, maxPos)
    let dayPnl = 0
    for (const s of sel) {
      sigs++
      const qty = Math.floor(100000 / s.entry_price)
      if (qty <= 0) continue
      const mfe = s[mK] || 0, mae = s[aK] || 0, tret = s[rK] || 0
      let ret
      if (tp > 0 && sl > 0 && mfe >= tp && mae >= sl) {
        ret = (mfe / Math.max(mae, 0.01)) > (tp / sl) ? tp : -sl
      } else if (tp > 0 && mfe >= tp) { ret = tp }
      else if (sl > 0 && mae >= sl) { ret = -sl }
      else { ret = tret }
      dayPnl += s.entry_price * (ret / 100) * qty
      if (ret > 0.05) wins++; else losses++
    }
    const cap = sel.reduce((a, c) => a + Math.floor(100000 / c.entry_price) * c.entry_price, 0)
    const m = cap / 5
    rocs.push(m > 0 ? (dayPnl / m) * 100 : 0)
    if (dayPnl > 0) green++
    totalPnl += dayPnl
  }
  const avgRoc = rocs.length > 0 ? rocs.reduce((a, b) => a + b, 0) / rocs.length : 0
  const wr = (wins + losses) > 0 ? wins / (wins + losses) * 100 : 0
  return { sigs, wr, avgRoc, totalPnl, green, days: dates.length, greenPct: dates.length > 0 ? green / dates.length * 100 : 0 }
}

// ═══════════════════════════════════════════════════════════════════════════════
// FEATURE IMPORTANCE
// ═══════════════════════════════════════════════════════════════════════════════

function featureImportance(rows, retKey) {
  const features = [
    { name: 'abs_move', fn: r => Math.abs(r.move_pct || 0) },
    { name: 'move_pct', fn: r => r.move_pct || 0 },
    { name: 'range_pct', fn: r => r.range_pct || 0 },
    { name: 'max_vr', fn: r => r.max_vr || 0 },
    { name: 'avg_vr', fn: r => r.avg_vr || 0 },
    { name: 'obs_vol', fn: r => r.obs_vol || 0 },
    { name: 'avg_body', fn: r => r.avg_body || 0 },
    { name: 'vwap_dist', fn: r => r.vwap_dist || 0 },
    { name: 'abs_vwap', fn: r => Math.abs(r.vwap_dist || 0) },
    { name: 'vol_accel', fn: r => r.vol_accel || 0 },
    { name: 'price_consistency', fn: r => r.price_consistency || 0 },
    { name: 'move_per_vol', fn: r => r.move_per_vol || 0 },
    { name: 'abs_gap', fn: r => Math.abs(r.gap_pct || 0) },
    { name: 'gap_pct', fn: r => r.gap_pct || 0 },
    { name: 'prev_range', fn: r => r.prev_range || 0 },
    // Binary
    { name: 'gap_dn(<-0.5)', fn: r => (r.gap_pct || 0) < -0.5 ? 1 : 0 },
    { name: 'gap_up(>0.5)', fn: r => (r.gap_pct || 0) > 0.5 ? 1 : 0 },
    { name: 'prev_dn', fn: r => (r.prev_dir || 0) < 0 ? 1 : 0 },
    { name: 'prev_up', fn: r => (r.prev_dir || 0) > 0 ? 1 : 0 },
    { name: '2day_dn', fn: r => (r.prev_dir || 0) < 0 && (r.prev2_dir || 0) < 0 ? 1 : 0 },
    { name: '2day_up', fn: r => (r.prev_dir || 0) > 0 && (r.prev2_dir || 0) > 0 ? 1 : 0 },
    { name: 'vol_accel>1.5', fn: r => (r.vol_accel || 0) > 1.5 ? 1 : 0 },
    { name: 'consistency>0.7', fn: r => (r.price_consistency || 0) > 0.7 ? 1 : 0 },
  ]

  const results = []
  for (const f of features) {
    const vals = rows.map(r => f.fn(r)).filter(v => typeof v === 'number' && !isNaN(v)).sort((a, b) => a - b)
    for (const pct of [0.5, 0.75, 0.9]) {
      const th = vals[Math.floor(vals.length * pct)]
      if (th === undefined || typeof th !== 'number' || th === 0) continue
      const hi = rows.filter(r => f.fn(r) >= th), lo = rows.filter(r => f.fn(r) < th)
      if (hi.length < 50 || lo.length < 50) continue
      const hR = hi.reduce((s, r) => s + (r[retKey] || 0), 0) / hi.length
      const lR = lo.reduce((s, r) => s + (r[retKey] || 0), 0) / lo.length
      results.push({ name: f.name, th, hR, lR, lift: hR - lR, hN: hi.length, lN: lo.length })
    }
  }
  return results.sort((a, b) => Math.abs(b.lift) - Math.abs(a.lift))
}

// ═══════════════════════════════════════════════════════════════════════════════
// EXHAUSTIVE SEARCH
// ═══════════════════════════════════════════════════════════════════════════════

function exhaustiveSearch(allRows) {
  const results = []
  let tested = 0

  // Direction splits
  const dirs = [
    { name: 'SELL', fn: r => r.direction === 'SELL' },
    { name: 'BUY', fn: r => r.direction === 'BUY' },
    { name: 'BOTH', fn: () => true },
  ]

  // Filters
  const filters = [
    { name: 'all', fn: () => true },
    // Gap
    { name: 'gapDn<-1', fn: r => (r.gap_pct||0) < -1 },
    { name: 'gapUp>0.5', fn: r => (r.gap_pct||0) > 0.5 },
    { name: 'gapUp>1', fn: r => (r.gap_pct||0) > 1 },
    { name: 'bigGap>2', fn: r => Math.abs(r.gap_pct||0) > 2 },
    // Prev day
    { name: 'prevDn', fn: r => (r.prev_dir||0) < 0 },
    { name: 'prevUp', fn: r => (r.prev_dir||0) > 0 },
    { name: '2dayDn', fn: r => (r.prev_dir||0) < 0 && (r.prev2_dir||0) < 0 },
    { name: '2dayUp', fn: r => (r.prev_dir||0) > 0 && (r.prev2_dir||0) > 0 },
    // Volume
    { name: 'vol>200', fn: r => (r.max_vr||0) > 200 },
    { name: 'vol>500', fn: r => (r.max_vr||0) > 500 },
    // Price consistency (steady movers vs whipsaws)
    { name: 'steady>0.6', fn: r => (r.price_consistency||0) > 0.6 },
    { name: 'steady>0.8', fn: r => (r.price_consistency||0) > 0.8 },
    // Volume acceleration (institutional entry pattern)
    { name: 'volAccel>1.5', fn: r => (r.vol_accel||0) > 1.5 },
    { name: 'volAccel<0.7', fn: r => (r.vol_accel||0) < 0.7 && (r.vol_accel||0) > 0 },
    // Body quality
    { name: 'body>0.6', fn: r => (r.avg_body||0) > 0.6 },
    // Move size
    { name: 'mv>0.3', fn: r => Math.abs(r.move_pct||0) > 0.3 },
    { name: 'mv>0.5', fn: r => Math.abs(r.move_pct||0) > 0.5 },
    { name: 'mv<0.5', fn: r => Math.abs(r.move_pct||0) < 0.5 },
    // VWAP
    { name: 'vwapAligned', fn: r => (r.direction==='BUY'&&(r.vwap_dist||0)>0.1)||(r.direction==='SELL'&&(r.vwap_dist||0)<-0.1) },
  ]

  const rankers = [
    { name: 'volRate', fn: (a, b) => (b.max_vr||0) - (a.max_vr||0) },
    { name: 'absMove', fn: (a, b) => Math.abs(b.move_pct||0) - Math.abs(a.move_pct||0) },
    { name: 'gapSize', fn: (a, b) => Math.abs(b.gap_pct||0) - Math.abs(a.gap_pct||0) },
    { name: 'volXmove', fn: (a, b) => ((b.max_vr||0)*Math.abs(b.move_pct||0)) - ((a.max_vr||0)*Math.abs(a.move_pct||0)) },
    { name: 'consistency', fn: (a, b) => (b.price_consistency||0) - (a.price_consistency||0) },
    { name: 'movePerVol', fn: (a, b) => (b.move_per_vol||0) - (a.move_per_vol||0) },
    { name: 'entryVol', fn: (a, b) => (b.obs_vol||0) - (a.obs_vol||0) },
  ]

  const tpSl = [
    [0, 0], [0.5, 0.3], [0.7, 0.3], [0.7, 0.5], [1.0, 0.3], [1.0, 0.5], [1.0, 0.7],
    [1.5, 0.5], [1.5, 0.7], [1.5, 1.0], [2.0, 0.7], [2.0, 1.0],
  ]

  // Smart compound: direction × (gap filter + momentum filter + quality filter) × ranker × tp/sl × exit × positions
  const gapFilters = filters.filter(f => f.name.startsWith('gap') || f.name.startsWith('big') || f.name === 'all')
  const momFilters = filters.filter(f => f.name.startsWith('prev') || f.name.startsWith('2day') || f.name === 'all')
  const qualFilters = filters.filter(f => !f.name.startsWith('gap') && !f.name.startsWith('big') && !f.name.startsWith('prev') && !f.name.startsWith('2day'))

  for (const dir of dirs) {
    const dirRows = allRows.filter(dir.fn)
    if (dirRows.length < 50) continue

    for (const gf of gapFilters) {
      for (const mf of momFilters) {
        for (const qf of qualFilters) {
          const filtered = dirRows.filter(r => gf.fn(r) && mf.fn(r) && qf.fn(r))
          if (filtered.length < 30) continue

          for (const ranker of rankers) {
            const sorted = [...filtered].sort(ranker.fn)
            for (const [tp, sl] of tpSl) {
              for (const pos of [3, 5, 8]) {
                for (const ex of ['s', 'm', 'l']) {
                  tested++
                  if (tested % 10000 === 0) process.stderr.write(`  ${tested} tested, ${results.length} valid...\r`)
                  const r = simulate(sorted, tp, sl, pos, ex)
                  if (r.sigs < 30) continue
                  if (r.avgRoc > 0) { // only keep profitable
                    results.push({
                      label: `${dir.name} ${gf.name}+${mf.name}+${qf.name} rank=${ranker.name} TP=${tp} SL=${sl} pos=${pos} ex=${ex}`,
                      ...r
                    })
                  }
                }
              }
            }
          }
        }
      }
    }
  }
  return results
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════════

async function main() {
  const t0 = Date.now()
  console.log(`\n${'═'.repeat(100)}`)
  console.log(`  OBSERVE-THEN-TRADE DEEP ANALYSIS`)
  console.log(`  ${FROM} to ${TO} — No lookahead, observation then execution`)
  console.log(`${'═'.repeat(100)}`)

  // Test multiple observation windows
  const obsWindows = [5, 7, 10, 15]
  const allResults = []

  for (const obsEnd of obsWindows) {
    console.log(`\n${'█'.repeat(100)}`)
    console.log(`  OBSERVATION WINDOW: ${obsEnd} minutes (bucket 1-${obsEnd})`)
    console.log(`  Entry at bucket ${obsEnd + 1}, exits at bucket ${obsEnd+20}/${obsEnd+40}/${obsEnd+65}`)
    console.log(`${'█'.repeat(100)}`)

    const rows = await buildMatrix(obsEnd)
    const buyRows = rows.filter(r => r.direction === 'BUY')
    const sellRows = rows.filter(r => r.direction === 'SELL')
    console.log(`  ${rows.length} candidates (${buyRows.length} BUY, ${sellRows.length} SELL)`)

    // Feature importance for SELL
    console.log(`\n  ── SELL Feature Importance (obs=${obsEnd}min, exit=med) ──`)
    const sellFI = featureImportance(sellRows, 'ret_m')
    console.log(`  ${'Feature'.padEnd(22)} ${'Thresh'.padStart(8)} ${'HIGH ret'.padStart(9)} ${'LOW ret'.padStart(9)} ${'Lift'.padStart(8)}`)
    for (const f of sellFI.slice(0, 15)) {
      const m = f.lift > 0.02 ? ' ★★★' : f.lift > 0.01 ? ' ★★' : f.lift > 0 ? ' ★' : ''
      console.log(`  ${f.name.padEnd(22)} ${f.th.toFixed(2).padStart(8)} ${(f.hR>=0?'+':'')+f.hR.toFixed(3).padStart(8)} ${(f.lR>=0?'+':'')+f.lR.toFixed(3).padStart(8)} ${(f.lift>=0?'+':'')+f.lift.toFixed(3).padStart(7)}${m}`)
    }

    // Feature importance for BUY
    console.log(`\n  ── BUY Feature Importance (obs=${obsEnd}min, exit=med) ──`)
    const buyFI = featureImportance(buyRows, 'ret_m')
    console.log(`  ${'Feature'.padEnd(22)} ${'Thresh'.padStart(8)} ${'HIGH ret'.padStart(9)} ${'LOW ret'.padStart(9)} ${'Lift'.padStart(8)}`)
    for (const f of buyFI.slice(0, 15)) {
      const m = f.lift > 0.02 ? ' ★★★' : f.lift > 0.01 ? ' ★★' : f.lift > 0 ? ' ★' : ''
      console.log(`  ${f.name.padEnd(22)} ${f.th.toFixed(2).padStart(8)} ${(f.hR>=0?'+':'')+f.hR.toFixed(3).padStart(8)} ${(f.lR>=0?'+':'')+f.lR.toFixed(3).padStart(8)} ${(f.lift>=0?'+':'')+f.lift.toFixed(3).padStart(7)}${m}`)
    }

    // Exhaustive search
    console.log(`\n  ── Exhaustive search (obs=${obsEnd}min) ──`)
    const t1 = Date.now()
    const results = exhaustiveSearch(rows)
    results.sort((a, b) => b.avgRoc - a.avgRoc)
    console.log(`  ${results.length} valid combos in ${((Date.now()-t1)/1000).toFixed(0)}s`)

    // Keep only top 500 per window to avoid memory issues
    results.sort((a, b) => b.avgRoc - a.avgRoc)
    const topResults = results.slice(0, 500)
    for (const r of topResults) { r.obsWindow = obsEnd; r.label = `obs=${obsEnd} ${r.label}` }
    allResults.push(...topResults)

    // Show top 20 for this window
    console.log(`\n  TOP 20 for obs=${obsEnd}min:`)
    console.log(`  ${'#'.padStart(3)} ${'Label'.padEnd(90)} Sigs Win%  AvgROC  Grn/Tot`)
    for (let i = 0; i < Math.min(20, results.length); i++) {
      const r = results[i]
      console.log(`  ${String(i+1).padStart(3)} ${r.label.padEnd(90)} ${String(r.sigs).padStart(4)} ${r.wr.toFixed(0).padStart(3)}%  ${(r.avgRoc>=0?'+':'')+r.avgRoc.toFixed(2)}%  ${r.green}/${r.days} (${r.greenPct.toFixed(0)}%)`)
    }
  }

  // Final: combine all observation windows, show absolute best
  allResults.sort((a, b) => b.avgRoc - a.avgRoc)
  console.log(`\n${'═'.repeat(130)}`)
  console.log(`  OVERALL TOP 50 — ALL OBSERVATION WINDOWS COMBINED`)
  console.log(`${'═'.repeat(130)}`)
  console.log(`  ${'#'.padStart(3)} ${'Label'.padEnd(100)} Sigs Win%  AvgROC  PnL       Grn/Tot`)
  console.log(`  ${'-'.repeat(125)}`)
  for (let i = 0; i < Math.min(50, allResults.length); i++) {
    const r = allResults[i]
    console.log(`  ${String(i+1).padStart(3)} ${r.label.padEnd(100)} ${String(r.sigs).padStart(4)} ${r.wr.toFixed(0).padStart(3)}%  ${(r.avgRoc>=0?'+':'')+r.avgRoc.toFixed(2)}%  ${(r.totalPnl>=0?'+':'')+Math.round(r.totalPnl).toString().padStart(8)}  ${r.green}/${r.days} (${r.greenPct.toFixed(0)}%)`)
  }

  // Stats
  const profitable = allResults.filter(r => r.avgRoc > 0).length
  const gt1 = allResults.filter(r => r.avgRoc >= 1).length
  const gt2 = allResults.filter(r => r.avgRoc >= 2).length
  console.log(`\n  Total combos: ${allResults.length} | Profitable: ${profitable} | >=1% ROC: ${gt1} | >=2% ROC: ${gt2}`)
  console.log(`  Completed in ${((Date.now()-t0)/1000).toFixed(0)}s`)

  // Best per direction
  for (const dir of ['SELL', 'BUY', 'BOTH']) {
    const best = allResults.filter(r => r.label.includes(dir + ' '))[0]
    if (best) console.log(`\n  Best ${dir}: ${best.label}\n    ROC: ${best.avgRoc.toFixed(2)}%, Win: ${best.wr.toFixed(0)}%, PnL: ${Math.round(best.totalPnl)}, Green: ${best.green}/${best.days}`)
  }
}

main().catch(console.error)
