# Dynamic Quantity & Direction Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BUY/SELL/BOTH direction toggle to UI backtest, implement equal-rupee allocation with dynamic quantity multipliers based on volume rate + morning range + stock price, and wire it through the live engine.

**Architecture:** New config fields (`direction_filter`, `capital_per_trade`) added to SignalConfig and ClickHouse. The signal engine computes dynamic qty at signal creation time using observable-at-entry factors. The UI backtest gets a direction toggle and uses the same dynamic qty formula in its `replaySymbolDay`. Quantity in the config becomes `capital_per_trade` (rupees to allocate per trade) instead of a fixed share count.

**Tech Stack:** Rust (engine), TypeScript/React (UI), ClickHouse SQL

---

### Task 1: Add New Config Fields to ClickHouse Schema

**Files:**
- Modify: `init/schema.sql:130-147`

- [ ] **Step 1: Add columns to schema.sql**

Add two new columns to `trading.config` table definition:

```sql
-- After sell_hard_exit_bucket line, add:
    direction_filter     String   DEFAULT 'BOTH',
    capital_per_trade    UInt32   DEFAULT 10000,
```

- [ ] **Step 2: Run ALTER TABLE on live ClickHouse**

```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "
  ALTER TABLE trading.config ADD COLUMN IF NOT EXISTS direction_filter String DEFAULT 'BOTH';
  ALTER TABLE trading.config ADD COLUMN IF NOT EXISTS capital_per_trade UInt32 DEFAULT 10000;
"
```

- [ ] **Step 3: Verify**

```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "DESCRIBE TABLE trading.config"
```

Expected: `direction_filter` and `capital_per_trade` appear in output.

- [ ] **Step 4: Commit**

```bash
git add init/schema.sql
git commit -m "feat: add direction_filter and capital_per_trade config columns"
```

---

### Task 2: Add Fields to Rust SignalConfig + DB Layer

**Files:**
- Modify: `engine/src/types.rs` — add `direction_filter: String` and `capital_per_trade: u32` to `SignalConfig`
- Modify: `engine/src/db/watchlist.rs` — add fields to `Row` struct, query, mapping, defaults, seed
- Modify: `engine/src/api/config.rs` — add fields to `update_config` patch handler + INSERT

- [ ] **Step 1: Add fields to SignalConfig in types.rs**

In `SignalConfig` struct after `sell_hard_exit_bucket`, add:

```rust
    pub direction_filter: String,    // "BUY", "SELL", or "BOTH"
    pub capital_per_trade: u32,      // Rupees to allocate per trade (0 = use fixed quantity field)
```

In `Default` impl, add:

```rust
    direction_filter: "BOTH".to_string(),
    capital_per_trade: 10000,
```

- [ ] **Step 2: Update db/watchlist.rs get_config**

Add `direction_filter` and `capital_per_trade` to:
1. The `Row` struct (add `direction_filter: String` and `capital_per_trade: u32`)
2. The SELECT column list
3. The fallback Row defaults (`direction_filter: "BOTH".to_string()`, `capital_per_trade: 10000`)
4. The Ok(SignalConfig { ... }) mapping

- [ ] **Step 3: Update db/watchlist.rs seed_config_if_empty**

Add the two new columns and bind values to the INSERT statement.

- [ ] **Step 4: Update api/config.rs update_config**

Add patch handling:
```rust
if let Some(v) = patch.get("direction_filter").and_then(|v| v.as_str()) { cfg.direction_filter = v.to_string(); }
if let Some(v) = patch.get("capital_per_trade").and_then(|v| v.as_u64()) { cfg.capital_per_trade = v as u32; }
```

Add the two columns + binds to the INSERT statement.

- [ ] **Step 5: Verify it compiles**

```bash
cd engine && cargo check
```

- [ ] **Step 6: Commit**

```bash
git add engine/src/types.rs engine/src/db/watchlist.rs engine/src/api/config.rs
git commit -m "feat: add direction_filter and capital_per_trade to SignalConfig"
```

---

### Task 3: Implement Dynamic Quantity Calculation in Engine

**Files:**
- Create: `engine/src/dynamic_qty.rs`
- Modify: `engine/src/lib.rs` or `engine/src/main.rs` — add `mod dynamic_qty;`

- [ ] **Step 1: Create engine/src/dynamic_qty.rs**

