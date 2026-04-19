use axum::{extract::{State, Query}, Json, response::IntoResponse, http::header};
use serde::Deserialize;
use std::collections::HashMap;
use crate::api::AppState;
use crate::api::ch_http::query_json;

fn validate_date(date_str: &str) -> bool {
    chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d").is_ok()
}

#[derive(Deserialize)]
pub struct SnapshotsQuery { pub symbol: String, pub date: Option<String> }

#[derive(Deserialize)]
pub struct SnapshotsBulkQuery {
    pub from: Option<String>,
    pub to:   Option<String>,
}

/// Fast bulk endpoint: uses TabSeparatedWithNames — ~3x smaller than JSON, minimal parsing.
/// UI parses the TSV client-side which is fast in JS.
pub async fn list_all(State(state): State<AppState>, Query(q): Query<SnapshotsBulkQuery>) -> impl IntoResponse {
    let today = crate::types::today_ist().format("%Y-%m-%d").to_string();
    let from = q.from.unwrap_or_else(|| today.clone());
    let to   = q.to.unwrap_or_else(|| today.clone());
    if !validate_date(&from) || !validate_date(&to) {
        return (
            [(header::CONTENT_TYPE, "application/json")],
            r#"{"error":"invalid date format"}"#.to_string(),
        );
    }

    // Use JSONCompact: returns {meta, data, rows} — column names once + data as arrays
    // ~3x smaller than JSONEachRow for 100K+ rows
    let sql = format!(
        "SELECT toString(s.trading_date) AS trading_date, s.symbol, s.bucket, s.ltp, \
         s.candle_open, s.candle_high, s.candle_low, s.volume_cum, s.volume_delta, \
         s.vwap, s.volume_rate, s.candle_body_ratio \
         FROM trading.snapshots s \
         WHERE s.trading_date >= toDate('{from}') AND s.trading_date <= toDate('{to}') \
         ORDER BY s.trading_date, s.symbol, s.bucket \
         FORMAT JSONCompact",
        from = from, to = to
    );

    let client = reqwest::Client::new();
    let url = format!("{}/", state.ch_url);

    match client.post(&url).body(sql).send().await {
        Ok(resp) => {
            match resp.text().await {
                Ok(body) => {
                    // JSONCompact returns: {"meta":[...],"data":[[...],[...]],"rows":N}
                    // We wrap it as: {"snapshots_compact": <the whole response>}
                    // UI will parse columns from meta + data arrays
                    let mut out = String::with_capacity(body.len() + 30);
                    out.push_str("{\"snapshots_compact\":");
                    out.push_str(&body);
                    out.push('}');
                    (
                        [(header::CONTENT_TYPE, "application/json")],
                        out,
                    )
                }
                Err(e) => (
                    [(header::CONTENT_TYPE, "application/json")],
                    format!("{{\"error\":\"{}\"}}", e),
                ),
            }
        }
        Err(e) => (
            [(header::CONTENT_TYPE, "application/json")],
            format!("{{\"error\":\"{}\"}}", e),
        ),
    }
}

pub async fn list(State(state): State<AppState>, Query(q): Query<SnapshotsQuery>) -> Json<serde_json::Value> {
    let date = q.date.unwrap_or_else(|| crate::types::today_ist().format("%Y-%m-%d").to_string());
    if !validate_date(&date) {
        return Json(serde_json::json!({ "error": "invalid date format, expected YYYY-MM-DD" }));
    }
    let client = reqwest::Client::new();
    let mut params = HashMap::new();
    params.insert("date".to_string(), date.clone());
    params.insert("symbol".to_string(), q.symbol.clone());
    match query_json(
        &client,
        &state.ch_url,
        "SELECT bucket, ltp, candle_open, candle_high, candle_low, volume_cum, volume_delta, \
         vwap, volume_rate, candle_body_ratio \
         FROM trading.snapshots WHERE trading_date = toDate({date:String}) AND symbol = {symbol:String} ORDER BY bucket",
        params,
    ).await {
        Ok(rows) => Json(serde_json::json!({ "snapshots": rows })),
        Err(e) => Json(serde_json::json!({ "error": e.to_string() })),
    }
}
