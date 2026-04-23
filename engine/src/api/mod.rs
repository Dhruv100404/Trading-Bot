use axum::{Router, routing::get};
use tower_http::cors::{CorsLayer, Any};
use std::sync::Arc;
use std::collections::HashMap;
use tokio::sync::RwLock;
use clickhouse::Client as ChClient;
use crate::config::Config;
use crate::dhan::market_data::QuoteItem;

pub mod positions;
pub mod swing;

/// Short-lived cache of Dhan quotes shared across swing endpoints.
pub struct CachedQuotes {
    pub fetched_at: std::time::Instant,
    pub by_security_id: HashMap<String, QuoteItem>,
}

#[derive(Clone)]
pub struct AppState {
    pub ch: ChClient,
    pub ch_url: String,
    #[allow(dead_code)]
    pub config: Config,
    pub quote_cache: Arc<RwLock<Option<CachedQuotes>>>,
}

pub async fn serve(ch: ChClient, app_config: Config) -> anyhow::Result<()> {
    let ch_url = app_config.clickhouse_url.clone();
    let state = AppState {
        ch,
        ch_url,
        config: app_config,
        quote_cache: Arc::new(RwLock::new(None)),
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/api/positions",                get(positions::list))
        .route("/api/swing/home",               get(swing::home))
        .route("/api/swing/scanner",            get(swing::scanner))
        .route("/api/swing/candidates/:symbol", get(swing::candidate_detail))
        .route("/api/swing/broker-status",      get(swing::broker_status))
        .layer(cors)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    tracing::info!("Swing API listening on :8080");
    axum::serve(listener, app).await?;
    Ok(())
}
