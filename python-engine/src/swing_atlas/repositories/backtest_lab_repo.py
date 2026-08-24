"""Reads for /api/backtests/python/latest and /charts/{name} -- the CSV/PNG
output of scripts/complex_strategy_optimizer.py, read back off disk.
Mirrors engine/src/api/backtest.rs::{read_python_lab_payload,python_chart}.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from swing_atlas.domain.time_utils import now_ist


class InvalidChartNameError(ValueError):
    pass


def repo_root() -> Path:
    return Path(os.environ.get("BACKTEST_LAB_ROOT", "."))


def python_lab_output_dir() -> Path:
    override = os.environ.get("BACKTEST_LAB_OUT_DIR")
    if override:
        return Path(override)
    return repo_root() / "docs" / "complex_strategy_tuning_lab"


def _csv_scalar_to_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in ("True", "true"):
        return True
    if value in ("False", "false"):
        return False
    try:
        return float(value)
    except ValueError:
        return value


def _read_csv_records_as_values(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for record in reader:
            rows.append(
                {header: _csv_scalar_to_value(value or "") for header, value in record.items()}
            )
            if limit is not None and len(rows) >= limit:
                break
    return rows


def read_python_lab_payload() -> dict[str, Any]:
    out_dir = python_lab_output_dir()
    predictions_path = out_dir / "latest_signal_predictions.csv"
    if not predictions_path.exists():
        predictions_path = out_dir / "current_signal_predictions.csv"

    return {
        "updated_at": now_ist().isoformat(),
        "output_dir": str(out_dir),
        "best": {
            "ma": _read_csv_records_as_values(out_dir / "ma_tuned_results.csv", limit=1),
            "panic": _read_csv_records_as_values(out_dir / "panic_tuned_results.csv", limit=1),
        },
        "period_returns": {
            "ma_yearly": _read_csv_records_as_values(out_dir / "ma_yearly_returns.csv"),
            "panic_yearly": _read_csv_records_as_values(out_dir / "panic_yearly_returns.csv"),
            "ma_monthly": _read_csv_records_as_values(out_dir / "ma_monthly_returns.csv"),
            "panic_monthly": _read_csv_records_as_values(out_dir / "panic_monthly_returns.csv"),
        },
        "predictions": _read_csv_records_as_values(predictions_path),
    }


def read_chart_bytes(name: str) -> bytes:
    """Raises InvalidChartNameError for path-traversal attempts, FileNotFoundError if missing."""
    if "/" in name or "\\" in name or not name.endswith(".png"):
        raise InvalidChartNameError("invalid chart name")
    path = python_lab_output_dir() / "charts" / name
    return path.read_bytes()
