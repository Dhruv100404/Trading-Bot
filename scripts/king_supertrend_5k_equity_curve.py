from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "docs" / "king_supertrend_5k_plan"
POSITION_SIZE = 5_000.0
CAPITALS = [50_000.0, 100_000.0, 300_000.0, 500_000.0]
SOURCES = [
    ("Weekly Supertrend", ROOT / "docs" / "king_supertrend_lab" / "weekly_supertrend_trades.csv"),
    ("King Candle", ROOT / "docs" / "king_supertrend_lab" / "king_candle_trades.csv"),
]


@dataclass(frozen=True)
class Trade:
    strategy: str
    symbol: str
    signal_week: date
    entry_date: date
    exit_date: date
    rank_score: float
    relvol: float
    capital: float
    pnl: float
    return_pct: float


def parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def fnum(value: str | None, default: float = 0.0) -> float:
    try:
        parsed = float(value or "")
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def read_trades(path: Path, strategy: str) -> list[Trade]:
    trades: list[Trade] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            entry = fnum(row.get("entry"))
            if entry <= 0:
                continue
            net_return = fnum(row.get("net_return"), fnum(row.get("gross_return")))
            quantity = max(1, int(POSITION_SIZE // entry))
            capital = quantity * entry
            trades.append(
                Trade(
                    strategy=strategy,
                    symbol=row["symbol"].strip(),
                    signal_week=parse_date(row["signal_week"]),
                    entry_date=parse_date(row["entry_date"]),
                    exit_date=parse_date(row["exit_date"]),
                    rank_score=fnum(row.get("rank_score")),
                    relvol=fnum(row.get("relvol")),
                    capital=capital,
                    pnl=capital * net_return,
                    return_pct=net_return * 100.0,
                )
            )
    return trades


def top_one_per_strategy_week(trades: list[Trade]) -> list[Trade]:
    grouped: dict[tuple[str, date], list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.strategy, trade.signal_week)].append(trade)

    selected: list[Trade] = []
    for rows in grouped.values():
        selected.append(
            sorted(rows, key=lambda item: (-item.rank_score, -item.relvol, item.symbol))[0]
        )
    return sorted(selected, key=lambda item: (item.entry_date, -item.rank_score, item.strategy, item.symbol))


def simulate_cash_curve(candidates: list[Trade], initial_capital: float) -> tuple[list[dict[str, object]], list[Trade], dict[str, float]]:
    dates = sorted({trade.entry_date for trade in candidates} | {trade.exit_date for trade in candidates})
    by_entry: dict[date, list[Trade]] = defaultdict(list)
    for trade in candidates:
        by_entry[trade.entry_date].append(trade)

    cash = initial_capital
    open_positions: list[Trade] = []
    accepted: list[Trade] = []
    skipped_cash = 0
    skipped_duplicate = 0
    realized = 0.0
    peak_equity = initial_capital
    max_drawdown = 0.0
    max_open = 0
    max_capital_used = 0.0
    curve: list[dict[str, object]] = []

    for current_date in dates:
        day_pnl = 0.0
        still_open: list[Trade] = []
        for trade in open_positions:
            if trade.exit_date < current_date:
                cash += trade.capital + trade.pnl
                realized += trade.pnl
                day_pnl += trade.pnl
            else:
                still_open.append(trade)
        open_positions = still_open

        entries_taken = 0
        skipped_today = 0
        for trade in sorted(by_entry[current_date], key=lambda item: (-item.rank_score, -item.strategy.count("King"), item.symbol)):
            if any(open_trade.symbol == trade.symbol for open_trade in open_positions):
                skipped_duplicate += 1
                skipped_today += 1
                continue
            if trade.capital <= cash + 0.01:
                cash -= trade.capital
                open_positions.append(trade)
                accepted.append(trade)
                entries_taken += 1
            else:
                skipped_cash += 1
                skipped_today += 1

        intraday_capital = sum(trade.capital for trade in open_positions)
        max_open = max(max_open, len(open_positions))
        max_capital_used = max(max_capital_used, intraday_capital)

        still_open = []
        for trade in open_positions:
            if trade.exit_date <= current_date:
                cash += trade.capital + trade.pnl
                realized += trade.pnl
                day_pnl += trade.pnl
            else:
                still_open.append(trade)
        open_positions = still_open

        capital_used = sum(trade.capital for trade in open_positions)
        equity = cash + capital_used
        peak_equity = max(peak_equity, equity)
        drawdown = equity - peak_equity
        max_drawdown = min(max_drawdown, drawdown)

        curve.append(
            {
                "date": current_date.isoformat(),
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "capital_used": round(capital_used, 2),
                "day_pnl": round(day_pnl, 2),
                "cumulative_pnl": round(equity - initial_capital, 2),
                "return_pct": round(100.0 * (equity - initial_capital) / initial_capital, 3),
                "drawdown": round(drawdown, 2),
                "drawdown_pct": round(100.0 * drawdown / initial_capital, 3),
                "open_positions": len(open_positions),
                "entries_taken": entries_taken,
                "skipped_entries": skipped_today,
            }
        )

    total_pnl = curve[-1]["cumulative_pnl"] if curve else 0.0
    first = min((trade.entry_date for trade in accepted), default=None)
    last = max((trade.exit_date for trade in accepted), default=None)
    years = max(((last - first).days / 365.25) if first and last else 0.0, 1 / 365.25)
    total_return = float(total_pnl) / initial_capital
    cagr = ((1.0 + total_return) ** (1.0 / years) - 1.0) * 100.0 if total_return > -0.999 else -100.0
    wins = sum(1 for trade in accepted if trade.pnl > 0)
    gross_profit = sum(trade.pnl for trade in accepted if trade.pnl > 0)
    gross_loss = abs(sum(trade.pnl for trade in accepted if trade.pnl < 0))
    stats = {
        "initial_capital": initial_capital,
        "candidates": float(len(candidates)),
        "trades_taken": float(len(accepted)),
        "skipped_cash": float(skipped_cash),
        "skipped_duplicate": float(skipped_duplicate),
        "total_pnl": float(total_pnl),
        "return_pct": 100.0 * total_return,
        "cagr_pct": cagr,
        "win_rate": 100.0 * wins / len(accepted) if accepted else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else 99.0,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": 100.0 * max_drawdown / initial_capital,
        "max_open_positions": float(max_open),
        "max_capital_used": max_capital_used,
        "max_capital_used_pct": 100.0 * max_capital_used / initial_capital,
    }
    return curve, accepted, stats


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_base_curve(out_dir: Path, curve: list[dict[str, object]], capital: float) -> None:
    dates = [datetime.strptime(str(row["date"]), "%Y-%m-%d") for row in curve]
    equity = [float(row["equity"]) for row in curve]
    drawdown_pct = [float(row["drawdown_pct"]) for row in curve]

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    axes[0].plot(dates, equity, color="#137a5d", linewidth=2.2)
    axes[0].axhline(capital, color="#6b7280", linewidth=1, linestyle="--")
    axes[0].set_title("Combined Weekly ST + King Candle, Top 1/Week, Rs 5k Per Trade")
    axes[0].set_ylabel("Equity (Rs)")
    axes[0].grid(True, alpha=0.25)

    axes[1].fill_between(dates, drawdown_pct, 0, color="#dc2626", alpha=0.22)
    axes[1].plot(dates, drawdown_pct, color="#b91c1c", linewidth=1.4)
    axes[1].set_ylabel("Drawdown %")
    axes[1].grid(True, alpha=0.25)

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "equity_curve_1l.png", dpi=160)
    plt.close(fig)


