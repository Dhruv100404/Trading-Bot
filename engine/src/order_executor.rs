use crate::types::{Signal, Direction};
use crate::config::Config;
use crate::dhan::client::DhanClient;
use clickhouse::Client as ChClient;
use anyhow::Result;
use uuid::Uuid;
use std::collections::HashMap;
use std::sync::{Arc, Mutex as StdMutex};
use tokio::sync::Semaphore;
use tokio::time::{sleep, Duration};
use chrono::Timelike;

/// Sent from poll_order_status back to the poller when an entry order is filled.
/// The poller uses this to update entry_price and recompute TP/SL.
/// Also carries broker-side SL order info placed immediately after fill.
#[derive(Debug, Clone)]
pub struct FillUpdate {
    pub symbol: String,
    pub account_client_id: String,
    pub fill_price: f32,
    pub signal_id: Uuid,
    /// Broker-side SL order ID (None if placement failed → software SL fallback).
    pub sl_order_id: Option<String>,
    /// Which broker this SL order lives on.
    pub sl_broker: String,
    /// Actual SL trigger price (tick-aligned, computed from fill_price).
    pub sl_trigger_price: f32,
    /// True if both SL-M and SL-Limit failed → poller must use software SL.
    pub sl_fallback_software: bool,
    /// Broker credentials needed for cancel/status-check later.
    pub broker_api_key: String,
    pub broker_access_token: String,
}

/// Per-account rate limiter: 8 concurrent orders per account (safe under Dhan's 10/sec).
/// Uses std::Mutex (not tokio) since the lock is held only for a HashMap lookup (~nanoseconds).
static ACCOUNT_LIMITERS: std::sync::OnceLock<StdMutex<HashMap<String, Arc<Semaphore>>>> = std::sync::OnceLock::new();

fn get_account_limiter(client_id: &str) -> Arc<Semaphore> {
    let map = ACCOUNT_LIMITERS.get_or_init(|| StdMutex::new(HashMap::new()));
    let mut guard = map.lock().unwrap_or_else(|e| e.into_inner());
    guard.entry(client_id.to_string())
        .or_insert_with(|| Arc::new(Semaphore::new(8)))
        .clone()
}

#[derive(Clone)]
pub struct OrderExecutor {
    pub config: Config,
}

impl OrderExecutor {
    pub fn new(config: Config) -> Self { Self { config } }

    /// Execute ENTRY order. Primary order is always MARKET.
    /// If it stays PENDING after 20s, poll_order_status cancels it and retries
    /// with an explicit LIMIT at ltp ± fallback_limit_slippage_pct%.
    pub async fn execute(&self, signal: &Signal, signal_id: Uuid, ch: &ChClient, account_client_id: &str,
        fill_tx: Option<tokio::sync::mpsc::UnboundedSender<FillUpdate>>,
        entry_reject_tx: Option<tokio::sync::mpsc::UnboundedSender<(String, Uuid)>>,
        sl_pct: f32,
        fallback_limit_slippage_pct: f32,
    ) -> Result<()> {
        self.place(signal, signal_id, ch, signal.direction.clone(), account_client_id,
            fill_tx, entry_reject_tx, sl_pct, fallback_limit_slippage_pct).await
    }

    /// Execute EXIT order for a specific account (always MARKET — exits must be instant).
    pub async fn execute_exit(&self, signal: &Signal, signal_id: Uuid, ch: &ChClient, account_client_id: &str) -> Result<()> {
        let exit_dir = match signal.direction {
            Direction::Buy => Direction::Sell,
            Direction::Sell => Direction::Buy,
        };
        // Exit orders: slippage_pct=0.0 → MARKET order path in place()
        self.place(signal, signal_id, ch, exit_dir, account_client_id, None, None, 0.0, 0.0).await
    }

