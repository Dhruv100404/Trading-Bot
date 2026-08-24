"""ClickHouse reads/writes for the backtest engine.

The SQL text here is kept byte-for-byte identical to engine/src/api/backtest.rs
(only Rust's format!() -> Python f-strings) per the migration plan's design
decision: since ClickHouse evaluates identical SQL text identically regardless of
caller language, this keeps the hardest verification problem ("did I re-derive the
math correctly?") from ever coming up -- the math never moved.
"""

from __future__ import annotations

from datetime import date

from swing_atlas.domain.backtest.models import (
    BacktestAnalysisTrade,
    BacktestCacheStatus,
    BacktestDateStrategySummary,
    BacktestDateSummary,
    BacktestDayQuality,
    BacktestEquityPoint,
    BacktestMonthlyReturn,
    BacktestRunSummary,
    BacktestStrategyDiagnostic,
    BacktestSymbolResult,
    BacktestTradeLogRow,
    BacktestYearlyReturn,
)
from swing_atlas.domain.backtest.strategy_specs import (
    BACKTEST_CAPITAL_PER_TRADE,
    BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
    MIN_BACKTEST_TRADES_FOR_VALIDATION,
    build_entries_cte,
    deprecated_strategy_sql_clause,
    escape_sql,
    load_backtest_strategy_specs,
    strategy_method_family,
)
from swing_atlas.repositories.clickhouse import ClickHouseRepo

_ACTIVE_CAPITAL = BACKTEST_CAPITAL_PER_TRADE * BACKTEST_MAX_NEW_POSITIONS_PER_DAY


async def latest_run_id(ch: ClickHouseRepo) -> str | None:
    rows = await ch.query_rows(
        "SELECT run_id FROM trading.backtest_trades GROUP BY run_id ORDER BY run_id DESC LIMIT 1"
    )
    return rows[0]["run_id"] if rows else None


async def refresh_backtest_feature_cache(ch: ClickHouseRepo) -> None:
    query = """
        INSERT INTO trading.daily_backtest_features
        WITH
            daily AS (
                SELECT symbol, toDate(date) AS trade_date, argMin(open, bucket) AS day_open,
                    max(high) AS day_high, min(low) AS day_low, argMax(close, bucket) AS day_close,
                    toFloat64(sum(volume)) AS day_volume
                FROM file('parquets/candles_*.parquet', Parquet)
                WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1)
                  AND date IS NOT NULL AND symbol IS NOT NULL AND open IS NOT NULL
                  AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL
                GROUP BY symbol, trade_date
            ),
            priced AS (
                SELECT *,
                    greatest(day_close - lagInFrame(day_close, 1, day_close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ), 0) AS gain,
                    greatest(lagInFrame(day_close, 1, day_close) OVER (
                        PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) - day_close, 0) AS loss
                FROM daily
            ),
            features AS (
                SELECT symbol, trade_date, day_open, day_high, day_low, day_close, day_volume,
                    toUInt32(row_number() OVER (PARTITION BY symbol ORDER BY trade_date)) AS rn,
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
                    avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20,
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
                    min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,
                    100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10
                FROM priced
            ),
            scored AS (
                SELECT *,
                    ((high_20d - day_close) / nullIf(high_20d, 0)) * 100.0 AS breakout_pct,
                    ((high_52w - day_close) / nullIf(high_52w, 0)) * 100.0 AS distance_to_52w_high_pct,
                    ((day_close - low_52w) / nullIf(high_52w - low_52w, 0.01)) * 100.0 AS range_position_pct,
                    day_volume / greatest(avg_volume20, 1.0) AS volume_ratio,
                    day_close > sma20 AND sma20 > sma50 AS trend_up,
                    day_close >= sma20 * 0.98 AND day_close <= sma20 * 1.03 AS pullback_zone,
                    day_close > sma200 AND rsi10 < 30 AS rsi10_pullback
                FROM features
            )
        SELECT
            symbol, trade_date, rn, day_open, day_high, day_low, day_close, day_volume,
            sma20, sma50, sma200, avg_volume20, high_20d, high_52w, low_52w, rsi10,
            breakout_pct, distance_to_52w_high_pct, range_position_pct, volume_ratio,
            toUInt8(trend_up), toUInt8(pullback_zone), toUInt8(rsi10_pullback),
            toUInt8(greatest(50, least(96, 50 + if(trend_up, 18, 0) + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0)) + if(distance_to_52w_high_pct <= 8.0, 10, 0) + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0)) + if(pullback_zone, 8, 0) + if(rsi10_pullback, 16, 0) + if(range_position_pct >= 70.0, 6, 0)))) AS score,
            now()
        FROM scored
        WHERE rn >= 252 AND day_close >= 50 AND avg_volume20 >= 100000
    """
    await ch.command(query)


async def backtest_cache_status(ch: ClickHouseRepo) -> BacktestCacheStatus:
    rows = await ch.query_rows(
        "SELECT "
        "toUInt64(count()) AS cached_rows, "
        "toUInt64(uniqExact(symbol)) AS symbols, "
        "toString(min(trade_date)) AS from_date, "
        "toString(max(trade_date)) AS to_date, "
        "toString(max(refreshed_at)) AS refreshed_at "
        "FROM trading.daily_backtest_features FINAL"
    )
    if not rows:
        return BacktestCacheStatus(
            cached_rows=0, symbols=0, from_date="", to_date="", refreshed_at=""
        )
    return BacktestCacheStatus(**rows[0])


