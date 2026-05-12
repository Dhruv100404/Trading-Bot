use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};
use chrono::{DateTime, Duration, NaiveDate};
use clickhouse::Row;
use serde::{de::DeserializeOwned, Deserialize, Serialize};

use crate::api::AppState;
use crate::api::swing::get_live_quotes;
use crate::types::{is_nse_holiday, now_ist};

pub(crate) const DEFAULT_PAPER_MAX_SESSIONS: u16 = 5;

const CREATE_PAPER_TRADES: &str = r#"
CREATE TABLE IF NOT EXISTS trading.paper_trades (
    symbol            String,
    company_name      String,
    setup_family      String,
    bias              String DEFAULT 'Long',
    entry_price       Float64,
    quantity          UInt32 DEFAULT 1,
    stop_loss         Float64,
    target_price      Float64,
    planned_at        DateTime DEFAULT now(),
    max_sessions      UInt16 DEFAULT 7,
    capital_allocated Float64 DEFAULT 50000,
    expected_hold     String DEFAULT '',
    thesis            String DEFAULT '',
    notes             String DEFAULT '',
    exit_price        Nullable(Float64),
    closed_at         Nullable(DateTime),
    close_reason      String DEFAULT '',
    realized_pnl      Float64 DEFAULT 0,
    enabled           UInt8 DEFAULT 1,
    inserted_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY symbol
"#;

const PAPER_TRADE_MIGRATIONS: &[&str] = &[
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS max_sessions UInt16 DEFAULT 10 AFTER planned_at",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS capital_allocated Float64 DEFAULT 50000 AFTER max_sessions",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS exit_price Nullable(Float64) AFTER notes",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS closed_at Nullable(DateTime) AFTER exit_price",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS close_reason String DEFAULT '' AFTER closed_at",
    "ALTER TABLE trading.paper_trades ADD COLUMN IF NOT EXISTS realized_pnl Float64 DEFAULT 0 AFTER close_reason",
];

#[derive(Row, Deserialize, Serialize, Clone)]
pub struct PaperTrade {
    symbol: String,
    company_name: String,
    setup_family: String,
    bias: String,
    entry_price: f64,
    quantity: u32,
    stop_loss: f64,
    target_price: f64,
    planned_at: String,
    max_sessions: u16,
    capital_allocated: f64,
    expected_hold: String,
    thesis: String,
    notes: String,
    exit_price: Option<f64>,
    closed_at: Option<String>,
    close_reason: String,
    realized_pnl: f64,
    current_price: f64,
    current_value: f64,
    unrealized_pnl: f64,
    unrealized_pnl_pct: f64,
    quote_source: String,
    quote_updated_at: String,
    enabled: u8,
}

#[derive(Row, Serialize)]
pub struct PaperTradeRow {
    pub(crate) symbol: String,
    pub(crate) company_name: String,
    pub(crate) setup_family: String,
    pub(crate) bias: String,
    pub(crate) entry_price: f64,
    pub(crate) quantity: u32,
    pub(crate) stop_loss: f64,
    pub(crate) target_price: f64,
    pub(crate) max_sessions: u16,
    pub(crate) capital_allocated: f64,
    pub(crate) expected_hold: String,
    pub(crate) thesis: String,
    pub(crate) notes: String,
    pub(crate) exit_price: Option<f64>,
    pub(crate) close_reason: String,
    pub(crate) realized_pnl: f64,
    pub(crate) enabled: u8,
}

#[derive(Deserialize)]
pub struct PaperTradeInput {
    symbol: String,
    company_name: String,
    setup_family: String,
    bias: Option<String>,
    entry_price: f64,
    quantity: Option<u32>,
    max_sessions: Option<u16>,
    capital_allocated: Option<f64>,
    stop_loss: f64,
    target_price: f64,
    expected_hold: Option<String>,
    thesis: Option<String>,
    notes: Option<String>,
}

#[derive(Row, Deserialize)]
struct PaperSymbolRow {
    symbol: String,
    security_id: String,
}

#[derive(Row, Deserialize)]
struct SettingRow {
    value: String,
}

#[derive(Deserialize)]
struct PaperHistoricalPriceRow {
    symbol: String,
    trade_date: String,
    day_close: f64,
}

#[derive(Deserialize)]
struct ClickHouseJsonEnvelope<T> {
    data: Vec<T>,
}

#[derive(Deserialize)]
pub struct PaperTradeCloseInput {
    exit_price: f64,
    close_reason: Option<String>,
}

#[derive(Serialize)]
pub struct PaperTradesResponse {
    trades: Vec<PaperTrade>,
}

#[derive(Deserialize)]
pub struct PaperBudgetInput {
    total_budget: f64,
}

#[derive(Serialize)]
pub struct PaperBudgetResponse {
    total_budget: f64,
    allocated_budget: f64,
    available_budget: f64,
}

fn paper_trade_select() -> &'static str {
    "SELECT symbol, company_name, setup_family, bias, entry_price, quantity, \
     stop_loss, target_price, \
     formatDateTime(planned_at, '%Y-%m-%dT%H:%i:%S%z') AS planned_at, \
     max_sessions, entry_price * quantity AS capital_allocated, expected_hold, thesis, notes, exit_price, \
     if(close_reason != '' AND close_reason != 'removed', formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%S%z'), if(isNull(closed_at), NULL, formatDateTime(assumeNotNull(closed_at), '%Y-%m-%dT%H:%i:%S%z'))) AS closed_at, \
     close_reason, realized_pnl, \
     if(isNull(exit_price), entry_price, assumeNotNull(exit_price)) AS current_price, \
     if(isNull(exit_price), entry_price * quantity, assumeNotNull(exit_price) * quantity) AS current_value, \
     if(isNull(exit_price), 0, realized_pnl) AS unrealized_pnl, \
     if(entry_price * quantity > 0, if(isNull(exit_price), 0, realized_pnl) / (entry_price * quantity) * 100, 0) AS unrealized_pnl_pct, \
     if(isNull(exit_price), 'entry', 'closed') AS quote_source, \
     formatDateTime(inserted_at, '%Y-%m-%dT%H:%i:%S%z') AS quote_updated_at, \
     enabled \
     FROM trading.paper_trades FINAL"
}

