"""Reads the weekly-lab CSV research outputs and the volume-groups liquidity
map off disk -- mirrors engine/src/api/swing.rs's load_weekly_lab_candidates/
load_weekly_lab_file/load_volume_groups_map.

Both are hand-rolled CSV/JSON parsers (not pandas) matching the Rust
original's dependency-free line-splitting, since these files are small and
read on every dashboard build.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from swing_atlas.domain.numeric import round2
from swing_atlas.domain.swing.models import WeeklyLabCandidate
from swing_atlas.domain.swing.research_confluence import WEEKLY_LAB_MAX_SIGNAL_AGE_DAYS
from swing_atlas.domain.time_utils import now_ist

logger = logging.getLogger(__name__)


def _parse_csv_f32(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return 0.0


def weekly_lab_signal_is_fresh(value: str) -> bool:
    try:
        year, month, day = (int(part) for part in value.split("-"))
        signal_date = date(year, month, day)
    except ValueError:
        return False
    age_days = (now_ist().date() - signal_date).days
    return 0 <= age_days <= WEEKLY_LAB_MAX_SIGNAL_AGE_DAYS


def load_weekly_lab_file(
    path: str,
    strategy_id: str,
    strategy_label: str,
    setup_family: str,
    strategy_status: str,
    breakout_trigger: bool,
    out: dict[str, WeeklyLabCandidate],
) -> None:
    file_path = Path(path)
    if not file_path.exists():
        return
    content = file_path.read_text(encoding="utf-8")

    stale_rows = 0
    newest_stale_date = ""
    for line in content.splitlines()[1:]:
        cols = [value.strip() for value in line.split(",")]
        if len(cols) < 12 or not cols[0]:
            continue
        if not weekly_lab_signal_is_fresh(cols[2]):
            stale_rows += 1
            if cols[2] > newest_stale_date:
                newest_stale_date = cols[2]
            continue

        symbol = cols[0]
        close = _parse_csv_f32(cols[3])
        high = _parse_csv_f32(cols[4])
        trigger_price = round2(high * 1.001) if breakout_trigger else round2(close)
        out[symbol] = WeeklyLabCandidate(
            symbol=symbol,
            strategy_id=strategy_id,
            strategy_label=strategy_label,
            setup_family=setup_family,
            strategy_status=strategy_status,
            signal_date=cols[2],
            trigger_price=trigger_price,
            close=close,
            supertrend=_parse_csv_f32(cols[5]),
            rank_score=_parse_csv_f32(cols[6]),
            relvol=_parse_csv_f32(cols[7]),
            rs13w_rank=_parse_csv_f32(cols[8]),
            body_ratio=_parse_csv_f32(cols[9]),
            range_atr=_parse_csv_f32(cols[11]),
        )

    if stale_rows > 0:
        logger.warning(
            "ignored %d stale weekly lab rows from %s (newest signal date: %s; max age: %d days)",
            stale_rows,
            path,
            newest_stale_date,
            WEEKLY_LAB_MAX_SIGNAL_AGE_DAYS,
        )


def load_weekly_lab_candidates() -> dict[str, WeeklyLabCandidate]:
    out: dict[str, WeeklyLabCandidate] = {}
    load_weekly_lab_file(
        "docs/king_supertrend_lab/weekly_supertrend_103_latest_candidates.csv",
        "weekly-supertrend-10-3",
        "Weekly Supertrend 10-3",
        "Weekly Supertrend",
        "Watch",
        False,
        out,
    )
    load_weekly_lab_file(
        "docs/king_supertrend_lab/king_candle_quality_breakout_latest_candidates.csv",
        "king-candle-quality-v1",
        "King Candle Quality",
        "King Candle Quality",
        "Candidate",
        True,
        out,
    )
    return out


_VOLUME_GROUPS_CANDIDATES = ("data/volume_groups.json", "../data/volume_groups.json")
_VOLUME_BUCKET_TOKENS = ("MEGA", "LARGE", "MID", "SMALL")


def load_volume_groups_map() -> dict[str, str]:
    for candidate in _VOLUME_GROUPS_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        groups = parsed.get("volume_groups") if isinstance(parsed, dict) else None
        if not isinstance(groups, dict):
            continue

        result: dict[str, str] = {}
        for raw_label, symbols in groups.items():
            bucket = next((token for token in _VOLUME_BUCKET_TOKENS if token in raw_label), None)
            if bucket is None or not isinstance(symbols, list):
                continue
            for symbol in symbols:
                if isinstance(symbol, str):
                    result[symbol] = bucket
        return result
    return {}
