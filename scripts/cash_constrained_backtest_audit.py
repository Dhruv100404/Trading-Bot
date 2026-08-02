from __future__ import annotations

import argparse
import csv
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


CAPITAL_PER_TRADE = 10_000.0
INITIAL_CAPITAL = 30_000.0
CLICKHOUSE_CONTAINER = "40-minute-auto-trader-main-clickhouse-1"
DEPRECATED_STRATEGIES = {
    "near-52w-high-tight-v2",
    "breakout-volume-v2",
    "tuned-ma-breakout-v1",
}
FILE_SOURCES = [
    {
        "strategy_id": "tuned-panic-reversal-v1",
        "method_family": "Panic Reversal",
        "setup_family": "Panic Reversal",
        "path": "docs/complex_strategy_tuning_lab/panic_best_trades.csv",
    },
    {
        "strategy_id": "weekly-supertrend-10-3",
        "method_family": "Weekly Supertrend",
        "setup_family": "Weekly Supertrend",
        "path": "docs/king_supertrend_lab/weekly_supertrend_trades.csv",
    },
    {
        "strategy_id": "king-candle-supertrend-breakout-v1",
        "method_family": "King Candle",
        "setup_family": "King Candle",
        "path": "docs/king_supertrend_lab/king_candle_trades.csv",
    },
    {
        "strategy_id": "king-candle-quality-v1",
        "method_family": "King Candle",
        "setup_family": "King Candle Quality",
        "path": "docs/king_supertrend_lab/king_candle_quality_trades.csv",
    },
]


@dataclass
class Trade:
    strategy_id: str
    method_family: str
    symbol: str
    signal_date: date
    entry_date: date
    exit_date: date
    setup_family: str
    entry_price: float
    exit_price: float
    quantity: int
    capital_used: float
    pnl: float
    return_pct: float
    exit_reason: str
    hold_sessions: int
    score: int


def parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def fnum(value: str | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def bval(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def method_family(strategy_id: str) -> str:
    sid = strategy_id.lower()
    if "supertrend" in sid:
        return "Weekly Supertrend"
    if "king-candle" in sid:
        return "King Candle"
    if "regime-mean" in sid:
        return "Regime Mean Reversion"
    if "regime-trend" in sid:
        return "Regime Trend"
    if "regime-breakout" in sid:
        return "Regime Breakout"
    if "regime-multifactor" in sid:
        return "Multi-Factor"
    if "reversal" in sid:
        return "Reversal"
    if "breakout" in sid:
        return "Breakout"
    if "pullback" in sid:
        return "Pullback"
    if "stretch" in sid or "rsi10" in sid:
        return "Mean Reversion"
    if "52w" in sid:
        return "52W Momentum"
    if "momentum" in sid:
        return "Momentum"
    return "Other"


def latest_run_id() -> str:
    cmd = [
        "docker",
        "exec",
        CLICKHOUSE_CONTAINER,
        "clickhouse-client",
        "--query",
        "SELECT run_id FROM trading.backtest_trades GROUP BY run_id ORDER BY run_id DESC LIMIT 1",
    ]
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def clickhouse_trades(run_id: str) -> list[Trade]:
    excluded = ", ".join(f"'{sid}'" for sid in sorted(DEPRECATED_STRATEGIES))
    query = f"""
    SELECT
        strategy_id,
        symbol,
        toString(signal_date) AS signal_date,
        toString(entry_date) AS entry_date,
        toString(exit_date) AS exit_date,
        setup_family,
        entry_price,
        exit_price,
        quantity,
        capital_used,
        pnl,
        return_pct,
        exit_reason,
        hold_sessions,
        score
    FROM trading.backtest_trades
    WHERE run_id = '{run_id.replace("'", "''")}'
      AND strategy_id NOT IN ({excluded})
    ORDER BY entry_date, score DESC, strategy_id, symbol
    FORMAT CSVWithNames
    """
    cmd = ["docker", "exec", "-i", CLICKHOUSE_CONTAINER, "clickhouse-client", "--query", query]
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    rows = csv.DictReader(result.stdout.splitlines())
    trades: list[Trade] = []
    for row in rows:
        trades.append(
            Trade(
                strategy_id=row["strategy_id"],
                method_family=method_family(row["strategy_id"]),
                symbol=row["symbol"],
                signal_date=parse_date(row["signal_date"]),
                entry_date=parse_date(row["entry_date"]),
                exit_date=parse_date(row["exit_date"]),
                setup_family=row["setup_family"],
                entry_price=fnum(row["entry_price"]),
                exit_price=fnum(row["exit_price"]),
                quantity=int(fnum(row["quantity"], 0)),
                capital_used=fnum(row["capital_used"]),
                pnl=fnum(row["pnl"]),
                return_pct=fnum(row["return_pct"]),
                exit_reason=row["exit_reason"],
                hold_sessions=int(fnum(row["hold_sessions"], 1)),
                score=int(fnum(row["score"], 50)),
            )
        )
    return trades


def file_backtest_trades(root: Path) -> list[Trade]:
    trades: list[Trade] = []
    for source in FILE_SOURCES:
        path = root / source["path"]
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                entry_price = fnum(row.get("entry"))
                if entry_price <= 0:
                    continue
                net_return = fnum(row.get("net_return"), fnum(row.get("gross_return")))
                exit_price = fnum(row.get("exit"), entry_price * (1 + net_return))
                quantity = max(1, int(CAPITAL_PER_TRADE // entry_price))
                capital_used = quantity * entry_price
                hit_target = bval(row.get("hit_target1")) or bval(row.get("hit_target2"))
                raw_reason = str(row.get("exit_reason") or "TIME").lower()
                if hit_target or "target" in raw_reason or "tp" in raw_reason:
                    exit_reason = "TP"
                elif "stop" in raw_reason or "sl" in raw_reason:
                    exit_reason = "SL"
                elif "rsi" in raw_reason:
                    exit_reason = "RSI40"
                else:
                    exit_reason = "TIME"
                trades.append(
                    Trade(
                        strategy_id=source["strategy_id"],
                        method_family=source["method_family"],
                        symbol=str(row["symbol"]).strip(),
                        signal_date=parse_date(row.get("signal_date") or row.get("signal_week") or ""),
                        entry_date=parse_date(row["entry_date"]),
                        exit_date=parse_date(row["exit_date"]),
                        setup_family=source["setup_family"],
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        capital_used=capital_used,
                        pnl=capital_used * net_return,
                        return_pct=net_return * 100.0,
                        exit_reason=exit_reason,
                        hold_sessions=int(round(fnum(row.get("hold_days"), fnum(row.get("hold_weeks"), 1)))),
                        score=int(max(1, min(100, round(fnum(row.get("rank_score"), 10) * 5)))),
                    )
                )
    return trades


def stddev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def losing_streak(trades: list[Trade]) -> int:
    ordered = sorted(trades, key=lambda t: (t.exit_date, t.entry_date, t.strategy_id, t.symbol))
    best = current = 0
    for trade in ordered:
        if trade.pnl <= 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def close_positions(open_positions: list[Trade], current_date: date, include_same_day: bool) -> tuple[list[Trade], float, float, int]:
    still_open: list[Trade] = []
    released_cash = 0.0
    pnl = 0.0
    closed = 0
    for trade in open_positions:
        should_close = trade.exit_date <= current_date if include_same_day else trade.exit_date < current_date
        if should_close:
            released_cash += trade.capital_used + trade.pnl
            pnl += trade.pnl
            closed += 1
        else:
            still_open.append(trade)
    return still_open, released_cash, pnl, closed


def simulate(strategy_id: str, trades: list[Trade]) -> tuple[dict, list[dict], list[dict]]:
    trades = [trade for trade in trades if trade.capital_used > 0 and trade.entry_date <= trade.exit_date]
    method = "Cash Portfolio" if strategy_id == "cash-portfolio-all" else (trades[0].method_family if trades else "Other")
    by_entry: dict[date, list[Trade]] = defaultdict(list)
    event_dates: set[date] = set()
    for trade in trades:
        by_entry[trade.entry_date].append(trade)
        event_dates.add(trade.entry_date)
        event_dates.add(trade.exit_date)

    cash = INITIAL_CAPITAL
    open_positions: list[Trade] = []
    accepted: list[Trade] = []
    equity_rows: list[dict] = []
    monthly: dict[tuple[int, int], dict] = {}
    peak = 0.0
    max_dd = 0.0
    peak_capital = 0.0
    max_open = 0
    capital_pct_sum = 0.0
    daily_returns: list[float] = []
    skipped = cash_blocked = dup_skipped = 0

    for current_date in sorted(event_dates):
        realized = 0.0
        closed_count = 0
        taken_today = 0
        skipped_today = 0

        open_positions, released_cash, pnl, closed = close_positions(open_positions, current_date, include_same_day=False)
        cash += released_cash
        realized += pnl
        closed_count += closed

        candidates = sorted(
            by_entry.get(current_date, []),
            key=lambda t: (-t.score, -t.signal_date.toordinal(), t.strategy_id, t.setup_family, t.symbol),
        )
        for candidate in candidates:
            if any(position.symbol == candidate.symbol for position in open_positions):
                dup_skipped += 1
                skipped += 1
                skipped_today += 1
                continue
            if candidate.capital_used <= cash + 0.01:
                cash -= candidate.capital_used
                open_positions.append(candidate)
                accepted.append(candidate)
                taken_today += 1
            else:
                cash_blocked += 1
                skipped += 1
                skipped_today += 1

        intraday_capital = sum(trade.capital_used for trade in open_positions)
        intraday_open = len(open_positions)
        closing_now = [trade for trade in open_positions if trade.exit_date <= current_date]
        open_positions = [trade for trade in open_positions if trade.exit_date > current_date]
        for trade in closing_now:
            cash += trade.capital_used + trade.pnl
            realized += trade.pnl
            closed_count += 1

        capital_used = sum(trade.capital_used for trade in open_positions)
        equity = cash + capital_used
        cumulative_pnl = equity - INITIAL_CAPITAL
        peak = max(peak, cumulative_pnl)
        dd = cumulative_pnl - peak
        max_dd = min(max_dd, dd)
        peak_day_capital = max(intraday_capital, capital_used)
        peak_capital = max(peak_capital, peak_day_capital)
        max_open = max(max_open, intraday_open, len(open_positions))
        capital_pct_sum += 100 * peak_day_capital / INITIAL_CAPITAL
        daily_returns.append(realized / INITIAL_CAPITAL)

        key = (current_date.year, current_date.month)
        month = monthly.setdefault(key, {"trades_closed": 0, "entries_taken": 0, "skipped_entries": 0, "pnl": 0.0, "ending_equity": equity, "max_drawdown_pct": 0.0})
        month["trades_closed"] += closed_count
        month["entries_taken"] += taken_today
        month["skipped_entries"] += skipped_today
        month["pnl"] += realized
        month["ending_equity"] = equity
        month["max_drawdown_pct"] = min(month["max_drawdown_pct"], 100 * dd / INITIAL_CAPITAL)

        equity_rows.append(
            {
                "strategy_id": strategy_id,
                "trade_date": current_date.isoformat(),
                "realized_pnl": round(realized, 2),
                "cumulative_pnl": round(cumulative_pnl, 2),
                "equity_value": round(equity, 2),
                "drawdown_rs": round(dd, 2),
                "return_pct": round(100 * cumulative_pnl / INITIAL_CAPITAL, 3),
                "open_positions": len(open_positions),
                "capital_used": round(capital_used, 2),
                "cash_available": round(cash, 2),
                "entries_taken": taken_today,
                "skipped_entries": skipped_today,
            }
        )

    total_pnl = equity_rows[-1]["cumulative_pnl"] if equity_rows else 0.0
    total_return = total_pnl / INITIAL_CAPITAL
    first_entry = min((trade.entry_date for trade in trades), default=None)
    last_exit = max((trade.exit_date for trade in trades), default=None)
    span_days = max(1, (last_exit - first_entry).days) if first_entry and last_exit else 1
    annualized = -100.0 if total_return <= -0.999 else ((1 + total_return) ** (365.25 / span_days) - 1) * 100
    gross_profit = sum(trade.pnl for trade in accepted if trade.pnl > 0)
    gross_loss = abs(sum(trade.pnl for trade in accepted if trade.pnl < 0))
    daily_std = stddev(daily_returns)
    downside = [value for value in daily_returns if value < 0]
    downside_std = stddev(downside)
    avg_daily = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    month_pnls = [row["pnl"] for row in monthly.values()]

    profile = {
        "strategy_id": strategy_id,
        "method_family": method,
        "initial_capital": INITIAL_CAPITAL,
        "candidate_trades": len(trades),
        "trades_taken": len(accepted),
        "skipped_entries": skipped,
        "cash_blocked_entries": cash_blocked,
        "duplicate_entries_skipped": dup_skipped,
        "raw_pnl": round(sum(trade.pnl for trade in trades), 2),
        "raw_return_pct": round(100 * sum(trade.pnl for trade in trades) / INITIAL_CAPITAL, 3),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(100 * total_return, 3),
        "annualized_return_pct": round(annualized, 2),
        "win_rate": round(100 * sum(1 for trade in accepted if trade.pnl > 0) / len(accepted), 2) if accepted else 0.0,
        "profit_factor": round(99.0 if gross_loss == 0 and gross_profit > 0 else (gross_profit / gross_loss if gross_loss else 0.0), 2),
        "sharpe_ratio": round(avg_daily / daily_std * math.sqrt(252), 2) if daily_std else 0.0,
        "sortino_ratio": round(avg_daily / downside_std * math.sqrt(252), 2) if downside_std else 0.0,
        "max_drawdown_rs": round(max_dd, 2),
        "max_drawdown_pct": round(100 * max_dd / INITIAL_CAPITAL, 2),
        "recovery_factor": round(total_pnl / abs(max_dd), 2) if max_dd else (99.0 if total_pnl > 0 else 0.0),
        "max_losing_streak": losing_streak(accepted),
        "positive_months_pct": round(100 * sum(1 for pnl in month_pnls if pnl > 0) / len(month_pnls), 2) if month_pnls else 0.0,
        "max_open_positions": max_open,
        "peak_capital_used": round(peak_capital, 2),
        "peak_capital_used_pct": round(100 * peak_capital / INITIAL_CAPITAL, 2),
        "avg_capital_used_pct": round(capital_pct_sum / len(event_dates), 2) if event_dates else 0.0,
        "from_date": first_entry.isoformat() if first_entry else "",
        "to_date": last_exit.isoformat() if last_exit else "",
    }

    monthly_rows = []
    for (year, month), row in sorted(monthly.items()):
        month_date = date(year, month, 1)
        monthly_rows.append(
            {
                "strategy_id": strategy_id,
                "year": year,
                "month": month,
                "month_label": month_date.strftime("%b"),
                "trades_closed": row["trades_closed"],
                "entries_taken": row["entries_taken"],
                "skipped_entries": row["skipped_entries"],
                "pnl": round(row["pnl"], 2),
                "return_pct": round(100 * row["pnl"] / INITIAL_CAPITAL, 3),
                "ending_equity": round(row["ending_equity"], 2),
                "max_drawdown_pct": round(row["max_drawdown_pct"], 2),
            }
        )
    return profile, monthly_rows, equity_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cash-constrained audit for stored swing backtest trades.")
    parser.add_argument("--out-dir", default="docs/cash_constrained_backtest_audit")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    out_dir = root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = latest_run_id()
    trades = clickhouse_trades(run_id) + file_backtest_trades(root)
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.strategy_id].append(trade)

    profiles: list[dict] = []
    monthly_rows: list[dict] = []
    equity_rows: list[dict] = []
    for strategy_id, rows in [("cash-portfolio-all", trades), *sorted(grouped.items())]:
        profile, months, curve = simulate(strategy_id, rows)
        profiles.append(profile)
        monthly_rows.extend(months)
        equity_rows.extend(curve)

    profiles.sort(key=lambda row: (0 if row["strategy_id"] == "cash-portfolio-all" else 1, -row["return_pct"]))
    write_csv(out_dir / "cash_profiles.csv", profiles)
    write_csv(out_dir / "cash_monthly_returns.csv", monthly_rows)
    write_csv(out_dir / "cash_equity_curve.csv", equity_rows)

    report_rows = profiles[:]
    lines = [
        "# Cash-Constrained Backtest Audit",
        "",
        f"- Run ID: `{run_id}`",
        f"- Initial cash: Rs {INITIAL_CAPITAL:,.0f}",
        "- Rule: exits free cash after same-day entries are evaluated; duplicate open symbols are skipped; P&L is realized on exit date.",
        "",
        "| Strategy | Candidates | Taken | Skipped | Raw Return | Cash Return | Cash Ann. | Max DD | Sharpe | Peak Used |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report_rows:
        lines.append(
            f"| {row['strategy_id']} | {row['candidate_trades']:,} | {row['trades_taken']:,} | {row['skipped_entries']:,} | "
            f"{row['raw_return_pct']:.2f}% | {row['return_pct']:.2f}% | {row['annualized_return_pct']:.2f}% | "
            f"{row['max_drawdown_pct']:.2f}% | {row['sharpe_ratio']:.2f} | {row['peak_capital_used_pct']:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `cash_profiles.csv`",
            "- `cash_monthly_returns.csv`",
            "- `cash_equity_curve.csv`",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