async def execute_backtest_run(ch: ClickHouseRepo, run_id: str) -> None:
    escaped_run_id = escape_sql(run_id)
    specs = load_backtest_strategy_specs()
    entries_cte = build_entries_cte(specs)
    query = f"""
        INSERT INTO trading.backtest_trades
        WITH
            features AS (
                SELECT *
                FROM trading.daily_backtest_features FINAL
                WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1)
            ),
            enriched AS (
                SELECT *,
                    lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_close,
                    lagInFrame(day_high, 1, day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_high,
                    lagInFrame(day_low, 1, day_low) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_low,
                    lagInFrame(sma20, 1, sma20) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_sma20,
                    lagInFrame(day_close, 3, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_3d,
                    lagInFrame(day_close, 20, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_20d,
                    lagInFrame(day_close, 60, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_60d
                FROM features
            ),
            scored_base AS (
                SELECT *,
                    min(day_low) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_low20,
                    max(day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_high20,
                    max(day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 55 PRECEDING AND 1 PRECEDING) AS prior_high55,
                    avg(greatest(day_high - day_low, abs(day_high - prev_close), abs(day_low - prev_close))) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14,
                    avg((day_high - day_low) / nullIf(day_close, 0.01)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_range_pct20,
                    stddevPop(day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS std20,
                    avg(greatest(day_close - prev_close, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain14,
                    avg(greatest(prev_close - day_close, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss14,
                    avg(if((day_high - prev_high) > (prev_low - day_low) AND (day_high - prev_high) > 0, day_high - prev_high, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS plus_dm14,
                    avg(if((prev_low - day_low) > (day_high - prev_high) AND (prev_low - day_low) > 0, prev_low - day_low, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS minus_dm14,
                    min((day_high - day_low) / nullIf(day_close, 0.01)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS min_range_pct7,
                    day_close / nullIf(close_3d, 0.01) - 1 AS ret3,
                    day_close / nullIf(close_20d, 0.01) - 1 AS ret20,
                    day_close / nullIf(close_60d, 0.01) - 1 AS ret60
                FROM enriched
            ),
            scored_indicators AS (
                SELECT *,
                    100 - (100 / (1 + avg_gain14 / greatest(avg_loss14, 0.000001))) AS rsi14,
                    (day_close - sma20) / nullIf(std20, 0) AS zscore20,
                    (day_close - sma20) / nullIf(atr14, 0) AS dist_sma20_atr,
                    (day_close - day_low) / nullIf(day_high - day_low, 0.01) AS close_location,
                    (day_high - day_low) / nullIf(atr14, 0) AS range_atr,
                    (day_close - day_low) / nullIf(day_low, 0.01) AS recovery_from_low_pct,
                    100 * plus_dm14 / nullIf(atr14, 0) AS plus_di14,
                    100 * minus_dm14 / nullIf(atr14, 0) AS minus_di14,
                    atr14 / nullIf(day_close, 0.01) AS atr_pct
                FROM scored_base
            ),
            scored AS (
                SELECT *,
                    100 * abs(plus_di14 - minus_di14) / greatest(plus_di14 + minus_di14, 0.000001) AS adx14,
                    day_close > sma50 AND sma50 > sma200 AND adx14 >= 20 AS trend_regime,
                    day_close <= high_20d AND breakout_pct <= 1.0 AND volume_ratio >= 1.2 AS regime_breakout_core
                FROM scored_indicators
            ),
            scored_cross AS (
                SELECT *,
                    toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret60)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs60_rank,
                    avg(if(day_close > sma200, 1.0, 0.0)) OVER (PARTITION BY trade_date) AS market_breadth200
                FROM scored
            ),
            signals AS (
                SELECT s.symbol, s.trade_date AS signal_date, s.rn AS signal_rn,
                    s.day_open, s.day_high, s.day_low, s.day_close, s.day_volume, s.sma20, s.sma50, s.sma200, s.avg_volume20, s.high_20d, s.high_52w, s.low_52w, s.rsi10, s.rsi14, s.breakout_pct, s.distance_to_52w_high_pct, s.range_position_pct, s.volume_ratio, s.atr14, s.atr_pct, s.avg_range_pct20, s.min_range_pct7, s.ret3, s.ret20, s.ret60, s.zscore20, s.dist_sma20_atr, s.close_location, s.range_atr, s.recovery_from_low_pct, s.adx14, s.trend_regime, s.regime_breakout_core, s.prior_high20, s.prior_high55, s.rs60_rank, s.market_breadth200, s.trend_up, s.pullback_zone, s.rsi10_pullback,
                    s.day_close > s.sma200 AND s.sma20 > s.sma50 AND (s.sma20 - s.day_close) > 2.2 * s.atr14 AND s.rsi10 < 35 AS atr_stretch_liquid_only,
                    s.day_close > s.sma200 AND not(s.trend_regime) AND s.adx14 < 30 AND s.zscore20 < -2.5 AND s.rsi14 < 30 AND s.dist_sma20_atr < -1.5 AND s.close_location >= 0.35 AS regime_mean_reversion,
                    s.trend_regime AND s.regime_breakout_core AND s.ret60 >= 0.10 AND s.close_location >= 0.85 AND s.volume_ratio >= 1.4 AS regime_trend_breakout,
                    s.trend_regime AND s.day_close > s.prior_high55 AND s.volume_ratio >= 2.6 AND s.ret60 >= 0.05 AND s.close_location >= 0.85 AND s.atr_pct > s.avg_range_pct20 AS regime_breakout_volume,
                    toUInt8(
                        if(s.ret20 > 0, 1, 0) + if(s.ret60 > 0.10, 1, 0) + if(s.range_position_pct >= 70, 1, 0) +
                        if(s.day_close > s.sma50, 1, 0) + if(s.sma50 > s.sma200, 1, 0) + if(s.day_close > s.sma200, 1, 0) +
                        if(s.volume_ratio > 1.0, 1, 0) + if(s.volume_ratio > 1.4, 1, 0) +
                        if(s.rsi14 >= 45 AND s.rsi14 <= 70, 1, 0) + if(s.zscore20 >= -0.8 AND s.zscore20 <= 1.8, 1, 0) + if(s.close_location > 0.55, 1, 0) -
                        if(s.atr_pct > 0.08, 1, 0)
                    ) AS regime_multifactor_score,
                    s.trend_up AND s.breakout_pct <= 1.5 AND s.volume_ratio >= 1.1 AND ((s.day_high - s.day_low) / nullIf(s.day_close, 0.01)) <= s.avg_range_pct20 * 0.75 AS compression_breakout,
                    s.day_close > s.sma50 AND s.sma50 > s.sma200 AND s.ret60 > 0.08 AND s.pullback_zone AND s.volume_ratio >= 0.7 AND s.volume_ratio <= 1.5 AND s.range_position_pct >= 55 AS strong_stock_pullback,
                    s.day_low < s.prior_low20 AND s.day_close > s.prior_low20 AND s.day_close > s.sma20 AND s.prev_close <= s.prev_sma20 AND s.volume_ratio >= 0.9 AND ((s.day_close - s.day_low) / nullIf(s.day_high - s.day_low, 0.01)) >= 0.70 AS trend_reversal_breakout,
                    s.trend_up AND s.market_breadth200 >= 0.38 AND s.rs60_rank >= 0.58 AND s.volume_ratio >= 1.3 AND s.close_location >= 0.58 AND s.atr14 > 0 AND s.day_high >= s.prior_high20 * 1.001 AND s.day_close >= s.prior_high20 * 1.001 * 0.985 AS tuned_ma_breakout,
                    s.ret3 <= -0.08 AND s.range_atr >= 1.35 AND s.close_location >= 0.64 AND s.recovery_from_low_pct >= 0.012 AND s.atr14 > 0 AS tuned_panic_reversal,
                    multiIf(tuned_panic_reversal, 'Panic Reversal', tuned_ma_breakout, 'MA Breakout', regime_mean_reversion, 'Regime Mean Reversion', regime_trend_breakout, 'Regime Trend Breakout', regime_breakout_volume, 'Regime Breakout Volume', regime_multifactor_score >= 9 AND regime_breakout_core, 'Regime Multi-Factor Score', rsi10_pullback, 'RSI10 Pullback Reversion', atr_stretch_liquid_only, 'ATR Stretch Liquid Only', trend_reversal_breakout, 'Trend Reversal Breakout', compression_breakout, 'Compression Breakout', strong_stock_pullback, 'Strong Stock Pullback', trend_up AND breakout_pct <= 1.5 AND volume_ratio >= 1.1, 'Breakout Setup', trend_up AND pullback_zone, 'Pullback To 20 DMA', day_close > sma50 AND distance_to_52w_high_pct <= 8.0, 'Near 52W High', 'Trend Filter') AS setup_family,
                    toUInt8(greatest(50, least(96, 50 + if(trend_up, 18, 0) + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0)) + if(distance_to_52w_high_pct <= 8.0, 10, 0) + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0)) + if(pullback_zone, 8, 0) + if(rsi10_pullback, 16, 0) + if(atr_stretch_liquid_only, 24, 0) + if(regime_mean_reversion, 28, 0) + if(regime_trend_breakout, 18, 0) + if(regime_breakout_volume, 18, 0) + if(regime_multifactor_score >= 9, 16, 0) + if(compression_breakout, 18, 0) + if(strong_stock_pullback, 16, 0) + if(trend_reversal_breakout, 24, 0) + if(range_position_pct >= 70.0, 6, 0)))) AS score
                FROM scored_cross s
            ),
            entries_raw AS ({entries_cte}),
            strategy_ranked_entries AS (
                SELECT *
                FROM (
                    SELECT *, row_number() OVER (PARTITION BY strategy_id, signal_date ORDER BY entry_score DESC, rank_volume_ratio DESC, entry_symbol ASC) AS daily_entry_rank
                    FROM entries_raw
                )
                WHERE daily_entry_rank <= max_positions_per_day
            ),
            deduped_entries AS (
                SELECT *
                FROM (
                    SELECT *, row_number() OVER (PARTITION BY entry_date, entry_symbol ORDER BY entry_score DESC, rank_volume_ratio DESC, strategy_id ASC) AS symbol_entry_rank
                    FROM strategy_ranked_entries
                )
                WHERE symbol_entry_rank = 1
            ),
            entries AS (
                SELECT *
                FROM (
                    SELECT *, row_number() OVER (PARTITION BY entry_date ORDER BY entry_score DESC, rank_volume_ratio DESC, entry_symbol ASC) AS portfolio_entry_rank
                    FROM deduped_entries
                )
                WHERE portfolio_entry_rank <= {BACKTEST_MAX_NEW_POSITIONS_PER_DAY}
            ),
            exits AS (
                SELECT e.strategy_id, e.entry_symbol AS symbol, e.signal_date, e.entry_setup_family AS setup_family, e.entry_score AS score, e.entry_date, e.entry_price, e.quantity, e.capital_per_trade, e.tp_pct, e.sl_pct, e.max_hold_sessions,
                    minIf(f.trade_date, e.strategy_id = 'rsi10-pullback-reversion-v1' AND f.rsi10 > 40) AS rsi_exit_date,
                    argMinIf(f.day_close, f.rn, e.strategy_id = 'rsi10-pullback-reversion-v1' AND f.rsi10 > 40) AS rsi_exit_price,
                    minIf(f.trade_date, f.day_low <= e.entry_price * (1 - e.sl_pct / 100.0)) AS stop_date,
                    minIf(f.trade_date, f.day_high >= e.entry_price * (1 + e.tp_pct / 100.0)) AS target_date,
                    argMax(f.day_close, f.rn) AS time_exit_price,
                    max(f.trade_date) AS time_exit_date
                FROM entries e
                INNER JOIN features f ON f.symbol = e.entry_symbol
                WHERE f.rn >= e.entry_rn AND f.rn < e.entry_rn + e.max_hold_sessions
                GROUP BY e.strategy_id, e.entry_symbol, e.signal_date, e.entry_setup_family, e.entry_score, e.entry_date, e.entry_price, e.quantity, e.capital_per_trade, e.tp_pct, e.sl_pct, e.max_hold_sessions
            ),
            trades AS (
                SELECT strategy_id, symbol, signal_date, entry_date,
                    multiIf(
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), stop_date,
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), target_date,
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), rsi_exit_date,
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), stop_date,
                        target_date != toDate('1970-01-01'), target_date,
                        time_exit_date
                    ) AS exit_date,
                    setup_family, entry_price,
                    multiIf(
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), entry_price * (1 - sl_pct / 100.0),
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), entry_price * (1 + tp_pct / 100.0),
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), rsi_exit_price,
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), entry_price * (1 - sl_pct / 100.0),
                        target_date != toDate('1970-01-01'), entry_price * (1 + tp_pct / 100.0),
                        time_exit_price
                    ) AS exit_price,
                    quantity, quantity * entry_price AS capital_used, (exit_price - entry_price) * quantity AS pnl, ((exit_price - entry_price) / entry_price) * 100.0 AS return_pct,
                    multiIf(
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), 'SL',
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), 'TP',
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), 'RSI40',
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), 'SL',
                        target_date != toDate('1970-01-01'), 'TP',
                        'TIME'
                    ) AS exit_reason,
                    toUInt16(dateDiff('day', entry_date, exit_date) + 1) AS hold_sessions, score
                FROM exits
                WHERE exit_date >= entry_date AND exit_price > 0
            )
        SELECT '{escaped_run_id}' AS run_id, strategy_id, symbol, signal_date, entry_date, exit_date, setup_family, entry_price, exit_price, quantity, capital_used, pnl, return_pct, exit_reason, hold_sessions, score
        FROM trades
    """
    await ch.command(query)


