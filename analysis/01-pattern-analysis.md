# Trading Pattern Analysis — Live Signal Data (Feb 20 - Mar 20, 2026)

## Data: 824 live signals with real outcomes, 206 F&O stocks

---

## FINDING 1: SELL Signals Are Destroying P&L

| Direction | Trades | Win% | Avg Ret% | Total P&L (1qty) |
|-----------|--------|------|----------|-------------------|
| BUY       | 380    | 51.8%| +0.099%  | +₹259             |
| SELL      | 444    | 41.9%| -0.083%  | -₹587             |

**SELL signals lose money net**. BUY is profitable but modest.

---

## FINDING 2: Monday SELLs Are Catastrophic

| DOW | Dir  | Trades | Win% | Total P&L |
|-----|------|--------|------|-----------|
| Mon | SELL | 135    | 17%  | -₹991     |
| Tue | SELL | 90     | 45.6%| +₹119     |
| Wed | SELL | 69     | 58%  | +₹206     |
| Thu | BUY  | 58     | 31%  | -₹232     |
| Fri | BUY  | 150    | 58.7%| +₹233     |
| Fri | SELL | 77     | 66.2%| +₹235     |

**Monday SELLs lose ₹991** — 17% win rate (stocks gap down over weekend, bounce back Monday).
**Thursday BUYs lose ₹232** — 31% win rate (profit-booking before weekend?).
**Friday is best day** for both BUY and SELL.

---

## FINDING 3: Market-Wide Gap Determines Best Direction

Average gap across all stocks defines the "market regime":

| Regime     | BUY Win% | BUY P&L  | SELL Win% | SELL P&L  |
|------------|----------|----------|-----------|-----------|
| gap_up (>1%)  | 14%   | -₹76     | 72%       | +₹256     |
| flat_up (0-1%)| 51%   | +₹268    | 41%       | -₹82      |
| flat_down (-1-0)| 62% | +₹211    | 42%       | -₹466     |
| gap_down (<-1%)| 49%  | -₹144    | 36%       | -₹295     |

**RULE: On gap-up market days → SELL only. All other days → BUY only.**

---

## FINDING 4: Entry Bucket Sweet Spot

| Bucket | BUY Win% | BUY Avg Ret% | SELL Win% |
|--------|----------|--------------|-----------|
| 8      | 51%      | +0.081%      | 45%       |
| 9      | 62.5%    | +0.286%      | 37.5%     |
| 10     | 54.7%    | +0.168%      | 29.3%     |
| 11+    | ~43%     | negative     | mixed     |

**BUY signals entered at bucket 9 (09:23 IST) have the best win rate (62.5%).**
Buckets 8-10 are the optimal entry window.

---

## FINDING 5: Individual Stock Gap Matters

### BUY signals by stock gap:
- gap_down > 3%: **77% win, +₹43** (mean reversion bounce — best BUY signal)
- gap_flat (±1%): 51% win, modest positive
- gap_up > 1%: 50% win, -₹2

### SELL signals by stock gap:
- gap_up > 1%: **62% win, +₹202** (overextended gap, sell the rally)
- gap_up > 3%: **83% win** (small sample)
- gap_down > 3%: **17% win, -₹113** (NEVER short a big gap-down — mean reversion)

---

## FINDING 6: Score Effect (number of signals fired)

### BUY:
| Score (signals fired) | Trades | Win% | Avg Ret |
|----------------------|--------|------|---------|
| 3 (score 4-5)        | 69     | 50.7%| +0.057% |
| 4 (score 6)           | 136    | 49.3%| +0.085% |
| 5 (score 7)           | 158    | 56.3%| +0.155% |
| 6 (score 8-9)         | 17     | 35.3%| -0.137% |

**BUY: 5 signals (score ~7) is the sweet spot. Score 8-9 is too selective and loses.**

### SELL:
| Score (signals fired) | Trades | Win% | Avg Ret |
|----------------------|--------|------|---------|
| 3                     | 113    | 37.2%| -0.16%  |
| 4                     | 153    | 41.8%| -0.103% |
| 5                     | 159    | 44%  | -0.033% |
| 6                     | 19     | 52.6%| +0.12%  |

---

## FINDING 7: Volume Rate at Entry Predicts BUY Success

| Vol Rate Band | BUY Win% | BUY Avg Ret |
|---------------|----------|-------------|
| <100          | 48.5%    | +0.037%     |
| 100-200       | 50%      | +0.122%     |
| 400-800       | 57.1%    | +0.152%     |
| >800          | **65.5%**| **+0.315%** |

**High volume rate (>800) BUY signals have 65% win rate** — momentum confirms the move.

---

## FINDING 8: Max Favorable Excursion (MFE) — Trailing Stop Potential

### BUY SL Hits (134 trades that hit stop-loss):
- 24.6% of them reached +0.3% favorable BEFORE hitting SL
- Only 3.2% reached +0.5%
- Most SL hits go straight against you — SL is working correctly

### BUY TIME Exits (91 trades):
- 73% reached +0.3% favorable during the trade
- 37% reached +0.5%
- Avg MFE = 0.415% → **trailing stop at 0.3% would capture significant profits**

### SELL TIME Exits (82 trades):
- 76% reached +0.3% favorable
- 40% reached +0.5%

---

## FINDING 9: TP Simulation (No SL)

Using actual MFE data from live signals, allocating ₹10,000 per trade:

| TP Level | BUY P&L | SELL P&L | Total |
|----------|---------|----------|-------|
| 0.3%     | +634    | +953     | +1587 |
| 0.5%     | +270    | +1180    | +1450 |
| 0.7%     | +1487   | +240     | +1727 |
| 1.0%     | +4424   | +2083    | +6507 |
| Time only| +2642   | -3768    | -1126 |

**For SELL: TP=0.3-0.5% dramatically improves performance** (captures early move before reversal).
**For BUY: Time exit outperforms small TPs** (winners run big; cutting early costs).

---

## FINDING 10: Stock Price Band — Dynamic Quantity

| Price Band | BUY Win% | BUY Avg P&L(₹) | SELL Win% | SELL Avg P&L(₹) |
|------------|----------|-----------------|-----------|-----------------|
| <200       | 59.3%    | ₹0.27           | 37.1%     | -₹0.11          |
| 200-500    | 54.2%    | ₹0.44           | 44.4%     | -₹0.20          |
| 500-1k     | 44.4%    | ₹0.59           | 44.9%     | -₹0.64          |
| 1k-2k      | 51.6%    | ₹0.90           | 41.4%     | -₹1.64          |
| 2k-5k      | 52.4%    | ₹0.99           | 40%       | -₹3.44          |
| >5k        | 40%      | ₹1.85           | 40.6%     | -₹5.02          |

Cheap stocks: higher BUY win rate, tiny P&L per share.
Expensive stocks: larger P&L per share but worse win rate.
**Equal-rupee allocation (~₹10k per trade) normalizes this.**

---

## SCENARIO COMPARISONS (₹10,000 per trade)

| Scenario | Trades | Win% | Avg Ret% | P&L (10k) | Per Trade |
|----------|--------|------|----------|-----------|-----------|
| A: All signals | 824 | 46.5% | +0.001% | +₹102 | +₹0.12 |
| B: BUY only | 380 | 51.8% | +0.099% | +₹3,770 | +₹9.92 |
| C: BUY noThu + SELL gapUp>1 | 378 | 56.9% | +0.170% | +₹6,421 | +₹16.99 |
| **D: REGIME filter + noThu** | **351** | **58.1%** | **+0.193%** | **+₹6,776** | **+₹19.30** |

**Scenario D is 66x better than baseline** with fewer trades.
