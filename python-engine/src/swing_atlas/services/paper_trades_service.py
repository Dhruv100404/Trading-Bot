"""Paper-trade orchestration -- mirrors engine/src/api/paper.rs's handler bodies
(list/upsert/close/remove/budget/set_budget) plus the live-quote enrichment
cascade (enrich_with_live_quotes/hydrate_with_historical_prices) and the lazy
auto-close-on-list sweep (auto_close_triggered_trades).
"""

from __future__ import annotations

import logging

import httpx

from swing_atlas.config import Settings
from swing_atlas.core.errors import AppError
from swing_atlas.domain.paper_trades import (
    InvalidStopLossError,
    InvalidTargetPriceError,
    is_nse_trading_day_now,
    trading_sessions_elapsed,
    validate_stop_loss,
    validate_target_price,
)
from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories import paper_trades_repo as repo
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.dhan.quote_cache import QuoteCache
from swing_atlas.repositories.watchlist_repo import WatchlistRepo
from swing_atlas.schemas.paper_trades import PaperTradeInput

logger = logging.getLogger(__name__)


class PaperTradesService:
    def __init__(
        self,
        settings: Settings,
        ch: ClickHouseRepo,
        watchlist_repo: WatchlistRepo,
        quote_cache: QuoteCache,
    ) -> None:
        self._settings = settings
        self._ch = ch
        self._watchlist_repo = watchlist_repo
        self._quote_cache = quote_cache

    async def list_trades(self) -> list[repo.PaperTrade]:
        await repo.ensure_table(self._ch)
        trades = await repo.fetch_visible_paper_trades(self._ch)
        async with httpx.AsyncClient(timeout=10.0) as client:
            await self._enrich_with_live_quotes(client, trades)

            if await self._auto_close_triggered_trades(trades) > 0:
                trades = await repo.fetch_visible_paper_trades(self._ch)
                await self._enrich_with_live_quotes(client, trades)

        return trades

    async def upsert(self, trade_input: PaperTradeInput) -> repo.PaperTrade:
        await repo.ensure_table(self._ch)

        entry_price = max(trade_input.entry_price, 0.01)
        try:
            stop_loss = validate_stop_loss(entry_price, trade_input.stop_loss)
        except InvalidStopLossError as exc:
            raise AppError(400, "invalid_stop_loss", str(exc)) from exc
        try:
            target_price = validate_target_price(entry_price, trade_input.target_price)
        except InvalidTargetPriceError as exc:
            raise AppError(400, "invalid_target_price", str(exc)) from exc

        if trade_input.quantity is not None:
            quantity = trade_input.quantity
        elif trade_input.capital_allocated is not None:
            quantity = int(trade_input.capital_allocated / entry_price)
        else:
            quantity = 1
        quantity = max(quantity, 1)
        max_sessions = max(trade_input.max_sessions or repo.DEFAULT_PAPER_MAX_SESSIONS, 1)

        row = repo.PaperTradeRow(
            symbol=trade_input.symbol.strip().upper(),
            company_name=trade_input.company_name,
            setup_family=trade_input.setup_family,
            bias=trade_input.bias or "Long",
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            target_price=target_price,
            max_sessions=max_sessions,
            capital_allocated=entry_price * quantity,
            expected_hold=(
                trade_input.expected_hold or f"{repo.DEFAULT_PAPER_MAX_SESSIONS} trading sessions"
            ),
            thesis=trade_input.thesis or "",
            notes=trade_input.notes or "",
            exit_price=None,
            close_reason="",
            realized_pnl=0.0,
            enabled=1,
        )
        await repo.insert_trade_row(self._ch, row)

        saved = await repo.fetch_trade_by_symbol(self._ch, row.symbol)
        if saved is None:
            raise AppError(500, "paper_trade_save_failed", "paper trades fetch saved: no row")
        trades = [saved]
        async with httpx.AsyncClient(timeout=10.0) as client:
            await self._enrich_with_live_quotes(client, trades)
        return trades[0]

    async def close(
        self, symbol: str, exit_price: float, close_reason: str | None
    ) -> repo.PaperTrade:
        await repo.ensure_table(self._ch)
        normalized_symbol = symbol.strip().upper()

        current = await repo.fetch_open_trade_by_symbol(self._ch, normalized_symbol)
        if current is None:
            raise AppError(404, "paper_trade_not_found", "open paper trade not found")

        await self._close_trade_row(
            current, max(exit_price, 0.0), close_reason or "session-expired"
        )

        saved = await repo.fetch_trade_by_symbol(self._ch, normalized_symbol)
        if saved is None:
            raise AppError(500, "paper_trade_close_failed", "paper trades fetch closed: no row")
        return saved

    async def remove(self, symbol: str) -> None:
        await repo.ensure_table(self._ch)
        row = repo.PaperTradeRow(
            symbol=symbol.strip().upper(),
            company_name="",
            setup_family="",
            bias="Long",
            entry_price=0.0,
            quantity=1,
            stop_loss=0.0,
            target_price=0.0,
            max_sessions=1,
            capital_allocated=0.0,
            expected_hold="",
            thesis="",
            notes="",
            exit_price=None,
            close_reason="removed",
            realized_pnl=0.0,
            enabled=0,
        )
        await repo.insert_trade_row(self._ch, row)

    async def budget(self) -> dict[str, float]:
        await repo.ensure_table(self._ch)
        return await self._budget_snapshot()

    async def set_budget(self, total_budget: float) -> dict[str, float]:
        await repo.ensure_table(self._ch)
        await repo.set_stored_total_budget(self._ch, max(total_budget, 0.0))
        return await self._budget_snapshot()

    async def _budget_snapshot(self) -> dict[str, float]:
        allocated = await repo.allocated_budget(self._ch)
        stored = await repo.stored_total_budget(self._ch)
        total_budget = max(stored if stored is not None else allocated, allocated)
        return {
            "total_budget": total_budget,
            "allocated_budget": allocated,
            "available_budget": total_budget - allocated,
        }

    async def _close_trade_row(
        self, current: repo.PaperTrade, exit_price: float, close_reason: str
    ) -> None:
        realized_pnl = (exit_price - current.entry_price) * current.quantity
        closed = repo.PaperTradeRow(
            symbol=current.symbol,
            company_name=current.company_name,
            setup_family=current.setup_family,
            bias=current.bias,
            entry_price=current.entry_price,
            quantity=current.quantity,
            stop_loss=current.stop_loss,
            target_price=current.target_price,
            max_sessions=current.max_sessions,
            capital_allocated=current.capital_allocated,
            expected_hold=current.expected_hold,
            thesis=current.thesis,
            notes=current.notes,
            exit_price=exit_price,
            close_reason=close_reason,
            realized_pnl=realized_pnl,
            enabled=0,
        )
        await repo.insert_trade_row(self._ch, closed)

    async def _auto_close_triggered_trades(self, trades: list[repo.PaperTrade]) -> int:
        closed_count = 0
        for trade in trades:
            if trade.enabled != 1:
                continue
            stop_hit = (
                trade.stop_loss > 0.0
                and trade.current_price > 0.0
                and trade.current_price <= trade.stop_loss
            )
            target_hit = (
                trade.target_price > 0.0
                and trade.current_price > 0.0
                and trade.current_price >= trade.target_price
            )
            sessions_elapsed = trading_sessions_elapsed(trade.planned_at)
            time_exit = sessions_elapsed >= trade.max_sessions

            if stop_hit:
                exit_price, close_reason = trade.stop_loss, "stop-loss"
            elif target_hit:
                exit_price, close_reason = trade.target_price, "target-hit"
            elif time_exit:
                exit_price = trade.current_price if trade.current_price > 0.0 else trade.entry_price
                close_reason = f"auto-closed after {trade.max_sessions} trading sessions"
            else:
                continue

            await self._close_trade_row(trade, exit_price, close_reason)
            closed_count += 1
        return closed_count

    async def _enrich_with_live_quotes(
        self, client: httpx.AsyncClient, trades: list[repo.PaperTrade]
    ) -> None:
        open_symbols = [trade.symbol for trade in trades if trade.enabled == 1]
        if not open_symbols:
            return

        if not is_nse_trading_day_now():
            await self._hydrate_with_historical_prices(trades, open_symbols, "last-close")
            return

        if not self._settings.dhan_access_token or not self._settings.dhan_client_id:
            await self._hydrate_with_historical_prices(trades, open_symbols, "parquet-history")
            return

        try:
            security_id_by_symbol = await self._watchlist_repo.load_security_ids_for_symbols(
                open_symbols
            )
        except Exception as exc:  # noqa: BLE001 -- falls back to historical prices on any failure
            logger.warning("paper quote symbol lookup failed: %s", exc)
            await self._hydrate_with_historical_prices(trades, open_symbols, "parquet-history")
            return
        symbol_by_security_id = {
            security_id: symbol for symbol, security_id in security_id_by_symbol.items()
        }
        security_ids = list(symbol_by_security_id.keys())
        if not security_ids:
            await self._hydrate_with_historical_prices(trades, open_symbols, "parquet-history")
            return

        try:
            quotes = await self._quote_cache.get_live_quotes(
                client,
                self._settings.dhan_base_url,
                self._settings.dhan_access_token,
                self._settings.dhan_client_id,
                self._settings.dhan_quote_endpoint,
                security_ids,
            )
        except Exception as exc:  # noqa: BLE001 -- falls back to historical prices on any failure
            logger.warning("paper live quote fetch failed: %s", exc)
            await self._hydrate_with_historical_prices(trades, open_symbols, "parquet-history")
            return

        now = now_ist().isoformat()
        hydrated_symbols: set[str] = set()
        for security_id, quote in quotes.items():
            symbol = symbol_by_security_id.get(security_id)
            if symbol is None:
                continue
            current_price = max(quote.last_price, 0.0)
            if current_price <= 0.0:
                continue
            for trade in trades:
                if trade.symbol != symbol or trade.enabled != 1:
                    continue
                _apply_current_price(trade, current_price, "dhan-live", now)
                hydrated_symbols.add(symbol)

        missing_symbols = [symbol for symbol in open_symbols if symbol not in hydrated_symbols]
        await self._hydrate_with_historical_prices(trades, missing_symbols, "parquet-history")

    async def _hydrate_with_historical_prices(
        self, trades: list[repo.PaperTrade], symbols: list[str], source_prefix: str
    ) -> None:
        if not symbols:
            return
        try:
            rows = await repo.load_latest_historical_prices(self._ch, symbols)
        except Exception as exc:  # noqa: BLE001 -- degrades to unenriched trades, never fatal
            logger.warning("paper historical price fallback failed: %s", exc)
            return

        now = now_ist().isoformat()
        for row in rows:
            day_close = row["day_close"]
            if day_close is None or day_close <= 0.0:
                continue
            quote_source = f"{source_prefix}:{row['trade_date']}"
            for trade in trades:
                if trade.symbol != row["symbol"] or trade.enabled != 1:
                    continue
                _apply_current_price(trade, day_close, quote_source, now)


def _apply_current_price(
    trade: repo.PaperTrade, current_price: float, quote_source: str, quote_updated_at: str
) -> None:
    trade.current_price = current_price
    trade.current_value = current_price * trade.quantity
    trade.unrealized_pnl = (current_price - trade.entry_price) * trade.quantity
    invested = trade.entry_price * trade.quantity
    trade.unrealized_pnl_pct = (trade.unrealized_pnl / invested * 100.0) if invested > 0.0 else 0.0
    trade.quote_source = quote_source
    trade.quote_updated_at = quote_updated_at
