# Deep Scoring Analysis — 2026-03-27

## Bug Found: pm2 scoring mismatch

The Rust engine used `config.min_move_pct` (0.7%) for pm2 threshold, requiring 1.4% move.
The UI used per-direction `buy/sell_min_move_pct` (0.25%), requiring only 0.5% move.
**Result**: Live system scored signals 8 instead of 10 → picked different (worse) stocks.
**Fix**: Changed Rust to use `dir_min_move` matching UI logic.

## Factor Rankings (consistent across both periods)

| Rank | Factor | Feb-Mar Lift | Dec-Jan Lift | Verdict |
|---|---|---|---|---|
| 1 | **pm** (move >= min) | +9.1% | +13.2% | **STRONGEST predictor** |
| 2 | **pm2** (move >= 2×min) | +6.4% | +10.5% | **Strong** |
| 3 | **vwap** (price vs VWAP) | +6.2% | +5.6% | **Strong** |
| 4 | vol (volume threshold) | +3.4% | +4.8% | Moderate |
| 5 | body (candle body > 0.6) | +2.7% | +3.5% | Moderate |
| 6 | vol2 (double volume) | +3.0% | +3.5% | Moderate |
| 7 | gap (gap continuation) | +3.9% | **-1.5%** | **INCONSISTENT** |
| 8 | gapSmall (|gap| < 2%) | **-7.7%** | **-6.5%** | **ANTI-signal** |
| 9 | consistentDir | -0.5% | -0.4% | Useless |

## Key Findings

1. **pm and pm2 are the most predictive** — price movement is the #1 predictor of TP hit
2. **VWAP cross is #3** — institutional flow indicator, consistent both periods
3. **Gap continuation is INCONSISTENT** — helps in Feb-Mar (+3.9%), hurts in Dec-Jan (-1.5%)
4. **gapSmall is ANTI-signal** — stocks with bigger gaps (>2%) actually have HIGHER TP hit rates
5. **No single factor strongly predicts** high MFE — all factors are "weak" predictors individually

## Alternative Scoring Systems

| System | Feb-Mar ROC | Dec-Jan ROC | Feb-Mar Pos Days |
|---|---|---|---|
| Current (pm2+2,vol2+2,vwap,gap,body) | -2.68% | -2.19% | 11/36 |
| **MFE-optimized** | **-0.59%** | **-1.42%** | **16/36** |
| **Anti-gap** | **-0.82%** | **-0.93%** | **19/36** |
| Consistency-focused | -1.38% | -1.39% | 16/36 |

**MFE-optimized** (pm:2, pm2:2, vol:1, vol2:2, vwap:2, body:2, highVR:1, consistentDir:2) performs best — almost breakeven vs -2.68% for current system.

**Anti-gap** (penalize gap instead of rewarding) has the most positive days (19/36 and 22/42).

## The Hard Truth

Even the best scoring system with cherry-pick top-10 from ~1800 candidates is **still negative** in backtest. The scoring improves selection but doesn't overcome the fundamental issue: selecting 10 from 1800 (0.5% acceptance) isn't selective enough. The 100% win rate needed 10 from 15,000+ (0.07% acceptance).
