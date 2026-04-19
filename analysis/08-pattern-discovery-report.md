# Pattern Discovery & Backtest Report

**Date**: 2026-03-31  
**Data**: 187,599 stock-day records across 78 trading days (2025-12-01 to 2026-03-24)  
**Stocks**: ~2,400 per day, 1-minute candle data (375 buckets per day)

---

## Files

| File | Purpose |
|------|---------|
| `data/candles-consolidated.ndjson` | Feb-Mar 2026 data (87,147 records, 2,438 symbols, 36 days) |
| `data/candles-consolidated_new.ndjson` | Dec 2025-Jan 2026 data (100,452 records, 2,407 symbols, 42 days) |
| `analysis/pattern_discovery.py` | Phase 1: pattern analysis — tests 80+ patterns, outputs `data/pattern_report.txt` |
| `analysis/backtest_sim.py` | Phase 2 v1: full backtest (HAS BUGS — see below) |
| `analysis/backtest_sim_v2.py` | Phase 2 v2: FIXED backtest with volume filter, correct capital, TP variants |
| `data/pattern_report.txt` | Raw pattern analysis output |
| `data/backtest_report.txt` | v1 backtest output (BUGGY — use v2) |
| `data/backtest_report_v2.txt` | v2 backtest output (CORRECTED) |

---

## Data Schema (per record in .ndjson)

```
{
  "symbol":      "ASIANHOTNR",
  "security_id": 16921,
  "date":        "2026-02-03",
  "dayOpen":     178.8,
  "gapPct":      6.4,
  "f5Range":     3.41,        // first 5-min price range (%)
  "f5Vol":       4087,        // first 5-min volume (shares)
  "maxUp20":     0,           // max upside in first 20 buckets (%)
  "maxDown20":   3.7,         // [OUTCOME — DO NOT use as feature!]
  "maxUp45/60":  ...,         // [OUTCOME — DO NOT use as feature!]
  "buckets": [
    {
      "b":  1,               // bucket number (1-indexed): 1 = 9:15 AM
      "o":  178.8,           // open
      "h":  178.8,           // high
      "l":  175.1,           // low
      "c":  178.69,          // close
      "v":  567,             // volume (shares)
      "vc": 567,             // cumulative volume
      "vw": 178.69,          // volume-weighted average price
      "vr": 9.45,            // volume ratio (vs some baseline)
      "br": 0.03             // buy ratio (0=all sell, 1=all buy)
    },
    ... // 375 buckets total (9:15 AM to 3:30 PM)
  ]
}
```

**LOOKAHEAD WARNING**: `maxUp20`, `maxDown20`, `maxUp45`, etc. are OUTCOME fields computed from future data. These MUST NEVER be used as features for signal generation.

---

## No-Lookahead Protocol

| Bucket (0-indexed) | Time | Role |
|---|---|---|
| 0 (b=1) | 9:15 AM | First data point |
| 0–5 (b=1–6) | 9:15–9:20 AM | Feature window (ONLY these used for signals) |
| 6 (b=7) | 9:21 AM | **Entry** — open price of this bucket |
| 44 (b=45) | 9:59 AM | BUY exit |
| 65 (b=66) | 10:20 AM | Current SELL exit |
| 89 (b=90) | 10:44 AM | New SELL exit (recommended) |

**Rule**: If signal fires at bucket 5 (9:20 AM), entry is at bucket 6 OPEN (9:21 AM). No data from bucket 6+ is used for the signal.

---

## Phase 1: Pattern Analysis (`pattern_discovery.py`)

### Method

1. Load all 187,599 records into numpy arrays
2. Compute vectorized features from buckets 0–5 only
3. For each pattern (boolean mask), compute:
   - **MFE** (max favorable excursion): best price in our favor from entry to exit
   - **MAE** (max adverse excursion): worst price against us
   - **Exit return**: close at exit bucket vs entry
   - **Win rate**: % of trades where MFE >= threshold
   - **Edge ratio**: MFE/MAE
   - **Expectancy**: avg return - 0.15% cost
4. Test across 7 exit points (b20, b30, b45, b60, b66, b75, b90)
5. Report best exit per pattern

### Features Used (all from buckets 0–5, no lookahead)

| Feature | Description | Computation |
|---|---|---|
| `gap_pct` | Gap from previous close | From record field `gapPct` |
| `b0_ret` | First candle return (%) | `(b0.close - b0.open) / b0.open × 100` |
| `b0_br` | First candle buy ratio | `b0.br` (0=all sell, 1=all buy) |
| `avg_br6` | Average buy ratio, buckets 0–5 | `mean(b0.br, b1.br, ..., b5.br)` |
| `or3_rng` | Opening range (%) of first 3 candles | `(max_high - min_low) / max_high × 100` for b0–b2 |
| `all2_red` / `all3_red` | Consecutive red candles | `b0.close < b0.open AND b1.close < b1.open ...` |
| `vwap_dev` | Price vs VWAP at bucket 5 | `(b5.close - b5.vwap) / b5.vwap × 100` |

