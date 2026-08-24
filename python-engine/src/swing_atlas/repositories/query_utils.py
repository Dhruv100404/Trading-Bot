"""Small filter-parsing helpers shared by the dynamic ClickHouse read repositories."""

from __future__ import annotations


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def clean(value: str | None, *, upper: bool = False, lower: bool = False) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if upper:
        return value.upper()
    if lower:
        return value.lower()
    return value
