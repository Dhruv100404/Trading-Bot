use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio::sync::{broadcast, Mutex, RwLock};
use clickhouse::Client as ChClient;
use chrono::{Timelike, Datelike};
use anyhow::Result;

use crate::config::Config;
use crate::types::{Gap15Config, Signal, Direction, WsEvent, compute_bucket, now_ist, today_ist};
use crate::derived::{compute as compute_derived, RunningState};
use crate::dhan::{client::DhanClient, market_data::fetch_quotes};
use crate::db::{snapshots, signals as sig_db, watchlist as wl_db};
use crate::exit_manager::{check_exit, check_exit_tp_time, ExitReason};
use crate::order_executor::{OrderExecutor, cancel_sl_order};
use crate::ws_feed::{self};

const WINDOW_START_H: u32 = 9;
const WINDOW_START_M: u32 = 14;
const WINDOW_END_H:   u32 = 15;
const WINDOW_END_M:   u32 = 31;

/// Primary entry bucket: 9:16 AM IST.
const ENTRY_BUCKET: u16 = 2;
/// Grace window: accept delayed entry up to bucket ENTRY_BUCKET + ENTRY_GRACE.
const ENTRY_GRACE: u16 = 3;
/// Circuit breaker: pause polling after this many consecutive full-failure cycles.
const MAX_CONSECUTIVE_FAILURES: u32 = 5;
/// WS tick freshness threshold for entry health check (seconds).
/// Kept at 30s to match main-loop staleness definition.
const WS_ENTRY_STALE_SECS: u64 = 30;

// ─────────────────────────────────────────────────────────
//  Data structures
// ─────────────────────────────────────────────────────────

/// Tracks an open position with its broker-side SL orders.
struct OpenPosition {
    signal:    Signal,
    signal_id: uuid::Uuid,
    last_ltp:  f32,
    /// Per-account SL order tracking (populated later via fill_rx).
    sl_orders: Vec<SlOrderTracker>,
}

impl OpenPosition {
    /// True if all accounts have a live broker-side SL order (no software fallback needed).
    fn all_sl_on_broker(&self) -> bool {
        !self.sl_orders.is_empty()
            && self.sl_orders.iter().all(|sl| !sl.fallback_software && sl.sl_order_id.is_some())
    }
}

/// Tracks a single broker-side SL order for one account.
#[derive(Clone)]
struct SlOrderTracker {
    account_client_id: String,
    broker:            String,
    sl_order_id:       Option<String>,
    sl_trigger_price:  f32,
    fallback_software: bool,
    api_key:           String,
    access_token:      String,
}

// ─────────────────────────────────────────────────────────
//  WS-driven entry: types
// ─────────────────────────────────────────────────────────

/// Outcome sent by ws_driven_gap15_entry_task back to the main loop via oneshot.
#[derive(Debug)]
enum EntryOutcome {
    /// WS was healthy; entry scan ran (0 or more candidates were traded).
    Fired,
    /// WS was stale/disconnected at entry time — main loop must use poll fallback.
    WsFailed { reason: String },
}

/// All inputs the WS-driven entry task needs.
///
/// Filter maps are cloned once at spawn time (cheap: ~410-entry HashMaps/HashSets).
/// The task owns all inputs — no shared mutable state except the two Arc-guarded
/// objects (`shared_ticks` and `fired_today`) which it reads/locks briefly.
struct EntryContext {
    // ── Read-only filter snapshot (cloned at spawn time) ──
    gap_pct_cache:      HashMap<String, f32>,
    large_mega_symbols: HashSet<String>,
    mis5_symbols:       HashSet<String>,
    strategy_config:    Gap15Config,
    /// sec_id → symbol (cloned from the persistent symbol_map in run())
    symbol_map:         HashMap<String, String>,
    trading_date:       chrono::NaiveDate,

    // ── Live tick snapshot (written by main loop every cycle, read at 9:16:00) ──
    /// Combined REST baseline + WS overwrite; main loop keeps this current.
    shared_ticks: Arc<RwLock<HashMap<String, f32>>>,

    // ── WS health oracle (shared RwLock with ws_feed task) ──
    live_feed: crate::ws_feed::SharedFeed,

    // ── Dedup: already-fired symbols for today ──
    fired_today: Arc<Mutex<HashSet<String>>>,

    // ── Results back to the main loop ──
    /// New OpenPosition per signal fired (main loop inserts into open_signals).
    new_pos_tx: tokio::sync::mpsc::UnboundedSender<(String, OpenPosition)>,
    /// Oneshot: task sends exactly one Fired or WsFailed back to the main loop.
    result_tx: tokio::sync::oneshot::Sender<EntryOutcome>,

    // ── Execution ──
    ch:       ChClient,
    executor: OrderExecutor,
    fill_tx:  tokio::sync::mpsc::UnboundedSender<crate::order_executor::FillUpdate>,
    /// Sent by poll_order_status when entry order is confirmed NOT filled.
    /// WS entry task passes this to executor.execute() so phantom positions get cleaned up.
    entry_reject_tx: tokio::sync::mpsc::UnboundedSender<(String, uuid::Uuid)>,
    /// Broadcast channel for UI/client WsEvent notifications.
    ws_tx: Arc<broadcast::Sender<String>>,
}

// ─────────────────────────────────────────────────────────
//  Helper: load volume group symbols
// ─────────────────────────────────────────────────────────

fn load_symbols_for_groups(enabled_groups: &HashSet<String>) -> HashSet<String> {
    let paths = ["data/volume_groups.json", "../data/volume_groups.json"];
    for path in &paths {
        if let Ok(text) = std::fs::read_to_string(path) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                let mut set = HashSet::new();
                if let Some(groups) = v.get("volume_groups").and_then(|g| g.as_object()) {
                    for (key, syms) in groups {
                        let group_key = if key.contains("MEGA")  { "MEGA"  }
                            else if key.contains("LARGE") { "LARGE" }
                            else if key.contains("MID")   { "MID"   }
                            else if key.contains("SMALL") { "SMALL" }
                            else { continue };
                        if !enabled_groups.contains(group_key) { continue; }
                        if let Some(arr) = syms.as_array() {
                            for s in arr {
                                if let Some(sym) = s.as_str() {
                                    set.insert(sym.to_string());
                                }
                            }
                        }
                    }
                }
                if !set.is_empty() {
                    tracing::info!("[GAP15] Loaded {} eligible symbols from {}", set.len(), path);
                    return set;
                }
            }
        }
    }
    tracing::error!("[GAP15] volume_groups.json not found — cap-group filter disabled (fail-open)");
    HashSet::new()
}

// ─────────────────────────────────────────────────────────
//  Helper: claim exit (dedup)
// ─────────────────────────────────────────────────────────

/// Attempt to claim the exit for `symbol` exactly once.
/// Returns true on first claim, false if already claimed (duplicate → skip).
#[inline]
fn claim_exit(exited_symbols: &mut HashSet<String>, symbol: &str) -> bool {
    exited_symbols.insert(symbol.to_string())
}

// ─────────────────────────────────────────────────────────
//  Helper: start / restart WS feed task
// ─────────────────────────────────────────────────────────

async fn start_ws_task(
    active_stocks: &[(String, String)],
    config: &Config,
    ch: &ChClient,
    live_feed: crate::ws_feed::SharedFeed,
    tick_tx: tokio::sync::mpsc::Sender<(String, f32)>,
    label: &str,
) -> Option<tokio::task::JoinHandle<()>> {
    if active_stocks.is_empty() {
        tracing::warn!("[WS] {}: no active stocks — WS not started", label);
        return None;
    }
    let equity_instruments: Vec<(String, u32)> = active_stocks.iter()
        .filter_map(|(sid, sym)| sid.parse::<u32>().ok().map(|id| (sym.clone(), id)))
        .collect();
    let fno_instruments = if config.ws_subscribe_fno_oi {
        ws_feed::load_futures_mapping(&equity_instruments).await
    } else {
        vec![]
    };
    let (ws_token, ws_cid) = crate::api::settings::get_market_data_token(ch).await;
    if ws_token.is_empty() {
        tracing::warn!("[WS] {}: no market data token — WS disabled (poll-only mode)", label);
        return None;
    }
    let n = equity_instruments.len();
    let handle = tokio::spawn(async move {
        ws_feed::run_ws_feed(live_feed, ws_token, ws_cid, equity_instruments, fno_instruments, tick_tx).await;
    });
    tracing::info!("[WS] {} ✅ Started for {} stocks", label, n);
    Some(handle)
}

// ─────────────────────────────────────────────────────────
//  WS-DRIVEN ENTRY TASK  (primary path)
// ─────────────────────────────────────────────────────────

