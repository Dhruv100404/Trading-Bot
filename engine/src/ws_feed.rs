use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tokio::time::{sleep, Duration, Instant};
use futures::{StreamExt, SinkExt};
use reqwest_websocket::{RequestBuilderExt, Message};

/// 5-level bid/ask depth
#[allow(dead_code)]
#[derive(Debug, Clone, Default)]
pub struct DepthLevel {
    pub price: f32,
    pub qty: u32,
    pub orders: u16,
}

/// Parsed tick data from WebSocket
#[derive(Debug, Clone)]
pub struct TickData {
    pub security_id: String,
    pub symbol: String,
    pub ltp: f32,
    pub atp: f32,
    pub volume: u32,
    pub buy_qty: u32,
    pub sell_qty: u32,
    #[allow(dead_code)] pub day_open: f32,
    #[allow(dead_code)] pub day_high: f32,
    #[allow(dead_code)] pub day_low: f32,
    #[allow(dead_code)] pub day_close: f32,
    pub oi: u32,
    pub prev_oi: u32,
    #[allow(dead_code)] pub depth_bid: [DepthLevel; 5],
    #[allow(dead_code)] pub depth_ask: [DepthLevel; 5],
    pub updated_at: Instant,
}

/// Shared state between WebSocket feed and poller
pub struct LiveFeed {
    /// symbol → latest tick data
    pub ticks: HashMap<String, TickData>,
    /// futures secId → latest OI
    pub oi_map: HashMap<u32, u32>,
    /// equity secId → symbol mapping
    pub secid_to_symbol: HashMap<u32, String>,
    /// futures secId → equity symbol mapping (for OI)
    pub fut_to_symbol: HashMap<u32, String>,
    /// WebSocket connection status
    pub ws_connected: bool,
    pub last_tick_time: Option<Instant>,
}

impl LiveFeed {
    pub fn new() -> Self {
        Self {
            ticks: HashMap::new(),
            oi_map: HashMap::new(),
            secid_to_symbol: HashMap::new(),
            fut_to_symbol: HashMap::new(),
            ws_connected: false,
            last_tick_time: None,
        }
    }
}

pub type SharedFeed = Arc<RwLock<LiveFeed>>;

pub fn new_shared_feed() -> SharedFeed {
    Arc::new(RwLock::new(LiveFeed::new()))
}

/// Build subscription JSON for a batch of instruments (max 100 per message)
fn build_subscribe_json(request_code: u8, instruments: &[(String, String)]) -> String {
    // instruments = [(exchange_segment, security_id), ...]
    let list: Vec<String> = instruments.iter()
        .map(|(seg, sid)| format!("{{\"ExchangeSegment\":\"{}\",\"SecurityId\":\"{}\"}}", seg, sid))
        .collect();
    format!("{{\"RequestCode\":{},\"InstrumentCount\":{},\"InstrumentList\":[{}]}}",
        request_code, instruments.len(), list.join(","))
}

/// Parse a Type 4 Quote packet (50 bytes)
fn parse_quote_50(buf: &[u8]) -> Option<(u32, f32, f32, u32, u32, u32)> {
    if buf.len() < 50 { return None; }
    let sec_id = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let ltp = f32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);
    let atp = f32::from_le_bytes([buf[18], buf[19], buf[20], buf[21]]);
    let volume = u32::from_le_bytes([buf[22], buf[23], buf[24], buf[25]]);
    let sell_qty = u32::from_le_bytes([buf[26], buf[27], buf[28], buf[29]]);
    let buy_qty = u32::from_le_bytes([buf[30], buf[31], buf[32], buf[33]]);
    Some((sec_id, ltp, atp, volume, sell_qty, buy_qty))
}

