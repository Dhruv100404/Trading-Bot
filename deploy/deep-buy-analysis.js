#!/usr/bin/env node
// DEEP BUY SIGNAL ANALYSIS — find profitable BUY patterns
// No future data lookahead. All features from bucket 1-4, outcomes from bucket 5+.

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

async function buildBuyMatrix() {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 1: Building BUY feature matrix')
  console.log('█'.repeat(80))

  const sql = `
    WITH
      entry AS (
        SELECT trading_date, symbol,
          argMin(ltp, bucket) as open_ltp,
          argMax(ltp, bucket) as entry_ltp,
          max(bucket) as entry_bucket,
          sum(volume_delta) as entry_vol,
          avg(volume_rate) as avg_vol_rate,
          max(volume_rate) as max_vol_rate,
          avg(vwap) as entry_vwap,
          avg(candle_body_ratio) as avg_body,
          max(candle_high) - min(candle_low) as entry_range,
          -- Volume acceleration: bucket2 vs bucket1, bucket3 vs bucket2
          sumIf(volume_delta, bucket = 2) as vol_b2,
          sumIf(volume_delta, bucket = 1) as vol_b1,
          sumIf(volume_delta, bucket = 3) as vol_b3,
          count() as entry_bars
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 4
        GROUP BY trading_date, symbol
        HAVING entry_bars >= 3 AND entry_ltp > 0 AND open_ltp > 0
      ),
      post_short AS (
        SELECT trading_date, symbol,
          max(candle_high) as post_high,
          min(candle_low) as post_low,
          argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 5 AND bucket <= 35
        GROUP BY trading_date, symbol
      ),
      post_medium AS (
        SELECT trading_date, symbol,
          max(candle_high) as post_high,
          min(candle_low) as post_low,
          argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 5 AND bucket <= 46
        GROUP BY trading_date, symbol
      ),
      post_long AS (
        SELECT trading_date, symbol,
          max(candle_high) as post_high,
          min(candle_low) as post_low,
          argMax(ltp, bucket) as last_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 5 AND bucket <= 71
        GROUP BY trading_date, symbol
      ),
      gaps AS (
        SELECT toString(t.trading_date) as td, t.symbol as sym,
          toFloat32(if(p.day_close > 0, (t.day_open - p.day_close) / p.day_close * 100, 0)) as gap_pct
        FROM (
          SELECT trading_date, symbol, argMin(ltp, bucket) as day_open
          FROM trading.snapshots
          WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          GROUP BY trading_date, symbol
        ) t
        ASOF LEFT JOIN (
          SELECT trading_date, symbol, argMax(ltp, bucket) as day_close
          FROM trading.snapshots
          WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          GROUP BY trading_date, symbol
        ) p ON t.symbol = p.symbol AND t.trading_date > p.trading_date
      ),
      prev_day AS (
        SELECT trading_date, symbol,
          (argMax(ltp, bucket) - argMin(ltp, bucket)) / argMin(ltp, bucket) * 100 as prev_range_pct,
          if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev_dir,
          argMax(ltp, bucket) as prev_close_ltp,
          argMin(ltp, bucket) as prev_open_ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 80
        GROUP BY trading_date, symbol
      ),
      -- 2-day momentum
      prev2_day AS (
        SELECT trading_date, symbol,
          if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev2_dir
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') - 15 AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 80
        GROUP BY trading_date, symbol
      )
    SELECT
      toString(e.trading_date) as trading_date, e.symbol,
      e.entry_ltp as entry_price,
      (e.entry_ltp - e.open_ltp) / e.open_ltp * 100 as move_pct,
      e.entry_vol, e.avg_vol_rate, e.max_vol_rate, e.avg_body,
      e.entry_range / e.open_ltp * 100 as range_pct,
      if(e.entry_vwap > 0, (e.entry_ltp - e.entry_vwap) / e.entry_vwap * 100, 0) as vwap_dist_pct,
      g.gap_pct,
      pd.prev_range_pct, pd.prev_dir,
      p2.prev2_dir,
      -- Volume acceleration
      if(e.vol_b1 > 0, e.vol_b2 / e.vol_b1, 0) as vol_accel_12,
      if(e.vol_b2 > 0, e.vol_b3 / e.vol_b2, 0) as vol_accel_23,
      -- BUY only
      if((e.entry_ltp - e.open_ltp) > 0, 'BUY', 'SELL') as direction,
      -- Outcomes at 3 exit windows (SHORT=35min, MED=46min, LONG=71min)
      (ps.post_high - e.entry_ltp) / e.entry_ltp * 100 as mfe_short,
      (e.entry_ltp - ps.post_low) / e.entry_ltp * 100 as mae_short,
      (ps.last_ltp - e.entry_ltp) / e.entry_ltp * 100 as ret_short,
      (pm.post_high - e.entry_ltp) / e.entry_ltp * 100 as mfe_med,
      (e.entry_ltp - pm.post_low) / e.entry_ltp * 100 as mae_med,
      (pm.last_ltp - e.entry_ltp) / e.entry_ltp * 100 as ret_med,
      (pl.post_high - e.entry_ltp) / e.entry_ltp * 100 as mfe_long,
      (e.entry_ltp - pl.post_low) / e.entry_ltp * 100 as mae_long,
      (pl.last_ltp - e.entry_ltp) / e.entry_ltp * 100 as ret_long
    FROM entry e
    JOIN post_short ps ON e.trading_date = ps.trading_date AND e.symbol = ps.symbol
    JOIN post_medium pm ON e.trading_date = pm.trading_date AND e.symbol = pm.symbol
    JOIN post_long pl ON e.trading_date = pl.trading_date AND e.symbol = pl.symbol
    LEFT JOIN gaps g ON toString(e.trading_date) = g.td AND e.symbol = g.sym
    LEFT JOIN prev_day pd ON e.trading_date - 1 = pd.trading_date AND e.symbol = pd.symbol
    LEFT JOIN prev2_day p2 ON e.trading_date - 2 = p2.trading_date AND e.symbol = p2.symbol
    WHERE (e.entry_ltp - e.open_ltp) / e.open_ltp * 100 > 0.05
  `
  const rows = await qj(sql)
  console.log(`  Loaded ${rows.length} BUY candidates across ${new Set(rows.map(r=>r.trading_date)).size} days`)
  return rows
}

