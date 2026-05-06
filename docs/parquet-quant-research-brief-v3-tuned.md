# Parquet Quant Research Brief V3 - Tuned For This Repo

Date: 2026-05-05

Purpose: turn the broad research brief into a strict, repo-specific plan for the `40-minute-auto-trader-main` parquet dataset and the existing Python/ClickHouse research workflow.

This is not a promise of profit and not a live-trading recommendation. Public research is used only to generate hypotheses. The final decision must come from this dataset, with costs, chronological validation, and conservative execution.

## 1. Project Reality

### Local structure

Use the current repo structure first:

```text
parquets/                         raw parquet market data
scripts/quant_research_pipeline.py existing research runner
docs/quant_research_outputs/       current output folder
strategies/*.json                  app-facing swing strategy definitions
docker-compose.yml                 ClickHouse mounts ./parquets as read-only user files
```

Do not create a separate five-script framework unless the single pipeline becomes too hard to maintain. The first tuning pass should harden `scripts/quant_research_pipeline.py` and add focused helpers inside that file or a small `scripts/quant/` package later.

### Current data shape

The existing output says the main research dataset is:

- Root monthly files: `parquets/candles_20*.parquet`
- Rows: about 482M intraday rows
- Symbols: about 1,342
- Date range: 2021-01-01 to 2026-04-30
- Buckets: 1 to 375 per session
- Columns: `date`, `symbol`, `bucket`, `open`, `high`, `low`, `close`, `volume`, `buy_ratio`, `cum_volume`, `vwap`, `vol_rate`
- Data type: bucketed intraday OHLCV, not tick data
- Bid/ask: not available
- Known issue: many zero-volume rows; current report found about 71M zero-volume rows

Implication: prioritize Indian cash-equity swing hypotheses. Use the intraday buckets mainly to build reliable daily bars, gaps, VWAP context, volume quality, and previous-session levels. Do not spend research time on day-trading, ORB, scalping, final-window momentum, or microstructure unless the user explicitly asks later.

## 2. Source Map - Use As Idea Generators Only

Use these sources to justify why a hypothesis is worth testing, not to prove it works here:

| Theme | Source | How to use it here |
|---|---|---|
| Trend following / time-series momentum | AQR, Hurst/Ooi/Pedersen; Moskowitz/Ooi/Pedersen | Lower-priority daily/swing baseline. More natural for futures and broad assets than single-name cash equities, but still useful as a sanity check. |
| Intraday momentum | Gao/Han/Li/Zhou | Deprioritized for this project. Use only as background for open/close context, not as a primary strategy family. |
| Opening range breakout | QuantConnect ORB note; Holmberg/Lonnbark/Lundstrom | Deprioritized for this project. ORB can inspire opening-volume quality features, but the target strategy must hold overnight or across sessions. |
| Short-term reversal | Blitz/Huij/Lansdorp/Verbeek | Medium-priority only if a benchmark/index return can be joined point-in-time. Raw loser reversal alone is not enough. |
| Volatility-managed exposure | Moreira/Muir | Use as a risk and exposure filter: reduce or reject trades during extreme realized volatility regimes. |
| Backtest overfitting | Bailey/Borwein/Lopez de Prado/Zhu | Use to enforce walk-forward, parameter sensitivity, and rejection of one-parameter wonders. |
| Lookahead/survivorship bias | QuantConnect research guide | Use to document point-in-time limitations, adjusted/raw price uncertainty, and universe bias. |
| Slippage realism | Backtrader slippage concepts | Use to model optimistic/base/stress costs and conservative stop/target ordering. |

Links:

- https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463
- https://ideas.repec.org/a/eee/jfinec/v129y2018i2p394-414.html
- https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/
- https://ideas.repec.org/p/hhs/umnees/0845.html
- https://ideas.repec.org/a/eee/finmar/v16y2013i3p477-504.html
- https://www.nber.org/papers/w22208
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/research-guide
- https://www.backtrader.com/docu/slippage/slippage/

## 3. Research Philosophy For This Repo

