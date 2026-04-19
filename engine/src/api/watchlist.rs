use axum::{extract::{State, Path, Query}, Json};
use serde::Deserialize;
use std::collections::HashMap;
use crate::api::AppState;
use crate::api::ch_http::query_json;
use crate::db::watchlist as wl_db;

#[derive(Deserialize)]
pub struct WatchlistQuery { pub enabled: Option<u8> }

pub async fn list(State(state): State<AppState>, Query(q): Query<WatchlistQuery>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    let result = if let Some(e) = q.enabled {
        let mut params = HashMap::new();
        params.insert("enabled".to_string(), e.to_string());
        query_json(
            &client,
            &state.ch_url,
            "SELECT security_id, symbol, company_name, tiers, enabled, min_volume \
             FROM trading.watchlist FINAL WHERE enabled = {enabled:UInt8} ORDER BY symbol LIMIT 5000",
            params,
        ).await
    } else {
        query_json(
            &client,
            &state.ch_url,
            "SELECT security_id, symbol, company_name, tiers, enabled, min_volume \
             FROM trading.watchlist FINAL ORDER BY symbol LIMIT 5000",
            HashMap::new(),
        ).await
    };
    match result {
        Ok(rows) => Json(serde_json::json!({ "watchlist": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct UpdateStock { pub enabled: Option<u8>, pub min_volume: Option<u32> }

pub async fn update_stock(State(state): State<AppState>, Path(security_id): Path<String>, Json(body): Json<UpdateStock>) -> Json<serde_json::Value> {
    if let Some(e) = body.enabled {
        if let Err(err) = state.ch.query(
            "INSERT INTO trading.watchlist (security_id, enabled) VALUES (?, ?)"
        ).bind(security_id.as_str()).bind(e).execute().await {
            tracing::warn!("update_stock enabled failed: {}", err);
        }
    }
    if let Some(v) = body.min_volume {
        if let Err(err) = state.ch.query(
            "INSERT INTO trading.watchlist (security_id, min_volume) VALUES (?, ?)"
        ).bind(security_id.as_str()).bind(v).execute().await {
            tracing::warn!("update_stock min_volume failed: {}", err);
        }
    }
    Json(serde_json::json!({ "ok": true }))
}

pub async fn list_tiers(State(state): State<AppState>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    match query_json(
        &client,
        &state.ch_url,
        "SELECT tier_name, enabled FROM trading.tier_state FINAL ORDER BY tier_name",
        HashMap::new(),
    ).await {
        Ok(rows) => Json(serde_json::json!({ "tiers": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct UpdateTier { pub enabled: u8 }

pub async fn update_tier(
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<UpdateTier>,
) -> Json<serde_json::Value> {
    // Save new tier state
    if let Err(e) = state.ch.query(
        "INSERT INTO trading.tier_state (tier_name, enabled) VALUES (?, ?)"
    ).bind(name.as_str()).bind(body.enabled).execute().await {
        tracing::warn!("update_tier tier_state failed: {}", e);
    }
    state.ch.query("OPTIMIZE TABLE trading.tier_state FINAL").execute().await.ok();

    // Re-evaluate watchlist.enabled using tiers + volume groups
    wl_db::reevaluate_watchlist_enabled(&state.ch).await;

    let count: u64 = state.ch
        .query("SELECT count() FROM trading.watchlist FINAL WHERE enabled = 1")
        .fetch_one().await.unwrap_or(0);
    Json(serde_json::json!({ "ok": true, "active_stocks": count }))
}

pub async fn list_volume_groups(State(state): State<AppState>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    match query_json(
        &client,
        &state.ch_url,
        "SELECT group_name, enabled FROM trading.volume_group_state FINAL ORDER BY group_name",
        HashMap::new(),
    ).await {
        Ok(rows) => Json(serde_json::json!({ "volume_groups": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct UpdateVolumeGroup { pub enabled: u8 }

pub async fn update_volume_group(
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<UpdateVolumeGroup>,
) -> Json<serde_json::Value> {
    // Save new volume group state
    if let Err(e) = state.ch.query(
        "INSERT INTO trading.volume_group_state (group_name, enabled) VALUES (?, ?)"
    ).bind(name.as_str()).bind(body.enabled).execute().await {
        return Json(serde_json::json!({ "error": e.to_string() }));
    }
    state.ch.query("OPTIMIZE TABLE trading.volume_group_state FINAL").execute().await.ok();

    // Re-evaluate watchlist.enabled using tiers + volume groups
    wl_db::reevaluate_watchlist_enabled(&state.ch).await;

    let count: u64 = state.ch
        .query("SELECT count() FROM trading.watchlist FINAL WHERE enabled = 1")
        .fetch_one().await.unwrap_or(0);
    Json(serde_json::json!({ "ok": true, "active_stocks": count }))
}
