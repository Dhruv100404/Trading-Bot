"""Per-symbol historical baselines evaluate_live_signal blends with today's
live quote -- mirrors engine/src/api/swing.rs::load_live_signal_baselines/
load_cached_live_signal_baselines.

Note: load_historical_fallbacks/load_cached_historical_fallbacks (a similarly
named but distinct CandidateSeed-fallback pair) are NOT ported -- confirmed
dead code in the Rust original.
"""

from __future__ import annotations

from swing_atlas.domain.swing.models import HistoricalScreenerFeatureRow
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.feature_row_mapping import to_feature_row
from swing_atlas.repositories.screener_feature_cache_repo import (
    ensure_screener_feature_cache,
    last_completed_trading_day,
    parquet_source_for_recent_months,
)

_RECENT_MONTHS = 24


async def load_cached_live_signal_baselines(
    ch: ClickHouseRepo, symbols: list[str]
) -> dict[str, HistoricalScreenerFeatureRow]:
    if not symbols:
        return {}
    await ensure_screener_feature_cache(ch)
    target_date = last_completed_trading_day()
    query = f"""
        WITH target AS (
            SELECT max(trade_date) AS data_date
            FROM trading.daily_screener_features FINAL
            WHERE trade_date <= toDate('{target_date}')
        )
        SELECT
            f.symbol AS symbol,
            toString(f.trade_date) AS trade_date,
            toFloat64(f.day_open) AS day_open,
            toFloat64(f.prev_close) AS prev_close,
            toFloat64(f.day_close) AS day_close,
            toFloat64(f.day_high) AS day_high,
            toFloat64(f.day_low) AS day_low,
            toString(f.day_volume) AS day_volume,
            toFloat64(f.sma20) AS sma20,
            toFloat64(f.sma50) AS sma50,
            toFloat64(f.sma200) AS sma200,
            toFloat64(f.avg_volume20) AS avg_volume20,
            toFloat64(f.high_20d) AS high_20d,
            toFloat64(f.high_52w) AS high_52w,
            toFloat64(f.low_52w) AS low_52w,
            toFloat64(f.rsi10) AS rsi10,
            toFloat64(f.atr14) AS atr14,
            toFloat64(f.prior_high20) AS prior_high20,
            toFloat64(f.prior_close3) AS prior_close3,
            toFloat64(f.ret3) AS ret3,
            toFloat64(f.range_atr) AS range_atr,
            toFloat64(f.recovery_from_low_pct) AS recovery_from_low_pct,
            toFloat64(f.rs60_rank) AS rs60_rank,
            toFloat64(f.market_breadth200) AS market_breadth200
        FROM trading.daily_screener_features AS f FINAL
        WHERE f.trade_date = (SELECT data_date FROM target)
          AND f.symbol IN %(symbols)s
    """
    rows = await ch.query_rows(query, parameters={"symbols": symbols})
    return {row["symbol"]: to_feature_row(row) for row in rows}


async def load_live_signal_baselines(
    ch: ClickHouseRepo, symbols: list[str]
) -> dict[str, HistoricalScreenerFeatureRow]:
    if not symbols:
        return {}
    cached = await load_cached_live_signal_baselines(ch, symbols)
    if cached:
        return cached

    parquet_source = parquet_source_for_recent_months(_RECENT_MONTHS)
    query = f"""
        WITH daily AS (
            SELECT
                symbol,
                toDate(date) AS trade_date,
                argMax(close, bucket) AS day_close,
                max(high) AS day_high,
                min(low) AS day_low,
                toUInt64(sum(volume)) AS day_volume
            FROM ({parquet_source})
            WHERE symbol IN %(symbols)s
              AND date IS NOT NULL
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
              AND volume IS NOT NULL
            GROUP BY symbol, trade_date
        )
        SELECT
            symbol,
            toString(trade_date) AS trade_date,
            toFloat64(day_close) AS day_close,
            toFloat64(day_high) AS day_high,
            toFloat64(day_low) AS day_low,
            day_volume,
            toFloat64(sma20) AS sma20,
            toFloat64(sma50) AS sma50,
            toFloat64(sma200) AS sma200,
            toFloat64(avg_volume20) AS avg_volume20,
            toFloat64(high_20d) AS high_20d,
            toFloat64(high_52w) AS high_52w,
            toFloat64(low_52w) AS low_52w,
            toFloat64(rsi10) AS rsi10
        FROM (
            SELECT
                symbol,
                trade_date,
                day_close,
                day_high,
                day_low,
                day_volume,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20,
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10,
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM (
                SELECT *,
                    greatest(day_close - lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS gain,
                    greatest(lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - day_close, 0) AS loss
                FROM daily
            )
        )
        WHERE rn = 1
    """
    rows = await ch.query_rows(query, parameters={"symbols": symbols})
    return {row["symbol"]: to_feature_row(row) for row in rows}
