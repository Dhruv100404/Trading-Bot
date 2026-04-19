# Split BUY/SELL Config Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split shared config parameters into independent BUY and SELL versions so each direction can be optimized separately, with a two-column UI layout.

**Architecture:** Add `buy_` and `sell_` prefixed versions of 5 key parameters (tp_pct, sl_pct, min_move_pct, min_vol_rate, capital_per_trade) to SignalConfig, ClickHouse, and the API. The signal engine reads the direction-appropriate value at signal creation time. The UI shows two side-by-side columns (BUY Settings / SELL Settings) for the direction-specific params, with shared params above. Backtest `replaySymbolDay` and `computeSignal` use the correct per-direction values.

**Tech Stack:** Rust (engine), TypeScript/React (UI), ClickHouse SQL

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `init/schema.sql` | Modify | Add 5 new columns |
| `engine/src/types.rs` | Modify | Add fields to SignalConfig + defaults |
| `engine/src/db/watchlist.rs` | Modify | Row struct, SELECT, seed, mapping |
| `engine/src/api/config.rs` | Modify | Patch handler + INSERT |
| `engine/src/signal_engine.rs` | Modify | Use per-direction tp/sl/move/vol_rate |
| `engine/src/dynamic_qty.rs` | Modify | Accept per-direction capital_per_trade |
| `ui/src/views/Backtest.tsx` | Modify | Split config UI, per-direction in computeSignal + replaySymbolDay |

## New Config Fields

| New Field | Replaces (for direction) | Default |
|-----------|-------------------------|---------|
| `buy_tp_pct` | `tp_pct` for BUY | 0 |
| `buy_sl_pct` | `sl_pct` for BUY | 0 |
| `sell_tp_pct` | `tp_pct` for SELL | 0 |
| `sell_sl_pct` | `sl_pct` for SELL | 0 |
| `buy_min_move_pct` | `min_move_pct` for BUY | 0.15 |
| `sell_min_move_pct` | `min_move_pct` for SELL | 0.15 |
| `buy_min_vol_rate` | `min_vol_rate` for BUY | 150 |
| `sell_min_vol_rate` | `min_vol_rate` for SELL | 150 |
| `buy_capital_per_trade` | `capital_per_trade` for BUY | 10000 |
| `sell_capital_per_trade` | `capital_per_trade` for SELL | 10000 |

Old shared fields (`tp_pct`, `sl_pct`, `min_move_pct`, `min_vol_rate`, `capital_per_trade`) remain in DB for backward compat but are **ignored** by engine/UI — the per-direction versions take precedence.

---

### Task 1: ClickHouse Schema

**Files:** `init/schema.sql`

- [ ] **Step 1: Add columns to schema.sql** after `capital_per_trade` line:
```sql
    buy_tp_pct           Float32  DEFAULT 0,
    buy_sl_pct           Float32  DEFAULT 0,
    sell_tp_pct          Float32  DEFAULT 0,
    sell_sl_pct          Float32  DEFAULT 0,
    buy_min_move_pct     Float32  DEFAULT 0.15,
    sell_min_move_pct    Float32  DEFAULT 0.15,
    buy_min_vol_rate     Float32  DEFAULT 150,
    sell_min_vol_rate    Float32  DEFAULT 150,
    buy_capital_per_trade UInt32  DEFAULT 10000,
    sell_capital_per_trade UInt32 DEFAULT 10000,
```

- [ ] **Step 2: Run ALTER TABLE on live ClickHouse** (one per column)

- [ ] **Step 3: Commit**

---

### Task 2: Rust SignalConfig + DB Layer

**Files:** `engine/src/types.rs`, `engine/src/db/watchlist.rs`

- [ ] **Step 1: Add 10 new fields to SignalConfig** in `types.rs` after `capital_per_trade`:
```rust
    pub buy_tp_pct: f32,
    pub buy_sl_pct: f32,
    pub sell_tp_pct: f32,
    pub sell_sl_pct: f32,
    pub buy_min_move_pct: f32,
    pub sell_min_move_pct: f32,
    pub buy_min_vol_rate: f32,
    pub sell_min_vol_rate: f32,
    pub buy_capital_per_trade: u32,
    pub sell_capital_per_trade: u32,
```

