"""ClickHouse reads/writes for paper trades -- mirrors engine/src/api/paper.rs.

Every write is a plain INSERT into a ReplacingMergeTree(inserted_at) keyed by
symbol: "updating" a trade means inserting a new row for that symbol, and reads
always go through `FINAL` so the latest insert wins. This matches the Rust
original exactly -- there is no UPDATE statement anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.schema import ensure_paper_trades_schema

DEFAULT_PAPER_MAX_SESSIONS = 10


@dataclass
class PaperTrade:
    """Mutable -- enrich_with_live_quotes/hydrate_with_historical_prices update
    current_price/current_value/unrealized_pnl* in place across a batch of trades,
    matching the Rust original's `&mut [PaperTrade]` iteration."""

    symbol: str
    company_name: str
    setup_family: str
    bias: str
    entry_price: float
    quantity: int
    stop_loss: float
    target_price: float
    planned_at: str
    max_sessions: int
    capital_allocated: float
    expected_hold: str
    thesis: str
    notes: str
    exit_price: float | None
    closed_at: str | None
    close_reason: str
    realized_pnl: float
    current_price: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    quote_source: str
    quote_updated_at: str
    enabled: int


@dataclass(frozen=True, slots=True)
class PaperTradeRow:
    """Insert shape -- matches engine/src/api/paper.rs's PaperTradeRow (a strict
    subset of PaperTrade's fields; planned_at/closed_at/inserted_at are left to
    the table's DEFAULT now() / implicit NULL)."""

    symbol: str
    company_name: str
    setup_family: str
    bias: str
    entry_price: float
    quantity: int
    stop_loss: float
    target_price: float
    max_sessions: int
    capital_allocated: float
    expected_hold: str
    thesis: str
    notes: str
    exit_price: float | None
    close_reason: str
    realized_pnl: float
    enabled: int


def _paper_trade_select() -> str:
    return (
        "SELECT symbol, company_name, setup_family, bias, entry_price, quantity, "
        "stop_loss, target_price, "
        "formatDateTime(planned_at, '%Y-%m-%dT%H:%i:%S%z') AS planned_at, "
        "max_sessions, entry_price * quantity AS capital_allocated, expected_hold, thesis, notes, exit_price, "
        "if(close_reason != '' AND close_reason != 'removed', formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%S%z'), "
        "if(isNull(closed_at), NULL, formatDateTime(assumeNotNull(closed_at), '%Y-%m-%dT%H:%i:%S%z'))) AS closed_at, "
        "close_reason, realized_pnl, "
        "if(isNull(exit_price), entry_price, assumeNotNull(exit_price)) AS current_price, "
        "if(isNull(exit_price), entry_price * quantity, assumeNotNull(exit_price) * quantity) AS current_value, "
        "if(isNull(exit_price), 0, realized_pnl) AS unrealized_pnl, "
        "if(entry_price * quantity > 0, if(isNull(exit_price), 0, realized_pnl) / (entry_price * quantity) * 100, 0) AS unrealized_pnl_pct, "
        "if(isNull(exit_price), 'entry', 'closed') AS quote_source, "
        "formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%S%z') AS quote_updated_at, "
        "enabled "
        "FROM trading.paper_trades FINAL"
    )


def _row_to_trade(row: dict[str, object]) -> PaperTrade:
    return PaperTrade(**row)  # type: ignore[arg-type]


async def ensure_table(ch: ClickHouseRepo) -> None:
    await ensure_paper_trades_schema(ch)


async def fetch_visible_paper_trades(ch: ClickHouseRepo) -> list[PaperTrade]:
    query = (
        f"{_paper_trade_select()} WHERE enabled = 1 OR close_reason != 'removed' "
        "ORDER BY inserted_at DESC"
    )
    rows = await ch.query_rows(query)
    return [_row_to_trade(row) for row in rows]


async def fetch_open_trade_by_symbol(ch: ClickHouseRepo, symbol: str) -> PaperTrade | None:
    query = f"{_paper_trade_select()} WHERE symbol = %(symbol)s AND enabled = 1 LIMIT 1"
    rows = await ch.query_rows(query, parameters={"symbol": symbol})
    return _row_to_trade(rows[0]) if rows else None


async def fetch_trade_by_symbol(ch: ClickHouseRepo, symbol: str) -> PaperTrade | None:
    query = f"{_paper_trade_select()} WHERE symbol = %(symbol)s LIMIT 1"
    rows = await ch.query_rows(query, parameters={"symbol": symbol})
    return _row_to_trade(rows[0]) if rows else None


def _row_dict(row: PaperTradeRow) -> dict[str, object]:
    return {
        "symbol": row.symbol,
        "company_name": row.company_name,
        "setup_family": row.setup_family,
        "bias": row.bias,
        "entry_price": row.entry_price,
        "quantity": row.quantity,
        "stop_loss": row.stop_loss,
        "target_price": row.target_price,
        "max_sessions": row.max_sessions,
        "capital_allocated": row.capital_allocated,
        "expected_hold": row.expected_hold,
        "thesis": row.thesis,
        "notes": row.notes,
        "exit_price": row.exit_price,
        "close_reason": row.close_reason,
        "realized_pnl": row.realized_pnl,
        "enabled": row.enabled,
    }


async def insert_trade_row(ch: ClickHouseRepo, row: PaperTradeRow) -> None:
    await ch.insert_rows("trading.paper_trades", [_row_dict(row)])


async def upsert_system_trade(ch: ClickHouseRepo, row: PaperTradeRow) -> None:
    await ensure_table(ch)
    await insert_trade_row(ch, row)


async def allocated_budget(ch: ClickHouseRepo) -> float:
    rows = await ch.query_rows(
        "SELECT sum(entry_price * quantity) AS allocated "
        "FROM trading.paper_trades FINAL WHERE enabled = 1"
    )
    value = rows[0]["allocated"] if rows else None
    return float(value) if value is not None else 0.0


async def stored_total_budget(ch: ClickHouseRepo) -> float | None:
    rows = await ch.query_rows(
        "SELECT value FROM trading.system_settings FINAL WHERE key = 'paper_total_budget' LIMIT 1"
    )
    if not rows:
        return None
    try:
        return float(rows[0]["value"])
    except (TypeError, ValueError):
        return None


async def load_latest_historical_prices(
    ch: ClickHouseRepo, symbols: list[str]
) -> list[dict[str, Any]]:
    """Latest known parquet close per symbol, for the offline/no-Dhan price
    fallback -- returns rows with symbol/trade_date/day_close keys."""
    if not symbols:
        return []
    query = """
        WITH daily AS (
            SELECT symbol, toDate(date) AS trade_date, argMax(close, bucket) AS day_close
            FROM file('parquets/candles_*.parquet', Parquet)
            WHERE symbol IN %(symbols)s AND date IS NOT NULL AND close IS NOT NULL
            GROUP BY symbol, trade_date
        ), ranked AS (
            SELECT symbol, toString(trade_date) AS trade_date, toFloat64(day_close) AS day_close,
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM daily
        )
        SELECT symbol, trade_date, day_close FROM ranked WHERE rn = 1
    """
    return await ch.query_rows(query, parameters={"symbols": symbols})


async def set_stored_total_budget(ch: ClickHouseRepo, total_budget: float) -> None:
    await ch.command(
        "INSERT INTO trading.system_settings (key, value) VALUES ('paper_total_budget', %(value)s)",
        parameters={"value": str(total_budget)},
    )
