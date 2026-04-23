mod config;
mod types;
mod dhan;
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
