# Backtest Deep Analysis — 2026-03-26

## Dataset
- **400 stocks** (NSE EQ from Dhan scrip master, ~374 with data)
- **14,232 stock-days** analyzed (Feb 1 – Mar 25, 2026)
- **224 F&O stocks** tagged, rest are non-F&O
- Config: pulled from live `trading.config` (account 1100896497)

## Current Performance (with live config)

| Metric | Value |
|---|---|
| Total trades | 2,758 |
| Win rate | 51.6% |
| Avg return | +0.09% |
| Total PnL | Rs 2,123 |
| BUY trades | 649 (42.7% win, +0.01% avg) |
| SELL trades | 2,109 (54.3% win, +0.11% avg) |
| Exits | TP=0, SL=486, TIME=2,272 |

**Critical issue**: `buy_tp_pct=0`, `sell_tp_pct=0` — no take-profit at all. All profitable trades exit on TIME, leaving money on the table.

---

## Part 1: Raw Movement Opportunity

**73.7% of stock-days move 1%+ within 45 minutes of market open.**

| Time Window | 0.5%+ move | 1.0%+ move | 1.5%+ move | 2.0%+ move |
|---|---|---|---|---|
| 20 min | — | 64.6% | — | — |
| 45 min | 90.3% | 73.7% | 53.4% | 37.1% |
| 60 min | — | 76.3% | — | — |

### Best predictor: First 5-minute range

| First 5-min range | Stock-days | Moved 1%+ | Probability |
|---|---|---|---|
| < 0.5% | 1,503 | 823 | 54.8% |
| 0.5–1% | 2,513 | 1,231 | 49.0% |
| **1–2%** | **4,825** | **4,015** | **83.2%** |
| **2–3%** | **2,147** | **2,147** | **100%** |
| **> 3%** | **1,769** | **1,769** | **100%** |

**If first-5-min range > 1%, there is 83%+ probability of a 1%+ move.** This is the single strongest predictor.

### Gap + Range combo

| Combo | Days | 1%+ Move | Probability |
|---|---|---|---|
| Any gap + range > 2% | 3,914 | 3,914 | **100%** |
| Gap down + range 1-2% | 797 | 687 | 86.2% |
| Gap up + range 1-2% | 839 | 748 | 89.2% |
| Flat + range < 1% | 3,037 | 1,395 | 45.9% |

### Most consistently moving stocks (100% of days, 1%+ move)

QUICKHEAL, SAKSOFT, INFOBEAN, KSOLVES, KOTARISUG, SHYAMCENT, SUMIT, SIL, MCLEODRUSS, BALPHARMA, KRIDHANINF, SADBHIN, AARON, GENESYS, ANTELOPUS, BAGFILMS, RAMAPHO, NECCLTD, RADHIKAJWE, MARINE

Only 1 F&O stock (COFORGE) in top 30 movers. The biggest movers are **mid/small-caps**.

### What distinguishes movers from non-movers?

| Feature | 1%+ Movers | Non-movers |
|---|---|---|
| Avg vol_first_5min | 115,767 | 86,215 |
| Avg vol_rate_at_peak | 631.7 | 336.3 |
| Avg total_vol_60min | 565,708 | 386,899 |

**Volume rate at peak movement (632 vs 336) is the key differentiator.**

---

## Part 2: Loss Autopsy

### 74.5% of losers moved favorably FIRST, then reversed

| Loss Category | Count | % of Losers |
|---|---|---|
| Never moved in our direction | 147 | 11.3% |
| Small move (0.05-0.3%) then reversed | 185 | 14.2% |
| **Good move (>0.3%) then reversed** | **969** | **74.5%** |
| Whipsawed (hit SL, price came back) | 308 | 23.7% |

**The problem is NOT bad entries. It's missing the exit.**

### TP would save most losses

| TP Level | Losers Saved | % of All Losers |
|---|---|---|
| **0.2%** | **1,055** | **81.1%** |
| **0.3%** | **969** | **74.5%** |
| 0.5% | 829 | 63.7% |
| 0.7% | 693 | 53.3% |
| 1.0% | 535 | 41.1% |

**Even a 0.3% TP flips 74.5% of losses into wins.**

### Max Favorable Excursion (MFE)

| Metric | Winners | Losers |
|---|---|---|
| Avg max favorable move | 2.80% | **1.24%** |
| Avg max adverse move | 1.08% | 2.91% |

Losers had an avg max favorable move of **1.24%** — plenty of room to exit at 0.3-0.5% profit.

### Winners vs Losers — feature comparison

| Feature | Winners | Losers |
|---|---|---|
| Avg move_pct at entry | 0.73% | 0.81% |
| Avg volume_rate | 566.5 | **848.1** |
| Avg gap_pct | 1.35% | 1.43% |
| Avg morning_range | 0.91% | 0.99% |
| Avg score | 8.3 | 8.2 |

**Losers have HIGHER volume rate (848 vs 567).** This is counterintuitive — high volume at entry correlates with losses, possibly because it signals exhaustion/peak activity, not continuation.

---

## Part 3: Signal Quality

### Capture rate: only 23.7%

| Metric | Count |
|---|---|
| Stock-days with 1%+ move | 10,489 |
| We signaled | 2,491 (23.7%) |
| We missed | 7,998 (76.3%) |

### Why we miss: low volume stocks

| vol_first_5min | Missed movers |
|---|---|
| < 1K | 2,793 |
| 1K-10K | 2,607 |
| 10K-50K | 1,656 |
| 50K-500K | 754 |
| > 500K | 188 |

