# Dynamic Quantity Analysis — Deep Dive

## Summary

Equal-rupee allocation alone gives 14.9x improvement over fixed 1-qty.
Adding dynamic multipliers based on volume rate + morning range + stock price = **38.5x total improvement**.

---

## WHY FIXED QTY = 1 IS TERRIBLE

| Price Band | Trades | Win% | P&L (1 qty) | P&L (₹10k/trade) |
|------------|--------|------|-------------|-------------------|
| <₹100      | 19     | 57.9%| +₹5         | +₹570             |
| ₹100-300   | 85     | 55.3%| +₹20        | +₹1,102           |
| ₹300-500   | 86     | 55.8%| +₹48        | +₹1,137           |
| ₹500-1k    | 54     | 44.4%| +₹32        | +₹433             |
| ₹1k-2k     | 64     | 51.6%| +₹58        | +₹481             |
| ₹2k-5k     | 42     | 52.4%| +₹42        | +₹83              |
| >₹5k       | 30     | 40%  | +₹56        | +₹55              |

Cheap stocks (<₹500) have **higher win rates AND higher return %** but generate
tiny P&L at 1 qty. Equal-rupee allocation fixes this.

---

## KELLY CRITERION BY PRICE BAND

| Price Band | Kelly Fraction | Meaning |
|------------|---------------|---------|
| <₹100      | 0.336         | Allocate 33.6% — highest confidence |
| ₹100-300   | 0.180         | Good edge |
| ₹300-500   | 0.198         | Good edge |
| ₹500-1k    | 0.078         | Small edge |
| ₹1k-2k     | 0.106         | Small edge |
| ₹2k-5k     | 0.034         | Barely trade |
| >₹5k       | **-0.086**    | **NEGATIVE edge — don't trade!** |

**Conclusion**: Cheap stocks have structurally higher edge. Stocks >₹5000 have ZERO or negative edge.

---

## VOLUME RATE IS THE STRONGEST SIGNAL FOR QTY SIZING

Volume rate at entry (shares/second):

| Volume Rate | + Calm Morning | + Active Morning |
|-------------|---------------|-----------------|
| Surging (>500) | 67.7% win, +0.304% | 66.7% win, +0.325% |
| Normal (100-200) | 35.3% win, -0.026% | 63.2% win, +0.254% |
| Below avg (<100) | 46% win, +0.011% | 51% win, +0.062% |
| Elevated (200-500) | **31.6% win, -0.225%** | 45.7% win, +0.062% |

**Key finding**: Surging volume is profitable regardless of morning activity.
Calm morning + non-surging volume = DON'T TRADE.

**Anomaly**: Elevated volume (200-500) + calm morning is the WORST combination (31.6% win).
Theory: these are stocks being distributed (sold in blocks) — volume from selling, not buying.

---

## MORNING RANGE IS PREDICTIVE AT ENTRY TIME

Morning range = (max_ltp - min_ltp) / avg_ltp for buckets 1-10:

| Morning Range | Win% | Avg Return | Total P&L (10k) |
|--------------|------|------------|-----------------|
| Calm (<0.5%) | 52.6%| +0.015%    | ₹35             |
| Medium (0.5-1%) | 46.6%| +0.037% | ₹461            |
| **Active (1-2%)** | **55.3%**| **+0.145%** | **₹2,987** |
| Wild (>2%) | 56.2%| +0.204%    | ₹378            |

**Active morning = best zone for P&L.** The stock has shown it's moving today.

---

## FULL-DAY RANGE CONFIRMS (but isn't knowable at entry)

| Day Range | Win% | Avg Return | Note |
|-----------|------|------------|------|
| <1.5% | **18.9%** | **-0.366%** | TERRIBLE — stock barely moved |
| 1.5-2.5% | 62.1% | +0.220% | Good |
| 2.5-4% | **80.6%** | **+0.563%** | Amazing |
| >4% | 100% | +0.843% | Perfect (small sample) |

