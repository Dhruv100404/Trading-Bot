# Intraday Pattern Analysis Report
**Date:** 2026-03-24
**Data Range:** 2025-12-29 to 2026-03-24 (59 trading days)
**Universe:** 206 NSE F&O equity stocks with complete data (40+ days, 60+ rows/day)

---

## Phase 1: Data Overview

### Q1: Database Summary
| Metric | Value |
|--------|-------|
| Total rows | 1,006,554 |
| Date range | 2025-12-29 to 2026-03-24 |
| Distinct symbols (all) | 2,439 |
| Distinct symbols (good data) | **206** |
| Trading days | 59 |
| Avg rows per symbol per day | ~76.6 |

### Q2-3: Data Quality
- 206 symbols have complete data (59 days, ~76.6 rows/day).
- 2,233 additional symbols have sparse/partial data (often just 1 day with 34 rows). These are excluded from all analysis.
- `daily_ref` table has exactly 12,153 rows (206 symbols x 59 days) -- perfect coverage.

### Data Gaps (Fields with No/Partial Data)
| Field | Status |
|-------|--------|
| oi_delta, oi_total | **All zeros** across all buckets -- OI data not available |
| spread_pct | **All zeros** except bucket 76 (206 rows at end of day) |
| bid, ask, bid_qty, ask_qty | **All zeros** except bucket 76 |
| volume_rate | Partial: ~18% of rows at bucket 2, ~41% at bucket 5, ~49% at bucket 30 |
| price_velocity | Available but mostly small values (|avg| < 0.08) |

**Implication:** OI-based signals (oiDir, oiSpike, oiAcc) in the current scoring system are scoring 0 for all entries. The "max 10" score is effectively max 7. Bid/ask imbalance analysis (Q40) is not possible.

---

## Phase 2: Price Movement Analysis

### Q4: Average Absolute Move from Open by Bucket

| Bucket | Time | Avg Abs Move (%) | Avg Signed Move (%) | Max Abs Move (%) |
|--------|------|-------------------|---------------------|------------------|
| 1 | 9:15 | 0.000 | 0.000 | 0.00 |
| 2 | 9:16 | 0.222 | -0.005 | 2.11 |
| 3 | 9:17 | 0.299 | +0.001 | 3.89 |
| 4 | 9:18 | 0.346 | +0.003 | 5.59 |
| 5 | 9:19 | 0.379 | -0.001 | 4.73 |
| 10 | 9:24 | 0.497 | -0.035 | 5.97 |
| 15 | 9:29 | 0.571 | -0.053 | 7.92 |
| 20 | 9:34 | 0.616 | -0.031 | 9.04 |
| 25 | 9:39 | 0.648 | -0.063 | 8.10 |
| 30 | 9:44 | 0.677 | -0.080 | 9.09 |

**Interpretation:**
- Moves grow rapidly in first 5 minutes, then slow down significantly.
- Slight negative bias in average signed move (market tends to drift down from open in this period).
- By bucket 5, the average stock has moved 0.38% from open.

### Q5: Distribution of Moves by Bucket

| Bucket | Time | >= 0.5% | >= 0.7% | >= 1.0% | >= 1.5% | >= 2.0% |
|--------|------|---------|---------|---------|---------|---------|
| 2 | 9:16 | 9.0% | 3.5% | 0.9% | 0.2% | 0.0% |
| 3 | 9:17 | 17.7% | 8.3% | 2.8% | 0.5% | 0.1% |
| 4 | 9:18 | 22.9% | 12.1% | 4.6% | 0.9% | 0.3% |
| 5 | 9:19 | 26.5% | 14.5% | 5.9% | 1.4% | 0.4% |
| 10 | 9:24 | 37.7% | 23.4% | 11.9% | 4.1% | 1.5% |
| 15 | 9:29 | 44.5% | 29.9% | 16.2% | 5.7% | 2.0% |
| 20 | 9:34 | 47.4% | 33.6% | 19.0% | 7.1% | 2.6% |
| 30 | 9:44 | 51.3% | 37.6% | 22.5% | 9.2% | 3.9% |

**Interpretation:** At the current 0.7% min_move threshold, only 3.5% of stock-days qualify at bucket 2, growing to 14.5% by bucket 5. This is a reasonable filter that captures the most volatile moves.

