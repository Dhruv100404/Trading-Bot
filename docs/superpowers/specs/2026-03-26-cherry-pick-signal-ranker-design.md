# Cherry-Pick Signal Ranker — Design Spec

## Goal

Add a signal ranking and selection layer to the trading engine that cherry-picks the top N signals per day by score, combined with TP-based exits, to maximize win rate and daily ROC.

**Evidence basis**: Backtest on 87,147 stock-days showed that selecting top 10 signals by score + TP=0.7% achieves 100% win rate over 36 days (Rs 1,741/day on Rs 50K capital). Current system takes all signals with no TP → 57.4% win rate, Rs 126/day.

## Current System

- `poller.rs` polls quotes every ~60s during market hours
- For each stock, `compute_signal()` checks filters (move%, volume, score, gaps)
- If signal passes all filters → immediately fire order via `order_executor.rs`
- One signal per stock per day (first qualifying signal wins)
- qty = `dynamicQty(baseQty=1, dirMult, confidence_score)` → typically 1-2 shares
- TP disabled (`buy_tp_pct=0, sell_tp_pct=0`), exits via SL or TIME only
- ~56 signals/day on 205 F&O stocks, Rs 12K capital/day, Rs 126/day PnL

## New System: Two-Phase Cherry-Pick Pipeline

### Phase 1 — Collect (during entry window)

```
For each poll during entry window buckets:

  For each stock in watchlist:
    result = compute_signal(snapshots, config, gap_pct, morning_range)
    if result is Some:
      candidate_pool.insert_or_update(symbol, candidate)
```

The `CandidatePool` is a `HashMap<String, Candidate>` keyed by symbol. If a stock fires at bucket 2 AND again at bucket 3, the later entry overwrites (more data = better signal). Pool resets daily.

Entry windows are per-direction from config:
- BUY: `buy_entry_start` to `buy_entry_end` (default: bucket 2-3)
- SELL: `sell_entry_start` to `sell_entry_end` (default: bucket 2-4)

**No orders fired during Phase 1.**

Exception: when `cherry_pick_enabled = false`, system behaves exactly as today — every signal fires immediately. Zero risk to existing behavior.

### Phase 1b — Select & Execute (at each entry bucket)

At each entry bucket (not waiting until window closes):

```
  available_slots = N - active_positions.len()
  if available_slots <= 0: skip

  candidates = pool.values()
    .filter(not already active)
    .sort_by(score DESC, entry_price ASC)  // cheap-first tiebreaker

  for candidate in candidates.take(available_slots):
    qty = floor(capital_per_trade / entry_price)
    tp_pct = compute_tp(base_tp, score, tp_score_scaling)
    tp_price = entry_price * (1 + direction_sign * tp_pct / 100)
    sl_price = entry_price * (1 - direction_sign * sl_pct / 100)
    → fire order
    → add to active_positions
```

This fires the best signals **immediately at bucket 2** (catching peak momentum), and fills remaining slots at bucket 3, 4 as new candidates appear or early TP exits free slots.

### Position Count (N)

```
buying_power = total_capital × 5   // 5x intraday margin
N = min(floor(buying_power / capital_per_trade), max_positions)
```

With defaults: `min(floor(50000 × 5 / 25000), 20) = min(10, 20) = 10`

### Position Sizing (qty)

```
qty = floor(capital_per_trade / entry_price)
if qty < 1: qty = 1   // expensive stocks still get 1 share via margin
if entry_price * qty < min_position_value: skip   // penny stock filter
```

With Rs 25K per trade: Rs 200 stock → 125 shares, Rs 2000 stock → 12 shares, Rs 8000 stock → 3 shares (using margin for the Rs 8K).

### Phase 2 — Exit Monitoring (bucket 5+, post-entry)

Normal exit logic as today via `check_exit()`:
- TP hit → exit
- SL hit → exit
- TIME hit → exit
- When a position exits and frees a slot: if still within entry window, next-best waitlisted candidate can fill the slot

## TP Strategy

### Fixed Mode (`tp_score_scaling = false`, default)

