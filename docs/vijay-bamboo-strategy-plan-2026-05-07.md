# Vijay / Bamboo-Style MTF Breakout Plan

Date: 2026-05-07

## Quick Take

The public Vijay Thakkar "Bamboo Method" maps closely to a trend-following swing framework:

- Respect the base.
- Wait for breakout.
- Ride the trend.
- Use explicit entry, stop-loss, and exit levels.

Our repo already has nearby strategies:

- `swing-breakout-v1`: daily trend plus 20-day breakout proximity and volume.
- `near-52w-high-v1`: leadership near a 52-week high.
- `pullback-20dma-v1`: continuation after a pullback to trend support.

The missing part is the exact multi-timeframe filter from the video: first qualify stocks on monthly or weekly structure, then execute on daily swing-high breakouts.

## Proposed Strategy

Strategy id: `bamboo-mtf-breakout-v1`

Purpose: find stocks that have already broken a long-term base or resistance, then enter only when the daily chart confirms continuation by clearing the prior swing high.

## Entry Logic

### Higher-Timeframe Qualification

A symbol is eligible only if all are true:

- Daily close is above SMA(50) and SMA(200).
- Weekly or monthly close is above a prior long-term resistance proxy.
- Long-term resistance proxy can start as the highest high over the previous 252 to 756 sessions, excluding the current breakout window.
- Price is in the top 25% of its 52-week range.
- Average 20-day volume is at least 100,000 shares.
- Market regime is not cautious.

### Daily Execution Trigger

After a symbol qualifies:

- Mark the prior daily swing high.
- Trigger when close breaks that swing high.
- Initial implementation can approximate swing high with prior 20-day or 55-day high.
- Better implementation should use pivot highs with 3 to 5 candles on each side, computed without lookahead in backtests.

### Confirmation Filters

Use at least two of these:

- Volume ratio is at least 1.1 versus 20-day average.
- Close is in the upper 35% of the candle range.
- Breakout candle range is not more than 2.5 ATR, to avoid chasing exhaustion.
- Gap is not more than 3%, unless close holds strongly above the breakout level.

## Exit Logic

Preferred video-style exits:

- Stop-loss: breakout candle low.
- Target 1: 2R.
- Target 2: 3R.
- Time stop: 10 to 20 sessions if neither stop nor target hits.

Backtest-friendly fallback:

- Stop: 1.2 to 1.6 ATR below entry, capped at breakout candle low if available.
- Target: 2.5 to 3.0 ATR.
- Same-day conflict rule: stop first.

## Why This Is Not Already Fully Covered

Current `swing-breakout-v1` only knows daily trend, 20-day breakout proximity, and volume. It does not require a weekly/monthly base breakout.

Current `near-52w-high-v1` catches leadership, but being near a 52-week high is not the same as clearing a multi-year base.

Current live scanner labels many breakout-style candidates as rejected or fragile based on the latest diagnostics, so this method should be added as research/paper-test first.

## Implementation Plan

1. Add research features in Python:
   - prior 52-week, 2-year, and 3-year highs excluding current day
   - weekly/monthly breakout flags
   - pivot-high breakout approximation
   - breakout candle low and R-multiple exits

2. Backtest `bamboo-mtf-breakout-v1` across existing parquet history:
   - base cost, optimistic cost, and stress cost
   - yearly performance
   - walk-forward windows
   - instrument contribution

3. Only promote if it clears gates:
   - profit factor above 1.25 base cost
   - profit factor above 1.05 stress cost
   - positive validation and out-of-sample expectancy
   - at least 10 positive walk-forward test windows
   - no single symbol contributes more than 20% of total P&L

4. If it passes, wire it into:
   - Rust historical screener
   - live scanner labels
   - strategy status mapping
   - paper-trade plan creation

5. Keep it paper-test only until forward results prove live slippage and timing.

## First Research Variants

- `bamboo_52w_daily_breakout`: 52-week range leadership plus prior 20-day breakout.
- `bamboo_2y_base_breakout`: close clears prior 504-session high, then daily prior 20-day breakout.
- `bamboo_3y_base_breakout`: close clears prior 756-session high, then daily prior 20-day breakout.
- `bamboo_weekly_confirmed`: weekly close above prior 52-week high, daily trigger next session.
- `bamboo_no_chase`: same as best variant but rejects entries more than 2 ATR above EMA20.

## Risk Notes

This method should work best in trending markets and can get chopped in sideways or falling regimes. That is consistent with the video and with our own rejected breakout diagnostics. The strategy must therefore include market breadth, trend, and no-chase filters before any live use.
