# Afternoon Momentum Analysis (2:00 PM - 3:30 PM) — 2026-03-27

## Dataset
- Feb-Mar 2026: 87,147 stock-days, 36 trading days
- Afternoon data: 87,084 stocks have buckets 286+ (2PM onwards)

## Part 1: Raw Afternoon Movement

**Stocks move significantly in the closing hour:**

| Move Threshold | % of Stocks | Meaning |
|---|---|---|
| >= 0.3% | **95.0%** | Almost every stock moves 0.3%+ |
| >= 0.5% | **86.8%** | Most stocks give 0.5% opportunity |
| >= 0.7% | **76.2%** | 3 out of 4 stocks move 0.7%+ |
| >= 1.0% | **60.1%** | 60% move 1%+ in afternoon |
| >= 1.5% | **38.7%** | More than 1/3 move 1.5%+ |

- **Avg net move: 0.94%** | **Avg max move: 1.59%**
- The afternoon has LESS movement than morning (2.01% avg MFE morning) but still very tradeable

## Part 2: Optimal Entry Time

| Entry Time | TP=0.3% Hit | TP=0.5% Hit | TP=0.7% Hit | MFE/MAE |
|---|---|---|---|---|
| **2:00 PM** | **92.8%** | **80.0%** | **67.8%** | **2.05** |
| 2:15 PM | 67.9% | 55.0% | 44.4% | 0.90 |
| 2:30 PM | 70.8% | 57.3% | 46.0% | 1.03 |
| 2:45 PM | 68.1% | 53.6% | 42.1% | 1.01 |
| 3:00 PM | 65.1% | 50.2% | 38.8% | 1.01 |

**Key finding: 2:00 PM entry has the BEST MFE/MAE (2.05)** — because you have 90 minutes until close. By 2:15 PM, the MFE/MAE drops to 0.90 because the early direction signal (first 15 min) often leads you into an already-moving trade that's about to reverse.

**The paradox: 2:00 PM entry is BLIND (no afternoon direction signal yet) but has the best ratio. 2:15 PM has a direction signal but worse ratio because the move already started.**

## Part 3: What Predicts Afternoon Movers

Top 10% of afternoon movers vs bottom 50%:

| Feature | Top 10% | Bottom 50% | Diff | Signal |
|---|---|---|---|---|
| **Volume acceleration (PM/morning)** | **8.9×** | **2.6×** | **245%** | ★★★ |
| **Morning range** | **3524** | **799** | **341%** | ★★★ |
| **Early PM move %** | **0.66%** | **0.17%** | **293%** | ★★★ |
| **PM volume rate** | **176** | **103** | **72%** | ★★★ |
| **Big morning move (>2%)** | **0.33** | **0.11** | **210%** | ★★★ |
| |Gap| % | 1.76 | 3.25 | 46% | ★★★ |

**The #1 predictor is volume acceleration** — stocks where afternoon volume is 8.9× the morning average are the big movers. This makes sense: institutional rebalancing in the closing hour drives volume spikes.

**Interesting: smaller gap stocks move MORE in afternoon.** Gap stocks already made their move in the morning.

## Part 4: Best Afternoon Trading Setup

| Strategy | Win% | Rs/day | ROC% | Positive Days | MFE/MAE |
|---|---|---|---|---|---|
| **VWAP+vol+move, rank by earlyMove, TP=0.7%** | **71.5%** | **Rs 68** | **+0.14%** | **22/36** | **1.03** |
| Morning cont + move, composite, TP=0.7% | 70.4% | Rs 42 | +0.08% | 22/36 | 0.97 |
| VWAP aligned, pmVolRate, TP=0.7% | 65.3% | Rs 29 | +0.06% | 21/36 | 0.95 |
| VWAP+vol+move, earlyMove, TP=0.5% | 76.4% | Rs -16 | -0.03% | 21/36 | 1.03 |

**Best afternoon setup: VWAP aligned + high PM volume rate + early PM move, ranked by move size, TP=0.7%**
- 71.5% win rate, +Rs 68/day, 22/36 positive days
- Marginally profitable — the afternoon is harder than morning

## Part 5: Combined Morning + Afternoon

| TP | Morning ROC | Afternoon ROC | **Combined ROC** | Positive Days |
|---|---|---|---|---|
| 0.3% | -0.05% | -0.33% | -0.37% | 18/36 |
| 0.5% | +0.10% | -0.31% | -0.21% | 16/36 |
| **0.7%** | **+0.16%** | **-0.24%** | **-0.08%** | **20/36** |

**The afternoon session DRAGS DOWN the morning profits.** Morning alone at TP=0.7% gives +0.16% ROC, but adding afternoon makes it -0.08%.

## Honest Conclusion

**The afternoon (2-3:30 PM) is NOT as profitable as morning for momentum trading:**

1. **Lower MFE**: Avg max move 1.59% (afternoon) vs 2.01% (morning) — less room to capture
2. **The direction paradox**: Entering at 2PM blind has better MFE/MAE (2.05) than entering at 2:15 with a signal (0.90) — because by 2:15, the easy money already moved
3. **Best afternoon ROC: +0.14%/day** — marginal, not the 1-2% daily ROC you need
4. **Combined sessions lose money** — afternoon losses eat morning gains
5. **The 2PM entry with 2.05 MFE/MAE is interesting** but requires a different approach (enter at 2PM without waiting for direction confirmation)

### What MIGHT work for afternoon:
- **Enter at exactly 2:00 PM** based on morning trend + VWAP position (not afternoon price action)
- Use **TP=0.3%** (92.8% hit rate at 2PM entry) for consistent small wins
- Focus on **stocks with volume acceleration** (PM vol > 5× morning avg)
- **Don't combine with morning** — run them on separate capital

### For 5%+ daily ROC:
The afternoon doesn't add the ROC you need. The path to 5% is:
1. **Morning session optimization** (better stock selection, volume rank mode)
2. **Increase capital** (5× capital = 5× absolute returns)
3. **Options trading** instead of equity (10-50× leverage on same move)
