# Exact System Analysis — Matching UI Backtest Logic

## What This Analysis Does Differently

Previous analyses used approximations. This one **exactly replicates** the `Backtest.tsx` signal engine:
- Same `computeSignal()` logic (direction detection, per-direction entry windows, scoring)
- Same `computeDynamicQty()` with confidence score and direction multiplier
- Same `replaySymbolDay()` bucket-by-bucket processing (one signal per day per stock)
- Same ROC calculation: `ROC = netPnL / (totalCapital / 5)` — where `totalCapital = Σ(entry_price × quantity)`
- Same exit logic: TP priority > SL > TIME

## Config Used (your live config)

| Parameter | BUY | SELL |
|---|---|---|
| Entry window | Bucket 2-3 (9:16-9:17) | Bucket 2-4 (9:16-9:18) |
| Min move | 0.45% | 0.25% |
| Min volume | 300 | 450 |
| Min score | 4 | 4 |
| **TP** | **0% (DISABLED)** | **0% (DISABLED)** |
| SL | 1.2% | 1.8% |
| Hard exit | Bucket 35 (9:49 IST) | Bucket 71 (11:25 IST) |
| Qty multiplier | 1x | 1x |
| Base quantity | 1 share | 1 share |
| Gap filter | gap >= 0% (BUY) | gap <= 10% (SELL) |

**Critical: TP is DISABLED for both directions.** All profit is captured only at TIME or SL exit.

---

## Current System Performance

### F&O Only (what you run today — 205 stocks)

| Metric | Value |
|---|---|
| Trades (36 days) | 2,008 |
| Win rate | 57.4% |
| Net PnL | Rs 4,533 |
| Capital deployed | Rs 15,23,110 |
| Margin capital (capital/5) | Rs 3,04,622 |
| **ROC** | **1.49%** |
| Daily PnL | Rs 126/day |
| Positive days | 27/36 (75%) |
| BUY trades | 464 | SELL: 1,544 |
| Avg position size | Rs 824 (qty ~1.2 × avg price Rs 666) |

### If You Expand to All NSE (~2,400 stocks)

| Metric | F&O Only | All NSE | Non-F&O Only |
|---|---|---|---|
| Trades | 2,008 | 15,251 | 13,243 |
| Win rate | 57.4% | 52.5% | 51.7% |
| Net PnL | Rs 4,533 | Rs 6,714 | Rs 2,181 |
| ROC | **1.49%** | **0.69%** | **0.33%** |
| Daily PnL | Rs 126 | Rs 187 | Rs 61 |
| Positive days | 27/36 | 21/36 | 20/36 |
| Avg position | Rs 824 | Rs 323 | Rs 256 |

**Key finding: Expanding to all NSE DILUTES ROC from 1.49% to 0.69%.** The non-F&O stocks add 13,243 trades but only Rs 2,181 total PnL — barely positive. The ROC drops because:
1. Non-F&O stocks are cheaper (avg Rs 237 vs Rs 666) → smaller position sizes with qty=1
2. Lower win rate (51.7% vs 57.4%)
3. More negative days (only 20/36 positive vs 27/36)

### The Quantity Problem

With `quantity=1` and `buy_qty_multiplier=1`:

| Quantity | Trades | Win% | PnL | Capital | Avg PnL/Trade |
|---|---|---|---|---|---|
| qty=1 | 13,677 | 52.4% | Rs 5,238 | Rs 41,07,584 | **Rs 0.4** |
| qty=2 | 1,574 | 52.8% | Rs 1,476 | Rs 7,23,756 | **Rs 0.9** |

**Average PnL per trade is Rs 0.4.** On a Rs 317 position (1 share of a Rs 293 stock), a 0.5% move = Rs 1.58. That's the mathematical reality of qty=1.

### PnL Distribution

| Range | Trades | % |
|---|---|---|
| < Rs -10 | 512 | 3.4% |
| Rs -10 to -5 | 838 | 5.5% |
| Rs -5 to -1 | 2,560 | 16.8% |
| Rs -1 to 0 | 3,162 | 20.7% |
| Rs 0 to +1 | 3,668 | 24.1% |
| Rs +1 to +5 | 3,000 | 19.7% |
| Rs +5 to +10 | 866 | 5.7% |
| > Rs +10 | 645 | 4.2% |

**44.8% of trades make less than Rs 1 profit.** The system generates correct signals but the position size makes them meaningless in absolute terms.

---

## Impact of Enabling TP

Your biggest lever. Currently `buy_tp_pct=0, sell_tp_pct=0`:

### On F&O Stocks (current universe)

| TP | Win Rate | PnL | ROC |
|---|---|---|---|
| 0% (current) | 57.4% | Rs 4,533 | 1.49% |
| 0.3% | 88.7% | Rs 2,413 | 0.79% |
| 0.5% | 80.3% | Rs 3,699 | 1.21% |
| **0.7%** | **74.0%** | **Rs 4,785** | **1.57%** |
| **1.0%** | **68.0%** | **Rs 6,288** | **2.06%** |
| **1.5%** | **62.4%** | **Rs 7,464** | **2.45%** |

**TP=1.0% increases ROC from 1.49% → 2.06% (+38%).** TP=1.5% pushes it to 2.45%.

Note: ROC doesn't increase as dramatically as raw PnL because the denominator (capital/5) stays the same — but PnL goes from Rs 4,533 → Rs 7,464 with TP=1.5%.

### On ALL NSE Stocks (expansion)

