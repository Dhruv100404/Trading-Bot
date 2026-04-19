use clickhouse::Client;
use anyhow::Result;
use chrono::{NaiveDate, Datelike};
use crate::types::Snapshot;

fn to_ch_date(d: NaiveDate) -> u16 {
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    (d - epoch).num_days() as u16
}

#[derive(clickhouse::Row, serde::Serialize)]
struct SnapshotRow {
    trading_date: u16,
    symbol: String,
    security_id: String,
    bucket: u16,
    ltp: f32,
    candle_open: f32,
    candle_high: f32,
    candle_low: f32,
    volume_cum: u64,
    volume_delta: u32,
    vwap: f32,
    volume_rate: f32,
    candle_body_ratio: f32,
}

pub async fn insert_batch(client: &Client, snapshots: &[Snapshot]) -> Result<()> {
    if snapshots.is_empty() { return Ok(()); }

    let mut ins = client.insert("trading.snapshots")?;
    let write_result: Result<()> = async {
        for s in snapshots {
            let days = to_ch_date(s.trading_date);
            ins.write(&SnapshotRow {
                trading_date: days,
                symbol: s.symbol.clone(),
                security_id: s.security_id.clone(),
                bucket: s.bucket,
                ltp: s.ltp,
                candle_open: s.candle_open,
                candle_high: s.candle_high,
                candle_low: s.candle_low,
                volume_cum: s.volume_cum,
                volume_delta: s.volume_delta,
                vwap: s.vwap,
                volume_rate: s.volume_rate,
                candle_body_ratio: s.candle_body_ratio,
            }).await?;
        }
        Ok(())
    }.await;
    ins.end().await?;
    write_result
}

pub async fn get_today_snapshots(
    client: &Client,
    trading_date: NaiveDate,
) -> Result<Vec<Snapshot>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row {
        symbol: String, security_id: String,
        bucket: u16, ltp: f32, candle_open: f32,
        candle_high: f32, candle_low: f32,
        volume_cum: u64, volume_delta: u32,
        vwap: f32, volume_rate: f32,
        candle_body_ratio: f32,
    }
    let rows = client.query(
        "SELECT symbol, security_id, bucket, ltp, candle_open, candle_high, candle_low, \
         volume_cum, volume_delta, vwap, volume_rate, candle_body_ratio \
         FROM trading.snapshots WHERE trading_date = toDate(?) ORDER BY symbol, bucket"
    ).bind(trading_date.format("%Y-%m-%d").to_string())
     .fetch_all::<Row>().await?;

    Ok(rows.into_iter().map(|r| Snapshot {
        symbol: r.symbol, security_id: r.security_id,
        trading_date, bucket: r.bucket, ltp: r.ltp,
        candle_open: r.candle_open, candle_high: r.candle_high, candle_low: r.candle_low,
        volume_cum: r.volume_cum, volume_delta: r.volume_delta,
        vwap: r.vwap, volume_rate: r.volume_rate, candle_body_ratio: r.candle_body_ratio,
    }).collect())
}

/// Returns the highest bucket number stored for a symbol/date.
/// Returns 0 if no data exists.
pub async fn get_max_bucket(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
) -> Result<u16> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { m: Option<u16> }
    let rows = client.query(
        "SELECT max(bucket) as m FROM trading.snapshots \
         WHERE trading_date = toDate(?) AND symbol = ?"
    )
    .bind(trading_date.format("%Y-%m-%d").to_string())
    .bind(symbol)
    .fetch_all::<Row>().await?;
    Ok(rows.first().and_then(|r| r.m).unwrap_or(0))
}

/// Returns true if we have bucket 375 (the closing bucket) for the given date.
pub async fn have_bucket_375(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
) -> Result<bool> {
    let m = get_max_bucket(client, trading_date, symbol).await?;
    Ok(m >= 375)
}

