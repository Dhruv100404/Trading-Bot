use std::collections::{HashMap, HashSet};
use std::fs;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE, Engine as _};
use chrono::{Datelike, TimeZone, Timelike, Utc};
use clickhouse::Row;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Deserializer, Serialize};
use tokio::sync::RwLockReadGuard;

use crate::api::{paper, AppState};
use crate::dhan::client::DhanClient;
use crate::dhan::market_data::{fetch_quotes, QuoteItem};
use crate::types::{compute_bucket, is_nse_holiday, now_ist, prev_trading_day};

#[derive(Row, Deserialize, Clone)]
struct WatchRow {
    security_id: String,
    symbol: String,
    company_name: String,
    tiers: Vec<String>,
    enabled: u8,
    min_volume: u32,
}

#[derive(Row, Deserialize)]
struct DhanAccountRow {
    client_id: String,
    access_token: String,
}

#[derive(Deserialize)]
struct DhanJwtClaims {
    exp: i64,
    iat: i64,
    #[serde(rename = "dhanClientId")]
    dhan_client_id: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct BrokerStatus {
    provider: String,
    configured: bool,
    state: String,
    message: String,
    credential_source: String,
    client_id: Option<String>,
    issued_at_utc: Option<String>,
    expires_at_utc: Option<String>,
    live_quotes: bool,
}

#[derive(Serialize, Clone)]
pub struct MarketRegime {
    label: String,
    tone: String,
    summary: String,
    advances: usize,
    declines: usize,
    breadth_ratio: f32,
}

#[derive(Serialize, Clone)]
pub struct SetupMix {
    family: String,
    count: usize,
    avg_score: f32,
}

#[derive(Serialize, Clone)]
pub struct LiveSignal {
    status: String,
    label: String,
    reason: String,
    strategy_id: String,
    strategy_label: String,
    strategy_status: String,
    setup_family: String,
    score: u8,
    as_of: String,
    trigger_price: Option<f32>,
}

#[derive(Serialize, Clone)]
pub struct SwingCandidate {
    symbol: String,
    company_name: String,
    setup_family: String,
    bias: String,
    score: u8,
    confidence: String,
    regime_fit: u8,
    risk_reward: f32,
    last_price: f32,
    day_change_pct: f32,
    open_gap_pct: f32,
    distance_to_high_pct: f32,
    liquidity_bucket: String,
    entry_zone: String,
    stop_loss: f32,
    target_price: f32,
    expected_hold: String,
    thesis: String,
    reasons: Vec<String>,
    risks: Vec<String>,
    source: String,
    live_signal: LiveSignal,
}

#[derive(Serialize)]
pub struct SwingHomeResponse {
    updated_at: String,
    broker: BrokerStatus,
    market_regime: MarketRegime,
    top_candidates: Vec<SwingCandidate>,
    scanner_count: usize,
    setup_mix: Vec<SetupMix>,
}

#[derive(Serialize)]
pub struct SwingScannerResponse {
    updated_at: String,
    broker: BrokerStatus,
    market_regime: MarketRegime,
    live_data: bool,
    total_candidates: usize,
    candidates: Vec<SwingCandidate>,
}

#[derive(Serialize)]
pub struct SwingCandidateResponse {
    updated_at: String,
    broker: BrokerStatus,
    market_regime: MarketRegime,
    candidate: Option<SwingCandidate>,
    message: Option<String>,
}

#[derive(Deserialize)]
pub struct ScannerQuery {
    limit: Option<usize>,
}

#[derive(Deserialize)]
pub struct HistoryQuery {
    range: Option<String>,
}

#[derive(Deserialize)]
pub struct HistoricalScreenerQuery {
    limit: Option<usize>,
    setup: Option<String>,
    strategy: Option<String>,
    min_price: Option<f64>,
    min_avg_volume: Option<f64>,
}

#[derive(Serialize)]
pub struct HistoricalCandle {
    date: String,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: u64,
}

#[derive(Serialize)]
pub struct HistoricalSummary {
    latest_close: f64,
    change_pct_1m: f64,
    change_pct_3m: f64,
    change_pct_1y: f64,
    high_52w: f64,
    low_52w: f64,
    avg_volume_20d: f64,
}

#[derive(Serialize)]
pub struct SymbolHistoryResponse {
    updated_at: String,
    symbol: String,
    range: String,
    candles: Vec<HistoricalCandle>,
    summary: Option<HistoricalSummary>,
    message: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct HistoricalScreenerRow {
    symbol: String,
    as_of: String,
    setup_family: String,
    strategy_id: String,
    strategy_label: String,
    strategy_status: String,
    score: u8,
    trend_label: String,
    close: f64,
    sma20: f64,
    sma50: f64,
    avg_volume20: f64,
    volume_ratio: f64,
    distance_to_20d_high_pct: f64,
    distance_to_52w_high_pct: f64,
    range_position_pct: f64,
    atr14: f64,
    atr_pct: f64,
    close_location: f64,
    gap_pct: f64,
    rs60_rank: f64,
    rs120_rank: f64,
    market_breadth200: f64,
    planned_entry: String,
    stop_loss: f64,
    target_price: f64,
    risk_reward: f64,
}

#[derive(Serialize)]
pub struct HistoricalScreenerResponse {
    updated_at: String,
    range: String,
    signal_date: Option<String>,
    total_rows: usize,
    rows: Vec<HistoricalScreenerRow>,
    message: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct BambooLatestSignal {
    strategy: String,
    symbol: String,
    signal_date: String,
    planned_entry: String,
    close: f64,
    stop: f64,
    target_from_close: f64,
    risk_multiple: f64,
    risk_pct_vs_close: f64,
    relvol: f64,
    range_position_52w: f64,
    ema20_dist_atr: f64,
    prior_high20: f64,
    prior_high55: f64,
    gap_pct: f64,
    close_loc: f64,
    rank_score: f64,
}

#[derive(Serialize)]
pub struct BambooLatestResponse {
    updated_at: String,
    signal_date: Option<String>,
    total_rows: usize,
    unique_symbols: usize,
    top_signals: Vec<BambooLatestSignal>,
    all_signals: Vec<BambooLatestSignal>,
    message: Option<String>,
}

#[derive(Clone)]
struct ResolvedDhanCredentials {
    access_token: String,
    client_id: String,
    source: String,
}

#[derive(Clone)]
struct CandidateSeed {
    symbol: String,
    company_name: String,
    tiers: Vec<String>,
    liquidity_bucket: String,
    open_price: f32,
    high_price: f32,
    low_price: f32,
    last_price: f32,
    prev_close: f32,
    day_volume: f64,
    day_change_pct: f32,
    open_gap_pct: f32,
    recovery_pct: f32,
    distance_to_high_pct: f32,
    intraday_range_pct: f32,
    source: String,
}

struct DashboardBundle {
    broker: BrokerStatus,
    market_regime: MarketRegime,
    candidates: Vec<SwingCandidate>,
}

#[derive(Deserialize)]
struct HistoricalDailyRow {
    trade_date: Option<String>,
    open: Option<f64>,
    high: Option<f64>,
    low: Option<f64>,
    close: Option<f64>,
    volume: Option<String>,
}

#[derive(Deserialize, Clone)]
struct HistoricalFallbackRow {
    symbol: String,
    trade_date: String,
    day_open: f64,
    day_high: f64,
    day_low: f64,
    day_close: f64,
    day_volume: f64,
    prev_close: f64,
}

#[derive(Deserialize)]
struct HistoricalScreenerFeatureRow {
    symbol: Option<String>,
    trade_date: Option<String>,
    day_open: Option<f64>,
    day_close: Option<f64>,
    day_high: Option<f64>,
    day_low: Option<f64>,
    day_volume: Option<String>,
    sma20: Option<f64>,
    sma50: Option<f64>,
    sma200: Option<f64>,
    avg_volume20: Option<f64>,
    high_20d: Option<f64>,
    high_52w: Option<f64>,
    low_52w: Option<f64>,
    rsi10: Option<f64>,
    atr14: Option<f64>,
    atr_pct: Option<f64>,
    range_pct: Option<f64>,
    close_location: Option<f64>,
    gap_pct: Option<f64>,
    prior_high20: Option<f64>,
    prior_high55: Option<f64>,
    prior_high252: Option<f64>,
    prior_low20: Option<f64>,
    rs60_rank: Option<f64>,
    rs120_rank: Option<f64>,
    market_breadth200: Option<f64>,
}

#[derive(Row, Deserialize)]
struct StrategyStatusRow {
    strategy_id: String,
    status: String,
}

const QUOTE_CACHE_TTL_SECS: u64 = 20;
const PAPER_CAPITAL_PER_SIGNAL: f64 = 50_000.0;

const CREATE_SCREENER_FEATURE_CACHE: &str = r#"
CREATE TABLE IF NOT EXISTS trading.daily_screener_features (
    trade_date     Date,
    symbol         String,
    day_open       Float64,
    day_high       Float64,
    day_low        Float64,
    day_close      Float64,
    prev_close     Float64,
    day_volume     UInt64,
    sma20          Float64,
    sma50          Float64,
    sma200         Float64,
    avg_volume20   Float64,
    high_20d       Float64,
    high_52w       Float64,
    low_52w        Float64,
    rsi10          Float64,
    atr14          Float64,
    atr_pct        Float64,
    range_pct      Float64,
    close_location Float64,
    gap_pct        Float64,
    prior_high20   Float64,
    prior_high55   Float64,
    prior_high252  Float64,
    prior_low20    Float64,
    rs60_rank      Float64,
    rs120_rank     Float64,
    market_breadth200 Float64,
    refreshed_at   DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(refreshed_at)
ORDER BY (trade_date, symbol)
"#;

const SCREENER_FEATURE_CACHE_ALTERS: &[&str] = &[
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS atr14 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS atr_pct Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS range_pct Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS close_location Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS gap_pct Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_high20 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_high55 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_high252 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_low20 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS rs60_rank Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS rs120_rank Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS market_breadth200 Float64 DEFAULT 0",
];

const CREATE_SIGNAL_LEDGER: &str = r#"
CREATE TABLE IF NOT EXISTS trading.signal_ledger (
    signal_key       String,
    symbol           String,
    strategy_id      String,
    strategy_label   String,
    strategy_status  String,
    setup_family     String,
    signal_date      String,
    first_seen_at    DateTime DEFAULT now(),
    last_seen_at     DateTime DEFAULT now(),
    entry_price      Float64,
    quantity         UInt32,
    stop_loss        Float64,
    target_price     Float64,
    score            UInt8,
    source           String DEFAULT 'historical-screener',
    status           String DEFAULT 'active',
    paper_status     String DEFAULT 'staged',
    close_reason     String DEFAULT '',
    realized_pnl     Float64 DEFAULT 0,
    inserted_at      DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY signal_key
"#;

#[derive(Row, Serialize)]
struct SignalLedgerInsertRow {
    signal_key: String,
    symbol: String,
    strategy_id: String,
    strategy_label: String,
    strategy_status: String,
    setup_family: String,
    signal_date: String,
    entry_price: f64,
    quantity: u32,
    stop_loss: f64,
    target_price: f64,
    score: u8,
    source: String,
    status: String,
    paper_status: String,
    close_reason: String,
    realized_pnl: f64,
}

#[derive(Row, Deserialize)]
struct SignalLedgerKeyRow {
    signal_key: String,
}

#[derive(Row, Deserialize)]
struct SymbolOnlyRow {
    symbol: String,
}

#[derive(Serialize)]
pub struct FreshSignalsResponse {
    updated_at: String,
    signal_date: Option<String>,
    eligible_rows: usize,
    new_rows: usize,
    seen_rows: usize,
    staged_rows: usize,
    rows: Vec<HistoricalScreenerRow>,
    message: Option<String>,
}

#[derive(Serialize)]
pub struct FeatureCacheRefreshResponse {
    updated_at: String,
    data_date: Option<String>,
    cached_rows: usize,
    message: String,
}

#[derive(Row, Deserialize)]
struct FeatureCacheStatsRow {
    data_date: String,
    #[serde(deserialize_with = "deserialize_clickhouse_u64")]
    cached_rows: u64,
    avg_atr14: f64,
}

#[derive(Deserialize)]
pub struct FreshSignalQuery {
    limit: Option<usize>,
    min_price: Option<f64>,
    min_avg_volume: Option<f64>,
}

pub async fn home(State(state): State<AppState>) -> Json<SwingHomeResponse> {
    let bundle = build_dashboard_bundle(&state, 16, None).await;
    let setup_mix = compute_setup_mix(&bundle.candidates);
    Json(SwingHomeResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        broker: bundle.broker,
        market_regime: bundle.market_regime,
        scanner_count: bundle.candidates.len(),
        top_candidates: bundle.candidates.into_iter().take(6).collect(),
        setup_mix,
    })
}

pub async fn scanner(
    State(state): State<AppState>,
    Query(query): Query<ScannerQuery>,
) -> Json<SwingScannerResponse> {
    let limit = query.limit.unwrap_or(24).clamp(6, 48);
    let bundle = build_dashboard_bundle(&state, limit, None).await;
    let live_data = bundle.broker.live_quotes;
    let total_candidates = bundle.candidates.len();
    Json(SwingScannerResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        broker: bundle.broker,
        market_regime: bundle.market_regime,
        live_data,
        total_candidates,
        candidates: bundle.candidates,
    })
}

pub async fn candidate_detail(
    State(state): State<AppState>,
    Path(symbol): Path<String>,
) -> Json<SwingCandidateResponse> {
    let bundle = build_dashboard_bundle(&state, 40, Some(symbol.as_str())).await;
    let candidate = bundle
        .candidates
        .iter()
        .find(|c| c.symbol.eq_ignore_ascii_case(&symbol))
        .cloned();
    Json(SwingCandidateResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        broker: bundle.broker,
        market_regime: bundle.market_regime,
        candidate,
        message: if bundle.candidates.is_empty() {
            Some("No swing candidates are available yet. Seed the watchlist and add a valid Dhan token to unlock live quotes.".to_string())
        } else {
            None
        },
    })
}

pub async fn broker_status(State(state): State<AppState>) -> Json<BrokerStatus> {
    Json(resolve_broker_status(&state).await)
}

pub async fn history(
    State(state): State<AppState>,
    Path(symbol): Path<String>,
    Query(query): Query<HistoryQuery>,
) -> Json<SymbolHistoryResponse> {
    let range = normalize_history_range(query.range.as_deref());
    match load_historical_candles(&state, &symbol, &range).await {
        Ok(candles) => {
            let summary = compute_historical_summary(&candles);
            let message = if candles.is_empty() {
                Some(format!("No parquet-backed history was found for {} in the selected range.", symbol))
            } else {
                None
            };
            Json(SymbolHistoryResponse {
                updated_at: crate::types::now_ist().to_rfc3339(),
                symbol,
                range,
                candles,
                summary,
                message,
            })
        }
        Err(err) => Json(SymbolHistoryResponse {
            updated_at: crate::types::now_ist().to_rfc3339(),
            symbol,
            range,
            candles: Vec::new(),
            summary: None,
            message: Some(format!("Historical parquet query failed: {}", err)),
        }),
    }
}

pub async fn historical_screener(
    State(state): State<AppState>,
    Query(query): Query<HistoricalScreenerQuery>,
) -> Json<HistoricalScreenerResponse> {
    let limit = query.limit.unwrap_or(40).clamp(10, 120);
    let setup_filter = query
        .setup
        .unwrap_or_else(|| "all".to_string())
        .to_lowercase();
    let strategy_filter = query
        .strategy
        .unwrap_or_else(|| "all".to_string())
        .to_lowercase();
    let min_price = query.min_price.unwrap_or(80.0).max(1.0);
    let min_avg_volume = query.min_avg_volume.unwrap_or(100_000.0).max(0.0);

    match load_historical_screener_rows(&state, min_price, min_avg_volume).await {
        Ok(rows) => {
            let strategy_statuses = load_latest_strategy_statuses(&state)
                .await
                .unwrap_or_default();
            let mut mapped: Vec<HistoricalScreenerRow> = rows
                .into_iter()
                .filter_map(|row| map_historical_screener_row(row, &strategy_statuses))
                .filter(|row| matches_setup_filter(row, &setup_filter))
                .filter(|row| matches_strategy_filter(row, &strategy_filter))
                .collect();
            mapped.sort_by(|a, b| {
                strategy_status_rank(&a.strategy_status)
                    .cmp(&strategy_status_rank(&b.strategy_status))
                    .then_with(|| b.score.cmp(&a.score))
                    .then_with(|| a.symbol.cmp(&b.symbol))
            });
            let signal_date = mapped.first().map(|row| row.as_of.clone());
            let total_rows = mapped.len();
            mapped.truncate(limit);

            Json(HistoricalScreenerResponse {
                updated_at: crate::types::now_ist().to_rfc3339(),
                range: "1y".to_string(),
                signal_date,
                total_rows,
                rows: mapped,
                message: None,
            })
        }
        Err(err) => Json(HistoricalScreenerResponse {
            updated_at: crate::types::now_ist().to_rfc3339(),
            range: "1y".to_string(),
            signal_date: None,
            total_rows: 0,
            rows: Vec::new(),
            message: Some(format!("Historical screener query failed: {}", err)),
        }),
    }
}

pub async fn fresh_signals(
    State(state): State<AppState>,
    Query(query): Query<FreshSignalQuery>,
) -> Result<Json<FreshSignalsResponse>, (StatusCode, String)> {
    let limit = query.limit.unwrap_or(40).clamp(1, 120);
    let min_price = query.min_price.unwrap_or(80.0).max(1.0);
    let min_avg_volume = query.min_avg_volume.unwrap_or(100_000.0).max(0.0);

    ensure_signal_ledger(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    paper::ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let rows = load_historical_screener_rows(&state, min_price, min_avg_volume)
        .await
        .map_err(|err| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Fresh signal screener query failed: {err}"),
            )
        })?;
    let strategy_statuses = load_latest_strategy_statuses(&state)
        .await
        .unwrap_or_default();
    let mut eligible: Vec<HistoricalScreenerRow> = rows
        .into_iter()
        .filter_map(|row| map_historical_screener_row(row, &strategy_statuses))
        .filter(is_paper_eligible_signal)
        .collect();
    eligible.sort_by(|a, b| {
        strategy_status_rank(&a.strategy_status)
            .cmp(&strategy_status_rank(&b.strategy_status))
            .then_with(|| b.score.cmp(&a.score))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });

