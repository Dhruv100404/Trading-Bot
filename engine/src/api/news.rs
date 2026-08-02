use anyhow::Result;
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use clickhouse::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

use crate::api::ch_http::query_json;
use crate::api::AppState;
use crate::news::{NewsArticle, NewsMention, NewsScore, NseLargeDeal, WatchlistCompany};

#[derive(Deserialize)]
pub struct NewsQuery {
    pub symbol: Option<String>,
    pub source: Option<String>,
    pub min_impact: Option<f32>,
    pub limit: Option<u32>,
}

#[derive(Deserialize)]
pub struct LargeDealsQuery {
    pub symbol: Option<String>,
    pub deal_type: Option<String>,
    pub side: Option<String>,
    pub limit: Option<u32>,
}

#[derive(Deserialize)]
pub struct CorporateEventsQuery {
    pub symbol: Option<String>,
    pub category: Option<String>,
    pub lookback_days: Option<u32>,
    pub limit: Option<u32>,
}

#[derive(Deserialize)]
pub struct PredictionHistoryQuery {
    pub success: Option<bool>,
    pub split: Option<String>,
    pub limit: Option<u32>,
}

#[derive(Debug, Serialize)]
pub struct NewsRefreshSource {
    pub source: String,
    pub kind: String,
    pub category: String,
    pub url: String,
}

#[derive(Debug, Serialize)]
pub struct NewsRefreshSummary {
    pub ok: bool,
    pub articles: usize,
    pub mentions: usize,
    pub scores: usize,
    pub deals: usize,
    pub watchlist_companies: usize,
    pub sources: Vec<NewsRefreshSource>,
    pub refreshed_at: String,
    pub deal_error: Option<String>,
}

pub async fn ensure_news_schema(client: &Client) -> Result<()> {
    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.news_articles (
                article_id String,
                source LowCardinality(String),
                source_kind LowCardinality(String),
                category LowCardinality(String),
                title String,
                url String,
                summary String,
                published_at Nullable(DateTime('Asia/Kolkata')),
                fetched_at DateTime('Asia/Kolkata'),
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toYYYYMM(fetched_at)
            ORDER BY (source, article_id)
            TTL fetched_at + INTERVAL 45 DAY",
        )
        .execute()
        .await?;

    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.news_mentions (
                article_id String,
                symbol String,
                security_id String,
                company_name String,
                match_confidence Float32,
                matched_text String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            ORDER BY (article_id, symbol)
            TTL inserted_at + INTERVAL 45 DAY",
        )
        .execute()
        .await?;

    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.news_scores (
                article_id String,
                symbol String,
                sentiment Float32,
                impact_score Float32,
                direction LowCardinality(String),
                horizon LowCardinality(String),
                confidence Float32,
                reason String,
                model LowCardinality(String),
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            ORDER BY (article_id, symbol)
            TTL inserted_at + INTERVAL 45 DAY",
        )
        .execute()
        .await?;

    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.nse_large_deals (
                deal_id String,
                deal_type LowCardinality(String),
                deal_date Date,
                deal_date_raw String,
                symbol String,
                security_name String,
                client_name String,
                side LowCardinality(String),
                quantity Float64,
                price Float64,
                value_lakh Float64,
                source_url String,
                fetched_at DateTime('Asia/Kolkata'),
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toYYYYMM(deal_date)
            ORDER BY (deal_date, deal_type, symbol, client_name, side, deal_id)
            TTL deal_date + INTERVAL 10 YEAR",
        )
        .execute()
        .await?;

    // Older deployments created this table with a 180-day ingestion-time TTL,
    // which made long-horizon deal research impossible. Keep a bounded but
    // research-appropriate history instead.
    client
        .query(
            "ALTER TABLE trading.nse_large_deals
             MODIFY TTL deal_date + INTERVAL 10 YEAR",
        )
        .execute()
        .await?;

    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.corporate_events (
                event_id String,
                source LowCardinality(String),
                source_event_id String,
                symbol String,
                company_name String,
                event_time DateTime64(3, 'Asia/Kolkata'),
                event_date Date,
                event_category LowCardinality(String),
                catalyst_score UInt8,
                title String,
                summary String,
                attachment_url String,
                source_url String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (symbol, event_date, event_category, event_id)",
        )
        .execute()
        .await?;

    Ok(())
}

