use axum::{
    body::Body,
    extract::{Path as AxumPath, Query, State},
    http::{header, StatusCode},
    response::Response,
    Json,
};
use chrono::{Datelike, NaiveDate};
use clickhouse::Row;
use csv::StringRecord;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    env, fs,
    io::ErrorKind,
    path::PathBuf,
    process::Command,
    time::Instant,
};

use crate::api::AppState;
use crate::types::now_ist;

const LATEST_RUN_ID: &str = "watchlist-swing-20260503-001";
const BACKTEST_CAPITAL_PER_TRADE: f64 = 10_000.0;
const BACKTEST_MAX_NEW_POSITIONS_PER_DAY: u16 = 3;

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

const CREATE_BACKTEST_FEATURE_CACHE: &str = r#"
CREATE TABLE IF NOT EXISTS trading.daily_backtest_features (
    symbol                    String,
    trade_date                Date,
    rn                        UInt32,
    day_open                  Float64,
    day_high                  Float64,
    day_low                   Float64,
    day_close                 Float64,
    day_volume                Float64,
    sma20                     Float64,
    sma50                     Float64,
    sma200                    Float64,
    avg_volume20              Float64,
    high_20d                  Float64,
    high_52w                  Float64,
    low_52w                   Float64,
    rsi10                     Float64,
    breakout_pct              Float64,
    distance_to_52w_high_pct  Float64,
    range_position_pct        Float64,
    volume_ratio              Float64,
    trend_up                  UInt8,
    pullback_zone             UInt8,
    rsi10_pullback            UInt8,
    score                     UInt8,
    refreshed_at              DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(refreshed_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol, trade_date)
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
    rsi_exits: u64,
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
pub struct BacktestEquityPoint {
    strategy_id: String,
    trade_date: String,
    daily_pnl: f64,
    cumulative_pnl: f64,
    drawdown_rs: f64,
    cumulative_return_pct: f64,
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
    signal_date: String,
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
pub struct BacktestDateSummary {
    trade_date: String,
    total_trades: u64,
    winners: u64,
    losers: u64,
    win_rate: f64,
    total_pnl: f64,
    avg_return_pct: f64,
    best_symbol: String,
    best_pnl: f64,
    worst_symbol: String,
    worst_pnl: f64,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestDateStrategySummary {
    strategy_id: String,
    setup_family: String,
    trades: u64,
    win_rate: f64,
    pnl: f64,
    best_symbol: String,
    best_pnl: f64,
    worst_symbol: String,
    worst_pnl: f64,
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
    equity_curve: Vec<BacktestEquityPoint>,
    diagnostics: Vec<BacktestStrategyDiagnostic>,
    winners: Vec<BacktestSymbolResult>,
    losers: Vec<BacktestSymbolResult>,
    day_quality: Vec<BacktestDayQuality>,
    trades: Vec<BacktestTradeLogRow>,
}

#[derive(Deserialize)]
pub struct BacktestDatewiseQuery {
    date: Option<String>,
    strategy: Option<String>,
    page: Option<u32>,
    page_size: Option<u32>,
}

#[derive(Serialize)]
pub struct BacktestDatewiseResponse {
    run_id: String,
    updated_at: String,
    selected_date: Option<String>,
    available_dates: Vec<String>,
    strategy_options: Vec<String>,
    summary: Option<BacktestDateSummary>,
    strategy_summaries: Vec<BacktestDateStrategySummary>,
    top_gainers: Vec<BacktestTradeLogRow>,
    top_losers: Vec<BacktestTradeLogRow>,
    rows: Vec<BacktestTradeLogRow>,
    page: u32,
    page_size: u32,
    total_rows: u64,
}

#[derive(Serialize)]
pub struct BacktestRunResponse {
    ok: bool,
    run_id: String,
    message: String,
    cache: BacktestCacheStatus,
    dashboard: BacktestDashboardResponse,
}

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct BacktestCacheStatus {
    cached_rows: u64,
    symbols: u64,
    from_date: String,
    to_date: String,
    refreshed_at: String,
}

#[derive(Serialize)]
pub struct BacktestCacheRefreshResponse {
    ok: bool,
    updated_at: String,
    cache: BacktestCacheStatus,
    message: String,
}

#[derive(Serialize)]
pub struct PythonBacktestLabResponse {
    ok: bool,
    updated_at: String,
    duration_ms: Option<u128>,
    message: String,
    payload: Value,
}

#[derive(Clone)]
struct BacktestStrategySpec {
    strategy_id: String,
    strategy_name: String,
    setup_family: String,
    min_score: u8,
    tp_pct: f64,
    sl_pct: f64,
    target_atr: Option<f64>,
    stop_atr: Option<f64>,
    max_hold_sessions: u16,
    max_positions_per_day: u16,
    capital_per_trade: f64,
    entry_condition_sql: Option<String>,
}

#[derive(Clone, Copy)]
struct FileStrategySource {
    strategy_id: &'static str,
    strategy_name: &'static str,
    setup_family: &'static str,
    method_family: &'static str,
    relative_path: &'static str,
}

#[derive(Clone)]
struct FileBacktestTrade {
    strategy_id: String,
    strategy_name: String,
    setup_family: String,
    method_family: String,
    symbol: String,
    signal_date: NaiveDate,
    entry_date: NaiveDate,
    exit_date: NaiveDate,
    entry_price: f64,
    exit_price: f64,
    quantity: u32,
    capital_used: f64,
    pnl: f64,
    return_pct: f64,
    exit_reason: String,
    hold_sessions: u16,
    score: u8,
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

pub async fn datewise(
    State(state): State<AppState>,
    Query(query): Query<BacktestDatewiseQuery>,
) -> Json<BacktestDatewiseResponse> {
    let run_id = latest_run_id(&state)
        .await
        .unwrap_or_else(|err| {
            tracing::warn!("latest backtest run lookup failed: {}", err);
            LATEST_RUN_ID.to_string()
        });
    let page = query.page.unwrap_or(1).max(1);
    let page_size = query.page_size.unwrap_or(25).clamp(10, 50);
    Json(build_datewise(&state, &run_id, query.date, query.strategy, page, page_size).await)
}

pub async fn run(
    State(state): State<AppState>,
) -> Result<Json<BacktestRunResponse>, (StatusCode, String)> {
    ensure_tables(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest table setup failed: {err}")))?;
    ensure_backtest_feature_cache_current(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest feature cache failed: {err}")))?;

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
        cache: backtest_cache_status(&state).await.unwrap_or_default(),
        dashboard,
    }))
}

pub async fn refresh_cache(
    State(state): State<AppState>,
) -> Result<Json<BacktestCacheRefreshResponse>, (StatusCode, String)> {
    ensure_tables(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest table setup failed: {err}")))?;
    refresh_backtest_feature_cache(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest cache refresh failed: {err}")))?;
    let cache = backtest_cache_status(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("backtest cache stats failed: {err}")))?;

    Ok(Json(BacktestCacheRefreshResponse {
        ok: true,
        updated_at: now_ist().to_rfc3339(),
        cache,
        message: "Backtest feature cache refreshed from parquet. Future backtest runs read ClickHouse cached features instead of rebuilding indicators from raw parquet.".to_string(),
    }))
}

pub async fn python_latest() -> Result<Json<PythonBacktestLabResponse>, (StatusCode, String)> {
    let payload = read_python_lab_payload()
        .map_err(|err| (StatusCode::NOT_FOUND, format!("python backtest lab output not found: {err}")))?;

    Ok(Json(PythonBacktestLabResponse {
        ok: true,
        updated_at: now_ist().to_rfc3339(),
        duration_ms: None,
        message: "Loaded current strategy lab results directly from CSV files.".to_string(),
        payload,
    }))
}

pub async fn run_python_lab() -> Result<Json<PythonBacktestLabResponse>, (StatusCode, String)> {
    let started = Instant::now();
    let result = tokio::task::spawn_blocking(run_python_optimizer)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("python optimizer task failed: {err}")))?;
    let message = result
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("python optimizer failed: {err}")))?;
    let payload = read_python_lab_payload()
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("python optimizer completed but payload could not be read: {err}")))?;

    Ok(Json(PythonBacktestLabResponse {
        ok: true,
        updated_at: now_ist().to_rfc3339(),
        duration_ms: Some(started.elapsed().as_millis()),
        message,
        payload,
    }))
}

pub async fn python_chart(
    AxumPath(name): AxumPath<String>,
) -> Result<Response<Body>, (StatusCode, String)> {
    if name.contains('/') || name.contains('\\') || !name.ends_with(".png") {
        return Err((StatusCode::BAD_REQUEST, "invalid chart name".to_string()));
    }

    let path = python_lab_output_dir().join("charts").join(name);
    let bytes = fs::read(&path)
        .map_err(|err| (StatusCode::NOT_FOUND, format!("chart not found: {} ({err})", path.display())))?;

    Response::builder()
        .header(header::CONTENT_TYPE, "image/png")
        .header(header::CACHE_CONTROL, "no-store")
        .body(Body::from(bytes))
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("chart response failed: {err}")))
}

