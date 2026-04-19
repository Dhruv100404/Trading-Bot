#!/usr/bin/env node
// DEEP PATTERN SEARCH — exhaustive analysis to find 2% daily ROC
// Analyzes raw ClickHouse data directly, bypasses signal engine limitations
// Tests hundreds of feature combinations, filters, ranking methods

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
// PHASE 1: Build feature matrix from raw snapshots
// ═══════════════════════════════════════════════════════════════════════════════

async function buildFeatureMatrix() {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 1: Building feature matrix from raw ClickHouse data')
  console.log('█'.repeat(80))

  // Get all (date, symbol) with entry window data (bucket 1-5) and outcome data (bucket 6+)
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
          count() as entry_bars
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 4
        GROUP BY trading_date, symbol
        HAVING entry_bars >= 3 AND entry_ltp > 0 AND open_ltp > 0
      ),
      post AS (
        SELECT trading_date, symbol,
          max(candle_high) as post_high,
          min(candle_low) as post_low,
          argMax(ltp, bucket) as last_ltp,
          max(bucket) as last_bucket
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') AND trading_date <= toDate('${TO}')
          AND bucket >= 5 AND bucket <= 80
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
      -- Multi-day momentum: was the stock moving in the same direction yesterday?
      prev_day AS (
        SELECT trading_date, symbol,
          (argMax(ltp, bucket) - argMin(ltp, bucket)) / argMin(ltp, bucket) * 100 as prev_day_range_pct,
          if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev_day_dir
        FROM trading.snapshots
        WHERE trading_date >= toDate('${FROM}') - 10 AND trading_date <= toDate('${TO}')
          AND bucket >= 1 AND bucket <= 80
        GROUP BY trading_date, symbol
      )
    SELECT
      toString(e.trading_date) as trading_date,
      e.symbol,
      -- Entry features
      e.entry_ltp as entry_price,
      (e.entry_ltp - e.open_ltp) / e.open_ltp * 100 as move_pct,
      e.entry_vol,
      e.avg_vol_rate,
      e.max_vol_rate,
      e.avg_body,
      e.entry_range / e.open_ltp * 100 as range_pct,
      -- VWAP
      if(e.entry_vwap > 0, (e.entry_ltp - e.entry_vwap) / e.entry_vwap * 100, 0) as vwap_dist_pct,
      -- Gap
      g.gap_pct,
      -- Multi-day momentum
      pd.prev_day_range_pct,
      pd.prev_day_dir,
      -- Direction
      if((e.entry_ltp - e.open_ltp) > 0, 'BUY', 'SELL') as direction,
      -- Outcome (MFE/MAE from entry)
      if((e.entry_ltp - e.open_ltp) > 0,
        (p.post_high - e.entry_ltp) / e.entry_ltp * 100,
        (e.entry_ltp - p.post_low) / e.entry_ltp * 100
      ) as mfe_pct,
      if((e.entry_ltp - e.open_ltp) > 0,
        (e.entry_ltp - p.post_low) / e.entry_ltp * 100,
        (p.post_high - e.entry_ltp) / e.entry_ltp * 100
      ) as mae_pct,
      -- Final return at various exit points
      if((e.entry_ltp - e.open_ltp) > 0,
        (p.last_ltp - e.entry_ltp) / e.entry_ltp * 100,
        (e.entry_ltp - p.last_ltp) / e.entry_ltp * 100
      ) as time_exit_ret
    FROM entry e
    JOIN post p ON e.trading_date = p.trading_date AND e.symbol = p.symbol
    LEFT JOIN gaps g ON toString(e.trading_date) = g.td AND e.symbol = g.sym
    LEFT JOIN prev_day pd ON e.trading_date - 1 = pd.trading_date AND e.symbol = pd.symbol
    WHERE abs((e.entry_ltp - e.open_ltp) / e.open_ltp * 100) >= 0.1
  `

  const rows = await qj(sql)
  console.log(`  Loaded ${rows.length} trade candidates across ${new Set(rows.map(r=>r.trading_date)).size} days`)
  return rows
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 2: Test every possible filter combination
// ═══════════════════════════════════════════════════════════════════════════��═══

function simulateTrades(candidates, tp_pct, sl_pct, maxPositions, capitalPerTrade) {
  // Group by date
  const byDate = {}
  for (const c of candidates) {
    if (!byDate[c.trading_date]) byDate[c.trading_date] = []
    byDate[c.trading_date].push(c)
  }
  const dates = Object.keys(byDate).sort()

  let totalPnl = 0
  let totalSignals = 0
  let wins = 0
  let losses = 0
  const dailyRocs = []
  let greenDays = 0

  for (const date of dates) {
    let dayCandidates = byDate[date]
    // Select top N by ranking score (already sorted)
    const selected = dayCandidates.slice(0, maxPositions)
    let dayPnl = 0

    for (const s of selected) {
      totalSignals++
      const qty = Math.floor(capitalPerTrade / s.entry_price)
      if (qty <= 0) continue

      // Simulate: did MFE reach TP before MAE reached SL?
      let ret
      if (s.mfe_pct >= tp_pct && s.mae_pct >= sl_pct) {
        // Both could trigger — estimate which came first based on ratio
        // If MFE/MAE ratio > TP/SL ratio, likely TP hit first
        ret = (s.mfe_pct / Math.max(s.mae_pct, 0.01)) > (tp_pct / sl_pct) ? tp_pct : -sl_pct
      } else if (s.mfe_pct >= tp_pct) {
        ret = tp_pct
      } else if (s.mae_pct >= sl_pct) {
        ret = -sl_pct
      } else {
        ret = s.time_exit_ret  // TIME exit
      }

      const pnl = s.entry_price * (ret / 100) * qty
      dayPnl += pnl
      if (ret > 0.05) wins++
      else losses++
    }

    const dayCap = selected.reduce((s, c) => s + Math.floor(capitalPerTrade / c.entry_price) * c.entry_price, 0)
    const margin = dayCap / 5
    dailyRocs.push(margin > 0 ? (dayPnl / margin) * 100 : 0)
    if (dayPnl > 0) greenDays++
    totalPnl += dayPnl
  }

  const avgRoc = dailyRocs.length > 0 ? dailyRocs.reduce((a, b) => a + b, 0) / dailyRocs.length : 0
  const winRate = (wins + losses) > 0 ? wins / (wins + losses) * 100 : 0

  return {
    signals: totalSignals,
    winRate,
    avgRoc,
    totalPnl,
    greenDays,
    totalDays: dates.length,
    greenPct: dates.length > 0 ? greenDays / dates.length * 100 : 0,
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 3: Exhaustive filter + ranking search
// ═══════════════════════════════════════════════════════════════════════════════

function runExhaustiveSearch(allRows) {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 2: Exhaustive filter + ranking + TP/SL search')
  console.log('█'.repeat(80))

  const results = []
  let tested = 0

  // Filters to test
  const directions = ['SELL', 'BUY', 'BOTH']
  const minMoves = [0.1, 0.2, 0.3, 0.5, 0.7]
  const minVolRates = [0, 100, 200, 500]
  const gapFilters = [
    { name: 'noGap', fn: () => true },
    { name: 'gapDn', fn: r => r.gap_pct < -0.5 },
    { name: 'gapUp', fn: r => r.gap_pct > 0.5 },
    { name: 'bigGap', fn: r => Math.abs(r.gap_pct) > 2 },
    { name: 'smallGap', fn: r => Math.abs(r.gap_pct) < 1 },
  ]
  const vwapFilters = [
    { name: 'noVwap', fn: () => true },
    { name: 'vwapAligned', fn: r => (r.direction === 'BUY' && r.vwap_dist_pct > 0) || (r.direction === 'SELL' && r.vwap_dist_pct < 0) },
    { name: 'vwapFar', fn: r => Math.abs(r.vwap_dist_pct) > 0.3 },
  ]
  const momentumFilters = [
    { name: 'noMom', fn: () => true },
    { name: 'sameDirMom', fn: r => r.prev_day_dir && ((r.direction === 'BUY' && r.prev_day_dir > 0) || (r.direction === 'SELL' && r.prev_day_dir < 0)) },
    { name: 'reversalMom', fn: r => r.prev_day_dir && ((r.direction === 'BUY' && r.prev_day_dir < 0) || (r.direction === 'SELL' && r.prev_day_dir > 0)) },
    { name: 'strongPrev', fn: r => r.prev_day_range_pct > 1.5 },
  ]
  const bodyFilters = [
    { name: 'noBody', fn: () => true },
    { name: 'strongBody', fn: r => r.avg_body > 0.6 },
  ]
  // Ranking methods
  const rankers = [
    { name: 'volRate', fn: (a, b) => (b.max_vol_rate || 0) - (a.max_vol_rate || 0) },
    { name: 'absMove', fn: (a, b) => Math.abs(b.move_pct) - Math.abs(a.move_pct) },
    // mfeMae REMOVED — uses future data (MFE/MAE only known after trade)
    { name: 'volXmove', fn: (a, b) => (b.max_vol_rate * Math.abs(b.move_pct)) - (a.max_vol_rate * Math.abs(a.move_pct)) },
    { name: 'range', fn: (a, b) => b.range_pct - a.range_pct },
    { name: 'vwapDist', fn: (a, b) => Math.abs(b.vwap_dist_pct) - Math.abs(a.vwap_dist_pct) },
    { name: 'gapSize', fn: (a, b) => Math.abs(b.gap_pct || 0) - Math.abs(a.gap_pct || 0) },
    { name: 'entryVol', fn: (a, b) => (b.entry_vol || 0) - (a.entry_vol || 0) },
  ]
  const tpSlCombos = [
    [0.5, 0.3], [0.5, 0.5], [0.7, 0.3], [0.7, 0.5], [0.7, 0.7],
    [1.0, 0.3], [1.0, 0.5], [1.0, 0.7], [1.0, 1.0],
    [1.5, 0.5], [1.5, 0.7], [1.5, 1.0],
    [2.0, 0.5], [2.0, 0.7], [2.0, 1.0],
    [0, 0],  // no TP/SL, pure TIME exit
  ]
  const positionSizes = [3, 5, 8, 12]

  const total = directions.length * minMoves.length * gapFilters.length * vwapFilters.length *
    momentumFilters.length * rankers.length * tpSlCombos.length * positionSizes.length
  console.log(`  Testing ~${total} combinations (will skip empty ones)...`)

  // Prioritized search — test most promising dimensions first
  for (const dir of directions) {
    for (const mm of minMoves) {
      for (const gf of gapFilters) {
        for (const vf of vwapFilters) {
          for (const mf of momentumFilters) {
            for (const bf of bodyFilters) {
              // Apply filters
              let filtered = allRows.filter(r => {
                if (dir !== 'BOTH' && r.direction !== dir) return false
                if (Math.abs(r.move_pct) < mm) return false
                if (!gf.fn(r)) return false
                if (!vf.fn(r)) return false
                if (!mf.fn(r)) return false
                if (!bf.fn(r)) return false
                return true
              })

              if (filtered.length < 100) continue // too few signals

              for (const ranker of rankers) {
                // Sort by ranking
                const sorted = [...filtered].sort(ranker.fn)

                for (const [tp, sl] of tpSlCombos) {
                  for (const maxPos of positionSizes) {
                    tested++
                    if (tested % 5000 === 0) process.stderr.write(`  ${tested} tested, ${results.length} valid...\r`)

                    const r = simulateTrades(sorted, tp, sl, maxPos, 100000)
                    if (r.signals < 50) continue

                    if (r.avgRoc > -0.1) { // only keep non-terrible results
                      results.push({
                        label: `${dir} mv>=${mm} ${gf.name} ${vf.name} ${mf.name} ${bf.name} rank=${ranker.name} TP=${tp} SL=${sl} pos=${maxPos}`,
                        ...r,
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
  }

  return results
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 4: Compound filters — test multi-condition combos
// ═══════════════════════════════════════════════════════════════════════════════

function runCompoundSearch(allRows) {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 3: Compound multi-condition filters')
  console.log('█'.repeat(80))

  const results = []
  let tested = 0

  // Test compound conditions that the earlier analysis found promising
  const compounds = [
    // Big gap + continuation
    { name: 'bigGapDn+SELL+volHigh', fn: r => r.direction === 'SELL' && r.gap_pct < -2 && r.max_vol_rate > 200 },
    { name: 'bigGapUp+BUY+volHigh', fn: r => r.direction === 'BUY' && r.gap_pct > 2 && r.max_vol_rate > 200 },
    // Gap reversal
    { name: 'gapDn+BUY+reversal', fn: r => r.direction === 'BUY' && r.gap_pct < -1 && Math.abs(r.move_pct) > 0.3 },
    { name: 'gapUp+SELL+reversal', fn: r => r.direction === 'SELL' && r.gap_pct > 1 && Math.abs(r.move_pct) > 0.3 },
    // VWAP + volume confirmation
    { name: 'SELL+vwapBelow+volHigh', fn: r => r.direction === 'SELL' && r.vwap_dist_pct < -0.2 && r.max_vol_rate > 300 },
    { name: 'BUY+vwapAbove+volHigh', fn: r => r.direction === 'BUY' && r.vwap_dist_pct > 0.2 && r.max_vol_rate > 300 },
    // Strong move + volume
    { name: 'SELL+bigMove+bigVol', fn: r => r.direction === 'SELL' && Math.abs(r.move_pct) > 0.5 && r.max_vol_rate > 500 },
    { name: 'BUY+bigMove+bigVol', fn: r => r.direction === 'BUY' && Math.abs(r.move_pct) > 0.5 && r.max_vol_rate > 500 },
    // Multi-day momentum + current direction
    { name: 'SELL+prevDaySame+move', fn: r => r.direction === 'SELL' && r.prev_day_dir < 0 && Math.abs(r.move_pct) > 0.3 },
    { name: 'BUY+prevDaySame+move', fn: r => r.direction === 'BUY' && r.prev_day_dir > 0 && Math.abs(r.move_pct) > 0.3 },
    // Reversal from previous day
    { name: 'SELL+prevDayUp+reversal', fn: r => r.direction === 'SELL' && r.prev_day_dir > 0 && r.prev_day_range_pct > 1 },
    { name: 'BUY+prevDayDn+reversal', fn: r => r.direction === 'BUY' && r.prev_day_dir < 0 && r.prev_day_range_pct > 1 },
    // Strong body + VWAP + volume (triple confirmation)
    { name: 'SELL+body+vwap+vol', fn: r => r.direction === 'SELL' && r.avg_body > 0.6 && r.vwap_dist_pct < 0 && r.max_vol_rate > 200 },
    { name: 'BUY+body+vwap+vol', fn: r => r.direction === 'BUY' && r.avg_body > 0.6 && r.vwap_dist_pct > 0 && r.max_vol_rate > 200 },
    // Gap continuation + VWAP + volume (the strongest from analysis)
    { name: 'SELL+gapDn+vwap+vol', fn: r => r.direction === 'SELL' && r.gap_pct < -0.5 && r.vwap_dist_pct < 0 && r.max_vol_rate > 150 },
    { name: 'BUY+gapUp+vwap+vol', fn: r => r.direction === 'BUY' && r.gap_pct > 0.5 && r.vwap_dist_pct > 0 && r.max_vol_rate > 150 },
    // Extreme gap + volume spike (institutional)
    { name: 'bigGap3+vol500', fn: r => Math.abs(r.gap_pct) > 3 && r.max_vol_rate > 500 },
    { name: 'bigGap3+vol500+aligned', fn: r => Math.abs(r.gap_pct) > 3 && r.max_vol_rate > 500 && ((r.direction==='BUY'&&r.gap_pct>0)||(r.direction==='SELL'&&r.gap_pct<0)) },
    // Wide range morning + direction (volatility play)
    { name: 'wideRange+SELL', fn: r => r.direction === 'SELL' && r.range_pct > 1.5 && r.max_vol_rate > 200 },
    { name: 'wideRange+BUY', fn: r => r.direction === 'BUY' && r.range_pct > 1.5 && r.max_vol_rate > 200 },
    // Tight move but high volume (accumulation before breakout)
    { name: 'tightMove+highVol+SELL', fn: r => r.direction === 'SELL' && Math.abs(r.move_pct) < 0.4 && Math.abs(r.move_pct) > 0.15 && r.max_vol_rate > 500 },
    { name: 'tightMove+highVol+BUY', fn: r => r.direction === 'BUY' && Math.abs(r.move_pct) < 0.4 && Math.abs(r.move_pct) > 0.15 && r.max_vol_rate > 500 },
    // Combined: prev day strong + today gap aligned + volume
    { name: 'prevStrong+gapAlign+vol+SELL', fn: r => r.direction === 'SELL' && r.prev_day_dir < 0 && r.prev_day_range_pct > 1 && r.gap_pct < 0 && r.max_vol_rate > 200 },
    { name: 'prevStrong+gapAlign+vol+BUY', fn: r => r.direction === 'BUY' && r.prev_day_dir > 0 && r.prev_day_range_pct > 1 && r.gap_pct > 0 && r.max_vol_rate > 200 },
    // ALL direction (take both BUY and SELL)
    { name: 'BOTH+bigGap+vol', fn: r => Math.abs(r.gap_pct) > 2 && r.max_vol_rate > 300 },
    { name: 'BOTH+vwapAligned+vol+body', fn: r => ((r.direction==='BUY'&&r.vwap_dist_pct>0.2)||(r.direction==='SELL'&&r.vwap_dist_pct<-0.2)) && r.max_vol_rate > 200 && r.avg_body > 0.5 },
    { name: 'BOTH+move0.5+vol300+vwap', fn: r => Math.abs(r.move_pct) > 0.5 && r.max_vol_rate > 300 && ((r.direction==='BUY'&&r.vwap_dist_pct>0)||(r.direction==='SELL'&&r.vwap_dist_pct<0)) },
  ]

  const rankers = [
    { name: 'volRate', fn: (a, b) => (b.max_vol_rate || 0) - (a.max_vol_rate || 0) },
    { name: 'absMove', fn: (a, b) => Math.abs(b.move_pct) - Math.abs(a.move_pct) },
    { name: 'volXmove', fn: (a, b) => (b.max_vol_rate * Math.abs(b.move_pct)) - (a.max_vol_rate * Math.abs(a.move_pct)) },
    { name: 'entryVol', fn: (a, b) => (b.entry_vol || 0) - (a.entry_vol || 0) },
  ]

  const tpSlCombos = [
    [0.5, 0.3], [0.7, 0.3], [0.7, 0.5], [1.0, 0.3], [1.0, 0.5], [1.0, 0.7],
    [1.5, 0.5], [1.5, 0.7], [1.5, 1.0], [2.0, 0.7], [2.0, 1.0],
    [0, 0],
  ]

  for (const comp of compounds) {
    const filtered = allRows.filter(comp.fn)
    if (filtered.length < 30) continue

    for (const ranker of rankers) {
      const sorted = [...filtered].sort(ranker.fn)

      for (const [tp, sl] of tpSlCombos) {
        for (const maxPos of [3, 5, 8]) {
          tested++
          const r = simulateTrades(sorted, tp, sl, maxPos, 100000)
          if (r.signals < 30) continue

          results.push({
            label: `${comp.name} rank=${ranker.name} TP=${tp} SL=${sl} pos=${maxPos}`,
            ...r,
          })
        }
      }
    }
  }

  console.log(`  Tested ${tested} compound combos, ${results.length} valid`)
  return results
}

// ═══════════════════════════════════════════════════════════════════════════════
// PHASE 5: Feature importance — what actually predicts profits?
// ═══════════════════════════════════════════════════════════════════════════════

function analyzeFeatureImportance(allRows) {
  console.log('\n' + '█'.repeat(80))
  console.log('  PHASE 4: Feature importance analysis')
  console.log('█'.repeat(80))

  // For each feature, split at various thresholds and compare MFE, MAE, time_exit_ret
  const features = [
    { name: 'abs_move_pct', fn: r => Math.abs(r.move_pct) },
    { name: 'max_vol_rate', fn: r => r.max_vol_rate || 0 },
    { name: 'avg_vol_rate', fn: r => r.avg_vol_rate || 0 },
    { name: 'abs_gap_pct', fn: r => Math.abs(r.gap_pct || 0) },
    { name: 'abs_vwap_dist', fn: r => Math.abs(r.vwap_dist_pct || 0) },
    { name: 'avg_body', fn: r => r.avg_body || 0 },
    { name: 'range_pct', fn: r => r.range_pct || 0 },
    { name: 'entry_vol', fn: r => r.entry_vol || 0 },
    { name: 'prev_day_range', fn: r => r.prev_day_range_pct || 0 },
    { name: 'gap_aligned', fn: r => ((r.direction==='BUY'&&(r.gap_pct||0)>0)||(r.direction==='SELL'&&(r.gap_pct||0)<0)) ? 1 : 0 },
    { name: 'vwap_aligned', fn: r => ((r.direction==='BUY'&&(r.vwap_dist_pct||0)>0)||(r.direction==='SELL'&&(r.vwap_dist_pct||0)<0)) ? 1 : 0 },
    { name: 'prev_day_aligned', fn: r => ((r.direction==='BUY'&&(r.prev_day_dir||0)>0)||(r.direction==='SELL'&&(r.prev_day_dir||0)<0)) ? 1 : 0 },
    { name: 'mfe_mae_ratio', fn: r => r.mfe_pct / Math.max(r.mae_pct, 0.01) },
  ]

  console.log(`\n  ${'Feature'.padEnd(20)} | Thresh  | HIGH: MFE  MAE  Ret  N    | LOW:  MFE  MAE  Ret  N    | Lift`)
  console.log(`  ${'-'.repeat(100)}`)

  for (const feat of features) {
    const vals = allRows.map(r => feat.fn(r)).sort((a, b) => a - b)
    // Test at percentiles: 25%, 50%, 75%, 90%
    for (const pct of [0.5, 0.75, 0.9]) {
      const thresh = vals[Math.floor(vals.length * pct)]
      if (thresh === 0 || isNaN(thresh) || typeof thresh !== 'number') continue

      const high = allRows.filter(r => feat.fn(r) >= thresh)
      const low = allRows.filter(r => feat.fn(r) < thresh)

      const hMfe = high.reduce((s, r) => s + r.mfe_pct, 0) / high.length
      const hMae = high.reduce((s, r) => s + r.mae_pct, 0) / high.length
      const hRet = high.reduce((s, r) => s + r.time_exit_ret, 0) / high.length
      const lMfe = low.reduce((s, r) => s + r.mfe_pct, 0) / low.length
      const lMae = low.reduce((s, r) => s + r.mae_pct, 0) / low.length
      const lRet = low.reduce((s, r) => s + r.time_exit_ret, 0) / low.length

      const lift = hRet - lRet
      const marker = lift > 0.05 ? ' ★★★' : lift > 0.02 ? ' ★★' : lift > 0 ? ' ★' : ''

      console.log(
        `  ${feat.name.padEnd(20)} | ${String(thresh.toFixed(2)).padStart(7)} | ` +
        `${hMfe.toFixed(2).padStart(5)} ${hMae.toFixed(2).padStart(5)} ${(hRet>=0?'+':'') + hRet.toFixed(3).padStart(6)} ${String(high.length).padStart(5)} | ` +
        `${lMfe.toFixed(2).padStart(5)} ${lMae.toFixed(2).padStart(5)} ${(lRet>=0?'+':'') + lRet.toFixed(3).padStart(6)} ${String(low.length).padStart(5)} | ` +
        `${(lift>=0?'+':'') + lift.toFixed(3)}${marker}`
      )
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════════

async function main() {
  console.log(`\n${'═'.repeat(80)}`)
  console.log(`  DEEP PATTERN SEARCH`)
  console.log(`  Finding 2% daily ROC across ${FROM} to ${TO}`)
  console.log(`  Capital: 500K, 5x margin, 100K per trade`)
  console.log(`${'═'.repeat(80)}`)

  const t0 = Date.now()
  const allRows = await buildFeatureMatrix()
  console.log(`  Feature matrix built in ${((Date.now()-t0)/1000).toFixed(1)}s`)

  // Feature importance first
  analyzeFeatureImportance(allRows)

  // Exhaustive search
  const t1 = Date.now()
  const exhaustive = runExhaustiveSearch(allRows)
  console.log(`  Exhaustive search done in ${((Date.now()-t1)/1000).toFixed(1)}s`)

  // Compound search
  const t2 = Date.now()
  const compound = runCompoundSearch(allRows)
  console.log(`  Compound search done in ${((Date.now()-t2)/1000).toFixed(1)}s`)

  // Combine and sort
  const all = [...exhaustive, ...compound].sort((a, b) => b.avgRoc - a.avgRoc)

  console.log(`\n${'═'.repeat(130)}`)
  console.log(`  TOP 80 CONFIGS — SORTED BY AVG DAILY ROC`)
  console.log(`${'═'.repeat(130)}`)
  console.log(`  ${'#'.padStart(3)} ${'Label'.padEnd(75)} Sigs Win%  AvgROC  TotalPnl  Grn/Tot`)
  console.log(`  ${'-'.repeat(125)}`)

  for (let i = 0; i < Math.min(80, all.length); i++) {
    const r = all[i]
    const roc = r.avgRoc >= 0 ? `+${r.avgRoc.toFixed(2)}%` : `${r.avgRoc.toFixed(2)}%`
    const pnl = r.totalPnl >= 0 ? `+${Math.round(r.totalPnl)}` : `${Math.round(r.totalPnl)}`
    console.log(
      `  ${String(i+1).padStart(3)} ${r.label.padEnd(75)} ${String(r.signals).padStart(4)} ${r.winRate.toFixed(0).padStart(3)}%  ${roc.padStart(7)}  ${pnl.padStart(9)}  ${r.greenDays}/${r.totalDays} (${r.greenPct.toFixed(0)}%)`
    )
  }

  // Best by direction
  for (const dir of ['SELL', 'BUY', 'BOTH']) {
    const best = all.filter(r => r.label.includes(dir))[0]
    if (best) {
      console.log(`\n  Best ${dir}: ${best.label}`)
      console.log(`    ROC: ${best.avgRoc.toFixed(2)}%, Win: ${best.winRate.toFixed(0)}%, P&L: ${Math.round(best.totalPnl)}, Green: ${best.greenDays}/${best.totalDays}`)
    }
  }

  console.log(`\n  Total tested: ${all.length} combos in ${((Date.now()-t0)/1000).toFixed(1)}s`)
  console.log(`  ${all.filter(r => r.avgRoc > 0).length} profitable configs found`)
  console.log(`  ${all.filter(r => r.avgRoc >= 1.0).length} configs with >= 1% daily ROC`)
  console.log(`  ${all.filter(r => r.avgRoc >= 2.0).length} configs with >= 2% daily ROC`)
}

main().catch(console.error)