function simulateBuy(candidates, tp, sl, maxPos, exitWindow) {
  const byDate = {}
  for (const c of candidates) {
    if (!byDate[c.trading_date]) byDate[c.trading_date] = []
    byDate[c.trading_date].push(c)
  }
  const dates = Object.keys(byDate).sort()
  let totalPnl = 0, totalSigs = 0, wins = 0, losses = 0
  const dailyRocs = []
  let greenDays = 0

  const mfeKey = exitWindow === 'short' ? 'mfe_short' : exitWindow === 'med' ? 'mfe_med' : 'mfe_long'
  const maeKey = exitWindow === 'short' ? 'mae_short' : exitWindow === 'med' ? 'mae_med' : 'mae_long'
  const retKey = exitWindow === 'short' ? 'ret_short' : exitWindow === 'med' ? 'ret_med' : 'ret_long'

  for (const date of dates) {
    const selected = byDate[date].slice(0, maxPos)
    let dayPnl = 0
    for (const s of selected) {
      totalSigs++
      const qty = Math.floor(100000 / s.entry_price)
      if (qty <= 0) continue
      let ret
      const mfe = s[mfeKey] || 0
      const mae = s[maeKey] || 0
      const timeRet = s[retKey] || 0

      if (tp > 0 && sl > 0 && mfe >= tp && mae >= sl) {
        ret = (mfe / Math.max(mae, 0.01)) > (tp / sl) ? tp : -sl
      } else if (tp > 0 && mfe >= tp) {
        ret = tp
      } else if (sl > 0 && mae >= sl) {
        ret = -sl
      } else {
        ret = timeRet
      }
      const pnl = s.entry_price * (ret / 100) * qty
      dayPnl += pnl
      if (ret > 0.05) wins++
      else losses++
    }
    const dayCap = selected.reduce((s, c) => s + Math.floor(100000 / c.entry_price) * c.entry_price, 0)
    const margin = dayCap / 5
    dailyRocs.push(margin > 0 ? (dayPnl / margin) * 100 : 0)
    if (dayPnl > 0) greenDays++
    totalPnl += dayPnl
  }
  const avgRoc = dailyRocs.length > 0 ? dailyRocs.reduce((a, b) => a + b, 0) / dailyRocs.length : 0
  const winRate = (wins + losses) > 0 ? wins / (wins + losses) * 100 : 0
  return { signals: totalSigs, winRate, avgRoc, totalPnl, greenDays, totalDays: dates.length, greenPct: dates.length > 0 ? greenDays / dates.length * 100 : 0 }
}

