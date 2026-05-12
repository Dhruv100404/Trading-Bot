# Swing Research Assistant Plan

Date: 2026-05-10

This is a research framework for Indian cash-equity swing trading. It is not stock advice and does not recommend any specific symbol. Every rule below must be treated as a hypothesis until it survives data quality checks, costs, walk-forward validation, and paper trading.

## Current Dataset Status

Existing research output under `docs/quant_research_outputs/` scanned:

- 482,245,704 intraday OHLCV rows
- 1,342 symbols
- Date range: 2021-01-01 to 2026-04-30
- Buckets per session: 1 to 375
- Missing OHLC rows: 0
- Non-positive OHLC rows: 0
- Bad OHLC relationship rows: 1
- Duplicate symbol/date/bucket rows: 0
- Zero-volume rows: 71,202,089, about 14.76 percent

Research impact:

- Zero-volume buckets must be handled before trusting volume features.
- Bid/ask spread is not available, so spread and slippage are model assumptions.
- Adjusted/raw price status is not proven from schema; corporate actions remain a risk.
- Survivorship bias is possible unless the symbol universe is proven point-in-time.

## Mandatory Data Quality Gates

Before testing any strategy:

- Remove rows with missing symbol, date, or OHLC.
- Remove impossible candles where high is below open/close or low is above open/close.
- Flag, do not blindly delete, abnormal gaps above 20-25 percent until corporate actions are checked.
- Require enough valid buckets or a complete daily candle before using a symbol/day.
- Exclude illiquid symbols before strategy testing, not after.

Suggested daily liquidity filters:

- Minimum close price: Rs 50 or Rs 100.
- 20-day average volume: at least 100,000 shares.
- 20-day average traded value: at least Rs 2 crore for research, preferably Rs 5 crore for live trading.
- Avoid symbols where zero-volume days or partial sessions are frequent.
- Cap order size to a small fraction of average traded value, such as 0.25-0.50 percent.

## Corporate Actions

For splits and bonuses:

- Use adjusted OHLC history when possible.
- If only raw data is available, detect extreme gaps and verify against corporate-action records.
- Do not treat split/bonus gaps as trading signals.

For dividends:

- Cash dividends can create smaller ex-date gaps.
- For short holding periods, large dividends should be flagged because raw OHLC can distort gap and stop logic.

For symbol changes:

- Map old and new symbols using ISIN/security id where possible.
- Avoid counting the same company as two separate histories.

## Cost Assumptions

Use at least three scenarios:

| Scenario | Fee per side | Slippage per side | Use |
|---|---:|---:|---|
| Optimistic | 4 bps | 3 bps | Sanity check only |
| Base | 8 bps | 5 bps | Default research case |
| Stress | 8 bps | 15 bps | Robustness gate |

Costs to include before trusting results:

- Brokerage
- STT
- Exchange transaction charges
- SEBI charges
- GST on brokerage and exchange charges
- Stamp duty on buy side
- Slippage
- Bid-ask spread
- Impact cost for less liquid names

## Strategy Hypotheses

### A. Trend Pullback

- Market regime: Nifty 500 above 100/200 DMA, realized volatility not extreme.
- Universe: liquid cash equities only.
- Entry: close above EMA50 and EMA100, pullback near EMA20, RSI14 between 38 and 58.
- Exit: ATR target, trailing stop, or 8-12 session time stop.
- Stop: below pullback low or 1.2-1.5 ATR.
- Sizing: risk 0.25-0.50 percent of capital per trade.
- Max trades: 5-10 open positions.
- Do not trade: below index 200 DMA, huge gap-up entries, poor liquidity.

### B. Breakout With Volume Confirmation

- Market regime: broad market above 100/200 DMA.
- Universe: liquid stocks with clean trend structure.
- Entry: close above prior 20/55-day high with relative volume above 1.25-1.5.
- Exit: ATR target, failed breakout close, or 10-15 session time stop.
- Stop: below breakout day low or 1.5-1.8 ATR.
- Do not trade: during high VIX, after very extended multi-day rallies, or on abnormal corporate-action gaps.

### C. Relative Strength Versus Nifty 500

