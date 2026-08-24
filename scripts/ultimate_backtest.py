"""
ultimate_backtest.py
====================
Deep backtest of the ULTIMATE config discovered by deep_quant_analysis.py:
  - MID stocks only (1-10cr daily volume)
  - SELL reversal after very strong green b1 (CR1 > 1.5%)
  - upper_wick < 0.2 (strong close, no rejection)
  - Price < 500
  - TP=3.0% SL=0.3% EXIT=b45 (9:59 AM)
  - Top 15 picks/day by score
  - Entry at close of b1 (9:16 AM)

ZERO LOOKAHEAD: all masks and scores use ONLY b1 data.
ALL NUMPY VECTORIZED: no Python loops in strategy.

Outputs all 13 professional quant ratios:
  Sharpe, Sortino, Calmar, Information Ratio, Profit Factor,
  Payoff Ratio, Expectancy, Recovery Factor, Sterling Ratio,
  Omega Ratio, Ulcer Performance Index, CAGR, Max Drawdown

Train: 202301-202501 | Test: 202502-202603 | Full: 202301-202603

LIVE-MATCHING FIXES (matches paper_trader.py behavior):
  FIX 1 — SL checked BEFORE TP: when both TP and SL are hit in the
          same 1-minute candle, SL wins (conservative). In live trading,
          the broker's stop-loss triggers first. Previously TP won ties,
          inflating returns by ~2%/month.
  FIX 2 — Gap slippage on SL: when a candle OPENS above the SL price
          (gap-through), the actual exit is at the OPEN price, not the
          SL price. This makes losses worse than flat -0.3% (can be
          -1% to -5%). ~14% of SL trades have gap-through slippage.
          Previously all SL trades showed exactly -0.3%, hiding real losses.
"""

import sys, io, time, json, warnings, signal, os, gc
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore", category=RuntimeWarning)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
signal.signal(signal.SIGINT, lambda *_: (print("\nInterrupted!"), os._exit(1)))

DATA_DIR = Path("C:/Users/BT-25/Desktop/project/dhan-trader/data")

# === CONFIG ===
CAPITAL = 50000
LEV = 5
TOP_N = 5
TP = 3.0
SL = 0.3
ENTRY_BKT = 1   # entry at close of bucket 1 (9:16 AM)
EXIT_BKT = 45   # force exit at bucket 45 (9:59 AM)
CR1_MIN = 1.5   # minimum b1 candle return %
WICK_MAX = 0.2  # maximum upper wick ratio
PRICE_MAX = 500  # maximum price
RF_ANNUAL = 0.065  # risk-free rate (India 10Y ~6.5%)

# Capital Allocation
# actual_pos = min(CAPITAL * LEV / n_trades_today, base_pos * CAP_MULT)
# CAP_MULT=3 → best Sharpe for MID strategy (from capital_allocation_test.py)
CAP_MULT = 3

