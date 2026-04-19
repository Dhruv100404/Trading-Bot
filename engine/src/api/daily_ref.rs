use axum::{extract::{State, Query}, Json};
use serde::Deserialize;
use std::collections::HashMap;
use crate::api::AppState;
use crate::db::snapshots as snap_db;

fn validate_date(date_str: &str) -> bool {
    chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d").is_ok()
}

#[derive(Deserialize)]
pub struct DailyRefQuery {
    pub symbol: String,
    pub date: Option<String>,
}

#[derive(Deserialize)]
pub struct DailyRefBulkQuery {
    pub from: Option<String>,
    pub to:   Option<String>,
}

/// API: return daily_ref data computed from snapshots table.
/// Computes: prev_close (prev day's bucket 375), day_open (today's bucket 1),
/// gap_pct, closing_price (today's bucket 375 ltp).
pub async fn get_all(
    State(state): State<AppState>,
    Query(q): Query<DailyRefBulkQuery>,
) -> Json<serde_json::Value> {
    let today = crate::types::today_ist().format("%Y-%m-%d").to_string();
    let from = q.from.unwrap_or_else(|| today.clone());
    let to   = q.to.unwrap_or_else(|| today.clone());
    if !validate_date(&from) || !validate_date(&to) {
        return Json(serde_json::json!({ "error": "invalid date format, expected YYYY-MM-DD" }));
    }

    // Fetch snapshots for the range + lookback 7 days (for prev_close)
    let from_date = match chrono::NaiveDate::parse_from_str(&from, "%Y-%m-%d") {
        Ok(d) => d - chrono::Duration::days(7),
        Err(_) => return Json(serde_json::json!({ "error": "invalid from date" })),
    };
    let to_date = match chrono::NaiveDate::parse_from_str(&to, "%Y-%m-%d") {
        Ok(d) => d,
        Err(_) => return Json(serde_json::json!({ "error": "invalid to date" })),
    };

    #[derive(clickhouse::Row, serde::Deserialize)]
    struct RefRow {
        trading_date: u16,
        symbol: String,
        max_b: Option<u16>,
        day_open: Option<f32>,
        closing_price: Option<f32>,
    }
    let epoch = chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();

    let rows = match state.ch.query(
        "SELECT trading_date, symbol, max(bucket) as max_b, \
         argMinIf(ltp, bucket, bucket = 1) as day_open, \
         argMaxIf(ltp, bucket, bucket = 375) as closing_price \
         FROM trading.snapshots \
         WHERE trading_date >= toDate(?) AND trading_date <= toDate(?) AND bucket <= 375 \
         GROUP BY trading_date, symbol \
         ORDER BY trading_date, symbol"
    ).bind(from_date.format("%Y-%m-%d").to_string())
      .bind(to_date.format("%Y-%m-%d").to_string())
      .fetch_all::<RefRow>().await {
        Ok(r) => r,
        Err(e) => return Json(serde_json::json!({ "error": e.to_string() })),
    };

    // Build HashMap<date, HashMap<symbol, (day_open, closing_price, max_bucket)>>
    let mut by_date: HashMap<String, HashMap<String, (f32, f32, u16)>> = HashMap::new();
    for r in &rows {
        let date_str = (epoch + chrono::Duration::days(r.trading_date as i64)).format("%Y-%m-%d").to_string();
        by_date.entry(date_str).or_default()
            .insert(r.symbol.clone(), (r.day_open.unwrap_or(0.0), r.closing_price.unwrap_or(0.0), r.max_b.unwrap_or(0)));
    }

    // For each date, compute gap_pct and prev_close
    let mut results: Vec<serde_json::Value> = Vec::new();
    let mut dates: Vec<String> = by_date.keys().cloned().collect();
    dates.sort();

    for date_str in &dates {
        let date = match chrono::NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
            Ok(d) => d,
            Err(_) => continue,
        };
        let sym_map = match by_date.get(date_str) {
            Some(m) => m,
            None => continue,
        };

        // Find previous trading day with bucket 375
        let prev_close = dates.iter()
            .filter(|d| *d < date_str)
            .filter_map(|pd| by_date.get(pd))
            .filter_map(|pm| pm.get(date_str))
            .filter(|&&(_, _, mb)| mb >= 375)
            .map(|&(_, cp, _)| cp)
            .last()
            .unwrap_or(0.0);

        for (symbol, &(day_open, closing_price, max_b)) in sym_map {
            let gap_pct = if prev_close > 0.0 && day_open > 0.0 {
                (day_open - prev_close) / prev_close * 100.0
            } else { 0.0 };

            results.push(serde_json::json!({
                "trading_date": date_str,
                "symbol": symbol,
                "day_open": day_open,
                "prev_close": prev_close,
                "closing_price": closing_price,
                "gap_pct": gap_pct,
                "max_bucket": max_b,
            }));
        }
    }

    Json(serde_json::json!({ "daily_refs": results }))
}

