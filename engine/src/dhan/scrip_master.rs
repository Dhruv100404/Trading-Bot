use crate::config::Config;
use clickhouse::Client as ChClient;
use anyhow::Result;
use std::collections::HashSet;

const RECOMMENDED_WATCHLIST_JSON: &str = include_str!("../../../data/recommended-watchlist.json");
const MARGIN_STOCKS_JSON: &str = include_str!("../../../data/margin-stocks.json");
const LIQUID_5L_JSON: &str = include_str!("../../../data/liquid-5l-symbols.json");

const SCRIP_MASTER_URL: &str =
    "https://images.dhan.co/api-data/api-scrip-master.csv";
const DATE_FILE: &str = "/tmp/last_scrip_date";

// Nifty 50 symbols (as of 2026 Q1 — update quarterly)
const NIFTY50: &[&str] = &[
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJAJFINSV","BAJFINANCE","BHARTIARTL","BPCL",
    "BRITANNIA","CIPLA","COALINDIA","DIVISLAB","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT",
    "M&M","MARUTI","NESTLEIND","NTPC","ONGC",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS",
    "TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
];

// Nifty 500 symbols — the implementer should populate this list before first use.
// Source: https://www.niftyindices.com/indices/equity/broad-based-indices/nifty500
// Download the CSV → extract SYMBOL column → paste as &str entries below.
// Known gap: if empty, Nifty500 tier toggle has no effect. This must be resolved before
// production use. Populate once and update quarterly.
const NIFTY500: &[&str] = &[];  // TODO: populate from NSE NIFTY500 CSV

/// Sync scrip master only when needed.
/// - On startup: skips if watchlist already has rows (survives container restarts).
/// - At midnight: forced=true bypasses the row-count check so we pick up new listings.
pub async fn sync_if_needed(ch: &ChClient, config: &Config) -> Result<()> {
    sync_inner(ch, config, false).await
}

pub async fn force_sync(ch: &ChClient, config: &Config) -> Result<()> {
    sync_inner(ch, config, true).await
}

async fn sync_inner(ch: &ChClient, config: &Config, forced: bool) -> Result<()> {
    if !forced {
        // Check ClickHouse: if watchlist already has rows, scrip master was already run today.
        // This survives container restarts without needing a persistent file.
        #[derive(clickhouse::Row, serde::Deserialize)]
        struct CountRow { cnt: u64 }
        let count = ch.query("SELECT count() AS cnt FROM trading.watchlist")
            .fetch_one::<CountRow>().await.map(|r| r.cnt).unwrap_or(0);
        if count > 0 {
            tracing::info!("Scrip master skipped — watchlist already has {} rows", count);
            return Ok(());
        }
    }
    download_and_populate(ch, config).await?;
    // Keep the date file as a secondary guard for same-session duplicate calls
    let today = crate::types::today_ist().to_string();
    let _ = std::fs::write(DATE_FILE, &today);
    Ok(())
}