### Q6: Continuation Analysis (After 0.7% Move)

For stocks that have already moved >= 0.7% from open by a given bucket, what happens next?

| Entry Bucket | Signals | Avg Max Favorable (%) | Avg Max Adverse (%) | Continue 0.7%+ | Continue 1.0%+ | Reverse to -0.6% |
|-------------|---------|----------------------|--------------------:|---------------:|---------------:|------------------:|
| 2 | 424 | 0.894 | -0.054 | 50.0% | 37.0% | 24.3% |
| 3 | 1,012 | 0.774 | +0.004 | 46.0% | 31.4% | 19.0% |
| 4 | 1,472 | 0.672 | -0.014 | 41.3% | 24.5% | 20.5% |
| 5 | 1,757 | 0.647 | +0.003 | 37.8% | 22.4% | 19.7% |
| 6 | 2,181 | 0.570 | +0.019 | 32.8% | 19.7% | 19.3% |
| 8 | 2,610 | 0.536 | +0.042 | 29.3% | 17.7% | 16.9% |
| 10 | 2,846 | 0.504 | +0.073 | 27.7% | 14.6% | 14.8% |

**KEY FINDING:** Earlier entries have significantly better continuation rates. At bucket 2, 50% of signals continue another 0.7%+. By bucket 5, this drops to 37.8%. This strongly supports keeping the entry window at buckets 2-3 (not 2-5).

### Q7/Q8: Peak Move Timing

For stocks that eventually move >= 1% from open, when does the peak occur?

| Peak Window | Count | Percentage | Avg Peak Move |
|------------|-------|------------|---------------|
| Bucket 1-5 (9:15-9:19) | 108 | 1.4% | 1.34% |
| Bucket 6-10 (9:20-9:24) | 406 | 5.4% | 1.50% |
| Bucket 11-15 (9:25-9:29) | 443 | 5.9% | 1.56% |
| Bucket 16-20 (9:30-9:34) | 386 | 5.2% | 1.60% |
| Bucket 21-30 (9:35-9:44) | 820 | 11.0% | 1.62% |
| Bucket 31-46 (9:45-10:00) | 1,256 | 16.8% | 1.74% |
| Bucket 47-76 (10:01-11:00) | 4,041 | **54.2%** | 1.81% |

**Interpretation:** 54% of peak moves occur after 10 AM. This means our current hard exit at bucket 31 (9:45) is cutting off potential gains early. However, the risk of reversal also increases with time -- the earlier TP/SL approach is safer.

---

## Phase 3: Entry Timing Optimization

### Q9: Win Rate by Entry Bucket (TP=0.7%, SL=0.6%, Hard Exit=Bucket 31)

| Entry Bucket | Time | Trades | Win Rate | SL Rate | Time Exit | Avg Return |
|-------------|------|--------|----------|---------|-----------|------------|
| **2** | **9:16** | **424** | **47.6%** | **39.2%** | **13.2%** | **+0.099%** |
| **3** | **9:17** | **1,012** | **44.9%** | **35.0%** | **20.2%** | **+0.104%** |
| 4 | 9:18 | 1,472 | 40.2% | 37.5% | 22.3% | +0.057% |
| 5 | 9:19 | 1,757 | 36.4% | 36.8% | 26.8% | +0.034% |
| 6 | 9:20 | 2,181 | 31.9% | 38.3% | 29.8% | -0.007% |
| 7 | 9:21 | 2,428 | 29.7% | 37.1% | 33.2% | -0.014% |
| 8 | 9:22 | 2,610 | 27.1% | 34.8% | 38.0% | -0.019% |
| 9 | 9:23 | 2,760 | 25.2% | 33.6% | 41.2% | -0.025% |
| 10 | 9:24 | 2,846 | 24.2% | 31.2% | 44.6% | -0.018% |

**KEY FINDING:** Entries at bucket 2-3 are significantly more profitable than later entries. After bucket 5, returns go negative. **Optimal entry window is bucket 2-3 (9:16-9:17).**

### Q10: Hard Exit Bucket Comparison (Entry Buckets 2-5)

