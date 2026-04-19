# No-Lookahead Pattern Analysis — 2026-03-28

## Methodology
- **Every feature** computed from bucket 1-3 only (9:15-9:17 IST) — KNOWN at entry time
- **Every outcome** measured from bucket 4+ (9:18 onwards) — UNKNOWN at entry
- **Zero future data leakage** — verified by checking that direction signal uses only entry-time move
- Two independent datasets: Feb-Mar 2026 (36 days) and Dec-Jan 2026 (42 days)

## Part 1: What Single Feature Best Predicts TP=0.7% Hit?

**Consistent across BOTH periods:**

| Feature | Feb-Mar Lift | Dec-Jan Lift | Consistent? |
|---|---|---|---|
| **|Gap| >= 2.9%** | **+16.7%** | **+15.4%** | YES — the strongest predictor |
| **VWAP distance >= 0.34%** | **+9.7%** | **+9.7%** | YES — identical both periods |
| **Volume accel (b2/b1) >= 0.04** | **+8.5%** | **+7.5%** | YES |
| **Volume at b3 > 0** | **+7.9%** | **+7.5%** | YES |
| **|Move| >= 0.49%** | **+7.0%** | **+6.3%** | YES |
| **C1 range >= 0.92%** | **+6.5%** | — | Moderate |
| Gap aligned (continuation) | +5.0% | — | Moderate |
| VWAP aligned | +3.8% | +3.1% | Weak |

**The #1 predictor is |Gap| > 2.9%** — stocks with big gaps have 68.8% TP=0.7% hit rate (vs 52% baseline). This is because big gap = overnight institutional decision = momentum continues.

**The #2 predictor is VWAP distance** — when price is far from VWAP in the trade direction, it means the move has institutional backing (VWAP represents average institutional entry).

## Part 2: Best Ranking Method for Cherry-Pick

| Ranking Method | Feb-Mar Win% | Feb-Mar ROC | Dec-Jan Win% | Dec-Jan ROC |
|---|---|---|---|---|
| **Volume rate (vr3)** | **63.0%** | **+0.23%** | **67.5%** | **+0.09%** |
| Total volume | 62.7% | +0.17% | — | — |
| vol*move*body | 66.4% | +0.21% | — | — |
| absMove (just biggest movers) | 49.1% | -3.08% | — | — |
| COMPOSITE (weighted all) | 56.5% | -2.25% | — | — |

**Volume rate is the best honest ranker — consistent positive ROC in both periods.**

The composite score and absMove are the WORST — picking the biggest movers or the "best scoring" stocks actually LOSES money. This is the key insight: **stocks that moved the most in 3 minutes are exhausted, not continuing.**

## Part 3: What the Top 5% Trades Look Like

The highest-MFE trades (avgMFE=5.30%) vs bottom 50% (avgMFE=0.32%):

| Feature | Top 5% | Bottom 50% | Diff | Signal |
|---|---|---|---|---|
| **Volume accel (b2/b1)** | **18.9** | **11.5** | **65%** | ★★★ |
| **Volume accel (b3/b2)** | **5.6** | **13.9** | **60%** | ★★★ (inverted!) |
| **|Move|** | **1.63%** | **1.10%** | **48%** | ★★ |
| **C1 range** | **2.06%** | **1.39%** | **48%** | ★★ |
| Volume at b3 | 35,859 | 27,010 | 33% | ★★ |

**Critical finding: Volume acceleration b2/b1 is HIGH (18.9x) but b3/b2 is LOW (5.6 vs 13.9).** This means the best trades have a volume SPIKE in bucket 2 that then settles. The spike is the institutional order hitting the market. By bucket 3, volume normalizes but price has already established direction.

## Part 4: Volume-Price Relationship (Honest Signal)

| Pattern | N | TP=0.7% Hit | TP=1.0% Hit | MFE/MAE |
|---|---|---|---|---|
| **Price UP + Vol increasing** | 2,543 | **59.2%** | **46.8%** | **1.17** |
| Price UP + Vol decreasing | 6,818 | 51.0% | 39.4% | 1.07 |
| **Price DN + Vol increasing** | 3,209 | **58.1%** | **43.3%** | 0.94 |
| Price DN + Vol decreasing | 11,374 | 53.6% | 40.0% | 1.07 |
| **VWAP aligned + vol confirm** | 3,687 | **59.4%** | **45.7%** | **1.05** |
| **All 3 aligned** | 1,756 | **59.9%** | **45.8%** | 1.03 |

**Volume increasing confirms the move.** When price moves AND volume is increasing, TP=0.7% hit rate jumps from 53% baseline to 59%. When volume is decreasing despite price moving, it's a weaker signal.

## Part 5: Best Filter + Rank Combination

**Feb-Mar (36 days):**

| Setup | Win% | ROC%/day | Positive Days |
|---|---|---|---|
| **SELL only × volRate, TP=1%** | **67.1%** | **+0.48%** | **24/36** |
| SELL only × volRate, TP=0.7% | 69.9% | +0.35% | 21/36 |
| sameDir3+vwap × volRate, TP=1% | 60.9% | +0.26% | 22/36 |
| allAligned × volRate, TP=0.7% | 70.6% | +0.25% | 22/36 |

**Dec-Jan (42 days):**

| Setup | Win% | ROC%/day | Positive Days |
|---|---|---|---|
| **SELL+vwap+vol × volRate, TP=0.7%** | **65.3%** | **+0.24%** | **26/42** |
| SELL+vwap+vol × volRate, TP=1% | 59.3% | +0.14% | 24/42 |
| vwapAligned × volRate, TP=0.7% | 68.1% | +0.13% | 24/42 |
| SELL only × volRate, TP=0.7% | 67.1% | +0.11% | 23/42 |

## The Honest Truth

**Best achievable with NO lookahead, NO future data:**

| | Feb-Mar | Dec-Jan | Combined |
|---|---|---|---|
| Strategy | SELL × volRate × TP=1% | SELL+vwap+vol × volRate × TP=0.7% | — |
| **Win Rate** | **67-70%** | **65-68%** | **~67%** |
| **Daily ROC** | **+0.35-0.48%** | **+0.11-0.24%** | **~0.25-0.35%** |
| **Positive Days** | **21-24/36** | **23-26/42** | **~60-67%** |
| **Monthly ROC** | **~7-10%** | **~3-5%** | **~5-7%** |

## Key Findings

1. **Volume rate is the single best honest ranker** — not score, not move%, not composite
2. **Big gaps (>2.9%) predict continuation** — the strongest single feature (+16.7% lift)
3. **VWAP distance predicts follow-through** — institutional flow indicator
4. **Volume-price confirmation works** but modestly (59% vs 53% baseline)
5. **Bid/ask imbalance data is EMPTY in stored snapshots** — this could be a huge missed signal
6. **SELL direction outperforms BUY** — consistent bear market edge
7. **The best combo is ~0.3% daily ROC** — honest, no lookahead, 67% win rate
8. **5% daily ROC is NOT achievable** with equity intraday without lookahead on this data

## What's Missing (potential improvements)

1. **Bid/Ask depth data** — the imbalanceAligned feature was always 0 (no depth data in snapshots). Real-time bid/ask from WebSocket could add 3-5% lift
2. **Multi-day momentum** — did this stock trend yesterday too? 2-day momentum not tested
3. **Sector correlation** — if all banking stocks are falling, individual bank stocks more likely to continue
4. **Options data** — unusual options activity predicts stock moves
5. **Pre-market data** — Dhan doesn't provide this but it would help
