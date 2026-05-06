CREATE DATABASE IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.backtest_runs (
    run_id              String,
    strategy_id         String,
    strategy_name       String,
    started_at          DateTime DEFAULT now(),
    completed_at        Nullable(DateTime),
    from_date           Date,
    to_date             Date,
    total_trades        UInt32 DEFAULT 0,
    win_rate            Float64 DEFAULT 0,
    total_pnl           Float64 DEFAULT 0,
    total_return_pct    Float64 DEFAULT 0,
    max_drawdown_pct    Float64 DEFAULT 0,
    status              String DEFAULT 'pending',
    error_message       String DEFAULT ''
) ENGINE = ReplacingMergeTree(started_at)
ORDER BY (strategy_id, run_id);

CREATE TABLE IF NOT EXISTS trading.backtest_trades (
    run_id              String,
    strategy_id         String,
    symbol              String,
    signal_date         Date,
    entry_date          Date,
    exit_date           Date,
    setup_family        String,
    entry_price         Float64,
    exit_price          Float64,
    quantity            UInt32,
    capital_used        Float64,
    pnl                 Float64,
    return_pct          Float64,
    exit_reason         String,
    hold_sessions       UInt16,
    score               UInt8
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(entry_date)
ORDER BY (strategy_id, run_id, entry_date, symbol);

CREATE TABLE IF NOT EXISTS trading.backtest_yearly_returns (
    run_id              String,
    strategy_id         String,
    year                UInt16,
    trades              UInt32,
    win_rate            Float64,
    pnl                 Float64,
    return_pct          Float64,
    max_drawdown_pct    Float64
) ENGINE = ReplacingMergeTree()
ORDER BY (strategy_id, run_id, year);