    let seen_keys = load_signal_ledger_keys(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    let mut new_candidates = Vec::new();
    for row in &eligible {
        if !seen_keys.contains(&signal_key_for(row)) {
            new_candidates.push(row.clone());
        }
    }
    let active_paper_symbols = load_active_paper_symbols(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    let fresh_rows = new_candidates
        .iter()
        .filter(|row| !active_paper_symbols.contains(&row.symbol))
        .take(limit)
        .cloned()
        .collect::<Vec<_>>();
    let staged_keys = fresh_rows
        .iter()
        .map(signal_key_for)
        .collect::<HashSet<_>>();

    let ledger_rows = new_candidates
        .iter()
        .map(|row| {
            let paper_status = if staged_keys.contains(&signal_key_for(row)) {
                "staged"
            } else if active_paper_symbols.contains(&row.symbol) {
                "already-active"
            } else {
                "baseline"
            };
            build_signal_ledger_row(row, paper_status)
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    insert_signal_ledger_rows(&state, &ledger_rows)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let mut staged_rows = 0usize;
    for row in fresh_rows.iter() {
        if stage_signal_to_paper(&state, row).await.is_ok() {
            staged_rows += 1;
        }
    }

    let signal_date = eligible
        .first()
        .map(|row| row.as_of.clone())
        .or_else(|| fresh_rows.first().map(|row| row.as_of.clone()));
    let message = if fresh_rows.is_empty() {
        Some("No new unique signals. Existing matching signals are already in the ledger or Paper Desk.".to_string())
    } else {
        None
    };

    Ok(Json(FreshSignalsResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        signal_date,
        eligible_rows: eligible.len(),
        new_rows: new_candidates.len(),
        seen_rows: eligible.len().saturating_sub(new_candidates.len()),
        staged_rows,
        rows: fresh_rows,
        message,
    }))
}

pub async fn refresh_feature_cache(
    State(state): State<AppState>,
) -> Result<Json<FeatureCacheRefreshResponse>, (StatusCode, String)> {
    ensure_screener_feature_cache(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    refresh_screener_feature_cache(&state)
        .await
        .map_err(|err| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("Feature cache refresh failed: {err}"),
            )
        })?;
    let stats = latest_feature_cache_stats(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("Feature cache stats failed: {err}")))?;

    Ok(Json(FeatureCacheRefreshResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        data_date: stats
            .as_ref()
            .filter(|row| row.cached_rows > 0)
            .map(|row| row.data_date.clone()),
        cached_rows: stats.map(|row| row.cached_rows as usize).unwrap_or(0),
        message: "Daily screener features cached in ClickHouse. RSI is based on close-to-close gains/losses; volume is stored separately as day volume and volume ratio inputs.".to_string(),
    }))
}

pub async fn bamboo_latest() -> Json<BambooLatestResponse> {
    let all_path = "docs/quant_research_outputs/bamboo_mtf_breakout_latest/latest_signals.csv";
    let top_path = "docs/quant_research_outputs/bamboo_mtf_breakout_latest/top_latest_signals.csv";
    let all_signals = read_bamboo_signal_csv(all_path).unwrap_or_default();
    let top_signals = read_bamboo_signal_csv(top_path).unwrap_or_else(|_| {
        let mut fallback = all_signals.clone();
        fallback.sort_by(|a, b| b.rank_score.total_cmp(&a.rank_score));
        fallback.truncate(4);
        fallback
    });
    let mut symbols = std::collections::HashSet::new();
    for signal in &all_signals {
        symbols.insert(signal.symbol.clone());
    }
    let signal_date = all_signals
        .first()
        .map(|signal| signal.signal_date.clone())
        .or_else(|| top_signals.first().map(|signal| signal.signal_date.clone()));
    let message = if all_signals.is_empty() && top_signals.is_empty() {
        Some("No Bamboo latest signal file was found yet. Run `python scripts\\bamboo_mtf_backtest.py --out-dir docs\\quant_research_outputs\\bamboo_mtf_breakout_latest`.".to_string())
    } else {
        None
    };

    Json(BambooLatestResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        signal_date,
        total_rows: all_signals.len(),
        unique_symbols: symbols.len(),
        top_signals,
        all_signals,
        message,
    })
}

