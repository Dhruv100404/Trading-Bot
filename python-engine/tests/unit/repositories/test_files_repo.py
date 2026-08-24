import json
from pathlib import Path

import pytest

from swing_atlas.repositories import files_repo


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NEWS_STRATEGY_EVIDENCE_DIR", str(tmp_path))
    return tmp_path


def test_read_strategy_evidence_adds_disclaimer_fields(tmp_path: Path) -> None:
    (tmp_path / "evidence.json").write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    evidence = files_repo.read_strategy_evidence()

    assert evidence["foo"] == "bar"
    assert evidence["evidence_kind"] == "historical_backtest"
    assert evidence["live_prediction_claim"] is False


def test_read_strategy_evidence_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        files_repo.read_strategy_evidence()


def _write_predictions_csv(tmp_path: Path, rows: list[dict[str, str]]) -> None:
    import csv

    path = tmp_path / "predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_prediction_history_filters_by_success_flag(tmp_path: Path) -> None:
    _write_predictions_csv(
        tmp_path,
        [
            {"symbol": "A", "success": "true", "split": "test", "entry_price": "100"},
            {"symbol": "B", "success": "false", "split": "test", "entry_price": "200"},
        ],
    )

    rows, matched = files_repo.read_prediction_history(success=True, split=None, limit=10)

    assert matched == 1
    assert rows[0]["symbol"] == "A"
    assert rows[0]["target_hit"] is True
    assert rows[0]["entry_price"] == 100.0  # numeric field coerced to float


def test_prediction_history_falls_back_to_outcome_column(tmp_path: Path) -> None:
    _write_predictions_csv(
        tmp_path,
        [
            {"symbol": "A", "outcome": "TARGET_HIT", "split": "test"},
            {"symbol": "B", "outcome": "STOP_LOSS", "split": "test"},
        ],
    )

    rows, matched = files_repo.read_prediction_history(success=True, split=None, limit=10)

    assert matched == 1
    assert rows[0]["symbol"] == "A"


def test_prediction_history_filters_by_split_case_insensitive(tmp_path: Path) -> None:
    _write_predictions_csv(
        tmp_path,
        [
            {"symbol": "A", "success": "true", "split": "OOS"},
            {"symbol": "B", "success": "true", "split": "train"},
        ],
    )

    rows, matched = files_repo.read_prediction_history(success=None, split="oos", limit=10)

    assert matched == 1
    assert rows[0]["symbol"] == "A"


def test_prediction_history_respects_limit_but_still_counts_matched(tmp_path: Path) -> None:
    _write_predictions_csv(
        tmp_path,
        [{"symbol": s, "success": "true"} for s in ("A", "B", "C")],
    )

    rows, matched = files_repo.read_prediction_history(success=True, split=None, limit=2)

    assert len(rows) == 2
    assert matched == 3
