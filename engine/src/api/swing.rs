use std::collections::HashMap;
use std::fs;

use axum::{
    extract::{Path, Query, State},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE, Engine as _};
use chrono::{Datelike, TimeZone, Utc};
use clickhouse::Row;
use serde::{Deserialize, Serialize};
use serde::de::DeserializeOwned;
use tokio::sync::RwLockReadGuard;

use crate::api::AppState;
use crate::dhan::client::DhanClient;
use crate::dhan::market_data::{fetch_quotes, QuoteItem};
use crate::types::{compute_bucket, is_nse_holiday, now_ist};

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

#[derive(Serialize)]
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
}

#[derive(Serialize)]
pub struct HistoricalScreenerResponse {
    updated_at: String,
    range: String,
    total_rows: usize,
    rows: Vec<HistoricalScreenerRow>,
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
    day_close: Option<f64>,
    day_high: Option<f64>,
    day_low: Option<f64>,
    day_volume: Option<String>,
    sma20: Option<f64>,
    sma50: Option<f64>,
    avg_volume20: Option<f64>,
    high_20d: Option<f64>,
    high_52w: Option<f64>,
    low_52w: Option<f64>,
}

#[derive(Row, Deserialize)]
struct StrategyStatusRow {
    strategy_id: String,
    status: String,
}

const QUOTE_CACHE_TTL_SECS: u64 = 20;

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
                .collect();
            mapped.sort_by(|a, b| {
                strategy_status_rank(&a.strategy_status)
                    .cmp(&strategy_status_rank(&b.strategy_status))
                    .then_with(|| b.score.cmp(&a.score))
                    .then_with(|| a.symbol.cmp(&b.symbol))
            });
            mapped.truncate(limit);

            Json(HistoricalScreenerResponse {
                updated_at: crate::types::now_ist().to_rfc3339(),
                range: "1y".to_string(),
                total_rows: mapped.len(),
                rows: mapped,
                message: None,
            })
        }
        Err(err) => Json(HistoricalScreenerResponse {
            updated_at: crate::types::now_ist().to_rfc3339(),
            range: "1y".to_string(),
            total_rows: 0,
            rows: Vec::new(),
            message: Some(format!("Historical screener query failed: {}", err)),
        }),
    }
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
    if !state.config.dhan_access_token.is_empty() && !state.config.dhan_client_id.is_empty() {
        return Some(ResolvedDhanCredentials {
            access_token: state.config.dhan_access_token.clone(),
            client_id: state.config.dhan_client_id.clone(),
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
            toFloat64(avg_volume20) AS avg_volume20, \
            toFloat64(high_20d) AS high_20d, \
            toFloat64(high_52w) AS high_52w, \
            toFloat64(low_52w) AS low_52w \
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
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM daily \
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
    let setup_family = if trend_up && breakout_pct <= 1.5 && volume_ratio >= 1.1 {
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
            WHERE toDate(date) >= subtractYears(today(), 2) \
              AND symbol IN (SELECT symbol FROM trading.watchlist WHERE enabled = 1) \
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
            toFloat64(avg_volume20) AS avg_volume20, \
            toFloat64(high_20d) AS high_20d, \
            toFloat64(high_52w) AS high_52w, \
            toFloat64(low_52w) AS low_52w \
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
                avg(day_volume) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_volume20, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high_20d, \
                max(day_high) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w, \
                min(day_low) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM daily \
        ) \
        WHERE rn = 1 AND day_close >= {min_price} AND avg_volume20 >= {min_avg_volume} \
        ORDER BY avg_volume20 DESC \
        LIMIT 1200",
        parquet_source
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
    let day_high = row.day_high?;
    let day_low = row.day_low?;
    let day_volume: u64 = row.day_volume?.parse().ok()?;
    let sma20 = row.sma20?;
    let sma50 = row.sma50?;
    let avg_volume20 = row.avg_volume20?;
    let high_20d = row.high_20d?;
    let high_52w = row.high_52w?;
    let low_52w = row.low_52w?;

    if day_close <= 0.0 || high_20d <= 0.0 || high_52w <= 0.0 || low_52w <= 0.0 {
        return None;
    }

    let breakout_pct = ((high_20d - day_close) / high_20d) * 100.0;
    let distance_to_52w_high_pct = ((high_52w - day_close) / high_52w) * 100.0;
    let range_span = (high_52w - low_52w).max(0.01);
    let range_position_pct = ((day_close - low_52w) / range_span) * 100.0;
    let volume_ratio = day_volume as f64 / avg_volume20.max(1.0);
    let trend_up = day_close > sma20 && sma20 > sma50;
    let pullback_zone = day_close >= sma20 * 0.98 && day_close <= sma20 * 1.03;

    let setup_family = if trend_up && breakout_pct <= 1.5 && volume_ratio >= 1.1 {
        "Breakout Setup"
    } else if trend_up && pullback_zone {
        "Pullback To 20 DMA"
    } else if row.day_close > row.sma50 && distance_to_52w_high_pct <= 8.0 {
        "Near 52W High"
    } else {
        "Trend Filter"
    };

    let trend_label = if trend_up {
        "Uptrend"
    } else if row.day_close > row.sma50 {
        "Constructive"
    } else {
        "Needs Work"
    };

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
    );
    let strategy_status = strategy_statuses
        .get(strategy_id)
        .cloned()
        .unwrap_or_else(|| default_strategy_status(strategy_id).to_string());

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
    })
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
) -> (&'static str, &'static str) {
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
    if setup_family == "Pullback To 20 DMA" && score >= 88 && trend_up && pullback_zone && volume_ratio >= 0.8 && day_close >= sma20 {
        return ("pullback-quality-v2", "Pullback Quality");
    }
    if setup_family == "Pullback To 20 DMA" {
        return ("pullback-20dma-v1", "Pullback 20DMA");
    }
    if setup_family == "Breakout Setup" && score >= 90 && volume_ratio >= 1.5 && breakout_pct <= 1.0 && trend_up {
        return ("breakout-volume-v2", "Breakout Volume");
    }
    if setup_family == "Breakout Setup" {
        return ("swing-breakout-v1", "Swing Breakout");
    }
    ("unlinked-screener", "Unlinked Screen")
}

fn default_strategy_status(strategy_id: &str) -> &'static str {
    match strategy_id {
        "momentum-core-v1" => "Research",
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
        "breakout" => row.setup_family == "Breakout Setup",
        "pullback" => row.setup_family == "Pullback To 20 DMA",
        "52wh" | "near-high" => row.setup_family == "Near 52W High",
        "trend" => row.setup_family == "Trend Filter",
        _ => true,
    }
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
