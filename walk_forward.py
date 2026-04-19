"""
walk_forward.py — Rolling 3-month window stability check
=========================================================
Reads daily_pnl_rs.npy written by s_gap15_p1k_deep.py and/or
s_gap_buy_deep.py and reports Sharpe / Return / MDD per
rolling window. Also supports a COMBINED (SELL + BUY) portfolio.

Goal:
  A single train/test split can get lucky. A strategy is only
  trustworthy if rolling OOS windows show STABLE Sharpe through time.

Usage:
  python walk_forward.py                # both strategies if present
  python walk_forward.py --sell         # SELL only
  python walk_forward.py --buy          # BUY only
  python walk_forward.py --combined     # equal-weight SELL + BUY only
  python walk_forward.py --window 3     # 3-month windows (default)
  python walk_forward.py --window 6     # 6-month windows

No data loading needed — uses cached daily P&L from the deep scripts.
Run the deep scripts first to produce daily_pnl_rs.npy.
"""

import sys, io, argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

DATA_DIR = Path("C:/Users/BT-25/Desktop/project/dhan-trader/data")
SELL_DIR = DATA_DIR / "analysis_gap15_p1k"
BUY_DIR  = DATA_DIR / "analysis_gap_buy_p1k"
OUT_DIR  = DATA_DIR / "analysis_walk_forward"
OUT_DIR.mkdir(exist_ok=True)

CAPITAL   = 50000
RF_ANNUAL = 0.065

# ─── CLI ─────────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--sell", action="store_true")
ap.add_argument("--buy",  action="store_true")
ap.add_argument("--combined", action="store_true")
ap.add_argument("--window", type=int, default=3, help="window size in months")
args = ap.parse_args()
run_sell = args.sell or not (args.sell or args.buy or args.combined)
run_buy  = args.buy  or not (args.sell or args.buy or args.combined)
run_comb = args.combined or not (args.sell or args.buy or args.combined)

# ─── LOADER ──────────────────────────────────────────────────────────────────
def load_series(name, folder):
    f = folder / "daily_pnl_rs.npy"
    u = folder / "udates.npy"
    if not f.exists() or not u.exists():
        print(f"  [{name}] missing {f} — run the deep script first. Skipping.")
        return None
    rs = np.load(f)
    dates = np.load(u, allow_pickle=True)
    print(f"  [{name}] {len(rs)} days | Total Rs {rs.sum():+,.0f}")
    return rs, dates

strategies = []
if run_sell:
    r = load_series("SELL", SELL_DIR)
    if r: strategies.append(("SELL", *r))
if run_buy:
    r = load_series("BUY", BUY_DIR)
    if r: strategies.append(("BUY", *r))

if run_comb and len(strategies) >= 2:
    # Align on common dates, sum daily P&L (equal notional, independent positions)
    sell_rs, sell_d = strategies[0][1], strategies[0][2]
    buy_rs,  buy_d  = strategies[1][1], strategies[1][2]
    common = np.intersect1d(sell_d, buy_d)
    si = {d: i for i, d in enumerate(sell_d)}
    bi = {d: i for i, d in enumerate(buy_d)}
    comb_rs = np.array([sell_rs[si[d]] + buy_rs[bi[d]] for d in common], np.float32)
    strategies.append(("COMBINED", comb_rs, common))
    print(f"  [COMBINED] {len(common)} days | Total Rs {comb_rs.sum():+,.0f}")

if not strategies:
    print("No data to analyze. Run s_gap15_p1k_deep.py and/or s_gap_buy_deep.py first.")
    sys.exit(0)

