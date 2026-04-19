# GAP REVERSAL MODE: Deep Analysis Methodology & Findings — 2026-03-29 

## Context
- **Goal**: Find 2%+ daily ROC on Rs 500K capital with 5x margin
- **Data**: 700 stocks, 80 trading days (Dec 2025 - Mar 2026), 1-minute candles
- **Market regime**: Bearish (Dec 2025 - Mar 2026) — SELL outperformed BUY

---

## Phase 1: Feature Matrix (Raw ClickHouse Query)

Built a feature matrix of 123,435 trade candidates directly from ClickHouse snapshots — bypassing the signal engine entirely.

### Query
```sql
WITH
  entry AS (
    SELECT trading_date, symbol,
      argMin(ltp, bucket) as open_ltp,
      argMax(ltp, bucket) as entry_ltp,
      max(bucket) as entry_bucket,
      sum(volume_delta) as entry_vol,
      avg(volume_rate) as avg_vol_rate,
      max(volume_rate) as max_vol_rate,
      avg(vwap) as entry_vwap,
      avg(candle_body_ratio) as avg_body,
      max(candle_high) - min(candle_low) as entry_range,
      count() as entry_bars
    FROM trading.snapshots
    WHERE trading_date >= toDate('{FROM}') AND trading_date <= toDate('{TO}')
      AND bucket >= 1 AND bucket <= 4
    GROUP BY trading_date, symbol
    HAVING entry_bars >= 3 AND entry_ltp > 0 AND open_ltp > 0
  ),
  post AS (
    SELECT trading_date, symbol,
      max(candle_high) as post_high,
      min(candle_low) as post_low,
      argMax(ltp, bucket) as last_ltp,
      max(bucket) as last_bucket
    FROM trading.snapshots
    WHERE trading_date >= toDate('{FROM}') AND trading_date <= toDate('{TO}')
      AND bucket >= 5 AND bucket <= 80
    GROUP BY trading_date, symbol
  ),
  gaps AS (
    -- ASOF JOIN to get gap_pct (today open vs prev day close)
    SELECT toString(t.trading_date) as td, t.symbol as sym,
      toFloat32(if(p.day_close > 0, (t.day_open - p.day_close) / p.day_close * 100, 0)) as gap_pct
    FROM (
      SELECT trading_date, symbol, argMin(ltp, bucket) as day_open
      FROM trading.snapshots
      WHERE trading_date >= toDate('{FROM}') - 10 AND trading_date <= toDate('{TO}')
      GROUP BY trading_date, symbol
    ) t
    ASOF LEFT JOIN (
      SELECT trading_date, symbol, argMax(ltp, bucket) as day_close
      FROM trading.snapshots
      WHERE trading_date >= toDate('{FROM}') - 10 AND trading_date <= toDate('{TO}')
      GROUP BY trading_date, symbol
    ) p ON t.symbol = p.symbol AND t.trading_date > p.trading_date
  ),
  prev_day AS (
    SELECT trading_date, symbol,
      (argMax(ltp, bucket) - argMin(ltp, bucket)) / argMin(ltp, bucket) * 100 as prev_day_range_pct,
      if(argMax(ltp, bucket) > argMin(ltp, bucket), 1, -1) as prev_day_dir
    FROM trading.snapshots
    WHERE trading_date >= toDate('{FROM}') - 10 AND trading_date <= toDate('{TO}')
      AND bucket >= 1 AND bucket <= 80
    GROUP BY trading_date, symbol
  )
SELECT
  toString(e.trading_date) as trading_date, e.symbol,
  e.entry_ltp as entry_price,
  (e.entry_ltp - e.open_ltp) / e.open_ltp * 100 as move_pct,
  e.entry_vol, e.avg_vol_rate, e.max_vol_rate, e.avg_body,
  e.entry_range / e.open_ltp * 100 as range_pct,
  if(e.entry_vwap > 0, (e.entry_ltp - e.entry_vwap) / e.entry_vwap * 100, 0) as vwap_dist_pct,
  g.gap_pct,
  pd.prev_day_range_pct, pd.prev_day_dir,
  if((e.entry_ltp - e.open_ltp) > 0, 'BUY', 'SELL') as direction,
  -- MFE/MAE (outcome, post-entry)
  if(direction = 'BUY',
    (p.post_high - e.entry_ltp) / e.entry_ltp * 100,
    (e.entry_ltp - p.post_low) / e.entry_ltp * 100) as mfe_pct,
  if(direction = 'BUY',
    (e.entry_ltp - p.post_low) / e.entry_ltp * 100,
    (p.post_high - e.entry_ltp) / e.entry_ltp * 100) as mae_pct,
  if(direction = 'BUY',
    (p.last_ltp - e.entry_ltp) / e.entry_ltp * 100,
    (e.entry_ltp - p.last_ltp) / e.entry_ltp * 100) as time_exit_ret
FROM entry e
JOIN post p ON e.trading_date = p.trading_date AND e.symbol = p.symbol
LEFT JOIN gaps g ON toString(e.trading_date) = g.td AND e.symbol = g.sym
LEFT JOIN prev_day pd ON e.trading_date - 1 = pd.trading_date AND e.symbol = pd.symbol
WHERE abs(move_pct) >= 0.1
FORMAT JSONEachRow
```

