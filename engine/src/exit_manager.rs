use crate::types::{Direction, Signal};

#[derive(Debug, Clone, PartialEq)]
pub enum ExitReason { Tp, Sl, Time }

#[derive(Debug, Clone)]
pub struct ExitResult {
    pub reason: ExitReason,
    pub exit_price: f32,
    pub exit_bucket: u16,
    pub actual_return_pct: f32,
    pub pnl_rupees: f32,
}

/// Check whether an open signal should exit at the current tick.
///
/// Priority (matches backtest FIX 1): SL > TP > Time.
/// When SL and TP both breach in the same tick, SL wins (conservative).
/// This matches the live broker behavior: stop-loss order triggers first.
pub fn check_exit(
    signal: &Signal,
    current_ltp: f32,
    current_bucket: u16,
    exit_bucket: u16,
) -> Option<ExitResult> {
    let tp_active = (signal.tp_price - signal.entry_price).abs() > 0.001;
    let sl_active = (signal.sl_price - signal.entry_price).abs() > 0.001;

    let should_exit_tp = tp_active && match signal.direction {
        Direction::Buy  => current_ltp >= signal.tp_price,
        Direction::Sell => current_ltp <= signal.tp_price,
    };
    let should_exit_sl = sl_active && match signal.direction {
        Direction::Buy  => current_ltp <= signal.sl_price,
        Direction::Sell => current_ltp >= signal.sl_price,
    };
    let should_exit_time = current_bucket >= exit_bucket;

    // SL > TP > Time (matches backtest conservative priority)
    let reason = if should_exit_sl {
        ExitReason::Sl
    } else if should_exit_tp {
        ExitReason::Tp
    } else if should_exit_time {
        ExitReason::Time
    } else {
        return None;
    };

    let actual_return_pct = match signal.direction {
        Direction::Buy  => (current_ltp - signal.entry_price) / signal.entry_price * 100.0,
        Direction::Sell => (signal.entry_price - current_ltp) / signal.entry_price * 100.0,
    };
    let pnl_rupees = signal.entry_price * (actual_return_pct / 100.0) * signal.quantity as f32;

    Some(ExitResult {
        reason,
        exit_price: current_ltp,
        exit_bucket: current_bucket,
        actual_return_pct,
        pnl_rupees,
    })
}

/// Check whether an open signal should exit — TP or Time only (SL handled by broker).
///
/// Used when the broker holds a protective SL-M order on the exchange.
/// Software only needs to detect TP and time-based exits.
pub fn check_exit_tp_time(
    signal: &Signal,
    current_ltp: f32,
    current_bucket: u16,
    exit_bucket: u16,
) -> Option<ExitResult> {
    let tp_active = (signal.tp_price - signal.entry_price).abs() > 0.001;

    let should_exit_tp = tp_active && match signal.direction {
        Direction::Buy  => current_ltp >= signal.tp_price,
        Direction::Sell => current_ltp <= signal.tp_price,
    };
    let should_exit_time = current_bucket >= exit_bucket;

    // TP > Time (SL is not checked — broker handles it)
    let reason = if should_exit_tp {
        ExitReason::Tp
    } else if should_exit_time {
        ExitReason::Time
    } else {
        return None;
    };

    let actual_return_pct = match signal.direction {
        Direction::Buy  => (current_ltp - signal.entry_price) / signal.entry_price * 100.0,
        Direction::Sell => (signal.entry_price - current_ltp) / signal.entry_price * 100.0,
    };
    let pnl_rupees = signal.entry_price * (actual_return_pct / 100.0) * signal.quantity as f32;

    Some(ExitResult {
        reason,
        exit_price: current_ltp,
        exit_bucket: current_bucket,
        actual_return_pct,
        pnl_rupees,
    })
}
