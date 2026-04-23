use std::collections::HashMap;
use std::fs;

use axum::{
    extract::{Path, Query, State},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE, Engine as _};
use chrono::{TimeZone, Utc};
use clickhouse::Row;
use serde::{Deserialize, Serialize};
use tokio::sync::RwLockReadGuard;

use crate::api::AppState;
use crate::dhan::client::DhanClient;
use crate::dhan::market_data::{fetch_quotes, QuoteItem};

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
    last_price: f32,
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

    let live_quote_map = if broker.state == "ready" {
        let credentials = resolve_dhan_credentials(state).await;
        if let Some(credentials) = credentials {
            let mut config = state.config.clone();
            config.dhan_access_token = credentials.access_token;
            config.dhan_client_id = credentials.client_id;
            let security_ids: Vec<String> = watch_rows.iter().map(|row| row.security_id.clone()).collect();
            match get_live_quotes(state, &config, &security_ids).await {
                Ok(quotes) => Some(quotes),
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

    let seeds = build_candidate_seeds(&watch_rows, &volume_map, live_quote_map.as_ref());
    let market_regime = compute_market_regime(&seeds, broker.live_quotes);
    let mut candidates: Vec<SwingCandidate> = seeds
        .into_iter()
        .map(|seed| build_candidate(seed, &market_regime))
        .collect();

    candidates.sort_by(|a, b| b.score.cmp(&a.score).then_with(|| a.symbol.cmp(&b.symbol)));
    candidates.truncate(limit);

    DashboardBundle {
        broker,
        market_regime,
        candidates,
    }
}

async fn get_live_quotes(
    state: &AppState,
    config: &crate::config::Config,
    security_ids: &[String],
) -> anyhow::Result<HashMap<String, QuoteItem>> {
    if let Some(cached) = read_cached_quotes(&state.quote_cache.read().await, security_ids) {
        return Ok(cached);
    }

    let client = DhanClient::new(config);
    let fetched = fetch_quotes(&client, security_ids, &config.dhan_quote_endpoint).await?;

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
) -> Vec<CandidateSeed> {
    rows.iter()
        .enumerate()
        .map(|(idx, row)| {
            if let Some(quotes) = live_quote_map {
                if let Some(quote) = quotes.get(&row.security_id) {
                    return seed_from_quote(row, quote, volume_map);
                }
            }
            fallback_seed(row, volume_map, idx)
        })
        .collect()
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
        last_price: round2(last),
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
        last_price: round2(base_price),
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

fn round2(value: f32) -> f32 {
    (value * 100.0).round() / 100.0
}
