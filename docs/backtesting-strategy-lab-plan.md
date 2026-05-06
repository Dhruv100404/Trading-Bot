# Backtesting Strategy Lab Plan

## Goal

Build an automated swing-strategy backtesting lab on top of the existing parquet candle history. The system should discover strategy definitions from a folder, run them across years of parquet data, store results, and show whether each strategy actually made money by year, setup, stock, and drawdown.

## Core Opinion

Backtesting should live beside the current Swing Atlas workflow, not inside paper trades. Scanner, Watchlist, and Paper Desk answer "what should I look at today?" Backtesting answers "did this idea work from 2021 to now?"

The engine should read parquet data through ClickHouse because Docker already mounts `./parquets` into ClickHouse at `/var/lib/clickhouse/user_files/parquets`. The Rust engine does not currently receive that folder, so ClickHouse `file('parquets/*.parquet', Parquet)` queries are the cleanest source of truth.

## Strategy Discovery

Create a root folder:

```text
strategies/
  swing-breakout.json
  pullback-20dma.json
  near-52w-high.json
```

Each file is a strategy definition. The engine scans this folder at startup or on API request and treats every enabled JSON file as a runnable strategy.

Required fields:

```json
{
  "id": "swing-breakout-v1",
  "name": "Swing Breakout V1",
  "enabled": true,
  "universe": {
    "source": "watchlist",
    "min_price": 50,
    "min_avg_volume20": 100000
  },
  "entry": {
    "setup_family": "Breakout Setup",
    "min_score": 85,
    "entry_price": "next_open"
  },
  "exit": {
    "take_profit_pct": 8,
    "stop_loss_pct": 4,
    "max_hold_sessions": 10,
    "exit_price": "close"
  },
  "risk": {
    "capital_per_trade": 50000,
    "max_positions_per_day": 5
  }
}
```

## Backtest Rules

1. Use only data available before the entry date.
2. Generate the signal from day `D` features.
3. Enter on day `D + 1` open, unless the strategy explicitly says same-day close.
4. Calculate quantity from `capital_per_trade / entry_price`.
5. Exit on the first condition hit:
   - target hit by future high
   - stop hit by future low
   - max hold sessions reached
6. If target and stop both hit in the same candle/day, use conservative ordering: stop first unless the strategy config says otherwise.
7. Store every trade, not only summaries.

This avoids look-ahead bias and makes the numbers believable.

## Initial Strategies

### 1. Swing Breakout

Uses current scanner logic:
- trend up
- near 20-day high
- volume ratio above 1.1
- score above 85

Good for momentum continuation.

### 2. Pullback To 20 DMA

Uses:
- price above 50 DMA
- close near 20 DMA
- trend up
- score above 80

Good for controlled risk entries.

### 3. Near 52W High

Uses:
- close within 8% of 52-week high
- constructive trend
- minimum volume

Good for leadership names.

## ClickHouse Tables

Add tables:

```sql
CREATE TABLE IF NOT EXISTS trading.backtest_runs (
    run_id String,
    strategy_id String,
    strategy_name String,
    started_at DateTime DEFAULT now(),
    completed_at Nullable(DateTime),
    from_date Date,
    to_date Date,
    total_trades UInt32,
    win_rate Float64,
    total_pnl Float64,
    total_return_pct Float64,
    max_drawdown_pct Float64,
    status String,
    error_message String DEFAULT ''
) ENGINE = ReplacingMergeTree(started_at)
ORDER BY (strategy_id, run_id);

CREATE TABLE IF NOT EXISTS trading.backtest_trades (
    run_id String,
    strategy_id String,
    symbol String,
    signal_date Date,
    entry_date Date,
    exit_date Date,
    setup_family String,
    entry_price Float64,
    exit_price Float64,
    quantity UInt32,
    capital_used Float64,
    pnl Float64,
    return_pct Float64,
    exit_reason String,
    hold_sessions UInt16,
    score UInt8
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(entry_date)
ORDER BY (strategy_id, run_id, entry_date, symbol);

CREATE TABLE IF NOT EXISTS trading.backtest_yearly_returns (
    run_id String,
    strategy_id String,
    year UInt16,
    trades UInt32,
    win_rate Float64,
    pnl Float64,
    return_pct Float64,
    max_drawdown_pct Float64
) ENGINE = ReplacingMergeTree()
ORDER BY (strategy_id, run_id, year);
```

## Engine API

Add a new `engine/src/api/backtest.rs`.

Endpoints:

```text
GET  /api/backtests/strategies
POST /api/backtests/run
GET  /api/backtests/runs
GET  /api/backtests/runs/:run_id
GET  /api/backtests/runs/:run_id/trades
```

The runner can be synchronous for MVP. Later it can become background job based.

## UI

Add a new sidebar page: `Backtests`.

First screen should show:
- strategy selector
- date range
- run button
- summary cards: total P&L, total return, CAGR-ish return, win rate, max drawdown, trades
- yearly returns table
- top winners and worst losers
- trade log with pagination

The page must feel like a trading report, not a marketing dashboard.

## MVP Implementation Order

1. Add ClickHouse backtest tables.
2. Add `strategies/` JSON strategy files.
3. Add strategy loader in Rust.
4. Add one ClickHouse-based backtest query for daily swing strategies.
5. Store run summary and trades.
6. Add API endpoints.
7. Add Backtests UI page.
8. Add yearly returns and drawdown chart.

## Later Improvements

- Parameter sweeps: TP 5/8/10%, SL 3/4/5%, hold 5/10/15 sessions.
- Walk-forward testing by year.
- Out-of-sample validation.
- Strategy comparison leaderboard.
- Export trades to CSV.
- Promote best strategy output into Scanner ranking.