| Hard Exit | Entry 2 Win/Ret | Entry 3 Win/Ret | Entry 4 Win/Ret | Entry 5 Win/Ret |
|-----------|----------------|----------------|----------------|----------------|
| Bucket 31 (9:45) | 47.6% / +0.099% | 44.9% / +0.104% | 40.2% / +0.057% | 36.4% / +0.034% |
| Bucket 46 (10:00) | 49.5% / +0.102% | 47.9% / +0.104% | 42.9% / +0.045% | 40.1% / +0.030% |
| Bucket 76 (11:00) | 51.7% / +0.103% | 50.3% / +0.098% | 45.7% / +0.048% | 44.2% / +0.031% |

**Interpretation:** Extending the hard exit window slightly improves win rates (more time for TP to hit) but returns stay similar because more time also means more SL hits. Bucket 31 is a reasonable compromise.

### Q12: BUY vs SELL Breakdown (TP=0.7%, SL=0.6%, Hard Exit=31)

| Entry | Direction | Trades | Win Rate | Avg Return |
|-------|-----------|--------|----------|------------|
| 2 | BUY | 188 | 36.7% | -0.034% |
| 2 | **SELL** | **236** | **56.4%** | **+0.204%** |
| 3 | BUY | 469 | 36.7% | +0.019% |
| 3 | **SELL** | **543** | **51.9%** | **+0.178%** |
| 4 | BUY | 739 | 33.8% | -0.008% |
| 4 | SELL | 733 | 46.7% | +0.121% |
| 5 | BUY | 845 | 32.3% | -0.011% |
| 5 | SELL | 912 | 40.2% | +0.076% |

**CRITICAL FINDING:** SELL signals massively outperform BUY signals across all entry buckets. BUY signals at bucket 2-3 are barely profitable (+0.019% at best). SELL signals at bucket 2-3 have 52-56% win rates and +0.18-0.20% average returns.

This is likely because stocks that gap up and then fall from open (triggering SELL) exhibit mean reversion, while stocks that gap down and rise from open (triggering BUY) are more likely to be noise/dead cat bounces.

---

## Phase 4: Exit Optimization

### Q13: Take Profit Sweep (All Signals, Entry 2-5, SL=0.6%, Hard Exit 31)

| TP (%) | Trades | Win Rate | SL Rate | Avg Return (approx) | Total P&L |
|--------|--------|----------|---------|---------------------|-----------|
| 0.3 | 4,665 | 64.9% | 28.9% | +0.021% | +99.0 |
| 0.4 | 4,665 | 58.8% | 31.8% | +0.045% | +207.6 |
| 0.5 | 4,665 | 52.2% | 34.0% | +0.057% | +267.6 |
| **0.6** | **4,665** | **46.3%** | **35.4%** | **+0.066%** | **+306.0** |
| 0.7 | 4,665 | 40.5% | 36.8% | +0.062% | +290.8 |
| 0.8 | 4,665 | 35.4% | 37.5% | +0.058% | +272.0 |
| 0.9 | 4,665 | 30.8% | 38.2% | +0.049% | +226.2 |
| 1.0 | 4,665 | 26.4% | 38.5% | +0.033% | +152.2 |
| 1.2 | 4,665 | 19.0% | 39.1% | -0.006% | -30.0 |

**Optimal TP = 0.6%** (not 0.7%). TP=0.6% gives the highest total P&L. TP above 1.2% becomes loss-making.

### Q14: Stop Loss Sweep (All Signals, Entry 2-5, TP=0.6%, Hard Exit 31)

| SL (%) | Trades | Win Rate | SL Rate | Avg Return | Total P&L |
|--------|--------|----------|---------|------------|-----------|
| 0.2 | 4,665 | 37.9% | 58.2% | +0.111% | +516.4 |
| 0.3 | 4,665 | 41.3% | 51.9% | +0.092% | +429.6 |
| 0.4 | 4,665 | 43.6% | 46.2% | +0.077% | +358.6 |
| 0.5 | 4,665 | 45.3% | 40.4% | +0.070% | +324.6 |
| 0.6 | 4,665 | 46.3% | 35.4% | +0.066% | +306.0 |
| 0.7 | 4,665 | 47.1% | 30.1% | +0.072% | +333.5 |
| 0.8 | 4,665 | 47.8% | 25.6% | +0.082% | +381.4 |
| 1.0 | 4,665 | 48.3% | 18.2% | +0.107% | +499.6 |
| 1.3 | 4,665 | 48.8% | 9.9% | +0.164% | +764.4 |
| 1.5 | 4,665 | 48.9% | 6.9% | +0.189% | +882.9 |

