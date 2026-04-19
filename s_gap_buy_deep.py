"""
s_gap_buy_deep.py — BUY twin of the champion SELL strategy
===========================================================
Strategy: GAP < -1.5% + Price < Rs 1000 → BUY at b1 close
Config : TP=3.0% SL=0.5% EXIT=b45 | Top 15/day ranked by |GAP|
         50k capital, 5x margin, COST=0.15% per trade

Mirror of s_gap15_p1k_deep.py with direction flipped:
  SELL gap-up  → short, profit when price drops
  BUY  gap-dn  → long,  profit when price rises

All FIXES kept:
  FIX 1 — SL before TP on same-candle tie (conservative)
  FIX 2 — Gap-through slippage on SL (for BUY: OPEN below SL price → exit at OPEN)
  FIX 3 — Transaction cost subtracted from every return (COST=0.15%)

Output: analysis_gap_buy_p1k/ (equity curve, monthly chart, drawdown, etc.)

Use this to see if adding a BUY leg to the SELL champion creates a
diversified portfolio (lower correlation, smoother equity curve).
"""

import sys, io, time, json, warnings, signal, os, gc
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
signal.signal(signal.SIGINT, lambda *_: (print("\nInterrupted!"), os._exit(1)))

BASE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = BASE_DIR / "data"
PARQUET_DIR = BASE_DIR / "parquets"
OUT_DIR     = BASE_DIR / "analysis_gap_buy_p1k"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CAPITAL = 50000; LEV = 5; RF_ANNUAL = 0.065
TP = 3.0; SL = 0.5; EXIT_BKT = 45; TOP_N = 15; ENTRY_BKT = 1
CAP_MULT = 2; COST = 0.15

GAP_THR   = -1.5      # BUY when gap <= -1.5%
PRICE_MAX = 1000

