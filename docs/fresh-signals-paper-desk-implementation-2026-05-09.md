# Fresh Signals And Paper Desk Implementation

Date: 2026-05-09

## Goal

The system now focuses on fresh strategy signals only, tags each screener result by latest data date and strategy, and automatically adds the newest valid stock suggestions into Paper Desk for tracking. Paper Desk separates system-added trades from manual trades and tracks P&L for both.

## What Changed

The implementation has four main parts:

1. Fresh signal screener
2. Strategy and date tagging
3. Automatic Paper Desk intake
4. Paper trade P&L analytics

There is also one credential improvement: the engine can now infer the Dhan client id from the JWT token when `DHAN_CLIENT_ID` is not present in `.env`.

## Fresh Signal Screener

Backend file:

`engine/src/api/swing.rs`

Endpoint:

`GET /api/swing/historical-screener`

The historical screener now works from the latest completed NSE trading day, not from the calendar day.

Example:

```text
If Monday is a market holiday, the last completed trading day is Friday.
Saturday and Sunday are skipped.
NSE holidays are skipped.
```

If parquet data is not available for the expected completed trading day, the screener falls back to the latest available parquet trading date before that target.

Flow:

1. Read daily candles from parquet files.
2. Build rolling features per stock:
   - SMA20
   - SMA50
   - average 20-day volume
   - 20-day high
   - 52-week high
   - 52-week low
3. Calculate the last completed trading day using NSE holiday logic.
4. Find the latest available parquet `trade_date` less than or equal to that completed trading day.
5. Keep only rows where `trade_date` equals that selected data date.
6. Map each row to a strategy id and strategy label.
7. Attach the latest strategy status from backtest diagnostics.

The screener response now includes:

```ts
signal_date: string | null
```

That lets the UI clearly show which data date produced the displayed signals.

## Strategy Mapping

Each screener row is mapped into a known strategy family when possible.

Current strategy ids include:

```text
momentum-core-v1
rsi10-pullback-reversion-v1
near-52w-high-runner-v2
near-52w-high-volume-v3
near-52w-high-tight-v2
near-52w-high-v1
pullback-quality-v2
pullback-20dma-v1
breakout-volume-v2
swing-breakout-v1
unlinked-screener
```

## New RSI10 Pullback Strategy

Strategy file:

`strategies/rsi10-pullback-reversion.json`

Source:

`https://www.youtube.com/watch?v=W8ENIXvcGlQ`

The new strategy is implemented as:

- Strategy id: `rsi10-pullback-reversion-v1`
- Setup family: `RSI10 Pullback Reversion`
- Trend filter: close above SMA200
- Entry signal: RSI10 below 30 on the latest completed trading day
- Entry model: next open in backtest/research logic, latest close proxy for Paper Desk staging
- Primary exit model: RSI10 above 40
- Time stop: 10 trading sessions for the research rule
- Paper Desk tracking window: 5 trading sessions
- Paper proxy risk: 4% stop and 4% target

The original training describes the rule on the S&P 500. This implementation adapts it to NSE watchlist stocks by applying the long-term trend filter to each stock with `close > SMA200`.

Current strategy statuses include:

```text
Research
Watch
Fragile
Rejected
Unlinked
```

Only `Research` and `Watch` statuses are considered fresh paper-intake candidates.

## Strategy Filtering

The historical screener accepts a new query parameter:

```text
strategy
```

Supported behavior:

```text
strategy=all
```

Shows all latest-date screener rows.

```text
strategy=fresh
```

Shows only fresh `Research` and `Watch` rows.

```text
strategy=momentum-core-v1
```

Shows only that specific strategy.

Status names such as `research` and `watch` can also be used.

Frontend type support was added in:

`ui/src/api.ts`

## Scanner UI

Frontend file:

`ui/src/App.tsx`

Scanner now defaults to fresh signals.

The scanner heading shows:

```text
Fresh signals from <latest signal date>
```

Scanner summary now shows:

