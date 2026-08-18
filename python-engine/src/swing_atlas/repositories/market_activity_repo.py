"""ClickHouse reads/writes for market-activity snapshots.

Read mirrors engine/src/api/market_activity.rs::list -- joins each row against the
latest snapshot per (source, metric_type, exchange, index_name) group. Insert
replaces engine/src/api/market_activity.rs::insert_json_each_row.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from swing_atlas.domain.market_activity.models import MarketActivityRow
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.query_utils import clamp, clean


class MarketActivityRepo:
    def __init__(self, ch: ClickHouseRepo) -> None:
        self._ch = ch

    async def list_activity(
        self,
        *,
        symbol: str | None,
        metric_type: str | None,
        exchange: str | None,
        source: str | None,
        min_volume_multiplier: float | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": clamp(limit if limit is not None else 150, 1, 500)}
        filters = []

        if (clean_symbol := clean(symbol, upper=True)) is not None:
            params["symbol"] = clean_symbol
            filters.append("a.symbol = %(symbol)s")
        if (clean_metric_type := clean(metric_type)) is not None:
            params["metric_type"] = clean_metric_type
            filters.append("a.metric_type = %(metric_type)s")
        if (clean_exchange := clean(exchange, upper=True)) is not None:
            params["exchange"] = clean_exchange
            filters.append("a.exchange = %(exchange)s")
        if (clean_source := clean(source)) is not None:
            params["source"] = clean_source
            filters.append("a.source = %(source)s")
        if min_volume_multiplier is not None:
            params["min_volume_multiplier"] = min_volume_multiplier
            filters.append("a.volume_multiplier >= %(min_volume_multiplier)s")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        sql = f"""
            SELECT
                a.row_id,
                formatDateTime(a.snapshot_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS snapshot_at,
                toString(a.trading_date) AS trading_date,
                a.source,
                a.metric_type,
                a.exchange,
                a.index_name,
                a.rank,
                a.symbol,
                a.stock_name,
                a.moneycontrol_id,
                a.slug,
                a.price,
                a.change_abs,
                a.change_pct,
                a.day_high,
                a.day_low,
                a.open,
                a.prev_close,
                a.volume,
                a.avg_volume,
                a.volume_multiplier,
                a.volume_change_pct,
                a.value_cr,
                a.vwap,
                a.direction,
                a.mcap_cr,
                a.month_return_pct,
                a.month3_return_pct,
                a.share_url,
                a.source_url,
                formatDateTime(a.fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at
            FROM trading.market_activity_snapshots AS a
            INNER JOIN (
                SELECT source, metric_type, exchange, index_name, max(snapshot_at) AS snapshot_at
                FROM trading.market_activity_snapshots
                GROUP BY source, metric_type, exchange, index_name
            ) AS latest
            ON a.source = latest.source
               AND a.metric_type = latest.metric_type
               AND a.exchange = latest.exchange
               AND a.index_name = latest.index_name
               AND a.snapshot_at = latest.snapshot_at
            {where_clause}
            ORDER BY a.metric_type ASC, a.rank ASC, a.symbol ASC
            LIMIT %(limit)s
        """
        return await self._ch.query_rows(sql, params)

    async def insert_rows(self, rows: list[MarketActivityRow]) -> None:
        await self._ch.insert_rows("trading.market_activity_snapshots", [asdict(r) for r in rows])
