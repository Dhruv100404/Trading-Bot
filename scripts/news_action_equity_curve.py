from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = ROOT / "docs" / "news_action_strategy" / "predictions.csv"
DEFAULT_OUT_DIR = ROOT / "docs" / "news_action_strategy"


def build_equity(
    predictions_path: Path,
    initial_capital: float,
    slots: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    trades = pd.read_csv(
        predictions_path,
        parse_dates=["signal_date", "entry_date", "exit_date"],
    ).sort_values(["exit_date", "symbol"], kind="stable")
    if trades.empty:
        raise ValueError("Prediction history is empty")
    if slots <= 0:
        raise ValueError("slots must be positive")

    trades["trade_net_return"] = trades["net_return_pct"].astype(float) / 100.0
    trades["portfolio_return_at_close"] = trades["trade_net_return"] / slots
    trades["equity_factor"] = (1.0 + trades["portfolio_return_at_close"]).cumprod()
    trades["equity"] = initial_capital * trades["equity_factor"]
    trades["running_peak"] = trades["equity"].cummax()
    trades["drawdown_pct"] = (trades["equity"] / trades["running_peak"] - 1.0) * 100.0

    initial_date = trades["exit_date"].min() - pd.Timedelta(days=1)
    initial_row = pd.DataFrame(
        {
            "exit_date": [initial_date],
            "symbol": ["START"],
            "outcome": ["START"],
            "target_hit": [False],
            "net_return_pct": [0.0],
            "portfolio_return_at_close": [0.0],
            "equity": [initial_capital],
            "running_peak": [initial_capital],
            "drawdown_pct": [0.0],
        }
    )
    curve = pd.concat(
        [
            initial_row,
            trades[
                [
                    "exit_date",
                    "symbol",
                    "outcome",
                    "target_hit",
                    "net_return_pct",
                    "portfolio_return_at_close",
                    "equity",
                    "running_peak",
                    "drawdown_pct",
                ]
            ],
        ],
        ignore_index=True,
    )

    annual_rows: list[dict[str, object]] = []
    prior_year_equity = initial_capital
    for year, group in trades.groupby(trades["exit_date"].dt.year, sort=True):
        year_factor = float((1.0 + group["portfolio_return_at_close"]).prod())
        ending_equity = prior_year_equity * year_factor
        local_equity = pd.concat(
            [
                pd.Series([prior_year_equity]),
                prior_year_equity * (1.0 + group["portfolio_return_at_close"]).cumprod(),
            ],
            ignore_index=True,
        )
        local_drawdown = local_equity / local_equity.cummax() - 1.0
        annual_rows.append(
            {
                "year": int(year),
                "period": (
                    "partial"
                    if int(year) in {int(trades["exit_date"].dt.year.min()), int(trades["exit_date"].dt.year.max())}
                    else "full_year"
                ),
                "trades": int(len(group)),
                "target_hits": int(group["target_hit"].astype(bool).sum()),
                "target_hit_rate_pct": round(float(group["target_hit"].astype(bool).mean() * 100), 2),
                "win_rate_pct": round(float((group["net_return_pct"] > 0).mean() * 100), 2),
                "portfolio_return_pct": round((year_factor - 1.0) * 100.0, 3),
                "ending_equity": round(ending_equity, 2),
                "max_drawdown_pct": round(float(local_drawdown.min() * 100.0), 3),
            }
        )
        prior_year_equity = ending_equity
    annual = pd.DataFrame(annual_rows)

    start_date = pd.Timestamp(trades["exit_date"].min())
    end_date = pd.Timestamp(trades["exit_date"].max())
    years = max((end_date - start_date).days / 365.25, 1 / 365.25)
    final_equity = float(trades["equity"].iloc[-1])
    summary = {
        "method": "trade-close compounded 10-slot portfolio proxy",
        "initial_capital": round(initial_capital, 2),
        "slots": slots,
        "allocation_per_trade_pct": round(100.0 / slots, 2),
        "start_date": start_date.date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "trades": int(len(trades)),
        "target_hits": int(trades["target_hit"].astype(bool).sum()),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_capital - 1.0) * 100.0, 3),
        "cagr_pct": round(((final_equity / initial_capital) ** (1.0 / years) - 1.0) * 100.0, 3),
        "max_drawdown_pct": round(float(trades["drawdown_pct"].min()), 3),
        "positive_years": int((annual["portfolio_return_pct"] > 0).sum()),
        "tested_years": int(len(annual)),
        "round_trip_cost_pct": 0.26,
        "note": "Equity changes when a simulated trade closes; open positions are not marked to market intraday.",
    }
    return curve, annual, summary


def save_chart(curve: pd.DataFrame, annual: pd.DataFrame, out_path: Path) -> None:
    plt.style.use("dark_background")
    fig, (ax_equity, ax_year) = plt.subplots(
        2,
        1,
        figsize=(12, 7.2),
        gridspec_kw={"height_ratios": [2.2, 1]},
        constrained_layout=True,
    )
    fig.patch.set_facecolor("#0b1220")
    for axis in (ax_equity, ax_year):
        axis.set_facecolor("#0b1220")
        axis.grid(color="#344054", alpha=0.32, linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)

    ax_equity.plot(curve["exit_date"], curve["equity"], color="#26df9a", linewidth=2.0)
    ax_equity.fill_between(
        curve["exit_date"],
        curve["equity"],
        float(curve["equity"].min()) * 0.995,
        color="#26df9a",
        alpha=0.08,
    )
    ax_equity.set_ylabel("Portfolio equity (INR)")
    ax_equity.set_title("News-action strategy: 10-slot trade-close equity proxy", loc="left")

    colors = ["#26df9a" if value >= 0 else "#ff6b7a" for value in annual["portfolio_return_pct"]]
    bars = ax_year.bar(annual["year"].astype(str), annual["portfolio_return_pct"], color=colors, alpha=0.86)
    ax_year.axhline(0, color="#98a2b3", linewidth=0.8)
    ax_year.set_ylabel("Annual return (%)")
    for bar, value in zip(bars, annual["portfolio_return_pct"]):
        ax_year.text(
            bar.get_x() + bar.get_width() / 2,
            value + (0.35 if value >= 0 else -0.35),
            f"{value:+.2f}%",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9,
        )
    fig.savefig(out_path, dpi=170, facecolor=fig.get_facecolor())
    plt.close(fig)


def run(args: argparse.Namespace) -> None:
    curve, annual, summary = build_equity(args.predictions, args.initial_capital, args.slots)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    curve.to_csv(args.out_dir / "equity_curve.csv", index=False)
    annual.to_csv(args.out_dir / "yearly_returns.csv", index=False)
    (args.out_dir / "equity_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    save_chart(curve, annual, args.out_dir / "equity_curve.png")
    print(json.dumps(summary, indent=2))
    print(annual.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the news-action strategy equity curve and yearly returns.")
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--slots", type=int, default=10)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
