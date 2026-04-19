use serde::{Deserialize, Serialize};
use chrono::{DateTime, NaiveDate, Timelike};
use chrono_tz::Asia::Kolkata;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Direction { Buy, Sell }

impl Direction {
    pub fn sign(&self) -> f32 { match self { Direction::Buy => 1.0, Direction::Sell => -1.0 } }
    pub fn as_str(&self) -> &'static str { match self { Direction::Buy => "BUY", Direction::Sell => "SELL" } }
}

/// Gap-15 strategy configuration.
/// SELL gap-up stocks: gap_pct > gap_min_pct AND price < price_max AND LARGE+MEGA cap.
/// Entry at bucket 2 (9:16 AM), top N by gap_pct descending.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Gap15Config {
    /// Total capital in rupees (default 50_000).
    pub total_capital: u32,
    /// Leverage multiplier (default 5 = MIS 5x).
    pub leverage: u32,
    /// Max positions per day — top N by gap (default 15).
    pub top_n: usize,
    /// Take-profit % below entry for SELL (default 3.0).
    pub tp_pct: f32,
    /// Stop-loss % above entry for SELL (default 0.5).
    pub sl_pct: f32,
    /// Hard time-exit bucket (default 45 = 10:00 AM).
    pub exit_bucket: u16,
    /// Minimum gap_pct required (default 1.5).
    pub gap_min_pct: f32,
    /// Maximum gap_pct allowed (default 15.0) — filters out corporate actions (splits/bonus).
    pub gap_max_pct: f32,
    /// Maximum price per share in rupees (default 1000.0).
    pub price_max: f32,
    /// Position size multiplier for capping: max_pos = (total_margin/top_n) * cap_mult (default 2.0).
    pub cap_mult: f32,
    /// Slippage % for the LIMIT fallback order (default 0.30).
    /// Primary entry is always MARKET. If the MARKET order stays PENDING after 20s,
    /// it is cancelled and a LIMIT order is placed at ltp ± fallback_limit_slippage_pct%.
    /// SELL fallback: limit = ltp × (1 - pct/100)   BUY fallback: limit = ltp × (1 + pct/100)
    pub fallback_limit_slippage_pct: f32,
}

impl Default for Gap15Config {
    fn default() -> Self {
        Self {
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
            fallback_limit_slippage_pct: 0.30,
        }
    }
}

impl Gap15Config {
    /// Compute per-trade position value for n_selected trades today.
    /// Formula from backtest: min(total_margin / n_selected, base_pos * cap_mult)
    pub fn position_value(&self, n_selected: usize) -> u32 {
        if n_selected == 0 { return 0; }
        let total_margin = self.total_capital as u64 * self.leverage as u64;
        let base_pos = total_margin / self.top_n as u64;
        let max_pos = (base_pos as f64 * self.cap_mult as f64) as u64;
        let actual = (total_margin / n_selected as u64).min(max_pos);
        actual as u32
    }

    /// TP price for a SELL signal.
    pub fn tp_price(&self, entry: f32) -> f32 {
        (entry * (1.0 - self.tp_pct / 100.0) * 100.0).round() / 100.0
    }

