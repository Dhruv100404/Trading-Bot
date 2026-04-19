use axum::{extract::State, Json};
use serde_json::Value;
use crate::api::AppState;
use crate::db::watchlist::{get_gap15_config, save_gap15_config};
use crate::types::Gap15Config;

pub async fn get_config(State(state): State<AppState>) -> Json<Value> {
    let cfg = get_gap15_config(&state.ch).await;
    Json(serde_json::to_value(cfg).unwrap_or_default())
}

pub async fn update_config(State(state): State<AppState>, Json(patch): Json<Value>) -> Json<Value> {
    let mut cfg: Gap15Config = get_gap15_config(&state.ch).await;

    if let Some(v) = patch.get("total_capital").and_then(|v| v.as_u64()) { cfg.total_capital = v as u32; }
    if let Some(v) = patch.get("leverage").and_then(|v| v.as_u64()) { cfg.leverage = v as u32; }
    if let Some(v) = patch.get("top_n").and_then(|v| v.as_u64()) { cfg.top_n = v as usize; }
    if let Some(v) = patch.get("tp_pct").and_then(|v| v.as_f64()) { cfg.tp_pct = v as f32; }
    if let Some(v) = patch.get("sl_pct").and_then(|v| v.as_f64()) { cfg.sl_pct = v as f32; }
    if let Some(v) = patch.get("exit_bucket").and_then(|v| v.as_u64()) { cfg.exit_bucket = v as u16; }
    if let Some(v) = patch.get("gap_min_pct").and_then(|v| v.as_f64()) { cfg.gap_min_pct = v as f32; }
    if let Some(v) = patch.get("gap_max_pct").and_then(|v| v.as_f64()) { cfg.gap_max_pct = v as f32; }
    if let Some(v) = patch.get("price_max").and_then(|v| v.as_f64()) { cfg.price_max = v as f32; }
    if let Some(v) = patch.get("cap_mult").and_then(|v| v.as_f64()) { cfg.cap_mult = v as f32; }

    match save_gap15_config(&state.ch, &cfg).await {
        Ok(_) => Json(serde_json::to_value(cfg).unwrap_or_default()),
        Err(e) => Json(serde_json::json!({"error": e.to_string()})),
    }
}
