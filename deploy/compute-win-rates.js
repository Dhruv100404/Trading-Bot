#!/usr/bin/env node
/**
 * Compute per-stock win rates from backtest results and store in trading.stock_win_rate.
 *
 * Usage: node deploy/compute-win-rates.js [from] [to] [account_id]
 *   from:       start date (default: 2025-12-02)
 *   to:         end date (default: 2026-03-28)
 *   account_id: account to use config from (default: 1100896497)
 *
 * Steps:
 *   1. Fetch config for the account
 *   2. Run backtest with smart_score_mode=OFF (no WR influence)
 *   3. Compute win rate per stock from results
 *   4. INSERT into trading.stock_win_rate (ReplacingMergeTree deduplicates)
 */

const API = process.env.API_URL || 'http://localhost:8080/api';
const CH = process.env.CLICKHOUSE_URL || 'http://localhost:8123';
const FROM = process.argv[2] || '2025-12-02';
const TO = process.argv[3] || '2026-03-28';
const ACCOUNT = process.argv[4] || ''; // empty = default config (shared across all accounts)
const MIN_TRADES = parseInt(process.env.MIN_TRADES || '2');

async function main() {
  console.log('='.repeat(70));
  console.log(`  COMPUTE WIN RATES: ${FROM} to ${TO} (account: ${ACCOUNT})`);
  console.log('='.repeat(70));

  // 1. Fetch config
  console.log('\n1. Fetching config...');
  const cfgResp = await fetch(`${API}/config?account_id=${ACCOUNT}`);
  const cfg = await cfgResp.json();
  if (cfg.error) { console.error('Config error:', cfg.error); process.exit(1); }

  // Force smart_score OFF for clean baseline
  cfg.smart_score_mode = false;
  cfg.from = FROM;
  cfg.to = TO;
  console.log(`   gap_reversal_mode: ${cfg.gap_reversal_mode}`);
  console.log(`   sell_entry: ${cfg.sell_entry_start}-${cfg.sell_entry_end}`);
  console.log(`   sell_hard_exit: ${cfg.sell_hard_exit_bucket}`);

  // 2. Run backtest
  console.log('\n2. Running backtest (smart_score OFF)...');
  const btResp = await fetch(`${API}/backtest/compute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  const bt = await btResp.json();
  if (!bt.signals) { console.error('Backtest failed:', bt); process.exit(1); }
  console.log(`   Signals: ${bt.signals.length} | Candidates: ${bt.total_candidates}`);

  // 3. Compute win rates
  console.log('\n3. Computing win rates...');
  const wins = {}; // sym -> { wins, total }
  for (const s of bt.signals) {
    if (!s.symbol || s.actual_return_pct == null) continue;
    if (!wins[s.symbol]) wins[s.symbol] = { w: 0, t: 0 };
    wins[s.symbol].t++;
    if (s.actual_return_pct > 0.15) wins[s.symbol].w++;
  }

  const rates = Object.entries(wins)
    .filter(([_, v]) => v.t >= MIN_TRADES)
    .map(([sym, v]) => ({ sym, wr: v.w / v.t, n: v.t }))
    .sort((a, b) => b.wr - a.wr);

  console.log(`   Stocks with >= ${MIN_TRADES} trades: ${rates.length}`);
  console.log(`   Top 10:`);
  rates.slice(0, 10).forEach(r => console.log(`     ${r.sym.padEnd(20)} WR=${(r.wr*100).toFixed(0)}% (${r.n} trades)`));
  console.log(`   Bottom 5:`);
  rates.slice(-5).forEach(r => console.log(`     ${r.sym.padEnd(20)} WR=${(r.wr*100).toFixed(0)}% (${r.n} trades)`));

  // 4. Insert into ClickHouse
  console.log('\n4. Inserting into trading.stock_win_rate...');
  if (rates.length === 0) {
    console.log('   No data to insert');
    return;
  }

  const values = rates.map(r => `('${r.sym}',${r.wr.toFixed(4)},${r.n})`).join(',');
  const sql = `INSERT INTO trading.stock_win_rate (symbol, win_rate, n_trades) VALUES ${values}`;
  const insertResp = await fetch(CH, { method: 'POST', body: sql });
  if (!insertResp.ok) {
    console.error('Insert failed:', await insertResp.text());
    process.exit(1);
  }
  console.log(`   Inserted ${rates.length} rows`);

  // 5. Verify
  const vResp = await fetch(CH, { method: 'POST', body: 'SELECT count(), round(avg(win_rate),2), round(min(win_rate),2), round(max(win_rate),2) FROM trading.stock_win_rate FINAL WHERE n_trades >= 5 FORMAT TabSeparated' });
  const v = await vResp.text();
  console.log(`   Verify: ${v.trim()}`);

  console.log('\n' + '='.repeat(70));
  console.log('  DONE — Now enable smart_score_mode in config to use these rates');
  console.log('='.repeat(70));
}

main().catch(e => { console.error('FATAL:', e); process.exit(1); });