async def fetch_summaries(ch: ClickHouseRepo, run_id: str) -> list[BacktestRunSummary]:
    rows = await ch.query_rows(
        "SELECT "
        "strategy_id, "
        "strategy_id AS strategy_name, "
        "toUInt32(count()) AS total_trades, "
        "round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, "
        "round(avg(return_pct), 3) AS avg_return_pct, "
        "round(sum(pnl), 2) AS total_pnl, "
        "round(100 * sum(pnl) / sum(capital_used), 3) AS deployed_return_pct, "
        "round(avg(hold_sessions), 2) AS avg_hold_sessions, "
        "countIf(exit_reason = 'TP') AS tp_exits, "
        "countIf(exit_reason = 'SL') AS sl_exits, "
        "countIf(exit_reason = 'TIME') AS time_exits, "
        "countIf(exit_reason = 'RSI40') AS rsi_exits, "
        "toString(min(entry_date)) AS from_date, "
        "toString(max(exit_date)) AS to_date "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        "GROUP BY strategy_id ORDER BY total_pnl DESC"
    )
    return [BacktestRunSummary(**row) for row in rows]


async def fetch_yearly_returns(ch: ClickHouseRepo, run_id: str) -> list[BacktestYearlyReturn]:
    rows = await ch.query_rows(
        "SELECT strategy_id, year, trades, win_rate, avg_return_pct, yearly_pnl AS pnl, return_pct "
        "FROM ( "
        "SELECT strategy_id, toUInt16(toYear(entry_date)) AS year, toUInt32(count()) AS trades, "
        "round(100 * countIf(trade_pnl > 0) / count(), 2) AS win_rate, "
        "round(avg(trade_return_pct), 3) AS avg_return_pct, "
        "round(sum(trade_pnl), 2) AS yearly_pnl, "
        f"round(100 * sum(trade_pnl) / greatest({_ACTIVE_CAPITAL}, 1), 3) AS return_pct "
        "FROM ( "
        "SELECT strategy_id, entry_date, pnl AS trade_pnl, return_pct AS trade_return_pct, capital_used "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        ") GROUP BY strategy_id, year "
        ") ORDER BY strategy_id, year"
    )
    return [BacktestYearlyReturn(**row) for row in rows]


