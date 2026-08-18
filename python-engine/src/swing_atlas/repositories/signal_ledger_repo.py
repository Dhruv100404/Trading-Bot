"""ClickHouse reads/writes for the fresh-signals dedup ledger -- mirrors
engine/src/api/swing.rs's ensure_signal_ledger/load_signal_ledger_keys/
load_active_paper_symbols/insert_signal_ledger_rows.

The ledger's purpose is purely deduplication: fresh_signals() checks it to
avoid re-staging a signal it has already seen, it never drives the paper-trade
decision itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from swing_atlas.domain.numeric import round2
from swing_atlas.domain.swing.models import HistoricalScreenerRow
from swing_atlas.domain.swing.screener import (
    PaperRule,
    paper_rule_for_strategy,
    quantity_for_capital,
    signal_key_for,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.paper_trades_repo import ensure_table as ensure_paper_trades_table
from swing_atlas.repositories.schema import ensure_signal_ledger_schema

PAPER_CAPITAL_PER_SIGNAL = 50_000.0


@dataclass(frozen=True, slots=True)
class SignalLedgerInsertRow:
    signal_key: str
    symbol: str
    strategy_id: str
    strategy_label: str
    strategy_status: str
    setup_family: str
    signal_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    target_price: float
    score: int
    source: str
    status: str
    paper_status: str
    close_reason: str
    realized_pnl: float


async def ensure_signal_ledger(ch: ClickHouseRepo) -> None:
    await ensure_signal_ledger_schema(ch)


async def load_signal_ledger_keys(ch: ClickHouseRepo) -> set[str]:
    await ensure_signal_ledger(ch)
    rows = await ch.query_rows("SELECT signal_key FROM trading.signal_ledger FINAL")
    return {row["signal_key"] for row in rows}


async def load_active_paper_symbols(ch: ClickHouseRepo) -> set[str]:
    await ensure_paper_trades_table(ch)
    rows = await ch.query_rows("SELECT symbol FROM trading.paper_trades FINAL WHERE enabled = 1")
    return {row["symbol"] for row in rows}


def resolve_entry_stop_target(
    row: HistoricalScreenerRow, rule: PaperRule
) -> tuple[float, int, float, float]:
    """Shared by build_signal_ledger_row and stage_signal_to_paper -- prefer the
    screener's own plan when it's sane (stop below / target above entry), else
    fall back to the strategy's fixed stop/target percentages."""
    entry_price = max(row.close, 0.01)
    quantity = quantity_for_capital(entry_price, PAPER_CAPITAL_PER_SIGNAL)
    if row.stop_loss > 0.0 and row.stop_loss < entry_price:
        stop_loss = row.stop_loss
    else:
        stop_loss = round2(entry_price * (1.0 - rule.stop_loss_pct / 100.0))
    if row.target_price > entry_price:
        target_price = row.target_price
    else:
        target_price = round2(entry_price * (1.0 + rule.take_profit_pct / 100.0))
    return entry_price, quantity, stop_loss, target_price


def build_signal_ledger_row(row: HistoricalScreenerRow, paper_status: str) -> SignalLedgerInsertRow:
    rule = paper_rule_for_strategy(row.strategy_id)
    if rule is None:
        raise ValueError(f"no paper rule for {row.strategy_id}")
    entry_price, quantity, stop_loss, target_price = resolve_entry_stop_target(row, rule)
    return SignalLedgerInsertRow(
        signal_key=signal_key_for(row),
        symbol=row.symbol,
        strategy_id=row.strategy_id,
        strategy_label=row.strategy_label,
        strategy_status=row.strategy_status,
        setup_family=row.setup_family,
        signal_date=row.as_of,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target_price=target_price,
        score=row.score,
        source="historical-screener",
        status="active",
        paper_status=paper_status,
        close_reason="",
        realized_pnl=0.0,
    )


async def insert_signal_ledger_rows(ch: ClickHouseRepo, rows: list[SignalLedgerInsertRow]) -> None:
    if not rows:
        return
    await ch.insert_rows(
        "trading.signal_ledger",
        [
            {
                "signal_key": row.signal_key,
                "symbol": row.symbol,
                "strategy_id": row.strategy_id,
                "strategy_label": row.strategy_label,
                "strategy_status": row.strategy_status,
                "setup_family": row.setup_family,
                "signal_date": row.signal_date,
                "entry_price": row.entry_price,
                "quantity": row.quantity,
                "stop_loss": row.stop_loss,
                "target_price": row.target_price,
                "score": row.score,
                "source": row.source,
                "status": row.status,
                "paper_status": row.paper_status,
                "close_reason": row.close_reason,
                "realized_pnl": row.realized_pnl,
            }
            for row in rows
        ],
    )