def plot_multi_capital(out_dir: Path, curves: dict[float, list[dict[str, object]]]) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = ["#2563eb", "#137a5d", "#7c3aed", "#d97706"]
    for idx, (capital, curve) in enumerate(curves.items()):
        dates = [datetime.strptime(str(row["date"]), "%Y-%m-%d") for row in curve]
        returns = [float(row["return_pct"]) for row in curve]
        ax.plot(dates, returns, linewidth=2, color=colors[idx % len(colors)], label=f"Rs {capital:,.0f}")
    ax.axhline(0, color="#6b7280", linewidth=1, linestyle="--")
    ax.set_title("Cash-Constrained Return Curves By Starting Capital")
    ax.set_ylabel("Return %")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "equity_curve_multi_capital.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 5k-per-trade equity curves for the narrowed King/Supertrend plan.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_trades = [trade for strategy, path in SOURCES for trade in read_trades(path, strategy)]
    candidates = top_one_per_strategy_week(all_trades)
    write_csv(
        out_dir / "selected_candidates.csv",
        [
            {
                "strategy": trade.strategy,
                "symbol": trade.symbol,
                "signal_week": trade.signal_week.isoformat(),
                "entry_date": trade.entry_date.isoformat(),
                "exit_date": trade.exit_date.isoformat(),
                "rank_score": round(trade.rank_score, 3),
                "relvol": round(trade.relvol, 3),
                "capital": round(trade.capital, 2),
                "pnl": round(trade.pnl, 2),
                "return_pct": round(trade.return_pct, 3),
            }
            for trade in candidates
        ],
    )

    curves: dict[float, list[dict[str, object]]] = {}
    stats_rows: list[dict[str, object]] = []
    for capital in CAPITALS:
        curve, accepted, stats = simulate_cash_curve(candidates, capital)
        curves[capital] = curve
        write_csv(out_dir / f"equity_curve_{int(capital)}.csv", curve)
        write_csv(
            out_dir / f"accepted_trades_{int(capital)}.csv",
            [
                {
                    "strategy": trade.strategy,
                    "symbol": trade.symbol,
                    "signal_week": trade.signal_week.isoformat(),
                    "entry_date": trade.entry_date.isoformat(),
                    "exit_date": trade.exit_date.isoformat(),
                    "rank_score": round(trade.rank_score, 3),
                    "capital": round(trade.capital, 2),
                    "pnl": round(trade.pnl, 2),
                    "return_pct": round(trade.return_pct, 3),
                }
                for trade in accepted
            ],
        )
        stats_rows.append({key: round(value, 3) for key, value in stats.items()})

    write_csv(out_dir / "capital_scenarios.csv", stats_rows)
    plot_base_curve(out_dir, curves[100_000.0], 100_000.0)
    plot_multi_capital(out_dir, curves)

    report = [
        "# 5k King/Supertrend Equity Curve",
        "",
        "- Position size: Rs 5,000 per trade",
        "- Strategy set: Weekly Supertrend + King Candle",
        "- Narrowing: top 1 signal per strategy per week",
        "- Portfolio rule: no duplicate open symbol; cash-constrained entries",
        "",
        "| Capital | Taken | Skipped | P&L | Return | CAGR | Max DD | PF | Max Open |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in stats_rows:
        skipped = row["skipped_cash"] + row["skipped_duplicate"]
        report.append(
            f"| Rs {row['initial_capital']:,.0f} | {row['trades_taken']:,.0f}/{row['candidates']:,.0f} | {skipped:,.0f} | "
            f"Rs {row['total_pnl']:,.0f} | {row['return_pct']:.2f}% | {row['cagr_pct']:.2f}% | "
            f"{row['max_drawdown_pct']:.2f}% | {row['profit_factor']:.2f} | {row['max_open_positions']:.0f} |"
        )
    report.extend(
        [
            "",
            "## Charts",
            "",
            "![Rs 1L equity curve](equity_curve_1l.png)",
            "",
            "![Multi-capital return curves](equity_curve_multi_capital.png)",
            "",
            "## Files",
            "",
            "- `selected_candidates.csv`",
            "- `capital_scenarios.csv`",
            "- `equity_curve_100000.csv`",
            "- `accepted_trades_100000.csv`",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