async fn build_dashboard_bundle(
    state: &AppState,
    limit: usize,
    symbol_filter: Option<&str>,
) -> DashboardBundle {
    let watch_rows = load_watch_rows(state, limit.max(24), symbol_filter).await;
    if watch_rows.is_empty() {
        return DashboardBundle {
            broker: resolve_broker_status(state).await,
            market_regime: MarketRegime {
                label: "Scanner Warming Up".to_string(),
                tone: "neutral".to_string(),
                summary: "No watchlist rows were found. Start the engine with ClickHouse and seed the Dhan scrip master to build the swing universe.".to_string(),
                advances: 0,
                declines: 0,
                breadth_ratio: 1.0,
            },
            candidates: Vec::new(),
        };
    }

    let mut broker = resolve_broker_status(state).await;
    let volume_map = load_volume_groups_map();

    let trading_day = is_nse_trading_day_now();
    let regular_session = is_regular_session_now();
    let live_quote_map = if broker.state == "ready" && trading_day {
        let credentials = resolve_dhan_credentials(state).await;
        if let Some(credentials) = credentials {
            let mut config = state.config.clone();
            config.dhan_access_token = credentials.access_token;
            config.dhan_client_id = credentials.client_id;
            let security_ids: Vec<String> = watch_rows.iter().map(|row| row.security_id.clone()).collect();
            match get_live_quotes(state, &config, &security_ids).await {
                Ok(quotes) => {
                    broker.live_quotes = true;
                    broker.message = if regular_session {
                        "Live Dhan quotes are available for the regular NSE session.".to_string()
                    } else {
                        "Dhan quotes are available after market close; using the latest quoted prices.".to_string()
                    };
                    Some(quotes)
                }
                Err(err) => {
                    broker.live_quotes = false;
                    broker.state = "degraded".to_string();
                    broker.message = format!("Credentials are configured but live quote fetch failed: {}", err);
                    None
                }
            }
        } else {
            None
        }
    } else {
        if broker.state == "ready" && !trading_day {
            broker.live_quotes = false;
            broker.message = "NSE is on a weekend or listed holiday; using parquet-backed last close for scanner prices.".to_string();
        }
        None
    };

    let symbols = watch_rows.iter().map(|row| row.symbol.clone()).collect::<Vec<_>>();
    let historical_fallbacks = load_historical_fallbacks(
        state,
        &symbols,
    )
    .await
    .map_err(|err| {
        tracing::warn!("historical parquet fallback failed: {}", err);
        err
    })
    .unwrap_or_default();
    let strategy_statuses = load_latest_strategy_statuses(state)
        .await
        .map_err(|err| {
            tracing::warn!("latest strategy status lookup failed: {}", err);
            err
        })
        .unwrap_or_default();
    let live_baselines = load_live_signal_baselines(state, &symbols)
        .await
        .map_err(|err| {
            tracing::warn!("live signal baseline lookup failed: {}", err);
            err
        })
        .unwrap_or_default();
    let seeds = build_candidate_seeds(&watch_rows, &volume_map, live_quote_map.as_ref(), &historical_fallbacks);
    let market_regime = compute_market_regime(&seeds, broker.live_quotes);
    let mut candidates: Vec<SwingCandidate> = seeds
        .into_iter()
        .map(|seed| {
            let baseline = live_baselines.get(&seed.symbol);
            build_live_candidate(seed, &market_regime, baseline, &strategy_statuses, regular_session)
        })
        .collect();

    candidates.sort_by(|a, b| {
        live_signal_rank(&a.live_signal.status)
            .cmp(&live_signal_rank(&b.live_signal.status))
            .then_with(|| b.score.cmp(&a.score))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    candidates.truncate(limit);

    DashboardBundle {
        broker,
        market_regime,
        candidates,
    }
}

pub(crate) async fn get_live_quotes(
    state: &AppState,
    config: &crate::config::Config,
    security_ids: &[String],
) -> anyhow::Result<HashMap<String, QuoteItem>> {
    if let Some(cached) = read_cached_quotes(&state.quote_cache.read().await, security_ids) {
        return Ok(cached);
    }

    let _guard = state.quote_fetch_lock.lock().await;
    if let Some(cached) = read_cached_quotes(&state.quote_cache.read().await, security_ids) {
        return Ok(cached);
    }

    let client = DhanClient::new(config);
    let fetched = match fetch_quotes(&client, security_ids, &config.dhan_quote_endpoint).await {
        Ok(fetched) => fetched,
        Err(err) => {
            if let Some(stale) = read_stale_cached_quotes(&state.quote_cache.read().await, security_ids) {
                tracing::warn!("Dhan quote fetch failed; using stale cached quotes: {}", err);
                return Ok(stale);
            }
            return Err(err);
        }
    };
    if fetched.is_empty() {
        anyhow::bail!("Dhan quote response did not include any NSE_EQ quotes for the requested security ids");
    }

    {
        let mut cache = state.quote_cache.write().await;
        *cache = Some(crate::api::CachedQuotes {
            fetched_at: std::time::Instant::now(),
            by_security_id: fetched.clone(),
        });
    }

    Ok(fetched)
}

fn read_cached_quotes(
    cache: &RwLockReadGuard<'_, Option<crate::api::CachedQuotes>>,
    security_ids: &[String],
) -> Option<HashMap<String, QuoteItem>> {
    let cached = cache.as_ref()?;
    if cached.fetched_at.elapsed().as_secs() > QUOTE_CACHE_TTL_SECS {
        return None;
    }

    let mut result = HashMap::new();
    for security_id in security_ids {
        let quote = cached.by_security_id.get(security_id)?;
        result.insert(security_id.clone(), quote.clone());
    }
    Some(result)
}

fn read_stale_cached_quotes(
    cache: &RwLockReadGuard<'_, Option<crate::api::CachedQuotes>>,
    security_ids: &[String],
) -> Option<HashMap<String, QuoteItem>> {
    let cached = cache.as_ref()?;
    if cached.fetched_at.elapsed().as_secs() > 15 * 60 {
        return None;
    }

    let mut result = HashMap::new();
    for security_id in security_ids {
        let quote = cached.by_security_id.get(security_id)?;
        result.insert(security_id.clone(), quote.clone());
    }
    Some(result)
}

async fn resolve_dhan_credentials(state: &AppState) -> Option<ResolvedDhanCredentials> {
    if !state.config.dhan_access_token.is_empty() {
        let client_id = if !state.config.dhan_client_id.is_empty() {
            state.config.dhan_client_id.clone()
        } else {
            parse_dhan_jwt_claims(&state.config.dhan_access_token)
                .ok()
                .and_then(|claims| claims.dhan_client_id)
                .unwrap_or_default()
        };

        if !client_id.is_empty() {
            return Some(ResolvedDhanCredentials {
                access_token: state.config.dhan_access_token.clone(),
                client_id,
                source: "environment".to_string(),
            });
        }

        return Some(ResolvedDhanCredentials {
            access_token: state.config.dhan_access_token.clone(),
            client_id: String::new(),
            source: "environment".to_string(),
        });
    }

    let row = state
        .ch
        .query(
            "SELECT client_id, access_token \
             FROM trading.accounts \
             WHERE enabled = 1 AND broker = 'DHAN' \
             ORDER BY inserted_at DESC LIMIT 1",
        )
        .fetch_optional::<DhanAccountRow>()
        .await
        .ok()
        .flatten();

    row.map(|row| ResolvedDhanCredentials {
        access_token: row.access_token,
        client_id: row.client_id,
        source: "accounts-db".to_string(),
    })
}

async fn resolve_broker_status(state: &AppState) -> BrokerStatus {
    let credentials = resolve_dhan_credentials(state).await;
    let Some(credentials) = credentials else {
        return BrokerStatus {
            provider: "DHAN".to_string(),
            configured: false,
            state: "missing".to_string(),
            message: "No Dhan credentials are configured yet. Add a fresh token to enable live scanner quotes.".to_string(),
            credential_source: "none".to_string(),
            client_id: None,
            issued_at_utc: None,
            expires_at_utc: None,
            live_quotes: false,
        };
    };

    let claims = match parse_dhan_jwt_claims(&credentials.access_token) {
        Ok(claims) => claims,
        Err(err) => {
            return BrokerStatus {
                provider: "DHAN".to_string(),
                configured: true,
                state: "invalid".to_string(),
                message: format!("Dhan token could not be decoded: {}", err),
                credential_source: credentials.source,
                client_id: Some(credentials.client_id),
                issued_at_utc: None,
                expires_at_utc: None,
                live_quotes: false,
            };
        }
    };

    let now_ts = Utc::now().timestamp();
    let expires_at = format_utc(claims.exp);
    let issued_at = format_utc(claims.iat);
    let client_id = claims
        .dhan_client_id
        .clone()
        .or_else(|| Some(credentials.client_id.clone()));

    if claims.exp <= now_ts {
        return BrokerStatus {
            provider: "DHAN".to_string(),
            configured: true,
            state: "expired".to_string(),
            message: format!(
                "The Dhan token expired on {}. Add a fresh token to restore live quote fetches.",
                expires_at.clone().unwrap_or_else(|| "an unknown date".to_string())
            ),
            credential_source: credentials.source,
            client_id,
            issued_at_utc: issued_at,
            expires_at_utc: expires_at,
            live_quotes: false,
        };
    }

    BrokerStatus {
        provider: "DHAN".to_string(),
        configured: true,
        state: "ready".to_string(),
        message: "Credentials are configured and live quote fetch is enabled.".to_string(),
        credential_source: credentials.source,
        client_id,
        issued_at_utc: issued_at,
        expires_at_utc: expires_at,
        live_quotes: true,
    }
}

fn parse_dhan_jwt_claims(token: &str) -> anyhow::Result<DhanJwtClaims> {
    let payload = token
        .split('.')
        .nth(1)
        .ok_or_else(|| anyhow::anyhow!("JWT payload segment is missing"))?;
    let mut padded = payload.to_string();
    while padded.len() % 4 != 0 {
        padded.push('=');
    }
    let decoded = URL_SAFE.decode(padded)?;
    Ok(serde_json::from_slice::<DhanJwtClaims>(&decoded)?)
}

fn format_utc(ts: i64) -> Option<String> {
    Utc.timestamp_opt(ts, 0).single().map(|dt| dt.to_rfc3339())
}

async fn load_watch_rows(
    state: &AppState,
    limit: usize,
    symbol_filter: Option<&str>,
) -> Vec<WatchRow> {
    let limit = limit.clamp(1, 128);

    let primary = if let Some(symbol) = symbol_filter {
        let query_enabled = format!(
            "SELECT security_id, symbol, company_name, tiers, enabled, min_volume \
             FROM trading.watchlist FINAL \
             WHERE upper(symbol) = upper(?) \
             ORDER BY enabled DESC, symbol LIMIT {}",
            limit
        );
        state
            .ch
            .query(&query_enabled)
            .bind(symbol)
            .fetch_all::<WatchRow>()
            .await
            .unwrap_or_default()
    } else {
        let query_enabled = format!(
            "SELECT security_id, symbol, company_name, tiers, enabled, min_volume \
             FROM trading.watchlist FINAL \
             WHERE enabled = 1 \
             ORDER BY symbol LIMIT {}",
            limit
        );
        state
            .ch
            .query(&query_enabled)
            .fetch_all::<WatchRow>()
            .await
            .unwrap_or_default()
    };

    if !primary.is_empty() {
        return primary;
    }

    let fallback_query = format!(
        "SELECT security_id, symbol, company_name, tiers, enabled, min_volume \
         FROM trading.watchlist FINAL ORDER BY symbol LIMIT {}",
        limit
    );
    state
        .ch
        .query(&fallback_query)
        .fetch_all::<WatchRow>()
        .await
        .unwrap_or_default()
}

fn build_candidate_seeds(
    rows: &[WatchRow],
    volume_map: &HashMap<String, String>,
    live_quote_map: Option<&HashMap<String, QuoteItem>>,
    historical_map: &HashMap<String, HistoricalFallbackRow>,
) -> Vec<CandidateSeed> {
    rows.iter()
        .enumerate()
        .map(|(idx, row)| {
            if let Some(quotes) = live_quote_map {
                if let Some(quote) = quotes.get(&row.security_id) {
                    return seed_from_quote(row, quote, volume_map);
                }
            }
            if let Some(history) = historical_map.get(&row.symbol) {
                return seed_from_history(row, history, volume_map);
            }
            fallback_seed(row, volume_map, idx)
        })
        .collect()
}

async fn load_historical_fallbacks(
    state: &AppState,
    symbols: &[String],
) -> anyhow::Result<HashMap<String, HistoricalFallbackRow>> {
    if symbols.is_empty() {
        return Ok(HashMap::new());
    }
    let cached = load_cached_historical_fallbacks(state, symbols).await?;
    if !cached.is_empty() {
        return Ok(cached);
    }

    let symbol_list = symbols
        .iter()
        .map(|symbol| format!("'{}'", symbol.replace('\'', "''")))
        .collect::<Vec<_>>()
        .join(",");

    let query = format!("WITH daily AS ( \
            SELECT \
                symbol, \
                toDate(date) AS trade_date, \
                argMin(open, bucket) AS day_open, \
                max(high) AS day_high, \
                min(low) AS day_low, \
                argMax(close, bucket) AS day_close, \
                toFloat64(sum(volume)) AS day_volume \
            FROM file('parquets/candles_*.parquet', Parquet) \
            WHERE symbol IN ({}) \
              AND date IS NOT NULL \
              AND open IS NOT NULL \
              AND high IS NOT NULL \
              AND low IS NOT NULL \
              AND close IS NOT NULL \
              AND volume IS NOT NULL \
            GROUP BY symbol, trade_date \
        ), ranked AS ( \
            SELECT \
                symbol, \
                toString(trade_date) AS trade_date, \
                toFloat64(day_open) AS day_open, \
                toFloat64(day_high) AS day_high, \
                toFloat64(day_low) AS day_low, \
                toFloat64(day_close) AS day_close, \
                toFloat64(day_volume) AS day_volume, \
                toFloat64(lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS prev_close, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM daily \
        ) \
        SELECT symbol, trade_date, day_open, day_high, day_low, day_close, day_volume, prev_close \
        FROM ranked \
        WHERE rn = 1", symbol_list);

    let rows = run_clickhouse_json_query::<HistoricalFallbackRow>(state, query).await?;
    Ok(rows.into_iter().map(|row| (row.symbol.clone(), row)).collect())
}

async fn load_live_signal_baselines(
    state: &AppState,
    symbols: &[String],
) -> anyhow::Result<HashMap<String, HistoricalScreenerFeatureRow>> {
    if symbols.is_empty() {
        return Ok(HashMap::new());
    }
    let cached = load_cached_live_signal_baselines(state, symbols).await?;
    if !cached.is_empty() {
        return Ok(cached);
    }

    let symbol_list = symbols
        .iter()
        .map(|symbol| format!("'{}'", escape_sql_string(symbol)))
        .collect::<Vec<_>>()
        .join(",");
    let parquet_source = parquet_source_for_recent_months(24);
    let query = format!(
        "WITH daily AS ( \
            SELECT \
                symbol, \
                toDate(date) AS trade_date, \
                argMax(close, bucket) AS day_close, \
                max(high) AS day_high, \
                min(low) AS day_low, \
                toUInt64(sum(volume)) AS day_volume \
            FROM ({}) \
            WHERE symbol IN ({}) \
              AND date IS NOT NULL \
              AND open IS NOT NULL \
              AND high IS NOT NULL \
              AND low IS NOT NULL \
              AND close IS NOT NULL \
              AND volume IS NOT NULL \
            GROUP BY symbol, trade_date \
        ) \
        SELECT \
            symbol, \
            toString(trade_date) AS trade_date, \
            toFloat64(day_close) AS day_close, \
            toFloat64(day_high) AS day_high, \
            toFloat64(day_low) AS day_low, \
            day_volume, \
            toFloat64(sma20) AS sma20, \
            toFloat64(sma50) AS sma50, \
            toFloat64(sma200) AS sma200, \
            toFloat64(avg_volume20) AS avg_volume20, \
            toFloat64(high_20d) AS high_20d, \
            toFloat64(high_52w) AS high_52w, \
            toFloat64(low_52w) AS low_52w, \
            toFloat64(rsi10) AS rsi10 \
        FROM ( \
            SELECT \
                symbol, \
                trade_date, \
                day_close, \
                day_high, \
                day_low, \
                day_volume, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200, \
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM ( \
                SELECT *, \
                    greatest(day_close - lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS gain, \
                    greatest(lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - day_close, 0) AS loss \
                FROM daily \
            ) \
        ) \
        WHERE rn = 1",
        parquet_source,
        symbol_list
    );

    let rows = run_clickhouse_json_query::<HistoricalScreenerFeatureRow>(state, query).await?;
    Ok(rows
        .into_iter()
        .filter_map(|row| row.symbol.clone().map(|symbol| (symbol, row)))
        .collect())
}

fn seed_from_history(
    row: &WatchRow,
    history: &HistoricalFallbackRow,
    volume_map: &HashMap<String, String>,
) -> CandidateSeed {
    let prev_close = history.prev_close.max(0.01) as f32;
    let open = history.day_open as f32;
    let high = history.day_high as f32;
    let low = history.day_low as f32;
    let last = history.day_close as f32;
    let day_change_pct = ((last - prev_close) / prev_close) * 100.0;
    let open_gap_pct = ((open - prev_close) / prev_close) * 100.0;
    let recovery_pct = if low > 0.0 { ((last - low) / low) * 100.0 } else { 0.0 };
    let distance_to_high_pct = if high > 0.0 { ((high - last) / high) * 100.0 } else { 0.0 };
    let intraday_range_pct = if prev_close > 0.0 { ((high - low) / prev_close) * 100.0 } else { 0.0 };

    CandidateSeed {
        symbol: row.symbol.clone(),
        company_name: row.company_name.clone(),
        tiers: row.tiers.clone(),
        liquidity_bucket: volume_map
            .get(&row.symbol)
            .cloned()
            .unwrap_or_else(|| liquidity_from_tiers(row)),
        open_price: round2(open),
        high_price: round2(high),
        low_price: round2(low),
        last_price: round2(last),
        prev_close: round2(prev_close),
        day_volume: history.day_volume,
        day_change_pct: round2(day_change_pct),
        open_gap_pct: round2(open_gap_pct),
        recovery_pct: round2(recovery_pct),
        distance_to_high_pct: round2(distance_to_high_pct),
        intraday_range_pct: round2(intraday_range_pct),
        source: format!("parquet-history:{}", history.trade_date),
    }
}

fn seed_from_quote(
    row: &WatchRow,
    quote: &QuoteItem,
    volume_map: &HashMap<String, String>,
) -> CandidateSeed {
    let prev_close = quote.close().max(0.01);
    let open = if quote.open() > 0.0 { quote.open() } else { prev_close };
    let high = if quote.high() > 0.0 { quote.high() } else { quote.last_price.max(open) };
    let low = if quote.low() > 0.0 { quote.low() } else { quote.last_price.min(open) };
    let last = if quote.last_price > 0.0 { quote.last_price } else { prev_close };
    let day_change_pct = ((last - prev_close) / prev_close) * 100.0;
    let open_gap_pct = ((open - prev_close) / prev_close) * 100.0;
    let recovery_pct = if low > 0.0 { ((last - low) / low) * 100.0 } else { 0.0 };
    let distance_to_high_pct = if high > 0.0 { ((high - last) / high) * 100.0 } else { 0.0 };
    let intraday_range_pct = if prev_close > 0.0 { ((high - low) / prev_close) * 100.0 } else { 0.0 };

    CandidateSeed {
        symbol: row.symbol.clone(),
        company_name: row.company_name.clone(),
        tiers: row.tiers.clone(),
        liquidity_bucket: volume_map
            .get(&row.symbol)
            .cloned()
            .unwrap_or_else(|| liquidity_from_tiers(row)),
        open_price: round2(open),
        high_price: round2(high),
        low_price: round2(low),
        last_price: round2(last),
        prev_close: round2(prev_close),
        day_volume: quote.volume as f64,
        day_change_pct: round2(day_change_pct),
        open_gap_pct: round2(open_gap_pct),
        recovery_pct: round2(recovery_pct),
        distance_to_high_pct: round2(distance_to_high_pct),
        intraday_range_pct: round2(intraday_range_pct),
        source: "dhan-live".to_string(),
    }
}

fn fallback_seed(
    row: &WatchRow,
    volume_map: &HashMap<String, String>,
    idx: usize,
) -> CandidateSeed {
    let seed = row
        .symbol
        .bytes()
        .fold(0u32, |acc, item| acc.wrapping_mul(33).wrapping_add(item as u32))
        .wrapping_add(idx as u32)
        .wrapping_add(row.min_volume);
    let base_price = 120.0 + (seed % 1800) as f32;
    let trend = ((seed % 420) as f32 / 100.0) - 1.0;
    let gap = (((seed / 11) % 220) as f32 / 100.0) - 0.9;
    let recovery = 0.8 + (((seed / 7) % 180) as f32 / 100.0);
    let distance_to_high = 0.3 + (((seed / 13) % 120) as f32 / 100.0);
    let range = 1.1 + (((seed / 17) % 180) as f32 / 100.0);

    CandidateSeed {
        symbol: row.symbol.clone(),
        company_name: row.company_name.clone(),
        tiers: row.tiers.clone(),
        liquidity_bucket: volume_map
            .get(&row.symbol)
            .cloned()
            .unwrap_or_else(|| liquidity_from_tiers(row)),
        open_price: round2(base_price * (1.0 + gap / 100.0)),
        high_price: round2(base_price * (1.0 + distance_to_high / 100.0)),
        low_price: round2(base_price * (1.0 - recovery / 100.0)),
        last_price: round2(base_price),
        prev_close: round2(base_price / (1.0 + trend / 100.0)),
        day_volume: 0.0,
        day_change_pct: round2(trend),
        open_gap_pct: round2(gap),
        recovery_pct: round2(recovery),
        distance_to_high_pct: round2(distance_to_high),
        intraday_range_pct: round2(range),
        source: "watchlist-fallback".to_string(),
    }
}

fn compute_market_regime(seeds: &[CandidateSeed], live_quotes: bool) -> MarketRegime {
    let advances = seeds.iter().filter(|seed| seed.day_change_pct > 0.25).count();
    let declines = seeds.iter().filter(|seed| seed.day_change_pct < -0.25).count();
    let breadth_ratio = if declines == 0 {
        advances as f32
    } else {
        advances as f32 / declines as f32
    };

    let (label, tone) = if breadth_ratio >= 1.4 {
        ("Risk-On Breadth", "bullish")
    } else if breadth_ratio <= 0.8 {
        ("Selective Tape", "cautious")
    } else {
        ("Balanced Rotation", "neutral")
    };

    let summary = if live_quotes {
        format!(
            "{} across the active swing universe with {} advancing names versus {} decliners.",
            label, advances, declines
        )
    } else {
        "The scanner is running in fallback mode because live Dhan quotes are unavailable; regime is estimated from the curated watchlist model.".to_string()
    };

    MarketRegime {
        label: label.to_string(),
        tone: tone.to_string(),
        summary,
        advances,
        declines,
        breadth_ratio: round2(breadth_ratio),
    }
}

fn build_live_candidate(
    seed: CandidateSeed,
    regime: &MarketRegime,
    baseline: Option<&HistoricalScreenerFeatureRow>,
    strategy_statuses: &HashMap<String, String>,
    entry_window_open: bool,
) -> SwingCandidate {
    let fallback_family = classify_setup_family(&seed);
    let fallback_score = fallback_candidate_score(&seed, &fallback_family, regime);
    let live_signal = evaluate_live_signal(&seed, baseline, strategy_statuses, entry_window_open);

    let family = if live_signal.setup_family == "Unscored" {
        fallback_family.clone()
    } else {
        live_signal.setup_family.clone()
    };
    let score = if live_signal.score > 0 { live_signal.score } else { fallback_score };
    let confidence = signal_confidence(&live_signal.status, score);

    let regime_fit = if regime.tone == "bullish" {
        (score as f32 * 0.96).round().clamp(60.0, 95.0) as u8
    } else if regime.tone == "neutral" {
        (score as f32 * 0.9).round().clamp(55.0, 90.0) as u8
    } else {
        (score as f32 * 0.82).round().clamp(50.0, 84.0) as u8
    };

    let (target_buffer, stop_buffer, max_hold_sessions) = strategy_exit_plan(&live_signal.strategy_id)
        .unwrap_or_else(|| {
            (
                target_buffer_for_family(&fallback_family),
                stop_buffer_for_family(&fallback_family),
                expected_hold_for_family(&fallback_family),
            )
        });
    let stop_loss = round2(seed.last_price * (1.0 - stop_buffer / 100.0));
    let target_price = round2(seed.last_price * (1.0 + target_buffer / 100.0));
    let risk_reward = round2((target_price - seed.last_price) / (seed.last_price - stop_loss).max(0.01));
    let entry_zone = format!(
        "Rs {:.2} - Rs {:.2}",
        seed.last_price * 0.995,
        seed.last_price * 1.008
    );
    let expected_hold = max_hold_sessions.to_string();

    let mut reasons = vec![
        live_signal.reason.clone(),
        format!(
            "{} is sitting {:.2}% from the session high with {:+.2}% day change and {:+.2}% opening gap.",
            seed.symbol, seed.distance_to_high_pct, seed.day_change_pct, seed.open_gap_pct
        ),
        format!(
            "{} liquidity bucket plus {} tiers make execution quality more dependable.",
            seed.liquidity_bucket,
            if seed.tiers.is_empty() {
                "base watchlist".to_string()
            } else {
                seed.tiers.join(", ")
            }
        ),
    ];
    reasons.dedup();

    let risks = vec![
        format!(
            "If price loses Rs {:.2}, the structure weakens and the thesis should be invalidated quickly.",
            stop_loss
        ),
        if live_signal.status != "ENTRY_NOW" {
            "This is not an approved live entry unless the signal status changes to Enter Now.".to_string()
        } else if regime.tone == "cautious" {
            "Market breadth is not fully supportive right now, so position size should stay controlled.".to_string()
        } else {
            "A failed move near the trigger can pull the setup back into a base-building phase.".to_string()
        },
    ];

    let thesis = match live_signal.status.as_str() {
        "ENTRY_NOW" => format!(
            "{} is currently matching {} from the latest backtest-approved live rules.",
            seed.symbol, live_signal.strategy_label
        ),
        "WATCH" => format!(
            "{} matches {}, but the latest backtest status is watch-only, so it should be monitored rather than entered automatically.",
            seed.symbol, live_signal.strategy_label
        ),
        "INVALIDATED" => format!(
            "{} has lost the live rule structure and should not be treated as an entry until it rebuilds.",
            seed.symbol
        ),
        "NO_TRADE" => format!(
            "{} is not a live entry because the matching rule is not approved by the latest backtest diagnostics.",
            seed.symbol
        ),
        _ => format!(
            "{} is on the radar, but the live data has not satisfied a backtest-approved entry rule yet.",
            seed.symbol
        ),
    };

    SwingCandidate {
        symbol: seed.symbol,
        company_name: seed.company_name,
        setup_family: family,
        bias: "Long".to_string(),
        score,
        confidence: confidence.to_string(),
        regime_fit,
        risk_reward,
        last_price: seed.last_price,
        day_change_pct: seed.day_change_pct,
        open_gap_pct: seed.open_gap_pct,
        distance_to_high_pct: seed.distance_to_high_pct,
        liquidity_bucket: seed.liquidity_bucket,
        entry_zone,
        stop_loss,
        target_price,
        expected_hold,
        thesis,
        reasons,
        risks,
        source: seed.source,
        live_signal,
    }
}

fn fallback_candidate_score(seed: &CandidateSeed, family: &str, regime: &MarketRegime) -> u8 {
    let liquidity_bonus = match seed.liquidity_bucket.as_str() {
        "MEGA" => 12.0,
        "LARGE" => 8.0,
        "MID" => 4.0,
        _ => 2.0,
    };
    let family_bonus = match family {
        "Breakout Continuation" => 18.0,
        "Gap-and-Hold" => 16.0,
        "Relative Strength Leader" => 15.0,
        "Pullback To Support" => 13.0,
        "Oversold Reclaim" => 11.0,
        _ => 10.0,
    };
    let regime_bonus = if regime.tone == "bullish" { 8.0 } else if regime.tone == "neutral" { 5.0 } else { 2.0 };
    let action_bonus = (seed.day_change_pct.max(0.0) * 8.0) + (seed.recovery_pct * 4.5);
    let tightness_bonus = ((2.5 - seed.distance_to_high_pct.clamp(0.0, 2.5)) * 6.0).max(0.0);
    let range_penalty = (seed.intraday_range_pct - 3.5).max(0.0) * 3.5;
    let tier_bonus = if seed.tiers.iter().any(|tier| tier == "Tier1") { 6.0 } else { 0.0 };

    (44.0 + family_bonus + regime_bonus + liquidity_bonus + action_bonus + tightness_bonus + tier_bonus - range_penalty)
        .round()
        .clamp(58.0, 96.0) as u8
}

fn evaluate_live_signal(
    seed: &CandidateSeed,
    baseline: Option<&HistoricalScreenerFeatureRow>,
    strategy_statuses: &HashMap<String, String>,
    entry_window_open: bool,
) -> LiveSignal {
    let Some(row) = baseline else {
        return LiveSignal {
            status: "WAIT_FOR_TRIGGER".to_string(),
            label: "Need History".to_string(),
            reason: "Live rule evaluation needs rolling historical features before it can produce an entry signal.".to_string(),
            strategy_id: "unscored".to_string(),
            strategy_label: "Unscored".to_string(),
            strategy_status: "Unknown".to_string(),
            setup_family: "Unscored".to_string(),
            score: 0,
            as_of: seed.source.clone(),
            trigger_price: None,
        };
    };

    let historical_close = row.day_close.unwrap_or(seed.prev_close as f64).max(0.01);
    let _open = seed.open_price as f64;
    let close = seed.last_price as f64;
    let high = seed.high_price.max(seed.last_price) as f64;
    let low = seed.low_price.min(seed.last_price) as f64;
    let sma20 = replace_latest_average(row.sma20.unwrap_or(historical_close), historical_close, close, 20.0);
    let sma50 = replace_latest_average(row.sma50.unwrap_or(historical_close), historical_close, close, 50.0);
    let sma200 = replace_latest_average(row.sma200.unwrap_or(historical_close), historical_close, close, 200.0);
    let rsi10 = row.rsi10.unwrap_or(50.0);
    let avg_volume20 = row.avg_volume20.unwrap_or(0.0).max(1.0);
    let high_20d = row.high_20d.unwrap_or(high).max(high);
    let high_52w = row.high_52w.unwrap_or(high).max(high);
    let low_52w = row.low_52w.unwrap_or(low).min(low);
    let day_volume = if seed.day_volume > 0.0 {
        seed.day_volume
    } else {
        parse_volume(row.day_volume.as_deref())
    };

    if close <= 0.0 || high_20d <= 0.0 || high_52w <= 0.0 || low_52w <= 0.0 {
        return default_live_signal();
    }

    let breakout_pct = ((high_20d - close) / high_20d) * 100.0;
    let distance_to_52w_high_pct = ((high_52w - close) / high_52w) * 100.0;
    let range_span = (high_52w - low_52w).max(0.01);
    let range_position_pct = ((close - low_52w) / range_span) * 100.0;
    let volume_ratio = day_volume / avg_volume20;
    let trend_up = close > sma20 && sma20 > sma50;
    let pullback_zone = close >= sma20 * 0.98 && close <= sma20 * 1.03;
    let rsi10_pullback = close > sma200 && rsi10 < 30.0;
    let setup_family = if rsi10_pullback {
        "RSI10 Pullback Reversion"
    } else if trend_up && breakout_pct <= 1.5 && volume_ratio >= 1.1 {
        "Breakout Setup"
    } else if trend_up && pullback_zone {
        "Pullback To 20 DMA"
    } else if close > sma50 && distance_to_52w_high_pct <= 8.0 {
        "Near 52W High"
    } else {
        "Trend Filter"
    };
    let score = live_strategy_score(
        trend_up,
        breakout_pct,
        distance_to_52w_high_pct,
        volume_ratio,
        pullback_zone,
        range_position_pct,
    );
    let (strategy_id, strategy_label) = strategy_match_for_screener(
        setup_family,
        score,
        trend_up,
        pullback_zone,
        breakout_pct,
        distance_to_52w_high_pct,
        range_position_pct,
        volume_ratio,
        close,
        sma20,
        sma200,
        rsi10,
    );
    let strategy_status = strategy_statuses
        .get(strategy_id)
        .cloned()
        .unwrap_or_else(|| default_strategy_status(strategy_id).to_string());
    let trigger_price = match setup_family {
        "Breakout Setup" => Some(round2(high_20d as f32)),
        "Pullback To 20 DMA" => Some(round2(sma20 as f32)),
        "Near 52W High" => Some(round2(high_52w as f32)),
        _ => None,
    };

    let lost_structure = close < sma50 * 0.985 || range_position_pct < 40.0;
    let (status, label, reason) = if lost_structure {
        (
            "INVALIDATED",
            "Invalidated",
            format!(
                "Price is below the live trend structure: close {:.2}, SMA50 {:.2}, range position {:.2}%.",
                close, sma50, range_position_pct
            ),
        )
    } else if strategy_id == "unlinked-screener" {
        (
            "WAIT_FOR_TRIGGER",
            "Wait For Trigger",
            format!(
                "No backtest-linked rule is active yet; score {}, setup {}, volume ratio {:.2}.",
                score, setup_family, volume_ratio
            ),
        )
    } else if strategy_status == "Research" && entry_window_open {
        (
            "ENTRY_NOW",
            "Enter Now",
            format!(
                "{} is active now with score {}, distance to 52W high {:.2}%, and volume ratio {:.2}.",
                strategy_label, score, distance_to_52w_high_pct, volume_ratio
            ),
        )
    } else if strategy_status == "Research" {
        (
            "WAIT_FOR_TRIGGER",
            "Signal Ready",
            format!(
                "{} matches the approved rule, but NSE regular-session entry is closed right now.",
                strategy_label
            ),
        )
    } else if strategy_status == "Watch" {
        (
            "WATCH",
            "Watch Only",
            format!(
                "{} matches, but latest backtest diagnostics mark it Watch rather than Research.",
                strategy_label
            ),
        )
    } else {
        (
            "NO_TRADE",
            "No Trade",
            format!(
                "{} is {}, so this rule is not approved for fresh live entries.",
                strategy_label, strategy_status
            ),
        )
    };

    LiveSignal {
        status: status.to_string(),
        label: label.to_string(),
        reason,
        strategy_id: strategy_id.to_string(),
        strategy_label: strategy_label.to_string(),
        strategy_status,
        setup_family: setup_family.to_string(),
        score,
        as_of: format!("{} / baseline {}", seed.source, row.trade_date.clone().unwrap_or_default()),
        trigger_price,
    }
}

fn default_live_signal() -> LiveSignal {
    LiveSignal {
        status: "WAIT_FOR_TRIGGER".to_string(),
        label: "Wait For Trigger".to_string(),
        reason: "Live signal rules could not be evaluated from the available data.".to_string(),
        strategy_id: "unscored".to_string(),
        strategy_label: "Unscored".to_string(),
        strategy_status: "Unknown".to_string(),
        setup_family: "Unscored".to_string(),
        score: 0,
        as_of: "unknown".to_string(),
        trigger_price: None,
    }
}

fn replace_latest_average(avg: f64, old_value: f64, new_value: f64, window: f64) -> f64 {
    ((avg * window) - old_value + new_value) / window
}

fn parse_volume(raw: Option<&str>) -> f64 {
    raw.and_then(|value| value.parse::<f64>().ok()).unwrap_or(0.0)
}

fn live_strategy_score(
    trend_up: bool,
    breakout_pct: f64,
    distance_to_52w_high_pct: f64,
    volume_ratio: f64,
    pullback_zone: bool,
    range_position_pct: f64,
) -> u8 {
    let mut score: f64 = 50.0;
    if trend_up {
        score += 18.0;
    }
    if breakout_pct <= 1.5 {
        score += 14.0;
    } else if breakout_pct <= 4.0 {
        score += 8.0;
    }
    if distance_to_52w_high_pct <= 8.0 {
        score += 10.0;
    }
    if volume_ratio >= 1.2 {
        score += 10.0;
    } else if volume_ratio >= 1.0 {
        score += 5.0;
    }
    if pullback_zone {
        score += 8.0;
    }
    if range_position_pct >= 70.0 {
        score += 6.0;
    }
    score.round().clamp(50.0, 96.0) as u8
}

fn strategy_exit_plan(strategy_id: &str) -> Option<(f32, f32, &'static str)> {
    match strategy_id {
        "swing-breakout-v1" => Some((8.0, 4.0, "10 sessions")),
        "breakout-volume-v2" => Some((10.0, 4.0, "12 sessions")),
        "pullback-20dma-v1" => Some((6.0, 3.0, "10 sessions")),
        "pullback-quality-v2" => Some((7.0, 3.0, "12 sessions")),
        "rsi10-pullback-reversion-v1" => Some((4.0, 4.0, "5 sessions")),
        "near-52w-high-v1" => Some((10.0, 5.0, "15 sessions")),
        "near-52w-high-tight-v2" => Some((8.0, 4.0, "12 sessions")),
        "near-52w-high-runner-v2" => Some((12.0, 5.0, "20 sessions")),
        "near-52w-high-volume-v3" => Some((10.0, 4.5, "15 sessions")),
        "momentum-core-v1" => Some((15.0, 6.0, "25 sessions")),
        _ => None,
    }
}

fn signal_confidence(status: &str, score: u8) -> &'static str {
    match status {
        "ENTRY_NOW" => "Enter Now",
        "WATCH" => "Watch Only",
        "NO_TRADE" => "No Trade",
        "INVALIDATED" => "Invalidated",
        _ if score >= 88 => "Wait For Trigger",
        _ => "Developing",
    }
}

fn live_signal_rank(status: &str) -> u8 {
    match status {
        "ENTRY_NOW" => 0,
        "WATCH" => 1,
        "WAIT_FOR_TRIGGER" => 2,
        "NO_TRADE" => 3,
        "INVALIDATED" => 4,
        _ => 5,
    }
}

#[allow(dead_code)]
fn build_candidate(seed: CandidateSeed, regime: &MarketRegime) -> SwingCandidate {
    let family = classify_setup_family(&seed);
    let liquidity_bonus = match seed.liquidity_bucket.as_str() {
        "MEGA" => 12.0,
        "LARGE" => 8.0,
        "MID" => 4.0,
        _ => 2.0,
    };
    let family_bonus = match family.as_str() {
        "Breakout Continuation" => 18.0,
        "Gap-and-Hold" => 16.0,
        "Relative Strength Leader" => 15.0,
        "Pullback To Support" => 13.0,
        "Oversold Reclaim" => 11.0,
        _ => 10.0,
    };
    let regime_bonus = if regime.tone == "bullish" { 8.0 } else if regime.tone == "neutral" { 5.0 } else { 2.0 };
    let action_bonus = (seed.day_change_pct.max(0.0) * 8.0) + (seed.recovery_pct * 4.5);
    let tightness_bonus = ((2.5 - seed.distance_to_high_pct.clamp(0.0, 2.5)) * 6.0).max(0.0);
    let range_penalty = (seed.intraday_range_pct - 3.5).max(0.0) * 3.5;
    let tier_bonus = if seed.tiers.iter().any(|tier| tier == "Tier1") { 6.0 } else { 0.0 };

    let raw_score = 44.0 + family_bonus + regime_bonus + liquidity_bonus + action_bonus + tightness_bonus + tier_bonus - range_penalty;
    let score = raw_score.round().clamp(58.0, 96.0) as u8;
    let confidence = if score >= 88 {
        "High Conviction"
    } else if score >= 78 {
        "Actionable"
    } else if score >= 68 {
        "Watchlist"
    } else {
        "Developing"
    };

    let regime_fit = if regime.tone == "bullish" {
        (score as f32 * 0.96).round().clamp(60.0, 95.0) as u8
    } else if regime.tone == "neutral" {
        (score as f32 * 0.9).round().clamp(55.0, 90.0) as u8
    } else {
        (score as f32 * 0.82).round().clamp(50.0, 84.0) as u8
    };

    let stop_loss = round2(seed.last_price * (1.0 - stop_buffer_for_family(&family) / 100.0));
    let target_price = round2(seed.last_price * (1.0 + target_buffer_for_family(&family) / 100.0));
    let risk_reward = round2((target_price - seed.last_price) / (seed.last_price - stop_loss).max(0.01));
    let entry_zone = format!(
        "₹{:.2} - ₹{:.2}",
        seed.last_price * 0.995,
        seed.last_price * 1.008
    );
    let expected_hold = expected_hold_for_family(&family).to_string();

    let reasons = vec![
        format!(
            "{} is sitting {:.2}% from the session high, which keeps the setup tight enough for a swing entry.",
            seed.symbol, seed.distance_to_high_pct
        ),
        format!(
            "Day change is {:+.2}% with a {:+.2}% opening gap, giving us a clean price-structure read.",
            seed.day_change_pct, seed.open_gap_pct
        ),
        format!(
            "{} liquidity bucket plus {} tiers make execution quality more dependable.",
            seed.liquidity_bucket,
            if seed.tiers.is_empty() {
                "base watchlist".to_string()
            } else {
                seed.tiers.join(", ")
            }
        ),
    ];

    let risks = vec![
        format!(
            "If price loses ₹{:.2}, the structure weakens and the thesis should be invalidated quickly.",
            stop_loss
        ),
        if regime.tone == "cautious" {
            "Market breadth is not fully supportive right now, so position size should stay controlled.".to_string()
        } else {
            "A failed breakout near the recent high can pull the setup back into a base-building phase.".to_string()
        },
    ];

    let thesis = match family.as_str() {
        "Breakout Continuation" => format!(
            "{} is pressing near its intraday high with strong recovery from the low, so the probability favors continuation if it holds the entry zone.",
            seed.symbol
        ),
        "Gap-and-Hold" => format!(
            "{} opened with strength and is still defending that gap, which is exactly the behavior we want before planning a multi-day continuation swing.",
            seed.symbol
        ),
        "Relative Strength Leader" => format!(
            "{} is outperforming the broader tape and staying firm despite rotation, which makes it a strong leadership candidate for the next swing leg.",
            seed.symbol
        ),
        "Oversold Reclaim" => format!(
            "{} is recovering from early weakness with improving price acceptance, so this qualifies as a controlled reclaim rather than blind dip buying.",
            seed.symbol
        ),
        _ => format!(
            "{} is building a cleaner base than most names in the universe and offers a defined risk box for a swing entry.",
            seed.symbol
        ),
    };

    SwingCandidate {
        symbol: seed.symbol,
        company_name: seed.company_name,
        setup_family: family,
        bias: "Long".to_string(),
        score,
        confidence: confidence.to_string(),
        regime_fit,
        risk_reward,
        last_price: seed.last_price,
        day_change_pct: seed.day_change_pct,
        open_gap_pct: seed.open_gap_pct,
        distance_to_high_pct: seed.distance_to_high_pct,
        liquidity_bucket: seed.liquidity_bucket,
        entry_zone,
        stop_loss,
        target_price,
        expected_hold,
        thesis,
        reasons,
        risks,
        source: seed.source,
        live_signal: default_live_signal(),
    }
}

fn classify_setup_family(seed: &CandidateSeed) -> String {
    if seed.day_change_pct >= 2.3 && seed.distance_to_high_pct <= 0.8 {
        "Breakout Continuation".to_string()
    } else if seed.open_gap_pct >= 1.0 && seed.day_change_pct >= 1.0 {
        "Gap-and-Hold".to_string()
    } else if seed.day_change_pct >= 0.7 && seed.recovery_pct >= 1.0 {
        "Relative Strength Leader".to_string()
    } else if seed.day_change_pct <= -0.6 && seed.recovery_pct >= 1.3 {
        "Oversold Reclaim".to_string()
    } else {
        "Pullback To Support".to_string()
    }
}

fn compute_setup_mix(candidates: &[SwingCandidate]) -> Vec<SetupMix> {
    let mut grouped: HashMap<String, (usize, u32)> = HashMap::new();
    for candidate in candidates {
        let entry = grouped
            .entry(candidate.setup_family.clone())
            .or_insert((0usize, 0u32));
        entry.0 += 1;
        entry.1 += candidate.score as u32;
    }

    let mut mix: Vec<SetupMix> = grouped
        .into_iter()
        .map(|(family, (count, score_sum))| SetupMix {
            family,
            count,
            avg_score: round2(score_sum as f32 / count as f32),
        })
        .collect();
    mix.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| b.avg_score.total_cmp(&a.avg_score)));
    mix
}