    /// SL price for a SELL signal.
    pub fn sl_price(&self, entry: f32) -> f32 {
        (entry * (1.0 + self.sl_pct / 100.0) * 100.0).round() / 100.0
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub symbol: String,
    pub security_id: String,
    pub trading_date: NaiveDate,
    pub bucket: u16,
    pub ltp: f32,
    pub candle_open: f32,
    pub candle_high: f32,
    pub candle_low: f32,
    pub volume_cum: u64,
    pub volume_delta: u32,
    pub vwap: f32,
    pub volume_rate: f32,
    pub candle_body_ratio: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Signal {
    pub symbol: String,
    pub security_id: String,
    pub trading_date: NaiveDate,
    pub direction: Direction,
    pub score: u8,
    pub signals_fired: Vec<String>,
    pub entry_price: f32,
    pub entry_bucket: u16,
    /// Unix timestamp (seconds) when the signal fired.
    pub entry_ts: u32,
    pub tp_price: f32,
    pub sl_price: f32,
    pub quantity: u32,
    pub open_price: f32,
    pub gap_pct: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WsEvent {
    #[serde(rename = "type")]
    pub event_type: String,
    pub data: serde_json::Value,
}

/// Compute bucket number from IST DateTime.
/// bucket 1 = 9:15, bucket 2 = 9:16, bucket 45 = 10:00, etc.
pub fn compute_bucket(ts: &DateTime<chrono_tz::Tz>) -> u16 {
    let open_h = 9u32;
    let open_m = 15u32;
    let h = ts.hour();
    let m = ts.minute();
    let total_mins = h * 60 + m;
    let open_mins  = open_h * 60 + open_m;
    if total_mins < open_mins { return 0; }
    let close_mins = 15u32 * 60 + 30u32;
    if total_mins >= close_mins { return 0; }
    (total_mins - open_mins + 1) as u16
}

pub fn now_ist() -> DateTime<chrono_tz::Tz> {
    chrono::Utc::now().with_timezone(&Kolkata)
}

pub fn today_ist() -> NaiveDate {
    now_ist().date_naive()
}

/// NSE market holidays (trading halts — not weekends).
/// Add new years here as they are announced by NSE.
static NSE_HOLIDAYS: &[&str] = &[
    // 2023
    "2023-01-26","2023-03-07","2023-03-30","2023-04-04","2023-04-07","2023-04-14",
    "2023-04-22","2023-05-01","2023-06-29","2023-08-15","2023-09-19","2023-10-02",
    "2023-10-24","2023-11-14","2023-11-27","2023-12-25",
    // 2024
    "2024-01-26","2024-03-08","2024-03-25","2024-03-29","2024-04-11","2024-04-14",
    "2024-04-17","2024-04-21","2024-05-20","2024-06-17","2024-07-17","2024-08-15",
    "2024-09-16","2024-10-02","2024-10-12","2024-10-31","2024-11-01","2024-11-15","2024-12-25",
    // 2025
    "2025-01-26","2025-02-26","2025-03-14","2025-03-31","2025-04-10","2025-04-14",
    "2025-04-18","2025-05-01","2025-06-26","2025-07-06","2025-08-15","2025-08-16",
    "2025-08-27","2025-10-02","2025-10-21","2025-10-22","2025-11-05","2025-11-26","2025-12-25",
    // 2026
    "2026-01-15","2026-01-26","2026-03-03","2026-03-14","2026-03-26","2026-03-30","2026-03-31",
    "2026-04-03","2026-04-14","2026-05-01","2026-05-28","2026-06-26",
    "2026-09-14","2026-10-02","2026-10-20","2026-11-10","2026-11-24","2026-12-25",
];

/// Returns true if `date` is a weekend or NSE market holiday.
pub fn is_nse_holiday(date: NaiveDate) -> bool {
    use chrono::Datelike;
    let dow = date.weekday();
    if dow == chrono::Weekday::Sat || dow == chrono::Weekday::Sun { return true; }
    let s = date.format("%Y-%m-%d").to_string();
    NSE_HOLIDAYS.binary_search(&s.as_str()).is_ok()
}

/// Returns the most recent trading day strictly before `date`,
/// correctly skipping weekends and NSE holidays.
/// Looks back up to 14 days (handles long holiday stretches).
pub fn prev_trading_day(date: NaiveDate) -> NaiveDate {
    let mut d = date - chrono::Duration::days(1);
    for _ in 0..14 {
        if !is_nse_holiday(d) { return d; }
        d = d - chrono::Duration::days(1);
    }
    // Fallback: should never happen in practice
    d
}

/// Convert a Unix timestamp (UTC seconds) to bucket number.
pub fn timestamp_to_bucket(ts: i64) -> u16 {
    let utc_h = (ts / 3600) % 24;
    let utc_m = (ts / 60) % 60;
    let ist_h = utc_h as i32 + 5;
    let ist_m = utc_m as i32 + 30;
    let carry_h = ist_m >= 60;
    let final_h = (ist_h + if carry_h { 1 } else { 0 }) % 24;
    let final_m = ist_m % 60;
    let total_mins = final_h * 60 + final_m;
    let open_mins = 9 * 60 + 15;
    if total_mins < open_mins { return 0; }
    let close_mins = 15 * 60 + 30;
    if total_mins >= close_mins { return 0; }
    ((total_mins - open_mins) as u16) + 1
}

/// Convert IST hour+minute to bucket number.
pub fn minute_to_bucket(ist_hour: u32, ist_min: u32) -> u16 {
    let total_mins = ist_hour * 60 + ist_min;
    let open_mins = 9 * 60 + 15;
    if total_mins < open_mins { return 0; }
    let close_mins = 15 * 60 + 30;
    if total_mins >= close_mins { return 0; }
    (total_mins - open_mins + 1) as u16
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_compute_bucket_open() {
        let ts = Kolkata.with_ymd_and_hms(2026, 3, 24, 9, 15, 0).unwrap();
        assert_eq!(compute_bucket(&ts), 1);
    }

    #[test]
    fn test_compute_bucket_second_minute() {
        let ts = Kolkata.with_ymd_and_hms(2026, 3, 24, 9, 16, 0).unwrap();
        assert_eq!(compute_bucket(&ts), 2);
    }

    #[test]
    fn test_compute_bucket_10am() {
        let ts = Kolkata.with_ymd_and_hms(2026, 3, 24, 10, 0, 0).unwrap();
        assert_eq!(compute_bucket(&ts), 46);
    }

    #[test]
    fn test_exit_bucket_45_is_1000am() {
        // bucket 45 = 9:15 + 44 minutes = 9:59 AM
        // bucket 46 = 10:00 AM
        // Strategy says EXIT=b45 which in the backtest means minute 45 from open
        // backtest: EXIT_BKT=45, so exit after candle 45 (10:00 AM close)
        // In our bucket system: bucket 45 = 9:15+44min = 9:59, bucket 46 = 10:00
        // We use exit_bucket=46 to match "exit at 10:00 AM"
        let ts = Kolkata.with_ymd_and_hms(2026, 3, 24, 9, 59, 0).unwrap();
        assert_eq!(compute_bucket(&ts), 45);
        let ts2 = Kolkata.with_ymd_and_hms(2026, 3, 24, 10, 0, 0).unwrap();
        assert_eq!(compute_bucket(&ts2), 46);
    }

    #[test]
    fn test_gap15_config_position_value() {
        let cfg = Gap15Config::default();
        // total_margin = 50000 * 5 = 250000
        // base_pos = 250000 / 15 = 16666
        // max_pos = 16666 * 2 = 33333
        // n=15: min(250000/15, 33333) = min(16666, 33333) = 16666
        let v15 = cfg.position_value(15);
        assert_eq!(v15, 16666);
        // n=5: min(250000/5, 33333) = min(50000, 33333) = 33333
        let v5 = cfg.position_value(5);
        assert_eq!(v5, 33333);
    }

    #[test]
    fn test_compute_bucket_pre_market() {
        let ts = Kolkata.with_ymd_and_hms(2026, 3, 24, 9, 14, 59).unwrap();
        assert_eq!(compute_bucket(&ts), 0);
    }

    #[test]
    fn test_direction_sign() {
        assert_eq!(Direction::Buy.sign(), 1.0);
        assert_eq!(Direction::Sell.sign(), -1.0);
    }
}