**IMPORTANT NOTE:** Wider SL appears better on total P&L, but this is misleading. Wider SL means fewer SL hits but TIME exits carry undefined risk. The avg returns above only count TP/SL outcomes, not the actual P&L at time exit.

### Q15: Best TP/SL Combinations (Including TIME Exit P&L)

| TP | SL | Trades | Win Rate | Avg Return | Total P&L |
|----|-----|--------|----------|------------|-----------|
| 1.0 | 0.4 | 4,665 | 25.2% | +0.114% | +529.7 |
| 0.9 | 0.4 | 4,665 | 29.3% | +0.109% | +507.8 |
| 1.0 | 0.5 | 4,665 | 25.9% | +0.104% | +483.2 |
| 0.8 | 0.4 | 4,665 | 33.6% | +0.103% | +481.9 |
| 0.6 | 0.4 | 4,665 | 43.6% | +0.088% | +410.0 |
| 0.7 | 0.6 | 4,665 | 40.5% | +0.071% | +330.6 |
| 0.5 | 0.4 | 4,665 | 48.9% | +0.071% | +332.7 |

**When accounting for actual TIME exit P&L:** The best TP/SL combo is TP=1.0/SL=0.4 with +0.114% avg return. However, a tight SL of 0.4% creates more whipsaw. **TP=0.6/SL=0.4 offers a good balance** with 43.6% win rate and +0.088% avg return.

### Q16: Average Return at Time-Exit Points

| Exit Bucket | Time | Avg Return | Median Return | % Positive |
|-------------|------|------------|---------------|------------|
| 16 | 9:30 | +0.080% | +0.074% | 53.8% |
| 21 | 9:35 | +0.082% | +0.066% | 53.4% |
| 26 | 9:40 | +0.034% | +0.020% | 50.8% |
| 31 | 9:45 | +0.087% | +0.057% | 52.8% |
| 46 | 10:00 | +0.050% | +0.006% | 50.0% |
| 76 | 11:00 | +0.064% | +0.015% | 50.5% |

**Interpretation:** Time exits are slightly positive on average. Bucket 31 (9:45) is a reasonable hard exit point -- later buckets don't improve returns and have wider variance (p25/p75 spread increases).

### SELL-Only TP/SL Optimization (Gap > -3%)

| Config | Trades | Win Rate | Avg Return |
|--------|--------|----------|------------|
| TP=0.6 SL=0.4 | 2,252 | 50.7% | +0.155% |
| TP=0.6 SL=0.5 | 2,252 | 52.6% | +0.152% |

**For SELL signals only with gap filter, TP=0.6%/SL=0.5% gives 52.6% win rate and +0.152% avg return per trade.**

---

## Phase 5: Volume and Momentum Filters

### Q17: Volume Rate at Entry

| Volume Rate Band | Trades | Win Rate | Avg Return |
|-----------------|--------|----------|------------|
| 0-50 | 3,352 | 41.1% | +0.073% |
| 50-100 | 271 | 37.6% | +0.038% |
| 100-200 | 238 | 33.6% | -0.027% |
| 200-500 | 306 | 36.9% | +0.002% |
| 500+ | 498 | 43.6% | +0.086% |

**Interpretation:** Volume rate shows a U-shaped pattern. Very low (< 50) and very high (500+) volume rates perform best. The mid-range (100-200) performs worst. The current min_vol_rate filter being disabled is actually correct -- requiring high volume rate would filter out the majority of profitable signals.

### Q19: OI Analysis

**OI data is entirely zeros across all buckets.** This means the current scoring system's OI-based signals (oiDir +1, oiSpike +1, oiAcc +1) are always scoring 0. These 3 points are effectively dead weight in the scoring system.

### Q20: Spread Filter

**Spread data is all zeros (except bucket 76).** Cannot analyze.

---

## Phase 6: Gap Analysis

### Q21: Gap Distribution