/// Dedicated async task for WebSocket-driven GAP15 entry.
///
/// ## Flow
/// 1. Sleep until **exactly 9:16:00.000 IST** (nanosecond target, ~1ms Tokio resolution).
/// 2. Validate grace window (abort if past `ENTRY_BUCKET + ENTRY_GRACE`).
/// 3. Check WS health: connected + last tick < 30s → if bad, send `WsFailed` → poll fallback.
/// 4. Snapshot `shared_ticks` (REST baseline + WS overwrite, maintained by main loop).
/// 5. Run full filter pipeline (gap%, price, cap-group, MIS-5, top-N).
/// 6. **Send `EntryOutcome::Fired` IMMEDIATELY** (before any I/O) to prevent poll fallback race.
/// 7. Build Signal structs, parallel DB insert, spawn order execution per signal.
/// 8. Send new `OpenPosition` entries to main loop via `new_pos_tx` for tracking.
///
/// ## Fallback guarantee
/// If this task returns without sending on `result_tx`, the main loop's supervision
/// detects the missing result via `entry_outcome_received` and triggers poll fallback.
async fn ws_driven_gap15_entry_task(ctx: EntryContext) {
    // ── Step 1: Sleep until exactly 9:16:00.000 IST ──
    {
        let now = now_ist();
        let h = now.hour() as i64;
        let m = now.minute() as i64;
        let s = now.second() as i64;
        let ns = now.nanosecond() as i64;
        // Nanoseconds elapsed since 9:00:00 today (in IST)
        let elapsed_since_9am_ns = (h - 9) * 3_600_000_000_000i64
            + m       * 60_000_000_000i64
            + s       *  1_000_000_000i64
            + ns;
        // Target: 9:16:00.000 = 16 minutes * 60 * 1e9 ns
        let target_ns = 16i64 * 60 * 1_000_000_000;
        let wait_ns = (target_ns - elapsed_since_9am_ns).max(0);

        if wait_ns > 0 {
            tracing::info!("[WS-ENTRY] ⏳ Sleeping {:.3}s until 9:16:00.000 IST",
                wait_ns as f64 / 1_000_000_000.0);
            tokio::time::sleep(std::time::Duration::from_nanos(wait_ns as u64)).await;
        } else {
            let late_ms = (-wait_ns) / 1_000_000;
            tracing::info!("[WS-ENTRY] Already past 9:16:00 ({}ms late) — proceeding immediately", late_ms);
        }
    }

    let now_entry = now_ist();
    let current_bucket = compute_bucket(&now_entry);

    // ── Step 2: Grace window check ──
    if current_bucket > ENTRY_BUCKET + ENTRY_GRACE {
        let msg = format!("woke at bucket {} (grace ends at {})", current_bucket, ENTRY_BUCKET + ENTRY_GRACE);
        tracing::error!("[WS-ENTRY] ⏰ {} — aborting, poll fallback will not help either", msg);
        let _ = ctx.result_tx.send(EntryOutcome::WsFailed { reason: msg });
        return;
    }
    if current_bucket > ENTRY_BUCKET {
        tracing::warn!("[WS-ENTRY] ⚠️ Entry delayed — running at bucket {} (target was {})",
            current_bucket, ENTRY_BUCKET);
    }

    // ── Step 3: WS health check ──
    //
    // Normal check: ws_connected=true AND last_tick < WS_ENTRY_STALE_SECS (30s).
    //
    // Market-open grace (bucket 1–3, i.e. 9:15–9:17 AM):
    //   The WS feed connects in pre-market (9:10–9:14 AM) but may not receive
    //   any ticks until the market actually opens at 9:15:00.  Between 9:15:00
    //   and 9:16:00 (when this task fires), the very first batch of ticks is
    //   in-flight.  `last_tick_time` could therefore be up to ~60s stale even
    //   on a perfectly healthy connection.
    //
    //   We relax the staleness threshold to 90s during bucket 1–3, and also
    //   check that shared_ticks has the REST baseline (≥100 symbols) as a
    //   proxy for "the poll ran and the connection is working".
    let (ws_ok, ws_health_msg) = {
        let feed = ctx.live_feed.read().await;
        let connected = feed.ws_connected;
        let last_tick_age_secs = feed.last_tick_time.map(|t| t.elapsed().as_secs());

        // At market open (bucket 1-3) be lenient: up to 90s since last tick is fine.
        let stale_threshold = if current_bucket <= 3 { 90u64 } else { WS_ENTRY_STALE_SECS };
        let fresh = last_tick_age_secs.map(|age| age < stale_threshold).unwrap_or(false);

        // Market-open grace: if we're in the first 3 buckets, the connection is live,
        // and shared_ticks has the REST baseline, trust the WS even with no ticks yet.
        let shared_ticks_size = ctx.shared_ticks.read().await.len();
        let market_open_grace = current_bucket <= 3
            && connected
            && shared_ticks_size >= 100
            && last_tick_age_secs.map(|age| age < 90).unwrap_or(true);

        let ok = connected && (fresh || market_open_grace);
        let msg = if !connected {
            format!("ws_connected=false (shared_ticks={})", shared_ticks_size)
        } else if ok && market_open_grace && !fresh {
            format!(
                "market-open grace (bucket={} last_tick={}s shared_ticks={})",
                current_bucket,
                last_tick_age_secs.unwrap_or(999),
                shared_ticks_size
            )
        } else if fresh {
            format!("healthy (last_tick={}s ago shared_ticks={})",
                last_tick_age_secs.unwrap_or(0), shared_ticks_size)
        } else {
            format!("last_tick={}s ago (stale, threshold={}s shared_ticks={})",
                last_tick_age_secs.unwrap_or(999), stale_threshold, shared_ticks_size)
        };
        (ok, msg)
    };

    if !ws_ok {
        tracing::warn!("[WS-ENTRY] ⚠️ WS unhealthy at entry: {} — signaling poll fallback", ws_health_msg);
        let _ = ctx.result_tx.send(EntryOutcome::WsFailed { reason: ws_health_msg });
        return;
    }
    tracing::info!("[WS-ENTRY] 🟢 WS {} at bucket {} — running entry scan",
        ws_health_msg, current_bucket);

    // ── Step 4: Snapshot latest tick LTPs ──
    // shared_ticks is kept current by the main loop:
    //   REST ltp_map (baseline for all symbols) + WS tick overwrite (fresher for active symbols).
    // We clone the whole map so we can release the read lock immediately.
    let tick_snapshot: HashMap<String, f32> = ctx.shared_ticks.read().await.clone();
    tracing::info!("[WS-ENTRY] Tick snapshot: {} symbols", tick_snapshot.len());

    if tick_snapshot.is_empty() {
        let msg = "tick_snapshot empty despite ws_connected=true (no ticks and no REST data yet)";
        tracing::error!("[WS-ENTRY] ❌ {} — aborting", msg);
        let _ = ctx.result_tx.send(EntryOutcome::WsFailed { reason: msg.into() });
        return;
    }

    // ── Step 5: Filter pipeline ──
    let scan_start = std::time::Instant::now();
    let mut candidates: Vec<(String, String, f32, f32)> = vec![]; // (symbol, sec_id, gap, ltp)
    {
        let fired = ctx.fired_today.lock().await; // acquired; no await below this line
        let mut n_fired    = 0u32;
        let mut n_no_gap   = 0u32;
        let mut n_no_ltp   = 0u32;
        let mut n_price    = 0u32;
        let mut n_cap      = 0u32;
        let mut n_mis5     = 0u32;
        let mut n_gap_pass = 0u32;

        for (sec_id, symbol) in &ctx.symbol_map {
            if fired.contains(symbol) { n_fired += 1; continue; }

            let gap = ctx.gap_pct_cache.get(symbol).copied().unwrap_or(0.0);
            if gap <= ctx.strategy_config.gap_min_pct || gap > ctx.strategy_config.gap_max_pct {
                n_no_gap += 1; continue;
            }
            n_gap_pass += 1;

            // Use tick_snapshot (REST baseline + WS overwrite — freshest available LTP)
            let ltp = match tick_snapshot.get(symbol).copied() {
                Some(l) => l,
                None => { n_no_ltp += 1; continue; }
            };
            if ltp <= 0.0 || ltp >= ctx.strategy_config.price_max { n_price += 1; continue; }

            if !ctx.large_mega_symbols.is_empty() && !ctx.large_mega_symbols.contains(symbol.as_str()) {
                n_cap += 1; continue;
            }
            if !ctx.mis5_symbols.is_empty() && !ctx.mis5_symbols.contains(symbol.as_str()) {
                n_mis5 += 1;
                tracing::debug!("[WS-ENTRY/MIS5] {} excluded — no 5x MIS margin today", symbol);
                continue;
            }
            candidates.push((symbol.clone(), sec_id.clone(), gap, ltp));
        }
        tracing::info!(
            "[WS-ENTRY] Filter: fired={} gap_pass={} no_gap={} no_ltp={} \
             price_fail={} cap_fail={} mis5_fail={} → {} raw candidates",
            n_fired, n_gap_pass, n_no_gap, n_no_ltp, n_price, n_cap, n_mis5, candidates.len()
        );
    } // fired lock released here

    // Sort by gap descending, take top N
    candidates.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
    candidates.truncate(ctx.strategy_config.top_n);
    let n = candidates.len();

    tracing::info!("[WS-ENTRY] 🎯 {} candidates (top-{}) identified in {:.1}ms via WS path",
        n, ctx.strategy_config.top_n, scan_start.elapsed().as_millis() as f64);

    // ── Step 6: Signal Fired IMMEDIATELY ──
    // This must happen BEFORE any I/O (DB insert, order execution).
    // It tells the main loop "WS path owns this entry cycle" so poll fallback never fires.
    if ctx.result_tx.send(EntryOutcome::Fired).is_err() {
        tracing::error!("[WS-ENTRY] result_tx already closed — main loop gone? Aborting.");
        return;
    }

    if n == 0 {
        tracing::info!("[WS-ENTRY] No qualifying candidates today — nothing to trade.");
        return;
    }

    // ── Step 7: Build Signal structs ──
    let pos_value = ctx.strategy_config.position_value(n);
    tracing::info!("[WS-ENTRY] Position value: ₹{} per trade (n={}, leverage={:.0}x)",
        pos_value, n, ctx.strategy_config.leverage);

    let signals: Vec<Signal> = candidates.iter().filter_map(|(symbol, sec_id, gap, entry_price)| {
        let qty = (pos_value as f32 / entry_price).floor() as u32;
        if qty == 0 {
            tracing::warn!("[WS-ENTRY] {} qty=0 at price={:.2} pos_value={} — skipping",
                symbol, entry_price, pos_value);
            return None;
        }
        Some(Signal {
            symbol:        symbol.clone(),
            security_id:   sec_id.clone(),
            trading_date:  ctx.trading_date,
            direction:     Direction::Sell,
            score:         (gap * 10.0).min(255.0) as u8,
            signals_fired: vec![format!("gap+{:.2}%_WS", gap)],
            entry_price:   *entry_price,
            entry_bucket:  current_bucket,
            entry_ts:      chrono::Utc::now().timestamp() as u32,
            tp_price:      ctx.strategy_config.tp_price(*entry_price),
            sl_price:      ctx.strategy_config.sl_price(*entry_price),
            quantity:      qty,
            open_price:    *entry_price,
            gap_pct:       *gap,
        })
    }).collect();

    for sig in &signals {
        tracing::info!("🔔⚡ [WS-ENTRY] SELL {} gap={:.2}% ws_ltp={:.2} qty={} TP={:.2} SL={:.2}",
            sig.symbol, sig.gap_pct, sig.entry_price, sig.quantity, sig.tp_price, sig.sl_price);
    }

    // ── Step 8: Parallel DB insert ──
    let insert_futs: Vec<_> = signals.iter().map(|sig| {
        let ch_c  = ctx.ch.clone();
        let sig_c = sig.clone();
        let cfg_c = ctx.strategy_config.clone();
        async move { (sig_c.symbol.clone(), sig_db::insert_signal(&ch_c, &sig_c, &cfg_c).await) }
    }).collect();
    let insert_results = futures::future::join_all(insert_futs).await;

    // ── Step 9: Spawn orders + register positions ──
    {
        let mut ft = ctx.fired_today.lock().await;
        let mut orders_spawned = 0u32;

        for (sig, (symbol, result)) in signals.into_iter().zip(insert_results.into_iter()) {
            match result {
                Err(e) => {
                    tracing::error!("[WS-ENTRY] ❌ Signal DB insert FAILED for {}: {} — skipping order",
                        symbol, e);
                }
                Ok(sig_id) => {
                    ft.insert(symbol.clone());

                    // Send new position to main loop for open_signals tracking
                    let pos = OpenPosition {
                        signal:    sig.clone(),
                        signal_id: sig_id,
                        last_ltp:  sig.entry_price,
                        sl_orders: vec![], // populated later by fill_rx
                    };
                    if ctx.new_pos_tx.send((symbol.clone(), pos)).is_err() {
                        tracing::error!("[WS-ENTRY] 💥 new_pos channel closed for {} — main loop gone?",
                            symbol);
                    }

                    // Spawn order execution (non-blocking)
                    let sig_c   = sig.clone();
                    let ch_c    = ctx.ch.clone();
                    let exec_c  = ctx.executor.clone();
                    let ftx_c   = ctx.fill_tx.clone();
                    let rtx_c   = ctx.entry_reject_tx.clone();
                    let sl_pct        = ctx.strategy_config.sl_pct;
                    let slippage_pct  = ctx.strategy_config.fallback_limit_slippage_pct;
                    tokio::spawn(async move {
                        tracing::info!("[WS-ENTRY] Submitting order: {} {} qty={} @ {:.2}",
                            sig_c.direction.as_str(), sig_c.symbol, sig_c.quantity, sig_c.entry_price);
                        if let Err(e) = exec_c.execute(&sig_c, sig_id, &ch_c, "", Some(ftx_c), Some(rtx_c), sl_pct, slippage_pct).await {
                            tracing::error!(
                                "[WS-ENTRY] 🚨 Order FAILED {}: {} — MANUAL ACTION NEEDED",
                                sig_c.symbol, e
                            );
                        }
                    });

                    // Broadcast signal to UI/clients
                    let evt = WsEvent {
                        event_type: "signal".into(),
                        data: serde_json::to_value(&sig).unwrap_or_default(),
                    };
                    let _ = ctx.ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                    orders_spawned += 1;
                }
            }
        }
        tracing::info!(
            "[WS-ENTRY] ✅ Entry complete: {}/{} SELL orders spawned in {:.0}ms total (WS path)",
            orders_spawned, n, scan_start.elapsed().as_millis()
        );
    }
}

