mod config;
mod types;
mod dhan;
mod market_activity;
mod news;
mod scrip_master;
mod api;

use anyhow::Result;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::from_default_env().add_directive("engine=info".parse()?),
        )
        .init();

    let config = config::Config::from_env()?;
    tracing::info!("swing-atlas engine starting");

    let ch = clickhouse::Client::default()
        .with_url(&config.clickhouse_url)
        .with_database("trading");

    tracing::info!("[INIT] ClickHouse URL: {}", config.clickhouse_url);
    wait_for_clickhouse(&ch).await?;
    tracing::info!("[INIT] ClickHouse healthy");

    api::news::ensure_news_schema(&ch).await?;
    api::market_activity::ensure_market_activity_schema(&ch).await?;
    tracing::info!("[INIT] market command-center tables ready");

    match scrip_master::seed_watchlist_if_empty(&ch).await {
        Ok(0) => tracing::info!("[INIT] watchlist already seeded"),
        Ok(count) => tracing::info!("[INIT] seeded {} NSE instruments from Dhan", count),
        Err(err) => tracing::warn!("[INIT] Dhan watchlist seed skipped: {}", err),
    }

    match api::news::backfill_corporate_events_if_empty(&ch).await {
        Ok(0) => tracing::info!("[INIT] corporate-event history already available"),
        Ok(count) => tracing::info!("[INIT] imported {} historical NSE corporate events", count),
        Err(err) => tracing::warn!("[INIT] corporate-event history backfill skipped: {}", err),
    }

    let news_ch = ch.clone();
    let news_ch_url = config.clickhouse_url.clone();
    tokio::spawn(async move {
        api::news::run_refresh_loop(news_ch, news_ch_url).await;
    });

    let activity_ch = ch.clone();
    let activity_ch_url = config.clickhouse_url.clone();
    tokio::spawn(async move {
        api::market_activity::run_refresh_loop(activity_ch, activity_ch_url).await;
    });

    api::serve(ch, config).await
}

/// Poll ClickHouse /ping until it responds (max 30 attempts, 2.5 min total).
async fn wait_for_clickhouse(client: &clickhouse::Client) -> Result<()> {
    for attempt in 1..=30 {
        match client.query("SELECT 1").fetch_one::<u8>().await {
            Ok(_) => return Ok(()),
            Err(e) => {
                tracing::warn!("ClickHouse not ready (attempt {}/30): {}", attempt, e);
                if attempt < 30 {
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                }
            }
        }
    }
    anyhow::bail!("ClickHouse did not become healthy after 30 attempts")
}
