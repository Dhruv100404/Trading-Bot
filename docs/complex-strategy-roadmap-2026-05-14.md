# Complex Strategy Roadmap - 2026-05-14

## Current Architecture Verdict

The system is not strictly a JSON strategy engine.

- `strategies/*.json` is mainly a registry, metadata, risk, and promotion layer.
- `engine/src/api/backtest.rs` contains app backtest strategy logic in Rust/SQL.
- `engine/src/api/swing.rs` contains hard-coded scanner/setup mapping logic.
- `research/strategies/*.py` is the right place for complex strategy code, adaptive filters, parameter experiments, and research-only exits.

So the right workflow is:

1. Express richer ideas in Python research modules.
2. Backtest with costs, stress slippage, chronological splits, and walk-forward windows.
3. Paper-test only the strategies that survive.
4. Promote the survivors into JSON/app/live wiring after forward evidence.

## What I Tested

Added `research/strategies/complex_adaptive_suite.py` with six complex/adaptive strategies:

- `complex_adaptive_stretch_reclaim`
- `complex_failed_breakdown_plus_breadth`
- `complex_quiet_rs_pullback`
- `complex_compression_leader_breakout`
- `complex_sma50_reclaim_continuation`
- `complex_market_panic_leader_bounce`

Output folder:

- `docs/python_research_outputs/complex_adaptive_suite/`

## Result

No new strategy passed the existing promotion gates.

Best candidate:

| Strategy | Trades | Win Rate | Profit Factor | Expectancy | Total Proxy Return | OOS PF |
|---|---:|---:|---:|---:|---:|---:|
| `complex_adaptive_stretch_reclaim` | 432 | 51.62% | 1.401 | 0.765% | 17.77% | 0.828 |

This is useful information: complexity improved the shape versus many breakout ideas, but did not fix the 2025/out-of-sample weakness.

## Honest Read

The system is not lacking because it only has JSON. It is lacking because the current edge is regime-sensitive.

The strongest family remains mean reversion / ATR stretch, not generic breakout or pullback. The next improvement should not be more random indicators. It should be a regime/kill-switch layer that prevents the mean-reversion system from trading during the bad 2025-style windows.

## Next Research Step

Build a "strategy governor" before promoting anything:

- No new trades when recent 20-trade expectancy is negative.
- No new trades when rolling market breadth is deteriorating.
- Reduce max trades per day after a drawdown cluster.
- Separate "normal breadth mean reversion" from "panic bounce" mode.
- Compare base ATR-stretch vs governed ATR-stretch using the same OOS and walk-forward gates.

Success criteria before paper promotion:

- Base profit factor >= 1.25.
- Stress-cost profit factor >= 1.05.
- Out-of-sample profit factor >= 1.0.
- 2025-style segment no longer deeply negative.
- Walk-forward test windows positive in at least 11 of 16 windows.

## Important Guardrail

Do not wire any of these new complex strategies directly into live execution. They are research-only until forward paper evidence improves.