function analyzeFeatures(rows) {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 2: BUY Feature Importance')
  console.log('█'.repeat(80))

  const features = [
    { name: 'move_pct', fn: r => r.move_pct || 0 },
    { name: 'max_vol_rate', fn: r => r.max_vol_rate || 0 },
    { name: 'avg_vol_rate', fn: r => r.avg_vol_rate || 0 },
    { name: 'gap_pct', fn: r => r.gap_pct || 0 },
    { name: 'abs_gap_pct', fn: r => Math.abs(r.gap_pct || 0) },
    { name: 'vwap_dist_pct', fn: r => r.vwap_dist_pct || 0 },
    { name: 'avg_body', fn: r => r.avg_body || 0 },
    { name: 'range_pct', fn: r => r.range_pct || 0 },
    { name: 'entry_vol', fn: r => r.entry_vol || 0 },
    { name: 'prev_range_pct', fn: r => r.prev_range_pct || 0 },
    { name: 'vol_accel_12', fn: r => r.vol_accel_12 || 0 },
    { name: 'vol_accel_23', fn: r => r.vol_accel_23 || 0 },
    // Binary features
    { name: 'gap_down(<0)', fn: r => (r.gap_pct || 0) < -0.3 ? 1 : 0 },
    { name: 'gap_up(>0)', fn: r => (r.gap_pct || 0) > 0.3 ? 1 : 0 },
    { name: 'prev_day_down', fn: r => (r.prev_dir || 0) < 0 ? 1 : 0 },
    { name: 'prev_day_up', fn: r => (r.prev_dir || 0) > 0 ? 1 : 0 },
    { name: 'prev2_day_down', fn: r => (r.prev2_dir || 0) < 0 ? 1 : 0 },
    { name: 'vwap_above', fn: r => (r.vwap_dist_pct || 0) > 0.1 ? 1 : 0 },
    { name: 'vwap_below', fn: r => (r.vwap_dist_pct || 0) < -0.1 ? 1 : 0 },
  ]

  for (const window of ['short', 'med', 'long']) {
    const retKey = window === 'short' ? 'ret_short' : window === 'med' ? 'ret_med' : 'ret_long'
    const exitBucket = window === 'short' ? 35 : window === 'med' ? 46 : 71
    console.log(`\n  ── Exit window: ${window} (bucket ${exitBucket}) ──`)
    console.log(`  ${'Feature'.padEnd(20)} | Thresh  | HIGH: AvgRet   N    | LOW:  AvgRet   N    | Lift`)
    console.log(`  ${'-'.repeat(85)}`)

    for (const feat of features) {
      const vals = rows.map(r => feat.fn(r)).filter(v => typeof v === 'number' && !isNaN(v)).sort((a, b) => a - b)
      for (const pct of [0.5, 0.75, 0.9]) {
        const thresh = vals[Math.floor(vals.length * pct)]
        if (thresh === undefined || thresh === 0 || typeof thresh !== 'number') continue
        const high = rows.filter(r => feat.fn(r) >= thresh)
        const low = rows.filter(r => feat.fn(r) < thresh)
        if (high.length < 50 || low.length < 50) continue
        const hRet = high.reduce((s, r) => s + (r[retKey] || 0), 0) / high.length
        const lRet = low.reduce((s, r) => s + (r[retKey] || 0), 0) / low.length
        const lift = hRet - lRet
        const marker = lift > 0.05 ? ' ★★★' : lift > 0.02 ? ' ★★' : lift > 0 ? ' ★' : ''
        if (Math.abs(lift) > 0.01) {
          console.log(
            `  ${feat.name.padEnd(20)} | ${String(thresh.toFixed(2)).padStart(7)} | ` +
            `${(hRet >= 0 ? '+' : '') + hRet.toFixed(3).padStart(7)} ${String(high.length).padStart(5)} | ` +
            `${(lRet >= 0 ? '+' : '') + lRet.toFixed(3).padStart(7)} ${String(low.length).padStart(5)} | ` +
            `${(lift >= 0 ? '+' : '') + lift.toFixed(3)}${marker}`
          )
        }
      }
    }
  }
}

