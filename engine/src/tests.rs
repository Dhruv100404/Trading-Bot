#![cfg(test)]

use crate::types::{Gap15Config, Signal, Direction};
use crate::exit_manager::{check_exit, ExitReason};
use chrono::NaiveDate;

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn default_cfg() -> Gap15Config {
    Gap15Config {
        total_capital: 50_000,
        leverage: 5,
        top_n: 15,
        tp_pct: 3.0,
        sl_pct: 0.5,
        exit_bucket: 45,
        gap_min_pct: 1.5,
        gap_max_pct: 15.0,
        price_max: 1000.0,
        cap_mult: 2.0,
    }
}

fn sell_signal(entry_price: f32, cfg: &Gap15Config) -> Signal {
    Signal {
        symbol: "TEST".to_string(),
        security_id: "12345".to_string(),
        trading_date: NaiveDate::from_ymd_opt(2026, 4, 12).unwrap(),
        direction: Direction::Sell,
        score: 50,
        signals_fired: vec!["gap+2.0%".to_string()],
        entry_price,
        entry_bucket: 2,
        entry_ts: 0,
        tp_price: cfg.tp_price(entry_price),
        sl_price: cfg.sl_price(entry_price),
        quantity: 10,
        open_price: entry_price,
        gap_pct: 2.0,
    }
}

// ─── Gap15Config: position_value ──────────────────────────────────────────────

#[test]
fn test_position_value_15_stocks() {
    let cfg = default_cfg();
    // total_margin = 50000 * 5 = 250000
    // base_pos = 250000 / 15 = 16666
    // max_pos = 16666 * 2 = 33333 (as u64 floor)
    // actual = min(250000/15, 33333) = 16666
    let pv = cfg.position_value(15);
    assert_eq!(pv, 16666, "position_value(15) should be 16666");
}

#[test]
fn test_position_value_fewer_stocks_capped() {
    let cfg = default_cfg();
    // n=5: total_margin/5 = 50000, but max_pos = 33333 → capped at 33333
    let pv = cfg.position_value(5);
    assert_eq!(pv, 33333, "position_value(5) should be capped at max_pos=33333");
}

#[test]
fn test_position_value_1_stock_capped() {
    let cfg = default_cfg();
    // n=1: total_margin/1 = 250000, capped at 33333
    let pv = cfg.position_value(1);
    assert_eq!(pv, 33333, "position_value(1) should be capped at max_pos=33333");
}

#[test]
fn test_position_value_zero_stocks() {
    let cfg = default_cfg();
    assert_eq!(cfg.position_value(0), 0);
}

#[test]
fn test_position_value_scales_with_leverage() {
    let mut cfg = default_cfg();
    cfg.leverage = 10;
    // total_margin = 500000, base = 500000/15 = 33333, max = 33333*2 = 66666
    // n=15: min(33333, 66666) = 33333
    let pv = cfg.position_value(15);
    assert_eq!(pv, 33333);
}

// ─── Gap15Config: tp_price / sl_price ─────────────────────────────────────────

#[test]
fn test_tp_price_sell() {
    let cfg = default_cfg();
    // entry=100, tp=3% → 100*(1-0.03) = 97.0
    let tp = cfg.tp_price(100.0);
    assert!((tp - 97.0).abs() < 0.01, "tp_price(100) = {tp}, expected 97.0");
}

#[test]
fn test_sl_price_sell() {
    let cfg = default_cfg();
    // entry=100, sl=0.5% → 100*(1+0.005) = 100.5
    let sl = cfg.sl_price(100.0);
    assert!((sl - 100.5).abs() < 0.01, "sl_price(100) = {sl}, expected 100.5");
}

#[test]
fn test_tp_sl_direction_sell() {
    let cfg = default_cfg();
    // For a SELL: tp_price < entry_price (profit when price falls)
    //             sl_price > entry_price (loss when price rises)
    let entry = 500.0_f32;
    assert!(cfg.tp_price(entry) < entry, "tp_price must be below entry for SELL");
    assert!(cfg.sl_price(entry) > entry, "sl_price must be above entry for SELL");
}

#[test]
fn test_prices_rounded_to_paisa() {
    let cfg = default_cfg();
    // entry=333.33 → tp = 333.33 * 0.97 = 323.33 (rounded to 2dp)
    let tp = cfg.tp_price(333.33);
    let rounded = (tp * 100.0).round() / 100.0;
    assert!((tp - rounded).abs() < 0.001, "tp_price should be rounded to 2 decimal places");
}

// ─── Exit Manager: TP exit ────────────────────────────────────────────────────

