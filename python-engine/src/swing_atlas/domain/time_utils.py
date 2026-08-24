"""IST time helpers -- mirrors engine/src/types.rs's now_ist/today_ist/is_nse_holiday."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def now_sql() -> str:
    """IST timestamp formatted the way ClickHouse DateTime columns expect."""
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def sql_time(dt: datetime) -> str:
    """Converts an aware datetime to IST and formats it for ClickHouse."""
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


# NSE market holidays (trading halts -- not weekends). Add new years here as
# they are announced by NSE. Mirrors engine/src/types.rs's NSE_HOLIDAYS.
_NSE_HOLIDAYS: frozenset[str] = frozenset(
    (
        # 2023
        "2023-01-26", "2023-03-07", "2023-03-30", "2023-04-04", "2023-04-07", "2023-04-14",
        "2023-04-22", "2023-05-01", "2023-06-29", "2023-08-15", "2023-09-19", "2023-10-02",
        "2023-10-24", "2023-11-14", "2023-11-27", "2023-12-25",
        # 2024
        "2024-01-26", "2024-03-08", "2024-03-25", "2024-03-29", "2024-04-11", "2024-04-14",
        "2024-04-17", "2024-04-21", "2024-05-20", "2024-06-17", "2024-07-17", "2024-08-15",
        "2024-09-16", "2024-10-02", "2024-10-12", "2024-10-31", "2024-11-01", "2024-11-15",
        "2024-12-25",
        # 2025
        "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14",
        "2025-04-18", "2025-05-01", "2025-06-26", "2025-07-06", "2025-08-15", "2025-08-16",
        "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-11-26",
        "2025-12-25",
        # 2026
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-14", "2026-03-26", "2026-03-30",
        "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
    )
)  # fmt: skip


def is_nse_holiday(value: date) -> bool:
    """True if `value` is a weekend or NSE market holiday."""
    if value.weekday() >= 5:  # Saturday=5, Sunday=6
        return True
    return value.isoformat() in _NSE_HOLIDAYS


def prev_trading_day(value: date) -> date:
    """The most recent trading day strictly before `value`, skipping weekends
    and NSE holidays. Looks back up to 14 days (handles long holiday stretches)."""
    cursor = value - timedelta(days=1)
    for _ in range(14):
        if not is_nse_holiday(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return cursor


def compute_bucket(ts: datetime) -> int:
    """Intraday minute-bucket number: bucket 1 = 9:15, bucket 45 = 10:00, etc.
    0 outside the 9:15-15:30 regular session."""
    open_mins = 9 * 60 + 15
    close_mins = 15 * 60 + 30
    total_mins = ts.hour * 60 + ts.minute
    if total_mins < open_mins or total_mins >= close_mins:
        return 0
    return total_mins - open_mins + 1


def is_regular_session_now() -> bool:
    now = now_ist()
    return not is_nse_holiday(now.date()) and compute_bucket(now) > 0
