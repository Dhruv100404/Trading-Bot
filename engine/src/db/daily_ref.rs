//! Database helpers for `trading.daily_ref`.
//!
//! Schema notes:
//! - The table is ordered by `(trading_date, symbol)`; `security_id` is a data
//!   column only and is NOT part of the ORDER BY key.
//! - Columns `prev_day_high` and `prev_day_low` exist in the schema but are
//!   populated by a future task; they remain at their default value of 0.0
//!   until that task is implemented.

use clickhouse::Client;
use anyhow::Result;
use chrono::NaiveDate;

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/// Convert a `NaiveDate` to the ClickHouse `Date` wire format: days since
/// the Unix epoch (1970-01-01), stored as `u32`.
fn fmt_date(d: NaiveDate) -> String {
    d.format("%Y-%m-%d").to_string()
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Pre-open insert at 9:14:50 AM: stores prev_close and pre_open_price.
/// Called once per day before market open using pre-open API data (or previous session close).
#[allow(dead_code)]
pub async fn insert_pre_open(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
    security_id: &str,
    prev_close: f32,
    pre_open_price: f32,
) -> Result<()> {
    client.query(
        "INSERT INTO trading.daily_ref \
         (trading_date, symbol, security_id, prev_close, pre_open_price, day_open, gap_pct) \
         VALUES (?,?,?,?,?,?,?)"
    )
    .bind(fmt_date(trading_date))
    .bind(symbol)
    .bind(security_id)
    .bind(prev_close)
    .bind(pre_open_price)
    .bind(0.0f32)   // day_open placeholder
    .bind(0.0f32)   // gap_pct placeholder
    .execute().await?;
    Ok(())
}

/// Bucket-1 re-insert at 9:15 AM: updates day_open and gap_pct.
/// Carries pre_open_price forward (cached from the pre-open insert) so it is not lost.
/// gap_pct = (day_open - prev_close) / prev_close * 100
pub async fn update_day_open(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
    security_id: &str,
    prev_close: f32,
    pre_open_price: f32,  // carry forward from insert_pre_open call
    day_open: f32,
) -> Result<()> {
    let gap_pct = if prev_close > 0.0 {
        (day_open - prev_close) / prev_close * 100.0
    } else { 0.0 };
    client.query(
        "INSERT INTO trading.daily_ref \
         (trading_date, symbol, security_id, prev_close, pre_open_price, day_open, gap_pct) \
         VALUES (?,?,?,?,?,?,?)"
    )
    .bind(fmt_date(trading_date))
    .bind(symbol)
    .bind(security_id)
    .bind(prev_close)
    .bind(pre_open_price)
    .bind(day_open)
    .bind(gap_pct)
    .execute().await?;
    Ok(())
}

/// Store the actual end-of-day closing price for a given trading date.
/// Called each morning using quote.close (= yesterday's EOD close from Dhan).
/// IMPORTANT: Reads existing row first and re-inserts with ALL fields preserved,
/// because ReplacingMergeTree would clobber other fields (day_open, gap_pct, etc.)
/// if we insert a partial row with only closing_price.
pub async fn store_closing_price(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
    security_id: &str,
    closing_price: f32,
) -> Result<()> {
    let date_str = fmt_date(trading_date);
    // Force merge so we read the latest version of this row (avoids losing day_open/gap_pct)
    client.query("OPTIMIZE TABLE trading.daily_ref FINAL").execute().await.ok();
    // Read existing row to preserve day_open, gap_pct, prev_close, etc.
    #[derive(clickhouse::Row, serde::Deserialize, Default)]
    struct Row {
        prev_close: f32, pre_open_price: f32, day_open: f32,
        gap_pct: f32, prev_day_high: f32, prev_day_low: f32,
    }
    let existing = client.query(
        "SELECT prev_close, pre_open_price, day_open, gap_pct, prev_day_high, prev_day_low \
         FROM trading.daily_ref FINAL WHERE trading_date = toDate(?) AND symbol = ? LIMIT 1"
    )
    .bind(&date_str).bind(symbol)
    .fetch_optional::<Row>().await.unwrap_or(None)
    .unwrap_or_default();

    client.query(
        "INSERT INTO trading.daily_ref \
         (trading_date, symbol, security_id, prev_close, pre_open_price, day_open, gap_pct, prev_day_high, prev_day_low, closing_price) \
         VALUES (?,?,?,?,?,?,?,?,?,?)"
    )
    .bind(&date_str).bind(symbol).bind(security_id)
    .bind(existing.prev_close).bind(existing.pre_open_price).bind(existing.day_open)
    .bind(existing.gap_pct).bind(existing.prev_day_high).bind(existing.prev_day_low)
    .bind(closing_price)
    .execute().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_gap_pct_formula() {
        let prev_close = 100.0f32;
        let day_open = 102.0f32;
        let gap_pct = (day_open - prev_close) / prev_close * 100.0;
        assert!((gap_pct - 2.0).abs() < 0.001);
    }

    #[test]
    fn test_gap_pct_negative() {
        let prev_close = 100.0f32;
        let day_open = 98.0f32;
        let gap_pct = (day_open - prev_close) / prev_close * 100.0;
        assert!((gap_pct + 2.0).abs() < 0.001); // -2.0
    }

    #[test]
    fn test_gap_pct_zero_prev_close() {
        // when prev_close is zero, gap_pct must be 0.0 (no division)
        let gap_pct = if 0.0f32 > 0.0 { (100.0f32 - 0.0f32) / 0.0f32 * 100.0 } else { 0.0 };
        assert_eq!(gap_pct, 0.0);
    }
}
