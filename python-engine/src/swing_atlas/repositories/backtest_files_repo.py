"""Reads the 4 file-backed backtest strategies off disk -- mirrors
engine/src/api/backtest.rs::load_file_backtest_trades. Missing files are
logged and skipped, not fatal (matches the Rust original).
"""

from __future__ import annotations

import csv
import logging

from swing_atlas.domain.backtest.file_trades import (
    FileBacktestTrade,
    file_strategy_sources,
    parse_file_backtest_trade,
)
from swing_atlas.repositories.backtest_lab_repo import repo_root

logger = logging.getLogger(__name__)


def load_file_backtest_trades() -> list[FileBacktestTrade]:
    root = repo_root()
    out: list[FileBacktestTrade] = []
    for source in file_strategy_sources():
        path = root / source.relative_path
        if not path.exists():
            logger.warning("file-backed strategy result missing: %s", path)
            continue
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for record in reader:
                trade = parse_file_backtest_trade(source, record)
                if trade is not None:
                    out.append(trade)
    return out