| Gap Range | Count | Percentage |
|-----------|-------|------------|
| < -3% | 278 | 2.3% |
| -3% to -1% | 1,035 | 8.5% |
| -1% to 0% | 3,855 | 31.7% |
| 0% to 1% | 5,889 | 48.5% |
| 1% to 2% | 690 | 5.7% |
| 2% to 3% | 199 | 1.6% |
| 3% to 5% | 145 | 1.2% |
| 5%+ | 62 | 0.5% |

~80% of days have gaps between -1% and +1%.

### Q22-23: Win Rate by Gap (BUY vs SELL)

**BUY Signals:**
| Gap Range | Trades | Win Rate | Avg Return |
|-----------|--------|----------|------------|
| < -3% | 110 | **49.1%** | **+0.147%** |
| -3% to -1% | 179 | 36.3% | -0.054% |
| **-1% to 0%** | **627** | **24.1%** | **-0.100%** |
| 0% to 1% | 1,057 | 35.7% | +0.015% |
| 1% to 2% | 167 | 45.5% | +0.135% |
| 2% to 3% | 28 | 50.0% | +0.157% |
| 3%+ | 73 | 37.0% | +0.021% |

**SELL Signals:**
| Gap Range | Trades | Win Rate | Avg Return |
|-----------|--------|----------|------------|
| **< -3%** | **172** | **32.0%** | **-0.122%** |
| -3% to -1% | 649 | 45.5% | +0.132% |
| -1% to 0% | 483 | 42.9% | +0.111% |
| 0% to 1% | 565 | 47.8% | +0.141% |
| **1% to 2%** | **182** | **59.9%** | **+0.317%** |
| 2% to 3% | 114 | 51.8% | +0.152% |
| 3%+ | 259 | 49.8% | +0.115% |

### Q24: Gap Filter Recommendations

**BUY signals:**
- **TERRIBLE when gap is -1% to 0%**: 24.1% win rate, -0.10% return. This is the largest group (627 trades) and it's strongly loss-making.
- **Good when gap < -3%**: 49.1% win rate (mean reversion after large gap down)
- **Good when gap > 0%**: 35-50% win rate (continuation of gap-up)
- **Recommendation: Only allow BUY when gap < -3% or gap > 0%**

**SELL signals:**
- **TERRIBLE when gap < -3%**: 32.0% win rate, -0.12% return. Selling after a huge gap down is mean-reversion against you.
- **BEST when gap 1-2%**: 59.9% win rate, +0.32% return! Gap-up stocks that start falling from open reverse strongly.
- **Recommendation: Block SELL when gap < -3%**

---

## Phase 7: Per-Symbol Analysis

### Q25: Top 20 Most Profitable Symbols

| Symbol | Trades | Win Rate | Avg Return | Total P&L |
|--------|--------|----------|------------|-----------|
| LICHSGFIN | 11 | 72.7% | +0.509% | +5.6 |
| AMBER | 41 | 78.0% | +0.502% | +20.6 |
| TVSMOTOR | 20 | 65.0% | +0.455% | +9.1 |
| PAYTM | 21 | 76.2% | +0.391% | +8.2 |
| ONGC | 26 | 69.2% | +0.369% | +9.6 |
| BLUESTARCO | 14 | 64.3% | +0.364% | +5.1 |
| ETERNAL | 61 | 67.2% | +0.343% | +20.9 |
| JSWENERGY | 39 | 64.1% | +0.341% | +13.3 |
| VOLTAS | 27 | 63.0% | +0.330% | +8.9 |
| BANKINDIA | 36 | 63.9% | +0.314% | +11.3 |
| SYNGENE | 30 | 63.3% | +0.303% | +9.1 |
| SWIGGY | 48 | 64.6% | +0.290% | +13.9 |
| PERSISTENT | 38 | 63.2% | +0.284% | +10.8 |
| LODHA | 38 | 60.5% | +0.282% | +10.7 |
| SAIL | 41 | 58.5% | +0.278% | +11.4 |

### Q26: Bottom 20 (Most Loss-Making Symbols)

