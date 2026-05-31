use std::collections::{HashMap, HashSet};
use std::fs;

use axum::{
    extract::{ws::{Message, WebSocket, WebSocketUpgrade}, Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE, Engine as _};
use chrono::{Datelike, TimeZone, Timelike, Utc};
use clickhouse::Row;
use futures_util::{SinkExt, StreamExt};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Deserializer, Serialize};
use tokio::sync::RwLockReadGuard;
use tokio::time::{self, Duration, Instant};
use tokio_tungstenite::{connect_async, tungstenite::Message as DhanWsMessage};

use crate::api::{paper, AppState, LiveTriggerAlertMarker};
use crate::dhan::client::DhanClient;
use crate::dhan::market_data::{fetch_intraday_candles, fetch_quotes, IntradayResponse, QuoteItem};
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
    trigger_source: Option<String>,
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
    source: String,
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
    prev_close: Option<f64>,
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
    prior_close3: Option<f64>,
    prior_low20: Option<f64>,
    ret3: Option<f64>,
    range_atr: Option<f64>,
    recovery_from_low_pct: Option<f64>,
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
    prior_close3   Float64,
    prior_low20    Float64,
    ret3           Float64,
    range_atr      Float64,
    recovery_from_low_pct Float64,
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
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_close3 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS prior_low20 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS ret3 Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS range_atr Float64 DEFAULT 0",
    "ALTER TABLE trading.daily_screener_features ADD COLUMN IF NOT EXISTS recovery_from_low_pct Float64 DEFAULT 0",
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

#[derive(Serialize, Clone)]
pub struct LiveStrategyRow {
    security_id: String,
    symbol: String,
    company_name: String,
    strategy_id: String,
    strategy_label: String,
    strategy_status: String,
    setup_family: String,
    signal_status: String,
    signal_label: String,
    reason: String,
    score: u8,
    last_price: f32,
    day_change_pct: f32,
    open_gap_pct: f32,
    volume: u64,
    trigger_price: Option<f32>,
    trigger_source: Option<String>,
    stop_loss: f32,
    target_price: f32,
    risk_reward: f32,
    source: String,
    updated_at: String,
}

#[derive(Serialize, Clone)]
pub struct LiveStrategySnapshot {
    event: String,
    updated_at: String,
    mode: String,
    feed_status: String,
    broker: BrokerStatus,
    market_regime: MarketRegime,
    total_watching: usize,
    triggered: usize,
    rows: Vec<LiveStrategyRow>,
    message: Option<String>,
}

#[derive(Clone)]
struct WeeklyLabCandidate {
    symbol: String,
    strategy_id: String,
    strategy_label: String,
    setup_family: String,
    strategy_status: String,
    signal_date: String,
    trigger_price: f32,
    close: f32,
    supertrend: f32,
    rank_score: f32,
    relvol: f32,
    rs13w_rank: f32,
    body_ratio: f32,
    range_atr: f32,
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
    avg_ret3_abs: f64,
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

pub async fn live_strategy_ws(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| stream_live_strategies(socket, state))
}