# ─── WALK-FORWARD WINDOWS ────────────────────────────────────────────────────
def window_stats(daily_rs):
    """Return Sharpe / return / MDD / n for a return stream."""
    d = daily_rs[daily_rs != 0]
    if len(d) < 10:
        return None
    dr = d / CAPITAL
    rf = RF_ANNUAL / 252
    exc = dr - rf
    sharpe = (exc.mean() / exc.std(ddof=1)) * np.sqrt(252) \
             if exc.std(ddof=1) > 0 else 0
    cum = CAPITAL * np.cumprod(1 + dr)
    pk = np.maximum.accumulate(cum)
    mdd = ((pk - cum) / pk).max() * 100 if len(cum) else 0
    tot = (cum[-1] / CAPITAL - 1) * 100
    return dict(sharpe=sharpe, ret_pct=tot, mdd=mdd, n_days=len(d))

def windows_by_month(dates, months_per_window):
    """Group date indices into non-overlapping N-month windows."""
    months = np.array([d[:7] for d in dates])
    u = sorted(np.unique(months))
    groups = [u[i:i + months_per_window]
              for i in range(0, len(u), months_per_window)]
    out = []
    for g in groups:
        if len(g) < months_per_window: continue
        idx = np.where(np.isin(months, g))[0]
        out.append((f"{g[0]}..{g[-1]}", idx))
    return out

# ─── REPORT ──────────────────────────────────────────────────────────────────
summary = {}
for name, daily_rs, dates in strategies:
    print(f"\n{'='*100}")
    print(f"  {name} — rolling {args.window}-month windows")
    print(f"{'='*100}")
    print(f"  {'Window':<22} {'Days':>6} {'Sharpe':>9} {'Ret %':>9} {'MDD %':>8}  Tag")
    print(f"  {'─'*78}")
    wins = windows_by_month(dates, args.window)
    rows = []
    for label, idx in wins:
        st = window_stats(daily_rs[idx])
        if st is None:
            print(f"  {label:<22} {'-':>6} {'-':>9} {'-':>9} {'-':>8}  skip")
            continue
        tag = "GREEN" if st['ret_pct'] > 0 else "RED"
        print(f"  {label:<22} {st['n_days']:>6} {st['sharpe']:>+9.2f} "
              f"{st['ret_pct']:>+9.2f} {st['mdd']:>8.2f}  {tag}")
        rows.append(dict(window=label, **st))
    df = pd.DataFrame(rows)
    if len(df):
        sh = df.sharpe.values
        print(f"  {'─'*78}")
        print(f"  Mean Sharpe: {sh.mean():+.2f} | "
              f"Std: {sh.std():.2f} | "
              f"Min: {sh.min():+.2f} | "
              f"Max: {sh.max():+.2f}")
        pos = (df.ret_pct > 0).sum()
        print(f"  Positive windows: {pos}/{len(df)} ({pos/len(df)*100:.0f}%)")

        # Stability verdict
        stable = (sh.min() > 0.3 * max(sh.max(), 0.01)) and (pos / len(df) >= 0.6)
        verdict = "STABLE EDGE" if stable else "UNSTABLE (possible overfit / regime-dependent)"
        print(f"  Verdict: {verdict}")

        df.to_csv(OUT_DIR / f"walk_forward_{name.lower()}_{args.window}m.csv", index=False)
        summary[name] = df

# ─── COMPARISON PLOT ─────────────────────────────────────────────────────────
if summary:
    fig, ax = plt.subplots(figsize=(14, 5))
    for name, df in summary.items():
        x = np.arange(len(df))
        ax.plot(x, df.sharpe.values, marker='o', label=name, linewidth=2)
    ax.axhline(0, color='black', linewidth=0.5)
    ax.axhline(1, color='green', linewidth=0.5, linestyle='--', alpha=0.5,
               label='Sharpe=1 threshold')
    ax.set_title(f'Rolling {args.window}-month Sharpe by window',
                 fontsize=14, fontweight='bold')
    ax.set_ylabel('Sharpe')
    ax.set_xlabel('Window index (chronological)')
    ax.legend(); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    p = OUT_DIR / f"walk_forward_sharpe_{args.window}m.png"
    plt.savefig(p, dpi=150); plt.close()
    print(f"\n  Saved comparison plot: {p}")

print(f"\n  All outputs: {OUT_DIR}")
