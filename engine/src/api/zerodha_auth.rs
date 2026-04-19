use axum::{extract::{State, Query}, response::{Html, Redirect}};
use serde::Deserialize;
use sha2::{Sha256, Digest};
use std::collections::HashMap;
use crate::api::AppState;
use crate::api::ch_http::{query_json, ch_exec};

/// GET /api/zerodha/login?client_id=XYZ
/// Returns the Kite login URL for a specific Zerodha account.
/// The UI opens this in a new window — user logs in — Zerodha redirects back to our callback.
#[derive(Deserialize)]
pub struct LoginQuery {
    pub client_id: String,
}

pub async fn login_url(State(state): State<AppState>, Query(q): Query<LoginQuery>) -> axum::response::Result<Redirect> {
    let http = reqwest::Client::new();
    let mut params = HashMap::new();
    params.insert("cid".to_string(), q.client_id.clone());

    let rows = query_json(
        &http, &state.ch_url,
        "SELECT api_key FROM trading.accounts FINAL WHERE client_id = {cid:String} AND broker = 'ZERODHA' LIMIT 1",
        params,
    ).await.map_err(|e| format!("DB error: {}", e))?;

    let api_key = rows.first()
        .and_then(|r| r.get("api_key"))
        .and_then(|v| v.as_str())
        .ok_or("Zerodha account not found or missing api_key")?;

    let url = format!("https://kite.zerodha.com/connect/login?v=3&api_key={}", api_key);
    Ok(Redirect::temporary(&url))
}

/// GET /api/zerodha/callback?request_token=XXX&action=login&status=success
/// Called by Zerodha after user login. Exchanges request_token for access_token.
/// This is the redirect URL you register in Zerodha developer console:
///   http://<your-ec2-ip>:3000/api/zerodha/callback
#[derive(Deserialize)]
pub struct CallbackQuery {
    pub request_token: Option<String>,
    pub action: Option<String>,
    pub status: Option<String>,
}