### Key Findings

#### SELL Gap Reversal (improved)

**Gap threshold sensitivity** — higher gap = better reversal probability:

| Filter | Win>0.5% | AvgRet | Expect@b66 |
|---|---|---|---|
| gap>0.1% (current) | 60.8% | +0.159% | **+0.009%** |
| gap>1.0% | 67.7% | +0.329% | **+0.179%** |
| gap>1.5% | 69.8% | +0.431% | **+0.281%** |
| gap>2.0% | 70.3% | +0.510% | **+0.360%** |
| gap>3.0% | 76.0% | +0.700% | **+0.550%** |
| gap>5.0% | 80.8% | +0.871% | **+0.721%** |

**Best new filter: `tight_OR3 < 0.3%`** (first 3 candles have very narrow range):

| Pattern | Exit | Signals | Win>0.5% | AvgRet | Edge | Expect |
|---|---|---|---|---|---|---|
| gap>1% + tight_OR3<0.3% | b66 | 6,317 | **72.5%** | +0.905% | **2.71x** | **+0.755%** |
| gap>1% + tight_OR3<0.3% | b90 | 6,317 | **76.6%** | +1.008% | **2.77x** | **+0.858%** |
| gap>0.5% + tight_OR3<0.3% | b66 | 9,300 | 68.2% | +0.741% | 2.39x | +0.591% |

**Interpretation**: A gap-up stock with a tight first-3-minute range means no buying follow-through — exhaustion at the gap level. The coiling pattern breaks DOWN.

**Second best: `avg_br6 < 0.40`** (sellers dominate first 6 minutes):

| Pattern | Exit | Signals | Win>0.5% | Expect |
|---|---|---|---|---|
| gap>1% + avg_br6<0.40 | b66 | 18,011 | 68.9% | +0.359% |
| gap>3% + avg_br6<0.40 | b90 | 4,487 | 77.9% | +0.875% |

**Exit timing**: b90 (10:44 AM) consistently outperforms b66 (10:20 AM) for ALL sell patterns.

#### BUY Gap-Down Reversal (new)

| Pattern | Exit | Signals | Win>0.5% | AvgRet | Expect |
|---|---|---|---|---|---|
| gap<-3% + avg_br6>0.55 | b45 | 1,185 | **73.2%** | +0.502% | **+0.352%** |
| gap<-3% | b45 | 4,998 | 64.9% | +0.454% | +0.304% |
| gap<-5% | b66 | 1,362 | 74.4% | +0.548% | +0.398% |

**BUY exit timing**: b45 (9:59 AM) is optimal. The rebound peaks early and fades. At b66, expectancy drops significantly.

---

## Phase 2: Backtest Simulation

### v1 Bugs Found (`backtest_sim.py`)

| Bug | Description | Impact |
|---|---|---|
| **BUG-1: Combined ROC** | Strategy G (E+F) uses 8 sell + 8 buy = 16 positions needing ₹160k capital, but ROC divides by ₹80k | **2x overstatement** of combined ROC |
| **BUG-2: No volume filter** | Picks micro-cap stocks (ASIANHOTNR, SHEKHAWATI, etc.) with potentially < ₹50k daily volume. ₹50k position in illiquid stock = massive slippage | **Inflated returns** |
| **BUG-3: No gap cap** | Stocks gapping 15-20% may be at daily circuit limits (NSE 10%/20% circuit). Can't short stocks at upper circuit | **Some trades impossible** |
| **BUG-4: No TP variants** | Can't test take-profit impact on day win rate | Missing analysis |

### v2 Fixes (`backtest_sim_v2.py`)

- Combined ROC uses `sell_capital + buy_capital` as denominator
- Volume filter: `f5Vol × day_open ≥ ₹2,00,000`
- Gap capped at ±15%
- TP variants: E+TP@0.5%, E+TP@1.0%, G+TP for green-day optimization

---

## Volume Filter — COMPULSORY for ALL Strategies

### Why every strategy MUST have volume filter in live:

1. **Position execution**: ₹50k position (5x margin on ₹10k) in a stock with ₹50k first-5-min volume = your order IS the market. Entry slippage: 1-3%, not 0.15%.

2. **Exit execution**: Gap reversal means selling into a falling stock (for sell) or buying a recovering stock (for buy). Illiquid stocks have wide bid-ask spreads during reversals.

