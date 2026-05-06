use axum::{Router, routing::{delete, get, post}};
use tower_http::cors::{CorsLayer, Any};
use std::sync::Arc;
use std::collections::HashMap;
use tokio::sync::{Mutex, RwLock};
use clickhouse::Client as ChClient;
use crate::config::Config;
use crate::dhan::market_data::QuoteItem;

pub mod positions;
pub mod paper;
pub mod swing;
pub mod backtest;

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
    pub quote_fetch_lock: Arc<Mutex<()>>,
}

pub async fn serve(ch: ChClient, app_config: Config) -> anyhow::Result<()> {
    let ch_url = app_config.clickhouse_url.clone();
    let state = AppState {
        ch,
        ch_url,
        config: app_config,
        quote_cache: Arc::new(RwLock::new(None)),
        quote_fetch_lock: Arc::new(Mutex::new(())),
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/api/positions",                get(positions::list))
        .route("/api/paper-trades",             get(paper::list).post(paper::upsert))
        .route("/api/paper-budget",             get(paper::budget).post(paper::set_budget))
        .route("/api/paper-trades/:symbol/close", post(paper::close))
        .route("/api/paper-trades/:symbol",     delete(paper::remove))
        .route("/api/swing/home",               get(swing::home))
        .route("/api/swing/scanner",            get(swing::scanner))
        .route("/api/swing/historical-screener", get(swing::historical_screener))
        .route("/api/swing/history/:symbol",    get(swing::history))
        .route("/api/swing/candidates/:symbol", get(swing::candidate_detail))
        .route("/api/swing/broker-status",      get(swing::broker_status))
        .route("/api/backtests/dashboard",       get(backtest::dashboard))
        .route("/api/backtests/run",             post(backtest::run))
        .layer(cors)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    tracing::info!("Swing API listening on :8080");
    axum::serve(listener, app).await?;
    Ok(())
}
