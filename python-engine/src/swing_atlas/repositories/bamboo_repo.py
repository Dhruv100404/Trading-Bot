"""Reads the Bamboo MTF breakout lab's latest-signal CSVs off disk -- mirrors
engine/src/api/swing.rs::read_bamboo_signal_csv.
"""

from __future__ import annotations

from pathlib import Path

from swing_atlas.domain.swing.models import BambooLatestSignal


def _parse_csv_f64(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return 0.0


def read_bamboo_signal_csv(path: str) -> list[BambooLatestSignal]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    headers = [value.strip() for value in lines[0].split(",")]

    rows = []
    for line in lines[1:]:
        values = [value.strip() for value in line.split(",")]

        def get(name: str, values: list[str] = values) -> str:
            try:
                return values[headers.index(name)]
            except (ValueError, IndexError):
                return ""

        rows.append(
            BambooLatestSignal(
                strategy=get("strategy"),
                symbol=get("symbol"),
                signal_date=get("signal_date"),
                planned_entry=get("planned_entry"),
                close=_parse_csv_f64(get("close")),
                stop=_parse_csv_f64(get("stop")),
                target_from_close=_parse_csv_f64(get("target_from_close")),
                risk_multiple=_parse_csv_f64(get("risk_multiple")),
                risk_pct_vs_close=_parse_csv_f64(get("risk_pct_vs_close")),
                relvol=_parse_csv_f64(get("relvol")),
                range_position_52w=_parse_csv_f64(get("range_position_52w")),
                ema20_dist_atr=_parse_csv_f64(get("ema20_dist_atr")),
                prior_high20=_parse_csv_f64(get("prior_high20")),
                prior_high55=_parse_csv_f64(get("prior_high55")),
                gap_pct=_parse_csv_f64(get("gap_pct")),
                close_loc=_parse_csv_f64(get("close_loc")),
                rank_score=_parse_csv_f64(get("rank_score")),
            )
        )
    return rows