function exhaustiveSearch(rows) {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 3: Exhaustive BUY filter+ranking search')
  console.log('█'.repeat(80))

  const results = []
  let tested = 0

  const filters = [
    // Gap filters
    { name: 'noGap', fn: () => true },
    { name: 'gapDn<-0.5', fn: r => (r.gap_pct||0) < -0.5 },
    { name: 'gapDn<-1', fn: r => (r.gap_pct||0) < -1 },
    { name: 'gapDn<-2', fn: r => (r.gap_pct||0) < -2 },
    { name: 'gapUp>0.5', fn: r => (r.gap_pct||0) > 0.5 },
    { name: 'gapUp>1', fn: r => (r.gap_pct||0) > 1 },
    { name: 'smallGap', fn: r => Math.abs(r.gap_pct||0) < 1 },
    { name: 'bigGap>2', fn: r => Math.abs(r.gap_pct||0) > 2 },
    // Prev day
    { name: 'noMom', fn: () => true },
    { name: 'prevDn', fn: r => (r.prev_dir||0) < 0 },
    { name: 'prevUp', fn: r => (r.prev_dir||0) > 0 },
    { name: 'prevDn+strong', fn: r => (r.prev_dir||0) < 0 && (r.prev_range_pct||0) > 1 },
    { name: 'prevUp+strong', fn: r => (r.prev_dir||0) > 0 && (r.prev_range_pct||0) > 1 },
    { name: '2dayDn', fn: r => (r.prev_dir||0) < 0 && (r.prev2_dir||0) < 0 },
    { name: '2dayUp', fn: r => (r.prev_dir||0) > 0 && (r.prev2_dir||0) > 0 },
    // VWAP
    { name: 'noVwap', fn: () => true },
    { name: 'vwapAbove', fn: r => (r.vwap_dist_pct||0) > 0.1 },
    { name: 'vwapBelow', fn: r => (r.vwap_dist_pct||0) < -0.1 },
    { name: 'vwapFar', fn: r => Math.abs(r.vwap_dist_pct||0) > 0.3 },
    // Volume
    { name: 'noVol', fn: () => true },
    { name: 'vol>200', fn: r => (r.max_vol_rate||0) > 200 },
    { name: 'vol>500', fn: r => (r.max_vol_rate||0) > 500 },
    // Move
    { name: 'noMove', fn: () => true },
    { name: 'mv>0.2', fn: r => (r.move_pct||0) > 0.2 },
    { name: 'mv>0.5', fn: r => (r.move_pct||0) > 0.5 },
    { name: 'mv<0.5', fn: r => (r.move_pct||0) < 0.5 && (r.move_pct||0) > 0.05 },
    // Body
    { name: 'noBody', fn: () => true },
    { name: 'body>0.6', fn: r => (r.avg_body||0) > 0.6 },
    // Volume acceleration
    { name: 'noAccel', fn: () => true },
    { name: 'volSpike', fn: r => (r.vol_accel_12||0) > 2 },
  ]

  const rankers = [
    { name: 'volRate', fn: (a, b) => (b.max_vol_rate||0) - (a.max_vol_rate||0) },
    { name: 'move', fn: (a, b) => (b.move_pct||0) - (a.move_pct||0) },
    { name: 'gapSize', fn: (a, b) => Math.abs(b.gap_pct||0) - Math.abs(a.gap_pct||0) },
    { name: 'gapDnSize', fn: (a, b) => (a.gap_pct||0) - (b.gap_pct||0) }, // most negative gap first
    { name: 'volXmove', fn: (a, b) => ((b.max_vol_rate||0)*Math.abs(b.move_pct||0)) - ((a.max_vol_rate||0)*Math.abs(a.move_pct||0)) },
    { name: 'entryVol', fn: (a, b) => (b.entry_vol||0) - (a.entry_vol||0) },
    { name: 'body', fn: (a, b) => (b.avg_body||0) - (a.avg_body||0) },
  ]

  const tpSlCombos = [
    [0, 0], [0.5, 0.3], [0.7, 0.3], [0.7, 0.5],
    [1.0, 0.3], [1.0, 0.5], [1.0, 0.7],
    [1.5, 0.5], [1.5, 0.7], [1.5, 1.0],
    [2.0, 0.5], [2.0, 0.7], [2.0, 1.0],
  ]

  // Compound filter combos (not full cartesian — smart combinations)
  const compounds = []

  // Gap × Momentum
  for (const gf of filters.filter(f => f.name.startsWith('gap') || f.name === 'noGap' || f.name.startsWith('big') || f.name.startsWith('small'))) {
    for (const mf of filters.filter(f => f.name.startsWith('prev') || f.name.startsWith('2day') || f.name === 'noMom')) {
      for (const vf of filters.filter(f => f.name.startsWith('vwap') || f.name === 'noVwap')) {
        for (const vlf of filters.filter(f => f.name.startsWith('vol') || f.name === 'noVol')) {
          compounds.push({
            name: `${gf.name}+${mf.name}+${vf.name}+${vlf.name}`,
            fn: r => gf.fn(r) && mf.fn(r) && vf.fn(r) && vlf.fn(r)
          })
        }
      }
    }
  }

  console.log(`  Testing ${compounds.length} filter combos × ${rankers.length} rankers × ${tpSlCombos.length} TP/SL × 3 exit windows × 3 positions...`)

  for (const comp of compounds) {
    const filtered = rows.filter(comp.fn)
    if (filtered.length < 50) continue

    for (const ranker of rankers) {
      const sorted = [...filtered].sort(ranker.fn)

      for (const [tp, sl] of tpSlCombos) {
        for (const maxPos of [3, 5, 8]) {
          for (const exitW of ['short', 'med', 'long']) {
            tested++
            if (tested % 10000 === 0) process.stderr.write(`  ${tested} tested, ${results.length} valid...\r`)

            const r = simulateBuy(sorted, tp, sl, maxPos, exitW)
            if (r.signals < 30) continue
            if (r.avgRoc > -0.5) {
              results.push({ label: `${comp.name} rank=${ranker.name} TP=${tp} SL=${sl} pos=${maxPos} exit=${exitW}`, ...r })
            }
          }
        }
      }
    }
  }

  console.log(`  Tested ${tested} combos, ${results.length} valid`)
  return results
}

