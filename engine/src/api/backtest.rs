use axum::{extract::State, http::StatusCode, Json};
use clickhouse::Row;
use serde::{Deserialize, Serialize};

use crate::api::AppState;
use crate::types::now_ist;

const LATEST_RUN_ID: &str = "watchlist-swing-20260503-001";

const CREATE_BACKTEST_TRADES: &str = r#"
CREATE TABLE IF NOT EXISTS trading.backtest_trades (
    run_id              String,
    strategy_id         String,
    symbol              String,
    signal_date         Date,
    entry_date          Date,
    exit_date           Date,
    setup_family        String,
    entry_price         Float64,
    exit_price          Float64,
    quantity            UInt32,
    capital_used        Float64,
    pnl                 Float64,
    return_pct          Float64,
    exit_reason         String,
    hold_sessions       UInt16,
    score               UInt8
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(entry_date)
ORDER BY (strategy_id, run_id, entry_date, symbol)
"#;

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestRunSummary {
    strategy_id: String,
    strategy_name: String,
    total_trades: u32,
    win_rate: f64,
    avg_return_pct: f64,
    total_pnl: f64,
    deployed_return_pct: f64,
    avg_hold_sessions: f64,
    tp_exits: u64,
    sl_exits: u64,
    time_exits: u64,
    from_date: String,
    to_date: String,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestYearlyReturn {
    strategy_id: String,
    year: u16,
    trades: u32,
    win_rate: f64,
    avg_return_pct: f64,
    pnl: f64,
    return_pct: f64,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestMonthlyReturn {
    strategy_id: String,
    year: u16,
    month: u8,
    month_label: String,
    trades: u32,
    win_rate: f64,
    pnl: f64,
    return_pct: f64,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestSymbolResult {
    strategy_id: String,
    symbol: String,
    trades: u32,
    win_rate: f64,
    pnl: f64,
    avg_return_pct: f64,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestTradeLogRow {
    strategy_id: String,
    symbol: String,
    entry_date: String,
    exit_date: String,
    setup_family: String,
    entry_price: f64,
    exit_price: f64,
    quantity: u32,
    pnl: f64,
    return_pct: f64,
    exit_reason: String,
    hold_sessions: u16,
    score: u8,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestDayQuality {
    strategy_id: String,
    trading_days: u64,
    positive_days_pct: f64,
    worst_day: f64,
    best_day: f64,
    max_drawdown_rs: f64,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestStrategyDiagnostic {
    strategy_id: String,
    method_family: String,
    total_trades: u32,
    total_pnl: f64,
    win_rate: f64,
    profit_factor: f64,
    expectancy_pct: f64,
    positive_months_pct: f64,
    median_monthly_pnl: f64,
    worst_month: f64,
    best_month: f64,
    max_drawdown_rs: f64,
    stability_score: f64,
    status: String,
}

#[derive(Serialize)]
pub struct BacktestDashboardResponse {
    run_id: String,
    updated_at: String,
    summaries: Vec<BacktestRunSummary>,
    yearly_returns: Vec<BacktestYearlyReturn>,
    monthly_returns: Vec<BacktestMonthlyReturn>,
    diagnostics: Vec<BacktestStrategyDiagnostic>,
    winners: Vec<BacktestSymbolResult>,
    losers: Vec<BacktestSymbolResult>,
    day_quality: Vec<BacktestDayQuality>,
    trades: Vec<BacktestTradeLogRow>,
}

#[derive(Serialize)]
pub struct BacktestRunResponse {
    ok: bool,
    run_id: String,
    message: String,
    dashboard: BacktestDashboardResponse,
}

pub async fn dashboard(State(state): State<AppState>) -> Json<BacktestDashboardResponse> {
    let run_id = latest_run_id(&state)
        .await
        .unwrap_or_else(|err| {
            tracing::warn!("latest backtest run lookup failed: {}", err);
            LATEST_RUN_ID.to_string()
        });

    Json(build_dashboard(&state, &run_id).await)
}

pub async fn run(
    State(state): State<AppState>,
) -> Result<Json<BacktestRunResponse>, (StatusCode, String)> {
    ensure_tables(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest table setup failed: {err}")))?;

    let run_id = format!("watchlist-swing-{}", now_ist().format("%Y%m%d-%H%M%S"));
    execute_backtest_run(&state, &run_id)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest run failed: {err}")))?;

    let dashboard = build_dashboard(&state, &run_id).await;
    let trade_count: u32 = dashboard.summaries.iter().map(|summary| summary.total_trades).sum();

    Ok(Json(BacktestRunResponse {
        ok: true,
        run_id,
        message: format!("Backtest completed with {} stored trades.", trade_count),
        dashboard,
    }))
}

async fn build_dashboard(state: &AppState, run_id: &str) -> BacktestDashboardResponse {
    let summaries = fetch_summaries(state, run_id).await.unwrap_or_default();
    let yearly_returns = fetch_yearly_returns(state, run_id).await.unwrap_or_default();
    let monthly_returns = fetch_monthly_returns(state, run_id).await.unwrap_or_default();
    let diagnostics = fetch_strategy_diagnostics(state, run_id).await.unwrap_or_default();
    let winners = fetch_symbol_results(state, run_id, false).await.unwrap_or_default();
    let losers = fetch_symbol_results(state, run_id, true).await.unwrap_or_default();
    let day_quality = fetch_day_quality(state, run_id).await.unwrap_or_default();
    let trades = fetch_trade_log(state, run_id).await.unwrap_or_default();

    BacktestDashboardResponse {
        run_id: run_id.to_string(),
        updated_at: chrono::Utc::now().to_rfc3339(),
        summaries,
        yearly_returns,
        monthly_returns,
        diagnostics,
        winners,
        losers,
        day_quality,
        trades,
    }
}

async fn ensure_tables(state: &AppState) -> anyhow::Result<()> {
    state.ch.query(CREATE_BACKTEST_TRADES).execute().await?;
    Ok(())
}

async fn latest_run_id(state: &AppState) -> anyhow::Result<String> {
    let run_id = state
        .ch
        .query("SELECT run_id FROM trading.backtest_trades GROUP BY run_id ORDER BY run_id DESC LIMIT 1")
        .fetch_one::<String>()
        .await?;
    Ok(run_id)
}

async fn execute_backtest_run(state: &AppState, run_id: &str) -> anyhow::Result<()> {
    let escaped_run_id = run_id.replace('\'', "''");
    let query = format!(
        "INSERT INTO trading.backtest_trades \
        WITH \
            strategies AS ( \
                SELECT 'swing-breakout-v1' AS strategy_id, 'Swing Breakout V1' AS strategy_name, 'Breakout Setup' AS setup_family, 85 AS min_score, 8.0 AS tp_pct, 4.0 AS sl_pct, 10 AS max_hold_sessions, 50000.0 AS capital_per_trade \
                UNION ALL \
                SELECT 'breakout-volume-v2', 'Breakout Volume V2', 'Breakout Setup', 90, 10.0, 4.0, 12, 50000.0 \
                UNION ALL \
                SELECT 'pullback-20dma-v1', 'Pullback To 20 DMA V1', 'Pullback To 20 DMA', 80, 6.0, 3.0, 10, 50000.0 \
                UNION ALL \
                SELECT 'pullback-quality-v2', 'Pullback Quality V2', 'Pullback To 20 DMA', 88, 7.0, 3.0, 12, 50000.0 \
                UNION ALL \
                SELECT 'near-52w-high-v1', 'Near 52W High V1', 'Near 52W High', 80, 10.0, 5.0, 15, 50000.0 \
                UNION ALL \
                SELECT 'near-52w-high-tight-v2', 'Near 52W High Tight V2', 'Near 52W High', 88, 8.0, 4.0, 12, 50000.0 \
                UNION ALL \
                SELECT 'near-52w-high-runner-v2', 'Near 52W High Runner V2', 'Near 52W High', 90, 12.0, 5.0, 20, 50000.0 \
                UNION ALL \
                SELECT 'near-52w-high-volume-v3', 'Near 52W High Volume V3', 'Near 52W High', 88, 10.0, 4.5, 15, 50000.0 \
                UNION ALL \
                SELECT 'momentum-core-v1', 'Momentum Core V1', 'Near 52W High', 92, 15.0, 6.0, 25, 50000.0 \
            ), \
            daily AS ( \
                SELECT symbol, toDate(date) AS trade_date, argMin(open, bucket) AS day_open, max(high) AS day_high, min(low) AS day_low, argMax(close, bucket) AS day_close, toFloat64(sum(volume)) AS day_volume \
                FROM file('parquets/candles_*.parquet', Parquet) \
                WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1) \
                  AND date IS NOT NULL AND symbol IS NOT NULL AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL \
                GROUP BY symbol, trade_date \
            ), \
            features AS ( \
                SELECT symbol, trade_date, day_open, day_high, day_low, day_close, day_volume, \
                    row_number() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn, \
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20, \
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50, \
                    avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                    min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w \
                FROM daily \
            ), \
            scored AS ( \
                SELECT *, \
                    ((high_20d - day_close) / nullIf(high_20d, 0)) * 100.0 AS breakout_pct, \
                    ((high_52w - day_close) / nullIf(high_52w, 0)) * 100.0 AS distance_to_52w_high_pct, \
                    ((day_close - low_52w) / nullIf(high_52w - low_52w, 0.01)) * 100.0 AS range_position_pct, \
                    day_volume / greatest(avg_volume20, 1.0) AS volume_ratio, \
                    day_close > sma20 AND sma20 > sma50 AS trend_up, \
                    day_close >= sma20 * 0.98 AND day_close <= sma20 * 1.03 AS pullback_zone \
                FROM features \
                WHERE rn >= 252 AND day_close >= 50 AND avg_volume20 >= 100000 \
            ), \
            signals AS ( \
                SELECT s.symbol, s.trade_date AS signal_date, s.rn AS signal_rn, \
                    s.day_close, s.sma20, s.sma50, s.breakout_pct, s.distance_to_52w_high_pct, s.range_position_pct, s.volume_ratio, s.trend_up, s.pullback_zone, \
                    multiIf(trend_up AND breakout_pct <= 1.5 AND volume_ratio >= 1.1, 'Breakout Setup', trend_up AND pullback_zone, 'Pullback To 20 DMA', day_close > sma50 AND distance_to_52w_high_pct <= 8.0, 'Near 52W High', 'Trend Filter') AS setup_family, \
                    toUInt8(greatest(50, least(96, 50 + if(trend_up, 18, 0) + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0)) + if(distance_to_52w_high_pct <= 8.0, 10, 0) + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0)) + if(pullback_zone, 8, 0) + if(range_position_pct >= 70.0, 6, 0)))) AS score \
                FROM scored s \
            ), \
            entries AS ( \
                SELECT st.strategy_id, st.tp_pct, st.sl_pct, st.max_hold_sessions, st.capital_per_trade, sig.symbol AS entry_symbol, sig.signal_date, sig.setup_family AS entry_setup_family, sig.score AS entry_score, e.trade_date AS entry_date, e.rn AS entry_rn, toFloat64(e.day_open) AS entry_price, toUInt32(greatest(1, floor(st.capital_per_trade / nullIf(toFloat64(e.day_open), 0)))) AS quantity \
                FROM signals sig \
                INNER JOIN strategies st ON sig.setup_family = st.setup_family \
                INNER JOIN features e ON e.symbol = sig.symbol AND e.rn = sig.signal_rn + 1 \
                WHERE e.day_open > 0 AND sig.score >= st.min_score \
                  AND multiIf( \
                    st.strategy_id = 'breakout-volume-v2', sig.volume_ratio >= 1.5 AND sig.breakout_pct <= 1.0 AND sig.trend_up, \
                    st.strategy_id = 'pullback-quality-v2', sig.trend_up AND sig.pullback_zone AND sig.volume_ratio >= 0.8 AND sig.day_close >= sig.sma20, \
                    st.strategy_id = 'near-52w-high-tight-v2', sig.distance_to_52w_high_pct <= 4.0 AND sig.range_position_pct >= 75.0, \
                    st.strategy_id = 'near-52w-high-runner-v2', sig.distance_to_52w_high_pct <= 3.0 AND sig.trend_up AND sig.volume_ratio >= 0.8, \
                    st.strategy_id = 'near-52w-high-volume-v3', sig.distance_to_52w_high_pct <= 6.0 AND sig.volume_ratio >= 1.15 AND sig.range_position_pct >= 75.0, \
                    st.strategy_id = 'momentum-core-v1', sig.distance_to_52w_high_pct <= 3.0 AND sig.range_position_pct >= 85.0 AND sig.trend_up, \
                    true \
                  ) \
            ), \
            exits AS ( \
                SELECT e.strategy_id, e.entry_symbol AS symbol, e.signal_date, e.entry_setup_family AS setup_family, e.entry_score AS score, e.entry_date, e.entry_price, e.quantity, e.capital_per_trade, e.tp_pct, e.sl_pct, e.max_hold_sessions, \
                    minIf(f.trade_date, f.day_low <= e.entry_price * (1 - e.sl_pct / 100.0)) AS stop_date, \
                    minIf(f.trade_date, f.day_high >= e.entry_price * (1 + e.tp_pct / 100.0)) AS target_date, \
                    argMax(f.day_close, f.rn) AS time_exit_price, \
                    max(f.trade_date) AS time_exit_date \
                FROM entries e \
                INNER JOIN features f ON f.symbol = e.entry_symbol \
                WHERE f.rn >= e.entry_rn AND f.rn < e.entry_rn + e.max_hold_sessions \
                GROUP BY e.strategy_id, e.entry_symbol, e.signal_date, e.entry_setup_family, e.entry_score, e.entry_date, e.entry_price, e.quantity, e.capital_per_trade, e.tp_pct, e.sl_pct, e.max_hold_sessions \
            ), \
            trades AS ( \
                SELECT strategy_id, symbol, signal_date, entry_date, \
                    multiIf(stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), stop_date, target_date != toDate('1970-01-01'), target_date, time_exit_date) AS exit_date, \
                    setup_family, entry_price, \
                    multiIf(stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), entry_price * (1 - sl_pct / 100.0), target_date != toDate('1970-01-01'), entry_price * (1 + tp_pct / 100.0), time_exit_price) AS exit_price, \
                    quantity, quantity * entry_price AS capital_used, (exit_price - entry_price) * quantity AS pnl, ((exit_price - entry_price) / entry_price) * 100.0 AS return_pct, \
                    multiIf(stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), 'SL', target_date != toDate('1970-01-01'), 'TP', 'TIME') AS exit_reason, \
                    toUInt16(dateDiff('day', entry_date, exit_date) + 1) AS hold_sessions, score \
                FROM exits \
                WHERE exit_date >= entry_date AND exit_price > 0 \
            ) \
        SELECT '{escaped_run_id}' AS run_id, strategy_id, symbol, signal_date, entry_date, exit_date, setup_family, entry_price, exit_price, quantity, capital_used, pnl, return_pct, exit_reason, hold_sessions, score \
        FROM trades"
    );

    state.ch.query(&query).execute().await?;
    Ok(())
}

async fn fetch_summaries(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestRunSummary>> {
    let query = format!(
        "SELECT \
            strategy_id, \
            strategy_id AS strategy_name, \
            toUInt32(count()) AS total_trades, \
            round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, \
            round(avg(return_pct), 3) AS avg_return_pct, \
            round(sum(pnl), 2) AS total_pnl, \
            round(100 * sum(pnl) / sum(capital_used), 3) AS deployed_return_pct, \
            round(avg(hold_sessions), 2) AS avg_hold_sessions, \
            countIf(exit_reason = 'TP') AS tp_exits, \
            countIf(exit_reason = 'SL') AS sl_exits, \
            countIf(exit_reason = 'TIME') AS time_exits, \
            toString(min(entry_date)) AS from_date, \
            toString(max(exit_date)) AS to_date \
        FROM trading.backtest_trades \
        WHERE run_id = '{}' \
        GROUP BY strategy_id \
        ORDER BY total_pnl DESC",
        run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestRunSummary>().await?)
}

async fn fetch_yearly_returns(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestYearlyReturn>> {
    let query = format!(
        "SELECT strategy_id, year, trades, win_rate, avg_return_pct, yearly_pnl AS pnl, return_pct \
        FROM ( \
            SELECT \
                strategy_id, \
                toUInt16(toYear(entry_date)) AS year, \
                toUInt32(count()) AS trades, \
                round(100 * countIf(trade_pnl > 0) / count(), 2) AS win_rate, \
                round(avg(trade_return_pct), 3) AS avg_return_pct, \
                round(sum(trade_pnl), 2) AS yearly_pnl, \
                round(100 * sum(trade_pnl) / sum(capital_used), 3) AS return_pct \
            FROM ( \
                SELECT strategy_id, entry_date, pnl AS trade_pnl, return_pct AS trade_return_pct, capital_used \
                FROM trading.backtest_trades \
                WHERE run_id = '{}' \
            ) \
            GROUP BY strategy_id, year \
        ) \
        ORDER BY strategy_id, year",
        run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestYearlyReturn>().await?)
}

async fn fetch_monthly_returns(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestMonthlyReturn>> {
    let query = format!(
        "SELECT strategy_id, year, month, month_label, trades, win_rate, monthly_pnl AS pnl, return_pct \
        FROM ( \
            SELECT \
                strategy_id, \
                toUInt16(toYear(entry_date)) AS year, \
                toUInt8(toMonth(entry_date)) AS month, \
                formatDateTime(entry_date, '%b') AS month_label, \
                toUInt32(count()) AS trades, \
                round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, \
                round(sum(pnl), 2) AS monthly_pnl, \
                round(100 * sum(pnl) / sum(capital_used), 3) AS return_pct \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, year, month, month_label \
        ) \
        ORDER BY strategy_id, year, month",
        run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestMonthlyReturn>().await?)
}

async fn fetch_strategy_diagnostics(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestStrategyDiagnostic>> {
    let query = format!(
        "WITH strategy_stats AS ( \
            SELECT \
                strategy_id, \
                toUInt32(count()) AS total_trades, \
                round(sum(pnl), 2) AS total_pnl, \
                round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, \
                sumIf(pnl, pnl > 0) AS gross_profit, \
                abs(sumIf(pnl, pnl < 0)) AS gross_loss, \
                round(avg(return_pct), 3) AS expectancy_pct \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id \
        ), monthly AS ( \
            SELECT strategy_id, toYYYYMM(entry_date) AS month_key, sum(pnl) AS monthly_pnl \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, month_key \
        ), monthly_stats AS ( \
            SELECT \
                strategy_id, \
                round(100 * countIf(monthly_pnl > 0) / count(), 2) AS positive_months_pct, \
                round(quantileExact(0.5)(monthly_pnl), 2) AS median_monthly_pnl, \
                round(min(monthly_pnl), 2) AS worst_month, \
                round(max(monthly_pnl), 2) AS best_month \
            FROM monthly \
            GROUP BY strategy_id \
        ), daily AS ( \
            SELECT strategy_id, entry_date AS d, sum(pnl) AS daily_pnl \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, d \
        ), equity AS ( \
            SELECT strategy_id, d, daily_pnl, sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS equity \
            FROM daily \
        ), dd AS ( \
            SELECT strategy_id, d, daily_pnl, equity, max(equity) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak \
            FROM equity \
        ), drawdowns AS ( \
            SELECT strategy_id, round(min(equity - peak), 2) AS max_drawdown_rs \
            FROM dd \
            GROUP BY strategy_id \
        ) \
        SELECT \
            strategy_id, method_family, total_trades, total_pnl, win_rate, profit_factor, expectancy_pct, \
            positive_months_pct, median_monthly_pnl, worst_month, best_month, max_drawdown_rs, \
            round(raw_stability_score, 2) AS stability_score, \
            multiIf(total_pnl <= 0, 'Rejected', raw_stability_score >= 56 AND positive_months_pct >= 55, 'Research', raw_stability_score >= 50, 'Watch', 'Fragile') AS status \
        FROM ( \
            SELECT \
                s.strategy_id AS strategy_id, \
                multiIf( \
                    position(s.strategy_id, 'breakout') > 0, 'Breakout', \
                    position(s.strategy_id, 'pullback') > 0, 'Pullback', \
                    position(s.strategy_id, '52w') > 0, '52W Momentum', \
                    position(s.strategy_id, 'momentum') > 0, 'Momentum', \
                    'Other' \
                ) AS method_family, \
                s.total_trades AS total_trades, \
                s.total_pnl AS total_pnl, \
                s.win_rate AS win_rate, \
                round(if(s.gross_loss = 0, if(s.gross_profit > 0, 99, 0), s.gross_profit / s.gross_loss), 2) AS profit_factor, \
                s.expectancy_pct AS expectancy_pct, \
                m.positive_months_pct AS positive_months_pct, \
                m.median_monthly_pnl AS median_monthly_pnl, \
                m.worst_month AS worst_month, \
                m.best_month AS best_month, \
                d.max_drawdown_rs AS max_drawdown_rs, \
                greatest(0, least(100, \
                    m.positive_months_pct * 0.42 \
                    + s.win_rate * 0.28 \
                    + least(18, if(s.gross_loss = 0, 18, (s.gross_profit / s.gross_loss) * 8)) \
                    + if(s.total_pnl > 0, 8, -18) \
                    - least(22, abs(d.max_drawdown_rs) / greatest(abs(s.total_pnl), 1) * 12) \
                )) AS raw_stability_score \
            FROM strategy_stats s \
            INNER JOIN monthly_stats m ON m.strategy_id = s.strategy_id \
            INNER JOIN drawdowns d ON d.strategy_id = s.strategy_id \
        ) \
        ORDER BY status ASC, stability_score DESC, total_pnl DESC",
        run_id, run_id, run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestStrategyDiagnostic>().await?)
}

async fn fetch_symbol_results(state: &AppState, run_id: &str, losers: bool) -> anyhow::Result<Vec<BacktestSymbolResult>> {
    let direction = if losers { "ASC" } else { "DESC" };
    let query = format!(
        "SELECT strategy_id, symbol, trades, win_rate, symbol_pnl AS pnl, avg_return_pct \
        FROM ( \
            SELECT \
                strategy_id, \
                symbol, \
                toUInt32(count()) AS trades, \
                round(100 * countIf(pnl > 0) / count(), 2) AS win_rate, \
                round(sum(pnl), 2) AS symbol_pnl, \
                round(avg(return_pct), 3) AS avg_return_pct \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, symbol \
            HAVING trades >= 20 \
        ) \
        ORDER BY symbol_pnl {} \
        LIMIT 15",
        run_id, direction
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestSymbolResult>().await?)
}

async fn fetch_day_quality(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestDayQuality>> {
    let query = format!(
        "WITH daily AS ( \
            SELECT strategy_id, entry_date AS d, sum(pnl) AS daily_pnl \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, d \
        ), equity AS ( \
            SELECT strategy_id, d, daily_pnl, sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS equity \
            FROM daily \
        ), dd AS ( \
            SELECT strategy_id, d, daily_pnl, equity, max(equity) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak \
            FROM equity \
        ) \
        SELECT \
            strategy_id, \
            count() AS trading_days, \
            round(100 * countIf(daily_pnl > 0) / count(), 2) AS positive_days_pct, \
            round(min(daily_pnl), 2) AS worst_day, \
            round(max(daily_pnl), 2) AS best_day, \
            round(min(equity - peak), 2) AS max_drawdown_rs \
        FROM dd \
        GROUP BY strategy_id \
        ORDER BY max_drawdown_rs DESC",
        run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestDayQuality>().await?)
}

async fn fetch_trade_log(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestTradeLogRow>> {
    let query = format!(
        "SELECT \
            strategy_id, \
            symbol, \
            toString(entry_date) AS entry_date, \
            toString(exit_date) AS exit_date, \
            setup_family, \
            entry_price, \
            exit_price, \
            quantity, \
            round(pnl, 2) AS pnl, \
            round(return_pct, 3) AS return_pct, \
            exit_reason, \
            hold_sessions, \
            score \
        FROM trading.backtest_trades \
        WHERE run_id = '{}' \
        ORDER BY entry_date DESC, abs(pnl) DESC \
        LIMIT 80",
        run_id
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestTradeLogRow>().await?)
}
