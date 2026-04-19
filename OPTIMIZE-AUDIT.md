# ClickHouse OPTIMIZE Audit — ReplacingMergeTree Race Conditions

ReplacingMergeTree deduplicates rows by ORDER BY key, but only during **background merges** (not at INSERT time). If you INSERT a new version of a row and immediately read, the SELECT FINAL may still return the old row until the merge completes. This doc lists every write-then-read pattern that needs `OPTIMIZE TABLE ... FINAL` or a design fix.

---

## HIGH RISK — Live trading impacted

### 1. `watchlist.rs` — Tier toggle resets watchlist to wrong count

**File:** `engine/src/api/watchlist.rs` ~line 77-104  
**Flow:**
```
1. INSERT INTO trading.tier_state (tier_name, enabled) VALUES (?, ?)   -- line 77
2. SELECT tier_name FROM trading.tier_state FINAL WHERE enabled = 1    -- line 84
3. SELECT ... FROM trading.watchlist FINAL ...                         -- line 96
4. INSERT INTO trading.watchlist ... enabled = (tier match)            -- line 96-101
```
**Problem:** Step 2 reads tiers immediately after step 1. If merge hasn't happened, the just-toggled tier is invisible. The watchlist re-eval then disables stocks that should be enabled (exactly what caused the 1269 -> 229 bug).  
**Fix:** Add `OPTIMIZE TABLE trading.tier_state FINAL` between step 1 and step 2.

---

### 2. `daily_ref.rs` — `store_closing_price()` can lose fields

**File:** `engine/src/db/daily_ref.rs` ~line 91-122  
**Flow:**
```
1. SELECT prev_close, day_open, gap_pct, ... FROM trading.daily_ref FINAL WHERE date=? AND symbol=?
2. INSERT INTO trading.daily_ref (...all fields...) VALUES (existing.prev_close, ..., new_closing_price)
```
**Problem:** Step 1 reads the existing row to preserve fields. But if a recent INSERT (e.g., from `update_day_open()`) hasn't merged, SELECT FINAL might return the **older** row missing `day_open`. The re-insert then overwrites the newer row with stale data.  
**Fix:** Add `OPTIMIZE TABLE trading.daily_ref FINAL` before the SELECT, or restructure to avoid read-then-reinsert.

---

### 3. `daily_ref.rs` — `update_day_open()` same pattern

**File:** `engine/src/db/daily_ref.rs` ~line 55-80  
**Flow:** Same read-existing-then-reinsert pattern as `store_closing_price()`.  
**Problem:** If pre-market seed inserted `prev_close` and this function runs shortly after to add `day_open`, the SELECT might miss the `prev_close` row.  
**Fix:** Same as above — OPTIMIZE before read, or combine into single insert.

---

### 4. `poller.rs` — Partial daily_ref inserts overwrite each other

**File:** `engine/src/poller.rs` ~line 1514-1551  
**Flow:**
```
1. INSERT INTO trading.daily_ref (date, symbol, ..., day_open, gap_pct, closing_price) -- full row, line 1514
2. INSERT INTO trading.daily_ref (date, symbol, ..., prev_day_high, prev_day_low)      -- partial row, line 1524
```
**Problem:** Both inserts target the same (date, symbol) key. The second insert has `0` for fields like `day_open`, `closing_price` because it only sets `prev_day_high/low`. If the second insert's row wins the merge (newer `inserted_at`), the full row's data is lost.  
**Fix:** Combine both inserts into a single full INSERT, or read-merge-write with OPTIMIZE.

---

### 5. `poller.rs` — Direction loaded before daily_ref fully populated

**File:** `engine/src/poller.rs` ~line 116-129  
**Flow:**
```
1. load_prev_day_directions() → SELECT ... FROM trading.daily_ref FINAL ...   -- gets 211 stocks
2. seed_daily_ref_premarket() → 1269 INSERTs into daily_ref                   -- adds remaining stocks
3. Direction map is never reloaded (map is non-empty)
```
**Problem:** Direction loads once when non-empty. Pre-market seed adds 1000+ stocks AFTER direction loaded. Those stocks have `pdd = 0` and are excluded from gap reversal. (This is the bug from April 1.)  
**Fix:** Code fix deployed: reload directions after seed completes. Also add OPTIMIZE before the reload to ensure all inserts are visible.

---

## MEDIUM RISK — UI/config may show stale data

### 6. `config.rs` — Config update may not be visible immediately

