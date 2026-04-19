use axum::{extract::State, Json};
use crate::api::AppState;
use chrono::Timelike;

pub async fn get_status(State(_state): State<AppState>) -> Json<serde_json::Value> {
    let now = crate::types::now_ist();
    let h = now.hour();
    let m = now.minute();
    let market_status = if h < 9 || (h == 9 && m < 15) { "PRE-OPEN" }
        else if h < 15 || (h == 15 && m < 30) { "LIVE" }
        else { "CLOSED" };
    Json(serde_json::json!({
        "market_status": market_status,
        "current_ist": now.format("%H:%M:%S").to_string(),
        "today": crate::types::today_ist().format("%Y-%m-%d").to_string(),
    }))
}
