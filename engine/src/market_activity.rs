use anyhow::{anyhow, Context, Result};
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, ACCEPT_LANGUAGE, CACHE_CONTROL, USER_AGENT};
use scraper::{Html, Selector};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::HashSet;

#[derive(Debug, Clone)]
pub struct MarketActivitySource {
    pub source: String,
    pub metric_type: String,
    pub exchange: String,
    pub index_name: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketActivityRow {
    pub row_id: String,
    pub snapshot_at: String,
    pub trading_date: String,
    pub source: String,
    pub metric_type: String,
    pub exchange: String,
    pub index_name: String,
    pub rank: u32,
    pub symbol: String,
    pub stock_name: String,
    pub moneycontrol_id: String,
    pub slug: String,
    pub price: f64,
    pub change_abs: f64,
    pub change_pct: f64,
    pub day_high: f64,
    pub day_low: f64,
    pub open: f64,
    pub prev_close: f64,
    pub volume: u64,
    pub avg_volume: u64,
    pub volume_multiplier: f64,
    pub volume_change_pct: f64,
    pub value_cr: f64,
    pub vwap: f64,
    pub direction: String,
    pub mcap_cr: f64,
    pub month_return_pct: f64,
    pub month3_return_pct: f64,
    pub share_url: String,
    pub source_url: String,
    pub fetched_at: String,
}

pub fn default_sources() -> Vec<MarketActivitySource> {
    vec![
        MarketActivitySource {
            source: "moneycontrol".to_string(),
            metric_type: "volume_shockers".to_string(),
            exchange: "NSE".to_string(),
            index_name: "all".to_string(),
            url: "https://www.moneycontrol.com/stocks/market-stats/volume-shockers-nse/"
                .to_string(),
        },
        MarketActivitySource {
            source: "moneycontrol".to_string(),
            metric_type: "most_active".to_string(),
            exchange: "NSE".to_string(),
            index_name: "all".to_string(),
            url: "https://www.moneycontrol.com/stocks/market-stats/most-active-stocks-nse/"
                .to_string(),
        },
    ]
}

pub fn configured_sources() -> Vec<MarketActivitySource> {
    let Ok(raw) = std::env::var("MARKET_ACTIVITY_SOURCES") else {
        return default_sources();
    };
    if raw.trim().is_empty() {
        return default_sources();
    }

    let sources: Vec<MarketActivitySource> = raw
        .split(';')
        .filter_map(|entry| {
            let parts: Vec<&str> = entry.split('|').map(str::trim).collect();
            if parts.len() != 5 {
                return None;
            }
            Some(MarketActivitySource {
                source: parts[0].to_string(),
                metric_type: parts[1].to_string(),
                exchange: parts[2].to_string(),
                index_name: parts[3].to_string(),
                url: parts[4].to_string(),
            })
        })
        .collect();

    if sources.is_empty() {
        tracing::warn!(
            "[MARKET-ACTIVITY] MARKET_ACTIVITY_SOURCES was set but no valid sources parsed; using defaults"
        );
        default_sources()
    } else {
        sources
    }
}

pub async fn fetch_market_activity(max_rows_per_source: usize) -> Result<Vec<MarketActivityRow>> {
    let mut headers = HeaderMap::new();
    headers.insert(
        USER_AGENT,
        HeaderValue::from_static(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
             (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        ),
    );
    headers.insert(
        ACCEPT,
        HeaderValue::from_static("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    );
    headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("en-US,en;q=0.9"));
    headers.insert(CACHE_CONTROL, HeaderValue::from_static("no-cache"));

    let client = reqwest::Client::builder()
        .default_headers(headers)
        .timeout(std::time::Duration::from_secs(20))
        .build()?;
    fetch_market_activity_from_sources(&client, &configured_sources(), max_rows_per_source).await
}

pub async fn fetch_market_activity_from_sources(
    client: &reqwest::Client,
    sources: &[MarketActivitySource],
    max_rows_per_source: usize,
) -> Result<Vec<MarketActivityRow>> {
    let mut rows = Vec::new();
    for source in sources {
        match fetch_moneycontrol_source(client, source, max_rows_per_source).await {
            Ok(mut fetched) => rows.append(&mut fetched),
            Err(err) => tracing::warn!(
                "[MARKET-ACTIVITY] source={} metric={} fetch failed: {}",
                source.source,
                source.metric_type,
                err
            ),
        }
    }

    Ok(rows)
}

async fn fetch_moneycontrol_source(
    client: &reqwest::Client,
    source: &MarketActivitySource,
    max_rows: usize,
) -> Result<Vec<MarketActivityRow>> {
    let body = client
        .get(&source.url)
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    parse_moneycontrol_activity_html(source, &body, max_rows)
}

pub fn parse_moneycontrol_activity_html(
    source: &MarketActivitySource,
    html: &str,
    max_rows: usize,
) -> Result<Vec<MarketActivityRow>> {
    let document = Html::parse_document(html);
    let selector = Selector::parse("script#__NEXT_DATA__")
        .map_err(|err| anyhow!("selector parse failed: {err:?}"))?;
    let script = document
        .select(&selector)
        .next()
        .map(|node| node.inner_html())
        .filter(|text| !text.trim().is_empty())
        .context("Moneycontrol __NEXT_DATA__ script not found")?;
    let json: Value =
        serde_json::from_str(&script).context("Moneycontrol __NEXT_DATA__ parse failed")?;
    parse_moneycontrol_activity_json(source, &json, max_rows)
}

pub fn parse_moneycontrol_activity_json(
    source: &MarketActivitySource,
    json: &Value,
    max_rows: usize,
) -> Result<Vec<MarketActivityRow>> {
    let snapshot_at = now_sql();
    let trading_date = crate::types::now_ist()
        .date_naive()
        .format("%Y-%m-%d")
        .to_string();
    let mut row_values = Vec::new();
    collect_json_object_rows(json, &mut row_values);

    let mut seen_symbols = HashSet::new();
    let mut out = Vec::new();
    for row in row_values {
        let symbol = json_string(row, &["symbol", "SYMBOL", "nseSymbol"])
            .unwrap_or_default()
            .trim()
            .to_ascii_uppercase();
        let stock_name =
            json_string(row, &["stockName", "companyName", "name"]).unwrap_or_default();
        if symbol.is_empty() || stock_name.is_empty() || !looks_like_activity_row(row) {
            continue;
        }
        if !seen_symbols.insert(symbol.clone()) {
            continue;
        }

        let rank = (out.len() + 1) as u32;
        let moneycontrol_id = json_string(row, &["scId", "sc_id", "id"]).unwrap_or_default();
        let slug = json_string(row, &["slug"]).unwrap_or_default();
        let share_url = json_string(row, &["shareUrl", "share_url"]).unwrap_or_default();
        let volume = json_u64(row, &["volume", "vol"]).unwrap_or(0);
        let avg_volume = json_u64(row, &["avgVol", "avgVolume", "averageVolume"]).unwrap_or(0);
        let volume_multiplier =
            json_f64(row, &["volMultiplier", "volumeMultiplier", "vol_mult"]).unwrap_or(0.0);
        let volume_change_pct =
            json_f64(row, &["volChg", "volumeChange", "volumeChangePct"]).unwrap_or(0.0);

        let row_id = hash_id(&[
            &source.source,
            &source.metric_type,
            &source.exchange,
            &snapshot_at,
            &rank.to_string(),
            &symbol,
        ]);

        out.push(MarketActivityRow {
            row_id,
            snapshot_at: snapshot_at.clone(),
            trading_date: trading_date.clone(),
            source: source.source.clone(),
            metric_type: source.metric_type.clone(),
            exchange: json_string(row, &["exchange", "ex"])
                .unwrap_or_else(|| source.exchange.clone())
                .to_ascii_uppercase(),
            index_name: source.index_name.clone(),
            rank,
            symbol,
            stock_name,
            moneycontrol_id,
            slug,
            price: round2(json_f64(row, &["currentPrice", "ltp", "price"]).unwrap_or(0.0)),
            change_abs: round2(
                json_f64(row, &["priceChange", "change", "netChange"]).unwrap_or(0.0),
            ),
            change_pct: round2(
                json_f64(row, &["perChange", "currPerChange", "pChange"]).unwrap_or(0.0),
            ),
            day_high: round2(json_f64(row, &["high", "dayHigh"]).unwrap_or(0.0)),
            day_low: round2(json_f64(row, &["low", "dayLow"]).unwrap_or(0.0)),
            open: round2(json_f64(row, &["open"]).unwrap_or(0.0)),
            prev_close: round2(json_f64(row, &["prevClose", "previousClose"]).unwrap_or(0.0)),
            volume,
            avg_volume,
            volume_multiplier: round2(volume_multiplier),
            volume_change_pct: round2(volume_change_pct),
            value_cr: round2(json_f64(row, &["value", "turnover", "tradedValue"]).unwrap_or(0.0)),
            vwap: round2(json_f64(row, &["vwap"]).unwrap_or(0.0)),
            direction: json_string(row, &["direction"]).unwrap_or_default(),
            mcap_cr: round2(json_f64(row, &["mcap", "marketCap"]).unwrap_or(0.0)),
            month_return_pct: round2(json_f64(row, &["monthReturn"]).unwrap_or(0.0)),
            month3_return_pct: round2(json_f64(row, &["month3Return"]).unwrap_or(0.0)),
            share_url,
            source_url: source.url.clone(),
            fetched_at: snapshot_at.clone(),
        });

        if out.len() >= max_rows {
            break;
        }
    }

    Ok(out)
}

fn looks_like_activity_row(row: &Value) -> bool {
    json_field(row, &["stockName"]).is_some()
        && json_field(row, &["symbol"]).is_some()
        && (json_field(row, &["volume"]).is_some()
            || json_field(row, &["volMultiplier"]).is_some()
            || json_field(row, &["value"]).is_some())
}

fn collect_json_object_rows<'a>(value: &'a Value, rows: &mut Vec<&'a Value>) {
    match value {
        Value::Array(items) => {
            for item in items {
                collect_json_object_rows(item, rows);
            }
        }
        Value::Object(map) => {
            rows.push(value);
            for child in map.values() {
                collect_json_object_rows(child, rows);
            }
        }
        _ => {}
    }
}

fn json_field<'a>(row: &'a Value, keys: &[&str]) -> Option<&'a Value> {
    let object = row.as_object()?;
    for key in keys {
        if let Some(value) = object.get(*key) {
            return Some(value);
        }
    }
    object.iter().find_map(|(actual, value)| {
        keys.iter()
            .any(|key| actual.eq_ignore_ascii_case(key))
            .then_some(value)
    })
}

