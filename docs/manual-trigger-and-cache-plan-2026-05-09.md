# Manual Trigger And Cache Plan

Date: 2026-05-09

## Problem

The UI was calling expensive endpoints automatically on page load and every 5 minutes. That caused ClickHouse parquet scans to run even when the user only opened the app.

The worst automatic behavior was:

- loading scanner data on app open
- loading Paper Desk enriched prices on app open
- loading backtest dashboard during the global refresh
- staging fresh signals during the global refresh
- repeating all of the above every 5 minutes

## Change Made

The frontend now uses manual triggers:

- `Load Workspace` button loads the general workspace snapshot.
- `Stage Fresh Signals` button runs `/api/swing/fresh-signals`.
- `Load Dashboard` button loads stored backtest dashboard data.
- `Run Backtest` button runs the heavy backtest.

Fresh signal staging no longer happens just because the app opened.

The automatic 5-minute refresh interval was removed.

## Cache Plan

Backend cache should be added in this order:

1. Latest daily feature cache
   - Store daily screener features in ClickHouse table, keyed by `data_date` and `symbol`.
   - Columns should include SMA20, SMA50, SMA200, RSI10, volume ratio, 20-day high, 52-week high, 52-week low.
   - Scanner should read this table first instead of recalculating rolling windows from parquet.
   - Implemented table: `trading.daily_screener_features`.
   - Manual endpoint: `POST /api/swing/feature-cache/refresh`.
   - UI button: `Refresh Feature Cache`.
   - RSI10 is calculated from close-to-close price gains/losses. Volume is not part of RSI; volume is stored separately as `day_volume` and `avg_volume20`.

2. Fresh signal cache
   - Store the latest fresh-signal result per `signal_date`.
   - Button click should reuse the cached result if the same `signal_date` is already computed.
   - Only ledger insert/staging should mutate state.

3. Paper Desk price cache
   - Store latest close per symbol/date once.
   - Paper Desk should not trigger repeated parquet scans per symbol.
   - Live Dhan quote should override cache during market hours.

4. Backtest run cache
   - Backtests should run as a job with a `run_id`.
   - UI should poll job status instead of waiting on one long request.
   - Completed results should be read from `trading.backtest_trades`, not recomputed.

## Target UX

Opening the app should be light.

Heavy work should happen only when the user explicitly clicks:

```text
Load Workspace
Refresh Feature Cache
Stage Fresh Signals
Load Dashboard
Run Backtest
```

The UI should also show the last cached timestamp so it is clear whether the user is seeing fresh data or stored data.
