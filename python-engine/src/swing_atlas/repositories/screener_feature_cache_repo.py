"""ClickHouse reads/writes for the daily screener feature cache and the
historical-screener strategy-status lookup -- mirrors the corresponding
functions in engine/src/api/swing.rs (ensure_screener_feature_cache,
latest_feature_cache_stats, refresh_screener_feature_cache,
load_cached_historical_screener_rows, load_historical_screener_rows,
load_latest_strategy_statuses).

SQL text is kept byte-for-byte identical to the Rust originals (only
format!() -> f-strings) per the migration plan's design decision, with one
unavoidable wire-format adjustment: Rust reads these queries over the
ClickHouse HTTP JSON interface, which stringifies UInt64 automatically:
clickhouse-connect's native protocol does not, so day_volume is cast to
str() explicitly in Python after fetch (see feature_row_mapping.to_feature_row)
rather than relying on transport-level coercion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from swing_atlas.domain.swing.models import HistoricalScreenerFeatureRow
from swing_atlas.domain.time_utils import is_nse_holiday, now_ist, prev_trading_day
from swing_atlas.repositories.clickhouse import ClickHouseRepo
from swing_atlas.repositories.feature_row_mapping import to_feature_row
from swing_atlas.repositories.schema import ensure_screener_feature_cache_schema

_RECENT_MONTHS = 24


def last_completed_trading_day() -> str:
    now = now_ist()
    today = now.date()
    close_reached = now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    if not is_nse_holiday(today) and close_reached:
        return today.isoformat()
    return prev_trading_day(today).isoformat()


def parquet_source_for_recent_months(months: int) -> str:
    """`SELECT *` union across per-year candle parquet files -- used by the
    uncached screener fallback query (same shape as candles)."""
    current_year = datetime.now(UTC).year
    years_to_scan = max((max(months, 1) + 11) // 12 + 1, 1)
    parts = [
        f"SELECT * FROM file('parquets/candles_{current_year - offset:04d}*.parquet', Parquet)"
        for offset in range(years_to_scan)
    ]
    return " UNION ALL ".join(parts)


def _parquet_feature_source_for_recent_months(months: int) -> str:
    """Same year span as parquet_source_for_recent_months but with the column
    list the feature-cache refresh query expects (candle_date instead of date)."""
    current_year = datetime.now(UTC).year
    years_to_scan = max((max(months, 1) + 11) // 12 + 1, 1)
    parts = [
        "SELECT date AS candle_date, symbol, bucket, open, high, low, close, volume "
        f"FROM file('parquets/candles_{current_year - offset:04d}*.parquet', Parquet)"
        for offset in range(years_to_scan)
    ]
    return " UNION ALL ".join(parts)


@dataclass(frozen=True, slots=True)
class FeatureCacheStats:
    data_date: str
    cached_rows: int
    avg_atr14: float
    avg_ret3_abs: float


async def ensure_screener_feature_cache(ch: ClickHouseRepo) -> None:
    await ensure_screener_feature_cache_schema(ch)


async def latest_feature_cache_stats(ch: ClickHouseRepo) -> FeatureCacheStats | None:
    await ensure_screener_feature_cache(ch)
    target_date = last_completed_trading_day()
    query = f"""
        WITH target AS (
            SELECT max(trade_date) AS data_date
            FROM trading.daily_screener_features FINAL
            WHERE trade_date <= toDate('{target_date}')
        )
        SELECT
            toString(trade_date) AS data_date,
            toUInt64(count()) AS cached_rows,
            toFloat64(avg(atr14)) AS avg_atr14,
            toFloat64(avg(abs(ret3))) AS avg_ret3_abs
        FROM trading.daily_screener_features FINAL
        WHERE trade_date = (SELECT data_date FROM target)
        GROUP BY trade_date
    """
    rows = await ch.query_rows(query)
    if not rows:
        return None
    row = rows[0]
    return FeatureCacheStats(
        data_date=row["data_date"],
        cached_rows=int(row["cached_rows"]),
        avg_atr14=row["avg_atr14"],
        avg_ret3_abs=row["avg_ret3_abs"],
    )


async def refresh_screener_feature_cache(ch: ClickHouseRepo) -> None:
    parquet_source = _parquet_feature_source_for_recent_months(_RECENT_MONTHS)
    target_date = last_completed_trading_day()
    query = f"""
        INSERT INTO trading.daily_screener_features
        (trade_date, symbol, day_open, day_high, day_low, day_close, prev_close, day_volume,
         sma20, sma50, sma200, avg_volume20, high_20d, high_52w, low_52w, rsi10,
         atr14, atr_pct, range_pct, close_location, gap_pct, prior_high20, prior_high55,
         prior_high252, prior_close3, prior_low20, ret3, range_atr, recovery_from_low_pct,
         rs60_rank, rs120_rank, market_breadth200, refreshed_at)
        WITH daily AS (
            SELECT
                symbol,
                toDate(candle_date) AS trade_date,
                argMin(open, bucket) AS day_open,
                max(high) AS day_high,
                min(low) AS day_low,
                argMax(close, bucket) AS day_close,
                toUInt64(sum(volume)) AS day_volume
            FROM ({parquet_source})
            WHERE toDate(candle_date) >= subtractYears(today(), 2)
              AND symbol IN (SELECT symbol FROM trading.watchlist WHERE enabled = 1)
              AND candle_date IS NOT NULL
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
              AND volume IS NOT NULL
            GROUP BY symbol, trade_date
        ), target_date AS (
            SELECT max(trade_date) AS data_date
            FROM daily
            WHERE trade_date <= toDate('{target_date}')
        ), with_prev AS (
            SELECT *,
                lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_close
            FROM daily
        ), priced AS (
            SELECT *,
                greatest(day_close - prev_close, 0) AS gain,
                greatest(prev_close - day_close, 0) AS loss,
                greatest(day_high - day_low, abs(day_high - prev_close), abs(day_low - prev_close)) AS true_range,
                if(prev_close > 0, ((day_open - prev_close) / prev_close) * 100, 0) AS gap_pct,
                if(day_close > 0, (day_high - day_low) / day_close, 0) AS range_pct,
                if(day_high > day_low, (day_close - day_low) / (day_high - day_low), 0.5) AS close_location
            FROM with_prev
        ), ranked AS (
            SELECT
                symbol,
                trade_date,
                day_open,
                day_high,
                day_low,
                day_close,
                prev_close,
                day_volume,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20,
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,
                avg(true_range) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14,
                if(day_close > 0, avg(true_range) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) / day_close, 0) AS atr_pct,
                range_pct,
                close_location,
                gap_pct,
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), day_high) AS prior_high20,
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 55 PRECEDING AND 1 PRECEDING), day_high) AS prior_high55,
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING), day_high) AS prior_high252,
                lagInFrame(day_close, 3, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prior_close3,
                coalesce(min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), day_low) AS prior_low20,
                if(lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret60,
                if(lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret120,
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10,
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM priced
        ), scored AS (
            SELECT *,
                if(prior_close3 > 0, day_close / prior_close3 - 1, 0) AS ret3,
                if(atr14 > 0, (day_high - day_low) / atr14, 0) AS range_atr,
                if(day_low > 0, (day_close - day_low) / day_low, 0) AS recovery_from_low_pct,
                toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret60)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs60_rank,
                toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret120)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs120_rank,
                avg(if(day_close > sma200, 1.0, 0.0)) OVER (PARTITION BY trade_date) AS market_breadth200
            FROM ranked
        )
        SELECT
            trade_date,
            symbol,
            toFloat64(day_open),
            toFloat64(day_high),
            toFloat64(day_low),
            toFloat64(day_close),
            toFloat64(prev_close),
            day_volume,
            toFloat64(sma20),
            toFloat64(sma50),
            toFloat64(sma200),
            toFloat64(avg_volume20),
            toFloat64(high_20d),
            toFloat64(high_52w),
            toFloat64(low_52w),
            toFloat64(rsi10),
            toFloat64(atr14),
            toFloat64(atr_pct),
            toFloat64(range_pct),
            toFloat64(close_location),
            toFloat64(gap_pct),
            toFloat64(prior_high20),
            toFloat64(prior_high55),
            toFloat64(prior_high252),
            toFloat64(prior_close3),
            toFloat64(prior_low20),
            toFloat64(ret3),
            toFloat64(range_atr),
            toFloat64(recovery_from_low_pct),
            toFloat64(rs60_rank),
            toFloat64(rs120_rank),
            toFloat64(market_breadth200),
            now()
        FROM scored
        WHERE rn = 1
          AND trade_date = (SELECT data_date FROM target_date)
    """
    await ch.command(query)


async def load_cached_historical_screener_rows(
    ch: ClickHouseRepo, min_price: float, min_avg_volume: float
) -> list[HistoricalScreenerFeatureRow]:
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
            toFloat64(f.atr_pct) AS atr_pct,
            toFloat64(f.range_pct) AS range_pct,
            toFloat64(f.close_location) AS close_location,
            toFloat64(f.gap_pct) AS gap_pct,
            toFloat64(f.prior_high20) AS prior_high20,
            toFloat64(f.prior_high55) AS prior_high55,
            toFloat64(f.prior_high252) AS prior_high252,
            toFloat64(f.prior_close3) AS prior_close3,
            toFloat64(f.prior_low20) AS prior_low20,
            toFloat64(f.ret3) AS ret3,
            toFloat64(f.range_atr) AS range_atr,
            toFloat64(f.recovery_from_low_pct) AS recovery_from_low_pct,
            toFloat64(f.rs60_rank) AS rs60_rank,
            toFloat64(f.rs120_rank) AS rs120_rank,
            toFloat64(f.market_breadth200) AS market_breadth200
        FROM trading.daily_screener_features AS f FINAL
        WHERE f.trade_date = (SELECT data_date FROM target)
          AND f.day_close >= {min_price}
          AND f.avg_volume20 >= {min_avg_volume}
        ORDER BY f.avg_volume20 DESC
        LIMIT 1200
    """
    rows = await ch.query_rows(query)
    return [to_feature_row(row) for row in rows]


