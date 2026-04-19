# Confidence Score Dynamic Quantity — Analysis Results

## The Problem with Fixed Capital-per-Trade

Equal capital allocation treats all signals the same. But signals with score 5 have 2.21% ROC while score 1-2 have 0.6% ROC. Betting the same amount on weak signals dilutes returns.

## Confidence Score (0-5)

Five binary factors, each adds +1:

| Factor | Condition | Rationale |
|--------|-----------|-----------|
| Cheap stock | price < ₹1,000 | Mid-caps have higher SELL edge (0.25% avg vs 0.19%) |
| Sweet spot volume | vol_cum 50k-500k at entry | Not too thin, not institutional-dominated |
| Surging volume rate | vol_rate >= 500 | Momentum confirmed by aggressive trading |
| Active morning | morning_range >= 0.5% | Stock is already moving, not dormant |
| Not exhausted | \|move_pct\| < 1.0% | Big moves already consumed, reversal risk |

## SELL Signal Performance by Score (59 days, 206 stocks)

| Score | Signals | Win% | Avg Return | ROC |
|-------|---------|------|------------|-----|
| 1 | 78 | 50% | 0.121% | 0.61% |
| 2 | 245 | 53.5% | 0.115% | 0.57% |
| **3** | **268** | **60.8%** | **0.317%** | **1.59%** |
| 4 | 321 | 55.8% | 0.218% | 1.09% |
| **5** | **155** | **58.7%** | **0.441%** | **2.21%** |

## Quantity Allocation Rule

| Score | Action | Qty Multiplier |
|-------|--------|---------------|
| 0-2 | **SKIP** | 0x (don't trade) |
| 3 | Trade | 1x |
| 4 | More | 1.5x |
| 5 | Double | 2x |

## Comparison (59 days)

| Method | Signals | P&L | ROC |
|--------|---------|-----|-----|
| Flat qty | 1067 | +26,137 | 1.22% |
| Dynamic qty | 1067 | +34,614 | 1.42% |
| Skip score<3 | 744 | +22,384 | **1.50%** |

## Key Insights for BUY vs SELL

**SELL**: High volume rate = GOOD (panic selling continues, momentum real)
**BUY**: High volume rate = BAD (algo-driven spike that reverses)

Best SELL combo: small/medium move + surging volume + active morning = 1.645% avg return
Worst BUY combo: any move + surging volume + hot morning = -0.55% avg return

## Per-Stock Patterns

**Best SELL stocks**: PGEL, NATIONALUM, MCX, RECLTD, SWIGGY, INOXWIND, ETERNAL (mid-caps)
**Worst SELL stocks**: RELIANCE, ICICIBANK, KOTAKBANK, IDEA (large-caps, institutional)

Mid-cap stocks (₹200-1000) with medium liquidity (50k-500k volume) are the sweet spot.