/// Insert a batch of snapshots for a historical date.
/// Only inserts rows that don't already exist (no overwrite of live data).
/// `candles` maps bucket -> (timestamp, open, high, low, close, volume).
pub async fn insert_historical_buckets(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
    security_id: &str,
    candles: &[(u16, i64, f32, f32, f32, f32, u64)], // (bucket, _ts, open, high, low, close, volume)
    start_bucket: u16,
) -> Result<()> {
    if candles.is_empty() { return Ok(()); }

    let mut ins = client.insert("trading.snapshots")?;
    for (bucket, _ts, open, high, low, close, volume) in candles {
        if *bucket < start_bucket { continue; }
        let days = to_ch_date(trading_date);

        // Compute derived fields for historical candle
        let range = high - low;
        let ltp = *close;
        let candle_body_ratio = if range > 0.0 { (ltp - open).abs() / range } else { 0.0 };
        let volume_delta = *volume as u32; // historical volume is total for period, use as delta
        let volume_cum = *volume;
        let vwap = if volume_cum > 0 { (*close as f64 * *volume as f64) as f32 } else { *close };

        // bucket → minute: bucket 1 = 9:15 AM, bucket 375 = 3:29 PM
        // minute = bucket + 14 (e.g. bucket 1 → 15 min = 9:15)
        // volume rate = volume_delta / 60 seconds (historical 1-min candle)
        let volume_rate = (volume_delta as f32) / 60.0;

        ins.write(&SnapshotRow {
            trading_date: days,
            symbol: symbol.to_string(),
            security_id: security_id.to_string(),
            bucket: *bucket,
            ltp,
            candle_open: *open,
            candle_high: *high,
            candle_low: *low,
            volume_cum,
            volume_delta,
            vwap,
            volume_rate,
            candle_body_ratio,
        }).await?;
    }
    ins.end().await?;
    Ok(())
}