- [ ] **Step 2: Set defaults** in `Default` impl:
```rust
    buy_tp_pct: 0.0,
    buy_sl_pct: 0.0,
    sell_tp_pct: 0.0,
    sell_sl_pct: 0.0,
    buy_min_move_pct: 0.15,
    sell_min_move_pct: 0.15,
    buy_min_vol_rate: 150.0,
    sell_min_vol_rate: 150.0,
    buy_capital_per_trade: 10000,
    sell_capital_per_trade: 10000,
```

- [ ] **Step 3: Update `db/watchlist.rs`** — add all 10 fields to: Row struct, SELECT query, fallback defaults, Ok(SignalConfig{...}) mapping, seed INSERT

- [ ] **Step 4: Verify compile:** `docker compose build engine`

- [ ] **Step 5: Commit**

---

### Task 3: API Config Endpoint

**Files:** `engine/src/api/config.rs`

- [ ] **Step 1: Add 10 patch handlers** (f64→f32 for pct fields, u64→u32 for capital fields):
```rust
    if let Some(v) = patch.get("buy_tp_pct").and_then(|v| v.as_f64()) { cfg.buy_tp_pct = v as f32; }
    // ... etc for all 10
```

- [ ] **Step 2: Add 10 columns + binds to INSERT statement**

- [ ] **Step 3: Verify compile:** `docker compose build engine`

- [ ] **Step 4: Commit**

---

### Task 4: Signal Engine — Per-Direction Logic

**Files:** `engine/src/signal_engine.rs`, `engine/src/dynamic_qty.rs`

- [ ] **Step 1: Update `compute_signal`** to use per-direction params:

Replace the single `min_move_pct` check:
```rust
    let dir_min_move = if move_pct > 0.0 { config.buy_min_move_pct } else { config.sell_min_move_pct };
    if move_pct.abs() < dir_min_move { return None; }
```

Replace the single `min_vol_rate` check:
```rust
    let dir_min_vol_rate = match direction {
        Direction::Buy => config.buy_min_vol_rate,
        Direction::Sell => config.sell_min_vol_rate,
    };
    if last.volume_rate < dir_min_vol_rate { return None; }
```

Replace `tp_price`/`sl_price` calculation:
```rust
    let (dir_tp, dir_sl) = match direction {
        Direction::Buy => (config.buy_tp_pct, config.buy_sl_pct),
        Direction::Sell => (config.sell_tp_pct, config.sell_sl_pct),
    };
    let tp_price = entry_price * (1.0 + dir_sign * dir_tp / 100.0);
    let sl_price = entry_price * (1.0 - dir_sign * dir_sl / 100.0);
```

- [ ] **Step 2: Update `dynamic_qty.rs`** `compute_quantity` to accept `capital_per_trade: u32` directly instead of reading from config, so the caller passes the right one:

```rust
pub fn compute_quantity(
    capital_per_trade: u32,
    fallback_qty: u32,
    entry_price: f32,
    volume_rate: f32,
    morning_range_pct: f32,
) -> u32 {
    if capital_per_trade == 0 { return fallback_qty; }
    // ... rest same
}
```

- [ ] **Step 3: Update signal_engine.rs** call to `compute_quantity`:
```rust
    let dir_capital = match direction {
        Direction::Buy => config.buy_capital_per_trade,
        Direction::Sell => config.sell_capital_per_trade,
    };
    let qty = crate::dynamic_qty::compute_quantity(
        dir_capital, config.quantity, entry_price, last.volume_rate, morning_range_pct,
    );
```

- [ ] **Step 4: Fix tests** — update `dynamic_qty::tests` to use new signature

- [ ] **Step 5: Verify:** `docker compose build engine`

- [ ] **Step 6: Commit**

---

### Task 5: UI — Split Config Panel

**Files:** `ui/src/views/Backtest.tsx`

- [ ] **Step 1: Add 10 fields to BacktestConfig interface**