- Fresh signal count
- Scanner candidate count
- Last data date
- Quote layer status

The UI includes filters for:

- Fresh signals
- All strategies
- Individual strategy labels
- Setup family
- Symbol search

## Paper Trade Rules

Frontend file:

`ui/src/App.tsx`

The UI has a local strategy-to-paper-rule map called `BACKTEST_PAPER_RULES`.

Each rule defines:

```ts
stopLossPct: number
takeProfitPct: number
source: string
```

Examples:

```text
near-52w-high-v1 -> 5% stop, 10% target
momentum-core-v1 -> 5% stop, 10% target
pullback-20dma-v1 -> 3% stop, 6% target
rsi10-pullback-reversion-v1 -> 4% stop, 4% target
swing-breakout-v1 -> 4% stop, 8% target
```

Paper trades use:

```text
5 trading sessions
```

as the default max hold period, because one trading week is five market sessions.

## Automatic Paper Desk Intake

Backend endpoint:

```text
POST /api/swing/fresh-signals
```

The unique-signal intake now belongs to the backend, not the browser. ClickHouse is the source of truth.

Auto intake only accepts candidates that satisfy all of these:

- Candidate is from the latest `signal_date`.
- Strategy status is `Research` or `Watch`.
- Strategy id has a known paper rule.
- Entry price is valid.
- Stop loss is below entry.
- Target is above entry.
- The symbol is not already open in Paper Desk.
- The same symbol and strategy were not already recorded in the signal ledger.

The max number of automatic suggestions per refresh is controlled by:

```ts
AUTO_PAPER_MAX_SUGGESTIONS = 7
```

## Signal Ledger

Backend file:

`engine/src/api/swing.rs`

ClickHouse table:

```text
trading.signal_ledger
```

The ledger is the memory layer that stops repeated daily signals from appearing as fresh.

Unique signal key:

```text
symbol | strategy_id
```

This means if the same stock keeps qualifying for the same strategy every day, it is only treated as new once.

Ledger statuses:

```text
staged
baseline
already-active
```

Behavior:

1. Run the latest completed trading-day screener.
2. Filter to paper-eligible strategy rows.
3. Check `trading.signal_ledger`.
4. Any `symbol | strategy_id` already in the ledger is considered seen.
5. Any unseen row is inserted into the ledger.
6. Only the first visible batch is staged into Paper Desk.
7. Remaining unseen current rows are marked as baseline so they do not trickle in as fake-new signals on later refreshes.

## Auto Trade Tagging

Every auto-created paper trade stores metadata inside `notes`.

Example:

```text
Auto-staged newest unique signal for 5 trading sessions.
signal_date=2026-05-09
strategy=momentum-core-v1
strategy_status=Research
signal_key=SYMBOL|momentum-core-v1
```

These tags are used to separate system-added trades from manual trades and prevent duplicate auto-staging.

Duplicate key:

```text
symbol | strategy_id
```

## Manual Paper Trades

Manual sends to Paper Desk are still allowed from the UI when the setup has:

- Valid entry
- Valid stop
- Valid target

Manual trades are tagged with:

```text
Manual paper-stage.
```

This lets Paper Desk separate manual performance from system-added performance.

## Paper Desk Analytics

Frontend file:

`ui/src/App.tsx`

CSS file:

`ui/src/index.css`

Paper Desk now has an analytics panel showing:

- System-added open count
- System-added open P&L
- System-added closed P&L
- Manual open count
- Manual open P&L
- Manual closed P&L
- Newest auto signal date

The panel labels are:

```text
System Added
Manual Added
Newest Auto Signal
```

Paper Desk also shows:

- Open trades
- Open P&L or reference P&L
- Open risk
- Closed P&L
- Capital budget
- Allocated capital
- Available capital
- At-stop risk
- Trades needing attention
- Session clock

The frontend session clock now skips weekends and NSE holidays. This prevents a Monday holiday from incorrectly advancing the paper-trade clock from Friday to Monday.

