WITH
    strategies AS (
        SELECT 'swing-breakout-v1' AS strategy_id, 'Swing Breakout V1' AS strategy_name, 'Breakout Setup' AS setup_family, 85 AS min_score, 8.0 AS tp_pct, 4.0 AS sl_pct, 10 AS max_hold_sessions, 50000.0 AS capital_per_trade
        UNION ALL
        SELECT 'pullback-20dma-v1', 'Pullback To 20 DMA V1', 'Pullback To 20 DMA', 80, 6.0, 3.0, 10, 50000.0
        UNION ALL
        SELECT 'near-52w-high-v1', 'Near 52W High V1', 'Near 52W High', 80, 10.0, 5.0, 15, 50000.0
    ),
    daily AS (
        SELECT
            symbol,
            toDate(date) AS trade_date,
            argMin(open, bucket) AS day_open,
            max(high) AS day_high,
            min(low) AS day_low,
            argMax(close, bucket) AS day_close,
            toFloat64(sum(volume)) AS day_volume
        FROM file('parquets/candles_*.parquet', Parquet)
        WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1)
          AND date IS NOT NULL
          AND symbol IS NOT NULL
          AND open IS NOT NULL
          AND high IS NOT NULL
          AND low IS NOT NULL
          AND close IS NOT NULL
          AND volume IS NOT NULL
        GROUP BY symbol, trade_date
    ),
    features AS (
        SELECT
            symbol,
            trade_date,
            day_open,
            day_high,
            day_low,
            day_close,
            day_volume,
            row_number() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn,
            avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
            avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
            avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20,
            max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d,
            max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
            min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w
        FROM daily
    ),
    scored AS (
        SELECT
            *,
            ((high_20d - day_close) / nullIf(high_20d, 0)) * 100.0 AS breakout_pct,
            ((high_52w - day_close) / nullIf(high_52w, 0)) * 100.0 AS distance_to_52w_high_pct,
            ((day_close - low_52w) / nullIf(high_52w - low_52w, 0.01)) * 100.0 AS range_position_pct,
            day_volume / greatest(avg_volume20, 1.0) AS volume_ratio,
            day_close > sma20 AND sma20 > sma50 AS trend_up,
            day_close >= sma20 * 0.98 AND day_close <= sma20 * 1.03 AS pullback_zone
        FROM features
        WHERE rn >= 252
          AND day_close >= 50
          AND avg_volume20 >= 100000
    ),
    signals AS (
        SELECT
            s.symbol,
            s.trade_date AS signal_date,
            s.rn AS signal_rn,
            multiIf(
                trend_up AND breakout_pct <= 1.5 AND volume_ratio >= 1.1, 'Breakout Setup',
                trend_up AND pullback_zone, 'Pullback To 20 DMA',
                day_close > sma50 AND distance_to_52w_high_pct <= 8.0, 'Near 52W High',
                'Trend Filter'
            ) AS setup_family,
            toUInt8(greatest(50, least(96,
                50
                + if(trend_up, 18, 0)
                + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0))
                + if(distance_to_52w_high_pct <= 8.0, 10, 0)
                + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0))
                + if(pullback_zone, 8, 0)
                + if(range_position_pct >= 70.0, 6, 0)
            ))) AS score
        FROM scored s
    ),
    entries AS (
        SELECT
            st.strategy_id,
            st.strategy_name,
            st.tp_pct,
            st.sl_pct,
            st.max_hold_sessions,
            st.capital_per_trade,
            sig.symbol AS entry_symbol,
            sig.signal_date,
            sig.setup_family AS entry_setup_family,
            sig.score AS entry_score,
            e.trade_date AS entry_date,
            e.rn AS entry_rn,
            toFloat64(e.day_open) AS entry_price,
            toUInt32(greatest(1, floor(st.capital_per_trade / nullIf(toFloat64(e.day_open), 0)))) AS quantity
        FROM signals sig
        INNER JOIN strategies st
            ON sig.setup_family = st.setup_family
        INNER JOIN features e
            ON e.symbol = sig.symbol
           AND e.rn = sig.signal_rn + 1
        WHERE e.day_open > 0
          AND sig.score >= st.min_score
    ),
    exits AS (
        SELECT
            e.strategy_id,
            e.strategy_name,
            e.entry_symbol AS symbol,
            e.signal_date,
            e.entry_setup_family AS setup_family,
            e.entry_score AS score,
            e.entry_date,
            e.entry_price,
            e.quantity,
            e.capital_per_trade,
            e.tp_pct,
            e.sl_pct,
            e.max_hold_sessions,
            minIf(f.trade_date, f.day_low <= e.entry_price * (1 - e.sl_pct / 100.0)) AS stop_date,
            minIf(f.trade_date, f.day_high >= e.entry_price * (1 + e.tp_pct / 100.0)) AS target_date,
            argMax(f.day_close, f.rn) AS time_exit_price,
            max(f.trade_date) AS time_exit_date
        FROM entries e
        INNER JOIN features f
            ON f.symbol = e.entry_symbol
        WHERE f.rn >= e.entry_rn
          AND f.rn < e.entry_rn + e.max_hold_sessions
        GROUP BY
            e.strategy_id,
            e.strategy_name,
            e.entry_symbol,
            e.signal_date,
            e.entry_setup_family,
            e.entry_score,
            e.entry_date,
            e.entry_price,
            e.quantity,
            e.capital_per_trade,
            e.tp_pct,
            e.sl_pct,
            e.max_hold_sessions
    ),
    trades AS (
        SELECT
            strategy_id,
            strategy_name,
            symbol,
            signal_date,
            entry_date,
            multiIf(
                stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), stop_date,
                target_date != toDate('1970-01-01'), target_date,
                time_exit_date
            ) AS exit_date,
            setup_family,
            entry_price,
            multiIf(
                stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), entry_price * (1 - sl_pct / 100.0),
                target_date != toDate('1970-01-01'), entry_price * (1 + tp_pct / 100.0),
                time_exit_price
            ) AS exit_price,
            quantity,
            quantity * entry_price AS capital_used,
            (exit_price - entry_price) * quantity AS pnl,
            ((exit_price - entry_price) / entry_price) * 100.0 AS return_pct,
            multiIf(
                stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), 'SL',
                target_date != toDate('1970-01-01'), 'TP',
                'TIME'
            ) AS exit_reason,
            toUInt16(dateDiff('day', entry_date, exit_date) + 1) AS hold_sessions,
            score
        FROM exits
    )
SELECT
    strategy_id,
    strategy_name,
    count() AS trades,
    round(100.0 * countIf(pnl > 0) / count(), 2) AS win_rate,
    round(avg(return_pct), 3) AS avg_return_pct,
    round(sum(pnl), 2) AS total_pnl,
    round(100.0 * sum(pnl) / nullIf(sum(capital_used), 0), 3) AS return_on_deployed_capital_pct,
    round(avg(hold_sessions), 2) AS avg_hold_sessions,
    countIf(exit_reason = 'TP') AS tp_exits,
    countIf(exit_reason = 'SL') AS sl_exits,
    countIf(exit_reason = 'TIME') AS time_exits,
    min(entry_date) AS from_date,
    max(exit_date) AS to_date
FROM trades
GROUP BY strategy_id, strategy_name
ORDER BY total_pnl DESC
FORMAT PrettyCompact
