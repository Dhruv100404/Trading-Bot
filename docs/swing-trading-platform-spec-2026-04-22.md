# Swing Trading Platform Spec

Date: 2026-04-22

## Objective

Pivot this project from a 40-minute intraday auto-trader into a swing-trading research and paper-trading platform for Indian equities.

The new product should:

- surface swing candidates, not intraday scalps
- explain every pick with evidence and historical context
- support paper trading as the default execution mode
- reuse the existing market data, ClickHouse storage, Docker setup, and analysis work where useful
- make the UI feel like a serious decision-support terminal, not a strategy config screen

## Product Direction

### Core product statement

This should become a swing-trading workspace where a user can:

1. scan the market for swing candidates
2. inspect why a stock qualified
3. see pattern evidence, risk levels, and regime context
4. place paper trades with planned entries, stop-losses, and targets
5. review journaled outcomes to improve the model over time

### What changes from the current system

The current repo is built around one intraday thesis:

- minute-bucket gap logic
- same-day entries and exits
- live event feed for intraday signals
- config objects and tables named around `gap15`

The new system should instead optimize for:

- end-of-day and daily swing analysis
- multi-day holding periods
- watchlist curation by setup quality and regime fit
- thesis-backed recommendations
- paper portfolio management

## Recommended Architecture

### Decision

Use a Docker-first modular monolith:

- `ui`: React + Vite + Tailwind frontend
- `engine`: Rust Axum API for product logic, read models, explainability payloads, and paper trading
- `research`: Python batch worker for feature engineering, backtests, scoring refreshes, and model/report generation
- `clickhouse`: analytical and operational store

This is better than splitting into many services right now because:

- the repo already has a strong Rust backend and Docker baseline
- the research layer is already Python-heavy and should stay batch-oriented for now
- swing trading does not require low-latency microservice complexity
- a modular monolith is much easier to rebuild cleanly and reason about

### Service layout

```text
React UI
  |
  v
Rust API / Orchestrator
  |-- candidate scan read models
  |-- thesis/explainability assembly
  |-- paper portfolio + orders
  |-- websocket updates
  |
  +--> ClickHouse
  |
  +--> Python research worker
          |-- EOD feature generation
          |-- pattern backtests
          |-- score calibration
          |-- nightly candidate refresh
```

### Why this fits the current repo

Keep and reuse:

- Docker setup in `docker-compose.yml`
- Rust API foundation in `engine/src/api`
- ClickHouse schema and access patterns
- account, position, and paper/live mode concepts
- analysis scripts under `analysis/` and `strategies/`
- watchlist source files in `data/`

Replace or heavily refactor:

- `gap15`-specific config, endpoints, labels, and tables
- minute-bucket-first dashboard mental model
- intraday-only performance views
- signal objects that assume same-day exit logic

## Target Domain Model

### Main concepts

- `market_regime`: broad state such as bullish trend, pullback, distribution, mean-reversion, high-volatility
- `setup_family`: swing pattern family such as breakout, pullback-to-support, volatility contraction, reclaim, relative strength continuation
- `candidate`: a stock currently qualifying for review
- `thesis`: the human-readable explanation of why the candidate exists
- `evidence_snapshot`: the numeric features and rule hits behind the thesis
- `paper_trade_plan`: intended entry zone, stop, targets, invalidation, and position size
- `paper_position`: active simulated trade
- `trade_journal`: outcome review and post-trade notes

### Recommended tables

Add new tables instead of stretching `signals` and `gap15_config` beyond recognition.

- `trading.market_bars_daily`
  Daily OHLCV and derived regime inputs per symbol.
- `trading.market_features_daily`
  Derived features such as ATR, relative strength, EMA stack, volume expansion, gap context, support/resistance proximity, squeeze state.
- `trading.swing_setups`
  Candidate setup rows for each symbol/date/setup family with score, state, and freshness.
- `trading.swing_evidence`
  Rule hits, factor contributions, confidence, historical analog stats, and narrative fragments.
- `trading.swing_models`
  Versioned scoring configs and thresholds.
- `trading.paper_trade_plans`
  Planned entries, stop, targets, max risk, expected R multiple, and rationale.
