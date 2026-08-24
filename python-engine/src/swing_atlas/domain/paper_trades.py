"""Pure paper-trade validation/session-counting logic -- mirrors the free
functions in engine/src/api/paper.rs (validate_stop_loss, validate_target_price,
trading_sessions_elapsed, count_trading_sessions, is_nse_trading_day_now).
"""

from __future__ import annotations

from datetime import date

from swing_atlas.domain.numeric import round2
from swing_atlas.domain.time_utils import is_nse_holiday, now_ist


class InvalidStopLossError(ValueError):
    pass


class InvalidTargetPriceError(ValueError):
    pass


def validate_stop_loss(entry_price: float, stop_loss: float) -> float:
    if stop_loss > 0.0 and stop_loss < entry_price:
        return round2(stop_loss)
    raise InvalidStopLossError("paper trade requires a stop_loss below entry_price")


def validate_target_price(entry_price: float, target_price: float) -> float:
    if target_price > entry_price:
        return round2(target_price)
    raise InvalidTargetPriceError("paper trade requires a target_price above entry_price")


def is_nse_trading_day_now() -> bool:
    return not is_nse_holiday(now_ist().date())


def parse_paper_datetime(value: str) -> date | None:
    """planned_at is always formatDateTime(..., '%Y-%m-%dT%H:%i:%S%z') from
    ClickHouse -- the date is always the first 10 characters, so this only
    needs the date component (matching Rust's DateTime::parse+date_naive(),
    which likewise discards time-of-day and offset for session counting)."""
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def count_trading_sessions(start: date, end: date) -> int:
    if end < start:
        return 0
    sessions = 0
    cursor = start
    while cursor <= end:
        if not is_nse_holiday(cursor):
            sessions += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return sessions


def trading_sessions_elapsed(planned_at: str) -> int:
    planned_date = parse_paper_datetime(planned_at)
    if planned_date is None:
        return 0
    return count_trading_sessions(planned_date, now_ist().date())