async def fetch_monthly_returns(ch: ClickHouseRepo, run_id: str) -> list[BacktestMonthlyReturn]:
    rows = await ch.query_rows(
        "SELECT strategy_id, year, month, month_label, trades, win_rate, monthly_pnl AS pnl, return_pct "
        "FROM ( "
        "SELECT strategy_id, toUInt16(toYear(entry_date)) AS year, toUInt8(toMonth(entry_date)) AS month, "
        "formatDateTime(entry_date, '%b') AS month_label, toUInt32(count()) AS trades, "
        "round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, "
        "round(sum(pnl), 2) AS monthly_pnl, "
        f"round(100 * sum(pnl) / greatest({_ACTIVE_CAPITAL}, 1), 3) AS return_pct "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        "GROUP BY strategy_id, year, month, month_label "
        ") ORDER BY strategy_id, year, month"
    )
    return [BacktestMonthlyReturn(**row) for row in rows]


async def fetch_equity_curve(ch: ClickHouseRepo, run_id: str) -> list[BacktestEquityPoint]:
    rows = await ch.query_rows(
        "WITH daily AS ( "
        "SELECT strategy_id, entry_date AS d, round(sum(pnl), 2) AS daily_pnl "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        "GROUP BY strategy_id, d "
        "), equity AS ( "
        "SELECT strategy_id, d, daily_pnl, "
        "sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS cumulative_pnl "
        "FROM daily "
        "), dd AS ( "
        "SELECT strategy_id, d, daily_pnl, cumulative_pnl, "
        "max(cumulative_pnl) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak "
        "FROM equity "
        ") "
        "SELECT strategy_id, toString(d) AS trade_date, round(daily_pnl, 2) AS daily_pnl, "
        "round(cumulative_pnl, 2) AS cumulative_pnl, round(cumulative_pnl - peak, 2) AS drawdown_rs, "
        f"round(100 * cumulative_pnl / greatest({_ACTIVE_CAPITAL}, 1), 3) AS cumulative_return_pct "
        "FROM dd ORDER BY strategy_id, d"
    )
    return [BacktestEquityPoint(**row) for row in rows]