fn json_string(row: &Value, keys: &[&str]) -> Option<String> {
    let value = json_field(row, keys)?;
    let raw = match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        _ => return None,
    };
    let cleaned = raw
        .replace('\u{00a0}', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    (!cleaned.is_empty() && cleaned != "-").then_some(cleaned)
}

fn json_f64(row: &Value, keys: &[&str]) -> Option<f64> {
    let value = json_field(row, keys)?;
    match value {
        Value::Number(value) => value.as_f64(),
        Value::String(value) => parse_number(value),
        _ => None,
    }
}

fn json_u64(row: &Value, keys: &[&str]) -> Option<u64> {
    let value = json_f64(row, keys)?;
    if value.is_finite() && value >= 0.0 {
        Some(value.round() as u64)
    } else {
        None
    }
}

fn parse_number(value: &str) -> Option<f64> {
    let cleaned: String = value
        .chars()
        .filter(|ch| ch.is_ascii_digit() || matches!(*ch, '.' | '-'))
        .collect();
    if cleaned.is_empty() || cleaned == "-" {
        None
    } else {
        cleaned.parse::<f64>().ok()
    }
}

fn now_sql() -> String {
    crate::types::now_ist()
        .format("%Y-%m-%d %H:%M:%S")
        .to_string()
}

fn hash_id(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        hasher.update(b"|");
    }
    hasher
        .finalize()
        .iter()
        .take(16)
        .map(|byte| format!("{:02x}", byte))
        .collect()
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_next_data_activity_rows() {
        let source = MarketActivitySource {
            source: "moneycontrol".to_string(),
            metric_type: "volume_shockers".to_string(),
            exchange: "NSE".to_string(),
            index_name: "all".to_string(),
            url: "https://example.com".to_string(),
        };
        let payload = serde_json::json!({
            "props": {
                "pageProps": {
                    "rows": [{
                        "stockName": "Example Industries",
                        "symbol": "EXAMPLE",
                        "currentPrice": "123.45",
                        "perChange": "2.4",
                        "volume": "1,25,000",
                        "avgVol": "25,000",
                        "volMultiplier": "5.0",
                        "value": "12.4"
                    }]
                }
            }
        });

        let rows = parse_moneycontrol_activity_json(&source, &payload, 20).unwrap();

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].symbol, "EXAMPLE");
        assert_eq!(rows[0].volume, 125000);
        assert_eq!(rows[0].volume_multiplier, 5.0);
    }
}