fn load_volume_groups_map() -> HashMap<String, String> {
    let candidates = ["data/volume_groups.json", "../data/volume_groups.json"];
    for path in candidates {
        if let Ok(content) = fs::read_to_string(path) {
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(groups) = parsed.get("volume_groups").and_then(|value| value.as_object()) {
                    let mut map = HashMap::new();
                    for (raw_label, symbols) in groups {
                        let bucket = if raw_label.contains("MEGA") {
                            "MEGA"
                        } else if raw_label.contains("LARGE") {
                            "LARGE"
                        } else if raw_label.contains("MID") {
                            "MID"
                        } else if raw_label.contains("SMALL") {
                            "SMALL"
                        } else {
                            continue;
                        };
                        if let Some(symbols) = symbols.as_array() {
                            for symbol in symbols {
                                if let Some(symbol) = symbol.as_str() {
                                    map.insert(symbol.to_string(), bucket.to_string());
                                }
                            }
                        }
                    }
                    return map;
                }
            }
        }
    }
    HashMap::new()
}

fn liquidity_from_tiers(row: &WatchRow) -> String {
    if row.tiers.iter().any(|tier| tier == "Tier1" || tier == "F&O") || row.enabled == 1 {
        "LARGE".to_string()
    } else {
        "MID".to_string()
    }
}

