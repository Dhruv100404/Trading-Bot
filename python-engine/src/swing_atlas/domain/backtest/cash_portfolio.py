"""Cash-constrained portfolio replay -- mirrors engine/src/api/backtest.rs's
simulate_cash_strategy/close_cash_positions/build_cash_analytics.

Deliberately ported as a direct line-by-line translation of the Rust control flow
(dataclasses + plain loops, not vectorized with pandas/numpy) per the migration
plan -- a structural match is the easiest thing to diff against the Rust original
for parity, and vectorizing is a fine follow-up only after that parity is confirmed.

Takes already-computed trades (from ClickHouse's SQL entry/exit simulation) and
replays them chronologically against a shared cash pool to see what's actually
achievable with limited capital -- it does not re-run entry/exit logic itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from swing_atlas.domain.backtest.file_trades import MONTH_LABELS, stddev_pop
from swing_atlas.domain.backtest.models import (
    BacktestAnalysisTrade,
    BacktestCashEquityPoint,
    BacktestCashMonthlyReturn,
    BacktestCashProfile,
)
from swing_atlas.domain.backtest.strategy_specs import (
    BACKTEST_CASH_ACCOUNT_CAPITAL,
    CASH_PORTFOLIO_STRATEGY_ID,
)
from swing_atlas.domain.numeric import round2, round3


@dataclass
class _CashMonthAccumulator:
    trades_closed: int = 0
    entries_taken: int = 0
    skipped_entries: int = 0
    pnl: float = 0.0
    ending_equity: float = 0.0
    max_drawdown_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class CashSimulationResult:
    profile: BacktestCashProfile
    monthly_returns: list[BacktestCashMonthlyReturn]
    equity_curve: list[BacktestCashEquityPoint]


def build_cash_analytics(
    trades: list[BacktestAnalysisTrade],
) -> tuple[
    list[BacktestCashProfile], list[BacktestCashMonthlyReturn], list[BacktestCashEquityPoint]
]:
    clean_trades = [t for t in trades if t.capital_used > 0.0 and t.entry_date <= t.exit_date]
    if not clean_trades:
        return [], [], []

    simulations = [
        simulate_cash_strategy(CASH_PORTFOLIO_STRATEGY_ID, "Cash Portfolio", clean_trades)
    ]

    grouped: dict[str, list[BacktestAnalysisTrade]] = {}
    for trade in clean_trades:
        grouped.setdefault(trade.strategy_id, []).append(trade)
    for strategy_id in sorted(grouped):
        rows = grouped[strategy_id]
        method_family = rows[0].method_family if rows else "Other"
        simulations.append(simulate_cash_strategy(strategy_id, method_family, rows))

    profiles = [s.profile for s in simulations]
    monthly_returns = [row for s in simulations for row in s.monthly_returns]
    equity_curve = [row for s in simulations for row in s.equity_curve]

    def _profile_sort_key(p: BacktestCashProfile) -> tuple[int, float, str]:
        if p.strategy_id == CASH_PORTFOLIO_STRATEGY_ID:
            return (0, 0.0, "")
        return (1, -p.return_pct, p.strategy_id)

    profiles.sort(key=_profile_sort_key)
    monthly_returns.sort(key=lambda r: (r.strategy_id, r.year, r.month))
    equity_curve.sort(key=lambda r: (r.strategy_id, r.trade_date))

    return profiles, monthly_returns, equity_curve


def _close_cash_positions(
    open_positions: list[BacktestAnalysisTrade],
    on_date: date,
    include_same_day: bool,
    cash: float,
    realized_today: float,
    closed_today: int,
) -> tuple[list[BacktestAnalysisTrade], float, float, int]:
    still_open = []
    for trade in open_positions:
        should_close = trade.exit_date <= on_date if include_same_day else trade.exit_date < on_date
        if should_close:
            cash += trade.capital_used + trade.pnl
            realized_today += trade.pnl
            closed_today += 1
        else:
            still_open.append(trade)
    return still_open, cash, realized_today, closed_today


def simulate_cash_strategy(
    strategy_id: str, method_family: str, trades: list[BacktestAnalysisTrade]
) -> CashSimulationResult:
    initial_capital = BACKTEST_CASH_ACCOUNT_CAPITAL
    entry_by_date: dict[date, list[BacktestAnalysisTrade]] = {}
    event_dates: set[date] = set()
    first_entry: date | None = None
    last_exit: date | None = None

    for trade in trades:
        entry_by_date.setdefault(trade.entry_date, []).append(trade)
        event_dates.add(trade.entry_date)
        event_dates.add(trade.exit_date)
        first_entry = (
            trade.entry_date if first_entry is None else min(first_entry, trade.entry_date)
        )
        last_exit = trade.exit_date if last_exit is None else max(last_exit, trade.exit_date)

    cash = initial_capital
    open_positions: list[BacktestAnalysisTrade] = []
    accepted_trades: list[BacktestAnalysisTrade] = []
    equity_curve: list[BacktestCashEquityPoint] = []
    monthly: dict[tuple[int, int], _CashMonthAccumulator] = {}
    daily_returns: list[float] = []
    cumulative_peak = 0.0
    max_drawdown_rs = 0.0
    max_open_positions = 0
    peak_capital_used = 0.0
    capital_used_pct_sum = 0.0
    event_day_count = 0
    skipped_entries = 0
    cash_blocked_entries = 0
    duplicate_entries_skipped = 0

    for event_date in sorted(event_dates):
        realized_today = 0.0
        closed_today = 0
        entries_taken_today = 0
        skipped_today = 0

        open_positions, cash, realized_today, closed_today = _close_cash_positions(
            open_positions, event_date, False, cash, realized_today, closed_today
        )

        candidates = entry_by_date.pop(event_date, [])
        candidates.sort(
            key=lambda t: (
                -t.score,
                -t.signal_date.toordinal(),
                t.strategy_id,
                t.setup_family,
                t.symbol,
            )
        )

        for candidate in candidates:
            if any(p.symbol == candidate.symbol for p in open_positions):
                duplicate_entries_skipped += 1
                skipped_entries += 1
                skipped_today += 1
                continue
            if candidate.capital_used <= cash + 0.01:
                cash -= candidate.capital_used
                accepted_trades.append(candidate)
                open_positions.append(candidate)
                entries_taken_today += 1
            else:
                cash_blocked_entries += 1
                skipped_entries += 1
                skipped_today += 1

        intraday_capital_used = sum(t.capital_used for t in open_positions)
        intraday_open_positions = len(open_positions)
        open_positions, cash, realized_today, closed_today = _close_cash_positions(
            open_positions, event_date, True, cash, realized_today, closed_today
        )

        capital_used = sum(t.capital_used for t in open_positions)
        equity_value = cash + capital_used
        cumulative_pnl = equity_value - initial_capital
        cumulative_peak = max(cumulative_peak, cumulative_pnl)
        drawdown_rs = cumulative_pnl - cumulative_peak
        max_drawdown_rs = min(max_drawdown_rs, drawdown_rs)
        max_open_positions = max(max_open_positions, intraday_open_positions, len(open_positions))
        peak_day_capital_used = max(intraday_capital_used, capital_used)
        peak_capital_used = max(peak_capital_used, peak_day_capital_used)
        capital_used_pct_sum += 100.0 * peak_day_capital_used / max(initial_capital, 1.0)
        event_day_count += 1
        daily_returns.append(realized_today / max(initial_capital, 1.0))

        key = (event_date.year, event_date.month)
        month = monthly.setdefault(key, _CashMonthAccumulator())
        month.trades_closed += closed_today
        month.entries_taken += entries_taken_today
        month.skipped_entries += skipped_today
        month.pnl += realized_today
        month.ending_equity = equity_value
        month.max_drawdown_pct = min(
            month.max_drawdown_pct, 100.0 * drawdown_rs / max(initial_capital, 1.0)
        )

        equity_curve.append(
            BacktestCashEquityPoint(
                strategy_id=strategy_id,
                trade_date=event_date.isoformat(),
                realized_pnl=round2(realized_today),
                cumulative_pnl=round2(cumulative_pnl),
                equity_value=round2(equity_value),
                drawdown_rs=round2(drawdown_rs),
                return_pct=round3(100.0 * cumulative_pnl / max(initial_capital, 1.0)),
                open_positions=len(open_positions),
                capital_used=round2(capital_used),
                cash_available=round2(cash),
                entries_taken=entries_taken_today,
                skipped_entries=skipped_today,
            )
        )

    total_pnl = equity_curve[-1].cumulative_pnl if equity_curve else 0.0
    total_return = total_pnl / max(initial_capital, 1.0)
    span_days = (
        max((last_exit - first_entry).days, 1)
        if first_entry is not None and last_exit is not None
        else 1.0
    )
    annualized_return_pct = (
        -100.0
        if total_return <= -0.999
        else ((1.0 + total_return) ** (365.25 / span_days) - 1.0) * 100.0
    )

    wins = sum(1 for t in accepted_trades if t.pnl > 0.0)
    gross_profit = sum(t.pnl for t in accepted_trades if t.pnl > 0.0)
    gross_loss = abs(sum(t.pnl for t in accepted_trades if t.pnl < 0.0))
    downside_returns = [v for v in daily_returns if v < 0.0]
    avg_daily_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    daily_std = stddev_pop(daily_returns)
    downside_std = stddev_pop(downside_returns)
    monthly_pnls = [row.pnl for row in monthly.values()]
    positive_months_pct = (
        0.0
        if not monthly_pnls
        else 100.0 * sum(1 for pnl in monthly_pnls if pnl > 0.0) / len(monthly_pnls)
    )

    monthly_returns = [
        BacktestCashMonthlyReturn(
            strategy_id=strategy_id,
            year=year,
            month=month_num,
            month_label=MONTH_LABELS[month_num - 1],
            trades_closed=row.trades_closed,
            entries_taken=row.entries_taken,
            skipped_entries=row.skipped_entries,
            pnl=round2(row.pnl),
            return_pct=round3(100.0 * row.pnl / max(initial_capital, 1.0)),
            ending_equity=round2(row.ending_equity),
            max_drawdown_pct=round2(row.max_drawdown_pct),
        )
        for (year, month_num), row in monthly.items()
    ]

    profile = BacktestCashProfile(
        strategy_id=strategy_id,
        method_family=method_family,
        initial_capital=round2(initial_capital),
        candidate_trades=len(trades),
        trades_taken=len(accepted_trades),
        skipped_entries=skipped_entries,
        cash_blocked_entries=cash_blocked_entries,
        duplicate_entries_skipped=duplicate_entries_skipped,
        total_pnl=round2(total_pnl),
        return_pct=round3(100.0 * total_return),
        annualized_return_pct=round2(annualized_return_pct),
        win_rate=round2(0.0 if not accepted_trades else 100.0 * wins / len(accepted_trades)),
        profit_factor=round2(
            (99.0 if gross_profit > 0.0 else 0.0)
            if gross_loss == 0.0
            else gross_profit / gross_loss
        ),
        sharpe_ratio=round2(0.0 if daily_std == 0.0 else avg_daily_return / daily_std * 252.0**0.5),
        sortino_ratio=round2(
            0.0 if downside_std == 0.0 else avg_daily_return / downside_std * 252.0**0.5
        ),
        max_drawdown_rs=round2(max_drawdown_rs),
        max_drawdown_pct=round2(100.0 * max_drawdown_rs / max(initial_capital, 1.0)),
        recovery_factor=round2(
            (99.0 if total_pnl > 0.0 else 0.0)
            if max_drawdown_rs == 0.0
            else total_pnl / abs(max_drawdown_rs)
        ),
        max_losing_streak=_max_losing_streak_analysis(accepted_trades),
        positive_months_pct=round2(positive_months_pct),
        max_open_positions=max_open_positions,
        peak_capital_used=round2(peak_capital_used),
        peak_capital_used_pct=round2(100.0 * peak_capital_used / max(initial_capital, 1.0)),
        avg_capital_used_pct=round2(
            0.0 if event_day_count == 0 else capital_used_pct_sum / event_day_count
        ),
        from_date=first_entry.isoformat() if first_entry is not None else "",
        to_date=last_exit.isoformat() if last_exit is not None else "",
    )

    return CashSimulationResult(
        profile=profile, monthly_returns=monthly_returns, equity_curve=equity_curve
    )


def _max_losing_streak_analysis(rows: list[BacktestAnalysisTrade]) -> int:
    ordered = sorted(rows, key=lambda t: (t.exit_date, t.entry_date, t.strategy_id, t.symbol))
    current = best = 0
    for trade in ordered:
        if trade.pnl <= 0.0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