/// Parse a Type 8 Full Quote packet (162 bytes) — includes OHLC + 5-level depth
fn parse_full_162(buf: &[u8]) -> Option<(u32, TickData)> {
    if buf.len() < 62 { return None; }
    let sec_id = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let ltp = f32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);
    let atp = f32::from_le_bytes([buf[18], buf[19], buf[20], buf[21]]);
    let volume = u32::from_le_bytes([buf[22], buf[23], buf[24], buf[25]]);
    let sell_qty = u32::from_le_bytes([buf[26], buf[27], buf[28], buf[29]]);
    let buy_qty = u32::from_le_bytes([buf[30], buf[31], buf[32], buf[33]]);
    let oi = u32::from_le_bytes([buf[34], buf[35], buf[36], buf[37]]);
    let day_open = f32::from_le_bytes([buf[46], buf[47], buf[48], buf[49]]);
    let day_close = f32::from_le_bytes([buf[50], buf[51], buf[52], buf[53]]);
    let day_high = f32::from_le_bytes([buf[54], buf[55], buf[56], buf[57]]);
    let day_low = f32::from_le_bytes([buf[58], buf[59], buf[60], buf[61]]);

    // Parse 5-level market depth (bytes 62-161, 20 bytes per level)
    // Per level: bid_qty(4) + bid_orders(2) + bid_price(4) + ask_price(4) + ask_orders(2) + ask_qty(4)
    let mut depth_bid = [DepthLevel::default(), DepthLevel::default(), DepthLevel::default(), DepthLevel::default(), DepthLevel::default()];
    let mut depth_ask = [DepthLevel::default(), DepthLevel::default(), DepthLevel::default(), DepthLevel::default(), DepthLevel::default()];

    if buf.len() >= 162 {
        for i in 0..5 {
            let off = 62 + i * 20;
            depth_bid[i] = DepthLevel {
                qty: u32::from_le_bytes([buf[off], buf[off+1], buf[off+2], buf[off+3]]),
                orders: u16::from_le_bytes([buf[off+4], buf[off+5]]),
                price: f32::from_le_bytes([buf[off+6], buf[off+7], buf[off+8], buf[off+9]]),
            };
            depth_ask[i] = DepthLevel {
                price: f32::from_le_bytes([buf[off+10], buf[off+11], buf[off+12], buf[off+13]]),
                orders: u16::from_le_bytes([buf[off+14], buf[off+15]]),
                qty: u32::from_le_bytes([buf[off+16], buf[off+17], buf[off+18], buf[off+19]]),
            };
        }
    }

    Some((sec_id, TickData {
        security_id: sec_id.to_string(),
        symbol: String::new(),
        ltp, atp, volume, buy_qty, sell_qty, day_open, day_high, day_low, day_close,
        oi, prev_oi: 0,
        depth_bid, depth_ask,
        updated_at: Instant::now(),
    }))
}

/// Parse Type 5 OI packet (12 bytes)
fn parse_oi_12(buf: &[u8]) -> Option<(u32, u32)> {
    if buf.len() < 12 { return None; }
    let sec_id = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let oi = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);
    Some((sec_id, oi))
}