/// Import the locally cached official NSE corporate-event history once. The
/// parquet is mounted read-only into ClickHouse's user_files directory. Live
/// refresh remains independent, so a missing research cache never prevents the
/// engine from starting.
pub async fn backfill_corporate_events_if_empty(client: &Client) -> Result<u64> {
    let existing = client
        .query("SELECT count() FROM trading.corporate_events")
        .fetch_one::<u64>()
        .await?;
    if existing > 0 {
        return Ok(0);
    }

    client
        .query(
            "INSERT INTO trading.corporate_events
             (event_id, source, source_event_id, symbol, company_name, event_time,
              event_date, event_category, catalyst_score, title, summary,
              attachment_url, source_url)
             SELECT
                lower(hex(MD5(concat(
                    ifNull(source, ''), '|', ifNull(source_event_id, ''), '|',
                    ifNull(symbol, ''), '|', toString(event_date), '|',
                    ifNull(event_category, ''), '|', ifNull(title, '')
                )))) AS event_id,
                ifNull(source, ''),
                ifNull(source_event_id, ''),
                upper(ifNull(symbol, '')),
                ifNull(company_name, ''),
                coalesce(
                    subtractMinutes(toDateTime64(event_time, 3, 'Asia/Kolkata'), 330),
                    toDateTime64(event_date, 3, 'Asia/Kolkata')
                ),
                event_date,
                ifNull(event_category, 'other'),
                toUInt8(greatest(0, least(100, catalyst_score))),
                ifNull(title, ''),
                ifNull(summary, ''),
                ifNull(attachment_url, ''),
                ifNull(source_url, '')
             FROM file('events/corporate_catalysts.parquet', Parquet)
             WHERE event_date >= toDate('2021-01-01')
               AND event_date <= today()
               AND catalyst_score >= 70
               AND source IN (
                    'nse_announcements', 'nse_financial_results',
                    'nse_integrated_financials'
               )
               AND event_category IN (
                    'financial_results', 'big_order',
                    'merger_acquisition', 'policy_regulatory'
               )
               AND upper(ifNull(symbol, '')) IN (
                    SELECT symbol FROM trading.watchlist FINAL WHERE symbol != ''
               )",
        )
        .execute()
        .await?;

    client
        .query("SELECT count() FROM trading.corporate_events")
        .fetch_one::<u64>()
        .await
        .map_err(Into::into)
}