async def fetch_strategy_diagnostics(
    ch: ClickHouseRepo, run_id: str
) -> list[BacktestStrategyDiagnostic]:
    escaped_run_id = escape_sql(run_id)
    rows = await ch.query_rows(f"""
        WITH strategy_stats AS (
            SELECT
                strategy_id,
                toUInt32(count()) AS total_trades,
                round(sum(pnl), 2) AS total_pnl,
                round(100 * countIf(pnl > 0) / count(), 2) AS win_rate,
                sumIf(pnl, pnl > 0) AS gross_profit,
                abs(sumIf(pnl, pnl < 0)) AS gross_loss,
                round(avg(return_pct), 3) AS expectancy_pct,
                ifNull(avgIf(return_pct, pnl > 0), 0) AS avg_win_pct,
                ifNull(avgIf(return_pct, pnl < 0), 0) AS avg_loss_pct,
                min(entry_date) AS first_entry_date,
                max(exit_date) AS last_exit_date
            FROM trading.backtest_trades
            WHERE run_id = '{escaped_run_id}'
            GROUP BY strategy_id
        ), monthly AS (
            SELECT strategy_id, toYYYYMM(entry_date) AS month_key, sum(pnl) AS monthly_pnl
            FROM trading.backtest_trades
            WHERE run_id = '{escaped_run_id}'
            GROUP BY strategy_id, month_key
        ), monthly_stats AS (
            SELECT
                strategy_id,
                round(100 * countIf(monthly_pnl > 0) / count(), 2) AS positive_months_pct,
                round(quantileExact(0.5)(monthly_pnl), 2) AS median_monthly_pnl,
                round(min(monthly_pnl), 2) AS worst_month,
                round(max(monthly_pnl), 2) AS best_month
            FROM monthly
            GROUP BY strategy_id
        ), daily AS (
            SELECT strategy_id, entry_date AS d, sum(pnl) AS daily_pnl
            FROM trading.backtest_trades
            WHERE run_id = '{escaped_run_id}'
            GROUP BY strategy_id, d
        ), equity AS (
            SELECT strategy_id, d, daily_pnl, sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS equity
            FROM daily
        ), dd AS (
            SELECT strategy_id, d, daily_pnl, equity, max(equity) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak
            FROM equity
        ), drawdowns AS (
            SELECT strategy_id, round(min(equity - peak), 2) AS max_drawdown_rs
            FROM dd
            GROUP BY strategy_id
        ), daily_stats AS (
            SELECT
                strategy_id,
                avg(daily_pnl / greatest({_ACTIVE_CAPITAL}, 1)) AS avg_daily_return,
                stddevPop(daily_pnl / greatest({_ACTIVE_CAPITAL}, 1)) AS std_daily_return,
                stddevPopIf(daily_pnl / greatest({_ACTIVE_CAPITAL}, 1), daily_pnl < 0) AS downside_daily_return
            FROM daily
            GROUP BY strategy_id
        ), trade_sequence AS (
            SELECT
                strategy_id,
                pnl,
                sum(if(pnl > 0, 1, 0)) OVER (PARTITION BY strategy_id ORDER BY entry_date, symbol ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS win_bucket
            FROM trading.backtest_trades
            WHERE run_id = '{escaped_run_id}'
        ), losing_streaks AS (
            SELECT strategy_id, toUInt32(max(streak_len)) AS max_losing_streak
            FROM (
                SELECT strategy_id, win_bucket, count() AS streak_len
                FROM trade_sequence
                WHERE pnl <= 0
                GROUP BY strategy_id, win_bucket
            )
            GROUP BY strategy_id
        )
        SELECT
            strategy_id, method_family, total_trades, total_pnl, win_rate, profit_factor, expectancy_pct,
            annualized_return_pct, max_drawdown_pct, sharpe_ratio, sortino_ratio, avg_win_pct, avg_loss_pct,
            payoff_ratio, max_losing_streak, recovery_factor,
            positive_months_pct, median_monthly_pnl, worst_month, best_month, max_drawdown_rs,
            round(raw_stability_score, 2) AS stability_score,
            multiIf(total_trades < {MIN_BACKTEST_TRADES_FOR_VALIDATION}, 'Fragile', total_pnl <= 0, 'Rejected', raw_stability_score >= 56 AND positive_months_pct >= 55, 'Candidate', raw_stability_score >= 50, 'Watch', 'Fragile') AS status
        FROM (
            SELECT
                s.strategy_id AS strategy_id,
                multiIf(
                    position(s.strategy_id, 'regime-mean') > 0, 'Regime Mean Reversion',
                    position(s.strategy_id, 'regime-trend') > 0, 'Regime Trend',
                    position(s.strategy_id, 'regime-breakout') > 0, 'Regime Breakout',
                    position(s.strategy_id, 'regime-multifactor') > 0, 'Multi-Factor',
                    position(s.strategy_id, 'reversal') > 0, 'Reversal',
                    position(s.strategy_id, 'breakout') > 0, 'Breakout',
                    position(s.strategy_id, 'pullback') > 0, 'Pullback',
                    position(s.strategy_id, 'stretch') > 0, 'Mean Reversion',
                    position(s.strategy_id, 'rsi10') > 0, 'Mean Reversion',
                    position(s.strategy_id, '52w') > 0, '52W Momentum',
                    position(s.strategy_id, 'momentum') > 0, 'Momentum',
                    'Other'
                ) AS method_family,
                s.total_trades AS total_trades,
                s.total_pnl AS total_pnl,
                s.win_rate AS win_rate,
                round(if(s.gross_loss = 0, if(s.gross_profit > 0, 99, 0), s.gross_profit / s.gross_loss), 2) AS profit_factor,
                s.expectancy_pct AS expectancy_pct,
                round(if(100 * s.total_pnl / greatest({_ACTIVE_CAPITAL}, 1) <= -99.9, -100, (pow(1 + s.total_pnl / greatest({_ACTIVE_CAPITAL}, 1), 365.25 / greatest(dateDiff('day', s.first_entry_date, s.last_exit_date) + 1, 1)) - 1) * 100), 2) AS annualized_return_pct,
                round(100 * d.max_drawdown_rs / greatest({_ACTIVE_CAPITAL}, 1), 2) AS max_drawdown_pct,
                round(if(ds.std_daily_return = 0, 0, ds.avg_daily_return / ds.std_daily_return * sqrt(252)), 2) AS sharpe_ratio,
                round(if(ds.downside_daily_return = 0, 0, ds.avg_daily_return / ds.downside_daily_return * sqrt(252)), 2) AS sortino_ratio,
                round(s.avg_win_pct, 3) AS avg_win_pct,
                round(s.avg_loss_pct, 3) AS avg_loss_pct,
                round(if(abs(s.avg_loss_pct) = 0, if(s.avg_win_pct > 0, 99, 0), s.avg_win_pct / abs(s.avg_loss_pct)), 2) AS payoff_ratio,
                ifNull(ls.max_losing_streak, 0) AS max_losing_streak,
                round(if(d.max_drawdown_rs = 0, if(s.total_pnl > 0, 99, 0), s.total_pnl / abs(d.max_drawdown_rs)), 2) AS recovery_factor,
                m.positive_months_pct AS positive_months_pct,
                m.median_monthly_pnl AS median_monthly_pnl,
                m.worst_month AS worst_month,
                m.best_month AS best_month,
                d.max_drawdown_rs AS max_drawdown_rs,
                greatest(0, least(100,
                    m.positive_months_pct * 0.42
                    + s.win_rate * 0.28
                    + least(18, if(s.gross_loss = 0, 18, (s.gross_profit / s.gross_loss) * 8))
                    + if(s.total_pnl > 0, 8, -18)
                    - least(22, abs(d.max_drawdown_rs) / greatest(abs(s.total_pnl), 1) * 12)
                )) AS raw_stability_score
            FROM strategy_stats s
            INNER JOIN monthly_stats m ON m.strategy_id = s.strategy_id
            INNER JOIN drawdowns d ON d.strategy_id = s.strategy_id
            INNER JOIN daily_stats ds ON ds.strategy_id = s.strategy_id
            LEFT JOIN losing_streaks ls ON ls.strategy_id = s.strategy_id
        )
        ORDER BY status ASC, stability_score DESC, total_pnl DESC
    """)
    return [BacktestStrategyDiagnostic(**row) for row in rows]