    async fn place(&self, signal: &Signal, signal_id: Uuid, ch: &ChClient, direction: Direction, account_client_id: &str,
        fill_tx: Option<tokio::sync::mpsc::UnboundedSender<FillUpdate>>,
        entry_reject_tx: Option<tokio::sync::mpsc::UnboundedSender<(String, Uuid)>>,
        sl_pct: f32,
        fallback_limit_slippage_pct: f32,
    ) -> Result<()> {
        #[derive(clickhouse::Row, serde::Deserialize)]
        #[allow(dead_code)]
        struct AccountRow { name: String, client_id: String, access_token: String, broker: String, api_key: String }

        // If account_client_id is specified, only place on that account.
        // If empty (default/paper), place on all LIVE accounts.
        let accounts = if account_client_id.is_empty() {
            ch.query("SELECT name, client_id, access_token, broker, api_key FROM trading.accounts FINAL WHERE mode = 'LIVE' AND enabled = 1")
                .fetch_all::<AccountRow>().await
                .unwrap_or_default()
        } else {
            ch.query("SELECT name, client_id, access_token, broker, api_key FROM trading.accounts FINAL WHERE client_id = ? AND mode = 'LIVE' AND enabled = 1")
                .bind(account_client_id)
                .fetch_all::<AccountRow>().await
                .unwrap_or_default()
        };

        if accounts.is_empty() {
            tracing::info!("[PAPER] {} {} {} qty={} account={}", direction.as_str(), signal.symbol, signal.entry_price, signal.quantity, account_client_id);
            return Ok(());
        }

        let order_type: u8 = if direction == signal.direction { 1 } else { 2 };
        let type_label = if order_type == 1 { "ENTRY" } else { "EXIT" };

        // Each task returns true if the order was placed successfully on that account.
        // We only remove the phantom position if ALL accounts fail — if even one broker
        // fills the order, the position is real and must stay in open_signals.
        let mut handles: Vec<tokio::task::JoinHandle<bool>> = vec![];

        for acc in accounts {
            let signal_id = signal_id;
            let security_id = signal.security_id.clone();
            let order_direction = direction.clone();
            let signal_direction = signal.direction.clone();
            let quantity = signal.quantity;
            let symbol = signal.symbol.clone();
            let endpoint = self.config.dhan_orders_endpoint.clone();
            let sl_pct = sl_pct;
            let cfg = Config {
                dhan_access_token: acc.access_token.clone(),
                dhan_client_id: acc.client_id.clone(),
                ..self.config.clone()
            };
            let ch = ch.clone();
            let today = signal.trading_date;
            let otype = order_type;
            let type_lbl = type_label.to_string();
            let limiter = get_account_limiter(&acc.client_id);
            let fill_tx_clone = fill_tx.clone();
            let reject_tx_clone = entry_reject_tx.clone();
            let broker = acc.broker.clone();
            let api_key = acc.api_key.clone();
            let access_token = acc.access_token.clone();
            let entry_ltp = signal.entry_price;
            let slippage_pct = fallback_limit_slippage_pct;

            handles.push(tokio::spawn(async move {
                let permit = limiter.acquire().await.expect("semaphore closed");

                let max_retries = 3u8;
                let mut last_err = String::new();
                let mut success = false;
                let broker_label = if broker == "ZERODHA" { "ZERODHA" } else { "DHAN" };

                for attempt in 1..=max_retries {
                    let result = if broker == "ZERODHA" {
                        let kite = crate::zerodha::client::ZerodhaClient::new(&api_key, &access_token);
                        crate::zerodha::orders::place_order(&kite, &symbol, &order_direction, quantity).await
                    } else {
                        let dhan = DhanClient::new(&cfg);
                        crate::dhan::orders::place_order(&dhan, &security_id, &order_direction, quantity, 0.0, &endpoint).await
                    };

                    match result {
                        Ok(order_id) => {
                            tracing::info!("[LIVE] ✅ {} {} {} qty={} account={} broker={} order_id={} (attempt {})",
                                type_lbl, order_direction.as_str(), symbol, quantity, acc.client_id, broker_label, order_id, attempt);

                            insert_order_record(&ch, today, signal_id, &acc.client_id, &symbol,
                                &order_id, &order_direction, quantity, otype, "PENDING", None).await.ok();

                            let oid = order_id.clone();
                            let ch2 = ch.clone();
                            let cfg2 = cfg.clone();
                            let sym2 = symbol.clone();
                            let cid2 = acc.client_id.clone();
                            let dir2 = order_direction.clone();
                            let ftx = fill_tx_clone.clone();
                            let rtx = reject_tx_clone.clone();
                            let broker2 = broker.clone();
                            let api_key2 = api_key.clone();
                            let access_token2 = access_token.clone();
                            let sec_id2 = security_id.clone();
                            let pos_dir2 = signal_direction.clone();
                            let endpoint2 = endpoint.clone();
                            let entry_ltp2 = entry_ltp;
                            let slippage2 = slippage_pct;
                            tokio::spawn(async move {
                                poll_order_status(
                                    ch2, cfg2, today, signal_id, &cid2, &sym2, &oid, &dir2,
                                    quantity, otype, ftx, rtx, &broker2, &api_key2, &access_token2,
                                    &sec_id2, &pos_dir2, sl_pct, &endpoint2,
                                    entry_ltp2, slippage2,
                                ).await;
                            });
                            success = true;
                            break;
                        }
                        Err(e) => {
                            last_err = e.to_string();
                            let is_rate_limit = last_err.contains("Too many") || last_err.contains("429");
                            if is_rate_limit {
                                tracing::warn!("[LIVE] ⚠️ RATE LIMITED: {} {} account={} broker={} — retry {}/{}",
                                    order_direction.as_str(), symbol, acc.client_id, broker_label, attempt, max_retries);
                                if attempt < max_retries { sleep(Duration::from_secs(2)).await; }
                            } else {
                                tracing::warn!("[LIVE] ⚠️ Order attempt {}/{} failed: {} {} account={} broker={} error={}",
                                    attempt, max_retries, order_direction.as_str(), symbol, acc.client_id, broker_label, last_err);
                                if attempt < max_retries { sleep(Duration::from_secs(1)).await; }
                            }
                        }
                    }
                }

                if !success {
                    tracing::error!("[LIVE] ❌ {} FAILED after {} retries: {} {} account={} broker={} error={}",
                        type_lbl, max_retries, order_direction.as_str(), symbol, acc.client_id, broker_label, last_err);
                    insert_order_record(&ch, today, signal_id, &acc.client_id, &symbol,
                        "", &order_direction, quantity, otype, "REJECTED", None).await.ok();
                }

                sleep(Duration::from_millis(125)).await;
                drop(permit);
                success  // ← return whether this account succeeded
            }));
        }

        // Wait for all account tasks and check if at least one broker placed the order.
        // Only send entry_reject if EVERY broker failed — if Dhan succeeded but Zerodha
        // failed, the position is real (Dhan filled it) and must stay in open_signals.
        let mut any_placed = false;
        for h in handles {
            match h.await {
                Ok(placed) => { if placed { any_placed = true; } }
                Err(e)     => { tracing::error!("Order task panicked: {:?}", e); }
            }
        }

        if order_type == 1 && !any_placed {
            if let Some(ref rtx) = entry_reject_tx {
                let sym = &signal.symbol;
                tracing::error!(
                    "[LIVE] 🗑️ ALL brokers failed for {} — removing phantom position from open_signals", sym
                );
                let _ = rtx.send((sym.clone(), signal_id));
            }
        }

        Ok(())
    }
}