Uses `buy_tp_pct` / `sell_tp_pct` from config directly. Set to 0.7% for all trades.

### Score-Based Mode (`tp_score_scaling = true`)

TP scales with signal score. Base TP from config acts as floor:

| Score | Multiplier | Example (base=0.5%) |
|---|---|---|
| 4-5 | 1.0× | 0.5% |
| 6-7 | 1.4× | 0.7% |
| 8-9 | 2.0× | 1.0% |
| 10+ | 3.0× | 1.5% |

Multipliers derived from MFE analysis: score 8+ signals have 2-3× more favorable excursion than score 4-5. Multipliers are hardcoded constants, not configurable.

TP price is computed AFTER selection (in cherry_pick.rs), not during signal generation. `signal_engine.rs` remains untouched.

## New Config Fields

Added to `trading.config` table:

| Field | Type | Default | Description |
|---|---|---|---|
| `cherry_pick_enabled` | UInt8 (bool) | 0 | Master switch. 0 = today's behavior exactly |
| `total_capital` | UInt32 | 50000 | Actual trading capital in Rs |
| `max_positions` | UInt16 | 20 | Hard ceiling on concurrent positions |
| `min_position_value` | UInt32 | 5000 | Skip if qty × price < this |
| `tp_score_scaling` | UInt8 (bool) | 0 | 0 = fixed TP, 1 = score-based TP |

Existing fields used as-is: `capital_per_trade`, `buy_tp_pct`, `sell_tp_pct`, `buy_sl_pct`, `sell_sl_pct`, `buy_entry_start/end`, `sell_entry_start/end`, `hard_exit_bucket`, `sell_hard_exit_bucket`.

## Rollout Strategy

| Step | Config Change | Risk |
|---|---|---|
| 1 | Set `buy_tp_pct=0.7, sell_tp_pct=0.7` | Zero. Just config. Immediate improvement. |
| 2 | Validate TP results in UI backtest for 1 week | None |
| 3 | Deploy cherry-pick code. Keep `cherry_pick_enabled=false` | None. Code is dormant. |
| 4 | Set `cherry_pick_enabled=true, total_capital=50000` | Low. Falls back to old behavior if disabled. |
| 5 | Enable `tp_score_scaling=true` after validating step 4 | Low. |
| 6 | Expand watchlist to 2000+ stocks (tier_state change) | Medium. More candidates for cherry-pick pool. |

## Files Changed

### New File: `engine/src/cherry_pick.rs`

```rust
pub struct Candidate {
    pub symbol: String,
    pub security_id: String,
    pub signal: Signal,
    pub entry_bucket: u16,
}

pub struct CandidatePool {
    candidates: HashMap<String, Candidate>,
}

impl CandidatePool {
    pub fn new() -> Self
    pub fn reset(&mut self)                          // daily reset
    pub fn insert(&mut self, candidate: Candidate)   // insert or update
    pub fn select_top_n(&self, n: usize, already_active: &HashSet<String>) -> Vec<Candidate>
    // returns sorted by (score DESC, entry_price ASC), excluding active symbols
}

pub fn compute_positions_count(config: &SignalConfig) -> usize {
    // min(floor(total_capital * 5 / capital_per_trade), max_positions)
}

pub fn compute_tp_price(signal: &Signal, config: &SignalConfig) -> f32 {
    // if tp_score_scaling: apply multiplier based on score
    // else: use buy_tp_pct / sell_tp_pct directly
}

pub fn compute_qty(entry_price: f32, config: &SignalConfig) -> u32 {
    // floor(capital_per_trade / entry_price), min 1
    // skip if entry_price * qty < min_position_value
}
```

### Modified: `engine/src/types.rs`

Add to `SignalConfig`:
```rust
pub cherry_pick_enabled: bool,     // default: false
pub total_capital: u32,            // default: 50000
pub max_positions: u16,            // default: 20
pub min_position_value: u32,       // default: 5000
pub tp_score_scaling: bool,        // default: false
```

### Modified: `engine/src/poller.rs`

