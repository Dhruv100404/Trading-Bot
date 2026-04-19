# WebSocket vs Polling Analysis — Dhan Market Feed

## Test Date: 2026-03-25 (during market hours)

---

## Polling (Current System)

| Metric | Value |
|--------|-------|
| Endpoint | `POST /v2/marketfeed/quote` |
| Batch size | 205 stocks in 1 call |
| Latency | ~100ms per call |
| Update frequency | 1 poll per 60 seconds |
| Data staleness | 0-60 seconds old |
| Rate limit | ~1 req/sec (429 on rapid calls) |
| Fields per stock | LTP, OHLC, volume, OI, bid/ask depth |

## WebSocket (Tested)

| Metric | Value |
|--------|-------|
| Endpoint | `wss://api-feed.dhan.co?version=2&token=...&clientId=...&authType=2` |
| Connection time | ~94-157ms |
| Tick rate | **8.8 ticks/sec for 10 stocks** |
| Estimated 200 stocks | **~170+ ticks/sec** |
| Data staleness | **<100ms (real-time)** |
| Subscribe format | JSON: `{ RequestCode: 17, InstrumentCount: N, InstrumentList: [...] }` |
| Response format | Binary (50 bytes per Quote tick) |
| Max instruments | 5000 per connection, 5 connections per user |
| Max per subscribe msg | 100 instruments per JSON message |

## WebSocket Binary Packet Format (Code 4 = Quote, 50 bytes)

```
Offset  Type       Size  Field
------  ----       ----  -----
0       uint8      1     Response code (4=Quote, 2=Ticker, 6=OI)
1-2     uint16 BE  2     Message length
3       uint8      1     Exchange segment (1=NSE_EQ, 13=NSE_FNO)
4-7     uint32 LE  4     Security ID
8-11    float32 LE 4     LTP (Last Traded Price) ← VERIFIED matches REST API
12-13   uint16 LE  2     Last Traded Quantity
14-17   uint32 LE  4     Last Trade Time (Unix epoch seconds)
18-21   float32 LE 4     ATP (Average Traded Price / VWAP)
22-25   uint32 LE  4     Volume (cumulative)
26-29   uint32 LE  4     Total Sell Quantity
30-33   uint32 LE  4     Total Buy Quantity
34-37   float32 LE 4     Day Open
38-41   float32 LE 4     Day Close (previous day close / current day close after market)
42-45   float32 LE 4     Day High
46-49   float32 LE 4     Day Low
```

### Verified Fields (RELIANCE, secId=1333):
- LTP: 791.40 (matched REST API: 791.75 within tick)
- ATP: 783.91
- Volume: 31,202,856
- Sell Qty: 1,054,768 | Buy Qty: 1,680,629
- Open: 768.20 | High: 792.80 | Low: 768.20 | Close: 764.90

### Subscribe JSON Request Codes:
- `15` = Ticker (LTP only, 16-byte response)
- `17` = Quote (LTP + OHLCV + ATP, 50-byte response)
- `21` = Full (Quote + Market Depth, 112-byte response)

---

## Head-to-Head Comparison

| Dimension | Polling | WebSocket |
|-----------|---------|-----------|
| **Signal detection latency** | Up to 60 seconds late | **Real-time (<100ms)** |
| **TP/SL exit accuracy** | Checked every 60s — price may overshoot | **Tick-by-tick — exits at exact price** |
| **Entry timing** | Enters at poll price (may have moved) | **Enters at trigger price** |
| **Implementation complexity** | Simple HTTP requests | Binary protocol, reconnection logic needed |
| **Data richness per tick** | Full OHLCV + depth | Full OHLCV + bid/ask qty |
| **Bandwidth** | ~50KB per poll (205 stocks) | ~8.5KB/sec continuous |
| **Connection reliability** | No state — each poll is independent | Needs heartbeat + reconnect |

---

## Recommendation: HYBRID Approach

### Keep Polling For:
- daily_ref seeding (bucket 0-3, gap_pct calculation)
- Snapshot storage to ClickHouse (historical data for backtesting)
- Fallback when WebSocket disconnects

