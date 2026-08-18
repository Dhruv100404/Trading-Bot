"""Reads from trading.watchlist that other repos share (news mention resolution,
swing dashboard scanning, and paper-trade price enrichment)."""

from __future__ import annotations

import logging

from swing_atlas.domain.news.models import WatchlistCompany
from swing_atlas.domain.swing.models import WatchRow
from swing_atlas.repositories.clickhouse import ClickHouseRepo

logger = logging.getLogger(__name__)

_WATCH_ROW_COLUMNS = "security_id, symbol, company_name, tiers, enabled, min_volume"


class WatchlistRepo:
    def __init__(self, ch: ClickHouseRepo) -> None:
        self._ch = ch

    async def load_companies(self) -> list[WatchlistCompany]:
        rows = await self._ch.query_rows(
            "SELECT symbol, security_id, company_name FROM trading.watchlist FINAL "
            "WHERE symbol != '' AND company_name != '' LIMIT 10000"
        )
        return [
            WatchlistCompany(
                symbol=row["symbol"],
                security_id=row["security_id"],
                company_name=row["company_name"],
            )
            for row in rows
        ]

    async def load_security_ids_for_symbols(self, symbols: list[str]) -> dict[str, str]:
        """symbol -> security_id, for the given symbols only. Mirrors the inline
        query in engine/src/api/paper.rs::enrich_with_live_quotes."""
        if not symbols:
            return {}
        rows = await self._ch.query_rows(
            "SELECT symbol, any(security_id) AS security_id FROM trading.watchlist FINAL "
            "WHERE enabled = 1 AND symbol IN %(symbols)s GROUP BY symbol",
            parameters={"symbols": symbols},
        )
        return {row["symbol"]: row["security_id"] for row in rows}

    async def load_watch_rows(self, limit: int, symbol_filter: str | None) -> list[WatchRow]:
        """Mirrors engine/src/api/swing.rs::load_watch_rows: an exact-symbol
        lookup when filtering, else the enabled watchlist, falling back to the
        unfiltered table if either query comes back empty. Any query failure
        degrades to an empty list, matching Rust's .unwrap_or_default()."""
        resolved_limit = min(max(limit, 1), 1200)
        try:
            if symbol_filter is not None:
                primary = await self._ch.query_rows(
                    f"SELECT {_WATCH_ROW_COLUMNS} FROM trading.watchlist FINAL "
                    f"WHERE upper(symbol) = upper(%(symbol)s) ORDER BY enabled DESC, symbol "
                    f"LIMIT {resolved_limit}",
                    parameters={"symbol": symbol_filter},
                )
            else:
                primary = await self._ch.query_rows(
                    f"SELECT {_WATCH_ROW_COLUMNS} FROM trading.watchlist FINAL "
                    f"WHERE enabled = 1 ORDER BY symbol LIMIT {resolved_limit}"
                )
            if primary:
                return [_to_watch_row(row) for row in primary]

            fallback = await self._ch.query_rows(
                f"SELECT {_WATCH_ROW_COLUMNS} FROM trading.watchlist FINAL "
                f"ORDER BY symbol LIMIT {resolved_limit}"
            )
            return [_to_watch_row(row) for row in fallback]
        except Exception as exc:  # noqa: BLE001 -- matches Rust's .unwrap_or_default() fallback
            logger.warning("load_watch_rows failed: %s", exc)
            return []


def _to_watch_row(row: dict[str, object]) -> WatchRow:
    return WatchRow(**row)  # type: ignore[arg-type]