async fn enrich_with_live_quotes(state: &AppState, trades: &mut [PaperTrade]) {
    let open_symbols: Vec<String> = trades
        .iter()
        .filter(|trade| trade.enabled == 1)
        .map(|trade| trade.symbol.clone())
        .collect();
    if open_symbols.is_empty() {
        return;
    }

    if !is_nse_trading_day_now() {
        hydrate_with_historical_prices(state, trades, &open_symbols, "last-close").await;
        return;
    }

    if state.config.dhan_access_token.is_empty() || state.config.dhan_client_id.is_empty() {
        hydrate_with_historical_prices(state, trades, &open_symbols, "parquet-history").await;
        return;
    }

    let rows = match state
        .ch
        .query(
            "SELECT symbol, any(security_id) AS security_id \
             FROM trading.watchlist FINAL \
             WHERE enabled = 1 AND symbol IN ? \
             GROUP BY symbol",
        )
        .bind(open_symbols.clone())
        .fetch_all::<PaperSymbolRow>()
        .await
    {
        Ok(rows) => rows,
        Err(err) => {
            tracing::warn!("paper quote symbol lookup failed: {}", err);
            hydrate_with_historical_prices(state, trades, &open_symbols, "parquet-history").await;
            return;
        }
    };

    let symbol_by_security: std::collections::HashMap<String, String> = rows
        .iter()
        .map(|row| (row.security_id.clone(), row.symbol.clone()))
        .collect();
    let security_ids: Vec<String> = symbol_by_security.keys().cloned().collect();
    if security_ids.is_empty() {
        hydrate_with_historical_prices(state, trades, &open_symbols, "parquet-history").await;
        return;
    }

    let quotes = match get_live_quotes(state, &state.config, &security_ids).await {
        Ok(quotes) => quotes,
        Err(err) => {
            tracing::warn!("paper live quote fetch failed: {}", err);
            hydrate_with_historical_prices(state, trades, &open_symbols, "parquet-history").await;
            return;
        }
    };

    let now = crate::types::now_ist().to_rfc3339();
    let mut hydrated_symbols = std::collections::HashSet::new();
    for (security_id, quote) in quotes {
        let Some(symbol) = symbol_by_security.get(&security_id) else {
            continue;
        };
        let current_price = f64::from(quote.last_price.max(0.0));
        if current_price <= 0.0 {
            continue;
        }
        for trade in trades.iter_mut().filter(|trade| trade.symbol == *symbol && trade.enabled == 1) {
            trade.current_price = current_price;
            trade.current_value = current_price * f64::from(trade.quantity);
            trade.unrealized_pnl = (current_price - trade.entry_price) * f64::from(trade.quantity);
            let invested = trade.entry_price * f64::from(trade.quantity);
            trade.unrealized_pnl_pct = if invested > 0.0 {
                trade.unrealized_pnl / invested * 100.0
            } else {
                0.0
            };
            trade.quote_source = "dhan-live".to_string();
            trade.quote_updated_at = now.clone();
            hydrated_symbols.insert(symbol.clone());
        }
    }

    let missing_symbols: Vec<String> = open_symbols
        .into_iter()
        .filter(|symbol| !hydrated_symbols.contains(symbol))
        .collect();
    hydrate_with_historical_prices(state, trades, &missing_symbols, "parquet-history").await;
}