| Symbol | Trades | Win Rate | Avg Return | Total P&L |
|--------|--------|----------|------------|-----------|
| BIOCON | 11 | 0.0% | -0.436% | -4.8 |
| WIPRO | 18 | 5.6% | -0.428% | -7.7 |
| AXISBANK | 12 | 0.0% | -0.350% | -4.2 |
| LUPIN | 12 | 0.0% | -0.300% | -3.6 |
| TATAPOWER | 17 | 17.6% | -0.300% | -5.1 |
| IREDA | 25 | 16.0% | -0.296% | -7.4 |
| DMART | 16 | 6.2% | -0.294% | -4.7 |
| IOC | 41 | 14.6% | -0.249% | -10.2 |
| SIEMENS | 31 | 22.6% | -0.229% | -7.1 |
| LAURUSLABS | 41 | 17.1% | -0.202% | -8.3 |

**Interpretation:** There are strong per-symbol patterns. Some stocks (AMBER, ETERNAL, SWIGGY) trend well intraday, while others (BIOCON, WIPRO, AXISBANK) consistently mean-revert, making momentum signals loss-making. A symbol-level filter could significantly improve performance.

### Q27: Top 20 by Signal Frequency

| Symbol | Signals | Signal Days | Avg/Day |
|--------|---------|-------------|---------|
| KAYNES | 69 | 28 | 2.5 |
| MCX | 64 | 23 | 2.8 |
| ETERNAL | 61 | 26 | 2.3 |
| POWERINDIA | 56 | 21 | 2.7 |
| INOXWIND | 55 | 23 | 2.4 |
| RVNL | 54 | 24 | 2.2 |
| NATIONALUM | 54 | 25 | 2.2 |
| PRESTIGE | 53 | 22 | 2.4 |

---

## Phase 8: Day-of-Week and Trend Analysis

### Q30: Win Rate by Day of Week

| Day | Trades | Win Rate | Avg Return |
|-----|--------|----------|------------|
| Mon | 1,248 | 43.7% | +0.079% |
| **Tue** | **1,047** | **46.9%** | **+0.133%** |
| Wed | 959 | 39.4% | +0.078% |
| **Thu** | **667** | **26.4%** | **-0.084%** |
| Fri | 744 | 40.1% | +0.047% |

**KEY FINDING:** Thursday is dramatically worse: 26.4% win rate and -0.084% avg return. This is likely related to weekly F&O expiry (Thursday). Expiry-day dynamics create whipsaw that kills momentum signals.

**Tuesday is the best day** with 46.9% win rate and +0.133% return.

### Q32: Win Rate by Week

| Week | Trades | Win Rate | Avg Return |
|------|--------|----------|------------|
| 2025-12-29 | 84 | 34.5% | +0.027% |
| 2026-01-05 | 253 | 32.0% | -0.032% |
| 2026-01-19 | 444 | 21.4% | **-0.215%** |
| 2026-01-26 | 414 | 44.9% | +0.097% |
| 2026-02-02 | 683 | 42.6% | +0.043% |
| 2026-02-09 | 344 | 40.7% | +0.104% |
| 2026-02-16 | 243 | 31.3% | -0.060% |
| **2026-03-02** | **370** | **48.4%** | **+0.159%** |
| **2026-03-09** | **767** | **50.8%** | **+0.204%** |
| 2026-03-16 | 529 | 42.9% | +0.125% |

**Week of Jan 19 was catastrophic** (-0.215% avg). This was likely budget-week / market stress. **March 2-16 was excellent** (48-51% win rate).

---

## Phase 9: Combined Filter Optimization

### Q33: Current Config Baseline Performance (Entry 2-5, TP 0.7%, SL 0.6%, Hard Exit 31)

| Metric | Value |
|--------|-------|
| Trading days | 59 |
| Avg signals/day | 79.1 |
| Avg wins/day | 32.0 |
| Avg losses/day | 29.1 |
| Overall win rate | 52.4% (TP only; incl TIME: 40.5%) |
| Avg daily P&L | +4.93 pct-points (sum of TP/SL returns) |
| Total P&L | +290.8 pct-points |
| Profitable days | 36 (61%) |
| Losing days | 23 (39%) |
| Median daily P&L | +2.9 pct-points |

### Q34: Filter Impact Comparison