```rust
use crate::types::{Signal, SignalConfig, Snapshot};

/// Compute dynamic quantity for a signal based on observable-at-entry factors.
///
/// Formula:
///   base_qty = max(floor(capital_per_trade / entry_price), 1)
///   multiplier starts at 1.0
///     + 1.0 if volume_rate >= 500 (surging volume)
///     + 0.5 if morning_range_pct >= 1.0 (active morning)
///     + 0.5 if entry_price < 500 (cheap stock structural edge)
///   multiplier capped at 3.0
///   SKIP (return 0) if volume_rate < 100 AND morning_range_pct < 0.5
///
/// If capital_per_trade == 0, falls back to config.quantity (fixed mode).
pub fn compute_quantity(
    config: &SignalConfig,
    entry_price: f32,
    volume_rate: f32,
    morning_range_pct: f32,
) -> u32 {
    // Fixed mode: capital_per_trade == 0 means use legacy fixed quantity
    if config.capital_per_trade == 0 {
        return config.quantity;
    }

    // Kill switch: dead signal — calm morning + low volume
    if volume_rate < 100.0 && morning_range_pct < 0.5 {
        return 0;
    }

    let base = (config.capital_per_trade as f32 / entry_price).floor().max(1.0) as u32;

    let mut mult: f32 = 1.0;
    if volume_rate >= 500.0 { mult += 1.0; }
    if morning_range_pct >= 1.0 { mult += 0.5; }
    if entry_price < 500.0 { mult += 0.5; }
    mult = mult.min(3.0);

    ((base as f32 * mult).floor() as u32).max(1)
}

/// Compute morning range % from snapshots up to the entry bucket.
/// morning_range = (max_ltp - min_ltp) / avg_ltp * 100
/// Uses buckets 1 through entry_bucket (inclusive).
pub fn morning_range_pct(snapshots: &[Snapshot], entry_bucket: u16) -> f32 {
    let morning: Vec<f32> = snapshots.iter()
        .filter(|s| s.bucket >= 1 && s.bucket <= entry_bucket)
        .map(|s| s.ltp)
        .collect();
    if morning.is_empty() { return 0.0; }
    let max = morning.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
    let min = morning.iter().cloned().fold(f32::INFINITY, f32::min);
    let avg = morning.iter().sum::<f32>() / morning.len() as f32;
    if avg <= 0.0 { return 0.0; }
    (max - min) / avg * 100.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg(capital: u32, qty: u32) -> SignalConfig {
        SignalConfig { capital_per_trade: capital, quantity: qty, ..SignalConfig::default() }
    }

    #[test]
    fn test_fixed_mode_when_capital_zero() {
        assert_eq!(compute_quantity(&cfg(0, 5), 100.0, 999.0, 5.0), 5);
    }

    #[test]
    fn test_base_qty_equal_rupee() {
        // 10000 / 250 = 40 shares, multiplier 1.0
        assert_eq!(compute_quantity(&cfg(10000, 1), 250.0, 150.0, 0.8), 40);
    }

    #[test]
    fn test_surging_volume_doubles() {
        // 10000 / 250 = 40, mult 1.0 + 1.0(surging) = 2.0 → 80
        assert_eq!(compute_quantity(&cfg(10000, 1), 250.0, 600.0, 0.8), 80);
    }

    #[test]
    fn test_all_bonuses_capped_at_3x() {
        // 10000 / 100 = 100, mult = 1.0 + 1.0(vol) + 0.5(morning) + 0.5(cheap) = 3.0
        assert_eq!(compute_quantity(&cfg(10000, 1), 100.0, 600.0, 1.5), 300);
    }

    #[test]
    fn test_kill_switch_skips() {
        // vol_rate < 100 AND morning_range < 0.5 → qty = 0
        assert_eq!(compute_quantity(&cfg(10000, 1), 250.0, 50.0, 0.3), 0);
    }

    #[test]
    fn test_min_qty_is_1() {
        // Very expensive stock: 10000 / 50000 = 0.2 → floor = 0 → clamped to 1
        assert_eq!(compute_quantity(&cfg(10000, 1), 50000.0, 200.0, 1.0), 1);
    }

    #[test]
    fn test_cheap_stock_bonus() {
        // 10000 / 400 = 25, mult 1.0 + 0.5(cheap<500) = 1.5 → 37
        assert_eq!(compute_quantity(&cfg(10000, 1), 400.0, 150.0, 0.8), 37);
    }
}
```

- [ ] **Step 2: Register the module**

In `engine/src/main.rs` (or `lib.rs`), add:
```rust
mod dynamic_qty;
```

- [ ] **Step 3: Run tests**

```bash
cd engine && cargo test dynamic_qty
```

Expected: All 7 tests pass.

- [ ] **Step 4: Commit**

```bash
git add engine/src/dynamic_qty.rs engine/src/main.rs
git commit -m "feat: add dynamic_qty module with equal-rupee + multiplier formula"
```

---

### Task 4: Wire Dynamic Qty into Signal Engine + Poller

**Files:**
- Modify: `engine/src/signal_engine.rs:125-141` — use dynamic qty instead of `config.quantity`
- Modify: `engine/src/poller.rs` — pass snapshots to compute morning_range, apply direction filter, skip qty=0 signals