Start from externally motivated rules, then let the parquet data reject most of them. Do not mine random combinations of time, volume, RSI, ATR, and candle shape until something looks perfect.

Every strategy object or function must declare:

- Thesis: why this market behavior could exist
- Required columns
- Required timeframe
- Long/short support
- Entry timestamp and what is known at that timestamp
- Stop, target, and max-hold logic known before entry
- Cost settings used
- What would invalidate the idea
- Why fills could be fake

Hard rule: no normal strategy is promoted only because the full-period metric looks good. It must survive costs, OOS, year-by-year, walk-forward, and nearby parameter checks.

## 4. Mandatory Data Audit Before Strategy Testing

The analyzer must produce or refresh these outputs before any backtest:

```text
docs/quant_research_outputs/schema_inventory.csv
docs/quant_research_outputs/data_quality.json
docs/quant_research_outputs/dataset_map.md
docs/quant_research_outputs/data_quality_report.md
docs/quant_research_outputs/missing_candles.csv
docs/quant_research_outputs/ohlcv_issues.csv
```

The dataset map must include:

- Files scanned and row counts
- Schema per file family
- Timestamp representation: `date` plus `bucket`
- Timezone assumption: India market sessions unless proven otherwise
- Symbol column and instrument count
- OHLCV mapping
- Timeframe/bucket inference
- Asset class inference: cash equities unless index/futures folders prove otherwise
- Session boundary inference
- Missing buckets per symbol/day
- Duplicate bars
- Impossible OHLC rows
- Zero-volume row distribution by bucket and symbol
- Adjusted vs raw price status, explicitly marked unknown if not provable
- Shorting assumption, default disabled
- Volume usability
- Bid/ask availability, currently no
- Survivorship/universe-bias risk

## 5. Strategy Priority For This Dataset

### P0 - Benchmarks and market context

Run before strategies:

- Equal-weighted next-session-open to close benchmark for the tradable universe
- Buy-and-hold proxy by symbol and equal-weighted universe
- If index parquets are valid, NIFTY/index benchmark
- Market regime summary by year, day of week, gap, range, realized volatility, and opening volume

### P1 - High-priority swing hypotheses

1. ATR stretch reversal
   - Existing report shows this as the strongest family. Retest it under stricter split, cost scenarios, walk-forward, and parameter sensitivity before paper trading.
   - Long setup: price is above long-term trend filter, but closes stretched below EMA20 by an ATR multiple with weak RSI.
   - Entry: next session open only.
   - Exit: ATR stop, ATR target, or max hold of roughly 5-10 sessions.
   - Reject if 2025-2026 OOS, stress costs, or nearby ATR/RSI variants fail.

2. Narrow range / inside bar breakout with regime filter
   - Use daily bars aggregated from the intraday files.
   - Entry next session open after compression signal.
   - Must work with nearby NR4/NR7/NR10 variants.

3. Trend pullback continuation after volatility compression
   - Use EMA20/50/100, ATR percentile, pullback to EMA20/50, next-session confirmation.
   - This maps well to the app's current swing workflow.
   - Prefer clean, interpretable pullback rules over broad moving-average sweeps.

4. Low-volume pullback continuation
   - Existing report showed positive but drawdown-heavy results; retest with stricter liquidity, trend, and volatility filters.
   - Thesis: pauses on lighter volume inside an uptrend can resume without chasing extended breakouts.
   - Reject if drawdown remains unacceptable or OOS fails.

5. Previous 20/55-day high breakout with trend and volume context
   - Retest prior-high breakouts, but require higher-timeframe trend, liquidity, and relative volume.
   - Entry next session open after a daily close signal.
   - Reject if it only works in the 2021 bull phase or dies after stress costs.

6. Gap fade or gap follow as a swing setup
   - Use `gap_pct`, day close location, VWAP relation, relative volume, and higher-timeframe trend.
   - The trade must be entered next session and held across sessions; do not turn this into an intraday gap scalp.
   - Separate small, medium, and large gaps.

7. Near-52-week-high leadership continuation
   - Aligns with the existing `strategies/near-52w-high.json` app concept.
   - Require trend quality, liquidity, relative strength, and non-extended ATR distance.
   - Reject if it is just market beta or only works in one year.