/// Cancel a broker-side SL order. Dispatches to the correct broker.
///
/// Returns `Ok(true)` if the order was successfully cancelled.
/// Returns `Ok(false)` if the order was already traded/completed (SL triggered).
/// Returns `Err` for network or auth failures.
pub async fn cancel_sl_order(
    broker: &str,
    order_id: &str,
    api_key: &str,
    access_token: &str,
    client_id: &str,
    dhan_base_url: &str,
) -> Result<bool> {
    if broker == "ZERODHA" {
        let kite = crate::zerodha::client::ZerodhaClient::new(api_key, access_token);
        crate::zerodha::orders::cancel_order(&kite, order_id).await
    } else {
        // Build a minimal DhanClient just for the cancel call
        let dhan = crate::dhan::client::DhanClient::new(&Config {
            dhan_base_url: dhan_base_url.to_string(),
            dhan_quote_endpoint: String::new(),
            dhan_orders_endpoint: String::new(),
            dhan_positions_endpoint: String::new(),
            dhan_access_token: access_token.to_string(),
            dhan_client_id: client_id.to_string(),
            clickhouse_url: String::new(),
            debug: false,
            ws_subscribe_fno_oi: false,
            gemini_api_key: String::new(),
        });
        crate::dhan::orders::cancel_order(&dhan, order_id).await
    }
}