3. **T2T/ASM/GSM stocks**: Many micro-caps with extreme gaps (10%+) are in restricted trading categories on NSE/BSE:
   - T2T (trade-to-trade): can't do intraday, must take delivery
   - ASM (Additional Surveillance Measure): reduced circuit limits
   - GSM (Graded Surveillance Measure): restricted trading
   
4. **Circuit filters**: NSE small-caps typically have 10% or 20% daily circuit limits. A stock at +15% gap could be near upper circuit = no sellers to fill your short.

### Minimum threshold

```
f5Vol (shares) × entry_price (₹) ≥ ₹2,00,000

Example: 
  Stock A: f5Vol=4000, price=₹180 → ₹7.2L ✓ (tradeable)
  Stock B: f5Vol=200,  price=₹50  → ₹10k  ✗ (illiquid)
```

For ₹50k position, this gives ~4:1 volume-to-position ratio (minimum). In practice, ₹5L+ is safer.

### Which strategies are MOST affected by volume filter:

| Strategy | Typical stocks picked | Volume sensitivity |
|---|---|---|
| A (gap>0.1%) | Top 8 gaps = extreme micro-caps | **VERY HIGH** — most inflated |
| B-E (gap>1.5%+) | Still skews micro-cap but less extreme | **HIGH** |
| F (BUY gap<-2%) | Gap-down stocks with buying = may have decent volume | **MODERATE-HIGH** |

---

## Path to 90-95% Green Days

### Corrected Reality

With volume filter applied (realistic scenario):
- **A2 (current + vol): 56.6% active day win rate** — FAR from 90%
- **A (no vol): 81.3%** — but trades illiquid stocks, not achievable in live

The 80%+ day win rate in v1 was from trading illiquid micro-caps where reversals are stronger. With volume filter, we're trading mid-caps where reversal is weaker.

### v2 Green-Day Test Results

| Strategy | Day Win | Total ROC | Tradeoff |
|---|---|---|---|
| E (best sell + vol) | 37.0% | 4.1% | Too few trades (1-2/day) |
| E + TP@0.5% | 48.1% | **-8.4%** | TP doesn't help — losses come from cost |
| E + TP@1.0% | 51.9% | 4.1% | Marginal improvement |
| G (E+F combined) | 44.7% | 11.2% (on 160k) | Diversification barely helps |

**TP does NOT help reach 90% green days.** The problem isn't that winners don't lock profit — it's that the WIN RATE per trade is low (29-55%) on liquid stocks.

### Why 90% Green Days Is Hard

1. **Gap reversal on liquid stocks** is a ~55% edge, not 65%+
2. **Market regime risk** — on broad bullish days, ALL sell gap-reversal fails simultaneously
3. **Limited trades per day** — with strict filters + volume, only 1-5 trades/day → insufficient diversification
4. **Cost friction** — 0.15% round-trip on a 1% avg move is 15% of profits

### Realistic Paths Forward (not tested yet)

**Option 1: Volume-weighted scoring (most promising)**
- Instead of `score = abs(gap)`, use `score = abs(gap) × sqrt(f5vol_rs / 200000)`
- This picks stocks that balance gap size with liquidity
- Avoids the "always pick the most illiquid" problem

**Option 2: Lower volume threshold + smaller position**
- If live uses ₹10k positions (not ₹50k), volume requirement drops to ₹50k
- More stocks qualify, including the ones where patterns work
- Tradeoff: smaller position = less absolute profit per trade

**Option 3: Market breadth filter (skip bad days)**
- At 9:15 AM, compute % of all 2,400 stocks with green first candle
- If > 65% green → market is strongly bullish → SKIP sell trades that day
- Can be computed from existing data, no lookahead
- Would reduce trades but avoid the biggest losing days

**Option 4: Different timeframe / approach entirely**
- Gap reversal in liquid stocks may need longer exit (2-3 hours, not 1 hour)
- Or: ORB (opening range breakout) instead of reversal — works better on liquid stocks
- These need separate analysis

---

## CORRECTED v2 Backtest Results (volume filter applied)

### Summary Table (CORRECTED)

