"""Backtest dashboard row shapes -- mirrors the structs in engine/src/api/backtest.rs.

Shared between ClickHouse-fetched rows (repositories/backtest_repo.py) and the
4 file-backed strategies (domain/backtest/file_trades.py) so both sources can be
blended into one dashboard response with an identical shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class BacktestRunSummary:
    strategy_id: str
    strategy_name: str
    total_trades: int
    win_rate: float
    avg_return_pct: float
    total_pnl: float
    deployed_return_pct: float
    avg_hold_sessions: float
    tp_exits: int
    sl_exits: int
    time_exits: int
    rsi_exits: int
    from_date: str
    to_date: str


@dataclass(frozen=True, slots=True)
class BacktestYearlyReturn:
    strategy_id: str
    year: int
    trades: int
    win_rate: float
    avg_return_pct: float
    pnl: float
    return_pct: float


@dataclass(frozen=True, slots=True)
class BacktestMonthlyReturn:
    strategy_id: str
    year: int
    month: int
    month_label: str
    trades: int
    win_rate: float
    pnl: float
    return_pct: float


@dataclass(frozen=True, slots=True)
class BacktestEquityPoint:
    strategy_id: str
    trade_date: str
    daily_pnl: float
    cumulative_pnl: float
    drawdown_rs: float
    cumulative_return_pct: float


@dataclass(frozen=True, slots=True)
class BacktestSymbolResult:
    strategy_id: str
    symbol: str
    trades: int
    win_rate: float
    pnl: float
    avg_return_pct: float


@dataclass(frozen=True, slots=True)
class BacktestTradeLogRow:
    strategy_id: str
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    setup_family: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float
    exit_reason: str
    hold_sessions: int
    score: int


@dataclass(frozen=True, slots=True)
class BacktestDateSummary:
    trade_date: str
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    total_pnl: float
    avg_return_pct: float
    best_symbol: str
    best_pnl: float
    worst_symbol: str
    worst_pnl: float


@dataclass(frozen=True, slots=True)
class BacktestDateStrategySummary:
    strategy_id: str
    setup_family: str
    trades: int
    win_rate: float
    pnl: float
    best_symbol: str
    best_pnl: float
    worst_symbol: str
    worst_pnl: float


@dataclass(frozen=True, slots=True)
class BacktestDayQuality:
    strategy_id: str
    trading_days: int
    positive_days_pct: float
    worst_day: float
    best_day: float
    max_drawdown_rs: float


@dataclass(frozen=True, slots=True)
class BacktestStrategyDiagnostic:
    strategy_id: str
    method_family: str
    total_trades: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    expectancy_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_win_pct: float
    avg_loss_pct: float
    payoff_ratio: float
    max_losing_streak: int
    recovery_factor: float
    positive_months_pct: float
    median_monthly_pnl: float
    worst_month: float
    best_month: float
    max_drawdown_rs: float
    stability_score: float
    status: str


@dataclass(frozen=True, slots=True)
class BacktestCashProfile:
    strategy_id: str
    method_family: str
    initial_capital: float
    candidate_trades: int
    trades_taken: int
    skipped_entries: int
    cash_blocked_entries: int
    duplicate_entries_skipped: int
    total_pnl: float
    return_pct: float
    annualized_return_pct: float
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_rs: float
    max_drawdown_pct: float
    recovery_factor: float
    max_losing_streak: int
    positive_months_pct: float
    max_open_positions: int
    peak_capital_used: float
    peak_capital_used_pct: float
    avg_capital_used_pct: float
    from_date: str
    to_date: str


@dataclass(frozen=True, slots=True)
class BacktestCashMonthlyReturn:
    strategy_id: str
    year: int
    month: int
    month_label: str
    trades_closed: int
    entries_taken: int
    skipped_entries: int
    pnl: float
    return_pct: float
    ending_equity: float
    max_drawdown_pct: float


@dataclass(frozen=True, slots=True)
class BacktestCashEquityPoint:
    strategy_id: str
    trade_date: str
    realized_pnl: float
    cumulative_pnl: float
    equity_value: float
    drawdown_rs: float
    return_pct: float
    open_positions: int
    capital_used: float
    cash_available: float
    entries_taken: int
    skipped_entries: int


@dataclass(frozen=True, slots=True)
class BacktestCacheStatus:
    cached_rows: int
    symbols: int
    from_date: str
    to_date: str
    refreshed_at: str


@dataclass(frozen=True, slots=True)
class BacktestAnalysisTrade:
    """Slim per-trade record the cash-portfolio simulation replays -- mirrors
    engine/src/api/backtest.rs's BacktestAnalysisTrade."""

    strategy_id: str
    method_family: str
    symbol: str
    signal_date: date
    entry_date: date
    exit_date: date
    setup_family: str
    capital_used: float
    pnl: float
    score: int