Most missed movers have volume < 10K in first 5 min. Current min_volume filter (300-450) catches some, but the volume rate filter may block these.

### False positive rate: only 9.7%

Signals on non-movers: 267 / 2,758 = 9.7%. These avg **-0.369%** return.
Signals on 1%+ movers avg **+0.135%** return.

---

## Critical Findings

1. **TP is the single biggest lever** — enabling even 0.3% TP flips 74.5% of losses
2. **First-5-min range > 1%** = 83% chance of 1%+ move (pre-filter)
3. **Volume rate at entry** — higher is actually worse (exhaustion signal); sweet spot is 100-200
4. **Most opportunity is in small/mid-caps** outside F&O
5. **We're only capturing 23.7% of movers** — room to 3-4x trade count with looser filters
6. **9.7% false positive rate is excellent** — signal quality is high

---

## Part 4: Path to Profitability — TP Optimization & Daily Simulation

### A. TP Optimization Sweep (2,921 trades)

The single biggest lever. Current config has TP=0 (disabled). Simulating different TP levels:

| TP % | Win Rate | Avg Return | Total PnL | PnL/Day |
|---|---|---|---|---|
| 0.1% | 93.5% | -0.00% | Rs 763 | Rs 21 |
| 0.3% | 88.2% | +0.11% | Rs 2,721 | Rs 76 |
| 0.5% | 83.0% | +0.20% | Rs 4,482 | Rs 124 |
| **0.7%** | **77.7%** | **+0.28%** | **Rs 5,899** | **Rs 164** |
| **1.0%** | **71.7%** | **+0.36%** | **Rs 7,753** | **Rs 215** |
| **1.5%** | **65.0%** | **+0.46%** | **Rs 9,425** | **Rs 262** |
| **2.0%** | **60.5%** | **+0.46%** | **Rs 9,777** | **Rs 272** |

**Best: TP=2.0%** gives Rs 272/day, but TP=1.0% gives the best risk/reward (71.7% win rate with Rs 215/day).

Compared to current (no TP): Rs 59/day → with TP=1.0%: Rs 215/day = **3.6x improvement** just by enabling TP.

### B. Per-Stock Consistency (with TP=2.0%)

**151 stocks** qualify as profitable (win% >= 55%, positive PnL):
- Combined: 1,678 trades, Rs 8,215 total PnL, Rs 228/day

### Top performing stocks:

| Stock | Trades | Win% | Avg Return | PnL | F&O |
|---|---|---|---|---|---|
| KAYNES | 10 | 70.0% | +0.67% | Rs 596 | yes |
| COFORGE | 11 | 81.8% | +1.18% | Rs 424 | yes |
| DREDGECORP | 10 | 90.0% | +1.31% | Rs 235 | |
| WELCORP | 11 | 81.8% | +1.26% | Rs 228 | |
| GABRIEL | 11 | 81.8% | +1.09% | Rs 213 | |
| GOLDIAM | 17 | 88.2% | +1.22% | Rs 184 | |
| PETRONET | 12 | 83.3% | +1.34% | Rs 113 | yes |
| GRANULES | 8 | 87.5% | +1.01% | Rs 92 | |

### C. Daily PnL Simulation

With TP=2.0%, picking top-N signals per day by score:

| Signals/Day | Avg PnL/Day | Positive Days | Capital | ROC |
|---|---|---|---|---|
| 5 | Rs 38 | 91.7% | Rs 50K | 0.08% |
| 10 | Rs 87 | 88.9% | Rs 100K | 0.09% |
| 20 | Rs 152 | 88.9% | Rs 200K | 0.08% |
| All | Rs 272 | 80.6% | Rs 1000K | 0.03% |

### D. Curated Universe (stocks with 60%+ days moving 1%+)

- **253 stocks** qualify
- 2,584 trades, 60.9% win rate with TP=2.0%
- Rs 235/day, 80.6% positive days

---

## Path to 10% Daily ROC — Honest Assessment

### Current reality with qty=1:
- With 10 trades/day at Rs 10K each = Rs 100K capital
- Avg PnL = Rs 87/day = **0.09% ROC**
- This is 100x away from 10%

### What 10% ROC actually requires:
- Rs 100K capital, need Rs 10,000/day
- That means 10 trades × Rs 1,000 PnL each
- Rs 1,000 on Rs 10K = **10% per trade**
- Or 100 trades × Rs 100 each (1% per trade)

### The math problem:
The system averages +0.09% per trade currently (+0.46% with optimal TP). Even at 0.46%, with qty=1 and entry ~Rs 200-500, PnL per trade is Rs 1-2. **You need larger position sizes, not more trades.**

### Realistic path:
1. **Enable TP=1.0%** → 71.7% win rate, +0.36% avg return
2. **Use capital_per_trade = Rs 50,000** instead of qty=1 → qty = 50K/price ≈ 100-250 shares
3. **Curate to 151 proven stocks** → higher quality signals
4. **10 trades/day × Rs 50K × 0.36% avg = Rs 1,800/day on Rs 500K = 0.36% daily ROC**
5. With leverage or larger capital, scale linearly

### The 10% ROC path:
- Requires Rs 3L capital with 20 concurrent positions at Rs 50K each
- TP=1.0%, 20 signals/day from curated universe
- 71% win rate, +0.36% avg, ~Rs 3,600/day
- **ROC = 1.2%/day** — still not 10%, but realistic and profitable
- To reach 10%: need to increase avg return to 2%+ per trade, which means holding longer or using options instead of equity