fn stop_buffer_for_family(family: &str) -> f32 {
    match family {
        "Breakout Continuation" => 3.2,
        "Gap-and-Hold" => 3.6,
        "Relative Strength Leader" => 3.0,
        "Oversold Reclaim" => 4.4,
        _ => 3.8,
    }
}

fn target_buffer_for_family(family: &str) -> f32 {
    match family {
        "Breakout Continuation" => 8.0,
        "Gap-and-Hold" => 7.0,
        "Relative Strength Leader" => 7.4,
        "Oversold Reclaim" => 9.0,
        _ => 6.5,
    }
}

fn expected_hold_for_family(family: &str) -> &'static str {
    match family {
        "Breakout Continuation" => "6-12 sessions",
        "Gap-and-Hold" => "4-9 sessions",
        "Relative Strength Leader" => "7-15 sessions",
        "Oversold Reclaim" => "3-7 sessions",
        _ => "5-10 sessions",
    }
}

fn normalize_history_range(raw: Option<&str>) -> String {
    match raw.unwrap_or("1y").to_ascii_lowercase().as_str() {
        "3m" => "3m".to_string(),
        "6m" => "6m".to_string(),
        "1y" => "1y".to_string(),
        "3y" => "3y".to_string(),
        "5y" => "5y".to_string(),
        _ => "1y".to_string(),
    }
}

fn last_completed_trading_day() -> chrono::NaiveDate {
    let now = now_ist();
    let today = now.date_naive();
    let close_reached = now.hour() > 15 || (now.hour() == 15 && now.minute() >= 30);

    if !is_nse_holiday(today) && close_reached {
        today
    } else {
        prev_trading_day(today)
    }
}

fn history_where_clause(range: &str) -> &'static str {
    match range {
        "3m" => "subtractMonths(today(), 3)",
        "6m" => "subtractMonths(today(), 6)",
        "3y" => "subtractYears(today(), 3)",
        "5y" => "subtractYears(today(), 5)",
        _ => "subtractYears(today(), 1)",
    }
}

fn history_month_span(range: &str) -> usize {
    match range {
        "3m" => 3,
        "6m" => 6,
        "3y" => 36,
        "5y" => 60,
        _ => 12,
    }
}

fn parquet_source_for_recent_months(months: usize) -> String {
    let now = Utc::now();
    let current_year = now.year();
    let years_to_scan = ((months.max(1) + 11) / 12 + 1).max(1);
    let mut parts = Vec::new();

    for offset in 0..years_to_scan {
        let year = current_year - offset as i32;
        parts.push(format!(
            "SELECT * FROM file('parquets/candles_{year:04}*.parquet', Parquet)"
        ));
    }

    parts.join(" UNION ALL ")
}

fn parquet_feature_source_for_recent_months(months: usize) -> String {
    let now = Utc::now();
    let current_year = now.year();
    let years_to_scan = ((months.max(1) + 11) / 12 + 1).max(1);
    let mut parts = Vec::new();

    for offset in 0..years_to_scan {
        let year = current_year - offset as i32;
        parts.push(format!(
            "SELECT date AS candle_date, symbol, bucket, open, high, low, close, volume FROM file('parquets/candles_{year:04}*.parquet', Parquet)"
        ));
    }

    parts.join(" UNION ALL ")
}

fn parquet_source_for_recent_years(years: usize) -> String {
    let now = Utc::now();
    let current_year = now.year();
    let mut parts = Vec::new();

    for offset in 0..years.max(1) {
        let year = current_year - offset as i32;
        parts.push(format!(
            "SELECT * FROM file('parquets/candles_{year:04}*.parquet', Parquet)"
        ));
    }

    parts.join(" UNION ALL ")
}

#[derive(Deserialize)]
struct ClickHouseJsonEnvelope<T> {
    data: Vec<T>,
}

async fn run_clickhouse_json_query<T: DeserializeOwned>(
    state: &AppState,
    sql: String,
) -> anyhow::Result<Vec<T>> {
    let response = reqwest::Client::new()
        .post(&state.ch_url)
        .body(format!("{} FORMAT JSON", sql))
        .send()
        .await?
        .error_for_status()?
        .json::<ClickHouseJsonEnvelope<T>>()
        .await?;
    Ok(response.data)
}

fn escape_sql_string(value: &str) -> String {
    value.replace('\'', "''")
}