8. Volatility-scaled time-series momentum
   - Lower priority because single-name cash equities are noisier than diversified futures.
   - Use as a daily/swing benchmark with volatility exposure caps.

### P2 - Optional swing refinements

9. Swing failed breakout / daily liquidity sweep reversal
   - Test daily reclaim of prior 20/55-day high/low or previous-session high/low.
   - Entry next session open after the reclaim is confirmed on daily data.
   - Stop beyond the sweep extreme.
   - Reject if the edge depends on unknown intraday sequencing.

### P3 - Deferred until supporting data is ready

10. Residual mean reversion
   - Only test after a point-in-time benchmark/index join is reliable.
   - Better with sector mapping, which this repo does not currently prove is available.

11. Regime-switching strategy selector
   - Only test after base strategies have been evaluated alone.
   - Regime filters must be predeclared, not chosen after looking at winners.

12. Day-trading hypotheses
   - ORB, first-window to final-window momentum, intraday VWAP scalps, and session-close trades are out of scope for the current swing-only research.

## 6. What To Remove Or Deprioritize From The Broad Brief

For this repo, do not lead with:

- Futures/FX/crypto assumptions
- Options strategies
- Tick-level microstructure
- Same-candle target and stop fills
- Overnight short strategies in cash equities unless borrow/shortability is modeled
- Broad RSI/MA/Bollinger sweeps without a source-backed thesis
- Rare perfect-win scans before the normal research pass

## 7. Execution And Cost Assumptions

### Conservative execution

Daily strategy:

- Signal generated after day `D` close.
- Entry at day `D + 1` open plus slippage.
- Stops and targets evaluated only after entry.
- If stop and target both hit in the same OHLC bar, assume stop first.
- Gap through stop fills at next available price, not the desired stop.

Intraday-derived swing features:

- Intraday buckets may be used to build daily open/high/low/close, VWAP, volume, gap, range, and previous-session features.
- A swing signal is still generated only after the session is complete.
- Entry remains next session open plus slippage.
- Same-day intraday entry/exit is out of scope.

### Cost scenarios

Use configurable placeholders until broker-specific charges are wired:

| Scenario | Fee per side | Slippage per side | Use |
|---|---:|---:|---|
| optimistic | 4 bps | 3 bps | sanity check only |
| base | 8 bps | 5 bps | default, matches the current script spirit |
| stress | 8 bps | 15 bps | robustness gate |

Report all metrics by scenario. A strategy that only survives optimistic costs is not validated.

## 8. Validation Rules

Use chronological splits computed from the actual min/max date:

- In-sample: first 60 percent
- Validation: next 20 percent
- Out-of-sample: final 20 percent

For the current 2021-01-01 to 2026-04-30 range, this is roughly:

- In-sample: 2021-01 to 2024-03
- Validation: 2024-03 to 2025-04
- Out-of-sample: 2025-04 to 2026-04

Also produce:

- Year-by-year metrics for 2021, 2022, 2023, 2024, 2025, and 2026 year-to-date
- Walk-forward: train 12 months, validate/select 3 months, test 3 months, roll 3 months
- Nearby parameter sensitivity around selected candidates
- Instrument contribution: no one symbol should explain the whole result
- Time contribution: no one year should contribute more than 40 percent of PnL unless labeled crisis/regime-specific

Minimum evidence defaults:

- Candidate: 50+ trades
- Serious strategy: 100+ trades
- OOS: 20+ trades
- Base-cost profit factor: > 1.25
- Stress-cost profit factor: > 1.05
- OOS profit factor: > 1.0
- Max drawdown must be survivable and clearly reported

## 9. Rare Perfect-Win Candidate Protocol

Run this only after the normal pass is complete.

Allowed:

- Maximum 3 setup filters, 1 entry trigger, 1 exit rule
- Externally motivated filters only
- Next-day execution
- Pre-entry stop and target
- Full optimistic/base/stress costs
- OOS and walk-forward reporting

Not allowed:

