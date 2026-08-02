use anyhow::{Context, Result};
use clickhouse::{Client, Row};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;

const DHAN_SCRIP_MASTER_URL: &str =
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv";

#[derive(Debug, Deserialize)]
struct DhanInstrumentRow {
    #[serde(rename = "EXCH_ID", default)]
    exchange: String,
    #[serde(rename = "SEGMENT", default)]
    segment: String,
    #[serde(rename = "SECURITY_ID", default)]
    security_id: String,
    #[serde(rename = "UNDERLYING_SYMBOL", default)]
    symbol: String,
    #[serde(rename = "SYMBOL_NAME", default)]
    company_name: String,
    #[serde(rename = "DISPLAY_NAME", default)]
    display_name: String,
    #[serde(rename = "INSTRUMENT_TYPE", default)]
    instrument_type: String,
    #[serde(rename = "SERIES", default)]
    series: String,
}

#[derive(Debug, Row, Serialize)]
struct WatchlistSeedRow {
    security_id: String,
    symbol: String,
    company_name: String,
    tiers: Vec<String>,
    enabled: u8,
    min_volume: u32,
}

pub async fn seed_watchlist_if_empty(client: &Client) -> Result<usize> {
    let count = client
        .query("SELECT count() FROM trading.watchlist")
        .fetch_one::<u64>()
        .await
        .context("read watchlist count")?;
    if count > 0 {
        return Ok(0);
    }

    let volume_groups = load_volume_groups()?;
    let body = reqwest::Client::builder()
        .user_agent("swing-atlas/1.0")
        .timeout(std::time::Duration::from_secs(90))
        .build()?
        .get(DHAN_SCRIP_MASTER_URL)
        .send()
        .await
        .context("download Dhan instrument list")?
        .error_for_status()
        .context("Dhan instrument list status")?
        .text()
        .await
        .context("read Dhan instrument list")?;

    let rows = parse_watchlist_rows(&body, &volume_groups)?;
    if rows.len() < 100 {
        anyhow::bail!(
            "Dhan instrument list produced only {} liquid NSE rows; refusing partial seed",
            rows.len()
        );
    }

    let mut insert = client
        .insert("trading.watchlist")
        .context("create watchlist insert")?;
    for row in &rows {
        insert.write(row).await.context("write watchlist seed")?;
    }
    insert.end().await.context("commit watchlist seed")?;
    Ok(rows.len())
}

fn parse_watchlist_rows(
    csv_body: &str,
    volume_groups: &HashMap<String, String>,
) -> Result<Vec<WatchlistSeedRow>> {
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(csv_body.as_bytes());
    let mut rows = Vec::new();
    for parsed in reader.deserialize::<DhanInstrumentRow>() {
        let row = match parsed {
            Ok(row) => row,
            Err(err) => {
                tracing::debug!("[SCRIP-MASTER] skipped malformed CSV row: {err}");
                continue;
            }
        };
        if row.exchange.trim() != "NSE"
            || row.segment.trim() != "E"
            || row.instrument_type.trim() != "ES"
            || row.series.trim() != "EQ"
        {
            continue;
        }
        let symbol = row.symbol.trim().to_ascii_uppercase();
        let security_id = row.security_id.trim().to_string();
        let Some(bucket) = volume_groups.get(&symbol) else {
            continue;
        };
        if symbol.is_empty() || security_id.is_empty() || security_id == "0" {
            continue;
        }
        let company_name = if row.company_name.trim().is_empty() {
            row.display_name.trim().to_string()
        } else {
            row.company_name.trim().to_string()
        };
        rows.push(WatchlistSeedRow {
            security_id,
            symbol,
            company_name,
            tiers: vec![bucket.clone()],
            enabled: u8::from(matches!(bucket.as_str(), "MEGA" | "LARGE")),
            min_volume: 0,
        });
    }
    rows.sort_by(|left, right| left.symbol.cmp(&right.symbol));
    rows.dedup_by(|left, right| left.security_id == right.security_id);
    Ok(rows)
}

fn load_volume_groups() -> Result<HashMap<String, String>> {
    let path = ["data/volume_groups.json", "../data/volume_groups.json"]
        .into_iter()
        .find(|path| std::path::Path::new(path).exists())
        .context("data/volume_groups.json not found")?;
    let parsed: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(path).context("read volume groups")?)
            .context("parse volume groups")?;
    let groups = parsed
        .get("volume_groups")
        .and_then(serde_json::Value::as_object)
        .context("volume_groups object missing")?;
    let mut out = HashMap::new();
    for (label, symbols) in groups {
        let bucket = if label.contains("MEGA") {
            "MEGA"
        } else if label.contains("LARGE") {
            "LARGE"
        } else if label.contains("MID") {
            "MID"
        } else if label.contains("SMALL") {
            "SMALL"
        } else {
            continue;
        };
        if let Some(symbols) = symbols.as_array() {
            for symbol in symbols.iter().filter_map(serde_json::Value::as_str) {
                out.insert(symbol.to_ascii_uppercase(), bucket.to_string());
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_supported_nse_equities_and_enables_liquid_groups() {
        let csv = "EXCH_ID,SEGMENT,SECURITY_ID,UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES\n\
NSE,E,1333,HDFCBANK,HDFC Bank Limited,HDFC Bank,ES,EQ\n\
NSE,E,9999,TINY,Tiny Limited,Tiny,ES,EQ\n\
BSE,E,500180,HDFCBANK,HDFC Bank Limited,HDFC Bank,ES,EQ\n";
        let groups = HashMap::from([
            ("HDFCBANK".to_string(), "MEGA".to_string()),
            ("TINY".to_string(), "MID".to_string()),
        ]);
        let rows = parse_watchlist_rows(csv, &groups).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].symbol, "HDFCBANK");
        assert_eq!(rows[0].enabled, 1);
        assert_eq!(rows[1].symbol, "TINY");
        assert_eq!(rows[1].enabled, 0);
    }
}