| TP | F&O ROC | All NSE ROC | F&O PnL | All NSE PnL |
|---|---|---|---|---|
| 0% | 1.49% | 0.69% | Rs 4,533 | Rs 6,714 |
| 0.7% | 1.57% | 1.45% | Rs 4,785 | Rs 14,007 |
| 1.0% | 2.06% | 1.89% | Rs 6,288 | Rs 18,273 |
| 1.5% | 2.45% | 2.24% | Rs 7,464 | Rs 21,656 |

**With TP enabled, expanding to all NSE becomes worthwhile.** At TP=1%, ALL NSE generates Rs 18,273 vs Rs 6,288 for F&O only — 2.9x more total PnL. ROC is still slightly lower (1.89% vs 2.06%) because non-F&O stocks are cheaper, but the absolute PnL is much higher.

---

## Capital-Based Quantity Changes Everything

Your current system uses qty=1 (or 2 with confidence multiplier). What if you allocate fixed capital per trade instead?

### Without TP (current config)

| Capital/Trade | Universe | Trades | Win% | PnL | ROC | Rs/Day |
|---|---|---|---|---|---|---|
| Rs 10K | F&O | 2,008 | 57.4% | Rs 41,963 | 1.08% | Rs 1,166 |
| Rs 10K | ALL | 15,251 | 52.5% | Rs 1,04,598 | 0.35% | Rs 2,905 |
| Rs 25K | F&O | 2,008 | 57.4% | Rs 1,06,899 | 1.08% | Rs 2,969 |
| Rs 25K | ALL | 15,251 | 52.5% | Rs 2,64,829 | 0.35% | Rs 7,356 |
| Rs 50K | F&O | 2,008 | 57.4% | Rs 2,15,518 | 1.08% | Rs 5,987 |
| Rs 50K | ALL | 15,251 | 52.5% | Rs 5,32,473 | 0.35% | Rs 14,791 |

**Even without TP, Rs 25K/trade on F&O generates Rs 2,969/day — 23x more than qty=1 (Rs 126/day).**

### With TP Enabled (the optimal config)

| TP | Capital/Trade | Universe | Win% | PnL | ROC | Rs/Day | Pos Days |
|---|---|---|---|---|---|---|---|
| 0.5% | Rs 25K | F&O | 80.3% | Rs 1,07,148 | 1.08% | Rs 2,976 | 32/36 |
| **0.7%** | **Rs 25K** | **F&O** | **74.0%** | **Rs 1,36,673** | **1.38%** | **Rs 3,796** | **32/36** |
| **1.0%** | **Rs 25K** | **F&O** | **68.0%** | **Rs 1,78,221** | **1.80%** | **Rs 4,951** | **30/36** |
| 0.7% | Rs 25K | ALL | 78.0% | Rs 10,35,329 | 1.37% | Rs 28,759 | 31/36 |
| **1.0%** | **Rs 25K** | **ALL** | **71.5%** | **Rs 13,22,253** | **1.74%** | **Rs 36,729** | **30/36** |

---

## The Real ROC Story

The ROC formula in your system: `ROC = PnL / (Σ entry_price × qty / 5)`

With qty=1, the denominator is tiny (~Rs 3L for F&O), so ROC looks decent (1.49%).
With Rs 25K/trade, the denominator is huge (~Rs 99L for F&O), so ROC drops to 1.08%.

**But absolute PnL is what matters for your account.**

| Scenario | ROC | Rs/Day | What You Actually Make |
|---|---|---|---|
| Current (qty=1, no TP) | 1.49% | Rs 126 | Rs 126 |
| qty=1 + TP=1% | 2.06% | Rs 175 | Rs 175 |
| Rs 25K/trade, no TP | 1.08% | Rs 2,969 | Rs 2,969 |
| **Rs 25K/trade + TP=1%, F&O** | **1.80%** | **Rs 4,951** | **Rs 4,951** |
| **Rs 25K/trade + TP=1%, ALL** | **1.74%** | **Rs 36,729** | **Rs 36,729** |

---

## Recommendations

### Immediate (no code changes needed):

1. **Enable TP**: Set `buy_tp_pct=1.0, sell_tp_pct=1.0` in the live config
   - Increases win rate from 57% to 68% (F&O) / 71.5% (all)
   - Increases PnL from Rs 4,533 → Rs 6,288 (F&O) / Rs 18,273 (all)

2. **Increase qty_multiplier**: Set `buy_qty_multiplier=2, sell_qty_multiplier=2`
   - Doubles position size via confidence scaling (high-confidence → qty=4 instead of 2)

### Short-term (minor code change):

3. **Switch to capital_per_trade mode**: Use `capital_per_trade=25000` instead of `quantity=1`
   - Need to verify the engine uses capital_per_trade correctly (check order_executor.rs)
   - Rs 25K per position → meaningful PnL per trade

### Medium-term:

4. **Expand universe**: Add non-F&O stocks but **only Tier 1** (1,213 stocks with 70%+ win rate)
   - Don't add all 2,400 — the bottom ~200 dilute quality
   - With TP=1%: 71.5% win rate, Rs 36,729/day (all stocks, Rs 25K/trade)

5. **Blacklist 198 stocks** that consistently lose

### What NOT to do:

- Don't expand to all 2,400 stocks WITHOUT enabling TP — it dilutes ROC from 1.49% to 0.69%
- Don't increase position size without TP — larger positions × same loss rate = bigger drawdowns
- The expansion only works WITH TP enabled as the safety net