- `trading.paper_orders`
  Simulated order intents and fills.
- `trading.paper_positions`
  Open and closed positions with mark-to-market history.
- `trading.trade_journal`
  Outcome review, exit quality, setup quality, notes, screenshots/links if added later.
- `trading.scan_runs`
  Metadata for nightly scans, backfills, and model refresh jobs.

### Keep but repurpose

- `watchlist`: keep, but make it swing-oriented with tags like `core`, `earnings-risk`, `high-beta`, `sector-leader`
- `accounts`: keep for future broker integration, but default product behavior should stay on `PAPER`
- `daily_ref`: keep as a useful daily reference layer

## Swing Analysis Engine

### Setup families for v1

Start with a limited set of explainable swing families:

1. Breakout continuation
2. Pullback to trend support
3. Base breakout after compression
4. Relative strength leader
5. Gap-and-hold continuation
6. Oversold reclaim for mean reversion

### Scoring model

Each setup should be scored with a weighted evidence model, not a black box:

- trend alignment
- relative strength vs index/sector
- volume confirmation
- volatility regime fit
- distance from support/resistance
- breakout freshness
- risk/reward quality
- historical win rate of similar examples

The final recommendation payload should include:

- `setup_score`
- `confidence_band`
- `regime_fit_score`
- `risk_reward_score`
- top 3 supporting reasons
- top 2 risks / invalidation reasons
- historical analog summary

### Explainability requirement

Every candidate shown in the UI must answer:

- Why this stock?
- Why now?
- What invalidates the idea?
- What is the expected hold duration?
- What historical evidence supports this setup family?

## Paper Trading Design

### Default trading mode

Paper trading is the default and primary mode.

The user should be able to:

- create a paper portfolio
- accept a suggested trade plan
- edit entry, stop, and targets before confirming
- simulate fills using daily/intraday data rules
- track open risk, realized P&L, win rate, expectancy, and setup-family performance

### Paper execution rules

Paper execution must be deterministic and auditable:

- planned order type: market, limit on pullback, breakout stop-entry
- fill simulation based on available candles
- slippage model configurable by liquidity bucket
- carry positions across days until exit or invalidation
- partial exits supported in the data model, even if hidden in v1 UI

### Exit logic

Support both manual and rules-based exits:

- stop-loss
- target 1 / target 2
- trailing stop
- time stop
- thesis invalidation
- discretionary close

## UI Specification

### Design direction

The UI should feel like a swing trading command center:

- confident, sharp, and information-dense
- built around idea review, not parameter tweaking
- optimized for daily workflow on desktop first, but still usable on mobile

### Primary navigation

Recommended navigation:

1. `Home`
   Market regime, scan summary, top opportunities, risk radar.
2. `Scanner`
   Filterable candidate table with score, setup family, sector, liquidity, and freshness.
3. `Idea Detail`
   Full thesis view for a stock.
4. `Paper Portfolio`
   Plans, positions, orders, exposure, realized and unrealized P&L.
5. `Research`
   Setup-family analytics, backtests, calibration, and evidence reports.
6. `Watchlists`
   Curated lists and tags.
7. `Settings`
   Data refresh, paper execution defaults, broker config, model version.

### Home screen

Must show:

- market regime card
- today's swing candidates
- setup-family distribution
- sectors with strongest breadth
- risk warnings like earnings proximity, high gap risk, weak market breadth
- recent paper portfolio performance

### Scanner screen

Main table columns:

- symbol
- setup family
- score
- confidence
- regime fit
- sector
- daily trend state
- entry zone
- stop
- target
- expected R
- average hold duration
- last refresh time

Essential filters:

- setup family
- bullish / bearish / mean-reversion
- sector
- liquidity bucket
- score range
- ATR / volatility range
- earnings event proximity
- watchlist membership

### Idea detail screen

This is the centerpiece of the product.

Sections:

- thesis summary
- chart with levels and annotations
- why it qualified
- factor score breakdown
- historical analogs
- trade plan
- risk box
- notes / journal

### Paper portfolio screen

Must support:

- pending plans
- open positions
- closed trades
- exposure by setup family and sector
- R-multiple distribution
- streaks and expectancy