## Trading Week Strategy Analytics

Frontend file:

`ui/src/App.tsx`

Paper Desk now includes a strategy-wise trading week table.

Grouping key:

```text
trading_week_start | strategy
```

The table shows:

- Week
- Strategy
- Entries
- Active count
- Closed count
- Wins/losses
- Closed P&L
- Average return after close

This is designed to answer the actual forward-test question:

```text
For fresh signals generated in a trading week, which strategy made money and which strategy lost money?
```

## Paper Trade Backend

Backend file:

`engine/src/api/paper.rs`

Endpoints:

```text
GET    /api/paper-trades
POST   /api/paper-trades
DELETE /api/paper-trades/:symbol
POST   /api/paper-trades/:symbol/close
GET    /api/paper-budget
POST   /api/paper-budget
```

The backend now supports:

- Paper trade table creation and migrations
- Stop-loss validation
- Target validation
- Quantity and capital allocation
- Live quote enrichment through Dhan
- Historical fallback pricing from parquet
- Open P&L calculation
- Closed realized P&L calculation
- Auto stop-loss close
- Auto time-based close
- Budget snapshot

## Live Quote And Fallback Pricing

Paper Desk tries to value open trades using live Dhan quotes when available.

If live quotes are not available, it falls back to latest parquet close.

Quote source can show values like:

```text
dhan-live
last-close:<date>
parquet-history:<date>
entry
closed
```

When fallback prices are used, UI labels P&L as reference P&L.

## Auto Close Logic

Backend file:

`engine/src/api/paper.rs`

Auto close happens when either:

1. Current price is at or below stop loss.
2. Current price is at or above target.
3. The trade has reached its max session count.

For RSI10 pullback research, the strategy backtest also tracks an RSI recovery exit when RSI10 moves above 40. Paper Desk uses the practical 4% target, 4% stop, and 5-session forward-test proxy so every new scanner suggestion can be compared consistently.

Close reasons include:

```text
stop-loss
target-hit
auto-closed after 5 trading sessions
```

When closed, realized P&L is calculated as:

```text
(exit_price - entry_price) * quantity
```

## Dhan Token Handling

Backend file:

`engine/src/api/swing.rs`

Function:

```rust
resolve_dhan_credentials(...)
```

Earlier behavior required both:

```text
DHAN_ACCESS_TOKEN
DHAN_CLIENT_ID
```

Now, if `DHAN_CLIENT_ID` is missing, the engine tries to decode the client id from the JWT token claim:

```text
dhanClientId
```

This prevents the engine from falling back to an older expired token in ClickHouse when `.env` already has a fresh token.

Current verified broker status:

```text
state: ready
credential_source: environment
client_id: 1100896497
live_quotes: true
```

## Verification Done

The following checks were run:

```powershell
npm run build
docker compose build engine
Invoke-WebRequest http://127.0.0.1:8080/api/swing/broker-status
```

Results:

- UI production build passed.
- Engine Docker build passed.
- Engine container restarted successfully.
- Broker status returned `ready`.
- Live quotes are enabled.
- Historical screener returned `signal_date: 2026-05-08` with 380 rows after the date/type fix.
- Fresh signal ledger returned `eligible=51`, then returned `new=0` on the second call, confirming repeated signals are suppressed.
- Signal ledger contained 51 remembered unique strategy signals.
- Paper Desk contained 14 active paper trades after the latest staging checks.
- After adding RSI10 Pullback Reversion, the fresh endpoint returned `eligible=61`, `new=10`, and `staged=7`, confirming the new strategy entered the unique-signal intake.

## Current Changed Files

```text
engine/src/api/paper.rs
engine/src/api/swing.rs
engine/src/api/backtest.rs
engine/src/api/mod.rs
ui/src/App.tsx
ui/src/api.ts
ui/src/index.css
docs/fresh-signals-paper-desk-implementation-2026-05-09.md
strategies/rsi10-pullback-reversion.json
scripts/backtest_strategy_summary.sql
```
