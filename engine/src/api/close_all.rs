use axum::{extract::State, Json};
use crate::api::AppState;
use crate::db::{signals as sig_db, watchlist as wl_db};
use crate::exit_manager::{ExitResult, ExitReason};
use crate::order_executor::{OrderExecutor, cancel_sl_order};
use crate::types::{Direction, today_ist};

pub async fn close_all(State(state): State<AppState>) -> Json<serde_json::Value> {
    let today = today_ist();

    let open = match sig_db::get_open_signals(&state.ch, today).await {
        Ok(v) => v,
        Err(e) => return Json(serde_json::json!({ "error": e.to_string() })),
    };

    if open.is_empty() {
        return Json(serde_json::json!({ "ok": true, "closed": 0, "message": "No open positions" }));
    }

    // ── Cancel all pending broker SL orders before placing exit orders ──
    cancel_all_sl_orders(&state.ch, today, &state.config.dhan_base_url).await;

    let executor = OrderExecutor::new(state.config.clone());
    let cfg = wl_db::get_gap15_config(&state.ch).await;
    let mut closed = 0usize;
    let mut errors: Vec<String> = vec![];

    for (sig_id, signal) in &open {
        // Get latest LTP from snapshots for accurate P&L
        let ltp = get_latest_ltp(&state.ch, today, &signal.symbol)
            .await
            .unwrap_or(signal.entry_price);

        let actual_return_pct = match signal.direction {
            Direction::Buy  => (ltp - signal.entry_price) / signal.entry_price * 100.0,
            Direction::Sell => (signal.entry_price - ltp) / signal.entry_price * 100.0,
        };
        let pnl_rupees = signal.entry_price * (actual_return_pct / 100.0) * signal.quantity as f32;

        let exit = ExitResult {
            reason: ExitReason::Time,
            exit_price: ltp,
            exit_bucket: 0,
            actual_return_pct,
            pnl_rupees,
        };

        // Close on ALL live accounts (close_all is an emergency button)
        match executor.execute_exit(signal, *sig_id, &state.ch, "").await {
            Ok(_) => {
                sig_db::update_signal_exit(&state.ch, signal, *sig_id, &exit, &cfg).await.ok();
                closed += 1;
            }
            Err(e) => errors.push(format!("{}: {}", signal.symbol, e)),
        }
    }

    Json(serde_json::json!({
        "ok": errors.is_empty(),
        "closed": closed,
        "total": open.len(),
        "errors": errors,
    }))
}

/// Cancel all pending broker SL_PROTECTION orders for today before emergency close.
async fn cancel_all_sl_orders(
    ch: &clickhouse::Client,
    today: chrono::NaiveDate,
    dhan_base_url: &str,
) {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct SlRow {
        dhan_order_id: String,
        account_client_id: String,
    }

    // Fetch all pending SL_PROTECTION orders for today
    let sl_orders = ch.query(
        "SELECT dhan_order_id, account_client_id \
         FROM trading.orders FINAL \
         WHERE trading_date = toDate(?) AND order_type = 3 AND status = 1"
    )
    .bind(today.format("%Y-%m-%d").to_string())
    .fetch_all::<SlRow>().await
    .unwrap_or_default();

    if sl_orders.is_empty() {
        return;
    }

    tracing::info!("[CLOSE_ALL] Cancelling {} pending SL orders before emergency close", sl_orders.len());

    // Fetch account credentials for cancellation
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct AccRow { client_id: String, broker: String, api_key: String, access_token: String }

    let accounts = ch.query(
        "SELECT client_id, broker, api_key, access_token \
         FROM trading.accounts FINAL WHERE mode = 'LIVE' AND enabled = 1"
    )
    .fetch_all::<AccRow>().await
    .unwrap_or_default();

    for sl in &sl_orders {
        let acc = match accounts.iter().find(|a| a.client_id == sl.account_client_id) {
            Some(a) => a,
            None => {
                tracing::warn!("[CLOSE_ALL] No account found for SL order {} (client={})", sl.dhan_order_id, sl.account_client_id);
                continue;
            }
        };

        match cancel_sl_order(&acc.broker, &sl.dhan_order_id, &acc.api_key, &acc.access_token, &acc.client_id, dhan_base_url).await {
            Ok(true) => tracing::info!("[CLOSE_ALL] Cancelled SL order {} for account {}", sl.dhan_order_id, sl.account_client_id),
            Ok(false) => tracing::info!("[CLOSE_ALL] SL order {} already filled/gone for account {}", sl.dhan_order_id, sl.account_client_id),
            Err(e) => tracing::error!("[CLOSE_ALL] Failed to cancel SL order {}: {}", sl.dhan_order_id, e),
        }
    }
}

async fn get_latest_ltp(
    ch: &clickhouse::Client,
    date: chrono::NaiveDate,
    symbol: &str,
) -> anyhow::Result<f32> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { ltp: f32 }
    let row = ch.query(
        "SELECT ltp FROM trading.snapshots \
         WHERE trading_date = toDate(?) AND symbol = ? \
         ORDER BY bucket DESC LIMIT 1"
    )
    .bind(date.format("%Y-%m-%d").to_string())
    .bind(symbol)
    .fetch_one::<Row>().await?;
    Ok(row.ltp)
}
