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

use crate::api::ch_http::query_json;
use crate::api::AppState;
use crate::market_activity::MarketActivityRow;

#[derive(Deserialize)]
pub struct MarketActivityQuery {
    pub symbol: Option<String>,
    pub metric_type: Option<String>,
    pub exchange: Option<String>,
    pub source: Option<String>,
    pub min_volume_multiplier: Option<f32>,
    pub limit: Option<u32>,
}

#[derive(Debug, Serialize)]
pub struct MarketActivityRefreshSource {
    pub source: String,
    pub metric_type: String,
    pub exchange: String,
    pub index_name: String,
    pub url: String,
}

#[derive(Debug, Serialize)]
pub struct MarketActivityRefreshSummary {
    pub ok: bool,
    pub rows: usize,
    pub sources: Vec<MarketActivityRefreshSource>,
    pub refreshed_at: String,
}

pub async fn ensure_market_activity_schema(client: &Client) -> Result<()> {
    client
        .query(
            "CREATE TABLE IF NOT EXISTS trading.market_activity_snapshots (
                row_id String,
                snapshot_at DateTime('Asia/Kolkata'),
                trading_date Date,
                source LowCardinality(String),
                metric_type LowCardinality(String),
                exchange LowCardinality(String),
                index_name LowCardinality(String),
                rank UInt32,
                symbol String,
                stock_name String,
                moneycontrol_id String,
                slug String,
                price Float64,
                change_abs Float64,
                change_pct Float64,
                day_high Float64,
                day_low Float64,
                open Float64,
                prev_close Float64,
                volume UInt64,
                avg_volume UInt64,
                volume_multiplier Float64,
                volume_change_pct Float64,
                value_cr Float64,
                vwap Float64,
                direction LowCardinality(String),
                mcap_cr Float64,
                month_return_pct Float64,
                month3_return_pct Float64,
                share_url String,
                source_url String,
                fetched_at DateTime('Asia/Kolkata'),
                inserted_at DateTime DEFAULT now()
            ) ENGINE = MergeTree
            PARTITION BY toYYYYMM(trading_date)
            ORDER BY (trading_date, metric_type, exchange, index_name, snapshot_at, rank, symbol)
            TTL snapshot_at + INTERVAL 180 DAY",
        )
        .execute()
        .await?;

    Ok(())
}

pub async fn list(
    State(state): State<AppState>,
    Query(query): Query<MarketActivityQuery>,
) -> impl IntoResponse {
    let limit = query.limit.unwrap_or(150).clamp(1, 500);
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
        filters.push("a.symbol = {symbol:String}".to_string());
    }
    if let Some(metric_type) = query
        .metric_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("metric_type".to_string(), metric_type.to_string());
        filters.push("a.metric_type = {metric_type:String}".to_string());
    }
    if let Some(exchange) = query
        .exchange
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        params.insert("exchange".to_string(), exchange.to_ascii_uppercase());
        filters.push("a.exchange = {exchange:String}".to_string());
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
    if let Some(min_volume_multiplier) = query.min_volume_multiplier {
        params.insert(
            "min_volume_multiplier".to_string(),
            min_volume_multiplier.to_string(),
        );
        filters.push("a.volume_multiplier >= {min_volume_multiplier:Float32}".to_string());
    }

    let where_clause = if filters.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", filters.join(" AND "))
    };

    let sql = format!(
        "SELECT
            a.row_id,
            formatDateTime(a.snapshot_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS snapshot_at,
            toString(a.trading_date) AS trading_date,
            a.source,
            a.metric_type,
            a.exchange,
            a.index_name,
            a.rank,
            a.symbol,
            a.stock_name,
            a.moneycontrol_id,
            a.slug,
            a.price,
            a.change_abs,
            a.change_pct,
            a.day_high,
            a.day_low,
            a.open,
            a.prev_close,
            a.volume,
            a.avg_volume,
            a.volume_multiplier,
            a.volume_change_pct,
            a.value_cr,
            a.vwap,
            a.direction,
            a.mcap_cr,
            a.month_return_pct,
            a.month3_return_pct,
            a.share_url,
            a.source_url,
            formatDateTime(a.fetched_at, '%Y-%m-%d %H:%i:%S', 'Asia/Kolkata') AS fetched_at
         FROM trading.market_activity_snapshots AS a
         INNER JOIN (
            SELECT source, metric_type, exchange, index_name, max(snapshot_at) AS snapshot_at
            FROM trading.market_activity_snapshots
            GROUP BY source, metric_type, exchange, index_name
         ) AS latest
         ON a.source = latest.source
            AND a.metric_type = latest.metric_type
            AND a.exchange = latest.exchange
            AND a.index_name = latest.index_name
            AND a.snapshot_at = latest.snapshot_at
         {where_clause}
         ORDER BY a.metric_type ASC, a.rank ASC, a.symbol ASC
         LIMIT {{limit:UInt32}}"
    );

    let client = reqwest::Client::new();
    match query_json(&client, &state.ch_url, &sql, params).await {
        Ok(rows) => (
            StatusCode::OK,
            Json(serde_json::json!({ "activity": rows })),
        ),
        Err(err) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "activity": [], "ok": false, "message": err.to_string(), "error": err.to_string() })),
        ),
    }
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