### Add WebSocket For:
- **Signal detection during entry window** (bucket 2-4): compute_signal on every LTP tick
- **TP/SL exit monitoring**: check exit conditions on every tick, not every 60s
- **SL protection**: a stock hitting SL at 9:20:15 gets exited at 9:20:15, not 9:21:00

### Expected Impact:
- Signal fires within **100ms** of price crossing threshold (vs 0-60s delay)
- Exit fires within **100ms** of TP/SL hit (vs 0-60s delay)
- SL overshoot reduced from potential 0.5-1% (60s of adverse movement) to near-zero
- Better fill prices due to faster execution

### Architecture:
```
WebSocket Feed (real-time)
  ↓ tick arrives
  ├→ Update in-memory LTP map
  ├→ If in entry window: run compute_signal()
  ├→ If has open positions: check_exit()
  └→ Every 60s: batch-write snapshots to ClickHouse (same as now)

Polling (every 60s, runs in parallel)
  ↓ poll cycle
  ├→ Fetch full quotes (backup data source)
  ├→ Store snapshots to ClickHouse
  ├→ Seed daily_ref
  └→ Fallback signal check if WS disconnected
```

---

## OI (Open Interest) — Real-Time via WebSocket

### Key Finding: OI is ONLY available on NSE_FNO, not NSE_EQ

| Segment | Example | OI Available? |
|---------|---------|--------------|
| NSE_EQ (equity) | RELIANCE secId=1333 | **No** (always 0) |
| NSE_FNO (futures) | RELIANCE Mar FUT secId=52023 | **Yes — 66,680,500** |

### Verified Live Data (March 25, 2026)

| Instrument | SecId | Segment | LTP | OI | High OI |
|-----------|-------|---------|-----|-----|---------|
| RELIANCE Equity | 1333 | NSE_EQ | 792.45 | 0 | 0 |
| RELIANCE Mar FUT | 52023 | NSE_FNO | 1424.60 | 66,680,500 | 67,678,500 |
| RELIANCE Apr FUT | 67003 | NSE_FNO | 1433.10 | 40,072,500 | 40,072,500 |

### OI WebSocket Packets

**Type 5 (OI update, 12 bytes)** — streams every time OI changes:
```
Offset  Type       Field
0       uint8      Response code (5)
1-2     uint16     Message length
3       uint8      Exchange segment
4-7     uint32 LE  Security ID
8-11    uint32 LE  Open Interest
```

**Type 8 (Full Quote, 162 bytes)** — also includes OI:
```
Offset 34-37: OI (uint32 LE)
Offset 38-41: Highest OI of the day (uint32 LE)
```

### How to Get OI for All 205 F&O Stocks

1. **Download scrip master**: `https://images.dhan.co/api-data/api-scrip-master.csv`
2. **Filter**: `SEM_SEGMENT=D AND SEM_INSTRUMENT_NAME=FUTSTK AND exchange=NSE` (nearest month expiry)
3. **Subscribe**: Both NSE_EQ (for LTP/depth) AND NSE_FNO (for OI) per stock
4. **Map**: Match equity symbol → futures secId using `SM_SYMBOL_NAME` column

Example mapping:
```
RELIANCE equity (NSE_EQ, 1333) → RELIANCE Mar FUT (NSE_FNO, 52023)
TCS equity (NSE_EQ, 2885)      → TCS Mar FUT (NSE_FNO, xxxxx)
```

---

## Bid/Ask Depth — 5-Level Real-Time

### Available via WebSocket Full Mode (RequestCode 21, Type 8 response)

Verified RELIANCE depth (live data):
```
Level 1: Bid  912 @ 792.30 (5 orders)  | Ask  143 @ 792.35 (4 orders)
Level 2: Bid 1501 @ 792.25 (6 orders)  | Ask 1299 @ 792.40 (6 orders)
Level 3: Bid 1416 @ 792.20 (6 orders)  | Ask  398 @ 792.45 (5 orders)
Level 4: Bid 1113 @ 792.15 (6 orders)  | Ask  849 @ 792.50 (7 orders)
Level 5: Bid  243 @ 792.10 (3 orders)  | Ask 2184 @ 792.55 (14 orders)
```