async fn hydrate_with_historical_prices(
    state: &AppState,
    trades: &mut [PaperTrade],
    symbols: &[String],
    source_prefix: &str,
) {
    if symbols.is_empty() {
        return;
    }

    let symbol_list = symbols
        .iter()
        .map(|symbol| format!("'{}'", escape_sql_string(symbol)))
        .collect::<Vec<_>>()
        .join(",");

    let query = format!(
        "WITH daily AS ( \
            SELECT \
                symbol, \
                toDate(date) AS trade_date, \
                argMax(close, bucket) AS day_close \
            FROM file('parquets/candles_*.parquet', Parquet) \
            WHERE symbol IN ({symbol_list}) \
              AND date IS NOT NULL \
              AND close IS NOT NULL \
            GROUP BY symbol, trade_date \
        ), ranked AS ( \
            SELECT \
                symbol, \
                toString(trade_date) AS trade_date, \
                toFloat64(day_close) AS day_close, \
                row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn \
            FROM daily \
        ) \
        SELECT symbol, trade_date, day_close \
        FROM ranked \
        WHERE rn = 1"
    );

    let rows = match run_clickhouse_json_query::<PaperHistoricalPriceRow>(state, query).await {
        Ok(rows) => rows,
        Err(err) => {
            tracing::warn!("paper historical price fallback failed: {}", err);
            return;
        }
    };

    let now = crate::types::now_ist().to_rfc3339();
    for row in rows {
        if row.day_close <= 0.0 {
            continue;
        }
        for trade in trades
            .iter_mut()
            .filter(|trade| trade.symbol == row.symbol && trade.enabled == 1)
        {
            apply_current_price(
                trade,
                row.day_close,
                format!("{source_prefix}:{}", row.trade_date),
                now.clone(),
            );
        }
    }
}

fn is_nse_trading_day_now() -> bool {
    let now = now_ist();
    !is_nse_holiday(now.date_naive())
}

fn apply_current_price(
    trade: &mut PaperTrade,
    current_price: f64,
    quote_source: String,
    quote_updated_at: String,
) {
    trade.current_price = current_price;
    trade.current_value = current_price * f64::from(trade.quantity);
    trade.unrealized_pnl = (current_price - trade.entry_price) * f64::from(trade.quantity);
    let invested = trade.entry_price * f64::from(trade.quantity);
    trade.unrealized_pnl_pct = if invested > 0.0 {
        trade.unrealized_pnl / invested * 100.0
    } else {
        0.0
    };
    trade.quote_source = quote_source;
    trade.quote_updated_at = quote_updated_at;
}

fn validate_stop_loss(entry_price: f64, stop_loss: f64) -> Result<f64, String> {
    if stop_loss > 0.0 && stop_loss < entry_price {
        return Ok(round2(stop_loss));
    }

    Err("paper trade requires a stop_loss below entry_price".to_string())
}

