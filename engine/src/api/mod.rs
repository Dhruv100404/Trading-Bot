use axum::{Router, routing::{get, patch, post}};
use tower_http::cors::{CorsLayer, Any};
use std::sync::Arc;
use std::collections::HashMap;
use tokio::sync::{broadcast, RwLock};
use clickhouse::Client as ChClient;
use crate::config::Config;
use crate::types::Snapshot;

pub mod ws;
pub mod signals;
pub mod snapshots;
pub mod daily_ref;
pub mod performance;
pub mod accounts;
pub mod watchlist;
pub mod config;
pub mod status;
pub mod ch_http;
pub mod close_all;
pub mod positions;
pub mod settings;
pub mod backtest;
pub mod zerodha_auth;

/// Cached snapshot + gap data for backtest. All data derived from snapshots table only.
/// No daily_ref dependency — gap_pct and direction computed from bucket 1 and bucket 375.
pub struct BacktestCache {
    pub from: String,
    pub to: String,
    pub bucket_limit: u16,
    pub by_ds: HashMap<(String, String), Vec<Snapshot>>,
    pub gaps: HashMap<String, f32>,
    pub stock_wr: HashMap<String, f32>,
    pub dates: Vec<String>,
    pub total_stock_days: u32,
}

#[derive(Clone)]
pub struct AppState {
    pub ch: ChClient,
    pub ch_url: String,
    pub ws_tx: Arc<broadcast::Sender<String>>,
    #[allow(dead_code)]
    pub config: Config,
    pub backtest_cache: Arc<RwLock<Option<BacktestCache>>>,
}

pub async fn serve(
    ch: ChClient,
    ws_tx: Arc<broadcast::Sender<String>>,
    app_config: Config,
) -> anyhow::Result<()> {
    let ch_url = app_config.clickhouse_url.clone();
    let state = AppState { ch, ch_url, ws_tx, config: app_config, backtest_cache: Arc::new(RwLock::new(None)) };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/ws",                         get(ws::handler))
        .route("/api/signals",                get(signals::list))
        .route("/api/snapshots",              get(snapshots::list))
        .route("/api/snapshots/bulk",         get(snapshots::list_all))
        .route("/api/daily_ref",              get(daily_ref::get))
        .route("/api/daily_ref/bulk",         get(daily_ref::get_all))
        .route("/api/performance",            get(performance::list))
        .route("/api/accounts",               get(accounts::list).post(accounts::create))
        .route("/api/accounts/health",        get(accounts::health))
        .route("/api/accounts/:client_id",    patch(accounts::update).delete(accounts::remove))
        .route("/api/watchlist",              get(watchlist::list))
        // Literal routes MUST come before parameterised routes to avoid axum route conflicts
        .route("/api/watchlist/tiers",        get(watchlist::list_tiers))
        .route("/api/watchlist/tiers/:name",  patch(watchlist::update_tier))
        .route("/api/watchlist/volume-groups", get(watchlist::list_volume_groups))
        .route("/api/watchlist/volume-groups/:name", patch(watchlist::update_volume_group))
        .route("/api/watchlist/:security_id", patch(watchlist::update_stock))
        .route("/api/config",                 get(config::get_config).put(config::update_config).patch(config::update_config))
        .route("/api/status",                 get(status::get_status))
        .route("/api/close-all",              post(close_all::close_all))
        .route("/api/positions",              get(positions::list))
        .route("/api/settings",               get(settings::get_settings).put(settings::update_settings))
        .route("/api/backtest/compute",      post(backtest::compute))
        .route("/api/zerodha/login",         get(zerodha_auth::login_url))
        .route("/api/zerodha/callback",      get(zerodha_auth::callback))
        .layer(cors)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    tracing::info!("API listening on :8080");
    axum::serve(listener, app).await?;
    Ok(())
}