fn run_python_optimizer() -> anyhow::Result<String> {
    let root = repo_root();
    let script = env::var("BACKTEST_LAB_SCRIPT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("scripts").join("complex_strategy_optimizer.py"));
    let out_dir = python_lab_output_dir();
    fs::create_dir_all(&out_dir)?;

    if !script.exists() {
        anyhow::bail!("script does not exist: {}", script.display());
    }

    let ma_grid = env::var("BACKTEST_LAB_MA_GRID").unwrap_or_else(|_| "140".to_string());
    let panic_grid = env::var("BACKTEST_LAB_PANIC_GRID").unwrap_or_else(|_| "180".to_string());
    let python_env = env::var("BACKTEST_PYTHON").ok();
    let mut attempts: Vec<(String, Vec<String>)> = Vec::new();
    if let Some(python) = python_env {
        attempts.push((python, Vec::new()));
    } else {
        attempts.push(("python".to_string(), Vec::new()));
        attempts.push(("py".to_string(), vec!["-3".to_string()]));
        attempts.push(("python3".to_string(), Vec::new()));
    }

    let mut last_not_found = None;
    for (program, prefix_args) in attempts {
        let mut command = Command::new(&program);
        command
            .current_dir(&root)
            .env("PYTHONUNBUFFERED", "1");
        for arg in prefix_args {
            command.arg(arg);
        }
        command
            .arg("-u")
            .arg(&script)
            .arg("--out-dir")
            .arg(&out_dir)
            .arg("--max-ma-grid")
            .arg(&ma_grid)
            .arg("--max-panic-grid")
            .arg(&panic_grid);

        match command.output() {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let stderr = String::from_utf8_lossy(&output.stderr);
                if output.status.success() {
                    let tail = stdout.lines().rev().take(8).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join("\n");
                    return Ok(format!(
                        "Python/NumPy backtest lab completed with {} MA grid rows and {} panic grid rows.{}",
                        ma_grid,
                        panic_grid,
                        if tail.is_empty() { String::new() } else { format!("\n{tail}") }
                    ));
                }
                anyhow::bail!(
                    "command `{}` exited with {}.\nstdout:\n{}\nstderr:\n{}",
                    program,
                    output.status,
                    stdout,
                    stderr
                );
            }
            Err(err) if err.kind() == ErrorKind::NotFound => {
                last_not_found = Some(format!("{program}: {err}"));
            }
            Err(err) => return Err(err.into()),
        }
    }

    anyhow::bail!(
        "no Python executable found. Set BACKTEST_PYTHON, or install python on PATH. Last error: {}",
        last_not_found.unwrap_or_else(|| "unknown".to_string())
    )
}

fn read_python_lab_payload() -> anyhow::Result<Value> {
    let out_dir = python_lab_output_dir();
    let predictions_path = if out_dir.join("latest_signal_predictions.csv").exists() {
        out_dir.join("latest_signal_predictions.csv")
    } else {
        out_dir.join("current_signal_predictions.csv")
    };

    Ok(json!({
        "updated_at": now_ist().to_rfc3339(),
        "output_dir": out_dir.display().to_string(),
        "best": {
            "ma": read_csv_records_as_values(&out_dir.join("ma_tuned_results.csv"), Some(1))?,
            "panic": read_csv_records_as_values(&out_dir.join("panic_tuned_results.csv"), Some(1))?,
        },
        "period_returns": {
            "ma_yearly": read_csv_records_as_values(&out_dir.join("ma_yearly_returns.csv"), None)?,
            "panic_yearly": read_csv_records_as_values(&out_dir.join("panic_yearly_returns.csv"), None)?,
            "ma_monthly": read_csv_records_as_values(&out_dir.join("ma_monthly_returns.csv"), None)?,
            "panic_monthly": read_csv_records_as_values(&out_dir.join("panic_monthly_returns.csv"), None)?,
        },
        "predictions": read_csv_records_as_values(&predictions_path, None)?,
    }))
}

fn read_csv_records_as_values(path: &PathBuf, limit: Option<usize>) -> anyhow::Result<Vec<Value>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let mut reader = csv::Reader::from_path(path)?;
    let headers = reader.headers()?.clone();
    let mut out = Vec::new();
    for record in reader.records() {
        let record = record?;
        out.push(csv_record_to_value(&headers, &record));
        if limit.is_some_and(|max| out.len() >= max) {
            break;
        }
    }
    Ok(out)
}

fn csv_record_to_value(headers: &StringRecord, record: &StringRecord) -> Value {
    let mut map = serde_json::Map::new();
    for (idx, header) in headers.iter().enumerate() {
        let value = record.get(idx).unwrap_or("").trim();
        map.insert(header.to_string(), csv_scalar_to_value(value));
    }
    Value::Object(map)
}

fn csv_scalar_to_value(value: &str) -> Value {
    if value.is_empty() {
        Value::Null
    } else if matches!(value, "True" | "true") {
        Value::Bool(true)
    } else if matches!(value, "False" | "false") {
        Value::Bool(false)
    } else if let Ok(parsed) = value.parse::<f64>() {
        json!(parsed)
    } else {
        Value::String(value.to_string())
    }
}