In the main poll loop, add branching:

```rust
if config.cherry_pick_enabled {
    // Phase 1: collect into pool
    // Phase 1b: select + execute at each entry bucket
    // Phase 2: exit monitoring (existing logic)
} else {
    // Existing logic unchanged
}
```

### Modified: `engine/src/db/watchlist.rs`

Read new fields from `trading.config` query.

### Modified: `ui/src/views/Backtest.tsx`

Change backtest loop from per-symbol to per-day:

```typescript
// Current:
for (symbol of symbols) {
  signal = replaySymbolDay(snaps, cfg, gap, date, symbol)
  if (signal) results.push(signal)
}

// New (when cherry_pick_enabled):
for (date of tradingDays) {
  candidates = []
  for (symbol of symbols) {
    signal = replaySymbolDay(snaps, cfg, gap, date, symbol)  // stops at entry, no exit sim
    if (signal) candidates.push(signal)
  }
  selected = candidates.sort(score DESC, price ASC).slice(0, N)
  for (signal of selected) {
    simulateExit(signal, snaps, cfg)  // apply TP/SL/TIME
    results.push(signal)
  }
  rejected_count += candidates.length - selected.length
}
```

New UI display: `"48 candidates → 10 selected → 10/10 won"` badge per day.

### Migration: `init/01_schema.sql`

```sql
ALTER TABLE trading.config
  ADD COLUMN IF NOT EXISTS cherry_pick_enabled UInt8 DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_capital UInt32 DEFAULT 50000,
  ADD COLUMN IF NOT EXISTS max_positions UInt16 DEFAULT 20,
  ADD COLUMN IF NOT EXISTS min_position_value UInt32 DEFAULT 5000,
  ADD COLUMN IF NOT EXISTS tp_score_scaling UInt8 DEFAULT 0;
```

## Deployment Note

Current ClickHouse snapshots contain only 205 F&O stocks. Cherry-pick works with any pool size. To expand to 2000+ stocks later, enable additional tiers in `trading.tier_state` table — the scrip_master sync will populate the watchlist, poller will start fetching quotes for them, and they'll flow into the candidate pool automatically.

## WebSocket Compatibility

The cherry-pick layer is **data-source agnostic**. Both polling and WebSocket feed into the same pipeline:

```
Poll tick (every 60s)  ──→  compute_signal()  ──→  CandidatePool
                                                        ↓
WS tick (real-time)    ──→  compute_signal()  ──→  CandidatePool
                                                        ↓
                                                  select_top_n()
                                                        ↓
                                                  execute orders
```

**With polling** (current): CandidatePool updates once per minute. Selection happens at each bucket boundary.

**With WebSocket** (current hybrid): CandidatePool updates on every tick (~170 ticks/sec for 200 stocks). Signals fire mid-bucket as prices cross thresholds. This is BETTER for cherry-pick because:
- More candidates enter the pool faster (sub-second vs 60s)
- TP exits fire at exact price (not next poll cycle) — reduces overshoot
- Score can be computed on every tick during entry window

The only change needed for WebSocket: the "select + execute" step should run when:
1. A new candidate enters the pool AND there are open slots, OR
2. A position exits (TP/SL) and frees a slot during entry window

This is already how `poller.rs` processes WS ticks today (line-by-line in the tick handler). The CandidatePool just adds a ranking step before order execution.

**Extra WS data (OI, depth)**: WebSocket provides OI (from NSE_FNO subscription) and 5-level depth that polling doesn't. These feed into the existing scoring in `compute_signal()` — they increase score accuracy which makes cherry-pick selection better. No cherry-pick code changes needed for this.

## What This Does NOT Change

- `signal_engine.rs` — signal generation logic untouched
- `exit_manager.rs` — exit checking logic untouched
- `order_executor.rs` — receives orders as before
- `dynamic_qty.rs` — replaced by `cherry_pick::compute_qty()` when enabled, original stays for backward compat
- API endpoints — config GET/PUT already handles dynamic fields via serde
- WebSocket feed — unaffected