pub async fn list(State(state): State<AppState>, Query(query): Query<NewsQuery>) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(100).clamp(1, 250);
    let mut params = HashMap::new();
    params.insert("limit".to_string(), limit.to_string());

    let mut filters = Vec::new();
    if let Some(symbol) = query
        .symbol
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("symbol".to_string(), symbol.to_ascii_uppercase());
        filters.push("m.symbol = {symbol:String}".to_string());
    }
    if let Some(source) = query
        .source
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("source".to_string(), source.to_string());
        filters.push("a.source = {source:String}".to_string());
    }
    if let Some(min_impact) = query.min_impact {
        params.insert("min_impact".to_string(), min_impact.to_string());
        filters.push("ifNull(s.impact_score, 0) >= {min_impact:Float32}".to_string());
    }

    let where_clause = if filters.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", filters.join(" AND "))
    };

    let sql = format!(
        "SELECT
            a.article_id AS article_id,
            a.source,
            a.source_kind,
            a.category,
            a.title,
            a.url,
            a.summary,
            if(isNull(a.published_at), '', formatDateTime(assumeNotNull(a.published_at), '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata')) AS published_at,
            formatDateTime(a.fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at,
            ifNull(m.symbol, '') AS symbol,
            ifNull(m.security_id, '') AS security_id,
            ifNull(m.company_name, '') AS company_name,
            ifNull(m.match_confidence, 0) AS match_confidence,
            ifNull(m.matched_text, '') AS matched_text,
            ifNull(s.sentiment, 0) AS sentiment,
            ifNull(s.impact_score, 0) AS impact_score,
            if(empty(s.direction), 'NEUTRAL', s.direction) AS direction,
            if(empty(s.horizon), 'MARKET', s.horizon) AS horizon,
            ifNull(s.confidence, 0) AS confidence,
            ifNull(s.reason, '') AS reason,
            ifNull(s.model, '') AS model
         FROM (
            SELECT article_id, source, source_kind, category, title, url, summary,
                   published_at, fetched_at
            FROM trading.news_articles FINAL
         ) AS a
         LEFT JOIN (
            SELECT article_id, symbol, security_id, company_name, match_confidence, matched_text
            FROM trading.news_mentions FINAL
         ) AS m ON m.article_id = a.article_id
         LEFT JOIN (
            SELECT article_id, symbol, sentiment, impact_score, direction, horizon, confidence, reason, model
            FROM trading.news_scores FINAL
         ) AS s ON s.article_id = m.article_id AND s.symbol = m.symbol
         {where_clause}
         ORDER BY ifNull(a.published_at, a.fetched_at) DESC, impact_score DESC, a.title ASC
         LIMIT {{limit:UInt32}}"
    );

    let client = reqwest::Client::new();
    match query_json(&client, &state.ch_url, &sql, params).await {
        Ok(rows) => (StatusCode::OK, Json(serde_json::json!({ "news": rows }))),
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "news": [], "ok": false, "message": err.to_string(), "error": err.to_string() })),
        ),
    }
}

pub async fn large_deals(
    State(state): State<AppState>,
    Query(query): Query<LargeDealsQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(100).clamp(1, 250);
    let mut params = HashMap::new();
    params.insert("limit".to_string(), limit.to_string());

    let mut filters = Vec::new();
    if let Some(symbol) = query
        .symbol
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("symbol".to_string(), symbol.to_ascii_uppercase());
        filters.push("symbol = {symbol:String}".to_string());
    }
    if let Some(deal_type) = query
        .deal_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("deal_type".to_string(), deal_type.to_ascii_uppercase());
        filters.push("deal_type = {deal_type:String}".to_string());
    }
    if let Some(side) = query
        .side
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("side".to_string(), side.to_ascii_uppercase());
        filters.push("side = {side:String}".to_string());
    }

    let where_clause = if filters.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", filters.join(" AND "))
    };

    let sql = format!(
        "SELECT
            deal_id,
            deal_type,
            toString(deal_date) AS deal_date,
            deal_date_raw,
            symbol,
            security_name,
            client_name,
            side,
            quantity,
            price,
            value_lakh,
            source_url,
            formatDateTime(fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at
         FROM trading.nse_large_deals FINAL
         {where_clause}
         ORDER BY deal_date DESC, value_lakh DESC, symbol ASC
         LIMIT {{limit:UInt32}}"
    );

    let client = reqwest::Client::new();
    match query_json(&client, &state.ch_url, &sql, params).await {
        Ok(rows) => (
            StatusCode::OK,
            Json(serde_json::json!({ "deals": rows })),
        ),
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "deals": [], "ok": false, "message": err.to_string(), "error": err.to_string() })),
        ),
    }
}

