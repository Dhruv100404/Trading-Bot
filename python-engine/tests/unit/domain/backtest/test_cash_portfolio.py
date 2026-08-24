"""Hand-computed parity tests for the cash-constrained portfolio replay --
mirrors engine/src/api/backtest.rs's simulate_cash_strategy. See the module
docstring in domain/backtest/cash_portfolio.py: this is a direct line-by-line
port of the Rust control flow, so these fixtures are worked out by hand against
the Rust logic (greedy chronological acceptance, ₹30k starting capital), not
just re-derived from the Python implementation itself.
"""

from datetime import date

from swing_atlas.domain.backtest.cash_portfolio import simulate_cash_strategy
from swing_atlas.domain.backtest.models import BacktestAnalysisTrade
from swing_atlas.domain.backtest.strategy_specs import BACKTEST_CASH_ACCOUNT_CAPITAL


def _trade(
    symbol: str, entry: date, exit_: date, capital_used: float, pnl: float, score: int
) -> BacktestAnalysisTrade:
    return BacktestAnalysisTrade(
        strategy_id="test-strategy",
        method_family="Other",
        symbol=symbol,
        signal_date=entry,
        entry_date=entry,
        exit_date=exit_,
        setup_family="Test",
        capital_used=capital_used,
        pnl=pnl,
        score=score,
    )


def test_greedy_acceptance_blocks_entry_that_exceeds_remaining_cash() -> None:
    assert BACKTEST_CASH_ACCOUNT_CAPITAL == 30_000.0

    trades = [
        # All three entered 2026-01-01, ranked by score (A=90, B=80, C=70).
        _trade("AAA", date(2026, 1, 1), date(2026, 1, 5), 10_000.0, 1_000.0, 90),
        _trade("BBB", date(2026, 1, 1), date(2026, 1, 10), 15_000.0, -500.0, 80),
        # Only 5,000 cash remains after A+B (30,000 - 10,000 - 15,000) -- C's
        # 10,000 capital_used exceeds that, so C must be cash-blocked.
        _trade("CCC", date(2026, 1, 3), date(2026, 1, 3), 10_000.0, 200.0, 70),
    ]

    result = simulate_cash_strategy("test-strategy", "Other", trades)

    assert result.profile.candidate_trades == 3
    assert result.profile.trades_taken == 2
    assert result.profile.cash_blocked_entries == 1
    assert result.profile.duplicate_entries_skipped == 0
    assert result.profile.skipped_entries == 1
    # A wins (+1000), B loses (-500) -> total pnl 500, win rate 50%.
    assert result.profile.total_pnl == 500.0
    assert result.profile.win_rate == 50.0
    assert result.profile.profit_factor == 2.0  # 1000 gross profit / 500 gross loss
    # Peak equity hits 31,000 on 01-05 (cumulative +1000), then drops to 30,500
    # on 01-10 (cumulative +500) -- a 500 drawdown from peak.
    assert result.profile.max_drawdown_rs == -500.0
    assert result.profile.max_open_positions == 2

    by_date = {point.trade_date: point for point in result.equity_curve}
    assert by_date["2026-01-01"].cumulative_pnl == 0.0
    assert by_date["2026-01-01"].capital_used == 25_000.0  # A (10k) + B (15k)
    assert by_date["2026-01-05"].cumulative_pnl == 1_000.0  # A closes, +1000 realized
    assert by_date["2026-01-05"].realized_pnl == 1_000.0
    assert by_date["2026-01-10"].cumulative_pnl == 500.0  # B closes, -500 realized
    assert by_date["2026-01-10"].realized_pnl == -500.0
    assert by_date["2026-01-10"].open_positions == 0


def test_duplicate_symbol_same_day_is_skipped_not_cash_blocked() -> None:
    trades = [
        _trade("AAA", date(2026, 1, 1), date(2026, 1, 5), 5_000.0, 100.0, 90),
        # Second AAA entry the same day -- plenty of cash available, but a
        # symbol can only be held once at a time, so this must be a duplicate
        # skip, not a cash-blocked skip.
        _trade("AAA", date(2026, 1, 1), date(2026, 1, 5), 5_000.0, 200.0, 80),
    ]

    result = simulate_cash_strategy("test-strategy", "Other", trades)

    assert result.profile.trades_taken == 1
    assert result.profile.duplicate_entries_skipped == 1
    assert result.profile.cash_blocked_entries == 0
    assert result.profile.skipped_entries == 1


def test_same_day_entry_and_exit_reopens_capital_for_a_later_candidate() -> None:
    # Entry-priority ordering means the higher-scored trade is considered first
    # each day; a SAME-day exit (include_same_day=True pass) frees its capital
    # before the day is over, but only entries considered *after* it in that
    # day's candidate order can use the freed cash (there's no re-evaluation of
    # already-rejected candidates within the same day).
    trades = [
        _trade("AAA", date(2026, 1, 1), date(2026, 1, 1), 20_000.0, 500.0, 100),
        _trade("BBB", date(2026, 1, 1), date(2026, 1, 5), 20_000.0, -100.0, 50),
    ]

    result = simulate_cash_strategy("test-strategy", "Other", trades)

    # A (score 100) is considered first: 20,000 <= 30,000 -> accepted, cash -> 10,000.
    # B (score 50) considered next: 20,000 > 10,000 -> cash-blocked, even though
    # A exits same-day (the same-day close pass runs *after* the acceptance loop).
    assert result.profile.trades_taken == 1
    assert result.profile.cash_blocked_entries == 1


def test_empty_trades_returns_zeroed_profile() -> None:
    result = simulate_cash_strategy("test-strategy", "Other", [])

    assert result.profile.candidate_trades == 0
    assert result.profile.trades_taken == 0
    assert result.equity_curve == []
    assert result.monthly_returns == []