/// Single DB call: compute gap_pct AND prev-day direction for ALL symbols.
/// prev_close = highest bucket in 345..375 from previous trading day.
/// direction = +1 if prev_close > prev_open, -1 otherwise.
/// Returns (gap_pct_map, direction_map) in one query.
pub async fn compute_gap_and_direction(
    client: &Client,
    trading_date: NaiveDate,
) -> Result<(std::collections::HashMap<String, f32>, std::collections::HashMap<String, i8>)> {
    let td = trading_date.format("%Y-%m-%d").to_string();

    let prev_day = crate::types::prev_trading_day(trading_date);
    let pd = prev_day.format("%Y-%m-%d").to_string();

    // ── Diagnostic: how many closing buckets exist for prev_day? ──
    tracing::info!(
        "[GAP-CALC] ▶ trading_date={td}  prev_trading_day={pd}  (skipped {} calendar day(s))",
        (trading_date - prev_day).num_days()
    );

    let ch_url = std::env::var("CLICKHOUSE_URL").unwrap_or_else(|_| "http://clickhouse:8123".into());

    // Count how many symbols have closing data (bucket 345-375) on prev_day for diagnostics.
    {
        let diag_sql = format!(
            "SELECT count(DISTINCT symbol) AS n \
             FROM trading.snapshots \
             WHERE trading_date = toDate('{pd}') AND bucket >= 345 AND bucket <= 375 \
             FORMAT TabSeparated"
        );
        match reqwest::Client::new().post(&ch_url).body(diag_sql).send().await {
            Ok(r) if r.status().is_success() => {
                let body = r.text().await.unwrap_or_default();
                let n: u64 = body.trim().parse().unwrap_or(0);
                if n == 0 {
                    tracing::error!(
                        "[GAP-CALC] ❌ DIAGNOSTIC: prev_day={pd} has ZERO symbols with \
                         closing buckets (345-375) in trading.snapshots! \
                         Gap calc WILL return empty. Check historical fill for {pd}."
                    );
                } else {
                    tracing::info!(
                        "[GAP-CALC] ✅ DIAGNOSTIC: prev_day={pd} has {n} symbols with \
                         closing buckets (345-375) — gap calc should succeed."
                    );
                }
            }
            Ok(r) => tracing::warn!("[GAP-CALC] DIAGNOSTIC query failed (HTTP {})", r.status()),
            Err(e) => tracing::warn!("[GAP-CALC] DIAGNOSTIC query error: {}", e),
        }
    }
    let sql = format!(
        "WITH \
         today_open AS ( \
           SELECT symbol, argMin(ltp, bucket) AS day_open \
           FROM trading.snapshots \
           WHERE trading_date = toDate('{td}') AND bucket >= 1 AND bucket <= 10 \
           GROUP BY symbol \
         ), \
         prev_gap AS ( \
           SELECT symbol, argMax(ltp, bucket) AS prev_close \
           FROM trading.snapshots \
           WHERE trading_date = toDate('{pd}') AND bucket >= 345 AND bucket <= 375 \
           GROUP BY symbol \
         ), \
         prev_dir AS ( \
           SELECT symbol, \
             argMax(ltp, bucket) AS dir_close, \
             argMinIf(ltp, bucket, bucket >= 1 AND bucket <= 10) AS dir_open \
           FROM trading.snapshots \
           WHERE trading_date = toDate('{pd}') AND bucket >= 1 AND bucket <= 375 \
           GROUP BY symbol \
         ) \
         SELECT d.symbol, \
           if(g.prev_close > 0 AND t.day_open > 0, (t.day_open - g.prev_close) / g.prev_close * 100, 0) AS gap_pct, \
           d.dir_close, \
           d.dir_open \
         FROM prev_dir d \
         LEFT JOIN prev_gap g ON d.symbol = g.symbol \
         LEFT JOIN today_open t ON d.symbol = t.symbol \
         WHERE d.dir_close > 0 AND d.dir_open > 0 \
         FORMAT TabSeparated",
        td = td, pd = pd
    );

    tracing::info!("[GAP-CALC] Computing gap+direction: today={td}, prev_day={pd}");
    let t0 = std::time::Instant::now();

    let resp = reqwest::Client::new().post(&ch_url).body(sql).send().await?;
    let status = resp.status();
    let text = resp.text().await?;

    if !status.is_success() {
        tracing::error!("[GAP-CALC] ClickHouse error ({}): {}", status, text.trim());
        return Err(anyhow::anyhow!("ClickHouse gap query failed: {}", text.trim()));
    }

    let mut gaps = std::collections::HashMap::new();
    let mut dirs = std::collections::HashMap::new();
    let mut total_rows = 0usize;
    let mut parse_errors = 0usize;

    for line in text.lines() {
        let p: Vec<&str> = line.split('\t').collect();
        if p.len() < 4 {
            if !line.trim().is_empty() { parse_errors += 1; }
            continue;
        }
        total_rows += 1;
        let symbol = p[0].to_string();
        let gap_pct: f32 = p[1].parse().unwrap_or(0.0);
        let dir_close: f32 = p[2].parse().unwrap_or(0.0);
        let dir_open: f32 = p[3].parse().unwrap_or(0.0);

        if gap_pct != 0.0 {
            gaps.insert(symbol.clone(), gap_pct);
        }
        if dir_close > 0.0 && dir_open > 0.0 {
            dirs.insert(symbol, if dir_close > dir_open { 1i8 } else { -1i8 });
        }
    }

    let bullish = dirs.values().filter(|&&d| d == 1).count();
    let bearish = dirs.values().filter(|&&d| d == -1).count();

    tracing::info!(
        "[GAP-CALC] Done in {:.1}s — {} symbols total, {} with gap_pct, {} direction ({} bull / {} bear){}",
        t0.elapsed().as_secs_f32(),
        total_rows,
        gaps.len(),
        dirs.len(),
        bullish,
        bearish,
        if parse_errors > 0 { format!(", {} parse errors", parse_errors) } else { String::new() }
    );

    tracing::info!(
        "[GAP-CALC] ◀ Result: gaps={} dirs={} total_rows={} \
         (trading_date={td} prev_trading_day={pd})",
        gaps.len(), dirs.len(), total_rows
    );
    if gaps.is_empty() {
        tracing::error!(
            "[GAP-CALC] ❌ gap_pct_cache will be EMPTY after this call. \
             Diagnosis: prev_day={pd} closing data missing OR today_open \
             (bucket 1-10) not yet written for {td}. \
             total_rows from ClickHouse={total_rows}"
        );
    }

    // Log top 5 gaps by absolute magnitude
    if !gaps.is_empty() {
        let mut sorted: Vec<(&String, &f32)> = gaps.iter().collect();
        sorted.sort_by(|a, b| b.1.abs().partial_cmp(&a.1.abs()).unwrap_or(std::cmp::Ordering::Equal));
        let top: Vec<String> = sorted.iter().take(5)
            .map(|(sym, gap)| format!("{} {:+.2}%", sym, gap))
            .collect();
        tracing::info!("[GAP-CALC] Top gaps: {}", top.join(", "));
    }

    Ok((gaps, dirs))
}