### Research screen

Use the existing analysis DNA here.

Show:

- setup-family performance over time
- regime breakdown
- win rate by holding duration
- entry quality analysis
- stop and target sensitivity
- best and worst examples

## API Design

### Read APIs

- `GET /api/swing/home`
- `GET /api/swing/scanner`
- `GET /api/swing/candidates/:symbol`
- `GET /api/swing/research/summary`
- `GET /api/paper/portfolio`
- `GET /api/paper/plans`
- `GET /api/paper/positions`
- `GET /api/paper/journal`

### Write APIs

- `POST /api/paper/plans`
- `PATCH /api/paper/plans/:id`
- `POST /api/paper/orders/simulate`
- `POST /api/paper/positions/:id/close`
- `POST /api/swing/watchlists`
- `PATCH /api/swing/watchlists/:id`
- `POST /api/research/refresh`

### Websocket events

- scan completed
- candidate updated
- paper order filled
- stop/target triggered
- portfolio metrics updated

## Research Worker Responsibilities

The Python worker should own batch and offline jobs:

- convert raw/parquet history into daily features
- compute setup candidates
- run walk-forward tests
- refresh setup-family score calibration
- generate regime summaries and evidence bundles
- store outputs into ClickHouse

This keeps heavy research loops out of the request path while still reusing the current `analysis/` and `strategies/` assets.

## Docker-Only Deployment

### Target compose stack

Recommended services in `docker-compose.yml`:

- `clickhouse`
- `engine`
- `research`
- `ui`

Optional later:

- `scheduler` if nightly jobs should be isolated from the API container

### Volume strategy

- mount `parquets/` read-only into `research`
- persist ClickHouse data in named volumes
- persist generated reports in a mounted `results/` or `artifacts/` path

### Environment variables

Add swing-specific env vars:

- `APP_MODE=paper`
- `SCAN_SCHEDULE_CRON`
- `DEFAULT_RISK_PER_TRADE`
- `DEFAULT_MAX_OPEN_POSITIONS`
- `SLIPPAGE_PROFILE`
- `MODEL_VERSION`

## Reuse Plan for Existing Code

### Reuse now

- `engine/src/api` routing structure
- ClickHouse client and schema migration pattern
- account and position concepts
- existing watchlist data sources
- analysis scripts as the starting point for swing setup research

### Refactor

- `ui/src/App.tsx` navigation and page model
- current frontend API typing around `Signal` and `Gap15Config`
- `engine/src/api/backtest.rs` so it becomes setup-family aware instead of gap-only
- any modules that assume same-day `TP/SL/TIME` exits only

### Deprioritize

- intraday websocket event feed as the main product surface
- bucket-based config editing UI
- strategy labels tied to 9:15-10:00 behavior

## Delivery Phases

### Phase 1: Foundation

- define new ClickHouse tables
- add daily feature pipeline
- add swing candidate and evidence endpoints
- replace UI shell and navigation

### Phase 2: Scanner + Thesis

- ship home, scanner, and idea detail pages
- ship explainability payloads
- wire historical analog summaries

### Phase 3: Paper Trading

- add trade plans, orders, positions, and journaling
- add mark-to-market updates and portfolio analytics

### Phase 4: Research Console

- add setup-family comparison, regime analysis, and calibration dashboards
- add one-click refresh jobs

### Phase 5: Broker Bridge

- keep live mode secondary until paper metrics are stable
- broker execution should reuse paper-trade plan objects, not bypass them

## Success Criteria

The rebuild is successful if:

- the app surfaces high-quality swing ideas every day with clear reasons
- users can inspect, accept, and manage paper trades end to end
- every recommendation is evidence-backed and auditable
- the UI feels purpose-built for swing trading
- the system runs fully through Docker without local ad hoc setup

## Immediate Implementation Recommendation

Build this as a swing platform, not a renamed intraday app.

Concretely:

- keep Rust as the serving/orchestration layer
- keep Python for research and nightly scans
- keep ClickHouse as the shared store
- rebuild the UI navigation around scanner, thesis, and portfolio
- create new swing tables rather than mutating `gap15` tables into something ambiguous
