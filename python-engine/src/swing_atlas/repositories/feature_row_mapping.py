"""Shared dict-row -> HistoricalScreenerFeatureRow mapping for the daily
screener feature cache queries -- used by both screener_feature_cache_repo.py
and live_signal_baseline_repo.py, which read the same table with the same
day_volume wire-format quirk (see ClickHouseRepo docstring: clickhouse-connect's
native protocol returns UInt64 as int, unlike Rust's HTTP-JSON transport which
stringifies it automatically -- HistoricalScreenerFeatureRow.day_volume is
typed str to match the Rust struct, so it's cast here after fetch).
"""

from __future__ import annotations

from typing import Any

from swing_atlas.domain.swing.models import HistoricalScreenerFeatureRow


def to_feature_row(row: dict[str, Any]) -> HistoricalScreenerFeatureRow:
    if row.get("day_volume") is not None:
        row = {**row, "day_volume": str(row["day_volume"])}
    return HistoricalScreenerFeatureRow(**row)