fn validate_target_price(entry_price: f64, target_price: f64) -> Result<f64, String> {
    if target_price > entry_price {
        return Ok(round2(target_price));
    }

    Err("paper trade requires a target_price above entry_price".to_string())
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn trading_sessions_elapsed(planned_at: &str) -> u16 {
    let now = now_ist();
    let Some(planned_date) = parse_paper_datetime(planned_at) else {
        return 0;
    };
    count_trading_sessions(planned_date, now.date_naive())
}

fn parse_paper_datetime(value: &str) -> Option<NaiveDate> {
    DateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%z")
        .ok()
        .map(|dt| dt.date_naive())
}

fn count_trading_sessions(from: NaiveDate, to: NaiveDate) -> u16 {
    if to < from {
        return 0;
    }

    let mut sessions = 0u16;
    let mut cursor = from;
    while cursor <= to {
        if !is_nse_holiday(cursor) {
            sessions = sessions.saturating_add(1);
        }
        cursor = cursor + Duration::days(1);
    }
    sessions
}

async fn run_clickhouse_json_query<T: DeserializeOwned>(
    state: &AppState,
    sql: String,
) -> anyhow::Result<Vec<T>> {
    let response = reqwest::Client::new()
        .post(&state.ch_url)
        .body(format!("{} FORMAT JSON", sql))
        .send()
        .await?
        .error_for_status()?
        .json::<ClickHouseJsonEnvelope<T>>()
        .await?;
    Ok(response.data)
}

fn escape_sql_string(value: &str) -> String {
    value.replace('\'', "''")
}

pub(crate) async fn ensure_table(state: &AppState) -> Result<(), String> {
    state
        .ch
        .query(CREATE_PAPER_TRADES)
        .execute()
        .await
        .map_err(|err| format!("paper_trades table: {err}"))?;

    for migration in PAPER_TRADE_MIGRATIONS {
        state
            .ch
            .query(migration)
            .execute()
            .await
            .map_err(|err| format!("paper_trades migration: {err}"))?;
    }

    Ok(())
}

pub(crate) async fn upsert_system_trade(
    state: &AppState,
    trade: PaperTradeRow,
) -> Result<(), String> {
    ensure_table(state).await?;

    let mut insert = state
        .ch
        .insert("trading.paper_trades")
        .map_err(|err| format!("paper trades system insert: {err}"))?;
    insert
        .write(&trade)
        .await
        .map_err(|err| format!("paper trades system write: {err}"))?;
    insert
        .end()
        .await
        .map_err(|err| format!("paper trades system commit: {err}"))?;

    Ok(())
}

async fn paper_budget_snapshot(state: &AppState) -> Result<PaperBudgetResponse, String> {
    ensure_table(state).await?;

    let allocated_budget: f64 = state
        .ch
        .query(
            "SELECT sum(entry_price * quantity) \
             FROM trading.paper_trades FINAL \
             WHERE enabled = 1",
        )
        .fetch_one::<f64>()
        .await
        .map_err(|err| format!("paper budget allocated: {err}"))?;

    let stored = state
        .ch
        .query("SELECT value FROM trading.system_settings FINAL WHERE key = 'paper_total_budget' LIMIT 1")
        .fetch_optional::<SettingRow>()
        .await
        .map_err(|err| format!("paper budget setting: {err}"))?;

    let total_budget = stored
        .and_then(|row| row.value.parse::<f64>().ok())
        .unwrap_or(allocated_budget)
        .max(allocated_budget);

    Ok(PaperBudgetResponse {
        total_budget,
        allocated_budget,
        available_budget: total_budget - allocated_budget,
    })
}

pub async fn budget(State(state): State<AppState>) -> Result<Json<PaperBudgetResponse>, (StatusCode, String)> {
    paper_budget_snapshot(&state)
        .await
        .map(Json)
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))
}

