from datetime import date

from swing_atlas.domain.backtest.file_trades import (
    FileStrategySource,
    fill_missing_monthly_returns,
    normalize_file_exit_reason,
    parse_file_backtest_trade,
)
from swing_atlas.domain.backtest.models import BacktestMonthlyReturn

_SOURCE = FileStrategySource(
    strategy_id="tuned-panic-reversal-v1",
    strategy_name="Panic Reversal Lab",
    setup_family="Panic Reversal",
    method_family="Panic Reversal",
    relative_path="docs/x.csv",
)


def test_parses_a_complete_row() -> None:
    record = {
        "symbol": "RELIANCE",
        "signal_date": "2026-01-01",
        "entry_date": "2026-01-02",
        "exit_date": "2026-01-05",
        "entry": "100.0",
        "exit": "110.0",
        "net_return": "0.10",
        "exit_reason": "target hit",
        "hold_days": "3",
        "rank_score": "18",
    }

    trade = parse_file_backtest_trade(_SOURCE, record)

    assert trade is not None
    assert trade.symbol == "RELIANCE"
    assert trade.entry_date == date(2026, 1, 2)
    assert trade.exit_date == date(2026, 1, 5)
    assert trade.exit_price == 110.0
    assert trade.exit_reason == "TP"
    assert trade.hold_sessions == 3
    assert trade.score == 90  # 18 * 5, clamped to [1, 100]
    assert trade.quantity == 100  # floor(10_000 / 100.0)
    assert trade.capital_used == 10_000.0
    assert trade.return_pct == 10.0


def test_missing_required_field_returns_none() -> None:
    record = {"symbol": "RELIANCE", "entry_date": "2026-01-02"}  # no signal_date/entry/net_return

    assert parse_file_backtest_trade(_SOURCE, record) is None


def test_zero_or_negative_entry_price_is_rejected() -> None:
    record = {
        "symbol": "RELIANCE",
        "signal_date": "2026-01-01",
        "entry_date": "2026-01-02",
        "exit_date": "2026-01-05",
        "entry": "0",
        "net_return": "0.10",
    }

    assert parse_file_backtest_trade(_SOURCE, record) is None


def test_exit_price_falls_back_to_entry_times_net_return_when_exit_column_missing() -> None:
    record = {
        "symbol": "RELIANCE",
        "signal_date": "2026-01-01",
        "entry_date": "2026-01-02",
        "exit_date": "2026-01-05",
        "entry": "100.0",
        "net_return": "0.05",
        # no "exit" column at all
    }

    trade = parse_file_backtest_trade(_SOURCE, record)

    assert trade is not None
    assert trade.exit_price == 105.0  # 100 * (1 + 0.05)


def test_signal_week_is_accepted_as_a_fallback_for_signal_date() -> None:
    record = {
        "symbol": "RELIANCE",
        "signal_week": "2026-01-01",
        "entry_date": "2026-01-02",
        "exit_date": "2026-01-05",
        "entry": "100.0",
        "net_return": "0.05",
    }

    trade = parse_file_backtest_trade(_SOURCE, record)

    assert trade is not None
    assert trade.signal_date == date(2026, 1, 1)


def test_normalize_exit_reason_priority() -> None:
    assert normalize_file_exit_reason("anything", hit_target=True) == "TP"
    assert normalize_file_exit_reason("Target Hit", hit_target=False) == "TP"
    assert normalize_file_exit_reason("Stop Loss", hit_target=False) == "SL"
    assert normalize_file_exit_reason("RSI exit", hit_target=False) == "RSI40"
    assert normalize_file_exit_reason("session expired", hit_target=False) == "TIME"


def test_fill_missing_monthly_returns_inserts_zero_trade_gaps() -> None:
    rows = [
        BacktestMonthlyReturn(
            strategy_id="s1", year=2026, month=1, month_label="Jan",
            trades=5, win_rate=60.0, pnl=100.0, return_pct=1.0,
        ),
        BacktestMonthlyReturn(
            strategy_id="s1", year=2026, month=3, month_label="Mar",
            trades=2, win_rate=50.0, pnl=-20.0, return_pct=-0.2,
        ),
    ]  # fmt: skip

    filled = fill_missing_monthly_returns(rows)

    assert [(r.year, r.month) for r in filled] == [(2026, 1), (2026, 2), (2026, 3)]
    gap = filled[1]
    assert gap.trades == 0
    assert gap.pnl == 0.0
    assert gap.month_label == "Feb"


def test_fill_missing_monthly_returns_handles_year_boundary() -> None:
    rows = [
        BacktestMonthlyReturn(
            strategy_id="s1", year=2025, month=12, month_label="Dec",
            trades=1, win_rate=100.0, pnl=50.0, return_pct=0.5,
        ),
        BacktestMonthlyReturn(
            strategy_id="s1", year=2026, month=2, month_label="Feb",
            trades=1, win_rate=0.0, pnl=-10.0, return_pct=-0.1,
        ),
    ]  # fmt: skip

    filled = fill_missing_monthly_returns(rows)

    assert [(r.year, r.month) for r in filled] == [(2025, 12), (2026, 1), (2026, 2)]
