# Backtest Results - 2026-05-03

Run id: `watchlist-swing-20260503-001`

Data:
- Parquet range: 2021-01-01 to 2026-04-27
- Tradable signal range after 252-day warmup: 2022-01-07 to 2026-04-27
- Universe: enabled `trading.watchlist` symbols
- Position sizing: Rs 50,000 per trade
- Entry rule: signal on day D, enter next trading day open
- Exit rule: target, stop, or max hold sessions; same-day target/stop conflict uses stop first

Important caveat:
This first pass is signal-level backtesting. It does not yet enforce `max_positions_per_day`, portfolio cash limits, brokerage, slippage, or overlapping-position prevention. Use `deployed_ret_pct` as signal quality return on deployed capital, not final portfolio CAGR.

## Strategy Summary

| Strategy | Trades | Win Rate | Avg Return | Total P&L | Deployed Return | Avg Hold |
|---|---:|---:|---:|---:|---:|---:|
| Near 52W High V1 | 43,934 | 52.97% | +0.694% | Rs 1,51,77,686 | +0.701% | 17.99 |
| Swing Breakout V1 | 11,992 | 48.85% | -0.192% | Rs -11,24,369 | -0.190% | 12.53 |
| Pullback To 20 DMA V1 | 37,644 | 48.26% | -0.220% | Rs -40,20,175 | -0.217% | 11.49 |

## Yearly Returns

| Strategy | Year | Trades | Win Rate | Avg Return | P&L | Deployed Return |
|---|---:|---:|---:|---:|---:|---:|
| Near 52W High | 2022 | 5,547 | 44.01% | -0.815% | Rs -22,12,981 | -0.806% |
| Near 52W High | 2023 | 14,275 | 61.39% | +2.228% | Rs 1,57,72,641 | +2.233% |
| Near 52W High | 2024 | 15,465 | 50.90% | +0.451% | Rs 34,33,077 | +0.451% |
| Near 52W High | 2025 | 5,970 | 49.36% | -0.140% | Rs -3,84,609 | -0.132% |
| Near 52W High | 2026 | 2,677 | 46.69% | -1.089% | Rs -14,30,441 | -1.093% |
| Pullback 20 DMA | 2022 | 5,729 | 41.65% | -1.068% | Rs -30,14,796 | -1.063% |
| Pullback 20 DMA | 2023 | 11,360 | 56.35% | +0.832% | Rs 46,76,024 | +0.833% |
| Pullback 20 DMA | 2024 | 10,424 | 46.85% | -0.370% | Rs -19,05,706 | -0.371% |
| Pullback 20 DMA | 2025 | 8,440 | 46.04% | -0.542% | Rs -22,24,759 | -0.537% |
| Pullback 20 DMA | 2026 | 1,691 | 36.01% | -1.877% | Rs -15,50,937 | -1.869% |
| Swing Breakout | 2022 | 2,037 | 44.97% | -0.924% | Rs -9,30,219 | -0.922% |
| Swing Breakout | 2023 | 3,871 | 55.57% | +0.813% | Rs 15,53,607 | +0.812% |
| Swing Breakout | 2024 | 2,875 | 46.40% | -0.546% | Rs -7,78,643 | -0.551% |
| Swing Breakout | 2025 | 2,652 | 47.25% | -0.296% | Rs -3,85,221 | -0.296% |
| Swing Breakout | 2026 | 557 | 36.62% | -2.171% | Rs -5,83,894 | -2.137% |

## Drawdown And Day Quality

| Strategy | Trading Days | Positive Days | Worst Day | Best Day | Max Cumulative Drawdown |
|---|---:|---:|---:|---:|---:|
| Near 52W High V1 | 1,050 | 60.95% | Rs -3,56,590 | Rs 2,73,441 | Rs -58,87,666 |
| Swing Breakout V1 | 1,003 | 51.74% | Rs -1,53,828 | Rs 1,03,669 | Rs -19,10,369 |
| Pullback To 20 DMA V1 | 1,052 | 50.19% | Rs -1,95,739 | Rs 1,80,964 | Rs -61,97,200 |

## Exit Quality

Near 52W High made money because TP winners were large enough to pay for many time exits:

| Exit | Trades | Avg Return | P&L |
|---|---:|---:|---:|
| TP | 10,694 | +10.000% | Rs 5,28,52,054 |
| SL | 2,608 | -5.000% | Rs -64,51,765 |
| TIME | 30,632 | -2.070% | Rs -3,12,22,603 |

Main weakness: too many time exits bleed. The next improvement should be a better time exit, trailing stop, or regime filter.

## Best Stocks

Top contributors, minimum 20 trades:

| Symbol | Strategy | Trades | Win Rate | P&L | Avg Return |
|---|---|---:|---:|---:|---:|
| MCX | Near 52W High | 249 | 64.66% | Rs 4,15,890 | +3.380% |
| LUPIN | Near 52W High | 187 | 78.61% | Rs 3,73,069 | +4.053% |
| CUMMINSIND | Near 52W High | 281 | 69.04% | Rs 3,61,743 | +2.650% |
| ANANTRAJ | Near 52W High | 293 | 67.24% | Rs 2,97,632 | +2.034% |
| LTF | Near 52W High | 226 | 61.06% | Rs 2,86,553 | +2.540% |

## Worst Stocks To Filter

Worst contributors, minimum 20 trades:

| Symbol | Strategy | Trades | Win Rate | P&L | Avg Return |
|---|---|---:|---:|---:|---:|
| BLS | Near 52W High | 91 | 52.75% | Rs -2,56,095 | -5.653% |
| PFOCUS | Near 52W High | 83 | 18.07% | Rs -2,35,636 | -5.688% |
| IDBI | Near 52W High | 117 | 34.19% | Rs -1,98,526 | -3.397% |
| ASHAPURMIN | Near 52W High | 142 | 42.96% | Rs -1,58,906 | -2.258% |
| BAJAJFINSV | Near 52W High | 124 | 25.00% | Rs -1,52,940 | -2.514% |

## Takeaway

The first profitable swing idea is not breakout or pullback. It is leadership continuation: stocks already close to 52-week highs.

The strategy is not stable enough yet because 2025 and early 2026 are negative. The next pass should add:

1. Market regime filter using NIFTY/BANKNIFTY parquet trend.
2. Portfolio cap: max 5 positions/day from highest score only.
3. Exclude historically bad symbols from the strategy universe.
4. Replace fixed time exit with trailing stop or break-even stop.
5. Run parameter sweep for TP/SL/hold windows.