#[test]
fn test_exit_tp_triggered() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // TP at 97.0; current ltp = 97.0 → should exit TP
    let result = check_exit(&sig, 97.0, 10, cfg.exit_bucket);
    assert!(result.is_some());
    let r = result.unwrap();
    assert_eq!(r.reason, ExitReason::Tp);
    assert!((r.actual_return_pct - 3.0).abs() < 0.01);
}

#[test]
fn test_exit_tp_not_triggered_above() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // ltp = 98.0, above tp=97.0 → no exit yet
    let result = check_exit(&sig, 98.0, 10, cfg.exit_bucket);
    assert!(result.is_none(), "Should not exit at ltp=98 when tp=97");
}

// ─── Exit Manager: SL exit ────────────────────────────────────────────────────

#[test]
fn test_exit_sl_triggered() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // SL at 100.5; current ltp = 100.5 → should exit SL
    let result = check_exit(&sig, 100.5, 10, cfg.exit_bucket);
    assert!(result.is_some());
    let r = result.unwrap();
    assert_eq!(r.reason, ExitReason::Sl);
    assert!(r.actual_return_pct < 0.0, "SL exit should be a loss");
}

#[test]
fn test_exit_sl_not_triggered_below() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // ltp = 100.3, below sl=100.5 → no exit
    let result = check_exit(&sig, 100.3, 10, cfg.exit_bucket);
    assert!(result.is_none());
}

// ─── Exit Manager: Time exit ──────────────────────────────────────────────────

#[test]
fn test_exit_time_at_exit_bucket() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // ltp=99.5 (no tp/sl hit), bucket=45 = exit_bucket → time exit
    let result = check_exit(&sig, 99.5, 45, cfg.exit_bucket);
    assert!(result.is_some());
    assert_eq!(result.unwrap().reason, ExitReason::Time);
}

#[test]
fn test_no_exit_before_exit_bucket() {
    let cfg = default_cfg();
    let sig = sell_signal(100.0, &cfg);
    // ltp = 99.5 (between entry and tp), bucket=44 → no exit
    let result = check_exit(&sig, 99.5, 44, cfg.exit_bucket);
    assert!(result.is_none());
}

// ─── Exit Manager: SL priority over TP ────────────────────────────────────────

#[test]
fn test_sl_wins_over_tp_same_tick() {
    let cfg = default_cfg();
    // Construct signal where somehow both SL and TP would trigger.
    // This is artificial but tests the priority: if SL triggers it should win.
    let mut sig = sell_signal(100.0, &cfg);
    // Force TP and SL to be on the same side of current ltp
    sig.tp_price = 100.5; // price "TP" above entry (won't normally happen, but tests priority)
    sig.sl_price = 100.5; // same price as TP
    // Both should trigger at ltp=100.5 → SL must win
    let result = check_exit(&sig, 100.5, 10, cfg.exit_bucket);
    assert!(result.is_some());
    assert_eq!(result.unwrap().reason, ExitReason::Sl, "SL must take priority over TP");
}

// ─── Entry filter logic ───────────────────────────────────────────────────────

#[test]
fn test_entry_filter_gap_threshold() {
    let cfg = default_cfg();
    // gap_min_pct = 1.5
    assert!(2.0 > cfg.gap_min_pct, "gap 2.0% should pass filter");
    assert!(!(1.4 > cfg.gap_min_pct), "gap 1.4% should fail filter");
    assert!(!(1.5 > cfg.gap_min_pct), "gap exactly 1.5% should fail (strict >)");
}

#[test]
fn test_entry_filter_price_cap() {
    let cfg = default_cfg();
    // price_max = 1000.0
    assert!(999.0 < cfg.price_max, "price 999 should pass filter");
    assert!(!(1000.0 < cfg.price_max), "price 1000 should fail (strict <)");
    assert!(!(1001.0 < cfg.price_max), "price 1001 should fail filter");
}

// ─── Margin arithmetic ────────────────────────────────────────────────────────

#[test]
fn test_total_margin() {
    let cfg = default_cfg();
    let total_margin = cfg.total_capital as u64 * cfg.leverage as u64;
    assert_eq!(total_margin, 250_000, "50000 * 5 = 250000");
}

#[test]
fn test_max_position_value() {
    let cfg = default_cfg();
    let total_margin = cfg.total_capital as u64 * cfg.leverage as u64;
    let base_pos = total_margin / cfg.top_n as u64;
    let max_pos = (base_pos as f64 * cfg.cap_mult as f64) as u64;
    assert_eq!(base_pos, 16666);
    assert_eq!(max_pos, 33333, "base_pos(16666) * cap_mult(2.0) ≈ 33333");
}