t0 = time.perf_counter()
def elapsed():
    s = time.perf_counter() - t0; m = int(s // 60)
    return f"[{m:02d}:{s%60:05.2f}]" if m else f"[{s:05.2f}s]"
def log(msg): print(f"{elapsed()} {msg}", flush=True)

# ─── LOAD ────────────────────────────────────────────────────────────────────
log("Loading (including 2022 unseen)...")
vg = json.load(open(DATA_DIR / "volume_groups.json"))["volume_groups"]
TARGET = set(vg.get("MEGA (>100cr/day)", [])) | set(vg.get("LARGE (10-100cr/day)", []))

ALL_M = (list(range(202201, 202213)) + list(range(202301, 202313)) +
         list(range(202401, 202413)) + list(range(202501, 202513)) +
         [202601, 202602, 202603])
COLS = ["symbol", "date", "gap_pct", "day_open", "bucket",
        "open", "high", "low", "close", "vwap", "buy_ratio", "volume"]
MAX_BKT = EXIT_BKT + 1

dfs = []
for ym in ALL_M:
    p = PARQUET_DIR / f"candles_{ym}.parquet"
    if not p.exists(): continue
    d = pd.read_parquet(p, columns=COLS)
    d = d[(d["bucket"] <= MAX_BKT) & (d["symbol"].isin(TARGET))]
    for c in ["open", "high", "low", "close", "gap_pct", "day_open", "vwap", "buy_ratio"]:
        d[c] = d[c].astype(np.float32)
    d["volume"] = d["volume"].astype(np.int32)
    dfs.append(d); log(f"  {ym}: {len(d):,} rows")
df = pd.concat(dfs, ignore_index=True); del dfs; gc.collect()
log(f"Total: {len(df):,} rows | {df['symbol'].nunique()} syms | {df['date'].nunique()} days")

# ─── PIVOT ───────────────────────────────────────────────────────────────────
log("Pivoting...")
sd = df.groupby(["symbol", "date"]).agg(
        gap_pct=("gap_pct", "first"),
        day_open=("day_open", "first")).reset_index()
piv = sd.copy()
for val in ["close", "open", "high", "low"]:
    sub = df[["symbol", "date", "bucket", val]]
    p = sub.pivot_table(index=["symbol", "date"], columns="bucket",
                        values=val, aggfunc="first")
    p.columns = [f"{val}_b{int(c)}" for c in p.columns]
    for col in p.columns:
        if p[col].dtype == np.float64: p[col] = p[col].astype(np.float32)
    piv = piv.merge(p, on=["symbol", "date"], how="left")
    del sub, p; gc.collect()
del df, sd; gc.collect()

DATES  = piv["date"].values.astype(str)
MONTHS = np.array([d[:7] for d in DATES])
SYMS   = piv["symbol"].values
N = len(piv)
BKTS = list(range(1, MAX_BKT + 1)); NB = len(BKTS)
b2i = {b: i for i, b in enumerate(BKTS)}
def bi(b): return b2i[b]
def _a(pfx, b):
    c = f"{pfx}_b{b}"
    return piv[c].values.astype(np.float32) if c in piv.columns \
           else np.full(N, np.nan, np.float32)

O = np.stack([_a("open",  b) for b in BKTS], 1)
H = np.stack([_a("high",  b) for b in BKTS], 1)
L = np.stack([_a("low",   b) for b in BKTS], 1)
C = np.stack([_a("close", b) for b in BKTS], 1)
GAP = piv["gap_pct"].values.astype(np.float32)
del piv; gc.collect()

udates = np.unique(DATES); nd = len(udates)
d2i = {d: i for i, d in enumerate(udates)}
DIDX = np.array([d2i[d] for d in DATES], np.int32)
umonths = sorted(np.unique(MONTHS)); nm = len(umonths)
m2i = {m: i for i, m in enumerate(umonths)}
d2m = np.zeros(nd, np.int32)
for i in range(N): d2m[DIDX[i]] = m2i[MONTHS[i]]

PRICE = C[:, bi(1)].copy()
VALID = (O[:, bi(1)] > 0) & ~np.isnan(C[:, bi(1)])
NC    = np.where(O[:, bi(1)] > 0,
                 (H[:, bi(1)] - L[:, bi(1)]) / O[:, bi(1)] * 100, 0) >= 0.01
log(f"Pivoted: {N:,} sd | {nd} days | {nm} months ({umonths[0]}..{umonths[-1]})")

# ─── MASK + SIMULATE (BUY direction) ─────────────────────────────────────────
log("Mask + Simulate (BUY long)...")
mask = (GAP <= GAP_THR) & (PRICE < PRICE_MAX) & (PRICE > 0) & NC & VALID
log(f"  Pool: {mask.sum():,}")

ei = bi(ENTRY_BKT); hi = bi(EXIT_BKT)
ep = C[:, ei].copy()
valid = mask & (ep > 0) & ~np.isnan(ep)
n_valid = int(valid.sum())
ep_v = ep[valid]; s = ei + 1; e = min(hi + 1, NB)
fH = H[valid, s:e]; fL = L[valid, s:e]; fC = C[valid, s:e]; fO = O[valid, s:e]
nf = fH.shape[1]

# BUY: TP when price rises (H >= tp_price), SL when price drops (L <= sl_price)
tph = fH >= ep_v[:, None] * (1 + TP / 100)
slh = fL <= ep_v[:, None] * (1 - SL / 100)

def first_true(a):
    any_ = a.any(1); ix = np.argmax(a, 1); ix[~any_] = nf; return ix
ti = first_true(tph); si = first_true(slh)

# FIX 1: SL before TP
sl_hit   = ((si < ti) | (si == ti)) & (si < nf)
tp_win   = (ti < si) & (ti < nf)
time_exit = ~tp_win & ~sl_hit

ret = np.full(n_valid, np.nan, np.float32)
ret[tp_win] = TP

# FIX 2: Gap-through slippage — for BUY, OPEN below SL price → exit at OPEN (worse)
sl_price = ep_v * (1 - SL / 100)
sl_idx = np.where(sl_hit)[0]
for j in sl_idx:
    si_j = si[j]
    if si_j < nf:
        o = fO[j, si_j]
        if not np.isnan(o) and o <= sl_price[j]:
            ret[j] = (o - ep_v[j]) / ep_v[j] * 100
        else:
            ret[j] = -SL
    else:
        ret[j] = -SL

if time_exit.any():
    rev = fC[time_exit][:, ::-1]
    vm = ~np.isnan(rev); fv = np.argmax(vm, 1); has = vm.any(1)
    lc = np.full(time_exit.sum(), np.nan, np.float32); lc[has] = rev[has, fv[has]]
    epe = ep_v[time_exit]
    # BUY return: (exit - entry) / entry * 100
    ret[time_exit] = np.where(epe > 0, (lc - epe) / epe * 100, np.nan).astype(np.float32)

# FIX 3: transaction cost
ret = ret - COST

full_ret = np.full(N, np.nan, np.float32); full_ret[valid] = ret
log(f"  Simulated {n_valid:,} trades")

# ─── TOP-N SELECTION (rank by |GAP| desc → most negative gap first) ──────────
has_r = ~np.isnan(full_ret) & mask
idx = np.where(has_r)[0]
vr = full_ret[idx]; vd = DIDX[idx]; vs = GAP[idx]
# For BUY: pick most negative gap first → sort by ascending GAP within same day
sk = vd.astype(np.float64) * 1e6 + vs.astype(np.float64)
order = np.argsort(sk)
sr = vr[order]; sd_ = vd[order]
dc = np.concatenate([[1], (np.diff(sd_) != 0).astype(np.int32)])
gs = np.where(dc)[0]
gc_ = np.arange(len(sr)) - np.repeat(gs, np.diff(np.concatenate([gs, [len(sr)]])))
sel = gc_ < TOP_N
sel_ret = sr[sel]; sel_day = sd_[sel]

# Daily P&L
total_margin = CAPITAL * LEV
base_pos = total_margin / TOP_N
max_pos  = base_pos * CAP_MULT
d_count = np.bincount(sel_day, minlength=nd).astype(np.float32)
active  = d_count > 0
d_pos = np.zeros(nd, np.float32)
d_pos[active] = np.minimum(total_margin / d_count[active], max_pos)

daily_rs = np.zeros(nd, np.float32)
for j in range(len(sel_ret)):
    di = sel_day[j]
    daily_rs[di] += sel_ret[j] / 100 * d_pos[di]
dpnl = daily_rs / CAPITAL * 100
cum_eq = CAPITAL + np.cumsum(daily_rs)
log(f"  Selected {len(sel_ret):,} trades | {int(active.sum())} active days")

# ─── STATS ───────────────────────────────────────────────────────────────────
wins = sel_ret[sel_ret > 0]; losses = sel_ret[sel_ret < 0]
total_t = len(sel_ret); n_w = len(wins); n_l = len(losses)
wr = n_w / total_t if total_t > 0 else 0

m_roc = []; m_trades = []; m_wins = []; m_pnl_rs = []
for mi, m in enumerate(umonths):
    dim = np.where((d2m == mi) & active)[0]
    mroc = dpnl[dim].sum() if len(dim) > 0 else 0.0
    mm = np.isin(sel_day, dim); mr = sel_ret[mm]
    m_roc.append(mroc); m_trades.append(len(mr))
    m_wins.append(int((mr > 0).sum())); m_pnl_rs.append(mroc / 100 * CAPITAL)
m_roc = np.array(m_roc); m_pnl_rs = np.array(m_pnl_rs)

is_unseen = np.array([m < '2023-01' for m in umonths])
is_train  = np.array([('2023-01' <= m <= '2025-01') for m in umonths])
is_test   = np.array([m >  '2025-01' for m in umonths])

# ─── REPORT ──────────────────────────────────────────────────────────────────
print(f"\n{'='*100}")
print(f"  S_GAP_BUY_P1K — BUY TWIN OF CHAMPION")
print(f"  GAP <= {GAP_THR}% + Price < Rs {PRICE_MAX} → BUY at b1 close (9:16 AM)")
print(f"  TP={TP}% SL={SL}% EXIT=b{EXIT_BKT} | Top {TOP_N}/day scored by |GAP|")
print(f"  Capital: Rs {CAPITAL:,} | Leverage: {LEV}x | Cost: {COST}%/trade")
print(f"  Data: {umonths[0]} to {umonths[-1]} ({nm} months)")
print(f"{'='*100}")

print(f"\n  TRADE SUMMARY")
print(f"  {'─'*50}")
print(f"  Pool:           {n_valid:,}")
print(f"  Selected:       {total_t:,}")
print(f"  Active days:    {int(active.sum())}")
print(f"  Wins:           {n_w:,} ({wr*100:.1f}%)")
print(f"  Losses:         {n_l:,}")
if n_w: print(f"  Avg win:        {wins.mean():+.3f}%")
if n_l: print(f"  Avg loss:       {losses.mean():+.3f}%")
print(f"  TP hits:        {int(tp_win.sum()):,} ({tp_win.sum()/max(n_valid,1)*100:.1f}%)")
print(f"  SL hits:        {int(sl_hit.sum()):,} ({sl_hit.sum()/max(n_valid,1)*100:.1f}%)")
print(f"  Time exits:     {int(time_exit.sum()):,} ({time_exit.sum()/max(n_valid,1)*100:.1f}%)")

# Monthly table
print(f"\n  MONTHLY P&L")
print(f"  {'─'*130}")
print(f"  {'Month':<10} {'ROC%':>8} {'Trades':>7} {'Wins':>6} {'WR%':>6} {'Rs P&L':>10} {'Cum Rs':>12} {'Equity':>12} {'':>8} {'Period':>8}")
print(f"  {'─'*130}")
cum_rs = 0
for mi, m in enumerate(umonths):
    roc = m_roc[mi]; t = m_trades[mi]; w = m_wins[mi]
    ww = w / t * 100 if t > 0 else 0
    rs = m_pnl_rs[mi]; cum_rs += rs
    st = "GREEN" if roc > 0 else "RED" if roc < 0 else "---"
    period = "UNSEEN" if is_unseen[mi] else "TRAIN" if is_train[mi] else "TEST"
    eq = CAPITAL + cum_rs
    if t > 0:
        print(f"  {m:<10} {roc:>+7.2f}% {t:>7} {w:>6} {ww:>5.1f}% {rs:>+9.0f} {cum_rs:>+11.0f} {eq:>11.0f} {st:<8} [{period}]")
print(f"  {'─'*130}")

for name, pm in [("UNSEEN (2022)", is_unseen),
                 ("TRAIN (2023-01..2025-01)", is_train),
                 ("TEST (2025-02..2026-03)", is_test),
                 ("FULL", np.ones(nm, bool))]:
    rocs = m_roc[pm]; trades_p = np.array(m_trades)[pm]
    if rocs.sum() == 0 and trades_p.sum() == 0: continue
    am = rocs != 0
    g = int((rocs > 0).sum()); r = int((rocs[am] <= 0).sum())
    print(f"\n  {name}:")
    print(f"    Months: {int(am.sum())} | Green: {g} Red: {r} | "
          f"Avg: {rocs[am].mean():+.2f}%/m | Total: {rocs.sum():+.1f}% | "
          f"Worst: {rocs[am].min():+.2f}%")
    print(f"    Trades: {int(trades_p.sum()):,} | Rs P&L: {m_pnl_rs[pm].sum():+,.0f}")

pk = np.maximum.accumulate(cum_eq); dd = (pk - cum_eq) / pk * 100
mdd_pct = dd.max(); mdd_rs = (pk - cum_eq).max()
print(f"\n  EQUITY CURVE")
print(f"  Start: Rs {CAPITAL:,} → End: Rs {cum_eq[-1]:,.0f}")
print(f"  Peak: Rs {pk.max():,.0f} | Max DD: {mdd_pct:.2f}% (Rs {mdd_rs:,.0f})")

# ─── 13 QUANT RATIOS (same as SELL script) ───────────────────────────────────
def compute_ratios(dpnl_arr, label):
    dr = dpnl_arr[dpnl_arr != 0]
    if len(dr) < 10:
        print(f"  {label}: insufficient data"); return None
    dr_d = dr / 100; rf_d = RF_ANNUAL / 252
    mean_d = dr_d.mean(); std_d = dr_d.std(ddof=1)
    ann_ret = mean_d * 252
    cum = CAPITAL * np.cumprod(1 + dr_d)
    years = len(dr) / 252
    pk = np.maximum.accumulate(cum); dd = (pk - cum) / pk
    mdd = dd.max(); avg_dd = dd[dd > 0].mean() if (dd > 0).any() else 0.001
    exc = dr_d - rf_d
    sharpe = (exc.mean() / exc.std(ddof=1)) * np.sqrt(252) if exc.std(ddof=1) > 0 else 0
    ds = dr_d[dr_d < rf_d] - rf_d
    ds_std = np.sqrt((ds ** 2).mean()) if len(ds) > 0 else 0.001
    sortino = (mean_d - rf_d) / ds_std * np.sqrt(252)
    calmar = ann_ret / mdd if mdd > 0 else 999
    bench = 0.12 / 252; trk = dr_d - bench
    ir = (trk.mean() / trk.std(ddof=1)) * np.sqrt(252) if trk.std(ddof=1) > 0 else 0
    g = dr_d[dr_d > 0].sum(); la = abs(dr_d[dr_d < 0].sum())
    pf = g / la if la > 0 else 999
    aw = dr_d[dr_d > 0].mean() if (dr_d > 0).any() else 0
    al = abs(dr_d[dr_d < 0].mean()) if (dr_d < 0).any() else 0.001
    payoff = aw / al
    wr_ = (dr_d > 0).sum() / len(dr_d)
    expectancy = (wr_ * aw - (1 - wr_) * al) * 100
    total_net = (cum[-1] - CAPITAL) / CAPITAL
    recovery = total_net / mdd if mdd > 0 else 999
    sterling = ann_ret / (avg_dd + 0.10)
    omega = g / la if la > 0 else 999
    ulcer = np.sqrt((dd ** 2).mean())
    upi = ann_ret / ulcer if ulcer > 0 else 999
    cagr = (cum[-1] / CAPITAL) ** (1 / years) - 1 if years > 0 else 0
    max_dd = mdd * 100

    print(f"\n  {'='*60}\n  {label}\n  {'='*60}")
    print(f"  {len(dr)} days ({years:.1f} yrs) | Rs {CAPITAL:,} → Rs {cum[-1]:,.0f}")
    print(f"  {'─'*60}")
    print(f"   1. Sharpe          {sharpe:>10.3f}")
    print(f"   2. Sortino         {sortino:>10.3f}")
    print(f"   3. Calmar          {calmar:>10.3f}")
    print(f"   4. Info Ratio      {ir:>10.3f}")
    print(f"   5. Profit Factor   {pf:>10.3f}")
    print(f"   6. Payoff Ratio    {payoff:>10.3f}")
    print(f"   7. Expectancy %    {expectancy:>9.3f}%")
    print(f"   8. Recovery Factor {recovery:>10.3f}")
    print(f"   9. Sterling        {sterling:>10.3f}")
    print(f"  10. Omega           {omega:>10.3f}")
    print(f"  11. UPI             {upi:>10.3f}")
    print(f"  12. CAGR            {cagr*100:>9.2f}%")
    print(f"  13. Max Drawdown    {max_dd:>9.2f}%")
    return dict(sharpe=sharpe, sortino=sortino, calmar=calmar, pf=pf,
                payoff=payoff, cagr=cagr * 100, mdd=max_dd)

unseen_days = np.zeros(nd, bool); train_days = np.zeros(nd, bool); test_days = np.zeros(nd, bool)
for di in range(nd):
    m = umonths[d2m[di]]
    if m < '2023-01':   unseen_days[di] = True
    elif m <= '2025-01': train_days[di] = True
    else:                test_days[di]  = True

compute_ratios(dpnl, "FULL PERIOD")
compute_ratios(dpnl[unseen_days], "UNSEEN 2022 (never touched)")
compute_ratios(dpnl[train_days],  "TRAIN (2023-01..2025-01)")
compute_ratios(dpnl[test_days],   "TEST  (2025-02..2026-03)")

# ─── CHARTS ──────────────────────────────────────────────────────────────────
log("Generating charts...")
# Equity curve
fig, ax = plt.subplots(figsize=(16, 6))
ax.plot(range(nd), cum_eq, color='#4CAF50', linewidth=1.5, label='BUY equity')
ax.fill_between(range(nd), CAPITAL, cum_eq, alpha=0.15, color='#4CAF50')
tick_pos = []; tick_lab = []
for mi, m in enumerate(umonths):
    if mi % 3 == 0:
        for di in range(nd):
            if d2m[di] == mi: tick_pos.append(di); tick_lab.append(m); break
ax.set_xticks(tick_pos); ax.set_xticklabels(tick_lab, rotation=45, fontsize=7)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f"Rs {x:,.0f}"))
ax.set_title('S_gap_buy_p1k Equity Curve', fontsize=14, fontweight='bold')
ax.set_ylabel('Equity (Rs)'); ax.set_xlabel('Trading Days')
ax.legend(); ax.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig(OUT_DIR / "equity_curve.png", dpi=150); plt.close()

