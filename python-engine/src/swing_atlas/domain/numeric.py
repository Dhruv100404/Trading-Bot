"""Numeric helpers shared across domains -- mirrors round2/round2_f64/parse_number,
duplicated in engine/src/news.rs and engine/src/market_activity.rs.
"""

from __future__ import annotations

import math


def round_half_away_from_zero(value: float) -> int:
    """Matches Rust's f32/f64::round() (Python's round() uses banker's rounding,
    which can disagree with Rust on exact .5 ties)."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def round_n(value: float, decimals: int) -> float:
    factor = 10.0**decimals
    return round_half_away_from_zero(value * factor) / factor


def round2(value: float) -> float:
    return round_n(value, 2)


def round3(value: float) -> float:
    return round_n(value, 3)


def parse_number(value: str) -> float | None:
    """Strips everything except digits/./- (handles Indian-style comma-grouped
    numbers like '1,25,000' by simply discarding the commas), then parses as float."""
    cleaned = "".join(ch for ch in value if ch.isdigit() or ch in ".-")
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
