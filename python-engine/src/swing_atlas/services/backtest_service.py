"""Backtest dashboard/run orchestration -- mirrors engine/src/api/backtest.rs's
build_dashboard/build_datewise/run/refresh_cache handler bodies.

Every individual ClickHouse fetch in build_dashboard/build_datewise degrades to an
empty default on failure rather than failing the whole response (matching the
Rust originals' pervasive `.unwrap_or_default()`) -- only `run`/`refresh_cache`
propagate real errors, since those can't produce a meaningful response if a step
fails partway through.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass

from swing_atlas.domain.backtest.cash_portfolio import build_cash_analytics
from swing_atlas.domain.backtest.file_trades import (
    DashboardResultSet,
    fill_missing_monthly_returns,
    filter_deprecated_backtest_results,
    merge_file_backtest_results,
)
from swing_atlas.domain.backtest.models import (
    BacktestAnalysisTrade,
    BacktestCacheStatus,
    BacktestCashEquityPoint,
    BacktestCashMonthlyReturn,
    BacktestCashProfile,
    BacktestDateStrategySummary,
    BacktestDateSummary,
    BacktestDayQuality,
    BacktestEquityPoint,
    BacktestMonthlyReturn,
    BacktestRunSummary,
    BacktestStrategyDiagnostic,
    BacktestSymbolResult,
    BacktestTradeLogRow,
    BacktestYearlyReturn,
)
from swing_atlas.domain.backtest.strategy_specs import is_deprecated_backtest_strategy
from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories import backtest_repo
from swing_atlas.repositories.backtest_files_repo import load_file_backtest_trades
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.schema import ensure_backtest_schema

logger = logging.getLogger(__name__)

LATEST_RUN_ID_FALLBACK = "watchlist-swing-20260503-001"


async def _or_default[T](label: str, coro: Awaitable[T], default: T) -> T:
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 -- degrade this one section, not the whole dashboard
        logger.warning("%s failed: %s", label, exc)
        return default


@dataclass(frozen=True, slots=True)
class BacktestDashboard:
    run_id: str
    updated_at: str
    summaries: list[BacktestRunSummary]
    yearly_returns: list[BacktestYearlyReturn]
    monthly_returns: list[BacktestMonthlyReturn]
    equity_curve: list[BacktestEquityPoint]
    diagnostics: list[BacktestStrategyDiagnostic]
    winners: list[BacktestSymbolResult]
    losers: list[BacktestSymbolResult]
    day_quality: list[BacktestDayQuality]
    trades: list[BacktestTradeLogRow]
    cash_profiles: list[BacktestCashProfile]
    cash_monthly_returns: list[BacktestCashMonthlyReturn]
    cash_equity_curve: list[BacktestCashEquityPoint]


@dataclass(frozen=True, slots=True)
class BacktestDatewise:
    run_id: str
    updated_at: str
    selected_date: str | None
    available_dates: list[str]
    strategy_options: list[str]
    summary: BacktestDateSummary | None
    strategy_summaries: list[BacktestDateStrategySummary]
    top_gainers: list[BacktestTradeLogRow]
    top_losers: list[BacktestTradeLogRow]
    rows: list[BacktestTradeLogRow]
    page: int
    page_size: int
    total_rows: int


async def resolve_latest_run_id(ch: ClickHouseRepo) -> str:
    run_id = await _or_default("latest backtest run lookup", backtest_repo.latest_run_id(ch), None)
    return run_id or LATEST_RUN_ID_FALLBACK


async def build_dashboard(ch: ClickHouseRepo, run_id: str) -> BacktestDashboard:
    result = DashboardResultSet(
        summaries=await _or_default(
            "fetch_summaries", backtest_repo.fetch_summaries(ch, run_id), []
        ),
        yearly_returns=await _or_default(
            "fetch_yearly_returns", backtest_repo.fetch_yearly_returns(ch, run_id), []
        ),
        monthly_returns=await _or_default(
            "fetch_monthly_returns", backtest_repo.fetch_monthly_returns(ch, run_id), []
        ),
        equity_curve=await _or_default(
            "fetch_equity_curve", backtest_repo.fetch_equity_curve(ch, run_id), []
        ),
        diagnostics=await _or_default(
            "fetch_strategy_diagnostics", backtest_repo.fetch_strategy_diagnostics(ch, run_id), []
        ),
        winners=await _or_default(
            "fetch_symbol_results(winners)",
            backtest_repo.fetch_symbol_results(ch, run_id, False),
            [],
        ),
        losers=await _or_default(
            "fetch_symbol_results(losers)", backtest_repo.fetch_symbol_results(ch, run_id, True), []
        ),
        day_quality=await _or_default(
            "fetch_day_quality", backtest_repo.fetch_day_quality(ch, run_id), []
        ),
        trades=await _or_default("fetch_trade_log", backtest_repo.fetch_trade_log(ch, run_id), []),
    )
    analysis_trades: list[BacktestAnalysisTrade] = await _or_default(
        "fetch_analysis_trades", backtest_repo.fetch_analysis_trades(ch, run_id), []
    )

    try:
        file_trades = load_file_backtest_trades()
        merge_file_backtest_results(result, file_trades)
    except Exception:
        logger.warning("file-backed backtest result load failed", exc_info=True)
        file_trades = []

    try:
        analysis_trades = analysis_trades + [
            BacktestAnalysisTrade(
                strategy_id=t.strategy_id,
                method_family=t.method_family,
                symbol=t.symbol,
                signal_date=t.signal_date,
                entry_date=t.entry_date,
                exit_date=t.exit_date,
                setup_family=t.setup_family,
                capital_used=t.capital_used,
                pnl=t.pnl,
                score=t.score,
            )
            for t in file_trades
        ]
    except Exception:
        logger.warning("file-backed cash analysis load failed", exc_info=True)

    filter_deprecated_backtest_results(result)
    result.monthly_returns = fill_missing_monthly_returns(result.monthly_returns)
    analysis_trades = [
        t for t in analysis_trades if not is_deprecated_backtest_strategy(t.strategy_id)
    ]
    cash_profiles, cash_monthly_returns, cash_equity_curve = build_cash_analytics(analysis_trades)

    return BacktestDashboard(
        run_id=run_id,
        updated_at=now_ist().isoformat(),
        summaries=result.summaries,
        yearly_returns=result.yearly_returns,
        monthly_returns=result.monthly_returns,
        equity_curve=result.equity_curve,
        diagnostics=result.diagnostics,
        winners=result.winners,
        losers=result.losers,
        day_quality=result.day_quality,
        trades=result.trades,
        cash_profiles=cash_profiles,
        cash_monthly_returns=cash_monthly_returns,
        cash_equity_curve=cash_equity_curve,
    )


async def build_datewise(
    ch: ClickHouseRepo,
    run_id: str,
    requested_date: str | None,
    strategy: str | None,
    page: int,
    page_size: int,
) -> BacktestDatewise:
    available_dates = await _or_default(
        "fetch_available_entry_dates", backtest_repo.fetch_available_entry_dates(ch, run_id), []
    )
    selected_date = requested_date if requested_date in available_dates else None
    if selected_date is None and available_dates:
        selected_date = available_dates[0]

    # Mirrors Rust's Option::filter(|v| !v.trim().is_empty() && !v.eq_ignore_ascii_case("all")):
    # the emptiness/"all" check uses a trimmed/lowercased view, but the value that's
    # actually kept is the ORIGINAL, untrimmed string -- not the trimmed one.
    if strategy is not None and strategy.strip() and strategy.strip().lower() != "all":
        strategy_filter = strategy
    else:
        strategy_filter = "all"

    if selected_date is not None:
        summary = await _or_default(
            "fetch_date_summary",
            backtest_repo.fetch_date_summary(ch, run_id, selected_date, strategy_filter),
            None,
        )
        strategy_summaries = await _or_default(
            "fetch_date_strategy_summaries",
            backtest_repo.fetch_date_strategy_summaries(ch, run_id, selected_date),
            [],
        )
        top_gainers = await _or_default(
            "fetch_date_trades(top_gainers)",
            backtest_repo.fetch_date_trades(
                ch, run_id, selected_date, strategy_filter, "pnl DESC", 5, 0
            ),
            [],
        )
        top_losers = await _or_default(
            "fetch_date_trades(top_losers)",
            backtest_repo.fetch_date_trades(
                ch, run_id, selected_date, strategy_filter, "pnl ASC", 5, 0
            ),
            [],
        )
        offset = (page - 1) * page_size
        rows = await _or_default(
            "fetch_date_trades(rows)",
            backtest_repo.fetch_date_trades(
                ch,
                run_id,
                selected_date,
                strategy_filter,
                "abs(pnl) DESC, symbol ASC",
                page_size,
                offset,
            ),
            [],
        )
        total_rows = await _or_default(
            "fetch_date_trade_count",
            backtest_repo.fetch_date_trade_count(ch, run_id, selected_date, strategy_filter),
            0,
        )
        strategy_options = [row.strategy_id for row in strategy_summaries]
    else:
        summary = None
        strategy_summaries = []
        top_gainers = []
        top_losers = []
        rows = []
        total_rows = 0
        strategy_options = []

    return BacktestDatewise(
        run_id=run_id,
        updated_at=now_ist().isoformat(),
        selected_date=selected_date,
        available_dates=available_dates,
        strategy_options=strategy_options,
        summary=summary,
        strategy_summaries=strategy_summaries,
        top_gainers=top_gainers,
        top_losers=top_losers,
        rows=rows,
        page=page,
        page_size=page_size,
        total_rows=total_rows,
    )


async def run_backtest(
    ch: ClickHouseRepo,
) -> tuple[str, str, BacktestCacheStatus, BacktestDashboard]:
    """Raises on any failure -- unlike the dashboard/datewise reads, this endpoint
    can't produce a meaningful response if a step fails partway through."""
    await ensure_backtest_schema(ch)
    await backtest_repo.refresh_backtest_feature_cache(ch)

    run_id = f"watchlist-swing-{now_ist().strftime('%Y%m%d-%H%M%S')}"
    await backtest_repo.execute_backtest_run(ch, run_id)

    count_rows = await ch.query_rows(
        "SELECT count() AS cnt FROM trading.backtest_trades WHERE run_id = %(run_id)s",
        {"run_id": run_id},
    )
    stored_trade_count = int(count_rows[0]["cnt"]) if count_rows else 0
    message = f"Backtest completed with {stored_trade_count} stored database trades."

    dashboard = await build_dashboard(ch, run_id)
    cache = await _or_default(
        "backtest_cache_status",
        backtest_repo.backtest_cache_status(ch),
        BacktestCacheStatus(cached_rows=0, symbols=0, from_date="", to_date="", refreshed_at=""),
    )
    return run_id, message, cache, dashboard


async def refresh_cache(ch: ClickHouseRepo) -> BacktestCacheStatus:
    """Raises on any failure -- same reasoning as run_backtest."""
    await ensure_backtest_schema(ch)
    await backtest_repo.refresh_backtest_feature_cache(ch)
    return await backtest_repo.backtest_cache_status(ch)