- Market regime: neutral to bullish.
- Universe: top liquid stocks ranked by 3-month and 6-month relative strength.
- Entry: stock near high while Nifty 500 is stable or improving.
- Exit: relative strength breakdown, ATR stop, or 10-20 session time stop.
- Stop: 1.2-1.8 ATR.
- Do not trade: if strength comes from one abnormal candle or illiquid spike.

### D. Mean Reversion In Strong Uptrend

- Market regime: index above 200 DMA, stock above 200 DMA.
- Universe: liquid uptrend stocks.
- Entry: short-term oversold reading such as RSI2 below 5 or close stretched below EMA20 by ATR.
- Exit: mean reversion to EMA20, RSI recovery, target, or 3-7 session time stop.
- Stop: hard ATR stop; no averaging down.
- Do not trade: falling 200 DMA, weak sectors, news-driven breakdowns.

### E. Moving-Average Compression And Expansion

- Market regime: broad market not in panic.
- Universe: liquid stocks with narrowing range and MAs converging.
- Entry: compression followed by close above short-term resistance with volume confirmation.
- Exit: ATR target, close back inside range, or 8-12 session time stop.
- Stop: below compression range low.
- Do not trade: low-volume fake breakouts or extended gap entries.

### F. Sector Momentum Rotation

- Market regime: bullish or sideways with sector dispersion.
- Universe: strongest sectors by 1-month and 3-month return, then strongest liquid stocks inside them.
- Entry: pullback or breakout only inside leading sectors.
- Exit: sector rank deterioration, stock stop, or 10-20 session time stop.
- Stop: ATR-based.
- Do not trade: when all sectors are weak or correlation is very high.

### G. Failed Breakdown / Reversal

- Market regime: bullish or recovering market.
- Universe: liquid stocks above long-term trend or reclaiming it.
- Entry: stock breaks prior 20-day low intraday/daily but closes back above support/VWAP context.
- Exit: reclaim follow-through target, ATR target, or 3-8 session time stop.
- Stop: below failed breakdown low.
- Do not trade: true distribution trends, high leverage names, or unresolved bad news.

## Current Backtest Findings From Existing Output

Strict gate result:

No robust strategy was found under the tested assumptions.

Best research candidates, not live-ready systems:

1. `atr_stretch_reversal`
   - Trades: 2,560
   - Base profit factor: 1.348
   - Base expectancy: 0.839 percent per trade
   - Stress profit factor: 1.255
   - Max drawdown proxy: -12.96 percent
   - Failure: out-of-sample profit factor was 0.939, so it must not be promoted yet.

2. `nr7_breakout_close`
   - Trades: 1,450
   - Base profit factor: 1.120
   - Base expectancy: 0.318 percent per trade
   - Stress profit factor: 1.043
   - Max drawdown proxy: -8.82 percent
   - Failure: base and stress profit factors are too weak, though OOS was better.

3. `low_volume_pullback_continuation`
   - Trades: 24,199
   - Base profit factor: 1.055
   - Base expectancy: 0.128 percent per trade
   - Failure: very high drawdown proxy and poor OOS behavior.

Avoid for now:

- Plain 20/55-day breakouts without stronger filters.
- Broad trend pullbacks that generate too many mediocre trades.
- RSI2 mean reversion without stricter regime/sector filters.
- Gap-down reversal as a general rule.
- Squeeze breakouts without better selectivity.

## Validation Rules

Use:

- 60 percent in-sample
- 20 percent validation
- 20 percent out-of-sample
- Year-by-year results
- Walk-forward testing
- Parameter sensitivity around selected candidates
- Cost scenario comparison
- Buy-and-hold Nifty 50/Nifty 500 benchmark comparison
- Symbol and sector contribution checks

Reject a strategy if:

- OOS profit factor is below 1.0.
- Stress-cost result is negative.
- Profit comes from one year or a few symbols.
- It needs exact weird parameters.
- It fails after small parameter changes.
- Max drawdown is not acceptable for the trader.
- It depends on unverified corporate-action or bad-volume data.

## Paper-Trading Checklist

Before real capital:

- Paper trade 30-50 valid signals.
- Record signal date, planned entry, actual next-open price, slippage, stop, target, exit, and reason for exit.
- Record rejected trades and why they were rejected.
- Compare live fills with backtest assumptions.
- Stop paper testing if slippage is much worse than the model.
- Continue only if rules are followed without discretion.
- Start real trading only with reduced size after paper results roughly match research behavior.