### Depth Packet Structure (within Type 8, offset 62-161)

5 levels × 20 bytes each = 100 bytes:
```
Per level (20 bytes):
  bid_qty (4) + bid_orders (2) + bid_price (4 float32 LE) +
  ask_price (4 float32 LE) + ask_orders (2) + ask_qty (4)
```

---

## How OI + Depth Can Improve Predictions

### OI-Based Signals (from NSE_FNO futures)

| OI Change | Price Direction | Interpretation | Signal |
|-----------|----------------|----------------|--------|
| **Rising OI + Rising Price** | UP | Long buildup — institutions adding longs | Strong BUY confirmation |
| **Rising OI + Falling Price** | DOWN | Short buildup — institutions adding shorts | Strong SELL confirmation |
| **Falling OI + Rising Price** | UP | Short covering — weak rally | Reduce BUY confidence |
| **Falling OI + Falling Price** | DOWN | Long unwinding — weak selloff | Reduce SELL confidence |

**Implementation**: Track OI delta per minute via Type 5 packets. If OI is building in the same direction as the price move, increase qty_multiplier. If OI diverges from price, skip the signal.

### Depth-Based Signals (from NSE_EQ 5-level depth)

**1. Order Book Imbalance (OBI)**:
```
OBI = (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)
```
- OBI > +0.3 = strong buying pressure → bullish
- OBI < -0.3 = strong selling pressure → bearish
- Range: -1 to +1

**From RELIANCE example**:
```
Total Bid = 912 + 1501 + 1416 + 1113 + 243 = 5,185
Total Ask = 143 + 1299 + 398 + 849 + 2184 = 4,873
OBI = (5185 - 4873) / (5185 + 4873) = +0.03 (neutral)
```

**2. Spread Analysis**:
```
Spread = best_ask - best_bid
Spread% = spread / LTP * 100
```
- Tight spread (<0.05%) = high confidence, liquid stock
- Wide spread (>0.2%) = low liquidity, risky entry

**3. Large Order Detection**:
- If L1 bid_qty > 5x average level qty → strong support wall
- If L1 ask_qty > 5x average → strong resistance wall
- Walls predict short-term price floors/ceilings

**4. Depth Acceleration**:
- If bid quantities are growing each tick → buying pressure increasing
- If ask quantities are growing → selling pressure increasing
- Track over 30-second windows for momentum confirmation

### Proposed Enhanced Confidence Score (0-10)

| Factor | Condition | Points | Source |
|--------|-----------|--------|--------|
| Price move | \|move\| ≥ min_move | +2 | Current (equity LTP) |
| VWAP cross | LTP vs ATP | +1 | Current (equity ATP) |
| Volume | vol > threshold | +1 | Current (equity volume) |
| **OI buildup** | OI rising + aligned with price direction | **+2** | **NEW (futures OI)** |
| **OI divergence** | OI falling against price direction | **-1** | **NEW (futures OI)** |
| **Depth imbalance** | \|OBI\| > 0.3 aligned with direction | **+1** | **NEW (equity depth)** |
| **Spread** | Spread < 0.05% | **+1** | **NEW (equity depth)** |
| Candle body | body_ratio > 0.6 | +1 | Current |
| Gap continuation | gap aligned with move | +1 | Current |

### Data Requirements

| Per Stock | WebSocket Subscriptions | Packets/sec |
|-----------|------------------------|-------------|
| Equity quote + depth | NSE_EQ, RequestCode 21 | ~5-10 ticks |
| Futures OI | NSE_FNO, RequestCode 19 | ~1-3 ticks |
| **Total per stock** | **2 subscriptions** | **~8-13 ticks** |
| **205 stocks** | **410 subscriptions** | **~1600-2600 ticks/sec** |

Dhan allows 5000 instruments per connection × 5 connections = 25,000 total. We need 410 — well within limits.
