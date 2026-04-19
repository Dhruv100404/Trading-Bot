use axum::{extract::State, Json};
use std::collections::HashMap;
use crate::api::AppState;
use crate::api::ch_http::query_json;

pub async fn list(State(state): State<AppState>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    match query_json(
        &client,
        &state.ch_url,
        "SELECT trading_date, buy_signals, sell_signals, profitable, losses, \
         avg_return_pct, net_pnl, capital_used, roc_pct \
         FROM trading.daily_performance ORDER BY trading_date DESC LIMIT 30",
        HashMap::new(),
    ).await {
        Ok(rows) => Json(serde_json::json!({ "performance": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}
