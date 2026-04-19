# WebSocket Real-Time Trading Implementation Plan

**Goal:** Replace 60-second polling with real-time WebSocket tick-by-tick trading while keeping polling as fallback.

**Architecture:** WebSocket feed runs in its own tokio task, maintains an in-memory LTP map shared with the poller. Signal engine runs on each tick during entry window. Exit manager checks TP/SL on every tick. Poller continues for snapshot storage, daily_ref seeding, and fallback.

---

## Task 1: WebSocket Client Module (`engine/src/ws_feed.rs`)

**New file.** Handles:
- Connect to `wss://api-feed.dhan.co` before market open
- Subscribe 205 equity stocks (Full mode, RequestCode 21) + F&O futures (OI mode, RequestCode 19)
- Parse binary packets (Type 4=Quote 50B, Type 8=Full 162B, Type 5=OI 12B)
- Auto-reconnect on disconnect (3 retries, 2s backoff)
- Fallback flag: if WS is down, poller knows to run signals

**Key data structures:**
```rust
// Shared between WS feed and poller
pub struct LiveFeed {
    pub ltp_map: HashMap<String, TickData>,  // symbol → latest tick
    pub oi_map: HashMap<String, u32>,         // symbol → latest OI (from futures)
    pub ws_connected: bool,
    pub last_tick_time: DateTime<Tz>,
}

pub struct TickData {
    pub ltp: f32,
    pub atp: f32,
    pub volume: u32,
    pub buy_qty: u32,
    pub sell_qty: u32,
    pub day_open: f32,
    pub day_high: f32,
    pub day_low: f32,
    pub day_close: f32,  // prev close
    pub updated_at: Instant,
}
```

---

## Task 2: Signal-on-Tick Engine

**Modify `poller.rs`.** When WS is connected:
- On each tick during entry window → run `compute_signal()` for that stock
- Check `fired_today` BEFORE computation (skip if already fired)
- Use debounce: skip if last signal check for this stock was <500ms ago
- On signal fire → spawn order (same as now)

When WS is disconnected:
- Poller runs signals as current system (every 60s)

---

## Task 3: Exit-on-Tick

**Modify exit logic.** When WS is connected:
- On each tick for stocks in `open_signals` → check TP/SL immediately
- Time exit still checked by poller (needs bucket calculation)
- On exit → spawn exit order (same as now)

---

## Task 4: F&O OI Subscription (only for F&O stocks)

- On startup: load scrip master, build equity_secid → futures_secid map
- Only subscribe NSE_FNO for stocks that have F&O tier tag
- Store OI in `oi_map`, write to snapshots table on each 60s poll cycle

---

## Task 5: Snapshot Storage (unchanged)

- Poller still runs every 60s
- Reads from `ltp_map` (populated by WS) instead of fetching quotes from REST
- If WS is down, falls back to REST quote fetch
- Writes snapshots to ClickHouse (same as now)
- Seeds daily_ref (same as now)

---

## Task 6: Testing

- Verify WS connects and receives ticks
- Verify signal fires on tick (not just on poll)
- Verify no duplicate signals (fired_today check)
- Verify exit fires on tick when TP/SL hit
- Verify fallback to polling when WS disconnects
- Verify F&O OI data flows for F&O stocks only
- Verify reconnection after disconnect
