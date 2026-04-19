use clickhouse::Client;
use anyhow::Result;

pub fn create_client(url: &str) -> Client {
    Client::default()
        .with_url(url)
        .with_database("trading")
}

/// Polls /ping until ClickHouse responds, max 30 attempts (150 sec)
pub async fn wait_healthy(client: &Client) -> Result<()> {
    for attempt in 1..=30 {
        match client.query("SELECT 1").fetch_one::<u8>().await {
            Ok(_) => {
                tracing::info!("ClickHouse healthy");
                return Ok(());
            }
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
