"""Parquet-backed daily candle history for the /api/swing/history/{symbol}
endpoint -- mirrors engine/src/api/swing.rs::load_historical_candles/
parquet_source_for_recent_years.
"""

from __future__ import annotations

from datetime import UTC, datetime

from swing_atlas.domain.numeric import round2
from swing_atlas.domain.swing.history import history_month_span, history_where_clause
from swing_atlas.domain.swing.models import HistoricalCandle
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.screener_feature_cache_repo import parquet_source_for_recent_months


def parquet_source_for_recent_years(years: int) -> str:
    current_year = datetime.now(UTC).year
    parts = [
        f"SELECT * FROM file('parquets/candles_{current_year - offset:04d}*.parquet', Parquet)"
        for offset in range(max(years, 1))
    ]
    return " UNION ALL ".join(parts)


async def load_historical_candles(
    ch: ClickHouseRepo, symbol: str, range_: str
) -> list[HistoricalCandle]:
    month_span = history_month_span(range_)
    if month_span > 24:
        parquet_source = parquet_source_for_recent_years(-(-month_span // 12))
    else:
        parquet_source = parquet_source_for_recent_months(month_span)

    query = f"""
        SELECT
            toString(date) AS trade_date,
            toFloat64(argMin(open, bucket)) AS open,
            toFloat64(max(high)) AS high,
            toFloat64(min(low)) AS low,
            toFloat64(argMax(close, bucket)) AS close,
            toUInt64(sum(volume)) AS volume
        FROM ({parquet_source})
        WHERE upper(symbol) = upper(%(symbol)s) AND toDate(date) >= {history_where_clause(range_)}
        GROUP BY date
        ORDER BY date
    """
    rows = await ch.query_rows(query, parameters={"symbol": symbol})

    candles = []
    for row in rows:
        if any(
            row.get(key) is None for key in ("trade_date", "open", "high", "low", "close", "volume")
        ):
            continue
        candles.append(
            HistoricalCandle(
                date=row["trade_date"],
                open=round2(row["open"]),
                high=round2(row["high"]),
                low=round2(row["low"]),
                close=round2(row["close"]),
                volume=int(row["volume"]),
            )
        )
    return candles