**File:** `engine/src/api/config.rs` ~line 28-118  
**Flow:**
```
1. SELECT ... FROM trading.config FINAL WHERE account_client_id = ?   -- read current
2. Modify fields in memory
3. INSERT INTO trading.config (...67 columns...)                      -- write new
```
**Problem:** After step 3, the next API GET might still return the old config if merge hasn't happened. The poller caches config every 5 min so it's less critical there, but the UI shows stale values.  
**Fix:** Add `OPTIMIZE TABLE trading.config FINAL` after INSERT in the PATCH handler, or return the patched object directly.

---

### 7. `settings.rs` — System settings read-after-write

**File:** `engine/src/api/settings.rs` ~line 31-54  
**Flow:**
```
1. INSERT INTO trading.system_settings (key, value) VALUES (?, ?)   -- set_setting()
2. SELECT value FROM trading.system_settings FINAL WHERE key = ?    -- get_setting() on next request
```
**Problem:** Next GET request might return old token/setting value.  
**Fix:** Add OPTIMIZE after INSERT, or cache in-memory.

---

### 8. `accounts.rs` — Account update read-after-write

**File:** `engine/src/api/accounts.rs` ~line 48-76  
**Flow:**
```
1. SELECT ... FROM trading.accounts FINAL WHERE client_id = ?   -- read current
2. INSERT INTO trading.accounts (...) VALUES (...)               -- update
```
**Problem:** Subsequent GET returns old account data until merge.  
**Fix:** OPTIMIZE after INSERT, or return the updated object directly.

---

## LOW RISK — Scripts/batch jobs

### 9. `compute-win-rates.js` — Verify reads stale count

**File:** `deploy/compute-win-rates.js` ~line 83-92  
**Flow:**
```
1. INSERT INTO trading.stock_win_rate ... VALUES (batch)
2. SELECT count(), avg(win_rate) FROM trading.stock_win_rate FINAL   -- immediate verify
```
**Fix:** Add `OPTIMIZE TABLE trading.stock_win_rate FINAL` before verify query.

---

### 10. `seed-liquid-tier.ts` — Tier + watchlist race

**File:** `scripts/seed-liquid-tier.ts` ~line 27-121  
**Flow:**
```
1. INSERT INTO trading.tier_state ('Liquid5L', 0)
2. SELECT ... FROM trading.watchlist FINAL ...             -- might not see tier
3. INSERT INTO trading.watchlist ... (with Liquid5L tier)
4. SELECT count() FROM trading.watchlist FINAL WHERE has(tiers, 'Liquid5L')  -- verify
```
**Fix:** OPTIMIZE after step 1 and after step 3.

---

### 11. `fix-daily-ref.js` — Verify after batch insert

**File:** `deploy/fix-daily-ref.js` ~line 108-138  
**Flow:** Batch INSERT into daily_ref, then verify query.  
**Fix:** OPTIMIZE before verify.

---

### 12. `scrip_master.rs` — force_sync at midnight resets watchlist

**File:** `engine/src/dhan/scrip_master.rs` ~line 41-153  
**Flow:** `force_sync()` is called at midnight. It re-inserts ALL watchlist rows with `enabled` based on active tiers. But `Liquid5L` tier was not in the scrip master CSV — so all Liquid5L stocks got `enabled=0`.  
**Fix:** Code fix: added `Liquid5L` parsing from `liquid-5l-symbols.json` into scrip master sync (not yet deployed).

---

## CRITICAL — Live P&L mismatch and execution issues

### 13. Entry price uses stale snapshot LTP, not actual broker fill price

**Files:** `engine/src/poller.rs` ~line 835-843, 979-998  
**Observed:** April 2, 2026 — MSPL and ZFCVINDIA  

**Flow:**
```
1. Poll cycle stores snapshot at bucket 6 (e.g., MSPL LTP = ₹30.93)      -- few seconds ago
2. Signal engine reads sorted_snaps.last().ltp = ₹30.93                   -- line 835, 843
3. Candidate created with entry_price = ₹30.93, added to pool             -- line 905-909
4. Cherry-pick selects, computes TP = 30.93 × (1 - 0.384%) = ₹30.57      -- line 986
5. Order placed to Dhan                                                    -- line 1000-1005
6. Dhan fills at ₹30.81 (market moved in 5-10 seconds)                    -- actual fill
7. System tracks exits using entry_price = ₹30.93 (WRONG)                 -- line 997
8. TP triggers when LTP hits ₹30.57, but real P&L is based on ₹30.81     -- mismatch
```
**Real-world impact (April 2):**

