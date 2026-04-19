use axum::{extract::State, Json};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use crate::api::AppState;
use crate::types::{Snapshot, Signal, Gap15Config, Direction, today_ist};
use crate::exit_manager::{check_exit, ExitReason};

#[derive(Deserialize)]
pub struct BacktestRequest {
    pub from: String,
    pub to: String,
    #[serde(flatten)]
    pub config: Gap15Config,
}

#[derive(Serialize, Clone)]
pub struct BacktestSignal {
    pub trading_date: String,
    pub symbol: String,
    pub direction: String,
    pub entry_price: f32,
    pub entry_bucket: u16,
    pub exit_price: f32,
    pub exit_bucket: u16,
    pub exit_reason: String,
    pub actual_return_pct: f32,
    pub pnl_rupees: f32,
    pub quantity: u32,
    pub gap_pct: f32,
}

#[derive(Serialize)]
pub struct BacktestResult {
    pub signals: Vec<BacktestSignal>,
    pub total_trades: usize,
    pub win_rate: f32,
    pub avg_return_pct: f32,
    pub total_pnl_rupees: f32,
    pub tp_hits: usize,
    pub sl_hits: usize,
    pub time_exits: usize,
}

pub async fn compute(State(state): State<AppState>, Json(req): Json<BacktestRequest>) -> Json<serde_json::Value> {
    let cfg = req.config;
    let from = req.from.clone();
    let to = req.to.clone();

    // Load snapshots for the date range
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct SnapRow {
        symbol: String, security_id: String,
        trading_date: String, bucket: u16,
        ltp: f32, candle_open: f32, candle_high: f32, candle_low: f32,
        volume_cum: u64, volume_delta: u32,
        vwap: f32, volume_rate: f32, candle_body_ratio: f32,
    }

    let rows = match state.ch.query(
        "SELECT symbol, security_id, toString(trading_date) AS trading_date, bucket, \
         ltp, candle_open, candle_high, candle_low, volume_cum, volume_delta, \
         vwap, volume_rate, candle_body_ratio \
         FROM trading.snapshots FINAL \
         WHERE trading_date >= toDate(?) AND trading_date <= toDate(?) \
         ORDER BY trading_date, symbol, bucket"
    )
    .bind(from.as_str())
    .bind(to.as_str())
    .fetch_all::<SnapRow>().await {
        Ok(r) => r,
        Err(e) => return Json(serde_json::json!({"error": e.to_string()})),
    };

    // Load gap_pct from snapshots (bucket1 open vs prev bucket375)
    // Group by (trading_date, symbol)
    let mut by_ds: HashMap<(String, String), Vec<Snapshot>> = HashMap::new();
    for r in rows {
        let snap = Snapshot {
            symbol: r.symbol.clone(),
            security_id: r.security_id.clone(),
            trading_date: today_ist(), // placeholder; not used in backtest logic
            bucket: r.bucket,
            ltp: r.ltp,
            candle_open: r.candle_open, candle_high: r.candle_high,
            candle_low: r.candle_low, volume_cum: r.volume_cum,
            volume_delta: r.volume_delta, vwap: r.vwap,
            volume_rate: r.volume_rate, candle_body_ratio: r.candle_body_ratio,
        };
        by_ds.entry((r.trading_date, r.symbol)).or_default().push(snap);
    }

    // Load gap_pct cache
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct GapRow { trading_date: String, symbol: String, gap_pct: f32 }
    let gap_rows = state.ch.query(
        "SELECT toString(trading_date) AS trading_date, symbol, gap_pct \
         FROM trading.daily_ref FINAL \
         WHERE trading_date >= toDate(?) AND trading_date <= toDate(?)"
    )
    .bind(from.as_str())
    .bind(to.as_str())
    .fetch_all::<GapRow>().await.unwrap_or_default();

    let mut gap_cache: HashMap<(String, String), f32> = HashMap::new();
    for r in gap_rows {
        gap_cache.insert((r.trading_date, r.symbol), r.gap_pct);
    }

    // Group snapshots by trading_date
    let mut by_date: HashMap<String, Vec<(String, Vec<Snapshot>, f32)>> = HashMap::new();
    for ((date, sym), mut snaps) in by_ds {
        snaps.sort_by_key(|s| s.bucket);
        let gap = gap_cache.get(&(date.clone(), sym.clone())).copied().unwrap_or(0.0);
        by_date.entry(date).or_default().push((sym, snaps, gap));
    }

    // Run gap15 strategy per day
    let mut all_signals: Vec<BacktestSignal> = vec![];

    let mut dates: Vec<String> = by_date.keys().cloned().collect();
    dates.sort();

    for date in &dates {
        let stocks = match by_date.get(date) { Some(s) => s, None => continue };

        // Filter: gap > gap_min_pct AND price < price_max (use bucket 2 LTP as entry)
        let mut candidates: Vec<(String, f32, f32, String)> = vec![]; // (symbol, gap_pct, entry_price, security_id)
        for (sym, snaps, gap) in stocks {
            if *gap <= cfg.gap_min_pct { continue; }
            if *gap > cfg.gap_max_pct { continue; } // filter corporate actions (splits/bonus)
            // Entry price = bucket 2 LTP (b1 close in backtest = C[:,bi(1)])
            let entry_snap = snaps.iter().find(|s| s.bucket == 2).or_else(|| snaps.iter().find(|s| s.bucket == 1));
            let entry_price = match entry_snap { Some(s) => s.ltp, None => continue };
            if entry_price <= 0.0 || entry_price >= cfg.price_max { continue; }
            let sec_id = snaps.first().map(|s| s.security_id.clone()).unwrap_or_default();
            candidates.push((sym.clone(), *gap, entry_price, sec_id));
        }

        // Sort by gap descending, take top N
        candidates.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(cfg.top_n);

        let n = candidates.len();
        if n == 0 { continue; }
        let pos_value = cfg.position_value(n);

        for (sym, gap, entry_price, _sec_id) in &candidates {
            let qty = (pos_value as f32 / entry_price).floor() as u32;
            if qty == 0 { continue; }

            let tp_price = cfg.tp_price(*entry_price);
            let sl_price = cfg.sl_price(*entry_price);

            // Simulate exit tick-by-tick through remaining buckets (3..=exit_bucket+N)
            let snaps = match by_date.get(date).and_then(|s| s.iter().find(|(s, _, _)| s == sym)) {
                Some((_, snaps, _)) => snaps,
                None => continue,
            };

            let mut exit_price = *entry_price;
            let mut exit_bucket = cfg.exit_bucket;
            let mut exit_reason = ExitReason::Time;

            // Build synthetic signal for check_exit
            let signal = Signal {
                symbol: sym.clone(), security_id: String::new(),
                trading_date: today_ist(),
                direction: Direction::Sell,
                score: (gap * 10.0).min(255.0) as u8,
                signals_fired: vec![format!("gap+{:.1}%", gap)],
                entry_price: *entry_price, entry_bucket: 2, entry_ts: 0,
                tp_price, sl_price, quantity: qty,
                open_price: *entry_price, gap_pct: *gap,
            };

            for snap in snaps.iter().filter(|s| s.bucket > 2) {
                let ltp = snap.ltp;
                if let Some(exit) = check_exit(&signal, ltp, snap.bucket, cfg.exit_bucket) {
                    exit_price = exit.exit_price;
                    exit_bucket = exit.exit_bucket;
                    exit_reason = exit.reason;
                    break;
                }
            }

            let actual_return_pct = (entry_price - exit_price) / entry_price * 100.0; // SELL
            let pnl_rupees = entry_price * (actual_return_pct / 100.0) * qty as f32;

            all_signals.push(BacktestSignal {
                trading_date: date.clone(),
                symbol: sym.clone(),
                direction: "SELL".to_string(),
                entry_price: *entry_price,
                entry_bucket: 2,
                exit_price,
                exit_bucket,
                exit_reason: format!("{:?}", exit_reason),
                actual_return_pct,
                pnl_rupees,
                quantity: qty,
                gap_pct: *gap,
            });
        }
    }

    let total = all_signals.len();
    let wins = all_signals.iter().filter(|s| s.actual_return_pct > 0.0).count();
    let avg_ret = if total > 0 { all_signals.iter().map(|s| s.actual_return_pct).sum::<f32>() / total as f32 } else { 0.0 };
    let total_pnl = all_signals.iter().map(|s| s.pnl_rupees).sum::<f32>();
    let tp_hits = all_signals.iter().filter(|s| s.exit_reason == "Tp").count();
    let sl_hits = all_signals.iter().filter(|s| s.exit_reason == "Sl").count();
    let time_exits = all_signals.iter().filter(|s| s.exit_reason == "Time").count();

    Json(serde_json::to_value(BacktestResult {
        signals: all_signals,
        total_trades: total,
        win_rate: if total > 0 { wins as f32 / total as f32 * 100.0 } else { 0.0 },
        avg_return_pct: avg_ret,
        total_pnl_rupees: total_pnl,
        tp_hits,
        sl_hits,
        time_exits,
    }).unwrap_or_default())
}