| Configuration | Trades | Win Rate | Avg Return | Signals/Day |
|--------------|--------|----------|------------|-------------|
| a) No filter (baseline) | 4,665 | 40.5% | +0.062% | 79.1 |
| b) SELL only | 2,424 | 46.4% | +0.125% | 41.1 |
| c) SELL + gap > -3% | 2,252 | 47.5% | +0.144% | 38.2 |
| **d) SELL + gap > -3% + no Thu** | **1,877** | **51.9%** | **+0.187%** | **31.8** |
| e) SELL + gap > -3% + entry 2-3 only | 708 | **55.9%** | **+0.222%** | 12.0 |
| f) Remove BUY(-1 to 0 gap) | 3,756 | 43.7% | +0.098% | 63.7 |
| g) SELL + BUY(gap < -3 or gap >= 1) | 2,802 | 46.2% | +0.124% | 47.5 |
| h) SELL + BUY(gap filtered) + no Thu | 2,383 | 49.5% | +0.155% | 40.4 |

### Q35: Move Size Analysis (Entry-Specific)

| Move Band | Dir | Trades | Win Rate | Avg Return |
|-----------|-----|--------|----------|------------|
| 0.5-0.7% | SELL | 2,240 | 38.9% | +0.115% |
| 0.7-1.0% | SELL | 1,493 | 43.7% | +0.109% |
| 1.0-1.5% | SELL | 692 | 49.6% | +0.144% |
| 1.5-2.0% | SELL | 176 | 51.7% | +0.161% |
| 2.0%+ | SELL | 63 | 58.7% | +0.202% |
| 0.5-0.7% | BUY | 2,340 | 28.3% | -0.020% |
| 0.7-1.0% | BUY | 1,435 | 32.8% | +0.002% |
| 1.0-1.5% | BUY | 673 | 34.6% | -0.026% |

**SELL signals get better with larger moves.** Lowering min_move to 0.5% for SELL could add 2,240 trades with 38.9% win / +0.115% return. However, this is lower than the 0.7%+ signals.

---

## Phase 10: Novel Pattern Analysis

### Q36: Morning Range Breakout

| Range Type | Breakout | Count | Avg Continuation | Continue 0.5%+ |
|-----------|----------|-------|-----------------|----------------|
| Tight (< 0.5%) | DOWN | 383 | +0.404% | 26.4% |
| Tight (< 0.5%) | UP | 277 | +0.402% | 27.1% |
| Wide (>= 0.5%) | DOWN | 5,692 | +0.556% | 44.1% |
| Wide (>= 0.5%) | UP | 4,541 | +0.551% | 42.3% |

**Wide morning ranges predict continuation better than tight ranges.** When the first 5 minutes have a range >= 0.5%, breakouts continue 0.5%+ 42-44% of the time (vs 26-27% for tight ranges). This supports the current approach of requiring a minimum move before entry.

### Q38: Price Velocity at Entry

Most entries have weak velocity (< 0.5 in absolute terms). The velocity field is too noisy in the first 5 minutes to be a reliable filter. Notable finding: SELL signals with negative velocity ("aligned") and SELL signals with positive velocity ("misaligned") both perform similarly well (~46-48% win rate).

### Q39: Price Acceleration Pattern

| Acceleration | Direction | Trades | Win Rate | Avg Return |
|-------------|-----------|--------|----------|------------|
| Accelerating | SELL | 112 | 42.0% | +0.047% |
| Decelerating | SELL | 487 | 41.3% | +0.102% |
| Steady | SELL | 313 | 38.0% | +0.048% |
| Decelerating | BUY | 393 | 33.1% | +0.007% |

**Interpretation:** Decelerating SELL signals (where the move from bucket 3-5 is slower than 1-3) perform best among SELL signals. This suggests that a strong initial move followed by slowing is a good entry -- the stock has committed to a direction.

### Q40: Bid-Ask Imbalance
**Not analyzable** -- bid/ask/bid_qty/ask_qty are all zeros in the data for entry buckets.

---

## Summary & Recommendations

### Current vs Optimal Config