| Stock | System Entry | Actual Fill | System P&L | Real P&L | Error |
|-------|-------------|------------|-----------|---------|-------|
| MSPL | ₹30.93 | ₹30.81 | +₹407 | +₹328 | ₹79 overstated |
| ZFCVINDIA | ₹14,569 | ₹14,428 | +₹368 | +₹90 | ₹278 overstated |

**Problem:** The `entry_price` is never updated after order execution. The `order_executor.execute()` spawns in a background task (line 1000) and its return value is not used to update `signal.entry_price`. All subsequent TP/SL checks and P&L calculations use the stale snapshot price.  
**Fix:** After order fill confirmation, update `signal.entry_price` to `averageTradedPrice` from Dhan, then recompute TP/SL. This requires the order executor to return the fill price and the poller to update `open_sigs`.

---

### 14. TP/SL computed once from stale price, never recomputed after fill

**Files:** `engine/src/cherry_pick.rs` ~line 153-178, `engine/src/poller.rs` ~line 986-987  
**Flow:**
```
1. signal.entry_price = snapshot LTP (stale)                               -- line 843
2. signal.tp_price = entry_price × (1 - tp_pct/100)                       -- line 986
3. signal.sl_price = entry_price × (1 + sl_pct/100)                       -- line 987
4. Order fills at different price                                          -- Dhan response
5. TP/SL levels remain based on stale entry_price                          -- never updated
```
**Problem:** For SELL trades, TP is below entry and SL is above entry. If the actual fill is lower than the snapshot price (common for SELL with slippage), the TP level is too aggressive (too low) and may not hit, or may hit prematurely based on system's wrong entry.  
**Example (ZFCVINDIA):**
- System TP = ₹14,401 (0.384% below ₹14,569)
- Correct TP should be = ₹14,373 (0.384% below actual fill ₹14,428)
- System triggered exit at ₹14,401 thinking +1.26%, but real return was only +0.31%  

**Fix:** Recompute `tp_price` and `sl_price` using actual fill price after order confirmation.

---

### 15. No minimum volume_rate filter for gap reversal candidates

**File:** `engine/src/poller.rs` ~line 827-870 (gap reversal SELL path)  
**Observed:** ZFCVINDIA selected with volume_rate = 11.9  

**Flow:**
```
1. Gap reversal checks: gap_pct > 0.1 ✓, pdd > 0 ✓, v2 not rejected ✓
2. No volume_rate check — stock enters pool regardless of liquidity
3. Cherry-pick selects by score (gap_pct × sell_pressure × mom_mult × 15)
4. ZFCVINDIA: price = ₹14,569, VR = 11.9, qty = 2 shares
5. Order placed → massive slippage: entry slip 0.97%, exit slip 0.12%
```
**Problem:** The gap reversal path has NO `volume_rate` filter (unlike the normal signal engine which has `min_vol_rate`). Low-liquidity stocks like ZFCVINDIA (VR=12) get selected, causing:
- Entry slippage > TP target (0.97% slip vs 0.384% TP)
- Dhan converts MARKET to LIMIT due to low liquidity
- Orders stuck in PENDING state (seen in logs: "still not terminal after all poll attempts")

**Fix:** Add `if l.volume_rate < sig_config.sell_min_vol_rate { None }` or a hardcoded minimum (e.g., VR >= 100) in the gap reversal candidate builder.

---

### 16. Dhan converts MARKET orders to LIMIT — causes pending orders and worse fills

**Observed:** Every order on April 1 and April 2  
**Evidence from Dhan API:**
```
Order placed as: orderType = "MARKET"
Dhan response:   orderType = "LIMIT", price = <some limit price>
```
**Problem:** Dhan's exchange gateway automatically converts MARKET orders to LIMIT for certain stocks (low liquidity, high volatility, or exchange rules). The LIMIT price is set at the best available price at order time, but by the time the order reaches the exchange, the price may have moved further, leaving the order PENDING.  
**Impact:**
- ZFCVINDIA entry: MARKET sent → converted to LIMIT at ₹14,428 → filled (lucky)
- DALMIASUG exit (April 1): MARKET sent → converted to LIMIT at ₹395.15 → stuck PENDING (price was ₹399), had to cancel and retry
- All exit orders show "still not terminal after all poll attempts" warnings

