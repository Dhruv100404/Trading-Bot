# Cherry-Pick Signal Ranker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cherry-pick layer that selects top N signals per day by score + applies TP, achieving near-100% win rate on Rs 50K capital.

**Architecture:** New `cherry_pick.rs` module handles CandidatePool, ranking, TP computation. `poller.rs` adds a branch: when `cherry_pick_enabled=true`, signals go through pool→rank→select flow instead of immediate execution. `Backtest.tsx` adds per-day grouping with same logic for validation.

**Tech Stack:** Rust (engine), TypeScript/React (UI), ClickHouse (schema), Docker (deployment)

**Spec:** `docs/superpowers/specs/2026-03-26-cherry-pick-signal-ranker-design.md`

---

### Task 1: Schema Migration — Add new config columns

**Files:**
- Modify: `init/schema.sql:130-174`

- [ ] **Step 1: Add columns to schema.sql**

Add after line 169 (`sell_gap_max_pct`), before `id`:

```sql
    cherry_pick_enabled  UInt8    DEFAULT 0,
    total_capital        UInt32   DEFAULT 50000,
    max_positions        UInt16   DEFAULT 20,
    min_position_value   UInt32   DEFAULT 5000,
    tp_score_scaling     UInt8    DEFAULT 0,
```

- [ ] **Step 2: Apply migration to running ClickHouse**

Run via docker exec (the running clickhouse container):
```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "
  ALTER TABLE trading.config
    ADD COLUMN IF NOT EXISTS cherry_pick_enabled UInt8 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_capital UInt32 DEFAULT 50000,
    ADD COLUMN IF NOT EXISTS max_positions UInt16 DEFAULT 20,
    ADD COLUMN IF NOT EXISTS min_position_value UInt32 DEFAULT 5000,
    ADD COLUMN IF NOT EXISTS tp_score_scaling UInt8 DEFAULT 0
"
```

- [ ] **Step 3: Verify**

```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "DESCRIBE trading.config" | grep -E "cherry|total_capital|max_positions|min_position|tp_score"
```

Expected: 5 new columns visible.

- [ ] **Step 4: Commit**

```bash
git add init/schema.sql
git commit -m "feat: add cherry-pick config columns to trading.config schema"
```

---

### Task 2: Rust types — Add new fields to SignalConfig

**Files:**
- Modify: `engine/src/types.rs:14-126`

- [ ] **Step 1: Add fields to SignalConfig struct**

Add after `sell_gap_max_pct: f32,` (line 79):

```rust
    // ── Cherry-pick settings ──
    pub cherry_pick_enabled: bool,
    pub total_capital: u32,
    pub max_positions: u16,
    pub min_position_value: u32,
    pub tp_score_scaling: bool,
```

- [ ] **Step 2: Add defaults**

In `impl Default for SignalConfig`, add after `sell_gap_max_pct: 100.0,` (line 123):

```rust
            cherry_pick_enabled: false,
            total_capital: 50000,
            max_positions: 20,
            min_position_value: 5000,
            tp_score_scaling: false,
```

- [ ] **Step 3: Verify build**

```bash
docker build -t dhan-backtest ./engine 2>&1 | grep -E "error|Finished"
```

