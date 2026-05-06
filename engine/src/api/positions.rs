use axum::{extract::State, Json};
use crate::api::AppState;

/// Fetch live positions + fund balance from Dhan/Zerodha for ALL enabled accounts.
/// Returns per-account results with error field if API call fails (expired token etc).
/// Never fails the whole response — each account is independent.
pub async fn list(State(state): State<AppState>) -> Json<serde_json::Value> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct AccRow {
        name: String,
        client_id: String,
        access_token: String,
        broker: String,
        api_key: String,
        enabled: u8,
    }

    let accounts = state.ch.query(
        "SELECT name, client_id, access_token, broker, api_key, enabled \
         FROM trading.accounts \
         ORDER BY inserted_at DESC"
    ).fetch_all::<AccRow>().await.unwrap_or_default();

    let mut accounts_by_client: std::collections::HashMap<String, AccRow> = std::collections::HashMap::new();
    for acc in accounts {
        if acc.enabled != 1 { continue; }
        accounts_by_client.entry(acc.client_id.clone()).or_insert(acc);
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap();

    let mut results = vec![];

    for (_, acc) in accounts_by_client {
        if acc.broker == "ZERODHA" {
            // ── Zerodha: Kite Connect positions + margins ──
            let auth = format!("token {}:{}", acc.api_key, acc.access_token);

            let (positions, pos_error) = match client.get("https://api.kite.trade/portfolio/positions")
                .header("Authorization", &auth)
                .header("X-Kite-Version", "3")
                .send().await
            {
                Ok(resp) => {
                    let status = resp.status();
                    let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
                    if body.get("status").and_then(|s| s.as_str()) == Some("error") {
                        let msg = body.get("message").and_then(|m| m.as_str()).unwrap_or("Unknown error");
                        (serde_json::json!([]), Some(format!("Zerodha positions: {} (HTTP {})", msg, status)))
                    } else {
                        // Kite returns { status: "success", data: { net: [...], day: [...] } }
                        // Normalize to flat array of net positions for UI compatibility
                        let net = body.pointer("/data/net").cloned().unwrap_or(serde_json::json!([]));
                        let positions = normalize_kite_positions(&net);
                        (positions, None)
                    }
                }
                Err(e) => (serde_json::json!([]), Some(format!("Zerodha positions: {}", e))),
            };

            let (balance, bal_error) = match client.get("https://api.kite.trade/user/margins/equity")
                .header("Authorization", &auth)
                .header("X-Kite-Version", "3")
                .send().await
            {
                Ok(resp) => {
                    let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
                    if body.get("status").and_then(|s| s.as_str()) == Some("error") {
                        let msg = body.get("message").and_then(|m| m.as_str()).unwrap_or("Unknown error");
                        (serde_json::json!({}), Some(format!("Zerodha margins: {}", msg)))
                    } else {
                        let data = body.get("data").cloned().unwrap_or(serde_json::json!({}));
                        // Map Kite margin fields to Dhan-compatible shape for UI
                        let balance = serde_json::json!({
                            "availabelBalance": data.get("net").and_then(|n| n.as_f64()).unwrap_or(0.0),
                            "utilizedAmount": data.get("utilised").and_then(|u| u.pointer("/debits").and_then(|d| d.as_f64())).unwrap_or(0.0),
                        });
                        (balance, None)
                    }
                }
                Err(e) => (serde_json::json!({}), Some(format!("Zerodha margins: {}", e))),
            };

            let error = match (pos_error, bal_error) {
                (Some(a), Some(b)) => Some(format!("{}; {}", a, b)),
                (Some(a), None) | (None, Some(a)) => Some(a),
                (None, None) => None,
            };

            results.push(serde_json::json!({
                "client_id": acc.client_id,
                "name": acc.name,
                "broker": "ZERODHA",
                "balance": balance,
                "positions": positions,
                "error": error,
            }));
        } else {
            // ── Dhan ──
            let access_token = if !state.config.dhan_access_token.is_empty() {
                &state.config.dhan_access_token
            } else {
                &acc.access_token
            };
            let client_id = if !state.config.dhan_client_id.is_empty() {
                &state.config.dhan_client_id
            } else {
                &acc.client_id
            };
            let dhan_headers = |req: reqwest::RequestBuilder| -> reqwest::RequestBuilder {
                req.header("access-token", access_token)
                   .header("client-id", client_id)
            };

            let (positions, pos_error) = match dhan_headers(client.get("https://api.dhan.co/v2/positions")).send().await {
                Ok(resp) => {
                    let status = resp.status();
                    let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!([]));
                    if status.is_client_error() || status.is_server_error() {
                        let msg = body.get("remarks").and_then(|r| r.as_str())
                            .or_else(|| body.get("message").and_then(|m| m.as_str()))
                            .unwrap_or("API error");
                        (serde_json::json!([]), Some(format!("Dhan positions: {} (HTTP {})", msg, status)))
                    } else {
                        (body, None)
                    }
                }
                Err(e) => (serde_json::json!([]), Some(format!("Dhan positions: {}", e))),
            };

            let (balance, bal_error) = match dhan_headers(client.get("https://api.dhan.co/v2/fundlimit")).send().await {
                Ok(resp) => {
                    let status = resp.status();
                    let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
                    if status.is_client_error() || status.is_server_error() {
                        let msg = body.get("remarks").and_then(|r| r.as_str()).unwrap_or("API error");
                        (serde_json::json!({}), Some(format!("Dhan fund limit: {} (HTTP {})", msg, status)))
                    } else {
                        (body, None)
                    }
                }
                Err(e) => (serde_json::json!({}), Some(format!("Dhan fund limit: {}", e))),
            };

            let error = match (pos_error, bal_error) {
                (Some(a), Some(b)) => Some(format!("{}; {}", a, b)),
                (Some(a), None) | (None, Some(a)) => Some(a),
                (None, None) => None,
            };

            results.push(serde_json::json!({
                "client_id": client_id,
                "name": acc.name,
                "broker": "DHAN",
                "balance": balance,
                "positions": positions,
                "error": error,
            }));
        }
    }

    Json(serde_json::json!({ "accounts": results }))
}