**Fix options:**
1. Use `validity: "IOC"` (Immediate or Cancel) instead of `"DAY"` for exit orders — fills what it can, cancels rest
2. Poll order status and if PENDING after 10s, cancel and re-place at current market price
3. For low-liquidity stocks, use LIMIT with a wider price band (e.g., entry ± 1%)

---

### 17. Candidate pool uses snapshot price from EARLIER bucket, not latest

**File:** `engine/src/poller.rs` ~line 820-909, `engine/src/cherry_pick.rs` ~line 36-37  
**Flow across buckets (entry window 2-6):**
```
Bucket 2: MSPL snapshot LTP = ₹30.86 → candidate created, entry_price = ₹30.86
Bucket 3: MSPL snapshot LTP = ₹31.50 → candidate OVERWRITTEN, entry_price = ₹31.50
Bucket 4: MSPL snapshot LTP = ₹31.19 → candidate OVERWRITTEN, entry_price = ₹31.19
Bucket 6: MSPL snapshot LTP = ₹30.93 → candidate OVERWRITTEN, entry_price = ₹30.93
           → Cherry-pick runs, selects MSPL with entry_price = ₹30.93
           → But actual market is already at ₹30.81 (bucket 7 level)
```
**Problem:** The pool correctly overwrites candidates at each bucket (line 36-37: `self.inner.insert(candidate.symbol.clone(), candidate)`), so the entry_price at selection time IS the latest snapshot. However, the snapshot itself is ~60-90 seconds old (stored during the previous poll cycle). Between snapshot capture and order execution, price moves.

**This is inherent to the 5-minute bucket design** — the entry_price will always be slightly stale. The real fix is #13 above (update to actual fill price).

---

## Recommended OPTIMIZE Insertion Points

| Location | After which operation | Table |
|----------|----------------------|-------|
| `watchlist.rs` after tier INSERT | `OPTIMIZE TABLE trading.tier_state FINAL` | tier_state |
| `watchlist.rs` after watchlist re-eval INSERT | `OPTIMIZE TABLE trading.watchlist FINAL` | watchlist |
| `daily_ref.rs` before SELECT in `store_closing_price()` | `OPTIMIZE TABLE trading.daily_ref FINAL` | daily_ref |
| `daily_ref.rs` before SELECT in `update_day_open()` | `OPTIMIZE TABLE trading.daily_ref FINAL` | daily_ref |
| `poller.rs` after seed completes, before direction reload | `OPTIMIZE TABLE trading.daily_ref FINAL` | daily_ref |
| `config.rs` after config INSERT | `OPTIMIZE TABLE trading.config FINAL` | config |
| `settings.rs` after setting INSERT | `OPTIMIZE TABLE trading.system_settings FINAL` | system_settings |
| `accounts.rs` after account INSERT | `OPTIMIZE TABLE trading.accounts FINAL` | accounts |
| `compute-win-rates.js` before verify | `OPTIMIZE TABLE trading.stock_win_rate FINAL` | stock_win_rate |

---

## Recommended Execution Fixes

| # | Issue | Impact | Fix | Priority |
|---|-------|--------|-----|----------|
| 13 | Entry price from snapshot, not fill | Wrong TP/SL, wrong P&L (₹79-278 error per trade) | Update entry_price from `averageTradedPrice` after fill | **P0** |
| 14 | TP/SL never recomputed after fill | Exits at wrong levels | Recompute TP/SL using actual fill price | **P0** |
| 15 | No volume_rate filter in gap reversal | Selects illiquid stocks, massive slippage | Add `min_vol_rate >= 100` check | **P0** |
| 16 | Dhan MARKET→LIMIT conversion | Pending orders, stuck exits | Use IOC validity for exits, retry logic | **P1** |
| 17 | Snapshot price ~60-90s stale | Entry price drift | Inherent to design; solved by #13 | **P2** |

---

## Alternative: Avoid OPTIMIZE overhead

`OPTIMIZE TABLE ... FINAL` forces a synchronous merge which can be slow on large tables. Alternatives:

1. **Return the written object directly** instead of re-reading from DB (for API PATCH endpoints)
2. **Use `SETTINGS mutations_sync=1`** for critical writes
3. **Combine partial inserts** into single full-row INSERT (daily_ref)
4. **In-memory cache** with DB as persistence (config, settings, accounts)
5. **Use `SELECT ... FINAL SETTINGS do_not_merge_across_partitions_select_final=1`** for faster FINAL reads