fn python_lab_output_dir() -> PathBuf {
    env::var("BACKTEST_LAB_OUT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo_root().join("docs").join("complex_strategy_tuning_lab"))
}

fn repo_root() -> PathBuf {
    env::var("BACKTEST_LAB_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

fn append_file_backtest_results(
    summaries: &mut Vec<BacktestRunSummary>,
    yearly_returns: &mut Vec<BacktestYearlyReturn>,
    monthly_returns: &mut Vec<BacktestMonthlyReturn>,
    equity_curve: &mut Vec<BacktestEquityPoint>,
    diagnostics: &mut Vec<BacktestStrategyDiagnostic>,
    winners: &mut Vec<BacktestSymbolResult>,
    losers: &mut Vec<BacktestSymbolResult>,
    day_quality: &mut Vec<BacktestDayQuality>,
    trades: &mut Vec<BacktestTradeLogRow>,
) -> anyhow::Result<()> {
    let file_trades = load_file_backtest_trades()?;
    if file_trades.is_empty() {
        return Ok(());
    }

    let file_ids = file_trades
        .iter()
        .map(|trade| trade.strategy_id.clone())
        .collect::<HashSet<_>>();

    summaries.retain(|row| !file_ids.contains(&row.strategy_id));
    yearly_returns.retain(|row| !file_ids.contains(&row.strategy_id));
    monthly_returns.retain(|row| !file_ids.contains(&row.strategy_id));
    equity_curve.retain(|row| !file_ids.contains(&row.strategy_id));
    diagnostics.retain(|row| !file_ids.contains(&row.strategy_id));
    winners.retain(|row| !file_ids.contains(&row.strategy_id));
    losers.retain(|row| !file_ids.contains(&row.strategy_id));
    day_quality.retain(|row| !file_ids.contains(&row.strategy_id));
    trades.retain(|row| !file_ids.contains(&row.strategy_id));

    summaries.extend(file_summaries(&file_trades));
    summaries.sort_by(|a, b| b.total_pnl.partial_cmp(&a.total_pnl).unwrap_or(std::cmp::Ordering::Equal));

    yearly_returns.extend(file_yearly_returns(&file_trades));
    yearly_returns.sort_by(|a, b| a.strategy_id.cmp(&b.strategy_id).then(a.year.cmp(&b.year)));

    monthly_returns.extend(file_monthly_returns(&file_trades));
    monthly_returns.sort_by(|a, b| {
        a.strategy_id
            .cmp(&b.strategy_id)
            .then(a.year.cmp(&b.year))
            .then(a.month.cmp(&b.month))
    });

    equity_curve.extend(file_equity_curve(&file_trades));
    equity_curve.sort_by(|a, b| a.strategy_id.cmp(&b.strategy_id).then(a.trade_date.cmp(&b.trade_date)));

    diagnostics.extend(file_diagnostics(&file_trades));
    diagnostics.sort_by(|a, b| {
        strategy_status_rank_for_dashboard(&a.status)
            .cmp(&strategy_status_rank_for_dashboard(&b.status))
            .then_with(|| b.stability_score.partial_cmp(&a.stability_score).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| b.total_pnl.partial_cmp(&a.total_pnl).unwrap_or(std::cmp::Ordering::Equal))
    });

    winners.extend(file_symbol_results(&file_trades, false));
    winners.sort_by(|a, b| a.strategy_id.cmp(&b.strategy_id));
    losers.extend(file_symbol_results(&file_trades, true));
    losers.sort_by(|a, b| a.strategy_id.cmp(&b.strategy_id));

    day_quality.extend(file_day_quality(&file_trades));
    day_quality.sort_by(|a, b| b.max_drawdown_rs.partial_cmp(&a.max_drawdown_rs).unwrap_or(std::cmp::Ordering::Equal));

    let mut file_log = file_trade_log(&file_trades);
    trades.append(&mut file_log);
    trades.sort_by(|a, b| {
        b.entry_date
            .cmp(&a.entry_date)
            .then_with(|| b.pnl.abs().partial_cmp(&a.pnl.abs()).unwrap_or(std::cmp::Ordering::Equal))
    });
    trades.truncate(160);

    Ok(())
}

fn file_strategy_sources() -> Vec<FileStrategySource> {
    vec![
        FileStrategySource {
            strategy_id: "tuned-ma-breakout-v1",
            strategy_name: "MA Breakout Lab",
            setup_family: "MA Breakout",
            method_family: "MA Breakout",
            relative_path: "docs/complex_strategy_tuning_lab/ma_best_trades.csv",
        },
        FileStrategySource {
            strategy_id: "tuned-panic-reversal-v1",
            strategy_name: "Panic Reversal Lab",
            setup_family: "Panic Reversal",
            method_family: "Panic Reversal",
            relative_path: "docs/complex_strategy_tuning_lab/panic_best_trades.csv",
        },
        FileStrategySource {
            strategy_id: "weekly-supertrend-10-3",
            strategy_name: "Weekly Supertrend 10-3",
            setup_family: "Weekly Supertrend",
            method_family: "Weekly Supertrend",
            relative_path: "docs/king_supertrend_lab/weekly_supertrend_trades.csv",
        },
        FileStrategySource {
            strategy_id: "king-candle-supertrend-breakout-v1",
            strategy_name: "King Candle Supertrend Breakout",
            setup_family: "King Candle",
            method_family: "King Candle",
            relative_path: "docs/king_supertrend_lab/king_candle_trades.csv",
        },
        FileStrategySource {
            strategy_id: "king-candle-quality-v1",
            strategy_name: "King Candle Quality",
            setup_family: "King Candle Quality",
            method_family: "King Candle",
            relative_path: "docs/king_supertrend_lab/king_candle_quality_trades.csv",
        },
    ]
}

fn load_file_backtest_trades() -> anyhow::Result<Vec<FileBacktestTrade>> {
    let root = repo_root();
    let mut out = Vec::new();
    for source in file_strategy_sources() {
        let path = root.join(source.relative_path);
        if !path.exists() {
            tracing::warn!("file-backed strategy result missing: {}", path.display());
            continue;
        }
        let mut reader = csv::Reader::from_path(&path)?;
        let headers = reader.headers()?.clone();
        for record in reader.records() {
            let record = record?;
            if let Some(trade) = parse_file_backtest_trade(source, &headers, &record) {
                out.push(trade);
            }
        }
    }
    Ok(out)
}

fn parse_file_backtest_trade(
    source: FileStrategySource,
    headers: &StringRecord,
    record: &StringRecord,
) -> Option<FileBacktestTrade> {
    let symbol = csv_field(headers, record, "symbol")?.to_string();
    let signal_date = parse_csv_date(csv_field(headers, record, "signal_date").or_else(|| csv_field(headers, record, "signal_week"))?)?;
    let entry_date = parse_csv_date(csv_field(headers, record, "entry_date")?)?;
    let exit_date = parse_csv_date(csv_field(headers, record, "exit_date")?)?;
    let entry_price = parse_csv_f64(csv_field(headers, record, "entry")?)?;
    if entry_price <= 0.0 {
        return None;
    }
    let net_return = parse_csv_f64(csv_field(headers, record, "net_return").or_else(|| csv_field(headers, record, "gross_return"))?)?;
    let exit_price = csv_field(headers, record, "exit")
        .and_then(parse_csv_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(entry_price * (1.0 + net_return));
    let quantity = (BACKTEST_CAPITAL_PER_TRADE / entry_price).floor().max(1.0) as u32;
    let capital_used = f64::from(quantity) * entry_price;
    let pnl = capital_used * net_return;
    let raw_reason = csv_field(headers, record, "exit_reason").unwrap_or("TIME");
    let hit_target = csv_field(headers, record, "hit_target1")
        .map(parse_csv_bool)
        .unwrap_or(false)
        || csv_field(headers, record, "hit_target2")
            .map(parse_csv_bool)
            .unwrap_or(false);
    let hold_sessions = csv_field(headers, record, "hold_days")
        .or_else(|| csv_field(headers, record, "hold_weeks"))
        .and_then(parse_csv_f64)
        .map(|value| value.round().clamp(1.0, f64::from(u16::MAX)) as u16)
        .unwrap_or(1);
    let score = csv_field(headers, record, "rank_score")
        .and_then(parse_csv_f64)
        .map(|value| (value * 5.0).round().clamp(1.0, 100.0) as u8)
        .unwrap_or(50);

    Some(FileBacktestTrade {
        strategy_id: source.strategy_id.to_string(),
        strategy_name: source.strategy_name.to_string(),
        setup_family: source.setup_family.to_string(),
        method_family: source.method_family.to_string(),
        symbol,
        signal_date,
        entry_date,
        exit_date,
        entry_price: round2(entry_price),
        exit_price: round2(exit_price),
        quantity,
        capital_used,
        pnl,
        return_pct: net_return * 100.0,
        exit_reason: normalize_file_exit_reason(raw_reason, hit_target),
        hold_sessions,
        score,
    })
}

fn csv_field<'a>(headers: &StringRecord, record: &'a StringRecord, name: &str) -> Option<&'a str> {
    headers
        .iter()
        .position(|header| header == name)
        .and_then(|idx| record.get(idx))
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn parse_csv_date(value: &str) -> Option<NaiveDate> {
    let date = value.get(0..10).unwrap_or(value);
    NaiveDate::parse_from_str(date, "%Y-%m-%d").ok()
}

fn parse_csv_f64(value: &str) -> Option<f64> {
    value.trim().parse::<f64>().ok().filter(|value| value.is_finite())
}

fn parse_csv_bool(value: &str) -> bool {
    matches!(value.trim().to_ascii_lowercase().as_str(), "true" | "1" | "yes" | "y")
}

fn normalize_file_exit_reason(raw: &str, hit_target: bool) -> String {
    let reason = raw.to_ascii_lowercase();
    if hit_target || reason.contains("target") || reason.contains("tp") {
        "TP".to_string()
    } else if reason.contains("stop") || reason.contains("sl") {
        "SL".to_string()
    } else if reason.contains("rsi") {
        "RSI40".to_string()
    } else {
        "TIME".to_string()
    }
}

fn file_summaries(trades: &[FileBacktestTrade]) -> Vec<BacktestRunSummary> {
    grouped_file_trades(trades)
        .into_values()
        .map(|rows| {
            let first = rows[0];
            let total_trades = rows.len() as u32;
            let total_pnl: f64 = rows.iter().map(|trade| trade.pnl).sum();
            let total_capital: f64 = rows.iter().map(|trade| trade.capital_used).sum();
            BacktestRunSummary {
                strategy_id: first.strategy_id.clone(),
                strategy_name: first.strategy_name.clone(),
                total_trades,
                win_rate: round2(100.0 * rows.iter().filter(|trade| trade.pnl > 0.0).count() as f64 / rows.len() as f64),
                avg_return_pct: round3(rows.iter().map(|trade| trade.return_pct).sum::<f64>() / rows.len() as f64),
                total_pnl: round2(total_pnl),
                deployed_return_pct: round3(100.0 * total_pnl / total_capital.max(1.0)),
                avg_hold_sessions: round2(rows.iter().map(|trade| f64::from(trade.hold_sessions)).sum::<f64>() / rows.len() as f64),
                tp_exits: rows.iter().filter(|trade| trade.exit_reason == "TP").count() as u64,
                sl_exits: rows.iter().filter(|trade| trade.exit_reason == "SL").count() as u64,
                time_exits: rows.iter().filter(|trade| trade.exit_reason == "TIME").count() as u64,
                rsi_exits: rows.iter().filter(|trade| trade.exit_reason == "RSI40").count() as u64,
                from_date: rows.iter().map(|trade| trade.entry_date).min().unwrap_or(first.entry_date).to_string(),
                to_date: rows.iter().map(|trade| trade.exit_date).max().unwrap_or(first.exit_date).to_string(),
            }
        })
        .collect()
}

fn file_yearly_returns(trades: &[FileBacktestTrade]) -> Vec<BacktestYearlyReturn> {
    let mut grouped: BTreeMap<(String, u16), Vec<&FileBacktestTrade>> = BTreeMap::new();
    for trade in trades {
        grouped
            .entry((trade.strategy_id.clone(), trade.entry_date.year() as u16))
            .or_default()
            .push(trade);
    }
    grouped
        .into_iter()
        .map(|((strategy_id, year), rows)| {
            let pnl: f64 = rows.iter().map(|trade| trade.pnl).sum();
            let capital: f64 = rows.iter().map(|trade| trade.capital_used).sum();
            BacktestYearlyReturn {
                strategy_id,
                year,
                trades: rows.len() as u32,
                win_rate: round2(100.0 * rows.iter().filter(|trade| trade.pnl > 0.0).count() as f64 / rows.len() as f64),
                avg_return_pct: round3(rows.iter().map(|trade| trade.return_pct).sum::<f64>() / rows.len() as f64),
                pnl: round2(pnl),
                return_pct: round3(100.0 * pnl / capital.max(1.0)),
            }
        })
        .collect()
}

fn file_monthly_returns(trades: &[FileBacktestTrade]) -> Vec<BacktestMonthlyReturn> {
    let mut grouped: BTreeMap<(String, u16, u8), Vec<&FileBacktestTrade>> = BTreeMap::new();
    for trade in trades {
        grouped
            .entry((trade.strategy_id.clone(), trade.entry_date.year() as u16, trade.entry_date.month() as u8))
            .or_default()
            .push(trade);
    }
    grouped
        .into_iter()
        .map(|((strategy_id, year, month), rows)| {
            let pnl: f64 = rows.iter().map(|trade| trade.pnl).sum();
            let capital: f64 = rows.iter().map(|trade| trade.capital_used).sum();
            let date = NaiveDate::from_ymd_opt(i32::from(year), u32::from(month), 1)
                .unwrap_or_else(|| NaiveDate::from_ymd_opt(1970, 1, 1).unwrap());
            BacktestMonthlyReturn {
                strategy_id,
                year,
                month,
                month_label: date.format("%b").to_string(),
                trades: rows.len() as u32,
                win_rate: round2(100.0 * rows.iter().filter(|trade| trade.pnl > 0.0).count() as f64 / rows.len() as f64),
                pnl: round2(pnl),
                return_pct: round3(100.0 * pnl / capital.max(1.0)),
            }
        })
        .collect()
}

fn file_equity_curve(trades: &[FileBacktestTrade]) -> Vec<BacktestEquityPoint> {
    let mut daily: BTreeMap<(String, NaiveDate), f64> = BTreeMap::new();
    for trade in trades {
        *daily.entry((trade.strategy_id.clone(), trade.entry_date)).or_default() += trade.pnl;
    }

    let active_capital = BACKTEST_CAPITAL_PER_TRADE * f64::from(BACKTEST_MAX_NEW_POSITIONS_PER_DAY);
    let mut out = Vec::new();
    let mut current_strategy = String::new();
    let mut cumulative = 0.0;
    let mut peak = 0.0;

    for ((strategy_id, trade_date), daily_pnl) in daily {
        if strategy_id != current_strategy {
            current_strategy = strategy_id.clone();
            cumulative = 0.0;
            peak = 0.0;
        }
        cumulative += daily_pnl;
        if cumulative > peak {
            peak = cumulative;
        }
        out.push(BacktestEquityPoint {
            strategy_id,
            trade_date: trade_date.to_string(),
            daily_pnl: round2(daily_pnl),
            cumulative_pnl: round2(cumulative),
            drawdown_rs: round2(cumulative - peak),
            cumulative_return_pct: round3(100.0 * cumulative / active_capital.max(1.0)),
        });
    }

    out
}

fn file_diagnostics(trades: &[FileBacktestTrade]) -> Vec<BacktestStrategyDiagnostic> {
    let monthly = file_monthly_returns(trades);
    let monthly_by_strategy = monthly.iter().fold(HashMap::<String, Vec<f64>>::new(), |mut map, row| {
        map.entry(row.strategy_id.clone()).or_default().push(row.pnl);
        map
    });
    let dd_by_strategy = file_day_quality(trades)
        .into_iter()
        .map(|row| (row.strategy_id.clone(), row.max_drawdown_rs))
        .collect::<HashMap<_, _>>();

    grouped_file_trades(trades)
        .into_values()
        .map(|rows| {
            let first = rows[0];
            let total_pnl: f64 = rows.iter().map(|trade| trade.pnl).sum();
            let wins: Vec<f64> = rows.iter().filter(|trade| trade.pnl > 0.0).map(|trade| trade.pnl).collect();
            let losses: Vec<f64> = rows.iter().filter(|trade| trade.pnl < 0.0).map(|trade| trade.pnl).collect();
            let gross_profit: f64 = wins.iter().sum();
            let gross_loss = losses.iter().sum::<f64>().abs();
            let months = monthly_by_strategy.get(&first.strategy_id).cloned().unwrap_or_default();
            let positive_months_pct = if months.is_empty() {
                0.0
            } else {
                100.0 * months.iter().filter(|pnl| **pnl > 0.0).count() as f64 / months.len() as f64
            };
            let worst_month = months.iter().copied().reduce(f64::min).unwrap_or(0.0);
            let best_month = months.iter().copied().reduce(f64::max).unwrap_or(0.0);
            let max_drawdown_rs = *dd_by_strategy.get(&first.strategy_id).unwrap_or(&0.0);
            let profit_factor = if gross_loss == 0.0 {
                if gross_profit > 0.0 { 99.0 } else { 0.0 }
            } else {
                gross_profit / gross_loss
            };
            let win_rate = 100.0 * wins.len() as f64 / rows.len() as f64;
            let raw_stability = (positive_months_pct * 0.42
                + win_rate * 0.28
                + profit_factor.min(2.25) * 8.0
                + if total_pnl > 0.0 { 8.0 } else { -18.0 }
                - (max_drawdown_rs.abs() / total_pnl.abs().max(1.0) * 12.0).min(22.0))
                .clamp(0.0, 100.0);
            let status = if total_pnl <= 0.0 {
                "Rejected"
            } else if raw_stability >= 56.0 && positive_months_pct >= 55.0 {
                "Candidate"
            } else if raw_stability >= 50.0 {
                "Watch"
            } else {
                "Fragile"
            };

            BacktestStrategyDiagnostic {
                strategy_id: first.strategy_id.clone(),
                method_family: first.method_family.clone(),
                total_trades: rows.len() as u32,
                total_pnl: round2(total_pnl),
                win_rate: round2(win_rate),
                profit_factor: round2(profit_factor),
                expectancy_pct: round3(rows.iter().map(|trade| trade.return_pct).sum::<f64>() / rows.len() as f64),
                positive_months_pct: round2(positive_months_pct),
                median_monthly_pnl: round2(median(months)),
                worst_month: round2(worst_month),
                best_month: round2(best_month),
                max_drawdown_rs: round2(max_drawdown_rs),
                stability_score: round2(raw_stability),
                status: status.to_string(),
            }
        })
        .collect()
}

fn file_symbol_results(trades: &[FileBacktestTrade], losers: bool) -> Vec<BacktestSymbolResult> {
    let mut grouped: BTreeMap<(String, String), Vec<&FileBacktestTrade>> = BTreeMap::new();
    for trade in trades {
        grouped
            .entry((trade.strategy_id.clone(), trade.symbol.clone()))
            .or_default()
            .push(trade);
    }
    let mut by_strategy: HashMap<String, Vec<BacktestSymbolResult>> = HashMap::new();
    for ((strategy_id, symbol), rows) in grouped {
        if rows.len() < 5 {
            continue;
        }
        let pnl: f64 = rows.iter().map(|trade| trade.pnl).sum();
        let row = BacktestSymbolResult {
            strategy_id: strategy_id.clone(),
            symbol,
            trades: rows.len() as u32,
            win_rate: round2(100.0 * rows.iter().filter(|trade| trade.pnl > 0.0).count() as f64 / rows.len() as f64),
            pnl: round2(pnl),
            avg_return_pct: round3(rows.iter().map(|trade| trade.return_pct).sum::<f64>() / rows.len() as f64),
        };
        by_strategy.entry(strategy_id).or_default().push(row);
    }

    let mut out = Vec::new();
    for rows in by_strategy.values_mut() {
        if losers {
            rows.sort_by(|a, b| {
                a.pnl
                    .partial_cmp(&b.pnl)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| a.win_rate.partial_cmp(&b.win_rate).unwrap_or(std::cmp::Ordering::Equal))
            });
        } else {
            rows.sort_by(|a, b| {
                b.win_rate
                    .partial_cmp(&a.win_rate)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| b.avg_return_pct.partial_cmp(&a.avg_return_pct).unwrap_or(std::cmp::Ordering::Equal))
                    .then_with(|| b.pnl.partial_cmp(&a.pnl).unwrap_or(std::cmp::Ordering::Equal))
            });
        }
        out.extend(rows.iter().take(12).cloned());
    }
    out
}

fn file_day_quality(trades: &[FileBacktestTrade]) -> Vec<BacktestDayQuality> {
    let mut daily: BTreeMap<(String, NaiveDate), f64> = BTreeMap::new();
    for trade in trades {
        *daily.entry((trade.strategy_id.clone(), trade.entry_date)).or_default() += trade.pnl;
    }

    let mut by_strategy: BTreeMap<String, Vec<(NaiveDate, f64)>> = BTreeMap::new();
    for ((strategy_id, date), pnl) in daily {
        by_strategy.entry(strategy_id).or_default().push((date, pnl));
    }

    by_strategy
        .into_iter()
        .map(|(strategy_id, mut rows)| {
            rows.sort_by_key(|(date, _)| *date);
            let mut cumulative = 0.0;
            let mut peak = 0.0;
            let mut max_drawdown = 0.0;
            for (_, pnl) in &rows {
                cumulative += pnl;
                if cumulative > peak {
                    peak = cumulative;
                }
                let dd = cumulative - peak;
                if dd < max_drawdown {
                    max_drawdown = dd;
                }
            }
            BacktestDayQuality {
                strategy_id,
                trading_days: rows.len() as u64,
                positive_days_pct: round2(100.0 * rows.iter().filter(|(_, pnl)| *pnl > 0.0).count() as f64 / rows.len() as f64),
                worst_day: round2(rows.iter().map(|(_, pnl)| *pnl).reduce(f64::min).unwrap_or(0.0)),
                best_day: round2(rows.iter().map(|(_, pnl)| *pnl).reduce(f64::max).unwrap_or(0.0)),
                max_drawdown_rs: round2(max_drawdown),
            }
        })
        .collect()
}

fn file_trade_log(trades: &[FileBacktestTrade]) -> Vec<BacktestTradeLogRow> {
    let mut rows = trades
        .iter()
        .map(|trade| BacktestTradeLogRow {
            strategy_id: trade.strategy_id.clone(),
            symbol: trade.symbol.clone(),
            signal_date: trade.signal_date.to_string(),
            entry_date: trade.entry_date.to_string(),
            exit_date: trade.exit_date.to_string(),
            setup_family: trade.setup_family.clone(),
            entry_price: trade.entry_price,
            exit_price: trade.exit_price,
            quantity: trade.quantity,
            pnl: round2(trade.pnl),
            return_pct: round3(trade.return_pct),
            exit_reason: trade.exit_reason.clone(),
            hold_sessions: trade.hold_sessions,
            score: trade.score,
        })
        .collect::<Vec<_>>();
    rows.sort_by(|a, b| {
        b.entry_date
            .cmp(&a.entry_date)
            .then_with(|| b.pnl.abs().partial_cmp(&a.pnl.abs()).unwrap_or(std::cmp::Ordering::Equal))
    });
    rows.truncate(80);
    rows
}

fn grouped_file_trades(trades: &[FileBacktestTrade]) -> BTreeMap<String, Vec<&FileBacktestTrade>> {
    let mut grouped: BTreeMap<String, Vec<&FileBacktestTrade>> = BTreeMap::new();
    for trade in trades {
        grouped.entry(trade.strategy_id.clone()).or_default().push(trade);
    }
    grouped
}

fn median(mut values: Vec<f64>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = values.len() / 2;
    if values.len() % 2 == 0 {
        (values[mid - 1] + values[mid]) / 2.0
    } else {
        values[mid]
    }
}

fn strategy_status_rank_for_dashboard(status: &str) -> u8 {
    match status {
        "Candidate" => 0,
        "Watch" => 1,
        "Fragile" => 2,
        "Rejected" => 3,
        _ => 4,
    }
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

async fn build_dashboard(state: &AppState, run_id: &str) -> BacktestDashboardResponse {
    let mut summaries = fetch_summaries(state, run_id).await.unwrap_or_default();
    let mut yearly_returns = fetch_yearly_returns(state, run_id).await.unwrap_or_default();
    let mut monthly_returns = fetch_monthly_returns(state, run_id).await.unwrap_or_default();
    let mut equity_curve = fetch_equity_curve(state, run_id).await.unwrap_or_default();
    let mut diagnostics = fetch_strategy_diagnostics(state, run_id).await.unwrap_or_default();
    let mut winners = fetch_symbol_results(state, run_id, false).await.unwrap_or_default();
    let mut losers = fetch_symbol_results(state, run_id, true).await.unwrap_or_default();
    let mut day_quality = fetch_day_quality(state, run_id).await.unwrap_or_default();
    let mut trades = fetch_trade_log(state, run_id).await.unwrap_or_default();

    if let Err(err) = append_file_backtest_results(
        &mut summaries,
        &mut yearly_returns,
        &mut monthly_returns,
        &mut equity_curve,
        &mut diagnostics,
        &mut winners,
        &mut losers,
        &mut day_quality,
        &mut trades,
    ) {
        tracing::warn!("file-backed backtest result load failed: {}", err);
    }

    BacktestDashboardResponse {
        run_id: run_id.to_string(),
        updated_at: chrono::Utc::now().to_rfc3339(),
        summaries,
        yearly_returns,
        monthly_returns,
        equity_curve,
        diagnostics,
        winners,
        losers,
        day_quality,
        trades,
    }
}

async fn build_datewise(
    state: &AppState,
    run_id: &str,
    requested_date: Option<String>,
    strategy: Option<String>,
    page: u32,
    page_size: u32,
) -> BacktestDatewiseResponse {
    let available_dates = fetch_available_entry_dates(state, run_id).await.unwrap_or_default();
    let selected_date = requested_date
        .filter(|date| available_dates.iter().any(|item| item == date))
        .or_else(|| available_dates.first().cloned());
    let strategy_filter = strategy
        .filter(|value| !value.trim().is_empty() && !value.eq_ignore_ascii_case("all"))
        .unwrap_or_else(|| "all".to_string());

    let (summary, strategy_summaries, top_gainers, top_losers, rows, total_rows, strategy_options) =
        if let Some(date) = selected_date.as_deref() {
            let summary = fetch_date_summary(state, run_id, date, &strategy_filter).await.unwrap_or(None);
            let strategy_summaries = fetch_date_strategy_summaries(state, run_id, date).await.unwrap_or_default();
            let top_gainers = fetch_date_trades(state, run_id, date, &strategy_filter, "pnl DESC", 5, 0).await.unwrap_or_default();
            let top_losers = fetch_date_trades(state, run_id, date, &strategy_filter, "pnl ASC", 5, 0).await.unwrap_or_default();
            let offset = u64::from(page.saturating_sub(1)) * u64::from(page_size);
            let rows = fetch_date_trades(state, run_id, date, &strategy_filter, "abs(pnl) DESC, symbol ASC", page_size, offset)
                .await
                .unwrap_or_default();
            let total_rows = fetch_date_trade_count(state, run_id, date, &strategy_filter).await.unwrap_or(0);
            let strategy_options = strategy_summaries
                .iter()
                .map(|row| row.strategy_id.clone())
                .collect::<Vec<_>>();
            (summary, strategy_summaries, top_gainers, top_losers, rows, total_rows, strategy_options)
        } else {
            (None, Vec::new(), Vec::new(), Vec::new(), Vec::new(), 0, Vec::new())
        };

    BacktestDatewiseResponse {
        run_id: run_id.to_string(),
        updated_at: chrono::Utc::now().to_rfc3339(),
        selected_date,
        available_dates,
        strategy_options,
        summary,
        strategy_summaries,
        top_gainers,
        top_losers,
        rows,
        page,
        page_size,
        total_rows,
    }
}

async fn ensure_tables(state: &AppState) -> anyhow::Result<()> {
    state.ch.query(CREATE_BACKTEST_TRADES).execute().await?;
    state.ch.query(CREATE_BACKTEST_FEATURE_CACHE).execute().await?;
    Ok(())
}

async fn ensure_backtest_feature_cache_current(state: &AppState) -> anyhow::Result<()> {
    refresh_backtest_feature_cache(state).await?;
    Ok(())
}

async fn refresh_backtest_feature_cache(state: &AppState) -> anyhow::Result<()> {
    let query = "INSERT INTO trading.daily_backtest_features \
        WITH \
            daily AS ( \
                SELECT symbol, toDate(date) AS trade_date, argMin(open, bucket) AS day_open, max(high) AS day_high, min(low) AS day_low, argMax(close, bucket) AS day_close, toFloat64(sum(volume)) AS day_volume \
                FROM file('parquets/candles_*.parquet', Parquet) \
                WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1) \
                  AND date IS NOT NULL AND symbol IS NOT NULL AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL \
                GROUP BY symbol, trade_date \
            ), \
            priced AS ( \
                SELECT *, \
                    greatest(day_close - lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS gain, \
                    greatest(lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - day_close, 0) AS loss \
                FROM daily \
            ), \
            features AS ( \
                SELECT symbol, trade_date, day_open, day_high, day_low, day_close, day_volume, \
                    toUInt32(row_number() OVER (PARTITION BY symbol ORDER BY trade_date)) AS rn, \
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20, \
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50, \
                    avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200, \
                    avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                    min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                    100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10 \
                FROM priced \
            ), \
            scored AS ( \
                SELECT *, \
                    ((high_20d - day_close) / nullIf(high_20d, 0)) * 100.0 AS breakout_pct, \
                    ((high_52w - day_close) / nullIf(high_52w, 0)) * 100.0 AS distance_to_52w_high_pct, \
                    ((day_close - low_52w) / nullIf(high_52w - low_52w, 0.01)) * 100.0 AS range_position_pct, \
                    day_volume / greatest(avg_volume20, 1.0) AS volume_ratio, \
                    day_close > sma20 AND sma20 > sma50 AS trend_up, \
                    day_close >= sma20 * 0.98 AND day_close <= sma20 * 1.03 AS pullback_zone, \
                    day_close > sma200 AND rsi10 < 30 AS rsi10_pullback \
                FROM features \
            ) \
        SELECT \
            symbol, trade_date, rn, day_open, day_high, day_low, day_close, day_volume, \
            sma20, sma50, sma200, avg_volume20, high_20d, high_52w, low_52w, rsi10, \
            breakout_pct, distance_to_52w_high_pct, range_position_pct, volume_ratio, \
            toUInt8(trend_up), toUInt8(pullback_zone), toUInt8(rsi10_pullback), \
            toUInt8(greatest(50, least(96, 50 + if(trend_up, 18, 0) + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0)) + if(distance_to_52w_high_pct <= 8.0, 10, 0) + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0)) + if(pullback_zone, 8, 0) + if(rsi10_pullback, 16, 0) + if(range_position_pct >= 70.0, 6, 0)))) AS score, \
            now() \
        FROM scored \
        WHERE rn >= 252 AND day_close >= 50 AND avg_volume20 >= 100000";
    state.ch.query(query).execute().await?;
    Ok(())
}

impl Default for BacktestCacheStatus {
    fn default() -> Self {
        Self {
            cached_rows: 0,
            symbols: 0,
            from_date: String::new(),
            to_date: String::new(),
            refreshed_at: String::new(),
        }
    }
}

async fn backtest_cache_status(state: &AppState) -> anyhow::Result<BacktestCacheStatus> {
    let status = state
        .ch
        .query(
            "SELECT \
                toUInt64(count()) AS cached_rows, \
                toUInt64(uniqExact(symbol)) AS symbols, \
                toString(min(trade_date)) AS from_date, \
                toString(max(trade_date)) AS to_date, \
                toString(max(refreshed_at)) AS refreshed_at \
            FROM trading.daily_backtest_features FINAL",
        )
        .fetch_one::<BacktestCacheStatus>()
        .await?;
    Ok(status)
}

fn build_entries_cte(strategies: &[BacktestStrategySpec]) -> String {
    strategies
        .iter()
        .map(strategy_entry_select)
        .collect::<Vec<_>>()
        .join(" UNION ALL ")
}

fn strategy_entry_select(strategy: &BacktestStrategySpec) -> String {
    let condition = strategy
        .entry_condition_sql
        .clone()
        .unwrap_or_else(|| default_strategy_condition(strategy));
    let tp_expr = dynamic_exit_pct(strategy.target_atr, strategy.tp_pct);
    let sl_expr = dynamic_exit_pct(strategy.stop_atr, strategy.sl_pct);

    format!(
        "SELECT '{}' AS strategy_id, {} AS tp_pct, {} AS sl_pct, {} AS max_hold_sessions, {} AS max_positions_per_day, {} AS capital_per_trade, sig.symbol AS entry_symbol, sig.signal_date, '{}' AS entry_setup_family, sig.score AS entry_score, sig.volume_ratio AS rank_volume_ratio, e.trade_date AS entry_date, e.rn AS entry_rn, toFloat64(e.day_open) AS entry_price, toUInt32(greatest(1, floor({} / nullIf(toFloat64(e.day_open), 0)))) AS quantity \
         FROM signals sig \
         INNER JOIN features e ON e.symbol = sig.symbol AND e.rn = sig.signal_rn + 1 \
         WHERE e.day_open > 0 AND sig.score >= {} AND ({})",
        escape_sql(&strategy.strategy_id),
        tp_expr,
        sl_expr,
        strategy.max_hold_sessions,
        strategy.max_positions_per_day,
        strategy.capital_per_trade,
        escape_sql(&strategy.setup_family),
        strategy.capital_per_trade,
        strategy.min_score,
        condition
    )
}

fn dynamic_exit_pct(atr_multiple: Option<f64>, fallback_pct: f64) -> String {
    match atr_multiple.filter(|value| value.is_finite() && *value > 0.0) {
        Some(multiple) => format!(
            "greatest(0.1, 100.0 * ({} * sig.atr14) / nullIf(toFloat64(e.day_open), 0))",
            multiple
        ),
        None => fallback_pct.to_string(),
    }
}

fn default_strategy_condition(strategy: &BacktestStrategySpec) -> String {
    match strategy.strategy_id.as_str() {
        "breakout-volume-v2" => "sig.volume_ratio >= 1.5 AND sig.breakout_pct <= 1.0 AND sig.trend_up = 1".to_string(),
        "pullback-quality-v2" => "sig.trend_up = 1 AND sig.pullback_zone = 1 AND sig.volume_ratio >= 0.8 AND sig.day_close >= sig.sma20".to_string(),
        "near-52w-high-tight-v2" => "sig.distance_to_52w_high_pct <= 4.0 AND sig.range_position_pct >= 75.0".to_string(),
        "near-52w-high-runner-v2" => "sig.distance_to_52w_high_pct <= 3.0 AND sig.trend_up = 1 AND sig.volume_ratio >= 0.8".to_string(),
        "near-52w-high-volume-v3" => "sig.distance_to_52w_high_pct <= 6.0 AND sig.volume_ratio >= 1.15 AND sig.range_position_pct >= 75.0".to_string(),
        "momentum-core-v1" => "sig.distance_to_52w_high_pct <= 3.0 AND sig.range_position_pct >= 85.0 AND sig.trend_up = 1".to_string(),
        "rsi10-pullback-reversion-v1" => "sig.day_close > sig.sma200 AND sig.rsi10 < 30".to_string(),
        "atr-stretch-liquid-only-v1" => "sig.atr_stretch_liquid_only = 1".to_string(),
        "regime-mean-reversion-v1" => "sig.regime_mean_reversion = 1".to_string(),
        "regime-trend-breakout-v1" => "sig.regime_trend_breakout = 1".to_string(),
        "regime-breakout-volume-v1" => "sig.regime_breakout_volume = 1".to_string(),
        "regime-multifactor-score-v1" => "sig.regime_multifactor_score >= 9 AND sig.regime_breakout_core = 1".to_string(),
        "compression-breakout-v1" => "sig.compression_breakout = 1".to_string(),
        "strong-stock-pullback-v1" => "sig.strong_stock_pullback = 1".to_string(),
        "trend-reversal-failed-breakdown-v1" => "sig.trend_reversal_breakout = 1".to_string(),
        _ => format!("sig.setup_family = '{}'", escape_sql(&strategy.setup_family)),
    }
}

fn load_backtest_strategy_specs() -> Vec<BacktestStrategySpec> {
    // Backtest strategy behavior is code-owned. Do not load strategy behavior from
    // external JSON files here; promote validated Python research into explicit
    // engine specs or a dedicated Python backtest service.
    let mut specs = built_in_variant_specs();
    if specs.is_empty() {
        specs.push(BacktestStrategySpec {
            strategy_id: "near-52w-high-v1".to_string(),
            strategy_name: "Near 52W High V1".to_string(),
            setup_family: "Near 52W High".to_string(),
            min_score: 80,
            tp_pct: 10.0,
            sl_pct: 5.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 15,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        });
    }

    specs
}

fn built_in_variant_specs() -> Vec<BacktestStrategySpec> {
    vec![
        BacktestStrategySpec {
            strategy_id: "regime-mean-reversion-v1".to_string(),
            strategy_name: "Regime Mean Reversion V1".to_string(),
            setup_family: "Regime Mean Reversion".to_string(),
            min_score: 50,
            tp_pct: 5.0,
            sl_pct: 4.0,
            target_atr: Some(2.1),
            stop_atr: Some(1.3),
            max_hold_sessions: 6,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "regime-trend-breakout-v1".to_string(),
            strategy_name: "Regime Trend Breakout V1".to_string(),
            setup_family: "Regime Trend Breakout".to_string(),
            min_score: 50,
            tp_pct: 8.0,
            sl_pct: 4.0,
            target_atr: Some(3.0),
            stop_atr: Some(1.5),
            max_hold_sessions: 12,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "regime-breakout-volume-v1".to_string(),
            strategy_name: "Regime Breakout Volume V1".to_string(),
            setup_family: "Regime Breakout Volume".to_string(),
            min_score: 50,
            tp_pct: 10.0,
            sl_pct: 5.0,
            target_atr: Some(3.6),
            stop_atr: Some(1.8),
            max_hold_sessions: 15,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "regime-multifactor-score-v1".to_string(),
            strategy_name: "Regime Multi-Factor Score V1".to_string(),
            setup_family: "Regime Multi-Factor Score".to_string(),
            min_score: 50,
            tp_pct: 8.0,
            sl_pct: 4.0,
            target_atr: Some(2.8),
            stop_atr: Some(1.4),
            max_hold_sessions: 10,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "breakout-volume-v2".to_string(),
            strategy_name: "Breakout Volume V2".to_string(),
            setup_family: "Breakout Setup".to_string(),
            min_score: 90,
            tp_pct: 10.0,
            sl_pct: 4.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 12,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "pullback-quality-v2".to_string(),
            strategy_name: "Pullback Quality V2".to_string(),
            setup_family: "Pullback To 20 DMA".to_string(),
            min_score: 88,
            tp_pct: 7.0,
            sl_pct: 3.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 12,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "near-52w-high-tight-v2".to_string(),
            strategy_name: "Near 52W High Tight V2".to_string(),
            setup_family: "Near 52W High".to_string(),
            min_score: 88,
            tp_pct: 8.0,
            sl_pct: 4.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 12,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "near-52w-high-runner-v2".to_string(),
            strategy_name: "Near 52W High Runner V2".to_string(),
            setup_family: "Near 52W High".to_string(),
            min_score: 90,
            tp_pct: 12.0,
            sl_pct: 5.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 20,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "near-52w-high-volume-v3".to_string(),
            strategy_name: "Near 52W High Volume V3".to_string(),
            setup_family: "Near 52W High".to_string(),
            min_score: 88,
            tp_pct: 10.0,
            sl_pct: 4.5,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 15,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
        BacktestStrategySpec {
            strategy_id: "momentum-core-v1".to_string(),
            strategy_name: "Momentum Core V1".to_string(),
            setup_family: "Near 52W High".to_string(),
            min_score: 92,
            tp_pct: 15.0,
            sl_pct: 6.0,
            target_atr: None,
            stop_atr: None,
            max_hold_sessions: 25,
            max_positions_per_day: BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
            capital_per_trade: BACKTEST_CAPITAL_PER_TRADE,
            entry_condition_sql: None,
        },
    ]
}

fn escape_sql(value: &str) -> String {
    value.replace('\'', "''")
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
    let strategies = load_backtest_strategy_specs();
    let entries_cte = build_entries_cte(&strategies);
    let query = format!(
        "INSERT INTO trading.backtest_trades \
        WITH \
            features AS ( \
                SELECT * \
                FROM trading.daily_backtest_features FINAL \
                WHERE symbol IN (SELECT symbol FROM trading.watchlist FINAL WHERE enabled = 1) \
            ), \
            enriched AS ( \
                SELECT *, \
                    lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_close, \
                    lagInFrame(day_high, 1, day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_high, \
                    lagInFrame(day_low, 1, day_low) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_low, \
                    lagInFrame(sma20, 1, sma20) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_sma20, \
                    lagInFrame(day_close, 3, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_3d, \
                    lagInFrame(day_close, 20, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_20d, \
                    lagInFrame(day_close, 60, day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS close_60d \
                FROM features \
            ), \
            scored_base AS ( \
                SELECT *, \
                    min(day_low) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_low20, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_high20, \
                    max(day_high) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 55 PRECEDING AND 1 PRECEDING) AS prior_high55, \
                    avg(greatest(day_high - day_low, abs(day_high - prev_close), abs(day_low - prev_close))) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14, \
                    avg((day_high - day_low) / nullIf(day_close, 0.01)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_range_pct20, \
                    stddevPop(day_close) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS std20, \
                    avg(greatest(day_close - prev_close, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain14, \
                    avg(greatest(prev_close - day_close, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss14, \
                    avg(if((day_high - prev_high) > (prev_low - day_low) AND (day_high - prev_high) > 0, day_high - prev_high, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS plus_dm14, \
                    avg(if((prev_low - day_low) > (day_high - prev_high) AND (prev_low - day_low) > 0, prev_low - day_low, 0)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS minus_dm14, \
                    min((day_high - day_low) / nullIf(day_close, 0.01)) OVER (PARTITION BY symbol ORDER BY rn ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS min_range_pct7, \
                    day_close / nullIf(close_3d, 0.01) - 1 AS ret3, \
                    day_close / nullIf(close_20d, 0.01) - 1 AS ret20, \
                    day_close / nullIf(close_60d, 0.01) - 1 AS ret60 \
                FROM enriched \
            ), \
            scored_indicators AS ( \
                SELECT *, \
                    100 - (100 / (1 + avg_gain14 / greatest(avg_loss14, 0.000001))) AS rsi14, \
                    (day_close - sma20) / nullIf(std20, 0) AS zscore20, \
                    (day_close - sma20) / nullIf(atr14, 0) AS dist_sma20_atr, \
                    (day_close - day_low) / nullIf(day_high - day_low, 0.01) AS close_location, \
                    (day_high - day_low) / nullIf(atr14, 0) AS range_atr, \
                    (day_close - day_low) / nullIf(day_low, 0.01) AS recovery_from_low_pct, \
                    100 * plus_dm14 / nullIf(atr14, 0) AS plus_di14, \
                    100 * minus_dm14 / nullIf(atr14, 0) AS minus_di14, \
                    atr14 / nullIf(day_close, 0.01) AS atr_pct \
                FROM scored_base \
            ), \
            scored AS ( \
                SELECT *, \
                    100 * abs(plus_di14 - minus_di14) / greatest(plus_di14 + minus_di14, 0.000001) AS adx14, \
                    day_close > sma50 AND sma50 > sma200 AND adx14 >= 20 AS trend_regime, \
                    day_close <= high_20d AND breakout_pct <= 1.0 AND volume_ratio >= 1.2 AS regime_breakout_core \
                FROM scored_indicators \
            ), \
            scored_cross AS ( \
                SELECT *, \
                    toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret60)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs60_rank, \
                    avg(if(day_close > sma200, 1.0, 0.0)) OVER (PARTITION BY trade_date) AS market_breadth200 \
                FROM scored \
            ), \
            signals AS ( \
                SELECT s.symbol, s.trade_date AS signal_date, s.rn AS signal_rn, \
                    s.day_open, s.day_high, s.day_low, s.day_close, s.day_volume, s.sma20, s.sma50, s.sma200, s.avg_volume20, s.high_20d, s.high_52w, s.low_52w, s.rsi10, s.rsi14, s.breakout_pct, s.distance_to_52w_high_pct, s.range_position_pct, s.volume_ratio, s.atr14, s.atr_pct, s.avg_range_pct20, s.min_range_pct7, s.ret3, s.ret20, s.ret60, s.zscore20, s.dist_sma20_atr, s.close_location, s.range_atr, s.recovery_from_low_pct, s.adx14, s.trend_regime, s.regime_breakout_core, s.prior_high20, s.prior_high55, s.rs60_rank, s.market_breadth200, s.trend_up, s.pullback_zone, s.rsi10_pullback, \
                    s.day_close > s.sma200 AND s.sma20 > s.sma50 AND (s.sma20 - s.day_close) > 2.2 * s.atr14 AND s.rsi10 < 35 AS atr_stretch_liquid_only, \
                    s.day_close > s.sma200 AND not(s.trend_regime) AND s.adx14 < 30 AND s.zscore20 < -2.5 AND s.rsi14 < 30 AND s.dist_sma20_atr < -1.5 AND s.close_location >= 0.35 AS regime_mean_reversion, \
                    s.trend_regime AND s.regime_breakout_core AND s.ret60 >= 0.10 AND s.close_location >= 0.85 AND s.volume_ratio >= 1.4 AS regime_trend_breakout, \
                    s.trend_regime AND s.day_close > s.prior_high55 AND s.volume_ratio >= 2.6 AND s.ret60 >= 0.05 AND s.close_location >= 0.85 AND s.atr_pct > s.avg_range_pct20 AS regime_breakout_volume, \
                    toUInt8( \
                        if(s.ret20 > 0, 1, 0) + if(s.ret60 > 0.10, 1, 0) + if(s.range_position_pct >= 70, 1, 0) + \
                        if(s.day_close > s.sma50, 1, 0) + if(s.sma50 > s.sma200, 1, 0) + if(s.day_close > s.sma200, 1, 0) + \
                        if(s.volume_ratio > 1.0, 1, 0) + if(s.volume_ratio > 1.4, 1, 0) + \
                        if(s.rsi14 >= 45 AND s.rsi14 <= 70, 1, 0) + if(s.zscore20 >= -0.8 AND s.zscore20 <= 1.8, 1, 0) + if(s.close_location > 0.55, 1, 0) - \
                        if(s.atr_pct > 0.08, 1, 0) \
                    ) AS regime_multifactor_score, \
                    s.trend_up AND s.breakout_pct <= 1.5 AND s.volume_ratio >= 1.1 AND ((s.day_high - s.day_low) / nullIf(s.day_close, 0.01)) <= s.avg_range_pct20 * 0.75 AS compression_breakout, \
                    s.day_close > s.sma50 AND s.sma50 > s.sma200 AND s.ret60 > 0.08 AND s.pullback_zone AND s.volume_ratio >= 0.7 AND s.volume_ratio <= 1.5 AND s.range_position_pct >= 55 AS strong_stock_pullback, \
                    s.day_low < s.prior_low20 AND s.day_close > s.prior_low20 AND s.day_close > s.sma20 AND s.prev_close <= s.prev_sma20 AND s.volume_ratio >= 0.9 AND ((s.day_close - s.day_low) / nullIf(s.day_high - s.day_low, 0.01)) >= 0.70 AS trend_reversal_breakout, \
                    s.trend_up AND s.market_breadth200 >= 0.38 AND s.rs60_rank >= 0.58 AND s.volume_ratio >= 1.3 AND s.close_location >= 0.58 AND s.atr14 > 0 AND s.day_high >= s.prior_high20 * 1.001 AND s.day_close >= s.prior_high20 * 1.001 * 0.985 AS tuned_ma_breakout, \
                    s.ret3 <= -0.08 AND s.range_atr >= 1.35 AND s.close_location >= 0.64 AND s.recovery_from_low_pct >= 0.012 AND s.atr14 > 0 AS tuned_panic_reversal, \
                    multiIf(tuned_panic_reversal, 'Panic Reversal', tuned_ma_breakout, 'MA Breakout', regime_mean_reversion, 'Regime Mean Reversion', regime_trend_breakout, 'Regime Trend Breakout', regime_breakout_volume, 'Regime Breakout Volume', regime_multifactor_score >= 9 AND regime_breakout_core, 'Regime Multi-Factor Score', rsi10_pullback, 'RSI10 Pullback Reversion', atr_stretch_liquid_only, 'ATR Stretch Liquid Only', trend_reversal_breakout, 'Trend Reversal Breakout', compression_breakout, 'Compression Breakout', strong_stock_pullback, 'Strong Stock Pullback', trend_up AND breakout_pct <= 1.5 AND volume_ratio >= 1.1, 'Breakout Setup', trend_up AND pullback_zone, 'Pullback To 20 DMA', day_close > sma50 AND distance_to_52w_high_pct <= 8.0, 'Near 52W High', 'Trend Filter') AS setup_family, \
                    toUInt8(greatest(50, least(96, 50 + if(trend_up, 18, 0) + if(breakout_pct <= 1.5, 14, if(breakout_pct <= 4.0, 8, 0)) + if(distance_to_52w_high_pct <= 8.0, 10, 0) + if(volume_ratio >= 1.2, 10, if(volume_ratio >= 1.0, 5, 0)) + if(pullback_zone, 8, 0) + if(rsi10_pullback, 16, 0) + if(atr_stretch_liquid_only, 24, 0) + if(regime_mean_reversion, 28, 0) + if(regime_trend_breakout, 18, 0) + if(regime_breakout_volume, 18, 0) + if(regime_multifactor_score >= 9, 16, 0) + if(compression_breakout, 18, 0) + if(strong_stock_pullback, 16, 0) + if(trend_reversal_breakout, 24, 0) + if(range_position_pct >= 70.0, 6, 0)))) AS score \
                FROM scored_cross s \
            ), \
            entries_raw AS ({entries_cte}), \
            strategy_ranked_entries AS ( \
                SELECT * \
                FROM ( \
                    SELECT *, row_number() OVER (PARTITION BY strategy_id, signal_date ORDER BY entry_score DESC, rank_volume_ratio DESC, entry_symbol ASC) AS daily_entry_rank \
                    FROM entries_raw \
                ) \
                WHERE daily_entry_rank <= max_positions_per_day \
            ), \
            deduped_entries AS ( \
                SELECT * \
                FROM ( \
                    SELECT *, row_number() OVER (PARTITION BY entry_date, entry_symbol ORDER BY entry_score DESC, rank_volume_ratio DESC, strategy_id ASC) AS symbol_entry_rank \
                    FROM strategy_ranked_entries \
                ) \
                WHERE symbol_entry_rank = 1 \
            ), \
            entries AS ( \
                SELECT * \
                FROM ( \
                    SELECT *, row_number() OVER (PARTITION BY entry_date ORDER BY entry_score DESC, rank_volume_ratio DESC, entry_symbol ASC) AS portfolio_entry_rank \
                    FROM deduped_entries \
                ) \
                WHERE portfolio_entry_rank <= {max_new_positions_per_day} \
            ), \
            exits AS ( \
                SELECT e.strategy_id, e.entry_symbol AS symbol, e.signal_date, e.entry_setup_family AS setup_family, e.entry_score AS score, e.entry_date, e.entry_price, e.quantity, e.capital_per_trade, e.tp_pct, e.sl_pct, e.max_hold_sessions, \
                    minIf(f.trade_date, e.strategy_id = 'rsi10-pullback-reversion-v1' AND f.rsi10 > 40) AS rsi_exit_date, \
                    argMinIf(f.day_close, f.rn, e.strategy_id = 'rsi10-pullback-reversion-v1' AND f.rsi10 > 40) AS rsi_exit_price, \
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
                    multiIf( \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), stop_date, \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), target_date, \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), rsi_exit_date, \
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), stop_date, \
                        target_date != toDate('1970-01-01'), target_date, \
                        time_exit_date \
                    ) AS exit_date, \
                    setup_family, entry_price, \
                    multiIf( \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), entry_price * (1 - sl_pct / 100.0), \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), entry_price * (1 + tp_pct / 100.0), \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), rsi_exit_price, \
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), entry_price * (1 - sl_pct / 100.0), \
                        target_date != toDate('1970-01-01'), entry_price * (1 + tp_pct / 100.0), \
                        time_exit_price \
                    ) AS exit_price, \
                    quantity, quantity * entry_price AS capital_used, (exit_price - entry_price) * quantity AS pnl, ((exit_price - entry_price) / entry_price) * 100.0 AS return_pct, \
                    multiIf( \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date) AND (rsi_exit_date = toDate('1970-01-01') OR stop_date <= rsi_exit_date), 'SL', \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND target_date != toDate('1970-01-01') AND (rsi_exit_date = toDate('1970-01-01') OR target_date <= rsi_exit_date), 'TP', \
                        strategy_id = 'rsi10-pullback-reversion-v1' AND rsi_exit_date != toDate('1970-01-01'), 'RSI40', \
                        stop_date != toDate('1970-01-01') AND (target_date = toDate('1970-01-01') OR stop_date <= target_date), 'SL', \
                        target_date != toDate('1970-01-01'), 'TP', \
                        'TIME' \
                    ) AS exit_reason, \
                    toUInt16(dateDiff('day', entry_date, exit_date) + 1) AS hold_sessions, score \
                FROM exits \
                WHERE exit_date >= entry_date AND exit_price > 0 \
            ) \
        SELECT '{escaped_run_id}' AS run_id, strategy_id, symbol, signal_date, entry_date, exit_date, setup_family, entry_price, exit_price, quantity, capital_used, pnl, return_pct, exit_reason, hold_sessions, score \
        FROM trades",
        entries_cte = entries_cte,
        max_new_positions_per_day = BACKTEST_MAX_NEW_POSITIONS_PER_DAY,
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
            countIf(exit_reason = 'RSI40') AS rsi_exits, \
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