// ─────────────────────────────────────────────────────────
//  Main polling loop
// ─────────────────────────────────────────────────────────

pub async fn run(
    ch: ChClient,
    config: Config,
    fired_today: Arc<Mutex<HashSet<String>>>,
    ws_tx: Arc<broadcast::Sender<String>>,
) -> Result<()> {
    // ── Load market data token from DB (not .env) ──
    let (md_token, md_client_id) = crate::api::settings::get_market_data_token(&ch).await;
    let mut dhan = if !md_token.is_empty() {
        tracing::info!("[INIT] Market data token loaded from DB (client_id={})", md_client_id);
        DhanClient::new(&Config {
            dhan_access_token: md_token,
            dhan_client_id: md_client_id,
            ..config.clone()
        })
    } else {
        tracing::warn!("[INIT] No market data token in DB — set it via Accounts UI");
        DhanClient::new(&config)
    };

    // Create executor once; clone it cheaply per-use.
    let executor = OrderExecutor::new(config.clone());

    // ── WebSocket infrastructure ──
    let live_feed = ws_feed::new_shared_feed();
    let (tick_tx, mut tick_rx) = tokio::sync::mpsc::channel::<(String, f32)>(50_000);
    let mut ws_task_handle: Option<tokio::task::JoinHandle<()>> = None;

    // ── Shared tick snapshot ──
    // Updated every poll cycle: REST ltp_map baseline + WS tick overwrite.
    // Read by ws_driven_gap15_entry_task at 9:16:00.
    let shared_ticks: Arc<RwLock<HashMap<String, f32>>> = Arc::new(RwLock::new(HashMap::new()));

    // ── WS entry task infrastructure ──
    // Oneshot for entry outcome (exactly one Fired or WsFailed per day).
    let (mut entry_done_tx, mut entry_done_rx) =
        tokio::sync::oneshot::channel::<EntryOutcome>();
    // Unbounded channel for new positions created by the WS entry task.
    let (new_pos_tx, mut new_pos_rx) =
        tokio::sync::mpsc::unbounded_channel::<(String, OpenPosition)>();
    let mut entry_task_handle: Option<tokio::task::JoinHandle<()>> = None;
    let mut ws_entry_task_spawned = false;
    let mut entry_outcome_received = false; // set when Fired or WsFailed arrives

    // open_signals: symbol → OpenPosition (signal + SL order tracking)
    let mut open_signals: HashMap<String, OpenPosition> = HashMap::new();

    // exited_symbols prevents double-execution when WS path and poll path both
    // detect an exit for the same symbol.
    let mut exited_symbols: HashSet<String> = HashSet::new();

    // Channel for fill-price updates from order_executor back to poller
    let (fill_tx, mut fill_rx) =
        tokio::sync::mpsc::unbounded_channel::<crate::order_executor::FillUpdate>();

    // Channel for entry rejections: sent when an entry order is confirmed NOT filled
    // (REJECTED, EXPIRED, or all poll retries exhausted without TRADED).
    // Poller removes the phantom position from open_signals to prevent a phantom exit order.
    let (entry_reject_tx, mut entry_reject_rx) =
        tokio::sync::mpsc::unbounded_channel::<(String, uuid::Uuid)>();

    // ── Derived state ──
    let mut derived_state: HashMap<String, RunningState> = HashMap::new();

    // ── Daily state variables ──
    let mut last_trading_date = today_ist();
    let mut historical_seeded_date: Option<chrono::NaiveDate> = None;
    let mut crash_recovery_done = false;
    let mut consecutive_failures = 0u32;
    let mut poll_fallback_needed = false;

    // Gap15 strategy state
    let mut gap_pct_cache: HashMap<String, f32> = HashMap::new();
    let mut large_mega_symbols: HashSet<String> = HashSet::new();
    let mut entry_fired_today = false;
    let mut strategy_config: Gap15Config = Gap15Config::default();
    let mut config_loaded_date: Option<chrono::NaiveDate> = None;
    let mut vol_groups_loaded_date: Option<chrono::NaiveDate> = None;

    // Persistent symbol_map (sec_id → symbol): rebuilt when active_stocks loads.
    // Replaces the local symbol_map that was previously computed every loop iteration.
    let mut symbol_map: HashMap<String, String> = HashMap::new();

    // Cache active stocks daily — single DB query per day
    let mut active_stocks_cache: Vec<(String, String)> = vec![];
    let mut active_stocks_date: Option<chrono::NaiveDate> = None;

    // MIS-5 symbols: fetched daily from the leverage sheet
    let mut mis5_symbols: HashSet<String> = HashSet::new();
    let mut mis5_loaded_date: Option<chrono::NaiveDate> = None;

    tracing::info!(
        "[GAP15] Poller started. Strategy: SELL gap>{}%<={}% price<{} top {} \
         at bucket {} TP={}% SL={}% EXIT={} | Entry path: WS-primary / poll-fallback",
        strategy_config.gap_min_pct, strategy_config.gap_max_pct,
        strategy_config.price_max, strategy_config.top_n,
        ENTRY_BUCKET, strategy_config.tp_pct, strategy_config.sl_pct,
        strategy_config.exit_bucket
    );

    loop {
        let now = now_ist();
        let h = now.hour();
        let m = now.minute();
        let in_window = (h > WINDOW_START_H || (h == WINDOW_START_H && m >= WINDOW_START_M))
            && (h < WINDOW_END_H || (h == WINDOW_END_H && m < WINDOW_END_M));

        if !in_window {
            let today = today_ist();

            // ── Midnight: full daily state reset ──
            if today != last_trading_date {
                // Abort WS task (prevents zombie tasks holding channels open)
                if let Some(handle) = ws_task_handle.take() {
                    handle.abort();
                    tracing::info!("[DAILY] WS feed task aborted for new trading day");
                }
                // Abort entry task if it somehow survived past midnight
                if let Some(handle) = entry_task_handle.take() {
                    handle.abort();
                    tracing::info!("[DAILY] WS entry task aborted for new trading day");
                }

                derived_state.clear();

                // Cancel any pending broker SL orders before clearing state
                for (sym, pos) in &open_signals {
                    for sl in &pos.sl_orders {
                        if let Some(ref oid) = sl.sl_order_id {
                            let oid_c     = oid.clone();
                            let broker_c  = sl.broker.clone();
                            let api_key_c = sl.api_key.clone();
                            let at_c      = sl.access_token.clone();
                            let cid_c     = sl.account_client_id.clone();
                            let base_url  = config.dhan_base_url.clone();
                            let sym_c     = sym.clone();
                            tokio::spawn(async move {
                                if let Err(e) = cancel_sl_order(&broker_c, &oid_c, &api_key_c, &at_c, &cid_c, &base_url).await {
                                    tracing::warn!("[DAILY] Failed to cancel SL order {} for {}: {}", oid_c, sym_c, e);
                                } else {
                                    tracing::info!("[DAILY] Cancelled SL order {} for {}", oid_c, sym_c);
                                }
                            });
                        }
                    }
                }

                open_signals.clear();
                exited_symbols.clear();
                gap_pct_cache.clear();
                large_mega_symbols.clear();
                symbol_map.clear();
                entry_fired_today = false;
                consecutive_failures = 0;
                poll_fallback_needed = false;
                historical_seeded_date = None;
                crash_recovery_done = false;
                config_loaded_date = None;
                vol_groups_loaded_date = None;
                active_stocks_cache.clear();
                active_stocks_date = None;
                mis5_symbols.clear();
                mis5_loaded_date = None;
                ws_entry_task_spawned = false;
                entry_outcome_received = false;
                shared_ticks.write().await.clear();
                // Drain stale channel messages from previous day
                while new_pos_rx.try_recv().is_ok() {}
                while entry_reject_rx.try_recv().is_ok() {}
                // Replace oneshot channels (can't drain — just recreate)
                let (new_tx, new_rx) = tokio::sync::oneshot::channel::<EntryOutcome>();
                entry_done_tx = new_tx;
                entry_done_rx = new_rx;

                fired_today.lock().await.clear();

                if let Err(e) = crate::dhan::scrip_master::force_sync(&ch, &config).await {
                    tracing::error!("[DAILY] Scrip master sync failed: {}", e);
                }
                last_trading_date = today;
                tracing::info!("[DAILY] New trading day {}: full state reset complete", today);
            }

            // ── Pre-market: MIS-5 fetch + WS startup at 9:10 AM ──
            if h == 9 && m >= 10 {
                if mis5_loaded_date != Some(today) {
                    mis5_symbols = crate::dhan::margin::fetch_mis5_symbols().await;
                    mis5_loaded_date = Some(today);
                    if mis5_symbols.is_empty() {
                        tracing::warn!("[MIS5] ⚠️ Empty — trading all eligible symbols (fail-open)");
                    } else {
                        tracing::info!("[MIS5] {} symbols with 5x margin loaded", mis5_symbols.len());
                    }
                }

                // Supervise WS task in pre-market
                let ws_died = ws_task_handle.as_ref().map(|h| h.is_finished()).unwrap_or(false);
                if ws_died {
                    tracing::warn!("[WS] Pre-market task died — restarting");
                    ws_task_handle = None;
                }

                if ws_task_handle.is_none() {
                    if active_stocks_date != Some(today) {
                        match wl_db::get_active_security_ids(&ch).await {
                            Ok(a) => {
                                symbol_map = a.iter().map(|(id, sym)| (id.clone(), sym.clone())).collect();
                                active_stocks_cache = a;
                                active_stocks_date = Some(today);
                            }
                            Err(e) => tracing::error!("[WS-PREMARKET] Failed to load active stocks: {}", e),
                        }
                    }
                    let label = format!("pre-market 9:{:02} ({} min before entry)", m, 16u32.saturating_sub(m));
                    ws_task_handle = start_ws_task(
                        &active_stocks_cache, &config, &ch,
                        live_feed.clone(), tick_tx.clone(), &label,
                    ).await;
                }
            }

            // ── Pre-market historical snapshot fill (7-9 AM) ──
            if h >= 7 && h < 9 && historical_seeded_date != Some(today) {
                let fill_start = std::time::Instant::now();
                tracing::info!("[HIST-FILL] Starting at {:02}:{:02}...", h, m);
                match tokio::time::timeout(
                    std::time::Duration::from_secs(300),
                    pre_market_historical_fill(&ch, &dhan, &config, today),
                ).await {
                    Ok(Ok(count)) => {
                        historical_seeded_date = Some(today);
                        tracing::info!("[HIST-FILL] ✅ {} buckets filled in {:.1}s",
                            count, fill_start.elapsed().as_secs_f32());
                    }
                    Ok(Err(e)) => {
                        tracing::error!("[HIST-FILL] ❌ Failed: {}", e);
                        historical_seeded_date = Some(today);
                    }
                    Err(_) => {
                        tracing::error!("[HIST-FILL] ⏱️ Timed out after 300s");
                        historical_seeded_date = Some(today);
                    }
                }
            }

            tokio::time::sleep(std::time::Duration::from_secs(10)).await;
            continue;
        }

        // ══════════════════════════════════════════════
        //              MARKET HOURS
        // ══════════════════════════════════════════════

        let trading_date = today_ist();
        let bucket = compute_bucket(&now);

        // ── WS task supervision: detect crash and restart during market hours ──
        {
            let ws_died = ws_task_handle.as_ref().map(|h| h.is_finished()).unwrap_or(false);
            if ws_died {
                tracing::error!("[WS] ⚠️ Feed task died at bucket {} — restarting", bucket);
                ws_task_handle = None;
                let label = format!("mid-session restart bucket={}", bucket);
                ws_task_handle = start_ws_task(
                    &active_stocks_cache, &config, &ch,
                    live_feed.clone(), tick_tx.clone(), &label,
                ).await;
            }
        }

        // ── Load strategy config once per day ──
        if config_loaded_date != Some(trading_date) {
            strategy_config = wl_db::get_gap15_config(&ch).await;
            config_loaded_date = Some(trading_date);
            tracing::info!("[GAP15] Config: gap>{}%<={}% price<{} top={} TP={}% SL={}% exit_bkt={}",
                strategy_config.gap_min_pct, strategy_config.gap_max_pct,
                strategy_config.price_max, strategy_config.top_n,
                strategy_config.tp_pct, strategy_config.sl_pct, strategy_config.exit_bucket);

            let (md_token, md_client_id) = crate::api::settings::get_market_data_token(&ch).await;
            if !md_token.is_empty() {
                dhan = DhanClient::new(&Config {
                    dhan_access_token: md_token,
                    dhan_client_id: if md_client_id.is_empty() {
                        config.dhan_client_id.clone()
                    } else {
                        md_client_id
                    },
                    ..config.clone()
                });
            }
        }

        // ── Load volume group symbols once per trading day ──
        if vol_groups_loaded_date != Some(trading_date) {
            let enabled_groups = crate::db::watchlist::get_enabled_volume_groups(&ch).await;
            large_mega_symbols = load_symbols_for_groups(&enabled_groups);
            vol_groups_loaded_date = Some(trading_date);
            tracing::info!("[GAP15] Volume groups {:?} → {} eligible symbols",
                enabled_groups, large_mega_symbols.len());
        }

        let in_entry_window = bucket >= ENTRY_BUCKET && bucket < ENTRY_BUCKET + ENTRY_GRACE;

        // ── Crash recovery: reload unclosed signals from DB on first window entry ──
        if !crash_recovery_done {
            crash_recovery_done = true;
            match sig_db::get_open_signals(&ch, trading_date).await {
                Ok(recovered) if !recovered.is_empty() => {
                    tracing::warn!("[RECOVERY] Reloading {} open signal(s) from DB", recovered.len());
                    let mut ft = fired_today.lock().await;
                    for (id, sig) in recovered {
                        ft.insert(sig.symbol.clone());
                        let ltp = sig.entry_price;
                        open_signals.entry(sig.symbol.clone()).or_insert(OpenPosition {
                            signal:    sig,
                            signal_id: id,
                            last_ltp:  ltp,
                            sl_orders: vec![],
                        });
                    }
                    // ── Recover broker SL orders from DB ──
                    {
                        #[derive(clickhouse::Row, serde::Deserialize)]
                        struct SlOrderRow {
                            signal_id:          String,
                            account_client_id:  String,
                            dhan_order_id:      String,
                            status:             u8,
                            filled_price:       f32,
                        }
                        #[derive(clickhouse::Row, serde::Deserialize)]
                        #[allow(dead_code)]
                        struct AccRow {
                            client_id:    String,
                            broker:       String,
                            api_key:      String,
                            access_token: String,
                        }

                        let sl_rows = ch.query(
                            "SELECT signal_id, account_client_id, dhan_order_id, status, filled_price \
                             FROM trading.orders FINAL \
                             WHERE trading_date = toDate(?) AND order_type = 3"
                        )
                        .bind(trading_date.format("%Y-%m-%d").to_string())
                        .fetch_all::<SlOrderRow>().await
                        .unwrap_or_default();

                        let accounts = ch.query(
                            "SELECT client_id, broker, api_key, access_token \
                             FROM trading.accounts FINAL WHERE mode = 'LIVE' AND enabled = 1"
                        )
                        .fetch_all::<AccRow>().await
                        .unwrap_or_default();

                        let mut sl_traded_syms: Vec<String> = vec![];
                        for sl_row in &sl_rows {
                            let pos = open_signals.values_mut()
                                .find(|p| p.signal_id.to_string() == sl_row.signal_id);
                            let pos = match pos { Some(p) => p, None => continue };

                            if sl_row.status == 2 {
                                let exit_price = if sl_row.filled_price > 0.0 {
                                    sl_row.filled_price
                                } else {
                                    pos.signal.sl_price
                                };
                                tracing::warn!("[RECOVERY] SL already TRADED for {} at {:.2} — recording exit",
                                    pos.signal.symbol, exit_price);
                                let actual_return_pct = match pos.signal.direction {
                                    Direction::Buy  => (exit_price - pos.signal.entry_price) / pos.signal.entry_price * 100.0,
                                    Direction::Sell => (pos.signal.entry_price - exit_price) / pos.signal.entry_price * 100.0,
                                };
                                let pnl_rupees = pos.signal.entry_price * (actual_return_pct / 100.0) * pos.signal.quantity as f32;
                                let exit = crate::exit_manager::ExitResult {
                                    reason: ExitReason::Sl,
                                    exit_price,
                                    exit_bucket: bucket,
                                    actual_return_pct,
                                    pnl_rupees,
                                };
                                sig_db::update_signal_exit(&ch, &pos.signal, pos.signal_id, &exit, &strategy_config).await.ok();
                                sl_traded_syms.push(pos.signal.symbol.clone());
                                continue;
                            }

                            if let Some(acc) = accounts.iter().find(|a| a.client_id == sl_row.account_client_id) {
                                let sl_trigger = match pos.signal.direction {
                                    Direction::Sell => pos.signal.entry_price * (1.0 + strategy_config.sl_pct / 100.0),
                                    Direction::Buy  => pos.signal.entry_price * (1.0 - strategy_config.sl_pct / 100.0),
                                };
                                pos.sl_orders.push(SlOrderTracker {
                                    account_client_id: acc.client_id.clone(),
                                    broker:            acc.broker.clone(),
                                    sl_order_id:       Some(sl_row.dhan_order_id.clone()),
                                    sl_trigger_price:  sl_trigger,
                                    fallback_software: false,
                                    api_key:           acc.api_key.clone(),
                                    access_token:      acc.access_token.clone(),
                                });
                                tracing::info!("[RECOVERY] Restored SL order {} for {} (account={} broker={})",
                                    sl_row.dhan_order_id, pos.signal.symbol, acc.client_id, acc.broker);
                            }
                        }
                        for sym in sl_traded_syms { open_signals.remove(&sym); }
                    }

                    if bucket > ENTRY_BUCKET {
                        entry_fired_today = true;
                        ws_entry_task_spawned = true; // don't spawn entry task if already past entry
                        tracing::info!("[RECOVERY] bucket {} > entry bucket — marking entry_fired=true", bucket);
                    }
                }
                Ok(_) => tracing::info!("[RECOVERY] No open signals to recover"),
                Err(e) => tracing::warn!("[RECOVERY] Failed: {}", e),
            }
        }

        // ── Active stocks: cached daily ──
        if active_stocks_date != Some(trading_date) {
            match wl_db::get_active_security_ids(&ch).await {
                Ok(a) => {
                    tracing::info!("[POLL] Active stocks loaded: {}", a.len());
                    // Rebuild persistent symbol_map alongside the cache
                    symbol_map = a.iter().map(|(id, sym)| (id.clone(), sym.clone())).collect();
                    active_stocks_cache = a;
                    active_stocks_date = Some(trading_date);
                }
                Err(e) => {
                    tracing::error!("[POLL] Failed to load active stocks: {}", e);
                    tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                    continue;
                }
            }
            // Fallback WS startup if pre-market block was missed
            if ws_task_handle.is_none() {
                let label = format!("late in-window start bucket={}", bucket);
                ws_task_handle = start_ws_task(
                    &active_stocks_cache, &config, &ch,
                    live_feed.clone(), tick_tx.clone(), &label,
                ).await;
                if ws_task_handle.is_some() {
                    tracing::warn!("[WS] ⚠️ Late WS start at bucket {} — ticks may not be ready for entry", bucket);
                }
            }
        }

        if active_stocks_cache.is_empty() {
            tracing::warn!("[POLL] No active stocks, sleeping 60s");
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            continue;
        }

        // ── Batch fetch REST quotes ──
        // security_ids from persistent symbol_map (already deduplicated as HashMap keys)
        let security_ids: Vec<String> = symbol_map.keys().cloned().collect();
        let mut all_quotes = HashMap::new();
        let mut fetch_ok = true;
        let poll_start = std::time::Instant::now();

        let chunks: Vec<&[String]> = security_ids.chunks(1000).collect();
        tracing::info!("[POLL] Fetching {} symbols in {} chunks (bucket {})",
            security_ids.len(), chunks.len(), bucket);

        for (i, chunk) in chunks.iter().enumerate() {
            if i > 0 && !in_entry_window {
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            }
            let chunk_start = std::time::Instant::now();
            match fetch_quotes(&dhan, chunk, &config.dhan_quote_endpoint).await {
                Ok(q) => {
                    tracing::info!("[POLL] Chunk {}/{}: {} quotes in {:.1}s",
                        i+1, chunks.len(), q.len(), chunk_start.elapsed().as_secs_f32());
                    all_quotes.extend(q);
                }
                Err(e) => {
                    tracing::error!("[POLL] ❌ Chunk {}/{} FAILED: {}", i+1, chunks.len(), e);
                    fetch_ok = false;
                    if !in_entry_window {
                        tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                    }
                }
            }
        }

        if !fetch_ok {
            consecutive_failures += 1;
            tracing::warn!("[POLL] ⚠️ Partial poll: {} quotes (consecutive failures: {})",
                all_quotes.len(), consecutive_failures);
            if consecutive_failures >= 3 {
                let evt = WsEvent {
                    event_type: "error".into(),
                    data: serde_json::json!({"message": format!("{} consecutive poll failures", consecutive_failures)}),
                };
                let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
            }
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES && all_quotes.is_empty() {
                tracing::error!("[POLL] 🛑 Circuit breaker: {} consecutive full failures — sleeping 2 min",
                    consecutive_failures);
                tokio::time::sleep(std::time::Duration::from_secs(120)).await;
                continue;
            }
            if all_quotes.is_empty() {
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                continue;
            }
        } else {
            consecutive_failures = 0;
        }

        // ── Build + persist snapshots ──
        let mut new_snapshots = vec![];
        for (sec_id, quote) in &all_quotes {
            let symbol = match symbol_map.get(sec_id) { Some(s) => s.clone(), None => continue };
            let state = derived_state.entry(symbol.clone()).or_default();
            let d = compute_derived(
                quote.last_price, quote.open(), quote.high(), quote.low(),
                quote.volume, state,
            );
            new_snapshots.push(crate::types::Snapshot {
                symbol: symbol.clone(), security_id: sec_id.clone(),
                trading_date, bucket, ltp: quote.last_price,
                candle_open: quote.open(), candle_high: quote.high(), candle_low: quote.low(),
                volume_cum: quote.volume, volume_delta: d.volume_delta,
                vwap: d.vwap, volume_rate: d.volume_rate,
                candle_body_ratio: d.candle_body_ratio,
            });
        }
        if let Err(e) = snapshots::insert_batch(&ch, &new_snapshots).await {
            tracing::error!("[POLL] Snapshot insert failed: {}", e);
        }
        tracing::info!("[POLL] Stored {} snapshots in {:.1}s (bucket {})",
            new_snapshots.len(), poll_start.elapsed().as_secs_f32(), bucket);

        // Build ltp_map from REST quotes (used for exit checks and shared_ticks baseline)
        let ltp_map: HashMap<String, f32> = new_snapshots.iter()
            .map(|s| (s.symbol.clone(), s.ltp))
            .collect();

        // ── Drain fill-price updates + store broker SL order info ──
        while let Ok(update) = fill_rx.try_recv() {
            if let Some(pos) = open_signals.get_mut(&update.symbol) {
                let old_ep = pos.signal.entry_price;
                pos.signal.entry_price = update.fill_price;
                pos.signal.tp_price = strategy_config.tp_price(pos.signal.entry_price);
                pos.signal.sl_price = strategy_config.sl_price(pos.signal.entry_price);
                pos.sl_orders.push(SlOrderTracker {
                    account_client_id: update.account_client_id.clone(),
                    broker:            update.sl_broker.clone(),
                    sl_order_id:       update.sl_order_id.clone(),
                    sl_trigger_price:  update.sl_trigger_price,
                    fallback_software: update.sl_fallback_software,
                    api_key:           update.broker_api_key.clone(),
                    access_token:      update.broker_access_token.clone(),
                });
                let sl_status = if update.sl_fallback_software { "SOFTWARE_FALLBACK" }
                    else { update.sl_order_id.as_deref().unwrap_or("NONE") };
                tracing::info!("[FILL] {} entry: {:.2} → {:.2} TP={:.2} SL={:.2} broker_sl={}",
                    update.symbol, old_ep, update.fill_price,
                    pos.signal.tp_price, pos.signal.sl_price, sl_status);
            }
        }

        // ── Drain WS tick channel ──
        // Dedup: latest LTP per symbol wins.
        // latest_ticks serves three purposes:
        //   1. Update shared_ticks (WS entry task reads this at 9:16:00)
        //   2. Real-time exit checks for open positions
        //   3. Fallback poll-entry ltp override (if WS path fails)
        let mut latest_ticks: HashMap<String, f32> = HashMap::new();
        let ws_active = {
            let feed = live_feed.read().await;
            feed.ws_connected
                && feed.last_tick_time
                    .map(|t| t.elapsed().as_secs() < 30)
                    .unwrap_or(bucket == 0)
        };
        while let Ok((sym, ltp)) = tick_rx.try_recv() {
            latest_ticks.insert(sym, ltp);
        }
        if !ws_active && latest_ticks.len() > 100 {
            tracing::debug!("[WS] Drained {} stale ticks (ws_active=false)", latest_ticks.len());
        }

        // ── Update shared_ticks for WS entry task ──
        // Write REST baseline first (all symbols), then overwrite with fresher WS ticks.
        {
            let mut st = shared_ticks.write().await;
            for (sym, ltp) in &ltp_map {
                st.entry(sym.clone()).or_insert(*ltp); // REST: only fill missing
            }
            for (sym, ltp) in &latest_ticks {
                st.insert(sym.clone(), *ltp); // WS: always overwrite (fresher)
            }
        }

        // ── Gap cache: compute from live REST prices ──
        //
        // This runs AFTER the REST poll so live_ltp (ltp_map) is available.
        // It avoids the fatal timing bug where compute_gap_and_direction queried
        // today_open from ClickHouse snapshots BEFORE the REST poll had written them.
        //
        // Algorithm: fetch prev_day closing prices from ClickHouse (single cheap
        // query, no today dependency), then compute gap_pct = (ltp - prev_close) /
        // prev_close in Rust.
        //
        // Only runs when the cache is empty AND we have live prices.
        // Once populated, the cache is stable for the day.
        if gap_pct_cache.is_empty() && !ltp_map.is_empty() {
            match snapshots::compute_gap_from_live_prices(trading_date, &ltp_map).await {
                Ok(gaps) if !gaps.is_empty() => {
                    tracing::info!(
                        "[GAP15] ✅ gap_pct_cache populated from live prices: {} symbols (bucket {})",
                        gaps.len(), bucket
                    );
                    gap_pct_cache = gaps;
                }
                Ok(_) => {
                    tracing::error!(
                        "[GAP15] ❌ compute_gap_from_live_prices returned 0 gaps at bucket {} \
                         — prev_day closing data missing in ClickHouse? \
                         No trades possible today.",
                        bucket
                    );
                }
                Err(e) => {
                    tracing::error!("[GAP15] ❌ compute_gap_from_live_prices failed: {}", e);
                }
            }
            tracing::info!(
                "[GAP15] gap_pct_cache size = {} after live-price refresh (bucket {})",
                gap_pct_cache.len(), bucket
            );
        }

        // ── Spawn WS-driven entry task ──
        //
        // Deliberately placed AFTER:
        //   1. REST poll      → ltp_map available (gap computation above)
        //   2. shared_ticks   → REST baseline already written (task wakes to full data)
        //   3. gap_pct_cache  → populated from live prices above
        //
        // The task sleeps internally until 9:16:00 IST.  Spawning it here (during
        // bucket 1, ~9:15:05–9:15:30) gives it ~30–55 seconds to accumulate WS ticks
        // on top of the REST baseline before the entry scan fires.
        {
            let mis5_ready = mis5_symbols.is_empty() || mis5_loaded_date == Some(trading_date);
            let all_data_ready = !gap_pct_cache.is_empty()
                && config_loaded_date == Some(trading_date)
                && vol_groups_loaded_date == Some(trading_date)
                && active_stocks_date == Some(trading_date)
                && mis5_ready;

            let in_grace = bucket <= ENTRY_BUCKET + ENTRY_GRACE;

            if !ws_entry_task_spawned && !entry_fired_today && all_data_ready && in_grace {
                let (done_tx, done_rx) = tokio::sync::oneshot::channel::<EntryOutcome>();
                entry_done_rx = done_rx;

                let ctx = EntryContext {
                    gap_pct_cache:      gap_pct_cache.clone(),
                    large_mega_symbols: large_mega_symbols.clone(),
                    mis5_symbols:       mis5_symbols.clone(),
                    strategy_config:    strategy_config.clone(),
                    symbol_map:         symbol_map.clone(),
                    trading_date,
                    shared_ticks:       shared_ticks.clone(),
                    live_feed:          live_feed.clone(),
                    fired_today:        fired_today.clone(),
                    new_pos_tx:         new_pos_tx.clone(),
                    result_tx:          done_tx,
                    ch:                 ch.clone(),
                    executor:           executor.clone(),
                    fill_tx:            fill_tx.clone(),
                    entry_reject_tx:    entry_reject_tx.clone(),
                    ws_tx:              ws_tx.clone(),
                };
                entry_task_handle = Some(tokio::spawn(ws_driven_gap15_entry_task(ctx)));
                ws_entry_task_spawned = true;
                tracing::info!(
                    "[ENTRY] 🚀 WS-driven entry task spawned at bucket {} \
                     ({} gap symbols, shared_ticks={}, ws_running={})",
                    bucket, gap_pct_cache.len(),
                    shared_ticks.read().await.len(),
                    ws_task_handle.is_some()
                );
            } else if !ws_entry_task_spawned && !entry_fired_today && !in_grace {
                ws_entry_task_spawned = true;
                entry_fired_today = true;
                tracing::error!(
                    "[ENTRY] ⏰ Missed grace window (bucket {}) without spawning entry task \
                     — all_data_ready={} (gap={} config={:?} vg={:?} stocks={:?} mis5={}) \
                     — no trades today",
                    bucket, all_data_ready,
                    gap_pct_cache.len(),
                    config_loaded_date,
                    vol_groups_loaded_date,
                    active_stocks_date,
                    mis5_ready
                );
            }
        }

        // ── WS exit checks ──
        if ws_active && !open_signals.is_empty() && !latest_ticks.is_empty() {
            let mut ws_exited: Vec<String> = vec![];
            for (sym, ltp) in &latest_ticks {
                if let Some(pos) = open_signals.get_mut(sym) {
                    pos.last_ltp = *ltp;
                    let exit = if pos.all_sl_on_broker() {
                        check_exit_tp_time(&pos.signal, *ltp, bucket, strategy_config.exit_bucket)
                    } else {
                        check_exit(&pos.signal, *ltp, bucket, strategy_config.exit_bucket)
                    };
                    if let Some(exit) = exit {
                        if !claim_exit(&mut exited_symbols, sym) {
                            ws_exited.push(sym.clone());
                            continue;
                        }
                        tracing::info!("🚪⚡ WS Exit: {} {} reason={:?} ret={:.2}% pnl=₹{:.2}",
                            pos.signal.direction.as_str(), sym, exit.reason,
                            exit.actual_return_pct, exit.pnl_rupees);

                        if exit.reason != ExitReason::Sl {
                            for sl in &pos.sl_orders {
                                if let Some(ref oid) = sl.sl_order_id {
                                    let oid_c    = oid.clone();
                                    let broker_c = sl.broker.clone();
                                    let ak_c     = sl.api_key.clone();
                                    let at_c     = sl.access_token.clone();
                                    let cid_c    = sl.account_client_id.clone();
                                    let base_url = config.dhan_base_url.clone();
                                    let sym_c    = sym.clone();
                                    tokio::spawn(async move {
                                        match cancel_sl_order(&broker_c, &oid_c, &ak_c, &at_c, &cid_c, &base_url).await {
                                            Ok(true)  => tracing::info!("[SL] Cancelled {} SL {} for exit", sym_c, oid_c),
                                            Ok(false) => tracing::info!("[SL] {} SL {} already filled", sym_c, oid_c),
                                            Err(e)    => tracing::warn!("[SL] Failed to cancel {} SL {}: {}", sym_c, oid_c, e),
                                        }
                                    });
                                }
                            }
                        }

                        let sig_c  = pos.signal.clone();
                        let sid_c  = pos.signal_id;
                        let ch_c   = ch.clone();
                        let cfg_c  = strategy_config.clone();
                        let exec_c = executor.clone();
                        let exit_c = exit.clone();
                        tokio::spawn(async move {
                            if let Err(e) = sig_db::update_signal_exit(&ch_c, &sig_c, sid_c, &exit_c, &cfg_c).await {
                                tracing::error!("[EXIT] DB persist failed for {}: {}", sig_c.symbol, e);
                            }
                            if let Err(e) = exec_c.execute_exit(&sig_c, sid_c, &ch_c, "").await {
                                tracing::error!("EXIT ORDER FAILED {}: {} — MANUAL CLOSE NEEDED", sig_c.symbol, e);
                            }
                        });
                        let evt = WsEvent {
                            event_type: "exit".into(),
                            data: serde_json::json!({
                                "id": pos.signal_id,
                                "exit_reason": format!("{:?}", exit.reason),
                                "exit_price": exit.exit_price,
                                "actual_return_pct": exit.actual_return_pct,
                                "pnl_rupees": exit.pnl_rupees,
                            }),
                        };
                        let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                        ws_exited.push(sym.clone());
                    }
                }
            }
            for sym in ws_exited { open_signals.remove(&sym); }
        }

        // ══════════════════════════════════════════════
        //  ENTRY: WS-path result handling + poll fallback
        // ══════════════════════════════════════════════

        // ── 1. Drain new positions sent by WS entry task ──
        while let Ok((sym, pos)) = new_pos_rx.try_recv() {
            tracing::info!("[ENTRY] 📥 Registering WS-path position: {} @ {:.2}",
                sym, pos.signal.entry_price);
            open_signals.insert(sym, pos);
        }

        // ── 1b. Drain entry rejections ──
        // When poll_order_status confirms an entry order was NOT filled (REJECTED,
        // EXPIRED, or retries exhausted), it sends (symbol, signal_id) here.
        // Remove the phantom position so no exit order is ever placed for shares
        // we don't actually own.
        while let Ok((sym, _sig_id)) = entry_reject_rx.try_recv() {
            if open_signals.remove(&sym).is_some() {
                tracing::error!(
                    "[ENTRY] 🗑️ Phantom position REMOVED for {} — entry order was never filled. \
                     No exit order will be placed.",
                    sym
                );
                // Also remove from fired_today so a retry on the same symbol is possible
                // if we're still in the grace window (though typically we are past it by now).
                fired_today.lock().await.remove(&sym);
            } else {
                tracing::warn!(
                    "[ENTRY] 🗑️ Entry reject received for {} but it was already not in open_signals \
                     (may have exited or was already cleaned up).",
                    sym
                );
            }
        }

        // ── 2. Check WS entry task outcome (oneshot) ──
        if !entry_outcome_received {
            match entry_done_rx.try_recv() {
                Ok(EntryOutcome::Fired) => {
                    entry_fired_today = true;
                    entry_outcome_received = true;
                    tracing::info!("[ENTRY] ✅ WS entry task reported Fired — entry_fired_today=true");
                }
                Ok(EntryOutcome::WsFailed { ref reason }) => {
                    tracing::warn!("[ENTRY] ⚠️ WS entry task reported WsFailed ({})", reason);
                    poll_fallback_needed = true;
                    entry_outcome_received = true;
                }
                Err(tokio::sync::oneshot::error::TryRecvError::Empty) => {
                    // Task still running — nothing to do this cycle
                }
                Err(tokio::sync::oneshot::error::TryRecvError::Closed) => {
                    // Channel closed without a message → task panicked or was aborted
                    if ws_entry_task_spawned && !entry_outcome_received {
                        tracing::error!(
                            "[ENTRY] 💥 WS entry task channel closed without result at bucket {} \
                             (task panicked?) — triggering poll fallback",
                            bucket
                        );
                        poll_fallback_needed = true;
                        entry_outcome_received = true;
                    }
                }
            }
        }

        // ── 3. Supervision: detect silent task crash ──
        // task.is_finished() AND no outcome received → panic without send
        if ws_entry_task_spawned && !entry_outcome_received {
            if let Some(ref h) = entry_task_handle {
                if h.is_finished() && in_entry_window {
                    tracing::error!(
                        "[ENTRY] 💥 WS entry task is_finished() without outcome at bucket {} \
                         — triggering poll fallback",
                        bucket
                    );
                    poll_fallback_needed = true;
                    entry_outcome_received = true;
                }
            }
        }

        // ── 4. Poll-based fallback entry ──
        //
        // Activates when:
        //   (a) WS entry task explicitly reported WsFailed, OR
        //   (b) WS entry task crashed without sending a result, OR
        //   (c) WS entry task was never spawned (engine started late / all_data_ready failed)
        //
        // Uses latest_ticks (WS) where available, ltp_map (REST) as fallback.
        if in_entry_window && !entry_fired_today && (poll_fallback_needed || !ws_entry_task_spawned) {
            let entry_start = std::time::Instant::now();
            let path = if poll_fallback_needed { "POLL-FALLBACK(ws-failed)" } else { "POLL-PRIMARY(no-ws-task)" };
            if bucket > ENTRY_BUCKET {
                tracing::warn!("[GAP15] ⚠️ [{}] Entry delayed — firing at bucket {} (target was {})",
                    path, bucket, ENTRY_BUCKET);
            }
            tracing::info!("[GAP15] 🔍 [{}] Entry scan: {} quotes, gap {:.1}%..{:.1}%, price<{:.0}",
                path, ltp_map.len(), strategy_config.gap_min_pct,
                strategy_config.gap_max_pct, strategy_config.price_max);

            let mut candidates: Vec<(String, String, f32, f32)> = vec![];
            {
                let fired = fired_today.lock().await;
                let mut n_no_gap = 0u32; let mut n_no_ltp = 0u32;
                let mut n_price = 0u32;  let mut n_cap = 0u32;
                let mut n_mis5 = 0u32;   let mut n_gap_pass = 0u32;

                for (sec_id, symbol) in &symbol_map {
                    if fired.contains(symbol) { continue; }
                    let gap = gap_pct_cache.get(symbol).copied().unwrap_or(0.0);
                    if gap <= strategy_config.gap_min_pct || gap > strategy_config.gap_max_pct {
                        n_no_gap += 1; continue;
                    }
                    n_gap_pass += 1;
                    // Prefer WS tick price (fresher), fall back to REST
                    let ltp = if ws_active {
                        latest_ticks.get(symbol).copied()
                            .or_else(|| ltp_map.get(symbol).copied())
                    } else {
                        ltp_map.get(symbol).copied()
                    };
                    let ltp = match ltp { Some(l) => l, None => { n_no_ltp += 1; continue; } };
                    if ltp <= 0.0 || ltp >= strategy_config.price_max { n_price += 1; continue; }
                    if !large_mega_symbols.is_empty() && !large_mega_symbols.contains(symbol.as_str()) {
                        n_cap += 1; continue;
                    }
                    if !mis5_symbols.is_empty() && !mis5_symbols.contains(symbol.as_str()) {
                        n_mis5 += 1; continue;
                    }
                    candidates.push((symbol.clone(), sec_id.clone(), gap, ltp));
                }
                tracing::info!(
                    "[GAP15] [{}] Filter: gap_pass={} no_gap={} no_ltp={} price_fail={} \
                     cap_fail={} mis5_fail={} → {} candidates",
                    path, n_gap_pass, n_no_gap, n_no_ltp, n_price, n_cap, n_mis5, candidates.len()
                );
            }

            candidates.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
            candidates.truncate(strategy_config.top_n);
            let n = candidates.len();

            tracing::info!("[GAP15] [{}] 🎯 {} candidates in {:.0}ms",
                path, n, entry_start.elapsed().as_millis());

            // Mark entry fired regardless of candidate count (prevents re-scan next cycle)
            entry_fired_today = true;

            if n > 0 {
                let pos_value = strategy_config.position_value(n);
                tracing::info!("[GAP15] [{}] Position value: ₹{} (n={})", path, pos_value, n);

                let signals: Vec<Signal> = candidates.iter().filter_map(|(symbol, sec_id, gap, entry_price)| {
                    let qty = (pos_value as f32 / entry_price).floor() as u32;
                    if qty == 0 {
                        tracing::warn!("[GAP15] [{}] {} qty=0 at {:.2} — skipping", path, symbol, entry_price);
                        return None;
                    }
                    Some(Signal {
                        symbol:        symbol.clone(),
                        security_id:   sec_id.clone(),
                        trading_date,
                        direction:     Direction::Sell,
                        score:         (gap * 10.0).min(255.0) as u8,
                        signals_fired: vec![format!("gap+{:.2}%_{}", gap, path)],
                        entry_price:   *entry_price,
                        entry_bucket:  bucket,
                        entry_ts:      chrono::Utc::now().timestamp() as u32,
                        tp_price:      strategy_config.tp_price(*entry_price),
                        sl_price:      strategy_config.sl_price(*entry_price),
                        quantity:      qty,
                        open_price:    *entry_price,
                        gap_pct:       *gap,
                    })
                }).collect();

                for sig in &signals {
                    tracing::info!("🔔 [GAP15] [{}] SELL {} gap={:.2}% price={:.2} qty={} TP={:.2} SL={:.2}",
                        path, sig.symbol, sig.gap_pct, sig.entry_price,
                        sig.quantity, sig.tp_price, sig.sl_price);
                }

                let insert_futs: Vec<_> = signals.iter().map(|sig| {
                    let ch_c  = ch.clone();
                    let sig_c = sig.clone();
                    let cfg_c = strategy_config.clone();
                    async move { (sig_c.symbol.clone(), sig_db::insert_signal(&ch_c, &sig_c, &cfg_c).await) }
                }).collect();
                let insert_results = futures::future::join_all(insert_futs).await;

                {
                    let mut ft = fired_today.lock().await;
                    for (sig, (symbol, result)) in signals.into_iter().zip(insert_results.into_iter()) {
                        match result {
                            Err(e) => tracing::error!("[GAP15] [{}] Signal insert FAILED for {}: {}", path, symbol, e),
                            Ok(sig_id) => {
                                ft.insert(symbol.clone());
                                let ltp = sig.entry_price;
                                open_signals.insert(symbol.clone(), OpenPosition {
                                    signal:    sig.clone(),
                                    signal_id: sig_id,
                                    last_ltp:  ltp,
                                    sl_orders: vec![],
                                });
                                let sig_clone  = sig.clone();
                                let ch_clone   = ch.clone();
                                let exec_clone = executor.clone();
                                let ftx        = fill_tx.clone();
                                let rtx        = entry_reject_tx.clone();
                                let sl_pct_val = strategy_config.sl_pct;
                                let slippage_val = strategy_config.fallback_limit_slippage_pct;
                                tokio::spawn(async move {
                                    if let Err(e) = exec_clone.execute(&sig_clone, sig_id, &ch_clone, "", Some(ftx), Some(rtx), sl_pct_val, slippage_val).await {
                                        tracing::error!("[GAP15] [{}] Order FAILED for {}: {}", "POLL", sig_clone.symbol, e);
                                    }
                                });
                                let evt = WsEvent {
                                    event_type: "signal".into(),
                                    data: serde_json::to_value(&sig).unwrap_or_default(),
                                };
                                let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                            }
                        }
                    }
                }
                tracing::info!("[GAP15] [{}] Entry complete: {} SELL orders spawned in {:.0}ms",
                    path, n, entry_start.elapsed().as_millis());
            } else {
                tracing::info!("[GAP15] [{}] No qualifying candidates today", path);
            }
        }

        // ── Exit manager: poll-based TP/SL/Time checks ──
        // Update last_known_ltp from REST quotes before checking exits
        for (sym, pos) in open_signals.iter_mut() {
            if let Some(&fresh) = ltp_map.get(sym.as_str()) {
                pos.last_ltp = fresh;
            }
        }

        // ── SL order status reconciliation: detect broker-side SL fills ──
        {
            let mut sl_exited: Vec<String> = vec![];
            for (sym, pos) in open_signals.iter() {
                let near_sl = pos.sl_orders.iter().any(|sl| {
                    if sl.sl_trigger_price <= 0.0 { return false; }
                    let dist = (pos.last_ltp - sl.sl_trigger_price).abs() / sl.sl_trigger_price;
                    dist < 0.02
                });
                let periodic_check = bucket % 5 == 0;
                if !near_sl && !periodic_check { continue; }

                for sl in &pos.sl_orders {
                    if let Some(ref oid) = sl.sl_order_id {
                        let status_result = if sl.broker == "ZERODHA" {
                            let kite = crate::zerodha::client::ZerodhaClient::new(&sl.api_key, &sl.access_token);
                            crate::zerodha::orders::get_order_status(&kite, oid).await
                                .map(|s| (s.order_status, s.average_traded_price))
                        } else {
                            let dhan_cfg = Config {
                                dhan_base_url:          config.dhan_base_url.clone(),
                                dhan_quote_endpoint:    String::new(),
                                dhan_orders_endpoint:   String::new(),
                                dhan_positions_endpoint: String::new(),
                                dhan_access_token:      sl.access_token.clone(),
                                dhan_client_id:         sl.account_client_id.clone(),
                                clickhouse_url:         String::new(),
                                debug:                  false,
                                ws_subscribe_fno_oi:    false,
                                gemini_api_key:         String::new(),
                            };
                            let dhan_sl = DhanClient::new(&dhan_cfg);
                            crate::dhan::orders::get_order_status(&dhan_sl, oid).await
                                .map(|s| (s.order_status.unwrap_or_else(|| "UNKNOWN".to_string()), s.average_traded_price))
                        };

                        match status_result {
                            Ok((status, avg_price)) => {
                                let is_filled = matches!(status.as_str(), "TRADED" | "COMPLETE");
                                if is_filled {
                                    let exit_price = if avg_price > 0.0 { avg_price } else { sl.sl_trigger_price };
                                    tracing::info!("🚪🛑 [SL] Broker SL filled: {} price={:.2} broker={} order={}",
                                        sym, exit_price, sl.broker, oid);
                                    let actual_return_pct = match pos.signal.direction {
                                        Direction::Buy  => (exit_price - pos.signal.entry_price) / pos.signal.entry_price * 100.0,
                                        Direction::Sell => (pos.signal.entry_price - exit_price) / pos.signal.entry_price * 100.0,
                                    };
                                    let pnl_rupees = pos.signal.entry_price * (actual_return_pct / 100.0) * pos.signal.quantity as f32;
                                    let exit = crate::exit_manager::ExitResult {
                                        reason: ExitReason::Sl,
                                        exit_price,
                                        exit_bucket: bucket,
                                        actual_return_pct,
                                        pnl_rupees,
                                    };
                                    let sig_c  = pos.signal.clone();
                                    let sid_c  = pos.signal_id;
                                    let ch_c   = ch.clone();
                                    let cfg_c  = strategy_config.clone();
                                    let exit_c = exit.clone();
                                    tokio::spawn(async move {
                                        sig_db::update_signal_exit(&ch_c, &sig_c, sid_c, &exit_c, &cfg_c).await.ok();
                                    });
                                    let evt = WsEvent {
                                        event_type: "exit".into(),
                                        data: serde_json::json!({
                                            "id": pos.signal_id,
                                            "exit_reason": "Sl",
                                            "exit_price": exit_price,
                                            "actual_return_pct": actual_return_pct,
                                            "pnl_rupees": pnl_rupees,
                                            "broker_sl": true,
                                        }),
                                    };
                                    let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                                    sl_exited.push(sym.clone());
                                    break;
                                }
                                if status == "CANCELLED" {
                                    tracing::error!("[SL] {} SL order {} CANCELLED externally on {} — software SL fallback",
                                        sym, oid, sl.broker);
                                }
                            }
                            Err(e) => tracing::warn!("[SL] Status check failed {} order={}: {}", sym, oid, e),
                        }
                    }
                }
            }
            for sym in &sl_exited {
                claim_exit(&mut exited_symbols, sym);
                open_signals.remove(sym);
            }
        }

        // ── Poll-based TP/SL/Time exit checks ──
        let mut exited: Vec<String> = vec![];
        for (sym, pos) in open_signals.iter() {
            let ltp = ltp_map.get(sym).copied().unwrap_or(pos.last_ltp);
            if ltp_map.get(sym).is_none() {
                tracing::warn!("[EXIT] {} not in quote batch — using last known LTP {:.2}", sym, ltp);
            }
            let exit = if pos.all_sl_on_broker() {
                check_exit_tp_time(&pos.signal, ltp, bucket, strategy_config.exit_bucket)
            } else {
                check_exit(&pos.signal, ltp, bucket, strategy_config.exit_bucket)
            };
            if let Some(exit) = exit {
                if !claim_exit(&mut exited_symbols, sym) {
                    tracing::debug!("[EXIT] {} already claimed — skipping duplicate", sym);
                    exited.push(sym.clone());
                    continue;
                }
                tracing::info!("🚪 Exit: {} {} reason={:?} ret={:.2}% pnl=₹{:.2}",
                    pos.signal.direction.as_str(), sym, exit.reason,
                    exit.actual_return_pct, exit.pnl_rupees);

                if exit.reason != ExitReason::Sl {
                    for sl in &pos.sl_orders {
                        if let Some(ref oid) = sl.sl_order_id {
                            let oid_c    = oid.clone();
                            let broker_c = sl.broker.clone();
                            let ak_c     = sl.api_key.clone();
                            let at_c     = sl.access_token.clone();
                            let cid_c    = sl.account_client_id.clone();
                            let base_url = config.dhan_base_url.clone();
                            let sym_c    = sym.clone();
                            tokio::spawn(async move {
                                match cancel_sl_order(&broker_c, &oid_c, &ak_c, &at_c, &cid_c, &base_url).await {
                                    Ok(true)  => tracing::info!("[SL] Cancelled {} SL {} for exit", sym_c, oid_c),
                                    Ok(false) => tracing::info!("[SL] {} SL {} already filled", sym_c, oid_c),
                                    Err(e)    => tracing::warn!("[SL] Failed to cancel {} SL {}: {}", sym_c, oid_c, e),
                                }
                            });
                        }
                    }
                }

                let sig_c  = pos.signal.clone();
                let sid_c  = pos.signal_id;
                let ch_c   = ch.clone();
                let cfg_c  = strategy_config.clone();
                let exec_c = executor.clone();
                let exit_c = exit.clone();
                tokio::spawn(async move {
                    if let Err(e) = sig_db::update_signal_exit(&ch_c, &sig_c, sid_c, &exit_c, &cfg_c).await {
                        tracing::error!("[EXIT] DB persist failed for {}: {}", sig_c.symbol, e);
                    }
                    if let Err(e) = exec_c.execute_exit(&sig_c, sid_c, &ch_c, "").await {
                        tracing::error!("EXIT ORDER FAILED for {}: {} — MANUAL CLOSE NEEDED", sig_c.symbol, e);
                    }
                });
                let evt = WsEvent {
                    event_type: "exit".into(),
                    data: serde_json::json!({
                        "id": pos.signal_id,
                        "exit_reason": format!("{:?}", exit.reason),
                        "exit_price": exit.exit_price,
                        "actual_return_pct": exit.actual_return_pct,
                        "pnl_rupees": exit.pnl_rupees,
                    }),
                };
                let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                exited.push(sym.clone());
            }
        }
        for sym in exited { open_signals.remove(&sym); }

        if bucket > strategy_config.exit_bucket && !open_signals.is_empty() {
            tracing::warn!("[GAP15] {} positions still open after exit bucket {} — check broker",
                open_signals.len(), strategy_config.exit_bucket);
        }

        // Throttle: 1s minimum between cycles (skip at entry window — every ms counts)
        if !in_entry_window {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }
    }
}