fn deserialize_clickhouse_u64<'de, D>(deserializer: D) -> Result<u64, D::Error>
where
    D: Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    match value {
        serde_json::Value::Number(number) => number
            .as_u64()
            .ok_or_else(|| serde::de::Error::custom("expected unsigned integer")),
        serde_json::Value::String(text) => text
            .parse::<u64>()
            .map_err(|err| serde::de::Error::custom(format!("invalid UInt64 string: {err}"))),
        _ => Err(serde::de::Error::custom("expected UInt64 number or string")),
    }
}

async fn ensure_screener_feature_cache(state: &AppState) -> Result<(), String> {
    state
        .ch
        .query(CREATE_SCREENER_FEATURE_CACHE)
        .execute()
        .await
        .map_err(|err| format!("daily_screener_features table: {err}"))?;
    for alter in SCREENER_FEATURE_CACHE_ALTERS {
        state
            .ch
            .query(alter)
            .execute()
            .await
            .map_err(|err| format!("daily_screener_features migration: {err}"))?;
    }
    Ok(())
}

async fn latest_feature_cache_stats(state: &AppState) -> anyhow::Result<Option<FeatureCacheStatsRow>> {
    ensure_screener_feature_cache(state)
        .await
        .map_err(|err| anyhow::anyhow!(err))?;
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "WITH target AS ( \
            SELECT max(trade_date) AS data_date \
            FROM trading.daily_screener_features FINAL \
            WHERE trade_date <= toDate('{target_date}') \
        ) \
        SELECT \
            toString(trade_date) AS data_date, \
            toUInt64(count()) AS cached_rows, \
            toFloat64(avg(atr14)) AS avg_atr14 \
        FROM trading.daily_screener_features FINAL \
        WHERE trade_date = (SELECT data_date FROM target) \
        GROUP BY trade_date"
    );
    Ok(run_clickhouse_json_query::<FeatureCacheStatsRow>(state, query)
        .await?
        .into_iter()
        .next())
}

async fn refresh_screener_feature_cache(state: &AppState) -> anyhow::Result<()> {
    let parquet_source = parquet_feature_source_for_recent_months(24);
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "INSERT INTO trading.daily_screener_features \
        (trade_date, symbol, day_open, day_high, day_low, day_close, prev_close, day_volume, \
         sma20, sma50, sma200, avg_volume20, high_20d, high_52w, low_52w, rsi10, \
         atr14, atr_pct, range_pct, close_location, gap_pct, prior_high20, prior_high55, \
         prior_high252, prior_low20, rs60_rank, rs120_rank, market_breadth200, refreshed_at) \
        WITH daily AS ( \
            SELECT \
                symbol, \
                toDate(candle_date) AS trade_date, \
                argMin(open, bucket) AS day_open, \
                max(high) AS day_high, \
                min(low) AS day_low, \
                argMax(close, bucket) AS day_close, \
                toUInt64(sum(volume)) AS day_volume \
            FROM ({}) \
            WHERE toDate(candle_date) >= subtractYears(today(), 2) \
              AND symbol IN (SELECT symbol FROM trading.watchlist WHERE enabled = 1) \
              AND candle_date IS NOT NULL \
              AND open IS NOT NULL \
              AND high IS NOT NULL \
              AND low IS NOT NULL \
              AND close IS NOT NULL \
              AND volume IS NOT NULL \
            GROUP BY symbol, trade_date \
        ), target_date AS ( \
            SELECT max(trade_date) AS data_date \
            FROM daily \
            WHERE trade_date <= toDate('{target_date}') \
        ), with_prev AS ( \
            SELECT *, \
                lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_close \
            FROM daily \
        ), priced AS ( \
            SELECT *, \
                greatest(day_close - prev_close, 0) AS gain, \
                greatest(prev_close - day_close, 0) AS loss, \
                greatest(day_high - day_low, abs(day_high - prev_close), abs(day_low - prev_close)) AS true_range, \
                if(prev_close > 0, ((day_open - prev_close) / prev_close) * 100, 0) AS gap_pct, \
                if(day_close > 0, (day_high - day_low) / day_close, 0) AS range_pct, \
                if(day_high > day_low, (day_close - day_low) / (day_high - day_low), 0.5) AS close_location \
            FROM with_prev \
        ), ranked AS ( \
            SELECT \
                symbol, \
                trade_date, \
                day_open, \
                day_high, \
                day_low, \
                day_close, \
                prev_close, \
                day_volume, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200, \
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                avg(true_range) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14, \
                if(day_close > 0, avg(true_range) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) / day_close, 0) AS atr_pct, \
                range_pct, \
                close_location, \
                gap_pct, \
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), day_high) AS prior_high20, \
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 55 PRECEDING AND 1 PRECEDING), day_high) AS prior_high55, \
                coalesce(max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING), day_high) AS prior_high252, \
                coalesce(min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), day_low) AS prior_low20, \
                if(lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret60, \
                if(lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret120, \
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM priced \
        ), scored AS ( \
            SELECT *, \
                toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret60)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs60_rank, \
                toFloat64(rank() OVER (PARTITION BY trade_date ORDER BY ret120)) / greatest(toFloat64(count() OVER (PARTITION BY trade_date)), 1.0) AS rs120_rank, \
                avg(if(day_close > sma200, 1.0, 0.0)) OVER (PARTITION BY trade_date) AS market_breadth200 \
            FROM ranked \
        ) \
        SELECT \
            trade_date, \
            symbol, \
            toFloat64(day_open), \
            toFloat64(day_high), \
            toFloat64(day_low), \
            toFloat64(day_close), \
            toFloat64(prev_close), \
            day_volume, \
            toFloat64(sma20), \
            toFloat64(sma50), \
            toFloat64(sma200), \
            toFloat64(avg_volume20), \
            toFloat64(high_20d), \
            toFloat64(high_52w), \
            toFloat64(low_52w), \
            toFloat64(rsi10), \
            toFloat64(atr14), \
            toFloat64(atr_pct), \
            toFloat64(range_pct), \
            toFloat64(close_location), \
            toFloat64(gap_pct), \
            toFloat64(prior_high20), \
            toFloat64(prior_high55), \
            toFloat64(prior_high252), \
            toFloat64(prior_low20), \
            toFloat64(rs60_rank), \
            toFloat64(rs120_rank), \
            toFloat64(market_breadth200), \
            now() \
        FROM scored \
        WHERE rn = 1 \
          AND trade_date = (SELECT data_date FROM target_date)",
        parquet_source
    );
    state.ch.query(&query).execute().await?;
    Ok(())
}

async fn load_cached_historical_fallbacks(
    state: &AppState,
    symbols: &[String],
) -> anyhow::Result<HashMap<String, HistoricalFallbackRow>> {
    ensure_screener_feature_cache(state)
        .await
        .map_err(|err| anyhow::anyhow!(err))?;
    let symbol_list = symbols
        .iter()
        .map(|symbol| format!("'{}'", escape_sql_string(symbol)))
        .collect::<Vec<_>>()
        .join(",");
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "WITH target AS ( \
            SELECT max(trade_date) AS data_date \
            FROM trading.daily_screener_features FINAL \
            WHERE trade_date <= toDate('{target_date}') \
        ) \
        SELECT \
            f.symbol, \
            toString(f.trade_date) AS trade_date, \
            toFloat64(f.day_open) AS day_open, \
            toFloat64(f.day_high) AS day_high, \
            toFloat64(f.day_low) AS day_low, \
            toFloat64(f.day_close) AS day_close, \
            toFloat64(f.day_volume) AS day_volume, \
            toFloat64(f.prev_close) AS prev_close \
        FROM trading.daily_screener_features AS f FINAL \
        WHERE f.trade_date = (SELECT data_date FROM target) \
          AND f.symbol IN ({symbol_list})"
    );
    let rows = run_clickhouse_json_query::<HistoricalFallbackRow>(state, query).await?;
    Ok(rows.into_iter().map(|row| (row.symbol.clone(), row)).collect())
}

async fn load_cached_live_signal_baselines(
    state: &AppState,
    symbols: &[String],
) -> anyhow::Result<HashMap<String, HistoricalScreenerFeatureRow>> {
    ensure_screener_feature_cache(state)
        .await
        .map_err(|err| anyhow::anyhow!(err))?;
    let symbol_list = symbols
        .iter()
        .map(|symbol| format!("'{}'", escape_sql_string(symbol)))
        .collect::<Vec<_>>()
        .join(",");
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "WITH target AS ( \
            SELECT max(trade_date) AS data_date \
            FROM trading.daily_screener_features FINAL \
            WHERE trade_date <= toDate('{target_date}') \
        ) \
        SELECT \
            f.symbol, \
            toString(f.trade_date) AS trade_date, \
            toFloat64(f.day_open) AS day_open, \
            toFloat64(f.day_close) AS day_close, \
            toFloat64(f.day_high) AS day_high, \
            toFloat64(f.day_low) AS day_low, \
            toString(f.day_volume) AS day_volume, \
            toFloat64(f.sma20) AS sma20, \
            toFloat64(f.sma50) AS sma50, \
            toFloat64(f.sma200) AS sma200, \
            toFloat64(f.avg_volume20) AS avg_volume20, \
            toFloat64(f.high_20d) AS high_20d, \
            toFloat64(f.high_52w) AS high_52w, \
            toFloat64(f.low_52w) AS low_52w, \
            toFloat64(f.rsi10) AS rsi10 \
        FROM trading.daily_screener_features AS f FINAL \
        WHERE f.trade_date = (SELECT data_date FROM target) \
          AND f.symbol IN ({symbol_list})"
    );
    let rows = run_clickhouse_json_query::<HistoricalScreenerFeatureRow>(state, query).await?;
    Ok(rows
        .into_iter()
        .filter_map(|row| row.symbol.clone().map(|symbol| (symbol, row)))
        .collect())
}

async fn load_historical_candles(
    state: &AppState,
    symbol: &str,
    range: &str,
) -> anyhow::Result<Vec<HistoricalCandle>> {
    let month_span = history_month_span(range);
    let parquet_source = if month_span > 24 {
        parquet_source_for_recent_years(((month_span as f32) / 12.0).ceil() as usize)
    } else {
        parquet_source_for_recent_months(month_span)
    };
    let query = format!(
        "SELECT \
            toString(date) AS trade_date, \
            toFloat64(argMin(open, bucket)) AS open, \
            toFloat64(max(high)) AS high, \
            toFloat64(min(low)) AS low, \
            toFloat64(argMax(close, bucket)) AS close, \
            toUInt64(sum(volume)) AS volume \
         FROM ({}) \
         WHERE upper(symbol) = upper(?) AND toDate(date) >= {} \
         GROUP BY date \
         ORDER BY date",
        parquet_source,
        history_where_clause(range)
    );

    let rows = run_clickhouse_json_query::<HistoricalDailyRow>(
        state,
        query.replace('?', &format!("'{}'", escape_sql_string(symbol))),
    )
    .await?;

    Ok(rows
        .into_iter()
        .filter_map(|row| {
            Some(HistoricalCandle {
                date: row.trade_date?,
                open: round2_f64(row.open?),
                high: round2_f64(row.high?),
                low: round2_f64(row.low?),
                close: round2_f64(row.close?),
                volume: row.volume?.parse().ok()?,
            })
        })
        .collect())
}

fn compute_historical_summary(candles: &[HistoricalCandle]) -> Option<HistoricalSummary> {
    let latest = candles.last()?;
    let last_20 = candles.iter().rev().take(20).collect::<Vec<_>>();
    let last_252 = candles.iter().rev().take(252).collect::<Vec<_>>();

    let avg_volume_20d = if last_20.is_empty() {
        0.0
    } else {
        last_20.iter().map(|c| c.volume as f64).sum::<f64>() / last_20.len() as f64
    };

    let high_52w = last_252
        .iter()
        .map(|c| c.high)
        .fold(f64::MIN, f64::max);
    let low_52w = last_252
        .iter()
        .map(|c| c.low)
        .fold(f64::MAX, f64::min);

    Some(HistoricalSummary {
        latest_close: round2_f64(latest.close),
        change_pct_1m: pct_change_from_offset(candles, 21),
        change_pct_3m: pct_change_from_offset(candles, 63),
        change_pct_1y: pct_change_from_offset(candles, 252),
        high_52w: round2_f64(if high_52w.is_finite() { high_52w } else { latest.high }),
        low_52w: round2_f64(if low_52w.is_finite() { low_52w } else { latest.low }),
        avg_volume_20d: round2_f64(avg_volume_20d),
    })
}

fn pct_change_from_offset(candles: &[HistoricalCandle], offset: usize) -> f64 {
    let Some(latest) = candles.last() else {
        return 0.0;
    };
    if candles.len() <= offset {
        return 0.0;
    }
    let base = candles[candles.len() - 1 - offset].close;
    if base <= 0.0 {
        return 0.0;
    }
    round2_f64(((latest.close - base) / base) * 100.0)
}

async fn load_latest_strategy_statuses(state: &AppState) -> anyhow::Result<HashMap<String, String>> {
    let rows = state
        .ch
        .query(
            "WITH latest AS ( \
                SELECT run_id \
                FROM trading.backtest_trades \
                GROUP BY run_id \
                ORDER BY run_id DESC \
                LIMIT 1 \
            ), totals AS ( \
                SELECT strategy_id, sum(pnl) AS total_pnl \
                FROM trading.backtest_trades \
                WHERE run_id = (SELECT run_id FROM latest) \
                GROUP BY strategy_id \
            ) \
            SELECT \
                strategy_id, \
                multiIf( \
                    total_pnl <= 0, 'Rejected', \
                    strategy_id = 'momentum-core-v1', 'Research', \
                    strategy_id = 'rsi10-pullback-reversion-v1', 'Research', \
                    strategy_id = 'failed-breakdown-reclaim-v1', 'Research', \
                    strategy_id = 'compression-breakout-v1', 'Watch', \
                    strategy_id = 'breakout-continuation-v1', 'Watch', \
                    strategy_id = 'rs-leader-continuation-v1', 'Watch', \
                    strategy_id = 'near-52w-high-runner-v2', 'Watch', \
                    'Fragile' \
                ) AS status \
            FROM totals",
        )
        .fetch_all::<StrategyStatusRow>()
        .await?;

    Ok(rows
        .into_iter()
        .map(|row| (row.strategy_id, row.status))
        .collect())
}

