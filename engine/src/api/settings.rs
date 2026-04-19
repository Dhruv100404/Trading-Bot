use axum::{extract::State, Json};
use serde::Deserialize;
use crate::api::AppState;

/// GET /api/settings — returns market data token (masked) and client_id
pub async fn get_settings(State(state): State<AppState>) -> Json<serde_json::Value> {
    let token = get_setting(&state.ch, "market_data_token").await;
    let client_id = get_setting(&state.ch, "market_data_client_id").await;
    // Mask token for display: show first 10 + last 10 chars
    let masked = if token.len() > 24 {
        format!("{}...{}", &token[..10], &token[token.len()-10..])
    } else if token.is_empty() {
        "NOT SET".to_string()
    } else {
        "***".to_string()
    };
    Json(serde_json::json!({
        "market_data_token_masked": masked,
        "market_data_client_id": client_id,
    }))
}

#[derive(Deserialize)]
pub struct UpdateSettings {
    pub market_data_token: Option<String>,
    pub market_data_client_id: Option<String>,
}

/// PUT /api/settings — update market data token and/or client_id
pub async fn update_settings(State(state): State<AppState>, Json(body): Json<UpdateSettings>) -> Json<serde_json::Value> {
    if let Some(token) = &body.market_data_token {
        set_setting(&state.ch, "market_data_token", token).await;
        tracing::info!("Market data token updated via UI");
    }
    if let Some(cid) = &body.market_data_client_id {
        set_setting(&state.ch, "market_data_client_id", cid).await;
        tracing::info!("Market data client_id updated to {}", cid);
    }
    Json(serde_json::json!({ "ok": true }))
}

async fn get_setting(ch: &clickhouse::Client, key: &str) -> String {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { value: String }
    ch.query("SELECT value FROM trading.system_settings FINAL WHERE key = ?")
        .bind(key)
        .fetch_one::<Row>()
        .await
        .map(|r| r.value)
        .unwrap_or_default()
}

async fn set_setting(ch: &clickhouse::Client, key: &str, value: &str) {
    ch.query("INSERT INTO trading.system_settings (key, value) VALUES (?, ?)")
        .bind(key)
        .bind(value)
        .execute()
        .await
        .ok();
    ch.query("OPTIMIZE TABLE trading.system_settings FINAL").execute().await.ok();
}

/// Read market data token for use by poller (not masked)
pub async fn get_market_data_token(ch: &clickhouse::Client) -> (String, String) {
    let token = get_setting(ch, "market_data_token").await;
    let client_id = get_setting(ch, "market_data_client_id").await;
    (token, client_id)
}
