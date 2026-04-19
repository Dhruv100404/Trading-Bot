use clickhouse::Client;
use anyhow::Result;
use crate::types::Gap15Config;

/// Create gap15_config table if it doesn't exist.
pub async fn migrate_gap15_schema(client: &Client) {
    let sql = "CREATE TABLE IF NOT EXISTS trading.gap15_config \
        (total_capital UInt32, leverage UInt32, top_n UInt32, \
         tp_pct Float32, sl_pct Float32, exit_bucket UInt16, \
         gap_min_pct Float32, gap_max_pct Float32 DEFAULT 15.0, price_max Float32, cap_mult Float32, \
         entry_slippage_pct Float32 DEFAULT 0.30, \
         inserted_at DateTime DEFAULT now()) \
        ENGINE = ReplacingMergeTree(inserted_at) ORDER BY tuple()";
    if let Err(e) = client.query(sql).execute().await {
        tracing::error!("[MIGRATE] gap15_config table creation failed: {}", e);
    }
}

/// Seed default Gap15Config if table is empty.
pub async fn seed_gap15_config_if_empty(client: &Client) -> Result<()> {
    let count: u64 = client
        .query("SELECT count() FROM trading.gap15_config FINAL")
        .fetch_one()
        .await
        .unwrap_or(0);
    if count == 0 {
        let cfg = Gap15Config::default();
        save_gap15_config(client, &cfg).await?;
        tracing::info!("[CONFIG] Seeded default Gap15Config");
    }
    Ok(())
}

/// Load the current Gap15Config from DB, falling back to defaults.
pub async fn get_gap15_config(client: &Client) -> Gap15Config {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row {
        total_capital: u32, leverage: u32, top_n: u32,
        tp_pct: f32, sl_pct: f32, exit_bucket: u16,
        gap_min_pct: f32, gap_max_pct: f32, price_max: f32, cap_mult: f32,
        // DB column is entry_slippage_pct; aliased here for the Rust struct field name.
        fallback_limit_slippage_pct: f32,
    }
    match client.query(
        "SELECT total_capital, leverage, top_n, tp_pct, sl_pct, exit_bucket, \
         gap_min_pct, gap_max_pct, price_max, cap_mult, \
         entry_slippage_pct AS fallback_limit_slippage_pct \
         FROM trading.gap15_config FINAL ORDER BY inserted_at DESC LIMIT 1"
    ).fetch_one::<Row>().await {
        Ok(r) => Gap15Config {
            total_capital: r.total_capital,
            leverage: r.leverage,
            top_n: r.top_n as usize,
            tp_pct: r.tp_pct,
            sl_pct: r.sl_pct,
            exit_bucket: r.exit_bucket,
            gap_min_pct: r.gap_min_pct,
            gap_max_pct: r.gap_max_pct,
            price_max: r.price_max,
            cap_mult: r.cap_mult,
            fallback_limit_slippage_pct: r.fallback_limit_slippage_pct,
        },
        Err(e) => {
            tracing::warn!("[CONFIG] gap15_config load failed ({}), using defaults", e);
            Gap15Config::default()
        }
    }
}

/// Persist a Gap15Config to DB.
pub async fn save_gap15_config(client: &Client, cfg: &Gap15Config) -> Result<()> {
    client.query(
        "INSERT INTO trading.gap15_config \
         (total_capital, leverage, top_n, tp_pct, sl_pct, exit_bucket, gap_min_pct, gap_max_pct, price_max, cap_mult, entry_slippage_pct) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    .bind(cfg.total_capital)
    .bind(cfg.leverage)
    .bind(cfg.top_n as u32)
    .bind(cfg.tp_pct)
    .bind(cfg.sl_pct)
    .bind(cfg.exit_bucket)
    .bind(cfg.gap_min_pct)
    .bind(cfg.gap_max_pct)
    .bind(cfg.price_max)
    .bind(cfg.cap_mult)
    .bind(cfg.fallback_limit_slippage_pct)
    .execute().await?;
    client.query("OPTIMIZE TABLE trading.gap15_config FINAL").execute().await.ok();
    Ok(())
}

pub async fn seed_tier_state_if_empty(client: &Client) -> Result<()> {
    let count: u64 = client
        .query("SELECT count() FROM trading.tier_state FINAL")
        .fetch_one()
        .await?;
    if count == 0 {
        client.query(
            "INSERT INTO trading.tier_state (tier_name, enabled) VALUES \
             ('F&O', 1), ('Nifty50', 0), ('Nifty500', 0), ('NSEActive', 0), ('AllNSE', 0)"
        ).execute().await?;
        tracing::info!("Seeded default tier state (F&O active)");
    }
    Ok(())
}

/// Migrate volume_group_state table.
pub async fn migrate_volume_group_schema(client: &Client) {
    let sql = "CREATE TABLE IF NOT EXISTS trading.volume_group_state \
        (group_name String, enabled UInt8 DEFAULT 1, inserted_at DateTime DEFAULT now()) \
        ENGINE = ReplacingMergeTree(inserted_at) ORDER BY group_name";
    if let Err(e) = client.query(sql).execute().await {
        tracing::error!("[MIGRATE] volume_group_state creation failed: {}", e);
    }
    // Seed defaults if empty
    let count: u64 = client
        .query("SELECT count() FROM trading.volume_group_state FINAL")
        .fetch_one()
        .await
        .unwrap_or(0);
    if count == 0 {
        for (name, enabled) in [("MEGA", 1u8), ("LARGE", 1u8), ("MID", 0u8), ("SMALL", 0u8)] {
            client.query(
                "INSERT INTO trading.volume_group_state (group_name, enabled) VALUES (?, ?)"
            ).bind(name).bind(enabled).execute().await.ok();
        }
        tracing::info!("[MIGRATE] Seeded volume_group_state defaults");
    }
}

/// Return set of enabled volume group names (e.g. {"MEGA", "LARGE"}).
pub async fn get_enabled_volume_groups(client: &Client) -> std::collections::HashSet<String> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { group_name: String }
    client.query(
        "SELECT group_name FROM trading.volume_group_state FINAL WHERE enabled = 1"
    )
    .fetch_all::<Row>()
    .await
    .unwrap_or_default()
    .into_iter()
    .map(|r| r.group_name)
    .collect()
}

