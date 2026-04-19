use axum::{extract::{State, Path}, Json};
use serde::Deserialize;
use std::collections::HashMap;
use crate::api::AppState;
use crate::api::ch_http::{query_json, ch_exec};

pub async fn list(State(state): State<AppState>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    match query_json(
        &client,
        &state.ch_url,
        "SELECT name, client_id, mode, enabled, broker FROM trading.accounts FINAL ORDER BY name",
        HashMap::new(),
    ).await {
        Ok(rows) => Json(serde_json::json!({ "accounts": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct CreateAccount {
    pub name: String,
    pub client_id: String,
    pub access_token: String,
    /// "DHAN" (default) or "ZERODHA"
    #[serde(default = "default_broker")]
    pub broker: String,
    /// Zerodha API key (required for ZERODHA, empty for DHAN)
    #[serde(default)]
    pub api_key: String,
    /// Zerodha API secret (required for ZERODHA OAuth token exchange)
    #[serde(default)]
    pub api_secret: String,
}
fn default_broker() -> String { "DHAN".to_string() }

pub async fn create(State(state): State<AppState>, Json(body): Json<CreateAccount>) -> Json<serde_json::Value> {
    let broker = if body.broker.eq_ignore_ascii_case("ZERODHA") { "ZERODHA" } else { "DHAN" };
    match state.ch.query(
        "INSERT INTO trading.accounts (name, client_id, access_token, broker, api_key, api_secret) VALUES (?, ?, ?, ?, ?, ?)"
    ).bind(body.name.as_str())
     .bind(body.client_id.as_str())
     .bind(body.access_token.as_str())
     .bind(broker)
     .bind(body.api_key.as_str())
     .bind(body.api_secret.as_str())
     .execute().await {
        Ok(_) => {
            state.ch.query("OPTIMIZE TABLE trading.accounts FINAL").execute().await.ok();
            Json(serde_json::json!({ "ok": true }))
        }
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

#[derive(Deserialize)]
pub struct UpdateAccount { pub mode: Option<String>, pub enabled: Option<u8> }

pub async fn update(State(state): State<AppState>, Path(client_id): Path<String>, Json(body): Json<UpdateAccount>) -> Json<serde_json::Value> {
    if body.mode.is_none() && body.enabled.is_none() {
        return Json(serde_json::json!({ "ok": true }));
    }

    let http = reqwest::Client::new();

    // Read current row via HTTP interface so Enum8 comes back as its string name in JSONEachRow.
    let mut rp = HashMap::new();
    rp.insert("cid".to_string(), client_id.clone());
    let rows = match query_json(
        &http, &state.ch_url,
        "SELECT name, access_token, mode, enabled, broker, api_key, api_secret FROM trading.accounts FINAL WHERE client_id = {cid:String} LIMIT 1",
        rp,
    ).await {
        Ok(r) if !r.is_empty() => r,
        Ok(_) => return Json(serde_json::json!({ "error": "account not found" })),
        Err(e) => return Json(serde_json::json!({ "error": format!("read failed: {}", e) })),
    };

    let row = &rows[0];
    let name         = row["name"].as_str().unwrap_or("").to_string();
    let access_token = row["access_token"].as_str().unwrap_or("").to_string();
    let cur_mode     = row["mode"].as_str().unwrap_or("PAPER").to_string();
    let cur_enabled: u8 = row["enabled"].as_u64().unwrap_or(1) as u8;
    let broker       = row["broker"].as_str().unwrap_or("DHAN").to_string();
    let api_key      = row["api_key"].as_str().unwrap_or("").to_string();
    let api_secret   = row["api_secret"].as_str().unwrap_or("").to_string();

    let new_mode    = body.mode.as_deref().unwrap_or(&cur_mode).to_string();
    let new_enabled = body.enabled.unwrap_or(cur_enabled);

    let mut wp = HashMap::new();
    wp.insert("name".to_string(), name);
    wp.insert("cid".to_string(),  client_id);
    wp.insert("token".to_string(), access_token);
    wp.insert("mode".to_string(),  new_mode);
    wp.insert("enabled".to_string(), new_enabled.to_string());
    wp.insert("broker".to_string(), broker);
    wp.insert("api_key".to_string(), api_key);
    wp.insert("api_secret".to_string(), api_secret);

    match ch_exec(
        &http, &state.ch_url,
        "INSERT INTO trading.accounts (name, client_id, access_token, mode, enabled, broker, api_key, api_secret) \
         VALUES ({name:String}, {cid:String}, {token:String}, {mode:String}, {enabled:UInt8}, {broker:String}, {api_key:String}, {api_secret:String})",
        wp,
    ).await {
        Ok(_) => {
            let _ = ch_exec(&http, &state.ch_url, "OPTIMIZE TABLE trading.accounts FINAL", HashMap::new()).await;
            Json(serde_json::json!({ "ok": true }))
        }
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

pub async fn remove(State(state): State<AppState>, Path(client_id): Path<String>) -> Json<serde_json::Value> {
    let client = reqwest::Client::new();
    let mut params = HashMap::new();
    params.insert("cid".to_string(), client_id);
    match ch_exec(
        &client,
        &state.ch_url,
        "DELETE FROM trading.accounts WHERE client_id = {cid:String}",
        params,
    ).await {
        Ok(_) => Json(serde_json::json!({ "ok": true })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}

/// GET /api/accounts/health — ping each account's broker API to check token validity.
/// Returns { "health": { "client_id": { "ok": bool, "error": "..." } } }
pub async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct AccRow { client_id: String, access_token: String, broker: String, api_key: String }

    let accounts = state.ch.query(
        "SELECT client_id, access_token, broker, api_key FROM trading.accounts FINAL WHERE enabled = 1"
    ).fetch_all::<AccRow>().await.unwrap_or_default();

    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap();

    let mut health = serde_json::Map::new();

    // Run all health checks concurrently
    let mut handles = vec![];
    for acc in accounts {
        let http = http.clone();
        handles.push(tokio::spawn(async move {
            let result = if acc.broker == "ZERODHA" {
                let auth = format!("token {}:{}", acc.api_key, acc.access_token);
                match http.get("https://api.kite.trade/user/profile")
                    .header("Authorization", &auth)
                    .header("X-Kite-Version", "3")
                    .send().await
                {
                    Ok(resp) => {
                        let status = resp.status();
                        let body: serde_json::Value = resp.json().await.unwrap_or_default();
                        if body.get("status").and_then(|s| s.as_str()) == Some("success") {
                            (true, String::new())
                        } else {
                            let msg = body.get("message").and_then(|m| m.as_str()).unwrap_or("Unknown");
                            (false, format!("{} (HTTP {})", msg, status))
                        }
                    }
                    Err(e) => (false, format!("Network: {}", e)),
                }
            } else {
                // Dhan: use /fundlimit as a lightweight auth check
                match http.get("https://api.dhan.co/v2/fundlimit")
                    .header("access-token", &acc.access_token)
                    .header("client-id", &acc.client_id)
                    .send().await
                {
                    Ok(resp) => {
                        let status = resp.status();
                        if status.is_success() {
                            (true, String::new())
                        } else {
                            let body: serde_json::Value = resp.json().await.unwrap_or_default();
                            let msg = body.get("remarks").and_then(|r| r.as_str())
                                .or_else(|| body.get("message").and_then(|m| m.as_str()))
                                .unwrap_or("Token expired or invalid");
                            (false, format!("{} (HTTP {})", msg, status))
                        }
                    }
                    Err(e) => (false, format!("Network: {}", e)),
                }
            };
            (acc.client_id, result)
        }));
    }

    for h in handles {
        if let Ok((client_id, (ok, error))) = h.await {
            health.insert(client_id, serde_json::json!({ "ok": ok, "error": error }));
        }
    }

    Json(serde_json::json!({ "health": health }))
}