pub async fn refresh_all(_client: &Client, ch_url: &str) -> Result<MarketActivityRefreshSummary> {
    let max_rows = market_activity_max_rows_per_source();
    let sources = crate::market_activity::configured_sources();
    let source_meta = sources
        .iter()
        .map(|source| MarketActivityRefreshSource {
            source: source.source.clone(),
            metric_type: source.metric_type.clone(),
            exchange: source.exchange.clone(),
            index_name: source.index_name.clone(),
            url: source.url.clone(),
        })
        .collect::<Vec<_>>();

    let rows = crate::market_activity::fetch_market_activity(max_rows).await?;
    insert_market_activity(ch_url, &rows).await?;

    Ok(MarketActivityRefreshSummary {
        ok: true,
        rows: rows.len(),
        sources: source_meta,
        refreshed_at: crate::types::now_ist()
            .format("%Y-%m-%d %H:%M:%S")
            .to_string(),
    })
}

pub fn market_activity_auto_refresh_enabled() -> bool {
    std::env::var("MARKET_ACTIVITY_AUTO_REFRESH")
        .map(|value| {
            let value = value.trim().to_ascii_lowercase();
            !matches!(value.as_str(), "0" | "false" | "no" | "off")
        })
        .unwrap_or(true)
}

pub fn market_activity_refresh_interval_secs() -> u64 {
    std::env::var("MARKET_ACTIVITY_REFRESH_INTERVAL_SECS")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(900)
        .clamp(600, 1800)
}

pub async fn run_refresh_loop(client: Client, ch_url: String) {
    if !market_activity_auto_refresh_enabled() {
        tracing::info!("[MARKET-ACTIVITY] auto-refresh disabled");
        return;
    }

    let interval_secs = market_activity_refresh_interval_secs();
    tracing::info!(
        "[MARKET-ACTIVITY] auto-refresh enabled; interval={}s",
        interval_secs
    );

    loop {
        match refresh_all(&client, &ch_url).await {
            Ok(summary) => tracing::info!(
                "[MARKET-ACTIVITY] refreshed rows={} sources={}",
                summary.rows,
                summary.sources.len()
            ),
            Err(err) => tracing::warn!("[MARKET-ACTIVITY] refresh failed: {}", err),
        }

        tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
    }
}

fn market_activity_max_rows_per_source() -> usize {
    std::env::var("MARKET_ACTIVITY_MAX_ROWS_PER_SOURCE")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .unwrap_or(75)
        .clamp(10, 250)
}

async fn insert_market_activity(ch_url: &str, rows: &[MarketActivityRow]) -> Result<()> {
    insert_json_each_row(ch_url, "trading.market_activity_snapshots", rows).await
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
