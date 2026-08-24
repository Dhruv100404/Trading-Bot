"""File-backed backtest strategies -- mirrors engine/src/api/backtest.rs's
FileBacktestTrade parsing and file_summaries/file_yearly_returns/etc. aggregation.

These 4 strategies are permanently sourced from static CSVs the Python research
labs produce (docs/complex_strategy_tuning_lab, docs/king_supertrend_lab), never
from trading.backtest_trades -- the dashboard blends them in at read time.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from swing_atlas.domain.backtest.models import (
    BacktestDayQuality,
    BacktestEquityPoint,
    BacktestMonthlyReturn,
    BacktestRunSummary,
    BacktestStrategyDiagnostic,
    BacktestSymbolResult,
    BacktestTradeLogRow,
    BacktestYearlyReturn,
)
from swing_atlas.domain.backtest.strategy_specs import (
    BACKTEST_CAPITAL_PER_TRADE,
    BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
    MIN_BACKTEST_TRADES_FOR_VALIDATION,
    is_deprecated_backtest_strategy,
)
from swing_atlas.domain.numeric import round2, round3, round_half_away_from_zero

_ACTIVE_CAPITAL = BACKTEST_CAPITAL_PER_TRADE * BACKTEST_MAX_NEW_POSITIONS_PER_DAY

MONTH_LABELS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class FileStrategySource:
    strategy_id: str
    strategy_name: str
    setup_family: str
    method_family: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class FileBacktestTrade:
    strategy_id: str
    strategy_name: str
    setup_family: str
    method_family: str
    symbol: str
    signal_date: date
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    capital_used: float
    pnl: float
    return_pct: float
    exit_reason: str
    hold_sessions: int
    score: int


def file_strategy_sources() -> list[FileStrategySource]:
    return [
        FileStrategySource(
            strategy_id="tuned-panic-reversal-v1",
            strategy_name="Panic Reversal Lab",
            setup_family="Panic Reversal",
            method_family="Panic Reversal",
            relative_path="docs/complex_strategy_tuning_lab/panic_best_trades.csv",
        ),
        FileStrategySource(
            strategy_id="weekly-supertrend-10-3",
            strategy_name="Weekly Supertrend 10-3",
            setup_family="Weekly Supertrend",
            method_family="Weekly Supertrend",
            relative_path="docs/king_supertrend_lab/weekly_supertrend_trades.csv",
        ),
        FileStrategySource(
            strategy_id="king-candle-supertrend-breakout-v1",
            strategy_name="King Candle Supertrend Breakout",
            setup_family="King Candle",
            method_family="King Candle",
            relative_path="docs/king_supertrend_lab/king_candle_trades.csv",
        ),
        FileStrategySource(
            strategy_id="king-candle-quality-v1",
            strategy_name="King Candle Quality",
            setup_family="King Candle Quality",
            method_family="King Candle",
            relative_path="docs/king_supertrend_lab/king_candle_quality_trades.csv",
        ),
    ]


def parse_csv_date(value: str) -> date | None:
    text = value[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_csv_f64(value: str) -> float | None:
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_csv_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "y")


def _csv_field(record: dict[str, str], name: str) -> str | None:
    value = record.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_file_exit_reason(raw: str, hit_target: bool) -> str:
    reason = raw.lower()
    if hit_target or "target" in reason or "tp" in reason:
        return "TP"
    if "stop" in reason or "sl" in reason:
        return "SL"
    if "rsi" in reason:
        return "RSI40"
    return "TIME"


def parse_file_backtest_trade(
    source: FileStrategySource, record: dict[str, str]
) -> FileBacktestTrade | None:
    symbol = _csv_field(record, "symbol")
    if symbol is None:
        return None

    signal_date_raw = _csv_field(record, "signal_date") or _csv_field(record, "signal_week")
    if signal_date_raw is None:
        return None
    signal_date = parse_csv_date(signal_date_raw)
    if signal_date is None:
        return None

    entry_date_raw = _csv_field(record, "entry_date")
    entry_date = parse_csv_date(entry_date_raw) if entry_date_raw else None
    if entry_date is None:
        return None

    exit_date_raw = _csv_field(record, "exit_date")
    exit_date = parse_csv_date(exit_date_raw) if exit_date_raw else None
    if exit_date is None:
        return None

    entry_raw = _csv_field(record, "entry")
    entry_price = parse_csv_f64(entry_raw) if entry_raw else None
    if entry_price is None or entry_price <= 0.0:
        return None

    net_return_raw = _csv_field(record, "net_return") or _csv_field(record, "gross_return")
    if net_return_raw is None:
        return None
    net_return = parse_csv_f64(net_return_raw)
    if net_return is None:
        return None

    exit_raw = _csv_field(record, "exit")
    parsed_exit = parse_csv_f64(exit_raw) if exit_raw else None
    exit_price = (
        parsed_exit
        if parsed_exit is not None and parsed_exit > 0.0
        else entry_price * (1.0 + net_return)
    )

    quantity = max(1, int(BACKTEST_CAPITAL_PER_TRADE // entry_price))
    capital_used = quantity * entry_price
    pnl = capital_used * net_return

    raw_reason = _csv_field(record, "exit_reason") or "TIME"
    hit_target1 = _csv_field(record, "hit_target1")
    hit_target2 = _csv_field(record, "hit_target2")
    hit_target = (hit_target1 is not None and parse_csv_bool(hit_target1)) or (
        hit_target2 is not None and parse_csv_bool(hit_target2)
    )

    hold_raw = _csv_field(record, "hold_days") or _csv_field(record, "hold_weeks")
    hold_value = parse_csv_f64(hold_raw) if hold_raw else None
    hold_sessions = (
        max(1, min(65535, round_half_away_from_zero(hold_value))) if hold_value is not None else 1
    )

    score_raw = _csv_field(record, "rank_score")
    score_value = parse_csv_f64(score_raw) if score_raw else None
    score = (
        max(1, min(100, round_half_away_from_zero(score_value * 5.0)))
        if score_value is not None
        else 50
    )

    return FileBacktestTrade(
        strategy_id=source.strategy_id,
        strategy_name=source.strategy_name,
        setup_family=source.setup_family,
        method_family=source.method_family,
        symbol=symbol,
        signal_date=signal_date,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=round2(entry_price),
        exit_price=round2(exit_price),
        quantity=quantity,
        capital_used=capital_used,
        pnl=pnl,
        return_pct=net_return * 100.0,
        exit_reason=normalize_file_exit_reason(raw_reason, hit_target),
        hold_sessions=hold_sessions,
        score=score,
    )


def grouped_file_trades(trades: list[FileBacktestTrade]) -> dict[str, list[FileBacktestTrade]]:
    grouped: dict[str, list[FileBacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.strategy_id].append(trade)
    return dict(sorted(grouped.items()))


def stddev_pop(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return float(variance**0.5)


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.median(values)


def max_losing_streak(rows: list[FileBacktestTrade]) -> int:
    ordered = sorted(rows, key=lambda t: (t.entry_date, t.symbol))
    current = best = 0
    for trade in ordered:
        if trade.pnl <= 0.0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def strategy_status_rank_for_dashboard(status: str) -> int:
    return {"Candidate": 0, "Watch": 1, "Fragile": 2, "Rejected": 3}.get(status, 4)


def file_summaries(trades: list[FileBacktestTrade]) -> list[BacktestRunSummary]:
    out = []
    for rows in grouped_file_trades(trades).values():
        first = rows[0]
        total_pnl = sum(t.pnl for t in rows)
        total_capital = sum(t.capital_used for t in rows)
        out.append(
            BacktestRunSummary(
                strategy_id=first.strategy_id,
                strategy_name=first.strategy_name,
                total_trades=len(rows),
                win_rate=round2(100.0 * sum(1 for t in rows if t.pnl > 0.0) / len(rows)),
                avg_return_pct=round3(sum(t.return_pct for t in rows) / len(rows)),
                total_pnl=round2(total_pnl),
                deployed_return_pct=round3(100.0 * total_pnl / max(total_capital, 1.0)),
                avg_hold_sessions=round2(sum(t.hold_sessions for t in rows) / len(rows)),
                tp_exits=sum(1 for t in rows if t.exit_reason == "TP"),
                sl_exits=sum(1 for t in rows if t.exit_reason == "SL"),
                time_exits=sum(1 for t in rows if t.exit_reason == "TIME"),
                rsi_exits=sum(1 for t in rows if t.exit_reason == "RSI40"),
                from_date=min(t.entry_date for t in rows).isoformat(),
                to_date=max(t.exit_date for t in rows).isoformat(),
            )
        )
    return out


def file_yearly_returns(trades: list[FileBacktestTrade]) -> list[BacktestYearlyReturn]:
    grouped: dict[tuple[str, int], list[FileBacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.strategy_id, trade.entry_date.year)].append(trade)

    out = []
    for (strategy_id, year), rows in sorted(grouped.items()):
        pnl = sum(t.pnl for t in rows)
        out.append(
            BacktestYearlyReturn(
                strategy_id=strategy_id,
                year=year,
                trades=len(rows),
                win_rate=round2(100.0 * sum(1 for t in rows if t.pnl > 0.0) / len(rows)),
                avg_return_pct=round3(sum(t.return_pct for t in rows) / len(rows)),
                pnl=round2(pnl),
                return_pct=round3(100.0 * pnl / max(_ACTIVE_CAPITAL, 1.0)),
            )
        )
    return out


def file_monthly_returns(trades: list[FileBacktestTrade]) -> list[BacktestMonthlyReturn]:
    grouped: dict[tuple[str, int, int], list[FileBacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.strategy_id, trade.entry_date.year, trade.entry_date.month)].append(trade)

    out = []
    for (strategy_id, year, month), rows in sorted(grouped.items()):
        pnl = sum(t.pnl for t in rows)
        out.append(
            BacktestMonthlyReturn(
                strategy_id=strategy_id,
                year=year,
                month=month,
                month_label=MONTH_LABELS[month - 1],
                trades=len(rows),
                win_rate=round2(100.0 * sum(1 for t in rows if t.pnl > 0.0) / len(rows)),
                pnl=round2(pnl),
                return_pct=round3(100.0 * pnl / max(_ACTIVE_CAPITAL, 1.0)),
            )
        )
    return out


def file_equity_curve(trades: list[FileBacktestTrade]) -> list[BacktestEquityPoint]:
    daily: dict[tuple[str, date], float] = defaultdict(float)
    for trade in trades:
        daily[(trade.strategy_id, trade.entry_date)] += trade.pnl

    out = []
    current_strategy = ""
    cumulative = 0.0
    peak = 0.0
    for (strategy_id, trade_date), daily_pnl in sorted(daily.items()):
        if strategy_id != current_strategy:
            current_strategy = strategy_id
            cumulative = 0.0
            peak = 0.0
        cumulative += daily_pnl
        peak = max(peak, cumulative)
        out.append(
            BacktestEquityPoint(
                strategy_id=strategy_id,
                trade_date=trade_date.isoformat(),
                daily_pnl=round2(daily_pnl),
                cumulative_pnl=round2(cumulative),
                drawdown_rs=round2(cumulative - peak),
                cumulative_return_pct=round3(100.0 * cumulative / max(_ACTIVE_CAPITAL, 1.0)),
            )
        )
    return out


def file_day_quality(trades: list[FileBacktestTrade]) -> list[BacktestDayQuality]:
    daily: dict[tuple[str, date], float] = defaultdict(float)
    for trade in trades:
        daily[(trade.strategy_id, trade.entry_date)] += trade.pnl

    by_strategy: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for (strategy_id, trade_date), pnl in daily.items():
        by_strategy[strategy_id].append((trade_date, pnl))

    out = []
    for strategy_id, rows in sorted(by_strategy.items()):
        rows.sort(key=lambda r: r[0])
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for _, pnl in rows:
            cumulative += pnl
            peak = max(peak, cumulative)
            max_drawdown = min(max_drawdown, cumulative - peak)
        out.append(
            BacktestDayQuality(
                strategy_id=strategy_id,
                trading_days=len(rows),
                positive_days_pct=round2(
                    100.0 * sum(1 for _, pnl in rows if pnl > 0.0) / len(rows)
                ),
                worst_day=round2(min(pnl for _, pnl in rows)),
                best_day=round2(max(pnl for _, pnl in rows)),
                max_drawdown_rs=round2(max_drawdown),
            )
        )
    return out


def file_diagnostics(trades: list[FileBacktestTrade]) -> list[BacktestStrategyDiagnostic]:
    monthly = file_monthly_returns(trades)
    monthly_by_strategy: dict[str, list[float]] = defaultdict(list)
    for row in monthly:
        monthly_by_strategy[row.strategy_id].append(row.pnl)
    dd_by_strategy = {row.strategy_id: row.max_drawdown_rs for row in file_day_quality(trades)}

    out = []
    for rows in grouped_file_trades(trades).values():
        first = rows[0]
        total_pnl = sum(t.pnl for t in rows)
        wins = [t.pnl for t in rows if t.pnl > 0.0]
        losses = [t.pnl for t in rows if t.pnl < 0.0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        win_returns = [t.return_pct for t in rows if t.return_pct > 0.0]
        loss_returns = [t.return_pct for t in rows if t.return_pct < 0.0]

        months = monthly_by_strategy.get(first.strategy_id, [])
        positive_months_pct = (
            0.0 if not months else 100.0 * sum(1 for pnl in months if pnl > 0.0) / len(months)
        )
        worst_month = min(months) if months else 0.0
        best_month = max(months) if months else 0.0
        max_drawdown_rs = dd_by_strategy.get(first.strategy_id, 0.0)

        daily: dict[date, float] = defaultdict(float)
        for trade in rows:
            daily[trade.entry_date] += trade.pnl
        daily_returns = [pnl / max(_ACTIVE_CAPITAL, 1.0) for pnl in daily.values()]
        downside_returns = [v for v in daily_returns if v < 0.0]
        avg_daily_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
        daily_std = stddev_pop(daily_returns)
        downside_std = stddev_pop(downside_returns)

        first_entry = min(t.entry_date for t in rows)
        last_exit = max(t.exit_date for t in rows)
        span_days = max((last_exit - first_entry).days, 1)
        total_return = total_pnl / max(_ACTIVE_CAPITAL, 1.0)
        annualized_return_pct = (
            -100.0
            if total_return <= -0.999
            else ((1.0 + total_return) ** (365.25 / span_days) - 1.0) * 100.0
        )

        avg_win_pct = sum(win_returns) / len(win_returns) if win_returns else 0.0
        avg_loss_pct = sum(loss_returns) / len(loss_returns) if loss_returns else 0.0
        payoff_ratio = (
            (99.0 if avg_win_pct > 0.0 else 0.0)
            if avg_loss_pct == 0.0
            else avg_win_pct / abs(avg_loss_pct)
        )
        recovery_factor = (
            (99.0 if total_pnl > 0.0 else 0.0)
            if max_drawdown_rs == 0.0
            else total_pnl / abs(max_drawdown_rs)
        )
        profit_factor = (
            (99.0 if gross_profit > 0.0 else 0.0)
            if gross_loss == 0.0
            else gross_profit / gross_loss
        )
        win_rate = 100.0 * len(wins) / len(rows)
        raw_stability = max(
            0.0,
            min(
                100.0,
                positive_months_pct * 0.42
                + win_rate * 0.28
                + min(profit_factor, 2.25) * 8.0
                + (8.0 if total_pnl > 0.0 else -18.0)
                - min(abs(max_drawdown_rs) / max(abs(total_pnl), 1.0) * 12.0, 22.0),
            ),
        )
        if len(rows) < MIN_BACKTEST_TRADES_FOR_VALIDATION:
            status = "Fragile"
        elif total_pnl <= 0.0:
            status = "Rejected"
        elif raw_stability >= 56.0 and positive_months_pct >= 55.0:
            status = "Candidate"
        elif raw_stability >= 50.0:
            status = "Watch"
        else:
            status = "Fragile"

        out.append(
            BacktestStrategyDiagnostic(
                strategy_id=first.strategy_id,
                method_family=first.method_family,
                total_trades=len(rows),
                total_pnl=round2(total_pnl),
                win_rate=round2(win_rate),
                profit_factor=round2(profit_factor),
                expectancy_pct=round3(sum(t.return_pct for t in rows) / len(rows)),
                annualized_return_pct=round2(annualized_return_pct),
                max_drawdown_pct=round2(100.0 * max_drawdown_rs / max(_ACTIVE_CAPITAL, 1.0)),
                sharpe_ratio=round2(
                    0.0 if daily_std == 0.0 else avg_daily_return / daily_std * 252.0**0.5
                ),
                sortino_ratio=round2(
                    0.0 if downside_std == 0.0 else avg_daily_return / downside_std * 252.0**0.5
                ),
                avg_win_pct=round3(avg_win_pct),
                avg_loss_pct=round3(avg_loss_pct),
                payoff_ratio=round2(payoff_ratio),
                max_losing_streak=max_losing_streak(rows),
                recovery_factor=round2(recovery_factor),
                positive_months_pct=round2(positive_months_pct),
                median_monthly_pnl=round2(median(months)),
                worst_month=round2(worst_month),
                best_month=round2(best_month),
                max_drawdown_rs=round2(max_drawdown_rs),
                stability_score=round2(raw_stability),
                status=status,
            )
        )
    return out


def file_symbol_results(
    trades: list[FileBacktestTrade], losers: bool
) -> list[BacktestSymbolResult]:
    grouped: dict[tuple[str, str], list[FileBacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.strategy_id, trade.symbol)].append(trade)

    by_strategy: dict[str, list[BacktestSymbolResult]] = defaultdict(list)
    for (strategy_id, symbol), rows in sorted(grouped.items()):
        if len(rows) < 5:
            continue
        pnl = sum(t.pnl for t in rows)
        by_strategy[strategy_id].append(
            BacktestSymbolResult(
                strategy_id=strategy_id,
                symbol=symbol,
                trades=len(rows),
                win_rate=round2(100.0 * sum(1 for t in rows if t.pnl > 0.0) / len(rows)),
                pnl=round2(pnl),
                avg_return_pct=round3(sum(t.return_pct for t in rows) / len(rows)),
            )
        )

    out: list[BacktestSymbolResult] = []
    for strategy_id in sorted(by_strategy):
        results = by_strategy[strategy_id]
        if losers:
            results.sort(key=lambda r: (r.pnl, r.win_rate))
        else:
            results.sort(key=lambda r: (-r.win_rate, -r.avg_return_pct, -r.pnl))
        out.extend(results[:12])
    return out


def file_trade_log(trades: list[FileBacktestTrade]) -> list[BacktestTradeLogRow]:
    rows = [
        BacktestTradeLogRow(
            strategy_id=t.strategy_id,
            symbol=t.symbol,
            signal_date=t.signal_date.isoformat(),
            entry_date=t.entry_date.isoformat(),
            exit_date=t.exit_date.isoformat(),
            setup_family=t.setup_family,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            quantity=t.quantity,
            pnl=round2(t.pnl),
            return_pct=round3(t.return_pct),
            exit_reason=t.exit_reason,
            hold_sessions=t.hold_sessions,
            score=t.score,
        )
        for t in trades
    ]
    rows.sort(key=lambda r: (r.entry_date, abs(r.pnl)), reverse=True)
    return rows[:80]


@dataclass
class DashboardResultSet:
    """The 9 parallel result lists engine/src/api/backtest.rs's build_dashboard
    threads through filter_deprecated_backtest_results/append_file_backtest_results
    as 9 separate &mut Vec params -- bundled here so the merge/filter functions
    below take one argument instead of 9."""

    summaries: list[BacktestRunSummary]
    yearly_returns: list[BacktestYearlyReturn]
    monthly_returns: list[BacktestMonthlyReturn]
    equity_curve: list[BacktestEquityPoint]
    diagnostics: list[BacktestStrategyDiagnostic]
    winners: list[BacktestSymbolResult]
    losers: list[BacktestSymbolResult]
    day_quality: list[BacktestDayQuality]
    trades: list[BacktestTradeLogRow]


def filter_deprecated_backtest_results(result: DashboardResultSet) -> None:
    result.summaries = [
        r for r in result.summaries if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.yearly_returns = [
        r for r in result.yearly_returns if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.monthly_returns = [
        r for r in result.monthly_returns if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.equity_curve = [
        r for r in result.equity_curve if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.diagnostics = [
        r for r in result.diagnostics if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.winners = [
        r for r in result.winners if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.losers = [r for r in result.losers if not is_deprecated_backtest_strategy(r.strategy_id)]
    result.day_quality = [
        r for r in result.day_quality if not is_deprecated_backtest_strategy(r.strategy_id)
    ]
    result.trades = [r for r in result.trades if not is_deprecated_backtest_strategy(r.strategy_id)]


def merge_file_backtest_results(
    result: DashboardResultSet, file_trades: list[FileBacktestTrade]
) -> None:
    """Blends the 4 file-backed strategies into `result` -- their DB-sourced rows
    (if any) are excluded first to avoid double-counting, matching
    engine/src/api/backtest.rs::append_file_backtest_results."""
    if not file_trades:
        return
    file_ids = {t.strategy_id for t in file_trades}

    result.summaries = [r for r in result.summaries if r.strategy_id not in file_ids]
    result.summaries.extend(file_summaries(file_trades))
    result.summaries.sort(key=lambda r: -r.total_pnl)

    result.yearly_returns = [r for r in result.yearly_returns if r.strategy_id not in file_ids]
    result.yearly_returns.extend(file_yearly_returns(file_trades))
    result.yearly_returns.sort(key=lambda r: (r.strategy_id, r.year))

    result.monthly_returns = [r for r in result.monthly_returns if r.strategy_id not in file_ids]
    result.monthly_returns.extend(file_monthly_returns(file_trades))
    result.monthly_returns.sort(key=lambda r: (r.strategy_id, r.year, r.month))

    result.equity_curve = [r for r in result.equity_curve if r.strategy_id not in file_ids]
    result.equity_curve.extend(file_equity_curve(file_trades))
    result.equity_curve.sort(key=lambda r: (r.strategy_id, r.trade_date))

    result.diagnostics = [r for r in result.diagnostics if r.strategy_id not in file_ids]
    result.diagnostics.extend(file_diagnostics(file_trades))
    result.diagnostics.sort(
        key=lambda r: (
            strategy_status_rank_for_dashboard(r.status),
            -r.stability_score,
            -r.total_pnl,
        )
    )

    result.winners = [r for r in result.winners if r.strategy_id not in file_ids]
    result.winners.extend(file_symbol_results(file_trades, losers=False))
    result.winners.sort(key=lambda r: r.strategy_id)

    result.losers = [r for r in result.losers if r.strategy_id not in file_ids]
    result.losers.extend(file_symbol_results(file_trades, losers=True))
    result.losers.sort(key=lambda r: r.strategy_id)

    result.day_quality = [r for r in result.day_quality if r.strategy_id not in file_ids]
    result.day_quality.extend(file_day_quality(file_trades))
    result.day_quality.sort(key=lambda r: -r.max_drawdown_rs)

    result.trades = [r for r in result.trades if r.strategy_id not in file_ids]
    result.trades.extend(file_trade_log(file_trades))
    result.trades.sort(key=lambda r: (r.entry_date, abs(r.pnl)), reverse=True)
    result.trades = result.trades[:160]


def fill_missing_monthly_returns(
    monthly_returns: list[BacktestMonthlyReturn],
) -> list[BacktestMonthlyReturn]:
    """The source query naturally returns only months that had entries. Fill the
    gaps between a strategy's first and last observed trade so the dashboard can
    distinguish a flat, zero-trade month from missing backtest data."""
    grouped: dict[str, dict[tuple[int, int], BacktestMonthlyReturn]] = defaultdict(dict)
    for row in monthly_returns:
        grouped[row.strategy_id][(row.year, row.month)] = row

    filled: list[BacktestMonthlyReturn] = []
    for strategy_id in sorted(grouped):
        rows = grouped[strategy_id]
        keys = sorted(rows.keys())
        if not keys:
            continue
        year, month = keys[0]
        end_year, end_month = keys[-1]
        while True:
            key = (year, month)
            if key in rows:
                filled.append(rows[key])
            else:
                filled.append(
                    BacktestMonthlyReturn(
                        strategy_id=strategy_id,
                        year=year,
                        month=month,
                        month_label=MONTH_LABELS[month - 1],
                        trades=0,
                        win_rate=0.0,
                        pnl=0.0,
                        return_pct=0.0,
                    )
                )
            if (year, month) == (end_year, end_month):
                break
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1

    filled.sort(key=lambda r: (r.strategy_id, r.year, r.month))
    return filled