| Strategy | Cap | TotROC | MaxDD | Sharpe | DayWin | TrdWin | Trades |
|---|---|---|---|---|---|---|---|
| **A: Current (no vol)** | 80k | 372.2% | 9.0% | 13.92 | 81.3% | 64.3% | 583 |
| **A2: Current + vol** | 80k | **136.6%** | 16.5% | 6.00 | **56.6%** | 54.8% | 588 |
| B: tight_OR3 + vol | 80k | 5.6% | 7.7% | 0.93 | 37.5% | 31.7% | 126 |
| C: avg_br<0.40 + vol | 80k | 52.7% | 14.0% | 3.41 | 59.2% | 44.9% | 577 |
| D: b0_br<0.30 + vol | 80k | 84.5% | 11.7% | 4.04 | 54.7% | 46.4% | 513 |
| E: tight+br combo + vol | 80k | 4.1% | 7.8% | 0.69 | 37.0% | 29.2% | 113 |
| F: BUY + vol | 80k | 18.3% | 38.9% | 1.06 | 48.0% | 48.2% | 525 |
| G: Combined (E+F) | **160k** | 11.2% | 15.9% | 1.29 | 44.7% | 44.8% | 638 |
| E+TP@0.5% | 80k | -8.4% | 9.6% | -2.34 | 48.1% | 43.4% | 113 |
| E+TP@1.0% | 80k | 4.1% | 5.5% | 0.99 | 51.9% | 40.7% | 113 |

### CRITICAL FINDING: Volume Filter Kills the New Patterns

| Metric | Without vol | With vol (₹2L) | Change |
|---|---|---|---|
| ROC (current strat) | 372.2% | 136.6% | **-63.3%** |
| Trade win rate | 64.3% | 54.8% | -9.5pp |
| Trades | 583 | 588 | +5 |

**The tight_OR3 filter was an ILLIQUIDITY ARTIFACT:**
- Without volume: tight_OR3 → 618.5% ROC (amazing)
- With volume: tight_OR3 → 5.6% ROC, 31.7% win (terrible)
- Reason: "tight first-3-candle range" = "nobody is trading this stock" → not a real signal

**Why illiquid stocks have better reversal:**
- Micro-caps gap 10-20% on retail euphoria (not institutional conviction)
- The euphoria exhausts in 5 minutes → reversal is strong and reliable
- But you CAN'T trade ₹50k positions in these stocks (you ARE the market)

### What Actually Works With Volume

1. **Current strategy (A2)** = BEST at 136.6% ROC, 56.6% day win
2. **D (gap>2% + b0_br<0.30)** = 84.5% ROC — b0_br filter has SOME value
3. **C (gap>1.5% + avg_br<0.40)** = 52.7% ROC — marginal improvement over random
4. **BUY (F)** = 18.3% ROC — barely profitable, 38.9% drawdown

### Honest Assessment

The v1 analysis found REAL patterns — but those patterns exist in untradeable stocks. For liquid stocks, the gap reversal effect is weaker and harder to improve on.

---

## Recommended Live Config (CORRECTED after v2)

**Major correction from v1: the new filters (tight_OR3, avg_br6) do NOT improve returns when volume filter is applied. The current strategy with volume filter is the best tested approach.**

### SELL (keep current, add volume + gap cap):

```
SELL:
  gap_min:          0.1%     (KEEP current — raising threshold doesn't help with vol filter)
  gap_max:          15.0%    (NEW: avoid circuit-limit stocks)
  f5vol_min_rs:     200000   (NEW: ₹2L min first-5-min volume — CRITICAL)
  exit_bucket:      66       (KEEP current — or test 90 in live)
  tight_or3:        OFF      (was artifact of illiquidity, drops to 5.6% ROC with vol)
  avg_br6:          OFF      (drops to 52.7% ROC with vol vs 136.6% without)
  max_positions:    8
  capital_per:      10000
  margin:           5x
```

### BUY (experimental — marginal edge):

```
BUY:
  gap_max:          -2.0%
  gap_min:          -15.0%
  avg_br6_min:      0.55
  f5vol_min_rs:     200000
  exit_bucket:      45       (9:59 AM)
  Status:           EXPERIMENTAL — only 18.3% ROC, 38.9% drawdown with vol
```

### What needs to change in the Rust engine

1. Add `f5vol_rs` check: `f5Vol × day_open >= 200000` (only change NEEDED now)
2. Add `gap_max` cap on sell side (safety: avoid 15%+ gap stocks)
3. Exit bucket can be tested at 90 (10:44 AM) but 66 is fine

### NEXT STEPS (not yet done)

1. **Volume-weighted scoring**: `score = gap × sqrt(f5vol_rs / MIN_VOL)` instead of pure gap ranking — most promising untested improvement
2. **Market breadth filter**: skip sell trades when broad market is bullish (bucket 0 analysis)
3. **Analysis on liquid-only data**: re-run pattern_discovery.py with pre-filtered volume>₹2L data to find what ACTUALLY works on tradeable stocks
4. **Position-size aware volume threshold**: if live uses ₹10k positions (not ₹50k with margin), volume threshold can be lower → more stocks qualify → different results