async fn fetch_equity_curve(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestEquityPoint>> {
    let active_capital = BACKTEST_CAPITAL_PER_TRADE * f64::from(BACKTEST_MAX_NEW_POSITIONS_PER_DAY);
    let query = format!(
        "WITH daily AS ( \
            SELECT strategy_id, entry_date AS d, round(sum(pnl), 2) AS daily_pnl \
            FROM trading.backtest_trades \
            WHERE run_id = '{}' \
            GROUP BY strategy_id, d \
        ), equity AS ( \
            SELECT \
                strategy_id, \
                d, \
                daily_pnl, \
                sum(daily_pnl) OVER (PARTITION BY strategy_id ORDER BY d) AS cumulative_pnl \
            FROM daily \
        ), dd AS ( \
            SELECT \
                strategy_id, \
                d, \
                daily_pnl, \
                cumulative_pnl, \
                max(cumulative_pnl) OVER (PARTITION BY strategy_id ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak \
            FROM equity \
        ) \
        SELECT \
            strategy_id, \
            toString(d) AS trade_date, \
            round(daily_pnl, 2) AS daily_pnl, \
            round(cumulative_pnl, 2) AS cumulative_pnl, \
            round(cumulative_pnl - peak, 2) AS drawdown_rs, \
            round(100 * cumulative_pnl / greatest({}, 1), 3) AS cumulative_return_pct \
        FROM dd \
        ORDER BY strategy_id, d",
        escape_sql(run_id),
        active_capital
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestEquityPoint>().await?)
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
            multiIf(total_pnl <= 0, 'Rejected', raw_stability_score >= 56 AND positive_months_pct >= 55, 'Candidate', raw_stability_score >= 50, 'Watch', 'Fragile') AS status \
        FROM ( \
            SELECT \
                s.strategy_id AS strategy_id, \
                multiIf( \
                    position(s.strategy_id, 'regime-mean') > 0, 'Regime Mean Reversion', \
                    position(s.strategy_id, 'regime-trend') > 0, 'Regime Trend', \
                    position(s.strategy_id, 'regime-breakout') > 0, 'Regime Breakout', \
                    position(s.strategy_id, 'regime-multifactor') > 0, 'Multi-Factor', \
                    position(s.strategy_id, 'reversal') > 0, 'Reversal', \
                    position(s.strategy_id, 'breakout') > 0, 'Breakout', \
                    position(s.strategy_id, 'pullback') > 0, 'Pullback', \
                    position(s.strategy_id, 'stretch') > 0, 'Mean Reversion', \
                    position(s.strategy_id, 'rsi10') > 0, 'Mean Reversion', \
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
    let ordering = if losers {
        "symbol_pnl ASC, win_rate ASC, avg_return_pct ASC"
    } else {
        "win_rate DESC, avg_return_pct DESC, symbol_pnl DESC"
    };
    let query = format!(
        "SELECT strategy_id, symbol, trades, win_rate, symbol_pnl AS pnl, avg_return_pct \
        FROM ( \
            SELECT \
                *, \
                row_number() OVER (PARTITION BY strategy_id ORDER BY {}) AS edge_rank \
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
                HAVING trades >= 5 \
            ) \
        ) \
        WHERE edge_rank <= 12 \
        ORDER BY strategy_id ASC, edge_rank ASC",
        ordering,
        run_id
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

async fn fetch_available_entry_dates(state: &AppState, run_id: &str) -> anyhow::Result<Vec<String>> {
    #[derive(Row, Deserialize)]
    struct DateRow {
        trade_date: String,
    }
    let query = format!(
        "SELECT toString(entry_date) AS trade_date \
        FROM trading.backtest_trades \
        WHERE run_id = '{}' \
        GROUP BY entry_date \
        ORDER BY entry_date DESC \
        LIMIT 120",
        escape_sql(run_id)
    );
    Ok(state
        .ch
        .query(&query)
        .fetch_all::<DateRow>()
        .await?
        .into_iter()
        .map(|row| row.trade_date)
        .collect())
}

fn date_strategy_clause(strategy: &str) -> String {
    if strategy == "all" {
        String::new()
    } else {
        format!(" AND t.strategy_id = '{}'", escape_sql(strategy))
    }
}

async fn fetch_date_trade_count(
    state: &AppState,
    run_id: &str,
    date: &str,
    strategy: &str,
) -> anyhow::Result<u64> {
    let query = format!(
        "SELECT count() \
        FROM trading.backtest_trades AS t \
        WHERE t.run_id = '{}' AND t.entry_date = toDate('{}'){}",
        escape_sql(run_id),
        escape_sql(date),
        date_strategy_clause(strategy)
    );
    Ok(state.ch.query(&query).fetch_one::<u64>().await?)
}

async fn fetch_date_summary(
    state: &AppState,
    run_id: &str,
    date: &str,
    strategy: &str,
) -> anyhow::Result<Option<BacktestDateSummary>> {
    let query = format!(
        "SELECT \
            toString(t.entry_date) AS trade_date, \
            toUInt64(count()) AS total_trades, \
            toUInt64(countIf(t.pnl > 0)) AS winners, \
            toUInt64(countIf(t.pnl < 0)) AS losers, \
            round(100 * countIf(t.pnl > 0) / count(), 2) AS win_rate, \
            round(sum(t.pnl), 2) AS total_pnl, \
            round(avg(t.return_pct), 3) AS avg_return_pct, \
            argMax(t.symbol, t.pnl) AS best_symbol, \
            round(max(t.pnl), 2) AS best_pnl, \
            argMin(t.symbol, t.pnl) AS worst_symbol, \
            round(min(t.pnl), 2) AS worst_pnl \
        FROM trading.backtest_trades AS t \
        WHERE t.run_id = '{}' AND t.entry_date = toDate('{}'){} \
        GROUP BY t.entry_date",
        escape_sql(run_id),
        escape_sql(date),
        date_strategy_clause(strategy)
    );
    Ok(state
        .ch
        .query(&query)
        .fetch_optional::<BacktestDateSummary>()
        .await?)
}

async fn fetch_date_strategy_summaries(
    state: &AppState,
    run_id: &str,
    date: &str,
) -> anyhow::Result<Vec<BacktestDateStrategySummary>> {
    let query = format!(
        "SELECT \
            t.strategy_id, \
            any(t.setup_family) AS setup_family, \
            toUInt64(count()) AS trades, \
            round(100 * countIf(t.pnl > 0) / count(), 2) AS win_rate, \
            round(sum(t.pnl), 2) AS pnl, \
            argMax(t.symbol, t.pnl) AS best_symbol, \
            round(max(t.pnl), 2) AS best_pnl, \
            argMin(t.symbol, t.pnl) AS worst_symbol, \
            round(min(t.pnl), 2) AS worst_pnl \
        FROM trading.backtest_trades AS t \
        WHERE t.run_id = '{}' AND t.entry_date = toDate('{}') \
        GROUP BY t.strategy_id \
        ORDER BY pnl DESC",
        escape_sql(run_id),
        escape_sql(date)
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestDateStrategySummary>().await?)
}

async fn fetch_date_trades(
    state: &AppState,
    run_id: &str,
    date: &str,
    strategy: &str,
    order_by: &str,
    limit: u32,
    offset: u64,
) -> anyhow::Result<Vec<BacktestTradeLogRow>> {
    let query = format!(
        "SELECT \
            t.strategy_id, \
            t.symbol, \
            toString(t.signal_date) AS signal_date, \
            toString(t.entry_date) AS entry_date, \
            toString(t.exit_date) AS exit_date, \
            t.setup_family, \
            t.entry_price, \
            t.exit_price, \
            t.quantity, \
            round(t.pnl, 2) AS pnl, \
            round(t.return_pct, 3) AS return_pct, \
            t.exit_reason, \
            t.hold_sessions, \
            t.score \
        FROM trading.backtest_trades AS t \
        WHERE t.run_id = '{}' AND t.entry_date = toDate('{}'){} \
        ORDER BY {} \
        LIMIT {} OFFSET {}",
        escape_sql(run_id),
        escape_sql(date),
        date_strategy_clause(strategy),
        order_by,
        limit,
        offset
    );
    Ok(state.ch.query(&query).fetch_all::<BacktestTradeLogRow>().await?)
}

async fn fetch_trade_log(state: &AppState, run_id: &str) -> anyhow::Result<Vec<BacktestTradeLogRow>> {
    let query = format!(
        "SELECT \
            strategy_id, \
            symbol, \
            toString(signal_date) AS signal_date, \
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