**All features are known at entry time (bucket 1-4). MFE/MAE/time_exit_ret are outcomes used ONLY for evaluation, not for stock selection.**

---

## Phase 2: Feature Importance Analysis

For each feature, split data at 50th, 75th, 90th percentile and compare HIGH vs LOW group outcomes.

### Results (Dec 2025 - Mar 2026)

| Feature | Threshold | HIGH avg return | LOW avg return | Lift | Signal |
|---|---|---|---|---|---|
| **abs_gap_pct** | 2.50% | +0.088% | -0.015% | **+0.103%** | **STRONGEST** |
| **abs_gap_pct** | 1.38% | +0.038% | -0.019% | **+0.057%** | Strong |
| **avg_vol_rate** | 21.22 | +0.025% | -0.034% | **+0.059%** | Strong |
| **avg_body** | 0.50 | +0.019% | -0.027% | +0.046% | Moderate |
| **max_vol_rate** | 43.43 | +0.018% | -0.026% | +0.044% | Moderate |
| abs_move_pct | 0.47% | -0.038% | +0.029% | **-0.066%** | **INVERSE** (big movers = exhausted) |
| abs_vwap_dist | 0.32% | -0.037% | +0.029% | **-0.066%** | **INVERSE** |
| range_pct | 1.37% | -0.021% | +0.012% | -0.033% | Inverse |
| gap_aligned | 1.0 | -0.049% | +0.032% | **-0.081%** | **Gap continuation LOSES** |
| prev_day_aligned | 1.0 | -0.010% | -0.001% | -0.010% | Weak |

### Key Discoveries

1. **Big gap = best predictor** (+0.103% lift at |gap| > 2.5%)
2. **Gap continuation LOSES money** (-0.081% lift) — gap REVERSAL is profitable
3. **Big movers are exhausted** — stocks that moved >0.47% in 3 min tend to reverse
4. **Volume rate is a positive signal** — institutional activity
5. **VWAP distance is INVERSE** — far from VWAP = overextended = reversal

---

## Phase 3: Exhaustive Filter + Ranking Grid Search

### Script: `deploy/deep-pattern-search.js`

Tested **490,328 combinations** across these dimensions:

**Filters tested:**
- Direction: SELL, BUY, BOTH
- Min move: 0.1%, 0.2%, 0.3%, 0.5%, 0.7%
- Min vol rate: 0, 100, 200, 500
- Gap filters: no filter, gap down, gap up, big gap (>2%), small gap (<1%)
- VWAP filters: no filter, VWAP aligned, VWAP far (>0.3%)
- Momentum filters: no filter, same direction as prev day, reversal from prev day, strong prev day (>1.5%)
- Body filters: no filter, strong body (>0.6)

**Ranking methods tested (ONLY entry-time data, NO future data):**
- volRate: rank by max volume rate
- absMove: rank by |move %|
- volXmove: rank by volume_rate * |move %|
- gapSize: rank by |gap %|
- entryVol: rank by entry volume
- vwapDist: rank by |VWAP distance|

**TP/SL combinations:**
- TP: 0, 0.5, 0.7, 1.0, 1.5, 2.0%
- SL: 0, 0.3, 0.5, 0.7, 1.0%

**Position sizes:** 3, 5, 8, 12

### Simulation Method
For each candidate: if MFE >= TP, score as TP hit. If MAE >= SL, score as SL hit. If both, estimate which hit first using MFE/MAE ratio vs TP/SL ratio. Otherwise, use TIME exit return.

---

## Phase 4: Results

### Top 10 Configs (Bearish Market Dec 2025 - Mar 2026)

| # | Config | Win% | Avg ROC/day | P&L | Green Days |
|---|---|---|---|---|---|
| 1 | **SELL gapUp reversalMom rank=gapSize TP=2 SL=0.7 pos=3** | 55% | **+3.48%** | +121K | 49/58 (84%) |
| 2 | SELL gapUp+vwapAligned reversalMom rank=gapSize TP=2 SL=0.7 pos=3 | 53% | +3.34% | +116K | 48/58 (83%) |
| 3 | SELL gapUp reversalMom rank=gapSize TP=2 SL=1 pos=3 | 59% | +3.32% | +115K | 40/58 (69%) |
| 4 | SELL gapUp+vwapAligned reversalMom rank=gapSize TP=2 SL=1 pos=3 | 58% | +3.25% | +113K | 40/58 (69%) |
| 5 | SELL mv>=0.3 gapUp reversalMom rank=gapSize TP=2 SL=0.7 pos=3 | 51% | +3.09% | +107K | 49/58 (84%) |
| 6 | SELL gapUp strongPrev rank=gapSize TP=2 SL=0.7 pos=3 | 52% | +3.08% | +107K | 47/58 (81%) |
| 7 | SELL gapUp+vwapAligned noMom rank=gapSize TP=2 SL=0.7 pos=3 | 50% | +2.95% | +140K | 70/79 (89%) |
| 8 | SELL gapUp noMom rank=gapSize TP=2 SL=0.7 pos=3 | 49% | +2.92% | +138K | 69/79 (87%) |
| 9 | SELL noGap reversalMom rank=gapSize TP=2 SL=0.7 pos=3 | 51% | +2.84% | +100K | 48/59 (81%) |
| 10 | SELL mv>=0.3 gapUp noMom rank=gapSize TP=2 SL=0.7 pos=3 | 49% | +2.84% | +134K | 70/79 (89%) |

