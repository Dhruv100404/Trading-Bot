"""
param_grid.py — Stability sweep around the champion SELL config
================================================================
Sweeps TP / SL / EXIT / GAP / PRICE around s_gap15_p1k_deep.py
and reports test-period Sharpe + CAGR + MDD + PF for every combo.

Purpose:
  If the champion sits on a PLATEAU (neighbors also good) → robust edge.
  If the champion is a SPIKE (neighbors bad) → likely overfit.

Output: analysis_gap15_p1k/param_grid.csv (sorted by test Sharpe desc)

All FIXES from champion kept:
  FIX 1 — SL before TP on same-candle tie
  FIX 2 — Gap-through slippage on SL
  FIX 3 — Transaction cost subtracted (COST=0.15%)
"""

import sys, io, time, json, warnings, os, gc
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("C:/Users/BT-25/Desktop/project/dhan-trader/data")
OUT_DIR  = DATA_DIR / "analysis_gap15_p1k"
OUT_DIR.mkdir(exist_ok=True)

CAPITAL = 50000; LEV = 5; RF_ANNUAL = 0.065
TOP_N = 15; ENTRY_BKT = 1; CAP_MULT = 2; COST = 0.15

# Grid (champion is TP=3.0, SL=0.5, EXIT=45, GAP=1.5, PRICE=1000)
TP_LIST    = [2.5, 3.0, 3.5]
SL_LIST    = [0.4, 0.5, 0.6]
EXIT_LIST  = [30, 45, 60]
GAP_LIST   = [1.25, 1.5, 1.75, 2.0]
PRICE_LIST = [750, 1000, 1500]

