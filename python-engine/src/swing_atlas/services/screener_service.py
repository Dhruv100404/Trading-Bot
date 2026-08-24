"""historical-screener / fresh-signals / feature-cache-refresh orchestration --
mirrors the corresponding handler bodies in engine/src/api/swing.rs.

fresh_signals is the one endpoint in this module with a real side effect: it
auto-stages new, deduplicated signals into trading.paper_trades. Everything it
writes goes through the same PaperTradesRepo.upsert_system_trade path a human
clicking "add to paper desk" would use -- there is no separate write path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from swing_atlas.domain.swing.models import HistoricalScreenerRow
from swing_atlas.domain.swing.screener import (
    is_paper_eligible_signal,
    map_historical_screener_row,
    matches_setup_filter,
    matches_strategy_filter,
    paper_rule_for_strategy,
    signal_key_for,
)
from swing_atlas.domain.swing.strategies import strategy_status_rank
from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories import (
    paper_trades_repo,
    screener_feature_cache_repo,
    signal_ledger_repo,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo

logger = logging.getLogger(__name__)

_DEFAULT_MIN_PRICE = 80.0
_DEFAULT_MIN_AVG_VOLUME = 100_000.0


@dataclass(frozen=True, slots=True)
class HistoricalScreenerResult:
    updated_at: str
    range: str
    signal_date: str | None
    total_rows: int
    rows: list[HistoricalScreenerRow]
    message: str | None


@dataclass(frozen=True, slots=True)
class FreshSignalsResult:
    updated_at: str
    signal_date: str | None
    eligible_rows: int
    new_rows: int
    seen_rows: int
    staged_rows: int
    rows: list[HistoricalScreenerRow]
    message: str | None


@dataclass(frozen=True, slots=True)
class FeatureCacheRefreshResult:
    updated_at: str
    data_date: str | None
    cached_rows: int
    message: str


def _sort_key(row: HistoricalScreenerRow) -> tuple[int, int, str]:
    return (strategy_status_rank(row.strategy_status), -row.score, row.symbol)


async def _mapped_screener_rows(
    ch: ClickHouseRepo, min_price: float, min_avg_volume: float
) -> list[HistoricalScreenerRow]:
    """Note: only the feature-row load can fail the caller -- a strategy-status
    lookup failure degrades to an empty map (matching Rust's .unwrap_or_default()),
    it never turns into a 500 on its own in either historical_screener or
    fresh_signals."""
    feature_rows = await screener_feature_cache_repo.load_historical_screener_rows(
        ch, min_price, min_avg_volume
    )
    try:
        strategy_statuses = await screener_feature_cache_repo.load_latest_strategy_statuses(ch)
    except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default() fallback
        logger.warning("latest strategy status lookup failed: %s", exc)
        strategy_statuses = {}
    mapped = (map_historical_screener_row(row, strategy_statuses) for row in feature_rows)
    return [row for row in mapped if row is not None]


async def historical_screener(
    ch: ClickHouseRepo,
    limit: int | None,
    setup: str | None,
    strategy: str | None,
    min_price: float | None,
    min_avg_volume: float | None,
) -> HistoricalScreenerResult:
    resolved_limit = min(max(limit if limit is not None else 40, 10), 120)
    setup_filter = (setup or "all").lower()
    strategy_filter = (strategy or "all").lower()
    resolved_min_price = max(min_price if min_price is not None else _DEFAULT_MIN_PRICE, 1.0)
    resolved_min_avg_volume = max(
        min_avg_volume if min_avg_volume is not None else _DEFAULT_MIN_AVG_VOLUME, 0.0
    )

    try:
        mapped = await _mapped_screener_rows(ch, resolved_min_price, resolved_min_avg_volume)
    except Exception as exc:  # noqa: BLE001 -- always-200-with-inline-error contract
        return HistoricalScreenerResult(
            updated_at=now_ist().isoformat(),
            range="1y",
            signal_date=None,
            total_rows=0,
            rows=[],
            message=f"Historical screener query failed: {exc}",
        )

    filtered = [
        row
        for row in mapped
        if matches_setup_filter(row, setup_filter) and matches_strategy_filter(row, strategy_filter)
    ]
    filtered.sort(key=_sort_key)
    signal_date = filtered[0].as_of if filtered else None
    total_rows = len(filtered)
    return HistoricalScreenerResult(
        updated_at=now_ist().isoformat(),
        range="1y",
        signal_date=signal_date,
        total_rows=total_rows,
        rows=filtered[:resolved_limit],
        message=None,
    )


async def fresh_signals(
    ch: ClickHouseRepo, limit: int | None, min_price: float | None, min_avg_volume: float | None
) -> FreshSignalsResult:
    resolved_limit = min(max(limit if limit is not None else 40, 1), 120)
    resolved_min_price = max(min_price if min_price is not None else _DEFAULT_MIN_PRICE, 1.0)
    resolved_min_avg_volume = max(
        min_avg_volume if min_avg_volume is not None else _DEFAULT_MIN_AVG_VOLUME, 0.0
    )

    await signal_ledger_repo.ensure_signal_ledger(ch)
    await paper_trades_repo.ensure_table(ch)

    mapped = await _mapped_screener_rows(ch, resolved_min_price, resolved_min_avg_volume)
    eligible = [row for row in mapped if is_paper_eligible_signal(row)]
    eligible.sort(key=_sort_key)

    seen_keys = await signal_ledger_repo.load_signal_ledger_keys(ch)
    new_candidates = [row for row in eligible if signal_key_for(row) not in seen_keys]

    active_paper_symbols = await signal_ledger_repo.load_active_paper_symbols(ch)
    fresh_rows = [row for row in new_candidates if row.symbol not in active_paper_symbols][
        :resolved_limit
    ]
    staged_keys = {signal_key_for(row) for row in fresh_rows}

    ledger_rows = []
    for row in new_candidates:
        if signal_key_for(row) in staged_keys:
            paper_status = "staged"
        elif row.symbol in active_paper_symbols:
            paper_status = "already-active"
        else:
            paper_status = "baseline"
        ledger_rows.append(signal_ledger_repo.build_signal_ledger_row(row, paper_status))
    await signal_ledger_repo.insert_signal_ledger_rows(ch, ledger_rows)

    staged_rows = 0
    for row in fresh_rows:
        try:
            await _stage_signal_to_paper(ch, row)
            staged_rows += 1
        except Exception as exc:  # noqa: BLE001 -- one bad signal must not sink the whole batch
            logger.warning("fresh-signals: failed to stage %s to paper: %s", row.symbol, exc)

    signal_date = eligible[0].as_of if eligible else (fresh_rows[0].as_of if fresh_rows else None)
    message = (
        None
        if fresh_rows
        else (
            "No new unique signals. Existing matching signals are already in the ledger or "
            "Paper Desk."
        )
    )

    return FreshSignalsResult(
        updated_at=now_ist().isoformat(),
        signal_date=signal_date,
        eligible_rows=len(eligible),
        new_rows=len(new_candidates),
        seen_rows=len(eligible) - len(new_candidates),
        staged_rows=staged_rows,
        rows=fresh_rows,
        message=message,
    )


async def _stage_signal_to_paper(ch: ClickHouseRepo, row: HistoricalScreenerRow) -> None:
    rule = paper_rule_for_strategy(row.strategy_id)
    if rule is None:
        raise ValueError(f"no paper rule for {row.strategy_id}")
    entry_price, quantity, stop_loss, target_price = signal_ledger_repo.resolve_entry_stop_target(
        row, rule
    )
    trade = paper_trades_repo.PaperTradeRow(
        symbol=row.symbol,
        company_name=row.symbol,
        setup_family=row.strategy_label,
        bias="Long",
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target_price=target_price,
        max_sessions=paper_trades_repo.DEFAULT_PAPER_MAX_SESSIONS,
        capital_allocated=entry_price * quantity,
        expected_hold=f"{paper_trades_repo.DEFAULT_PAPER_MAX_SESSIONS} trading sessions",
        thesis=(
            f"{row.symbol} is a new unique {row.strategy_label} signal from "
            f"{row.as_of} with score {row.score}."
        ),
        notes=(
            f"Auto-staged newest unique signal. signal_date={row.as_of} "
            f"strategy={row.strategy_id} strategy_status={row.strategy_status} "
            f"signal_key={signal_key_for(row)} rule={rule.source} "
            f"stop_pct={rule.stop_loss_pct:.2f} target_pct={rule.take_profit_pct:.2f}"
        ),
        exit_price=None,
        close_reason="",
        realized_pnl=0.0,
        enabled=1,
    )
    await paper_trades_repo.upsert_system_trade(ch, trade)


async def refresh_feature_cache(ch: ClickHouseRepo) -> FeatureCacheRefreshResult:
    await screener_feature_cache_repo.ensure_screener_feature_cache(ch)
    await screener_feature_cache_repo.refresh_screener_feature_cache(ch)
    stats = await screener_feature_cache_repo.latest_feature_cache_stats(ch)

    data_date = stats.data_date if stats is not None and stats.cached_rows > 0 else None
    cached_rows = stats.cached_rows if stats is not None else 0
    return FeatureCacheRefreshResult(
        updated_at=now_ist().isoformat(),
        data_date=data_date,
        cached_rows=cached_rows,
        message=(
            "Daily screener features cached in ClickHouse. RSI is based on close-to-close "
            "gains/losses; volume is stored separately as day volume and volume ratio inputs."
        ),
    )