### Summary Stats
- **1,300 configs >= 2% daily ROC** (all honest, no future data)
- **26,764 configs >= 1% daily ROC**
- **370,669 profitable configs** out of 490,328 tested

### Worst Configs (avoid)
All BUY + TP score scaling combos were the worst performers (-0.7% to -0.9% daily ROC).

---

## Phase 5: The Winning Pattern Explained

### Gap Reversal = Mean Reversion on Exhausted Stocks

The pattern: **SELL stocks that gapped UP after an UP day**

1. **Previous day: stock went UP** (uptrend)
2. **Today: stock gaps UP** (overnight continuation — more buying)
3. **First 3 minutes: price starts falling** (SELL direction fires)
4. **Signal: exhaustion** — the stock has been going up too long, institutions take profit at the open
5. **Bigger the gap = stronger the reversal** (hence rank by gap size)

### Why SELL Dominated in This Period

The Dec 2025 - Mar 2026 period was bearish. In a bear market:
- Stocks that gap up are "dead cat bounces" — they fade
- SELL gap-reversal catches these fades perfectly
- BUY signals tend to fail because the overall trend is down

### Why It Might Change in a Bull Market

In a bull market (sustained uptrend):
- Stocks that gap DOWN after a DOWN day are "exhaustion" for the bears
- **BUY gap-reversal** (buy gap-down stocks where prev day was DOWN) should work
- The same mean-reversion logic applies, just inverted

---

## How to Re-Run This Analysis for Bull Market

### Step 1: Run the feature matrix query
```bash
node deploy/deep-pattern-search.js
```
Change `FROM` and `TO` dates to the new period.

### Step 2: Look at Feature Importance
Check if the same patterns hold:
- Does `abs_gap_pct` still have the highest lift?
- Does `gap_aligned` still have NEGATIVE lift? (if positive, trend-following works better)
- Does `prev_day_aligned` show different behavior?

### Step 3: Check Top Configs by Direction
If BUY configs start dominating the top 10, the market is bullish and you should:
1. Enable **BUY gap-reversal** (gap DOWN + prev day DOWN + BUY direction)
2. Or switch to **gap continuation** (gap UP + prev day UP + BUY direction)

### Step 4: Key Metrics to Watch Live
- If SELL gap-reversal win rate drops below 45% for 5+ consecutive days → market regime changing
- If BUY signals start winning > 55% → bull market starting
- If both directions lose → choppy/sideways market, reduce position size

---

## For Bull Market: What to Change

### Option A: BUY Gap Reversal
Pattern: BUY stocks that gapped DOWN after a DOWN day (bears exhausted)
- Filter: `gap_pct < -0.1 AND prev_day_dir < 0`
- Direction: BUY
- Ranking: |gap_pct| descending (biggest gap down = strongest reversal)
- TP: 2%, SL: 0.7%, 3 positions

### Option B: BUY Gap Continuation
Pattern: BUY stocks that gapped UP with strong momentum
- Filter: `gap_pct > 1.0 AND max_vol_rate > 200`
- Direction: BUY
- Ranking: gap_pct * vol_rate
- TP: 1.5%, SL: 0.5%, 5 positions

### Option C: Auto-detect regime
Run the deep search weekly on the last 20 trading days. If the top configs shift from SELL to BUY, switch the live system.

---

## Scripts Reference

| Script | Purpose |
|---|---|
| `deploy/deep-pattern-search.js` | Full grid search (490K combos) |
| `deploy/optimize-backtest.js` | Quick grid search via Rust API (180 combos) |
| `deploy/analyze-no-lookahead.js` | Feature importance (single features) |
| `deploy/analyze-gap-plus-day-pattern.js` | Gap + day movement patterns |

---

## Critical Warnings

1. **MFE/MAE ranking uses FUTURE data** — never use `mfeMae` as a ranker. It shows 100% win rate because it knows the outcome.
2. **The deep search script's simulation is approximate** — it estimates TP/SL timing from MFE/MAE. The Rust backtest's bucket-by-bucket simulation is more accurate. Always verify top configs in the backtest UI.
3. **Overfitting risk** — 490K combos tested on 80 days. The top config might be overfit. Check consistency: does it work on Dec only AND Jan-Mar separately?
4. **Market regime** — SELL dominance = bearish. This WILL change. Re-run analysis monthly.