// ─────────────────────────────────────────────────────────
//  Pre-market historical snapshot fill
// ─────────────────────────────────────────────────────────

/// Fills missing prior-day buckets via Dhan 1-min intraday API.
/// Ensures bucket 345+ (near-close price) exists for gap_pct computation next morning.
/// Uses prev_trading_day() to correctly skip weekends AND NSE market holidays.
async fn pre_market_historical_fill(
    ch: &ChClient,
    _dhan: &DhanClient,
    config: &Config,
    today: chrono::NaiveDate,
) -> Result<usize> {
    use crate::db::snapshots;
    use crate::types::timestamp_to_bucket;
    use crate::dhan::market_data::IntradayResponse;

    let active = wl_db::get_active_security_ids(ch).await?;
    if active.is_empty() {
        tracing::warn!("[HIST-FILL] No enabled stocks in watchlist");
        return Ok(0);
    }

    // Correctly skip weekends AND NSE holidays (e.g. April 14 → April 11)
    let lookback = crate::types::prev_trading_day(today);
    let date_str = lookback.format("%Y-%m-%d").to_string();
    tracing::info!("[HIST-FILL] Target backfill date: {} (prev_trading_day of {})", date_str, today);

    #[derive(clickhouse::Row, serde::Deserialize)]
    struct MaxBucket { symbol: String, max_b: u16 }
    let covered = ch.query(
        "SELECT symbol, max(bucket) AS max_b FROM trading.snapshots \
         WHERE trading_date = toDate(?) GROUP BY symbol"
    ).bind(date_str.clone()).fetch_all::<MaxBucket>().await.unwrap_or_default();
    let covered_map: HashMap<String, u16> = covered.into_iter()
        .map(|r| (r.symbol, r.max_b))
        .collect();

    let needs_fill: Vec<(String, String)> = active.iter()
        .filter(|(_, sym)| covered_map.get(sym.as_str()).copied().unwrap_or(0) < 345)
        .cloned()
        .collect();

    if needs_fill.is_empty() {
        tracing::info!("[HIST-FILL] ✅ All stocks have bucket >= 345 for {} — skipping", date_str);
        return Ok(0);
    }

    tracing::info!("[HIST-FILL] {}/{} stocks need backfill for {}",
        needs_fill.len(), active.len(), date_str);

    #[derive(clickhouse::Row, serde::Deserialize)]
    struct ExistBucket { symbol: String, b: u16 }
    let existing_rows = ch.query(
        "SELECT symbol, bucket as b FROM trading.snapshots WHERE trading_date = toDate(?)"
    ).bind(date_str.clone()).fetch_all::<ExistBucket>().await.unwrap_or_default();
    let existing_buckets: std::collections::HashSet<(String, u16)> = existing_rows
        .into_iter().map(|r| (r.symbol, r.b)).collect();

    let http_client = reqwest::Client::new();
    let (db_token, db_cid) = crate::api::settings::get_market_data_token(ch).await;
    let token     = if db_token.is_empty() { config.dhan_access_token.clone() } else { db_token };
    let client_id = if db_cid.is_empty()   { config.dhan_client_id.clone()    } else { db_cid   };
    if token.is_empty() {
        tracing::warn!("[HIST-FILL] No token — skipping");
        return Ok(0);
    }

    let mut filled = 0usize;
    let mut failed = 0usize;

    for chunk in needs_fill.chunks(5) {
        let mut handles = Vec::new();
        for (sec_id, _symbol) in chunk {
            let http = http_client.clone();
            let tk   = token.clone();
            let cid  = client_id.clone();
            let sid  = sec_id.clone();
            let ds   = date_str.clone();
            handles.push(tokio::spawn(async move {
                let body = serde_json::json!({
                    "securityId": sid, "exchangeSegment": "NSE_EQ",
                    "instrument": "EQUITY", "expiryCode": 0,
                    "fromDate": ds, "toDate": ds,
                });
                for attempt in 0..3u32 {
                    if attempt > 0 {
                        tokio::time::sleep(std::time::Duration::from_millis(200u64 * (1 << attempt))).await;
                    }
                    let resp = match http.post("https://api.dhan.co/v2/charts/intraday")
                        .header("access-token", &tk)
                        .header("client-id", &cid)
                        .header("Content-Type", "application/json")
                        .json(&body).send().await
                    {
                        Ok(r)  => r,
                        Err(_) => continue,
                    };
                    let st = resp.status().as_u16();
                    if st == 429 { continue; }
                    if st == 400 { break;    }
                    if resp.status().is_success() {
                        return resp.json::<IntradayResponse>().await.ok();
                    }
                }
                None
            }));
        }
        for (i, h) in handles.into_iter().enumerate() {
            let (sec_id, symbol) = &chunk[i];
            let data = h.await.unwrap_or(None);
            let Some(resp) = data else { failed += 1; continue; };
            let candles: Vec<(u16, i64, f32, f32, f32, f32, u64)> = {
                let mut v = Vec::new();
                for j in 0..resp.open.len() {
                    let b = timestamp_to_bucket(resp.timestamp[j]);
                    if b == 0 || b > 375 { continue; }
                    v.push((b, resp.timestamp[j],
                        resp.open[j] as f32, resp.high[j] as f32,
                        resp.low[j] as f32,  resp.close[j] as f32,
                        resp.volume[j] as u64));
                }
                v
            };
            if candles.is_empty() { continue; }
            let filtered: Vec<_> = candles.into_iter()
                .filter(|(b, _, _, _, _, _, _)| !existing_buckets.contains(&(symbol.clone(), *b)))
                .collect();
            if filtered.is_empty() { continue; }
            if let Err(e) = snapshots::insert_historical_buckets(
                ch, lookback, symbol, sec_id, &filtered, 1
            ).await {
                tracing::warn!("[HIST-FILL] insert failed for {}: {}", symbol, e);
                failed += 1;
            } else {
                filled += filtered.len();
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }

    tracing::info!("[HIST-FILL] Done: {} buckets filled, {} failed", filled, failed);
    Ok(filled)
}