Suggested live-risk starting limits after paper validation:

- Risk per trade: 0.25 percent of capital.
- Max open positions: 5.
- Max sector exposure: 25-30 percent.
- Max daily new entries: 2-3.
- Stop trading a strategy temporarily after 8-10 consecutive losses or if drawdown exceeds the tested range.

## 2026-05-10 Second-Pass Update

The main research pipeline was extended with extra features and seven new second-pass hypotheses:

- Relative-strength EMA20 pullback
- Near-52-week-high relative-strength pullback
- Failed 20-day breakdown reclaim
- ATR stretch leader reclaim
- Volatility dry-up relative-strength breakout
- Defensive NR7 leader breakout
- Post-gap reclaim swing

The full pipeline completed successfully after these additions. The new batch did not produce a clean live-ready strategy under the strict gates. Best results from the updated main run:

| Strategy | Trades | Profit factor | Expectancy | Max DD proxy | Verdict |
|---|---:|---:|---:|---:|---|
| `atr_stretch_reversal` | 2,560 | 1.348 | 0.839% | -12.96% | Reject for now: OOS PF below 1.0 |
| `atr_stretch_leader_reclaim` | 1,185 | 1.262 | 0.549% | -13.54% | Reject for now: OOS PF below 1.0 |
| `nr7_breakout_close` | 1,453 | 1.129 | 0.340% | -8.82% | Reject for now: base PF below 1.25 |
| `near_52w_rs_pullback` | 11,244 | 1.019 | 0.058% | -83.75% | Reject |
| `failed_breakdown_reclaim20` | 2,343 | 1.007 | 0.016% | -22.12% | Reject |

The best paper-test candidate in the existing second-pass research output remains:

| Strategy | Trades | Profit factor | Expectancy | Max DD proxy | OOS PF | Stress note |
|---|---:|---:|---:|---:|---:|---|
| `atr_stretch_liquid_only` | 1,881 | 1.464 | 1.042% | -13.01% | 1.061 | Passed summary gates in `second_pass_swing`; still paper-test only |

Interpretation:

- The ATR-stretch family has the strongest repeated evidence.
- Simple liquidity filtering improved the ATR-stretch setup more than fancy relative-strength filters.
- NR7 breakout is interesting but still too weak unless a better regime filter is found.
- Relative-strength pullbacks and gap reclaims generated many trades but not enough edge after costs.
- The next research step should be narrow: improve `atr_stretch_liquid_only`, not randomly add more indicators.

## 2026-05-10 Trend Reversal Breakout Check

Tested a separate trend-reversal breakout family where a stock first shows weakness/basing, then regains moving averages or failed-breakdown support and starts moving upward.

New pipeline features added:

- SMA20/SMA50/SMA100/SMA200 reclaim flags
- Recent below-SMA flags
- Prior close and prior moving-average values

Tested variants:

| Strategy | Trades | Profit factor | Expectancy | Max DD proxy | Verdict |
|---|---:|---:|---:|---:|---|
| `trend_reversal_sma50_breakout` | 1,868 | 0.788 | -0.582% | -45.94% | Reject |
| `trend_reversal_sma100_base_breakout` | 1,539 | 0.794 | -0.592% | -41.41% | Reject |
| `trend_reversal_sma200_reclaim` | 1,346 | 0.795 | -0.668% | -39.16% | Reject |
| `trend_reversal_failed_breakdown_breakout` | 158 | 1.226 | 0.514% | -3.67% | Interesting but reject: OOS failed |

Refined variants:

| Strategy | Trades | Profit factor | Expectancy | OOS PF | Verdict |
|---|---:|---:|---:|---:|---|
| `failed_breakdown_rs_turnaround` | 134 | 1.282 | 0.620% | 0.399 | Interesting historically, reject for now |
| `sma200_reclaim_no_chase` | 1,693 | 0.956 | -0.113% | 0.628 | Reject |
| `sma50_reclaim_without_breakout_chase` | 4,547 | 0.907 | -0.208% | 0.760 | Reject |
| `turnaround_55d_base_breakout` | 2,397 | 0.882 | -0.334% | 0.828 | Reject |
| `turnaround_20d_base_breakout` | 3,558 | 0.737 | -0.689% | 0.757 | Reject |