- Exact minute fitting without a market reason
- Exact thresholds such as 1.732 ATR
- Dozens of conditions
- Same-day fantasy fills
- Searching until a perfect sample appears

Report each perfect candidate as:

```text
Candidate name:
Rules:
Total trades:
Trades per month:
Years active:
OOS trades:
Walk-forward survival:
Base-cost result:
Stress-cost result:
Nearest parameter variants:
Reason it might be real:
Reason it is probably fragile:
Final label: reject / watchlist / paper-test only
```

A 100 percent win rate with fewer than 30 trades is an observation, not a strategy.

## 10. Scoring

Score only after all validation files exist:

| Component | Points |
|---|---:|
| Economic intuition | 15 |
| In-sample performance | 10 |
| Out-of-sample performance | 20 |
| Walk-forward stability | 20 |
| Parameter stability | 15 |
| Cost/slippage survival | 10 |
| Trade count adequacy | 5 |
| Simplicity | 5 |

Hard reject if:

- Lookahead bias is found
- Same-day unrealistic fill is required
- OOS profit factor is below 1.0
- Stress-cost result is negative, unless explicitly labeled low-cost-only research
- PnL comes from one or two trades
- A data quality issue explains the edge
- Strategy requires shorting but shorting is not realistic for the target account

## 11. Repo-Specific Output Contract

Keep the existing outputs and add missing strict-validation outputs:

```text
docs/quant_research_outputs/schema_inventory.csv
docs/quant_research_outputs/data_quality.json
docs/quant_research_outputs/dataset_map.md
docs/quant_research_outputs/data_quality_report.md
docs/quant_research_outputs/market_behavior.json
docs/quant_research_outputs/strategy_metrics.csv
docs/quant_research_outputs/cost_scenario_metrics.csv
docs/quant_research_outputs/split_metrics.csv
docs/quant_research_outputs/year_by_year.csv
docs/quant_research_outputs/walk_forward.csv
docs/quant_research_outputs/parameter_sensitivity.csv
docs/quant_research_outputs/instrument_contribution.csv
docs/quant_research_outputs/rejected_strategies.md
docs/quant_research_outputs/rare_perfect_candidates.csv
docs/quant_research_outputs/trade_log.csv
docs/quant_research_outputs/final_report.md
docs/quant_research_outputs/charts/*.png
```

The final report must explicitly say `No robust strategy was found under the tested assumptions` if no strategy passes. That is a valid result.

## 12. Implementation Plan For This Codebase

### Phase 1 - Harden the existing pipeline

Update `scripts/quant_research_pipeline.py`:

- Extend `StrategySpec` with thesis, required columns, timeframe, direction, invalidation, execution caveat, and parameter grid metadata.
- Add cost scenario loops.
- Replace fixed 2025 OOS split with dynamic 60/20/20 chronological splits.
- Add walk-forward output.
- Add strict rejection reasons.
- Add `dataset_map.md` and `data_quality_report.md`.

### Phase 2 - Build swing features from parquet data

Add ClickHouse loaders that read only the columns and date ranges needed:

- Daily OHLCV bars aggregated from bucketed candles
- Previous day, 20-day, 55-day, and 52-week highs/lows
- ATR, realized volatility, gap size, daily range, and close location value
- EMA/SMA trend, slopes, and distance from moving averages
- Relative volume and zero-volume quality filters
- End-of-day VWAP relation as a swing context feature
- Opening gap and opening-volume summary only if they improve next-session swing entries

Do not materialize all 482M rows into pandas unless filtered or aggregated first.

### Phase 3 - Retest and implement P1 swing strategies

Implement in this order:

1. `atr_stretch_reversal`
2. `nr7_breakout_close`
3. `trend_pullback_ema20`
4. `low_volume_pullback_continuation`
5. `previous20_or_55_high_breakout`
6. `gap_fade_or_follow_swing`
7. `near_52w_high_leadership`

Do not promote any current result until it survives the new validation contract.

### Phase 4 - Promote only validated rules into the app

Only after a strategy passes:

