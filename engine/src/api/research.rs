use std::collections::HashMap;
use std::fs;
use std::path::Path;

use axum::{http::StatusCode, Json};
use serde::Serialize;
use serde_json::Value;

const MULTI_DERIVE_DIR: &str = "docs/multi_derive_outputs";
const CHAMPION_DIR: &str = "docs/multi_derive_outputs/real_backtest_atr_stretch_sideways_champion";

#[derive(Serialize, Clone)]
pub struct MultiDeriveMetric {
    strategy: String,
    cost_scenario: String,
    trades: u32,
    win_rate: f64,
    profit_factor: f64,
    total_return_proxy_pct: f64,
    max_drawdown_proxy_pct: f64,
    expectancy_pct: f64,
    avg_hold_days: f64,
    stop_atr: f64,
    target_atr: f64,
    max_hold_days: u32,
}

#[derive(Serialize, Clone)]
pub struct MultiDeriveSplit {
    strategy: String,
    trades: u32,
    win_rate: f64,
    profit_factor: f64,
    total_return_proxy_pct: f64,
    max_drawdown_proxy_pct: f64,
    expectancy_pct: f64,
    range_start: String,
    range_end: String,
}

#[derive(Serialize, Clone)]
pub struct MultiDeriveYear {
    year: u16,
    trades: u32,
    win_rate: f64,
    profit_factor: f64,
    total_return_proxy_pct: f64,
    max_drawdown_proxy_pct: f64,
    expectancy_pct: f64,
}

#[derive(Serialize, Clone)]
pub struct MultiDeriveExit {
    exit_reason: String,
    trades: u32,
    win_rate: f64,
    avg_net_return_pct: f64,
    total_net_return_pct: f64,
    avg_hold_days: f64,
}

#[derive(Serialize, Clone)]
pub struct MultiDeriveSymbol {
    symbol: String,
    trades: u32,
    win_rate: f64,
    avg_net_return_pct: f64,
    total_net_return_pct: f64,
}

#[derive(Serialize, Clone)]
pub struct MultiDeriveCandidate {
    symbol: String,
    trade_date: String,
    setup_family: String,
    close: f64,
    next_open: f64,
    composite_alpha_score: f64,
    rank_score: f64,
    regime_label: String,
    market_stress_score: f64,
    trend_regime: String,
    volatility_regime: String,
    rs60_rank: f64,
    relvol: f64,
    atr_pct: f64,
    gap_pct: f64,
    mfe_10d_pct: f64,
    mae_10d_pct: f64,
    hit_2pct_10d: bool,
    drawdown_3pct_10d: bool,
}

#[derive(Serialize)]
pub struct MultiDeriveResponse {
    updated_at: String,
    manifest: Value,
    champion_manifest: Value,
    metrics: Vec<MultiDeriveMetric>,
    splits: Vec<MultiDeriveSplit>,
    yearly: Vec<MultiDeriveYear>,
    exits: Vec<MultiDeriveExit>,
    symbols: Vec<MultiDeriveSymbol>,
    latest_candidates: Vec<MultiDeriveCandidate>,
    message: Option<String>,
}

pub async fn multi_derive() -> Result<Json<MultiDeriveResponse>, (StatusCode, String)> {
    let manifest = read_json(&format!("{MULTI_DERIVE_DIR}/manifest.json")).unwrap_or(Value::Null);
    let champion_manifest = read_json(&format!("{CHAMPION_DIR}/manifest.json")).unwrap_or(Value::Null);
    let metrics = read_metrics(&format!("{CHAMPION_DIR}/real_backtest_metrics.csv"))?;
    let splits = read_splits(&format!("{CHAMPION_DIR}/real_backtest_split_metrics.csv"))?;
    let yearly = read_yearly(&format!("{CHAMPION_DIR}/real_backtest_year_by_year.csv"))?;
    let exits = read_exits(&format!("{CHAMPION_DIR}/real_backtest_exit_summary.csv"))?;
    let symbols = read_symbols(&format!("{CHAMPION_DIR}/real_backtest_symbol_contribution.csv"))?;
    let latest_candidates = read_latest_candidates(&format!("{MULTI_DERIVE_DIR}/latest_candidates.csv"))?;

    let message = if metrics.is_empty() {
        Some("No multi-derive real backtest output was found. Run `python scripts\\multi_derive_research_pipeline.py` and `python scripts\\multi_derive_real_backtest.py`.".to_string())
    } else {
        None
    };

    Ok(Json(MultiDeriveResponse {
        updated_at: crate::types::now_ist().to_rfc3339(),
        manifest,
        champion_manifest,
        metrics,
        splits,
        yearly,
        exits,
        symbols,
        latest_candidates,
        message,
    }))
}

fn read_json(path: &str) -> anyhow::Result<Value> {
    let content = fs::read_to_string(Path::new(path))?;
    Ok(serde_json::from_str(&content)?)
}

fn read_csv(path: &str) -> Result<Vec<HashMap<String, String>>, (StatusCode, String)> {
    let content = fs::read_to_string(Path::new(path)).map_err(|err| {
        (
            StatusCode::NOT_FOUND,
            format!("Could not read multi-derive output `{path}`: {err}"),
        )
    })?;
    let mut lines = content.lines().filter(|line| !line.trim().is_empty());
    let Some(header_line) = lines.next() else {
        return Ok(Vec::new());
    };
    let headers = split_csv_line(header_line);
    let mut rows = Vec::new();
    for line in lines {
        let values = split_csv_line(line);
        let mut row = HashMap::new();
        for (idx, header) in headers.iter().enumerate() {
            row.insert(header.clone(), values.get(idx).cloned().unwrap_or_default());
        }
        rows.push(row);
    }
    Ok(rows)
}