async fn stream_live_strategies(mut socket: WebSocket, state: AppState) {
    let mut broker = resolve_broker_status(&state).await;
    let Some(credentials) = resolve_dhan_credentials(&state).await else {
        let snapshot = empty_live_snapshot(
            broker,
            "missing-credentials",
            "No Dhan credentials are configured, so live strategy streaming cannot start.",
        );
        let _ = send_live_snapshot(&mut socket, &snapshot).await;
        return;
    };

    if broker.state != "ready" {
        let msg = broker.message.clone();
        let snapshot = empty_live_snapshot(broker, "broker-not-ready", &msg);
        let _ = send_live_snapshot(&mut socket, &snapshot).await;
        return;
    }

    let watch_rows = load_watch_rows(&state, 1000, None).await;
    if watch_rows.is_empty() {
        let snapshot = empty_live_snapshot(
            broker,
            "empty-universe",
            "No enabled watchlist instruments were found for live strategy streaming.",
        );
        let _ = send_live_snapshot(&mut socket, &snapshot).await;
        return;
    }

    let volume_map = load_volume_groups_map();
    let weekly_lab_candidates = load_weekly_lab_candidates();
    let symbols = watch_rows.iter().map(|row| row.symbol.clone()).collect::<Vec<_>>();
    let baselines = load_live_signal_baselines(&state, &symbols).await.unwrap_or_default();
    let strategy_statuses = load_latest_strategy_statuses(&state).await.unwrap_or_default();
    let mut quote_map = initial_live_quotes(&state, &credentials, &watch_rows).await.unwrap_or_default();
    let snapshot = build_live_strategy_snapshot(
        broker.clone(),
        "dhan-websocket",
        "connecting",
        &watch_rows,
        &volume_map,
        &quote_map,
        &baselines,
        &strategy_statuses,
        &weekly_lab_candidates,
        Some("Connecting to Dhan live market feed...".to_string()),
    );
    if !publish_live_snapshot(&mut socket, &state, &snapshot).await {
        return;
    }

    let url = format!(
        "wss://api-feed.dhan.co?version=2&token={}&clientId={}&authType=2",
        credentials.access_token,
        credentials.client_id,
    );
    let Ok((mut dhan_ws, _)) = connect_async(&url).await else {
        stream_rest_strategy_snapshots(
            socket,
            state,
            credentials,
            broker,
            watch_rows,
            volume_map,
            quote_map,
            baselines,
            strategy_statuses,
            weekly_lab_candidates,
            "websocket-connect-failed",
            "Dhan websocket connection failed; keeping the live panel refreshed from REST quote snapshots.",
        ).await;
        return;
    };

    for chunk in watch_rows.chunks(100) {
        let message = serde_json::json!({
            "RequestCode": 17,
            "InstrumentCount": chunk.len(),
            "InstrumentList": chunk.iter().map(|row| serde_json::json!({
                "ExchangeSegment": "NSE_EQ",
                "SecurityId": row.security_id,
            })).collect::<Vec<_>>(),
        });
        if dhan_ws.send(DhanWsMessage::Text(message.to_string())).await.is_err() {
            let snapshot = build_live_strategy_snapshot(
                broker,
                "dhan-websocket",
                "subscribe-failed",
                &watch_rows,
                &volume_map,
                &quote_map,
                &baselines,
                &strategy_statuses,
                &weekly_lab_candidates,
                Some("Connected to Dhan websocket, but instrument subscription failed.".to_string()),
            );
            let _ = publish_live_snapshot(&mut socket, &state, &snapshot).await;
            return;
        }
    }

    let mut heartbeat = time::interval(Duration::from_secs(5));
    let mut last_sent = Instant::now() - Duration::from_secs(2);
    loop {
        tokio::select! {
            _ = heartbeat.tick() => {
                let snapshot = build_live_strategy_snapshot(
                    broker.clone(),
                    "dhan-websocket",
                    "streaming",
                    &watch_rows,
                    &volume_map,
                    &quote_map,
                    &baselines,
                    &strategy_statuses,
                    &weekly_lab_candidates,
                    None,
                );
                if !publish_live_snapshot(&mut socket, &state, &snapshot).await {
                    break;
                }
            }
            browser_msg = socket.recv() => {
                match browser_msg {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Ok(Message::Ping(payload))) => {
                        if socket.send(Message::Pong(payload)).await.is_err() {
                            break;
                        }
                    }
                    Some(Err(_)) => break,
                    _ => {}
                }
            }
            dhan_msg = dhan_ws.next() => {
                match dhan_msg {
                    Some(Ok(DhanWsMessage::Binary(bytes))) => {
                        for tick in parse_dhan_quote_packets(&bytes) {
                            let security_id = tick.security_id.clone();
                            quote_map.entry(security_id).and_modify(|quote| {
                                if tick.last_price > 0.0 { quote.last_price = tick.last_price; }
                                if tick.open > 0.0 { quote.ohlc.open = tick.open; }
                                if tick.high > 0.0 { quote.ohlc.high = tick.high; }
                                if tick.low > 0.0 { quote.ohlc.low = tick.low; }
                                if tick.prev_close > 0.0 { quote.ohlc.close = tick.prev_close; }
                                if tick.volume > 0 { quote.volume = tick.volume; }
                            }).or_insert_with(|| tick.into_quote_item());
                        }
                        if last_sent.elapsed() >= Duration::from_millis(900) {
                            last_sent = Instant::now();
                            let snapshot = build_live_strategy_snapshot(
                                broker.clone(),
                                "dhan-websocket",
                                "streaming",
                                &watch_rows,
                                &volume_map,
                                &quote_map,
                                &baselines,
                                &strategy_statuses,
                                &weekly_lab_candidates,
                                None,
                            );
                            if !publish_live_snapshot(&mut socket, &state, &snapshot).await {
                                break;
                            }
                        }
                    }
                    Some(Ok(DhanWsMessage::Ping(payload))) => {
                        let _ = dhan_ws.send(DhanWsMessage::Pong(payload)).await;
                    }
                    Some(Ok(DhanWsMessage::Close(_))) | None => {
                        broker.live_quotes = false;
                        let snapshot = build_live_strategy_snapshot(
                            broker,
                            "dhan-websocket",
                            "disconnected",
                            &watch_rows,
                            &volume_map,
                            &quote_map,
                            &baselines,
                            &strategy_statuses,
                            &weekly_lab_candidates,
                            Some("Dhan websocket disconnected. Reopen Strategies to reconnect.".to_string()),
                        );
                        let _ = publish_live_snapshot(&mut socket, &state, &snapshot).await;
                        break;
                    }
                    Some(Err(err)) => {
                        broker.live_quotes = false;
                        let snapshot = build_live_strategy_snapshot(
                            broker,
                            "dhan-websocket",
                            "feed-error",
                            &watch_rows,
                            &volume_map,
                            &quote_map,
                            &baselines,
                            &strategy_statuses,
                            &weekly_lab_candidates,
                            Some(format!("Dhan websocket error: {err}")),
                        );
                        let _ = publish_live_snapshot(&mut socket, &state, &snapshot).await;
                        break;
                    }
                    _ => {}
                }
            }
        }
    }
}

async fn send_live_snapshot(socket: &mut WebSocket, snapshot: &LiveStrategySnapshot) -> Result<(), axum::Error> {
    let payload = serde_json::to_string(snapshot).unwrap_or_else(|_| "{\"event\":\"error\"}".to_string());
    socket.send(Message::Text(payload)).await
}

async fn publish_live_snapshot(socket: &mut WebSocket, state: &AppState, snapshot: &LiveStrategySnapshot) -> bool {
    maybe_send_telegram_trigger_alerts(state, snapshot).await;
    send_live_snapshot(socket, snapshot).await.is_ok()
}

async fn maybe_send_telegram_trigger_alerts(state: &AppState, snapshot: &LiveStrategySnapshot) {
    let token = state.config.telegram_bot_token.trim();
    let chat_id = state.config.telegram_chat_id.trim();
    if token.is_empty() || chat_id.is_empty() {
        return;
    }

    let mut messages = Vec::new();
    {
        let mut alert_state = state.telegram_alert_state.lock().await;
        for row in snapshot.rows.iter().filter(|row| row.source == "dhan-live") {
            let key = format!("{}:{}", row.strategy_id, row.symbol);
            let trigger = match row.trigger_price {
                Some(value) if value > 0.0 => value,
                _ => {
                    alert_state.insert(key, LiveTriggerAlertMarker {
                        last_price: row.last_price,
                        notified_trigger: None,
                    });
                    continue;
                }
            };

            let marker = alert_state.entry(key).or_insert(LiveTriggerAlertMarker {
                last_price: row.last_price,
                notified_trigger: None,
            });
            let crossed_trigger = marker.last_price > 0.0
                && marker.last_price < trigger
                && row.last_price >= trigger;
            let already_notified = marker
                .notified_trigger
                .map(|notified| (notified - trigger).abs() < 0.005)
                .unwrap_or(false);

            if crossed_trigger && row.signal_status == "ENTRY_NOW" && !already_notified {
                marker.notified_trigger = Some(trigger);
                messages.push(format_telegram_trigger_message(row, trigger));
            }
            marker.last_price = row.last_price;
        }
    }

    for message in messages {
        if let Err(err) = send_telegram_message(token, chat_id, &message).await {
            tracing::warn!("telegram trigger alert failed: {err}");
        }
    }
}