| Parameter | Current Config | Optimal Config | Impact |
|-----------|---------------|----------------|--------|
| Entry window | Bucket 2-5 | **Bucket 2-3** | Win rate +8-10% at bucket 2-3 vs 4-5 |
| Direction | Both BUY + SELL | **SELL preferred** | SELL: 46-56% win vs BUY: 32-37% win |
| TP | 0.7% | **0.6%** | Optimal total P&L at 0.6% |
| SL | 0.6% | **0.4-0.5%** | Tighter SL with lower TP improves risk-adjusted returns |
| Hard exit | Bucket 31 | **Bucket 31** (keep) | Good balance point |
| min_move | 0.7% | **0.7%** (keep) | Works well, higher is better per-trade but fewer signals |
| gap_filter (BUY) | Disabled | **Block BUY when gap -1% to 0%** | BUY -1to0 gap: 24.1% win rate = pure loss |
| gap_filter (SELL) | Disabled | **Block SELL when gap < -3%** | SELL at huge gap-down: 32% win = loss |
| Day filter | None | **Skip Thursday** | Thu: 26.4% win / -0.084% avg |
| min_score | 4 | Irrelevant if OI scores are 0 | OI signals worth 3 points are dead weight |

### Filters That Help vs Hurt

**Strongly Helpful:**
1. **SELL-only or SELL-preferred:** +2x improvement in avg return over baseline
2. **Block Thursday:** +5% win rate, eliminates the worst day
3. **Gap filter for SELL (> -3%):** Small but consistent improvement
4. **Gap filter for BUY (< -3% or > 0%):** Eliminates the worst BUY category
5. **Entry window 2-3 only:** +15% win rate vs bucket 4-5

**Neutral/Not Available:**
- Volume rate: U-shaped, not a clean filter
- OI signals: No data available
- Spread filter: No data available
- Price velocity: Too noisy in first minutes
- Bid/ask imbalance: No data

**Potentially Harmful:**
- Requiring high volume_rate (100-200 band is worst)
- TP > 1.0% (total P&L goes negative)

### Novel Patterns Worth Implementing

1. **Wide morning range confirmation:** When 5-min range >= 0.5%, breakouts are 44% likely to continue 0.5%+. This aligns with the current min_move requirement.

2. **Decelerating SELL signals:** SELL signals where the move slows between bucket 1-3 and 3-5 have +0.102% avg return vs +0.047% for accelerating. Could be a scoring bonus.

3. **Gap-direction interaction for SELL:** SELL signals after gap-up of 1-2% have 59.9% win rate and +0.317% avg return -- the single strongest signal discovered.

### Recommended Configuration Tiers

**Tier 1: Conservative (High win rate, fewer signals)**
```
entry_window: [2, 3]
direction: SELL_ONLY
tp: 0.6%
sl: 0.5%
hard_exit: bucket 31
gap_filter_sell_min: -3.0
skip_thursday: true
```
Expected: ~12 signals/day, ~56% win rate, ~+0.22% avg return

**Tier 2: Balanced (Good returns, more signals)**
```
entry_window: [2, 5]
direction: SELL preferred, BUY only with gap < -3% or gap > 0%
tp: 0.6%
sl: 0.5%
hard_exit: bucket 31
gap_filter_sell_min: -3.0
gap_filter_buy: exclude -1% to 0%
skip_thursday: true
```
Expected: ~40 signals/day, ~50% win rate, ~+0.155% avg return

**Tier 3: Aggressive (Max total P&L, lower per-trade edge)**
```
entry_window: [2, 5]
direction: both
tp: 0.6%
sl: 0.4%
hard_exit: bucket 31
gap_filter_min: -3.5
```
Expected: ~76 signals/day, ~44% win rate, ~+0.09% avg return

### Expected Daily Performance (Tier 2 Recommended)

| Metric | Estimate |
|--------|----------|
| Signals per day | ~40 |
| Win rate | ~50% |
| Avg return per trade | +0.155% |
| Daily P&L (sum) | ~6.2 pct-points |
| Profitable days | ~60-65% |
| Monthly cumulative P&L | ~130 pct-points |

### Scoring System Fix

The current max score is 10 but OI-based signals (oiDir, oiSpike, oiAcc = 3 points) are always zero. Effective max is 7. With min_score=4, this means the remaining signals (pm, pm2, vol, vol2 = 7 points) must contribute at least 4. Recommendation:

1. Remove OI signals from scoring until OI data is available
2. Set max score to 7
3. Consider adding gap-direction alignment as a scoring factor (+2 for SELL with gap > 1%)
4. Consider adding Thursday penalty (-2 or exclude)

---

*Analysis performed on 2026-03-24 using ClickHouse queries against trading.snapshots (1M+ rows) and trading.daily_ref (12K rows).*