// _config reserved for future proxy/auth settings
async fn download_and_populate(ch: &ChClient, _config: &Config) -> Result<()> {
    tracing::info!("Downloading scrip master from Dhan...");
    let resp = reqwest::get(SCRIP_MASTER_URL).await?.error_for_status()?;
    let body = resp.text().await?;

    let nifty50_set: HashSet<&str> = NIFTY50.iter().copied().collect();
    let nifty500_set: HashSet<&str> = NIFTY500.iter().copied().collect();

    // Parse recommended watchlist for Tier1/Tier2
    let (tier1_set, tier2_set) = parse_recommended_tiers();

    // Parse margin stocks (4-10x leverage) for Margin4x tier
    let margin4x_set = parse_margin_stocks();

    // Parse liquid-5l symbols for Liquid5L tier
    let liquid5l_set = parse_liquid5l_symbols();

    let mut rdr = csv::Reader::from_reader(body.as_bytes());
    let records: Vec<csv::StringRecord> = rdr.records().filter_map(|r| r.ok()).collect();

    // Column mapping (0-indexed) from actual CSV header:
    // 0: SEM_EXM_EXCH_ID, 1: SEM_SEGMENT, 2: SEM_SMST_SECURITY_ID,
    // 3: SEM_INSTRUMENT_NAME, 4: SEM_EXPIRY_CODE, 5: SEM_TRADING_SYMBOL,
    // ...13: SEM_EXCH_INSTRUMENT_TYPE, 14: SEM_SERIES, 15: SM_SYMBOL_NAME

    // Pass 1: collect F&O base symbols from FUTSTK rows
    // FUTSTK trading symbols are like "RELIANCE-Mar2026-FUT"; strip the suffix to get base symbol.
    let mut fo_symbols: HashSet<String> = HashSet::new();
    for record in &records {
        let exchange   = record.get(0).unwrap_or("").trim();
        let instrument = record.get(3).unwrap_or("").trim();
        let raw_symbol = record.get(5).unwrap_or("").trim();
        if exchange == "NSE" && instrument == "FUTSTK" && !raw_symbol.is_empty() {
            let base = raw_symbol.split('-').next().unwrap_or(raw_symbol).to_string();
            fo_symbols.insert(base);
        }
    }

    // Pass 2: process EQ rows and tag tiers
    let mut rows: Vec<WatchlistRow> = vec![];
    for record in &records {
        let security_id = record.get(2).unwrap_or("").trim().to_string();
        let exchange    = record.get(0).unwrap_or("").trim();
        let series      = record.get(14).unwrap_or("").trim();
        let symbol      = record.get(5).unwrap_or("").trim().to_string();
        let company     = record.get(15).unwrap_or("").trim().to_string();

        if security_id.is_empty() || symbol.is_empty() { continue; }
        if exchange != "NSE" || series != "EQ" { continue; }

        let mut tiers: Vec<String> = vec!["AllNSE".into()];
        if fo_symbols.contains(&symbol) { tiers.push("F&O".into()); }
        if nifty50_set.contains(symbol.as_str()) {
            tiers.push("Nifty50".into());
            tiers.push("Nifty500".into());
        } else if nifty500_set.contains(symbol.as_str()) {
            tiers.push("Nifty500".into());
        }
        if tier1_set.contains(symbol.as_str()) { tiers.push("Tier1".into()); }
        if tier2_set.contains(symbol.as_str()) { tiers.push("Tier2".into()); }
        if margin4x_set.contains(symbol.as_str()) { tiers.push("Margin4x".into()); }
        if liquid5l_set.contains(symbol.as_str()) { tiers.push("Liquid5L".into()); }
        // NSEActive is always included — enabling this tier in tier_state activates ALL NSE EQ stocks
        tiers.push("NSEActive".into());

        rows.push(WatchlistRow { security_id, symbol, company, tiers });
    }

    tracing::info!("Parsed {} NSE EQ instruments from scrip master", rows.len());

    // Get active tiers from DB to determine initial enabled state
    let active_tiers = get_active_tiers(ch).await?;

    // Batch insert to watchlist (1000 at a time)
    for chunk in rows.chunks(1000) {
        let mut ins = ch.insert("trading.watchlist")?;
        for row in chunk {
            let enabled: u8 = if row.tiers.iter().any(|t| active_tiers.contains(t)) { 1 } else { 0 };
            ins.write(&WatchlistClickhouseRow {
                security_id: row.security_id.clone(),
                symbol: row.symbol.clone(),
                company_name: row.company.clone(),
                tiers: row.tiers.clone(),
                enabled,
                min_volume: 0,
            }).await?;
        }
        ins.end().await?;
    }

    tracing::info!("Watchlist populated/updated");

    // Re-apply volume group enabled state after bulk insert.
    // The insert above sets enabled based on tier_state (all-or-nothing per tier).
    // Volume groups are the primary control for which 410 stocks are active.
    // Without this call, midnight scrip sync would reset all stocks to enabled=0
    // (since all tiers are disabled), wiping out the user's volume group configuration.
    crate::db::watchlist::reevaluate_watchlist_enabled(ch).await;
    tracing::info!("Watchlist enabled state re-applied from volume groups after scrip sync");

    Ok(())
}

fn parse_margin_stocks() -> HashSet<String> {
    let mut set = HashSet::new();
    if let Ok(val) = serde_json::from_str::<serde_json::Value>(MARGIN_STOCKS_JSON) {
        if let Some(arr) = val.get("stocks").and_then(|v| v.as_array()) {
            for item in arr {
                if let Some(sym) = item.get("tradingSymbol").and_then(|v| v.as_str()) {
                    set.insert(sym.to_string());
                }
            }
        }
    }
    tracing::info!("Margin stocks (4-10x): {} symbols loaded", set.len());
    set
}

fn parse_liquid5l_symbols() -> HashSet<String> {
    let mut set = HashSet::new();
    if let Ok(arr) = serde_json::from_str::<Vec<String>>(LIQUID_5L_JSON) {
        for sym in arr { set.insert(sym); }
    }
    tracing::info!("Liquid5L: {} symbols loaded", set.len());
    set
}

fn parse_recommended_tiers() -> (HashSet<String>, HashSet<String>) {
    let mut tier1 = HashSet::new();
    let mut tier2 = HashSet::new();
    if let Ok(val) = serde_json::from_str::<serde_json::Value>(RECOMMENDED_WATCHLIST_JSON) {
        if let Some(arr) = val.get("tier1").and_then(|v| v.as_array()) {
            for s in arr { if let Some(sym) = s.as_str() { tier1.insert(sym.to_string()); } }
        }
        if let Some(arr) = val.get("tier2").and_then(|v| v.as_array()) {
            for s in arr { if let Some(sym) = s.as_str() { tier2.insert(sym.to_string()); } }
        }
    }
    tracing::info!("Recommended watchlist: {} Tier1, {} Tier2 symbols", tier1.len(), tier2.len());
    (tier1, tier2)
}

async fn get_active_tiers(ch: &ChClient) -> Result<HashSet<String>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { tier_name: String }
    let rows = ch.query("SELECT tier_name FROM trading.tier_state FINAL WHERE enabled = 1")
        .fetch_all::<Row>().await?;
    Ok(rows.into_iter().map(|r| r.tier_name).collect())
}

struct WatchlistRow {
    security_id: String,
    symbol: String,
    company: String,
    tiers: Vec<String>,
}

#[derive(clickhouse::Row, serde::Serialize)]
struct WatchlistClickhouseRow {
    security_id: String,
    symbol: String,
    company_name: String,
    tiers: Vec<String>,
    enabled: u8,
    min_volume: u32,
}