async fn load_historical_screener_rows(
    state: &AppState,
    min_price: f64,
    min_avg_volume: f64,
) -> anyhow::Result<Vec<HistoricalScreenerFeatureRow>> {
    ensure_screener_feature_cache(state)
        .await
        .map_err(|err| anyhow::anyhow!(err))?;
    let cache_stats = latest_feature_cache_stats(state).await?;
    let cache_usable = cache_stats
        .as_ref()
        .is_some_and(|row| row.cached_rows > 0 && row.avg_atr14 > 0.0);
    if !cache_usable {
        refresh_screener_feature_cache(state).await?;
    }
    let cached = load_cached_historical_screener_rows(state, min_price, min_avg_volume).await?;
    if cache_usable || !cached.is_empty() {
        return Ok(cached);
    }

    let parquet_source = parquet_source_for_recent_months(24);
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "WITH daily AS ( \
            SELECT \
                symbol, \
                toDate(date) AS trade_date, \
                argMax(close, bucket) AS day_close, \
                max(high) AS day_high, \
                min(low) AS day_low, \
                toUInt64(sum(volume)) AS day_volume \
            FROM ({}) \
            WHERE toDate(date) >= subtractYears(today(), 2) \
              AND symbol IN (SELECT symbol FROM trading.watchlist WHERE enabled = 1) \
            GROUP BY symbol, trade_date \
        ), target_date AS ( \
            SELECT max(trade_date) AS data_date \
            FROM daily \
            WHERE trade_date <= toDate('{target_date}') \
        ), ranked AS ( \
            SELECT \
                symbol, \
                trade_date, \
                day_close, \
                day_high, \
                day_low, \
                day_volume, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50, \
                avg(day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200, \
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM ( \
                SELECT *, \
                    greatest(day_close - lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS gain, \
                    greatest(lagInFrame(day_close, 1, day_close) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - day_close, 0) AS loss \
                FROM daily \
            ) \
        ) \
        SELECT \
            symbol, \
            toString(ranked.trade_date) AS trade_date, \
            toFloat64(day_close) AS day_close, \
            toFloat64(day_high) AS day_high, \
            toFloat64(day_low) AS day_low, \
            day_volume, \
            toFloat64(sma20) AS sma20, \
            toFloat64(sma50) AS sma50, \
            toFloat64(sma200) AS sma200, \
            toFloat64(avg_volume20) AS avg_volume20, \
            toFloat64(high_20d) AS high_20d, \
            toFloat64(high_52w) AS high_52w, \
            toFloat64(low_52w) AS low_52w, \
            toFloat64(rsi10) AS rsi10 \
        FROM ranked \
        WHERE rn = 1 \
          AND ranked.trade_date = (SELECT data_date FROM target_date) \
          AND day_close >= {min_price} \
          AND avg_volume20 >= {min_avg_volume} \
        ORDER BY avg_volume20 DESC \
        LIMIT 1200",
        parquet_source
    );

    run_clickhouse_json_query::<HistoricalScreenerFeatureRow>(state, query).await
}

async fn load_cached_historical_screener_rows(
    state: &AppState,
    min_price: f64,
    min_avg_volume: f64,
) -> anyhow::Result<Vec<HistoricalScreenerFeatureRow>> {
    let target_date = last_completed_trading_day().format("%Y-%m-%d").to_string();
    let query = format!(
        "WITH target AS ( \
            SELECT max(trade_date) AS data_date \
            FROM trading.daily_screener_features FINAL \
            WHERE trade_date <= toDate('{target_date}') \
        ) \
        SELECT \
            f.symbol, \
            toString(f.trade_date) AS trade_date, \
            toFloat64(f.day_open) AS day_open, \
            toFloat64(f.day_close) AS day_close, \
            toFloat64(f.day_high) AS day_high, \
            toFloat64(f.day_low) AS day_low, \
            toString(f.day_volume) AS day_volume, \
            toFloat64(f.sma20) AS sma20, \
            toFloat64(f.sma50) AS sma50, \
            toFloat64(f.sma200) AS sma200, \
            toFloat64(f.avg_volume20) AS avg_volume20, \
            toFloat64(f.high_20d) AS high_20d, \
            toFloat64(f.high_52w) AS high_52w, \
            toFloat64(f.low_52w) AS low_52w, \
            toFloat64(f.rsi10) AS rsi10, \
            toFloat64(f.atr14) AS atr14, \
            toFloat64(f.atr_pct) AS atr_pct, \
            toFloat64(f.range_pct) AS range_pct, \
            toFloat64(f.close_location) AS close_location, \
            toFloat64(f.gap_pct) AS gap_pct, \
            toFloat64(f.prior_high20) AS prior_high20, \
            toFloat64(f.prior_high55) AS prior_high55, \
            toFloat64(f.prior_high252) AS prior_high252, \
            toFloat64(f.prior_low20) AS prior_low20, \
            toFloat64(f.rs60_rank) AS rs60_rank, \
            toFloat64(f.rs120_rank) AS rs120_rank, \
            toFloat64(f.market_breadth200) AS market_breadth200 \
        FROM trading.daily_screener_features AS f FINAL \
        WHERE f.trade_date = (SELECT data_date FROM target) \
          AND f.day_close >= {min_price} \
          AND f.avg_volume20 >= {min_avg_volume} \
        ORDER BY f.avg_volume20 DESC \
        LIMIT 1200"
    );
    run_clickhouse_json_query::<HistoricalScreenerFeatureRow>(state, query).await
}

fn map_historical_screener_row(
    row: HistoricalScreenerFeatureRow,
    strategy_statuses: &HashMap<String, String>,
) -> Option<HistoricalScreenerRow> {
    let symbol = row.symbol?;
    let trade_date = row.trade_date?;
    let day_close = row.day_close?;
    let _day_high = row.day_high?;
    let _day_low = row.day_low?;
    let day_volume: u64 = row.day_volume?.parse().ok()?;
    let sma20 = row.sma20?;
    let sma50 = row.sma50?;
    let sma200 = row.sma200.unwrap_or(sma50);
    let avg_volume20 = row.avg_volume20?;
    let high_20d = row.high_20d?;
    let high_52w = row.high_52w?;
    let low_52w = row.low_52w?;
    let day_high = row.day_high?;
    let day_low = row.day_low?;

    if day_close <= 0.0 || high_20d <= 0.0 || high_52w <= 0.0 || low_52w <= 0.0 {
        return None;
    }

    let atr14 = row.atr14.unwrap_or_else(|| (day_high - day_low).abs()).max(0.0);
    let atr_pct = row
        .atr_pct
        .filter(|value| *value > 0.0)
        .unwrap_or_else(|| if day_close > 0.0 { atr14 / day_close } else { 0.0 });
    let range_pct = row
        .range_pct
        .filter(|value| *value > 0.0)
        .unwrap_or_else(|| if day_close > 0.0 { (day_high - day_low).max(0.0) / day_close } else { 0.0 });
    let close_location = row
        .close_location
        .unwrap_or_else(|| if day_high > day_low { (day_close - day_low) / (day_high - day_low) } else { 0.5 })
        .clamp(0.0, 1.0);
    let gap_pct = row.gap_pct.unwrap_or_else(|| {
        row.day_open
            .filter(|open| *open > 0.0)
            .map(|open| ((open - day_close) / day_close) * 100.0)
            .unwrap_or(0.0)
    });
    let prior_high20 = row.prior_high20.filter(|value| *value > 0.0).unwrap_or(high_20d);
    let prior_high55 = row.prior_high55.filter(|value| *value > 0.0).unwrap_or(prior_high20);
    let prior_high252 = row.prior_high252.filter(|value| *value > 0.0).unwrap_or(high_52w);
    let prior_low20 = row.prior_low20.filter(|value| *value > 0.0).unwrap_or(day_low);
    let rs60_rank = row.rs60_rank.unwrap_or(0.5).clamp(0.0, 1.0);
    let rs120_rank = row.rs120_rank.unwrap_or(0.5).clamp(0.0, 1.0);
    let market_breadth200 = row.market_breadth200.unwrap_or(0.5).clamp(0.0, 1.0);
    let breakout_pct = ((prior_high20 - day_close) / prior_high20) * 100.0;
    let distance_to_52w_high_pct = ((high_52w - day_close) / high_52w) * 100.0;
    let range_span = (high_52w - low_52w).max(0.01);
    let range_position_pct = ((day_close - low_52w) / range_span) * 100.0;
    let volume_ratio = day_volume as f64 / avg_volume20.max(1.0);
    let trend_up = day_close > sma20 && sma20 > sma50;
    let pullback_zone = day_close >= sma20 * 0.98 && day_close <= sma20 * 1.03;
    let rsi10 = row.rsi10.unwrap_or(50.0);
    let rsi10_pullback = day_close > sma200 && rsi10 < 30.0;
    let breakout_close = day_close > prior_high20 && close_location >= 0.6;
    let compression_breakout = breakout_close
        && volume_ratio >= 1.05
        && atr_pct < 0.08
        && range_pct <= (atr_pct * 1.05).max(0.015);
    let failed_breakdown_reclaim = day_low < prior_low20
        && day_close > prior_low20
        && close_location >= 0.65
        && volume_ratio >= 0.8;
    let relative_strength_leader = rs60_rank >= 0.75
        && rs120_rank >= 0.65
        && day_close > sma50
        && (distance_to_52w_high_pct <= 10.0 || day_close > prior_high55 || day_close > prior_high252);

    let setup_family = if rsi10_pullback {
        "RSI10 Pullback Reversion"
    } else if failed_breakdown_reclaim {
        "Failed Breakdown Reclaim"
    } else if compression_breakout {
        "Compression Breakout"
    } else if trend_up && breakout_close && volume_ratio >= 1.1 {
        "Breakout Continuation"
    } else if relative_strength_leader {
        "Relative Strength Leader"
    } else if trend_up && pullback_zone {
        "Pullback To 20 DMA"
    } else if day_close > sma50 && distance_to_52w_high_pct <= 8.0 {
        "Near 52W High"
    } else {
        "Trend Filter"
    };

    let trend_label = if trend_up {
        "Uptrend"
    } else if day_close > sma50 {
        "Constructive"
    } else {
        "Needs Work"
    };

    let mut score: f64 = 50.0;
    if trend_up {
        score += 18.0;
    }
    if breakout_close {
        score += 14.0;
    } else if breakout_pct <= 1.5 {
        score += 10.0;
    } else if breakout_pct <= 4.0 {
        score += 8.0;
    }
    if distance_to_52w_high_pct <= 8.0 {
        score += 10.0;
    }
    if volume_ratio >= 1.2 {
        score += 10.0;
    } else if volume_ratio >= 1.0 {
        score += 5.0;
    }
    if pullback_zone {
        score += 8.0;
    }
    if rsi10_pullback {
        score += 16.0;
    }
    if failed_breakdown_reclaim {
        score += 14.0;
    }
    if compression_breakout {
        score += 12.0;
    }
    if rs60_rank >= 0.75 {
        score += 8.0;
    } else if rs60_rank >= 0.60 {
        score += 4.0;
    }
    if close_location >= 0.75 {
        score += 6.0;
    }
    if market_breadth200 >= 0.45 {
        score += 4.0;
    }
    if range_position_pct >= 70.0 {
        score += 6.0;
    }
    let score = score.round().clamp(50.0, 96.0) as u8;
    let (strategy_id, strategy_label) = strategy_match_for_screener(
        setup_family,
        score,
        trend_up,
        pullback_zone,
        breakout_pct,
        distance_to_52w_high_pct,
        range_position_pct,
        volume_ratio,
        day_close,
        sma20,
        sma200,
        rsi10,
    );
    let strategy_status = strategy_statuses
        .get(strategy_id)
        .cloned()
        .unwrap_or_else(|| default_strategy_status(strategy_id).to_string());
    let (planned_entry, stop_loss, target_price, risk_reward) = historical_trade_plan(
        setup_family,
        day_close,
        day_low,
        sma20,
        atr14,
        prior_high20,
        prior_low20,
    );

    Some(HistoricalScreenerRow {
        symbol,
        as_of: trade_date,
        setup_family: setup_family.to_string(),
        strategy_id: strategy_id.to_string(),
        strategy_label: strategy_label.to_string(),
        strategy_status,
        score,
        trend_label: trend_label.to_string(),
        close: round2_f64(day_close),
        sma20: round2_f64(sma20),
        sma50: round2_f64(sma50),
        avg_volume20: round2_f64(avg_volume20),
        volume_ratio: round2_f64(volume_ratio),
        distance_to_20d_high_pct: round2_f64(breakout_pct.max(0.0)),
        distance_to_52w_high_pct: round2_f64(distance_to_52w_high_pct.max(0.0)),
        range_position_pct: round2_f64(range_position_pct.clamp(0.0, 100.0)),
        atr14: round2_f64(atr14),
        atr_pct: round2_f64(atr_pct * 100.0),
        close_location: round2_f64(close_location * 100.0),
        gap_pct: round2_f64(gap_pct),
        rs60_rank: round2_f64(rs60_rank * 100.0),
        rs120_rank: round2_f64(rs120_rank * 100.0),
        market_breadth200: round2_f64(market_breadth200 * 100.0),
        planned_entry,
        stop_loss,
        target_price,
        risk_reward,
    })
}

fn historical_trade_plan(
    setup_family: &str,
    close: f64,
    low: f64,
    sma20: f64,
    atr14: f64,
    prior_high20: f64,
    prior_low20: f64,
) -> (String, f64, f64, f64) {
    let atr = atr14.max(close * 0.015).max(0.01);
    let raw_stop = match setup_family {
        "Breakout Continuation" | "Compression Breakout" => (prior_high20 - 0.35 * atr).min(close - 1.2 * atr),
        "Pullback To 20 DMA" => (sma20 - 0.8 * atr).min(low - 0.25 * atr),
        "Failed Breakdown Reclaim" => prior_low20.min(low) - 0.25 * atr,
        "RSI10 Pullback Reversion" => close - 1.4 * atr,
        "Relative Strength Leader" | "Near 52W High" => close - 1.6 * atr,
        _ => close - 1.5 * atr,
    };
    let stop_loss = round2_f64(raw_stop.max(close * 0.88).min(close - 0.01));
    let risk = (close - stop_loss).max(0.01);
    let reward_multiple = match setup_family {
        "RSI10 Pullback Reversion" => 1.4,
        "Pullback To 20 DMA" | "Failed Breakdown Reclaim" => 1.8,
        _ => 2.0,
    };
    let target_price = round2_f64(close + risk * reward_multiple);
    let risk_reward = round2_f64((target_price - close) / risk);
    let planned_entry = match setup_family {
        "Breakout Continuation" | "Compression Breakout" => {
            format!("Next session strength above Rs {:.2}", prior_high20.max(close))
        }
        "Pullback To 20 DMA" => format!("Buy zone near 20 DMA Rs {:.2} to close Rs {:.2}", sma20, close),
        "Failed Breakdown Reclaim" => format!("Reclaim hold above Rs {:.2}", prior_low20),
        _ => format!("Next session confirmation near Rs {:.2}", close),
    };
    (planned_entry, stop_loss, target_price, risk_reward)
}