/// Load symbols belonging to the given enabled volume groups from volume_groups.json.
fn load_vol_symbols(enabled_groups: &std::collections::HashSet<String>) -> std::collections::HashSet<String> {
    let paths = ["data/volume_groups.json", "../data/volume_groups.json"];
    for path in &paths {
        if let Ok(content) = std::fs::read_to_string(path) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(groups) = v.get("volume_groups").and_then(|g| g.as_object()) {
                    let mut set = std::collections::HashSet::new();
                    for (key, symbols) in groups {
                        let group_key = if key.contains("MEGA") { "MEGA" }
                            else if key.contains("LARGE") { "LARGE" }
                            else if key.contains("MID") { "MID" }
                            else if key.contains("SMALL") { "SMALL" }
                            else { continue };
                        if !enabled_groups.contains(group_key) { continue; }
                        if let Some(arr) = symbols.as_array() {
                            for sym in arr {
                                if let Some(s) = sym.as_str() { set.insert(s.to_string()); }
                            }
                        }
                    }
                    return set;
                }
            }
        }
    }
    std::collections::HashSet::new()
}

/// Re-evaluate watchlist.enabled for ALL stocks based on active volume groups only.
/// Gap15 strategy: a stock is enabled if its symbol is in any enabled volume group.
/// If volume_groups.json is unavailable, falls back to tier-based logic (fail-open).
pub async fn reevaluate_watchlist_enabled(client: &Client) {
    let enabled_groups = get_enabled_volume_groups(client).await;
    let vol_symbols = load_vol_symbols(&enabled_groups);

    if !vol_symbols.is_empty() {
        // Primary path: enable stocks that are in an active volume group
        let sym_list: Vec<String> = vol_symbols.iter()
            .map(|s| format!("'{}'", s.replace('\'', "\\'")))
            .collect();
        let sym_in = format!("({})", sym_list.join(","));
        let q = format!(
            "INSERT INTO trading.watchlist (security_id, symbol, company_name, tiers, enabled) \
             SELECT security_id, symbol, company_name, tiers, \
               if(symbol IN {sym_in}, 1, 0) AS enabled \
             FROM trading.watchlist FINAL",
            sym_in = sym_in
        );
        if let Err(e) = client.query(&q).execute().await {
            tracing::warn!("[WATCHLIST] reevaluate vol-group failed: {}", e);
        }
    } else if enabled_groups.is_empty() {
        // No groups enabled: disable all
        client.query(
            "INSERT INTO trading.watchlist (security_id, symbol, company_name, tiers, enabled) \
             SELECT security_id, symbol, company_name, tiers, 0 FROM trading.watchlist FINAL"
        ).execute().await.ok();
    } else {
        // volume_groups.json missing — fall back to tier-based logic (fail-open)
        tracing::warn!("[WATCHLIST] volume_groups.json not found — using tier-based fallback");
        #[derive(clickhouse::Row, serde::Deserialize)]
        struct TierRow { tier_name: String }
        let tiers = client
            .query("SELECT tier_name FROM trading.tier_state FINAL WHERE enabled = 1")
            .fetch_all::<TierRow>().await.unwrap_or_default();
        let tier_names: Vec<String> = tiers.iter().map(|t| format!("'{}'", t.tier_name)).collect();
        let tier_array = if tier_names.is_empty() { "[]".to_string() } else { format!("[{}]", tier_names.join(",")) };
        let q = format!(
            "INSERT INTO trading.watchlist (security_id, symbol, company_name, tiers, enabled) \
             SELECT security_id, symbol, company_name, tiers, \
               if(hasAny(tiers, {tier_arr}), 1, 0) AS enabled \
             FROM trading.watchlist FINAL",
            tier_arr = tier_array
        );
        client.query(&q).execute().await.ok();
    }

    client.query("OPTIMIZE TABLE trading.watchlist FINAL").execute().await.ok();
}

pub async fn get_active_security_ids(client: &Client) -> Result<Vec<(String, String)>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { security_id: String, symbol: String }

    let rows = client
        .query("SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled = 1")
        .fetch_all::<Row>()
        .await?;
    tracing::info!("Active watchlist: {} stocks", rows.len());
    Ok(rows.into_iter().map(|r| (r.security_id, r.symbol)).collect())
}
