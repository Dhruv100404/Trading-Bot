use axum::{extract::{State, Query}, Json};
use serde::{Deserialize, Serialize};
use crate::api::AppState;

fn validate_date(date_str: &str) -> bool {
    chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d").is_ok()
}

#[derive(Deserialize)]
pub struct SignalsQuery { pub date: Option<String>, pub symbol: Option<String> }

pub async fn list(State(state): State<AppState>, Query(q): Query<SignalsQuery>) -> Json<serde_json::Value> {
    let date = q.date.unwrap_or_else(|| crate::types::today_ist().format("%Y-%m-%d").to_string());
    if !validate_date(&date) {
        return Json(serde_json::json!({ "error": "invalid date format, expected YYYY-MM-DD" }));
    }

    // Use HTTP interface to avoid native client UTF-8 issues with Array(String) containing ✓
    let client = reqwest::Client::new();
    let sql = if let Some(sym) = &q.symbol {
        format!(
            "SELECT id, symbol, direction, entry_price, entry_bucket, score, \
             arrayStringConcat(signals_fired, ' ') as signals_fired, \
             tp_price, sl_price, quantity, exit_price, exit_bucket, exit_reason, \
             actual_return_pct, pnl_rupees \
             FROM trading.signals FINAL WHERE trading_date = toDate('{}') AND symbol = '{}' \
             FORMAT JSON",
            date, sym.replace('\'', "\\'")
        )
    } else {
        format!(
            "SELECT id, symbol, direction, entry_price, entry_bucket, score, \
             arrayStringConcat(signals_fired, ' ') as signals_fired, \
             tp_price, sl_price, quantity, exit_price, exit_bucket, exit_reason, \
             actual_return_pct, pnl_rupees \
             FROM trading.signals FINAL WHERE trading_date = toDate('{}') \
             FORMAT JSON",
            date
        )
    };

    match client.post(&format!("{}/", state.ch_url))
        .body(sql)
        .send().await
    {
        Ok(resp) => {
            match resp.json::<serde_json::Value>().await {
                Ok(json) => {
                    let rows = json.get("data").cloned().unwrap_or(serde_json::json!([]));
                    Json(serde_json::json!({ "signals": rows }))
                }
                Err(e) => Json(serde_json::json!({ "signals": [], "error": e.to_string() })),
            }
        }
        Err(e) => Json(serde_json::json!({ "signals": [], "error": e.to_string() })),
    }
}