/// Load nearest-month futures security IDs for F&O stocks from Dhan scrip master.
/// Returns Vec<(equity_symbol, futures_secid)>
pub async fn load_futures_mapping(equity_symbols: &[(String, u32)]) -> Vec<(String, u32)> {
    let url = "https://images.dhan.co/api-data/api-scrip-master.csv";
    let resp = match reqwest::get(url).await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("[WS] Failed to download scrip master for FNO mapping: {}", e);
            return vec![];
        }
    };
    let text = match resp.text().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("[WS] Failed to read scrip master CSV: {}", e);
            return vec![];
        }
    };

    // Build set of equity symbols we care about
    let equity_set: std::collections::HashSet<String> = equity_symbols.iter()
        .map(|(sym, _)| sym.to_uppercase()).collect();

    // Parse CSV: find NSE FUTSTK rows with nearest expiry
    // Columns: SEM_EXM_EXCH_ID, SEM_SEGMENT, SEM_SMST_SECURITY_ID, SEM_INSTRUMENT_NAME,
    //          SEM_EXPIRY_CODE, SEM_TRADING_SYMBOL, SEM_LOT_UNITS, SEM_CUSTOM_SYMBOL,
    //          SEM_EXPIRY_DATE, SEM_STRIKE_PRICE, SEM_OPTION_TYPE, ...
    let mut futures: HashMap<String, (u32, String)> = HashMap::new(); // symbol → (secid, expiry_date)

    for line in text.lines().skip(1) {
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 9 { continue; }
        let exchange = cols[0];
        let instrument = cols[3];
        let sec_id_str = cols[2];
        let trading_symbol = cols[5];
        let expiry_date = cols[8];

        if exchange != "NSE" || instrument != "FUTSTK" { continue; }

        // Extract base symbol from trading_symbol like "RELIANCE-Mar2026-FUT"
        let base_symbol = trading_symbol.split('-').next().unwrap_or("").to_uppercase();
        if !equity_set.contains(&base_symbol) { continue; }

        let sec_id: u32 = match sec_id_str.parse() {
            Ok(id) => id,
            Err(_) => continue,
        };

        // Keep the nearest expiry (smallest date that's >= today)
        let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
        if expiry_date < today.as_str() { continue; } // expired

        match futures.get(&base_symbol) {
            Some((_, ref existing_expiry)) if expiry_date >= existing_expiry.as_str() => {
                // Keep existing (earlier expiry)
            }
            _ => {
                futures.insert(base_symbol.clone(), (sec_id, expiry_date.to_string()));
            }
        }
    }

    let result: Vec<(String, u32)> = futures.into_iter()
        .map(|(sym, (sid, _))| (sym, sid))
        .collect();

    tracing::info!("[WS] Loaded {} futures mappings for OI tracking", result.len());
    result
}