async function main() {
  console.log(`\n${'═'.repeat(80)}`)
  console.log(`  DEEP BUY SIGNAL ANALYSIS`)
  console.log(`  ${FROM} to ${TO} — BUY direction only, no lookahead`)
  console.log(`${'═'.repeat(80)}`)

  const t0 = Date.now()
  const rows = await buildBuyMatrix()

  analyzeFeatures(rows)

  const t1 = Date.now()
  const results = exhaustiveSearch(rows)
  results.sort((a, b) => b.avgRoc - a.avgRoc)

  console.log(`\n${'═'.repeat(140)}`)
  console.log(`  TOP 80 BUY CONFIGS — SORTED BY AVG DAILY ROC`)
  console.log(`${'═'.repeat(140)}`)
  console.log(`  ${'#'.padStart(3)} ${'Label'.padEnd(85)} Sigs Win%  AvgROC  TotalPnl  Grn/Tot`)
  console.log(`  ${'-'.repeat(135)}`)

  for (let i = 0; i < Math.min(80, results.length); i++) {
    const r = results[i]
    const roc = r.avgRoc >= 0 ? `+${r.avgRoc.toFixed(2)}%` : `${r.avgRoc.toFixed(2)}%`
    const pnl = r.totalPnl >= 0 ? `+${Math.round(r.totalPnl)}` : `${Math.round(r.totalPnl)}`
    console.log(
      `  ${String(i+1).padStart(3)} ${r.label.padEnd(85)} ${String(r.signals).padStart(4)} ${r.winRate.toFixed(0).padStart(3)}%  ${roc.padStart(7)}  ${pnl.padStart(9)}  ${r.greenDays}/${r.totalDays} (${r.greenPct.toFixed(0)}%)`
    )
  }

  console.log(`\n  Total tested: ${results.length} valid combos in ${((Date.now()-t0)/1000).toFixed(1)}s`)
  console.log(`  ${results.filter(r => r.avgRoc > 0).length} profitable BUY configs found`)
  console.log(`  ${results.filter(r => r.avgRoc >= 1.0).length} configs with >= 1% daily ROC`)
  console.log(`  ${results.filter(r => r.avgRoc >= 2.0).length} configs with >= 2% daily ROC`)

  // Save results
  console.log(`\n  Results saved to docs/deep-buy-analysis-results.md`)
}

main().catch(console.error)