// ─────────────────────────────────────────────────────────
//  Live-price gap computation (bucket-1 fast path)
// ─────────────────────────────────────────────────────────

/// Compute gap_pct for all symbols using *live REST prices* as today's open.
///
/// This is the **primary** gap computation path, called right after the first
/// REST poll completes at bucket 1 (9:15 AM).  It avoids the fatal timing
/// dependency that broke `compute_gap_and_direction`: that function reads
/// `today_open` from ClickHouse snapshots, but those snapshots don't exist
/// until *after* the first REST poll writes them — which is too late.
///
/// Algorithm:
///   1. Query ClickHouse for prev_day closing prices (bucket 345-375) only.
///      prev_day = `prev_trading_day(trading_date)` — correctly skips holidays.
///   2. Compute gap_pct = (live_ltp - prev_close) / prev_close × 100  in Rust.
///   3. Return the gap_pct map (only symbols with non-zero gap).
///
/// No dependency on today's ClickHouse data whatsoever.
pub async fn compute_gap_from_live_prices(
    trading_date: NaiveDate,
    live_ltp: &std::collections::HashMap<String, f32>,
) -> Result<std::collections::HashMap<String, f32>> {
    if live_ltp.is_empty() {
        return Ok(std::collections::HashMap::new());
    }

    let prev_day = crate::types::prev_trading_day(trading_date);
    let pd = prev_day.format("%Y-%m-%d").to_string();

    tracing::info!(
        "[GAP-LIVE] ▶ trading_date={}  prev_trading_day={}  live_prices={}",
        trading_date.format("%Y-%m-%d"),
        pd,
        live_ltp.len()
    );

    let ch_url = std::env::var("CLICKHOUSE_URL")
        .unwrap_or_else(|_| "http://clickhouse:8123".into());

    // Single query: prev_close per symbol from bucket 345-375 of prev_day.
    let sql = format!(
        "SELECT symbol, argMax(ltp, bucket) AS prev_close \
         FROM trading.snapshots \
         WHERE trading_date = toDate('{pd}') AND bucket >= 345 AND bucket <= 375 \
         GROUP BY symbol \
         FORMAT TabSeparated"
    );

    let t0 = std::time::Instant::now();
    let resp = reqwest::Client::new().post(&ch_url).body(sql).send().await?;
    let status = resp.status();
    let text = resp.text().await?;

    if !status.is_success() {
        tracing::error!("[GAP-LIVE] ClickHouse error ({}): {}", status, text.trim());
        return Err(anyhow::anyhow!("prev_close query failed: {}", text.trim()));
    }

    let mut prev_closes: std::collections::HashMap<String, f32> =
        std::collections::HashMap::new();
    for line in text.lines() {
        let p: Vec<&str> = line.split('\t').collect();
        if p.len() < 2 { continue; }
        let prev_close: f32 = p[1].parse().unwrap_or(0.0);
        if prev_close > 0.0 {
            prev_closes.insert(p[0].to_string(), prev_close);
        }
    }

    if prev_closes.is_empty() {
        tracing::error!(
            "[GAP-LIVE] ❌ prev_day={pd} has NO closing data (bucket 345-375) in \
             trading.snapshots — historical fill may have failed for {pd}. \
             gap_pct_cache will remain empty."
        );
        return Ok(std::collections::HashMap::new());
    }

    // Compute gap_pct in Rust: no ClickHouse round-trip for today's data.
    let mut gaps = std::collections::HashMap::new();
    let mut n_no_ltp = 0u32;
    for (symbol, &prev_close) in &prev_closes {
        match live_ltp.get(symbol).copied() {
            Some(ltp) if ltp > 0.0 => {
                let gap_pct = (ltp - prev_close) / prev_close * 100.0;
                if gap_pct != 0.0 {
                    gaps.insert(symbol.clone(), gap_pct);
                }
            }
            _ => n_no_ltp += 1,
        }
    }

    // Top-5 gaps for sanity check
    {
        let mut sorted: Vec<(&String, &f32)> = gaps.iter().collect();
        sorted.sort_by(|a, b| b.1.abs().partial_cmp(&a.1.abs())
            .unwrap_or(std::cmp::Ordering::Equal));
        let top: Vec<String> = sorted.iter().take(5)
            .map(|(sym, gap)| format!("{} {:+.2}%", sym, gap))
            .collect();
        tracing::info!(
            "[GAP-LIVE] ◀ Done in {:.1}s — prev_closes={} live_ltp={} \
             gaps={} no_ltp={}{}",
            t0.elapsed().as_secs_f32(),
            prev_closes.len(),
            live_ltp.len(),
            gaps.len(),
            n_no_ltp,
            if top.is_empty() { String::new() }
            else { format!("  top5: {}", top.join(", ")) }
        );
    }

    if gaps.is_empty() {
        tracing::error!(
            "[GAP-LIVE] ❌ 0 gaps computed despite {} prev_closes and {} live prices \
             — all gap_pcts were 0.0 (price unchanged from prev_close)?",
            prev_closes.len(), live_ltp.len()
        );
    }

    Ok(gaps)
}

