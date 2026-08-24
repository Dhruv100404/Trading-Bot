"""Symbol history + Bamboo lab signal orchestration -- mirrors
engine/src/api/swing.rs's history/load_dhan_intraday_history/bamboo_latest.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

import httpx

from swing_atlas.config import Settings
from swing_atlas.domain.swing.history import (
    append_live_quote_candle,
    compute_historical_summary,
    intraday_chart_day,
    intraday_response_to_candles,
    normalize_history_range,
)
from swing_atlas.domain.swing.models import HistoricalCandle, HistoricalSummary
from swing_atlas.domain.time_utils import now_ist
from swing_atlas.repositories.bamboo_repo import read_bamboo_signal_csv
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.dhan.market_data import fetch_intraday_candles
from swing_atlas.repositories.dhan.quote_cache import QuoteCache
from swing_atlas.repositories.historical_candles_repo import load_historical_candles
from swing_atlas.repositories.watchlist_repo import WatchlistRepo
from swing_atlas.services.broker_status_service import BrokerStatusService

logger = logging.getLogger(__name__)

_BAMBOO_ALL_PATH = "docs/quant_research_outputs/bamboo_mtf_breakout_latest/latest_signals.csv"
_BAMBOO_TOP_PATH = "docs/quant_research_outputs/bamboo_mtf_breakout_latest/top_latest_signals.csv"
_BAMBOO_MISSING_MESSAGE = (
    "No Bamboo latest signal file was found yet. Run "
    "`python scripts\\bamboo_mtf_backtest.py --out-dir "
    "docs\\quant_research_outputs\\bamboo_mtf_breakout_latest`."
)


class SwingHistoryService:
    def __init__(
        self,
        settings: Settings,
        ch: ClickHouseRepo,
        watchlist_repo: WatchlistRepo,
        broker_status_service: BrokerStatusService,
        quote_cache: QuoteCache,
    ) -> None:
        self._settings = settings
        self._ch = ch
        self._watchlist_repo = watchlist_repo
        self._broker_status_service = broker_status_service
        self._quote_cache = quote_cache

    async def history(self, symbol: str, range_query: str | None) -> dict[str, Any]:
        range_ = normalize_history_range(range_query)
        updated_at = now_ist().isoformat()

        if range_ == "1d":
            try:
                candles = await self._load_dhan_intraday_history(symbol)
            except Exception as exc:  # noqa: BLE001 -- always-200-with-inline-error contract
                return self._response(
                    updated_at,
                    symbol,
                    range_,
                    "dhan-intraday",
                    [],
                    None,
                    f"Dhan intraday chart failed: {exc}",
                )
            if candles:
                summary = compute_historical_summary(candles)
                return self._response(
                    updated_at, symbol, range_, "dhan-intraday", candles, summary, None
                )
            return self._response(
                updated_at, symbol, range_, "dhan-intraday", [], None,
                "Dhan intraday chart returned no candles for the selected trading day.",
            )  # fmt: skip

        try:
            candles = await load_historical_candles(self._ch, symbol, range_)
        except Exception as exc:  # noqa: BLE001 -- always-200-with-inline-error contract
            return self._response(
                updated_at,
                symbol,
                range_,
                "parquet-history",
                [],
                None,
                f"Historical parquet query failed: {exc}",
            )

        summary = compute_historical_summary(candles)
        message = (
            None
            if candles
            else f"No parquet-backed history was found for {symbol} in the selected range."
        )
        return self._response(
            updated_at, symbol, range_, "parquet-history", candles, summary, message
        )

    async def _load_dhan_intraday_history(self, symbol: str) -> list[HistoricalCandle]:
        credentials = await self._broker_status_service.resolve_dhan_credentials()
        if credentials is None:
            raise RuntimeError("Dhan credentials are not configured")

        watch_rows = await self._watchlist_repo.load_watch_rows(1, symbol)
        watch = next((row for row in watch_rows if row.symbol.upper() == symbol.upper()), None)
        if watch is None:
            raise RuntimeError(f"{symbol} is not present in the Dhan watchlist/security master")

        target_day = intraday_chart_day()
        from_time = f"{target_day:%Y-%m-%d} 09:15:00"
        to_time = f"{target_day:%Y-%m-%d} 15:30:00"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await fetch_intraday_candles(
                client,
                self._settings.dhan_base_url,
                credentials.access_token,
                credentials.client_id,
                watch.security_id,
                from_time,
                to_time,
            )
            candles = intraday_response_to_candles(response)

            if target_day.date() == now_ist().date():
                try:
                    async with httpx.AsyncClient(timeout=10.0) as quote_client:
                        quotes = await self._quote_cache.get_live_quotes(
                            quote_client,
                            self._settings.dhan_base_url,
                            credentials.access_token,
                            credentials.client_id,
                            self._settings.dhan_quote_endpoint,
                            [watch.security_id],
                        )
                    quote = next(iter(quotes.values()), None)
                    if quote is not None:
                        append_live_quote_candle(candles, quote)
                except Exception as exc:  # noqa: BLE001 -- a stale bar beats no bar
                    logger.warning("live quote append for intraday history failed: %s", exc)

        return candles

    @staticmethod
    def _response(
        updated_at: str,
        symbol: str,
        range_: str,
        source: str,
        candles: list[HistoricalCandle],
        summary: HistoricalSummary | None,
        message: str | None,
    ) -> dict[str, Any]:
        return {
            "updated_at": updated_at,
            "symbol": symbol,
            "range": range_,
            "source": source,
            "candles": [asdict(c) for c in candles],
            "summary": asdict(summary) if summary is not None else None,
            "message": message,
        }

    async def bamboo_latest(self) -> dict[str, Any]:
        all_signals = read_bamboo_signal_csv(_BAMBOO_ALL_PATH)
        top_signals = read_bamboo_signal_csv(_BAMBOO_TOP_PATH)
        if not top_signals:
            top_signals = sorted(all_signals, key=lambda s: s.rank_score, reverse=True)[:4]

        symbols = {signal.symbol for signal in all_signals}
        signal_date = (
            all_signals[0].signal_date
            if all_signals
            else (top_signals[0].signal_date if top_signals else None)
        )
        message = _BAMBOO_MISSING_MESSAGE if not all_signals and not top_signals else None

        return {
            "updated_at": now_ist().isoformat(),
            "signal_date": signal_date,
            "total_rows": len(all_signals),
            "unique_symbols": len(symbols),
            "top_signals": [asdict(s) for s in top_signals],
            "all_signals": [asdict(s) for s in all_signals],
            "message": message,
        }