async def fetch_symbol_results(
    ch: ClickHouseRepo, run_id: str, losers: bool
) -> list[BacktestSymbolResult]:
    ordering = (
        "symbol_pnl ASC, win_rate ASC, avg_return_pct ASC"
        if losers
        else "win_rate DESC, avg_return_pct DESC, symbol_pnl DESC"
    )
    rows = await ch.query_rows(
        "SELECT strategy_id, symbol, trades, win_rate, symbol_pnl AS pnl, avg_return_pct "
        "FROM ( "
        f"SELECT *, row_number() OVER (PARTITION BY strategy_id ORDER BY {ordering}) AS edge_rank "
        "FROM ( "
        "SELECT strategy_id, symbol, toUInt32(count()) AS trades, "
        "round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, "
        "round(sum(pnl), 2) AS symbol_pnl, "
        "round(avg(return_pct), 3) AS avg_return_pct "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        "GROUP BY strategy_id, symbol HAVING trades >= 5 "
        ") "
        ") WHERE edge_rank <= 12 ORDER BY strategy_id ASC, edge_rank ASC"
    )
    return [BacktestSymbolResult(**row) for row in rows]


async def fetch_day_quality(ch: ClickHouseRepo, run_id: str) -> list[BacktestDayQuality]:
    rows = await ch.query_rows(
        "WITH daily AS ( "
        "SELECT strategy_id, entry_date AS d, sum(pnl) AS daily_pnl "
        f"FROM trading.backtest_trades WHERE run_id = '{escape_sql(run_id)}' "
        "GROUP BY strategy_id, d "
        "), equity AS ( "
        "SELECT strategy_id, d, daily_pnl, sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS equity "
        "FROM daily "
        "), dd AS ( "
        "SELECT strategy_id, d, daily_pnl, equity, "
        "max(equity) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak "
        "FROM equity "
        ") "
        "SELECT strategy_id, count() AS trading_days, "
        "round(100 * countIf(daily_pnl > 0) / count(), 2) AS positive_days_pct, "
        "round(min(daily_pnl), 2) AS worst_day, round(max(daily_pnl), 2) AS best_day, "
        "round(min(equity - peak), 2) AS max_drawdown_rs "
        "FROM dd GROUP BY strategy_id ORDER BY max_drawdown_rs DESC"
    )
    return [BacktestDayQuality(**row) for row in rows]