# Monthly bar
fig, ax = plt.subplots(figsize=(18, 6))
colors = []
for mi in range(nm):
    if is_unseen[mi]: colors.append('#9C27B0' if m_roc[mi] > 0 else '#E91E63')
    elif is_train[mi]: colors.append('#4CAF50' if m_roc[mi] > 0 else '#F44336')
    else:               colors.append('#2196F3' if m_roc[mi] > 0 else '#FF9800')
ax.bar(range(nm), m_roc, color=colors, edgecolor='white', linewidth=0.5)
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Monthly ROC % — S_gap_buy_p1k', fontsize=14, fontweight='bold')
ax.set_ylabel('Monthly ROC %')
ax.set_xticks(range(nm)); ax.set_xticklabels([m[2:] for m in umonths], rotation=90, fontsize=7)
ax.grid(True, alpha=0.3, axis='y')
plt.tight_layout(); plt.savefig(OUT_DIR / "monthly_roc.png", dpi=150); plt.close()

# Drawdown
fig, ax = plt.subplots(figsize=(16, 4))
ax.fill_between(range(nd), -dd, 0, color='#F44336', alpha=0.4)
ax.plot(range(nd), -dd, color='#F44336', linewidth=0.5)
ax.set_title('Drawdown %', fontsize=14, fontweight='bold'); ax.set_ylabel('Drawdown %')
ax.set_xticks(tick_pos); ax.set_xticklabels(tick_lab, rotation=45, fontsize=7)
ax.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig(OUT_DIR / "drawdown.png", dpi=150); plt.close()

# Save daily_pnl for combined portfolio analysis
np.save(OUT_DIR / "daily_pnl_rs.npy", daily_rs)
np.save(OUT_DIR / "udates.npy", udates)
log(f"Saved daily_pnl_rs.npy (for combined-portfolio analysis later)")

print(f"\n  Charts saved to: {OUT_DIR}")
log(f"Done in {time.perf_counter()-t0:.1f}s")