pub async fn set_budget(
    State(state): State<AppState>,
    Json(input): Json<PaperBudgetInput>,
) -> Result<Json<PaperBudgetResponse>, (StatusCode, String)> {
    ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let total_budget = input.total_budget.max(0.0);
    state
        .ch
        .query(
            "INSERT INTO trading.system_settings (key, value) VALUES ('paper_total_budget', ?)",
        )
        .bind(total_budget.to_string())
        .execute()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper budget save: {err}")))?;

    paper_budget_snapshot(&state)
        .await
        .map(Json)
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))
}

pub async fn list(State(state): State<AppState>) -> Result<Json<PaperTradesResponse>, (StatusCode, String)> {
    ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let mut trades = fetch_visible_paper_trades(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
    enrich_with_live_quotes(&state, &mut trades).await;

    if auto_close_triggered_trades(&state, &trades).await? > 0 {
        trades = fetch_visible_paper_trades(&state)
            .await
            .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;
        enrich_with_live_quotes(&state, &mut trades).await;
    }

    Ok(Json(PaperTradesResponse { trades }))
}

async fn fetch_visible_paper_trades(state: &AppState) -> Result<Vec<PaperTrade>, String> {
    state
        .ch
        .query(&format!(
            "{} WHERE enabled = 1 OR close_reason != 'removed' ORDER BY inserted_at DESC",
            paper_trade_select()
        ))
        .fetch_all::<PaperTrade>()
        .await
        .map_err(|err| format!("paper trades list: {err}"))
}

async fn auto_close_triggered_trades(state: &AppState, trades: &[PaperTrade]) -> Result<usize, (StatusCode, String)> {
    let mut closed_count = 0;

    for trade in trades.iter().filter(|trade| trade.enabled == 1) {
        let stop_hit = trade.stop_loss > 0.0 && trade.current_price > 0.0 && trade.current_price <= trade.stop_loss;
        let target_hit = trade.target_price > 0.0 && trade.current_price > 0.0 && trade.current_price >= trade.target_price;
        let sessions_elapsed = trading_sessions_elapsed(&trade.planned_at);
        let time_exit = sessions_elapsed >= trade.max_sessions;

        let (exit_price, close_reason) = if stop_hit {
            (trade.stop_loss, "stop-loss".to_string())
        } else if target_hit {
            (trade.target_price, "target-hit".to_string())
        } else if time_exit {
            (
                if trade.current_price > 0.0 { trade.current_price } else { trade.entry_price },
                format!("auto-closed after {} trading sessions", trade.max_sessions),
            )
        } else {
            continue;
        };

        close_trade_row(state, trade, exit_price, close_reason).await?;
        closed_count += 1;
    }

    Ok(closed_count)
}

pub async fn upsert(
    State(state): State<AppState>,
    Json(input): Json<PaperTradeInput>,
) -> Result<Json<PaperTrade>, (StatusCode, String)> {
    ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let entry_price = input.entry_price.max(0.01);
    let stop_loss = validate_stop_loss(entry_price, input.stop_loss)
        .map_err(|err| (StatusCode::BAD_REQUEST, err))?;
    let target_price = validate_target_price(entry_price, input.target_price)
        .map_err(|err| (StatusCode::BAD_REQUEST, err))?;
    let quantity = input
        .quantity
        .unwrap_or_else(|| {
            input
                .capital_allocated
                .map(|capital| (capital / entry_price).floor() as u32)
                .unwrap_or(1)
        })
        .max(1);
    let capital_allocated = entry_price * f64::from(quantity);
    let trade = PaperTradeRow {
        symbol: input.symbol.trim().to_uppercase(),
        company_name: input.company_name,
        setup_family: input.setup_family,
        bias: input.bias.unwrap_or_else(|| "Long".to_string()),
        entry_price,
        quantity,
        stop_loss,
        target_price,
        max_sessions: input.max_sessions.unwrap_or(DEFAULT_PAPER_MAX_SESSIONS).max(1),
        capital_allocated,
        expected_hold: input
            .expected_hold
            .unwrap_or_else(|| format!("{} trading sessions", DEFAULT_PAPER_MAX_SESSIONS)),
        thesis: input.thesis.unwrap_or_default(),
        notes: input.notes.unwrap_or_default(),
        exit_price: None,
        close_reason: String::new(),
        realized_pnl: 0.0,
        enabled: 1,
    };

    let mut insert = state
        .ch
        .insert("trading.paper_trades")
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades insert: {err}")))?;
    insert
        .write(&trade)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades write: {err}")))?;
    insert
        .end()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades commit: {err}")))?;

    let saved = state
        .ch
        .query(&format!(
            "{} WHERE symbol = ? ORDER BY inserted_at DESC LIMIT 1",
            paper_trade_select()
        ))
        .bind(&trade.symbol)
        .fetch_one::<PaperTrade>()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades fetch saved: {err}")))?;

    let mut enriched = vec![saved];
    enrich_with_live_quotes(&state, &mut enriched).await;
    Ok(Json(enriched.remove(0)))
}

pub async fn close(
    State(state): State<AppState>,
    Path(symbol): Path<String>,
    Json(input): Json<PaperTradeCloseInput>,
) -> Result<Json<PaperTrade>, (StatusCode, String)> {
    ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let symbol = symbol.trim().to_uppercase();
    let current = state
        .ch
        .query(&format!(
            "{} WHERE symbol = ? AND enabled = 1 LIMIT 1",
            paper_trade_select()
        ))
        .bind(&symbol)
        .fetch_one::<PaperTrade>()
        .await
        .map_err(|err| (StatusCode::NOT_FOUND, format!("open paper trade not found: {err}")))?;

    let exit_price = input.exit_price.max(0.0);
    close_trade_row(
        &state,
        &current,
        exit_price,
        input
            .close_reason
            .unwrap_or_else(|| "session-expired".to_string()),
    )
    .await?;

    let saved = state
        .ch
        .query(&format!("{} WHERE symbol = ? LIMIT 1", paper_trade_select()))
        .bind(&symbol)
        .fetch_one::<PaperTrade>()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades fetch closed: {err}")))?;

    Ok(Json(saved))
}

async fn close_trade_row(
    state: &AppState,
    current: &PaperTrade,
    exit_price: f64,
    close_reason: String,
) -> Result<(), (StatusCode, String)> {
    let realized_pnl = (exit_price - current.entry_price) * f64::from(current.quantity);
    let closed = PaperTradeRow {
        symbol: current.symbol.clone(),
        company_name: current.company_name.clone(),
        setup_family: current.setup_family.clone(),
        bias: current.bias.clone(),
        entry_price: current.entry_price,
        quantity: current.quantity,
        stop_loss: current.stop_loss,
        target_price: current.target_price,
        max_sessions: current.max_sessions,
        capital_allocated: current.capital_allocated,
        expected_hold: current.expected_hold.clone(),
        thesis: current.thesis.clone(),
        notes: current.notes.clone(),
        exit_price: Some(exit_price),
        close_reason,
        realized_pnl,
        enabled: 0,
    };

    let mut insert = state
        .ch
        .insert("trading.paper_trades")
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades close insert: {err}")))?;
    insert
        .write(&closed)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades close write: {err}")))?;
    insert
        .end()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades close commit: {err}")))?;

    Ok(())
}

pub async fn remove(
    State(state): State<AppState>,
    Path(symbol): Path<String>,
) -> Result<StatusCode, (StatusCode, String)> {
    ensure_table(&state)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, err))?;

    let disabled = PaperTradeRow {
        symbol: symbol.trim().to_uppercase(),
        company_name: String::new(),
        setup_family: String::new(),
        bias: "Long".to_string(),
        entry_price: 0.0,
        quantity: 1,
        stop_loss: 0.0,
        target_price: 0.0,
        max_sessions: 1,
        capital_allocated: 0.0,
        expected_hold: String::new(),
        thesis: String::new(),
        notes: String::new(),
        exit_price: None,
        close_reason: "removed".to_string(),
        realized_pnl: 0.0,
        enabled: 0,
    };

    let mut insert = state
        .ch
        .insert("trading.paper_trades")
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades delete insert: {err}")))?;
    insert
        .write(&disabled)
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades delete write: {err}")))?;
    insert
        .end()
        .await
        .map_err(|err| (StatusCode::INTERNAL_SERVER_ERROR, format!("paper trades delete commit: {err}")))?;

    Ok(StatusCode::NO_CONTENT)
}
