use clickhouse::Client;
use anyhow::Result;
use std::collections::HashSet;
use chrono::NaiveDate;
use crate::types::{Signal, Direction, Gap15Config};
use crate::exit_manager::ExitResult;

fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'")
}

pub async fn load_fired_today(client: &Client) -> Result<HashSet<String>> {
    let today = crate::types::today_ist();
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { symbol: String }
    let rows = client.query(
        "SELECT DISTINCT symbol FROM trading.signals FINAL WHERE trading_date = toDate(?)"
    ).bind(today.format("%Y-%m-%d").to_string())
     .fetch_all::<Row>().await?;
    Ok(rows.into_iter().map(|r| r.symbol).collect())
}

pub async fn insert_signal(client: &Client, signal: &Signal, config: &Gap15Config) -> Result<uuid::Uuid> {
    let id = uuid::Uuid::new_v4();
    let dir_val: u8 = if signal.direction == crate::types::Direction::Buy { 1 } else { 2 };
    let fired_arr = format!("[{}]", signal.signals_fired.iter().map(|s| format!("'{}'", esc(s))).collect::<Vec<_>>().join(","));
    let date_str = signal.trading_date.format("%Y-%m-%d").to_string();

    tracing::info!("Inserting signal: {} {} {} price={} bucket={} qty={} gap={:.2}%",
        signal.direction.as_str(), signal.symbol, date_str,
        signal.entry_price, signal.entry_bucket, signal.quantity, signal.gap_pct);

    client.query(&format!(
        "INSERT INTO trading.signals \
         (id, trading_date, symbol, security_id, direction, entry_price, entry_bucket, \
          entry_ts, score, signals_fired, tp_price, sl_price, quantity, \
          cfg_entry_start, cfg_entry_end, cfg_min_move_pct, cfg_min_volume, \
          cfg_min_score, cfg_tp_pct, cfg_sl_pct, cfg_hard_exit_bucket, cfg_quantity) \
         VALUES ('{}', toDate('{}'), '{}', '{}', {}, {}, {}, {}, {}, {}, {}, {}, {}, 1, 2, 0, 0, 0, {}, {}, {}, 1)",
        id, date_str,
        esc(&signal.symbol), esc(&signal.security_id),
        dir_val,
        signal.entry_price, signal.entry_bucket,
        signal.entry_ts,
        signal.score, fired_arr,
        signal.tp_price, signal.sl_price, signal.quantity,
        config.tp_pct, config.sl_pct, config.exit_bucket,
    )).execute().await?;

    tracing::info!("Signal inserted: {} {} id={}", signal.direction.as_str(), signal.symbol, id);
    Ok(id)
}

pub async fn update_signal_exit(
    client: &Client,
    signal: &Signal,
    signal_id: uuid::Uuid,
    exit: &ExitResult,
    config: &Gap15Config,
) -> Result<()> {
    let dir_val: u8 = if signal.direction == crate::types::Direction::Buy { 1 } else { 2 };
    let exit_reason_val: u8 = match exit.reason {
        crate::exit_manager::ExitReason::Tp => 1,
        crate::exit_manager::ExitReason::Sl => 2,
        crate::exit_manager::ExitReason::Time => 3,
    };
    let fired_arr = format!("[{}]", signal.signals_fired.iter().map(|s| format!("'{}'", esc(s))).collect::<Vec<_>>().join(","));
    let date_str = signal.trading_date.format("%Y-%m-%d").to_string();

    client.query(&format!(
        "INSERT INTO trading.signals \
         (id, trading_date, symbol, security_id, direction, entry_price, entry_bucket, \
          entry_ts, score, signals_fired, tp_price, sl_price, quantity, \
          exit_price, exit_bucket, exit_reason, actual_return_pct, pnl_rupees, \
          cfg_entry_start, cfg_entry_end, cfg_min_move_pct, cfg_min_volume, \
          cfg_min_score, cfg_tp_pct, cfg_sl_pct, cfg_hard_exit_bucket, cfg_quantity) \
         VALUES ('{}',toDate('{}'),'{}','{}',{},{},{},{},{},{},{},{},{},{},{},{},{},{},1,2,0,0,0,{},{},{},1)",
        signal_id, date_str,
        esc(&signal.symbol), esc(&signal.security_id),
        dir_val,
        signal.entry_price, signal.entry_bucket,
        signal.entry_ts,
        signal.score, fired_arr,
        signal.tp_price, signal.sl_price, signal.quantity,
        exit.exit_price, exit.exit_bucket, exit_reason_val,
        exit.actual_return_pct, exit.pnl_rupees,
        config.tp_pct, config.sl_pct, config.exit_bucket,
    )).execute().await?;
    Ok(())
}

/// Reload unclosed signals from DB for crash recovery.
pub async fn get_open_signals(
    client: &Client,
    trading_date: NaiveDate,
) -> Result<Vec<(uuid::Uuid, Signal)>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    #[allow(dead_code)]
    struct Row {
        id: String,
        symbol: String, security_id: String,
        direction: u8,
        entry_price: f32, entry_bucket: u16,
        entry_ts: u32,
        score: u8, signals_fired: Vec<String>,
        tp_price: f32, sl_price: f32, quantity: u32,
    }
    let rows = client.query(
        "SELECT id, symbol, security_id, direction, entry_price, entry_bucket, \
         toUnixTimestamp(entry_ts) AS entry_ts, \
         score, signals_fired, tp_price, sl_price, quantity \
         FROM trading.signals FINAL \
         WHERE trading_date = toDate(?) AND exit_reason IS NULL"
    ).bind(trading_date.format("%Y-%m-%d").to_string())
     .fetch_all::<Row>().await?;

    Ok(rows.into_iter().filter_map(|r| {
        let id = uuid::Uuid::parse_str(&r.id).ok()?;
        let direction = if r.direction == 1 { Direction::Buy } else { Direction::Sell };
        Some((id, Signal {
            trading_date,
            symbol: r.symbol,
            security_id: r.security_id,
            direction,
            entry_price: r.entry_price,
            entry_bucket: r.entry_bucket,
            entry_ts: r.entry_ts,
            score: r.score,
            signals_fired: r.signals_fired,
            tp_price: r.tp_price,
            sl_price: r.sl_price,
            quantity: r.quantity,
            open_price: r.entry_price,
            gap_pct: 0.0,
        }))
    }).collect())
}