fn format_telegram_trigger_message(row: &LiveStrategyRow, trigger: f32) -> String {
    let source = row
        .trigger_source
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or("strategy trigger");
    format!(
        "Swing Atlas trigger hit\n{} - {}\nLTP Rs {:.2} crossed trigger Rs {:.2} ({})\nStop Rs {:.2} | Target Rs {:.2} | Score {} | Volume {}",
        row.symbol,
        row.strategy_label,
        row.last_price,
        trigger,
        source,
        row.stop_loss,
        row.target_price,
        row.score,
        row.volume
    )
}

async fn send_telegram_message(token: &str, chat_id: &str, text: &str) -> anyhow::Result<()> {
    let url = format!("https://api.telegram.org/bot{token}/sendMessage");
    let response = reqwest::Client::new()
        .post(url)
        .json(&serde_json::json!({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": true,
        }))
        .send()
        .await?;
    if !response.status().is_success() {
        anyhow::bail!("telegram sendMessage returned {}", response.status());
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn stream_rest_strategy_snapshots(
    mut socket: WebSocket,
    state: AppState,
    credentials: ResolvedDhanCredentials,
    broker: BrokerStatus,
    watch_rows: Vec<WatchRow>,
    volume_map: HashMap<String, String>,
    mut quote_map: HashMap<String, QuoteItem>,
    baselines: HashMap<String, HistoricalScreenerFeatureRow>,
    strategy_statuses: HashMap<String, String>,
    weekly_lab_candidates: HashMap<String, WeeklyLabCandidate>,
    feed_status: &str,
    message: &str,
) {
    let mut interval = time::interval(Duration::from_secs(10));
    let mut first = true;
    loop {
        if !first {
            if let Ok(quotes) = initial_live_quotes(&state, &credentials, &watch_rows).await {
                quote_map = quotes;
            }
        }
        let snapshot = build_live_strategy_snapshot(
            broker.clone(),
            "rest-snapshot",
            feed_status,
            &watch_rows,
            &volume_map,
            &quote_map,
            &baselines,
            &strategy_statuses,
            &weekly_lab_candidates,
            Some(message.to_string()),
        );
        first = false;
        if !publish_live_snapshot(&mut socket, &state, &snapshot).await {
            break;
        }
        tokio::select! {
            _ = interval.tick() => {}
            msg = socket.recv() => {
                match msg {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Ok(Message::Ping(payload))) => {
                        if socket.send(Message::Pong(payload)).await.is_err() {
                            break;
                        }
                    }
                    Some(Err(_)) => break,
                    _ => {}
                }
            }
        }
    }
}

fn empty_live_snapshot(broker: BrokerStatus, status: &str, message: &str) -> LiveStrategySnapshot {
    LiveStrategySnapshot {
        event: "live-strategy-snapshot".to_string(),
        updated_at: now_ist().to_rfc3339(),
        mode: "dhan-websocket".to_string(),
        feed_status: status.to_string(),
        broker,
        market_regime: MarketRegime {
            label: "Live Feed Unavailable".to_string(),
            tone: "neutral".to_string(),
            summary: message.to_string(),
            advances: 0,
            declines: 0,
            breadth_ratio: 0.0,
        },
        total_watching: 0,
        triggered: 0,
        rows: Vec::new(),
        message: Some(message.to_string()),
    }
}

async fn initial_live_quotes(
    state: &AppState,
    credentials: &ResolvedDhanCredentials,
    watch_rows: &[WatchRow],
) -> anyhow::Result<HashMap<String, QuoteItem>> {
    let mut config = state.config.clone();
    config.dhan_access_token = credentials.access_token.clone();
    config.dhan_client_id = credentials.client_id.clone();
    let security_ids = watch_rows
        .iter()
        .map(|row| row.security_id.clone())
        .collect::<Vec<_>>();
    get_live_quotes(state, &config, &security_ids).await
}

fn build_live_strategy_snapshot(
    broker: BrokerStatus,
    mode: &str,
    feed_status: &str,
    watch_rows: &[WatchRow],
    volume_map: &HashMap<String, String>,
    quote_map: &HashMap<String, QuoteItem>,
    baselines: &HashMap<String, HistoricalScreenerFeatureRow>,
    strategy_statuses: &HashMap<String, String>,
    weekly_lab_candidates: &HashMap<String, WeeklyLabCandidate>,
    message: Option<String>,
) -> LiveStrategySnapshot {
    let regular_session = is_regular_session_now();
    let seeds = watch_rows
        .iter()
        .filter_map(|row| quote_map.get(&row.security_id).map(|quote| seed_from_quote(row, quote, volume_map)))
        .collect::<Vec<_>>();
    let market_regime = compute_market_regime(&seeds, true);
    let now = now_ist().to_rfc3339();
    let mut rows = watch_rows
        .iter()
        .filter_map(|watch| {
            let quote = quote_map.get(&watch.security_id)?;
            let seed = seed_from_quote(watch, quote, volume_map);
            let candidate = build_live_candidate(
                seed,
                &market_regime,
                baselines.get(&watch.symbol),
                strategy_statuses,
                weekly_lab_candidates.get(&watch.symbol),
                regular_session,
            );
            if candidate.live_signal.strategy_id == "unscored" || candidate.live_signal.strategy_id == "unlinked-screener" {
                return None;
            }
            Some(LiveStrategyRow {
                security_id: watch.security_id.clone(),
                symbol: candidate.symbol,
                company_name: candidate.company_name,
                strategy_id: candidate.live_signal.strategy_id,
                strategy_label: candidate.live_signal.strategy_label,
                strategy_status: candidate.live_signal.strategy_status,
                setup_family: candidate.setup_family,
                signal_status: candidate.live_signal.status,
                signal_label: candidate.live_signal.label,
                reason: candidate.live_signal.reason,
                score: candidate.score,
                last_price: candidate.last_price,
                day_change_pct: candidate.day_change_pct,
                open_gap_pct: candidate.open_gap_pct,
                volume: quote.volume,
                trigger_price: candidate.live_signal.trigger_price,
                trigger_source: candidate.live_signal.trigger_source.clone(),
                stop_loss: candidate.stop_loss,
                target_price: candidate.target_price,
                risk_reward: candidate.risk_reward,
                source: candidate.source,
                updated_at: now.clone(),
            })
        })
        .collect::<Vec<_>>();

    rows.sort_by(|a, b| {
        live_signal_rank(&a.signal_status)
            .cmp(&live_signal_rank(&b.signal_status))
            .then_with(|| strategy_status_rank(&a.strategy_status).cmp(&strategy_status_rank(&b.strategy_status)))
            .then_with(|| b.score.cmp(&a.score))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    rows.truncate(80);
    let triggered = rows.iter().filter(|row| row.signal_status == "ENTRY_NOW").count();

    LiveStrategySnapshot {
        event: "live-strategy-snapshot".to_string(),
        updated_at: now,
        mode: mode.to_string(),
        feed_status: feed_status.to_string(),
        broker,
        market_regime,
        total_watching: rows.len(),
        triggered,
        rows,
        message,
    }
}

#[derive(Default)]
struct DhanQuoteTick {
    security_id: String,
    last_price: f32,
    open: f32,
    high: f32,
    low: f32,
    prev_close: f32,
    volume: u64,
}

impl DhanQuoteTick {
    fn into_quote_item(self) -> QuoteItem {
        QuoteItem {
            last_price: self.last_price,
            ohlc: crate::dhan::market_data::QuoteOhlc {
                open: self.open,
                high: self.high,
                low: self.low,
                close: self.prev_close,
            },
            volume: self.volume,
            ..Default::default()
        }
    }
}

fn parse_dhan_quote_packets(bytes: &[u8]) -> Vec<DhanQuoteTick> {
    let mut ticks = Vec::new();
    let mut offset = 0usize;
    while offset + 8 <= bytes.len() {
        let code = bytes[offset];
        let packet_len = i16::from_le_bytes([bytes[offset + 1], bytes[offset + 2]]).max(0) as usize;
        let packet_len = if packet_len >= 8 && offset + packet_len <= bytes.len() {
            packet_len
        } else {
            bytes.len() - offset
        };
        let packet = &bytes[offset..offset + packet_len];
        let security_id = i32::from_le_bytes([packet[4], packet[5], packet[6], packet[7]]).to_string();
        match code {
            4 if packet.len() >= 50 => {
                ticks.push(DhanQuoteTick {
                    security_id,
                    last_price: read_f32_le(packet, 8),
                    volume: read_i32_le(packet, 22).max(0) as u64,
                    open: read_f32_le(packet, 34),
                    prev_close: read_f32_le(packet, 38),
                    high: read_f32_le(packet, 42),
                    low: read_f32_le(packet, 46),
                });
            }
            2 if packet.len() >= 16 => {
                ticks.push(DhanQuoteTick {
                    security_id,
                    last_price: read_f32_le(packet, 8),
                    ..Default::default()
                });
            }
            6 if packet.len() >= 16 => {
                ticks.push(DhanQuoteTick {
                    security_id,
                    prev_close: read_f32_le(packet, 8),
                    ..Default::default()
                });
            }
            8 if packet.len() >= 62 => {
                ticks.push(DhanQuoteTick {
                    security_id,
                    last_price: read_f32_le(packet, 8),
                    volume: read_i32_le(packet, 22).max(0) as u64,
                    open: read_f32_le(packet, 46),
                    prev_close: read_f32_le(packet, 50),
                    high: read_f32_le(packet, 54),
                    low: read_f32_le(packet, 58),
                });
            }
            _ => {}
        }
        if packet_len == 0 {
            break;
        }
        offset += packet_len;
    }
    ticks
}

fn read_f32_le(packet: &[u8], offset: usize) -> f32 {
    if packet.len() < offset + 4 {
        return 0.0;
    }
    f32::from_le_bytes([packet[offset], packet[offset + 1], packet[offset + 2], packet[offset + 3]])
}

fn read_i32_le(packet: &[u8], offset: usize) -> i32 {
    if packet.len() < offset + 4 {
        return 0;
    }
    i32::from_le_bytes([packet[offset], packet[offset + 1], packet[offset + 2], packet[offset + 3]])
}

pub async fn history(
    State(state): State<AppState>,
    Path(symbol): Path<String>,
    Query(query): Query<HistoryQuery>,
) -> Json<SymbolHistoryResponse> {
    let range = normalize_history_range(query.range.as_deref());
    if range == "1d" {
        return match load_dhan_intraday_history(&state, &symbol).await {
            Ok(candles) if !candles.is_empty() => {
                let summary = compute_historical_summary(&candles);
                Json(SymbolHistoryResponse {
                    updated_at: crate::types::now_ist().to_rfc3339(),
                    symbol,
                    range,
                    source: "dhan-intraday".to_string(),
                    candles,
                    summary,
                    message: None,
                })
            }
            Ok(_) => Json(SymbolHistoryResponse {
                updated_at: crate::types::now_ist().to_rfc3339(),
                symbol,
                range,
                source: "dhan-intraday".to_string(),
                candles: Vec::new(),
                summary: None,
                message: Some("Dhan intraday chart returned no candles for the selected trading day.".to_string()),
            }),
            Err(err) => Json(SymbolHistoryResponse {
                updated_at: crate::types::now_ist().to_rfc3339(),
                symbol,
                range,
                source: "dhan-intraday".to_string(),
                candles: Vec::new(),
                summary: None,
                message: Some(format!("Dhan intraday chart failed: {}", err)),
            }),
        };
    }

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
                source: "parquet-history".to_string(),
                candles,
                summary,
                message,
            })
        }
        Err(err) => Json(SymbolHistoryResponse {
            updated_at: crate::types::now_ist().to_rfc3339(),
            symbol,
            range,
            source: "parquet-history".to_string(),
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
    let weekly_lab_candidates = load_weekly_lab_candidates();

    let regular_session = is_regular_session_now();
    let live_quote_map = if broker.state == "ready" {
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
                        "Dhan quotes are available outside regular NSE hours; using the latest broker-sourced prices.".to_string()
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
        None
    };

    let symbols = watch_rows.iter().map(|row| row.symbol.clone()).collect::<Vec<_>>();
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
    let seeds = build_candidate_seeds(&watch_rows, &volume_map, live_quote_map.as_ref());
    let market_regime = compute_market_regime(&seeds, broker.live_quotes);
    let mut candidates: Vec<SwingCandidate> = seeds
        .into_iter()
        .map(|seed| {
            let baseline = live_baselines.get(&seed.symbol);
            let weekly_lab = weekly_lab_candidates.get(&seed.symbol);
            build_live_candidate(seed, &market_regime, baseline, &strategy_statuses, weekly_lab, regular_session)
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
    let limit = limit.clamp(1, 1200);

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
) -> Vec<CandidateSeed> {
    rows.iter()
        .filter_map(|row| {
            if let Some(quotes) = live_quote_map {
                if let Some(quote) = quotes.get(&row.security_id) {
                    return Some(seed_from_quote(row, quote, volume_map));
                }
            }
            None
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
    weekly_lab: Option<&WeeklyLabCandidate>,
    entry_window_open: bool,
) -> SwingCandidate {
    let fallback_family = classify_setup_family(&seed);
    let fallback_score = fallback_candidate_score(&seed, &fallback_family, regime);
    let daily_signal = evaluate_live_signal(&seed, baseline, strategy_statuses, entry_window_open);
    let weekly_signal = weekly_lab.map(|row| evaluate_weekly_lab_signal(&seed, row, entry_window_open));
    let live_signal = choose_live_signal(daily_signal, weekly_signal);

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
    let planned_entry = live_signal
        .trigger_price
        .filter(|price| *price > 0.0)
        .unwrap_or(seed.last_price);
    let stop_loss = round2(planned_entry * (1.0 - stop_buffer / 100.0));
    let target_price = round2(planned_entry * (1.0 + target_buffer / 100.0));
    let risk_reward = round2((target_price - planned_entry) / (planned_entry - stop_loss).max(0.01));
    let entry_zone = if live_signal.trigger_price.is_some() {
        format!("Trigger Rs {:.2}", planned_entry)
    } else {
        format!(
            "Rs {:.2} - Rs {:.2}",
            seed.last_price * 0.995,
            seed.last_price * 1.008
        )
    };
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

fn choose_live_signal(daily: LiveSignal, weekly: Option<LiveSignal>) -> LiveSignal {
    let Some(weekly) = weekly else {
        return daily;
    };
    if weekly.strategy_id == "king-candle-quality-v1" {
        return weekly;
    }
    if matches!(daily.status.as_str(), "ENTRY_NOW" | "WATCH") {
        daily
    } else {
        weekly
    }
}

fn evaluate_weekly_lab_signal(
    seed: &CandidateSeed,
    row: &WeeklyLabCandidate,
    entry_window_open: bool,
) -> LiveSignal {
    let trigger = row.trigger_price.max(0.01);
    let live_price = seed.last_price.max(0.01);
    let trigger_source = if row.strategy_id == "king-candle-quality-v1" {
        "King candle high + 0.1%".to_string()
    } else {
        "Weekly close baseline".to_string()
    };
    let score = (
        68.0
        + row.rs13w_rank.clamp(0.0, 1.0) * 14.0
        + row.relvol.clamp(0.0, 6.0) * 1.6
        + row.body_ratio.clamp(0.0, 1.0) * 5.0
        + row.range_atr.clamp(0.0, 4.0) * 2.0
        + (row.rank_score / 3.0).clamp(0.0, 6.0)
    ).round().clamp(60.0, 96.0) as u8;

    let lost_supertrend = row.supertrend > 0.0 && live_price < row.supertrend;
    let trigger_hit = live_price >= trigger;
    let (status, label, reason) = if lost_supertrend {
        (
            "INVALIDATED",
            "Invalidated",
            format!(
                "{} is below weekly Supertrend support: live {:.2}, Supertrend {:.2}.",
                row.strategy_label, live_price, row.supertrend
            ),
        )
    } else if row.strategy_id == "king-candle-quality-v1" && trigger_hit && entry_window_open {
        (
            "ENTRY_NOW",
            "Enter Now",
            format!(
                "King Candle Quality trigger is live: LTP {:.2} is above trigger {:.2}; relvol {:.2}x and RS13 rank {:.0}%.",
                live_price,
                trigger,
                row.relvol,
                row.rs13w_rank * 100.0
            ),
        )
    } else if row.strategy_id == "king-candle-quality-v1" && trigger_hit {
        (
            "WAIT_FOR_TRIGGER",
            "Signal Ready",
            format!(
                "King Candle Quality trigger {:.2} is cleared, but regular-session entry is closed right now.",
                trigger
            ),
        )
    } else if row.strategy_id == "king-candle-quality-v1" {
        (
            "WAIT_FOR_TRIGGER",
            "Wait Above King High",
            format!(
                "King Candle Quality is armed from {}; needs live price above {:.2}. Current LTP {:.2}.",
                row.signal_date, trigger, live_price
            ),
        )
    } else {
        (
            "WATCH",
            "Weekly Trend Watch",
            format!(
                "Weekly Supertrend 10-3 is positive from {}; LTP {:.2}, weekly close baseline {:.2}, Supertrend {:.2}.",
                row.signal_date, live_price, row.close, row.supertrend
            ),
        )
    };

    LiveSignal {
        status: status.to_string(),
        label: label.to_string(),
        reason,
        strategy_id: row.strategy_id.clone(),
        strategy_label: row.strategy_label.clone(),
        strategy_status: row.strategy_status.clone(),
        setup_family: row.setup_family.clone(),
        score,
        as_of: format!("dhan-live / weekly {}", row.signal_date),
        trigger_price: Some(trigger),
        trigger_source: Some(trigger_source),
    }
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
            trigger_source: None,
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
    let historical_high_52w = row.high_52w.unwrap_or(high).max(0.01);
    let high_52w = historical_high_52w.max(high);
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
    let atr14 = row.atr14.unwrap_or_else(|| (high - low).abs()).max(close * 0.01);
    let prior_high20 = row.prior_high20.filter(|value| *value > 0.0).unwrap_or(high_20d);
    let prior_close3 = row.prior_close3.filter(|value| *value > 0.0).unwrap_or(close);
    let close_location = if high > low { ((close - low) / (high - low)).clamp(0.0, 1.0) } else { 0.5 };
    let range_atr = if atr14 > 0.0 { (high - low).max(0.0) / atr14 } else { 0.0 };
    let recovery_from_low_pct = if low > 0.0 { (close - low).max(0.0) / low } else { 0.0 };
    let ret3 = if prior_close3 > 0.0 { close / prior_close3 - 1.0 } else { 0.0 };
    let rs60_rank = row.rs60_rank.unwrap_or(0.5).clamp(0.0, 1.0);
    let market_breadth200 = row.market_breadth200.unwrap_or(0.5).clamp(0.0, 1.0);
    let trend_up = close > sma20 && sma20 > sma50;
    let pullback_zone = close >= sma20 * 0.98 && close <= sma20 * 1.03;
    let rsi10_pullback = close > sma200 && rsi10 < 30.0;
    let tuned_ma_breakout = trend_up
        && market_breadth200 >= 0.38
        && rs60_rank >= 0.58
        && volume_ratio >= 1.3
        && close_location >= 0.58
        && atr14 > 0.0
        && high >= prior_high20 * 1.001
        && close >= prior_high20 * 1.001 * 0.985;
    let tuned_panic_reversal = ret3 <= -0.08
        && range_atr >= 1.35
        && close_location >= 0.64
        && recovery_from_low_pct >= 0.012
        && atr14 > 0.0;
    let setup_family = if tuned_panic_reversal {
        "Panic Reversal"
    } else if tuned_ma_breakout {
        "MA Breakout"
    } else if rsi10_pullback {
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
        tuned_ma_breakout,
        tuned_panic_reversal,
    );
    let strategy_status = strategy_statuses
        .get(strategy_id)
        .cloned()
        .unwrap_or_else(|| default_strategy_status(strategy_id).to_string());
    let (trigger_price, trigger_source) = match setup_family {
        "MA Breakout" => (
            Some(round2((prior_high20 * 1.001) as f32)),
            Some("Prior 20D high + 0.1%".to_string()),
        ),
        "Panic Reversal" => (
            Some(round2((low + 0.25 * (high - low)) as f32)),
            Some("25% recovery from intraday low".to_string()),
        ),
        "Breakout Setup" => (
            Some(round2((prior_high20 * 1.001) as f32)),
            Some("Prior 20D high + 0.1%".to_string()),
        ),
        "Pullback To 20 DMA" => (
            Some(round2(sma20 as f32)),
            Some("Live-adjusted SMA20 reclaim".to_string()),
        ),
        "Near 52W High" => (
            Some(round2((historical_high_52w * 1.001) as f32)),
            Some("52W high + 0.1%".to_string()),
        ),
        _ => (None, None),
    };
    let trigger_hit = trigger_price
        .map(|trigger| close >= trigger as f64)
        .unwrap_or(false);

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
    } else if strategy_status == "Candidate" && !trigger_hit {
        (
            "WAIT_FOR_TRIGGER",
            "Wait For Trigger",
            match trigger_price {
                Some(trigger) => format!(
                    "{} is approved, but live LTP {:.2} has not crossed trigger {:.2} ({}) yet.",
                    strategy_label,
                    close,
                    trigger,
                    trigger_source.as_deref().unwrap_or("strategy trigger")
                ),
                None => format!(
                    "{} is approved, but no live trigger could be derived for setup {}.",
                    strategy_label, setup_family
                ),
            },
        )
    } else if strategy_status == "Candidate" && entry_window_open {
        (
            "ENTRY_NOW",
            "Enter Now",
            format!(
                "{} trigger is live: LTP {:.2} is above trigger {:.2}; score {}, distance to 52W high {:.2}%, volume ratio {:.2}.",
                strategy_label,
                close,
                trigger_price.unwrap_or(close as f32),
                score,
                distance_to_52w_high_pct,
                volume_ratio
            ),
        )
    } else if strategy_status == "Candidate" {
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
                "{} matches, but latest backtest diagnostics mark it Watch rather than Candidate.",
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
        trigger_source,
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
        trigger_source: None,
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
        "king-candle-quality-v1" => Some((40.0, 8.0, "20-30 weeks / weekly ST trail")),
        "weekly-supertrend-10-3" => Some((40.0, 10.0, "20-30 weeks / weekly ST trail")),
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

fn load_weekly_lab_candidates() -> HashMap<String, WeeklyLabCandidate> {
    let mut out = HashMap::new();
    load_weekly_lab_file(
        "docs/king_supertrend_lab/weekly_supertrend_103_latest_candidates.csv",
        "weekly-supertrend-10-3",
        "Weekly Supertrend 10-3",
        "Weekly Supertrend",
        "Watch",
        false,
        &mut out,
    );
    load_weekly_lab_file(
        "docs/king_supertrend_lab/king_candle_quality_breakout_latest_candidates.csv",
        "king-candle-quality-v1",
        "King Candle Quality",
        "King Candle Quality",
        "Candidate",
        true,
        &mut out,
    );
    out
}

#[allow(clippy::too_many_arguments)]
fn load_weekly_lab_file(
    path: &str,
    strategy_id: &str,
    strategy_label: &str,
    setup_family: &str,
    strategy_status: &str,
    breakout_trigger: bool,
    out: &mut HashMap<String, WeeklyLabCandidate>,
) {
    let Ok(content) = fs::read_to_string(path) else {
        return;
    };
    for line in content.lines().skip(1) {
        let cols = line.split(',').map(str::trim).collect::<Vec<_>>();
        if cols.len() < 12 || cols[0].is_empty() {
            continue;
        }
        let symbol = cols[0].to_string();
        let close = parse_csv_f32(cols[3]);
        let high = parse_csv_f32(cols[4]);
        let trigger_price = if breakout_trigger {
            round2(high * 1.001)
        } else {
            round2(close)
        };
        out.insert(symbol.clone(), WeeklyLabCandidate {
            symbol,
            strategy_id: strategy_id.to_string(),
            strategy_label: strategy_label.to_string(),
            setup_family: setup_family.to_string(),
            strategy_status: strategy_status.to_string(),
            signal_date: cols[2].to_string(),
            trigger_price,
            close,
            supertrend: parse_csv_f32(cols[5]),
            rank_score: parse_csv_f32(cols[6]),
            relvol: parse_csv_f32(cols[7]),
            rs13w_rank: parse_csv_f32(cols[8]),
            body_ratio: parse_csv_f32(cols[9]),
            range_atr: parse_csv_f32(cols[11]),
        });
    }
}

fn parse_csv_f32(raw: &str) -> f32 {
    raw.parse::<f32>().unwrap_or(0.0)
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
    match raw.unwrap_or("1d").to_ascii_lowercase().as_str() {
        "1d" | "live" | "intraday" => "1d".to_string(),
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
            toFloat64(avg(atr14)) AS avg_atr14, \
            toFloat64(avg(abs(ret3))) AS avg_ret3_abs \
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
         prior_high252, prior_close3, prior_low20, ret3, range_atr, recovery_from_low_pct, \
         rs60_rank, rs120_rank, market_breadth200, refreshed_at) \
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
                lagInFrame(day_close, 3, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prior_close3, \
                coalesce(min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), day_low) AS prior_low20, \
                if(lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 60, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret60, \
                if(lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) > 0, day_close / lagInFrame(day_close, 120, 0) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1, 0) AS ret120, \
                100 - (100 / (1 + avg(gain) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) / greatest(avg(loss) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0.000001))) AS rsi10, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM priced \
        ), scored AS ( \
            SELECT *, \
                if(prior_close3 > 0, day_close / prior_close3 - 1, 0) AS ret3, \
                if(atr14 > 0, (day_high - day_low) / atr14, 0) AS range_atr, \
                if(day_low > 0, (day_close - day_low) / day_low, 0) AS recovery_from_low_pct, \
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
            toFloat64(prior_close3), \
            toFloat64(prior_low20), \
            toFloat64(ret3), \
            toFloat64(range_atr), \
            toFloat64(recovery_from_low_pct), \
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
            toFloat64(f.prev_close) AS prev_close, \
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
            toFloat64(f.prior_high20) AS prior_high20, \
            toFloat64(f.prior_close3) AS prior_close3, \
            toFloat64(f.ret3) AS ret3, \
            toFloat64(f.range_atr) AS range_atr, \
            toFloat64(f.recovery_from_low_pct) AS recovery_from_low_pct, \
            toFloat64(f.rs60_rank) AS rs60_rank, \
            toFloat64(f.market_breadth200) AS market_breadth200 \
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

async fn load_dhan_intraday_history(
    state: &AppState,
    symbol: &str,
) -> anyhow::Result<Vec<HistoricalCandle>> {
    let credentials = resolve_dhan_credentials(state)
        .await
        .ok_or_else(|| anyhow::anyhow!("Dhan credentials are not configured"))?;
    let watch = load_watch_rows(state, 1, Some(symbol))
        .await
        .into_iter()
        .find(|row| row.symbol.eq_ignore_ascii_case(symbol))
        .ok_or_else(|| anyhow::anyhow!("{} is not present in the Dhan watchlist/security master", symbol))?;

    let mut config = state.config.clone();
    config.dhan_access_token = credentials.access_token;
    config.dhan_client_id = credentials.client_id;
    let client = DhanClient::new(&config);
    let target_day = intraday_chart_day();
    let from_time = format!("{} 09:15:00", target_day.format("%Y-%m-%d"));
    let to_time = format!("{} 15:30:00", target_day.format("%Y-%m-%d"));
    let response = fetch_intraday_candles(&client, &watch.security_id, &from_time, &to_time).await?;
    let mut candles = intraday_response_to_candles(response);

    if target_day == now_ist().date_naive() {
        if let Ok(quotes) = get_live_quotes(state, &config, &[watch.security_id]).await {
            if let Some(quote) = quotes.values().next() {
                append_live_quote_candle(&mut candles, quote);
            }
        }
    }

    Ok(candles)
}

fn intraday_chart_day() -> chrono::NaiveDate {
    let now = now_ist();
    let today = now.date_naive();
    let session_has_started = compute_bucket(&now) > 0
        || now.hour() > 15
        || (now.hour() == 15 && now.minute() >= 30);

    if !is_nse_holiday(today) && session_has_started {
        today
    } else {
        prev_trading_day(today)
    }
}

fn intraday_response_to_candles(response: IntradayResponse) -> Vec<HistoricalCandle> {
    let len = response
        .timestamp
        .len()
        .min(response.open.len())
        .min(response.high.len())
        .min(response.low.len())
        .min(response.close.len())
        .min(response.volume.len());

    let mut candles = Vec::with_capacity(len);
    for idx in 0..len {
        let Some(ts) = Utc.timestamp_opt(response.timestamp[idx].round() as i64, 0).single() else {
            continue;
        };
        candles.push(HistoricalCandle {
            date: ts.with_timezone(&chrono_tz::Asia::Kolkata).to_rfc3339(),
            open: round2_f64(response.open[idx]),
            high: round2_f64(response.high[idx]),
            low: round2_f64(response.low[idx]),
            close: round2_f64(response.close[idx]),
            volume: response.volume[idx].max(0.0).round() as u64,
        });
    }
    candles
}

fn append_live_quote_candle(candles: &mut Vec<HistoricalCandle>, quote: &QuoteItem) {
    let last_price = quote.last_price;
    if last_price <= 0.0 {
        return;
    }
    let live_price = round2_f64(last_price as f64);
    let live_high = round2_f64(quote.high().max(last_price) as f64);
    let live_low = round2_f64(if quote.low() > 0.0 { quote.low().min(last_price) } else { last_price } as f64);
    let live_open = round2_f64(if quote.open() > 0.0 { quote.open() } else { last_price } as f64);
    let live_volume = quote.volume;

    if let Some(last) = candles.last_mut() {
        last.close = live_price;
        last.high = last.high.max(live_high);
        last.low = if last.low > 0.0 { last.low.min(live_low) } else { live_low };
        if live_volume > 0 {
            last.volume = live_volume;
        }
        return;
    }

    candles.push(HistoricalCandle {
        date: now_ist().to_rfc3339(),
        open: live_open,
        high: live_high,
        low: live_low,
        close: live_price,
        volume: live_volume,
    });
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
                    strategy_id = 'momentum-core-v1', 'Candidate', \
                    strategy_id = 'rsi10-pullback-reversion-v1', 'Candidate', \
                    strategy_id = 'failed-breakdown-reclaim-v1', 'Candidate', \
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
        .is_some_and(|row| row.cached_rows > 0 && row.avg_atr14 > 0.0 && row.avg_ret3_abs > 0.0);
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
            toFloat64(f.prior_close3) AS prior_close3, \
            toFloat64(f.prior_low20) AS prior_low20, \
            toFloat64(f.ret3) AS ret3, \
            toFloat64(f.range_atr) AS range_atr, \
            toFloat64(f.recovery_from_low_pct) AS recovery_from_low_pct, \
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
    let prior_close3 = row.prior_close3.filter(|value| *value > 0.0).unwrap_or(day_close);
    let prior_low20 = row.prior_low20.filter(|value| *value > 0.0).unwrap_or(day_low);
    let rs60_rank = row.rs60_rank.unwrap_or(0.5).clamp(0.0, 1.0);
    let rs120_rank = row.rs120_rank.unwrap_or(0.5).clamp(0.0, 1.0);
    let market_breadth200 = row.market_breadth200.unwrap_or(0.5).clamp(0.0, 1.0);
    let ret3 = row
        .ret3
        .unwrap_or_else(|| if prior_close3 > 0.0 { day_close / prior_close3 - 1.0 } else { 0.0 });
    let range_atr = row
        .range_atr
        .filter(|value| *value > 0.0)
        .unwrap_or_else(|| if atr14 > 0.0 { (day_high - day_low).max(0.0) / atr14 } else { 0.0 });
    let recovery_from_low_pct = row
        .recovery_from_low_pct
        .unwrap_or_else(|| if day_low > 0.0 { (day_close - day_low).max(0.0) / day_low } else { 0.0 });
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
    let tuned_ma_breakout = trend_up
        && market_breadth200 >= 0.38
        && rs60_rank >= 0.58
        && volume_ratio >= 1.3
        && close_location >= 0.58
        && atr14 > 0.0
        && day_high >= prior_high20 * 1.001
        && day_close >= prior_high20 * 1.001 * 0.985;
    let tuned_panic_reversal = ret3 <= -0.08
        && range_atr >= 1.35
        && close_location >= 0.64
        && recovery_from_low_pct >= 0.012
        && atr14 > 0.0;
    let relative_strength_leader = rs60_rank >= 0.75
        && rs120_rank >= 0.65
        && day_close > sma50
        && (distance_to_52w_high_pct <= 10.0 || day_close > prior_high55 || day_close > prior_high252);

    let setup_family = if tuned_panic_reversal {
        "Panic Reversal"
    } else if tuned_ma_breakout {
        "MA Breakout"
    } else if rsi10_pullback {
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
    if tuned_ma_breakout {
        score += 20.0;
    }
    if tuned_panic_reversal {
        score += 24.0;
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
        tuned_ma_breakout,
        tuned_panic_reversal,
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
        prior_close3,
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
    prior_close3: f64,
    prior_low20: f64,
) -> (String, f64, f64, f64) {
    let atr = atr14.max(close * 0.015).max(0.01);
    let raw_stop = match setup_family {
        "MA Breakout" => low.min(close - atr),
        "Panic Reversal" => low,
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
        "MA Breakout" => 2.5,
        "Panic Reversal" => 2.0,
        "RSI10 Pullback Reversion" => 1.4,
        "Pullback To 20 DMA" | "Failed Breakdown Reclaim" => 1.8,
        _ => 2.0,
    };
    let target_price = if setup_family == "Panic Reversal" && prior_close3 > close {
        round2_f64(prior_close3)
    } else {
        round2_f64(close + risk * reward_multiple)
    };
    let risk_reward = round2_f64((target_price - close) / risk);
    let planned_entry = match setup_family {
        "MA Breakout" => format!("Breakout trigger above Rs {:.2}; confirm the 20D level holds", prior_high20),
        "Panic Reversal" => format!("Panic reclaim hold above Rs {:.2}; target pre-panic close Rs {:.2}", low, prior_close3.max(close)),
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
    tuned_ma_breakout: bool,
    tuned_panic_reversal: bool,
) -> (&'static str, &'static str) {
    if tuned_panic_reversal {
        return ("tuned-panic-reversal-v1", "Panic Reversal Lab");
    }
    if tuned_ma_breakout {
        return ("tuned-ma-breakout-v1", "MA Breakout Lab");
    }
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
        "king-candle-quality-v1" => "Candidate",
        "weekly-supertrend-10-3" => "Watch",
        "tuned-ma-breakout-v1" => "Candidate",
        "tuned-panic-reversal-v1" => "Watch",
        "momentum-core-v1" => "Candidate",
        "rsi10-pullback-reversion-v1" => "Candidate",
        "failed-breakdown-reclaim-v1" => "Candidate",
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
        "Candidate" => 0,
        "Watch" => 1,
        "Fragile" => 2,
        "Rejected" => 3,
        _ => 4,
    }
}

fn matches_setup_filter(row: &HistoricalScreenerRow, setup_filter: &str) -> bool {
    match setup_filter {
        "all" => true,
        "ma" | "ma-breakout" => row.setup_family == "MA Breakout",
        "panic" | "panic-reversal" => row.setup_family == "Panic Reversal",
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
        "fresh" | "signals" => matches!(row.strategy_status.as_str(), "Candidate" | "Watch"),
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
    let stop_loss = if row.stop_loss > 0.0 && row.stop_loss < entry_price {
        row.stop_loss
    } else {
        round2_f64(entry_price * (1.0 - rule.stop_loss_pct / 100.0))
    };
    let target_price = if row.target_price > entry_price {
        row.target_price
    } else {
        round2_f64(entry_price * (1.0 + rule.take_profit_pct / 100.0))
    };
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
        stop_loss,
        target_price,
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
    let stop_loss = if row.stop_loss > 0.0 && row.stop_loss < entry_price {
        row.stop_loss
    } else {
        round2_f64(entry_price * (1.0 - rule.stop_loss_pct / 100.0))
    };
    let target_price = if row.target_price > entry_price {
        row.target_price
    } else {
        round2_f64(entry_price * (1.0 + rule.take_profit_pct / 100.0))
    };
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
    matches!(row.strategy_status.as_str(), "Candidate" | "Watch")
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
        "tuned-ma-breakout-v1" => PaperRule {
            stop_loss_pct: 6.0,
            take_profit_pct: 12.0,
            source: "tuned MA breakout lab model",
        },
        "tuned-panic-reversal-v1" => PaperRule {
            stop_loss_pct: 4.0,
            take_profit_pct: 10.0,
            source: "tuned panic reversal lab model",
        },
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
            source: "engine RSI10 pullback model",
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