# ─── TIMER ───────────────────────────────────────────────────────────────────
t0 = time.perf_counter()
def log(msg):
    s = time.perf_counter() - t0
    m = int(s // 60)
    p = f"[{m:02d}:{s%60:05.2f}]" if m else f"[{s:05.2f}s]"
    print(f"{p} {msg}", flush=True)

# ─── LOAD (same universe + months as champion) ───────────────────────────────
log("Loading parquets...")
vg = json.load(open(DATA_DIR / "volume_groups.json"))["volume_groups"]
TARGET = set(vg.get("MEGA (>100cr/day)", [])) | set(vg.get("LARGE (10-100cr/day)", []))

ALL_M = (list(range(202201, 202213)) + list(range(202301, 202313)) +
         list(range(202401, 202413)) + list(range(202501, 202513)) +
         [202601, 202602, 202603])
MAX_EXIT = max(EXIT_LIST)
MAX_BKT  = MAX_EXIT + 1
COLS = ["symbol", "date", "gap_pct", "day_open", "bucket",
        "open", "high", "low", "close"]

dfs = []
for ym in ALL_M:
    p = DATA_DIR / f"candles_{ym}.parquet"
    if not p.exists(): continue
    d = pd.read_parquet(p, columns=COLS)
    d = d[(d["bucket"] <= MAX_BKT) & (d["symbol"].isin(TARGET))]
    for c in ["open", "high", "low", "close", "gap_pct", "day_open"]:
        d[c] = d[c].astype(np.float32)
    dfs.append(d)
df = pd.concat(dfs, ignore_index=True); del dfs; gc.collect()
log(f"Loaded: {len(df):,} rows | {df['symbol'].nunique()} syms | {df['date'].nunique()} days")

# ─── PIVOT TO (stock-day × bucket) MATRICES ──────────────────────────────────
log("Pivoting...")
sd = df.groupby(["symbol", "date"]).agg(
        gap_pct=("gap_pct", "first"),
        day_open=("day_open", "first")).reset_index()
piv = sd.copy()
for val in ["close", "open", "high", "low"]:
    p = df[["symbol", "date", "bucket", val]].pivot_table(
            index=["symbol", "date"], columns="bucket",
            values=val, aggfunc="first")
    p.columns = [f"{val}_b{int(c)}" for c in p.columns]
    piv = piv.merge(p, on=["symbol", "date"], how="left")
del df, sd; gc.collect()

DATES  = piv["date"].values.astype(str)
MONTHS = np.array([d[:7] for d in DATES])
N  = len(piv)
BKTS = list(range(1, MAX_BKT + 1)); NB = len(BKTS)
b2i = {b: i for i, b in enumerate(BKTS)}
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

PRICE = C[:, b2i[1]].copy()
VALID = (O[:, b2i[1]] > 0) & ~np.isnan(C[:, b2i[1]])
NC    = np.where(O[:, b2i[1]] > 0,
                 (H[:, b2i[1]] - L[:, b2i[1]]) / O[:, b2i[1]] * 100, 0) >= 0.01
log(f"Matrix ready: {N:,} sd | {nd} days | buckets 1..{MAX_BKT}")

# Day → period classification
DAY_PERIOD = np.empty(nd, dtype='U8')
for di, d in enumerate(udates):
    m = d[:7]
    if m < '2023-01':   DAY_PERIOD[di] = 'UNSEEN'
    elif m <= '2025-01': DAY_PERIOD[di] = 'TRAIN'
    else:                DAY_PERIOD[di] = 'TEST'

# ─── SIMULATOR ───────────────────────────────────────────────────────────────
def simulate(tp, sl, exit_bkt, gap_thr, price_max):
    """Return dict of stats or None if nothing qualifies."""
    mask = (GAP > gap_thr) & (PRICE < price_max) & (PRICE > 0) & NC & VALID
    ei = b2i[ENTRY_BKT]; hi = b2i[exit_bkt]
    ep = C[:, ei].copy()
    valid = mask & (ep > 0) & ~np.isnan(ep)
    if valid.sum() < 50:
        return None
    ep_v = ep[valid]
    s, e = ei + 1, min(hi + 1, NB)
    fH = H[valid, s:e]; fL = L[valid, s:e]; fC = C[valid, s:e]; fO = O[valid, s:e]
    nf = fH.shape[1]

    tph = fL <= ep_v[:, None] * (1 - tp / 100)
    slh = fH >= ep_v[:, None] * (1 + sl / 100)

    def first_true(a):
        any_ = a.any(1); ix = np.argmax(a, 1); ix[~any_] = nf; return ix
    ti = first_true(tph); si = first_true(slh)

    # FIX 1: SL checked before TP → SL wins same-candle tie
    sl_hit   = ((si < ti) | (si == ti)) & (si < nf)
    tp_win   = (ti < si) & (ti < nf)
    time_ex  = ~tp_win & ~sl_hit

    n_valid = valid.sum()
    ret = np.full(n_valid, np.nan, np.float32)
    ret[tp_win] = tp

    # FIX 2: Gap-through slippage on SL
    sl_price = ep_v * (1 + sl / 100)
    sl_idx = np.where(sl_hit)[0]
    for j in sl_idx:
        si_j = si[j]
        if si_j < nf:
            o = fO[j, si_j]
            if not np.isnan(o) and o >= sl_price[j]:
                ret[j] = -(o - ep_v[j]) / ep_v[j] * 100
            else:
                ret[j] = -sl
        else:
            ret[j] = -sl

    if time_ex.any():
        rev = fC[time_ex][:, ::-1]
        vm = ~np.isnan(rev); fv = np.argmax(vm, 1); has = vm.any(1)
        lc = np.full(time_ex.sum(), np.nan, np.float32); lc[has] = rev[has, fv[has]]
        epe = ep_v[time_ex]
        ret[time_ex] = np.where(epe > 0, (epe - lc) / epe * 100, np.nan).astype(np.float32)

    # FIX 3: transaction cost
    ret = ret - COST

    # Top-N per day by GAP
    idx = np.where(valid)[0]
    vr = ret; vd = DIDX[idx]; vs = GAP[idx]
    sk = vd.astype(np.float64) * 1e6 - vs.astype(np.float64)
    order = np.argsort(sk)
    sr = vr[order]; sd_ = vd[order]
    dc = np.concatenate([[1], (np.diff(sd_) != 0).astype(np.int32)])
    gs = np.where(dc)[0]
    gc_ = np.arange(len(sr)) - np.repeat(gs, np.diff(np.concatenate([gs, [len(sr)]])))
    sel = gc_ < TOP_N
    sel_ret = sr[sel]; sel_day = sd_[sel]

    # Daily P&L (capped allocation)
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
        if np.isnan(sel_ret[j]): continue
        daily_rs[di] += sel_ret[j] / 100 * d_pos[di]
    dpnl = daily_rs / CAPITAL * 100

    def period_stats(day_mask):
        dr = dpnl[day_mask & active]
        if len(dr) < 10: return {}
        dr_d = dr / 100
        years = len(dr) / 252
        cum = CAPITAL * np.cumprod(1 + dr_d)
        pk = np.maximum.accumulate(cum); dd = (pk - cum) / pk
        mdd = dd.max() * 100 if len(dd) else 0
        rf_d = RF_ANNUAL / 252
        exc = dr_d - rf_d
        sharpe = (exc.mean() / exc.std(ddof=1)) * np.sqrt(252) \
                 if exc.std(ddof=1) > 0 else 0
        g = dr_d[dr_d > 0].sum(); la = abs(dr_d[dr_d < 0].sum())
        pf = g / la if la > 0 else 999
        cagr = (cum[-1] / CAPITAL) ** (1 / years) - 1 if years > 0 else 0
        return dict(sharpe=sharpe, cagr=cagr * 100, mdd=mdd, pf=pf,
                    days=int(day_mask.sum()), ret_tot=(cum[-1]/CAPITAL - 1) * 100)

    full_mask   = np.ones(nd, bool)
    train_mask  = DAY_PERIOD == 'TRAIN'
    test_mask   = DAY_PERIOD == 'TEST'
    unseen_mask = DAY_PERIOD == 'UNSEEN'

    wr = (sel_ret > 0).sum() / len(sel_ret) if len(sel_ret) else 0
    out = dict(tp=tp, sl=sl, exit=exit_bkt, gap=gap_thr, price=price_max,
               n_trades=int(len(sel_ret)), win_rate=wr * 100)
    for name, m in [('full', full_mask), ('train', train_mask),
                    ('test', test_mask), ('unseen', unseen_mask)]:
        s = period_stats(m)
        for k, v in s.items():
            out[f"{name}_{k}"] = v
    return out

# ─── SWEEP ───────────────────────────────────────────────────────────────────
combos = list(product(TP_LIST, SL_LIST, EXIT_LIST, GAP_LIST, PRICE_LIST))
log(f"Running {len(combos)} configs...")

rows = []
for i, (tp, sl, exit_b, gap_thr, pmx) in enumerate(combos, 1):
    r = simulate(tp, sl, exit_b, gap_thr, pmx)
    if r is None:
        log(f"  [{i}/{len(combos)}] TP={tp} SL={sl} EX={exit_b} GAP={gap_thr} PRICE={pmx} → skip (too few)")
        continue
    rows.append(r)
    if i % 10 == 0:
        log(f"  [{i}/{len(combos)}] done")

df_r = pd.DataFrame(rows)
df_r = df_r.sort_values("test_sharpe", ascending=False)

out_csv = OUT_DIR / "param_grid.csv"
df_r.to_csv(out_csv, index=False)
log(f"Saved: {out_csv}")

# ─── REPORT ──────────────────────────────────────────────────────────────────
print(f"\n{'='*130}")
print(f"  TOP 15 BY TEST SHARPE (champion: TP=3.0 SL=0.5 EX=45 GAP=1.5 PRICE=1000)")
print(f"{'='*130}")
cols = ["tp", "sl", "exit", "gap", "price", "n_trades", "win_rate",
        "train_sharpe", "test_sharpe", "unseen_sharpe", "full_sharpe",
        "test_cagr", "test_mdd", "test_pf"]
print(df_r[cols].head(15).to_string(index=False,
      float_format=lambda x: f"{x:.2f}"))

print(f"\n{'─'*130}")
print(f"  CHAMPION ROW")
print(f"{'─'*130}")
champ = df_r[(df_r.tp == 3.0) & (df_r.sl == 0.5) & (df_r.exit == 45) &
             (df_r.gap == 1.5) & (df_r.price == 1000)]
if len(champ):
    print(champ[cols].to_string(index=False,
          float_format=lambda x: f"{x:.2f}"))
    rank = (df_r["test_sharpe"].values >
            champ["test_sharpe"].values[0]).sum() + 1
    print(f"\n  Champion rank by test_sharpe: {rank}/{len(df_r)}")

print(f"\n{'─'*130}")
print(f"  STABILITY CHECK — mean test_sharpe of champion's 6 nearest neighbors")
print(f"{'─'*130}")
neigh = df_r[
    (df_r.tp.isin([2.5, 3.0, 3.5])) &
    (df_r.sl.isin([0.4, 0.5, 0.6])) &
    (df_r.exit == 45) & (df_r.gap == 1.5) & (df_r.price == 1000)]
print(neigh[cols].to_string(index=False,
      float_format=lambda x: f"{x:.2f}"))
print(f"\n  Neighbors mean test_sharpe: {neigh['test_sharpe'].mean():.2f}")
print(f"  Neighbors min  test_sharpe: {neigh['test_sharpe'].min():.2f}")
print(f"\n  ✓ If min >= 0.7 × champion → plateau (robust)")
print(f"  ✗ If min <<  champion       → spike (overfit)")

log(f"Done in {time.perf_counter()-t0:.1f}s")