fn split_csv_line(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut in_quotes = false;
    let chars = line.chars().peekable();
    for ch in chars {
        match ch {
            '"' => in_quotes = !in_quotes,
            ',' if !in_quotes => {
                out.push(current.trim_matches('"').to_string());
                current.clear();
            }
            _ => current.push(ch),
        }
    }
    out.push(current.trim_matches('"').to_string());
    out
}

fn s(row: &HashMap<String, String>, key: &str) -> String {
    row.get(key).cloned().unwrap_or_default()
}

fn f(row: &HashMap<String, String>, key: &str) -> f64 {
    row.get(key)
        .map(|value| {
            if value.eq_ignore_ascii_case("inf") {
                99.0
            } else {
                value
                    .parse::<f64>()
                    .ok()
                    .filter(|parsed| parsed.is_finite())
                    .unwrap_or(0.0)
            }
        })
        .unwrap_or(0.0)
}

fn u32v(row: &HashMap<String, String>, key: &str) -> u32 {
    row.get(key)
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(0)
}

fn u16v(row: &HashMap<String, String>, key: &str) -> u16 {
    row.get(key)
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(0)
}

fn b(row: &HashMap<String, String>, key: &str) -> bool {
    row.get(key)
        .map(|value| value.eq_ignore_ascii_case("true") || value == "1")
        .unwrap_or(false)
}

fn read_metrics(path: &str) -> Result<Vec<MultiDeriveMetric>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .map(|row| MultiDeriveMetric {
            strategy: s(&row, "strategy"),
            cost_scenario: s(&row, "cost_scenario"),
            trades: u32v(&row, "trades"),
            win_rate: f(&row, "win_rate"),
            profit_factor: f(&row, "profit_factor"),
            total_return_proxy_pct: f(&row, "total_return_proxy_pct"),
            max_drawdown_proxy_pct: f(&row, "max_drawdown_proxy_pct"),
            expectancy_pct: f(&row, "expectancy_pct"),
            avg_hold_days: f(&row, "avg_hold_days"),
            stop_atr: f(&row, "stop_atr"),
            target_atr: f(&row, "target_atr"),
            max_hold_days: u32v(&row, "max_hold_days"),
        })
        .collect())
}

fn read_splits(path: &str) -> Result<Vec<MultiDeriveSplit>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .map(|row| MultiDeriveSplit {
            strategy: s(&row, "strategy"),
            trades: u32v(&row, "trades"),
            win_rate: f(&row, "win_rate"),
            profit_factor: f(&row, "profit_factor"),
            total_return_proxy_pct: f(&row, "total_return_proxy_pct"),
            max_drawdown_proxy_pct: f(&row, "max_drawdown_proxy_pct"),
            expectancy_pct: f(&row, "expectancy_pct"),
            range_start: s(&row, "range_start"),
            range_end: s(&row, "range_end"),
        })
        .collect())
}

fn read_yearly(path: &str) -> Result<Vec<MultiDeriveYear>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .map(|row| MultiDeriveYear {
            year: u16v(&row, "year"),
            trades: u32v(&row, "trades"),
            win_rate: f(&row, "win_rate"),
            profit_factor: f(&row, "profit_factor"),
            total_return_proxy_pct: f(&row, "total_return_proxy_pct"),
            max_drawdown_proxy_pct: f(&row, "max_drawdown_proxy_pct"),
            expectancy_pct: f(&row, "expectancy_pct"),
        })
        .collect())
}

fn read_exits(path: &str) -> Result<Vec<MultiDeriveExit>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .map(|row| MultiDeriveExit {
            exit_reason: s(&row, "exit_reason"),
            trades: u32v(&row, "trades"),
            win_rate: f(&row, "win_rate"),
            avg_net_return_pct: f(&row, "avg_net_return_pct"),
            total_net_return_pct: f(&row, "total_net_return_pct"),
            avg_hold_days: f(&row, "avg_hold_days"),
        })
        .collect())
}

fn read_symbols(path: &str) -> Result<Vec<MultiDeriveSymbol>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .take(20)
        .map(|row| MultiDeriveSymbol {
            symbol: s(&row, "symbol"),
            trades: u32v(&row, "trades"),
            win_rate: f(&row, "win_rate"),
            avg_net_return_pct: f(&row, "avg_net_return_pct"),
            total_net_return_pct: f(&row, "total_net_return_pct"),
        })
        .collect())
}

fn read_latest_candidates(path: &str) -> Result<Vec<MultiDeriveCandidate>, (StatusCode, String)> {
    Ok(read_csv(path)?
        .into_iter()
        .take(40)
        .map(|row| MultiDeriveCandidate {
            symbol: s(&row, "symbol"),
            trade_date: s(&row, "trade_date"),
            setup_family: s(&row, "derived_setup_family"),
            close: f(&row, "close"),
            next_open: f(&row, "next_open"),
            composite_alpha_score: f(&row, "composite_alpha_score"),
            rank_score: f(&row, "rank_score"),
            regime_label: s(&row, "regime_label"),
            market_stress_score: f(&row, "market_stress_score"),
            trend_regime: s(&row, "trend_regime"),
            volatility_regime: s(&row, "volatility_regime"),
            rs60_rank: f(&row, "rs60_rank"),
            relvol: f(&row, "relvol"),
            atr_pct: f(&row, "atr_pct"),
            gap_pct: f(&row, "gap_pct"),
            mfe_10d_pct: f(&row, "mfe_10d_pct"),
            mae_10d_pct: f(&row, "mae_10d_pct"),
            hit_2pct_10d: b(&row, "hit_2pct_10d"),
            drawdown_3pct_10d: b(&row, "drawdown_3pct_10d"),
        })
        .collect())
}
