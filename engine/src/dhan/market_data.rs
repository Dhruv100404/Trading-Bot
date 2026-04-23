// Verified against actual Dhan API response on 2026-03-24.
// Response: {"status":"success","data":{"NSE_EQ":{"1333":{"last_price":755,"ohlc":{...},...}}}}
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use anyhow::Result;
use crate::dhan::client::DhanClient;

#[derive(Debug, Deserialize, Default, Clone)]
pub struct QuoteOhlc {
    #[serde(default)] pub open: f32,
    #[serde(default)] pub high: f32,
    #[serde(default)] pub low: f32,
    #[serde(default)] pub close: f32,  // previous day's EOD closing price
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, Default, Clone)]
pub struct DepthLevel {
    #[serde(default)] pub price: f32,
    #[serde(default)] pub quantity: u32,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Depth {
    #[serde(default)] pub buy: Vec<DepthLevel>,
    #[serde(default)] pub sell: Vec<DepthLevel>,
}

#[derive(Debug, Deserialize, Default, Clone)]
pub struct QuoteItem {
    #[serde(default)] pub last_price: f32,
    #[serde(default)] pub ohlc: QuoteOhlc,
    #[serde(default)] pub volume: u64,
    #[allow(dead_code)] #[serde(default)] pub oi: u64,
    #[allow(dead_code)] #[serde(default)] pub buy_quantity: u32,
    #[allow(dead_code)] #[serde(default)] pub sell_quantity: u32,
    #[allow(dead_code)] #[serde(default)] pub depth: Depth,
}

impl QuoteItem {
    pub fn open(&self) -> f32 { self.ohlc.open }
    pub fn high(&self) -> f32 { self.ohlc.high }
    pub fn low(&self) -> f32 { self.ohlc.low }
    pub fn close(&self) -> f32 { self.ohlc.close }
}

#[derive(Debug, Serialize)]
struct QuoteRequest {
    #[serde(rename = "NSE_EQ", skip_serializing_if = "Vec::is_empty")]
    nse_eq: Vec<u64>,
}

/// Fetch quotes for a batch of security IDs (max 1000 per call).
/// Dhan requires security IDs sent as integers, not strings.
/// Retries up to 2 times on 429/5xx with exponential backoff.
pub async fn fetch_quotes(
    client: &DhanClient,
    security_ids: &[String],
    endpoint: &str,
) -> Result<HashMap<String, QuoteItem>> {
    let ids: Vec<u64> = security_ids.iter()
        .filter_map(|s| s.parse::<u64>().ok())
        .collect();
    let req_body = QuoteRequest { nse_eq: ids };

    let mut last_err = String::new();
    for attempt in 0..3u32 {
        if attempt > 0 {
            let backoff = std::time::Duration::from_secs(2u64.pow(attempt));
            tracing::warn!("[QUOTE] Retry {}/2 after {}s backoff", attempt, backoff.as_secs());
            tokio::time::sleep(backoff).await;
        }

        let resp = match client.post(endpoint).json(&req_body).send().await {
            Ok(r) => r,
            Err(e) => { last_err = format!("request error: {}", e); continue; }
        };

        let http_status = resp.status().as_u16();
        let text = resp.text().await.unwrap_or_default();

        if http_status == 429 {
            tracing::warn!("[QUOTE] 429 rate limited (attempt {})", attempt + 1);
            last_err = "429 rate limited".into();
            continue;
        }
        if http_status >= 500 {
            tracing::warn!("[QUOTE] Server error {} (attempt {})", http_status, attempt + 1);
            last_err = format!("server error {}", http_status);
            continue;
        }

        if client.debug {
            let preview: String = text.chars().take(500).collect();
            tracing::debug!("Quote API raw response: {}", preview);
        }

        let v: serde_json::Value = serde_json::from_str(&text).map_err(|e| {
            let preview: String = text.chars().take(300).collect();
            anyhow::anyhow!("JSON parse error: {} | body: {}", e, preview)
        })?;

        let status = v["status"].as_str().unwrap_or("unknown");
        if status != "success" {
            let msg: String = v["data"].to_string().chars().take(200).collect();
            return Err(anyhow::anyhow!("Dhan API status={} | {}", status, msg));
        }

        let mut result = HashMap::new();
        if let Some(segments) = v["data"].as_object() {
            for (_segment, items) in segments {
                if let Some(items_map) = items.as_object() {
                    for (sec_id, item_val) in items_map {
                        match serde_json::from_value::<QuoteItem>(item_val.clone()) {
                            Ok(q) => { result.insert(sec_id.clone(), q); }
                            Err(e) => {
                                tracing::warn!("Skipping sec_id={}: parse error: {}", sec_id, e);
                            }
                        }
                    }
                }
            }
        }
        return Ok(result);
    }

    Err(anyhow::anyhow!("fetch_quotes failed after 3 attempts: {}", last_err))
}