pub async fn corporate_events(
    State(state): State<AppState>,
    Query(query): Query<CorporateEventsQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(30).clamp(1, 100);
    let lookback_days = query.lookback_days.unwrap_or(730).clamp(1, 2_000);
    let mut params = HashMap::new();
    params.insert("limit".to_string(), limit.to_string());
    params.insert("lookback_days".to_string(), lookback_days.to_string());

    let mut filters = vec![
        "event_date >= today() - toIntervalDay({lookback_days:UInt32})".to_string(),
        "event_date <= today()".to_string(),
    ];
    if let Some(symbol) = query
        .symbol
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("symbol".to_string(), symbol.to_ascii_uppercase());
        filters.push("symbol = {symbol:String}".to_string());
    }
    if let Some(category) = query
        .category
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("category".to_string(), category.to_ascii_lowercase());
        filters.push("event_category = {category:String}".to_string());
    }

    let sql = format!(
        "SELECT
            event_id,
            source,
            source_event_id,
            symbol,
            company_name,
            formatDateTime(latest_event_time, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS event_time,
            toString(event_date) AS event_date,
            event_category,
            catalyst_score,
            title,
            summary,
            attachment_url,
            source_url,
            evidence_count
         FROM (
            SELECT
                argMax(event_id, event_time) AS event_id,
                arrayStringConcat(groupUniqArray(source), ', ') AS source,
                argMax(source_event_id, event_time) AS source_event_id,
                symbol,
                argMax(company_name, event_time) AS company_name,
                max(event_time) AS latest_event_time,
                event_date,
                event_category,
                max(catalyst_score) AS catalyst_score,
                argMax(title, event_time) AS title,
                argMax(summary, event_time) AS summary,
                argMax(attachment_url, event_time) AS attachment_url,
                argMax(source_url, event_time) AS source_url,
                count() AS evidence_count
            FROM trading.corporate_events FINAL
            WHERE {}
            GROUP BY symbol, event_date, event_category
         )
         ORDER BY event_date DESC, catalyst_score DESC, latest_event_time DESC, symbol ASC
         LIMIT {{limit:UInt32}}",
        filters.join(" AND ")
    );

    let client = reqwest::Client::new();
    match query_json(&client, &state.ch_url, &sql, params).await {
        Ok(rows) => (
            StatusCode::OK,
            Json(serde_json::json!({ "events": rows })),
        ),
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "events": [], "ok": false, "message": err.to_string(), "error": err.to_string() })),
        ),
    }
}

pub async fn strategy_evidence() -> impl IntoResponse {
    let path = strategy_artifact_path("evidence.json");
    match std::fs::read_to_string(&path)
        .map_err(anyhow::Error::from)
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).map_err(Into::into))
    {
        Ok(mut evidence) => {
            if let Some(object) = evidence.as_object_mut() {
                object.insert(
                    "evidence_kind".to_string(),
                    serde_json::Value::String("historical_backtest".to_string()),
                );
                object.insert(
                    "live_prediction_claim".to_string(),
                    serde_json::Value::Bool(false),
                );
            }
            (StatusCode::OK, Json(evidence))
        }
        Err(err) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "ok": false,
                "evidence_kind": "historical_backtest",
                "message": format!("Strategy evidence is unavailable: {err}"),
            })),
        ),
    }
}

pub async fn prediction_history(
    Query(query): Query<PredictionHistoryQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(12).clamp(1, 100) as usize;
    let path = strategy_artifact_path("predictions.csv");
    match read_prediction_history(&path, &query, limit) {
        Ok((rows, matched)) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "predictions": rows,
                "matched": matched,
                "evidence_kind": "historical_backtest",
                "live_predictions": false,
                "message": "Historical simulation only; prospective paper outcomes are not available yet.",
            })),
        ),
        Err(err) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "predictions": [],
                "matched": 0,
                "evidence_kind": "historical_backtest",
                "live_predictions": false,
                "message": format!("Prediction history is unavailable: {err}"),
            })),
        ),
    }
}

fn strategy_artifact_path(name: &str) -> PathBuf {
    let root = std::env::var("NEWS_STRATEGY_EVIDENCE_DIR")
        .unwrap_or_else(|_| "/app/docs/news_action_strategy".to_string());
    PathBuf::from(root).join(name)
}