/// Place a protective SL order after an entry fill.
/// Returns (sl_order_id, sl_trigger_price, fallback_to_software).
async fn place_protective_sl(
    broker: &str,
    api_key: &str,
    access_token: &str,
    cfg: &Config,
    security_id: &str,
    symbol: &str,
    position_direction: &Direction,
    quantity: u32,
    fill_price: f32,
    sl_pct: f32,
    endpoint: &str,
) -> (Option<String>, f32, bool) {
    // Compute SL trigger from actual fill price
    let sl_trigger = match position_direction {
        Direction::Sell => fill_price * (1.0 + sl_pct / 100.0),  // short: SL above entry
        Direction::Buy  => fill_price * (1.0 - sl_pct / 100.0),  // long: SL below entry
    };

    let result = if broker == "ZERODHA" {
        let kite = crate::zerodha::client::ZerodhaClient::new(api_key, access_token);
        crate::zerodha::orders::place_sl_order(&kite, symbol, position_direction, quantity, sl_trigger).await
    } else {
        let dhan = DhanClient::new(cfg);
        crate::dhan::orders::place_sl_order(&dhan, security_id, position_direction, quantity, sl_trigger, endpoint).await
    };

    match result {
        Ok(sl_oid) => {
            tracing::info!("[SL] ✅ {} broker={} trigger={:.2} order_id={}", symbol, broker, sl_trigger, sl_oid);
            (Some(sl_oid), sl_trigger, false)
        }
        Err(e) => {
            tracing::error!("[SL] ❌ {} broker={} FAILED — falling back to software SL: {}", symbol, broker, e);
            (None, sl_trigger, true)
        }
    }
}