Conclusion:

- Plain trend-reversal breakouts are not working well in this dataset after costs.
- The only promising sub-pattern is failed breakdown plus reclaim, but it collapses in out-of-sample testing.
- Do not paper trade this family yet.
- If revisited, it needs an additional regime/sector catalyst or a stricter confirmation rule before promotion.

## 2026-05-10 App Backtest Wiring

The trend-reversal failed-breakdown setup was added to the app backtest runner as:

- Strategy JSON: `strategies/trend-reversal-failed-breakdown.json`
- Strategy id: `trend-reversal-failed-breakdown-v1`
- Setup family: `Trend Reversal Breakout`
- Engine mapping: `engine/src/api/backtest.rs`

App backtest run:

- Run id: `watchlist-swing-20260510-223048`
- Cache scope: watchlist-gated daily feature cache, 371 symbols
- Cache range: 2022-01-06 to 2026-05-08

Result:

| Metric | Value |
|---|---:|
| Trades | 138 |
| Win rate | 43.48% |
| Avg return per trade | 0.060% |
| Total PnL proxy | Rs 3,770.13 |
| Profit factor | 1.03 |
| Positive months | 47.83% |
| Max drawdown proxy | Rs -51,591.69 |
| Dashboard status | Fragile |

Year-wise app result:

| Year | Trades | Win rate | Avg return | PnL proxy |
|---|---:|---:|---:|---:|
| 2022 | 29 | 37.93% | -0.197% | Rs -2,895.65 |
| 2023 | 23 | 69.57% | 2.082% | Rs 23,707.35 |
| 2024 | 41 | 58.54% | 1.573% | Rs 31,838.52 |
| 2025 | 32 | 25.00% | -1.761% | Rs -28,337.25 |
| 2026 | 13 | 7.69% | -3.231% | Rs -20,542.84 |

Interpretation:

- It is now visible in Backtests.
- It is not good enough to promote.
- The edge appears concentrated in 2023-2024 and breaks in 2025-2026.

## 2026-05-10 Non-Sector App Backtest Completion

Sector momentum was intentionally skipped. The remaining requested families were wired into app Backtests using only the current watchlist daily feature cache:

- Trend continuation: existing `swing-breakout-v1` / `breakout-volume-v2`
- Pullback in strong stock: new `strong-stock-pullback-v1`
- Breakout after compression: new `compression-breakout-v1`
- Mean reversion in uptrend: `rsi10-pullback-reversion-v1` plus app proxy for `atr-stretch-liquid-only-v1`
- Failed breakdown reversal: `trend-reversal-failed-breakdown-v1`

New files:

- `strategies/compression-breakout.json`
- `strategies/strong-stock-pullback.json`
- Updated `strategies/atr-stretch-liquid-only.json`
- Updated `engine/src/api/backtest.rs`

Fresh app backtest:

- Run id: `watchlist-swing-20260510-224511`
- Cache scope: watchlist-gated daily feature cache
- Cache range: 2022-01-06 to 2026-05-08

Current app-backtest results:

| Strategy | Family | Trades | PF | Expectancy | Win rate | Status |
|---|---|---:|---:|---:|---:|---|
| `strong-stock-pullback-v1` | Pullback | 12,977 | 1.17 | 0.288% | 44.27% | Watch |
| `compression-breakout-v1` | Breakout | 1,125 | 1.08 | 0.141% | 44.71% | Fragile |
| `trend-reversal-failed-breakdown-v1` | Reversal | 138 | 1.03 | 0.060% | 43.48% | Fragile |
| `atr-stretch-liquid-only-v1` | Mean Reversion | 123 | 0.82 | -0.395% | 39.02% | Rejected |

Year-wise warning:

- `strong-stock-pullback-v1` is the only new app strategy with a `Watch` label, but it was weak in 2022 and negative in 2026.
- `compression-breakout-v1` is mildly positive overall but not stable enough.
- `atr-stretch-liquid-only-v1` did not translate well into the app proxy because the app uses current daily-cache TP/SL rules rather than the full Python ATR-exit research engine.

Interpretation:

- The app now covers all requested non-sector families.
- The only one worth monitoring in Backtests is `strong-stock-pullback-v1`.
- None of the newly mapped strategies should be treated as live-approved.