async def load_historical_screener_rows(
    ch: ClickHouseRepo, min_price: float, min_avg_volume: float
) -> list[HistoricalScreenerFeatureRow]:
    await ensure_screener_feature_cache(ch)
    cache_stats = await latest_feature_cache_stats(ch)
    cache_usable = (
        cache_stats is not None and cache_stats.cached_rows > 0
        and cache_stats.avg_atr14 > 0.0 and cache_stats.avg_ret3_abs > 0.0
    )  # fmt: skip
    if not cache_usable:
        await refresh_screener_feature_cache(ch)
    cached = await load_cached_historical_screener_rows(ch, min_price, min_avg_volume)
    if cache_usable or cached:
        return cached

    parquet_source = parquet_source_for_recent_months(_RECENT_MONTHS)
    target_date = last_completed_trading_day()
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
            WHERE toDate(date) >= subtractYears(today(), 2)
              AND symbol IN (SELECT symbol FROM trading.watchlist WHERE enabled = 1)
            GROUP BY symbol, trade_date
        ), target_date AS (
            SELECT max(trade_date) AS data_date
            FROM daily
            WHERE trade_date <= toDate('{target_date}')
        ), ranked AS (
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
        SELECT
            symbol,
            toString(ranked.trade_date) AS trade_date,
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
        FROM ranked
        WHERE rn = 1
          AND ranked.trade_date = (SELECT data_date FROM target_date)
          AND day_close >= {min_price}
          AND avg_volume20 >= {min_avg_volume}
        ORDER BY avg_volume20 DESC
        LIMIT 1200
    """
    rows = await ch.query_rows(query)
    return [to_feature_row(row) for row in rows]


async def load_latest_strategy_statuses(ch: ClickHouseRepo) -> dict[str, str]:
    query = """
        WITH latest AS (
            SELECT run_id
            FROM trading.backtest_trades
            GROUP BY run_id
            ORDER BY run_id DESC
            LIMIT 1
        ), totals AS (
            SELECT strategy_id, sum(pnl) AS total_pnl
            FROM trading.backtest_trades
            WHERE run_id = (SELECT run_id FROM latest)
            GROUP BY strategy_id
        )
        SELECT
            strategy_id,
            multiIf(
                total_pnl <= 0, 'Rejected',
                strategy_id = 'momentum-core-v1', 'Candidate',
                strategy_id = 'rsi10-pullback-reversion-v1', 'Candidate',
                strategy_id = 'failed-breakdown-reclaim-v1', 'Candidate',
                strategy_id = 'compression-breakout-v1', 'Watch',
                strategy_id = 'breakout-continuation-v1', 'Watch',
                strategy_id = 'rs-leader-continuation-v1', 'Watch',
                strategy_id = 'near-52w-high-runner-v2', 'Watch',
                'Fragile'
            ) AS status
        FROM totals
    """
    rows = await ch.query_rows(query)
    return {row["strategy_id"]: row["status"] for row in rows}