#[allow(clippy::too_many_arguments)]
fn strategy_match_for_screener(
    setup_family: &str,
    score: u8,
    trend_up: bool,
    pullback_zone: bool,
    breakout_pct: f64,
    distance_to_52w_high_pct: f64,
    range_position_pct: f64,
    volume_ratio: f64,
    day_close: f64,
    sma20: f64,
    sma200: f64,
    rsi10: f64,
) -> (&'static str, &'static str) {
    if day_close > sma200 && rsi10 < 30.0 {
        return ("rsi10-pullback-reversion-v1", "RSI10 Pullback");
    }
    if setup_family == "Failed Breakdown Reclaim" && score >= 86 {
        return ("failed-breakdown-reclaim-v1", "Failed Breakdown Reclaim");
    }
    if setup_family == "Compression Breakout" && score >= 88 {
        return ("compression-breakout-v1", "Compression Breakout");
    }
    if setup_family == "Breakout Continuation" && score >= 88 && volume_ratio >= 1.1 && trend_up {
        return ("breakout-continuation-v1", "Breakout Continuation");
    }
    if setup_family == "Relative Strength Leader" && score >= 86 {
        return ("rs-leader-continuation-v1", "RS Leader Continuation");
    }
    if setup_family == "Pullback To 20 DMA" && score >= 88 && trend_up && pullback_zone && volume_ratio >= 0.8 && day_close >= sma20 {
        return ("pullback-quality-v2", "Pullback Quality");
    }
    if setup_family == "Pullback To 20 DMA" {
        return ("pullback-20dma-v1", "Pullback 20DMA");
    }
    if distance_to_52w_high_pct <= 3.0 && range_position_pct >= 85.0 && trend_up && score >= 92 {
        return ("momentum-core-v1", "Momentum Core");
    }
    if distance_to_52w_high_pct <= 3.0 && trend_up && volume_ratio >= 0.8 && score >= 90 {
        return ("near-52w-high-runner-v2", "52W Runner");
    }
    if distance_to_52w_high_pct <= 6.0 && volume_ratio >= 1.15 && range_position_pct >= 75.0 && score >= 88 {
        return ("near-52w-high-volume-v3", "52W Volume");
    }
    if distance_to_52w_high_pct <= 4.0 && range_position_pct >= 75.0 && score >= 88 {
        return ("near-52w-high-tight-v2", "52W Tight");
    }
    if setup_family == "Near 52W High" && score >= 80 {
        return ("near-52w-high-v1", "Near 52W High");
    }
    if setup_family == "Breakout Setup" && score >= 90 && volume_ratio >= 1.5 && breakout_pct <= 1.0 && trend_up {
        return ("breakout-volume-v2", "Breakout Volume");
    }
    if setup_family == "Breakout Setup" || setup_family == "Breakout Continuation" {
        return ("swing-breakout-v1", "Swing Breakout");
    }
    ("unlinked-screener", "Unlinked Screen")
}

fn default_strategy_status(strategy_id: &str) -> &'static str {
    match strategy_id {
        "momentum-core-v1" => "Research",
        "rsi10-pullback-reversion-v1" => "Research",
        "failed-breakdown-reclaim-v1" => "Research",
        "compression-breakout-v1" => "Watch",
        "breakout-continuation-v1" => "Watch",
        "rs-leader-continuation-v1" => "Watch",
        "near-52w-high-runner-v2" => "Watch",
        "near-52w-high-v1" | "near-52w-high-tight-v2" | "near-52w-high-volume-v3" => "Fragile",
        "pullback-20dma-v1" | "pullback-quality-v2" | "swing-breakout-v1" | "breakout-volume-v2" => "Rejected",
        _ => "Unlinked",
    }
}

fn strategy_status_rank(status: &str) -> u8 {
    match status {
        "Research" => 0,
        "Watch" => 1,
        "Fragile" => 2,
        "Rejected" => 3,
        _ => 4,
    }
}

fn matches_setup_filter(row: &HistoricalScreenerRow, setup_filter: &str) -> bool {
    match setup_filter {
        "all" => true,
        "breakout" => matches!(row.setup_family.as_str(), "Breakout Setup" | "Breakout Continuation" | "Compression Breakout"),
        "pullback" => row.setup_family == "Pullback To 20 DMA",
        "compression" => row.setup_family == "Compression Breakout",
        "reclaim" | "failed-breakdown" => row.setup_family == "Failed Breakdown Reclaim",
        "rs" | "relative-strength" => row.setup_family == "Relative Strength Leader",
        "rsi10" | "reversion" => row.setup_family == "RSI10 Pullback Reversion",
        "52wh" | "near-high" => row.setup_family == "Near 52W High",
        "trend" => row.setup_family == "Trend Filter",
        _ => true,
    }
}

fn matches_strategy_filter(row: &HistoricalScreenerRow, strategy_filter: &str) -> bool {
    match strategy_filter {
        "all" => true,
        "fresh" | "signals" => matches!(row.strategy_status.as_str(), "Research" | "Watch"),
        value => {
            row.strategy_id.eq_ignore_ascii_case(value)
                || row.strategy_label.to_lowercase().replace(' ', "-") == value
                || row.strategy_status.to_lowercase() == value
        }
    }
}

async fn ensure_signal_ledger(state: &AppState) -> Result<(), String> {
    state
        .ch
        .query(CREATE_SIGNAL_LEDGER)
        .execute()
        .await
        .map_err(|err| format!("signal_ledger table: {err}"))
}

async fn load_signal_ledger_keys(state: &AppState) -> Result<HashSet<String>, String> {
    ensure_signal_ledger(state).await?;
    let rows = state
        .ch
        .query("SELECT signal_key FROM trading.signal_ledger FINAL")
        .fetch_all::<SignalLedgerKeyRow>()
        .await
        .map_err(|err| format!("signal ledger keys: {err}"))?;
    Ok(rows.into_iter().map(|row| row.signal_key).collect())
}

async fn load_active_paper_symbols(state: &AppState) -> Result<HashSet<String>, String> {
    paper::ensure_table(state).await?;
    let rows = state
        .ch
        .query("SELECT symbol FROM trading.paper_trades FINAL WHERE enabled = 1")
        .fetch_all::<SymbolOnlyRow>()
        .await
        .map_err(|err| format!("active paper symbols: {err}"))?;
    Ok(rows.into_iter().map(|row| row.symbol).collect())
}

fn build_signal_ledger_row(
    row: &HistoricalScreenerRow,
    paper_status: &str,
) -> Result<SignalLedgerInsertRow, String> {
    let Some(rule) = paper_rule_for_strategy(&row.strategy_id) else {
        return Err(format!("no paper rule for {}", row.strategy_id));
    };
    let entry_price = row.close.max(0.01);
    let quantity = quantity_for_capital(entry_price, PAPER_CAPITAL_PER_SIGNAL);
    Ok(SignalLedgerInsertRow {
        signal_key: signal_key_for(row),
        symbol: row.symbol.clone(),
        strategy_id: row.strategy_id.clone(),
        strategy_label: row.strategy_label.clone(),
        strategy_status: row.strategy_status.clone(),
        setup_family: row.setup_family.clone(),
        signal_date: row.as_of.clone(),
        entry_price,
        quantity,
        stop_loss: round2_f64(entry_price * (1.0 - rule.stop_loss_pct / 100.0)),
        target_price: round2_f64(entry_price * (1.0 + rule.take_profit_pct / 100.0)),
        score: row.score,
        source: "historical-screener".to_string(),
        status: "active".to_string(),
        paper_status: paper_status.to_string(),
        close_reason: String::new(),
        realized_pnl: 0.0,
    })
}

async fn insert_signal_ledger_rows(
    state: &AppState,
    rows: &[SignalLedgerInsertRow],
) -> Result<(), String> {
    if rows.is_empty() {
        return Ok(());
    }
    let mut insert = state
        .ch
        .insert("trading.signal_ledger")
        .map_err(|err| format!("signal ledger insert: {err}"))?;
    for row in rows {
        insert
            .write(row)
            .await
            .map_err(|err| format!("signal ledger write: {err}"))?;
    }
    insert
        .end()
        .await
        .map_err(|err| format!("signal ledger commit: {err}"))?;

    Ok(())
}

async fn stage_signal_to_paper(
    state: &AppState,
    row: &HistoricalScreenerRow,
) -> Result<(), String> {
    let Some(rule) = paper_rule_for_strategy(&row.strategy_id) else {
        return Err(format!("no paper rule for {}", row.strategy_id));
    };
    let entry_price = row.close.max(0.01);
    let quantity = quantity_for_capital(entry_price, PAPER_CAPITAL_PER_SIGNAL);
    let stop_loss = round2_f64(entry_price * (1.0 - rule.stop_loss_pct / 100.0));
    let target_price = round2_f64(entry_price * (1.0 + rule.take_profit_pct / 100.0));
    let trade = paper::PaperTradeRow {
        symbol: row.symbol.clone(),
        company_name: row.symbol.clone(),
        setup_family: row.strategy_label.clone(),
        bias: "Long".to_string(),
        entry_price,
        quantity,
        stop_loss,
        target_price,
        max_sessions: paper::DEFAULT_PAPER_MAX_SESSIONS,
        capital_allocated: entry_price * f64::from(quantity),
        expected_hold: format!("{} trading sessions", paper::DEFAULT_PAPER_MAX_SESSIONS),
        thesis: format!(
            "{} is a new unique {} signal from {} with score {}.",
            row.symbol, row.strategy_label, row.as_of, row.score
        ),
        notes: format!(
            "Auto-staged newest unique signal. signal_date={} strategy={} strategy_status={} signal_key={} rule={} stop_pct={:.2} target_pct={:.2}",
            row.as_of,
            row.strategy_id,
            row.strategy_status,
            signal_key_for(row),
            rule.source,
            rule.stop_loss_pct,
            rule.take_profit_pct
        ),
        exit_price: None,
        close_reason: String::new(),
        realized_pnl: 0.0,
        enabled: 1,
    };

    paper::upsert_system_trade(state, trade).await
}

fn is_paper_eligible_signal(row: &HistoricalScreenerRow) -> bool {
    matches!(row.strategy_status.as_str(), "Research" | "Watch")
        && paper_rule_for_strategy(&row.strategy_id).is_some()
        && row.close > 0.0
}

fn signal_key_for(row: &HistoricalScreenerRow) -> String {
    format!("{}|{}", row.symbol, row.strategy_id)
}

struct PaperRule {
    stop_loss_pct: f64,
    take_profit_pct: f64,
    source: &'static str,
}

fn paper_rule_for_strategy(strategy_id: &str) -> Option<PaperRule> {
    let rule = match strategy_id {
        "near-52w-high-v1"
        | "near-52w-high-runner-v2"
        | "near-52w-high-volume-v3"
        | "near-52w-high-tight-v2"
        | "momentum-core-v1" => PaperRule {
            stop_loss_pct: 5.0,
            take_profit_pct: 10.0,
            source: "near-52w-high backtest family",
        },
        "pullback-20dma-v1" | "pullback-quality-v2" => PaperRule {
            stop_loss_pct: 3.0,
            take_profit_pct: 6.0,
            source: "pullback-20dma backtest family",
        },
        "rsi10-pullback-reversion-v1" => PaperRule {
            stop_loss_pct: 4.0,
            take_profit_pct: 4.0,
            source: "built-in RSI10 pullback rule",
        },
        "failed-breakdown-reclaim-v1" => PaperRule {
            stop_loss_pct: 4.0,
            take_profit_pct: 7.0,
            source: "daily failed-breakdown reclaim model",
        },
        "compression-breakout-v1" | "breakout-continuation-v1" | "swing-breakout-v1" | "breakout-volume-v2" => PaperRule {
            stop_loss_pct: 4.0,
            take_profit_pct: 8.0,
            source: "swing-breakout backtest family",
        },
        "rs-leader-continuation-v1" => PaperRule {
            stop_loss_pct: 5.0,
            take_profit_pct: 10.0,
            source: "relative-strength continuation model",
        },
        _ => return None,
    };
    Some(rule)
}

fn quantity_for_capital(price: f64, capital: f64) -> u32 {
    ((capital / price.max(0.01)).floor() as u32).max(1)
}

fn read_bamboo_signal_csv(path: &str) -> anyhow::Result<Vec<BambooLatestSignal>> {
    let content = fs::read_to_string(path)?;
    let mut lines = content.lines();
    let Some(header_line) = lines.next() else {
        return Ok(Vec::new());
    };
    let headers = header_line.split(',').map(|value| value.trim().to_string()).collect::<Vec<_>>();
    let mut rows = Vec::new();

    for line in lines {
        if line.trim().is_empty() {
            continue;
        }
        let values = line.split(',').map(|value| value.trim()).collect::<Vec<_>>();
        let get = |name: &str| -> &str {
            headers
                .iter()
                .position(|header| header == name)
                .and_then(|index| values.get(index).copied())
                .unwrap_or("")
        };
        rows.push(BambooLatestSignal {
            strategy: get("strategy").to_string(),
            symbol: get("symbol").to_string(),
            signal_date: get("signal_date").to_string(),
            planned_entry: get("planned_entry").to_string(),
            close: parse_csv_f64(get("close")),
            stop: parse_csv_f64(get("stop")),
            target_from_close: parse_csv_f64(get("target_from_close")),
            risk_multiple: parse_csv_f64(get("risk_multiple")),
            risk_pct_vs_close: parse_csv_f64(get("risk_pct_vs_close")),
            relvol: parse_csv_f64(get("relvol")),
            range_position_52w: parse_csv_f64(get("range_position_52w")),
            ema20_dist_atr: parse_csv_f64(get("ema20_dist_atr")),
            prior_high20: parse_csv_f64(get("prior_high20")),
            prior_high55: parse_csv_f64(get("prior_high55")),
            gap_pct: parse_csv_f64(get("gap_pct")),
            close_loc: parse_csv_f64(get("close_loc")),
            rank_score: parse_csv_f64(get("rank_score")),
        });
    }

    Ok(rows)
}

fn parse_csv_f64(raw: &str) -> f64 {
    raw.parse::<f64>().unwrap_or(0.0)
}

fn is_nse_trading_day_now() -> bool {
    let now = now_ist();
    !is_nse_holiday(now.date_naive())
}

fn is_regular_session_now() -> bool {
    let now = now_ist();
    !is_nse_holiday(now.date_naive()) && compute_bucket(&now) > 0
}

fn round2(value: f32) -> f32 {
    (value * 100.0).round() / 100.0
}

fn round2_f64(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}