- [ ] **Step 1: Update signal_engine.rs to accept morning_range_pct**

Change `compute_signal` signature to accept `morning_range_pct: f32`:

```rust
pub fn compute_signal(
    snapshots: &[Snapshot],
    config: &SignalConfig,
    gap_pct: f32,
    morning_range_pct: f32,
) -> Option<Signal> {
```

Replace line 137 (`quantity: config.quantity,`) with:

```rust
        quantity: crate::dynamic_qty::compute_quantity(
            config,
            entry_price,
            last.volume_rate,
            morning_range_pct,
        ),
```

After `if score_u8 < config.min_score { return None; }`, add:

```rust
    let qty = crate::dynamic_qty::compute_quantity(
        config, entry_price, last.volume_rate, morning_range_pct,
    );
    if qty == 0 { return None; }  // kill switch: skip dead signals
```

And use `qty` in the Signal struct instead of the inline call.

- [ ] **Step 2: Update poller.rs signal engine call**

In the signal firing section (~line 216), before calling `compute_signal`, compute morning_range:

```rust
let mr = crate::dynamic_qty::morning_range_pct(&snaps, sig_config.entry_bucket_end as u16);
if let Some(signal) = compute_signal(&snaps, &sig_config, gap_pct, mr) {
```

- [ ] **Step 3: Add direction filter to poller.rs**

After computing the signal, check direction_filter before executing:

```rust
if let Some(signal) = compute_signal(&snaps, &sig_config, gap_pct, mr) {
    // Direction filter: skip if config says BUY-only or SELL-only
    let dominated = match sig_config.direction_filter.as_str() {
        "BUY" => signal.direction == crate::types::Direction::Sell,
        "SELL" => signal.direction == crate::types::Direction::Buy,
        _ => false, // "BOTH" or anything else = allow all
    };
    if dominated {
        tracing::debug!("Skipped {} {} (direction_filter={})", signal.direction.as_str(), signal.symbol, sig_config.direction_filter);
        continue;
    }
    // ... existing signal insert + execute logic
```

- [ ] **Step 4: Fix all test call sites**

Update all calls to `compute_signal` in `signal_engine.rs` tests to pass `0.0` as `morning_range_pct`:

```rust
let signal = compute_signal(&snaps, &default_config(), 0.5, 0.0);
```

- [ ] **Step 5: Verify it compiles and tests pass**

```bash
cd engine && cargo test
```

- [ ] **Step 6: Commit**

```bash
git add engine/src/signal_engine.rs engine/src/poller.rs
git commit -m "feat: wire dynamic qty + direction filter into signal engine and poller"
```

---

### Task 5: Add Direction Toggle + Capital-per-Trade to Backtest UI

**Files:**
- Modify: `ui/src/views/Backtest.tsx`

- [ ] **Step 1: Add fields to BacktestConfig interface**

After `min_vol_rate: number`, add:

```typescript
  direction_filter: 'BUY' | 'SELL' | 'BOTH'
  capital_per_trade: number
```

- [ ] **Step 2: Update configFromApi to read new fields**

In `configFromApi()`, add:

```typescript
    direction_filter: (c as any).direction_filter || 'BOTH',
    capital_per_trade: (c as any).capital_per_trade || 10000,
```

- [ ] **Step 3: Add dynamic qty helper function**

Before the `Backtest` component, add:

```typescript
function computeDynamicQty(cfg: BacktestConfig, entryPrice: number, volumeRate: number, morningRangePct: number): number {
  if (cfg.capital_per_trade === 0) return cfg.quantity
  if (volumeRate < 100 && morningRangePct < 0.5) return 0  // kill switch
  const base = Math.max(Math.floor(cfg.capital_per_trade / entryPrice), 1)
  let mult = 1.0
  if (volumeRate >= 500) mult += 1.0
  if (morningRangePct >= 1.0) mult += 0.5
  if (entryPrice < 500) mult += 0.5
  mult = Math.min(mult, 3.0)
  return Math.max(Math.floor(base * mult), 1)
}

function computeMorningRange(snaps: Omit<SnapshotWithSymbol, 'symbol' | 'trading_date'>[], entryBucket: number): number {
  const morning = snaps.filter(s => s.bucket >= 1 && s.bucket <= entryBucket).map(s => s.ltp)
  if (morning.length === 0) return 0
  const max = Math.max(...morning)
  const min = Math.min(...morning)
  const avg = morning.reduce((a, b) => a + b, 0) / morning.length
  return avg > 0 ? (max - min) / avg * 100 : 0
}
```

- [ ] **Step 4: Update replaySymbolDay to use direction filter + dynamic qty**

In `replaySymbolDay`, after signal is computed (around line 145 where `fired = true`):