pub async fn callback(State(state): State<AppState>, Query(q): Query<CallbackQuery>) -> Html<String> {
    // Check for error from Zerodha
    if q.status.as_deref() != Some("success") || q.request_token.is_none() {
        return Html(error_page("Login failed or was cancelled by user."));
    }

    let request_token = q.request_token.unwrap();
    let http = reqwest::Client::new();

    // Find ALL Zerodha accounts — we don't know which one logged in yet.
    // After token exchange, the response tells us the user_id which we match to client_id.
    let rows = match query_json(
        &http, &state.ch_url,
        "SELECT name, client_id, api_key, api_secret, access_token FROM trading.accounts FINAL WHERE broker = 'ZERODHA'",
        HashMap::new(),
    ).await {
        Ok(r) => r,
        Err(e) => return Html(error_page(&format!("DB error: {}", e))),
    };

    if rows.is_empty() {
        return Html(error_page("No Zerodha accounts found. Add one in the Accounts page first."));
    }

    // Try token exchange with each Zerodha account's api_key + api_secret
    let mut matched_client_id = String::new();
    let mut matched_name = String::new();
    let mut new_token = String::new();
    let mut last_error = String::new();

    for row in &rows {
        let api_key = row.get("api_key").and_then(|v| v.as_str()).unwrap_or("");
        let api_secret = row.get("api_secret").and_then(|v| v.as_str()).unwrap_or("");
        let client_id = row.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
        let name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");

        if api_key.is_empty() || api_secret.is_empty() {
            continue;
        }

        // Checksum = SHA256(api_key + request_token + api_secret)
        let mut hasher = Sha256::new();
        hasher.update(api_key.as_bytes());
        hasher.update(request_token.as_bytes());
        hasher.update(api_secret.as_bytes());
        let checksum = format!("{:x}", hasher.finalize());

        // Exchange request_token for access_token
        let resp = match http.post("https://api.kite.trade/session/token")
            .form(&[
                ("api_key", api_key),
                ("request_token", request_token.as_str()),
                ("checksum", checksum.as_str()),
            ])
            .send().await
        {
            Ok(r) => r,
            Err(e) => {
                last_error = format!("Network error: {}", e);
                continue;
            }
        };

        let body: serde_json::Value = match resp.json().await {
            Ok(b) => b,
            Err(e) => {
                last_error = format!("Parse error: {}", e);
                continue;
            }
        };

        if body.get("status").and_then(|s| s.as_str()) == Some("success") {
            if let Some(data) = body.get("data") {
                new_token = data.get("access_token")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();

                if !new_token.is_empty() {
                    matched_client_id = client_id.to_string();
                    matched_name = name.to_string();
                    break;
                }
            }
        } else {
            let msg = body.get("message").and_then(|m| m.as_str()).unwrap_or("Unknown error");
            last_error = format!("Kite: {}", msg);
        }
    }

    if new_token.is_empty() {
        return Html(error_page(&format!("Token exchange failed: {}", last_error)));
    }

    // Update access_token in DB (insert new row — ReplacingMergeTree handles dedup)
    let row = rows.iter().find(|r| r.get("client_id").and_then(|v| v.as_str()) == Some(&matched_client_id));
    if let Some(row) = row {
        let mut wp = HashMap::new();
        wp.insert("name".to_string(), matched_name.clone());
        wp.insert("cid".to_string(), matched_client_id.clone());
        wp.insert("token".to_string(), new_token);
        wp.insert("api_key".to_string(), row.get("api_key").and_then(|v| v.as_str()).unwrap_or("").to_string());
        wp.insert("api_secret".to_string(), row.get("api_secret").and_then(|v| v.as_str()).unwrap_or("").to_string());

        if let Err(e) = ch_exec(
            &http, &state.ch_url,
            "INSERT INTO trading.accounts (name, client_id, access_token, broker, api_key, api_secret, mode, enabled) \
             VALUES ({name:String}, {cid:String}, {token:String}, 'ZERODHA', {api_key:String}, {api_secret:String}, 'LIVE', 1)",
            wp,
        ).await {
            return Html(error_page(&format!("DB update failed: {}", e)));
        }

        let _ = ch_exec(&http, &state.ch_url, "OPTIMIZE TABLE trading.accounts FINAL", HashMap::new()).await;
    }

    // Success — redirect to accounts page
    Html(success_page(&matched_name, &matched_client_id))
}

fn error_page(msg: &str) -> String {
    format!(r#"<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Zerodha Login</title>
<style>body{{font-family:system-ui;background:#0D0F14;color:#FF5252;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.box{{background:#151821;border:1px solid #FF5252;border-radius:12px;padding:32px;max-width:480px;text-align:center}}
h2{{margin:0 0 12px}}p{{color:#8a8f9e;font-size:14px}}a{{color:#2979FF;text-decoration:none}}</style></head>
<body><div class="box"><h2>Login Failed</h2><p>{}</p><br><a href="/">Back to Dashboard</a></div></body></html>"#, msg)
}

fn success_page(name: &str, client_id: &str) -> String {
    format!(r#"<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Zerodha Login</title>
<meta http-equiv="refresh" content="3;url=/">
<style>body{{font-family:system-ui;background:#0D0F14;color:#00E676;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.box{{background:#151821;border:1px solid #00E676;border-radius:12px;padding:32px;max-width:480px;text-align:center}}
h2{{margin:0 0 12px}}p{{color:#8a8f9e;font-size:14px}}a{{color:#2979FF;text-decoration:none}}</style></head>
<body><div class="box"><h2>Connected!</h2><p>Zerodha account <b>{}</b> ({}) is now linked.<br>Access token updated. Redirecting...</p><br><a href="/">Go to Dashboard</a></div></body></html>"#, name, client_id)
}
