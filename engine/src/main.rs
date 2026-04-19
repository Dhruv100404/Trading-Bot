mod config;
mod types;
mod dhan;
mod zerodha;
mod db;
mod derived;
mod exit_manager;
mod order_executor;
mod ws_feed;
mod poller;
mod api;

use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env()
            .add_directive("engine=info".parse()?))
        .init();

    let config = config::Config::from_env()?;
    tracing::info!("dhan-trader engine starting (gap15 strategy)");

    // DB setup
    let ch = db::clickhouse::create_client(&config.clickhouse_url);
    tracing::info!("[INIT] Waiting for ClickHouse at {}...", config.clickhouse_url);
    db::clickhouse::wait_healthy(&ch).await?;
    tracing::info!("[INIT] ClickHouse healthy");
    db::watchlist::migrate_gap15_schema(&ch).await;
    db::watchlist::seed_gap15_config_if_empty(&ch).await?;
    db::watchlist::seed_tier_state_if_empty(&ch).await?;
    db::watchlist::migrate_volume_group_schema(&ch).await;
    tracing::info!("[INIT] Schema migrated, config seeded");

    // Scrip master
    tracing::info!("[INIT] Syncing scrip master...");
    dhan::scrip_master::sync_if_needed(&ch, &config).await?;
    tracing::info!("[INIT] Scrip master sync complete");

    // Load fired_today from DB
    let fired_today = db::signals::load_fired_today(&ch).await?;
    tracing::info!("[INIT] Loaded {} fired-today symbols from DB", fired_today.len());
    let fired_today = std::sync::Arc::new(tokio::sync::Mutex::new(fired_today));

    // WebSocket broadcast channel
    let (ws_tx, _) = tokio::sync::broadcast::channel(256);
    let ws_tx = std::sync::Arc::new(ws_tx);

    // Start API server
    let api_handle = {
        let ch = ch.clone();
        let ws_tx = ws_tx.clone();
        let config = config.clone();
        tokio::spawn(async move {
            api::serve(ch, ws_tx, config).await
        })
    };

    // Start poller
    let poller_handle = tokio::spawn(
        poller::run(ch.clone(), config.clone(), fired_today, ws_tx.clone())
    );

    tokio::select! {
        r = api_handle => { tracing::error!("API exited: {:?}", r); }
        r = poller_handle => { tracing::error!("Poller exited: {:?}", r); }
    }

    Ok(())
}