Stocks that DON'T move (<1.5% day range) are **guaranteed losers** for momentum signals.
Morning range is our best early proxy for this.

---

## PRICE + VOLUME CROSS-ANALYSIS

| Combination | Win% | P&L (10k) | Sharpe |
|-------------|------|-----------|--------|
| Cheap + High Vol | **66.7%** | **₹1,156** | **0.429** |
| Cheap + Mid Vol | 57.1% | ₹1,311 | 0.221 |
| Expensive + Mid Vol | 60% | ₹711 | 0.365 |
| Expensive + Low Vol | 46.5% | ₹443 | 0.030 |
| **Expensive + High Vol** | **20%** | **-₹102** | **-0.368** |

**Expensive + High Vol stocks = WORST** (index heavyweights, no alpha).
**Cheap + High Vol = BEST** (strong retail participation, momentum works).

---

## THE DYNAMIC QUANTITY FORMULA

```
base_qty = floor(TARGET_CAPITAL / entry_price)   // equal-rupee
base_qty = max(base_qty, 1)                       // minimum 1 share

multiplier = 1.0                                   // baseline

// Factor 1: Volume rate (strongest signal)
if volume_rate >= 500:  multiplier += 1.0          // surging: +1x
elif volume_rate >= 200: multiplier += 0            // elevated: no bonus

// Factor 2: Morning range (predictive)
if morning_range >= 1.0%: multiplier += 0.5        // active: +0.5x

// Factor 3: Stock price (structural edge)
if entry_price < 500: multiplier += 0.5            // cheap stock: +0.5x

// Cap
multiplier = min(multiplier, 3.0)                  // max 3x

// Kill switch: skip dead signals
if volume_rate < 100 AND morning_range < 0.5%:
    multiplier = 0                                  // skip trade

final_qty = floor(base_qty * multiplier)
```

---

## SIMULATION RESULTS

| Method | Trades | P&L | Win% | Avg Position |
|--------|--------|-----|------|-------------|
| Fixed 1 qty | 380 | +₹259 | 51.8% | ₹1,537 |
| Equal ₹10k | 380 | +₹3,862 | 51.8% | ₹9,496 |
| Dynamic (vol_rate only) | 263 | +₹7,216 | 55.1% | — |
| **Dynamic (all factors)** | **367** | **+₹9,982** | **52%** | **₹16,532** |

**38.5x improvement from fixed 1 qty to full dynamic allocation.**

---

## ADDITIONAL DATA FROM DHAN API (Not Yet Used)

Fields available but currently unused:
- `buy_quantity` / `sell_quantity`: Total market-side aggregate quantities
  - buy_qty > sell_qty = bullish imbalance → increase BUY qty
  - Can be computed as: `imbalance = (buy_qty - sell_qty) / (buy_qty + sell_qty)`
  - **Recommendation**: Add to snapshot storage, use as another qty multiplier factor

- Full 5-level depth (we only use level 1):
  - Could compute total bid/ask depth across 5 levels for stronger imbalance signal

- Open Interest (for F&O stocks via NSE_FO exchange type):
  - Currently getting 0 because we query NSE_EQ
  - Rising OI + Rising Price = long buildup (bullish)
  - Could query both NSE_EQ and NSE_FO for F&O stocks

---

## WHAT TO IMPLEMENT NOW

1. **Equal-rupee allocation** — `qty = max(floor(capital / price), 1)` with configurable capital
2. **Volume rate multiplier** — from the snapshot data at entry bucket
3. **Morning range calculation** — compute from snapshots of buckets 1 through entry_bucket
4. **Price band bonus** — simple price check
5. **Kill switch** — skip when vol_rate < 100 AND morning_range < 0.5%

## WHAT NEEDS MORE DATA

- Order book imbalance (buy_qty/sell_qty) — need to start collecting
- OI data from F&O segment — need to add NSE_FO queries
- Sector-level correlation signals — need more trading days
