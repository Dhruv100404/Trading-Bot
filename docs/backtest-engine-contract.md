# Backtest Engine Contract

Backtest strategy logic is code-owned, not config-file owned.

Active paths:

- App Backtest API: Rust/ClickHouse in `engine/src/api/backtest.rs`
- Research and tuning backtests: Python/NumPy scripts in `scripts/`
- Latest tuned outputs: `docs/complex_strategy_tuning_lab/`

Removed path:

- File-based strategy config registry under the old `strategies` directory

Rules going forward:

1. Put complex strategy logic in Python first.
2. Promote only validated rules into the engine or a dedicated Python service.
3. Do not reintroduce file-based strategy configs as the source of backtest behavior.
4. Keep Backtest results traceable to executable code and generated trade logs.