Expected: Compilation errors in `db/watchlist.rs` (ConfigRow doesn't have new fields yet). That's expected — Task 3 fixes it.

- [ ] **Step 4: Commit**

```bash
git add engine/src/types.rs
git commit -m "feat: add cherry_pick fields to SignalConfig struct"
```

---

### Task 3: DB layer — Read new config fields

**Files:**
- Modify: `engine/src/db/watchlist.rs:104-178`

- [ ] **Step 1: Update CONFIG_SELECT_COLS**

Add to the SELECT string (after `sell_gap_max_pct`):

```rust
const CONFIG_SELECT_COLS: &str = "SELECT entry_bucket_start, entry_bucket_end, min_move_pct, min_volume, \
    min_score, tp_pct, sl_pct, hard_exit_bucket, quantity, \
    gap_filter_min_pct, gap_filter_max_pct, sell_gap_min_pct, min_vol_rate, \
    sell_hard_exit_bucket, buy_gap_max_pct, direction_filter, capital_per_trade, \
    buy_tp_pct, buy_sl_pct, sell_tp_pct, sell_sl_pct, \
    buy_min_move_pct, sell_min_move_pct, buy_min_vol_rate, sell_min_vol_rate, \
    buy_capital_per_trade, sell_capital_per_trade, \
    buy_qty_multiplier, sell_qty_multiplier, \
    buy_entry_start, buy_entry_end, sell_entry_start, sell_entry_end, \
    buy_min_volume, sell_min_volume, buy_min_score, sell_min_score, \
    buy_gap_min_pct, sell_gap_max_pct, \
    cherry_pick_enabled, total_capital, max_positions, min_position_value, tp_score_scaling, \
    account_client_id \
    FROM trading.config";
```

- [ ] **Step 2: Update ConfigRow struct**

Add fields:

```rust
struct ConfigRow {
    // ... existing fields ...
    buy_gap_min_pct: f32, sell_gap_max_pct: f32,
    cherry_pick_enabled: u8, total_capital: u32, max_positions: u16,
    min_position_value: u32, tp_score_scaling: u8,
    #[allow(dead_code)] account_client_id: String,
}
```

- [ ] **Step 3: Update row_to_config()**

Add at end before closing brace:

```rust
        cherry_pick_enabled: row.cherry_pick_enabled != 0,
        total_capital: row.total_capital,
        max_positions: row.max_positions,
        min_position_value: row.min_position_value,
        tp_score_scaling: row.tp_score_scaling != 0,
```

- [ ] **Step 4: Update default_config_row()**

Add:

```rust
        cherry_pick_enabled: 0, total_capital: 50000, max_positions: 20,
        min_position_value: 5000, tp_score_scaling: 0,
```

- [ ] **Step 5: Update seed_config_if_empty()**

Add the 5 new columns and bind values to the INSERT query.

- [ ] **Step 6: Verify build compiles**

```bash
docker build -t dhan-backtest ./engine 2>&1 | grep -E "error|Finished"
```

Expected: `Finished` with no errors.

- [ ] **Step 7: Commit**

```bash
git add engine/src/db/watchlist.rs
git commit -m "feat: read cherry-pick config fields from ClickHouse"
```

---

### Task 4: New module — cherry_pick.rs

**Files:**
- Create: `engine/src/cherry_pick.rs`
- Modify: `engine/src/main.rs` (add `mod cherry_pick;`)
- Modify: `engine/src/lib.rs` (add `pub mod cherry_pick;`)

- [ ] **Step 1: Create cherry_pick.rs**

```rust
use std::collections::HashMap;
use std::collections::HashSet;
use crate::types::{Signal, SignalConfig, Direction};

/// A candidate signal waiting in the pool for ranking
#[derive(Clone, Debug)]
pub struct Candidate {
    pub symbol: String,
    pub security_id: String,
    pub signal: Signal,
}

/// Pool collects candidates during the entry window, then ranks and selects top N
pub struct CandidatePool {
    candidates: HashMap<String, Candidate>,  // keyed by symbol
}

impl CandidatePool {
    pub fn new() -> Self {
        Self { candidates: HashMap::new() }
    }

    /// Reset at start of each trading day
    pub fn reset(&mut self) {
        self.candidates.clear();
    }

    /// Insert or update a candidate. Later entries overwrite earlier ones (more data = better signal).
    pub fn insert(&mut self, candidate: Candidate) {
        self.candidates.insert(candidate.symbol.clone(), candidate);
    }

    pub fn len(&self) -> usize {
        self.candidates.len()
    }

    /// Select top N candidates, excluding already-active symbols.
    /// Sorted by: score DESC, then entry_price ASC (cheaper = better capital efficiency)
    pub fn select_top_n(&self, n: usize, already_active: &HashSet<String>) -> Vec<Candidate> {
        let mut eligible: Vec<&Candidate> = self.candidates.values()
            .filter(|c| !already_active.contains(&c.symbol))
            .collect();

        eligible.sort_by(|a, b| {
            b.signal.score.cmp(&a.signal.score)
                .then(a.signal.entry_price.partial_cmp(&b.signal.entry_price)
                    .unwrap_or(std::cmp::Ordering::Equal))
        });

        eligible.into_iter().take(n).cloned().collect()
    }
}

/// Compute max concurrent positions from config
pub fn compute_max_positions(config: &SignalConfig) -> usize {
    let buying_power = config.total_capital as u64 * 5; // 5x intraday margin
    let cpt = config.capital_per_trade.max(1) as u64;
    let from_capital = (buying_power / cpt) as usize;
    from_capital.min(config.max_positions as usize).max(1)
}

/// Compute quantity for a position
pub fn compute_qty(entry_price: f32, config: &SignalConfig) -> u32 {
    if entry_price <= 0.0 { return 0; }
    let qty = (config.capital_per_trade as f32 / entry_price).floor() as u32;
    let qty = qty.max(1); // at least 1 share (margin covers expensive stocks)
    // Skip if position too small
    if entry_price * qty as f32 <= config.min_position_value as f32 {
        return 0;
    }
    qty
}

/// Compute TP price based on score (when tp_score_scaling enabled)
pub fn compute_tp_price(signal: &Signal, config: &SignalConfig) -> f32 {
    let base_tp = match signal.direction {
        Direction::Buy => config.buy_tp_pct,
        Direction::Sell => config.sell_tp_pct,
    };

    let tp_pct = if config.tp_score_scaling && base_tp > 0.0 {
        let multiplier = match signal.score {
            0..=5 => 1.0,
            6..=7 => 1.4,
            8..=9 => 2.0,
            _ => 3.0, // 10+
        };
        base_tp * multiplier
    } else {
        base_tp
    };

    let dir_sign = signal.direction.sign();
    signal.entry_price * (1.0 + dir_sign * tp_pct / 100.0)
}

/// Compute SL price (unchanged from current logic, but centralized here)
pub fn compute_sl_price(signal: &Signal, config: &SignalConfig) -> f32 {
    let sl_pct = match signal.direction {
        Direction::Buy => config.buy_sl_pct,
        Direction::Sell => config.sell_sl_pct,
    };
    let dir_sign = signal.direction.sign();
    signal.entry_price * (1.0 - dir_sign * sl_pct / 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn make_signal(symbol: &str, score: u8, price: f32) -> Signal {
        Signal {
            symbol: symbol.into(), security_id: "1".into(),
            trading_date: NaiveDate::from_ymd_opt(2026, 3, 26).unwrap(),
            direction: Direction::Sell, score,
            signals_fired: vec![], entry_price: price,
            entry_bucket: 2, entry_ts: 0,
            tp_price: price * 0.993, sl_price: price * 1.018,
            quantity: 1, open_price: price * 1.005, gap_pct: 0.5,
        }
    }

    #[test]
    fn test_select_top_n_by_score_then_price() {
        let mut pool = CandidatePool::new();
        pool.insert(Candidate { symbol: "A".into(), security_id: "1".into(), signal: make_signal("A", 7, 500.0) });
        pool.insert(Candidate { symbol: "B".into(), security_id: "2".into(), signal: make_signal("B", 9, 300.0) });
        pool.insert(Candidate { symbol: "C".into(), security_id: "3".into(), signal: make_signal("C", 9, 200.0) });
        pool.insert(Candidate { symbol: "D".into(), security_id: "4".into(), signal: make_signal("D", 5, 100.0) });

        let selected = pool.select_top_n(2, &HashSet::new());
        assert_eq!(selected.len(), 2);
        assert_eq!(selected[0].symbol, "C"); // score=9, price=200 (cheapest of score=9)
        assert_eq!(selected[1].symbol, "B"); // score=9, price=300
    }

    #[test]
    fn test_select_excludes_active() {
        let mut pool = CandidatePool::new();
        pool.insert(Candidate { symbol: "A".into(), security_id: "1".into(), signal: make_signal("A", 9, 100.0) });
        pool.insert(Candidate { symbol: "B".into(), security_id: "2".into(), signal: make_signal("B", 7, 200.0) });

        let mut active = HashSet::new();
        active.insert("A".to_string());

        let selected = pool.select_top_n(2, &active);
        assert_eq!(selected.len(), 1);
        assert_eq!(selected[0].symbol, "B");
    }

    #[test]
    fn test_compute_max_positions() {
        let mut cfg = SignalConfig::default();
        cfg.total_capital = 50000;
        cfg.capital_per_trade = 25000;
        cfg.max_positions = 20;
        assert_eq!(compute_max_positions(&cfg), 10); // 50K*5/25K = 10, min(10, 20)

        cfg.max_positions = 5;
        assert_eq!(compute_max_positions(&cfg), 5); // capped at 5
    }

    #[test]
    fn test_compute_qty() {
        let mut cfg = SignalConfig::default();
        cfg.capital_per_trade = 25000;
        cfg.min_position_value = 5000;

        assert_eq!(compute_qty(200.0, &cfg), 125); // 25000/200 = 125
        assert_eq!(compute_qty(2000.0, &cfg), 12);  // 25000/2000 = 12
        assert_eq!(compute_qty(30000.0, &cfg), 1);  // min 1 share
    }

    #[test]
    fn test_compute_qty_skip_penny() {
        let mut cfg = SignalConfig::default();
        cfg.capital_per_trade = 25000;
        cfg.min_position_value = 5000;

        // Rs 2 stock: qty=12500, position=25000 > 5000 → OK
        assert!(compute_qty(2.0, &cfg) > 0);
        // But if min_position_value is very high:
        cfg.min_position_value = 30000;
        assert_eq!(compute_qty(200.0, &cfg), 0); // 125*200=25000 < 30000 → skip
    }

    #[test]
    fn test_tp_score_scaling() {
        let mut cfg = SignalConfig::default();
        cfg.sell_tp_pct = 0.5;
        cfg.tp_score_scaling = true;

        let sig5 = make_signal("A", 5, 1000.0);
        let sig8 = make_signal("B", 8, 1000.0);
        let sig10 = make_signal("C", 10, 1000.0);

        // score 5: 1.0x → 0.5% TP → sell tp = 1000 * (1 - 0.005) = 995
        let tp5 = compute_tp_price(&sig5, &cfg);
        assert!((tp5 - 995.0).abs() < 0.1);

        // score 8: 2.0x → 1.0% TP → sell tp = 1000 * (1 - 0.01) = 990
        let tp8 = compute_tp_price(&sig8, &cfg);
        assert!((tp8 - 990.0).abs() < 0.1);

        // score 10: 3.0x → 1.5% TP → sell tp = 1000 * (1 - 0.015) = 985
        let tp10 = compute_tp_price(&sig10, &cfg);
        assert!((tp10 - 985.0).abs() < 0.1);
    }

    #[test]
    fn test_tp_no_scaling() {
        let mut cfg = SignalConfig::default();
        cfg.sell_tp_pct = 0.7;
        cfg.tp_score_scaling = false;

        let sig = make_signal("A", 10, 1000.0);
        let tp = compute_tp_price(&sig, &cfg);
        // No scaling: 0.7% regardless of score → 1000 * (1 - 0.007) = 993
        assert!((tp - 993.0).abs() < 0.1);
    }

    #[test]
    fn test_pool_overwrite() {
        let mut pool = CandidatePool::new();
        pool.insert(Candidate { symbol: "A".into(), security_id: "1".into(), signal: make_signal("A", 5, 100.0) });
        pool.insert(Candidate { symbol: "A".into(), security_id: "1".into(), signal: make_signal("A", 9, 100.0) });
        assert_eq!(pool.len(), 1);
        let selected = pool.select_top_n(1, &HashSet::new());
        assert_eq!(selected[0].signal.score, 9); // later entry overwrites
    }
}
```

- [ ] **Step 2: Register module in main.rs and lib.rs**

In `engine/src/main.rs`, add after `mod dynamic_qty;`:
```rust
mod cherry_pick;
```

In `engine/src/lib.rs`, add:
```rust
pub mod cherry_pick;
```

- [ ] **Step 3: Build and run tests**

```bash
docker build -t dhan-backtest ./engine 2>&1 | grep -E "error|Finished|test result"
```

Expected: `Finished` with all tests passing.

- [ ] **Step 4: Commit**

```bash
git add engine/src/cherry_pick.rs engine/src/main.rs engine/src/lib.rs
git commit -m "feat: add cherry_pick module with CandidatePool, ranking, TP scaling"
```

---

### Task 5: Poller integration — cherry-pick branch

**Files:**
- Modify: `engine/src/poller.rs:448-509` (signal engine section)

This is the critical change. We add a branch in the per-account signal processing section.

- [ ] **Step 1: Add CandidatePool to per-account state**

At the top of `run()`, after `account_fired_today` (line 55), add:

```rust
    // Cherry-pick: per-account candidate pools
    let mut account_candidate_pools: HashMap<String, crate::cherry_pick::CandidatePool> = HashMap::new();
```

In the daily reset block (line 77-88), add:

```rust
                account_candidate_pools.clear();
```

- [ ] **Step 2: Replace the signal engine section (lines 456-509)**

Replace the signal firing block with cherry-pick-aware logic:

```rust
            // ── Signal engine ──
            let wide_start = sig_config.buy_entry_start.min(sig_config.sell_entry_start) as u16;
            let wide_end = sig_config.buy_entry_end.max(sig_config.sell_entry_end) as u16;
            if bucket >= wide_start && bucket <= wide_end {
                tracing::info!("[{}] Signal engine: bucket={} window={}-{} symbols={}", acct_label, bucket, wide_start, wide_end, by_symbol.len());

                // Collect candidates
                for (symbol, snaps) in &by_symbol {
                    if fired.contains(symbol) { continue; }
                    let mut sorted_snaps = snaps.clone();
                    sorted_snaps.sort_by_key(|s| s.bucket);
                    let gap_pct = daily_ref::get_gap_pct(&ch, trading_date, symbol).await.unwrap_or(0.0);
                    let mr = crate::dynamic_qty::morning_range_pct(&sorted_snaps, wide_end);
                    if let Some(signal) = compute_signal(&sorted_snaps, sig_config, gap_pct, mr) {
                        let dir_blocked = match sig_config.direction_filter.as_str() {
                            "BUY" => signal.direction == crate::types::Direction::Sell,
                            "SELL" => signal.direction == crate::types::Direction::Buy,
                            _ => false,
                        };
                        if dir_blocked { continue; }

                        if sig_config.cherry_pick_enabled {
                            // Cherry-pick mode: add to pool, don't execute yet
                            let pool = account_candidate_pools.entry(account_id.clone())
                                .or_insert_with(crate::cherry_pick::CandidatePool::new);
                            pool.insert(crate::cherry_pick::Candidate {
                                symbol: symbol.clone(),
                                security_id: signal.security_id.clone(),
                                signal,
                            });
                        } else {
                            // Legacy mode: fire immediately (existing behavior)
                            tracing::info!("🔔 Signal [{}]: {} {} score={} qty={}",
                                acct_label, signal.direction.as_str(), signal.symbol, signal.score, signal.quantity);
                            match sig_db::insert_signal(&ch, &signal, sig_config).await {
                            Err(e) => tracing::error!("Signal insert FAILED for {}: {}", signal.symbol, e),
                            Ok(sig_id) => {
                                fired.insert(signal.symbol.clone());
                                fired_today.lock().await.insert(signal.symbol.clone());
                                let last_ltp = signal.entry_price;
                                open_sigs.insert(signal.symbol.clone(), (signal.clone(), sig_id, last_ltp));
                                let sig_clone = signal.clone();
                                let ch_clone = ch.clone();
                                let exec_clone = OrderExecutor::new(config.clone());
                                let acct_id_clone = account_id.clone();
                                tokio::spawn(async move {
                                    if let Err(e) = exec_clone.execute(&sig_clone, sig_id, &ch_clone, &acct_id_clone).await {
                                        tracing::error!("Order failed for {} [{}]: {}", sig_clone.symbol, acct_id_clone, e);
                                    }
                                });
                                let evt = WsEvent { event_type: "signal".into(), data: serde_json::to_value(&signal).unwrap_or_default() };
                                let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                            }}
                        }
                    }
                }

                // Cherry-pick: select and execute top N from pool
                if sig_config.cherry_pick_enabled {
                    let pool = account_candidate_pools.entry(account_id.clone())
                        .or_insert_with(crate::cherry_pick::CandidatePool::new);
                    let max_n = crate::cherry_pick::compute_max_positions(sig_config);
                    let active_symbols: std::collections::HashSet<String> = open_sigs.keys().cloned().collect();
                    let available_slots = max_n.saturating_sub(active_symbols.len());

                    if available_slots > 0 {
                        let selected = pool.select_top_n(available_slots, &active_symbols.iter().chain(fired.iter()).cloned().collect());
                        tracing::info!("[{}] Cherry-pick: pool={} active={} slots={} selected={}",
                            acct_label, pool.len(), active_symbols.len(), available_slots, selected.len());

                        for candidate in selected {
                            let mut signal = candidate.signal;
                            // Recompute qty using capital-based sizing
                            let qty = crate::cherry_pick::compute_qty(signal.entry_price, sig_config);
                            if qty == 0 { continue; } // skip penny stocks
                            signal.quantity = qty;
                            // Recompute TP/SL using cherry-pick logic
                            signal.tp_price = crate::cherry_pick::compute_tp_price(&signal, sig_config);
                            signal.sl_price = crate::cherry_pick::compute_sl_price(&signal, sig_config);

                            tracing::info!("🔔🏆 Cherry-picked [{}]: {} {} score={} qty={} tp={:.2}",
                                acct_label, signal.direction.as_str(), signal.symbol, signal.score, signal.quantity, signal.tp_price);

                            match sig_db::insert_signal(&ch, &signal, sig_config).await {
                            Err(e) => tracing::error!("Signal insert FAILED for {}: {}", signal.symbol, e),
                            Ok(sig_id) => {
                                fired.insert(signal.symbol.clone());
                                fired_today.lock().await.insert(signal.symbol.clone());
                                let last_ltp = signal.entry_price;
                                open_sigs.insert(signal.symbol.clone(), (signal.clone(), sig_id, last_ltp));
                                let sig_clone = signal.clone();
                                let ch_clone = ch.clone();
                                let exec_clone = OrderExecutor::new(config.clone());
                                let acct_id_clone = account_id.clone();
                                tokio::spawn(async move {
                                    if let Err(e) = exec_clone.execute(&sig_clone, sig_id, &ch_clone, &acct_id_clone).await {
                                        tracing::error!("Order failed for {} [{}]: {}", sig_clone.symbol, acct_id_clone, e);
                                    }
                                });
                                let evt = WsEvent { event_type: "signal".into(), data: serde_json::to_value(&signal).unwrap_or_default() };
                                let _ = ws_tx.send(serde_json::to_string(&evt).unwrap_or_default());
                            }}
                        }
                    }
                }
            }
```

- [ ] **Step 3: Build and verify**

```bash
docker build -t dhan-backtest ./engine 2>&1 | grep -E "error|Finished"
```

Expected: `Finished` with no errors.

- [ ] **Step 4: Commit**

```bash
git add engine/src/poller.rs
git commit -m "feat: integrate cherry-pick into poller signal engine"
```

---

### Task 6: WebSocket tick handler — cherry-pick branch

**Files:**
- Modify: `engine/src/poller.rs:354-392` (WS tick signal detection)

- [ ] **Step 1: Add cherry-pick branch to WS tick handler**

In the WS tick signal detection block (around line 361), after `compute_signal` returns Some, add the same cherry-pick branch as the polling path:

```rust
                                        if !dir_blocked {
                                            if sig_config.cherry_pick_enabled {
                                                let pool = account_candidate_pools.entry(account_id.clone())
                                                    .or_insert_with(crate::cherry_pick::CandidatePool::new);
                                                pool.insert(crate::cherry_pick::Candidate {
                                                    symbol: sym.clone(),
                                                    security_id: signal.security_id.clone(),
                                                    signal,
                                                });
                                                // Selection happens in the main poll loop below
                                            } else {
                                                // existing immediate-fire logic unchanged
                                                tracing::info!("🔔⚡ WS Signal ...");
                                                // ... existing code ...
                                            }
                                        }
```

Note: The actual selection (select_top_n + execute) happens in the main poll loop (Task 5 code), which runs after WS ticks are drained. This is intentional — WS ticks feed the pool, poll cycle triggers selection.

- [ ] **Step 2: Build and verify**

- [ ] **Step 3: Commit**

```bash
git add engine/src/poller.rs
git commit -m "feat: cherry-pick branch in WebSocket tick handler"
```

---

### Task 7: UI Backtest.tsx — Cherry-pick simulation

**Files:**
- Modify: `ui/src/views/Backtest.tsx`

- [ ] **Step 1: Add new config fields to BacktestConfig interface (line 7)**

```typescript
  cherry_pick_enabled: boolean
  total_capital: number
  max_positions: number
  min_position_value: number
  tp_score_scaling: boolean
```

- [ ] **Step 2: Add defaults in configFromApi() (line 299)**

```typescript
    cherry_pick_enabled: !!(c as any).cherry_pick_enabled,
    total_capital: (c as any).total_capital ?? 50000,
    max_positions: (c as any).max_positions ?? 20,
    min_position_value: (c as any).min_position_value ?? 5000,
    tp_score_scaling: !!(c as any).tp_score_scaling,
```

- [ ] **Step 3: Add TP scaling function**

After `computeDynamicQty` (line 96):

```typescript
function computeTPPrice(entryPrice: number, direction: 'BUY' | 'SELL', score: number, cfg: BacktestConfig): number {
  const baseTp = direction === 'BUY' ? cfg.buy_tp_pct : cfg.sell_tp_pct
  let tpPct = baseTp
  if (cfg.tp_score_scaling && baseTp > 0) {
    const mult = score <= 5 ? 1.0 : score <= 7 ? 1.4 : score <= 9 ? 2.0 : 3.0
    tpPct = baseTp * mult
  }
  const dirSign = direction === 'BUY' ? 1 : -1
  return entryPrice * (1 + dirSign * tpPct / 100)
}

function computeCherryQty(entryPrice: number, cfg: BacktestConfig): number {
  if (entryPrice <= 0) return 0
  const qty = Math.max(Math.floor(cfg.capital_per_trade / entryPrice), 1)
  if (entryPrice * qty < cfg.min_position_value) return 0
  return qty
}
```

- [ ] **Step 4: Modify the main backtest loop**

Find the section where `replaySymbolDay` is called for each symbol (in the useMemo that generates signals). Wrap it in a per-day grouping when cherry_pick_enabled:

```typescript
// Inside the backtest useMemo:
if (cfg.cherry_pick_enabled) {
  // Per-day cherry-pick mode
  const maxN = Math.min(
    Math.floor(cfg.total_capital * 5 / cfg.capital_per_trade),
    cfg.max_positions
  )

  for (const date of tradingDays) {
    // Collect all candidates for this day
    const dayCandidates: BacktestSignal[] = []
    for (const symbol of symbols) {
      const key = `${date}|${symbol}`
      const snaps = snapMap[key]
      const ref = refMap[key]
      if (!snaps) continue
      const gapPct = ref?.gap_pct ?? 0
      const result = replaySymbolDay(snaps, cfg, gapPct, date, symbol)
      if (result) dayCandidates.push(result)
    }

    // Rank: score DESC, entry_price ASC
    dayCandidates.sort((a, b) => b.score - a.score || a.entry_price - b.entry_price)

    // Select top N
    const selected = dayCandidates.slice(0, maxN)

    // Recompute qty + TP for selected, then simulate exits
    for (const sig of selected) {
      // Recompute qty
      sig.quantity = computeCherryQty(sig.entry_price, cfg)
      if (sig.quantity === 0) continue

      // Recompute TP
      const newTp = computeTPPrice(sig.entry_price, sig.direction, sig.score, cfg)
      sig.tp_price = Math.round(newTp * 100) / 100

      // Re-simulate exit with new TP
      // ... (re-run exit logic on the snapshots for this symbol)

      signals.push(sig)
    }
  }
} else {
  // Legacy per-symbol mode (existing code, unchanged)
  for (const symbol of symbols) { ... }
}
```

- [ ] **Step 5: Add config controls to the UI panel**

In the config fields section (around line 383), add new fields under a "Cherry-Pick" section:

```typescript
const CHERRY_FIELDS: CfgField[] = [
  { key: 'cherry_pick_enabled', label: 'Cherry-Pick', step: 1 },
  { key: 'total_capital', label: 'Total Capital (₹)', step: 10000 },
  { key: 'max_positions', label: 'Max Positions', step: 1 },
  { key: 'min_position_value', label: 'Min Position (₹)', step: 1000 },
  { key: 'tp_score_scaling', label: 'TP Score Scaling', step: 1 },
]
```

- [ ] **Step 6: Add summary badge showing candidates vs selected**

In the performance display, add a line:

```typescript
{cfg.cherry_pick_enabled && (
  <div>Cherry-pick: {totalCandidates} candidates → {perf.total} selected</div>
)}
```

- [ ] **Step 7: Verify UI builds**

```bash
cd ui && npm run build
```

- [ ] **Step 8: Commit**

```bash
git add ui/src/views/Backtest.tsx
git commit -m "feat: cherry-pick simulation in UI backtest"
```

---

### Task 8: Docker rebuild + integration test

**Files:**
- Modify: `engine/Dockerfile` (already updated from backtest binary work)

- [ ] **Step 1: Rebuild everything**

```bash
docker compose build engine ui
```

- [ ] **Step 2: Restart stack**

```bash
docker compose up -d
```

- [ ] **Step 3: Apply schema migration**

```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "
  ALTER TABLE trading.config
    ADD COLUMN IF NOT EXISTS cherry_pick_enabled UInt8 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_capital UInt32 DEFAULT 50000,
    ADD COLUMN IF NOT EXISTS max_positions UInt16 DEFAULT 20,
    ADD COLUMN IF NOT EXISTS min_position_value UInt32 DEFAULT 5000,
    ADD COLUMN IF NOT EXISTS tp_score_scaling UInt8 DEFAULT 0
"
```

- [ ] **Step 4: Verify config reads correctly**

```bash
curl -s http://localhost:8080/api/config | python3 -c "import sys,json; d=json.load(sys.stdin); print('cherry_pick_enabled:', d.get('cherry_pick_enabled', 'MISSING'))"
```

Expected: `cherry_pick_enabled: 0`

- [ ] **Step 5: Test in UI backtest**

1. Open http://localhost:3000 → Backtest tab
2. Set `buy_tp_pct=0.7, sell_tp_pct=0.7`
3. Run backtest → note win rate (should be ~74% F&O)
4. Enable `cherry_pick_enabled=true`, set `total_capital=50000`
5. Run backtest → should see "N candidates → 10 selected" and higher win rate

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: complete cherry-pick signal ranker integration"
```

---

### Task 9: Rollout — enable in live config

- [ ] **Step 1: Enable TP first (safe, no code change)**

```bash
docker exec dhan-trader-clickhouse-1 clickhouse-client --query "
  INSERT INTO trading.config
  SELECT *, 0.7 AS buy_tp_pct_new, 0.7 AS sell_tp_pct_new
  FROM trading.config FINAL
  LIMIT 1
"
```

Or just update via the UI config panel: set `buy_tp_pct=0.7`, `sell_tp_pct=0.7`.

- [ ] **Step 2: Monitor for 1-2 days with TP only**

Check signals table for TP exits happening.

- [ ] **Step 3: Enable cherry-pick**

Via UI: set `cherry_pick_enabled=1`, `total_capital=50000`, `capital_per_trade=25000`.

- [ ] **Step 4: Monitor first live day**

Check logs: `docker logs dhan-trader-engine-1 | grep "Cherry-pick"`

Expected: `Cherry-pick: pool=X active=Y slots=Z selected=W` logs during entry window.
