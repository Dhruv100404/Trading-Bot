# Strategy Format And Backtest Workflow

Date: 2026-05-09

## Current Answer

The fixed strategy definition format is JSON under `strategies/`.

The app now has a first-pass strategy registry/import flow:

- `GET /api/strategies` lists JSON strategy files.
- `POST /api/strategies/import` validates and writes `strategies/<id>.json`.
- Backtest-ready imports can run immediately when they use a supported template.
- Scanner and Paper Desk promotion still require mapped live rules and exit rules before a new strategy is treated as a live/paper-trade intake source.

This split is intentional. Backtesting a research idea should be easy; promoting it into fresh signals and Paper Desk should stay controlled.

## Python Research Lane

Use Python, not app JSON, for open-ended strategy discovery.

- Put rich strategy logic in `research/strategies/*.py`.
- Run it with `python scripts\research_backtest.py`.
- Read outputs in `docs/python_research_outputs/`.
- Keep app JSON as registry/promotional metadata until a strategy passes costs, out-of-sample checks, walk-forward windows, and paper trading.

This keeps the AI free to express real strategy logic in Python while keeping the Rust app and live scanner stable.

## Strategy JSON Format

Use this shape for any new strategy:

```json
{
  "id": "example-strategy-v1",
  "name": "Example Strategy V1",
  "description": "One clear sentence explaining the setup.",
  "enabled": true,
  "source": {
    "kind": "youtube | book | research | user",
    "title": "Source title",
    "url": "https://example.com",
    "credit": "Original author or source"
  },
  "universe": {
    "source": "watchlist",
    "market": "NSE equities",
    "min_price": 80,
    "min_avg_volume20": 100000
  },
  "entry": {
    "setup_family": "Readable Setup Family",
    "trend_filter": "close_above_sma200",
    "entry_price": "next_open",
    "rules": [
      { "field": "rsi10", "operator": "<", "value": 30 }
    ]
  },
  "exit": {
    "primary": "indicator_or_target_rule",
    "time_stop_sessions": 10,
    "exit_price": "next_open_after_exit_signal",
    "paper_proxy": {
      "take_profit_pct": 4,
      "stop_loss_pct": 4,
      "max_hold_sessions": 5
    }
  },
  "risk": {
    "capital_per_trade": 50000,
    "max_positions_per_day": 7
  },
  "notes": [
    "Any adaptation or limitation goes here."
  ]
}
```

## Engine Surfaces That Must Match

## Supported Import Templates

### Setup-Family Template

This is the fastest self-serve format. It reuses the app's existing feature engine and scores.

Required fields:

- `id`
- `name`
- `enabled`
- `entry.setup_family`
- `entry.min_score`
- `exit.take_profit_pct`
- `exit.stop_loss_pct`
- `exit.max_hold_sessions`
- `risk.capital_per_trade`

### Structured JSON Rule Template

Backtests can also execute structured `entry.rules` objects directly. Plain text rules remain notes for humans; object rules become SQL predicates against the cached daily feature row.

Supported rule shape:

```json
{ "field": "rsi10", "operator": "<", "value": 30 }
```

Supported comparison fields include:

- `open`, `high`, `low`, `close`, `volume`
- `sma20`, `sma50`, `sma200`
- `avg_volume20`, `high_20d`, `high_52w`, `low_52w`
- `rsi10`, `breakout_pct`, `distance_to_52w_high_pct`, `range_position_pct`
- `volume_ratio`, `atr14`, `avg_range_pct20`, `min_range_pct7`, `ret60`
- boolean flags: `trend_up`, `pullback_zone`, `rsi10_pullback`

Supported operators: `<`, `<=`, `>`, `>=`, `=`, `==`, `!=`, and `between`.

Examples:

```json
{ "field": "close", "operator": ">", "value_field": "sma200" }
{ "field": "volume_ratio", "operator": "between", "min": 0.7, "max": 1.5 }
{ "rule": "trend_up" }
```

When `exit.target_atr` and `exit.stop_atr` are present, the backtest runner uses ATR-based target/stop distances instead of fixed percentages. `risk.max_positions_per_day` is also applied by ranking each strategy's same-day candidates by score, then volume ratio.

Supported setup families today:

- `Breakout Setup`
- `Pullback To 20 DMA`
- `Near 52W High`
- `RSI10 Pullback Reversion`

Example:

```json
{
  "id": "my-setup-family-v1",
  "name": "My Setup Family V1",
  "enabled": true,
  "entry": {
    "setup_family": "Near 52W High",
    "min_score": 88,
    "entry_price": "next_open"
  },
  "exit": {
    "take_profit_pct": 8,
    "stop_loss_pct": 4,
    "max_hold_sessions": 12,
    "exit_price": "close",
    "same_day_conflict": "stop_first"
  },
  "risk": {
    "capital_per_trade": 50000,
    "max_positions_per_day": 5
  }
}
```

### RSI10 Pullback Template

Use this for the Larry Connors style pullback strategy:

- `entry.trend_filter = close_above_sma200`
- `entry.rsi_period = 10`
- `entry.rsi_below = 30`
- `exit.primary = rsi10_crosses_above_40`
- `exit.time_stop_sessions = 10`

RSI is calculated from close-to-close price gains and losses. It is not volume-based.

## Live Promotion Links

For a strategy to become fully live inside this app, it needs these links:

1. `strategies/<strategy-id>.json`
2. `engine/src/api/swing.rs`
   - historical screener feature calculation
   - setup family detection
   - strategy id mapping
   - paper rule mapping
   - default status
3. Backtest registry
   - setup-family imports are now read from `strategies/*.json`
   - custom expressions still need an explicit Rust/SQL mapping
   - entry condition
   - exit condition
   - diagnostics family
4. `scripts/backtest_strategy_summary.sql`
   - standalone SQL summary parity
5. `ui/src/App.tsx`
   - paper rule display
   - research card or strategy label

## RSI10 Pullback Example

The strategy added from `https://www.youtube.com/watch?v=W8ENIXvcGlQ` now follows this format:

```text
id: rsi10-pullback-reversion-v1
entry: close > SMA200 and RSI10 < 30
research exit: RSI10 > 40 or 10 sessions
paper proxy: 4% target, 4% stop, 5 trading sessions
source: strategies/rsi10-pullback-reversion.json
```

## Next Needed Upgrade

## Faster Backtests

Backtests no longer need to rebuild daily candles and rolling indicators from parquet on every run.

New flow:

1. Click `Refresh Backtest Cache`.
2. Engine reads parquet once and writes `trading.daily_backtest_features`.
3. Click `Run Backtest`.
4. Engine reads cached ClickHouse feature rows and writes `trading.backtest_trades`.
5. Dashboard reads summaries from `trading.backtest_trades`.

The expensive part is now explicit and separate. Normal runs should be much faster after cache refresh.

## Future Clean Version

The clean future version is a strategy runner that reads JSON directly and builds scanner/backtest rules from a small allowed operator set.

Until that exists, the safe workflow is:

1. Add strategy JSON.
2. Add engine feature/rule mapping.
3. Add Paper Desk rule.
4. Run scanner/fresh-signal smoke tests.
5. Run optimized backtest.
6. Promote only after paper results are visible strategy-wise.