/// Run the WebSocket feed. Connects, subscribes, and processes ticks.
/// Automatically reconnects on failure. Runs until dropped.
pub async fn run_ws_feed(
    feed: SharedFeed,
    token: String,
    client_id: String,
    equity_instruments: Vec<(String, u32)>,   // (symbol, security_id) for NSE_EQ
    fno_instruments: Vec<(String, u32)>,       // (symbol, futures_security_id) for NSE_FNO OI
    tick_tx: tokio::sync::mpsc::Sender<(String, f32)>,  // (symbol, ltp) channel for signal engine
) {
    // Build secid→symbol maps
    {
        let mut f = feed.write().await;
        for (sym, sid) in &equity_instruments {
            f.secid_to_symbol.insert(*sid, sym.clone());
        }
        for (sym, sid) in &fno_instruments {
            f.fut_to_symbol.insert(*sid, sym.clone());
        }
    }

    let max_retries = 5u32;
    let mut retry_count = 0u32;

    let token = token.trim().to_string();
    let client_id = client_id.trim().to_string();

    loop {
        // Allow override for testing with mock server: WS_FEED_URL=ws://localhost:9999
        let base = std::env::var("WS_FEED_URL")
            .unwrap_or_else(|_| "wss://api-feed.dhan.co".to_string());
        let url = format!(
            "{}?version=2&token={}&clientId={}&authType=2",
            base, token, client_id
        );

        tracing::info!("[WS] ▶ Connecting (token_len={}, cid={}, attempt {})...", token.len(), client_id, retry_count + 1);

        let client = reqwest::Client::new();
        match client.get(&url).upgrade().send().await {
            Ok(response) => {
                match response.into_websocket().await {
                    Ok(mut ws) => {
                        retry_count = 0;
                        tracing::info!("[WS] ✅ Connected! Subscribing {} equity + {} FNO instruments",
                            equity_instruments.len(), fno_instruments.len());

                        { let mut f = feed.write().await; f.ws_connected = true; }

                        // Subscribe equity in batches of 100 (Full mode = 21)
                        // Small delay between batches to avoid overwhelming Dhan's subscription handler
                        let eq_batches = (equity_instruments.len() + 99) / 100;
                        tracing::info!("[WS] Subscribing equity: {} instruments in {} batches", equity_instruments.len(), eq_batches);
                        for (batch_idx, chunk) in equity_instruments.chunks(100).enumerate() {
                            if batch_idx > 0 {
                                tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                            }
                            let instruments: Vec<(String, String)> = chunk.iter()
                                .map(|(_, sid)| ("NSE_EQ".to_string(), sid.to_string())).collect();
                            let msg = build_subscribe_json(21, &instruments);
                            if let Err(e) = ws.send(Message::Text(msg)).await {
                                tracing::error!("[WS] Equity subscribe batch {}/{} failed: {}", batch_idx + 1, eq_batches, e);
                                break;
                            }
                            tracing::debug!("[WS] Equity batch {}/{} subscribed ({} instruments)", batch_idx + 1, eq_batches, chunk.len());
                        }

                        // Subscribe F&O OI in batches of 100 (OI mode = 19)
                        let fno_batches = (fno_instruments.len() + 99) / 100;
                        tracing::info!("[WS] Subscribing F&O OI: {} instruments in {} batches", fno_instruments.len(), fno_batches);
                        for (batch_idx, chunk) in fno_instruments.chunks(100).enumerate() {
                            let instruments: Vec<(String, String)> = chunk.iter()
                                .map(|(_, sid)| ("NSE_FNO".to_string(), sid.to_string())).collect();
                            let msg = build_subscribe_json(19, &instruments);
                            if let Err(e) = ws.send(Message::Text(msg)).await {
                                tracing::error!("[WS] FNO subscribe batch {}/{} failed: {}", batch_idx + 1, fno_batches, e);
                                break;
                            }
                        }

                        tracing::info!("[WS] ✅ All subscription batches sent ({} equity + {} FNO). Listening for ticks...",
                            equity_instruments.len(), fno_instruments.len());

                        // Track unique symbols receiving ticks — detect silent drops
                        let mut unique_tickers: std::collections::HashSet<u32> = std::collections::HashSet::new();
                        let subscribe_time = tokio::time::Instant::now();
                        let mut next_health_log = tokio::time::Instant::now() + std::time::Duration::from_secs(30);
                        let mut total_ticks: u64 = 0;

                        // Process incoming messages
                        while let Some(msg_result) = ws.next().await {
                            total_ticks += 1;

                            // Periodic health log: first at 30s, then every 60s
                            if tokio::time::Instant::now() >= next_health_log {
                                let elapsed = subscribe_time.elapsed().as_secs();
                                let expected = equity_instruments.len();
                                let pct = if expected > 0 { unique_tickers.len() * 100 / expected } else { 0 };
                                let tps = if elapsed > 0 { total_ticks / elapsed } else { total_ticks };
                                if unique_tickers.len() < expected / 2 {
                                    tracing::warn!("[WS] ⚠️ HEALTH {}s: {}/{} instruments active ({}%) | {} ticks/s — POSSIBLE SUBSCRIPTION LIMIT",
                                        elapsed, unique_tickers.len(), expected, pct, tps);
                                } else {
                                    tracing::info!("[WS] HEALTH {}s: {}/{} instruments active ({}%) | {} ticks/s",
                                        elapsed, unique_tickers.len(), expected, pct, tps);
                                }
                                next_health_log = tokio::time::Instant::now() + std::time::Duration::from_secs(60);
                            }
                            match msg_result {
                                Ok(Message::Binary(data)) => {
                                    if data.len() < 8 {
                                        tracing::debug!("[WS] Malformed packet: len={} (expected >=8)", data.len());
                                        continue;
                                    }
                                    let msg_type = data[0];
                                    match msg_type {
                                        8 => {
                                            if let Some((sec_id, mut tick)) = parse_full_162(&data) {
                                                unique_tickers.insert(sec_id);
                                                let mut f = feed.write().await;
                                                if let Some(sym) = f.secid_to_symbol.get(&sec_id).cloned() {
                                                    tick.symbol = sym.clone();
                                                    tick.security_id = sec_id.to_string();
                                                    let ltp = tick.ltp;
                                                    f.ticks.insert(sym.clone(), tick);
                                                    f.last_tick_time = Some(Instant::now());
                                                    drop(f);
                                                    if let Err(e) = tick_tx.try_send((sym.clone(), ltp)) {
                                                        tracing::debug!("[WS] Tick channel full, dropped {}: {}", sym, e);
                                                    }
                                                }
                                            } else {
                                                tracing::debug!("[WS] Failed to parse type-8 packet (len={})", data.len());
                                            }
                                        }
                                        4 => {
                                            if let Some((sec_id, ltp, atp, volume, sell_qty, buy_qty)) = parse_quote_50(&data) {
                                                unique_tickers.insert(sec_id);
                                                let mut f = feed.write().await;
                                                if let Some(sym) = f.secid_to_symbol.get(&sec_id).cloned() {
                                                    if let Some(tick) = f.ticks.get_mut(&sym) {
                                                        tick.ltp = ltp;
                                                        tick.atp = atp;
                                                        tick.volume = volume;
                                                        tick.sell_qty = sell_qty;
                                                        tick.buy_qty = buy_qty;
                                                        tick.updated_at = Instant::now();
                                                    }
                                                    f.last_tick_time = Some(Instant::now());
                                                    drop(f);
                                                    if let Err(e) = tick_tx.try_send((sym.clone(), ltp)) {
                                                        tracing::debug!("[WS] Tick channel full, dropped {}: {}", sym, e);
                                                    }
                                                }
                                            } else {
                                                tracing::debug!("[WS] Failed to parse type-4 packet (len={})", data.len());
                                            }
                                        }
                                        5 => {
                                            if let Some((sec_id, oi)) = parse_oi_12(&data) {
                                                let mut f = feed.write().await;
                                                let prev = f.oi_map.get(&sec_id).copied().unwrap_or(0);
                                                f.oi_map.insert(sec_id, oi);
                                                if let Some(sym) = f.fut_to_symbol.get(&sec_id).cloned() {
                                                    if let Some(tick) = f.ticks.get_mut(&sym) {
                                                        tick.prev_oi = if tick.oi > 0 { tick.oi } else { prev };
                                                        tick.oi = oi;
                                                    }
                                                }
                                            }
                                        }
                                        other => {
                                            tracing::debug!("[WS] Unknown message type: {} (len={})", other, data.len());
                                        }
                                    }
                                }
                                Ok(Message::Close { .. }) => {
                                    let uptime = subscribe_time.elapsed().as_secs();
                                    tracing::warn!("[WS] ❌ Server closed connection after {}s uptime ({} unique instruments, {} total ticks)",
                                        uptime, unique_tickers.len(), total_ticks);
                                    break;
                                }
                                Ok(Message::Text(txt)) => {
                                    // Dhan may send text error messages
                                    tracing::warn!("[WS] ⚠️ Text message from server: {}", &txt[..txt.len().min(500)]);
                                }
                                Err(e) => {
                                    let uptime = subscribe_time.elapsed().as_secs();
                                    tracing::error!("[WS] ❌ Read error after {}s uptime: {}", uptime, e);
                                    break;
                                }
                                _ => {}
                            }
                        }

                        { let mut f = feed.write().await; f.ws_connected = false; }
                        tracing::warn!("[WS] ❌ Disconnected (ran {}s, {} instruments, {} ticks). Will reconnect...",
                            subscribe_time.elapsed().as_secs(), unique_tickers.len(), total_ticks);
                    }
                    Err(e) => {
                        tracing::error!("[WS] ❌ WebSocket upgrade failed: {}", e);
                    }
                }
            }
            Err(e) => {
                tracing::error!("[WS] ❌ Connection failed: {}", e);
                retry_count += 1;
                if retry_count >= max_retries {
                    tracing::error!("[WS] ❌ Max retries ({}) exceeded — falling back to REST polling only", max_retries);
                    // Reset retry count after a longer wait
                    sleep(Duration::from_secs(60)).await;
                    retry_count = 0;
                    continue;
                }
            }
        }

        // Reconnect backoff
        let backoff = Duration::from_secs(2u64.pow(retry_count.min(4)));
        tracing::info!("[WS] Reconnecting in {:?}...", backoff);
        sleep(backoff).await;
    }
}
