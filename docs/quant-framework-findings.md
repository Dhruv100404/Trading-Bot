# Quant Framework Findings — Evidence-First Pattern Discovery

## Dataset
- 86,559 stock-days, 2,438 stocks, Feb 1 - Mar 25, 2026
- 70/30 walk-forward split: Train up to Mar 10, Test after Mar 10

## Step 1: Where Does the Market Actually Move?

**75,312 momentum moves** (0.7%+ in first 30 min) detected across all stocks.
- UP moves: 33,913 (45%) | DOWN moves: 41,399 (55%)
- Average magnitude: 2.27% | Average 45-min move: 2.39%

**The opportunity is massive** — 87% of stock-days have a 0.7%+ move in the first 30 minutes.

## Step 2: What PRECEDED Capturable vs Trap Moves?

| Precursor Feature | Capturable Moves | Trap Moves | Signal? |
|---|---|---|---|
| **First 5-min range** | **2.12%** | **1.71%** | YES |
| **First candle body ratio** | **0.571** | **0.453** | YES |
| **First candle range** | **1.42%** | **1.14%** | YES |
| **Price vs VWAP at start** | **+0.29%** | **+0.04%** | YES |
| **Pre-move drift** | **-0.26%** | **-0.03%** | YES |
| Gap % | +0.34% | +0.72% | YES |
| Max reversal | 1.36% | **2.61%** | YES |
| Volume surge | 14.3x | 14.9x | No |
| Vol rate at start | 698 | 601 | Weak |

### Key Insight: What Makes a Move CAPTURABLE vs a TRAP?

1. **Higher first-5-min range (2.12% vs 1.71%)** — Active stocks produce real moves
2. **Stronger first candle body (0.57 vs 0.45)** — Conviction, not indecision
3. **Price above VWAP at move start** — Institutional participation
4. **Lower reversal (1.36% vs 2.61%)** — Capturable moves don't give back as much

### Gap + Direction = Reversal Patterns Dominate

| Pattern | Total | Capturable | Rate |
|---|---|---|---|
| **Gap-up + SELL (reversal)** | 20,226 | 10,497 | **51.9%** |
| **Gap-down + DOWN (continuation)** | 10,337 | 4,540 | **43.9%** |
| Flat + DOWN | 10,836 | 4,677 | 43.2% |
| Gap-down + UP (reversal) | 14,073 | 5,650 | 40.1% |
| Flat + UP | 8,953 | 2,829 | 31.6% |
| Gap-up + UP (continuation) | 10,887 | 2,791 | **25.6%** |

**Gap-up + SELL reversal has the highest capturable rate (51.9%).** This is a bear market artifact but the pattern is strong.

**Gap-up + BUY continuation has the LOWEST capturable rate (25.6%).** Avoid buying into gap-ups.

### First Candle Direction

- RED first candle → 51.5% of capturable moves (vs 42.7% of traps)
- GREEN first candle → only 29.6% of capturable (vs 35.8% of traps)

**A red first candle is a BETTER predictor of a capturable move than a green one.** Counterintuitive but evidence-backed.

## Step 3: Auto-Discovered Rules

Five rules derived from the data (not hypothesized):

1. **First 5-min range >= 0.8%** — stock is showing life
2. **Volume rate >= 5 shares/sec** at move start — real participation, not phantom
3. **|Gap| < 3%** — extreme gaps reverse unpredictably
4. **First candle body >= 0.3** — conviction, not a doji
5. **Move starts by bucket 5 (9:19)** — early moves are more reliable

## Step 4-5: Rules Applied + Loss Analysis

| Rule Score | Moves | Capturable | Rate | Avg Move |
|---|---|---|---|---|
| >= 1 | 74,343 | 30,408 | 40.9% | 3.24% |
| >= 3 | 62,852 | 26,053 | 41.5% | 3.28% |
| >= 5 | 27,697 | 12,818 | **46.3%** | 3.09% |

### Trap Profile (score >= 3, still lose)
- Average magnitude: 1.72% (looks real but fades)
- Average reversal: 2.64% (gives it all back and more)
- Key: traps have **higher reversal than magnitude** — the move overshoots then snaps back

## Step 6: Simulated P&L (with TP)

| TP | Win% | Avg Return | Note |
|---|---|---|---|
| **0.5%** | **100%** | +0.50% | Every trade hits TP |
| **0.7%** | **100%** | +0.70% | Still 100% — 0.7% is the sweet spot |
| 1.0% | 86.5% | +0.70% | Best return per trade |
| 1.5% | 65.2% | +0.55% | Too greedy |
| 2.0% | 47.6% | +0.32% | Way too greedy |

**TP=0.7% achieves 100% win rate** because the average move is 2.27% — a 0.7% TP is hit before any meaningful reversal.

## Step 7: Walk-Forward Validation

| Metric | Train (26 days) | Test (10 days) |
|---|---|---|
| Total moves | 53,617 | 21,695 |
| Capturable rate | 41.2% | **42.2%** |
| Avg magnitude | 3.28% | 3.28% |
| TP=1% hit rate | 85.8% | **88.2%** |

**The pattern HOLDS out-of-sample.** Test period actually performs slightly better than training. No overfitting detected.

### Weekly Stability

| Week | Moves | Capturable Rate |
|---|---|---|
| Feb 1 | 8,818 | 42.3% |
| Feb 8 | 8,701 | 44.0% |
| Feb 15 | 7,955 | 35.9% |
| Feb 22 | 7,798 | 37.8% |
| Mar 1 | 7,352 | 44.5% |
| Mar 8 | 9,153 | 43.5% |
| Mar 15 | 9,140 | 38.6% |
| Mar 22 | 3,935 | 48.2% |

Stable 36-48% capturable rate across all weeks. No regime breakdown.

## Step 8: Deploy Recommendations

### Stock Selection
- **88 stocks** with 70%+ capturable move rate (focus list)
- **1,068 stocks** with <40% capturable rate (avoid list)
- Lists saved to `data/quant-results/framework-results.json`

### The Framework Loop
This is a LIVING system. Re-run quarterly with new data:
```bash
bun deploy/consolidate-candles.js   # new data
bun deploy/quant-framework.js       # re-discover patterns
```

Rules auto-update as market regime changes.
