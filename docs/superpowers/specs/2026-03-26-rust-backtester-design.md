# Rust CLI Backtester — Design Spec

## Goal

CLI tool to backtest signal generation across all 2460 NSE EQ stocks using downloaded JSON candle files. Primary purpose: evaluate whether expanding beyond the current 205 F&O stocks improves overall system performance.

## Architecture

### Binary

`engine/src/bin/backtest.rs` — new binary in the existing engine crate.

### Prerequisite: lib.rs

The engine crate currently has no `lib.rs`. Create one that re-exports the modules the backtester needs:

```rust
// engine/src/lib.rs
pub mod types;
pub mod signal_engine;
pub mod exit_manager;
pub mod dynamic_qty;
pub mod derived;
```

This lets `backtest.rs` import via `use engine::signal_engine::compute_signal;` etc. No changes to existing module code.

### Data Flow

```
CLI args (--from, --to, --config, --data-dir, --csv)
  │
  ├─ Parse config from JSON file (or use defaults)
  ├─ Download scrip master CSV → tag F&O stocks
  ├─ Scan data/candles/ for stock directories
  │
  ├─ Per stock (parallel via rayon):
  │   ├─ Sort day files chronologically
  │   ├─ For each day:
  │   │   ├─ Read JSON → Vec<Candle>
  │   │   ├─ Convert candles → Vec<Snapshot> (compute derived fields)
  │   │   ├─ Compute gap_pct from prev_day's last close
  │   │   ├─ Compute morning_range_pct
  │   │   ├─ Run compute_signal() → Option<Signal>
  │   │   ├─ If signal: scan remaining buckets for exit via check_exit()
  │   │   └─ Collect TradeResult
  │   └─ Carry prev_close to next day
  │
  └─ Aggregate results → print summary + optional CSV
```

### JSON → Snapshot Conversion

Each JSON candle has: `{ timestamp, open, high, low, close, volume }`

Convert to `Snapshot` by computing:
- `bucket`: from timestamp → IST → bucket number
- `volume_cum`: running sum of volumes
- `volume_delta`: current candle's volume
- `vwap`, `price_velocity`, `volume_rate`, `candle_body_ratio`: via `derived::compute()`
- `oi_total`, `oi_delta`, `bid`, `ask`, `spread_pct`: set to 0 (not available in JSON candle data)

### Gap Calculation

- `prev_close` = previous trading day's last candle close price
- `day_open` = first candle's open price
- `gap_pct = (day_open - prev_close) / prev_close * 100`
- First day of range: `gap_pct = 0.0` (no previous data)

### F&O Tagging

Download Dhan scrip master CSV at startup (same as `scrip_master.rs`):
- Extract FUTSTK symbols → F&O set
- Tag each stock as F&O or non-F&O
- Used only for summary comparison, not for filtering

### Config

Reads from JSON file (default: `backtest-config.json`), fields match `SignalConfig`.
Missing fields fall back to `SignalConfig::default()`.

### CLI Interface

```
backtest [OPTIONS]

Options:
  --from <DATE>       Start date (default: earliest available)
  --to <DATE>         End date (default: latest available)
  --data-dir <PATH>   Candle data directory (default: data/candles)
  --config <PATH>     Config JSON file (default: backtest-config.json)
  --csv <PATH>        Output full trade log to CSV
  --threads <N>       Parallelism (default: num_cpus)
```

### Output

**Terminal (always)**:
```
=== Backtest: 2026-02-01 → 2026-03-25 | 2460 stocks | 36 days ===

SUMMARY
  Total trades: 4,521    Win rate: 62.3%
  Avg return: +0.34%     Total PnL: Rs 1,53,200
  BUY:  2,890 trades     Win: 64.1%   Avg: +0.38%
  SELL: 1,631 trades     Win: 59.2%   Avg: +0.27%
  Exits: TP=2,817  SL=1,204  TIME=500

TOP 20 STOCKS BY PnL
  RELIANCE    23 trades   78.3% win   Rs 12,400
  ...

BOTTOM 20 STOCKS BY PnL
  ...

F&O vs NON-F&O
  F&O (205):      2,100 trades   63.5% win   Rs 89,000
  Non-F&O (2255): 2,421 trades   61.3% win   Rs 64,200
```

**CSV (--csv flag)**:
Columns: `date,symbol,direction,entry_bucket,entry_price,exit_bucket,exit_price,exit_reason,return_pct,pnl,quantity,score,is_fno`

### Dependencies (new)

- `rayon` — parallel stock processing
- `clap` — CLI argument parsing
- `serde_json` — JSON candle parsing (already in crate)

## What This Does NOT Do

- No ClickHouse reads or writes
- No parameter optimization / sweep (single config per run)
- No order execution
- No UI
