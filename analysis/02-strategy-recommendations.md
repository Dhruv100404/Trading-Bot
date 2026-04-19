# Strategy Recommendations — Actionable Improvements

Based on analysis of 824 live signals (Feb 20 - Mar 20) and 59 days of snapshot history (Dec 29 - Mar 24) across 206 F&O stocks.

---

## THE PROBLEM

Your current system fires both BUY and SELL signals every day regardless of market context.
- **SELL signals lose money net** (-₹587 on 444 trades)
- SELL signals on the wrong days are catastrophic (Monday SELLs: -₹991 alone)
- BUY signals on wrong days also lose (Thursday: -₹232)
- **Baseline net P&L = -₹328** (at 1 qty per trade) despite a 46.5% "win" rate

---

## RECOMMENDATION 1: Market Regime Direction Filter (HIGHEST IMPACT)

**Compute "market regime" at open** = average gap% across all watchlist stocks.

| Regime | Condition | Action |
|--------|-----------|--------|
| **gap_up** | avg_gap >= 1% | **SELL only** (72% win rate, +₹256/day) |
| **flat_up** | 0% <= avg_gap < 1% | **BUY only** |
| **flat_down** | -1% <= avg_gap < 0% | **BUY only** (62% win rate) |
| **gap_down** | avg_gap < -1% | **BUY only** (mean reversion day) |

**Implementation**: At bucket 1-2, compute avg gap across all daily_ref entries.
Set a flag `market_direction` that the signal engine checks before firing.

**Impact on live data**: 351 trades (down from 824), 58.1% win rate, **+₹6,776** per 10k allocation = **66x improvement over baseline**.

---

## RECOMMENDATION 2: Day-of-Week Filter

| Rule | Rationale | Impact |
|------|-----------|--------|
| **No Thursday BUY** | 31% win rate, -₹232 net | Saves ₹232 |
| **No Monday SELL** | 17% win rate, -₹991 net | Saves ₹991 |

Combined with regime filter, these are already captured, but add them as hard rules for days when regime is ambiguous.

---

## RECOMMENDATION 3: Dynamic Quantity by Confidence Composite Score

Build a composite score (0-4) from multiple factors:

| Factor | Points | Condition |
|--------|--------|-----------|
| Entry bucket | +1 | BUY entered at bucket 8-10 |
| Day-of-week OK | +1 | Not Thursday-BUY or Monday-SELL |
| Regime match | +1 | BUY on non-gap-up / SELL on gap-up |
| Individual stock gap bonus (BUY) | +1 | Stock gap < -3% (strong mean reversion) |
| Individual stock gap bonus (SELL) | +1 | Stock gap > +1% (overextended) |

**Quantity allocation**:

| Composite | Action | Qty Multiplier |
|-----------|--------|----------------|
| 0-2 | **SKIP TRADE** | 0x |
| 3 | Trade normally | 1x |
| 4 | **Double down** | 2x |

**Impact**: 253 trades, 62.8% win rate, **+₹7,835** with dynamic qty (vs +₹7,084 flat).
Only 4 losing days out of 16 trading days.

---

## RECOMMENDATION 4: Equal-Rupee Allocation (Not Fixed Qty)

Currently: 1 share per trade regardless of price.
- ₹100 stock gets ₹100 exposure
- ₹10,000 stock gets ₹10,000 exposure

**Fix**: Allocate equal rupee amount per trade (e.g., ₹10,000 per trade).
- `quantity = floor(target_capital / entry_price)`
- Minimum 1 share

This normalizes P&L across price bands. Without it:
- Cheap stocks have high win rates but tiny P&L per share
- Expensive stocks have large P&L swings that dominate

---

## RECOMMENDATION 5: Asymmetric TP/SL Rules by Direction

### For BUY signals (time-exit is best):
- **No TP target** — let winners run to time exit
- **Trailing breakeven stop**: If trade reaches +0.3% favorable, move SL to entry price
- This saves ₹2,856 (per 10k allocation) from trades that reversed after going green

### For SELL signals (quick TP is best):
- **TP = 0.3-0.5%** — take profit quickly before mean reversion kicks in
- SELL signals that hit TP=0.3% generate +₹953 vs time-only which loses -₹3,768
- **No SL** — rely on time exit for losing SELLs

Key insight from MFE analysis:
- 73% of BUY TIME exits reach +0.3% at some point → trailing stop captures this
- 76% of SELL TIME exits reach +0.3% → quick TP captures this
- SELL signals lose because they REVERSE after initial move — exit early!

---

## RECOMMENDATION 6: Volume Rate Filter for BUY Signals

| Volume Rate at Entry | BUY Win% | Avg Return |
|---------------------|----------|------------|
| < 100 | 48.5% | +0.037% |
| 100-200 | 50% | +0.122% |
| 400-800 | 57.1% | +0.152% |
| **> 800** | **65.5%** | **+0.315%** |

**Rule**: For BUY signals, prefer volume_rate > 200. When > 800, increase qty (add +1 to composite score).

---

## IMPLEMENTATION PRIORITY

1. **Regime filter** (biggest bang, easiest to implement — just avg gap at open)
2. **Day-of-week filter** (trivial boolean check)
3. **Equal-rupee allocation** (change qty calculation)
4. **Composite score + dynamic qty** (combines 1-3 with gap/volume bonuses)
5. **Asymmetric TP/SL by direction** (different exit logic for BUY vs SELL)
6. **Trailing breakeven stop** (needs per-bucket tracking)

---

## EXPECTED COMBINED IMPACT

| Metric | Current | After Optimizations |
|--------|---------|---------------------|
| Trades/day | ~40-50 | ~15-25 (higher quality) |
| Win rate | 46.5% | 58-63% |
| Avg return/trade | +0.001% | +0.19-0.27% |
| Daily P&L (10k/trade) | ~₹2 | ~₹300-450 |
| Losing days | ~50% | ~25-30% |

---

## WHAT THIS DOES NOT ADDRESS (Future Work)

- **Stock-specific patterns**: VEDL, BHEL, TATAPOWER consistently profitable for BUY — a "stock quality" score could help
- **Intraday momentum detection**: Using bucket 1-3 price action as a real-time regime update
- **Sector rotation**: Some sectors work better on certain days
- **Position sizing by Kelly criterion**: Using historical win rates and R:R ratios
- **Multi-timeframe confirmation**: Combining 1-min and 5-min signals