- Convert the exact selected rule into a `strategies/*.json` app-facing definition.
- Add a paper-trading plan.
- Log expected entry, actual entry, slippage, reason for rejection, and live-vs-backtest drift.

## 13. Analyzer Prompt V3 - Paste This

```text
You are a quantitative trading researcher and backtesting engineer working inside this repo:
C:\Users\dhruv\Downloads\40-minute-auto-trader-main\40-minute-auto-trader-main

Use the attached tuned Markdown brief:
docs/parquet-quant-research-brief-v3-tuned.md

Parquet folder:
C:\Users\dhruv\Downloads\40-minute-auto-trader-main\40-minute-auto-trader-main\parquets

Primary objective:
Find realistic, robust swing trading strategies for this bucketed Indian-equity-style parquet dataset. Use intraday buckets only to build better daily/session features. Public research is only an idea source. Validate strictly on this parquet data after costs. Separately report rare perfect-win candidates only as observations unless they survive the strict protocol.

Repo constraints:
- Prefer updating scripts/quant_research_pipeline.py and docs/quant_research_outputs/ before creating a new framework.
- Use ClickHouse file() queries for large parquet scans.
- Do not load all 482M intraday rows into pandas unless the query is narrowed first.
- Keep app-facing strategies/*.json for validated deployable definitions only.
- Treat bucketed OHLCV as non-tick data.
- Disable shorting by default unless a strategy is explicitly labeled hypothetical-short.
- Do not implement intraday day-trading strategies such as ORB, final-window momentum, VWAP scalps, or same-session gap scalps unless the user asks later.

Workflow:
1. Audit every parquet family before strategy testing.
2. Build dataset_map.md, data_quality_report.md, missing_candles.csv, and ohlcv_issues.csv.
3. State timezone, session, adjusted/raw price, shorting, volume, bid/ask, and survivorship assumptions.
4. Build benchmarks before strategy tests.
5. Test P1 swing hypotheses first: ATR stretch reversal, NR7/narrow-range breakout, trend pullback compression, low-volume pullback continuation, prior-high breakout, swing gap fade/follow, and near-52-week-high leadership.
6. Use next-day execution: signal after day D close, entry at day D + 1 open plus slippage.
7. No same-day intraday entry/exit logic. Intraday buckets can only support daily/session features.
8. Run optimistic, base, and stress costs.
9. Use chronological 60/20/20 splits, year-by-year results, and walk-forward validation.
10. Show rejected strategies with specific failure reasons.
11. Run parameter sensitivity around selected candidates.
12. Only after normal testing, scan rare perfect-win candidates using the strict protocol.
13. Export all required CSV, Markdown, and chart outputs under docs/quant_research_outputs/.
14. If nothing survives, say exactly: No robust strategy was found under the tested assumptions.

Required final report sections:
A. Dataset summary
B. Data quality issues
C. Assumptions and known limitations
D. Market behavior analysis
E. Benchmarks
F. Source-inspired strategy hypotheses tested
G. Failed strategies and why they failed
H. Top validated strategies, if any
I. Exact rules for selected strategies
J. Backtest metrics by cost scenario
K. In-sample, validation, and out-of-sample results
L. Year-by-year results
M. Walk-forward results
N. Parameter sensitivity
O. Instrument and year contribution concentration
P. Rare historical perfect-win candidates, if any
Q. Why rare candidates may fail live
R. Risk management and position sizing
S. Live trading warnings
T. Paper-trading plan

Important:
- Do not promise guaranteed profit.
- Do not hide weak sample size.
- Do not use future data.
- Do not use adjusted data incorrectly.
- Do not enter and exit on the same day.
- Do not optimize hundreds of variants and only show the winner.
- Show rejected strategies too.
- Be brutally honest.
```

## 14. Runbook

Start ClickHouse first:

```powershell
docker compose up -d clickhouse
```

Run the current pipeline:

```powershell
python scripts\quant_research_pipeline.py --parquet-dir parquets --out-dir docs\quant_research_outputs --refresh-cache
```

After the V3 implementation changes are added, the same command should refresh all strict-validation outputs.