- [ ] **Step 2: Update configFromApi** to read new fields with fallbacks

- [ ] **Step 3: Replace CONFIG_FIELDS** with three arrays:
```typescript
const SHARED_FIELDS = [
  { key: 'entry_bucket_start', label: 'Entry Start', step: 1 },
  { key: 'entry_bucket_end', label: 'Entry End', step: 1 },
  { key: 'min_volume', label: 'Min Volume', step: 100 },
  { key: 'min_score', label: 'Min Score', step: 1 },
  { key: 'quantity', label: 'Fixed Qty', step: 1 },
  { key: 'gap_min_pct', label: 'Gap Min %', step: 0.5 },
  { key: 'gap_max_pct', label: 'Gap Max %', step: 0.5 },
]
const BUY_FIELDS = [
  { key: 'hard_exit_bucket', label: 'Exit Bucket', step: 1 },
  { key: 'buy_min_move_pct', label: 'Min Move %', step: 0.05 },
  { key: 'buy_tp_pct', label: 'TP %', step: 0.1 },
  { key: 'buy_sl_pct', label: 'SL %', step: 0.1 },
  { key: 'buy_gap_max_pct', label: 'Gap Max %', step: 0.5 },
  { key: 'buy_min_vol_rate', label: 'Min Vol Rate', step: 10 },
  { key: 'buy_capital_per_trade', label: 'Capital/Trade ₹', step: 1000 },
]
const SELL_FIELDS = [
  { key: 'sell_hard_exit_bucket', label: 'Exit Bucket', step: 1 },
  { key: 'sell_min_move_pct', label: 'Min Move %', step: 0.05 },
  { key: 'sell_tp_pct', label: 'TP %', step: 0.1 },
  { key: 'sell_sl_pct', label: 'SL %', step: 0.1 },
  { key: 'sell_gap_min_pct', label: 'Gap Min %', step: 0.5 },
  { key: 'sell_min_vol_rate', label: 'Min Vol Rate', step: 10 },
  { key: 'sell_capital_per_trade', label: 'Capital/Trade ₹', step: 1000 },
]
```

- [ ] **Step 4: Update config UI layout** — shared params on top, then two columns:
```tsx
{/* Shared */}
<div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
  {SHARED_FIELDS.map(...)}
</div>
{/* BUY | SELL side by side */}
<div className="grid grid-cols-2 gap-4">
  <div>
    <h4 className="text-xs font-bold text-emerald-400 mb-2">BUY Settings</h4>
    <div className="grid grid-cols-2 gap-3">
      {BUY_FIELDS.map(...)}
    </div>
  </div>
  <div>
    <h4 className="text-xs font-bold text-red-400 mb-2">SELL Settings</h4>
    <div className="grid grid-cols-2 gap-3">
      {SELL_FIELDS.map(...)}
    </div>
  </div>
</div>
```

- [ ] **Step 5: Update `computeSignal`** to use per-direction params:
```typescript
const dirMinMove = direction === 'BUY' ? cfg.buy_min_move_pct : cfg.sell_min_move_pct
const dirMinVolRate = direction === 'BUY' ? cfg.buy_min_vol_rate : cfg.sell_min_vol_rate
const dirTp = direction === 'BUY' ? cfg.buy_tp_pct : cfg.sell_tp_pct
const dirSl = direction === 'BUY' ? cfg.buy_sl_pct : cfg.sell_sl_pct
```

- [ ] **Step 6: Update `replaySymbolDay`** — use per-direction TP/SL in exit check, per-direction capital in `computeDynamicQty`

- [ ] **Step 7: Update `handleSaveToEngine`** — send all 10 new fields

- [ ] **Step 8: Verify:** `docker compose build ui`

- [ ] **Step 9: Commit**

---

### Task 6: Deploy + Verify

- [ ] **Step 1:** `docker compose build engine ui`
- [ ] **Step 2:** `docker compose up -d`
- [ ] **Step 3:** Verify API returns new fields: `curl http://localhost:3001/api/config`
- [ ] **Step 4:** Test in Backtest UI — set BUY exit=20, SELL exit=60, verify different ROC
