from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAB_DIR = ROOT / "docs" / "complex_strategy_tuning_lab"
OUT_DIR = LAB_DIR / "algotest_exports"
SIGNAL_TIME = "14:15"

HEADERS = ["TRADE #", "TYPE", "SIGNAL", "DATE AND TIME", "PRICE INR"]
SAMPLE_HEADERS = ["Trade #", "Type", "Signal", "Date and time", "Price INR"]
SAMPLE_SIGNAL = "Close entry(s) order Id"
SAMPLE_TRADE_START = 960


def date_time(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return f"{parsed:%Y-%m-%d} {SIGNAL_TIME}"


def price(value: float) -> str:
    return f"{value:.2f}"


def trade_rows(source_path: Path, strategy_label: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with source_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for idx, trade in enumerate(reader, start=1):
            entry = float(trade["entry"])
            gross_return = float(trade["gross_return"])
            synthetic_exit = entry * (1.0 + gross_return)
            symbol = trade["symbol"].strip().upper()
            signal = f"{symbol} {strategy_label}"
            rows.append(
                {
                    "TRADE #": str(idx),
                    "TYPE": "Entry Long",
                    "SIGNAL": signal,
                    "DATE AND TIME": date_time(trade["entry_date"]),
                    "PRICE INR": price(entry),
                }
            )
            rows.append(
                {
                    "TRADE #": str(idx),
                    "TYPE": "Exit Long",
                    "SIGNAL": signal,
                    "DATE AND TIME": date_time(trade["exit_date"]),
                    "PRICE INR": price(synthetic_exit),
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def to_sample_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    trade_numbers = sorted({int(row["TRADE #"]) for row in rows})
    remap = {str(old): str(SAMPLE_TRADE_START + idx) for idx, old in enumerate(trade_numbers)}
    sample_rows: list[dict[str, str]] = []
    for row in rows:
        sample_rows.append(
            {
                "Trade #": remap[row["TRADE #"]],
                "Type": row["TYPE"],
                "Signal": SAMPLE_SIGNAL,
                "Date and time": row["DATE AND TIME"],
                "Price INR": row["PRICE INR"],
            }
        )
    return sample_rows


def write_sample_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_HEADERS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(to_sample_rows(rows))


def screenshot_order(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_trade: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_trade.setdefault(row["TRADE #"], []).append(row)
    ordered: list[dict[str, str]] = []
    for trade_id in sorted(by_trade, key=lambda value: int(value)):
        pair = by_trade[trade_id]
        ordered.extend(sorted(pair, key=lambda item: 0 if item["TYPE"] == "Exit Long" else 1))
    return ordered


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    exports = [
        (
            LAB_DIR / "ma_best_trades.csv",
            "MA Breakout Lab",
            OUT_DIR / "ma_breakout_algotest.csv",
            OUT_DIR / "ma_breakout_algotest_chronological.csv",
        ),
        (
            LAB_DIR / "panic_best_trades.csv",
            "Panic Reversal Lab",
            OUT_DIR / "panic_reversal_algotest.csv",
            OUT_DIR / "panic_reversal_algotest_chronological.csv",
        ),
    ]

    combined_rows: list[dict[str, str]] = []
    for source, label, screenshot_path, chronological_path in exports:
        rows = trade_rows(source, label)
        write_sample_csv(chronological_path, rows)
        write_sample_csv(screenshot_path, screenshot_order(rows))
        combined_rows.extend(rows)
        print(f"Wrote {screenshot_path} ({len(rows)} rows)")
        print(f"Wrote {chronological_path} ({len(rows)} rows)")

    combined_rows.sort(key=lambda row: (row["DATE AND TIME"], int(row["TRADE #"]), row["TYPE"]))
    write_sample_csv(OUT_DIR / "combined_lab_strategies_algotest_chronological.csv", combined_rows)
    print(f"Wrote {OUT_DIR / 'combined_lab_strategies_algotest_chronological.csv'} ({len(combined_rows)} rows)")


if __name__ == "__main__":
    main()