async fn insert_order_record(
    ch: &ChClient, trading_date: chrono::NaiveDate,
    signal_id: Uuid, account_client_id: &str, symbol: &str,
    dhan_order_id: &str, direction: &Direction,
    quantity: u32, order_type: u8, status: &str, filled_price: Option<f32>,
) -> Result<()> {
    fn to_ch_date(d: chrono::NaiveDate) -> u32 {
        let epoch = chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
        (d - epoch).num_days() as u32
    }
    let dir_val: u8 = if *direction == Direction::Buy { 1 } else { 2 };
    let status_val: u8 = match status {
        "PENDING" | "TRANSIT" | "PARTIALLY_TRADED" => 1,
        "FILLED" | "TRADED"                        => 2,
        "REJECTED"                                 => 3,
        _                                          => 4,
    };
    let fp = filled_price.unwrap_or(0.0);
    ch.query(
        "INSERT INTO trading.orders (trading_date, signal_id, account_client_id, symbol, \
         dhan_order_id, direction, quantity, order_type, status, filled_price) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    .bind(to_ch_date(trading_date))
    .bind(signal_id.to_string())
    .bind(account_client_id)
    .bind(symbol)
    .bind(dhan_order_id)
    .bind(dir_val)
    .bind(quantity)
    .bind(order_type)
    .bind(status_val)
    .bind(fp)
    .execute().await?;
    Ok(())
}

/// Poll an order until it reaches a terminal state or the trading session ends.
///
/// ## Why this is more than a simple retry loop
///
/// Since March 21 2025, Dhan converts ALL API MARKET orders to LIMIT orders with
/// Market Price Protection (MPP).  The converted limit price may be outside the
/// current best bid/offer, so the order stays PENDING on the exchange order book
/// — potentially for the ENTIRE trading session — and fills only when the market
/// price moves into the MPP band.
///
/// Consequence: a PENDING status after 50 seconds does NOT mean the order failed.
/// It means the order is LIVE and should stay in open_signals.
///
/// ## True terminal states (confirmed not filled → remove phantom position)
///   REJECTED  — broker/exchange refused the order
///   EXPIRED   — DAY order reached EOD without filling
///   CANCELLED — explicitly cancelled (user or auto-square-off)
///
/// ## Live states (order still on the book → keep polling)
///   PENDING, TRANSIT    — order active, not yet traded
///   PART_TRADED         — partially filled, residual quantity still live
///
/// ## Polling schedule
///   Quick phase : 5s, 5s, 10s  (catch instant fills)
///   Fallback    : if still PENDING after quick phase AND this is an entry order,
///                 cancel the MARKET order and place an explicit LIMIT at
///                 entry_ltp ± fallback_slippage_pct% to guarantee execution.
///   Long phase  : every 60s until 15:25 IST (session_cutoff_utc = 09:55 UTC)
///                 If still unresolved by then, treat as EXPIRED.
async fn poll_order_status(
    ch: ChClient, cfg: Config,
    trading_date: chrono::NaiveDate, signal_id: Uuid,
    account_client_id: &str, symbol: &str,
    order_id: &str, direction: &Direction, quantity: u32, order_type: u8,
    fill_tx: Option<tokio::sync::mpsc::UnboundedSender<FillUpdate>>,
    // Sends (symbol, signal_id) when entry is confirmed NOT filled.
    // Poller removes the phantom position to prevent a phantom exit order.
    entry_reject_tx: Option<tokio::sync::mpsc::UnboundedSender<(String, Uuid)>>,
    broker: &str, api_key: &str, access_token: &str,
    security_id: &str, position_direction: &Direction, sl_pct: f32,
    orders_endpoint: &str,
    // LTP at signal time — used to compute fallback LIMIT price.
    entry_ltp: f32,
    // Slippage % for LIMIT fallback (e.g. 0.3). 0.0 = no fallback (exit orders).
    fallback_slippage_pct: f32,
) {
    let mut last_status = String::from("UNKNOWN");
    let mut last_avg_price: f32 = 0.0;
    let mut attempt = 0u32;
    // Tracks the currently-active order id — switches to LIMIT fallback id if retried.
    let mut active_order_id = order_id.to_string();

    // ── Macro: fetch status for active_order_id ─────────────────────────────────
    macro_rules! do_fetch {
        () => {{
            if broker == "ZERODHA" {
                let kite = crate::zerodha::client::ZerodhaClient::new(api_key, access_token);
                crate::zerodha::orders::get_order_status(&kite, &active_order_id).await
                    .map(|s| (s.order_status, s.average_traded_price))
            } else {
                let dhan = DhanClient::new(&cfg);
                crate::dhan::orders::get_order_status(&dhan, &active_order_id).await
                    .map(|s| (s.order_status.unwrap_or_else(|| "UNKNOWN".into()), s.average_traded_price))
            }
        }};
    }

    // ── Phase 1: quick polls (5s, 5s, 10s) ────────────────────────────────────
    for delay in [5u64, 5, 10] {
        sleep(Duration::from_secs(delay)).await;
        attempt += 1;

        match do_fetch!() {
            Ok((raw, price)) => {
                tracing::info!(
                    "[LIVE] {} order_id={} broker={} status={} avg_price={:.2} (quick poll #{})",
                    symbol, active_order_id, broker, raw, price, attempt
                );
                last_status = raw.clone();
                last_avg_price = price;

                let is_terminal = matches!(
                    raw.as_str(),
                    "TRADED" | "COMPLETE" | "REJECTED" | "EXPIRED" | "CANCELLED"
                );
                if is_terminal {
                    process_terminal(
                        &ch, &cfg, trading_date, signal_id, account_client_id, symbol,
                        &active_order_id, direction, quantity, order_type,
                        &fill_tx, &entry_reject_tx,
                        broker, api_key, access_token, security_id, position_direction,
                        sl_pct, orders_endpoint,
                        &raw, price,
                    ).await;
                    return;
                }
                if raw == "PART_TRADED" {
                    tracing::info!(
                        "[LIVE] {} order {} PART_TRADED (avg={:.2}) — keep polling for full fill",
                        symbol, active_order_id, price
                    );
                }
            }
            Err(e) => {
                tracing::warn!("[LIVE] Status poll error {} #{}: {}", symbol, attempt, e);
            }
        }
    }

    // ── LIMIT fallback: cancel stale MARKET order, retry with explicit LIMIT ────
    // Fires only for ENTRY orders (order_type==1) still PENDING after quick polls.
    // Cancels the MPP-stuck MARKET order and places a tight explicit LIMIT.
    // EXIT orders always skip this (they use MARKET and fill within seconds).
    if order_type == 1
        && fallback_slippage_pct > 0.0
        && entry_ltp > 0.0
        && matches!(last_status.as_str(), "PENDING" | "TRANSIT" | "UNKNOWN")
    {
        tracing::warn!(
            "[LIVE] ⚡ {} MARKET order {} still '{}' after quick polls (20s). \
             Cancelling and retrying with LIMIT at ±{:.2}% slippage.",
            symbol, active_order_id, last_status, fallback_slippage_pct
        );

        // Step 1: Cancel the stuck MARKET order (best-effort)
        let cancel_result = if broker == "ZERODHA" {
            let kite = crate::zerodha::client::ZerodhaClient::new(api_key, access_token);
            crate::zerodha::orders::cancel_order(&kite, &active_order_id).await
        } else {
            let dhan = DhanClient::new(&cfg);
            crate::dhan::orders::cancel_order(&dhan, &active_order_id).await
        };
        match &cancel_result {
            Ok(cancelled) => tracing::info!(
                "[LIVE] {} MARKET order {} cancel: cancelled={}",
                symbol, active_order_id, cancelled
            ),
            Err(e) => tracing::warn!(
                "[LIVE] {} MARKET order {} cancel failed (may have already filled): {}",
                symbol, active_order_id, e
            ),
        }

        // Check if it filled during the cancel attempt
        match do_fetch!() {
            Ok((raw, price)) if matches!(raw.as_str(), "TRADED" | "COMPLETE") => {
                tracing::info!(
                    "[LIVE] {} MARKET order {} filled (avg={:.2}) during cancel window — no LIMIT retry.",
                    symbol, active_order_id, price
                );
                process_terminal(
                    &ch, &cfg, trading_date, signal_id, account_client_id, symbol,
                    &active_order_id, direction, quantity, order_type,
                    &fill_tx, &entry_reject_tx,
                    broker, api_key, access_token, security_id, position_direction,
                    sl_pct, orders_endpoint,
                    &raw, price,
                ).await;
                return;
            }
            Ok((raw, _)) => {
                tracing::info!(
                    "[LIVE] {} MARKET order {} status after cancel: {}", symbol, active_order_id, raw
                );
            }
            Err(e) => {
                tracing::warn!("[LIVE] {} status check after cancel failed: {}", symbol, e);
            }
        }

        // Step 2: Place LIMIT order with fallback slippage
        let raw_limit = match direction {
            Direction::Sell => entry_ltp * (1.0 - fallback_slippage_pct / 100.0),
            Direction::Buy  => entry_ltp * (1.0 + fallback_slippage_pct / 100.0),
        };
        let limit_price = (raw_limit * 20.0).round() / 20.0;  // ₹0.05 NSE tick

        tracing::info!(
            "[LIVE] ⚡ {} placing LIMIT fallback {} @ {:.2} (entry_ltp={:.2} slippage={:.2}%)",
            symbol, direction.as_str(), limit_price, entry_ltp, fallback_slippage_pct
        );

        let limit_result = if broker == "ZERODHA" {
            Err(anyhow::anyhow!("Zerodha LIMIT fallback not implemented"))
        } else {
            let dhan = DhanClient::new(&cfg);
            crate::dhan::orders::place_order(
                &dhan, security_id, direction, quantity, limit_price, orders_endpoint,
            ).await
        };

        match limit_result {
            Ok(new_order_id) => {
                tracing::info!(
                    "[LIVE] ✅ LIMIT fallback placed: {} {} order_id={}",
                    symbol, direction.as_str(), new_order_id
                );
                insert_order_record(
                    &ch, trading_date, signal_id, account_client_id, symbol,
                    &new_order_id, direction, quantity, order_type, "PENDING", None,
                ).await.ok();

                // Switch to polling the new LIMIT order
                active_order_id = new_order_id;
                attempt = 0;
                last_status = "PENDING".to_string();

                // Quick-poll the LIMIT fallback (5s, 5s, 10s)
                for delay in [5u64, 5, 10] {
                    sleep(Duration::from_secs(delay)).await;
                    attempt += 1;
                    match do_fetch!() {
                        Ok((raw, price)) => {
                            tracing::info!(
                                "[LIVE] {} LIMIT-fallback order_id={} status={} avg={:.2} (poll #{})",
                                symbol, active_order_id, raw, price, attempt
                            );
                            last_status = raw.clone();
                            last_avg_price = price;
                            if matches!(raw.as_str(), "TRADED" | "COMPLETE" | "REJECTED" | "EXPIRED" | "CANCELLED") {
                                process_terminal(
                                    &ch, &cfg, trading_date, signal_id, account_client_id, symbol,
                                    &active_order_id, direction, quantity, order_type,
                                    &fill_tx, &entry_reject_tx,
                                    broker, api_key, access_token, security_id, position_direction,
                                    sl_pct, orders_endpoint,
                                    &raw, price,
                                ).await;
                                return;
                            }
                        }
                        Err(e) => {
                            tracing::warn!("[LIVE] LIMIT-fallback poll error {} #{}: {}", symbol, attempt, e);
                        }
                    }
                }
                // LIMIT fallback also didn't fill in 20s — fall into long-poll below
            }
            Err(e) => {
                tracing::error!(
                    "[LIVE] ❌ {} LIMIT fallback placement FAILED: {} — removing phantom position",
                    symbol, e
                );
                if let Some(ref rtx) = entry_reject_tx {
                    let _ = rtx.send((symbol.to_string(), signal_id));
                }
                return;
            }
        }
    }

    // ── Phase 2: long-poll until 15:25 IST (09:55 UTC) ───────────────────────
    // Only ENTRY orders need long-polling — MPP LIMIT can sit PENDING all session.
    // Exit orders are always MARKET and fill within seconds.
    if order_type == 1 && matches!(last_status.as_str(),
        "PENDING" | "TRANSIT" | "PART_TRADED" | "UNKNOWN")
    {
        tracing::warn!(
            "[LIVE] ⏳ {} order {} still '{}' after quick polls. \
             Switching to long-poll (every 60s) until 15:25 IST.",
            symbol, active_order_id, last_status
        );

        // 15:25 IST = 09:55 UTC
        const CUTOFF_H: u32 = 9;
        const CUTOFF_M: u32 = 55;
        let mut long_attempts = 0u32;

        loop {
            let now_utc = chrono::Utc::now();
            let past_cutoff = now_utc.hour() > CUTOFF_H
                || (now_utc.hour() == CUTOFF_H && now_utc.minute() >= CUTOFF_M);

            if past_cutoff {
                tracing::warn!(
                    "[LIVE] ⌛ {} order {} reached session cutoff (15:25 IST) \
                     with last_status='{}'. Dhan auto-square-off imminent. \
                     Treating as EXPIRED → removing phantom position from open_signals.",
                    symbol, active_order_id, last_status
                );
                if let Some(ref rtx) = entry_reject_tx {
                    let _ = rtx.send((symbol.to_string(), signal_id));
                }
                insert_order_record(
                    &ch, trading_date, signal_id, account_client_id,
                    symbol, &active_order_id, direction, quantity, order_type,
                    "EXPIRED", None,
                ).await.ok();
                return;
            }

            sleep(Duration::from_secs(60)).await;
            attempt += 1;
            long_attempts += 1;

            match do_fetch!() {
                Ok((raw, price)) => {
                    tracing::info!(
                        "[LIVE] {} order_id={} broker={} status={} avg_price={:.2} \
                         (long-poll #{}, total #{})",
                        symbol, active_order_id, broker, raw, price, long_attempts, attempt
                    );
                    last_status = raw.clone();
                    last_avg_price = price;

                    let is_terminal = matches!(
                        raw.as_str(),
                        "TRADED" | "COMPLETE" | "REJECTED" | "EXPIRED" | "CANCELLED"
                    );
                    if is_terminal {
                        process_terminal(
                            &ch, &cfg, trading_date, signal_id, account_client_id, symbol,
                            &active_order_id, direction, quantity, order_type,
                            &fill_tx, &entry_reject_tx,
                            broker, api_key, access_token, security_id, position_direction,
                            sl_pct, orders_endpoint,
                            &raw, price,
                        ).await;
                        return;
                    }
                }
                Err(e) => {
                    tracing::warn!(
                        "[LIVE] Long-poll status error {} #{}: {}", symbol, attempt, e
                    );
                }
            }
        }
    }

    // ── Fallback: exit orders / non-entry that didn't reach terminal ───────────
    tracing::warn!(
        "[LIVE] {} order_id={} broker={} exhausted all polls (last={} order_type={})",
        symbol, active_order_id, broker, last_status, order_type
    );
    insert_order_record(
        &ch, trading_date, signal_id, account_client_id,
        symbol, &active_order_id, direction, quantity, order_type,
        &last_status, if last_avg_price > 0.0 { Some(last_avg_price) } else { None },
    ).await.ok();
}

/// Process a terminal order status: handle fill, SL placement, rejection, and DB update.
#[allow(clippy::too_many_arguments)]
async fn process_terminal(
    ch: &ChClient, cfg: &Config,
    trading_date: chrono::NaiveDate, signal_id: Uuid,
    account_client_id: &str, symbol: &str,
    order_id: &str, direction: &Direction, quantity: u32, order_type: u8,
    fill_tx: &Option<tokio::sync::mpsc::UnboundedSender<FillUpdate>>,
    entry_reject_tx: &Option<tokio::sync::mpsc::UnboundedSender<(String, Uuid)>>,
    broker: &str, api_key: &str, access_token: &str,
    security_id: &str, position_direction: &Direction,
    sl_pct: f32, orders_endpoint: &str,
    raw_status: &str, avg_price: f32,
) {
    let is_filled = matches!(raw_status, "TRADED" | "COMPLETE");
    let filled = if avg_price > 0.0 { Some(avg_price) } else { None };

    if !is_filled && order_type == 1 {
        // REJECTED, EXPIRED, or CANCELLED for an entry order.
        // The position was added to open_signals optimistically — remove it now
        // so no phantom exit order is ever placed for shares we don't own.
        tracing::error!(
            "[LIVE] 🗑️ ENTRY_REJECT: {} order_id={} broker={} status={} \
             — removing phantom position from open_signals",
            symbol, order_id, broker, raw_status
        );
        if let Some(ref rtx) = entry_reject_tx {
            let _ = rtx.send((symbol.to_string(), signal_id));
        }
    }

    if is_filled && order_type == 1 && avg_price > 0.0 {
        let (sl_oid, sl_trigger, sl_fallback) = if sl_pct > 0.0 {
            place_protective_sl(
                broker, api_key, access_token, cfg,
                security_id, symbol, position_direction,
                quantity, avg_price, sl_pct, orders_endpoint,
            ).await
        } else {
            (None, 0.0, true)
        };

        if let Some(ref sl_order_id) = sl_oid {
            let sl_dir = match position_direction {
                Direction::Buy => Direction::Sell,
                Direction::Sell => Direction::Buy,
            };
            insert_order_record(
                ch, trading_date, signal_id, account_client_id,
                symbol, sl_order_id, &sl_dir, quantity,
                3, "PENDING", None,
            ).await.ok();
        }

        if let Some(ref tx) = fill_tx {
            let _ = tx.send(FillUpdate {
                symbol: symbol.to_string(),
                account_client_id: account_client_id.to_string(),
                fill_price: avg_price,
                signal_id,
                sl_order_id: sl_oid,
                sl_broker: broker.to_string(),
                sl_trigger_price: sl_trigger,
                sl_fallback_software: sl_fallback,
                broker_api_key: api_key.to_string(),
                broker_access_token: access_token.to_string(),
            });
            tracing::info!(
                "[FILL] {} fill_price={:.2} broker={} sl_placed={} (order {})",
                symbol, avg_price, broker, !sl_fallback, order_id
            );
        }
    }

    let db_status = if is_filled { "TRADED" } else { raw_status };
    insert_order_record(
        ch, trading_date, signal_id, account_client_id,
        symbol, order_id, direction, quantity, order_type,
        db_status, filled,
    ).await.ok();
}