/// 1-minute intraday candle from Dhan intraday charts API.
#[derive(Debug, Deserialize)]
pub struct IntradayCandle {
    pub timestamp: i64,
    pub open: f32,
    pub high: f32,
    pub low: f32,
    pub close: f32,
    #[serde(default)]
    pub volume: f64,
}

/// Intraday historical response wrapper.
#[derive(Debug, Deserialize)]
pub struct IntradayResponse {
    #[serde(rename = "open", default)]
    pub open: Vec<f64>,
    #[serde(rename = "high", default)]
    pub high: Vec<f64>,
    #[serde(rename = "low", default)]
    pub low: Vec<f64>,
    #[serde(rename = "close", default)]
    pub close: Vec<f64>,
    #[serde(rename = "timestamp", default)]
    pub timestamp: Vec<i64>,
    #[serde(rename = "volume", default)]
    pub volume: Vec<f64>,
}

/// Fetch 1-minute intraday candles for a security between two dates.
/// Returns all candles; caller splits by date and converts to snapshot buckets.
pub async fn fetch_intraday_candles(
    client: &DhanClient,
    security_id: &str,
    from_date: &str, // "YYYY-MM-DD"
    to_date: &str,   // "YYYY-MM-DD"
) -> Result<IntradayResponse> {
    #[derive(Serialize)]
    struct Req<'a> {
        #[serde(rename = "securityId")]
        security_id: &'a str,
        #[serde(rename = "exchangeSegment")]
        exchange_segment: &'a str,
        instrument: &'a str,
        expiry_code: i32,
        #[serde(rename = "fromDate")]
        from_date: &'a str,
        #[serde(rename = "toDate")]
        to_date: &'a str,
    }

    let req_body = Req {
        security_id,
        exchange_segment: "NSE_EQ",
        instrument: "EQUITY",
        expiry_code: 0,
        from_date,
        to_date,
    };

    let mut last_err = String::new();
    for attempt in 0..3u32 {
        if attempt > 0 {
            let backoff = std::time::Duration::from_secs(2u64.pow(attempt));
            tokio::time::sleep(backoff).await;
        }

        let resp = match client.post("v2/charts/intraday").json(&req_body).send().await {
            Ok(r) => r,
            Err(e) => { last_err = format!("request error: {}", e); continue; }
        };

        let http_status = resp.status().as_u16();

        if http_status == 429 || http_status == 400 {
            let body = resp.text().await.unwrap_or_default();
            tracing::warn!("[INTRADAY] HTTP {} (attempt {}): {}", http_status, attempt + 1, &body[..body.len().min(200)]);
            last_err = format!("HTTP {}", http_status);
            continue;
        }
        if http_status >= 500 {
            last_err = format!("server error {}", http_status);
            continue;
        }

        let data: IntradayResponse = match resp.json().await {
            Ok(d) => d,
            Err(e) => { last_err = format!("json error: {}", e); continue; }
        };

        return Ok(data);
    }

    Err(anyhow::anyhow!("fetch_intraday_candles failed after 3 attempts: {}", last_err))
}