/// Get the LTP from the latest bucket available in [min_bucket, max_bucket] for a
/// symbol/date, where "latest" means the bucket with the highest bucket number.
/// This gives the last traded price closest to market close.
/// Returns 0.0 if no data exists in the range.
///
/// Used as a fallback when bucket 375 is missing (e.g. failed historical backfill).
/// Call with max_bucket=375, min_bucket=350 to get the close proxy from bucket 350+.
pub async fn get_last_price_before_close(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
    max_bucket: u16,
    min_bucket: u16,
) -> Result<f32> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Row { ltp: f32 }
    let rows = client.query(
        "SELECT ltp FROM trading.snapshots \
         WHERE trading_date = toDate(?) AND symbol = ? AND bucket <= ? AND bucket >= ? \
         ORDER BY bucket DESC LIMIT 1"
    )
    .bind(trading_date.format("%Y-%m-%d").to_string())
    .bind(symbol)
    .bind(max_bucket)
    .bind(min_bucket)
    .fetch_all::<Row>().await?;
    Ok(rows.first().map(|r| r.ltp).unwrap_or(0.0))
}

/// Get the closing price for a symbol/date: LTP at bucket 375 (market close).
/// Returns 0.0 if bucket 375 is not found.
pub async fn get_closing_price(
    client: &Client,
    trading_date: NaiveDate,
    symbol: &str,
) -> Result<f32> {
    get_last_price_before_close(client, trading_date, symbol, 375, 375).await
}