async def fetch_available_entry_dates(ch: ClickHouseRepo, run_id: str) -> list[str]:
    exclusion = deprecated_strategy_sql_clause("t")
    rows = await ch.query_rows(
        "SELECT toString(entry_date) AS trade_date "
        f"FROM trading.backtest_trades AS t WHERE t.run_id = '{escape_sql(run_id)}'{exclusion} "
        "GROUP BY entry_date ORDER BY entry_date DESC LIMIT 120"
    )
    return [row["trade_date"] for row in rows]


def _date_strategy_clause(strategy: str) -> str:
    excluded = deprecated_strategy_sql_clause("t")
    if strategy == "all":
        return excluded
    return f"{excluded} AND t.strategy_id = '{escape_sql(strategy)}'"


async def fetch_date_trade_count(
    ch: ClickHouseRepo, run_id: str, entry_date: str, strategy: str
) -> int:
    rows = await ch.query_rows(
        "SELECT count() AS cnt FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}' AND t.entry_date = toDate('{escape_sql(entry_date)}')"
        f"{_date_strategy_clause(strategy)}"
    )
    return int(rows[0]["cnt"]) if rows else 0


async def fetch_date_summary(
    ch: ClickHouseRepo, run_id: str, entry_date: str, strategy: str
) -> BacktestDateSummary | None:
    rows = await ch.query_rows(
        "SELECT "
        "toString(t.entry_date) AS trade_date, "
        "toUInt64(count()) AS total_trades, "
        "toUInt64(countIf(t.pnl > 0)) AS winners, "
        "toUInt64(countIf(t.pnl < 0)) AS losers, "
        "round(100 * countIf(t.pnl > 0) / count(), 2) AS win_rate, "
        "round(sum(t.pnl), 2) AS total_pnl, "
        "round(avg(t.return_pct), 3) AS avg_return_pct, "
        "argMax(t.symbol, t.pnl) AS best_symbol, "
        "round(max(t.pnl), 2) AS best_pnl, "
        "argMin(t.symbol, t.pnl) AS worst_symbol, "
        "round(min(t.pnl), 2) AS worst_pnl "
        "FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}' AND t.entry_date = toDate('{escape_sql(entry_date)}')"
        f"{_date_strategy_clause(strategy)} "
        "GROUP BY t.entry_date"
    )
    return BacktestDateSummary(**rows[0]) if rows else None