fn read_prediction_history(
    path: &std::path::Path,
    query: &PredictionHistoryQuery,
    limit: usize,
) -> Result<(Vec<serde_json::Value>, usize)> {
    let mut reader = csv::ReaderBuilder::new().flexible(true).from_path(path)?;
    let headers = reader.headers()?.clone();
    let success_index = headers
        .iter()
        .position(|header| matches!(header, "target_hit" | "success"));
    let outcome_index = headers.iter().position(|header| header == "outcome" || header == "exit_reason");
    let split_index = headers.iter().position(|header| header == "split");
    let requested_split = query
        .split
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase);

    let mut rows = Vec::new();
    let mut matched = 0usize;
    for record in reader.records() {
        let record = record?;
        let row_success = success_index
            .and_then(|index| record.get(index))
            .and_then(parse_bool)
            .or_else(|| {
                outcome_index
                    .and_then(|index| record.get(index))
                    .map(|value| value.to_ascii_uppercase().starts_with("TARGET"))
            });
        if let Some(expected) = query.success {
            if row_success != Some(expected) {
                continue;
            }
        }
        if let Some(expected) = requested_split.as_deref() {
            let actual = split_index
                .and_then(|index| record.get(index))
                .unwrap_or_default()
                .trim()
                .to_ascii_lowercase();
            if actual != expected {
                continue;
            }
        }

        matched += 1;
        if rows.len() >= limit {
            continue;
        }
        let mut object = serde_json::Map::new();
        for (header, value) in headers.iter().zip(record.iter()) {
            object.insert(header.to_string(), prediction_csv_value(header, value));
        }
        object.insert(
            "target_hit".to_string(),
            row_success.map(serde_json::Value::Bool).unwrap_or(serde_json::Value::Null),
        );
        rows.push(serde_json::Value::Object(object));
    }
    Ok((rows, matched))
}

fn parse_bool(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "1" | "yes" => Some(true),
        "false" | "0" | "no" => Some(false),
        _ => None,
    }
}

fn prediction_csv_value(header: &str, value: &str) -> serde_json::Value {
    let value = value.trim();
    if value.is_empty() {
        return serde_json::Value::Null;
    }
    if matches!(header, "target_hit" | "success") {
        if let Some(parsed) = parse_bool(value) {
            return serde_json::Value::Bool(parsed);
        }
    }
    const NUMERIC_FIELDS: &[&str] = &[
        "entry_price", "target_price", "stop_price", "exit_price", "entry", "target",
        "stop", "exit", "hold_sessions", "net_return_pct", "relvol50", "stop_pct",
        "signal_rank", "close_location", "market_breadth200", "event_count",
        "max_catalyst_score",
    ];
    if NUMERIC_FIELDS.contains(&header) {
        if let Ok(parsed) = value.parse::<f64>() {
            if let Some(number) = serde_json::Number::from_f64(parsed) {
                return serde_json::Value::Number(number);
            }
        }
    }
    serde_json::Value::String(value.to_string())
}

pub async fn refresh(State(state): State<AppState>) -> impl IntoResponse {
    match refresh_all(&state.ch, &state.ch_url).await {
        Ok(summary) => (
            StatusCode::OK,
            Json(serde_json::to_value(summary).unwrap_or_else(|err| {
                serde_json::json!({ "ok": false, "message": err.to_string(), "error": err.to_string() })
            })),
        ),
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "ok": false, "message": err.to_string(), "error": err.to_string() })),
        ),
    }
}

pub async fn refresh_all(client: &Client, ch_url: &str) -> Result<NewsRefreshSummary> {
    let max_per_source = news_max_per_source();
    let sources = crate::news::configured_sources();
    let source_meta = sources
        .iter()
        .map(|source| NewsRefreshSource {
            source: source.source.clone(),
            kind: source.kind.as_str().to_string(),
            category: source.category.clone(),
            url: source.url.clone(),
        })
        .collect::<Vec<_>>();

    let articles = crate::news::fetch_articles(max_per_source).await?;
    let companies = load_watchlist_companies(client).await?;
    let mentions = crate::news::resolve_mentions(&articles, &companies);
    let scores = crate::news::score_mentions(&articles, &mentions);

    let mut deal_error = None;
    let deals = match crate::news::fetch_nse_large_deals(nse_large_deals_lookback_days()).await {
        Ok(rows) => rows,
        Err(err) => {
            let message = err.to_string();
            tracing::warn!("[NEWS] NSE large-deals refresh failed: {}", message);
            deal_error = Some(message);
            Vec::new()
        }
    };

    insert_news_refresh(ch_url, &articles, &mentions, &scores, &deals).await?;

    Ok(NewsRefreshSummary {
        ok: true,
        articles: articles.len(),
        mentions: mentions.len(),
        scores: scores.len(),
        deals: deals.len(),
        watchlist_companies: companies.len(),
        sources: source_meta,
        refreshed_at: crate::types::now_ist()
            .format("%Y-%m-%d %H:%M:%S")
            .to_string(),
        deal_error,
    })
}