/// Normalize Kite positions (net array) to Dhan-compatible shape for the UI.
/// Kite fields → Dhan-style fields used by the frontend.
fn normalize_kite_positions(net: &serde_json::Value) -> serde_json::Value {
    let arr = match net.as_array() {
        Some(a) => a,
        None => return serde_json::json!([]),
    };

    let positions: Vec<serde_json::Value> = arr.iter().map(|p| {
        let buy_qty = p.get("buy_quantity").and_then(|v| v.as_i64()).unwrap_or(0);
        let sell_qty = p.get("sell_quantity").and_then(|v| v.as_i64()).unwrap_or(0);
        let net_qty = p.get("quantity").and_then(|v| v.as_i64()).unwrap_or(0);
        let buy_avg = p.get("buy_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let sell_avg = p.get("sell_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let pnl = p.get("pnl").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let unrealised = p.get("unrealised").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let realised = p.get("realised").and_then(|v| v.as_f64()).unwrap_or(0.0);

        serde_json::json!({
            "tradingSymbol": p.get("tradingsymbol").and_then(|v| v.as_str()).unwrap_or(""),
            "securityId": p.get("instrument_token").and_then(|v| v.as_u64()).unwrap_or(0).to_string(),
            "positionType": if net_qty > 0 { "LONG" } else if net_qty < 0 { "SHORT" } else { "CLOSED" },
            "exchangeSegment": p.get("exchange").and_then(|v| v.as_str()).unwrap_or("NSE"),
            "productType": p.get("product").and_then(|v| v.as_str()).unwrap_or("MIS"),
            "buyAvg": buy_avg,
            "buyQty": buy_qty,
            "sellAvg": sell_avg,
            "sellQty": sell_qty,
            "netQty": net_qty,
            "realizedProfit": if net_qty == 0 { pnl } else { realised },
            "unrealizedProfit": if net_qty == 0 { 0.0 } else { unrealised },
            "costPrice": if buy_avg > 0.0 { buy_avg } else { sell_avg },
            "dayBuyValue": p.get("buy_value").and_then(|v| v.as_f64()).unwrap_or(0.0),
            "daySellValue": p.get("sell_value").and_then(|v| v.as_f64()).unwrap_or(0.0),
        })
    }).collect();

    serde_json::json!(positions)
}