async def fetch_date_strategy_summaries(
    ch: ClickHouseRepo, run_id: str, entry_date: str
) -> list[BacktestDateStrategySummary]:
    exclusion = deprecated_strategy_sql_clause("t")
    rows = await ch.query_rows(
        "SELECT "
        "t.strategy_id, "
        "any(t.setup_family) AS setup_family, "
        "toUInt64(count()) AS trades, "
        "round(100 * countIf(t.pnl > 0) / count(), 2) AS win_rate, "
        "round(sum(t.pnl), 2) AS pnl, "
        "argMax(t.symbol, t.pnl) AS best_symbol, "
        "round(max(t.pnl), 2) AS best_pnl, "
        "argMin(t.symbol, t.pnl) AS worst_symbol, "
        "round(min(t.pnl), 2) AS worst_pnl "
        "FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}' AND t.entry_date = toDate('{escape_sql(entry_date)}')"
        f"{exclusion} "
        "GROUP BY t.strategy_id ORDER BY pnl DESC"
    )
    return [BacktestDateStrategySummary(**row) for row in rows]


async def fetch_date_trades(
    ch: ClickHouseRepo,
    run_id: str,
    entry_date: str,
    strategy: str,
    order_by: str,
    limit: int,
    offset: int,
) -> list[BacktestTradeLogRow]:
    rows = await ch.query_rows(
        "SELECT "
        "t.strategy_id, t.symbol, "
        "toString(t.signal_date) AS signal_date, "
        "toString(t.entry_date) AS entry_date, "
        "toString(t.exit_date) AS exit_date, "
        "t.setup_family, t.entry_price, t.exit_price, t.quantity, "
        "round(t.pnl, 2) AS pnl, round(t.return_pct, 3) AS return_pct, "
        "t.exit_reason, t.hold_sessions, t.score "
        "FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}' AND t.entry_date = toDate('{escape_sql(entry_date)}')"
        f"{_date_strategy_clause(strategy)} "
        f"ORDER BY {order_by} LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return [BacktestTradeLogRow(**row) for row in rows]


async def fetch_trade_log(ch: ClickHouseRepo, run_id: str) -> list[BacktestTradeLogRow]:
    exclusion = deprecated_strategy_sql_clause("t")
    rows = await ch.query_rows(
        "SELECT "
        "t.strategy_id, t.symbol, "
        "toString(t.signal_date) AS signal_date, "
        "toString(t.entry_date) AS entry_date, "
        "toString(t.exit_date) AS exit_date, "
        "t.setup_family, t.entry_price, t.exit_price, t.quantity, "
        "round(t.pnl, 2) AS pnl, round(t.return_pct, 3) AS return_pct, "
        "t.exit_reason, t.hold_sessions, t.score "
        "FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}'{exclusion} "
        "ORDER BY t.entry_date DESC, abs(t.pnl) DESC LIMIT 80"
    )
    return [BacktestTradeLogRow(**row) for row in rows]


async def fetch_analysis_trades(ch: ClickHouseRepo, run_id: str) -> list[BacktestAnalysisTrade]:
    exclusion = deprecated_strategy_sql_clause("t")
    rows = await ch.query_rows(
        "SELECT "
        "t.strategy_id, t.symbol, "
        "toString(t.signal_date) AS signal_date, "
        "toString(t.entry_date) AS entry_date, "
        "toString(t.exit_date) AS exit_date, "
        "t.setup_family, t.capital_used, t.pnl, t.score "
        "FROM trading.backtest_trades AS t "
        f"WHERE t.run_id = '{escape_sql(run_id)}'{exclusion} "
        "ORDER BY t.entry_date ASC, t.score DESC, t.strategy_id ASC, t.symbol ASC"
    )

    out: list[BacktestAnalysisTrade] = []
    for row in rows:
        # ClickHouse's toString(date) output is always ISO "YYYY-MM-DD" here.
        try:
            signal_date = date.fromisoformat(row["signal_date"])
            entry_date = date.fromisoformat(row["entry_date"])
            exit_date = date.fromisoformat(row["exit_date"])
        except ValueError:
            continue
        out.append(
            BacktestAnalysisTrade(
                method_family=strategy_method_family(row["strategy_id"]),
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                signal_date=signal_date,
                entry_date=entry_date,
                exit_date=exit_date,
                setup_family=row["setup_family"],
                capital_used=row["capital_used"],
                pnl=row["pnl"],
                score=row["score"],
            )
        )
    return out