pub fn news_auto_refresh_enabled() -> bool {
    std::env::var("NEWS_AUTO_REFRESH")
        .map(|value| {
            let value = value.trim().to_ascii_lowercase();
            !matches!(value.as_str(), "0" | "false" | "no" | "off")
        })
        .unwrap_or(true)
}

pub fn news_refresh_interval_secs() -> u64 {
    std::env::var("NEWS_REFRESH_INTERVAL_SECS")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(900)
        .clamp(600, 1800)
}

pub async fn run_refresh_loop(client: Client, ch_url: String) {
    if !news_auto_refresh_enabled() {
        tracing::info!("[NEWS] auto-refresh disabled");
        return;
    }

    let interval_secs = news_refresh_interval_secs();
    tracing::info!(
        "[NEWS] auto-refresh enabled; interval={}s NSE_LOOKBACK={}d",
        interval_secs,
        nse_large_deals_lookback_days()
    );

    loop {
        match refresh_all(&client, &ch_url).await {
            Ok(summary) => tracing::info!(
                "[NEWS] refreshed articles={} mentions={} scores={} deals={} deal_error={}",
                summary.articles,
                summary.mentions,
                summary.scores,
                summary.deals,
                summary.deal_error.as_deref().unwrap_or("none")
            ),
            Err(err) => tracing::warn!("[NEWS] refresh failed: {}", err),
        }

        tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
    }
}

async fn load_watchlist_companies(client: &Client) -> Result<Vec<WatchlistCompany>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row {
        symbol: String,
        security_id: String,
        company_name: String,
    }

    let rows = client
        .query(
            "SELECT symbol, security_id, company_name
             FROM trading.watchlist FINAL
             WHERE symbol != '' AND company_name != ''
             LIMIT 10000",
        )
        .fetch_all::<Row>()
        .await?;

    Ok(rows
        .into_iter()
        .map(|row| WatchlistCompany {
            symbol: row.symbol,
            security_id: row.security_id,
            company_name: row.company_name,
        })
        .collect())
}

async fn insert_news_refresh(
    ch_url: &str,
    articles: &[NewsArticle],
    mentions: &[NewsMention],
    scores: &[NewsScore],
    deals: &[NseLargeDeal],
) -> Result<()> {
    insert_json_each_row(ch_url, "trading.news_articles", articles).await?;
    insert_json_each_row(ch_url, "trading.news_mentions", mentions).await?;
    insert_json_each_row(ch_url, "trading.news_scores", scores).await?;
    insert_json_each_row(ch_url, "trading.nse_large_deals", deals).await?;
    Ok(())
}

fn news_max_per_source() -> usize {
    std::env::var("NEWS_MAX_PER_SOURCE")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .unwrap_or(40)
        .clamp(5, 100)
}

fn nse_large_deals_lookback_days() -> i64 {
    std::env::var("NSE_LARGE_DEALS_LOOKBACK_DAYS")
        .ok()
        .and_then(|value| value.trim().parse::<i64>().ok())
        .unwrap_or(7)
        .clamp(0, 30)
}

async fn insert_json_each_row<T: Serialize>(ch_url: &str, table: &str, rows: &[T]) -> Result<()> {
    if rows.is_empty() {
        return Ok(());
    }

    let mut body = format!("INSERT INTO {} FORMAT JSONEachRow\n", table);
    for row in rows {
        body.push_str(&serde_json::to_string(row)?);
        body.push('\n');
    }

    reqwest::Client::new()
        .post(ch_url)
        .body(body)
        .send()
        .await?
        .error_for_status()?;

    Ok(())
}