/// API: return daily_ref data for a single symbol/date.
pub async fn get(
    State(state): State<AppState>,
    Query(q): Query<DailyRefQuery>,
) -> Json<serde_json::Value> {
    let date = q.date.unwrap_or_else(|| {
        crate::types::today_ist().format("%Y-%m-%d").to_string()
    });
    if !validate_date(&date) {
        return Json(serde_json::json!({ "error": "invalid date format, expected YYYY-MM-DD" }));
    }
    let trading_date = match chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d") {
        Ok(d) => d,
        Err(_) => return Json(serde_json::json!({ "error": "invalid date" })),
    };

    #[derive(clickhouse::Row, serde::Deserialize)]
    struct RefRow {
        trading_date: u16,
        symbol: String,
        max_b: Option<u16>,
        day_open: Option<f32>,
        closing_price: Option<f32>,
    }
    let epoch = chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();

    let rows = match state.ch.query(
        "SELECT trading_date, symbol, max(bucket) as max_b, \
         argMinIf(ltp, bucket, bucket = 1) as day_open, \
         argMaxIf(ltp, bucket, bucket = 375) as closing_price \
         FROM trading.snapshots \
         WHERE symbol = ? AND bucket <= 375 \
         GROUP BY trading_date, symbol \
         ORDER BY trading_date"
    ).bind(&q.symbol)
      .fetch_all::<RefRow>().await {
        Ok(r) => r,
        Err(e) => return Json(serde_json::json!({ "error": e.to_string() })),
    };

    let row = rows.iter().find(|r| {
        epoch + chrono::Duration::days(r.trading_date as i64) == trading_date
    });

    if let Some(r) = row {
        let td = epoch + chrono::Duration::days(r.trading_date as i64);
        let date_str = td.format("%Y-%m-%d").to_string();
        let day_open = r.day_open.unwrap_or(0.0);

        // prev_close: previous trading day's bucket 375 ltp
        let prev_close = rows.iter()
            .filter(|x| epoch + chrono::Duration::days(x.trading_date as i64) < trading_date)
            .filter(|x| x.max_b.unwrap_or(0) >= 375)
            .max_by_key(|x| x.trading_date)
            .and_then(|pr| pr.closing_price)
            .unwrap_or(0.0);

        let closing_price = r.closing_price.unwrap_or(0.0);
        let gap_pct = if prev_close > 0.0 && day_open > 0.0 {
            (day_open - prev_close) / prev_close * 100.0
        } else { 0.0 };

        Json(serde_json::json!({
            "daily_ref": {
                "trading_date": date_str,
                "symbol": r.symbol,
                "day_open": day_open,
                "prev_close": prev_close,
                "closing_price": closing_price,
                "gap_pct": gap_pct,
            }
        }))
    } else {
        Json(serde_json::json!({ "daily_ref": null }))
    }
}