a) Add direction filter check — if signal direction doesn't match `cfg.direction_filter`, skip:
```typescript
    if (res && !fired) {
      // Direction filter
      if (cfg.direction_filter !== 'BOTH' && res.direction !== cfg.direction_filter) {
        // Don't fire — direction not allowed
      } else {
        const morningRange = computeMorningRange(sorted, res.entryBucket)
        const entrySnap = sorted.find(s => s.bucket === res.entryBucket)
        const volRate = entrySnap?.volume_rate ?? 0
        const dynQty = computeDynamicQty(cfg, res.entryPrice, volRate, morningRange)
        if (dynQty === 0) {
          // Kill switch — skip this signal
        } else {
          fired = true
          // ... create active signal, using dynQty for quantity
```

b) Update PnL calculation (line ~176) to use dynamic qty:
```typescript
        const pnl: number = active.entry_price * (retPct / 100) * active.quantity
```

Where `active.quantity` is set from `dynQty` when creating the signal (add `quantity` field to BacktestSignal).

- [ ] **Step 5: Add `quantity` field to BacktestSignal interface**

```typescript
interface BacktestSignal {
  // ... existing fields
  quantity: number  // dynamic qty used for this trade
}
```

- [ ] **Step 6: Update perfFromSignals to use per-signal quantity**

Change `capital` calculation:
```typescript
const capital = signals.reduce((s, sig) => s + sig.entry_price * sig.quantity, 0)
```

And wherever `cfg.quantity` is used for capital calc, use per-signal quantity instead.

- [ ] **Step 7: Add direction toggle UI to config panel**

After the config fields grid (around line 535), add a direction toggle:

```tsx
{/* Direction filter toggle */}
<div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
  <span style={{ fontSize: 12, color: '#9ca3af' }}>Direction:</span>
  {(['BUY', 'SELL', 'BOTH'] as const).map(d => (
    <button key={d}
      onClick={() => setCfg(prev => prev ? { ...prev, direction_filter: d } : prev)}
      style={{
        padding: '4px 12px', fontSize: 12, borderRadius: 4, cursor: 'pointer',
        background: cfg?.direction_filter === d ? (d === 'BUY' ? '#065f46' : d === 'SELL' ? '#7f1d1d' : '#1e3a5f') : '#1f2937',
        color: cfg?.direction_filter === d ? '#fff' : '#9ca3af',
        border: cfg?.direction_filter === d ? '1px solid #10b981' : '1px solid #374151',
      }}>
      {d}
    </button>
  ))}
</div>
```

- [ ] **Step 8: Add capital_per_trade to CONFIG_FIELDS**

```typescript
{ key: 'capital_per_trade', label: 'Capital/Trade ₹', step: 1000 },
```

- [ ] **Step 9: Update handleSaveToEngine to send new fields**

Add to the putConfig call:
```typescript
direction_filter: cfg.direction_filter,
capital_per_trade: cfg.capital_per_trade,
```

- [ ] **Step 10: Verify UI builds**

```bash
cd ui && npm run build
```

- [ ] **Step 11: Commit**

```bash
git add ui/src/views/Backtest.tsx
git commit -m "feat: add direction toggle + dynamic qty to backtest UI"
```

---

### Task 6: Update Backtest P&L Display for Dynamic Qty

**Files:**
- Modify: `ui/src/views/Backtest.tsx`

- [ ] **Step 1: Update signals table to show quantity**

In the signals table row rendering, add a column showing `sig.quantity`:
```tsx
<td style={...}>{sig.quantity}</td>
```

And add the header:
```tsx
<th>Qty</th>
```

- [ ] **Step 2: Update daily breakdown to use per-signal quantity**

In `perDatePerf`, change the call to pass quantity from each signal instead of cfg.quantity:
The `perfFromSignals` function already reads `sig.quantity` after Task 5 Step 6.

- [ ] **Step 3: Update EOD exit P&L calculation**

In the fallback EOD exit in `replaySymbolDay` (lines ~184-193), use the signal's quantity:
```typescript
const pnl: number = active.entry_price * (retPct / 100) * active.quantity
```

- [ ] **Step 4: Verify everything works**

```bash
cd ui && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/views/Backtest.tsx
git commit -m "feat: show dynamic qty in backtest results"
```

---

### Task 7: Rebuild and Deploy

- [ ] **Step 1: Build engine**

```bash
docker compose build engine
```

- [ ] **Step 2: Build UI**

```bash
docker compose build ui
```

- [ ] **Step 3: Deploy**

```bash
docker compose up -d
```

- [ ] **Step 4: Verify config API returns new fields**

```bash
curl http://localhost:3001/api/config
```

Expected: Response includes `direction_filter` and `capital_per_trade`.

- [ ] **Step 5: Commit any remaining changes**

```bash
git add -A && git commit -m "chore: rebuild for dynamic qty + direction filter"
```
