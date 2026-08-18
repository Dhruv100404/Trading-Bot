"""Reads from static research artifacts under docs/ -- these are pre-computed files
the engine serves as-is, not live data. Mirrors engine/src/api/news.rs's
strategy_evidence/prediction_history handlers.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

_PREDICTION_NUMERIC_FIELDS = {
    "entry_price",
    "target_price",
    "stop_price",
    "exit_price",
    "entry",
    "target",
    "stop",
    "exit",
    "hold_sessions",
    "net_return_pct",
    "relvol50",
    "stop_pct",
    "signal_rank",
    "close_location",
    "market_breadth200",
    "event_count",
    "max_catalyst_score",
}


def news_strategy_evidence_dir() -> Path:
    return Path(os.environ.get("NEWS_STRATEGY_EVIDENCE_DIR", "/app/docs/news_action_strategy"))


def read_strategy_evidence() -> dict[str, Any]:
    """Raises FileNotFoundError / json.JSONDecodeError / ValueError -- caller maps to a 404."""
    path = news_strategy_evidence_dir() / "evidence.json"
    with path.open(encoding="utf-8") as f:
        evidence = json.load(f)
    if not isinstance(evidence, dict):
        raise ValueError(f"expected a JSON object in {path}, got {type(evidence).__name__}")
    evidence["evidence_kind"] = "historical_backtest"
    evidence["live_prediction_claim"] = False
    return evidence


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    return None


def _prediction_csv_value(header: str, raw_value: str | None) -> Any:
    value = (raw_value or "").strip()
    if not value:
        return None
    if header in ("target_hit", "success"):
        parsed = _parse_bool(value)
        if parsed is not None:
            return parsed
    if header in _PREDICTION_NUMERIC_FIELDS:
        try:
            return float(value)
        except ValueError:
            pass
    return value


def read_prediction_history(
    *, success: bool | None, split: str | None, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Raises FileNotFoundError -- caller maps that to a 404."""
    path = news_strategy_evidence_dir() / "predictions.csv"
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        success_field = next((h for h in fieldnames if h in ("target_hit", "success")), None)
        outcome_field = next((h for h in fieldnames if h in ("outcome", "exit_reason")), None)
        split_field = "split" if "split" in fieldnames else None
        requested_split = split.strip().lower() if split and split.strip() else None

        rows: list[dict[str, Any]] = []
        matched = 0
        for record in reader:
            row_success: bool | None = None
            if success_field:
                row_success = _parse_bool(record.get(success_field) or "")
            if row_success is None and outcome_field:
                row_success = (record.get(outcome_field) or "").strip().upper().startswith("TARGET")

            if success is not None and row_success != success:
                continue
            if requested_split is not None:
                actual = (record.get(split_field) or "").strip().lower() if split_field else ""
                if actual != requested_split:
                    continue

            matched += 1
            if len(rows) >= limit:
                continue
            row: dict[str, Any] = {
                header: _prediction_csv_value(header, value) for header, value in record.items()
            }
            row["target_hit"] = row_success
            rows.append(row)

    return rows, matched