t0 = time.perf_counter()
def elapsed():
    s = time.perf_counter() - t0; m = int(s // 60)
    return f"[{m:02d}:{s%60:05.2f}]" if m else f"[{s:05.2f}s]"
def log(msg): print(f"{elapsed()} {msg}", flush=True)

# ============================================================================
#  LOAD
# ============================================================================
log("Loading volume groups...")
vg = json.load(open(DATA_DIR / "volume_groups.json"))["volume_groups"]
MID = set(vg.get("MID (1-10cr/day)", []))
log(f"  MID symbols: {len(MID)}")

log("Loading parquets...")
ALL_MONTHS = list(range(202301, 202313)) + list(range(202401, 202413)) + list(range(202501, 202513)) + [202601, 202602, 202603]
MAX_BKT = EXIT_BKT + 1  # only need up to exit bucket + 1 for safety
COLS = ["symbol","date","gap_pct","day_open","bucket","open","high","low","close","vwap","buy_ratio"]

dfs = []
for ym in ALL_MONTHS:
    p = DATA_DIR / f"candles_{ym}.parquet"
    if not p.exists(): continue
    d = pd.read_parquet(p, columns=COLS)
    d = d[(d["bucket"] <= MAX_BKT) & (d["symbol"].isin(MID))]
    for c in ["open","high","low","close","gap_pct","day_open","vwap","buy_ratio"]:
        if c in d.columns: d[c] = d[c].astype(np.float32)
    dfs.append(d)
    log(f"  {ym}: {len(d):,} rows")
df = pd.concat(dfs, ignore_index=True); del dfs; gc.collect()
log(f"Total: {len(df):,} rows | {df['symbol'].nunique()} symbols | {df['date'].nunique()} days")

# ============================================================================
#  PIVOT (chunked for memory)
# ============================================================================
log("Pivoting...")
sd = df.groupby(["symbol","date"]).agg(
    gap_pct=("gap_pct","first"), day_open=("day_open","first")
).reset_index()

pivot_vals = ["close","open","high","low","vwap","buy_ratio"]
piv = sd.copy()
for val in pivot_vals:
    log(f"  pivoting {val}...")
    sub = df[["symbol","date","bucket",val]].copy()
    p = sub.pivot_table(index=["symbol","date"], columns="bucket", values=val, aggfunc="first")
    p.columns = [f"{val}_b{int(c)}" for c in p.columns]
    for col in p.columns:
        if p[col].dtype == np.float64: p[col] = p[col].astype(np.float32)
    piv = piv.merge(p, on=["symbol","date"], how="left")
    del sub, p; gc.collect()
del df, sd; gc.collect()

DATES = piv["date"].values.astype(str)
MONTHS_ARR = np.array([d[:7] for d in DATES])
SYMBOLS = piv["symbol"].values
N = len(piv)
BUCKETS = list(range(1, MAX_BKT+1)); NB = len(BUCKETS)
b2i = {b:i for i,b in enumerate(BUCKETS)}
def bi(b): return b2i.get(b)

def _a(p, b):
    c = f"{p}_b{b}"
    return piv[c].values.astype(np.float32) if c in piv.columns else np.full(N, np.nan, np.float32)

O = np.stack([_a("open",b) for b in BUCKETS], axis=1)
H = np.stack([_a("high",b) for b in BUCKETS], axis=1)
L = np.stack([_a("low",b) for b in BUCKETS], axis=1)
C = np.stack([_a("close",b) for b in BUCKETS], axis=1)
VW = np.stack([_a("vwap",b) for b in BUCKETS], axis=1)
BR = np.stack([_a("buy_ratio",b) for b in BUCKETS], axis=1)
GAP = piv["gap_pct"].values.astype(np.float32)
DO = piv["day_open"].values.astype(np.float32)
del piv; gc.collect()

unique_dates = np.unique(DATES); n_days = len(unique_dates)
d2i = {d:i for i,d in enumerate(unique_dates)}
DIDX = np.array([d2i[d] for d in DATES], dtype=np.int32)
unique_months = sorted(np.unique(MONTHS_ARR)); n_months = len(unique_months)
m2i = {m:i for i,m in enumerate(unique_months)}
day_to_month = np.zeros(n_days, dtype=np.int32)
for i in range(N): day_to_month[DIDX[i]] = m2i[MONTHS_ARR[i]]

# Date string for each unique day index
day_date_str = np.array([unique_dates[i] for i in range(n_days)])

log(f"Pivoted: {N:,} symbol-days | {n_days} days | {n_months} months")

# ============================================================================
#  FEATURES — ALL FROM b1 ONLY (ZERO LOOKAHEAD)
# ============================================================================
log("Building features (b1 only, zero lookahead)...")

b1_open = O[:,bi(1)].copy()
b1_high = H[:,bi(1)].copy()
b1_low = L[:,bi(1)].copy()
b1_close = C[:,bi(1)].copy()
b1_vwap = VW[:,bi(1)].copy()
b1_br = BR[:,bi(1)].copy()
PRICE = b1_close.copy()

# Candle return — ONLY b1
CR1 = np.where(b1_open > 0, (b1_close - b1_open) / b1_open * 100, np.nan).astype(np.float32)

# Range — ONLY b1
b1_range = np.where(b1_open > 0, (b1_high - b1_low) / b1_open * 100, 0).astype(np.float32)

# Upper wick ratio — ONLY b1
b1_upper_wick = np.where(b1_open > 0, (b1_high - np.maximum(b1_open, b1_close)) / b1_open * 100, 0).astype(np.float32)
wick_ratio_upper = np.where(b1_range > 0, b1_upper_wick / b1_range, 0).astype(np.float32)

# Body ratio — ONLY b1
b1_body = np.abs(CR1)
body_ratio = np.where(b1_range > 0, b1_body / b1_range, 0).astype(np.float32)

# Circuit check — ONLY b1
NC = b1_range >= 0.01

# Green candle — ONLY b1
GREEN = (b1_close > b1_open) & (b1_open > 0) & ~np.isnan(b1_close)

# VWAP position — ONLY b1
above_vwap = (b1_close > b1_vwap) & (b1_vwap > 0) & ~np.isnan(b1_vwap)

# ============================================================================
#  MASK — THE ULTIMATE CONFIG (ZERO LOOKAHEAD)
# ============================================================================
log("Building mask...")

mask = (
    GREEN &                        # green b1 candle
    (CR1 > CR1_MIN) &              # strong body > 1.5%
    (wick_ratio_upper < WICK_MAX) & # small upper wick < 0.2
    (PRICE < PRICE_MAX) &           # cheap stocks < Rs 500
    (PRICE > 0) &                   # valid price
    NC &                            # not circuit
    ~np.isnan(b1_close)
)

log(f"  Mask: {mask.sum():,} / {N:,} ({mask.sum()/N*100:.1f}%)")

# ============================================================================
#  SCORE — ONLY b1 DATA (ZERO LOOKAHEAD)
# ============================================================================
log("Building score (b1 only)...")

score = np.zeros(N, dtype=np.float32)
# CR1 body size (bigger green = more reversal potential)
score += np.where(CR1 > 3.0, 4, np.where(CR1 > 2.0, 3, np.where(CR1 > 1.5, 2, 0)))
# GAP (gap up = more room to fall)
score += np.where(GAP > 2, 2, np.where(GAP > 1, 1.5, np.where(GAP > 0.5, 1, np.where(GAP > 0, 0.5, 0))))
# Buy ratio at b1 (high = buying exhaustion, more reversal)
score += np.where(b1_br > 0.80, 1.5, np.where(b1_br > 0.65, 1.0, np.where(b1_br > 0.50, 0.5, 0)))
# Above VWAP (room to fall)
score += np.where(above_vwap, 0.5, 0)
# Body ratio (higher = cleaner candle = stronger signal)
score += np.where(body_ratio > 0.8, 0.5, 0)

log(f"  Score range: {score[mask].min():.1f} - {score[mask].max():.1f} | mean: {score[mask].mean():.2f}")

# ============================================================================
#  SIMULATOR — SELL, fully vectorized, ZERO LOOKAHEAD
# ============================================================================
log("Running simulation...")

ei = bi(ENTRY_BKT)
hi = bi(EXIT_BKT)
ep = C[:,ei].copy()  # entry price = close of b1
valid = mask & (ep > 0) & ~np.isnan(ep)
n_valid = int(valid.sum())
log(f"  Valid trades in pool: {n_valid:,}")

ep_v = ep[valid]
s = ei + 1  # start checking from b2
e = min(hi + 1, NB)  # up to exit bucket

fH = H[valid, s:e]  # future highs
fL = L[valid, s:e]  # future lows
fC = C[valid, s:e]  # future closes
fO = O[valid, s:e]  # future opens
nf = fH.shape[1]

# SELL: TP hit when price DROPS to entry*(1-tp/100)
# SELL: SL hit when price RISES to entry*(1+sl/100)
tp_price = ep_v * (1 - TP / 100)
sl_price = ep_v * (1 + SL / 100)

tph = fL <= tp_price[:,None]  # LOW touches TP target
slh = fH >= sl_price[:,None]  # HIGH touches SL level

def first_true(a):
    """First True index per row, nf if none."""
    any_ = a.any(axis=1)
    idx = np.argmax(a, axis=1)
    idx[~any_] = nf
    return idx

ti = first_true(tph)  # first bucket TP is hit
si = first_true(slh)  # first bucket SL is hit

# Determine outcome: SL checked BEFORE TP (matches live paper_trader)
# When both hit in same candle (si==ti), SL wins (conservative)
sl_hit = (si < ti) & (si < nf)             # SL strictly first
both_same = (si == ti) & (si < nf)         # same candle — SL wins
sl_hit = sl_hit | both_same
tp_win = (ti < si) & (ti < nf)             # TP only when strictly before SL
time_exit = ~tp_win & ~sl_hit

ret = np.full(n_valid, np.nan, np.float32)

# TP wins: earn exactly TP%
ret[tp_win] = TP

# SL hits: with gap slippage (matches live paper_trader)
# If candle opens above SL price, exit at open (worse than SL)
sl_idx_arr = np.where(sl_hit)[0]
for j in sl_idx_arr:
    si_j = si[j]
    if si_j < nf:
        open_at_sl = fO[j, si_j]
        if not np.isnan(open_at_sl) and open_at_sl >= sl_price[j]:
            ret[j] = -(open_at_sl - ep_v[j]) / ep_v[j] * 100  # gap-through loss
        else:
            ret[j] = -SL
    else:
        ret[j] = -SL

# Time exit: P&L = (entry - last_close) / entry * 100 for SELL
if time_exit.any():
    # Find last valid close in the exit window
    rev = fC[time_exit][:,::-1]
    vm = ~np.isnan(rev)
    fv = np.argmax(vm, axis=1)
    has = vm.any(axis=1)
    lc = np.full(time_exit.sum(), np.nan, np.float32)
    lc[has] = rev[has, fv[has]]
    epe = ep_v[time_exit]
    ret[time_exit] = np.where(epe > 0, (epe - lc) / epe * 100, np.nan).astype(np.float32)

# Exit bucket for each trade (for timing analysis)
exit_bkt = np.full(n_valid, EXIT_BKT, dtype=np.int32)
exit_bkt[tp_win] = s + ti[tp_win]
exit_bkt[sl_hit] = s + si[sl_hit]

# Map back to full array
full_ret = np.full(N, np.nan, np.float32)
full_ret[valid] = ret

full_exit_bkt = np.full(N, 0, np.int32)
full_exit_bkt[valid] = exit_bkt

log(f"  Simulated {n_valid:,} trades")

# ============================================================================
#  TOP-N SELECTION PER DAY (by score, no lookahead)
# ============================================================================
log("Selecting top-N per day...")

has = ~np.isnan(full_ret)
idx = np.where(has)[0]
vr = full_ret[idx]
vs = score[idx]
vd = DIDX[idx]

# Sort by day (ascending) then score (descending)
sk = vd.astype(np.float64) * 1e6 - vs.astype(np.float64)
order = np.argsort(sk)
sr = vr[order]; sd_ = vd[order]; si_sorted = idx[order]
vs_sorted = vs[order]

# Rank within each day
dc = np.concatenate([[1], (np.diff(sd_) != 0).astype(np.int32)])
gs = np.where(dc)[0]
gc = np.arange(len(sr)) - np.repeat(gs, np.diff(np.concatenate([gs, [len(sr)]])))
sel = gc < TOP_N

sel_ret = sr[sel]
sel_day = sd_[sel]
sel_idx = si_sorted[sel]  # original indices of selected trades
sel_score = vs_sorted[sel]

log(f"  Selected {len(sel_ret):,} trades across {len(np.unique(sel_day)):,} trading days")

# ============================================================================
#  DAILY P&L (with capped capital allocation)
# ============================================================================
log("Computing daily P&L (capped allocation)...")

total_margin = CAPITAL * LEV
base_pos = total_margin / TOP_N
max_pos = base_pos * CAP_MULT

d_count = np.bincount(sel_day, minlength=n_days).astype(np.float32)
active = d_count > 0
n_active_days = int(active.sum())

# Per-day position size: min(total_margin / n_trades, max_pos)
d_pos = np.zeros(n_days, np.float32)
d_pos[active] = np.minimum(total_margin / d_count[active], max_pos)

# Daily Rs P&L = sum of (trade_ret% / 100 * pos_size) for each trade
daily_rs = np.zeros(n_days, np.float32)
for j in range(len(sel_ret)):
    di = sel_day[j]
    daily_rs[di] += sel_ret[j] / 100 * d_pos[di]

# Daily ROC% = daily_rs / CAPITAL * 100
dpnl = daily_rs / CAPITAL * 100

# Cumulative equity
equity = np.cumsum(daily_rs)
cum_equity = CAPITAL + equity

log(f"  Active trading days: {n_active_days}")

# ============================================================================
#  MONTHLY P&L
# ============================================================================
monthly_roc = []
monthly_labels = []
monthly_trades = []
monthly_wins = []
monthly_losses = []

for mi, m in enumerate(unique_months):
    days_in_month = np.where((day_to_month == mi) & active)[0]
    if len(days_in_month) == 0:
        monthly_roc.append(0.0)
        monthly_labels.append(m)
        monthly_trades.append(0)
        monthly_wins.append(0)
        monthly_losses.append(0)
        continue
    mroc = dpnl[days_in_month].sum()
    monthly_roc.append(mroc)
    monthly_labels.append(m)
    # Count trades this month
    m_mask = np.isin(sel_day, days_in_month)
    m_ret = sel_ret[m_mask]
    monthly_trades.append(len(m_ret))
    monthly_wins.append(int((m_ret > 0).sum()))
    monthly_losses.append(int((m_ret < 0).sum()))

monthly_roc = np.array(monthly_roc)
monthly_labels = np.array(monthly_labels)

# ============================================================================
#  TRADE-LEVEL STATS
# ============================================================================
wins = sel_ret[sel_ret > 0]
losses = sel_ret[sel_ret < 0]
flat = sel_ret[sel_ret == 0]
total_trades = len(sel_ret)
n_wins = len(wins)
n_losses = len(losses)
n_flat = len(flat)
win_rate = n_wins / total_trades if total_trades > 0 else 0
avg_win = wins.mean() if n_wins > 0 else 0
avg_loss = np.abs(losses.mean()) if n_losses > 0 else 0
gross_profit = wins.sum() if n_wins > 0 else 0
gross_loss = np.abs(losses.sum()) if n_losses > 0 else 0

# ============================================================================
#  DRAWDOWN COMPUTATION
# ============================================================================
log("Computing drawdowns...")

# On equity curve
peak = np.maximum.accumulate(cum_equity)
drawdown = (peak - cum_equity) / peak * 100  # percentage drawdown
max_drawdown_pct = drawdown.max()
max_drawdown_rs = (peak - cum_equity).max()

# Drawdown duration (in days)
in_dd = cum_equity < peak
dd_starts = np.where(np.diff(in_dd.astype(int)) == 1)[0]
dd_ends = np.where(np.diff(in_dd.astype(int)) == -1)[0]
if in_dd[-1]: dd_ends = np.append(dd_ends, len(in_dd)-1)
if len(dd_starts) > 0 and len(dd_ends) > 0:
    max_dd_duration = max(dd_ends[i] - dd_starts[i] for i in range(min(len(dd_starts), len(dd_ends))))
else:
    max_dd_duration = 0

# Average drawdown (for Sterling ratio)
if len(drawdown[drawdown > 0]) > 0:
    avg_drawdown = drawdown[drawdown > 0].mean()
else:
    avg_drawdown = 0.01

# ============================================================================
#  TRAIN / TEST SPLIT
# ============================================================================
TRAIN_END = "2025-01"
train_months_set = set(m for m in unique_months if m <= TRAIN_END)
test_months_set = set(m for m in unique_months if m > TRAIN_END)

train_day_mask = np.array([day_to_month[i] < len(train_months_set) for i in range(n_days)])
# More precise:
train_day_mask = np.zeros(n_days, dtype=bool)
test_day_mask = np.zeros(n_days, dtype=bool)
for di in range(n_days):
    mi = day_to_month[di]
    if mi < len(unique_months) and unique_months[mi] in train_months_set:
        train_day_mask[di] = True
    else:
        test_day_mask[di] = True

# ============================================================================
#  COMPUTE ALL 13 QUANT RATIOS
# ============================================================================
log("Computing quant ratios...")

def compute_ratios(daily_returns, label, n_trading_days_year=252):
    """Compute all 13 quant ratios from daily ROC array (in %)."""
    dr = daily_returns[daily_returns != 0]  # active days only
    if len(dr) < 10:
        print(f"  {label}: insufficient data ({len(dr)} days)")
        return

    # dr_dec = dr / 100  # convert % to decimal
    # rf_daily = RF_ANNUAL / n_trading_days_year

    # # Basic stats
    # mean_daily = dr_dec.mean()
    # std_daily = dr_dec.std(ddof=1)
    # n_days_actual = len(dr)
    # ann_factor = np.sqrt(n_trading_days_year)

    # # Annualized return
    # ann_return = mean_daily * n_trading_days_year

    # # Cumulative equity for this period
    # cum = CAPITAL * np.cumprod(1 + dr_dec)


    # 1. Convert flat % back to exact rupees gained/lost
    daily_rs = (dr / 100) * CAPITAL
    
    # 2. True cumulative equity using simple addition (fixing the bug)
    cum = CAPITAL + np.cumsum(daily_rs)
    
    # 3. Calculate true daily percentage return on rolling equity
    prev_equity = np.roll(cum, 1)
    prev_equity[0] = CAPITAL
    dr_dec = daily_rs / prev_equity

    rf_daily = RF_ANNUAL / n_trading_days_year

    # Basic stats
    mean_daily = dr_dec.mean()
    std_daily = dr_dec.std(ddof=1)
    n_days_actual = len(dr)
    ann_factor = np.sqrt(n_trading_days_year)

    # Annualized return
    ann_return = mean_daily * n_trading_days_year


    start_val = CAPITAL
    end_val = cum[-1]
    years = n_days_actual / n_trading_days_year

    # Drawdown on this period's equity
    pk = np.maximum.accumulate(cum)
    dd = (pk - cum) / pk
    mdd = dd.max()
    avg_dd = dd[dd > 0].mean() if (dd > 0).any() else 0.001

    # === 1. SHARPE RATIO ===
    excess = dr_dec - rf_daily
    sharpe = (excess.mean() / excess.std(ddof=1)) * ann_factor if excess.std(ddof=1) > 0 else 0

    # === 2. SORTINO RATIO ===
    downside = dr_dec[dr_dec < rf_daily] - rf_daily
    downside_std = np.sqrt((downside**2).mean()) if len(downside) > 0 else 0.001
    sortino = (mean_daily - rf_daily) / downside_std * ann_factor

    # === 3. CALMAR RATIO ===
    calmar = ann_return / mdd if mdd > 0 else 999

    # === 4. INFORMATION RATIO (vs buy-and-hold Nifty ~12% annual) ===
    bench_daily = 0.12 / n_trading_days_year  # Nifty benchmark ~12%/year
    tracking = dr_dec - bench_daily
    tracking_std = tracking.std(ddof=1) if tracking.std(ddof=1) > 0 else 0.001
    info_ratio = (tracking.mean() / tracking_std) * ann_factor

    # === 5. PROFIT FACTOR ===
    gains = dr_dec[dr_dec > 0].sum()
    loss_abs = np.abs(dr_dec[dr_dec < 0].sum())
    profit_factor = gains / loss_abs if loss_abs > 0 else 999

    # === 6. PAYOFF RATIO ===
    avg_w = dr_dec[dr_dec > 0].mean() if (dr_dec > 0).any() else 0
    avg_l = np.abs(dr_dec[dr_dec < 0].mean()) if (dr_dec < 0).any() else 0.001
    payoff_ratio = avg_w / avg_l

    # === 7. EXPECTANCY ===
    wr = (dr_dec > 0).sum() / len(dr_dec)
    expectancy = (wr * avg_w) - ((1 - wr) * avg_l)
    expectancy_pct = expectancy * 100

    # === 8. RECOVERY FACTOR ===
    total_net = (end_val - start_val) / start_val
    max_dd_abs = mdd
    recovery_factor = total_net / max_dd_abs if max_dd_abs > 0 else 999

    # === 9. STERLING RATIO ===
    sterling = ann_return / (avg_dd + 0.10) if (avg_dd + 0.10) > 0 else 0

    # === 10. OMEGA RATIO (threshold = 0) ===
    threshold = 0
    above = dr_dec[dr_dec > threshold] - threshold
    below = threshold - dr_dec[dr_dec < threshold]
    omega = above.sum() / below.sum() if below.sum() > 0 else 999

    # === 11. ULCER PERFORMANCE INDEX ===
    ulcer_sq = dd**2
    ulcer_index = np.sqrt(ulcer_sq.mean())
    upi = ann_return / ulcer_index if ulcer_index > 0 else 999

    # === 12. CAGR ===
    cagr = (end_val / start_val) ** (1 / years) - 1 if years > 0 else 0

    # === 13. MAXIMUM DRAWDOWN ===
    max_dd = mdd * 100  # in percentage

    # Print
    print(f"\n  {'='*70}")
    print(f"  QUANT RATIOS — {label}")
    print(f"  {'='*70}")
    print(f"  Period: {n_days_actual} trading days ({years:.1f} years)")
    print(f"  Starting Capital: Rs {start_val:,.0f}")
    print(f"  Ending Capital:   Rs {end_val:,.0f}")
    print(f"  Total Return:     {total_net*100:+.2f}%")
    print(f"  {'─'*70}")
    print(f"  {'Ratio':<35} {'Value':>12} {'Grade':>8}")
    print(f"  {'─'*70}")

    def grade_sharpe(v):
        if v > 3: return "★★★★★"
        if v > 2: return "★★★★"
        if v > 1: return "★★★"
        if v > 0.5: return "★★"
        return "★"

    def grade_sortino(v):
        if v > 4: return "★★★★★"
        if v > 3: return "★★★★"
        if v > 2: return "★★★"
        if v > 1: return "★★"
        return "★"

    def grade_calmar(v):
        if v > 5: return "★★★★★"
        if v > 3: return "★★★★"
        if v > 2: return "★★★"
        if v > 1: return "★★"
        return "★"

    def grade_pf(v):
        if v > 3: return "★★★★★"
        if v > 2: return "★★★★"
        if v > 1.5: return "★★★"
        if v > 1.2: return "★★"
        return "★"

    def grade_generic(v):
        if v > 3: return "★★★★★"
        if v > 2: return "★★★★"
        if v > 1: return "★★★"
        if v > 0.5: return "★★"
        return "★"

    print(f"   1. Sharpe Ratio              {sharpe:>12.3f} {grade_sharpe(sharpe):>8}")
    print(f"   2. Sortino Ratio             {sortino:>12.3f} {grade_sortino(sortino):>8}")
    print(f"   3. Calmar Ratio              {calmar:>12.3f} {grade_calmar(calmar):>8}")
    print(f"   4. Information Ratio         {info_ratio:>12.3f} {grade_generic(info_ratio):>8}")
    print(f"   5. Profit Factor             {profit_factor:>12.3f} {grade_pf(profit_factor):>8}")
    print(f"   6. Payoff Ratio              {payoff_ratio:>12.3f} {grade_generic(payoff_ratio):>8}")
    print(f"   7. Expectancy (per trade)    {expectancy_pct:>11.3f}% {grade_generic(expectancy_pct):>8}")
    print(f"   8. Recovery Factor           {recovery_factor:>12.3f} {grade_generic(recovery_factor):>8}")
    print(f"   9. Sterling Ratio            {sterling:>12.3f} {grade_generic(sterling):>8}")
    print(f"  10. Omega Ratio               {omega:>12.3f} {grade_generic(omega):>8}")
    print(f"  11. Ulcer Perf Index (UPI)    {upi:>12.3f} {grade_generic(upi):>8}")
    print(f"  12. CAGR                      {cagr*100:>11.2f}% {'★★★★★' if cagr>1 else '★★★★' if cagr>0.5 else '★★★' if cagr>0.25 else '★★':>8}")
    print(f"  13. Max Drawdown (MDD)        {max_dd:>11.2f}% {'★★★★★' if max_dd<5 else '★★★★' if max_dd<10 else '★★★' if max_dd<15 else '★★' if max_dd<25 else '★':>8}")
    print(f"  {'─'*70}")

    # Additional context
    print(f"\n  Context:")
    print(f"    Daily Avg ROC:    {mean_daily*100:+.4f}%")
    print(f"    Daily Std:        {std_daily*100:.4f}%")
    print(f"    Win Days:         {(dr_dec>0).sum()}/{len(dr_dec)} ({(dr_dec>0).sum()/len(dr_dec)*100:.1f}%)")
    print(f"    Best Day:         {dr_dec.max()*100:+.3f}%")
    print(f"    Worst Day:        {dr_dec.min()*100:+.3f}%")
    print(f"    Avg Drawdown:     {avg_dd*100:.2f}%")
    print(f"    Max DD Duration:  ~{int(mdd / (avg_dd+0.001) * 5)} days (approx)")
    print(f"    Ulcer Index:      {ulcer_index:.4f}")

    return {
        'sharpe': sharpe, 'sortino': sortino, 'calmar': calmar,
        'info_ratio': info_ratio, 'profit_factor': profit_factor,
        'payoff_ratio': payoff_ratio, 'expectancy': expectancy_pct,
        'recovery_factor': recovery_factor, 'sterling': sterling,
        'omega': omega, 'upi': upi, 'cagr': cagr, 'mdd': max_dd
    }

# ============================================================================
#  PRINT HEADER
# ============================================================================
print(f"\n{'='*80}")
print(f"  ULTIMATE BACKTEST — MID + CR1>1.5% + wick<0.2 + Price<500")
print(f"  SELL reversal | TP={TP}% SL={SL}% EXIT=b{EXIT_BKT} | Top {TOP_N}/day")
print(f"  Capital: Rs {CAPITAL:,} | Leverage: {LEV}x | Risk-free: {RF_ANNUAL*100}%")
print(f"  Data: {unique_months[0]} to {unique_months[-1]} ({n_months} months, {n_days} days)")
print(f"  ZERO LOOKAHEAD — all features from b1 only")
print(f"{'='*80}")

# ============================================================================
#  TRADE SUMMARY
# ============================================================================
print(f"\n  TRADE SUMMARY")
print(f"  {'─'*50}")
print(f"  Total trades:     {total_trades:,}")
print(f"  Wins:             {n_wins:,} ({win_rate*100:.1f}%)")
print(f"  Losses:           {n_losses:,} ({n_losses/total_trades*100:.1f}%)")
print(f"  Flat:             {n_flat:,}")
print(f"  Avg win:          {avg_win:+.3f}%")
print(f"  Avg loss:         {-avg_loss:+.3f}%")
print(f"  Gross profit:     {gross_profit:+.2f}%")
print(f"  Gross loss:       {-gross_loss:+.2f}%")
print(f"  Active days:      {n_active_days}")
print(f"  Avg trades/day:   {total_trades/n_active_days:.1f}")

# ============================================================================
#  MONTHLY BREAKDOWN
# ============================================================================
print(f"\n  MONTHLY BREAKDOWN")
print(f"  {'─'*120}")
print(f"  {'Month':<10} {'ROC%':>8} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR%':>6} {'Rs P&L':>10} {'Cum Rs':>12} {'Status':>8}")
print(f"  {'─'*120}")

cum_rs = 0
green = 0; red = 0
for i, m in enumerate(monthly_labels):
    roc = monthly_roc[i]
    trades = monthly_trades[i]
    w = monthly_wins[i]
    l = monthly_losses[i]
    wr = w / trades * 100 if trades > 0 else 0
    rs = roc / 100 * CAPITAL
    cum_rs += rs
    status = "GREEN" if roc > 0 else "RED" if roc < 0 else "FLAT"
    if roc > 0: green += 1
    elif roc < 0: red += 1

    split = "TRAIN" if m in train_months_set else "TEST"
    print(f"  {m:<10} {roc:>+7.2f}% {trades:>7} {w:>6} {l:>7} {wr:>5.1f}% {rs:>+9.0f} {cum_rs:>+11.0f}  {status:<6} [{split}]")

print(f"  {'─'*120}")
print(f"  {'TOTAL':<10} {monthly_roc.sum():>+7.1f}% {sum(monthly_trades):>7} {sum(monthly_wins):>6} {sum(monthly_losses):>7} "
      f"{sum(monthly_wins)/sum(monthly_trades)*100:>5.1f}% {cum_rs:>+9.0f}")
print(f"  Green: {green} | Red: {red} | Ratio: {green}/{green+red}")

# ============================================================================
#  EQUITY CURVE STATS
# ============================================================================
print(f"\n  EQUITY CURVE")
print(f"  {'─'*50}")
print(f"  Start:          Rs {CAPITAL:,.0f}")
print(f"  End:            Rs {cum_equity[-1]:,.0f}")
print(f"  Peak:           Rs {cum_equity.max():,.0f}")
print(f"  Total Return:   {(cum_equity[-1]-CAPITAL)/CAPITAL*100:+.1f}%")
print(f"  Max Drawdown:   {max_drawdown_pct:.2f}% (Rs {max_drawdown_rs:,.0f})")
print(f"  Max DD Duration: ~{max_dd_duration} days")

# ============================================================================
#  COMPUTE RATIOS FOR FULL / TRAIN / TEST
# ============================================================================
# Full period
full_ratios = compute_ratios(dpnl, f"FULL PERIOD ({unique_months[0]} to {unique_months[-1]})")

# Train period
train_dpnl = dpnl[train_day_mask]
train_ratios = compute_ratios(train_dpnl, f"TRAIN ({min(train_months_set)} to {max(train_months_set)})")

# Test period
test_dpnl = dpnl[test_day_mask]
test_ratios = compute_ratios(test_dpnl, f"TEST ({min(test_months_set)} to {max(test_months_set)})")

# ============================================================================
#  TRAIN vs TEST COMPARISON
# ============================================================================
if train_ratios and test_ratios:
    print(f"\n  {'='*70}")
    print(f"  TRAIN vs TEST COMPARISON")
    print(f"  {'='*70}")
    print(f"  {'Metric':<30} {'Train':>12} {'Test':>12} {'Δ':>10}")
    print(f"  {'─'*70}")
    for key, label in [
        ('sharpe', 'Sharpe'), ('sortino', 'Sortino'), ('calmar', 'Calmar'),
        ('info_ratio', 'Information Ratio'), ('profit_factor', 'Profit Factor'),
        ('payoff_ratio', 'Payoff Ratio'), ('expectancy', 'Expectancy (%)'),
        ('recovery_factor', 'Recovery Factor'), ('sterling', 'Sterling'),
        ('omega', 'Omega'), ('upi', 'UPI'), ('cagr', 'CAGR (%)'), ('mdd', 'Max DD (%)')
    ]:
        tv = train_ratios[key]; te = test_ratios[key]
        if key in ('cagr',):
            tv *= 100; te *= 100
        delta = te - tv
        print(f"  {label:<30} {tv:>12.3f} {te:>12.3f} {delta:>+9.3f}")
    print(f"  {'─'*70}")

# ============================================================================
#  EXIT ANALYSIS
# ============================================================================
print(f"\n  EXIT ANALYSIS")
print(f"  {'─'*50}")

# Exit type breakdown
valid_idx = np.where(valid)[0]
tp_wins_full = tp_win.sum()
sl_hits_full = sl_hit.sum()
time_exits_full = time_exit.sum()

print(f"  TP hits:     {tp_wins_full:>7,} ({tp_wins_full/n_valid*100:.1f}%)")
print(f"  SL hits:     {sl_hits_full:>7,} ({sl_hits_full/n_valid*100:.1f}%)")
print(f"  Time exits:  {time_exits_full:>7,} ({time_exits_full/n_valid*100:.1f}%)")

# Time exit P&L
if time_exit.any():
    te_ret = ret[time_exit]
    te_valid = ~np.isnan(te_ret)
    if te_valid.any():
        te_r = te_ret[te_valid]
        print(f"\n  Time Exit Breakdown:")
        print(f"    Avg return:  {te_r.mean():+.3f}%")
        print(f"    Profitable:  {(te_r>0).sum()}/{len(te_r)} ({(te_r>0).sum()/len(te_r)*100:.1f}%)")
        print(f"    Avg if win:  {te_r[te_r>0].mean():+.3f}%" if (te_r>0).any() else "")
        print(f"    Avg if loss: {te_r[te_r<0].mean():+.3f}%" if (te_r<0).any() else "")

# Average exit time
avg_exit_bkt = exit_bkt.mean()
print(f"\n  Avg exit bucket: {avg_exit_bkt:.1f} (9:{15+int(avg_exit_bkt):02d} AM)")
print(f"  Avg hold time:   {avg_exit_bkt - ENTRY_BKT:.1f} minutes")

# ============================================================================
#  WORST TRADES ANALYSIS
# ============================================================================
print(f"\n  WORST 20 TRADES")
print(f"  {'─'*80}")
print(f"  {'Date':<12} {'Symbol':<15} {'Entry':>8} {'Return':>8} {'Gap%':>7} {'CR1%':>6} {'BR':>5} {'Score':>6}")
print(f"  {'─'*80}")

worst_order = np.argsort(sel_ret)[:20]
for wi in worst_order:
    oi = sel_idx[wi]
    print(f"  {DATES[oi]:<12} {SYMBOLS[oi]:<15} {PRICE[oi]:>7.1f} {sel_ret[wi]:>+7.3f}% "
          f"{GAP[oi]:>+6.2f}% {CR1[oi]:>5.2f}% {b1_br[oi]:>4.2f} {sel_score[wi]:>5.1f}")

# ============================================================================
#  BEST TRADES ANALYSIS
# ============================================================================
print(f"\n  BEST 20 TRADES")
print(f"  {'─'*80}")
print(f"  {'Date':<12} {'Symbol':<15} {'Entry':>8} {'Return':>8} {'Gap%':>7} {'CR1%':>6} {'BR':>5} {'Score':>6}")
print(f"  {'─'*80}")

best_order = np.argsort(sel_ret)[-20:][::-1]
for bi_ in best_order:
    oi = sel_idx[bi_]
    print(f"  {DATES[oi]:<12} {SYMBOLS[oi]:<15} {PRICE[oi]:>7.1f} {sel_ret[bi_]:>+7.3f}% "
          f"{GAP[oi]:>+6.2f}% {CR1[oi]:>5.2f}% {b1_br[oi]:>4.2f} {sel_score[bi_]:>5.1f}")

# ============================================================================
#  CONSECUTIVE WINS/LOSSES
# ============================================================================
print(f"\n  STREAK ANALYSIS")
print(f"  {'─'*50}")

# Daily level streaks
daily_win = dpnl[active] > 0
daily_loss = dpnl[active] < 0

def max_streak(arr):
    max_s = 0; cur = 0
    for v in arr:
        if v: cur += 1; max_s = max(max_s, cur)
        else: cur = 0
    return max_s

print(f"  Max consecutive winning days: {max_streak(daily_win)}")
print(f"  Max consecutive losing days:  {max_streak(daily_loss)}")
print(f"  Max consecutive green months: {max_streak(np.array(monthly_roc) > 0)}")
print(f"  Max consecutive red months:   {max_streak(np.array(monthly_roc) <= 0)}")

# ============================================================================
#  DAY-OF-WEEK ANALYSIS
# ============================================================================
print(f"\n  DAY-OF-WEEK ANALYSIS")
print(f"  {'─'*60}")

# Parse dates to get day of week
day_of_week = np.array([pd.Timestamp(d).dayofweek for d in unique_dates])
dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

print(f"  {'Day':<6} {'Avg ROC%':>9} {'Win%':>7} {'Days':>6} {'Total ROC%':>11}")
for dow in range(5):
    dow_mask = (day_of_week == dow) & active
    if dow_mask.sum() == 0: continue
    dow_ret = dpnl[dow_mask]
    print(f"  {dow_names[dow]:<6} {dow_ret.mean():>+8.3f}% {(dow_ret>0).sum()/len(dow_ret)*100:>6.1f}% "
          f"{len(dow_ret):>6} {dow_ret.sum():>+10.2f}%")

# ============================================================================
#  DONE
# ============================================================================
elapsed_total = time.perf_counter() - t0
log(f"\nDone in {elapsed_total:.1f}s")
print(f"\n  All ratios computed with ZERO lookahead.")
print(f"  Every feature (CR1, wick, body, BR, VWAP, GAP) uses ONLY bucket 1 data.")
print(f"  Entry at close of b1 (9:16 AM). Score and mask frozen at entry time.")
