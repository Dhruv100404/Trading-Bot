"""
Pattern Discovery - No-Lookahead Trading Pattern Analysis
=========================================================
Data: ~187k stock-day records, 1-min candles (9:15 AM - 3:30 PM)
Goal: Find high-probability BUY and SELL patterns

STRICT NO-LOOKAHEAD RULE:
  If signal is at bucket N (0-indexed), features use ONLY buckets 0..N
  Entry price = bucket N+1 open (we can't trade at bucket N close without lookahead)

Bucket index (0-indexed):
  0 = 9:15 AM,  1 = 9:16,  2 = 9:17 ... 5 = 9:20 (bucket 6 in 1-indexed)
  Entry (default) = index 6 open = 9:21 AM open
  Current exit = index 65 = 10:20 AM close
"""

import numpy as np
import json
import time
import sys
from pathlib import Path

DATA_DIR    = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE    = DATA_DIR / 'pattern_report.txt'
MAX_B       = 100   # load first 100 buckets (covers up to ~10:54 AM)

# 0-indexed exit points tested
EXIT_POINTS = {
    'b20' : 19,  # 9:34 AM
    'b30' : 29,  # 9:44 AM
    'b45' : 44,  # 9:59 AM
    'b60' : 59,  # 10:14 AM
    'b66' : 65,  # 10:20 AM  ← current strategy exit
    'b75' : 74,  # 10:29 AM
    'b90' : 89,  # 10:44 AM
}

# Field indices inside bkt array
O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6

# ─────────────────────────────────────────────
# 1. LOAD DATA
# ─────────────────────────────────────────────
def load_data():
    t0 = time.time()
    files = [
        DATA_DIR / 'candles-consolidated.ndjson',
        DATA_DIR / 'candles-consolidated_new.ndjson',
    ]

    # First pass: count records
    total = 0
    for fp in files:
        with open(fp) as f:
            for _ in f:
                total += 1

    print(f"Total records: {total}")

    gap_pct  = np.empty(total, dtype=np.float32)
    day_open = np.empty(total, dtype=np.float32)
    # (N, MAX_B, 7): o,h,l,c,v,vw,br
    bkt      = np.zeros((total, MAX_B, 7), dtype=np.float32)
    symbols  = []
    dates    = []

    idx = 0
    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                gap_pct[idx]  = rec['gapPct']
                day_open[idx] = rec['dayOpen']
                symbols.append(rec['symbol'])
                dates.append(rec['date'])
                bkts = rec['buckets']
                nb   = min(len(bkts), MAX_B)
                for j in range(nb):
                    b = bkts[j]
                    bkt[idx, j, O]  = b['o']
                    bkt[idx, j, H]  = b['h']
                    bkt[idx, j, L]  = b['l']
                    bkt[idx, j, C]  = b['c']
                    bkt[idx, j, V]  = b['v']
                    bkt[idx, j, VW] = b.get('vw', b['c'])
                    bkt[idx, j, BR] = b.get('br', 0.5)
                idx += 1
        print(f"  Loaded {fp.name}")

    print(f"Data load complete in {time.time()-t0:.1f}s")
    return gap_pct, day_open, bkt, symbols, dates


# ─────────────────────────────────────────────
# 2. FEATURE EXTRACTION (vectorized, NO LOOKAHEAD)
# ─────────────────────────────────────────────
def compute_features(gap_pct, bkt):
    """
    All features derived from data known at/before the signal bucket (index 5 = 9:20 AM).
    Entry price = bkt[:, 6, O]  (9:21 AM open — first price we can ACTUALLY trade at)
    """
    N = len(gap_pct)

    # ── Entry price (9:21 open) ──
    entry_price = bkt[:, 6, O].copy()
    valid = (entry_price > 0) & (bkt[:, 0, O] > 0)

    # ── Bucket 0 (9:15) ──
    b0_o   = bkt[:, 0, O]
    b0_c   = bkt[:, 0, C]
    b0_h   = bkt[:, 0, H]
    b0_l   = bkt[:, 0, L]
    b0_v   = bkt[:, 0, V]
    b0_vw  = bkt[:, 0, VW]
    b0_br  = bkt[:, 0, BR]
    b0_ret = np.where(b0_o > 0, (b0_c - b0_o) / b0_o * 100, 0.0)
    b0_rng = np.where(b0_o > 0, (b0_h - b0_l)  / b0_o * 100, 0.0)

    # ── Bucket 1 (9:16) ──
    b1_o   = bkt[:, 1, O]
    b1_c   = bkt[:, 1, C]
    b1_br  = bkt[:, 1, BR]
    b1_ret = np.where(b1_o > 0, (b1_c - b1_o) / b1_o * 100, 0.0)

    # ── Bucket 5 (9:20) — last bucket before entry ──
    b5_c   = bkt[:, 5, C]
    b5_vw  = bkt[:, 5, VW]

    # ── Candle directions (green = close > open) ──
    green = bkt[:, :6, C] > bkt[:, :6, O]   # shape (N, 6)
    red   = ~green

    all2_red   = red[:,  0] & red[:,  1]
    all3_red   = red[:,  0] & red[:,  1] & red[:,  2]
    all5_red   = red[:,  0] & red[:,  1] & red[:,  2] & red[:,  3] & red[:,  4]
    all2_green = green[:,0] & green[:,1]
    all3_green = green[:,0] & green[:,1] & green[:,2]
    all5_green = green[:,0] & green[:,1] & green[:,2] & green[:,3] & green[:,4]

    # ── Opening Range (OR) — using ONLY first 3 and first 5 buckets ──
    or3_h = np.maximum.reduce([bkt[:, i, H] for i in range(3)])
    or3_l = np.minimum.reduce([bkt[:, i, L] for i in range(3)])
    or5_h = np.maximum.reduce([bkt[:, i, H] for i in range(5)])
    or5_l = np.minimum.reduce([bkt[:, i, L] for i in range(5)])

    # OR range as % of price
    or3_rng = np.where(or3_h > 0, (or3_h - or3_l) / or3_h * 100, 0.0)
    or5_rng = np.where(or5_h > 0, (or5_h - or5_l) / or5_h * 100, 0.0)

    # ── ORB signal at bucket 5 (9:20): did price break OR3 in buckets 3..5? ──
    # "price broke OR3_high sometime during b3, b4, or b5"
    max_h_b3_b5 = np.maximum.reduce([bkt[:, i, H] for i in range(3, 6)])
    min_l_b3_b5 = np.minimum.reduce([bkt[:, i, L] for i in range(3, 6)])
    orb3_buy_break  = max_h_b3_b5 > or3_h   # price broke above OR3 = bullish
    orb3_sell_break = min_l_b3_b5 < or3_l   # price broke below OR3 = bearish

    # For entry at bucket 7 (index 6), ORB signal at bucket 6:
    max_h_b4_b6 = np.maximum.reduce([bkt[:, i, H] for i in range(4, 7)])
    min_l_b4_b6 = np.minimum.reduce([bkt[:, i, L] for i in range(4, 7)])
    orb5_buy_break  = max_h_b4_b6 > or5_h
    orb5_sell_break = min_l_b4_b6 < or5_l

    # ── VWAP deviation at bucket 5 ──
    vwap_dev = np.where(b5_vw > 0, (b5_c - b5_vw) / b5_vw * 100, 0.0)

    # ── Average buy-ratio over first 6 buckets ──
    avg_br6 = np.mean(bkt[:, :6, BR], axis=1)

    # ── Volume concentration: bucket 0 share of total first-5-bucket volume ──
    vol5  = np.sum(bkt[:, :5, V], axis=1)
    vol0_share = np.where(vol5 > 0, b0_v / vol5, 0.0)

    # ── Bucket 0 volume ratio (b0_vr already in data; approximation: b0_v vs avg(b1..b5)) ──
    avg_vol_b1_b5 = np.mean(bkt[:, 1:6, V], axis=1)
    b0_vol_ratio  = np.where(avg_vol_b1_b5 > 0, b0_v / avg_vol_b1_b5, 0.0)

    # ── Price momentum: close at b5 vs open at b0 ──
    pct_move_open_to_b5 = np.where(b0_o > 0, (b5_c - b0_o) / b0_o * 100, 0.0)

    # ── Range contraction: OR3 tighter than b0 alone ──
    tight_or3 = or3_rng < b0_rng

    return {
        'valid'           : valid,
        'entry_price'     : entry_price,
        'b0_ret'          : b0_ret,
        'b0_rng'          : b0_rng,
        'b0_br'           : b0_br,
        'b1_ret'          : b1_ret,
        'b1_br'           : b1_br,
        'all2_red'        : all2_red,
        'all3_red'        : all3_red,
        'all5_red'        : all5_red,
        'all2_green'      : all2_green,
        'all3_green'      : all3_green,
        'all5_green'      : all5_green,
        'or3_h'           : or3_h,
        'or3_l'           : or3_l,
        'or3_rng'         : or3_rng,
        'or5_h'           : or5_h,
        'or5_l'           : or5_l,
        'or5_rng'         : or5_rng,
        'orb3_buy'        : orb3_buy_break,
        'orb3_sell'       : orb3_sell_break,
        'orb5_buy'        : orb5_buy_break,
        'orb5_sell'       : orb5_sell_break,
        'vwap_dev'        : vwap_dev,
        'avg_br6'         : avg_br6,
        'vol0_share'      : vol0_share,
        'b0_vol_ratio'    : b0_vol_ratio,
        'move_open_b5'    : pct_move_open_to_b5,
        'tight_or3'       : tight_or3,
    }


# ─────────────────────────────────────────────
# 3. FORWARD OUTCOME COMPUTATION
# ─────────────────────────────────────────────
def compute_outcomes(bkt, entry_price):
    """
    For each exit bucket, compute:
      sell_mfe : max favorable excursion for SHORT (how far price fell)
      sell_mae : max adverse excursion for SHORT
      sell_ret : exit return for SHORT (entry - exit_close) / entry
      buy_mfe  : max favorable excursion for LONG
      buy_mae  : max adverse excursion for LONG
      buy_ret  : exit return for LONG (exit_close - entry) / entry
    All percentages.  Entry is at index 6 open (bkt[:,6,O]).
    """
    B_START = 6  # entry is at this bucket's open; price action from here
    ep = entry_price.copy()
    ep_safe = np.where(ep > 0, ep, 1.0)

    outcomes = {}
    for name, b_exit in EXIT_POINTS.items():
        if b_exit >= MAX_B:
            continue
        h_sl = bkt[:, B_START : b_exit + 1, H]   # highs from entry to exit
        l_sl = bkt[:, B_START : b_exit + 1, L]   # lows
        c_ex = bkt[:, b_exit, C]                  # close at exit bucket

        max_h = np.max(h_sl, axis=1)
        min_l = np.min(l_sl, axis=1)

        # Replace zeros to avoid div errors
        ep_s = ep_safe

        sell_mfe = (ep - min_l) / ep_s * 100   # positive = price fell (good for short)
        sell_mae = (max_h - ep) / ep_s * 100    # positive = price rose (bad for short)
        sell_ret = (ep - c_ex)   / ep_s * 100

        buy_mfe  = (max_h - ep) / ep_s * 100
        buy_mae  = (ep - min_l) / ep_s * 100
        buy_ret  = (c_ex - ep)  / ep_s * 100

        outcomes[name] = dict(
            sell_mfe=sell_mfe, sell_mae=sell_mae, sell_ret=sell_ret,
            buy_mfe=buy_mfe,   buy_mae=buy_mae,   buy_ret=buy_ret,
        )
    return outcomes


# ─────────────────────────────────────────────
# 4. PATTERN STATISTICS
# ─────────────────────────────────────────────
COST = 0.15   # % round-trip transaction cost (brokerage + slippage)
MIN_N = 50    # minimum signals to report a pattern

def stats(mask, outcomes, direction, exit_name):
    """Compute statistics for a boolean mask and direction (sell/buy)."""
    if exit_name not in outcomes:
        return None
    n = int(np.sum(mask))
    if n < MIN_N:
        return None
    oc  = outcomes[exit_name]
    pfx = direction  # 'sell' or 'buy'
    mfe = oc[f'{pfx}_mfe'][mask]
    mae = oc[f'{pfx}_mae'][mask]
    ret = oc[f'{pfx}_ret'][mask]

    avg_mfe   = float(np.mean(mfe))
    avg_mae   = float(np.mean(mae))
    avg_ret   = float(np.mean(ret))
    edge      = avg_mfe / max(avg_mae, 0.001)
    expect    = avg_ret - COST
    win05     = float(np.mean(mfe >= 0.5) * 100)
    win1      = float(np.mean(mfe >= 1.0) * 100)
    win2      = float(np.mean(mfe >= 2.0) * 100)
    pos_ret   = float(np.mean(ret > 0) * 100)
    med_ret   = float(np.median(ret))

    return dict(n=n, avg_mfe=avg_mfe, avg_mae=avg_mae, avg_ret=avg_ret,
                edge=edge, expect=expect, win05=win05, win1=win1, win2=win2,
                pos_ret=pos_ret, med_ret=med_ret)


def best_exit(mask, outcomes, direction):
    """Find which exit bucket gives highest expectancy for this pattern."""
    best, best_ex = None, None
    for ex_name in EXIT_POINTS:
        s = stats(mask, outcomes, direction, ex_name)
        if s and (best is None or s['expect'] > best['expect']):
            best, best_ex = s, ex_name
    return best, best_ex


def fmt(name, direction, exit_name, s):
    if s is None:
        return f"  (insufficient data)"
    sym = '▼' if direction == 'sell' else '▲'
    return (f"  {sym} {name:<45s} exit={exit_name}  "
            f"n={s['n']:5d}  "
            f"win>0.5%={s['win05']:5.1f}%  "
            f"win>1%={s['win1']:5.1f}%  "
            f"avgRet={s['avg_ret']:+.3f}%  "
            f"medRet={s['med_ret']:+.3f}%  "
            f"edge={s['edge']:4.2f}x  "
            f"expect={s['expect']:+.3f}%")


# ─────────────────────────────────────────────
# 5. DEFINE AND TEST ALL PATTERNS
# ─────────────────────────────────────────────
def run_all_patterns(gap_pct, feat, outcomes, out):
    results = []

    def test(name, direction, mask):
        mask = mask & feat['valid']
        s, ex = best_exit(mask, outcomes, direction)
        if s:
            results.append((name, direction, ex, s, mask))
        return s, ex

    g = gap_pct
    f = feat

    # ── BASELINE (current strategy) ──
    out.write("\n" + "="*90 + "\n")
    out.write("BASELINE — Current Strategy (SELL gap-up > 0.1%, all stocks)\n")
    out.write("="*90 + "\n")
    base_mask = (g > 0.1) & f['valid']
    for ex_name in EXIT_POINTS:
        s = stats(base_mask, outcomes, 'sell', ex_name)
        if s:
            out.write(fmt("gap>0.1% [BASELINE]", 'sell', ex_name, s) + "\n")

    # ── SECTION: SELL patterns ──
    out.write("\n" + "="*90 + "\n")
    out.write("SELL PATTERNS (SHORT)\n")
    out.write("="*90 + "\n")

    sell_patterns = [
        # ── Gap size tiers ──
        ("gap>0.5%",                    (g > 0.5)),
        ("gap>1%",                      (g > 1.0)),
        ("gap>1.5%",                    (g > 1.5)),
        ("gap>2%",                      (g > 2.0)),
        ("gap>3%",                      (g > 3.0)),
        ("gap 0.5-1%",                  (g > 0.5) & (g <= 1.0)),
        ("gap 1-2%",                    (g > 1.0) & (g <= 2.0)),
        ("gap 2-5%",                    (g > 2.0) & (g <= 5.0)),
        ("gap>5%",                      (g > 5.0)),

        # ── First candle direction ──
        ("gap>0.5% + b0_RED",           (g > 0.5) & ~f['all2_green'][:, np.newaxis].reshape(-1) if False else (g > 0.5) & (f['b0_ret'] < 0)),
        ("gap>0.5% + b0_GREEN",         (g > 0.5) & (f['b0_ret'] > 0)),
        ("gap>1% + b0_RED",             (g > 1.0) & (f['b0_ret'] < 0)),
        ("gap>1% + b0_GREEN",           (g > 1.0) & (f['b0_ret'] > 0)),
        ("gap>2% + b0_RED",             (g > 2.0) & (f['b0_ret'] < 0)),
        ("gap>2% + b0_GREEN",           (g > 2.0) & (f['b0_ret'] > 0)),

        # ── First candle buy ratio ──
        ("gap>0.5% + b0_br<0.3",        (g > 0.5) & (f['b0_br'] < 0.3)),
        ("gap>0.5% + b0_br<0.4",        (g > 0.5) & (f['b0_br'] < 0.4)),
        ("gap>0.5% + b0_br>0.6",        (g > 0.5) & (f['b0_br'] > 0.6)),
        ("gap>1% + b0_br<0.3",          (g > 1.0) & (f['b0_br'] < 0.3)),
        ("gap>1% + b0_br>0.6",          (g > 1.0) & (f['b0_br'] > 0.6)),
        ("gap>2% + b0_br<0.3",          (g > 2.0) & (f['b0_br'] < 0.3)),
        ("gap>2% + b0_br>0.6",          (g > 2.0) & (f['b0_br'] > 0.6)),

        # ── Combo: gap + b0_red + low buy ratio ──
        ("gap>0.5% + b0_RED + b0_br<0.4",  (g > 0.5) & (f['b0_ret'] < 0) & (f['b0_br'] < 0.4)),
        ("gap>1% + b0_RED + b0_br<0.4",    (g > 1.0) & (f['b0_ret'] < 0) & (f['b0_br'] < 0.4)),
        ("gap>2% + b0_RED + b0_br<0.4",    (g > 2.0) & (f['b0_ret'] < 0) & (f['b0_br'] < 0.4)),
        ("gap>0.5% + b0_RED + b0_br<0.3",  (g > 0.5) & (f['b0_ret'] < 0) & (f['b0_br'] < 0.3)),

        # ── Consecutive red candles ──
        ("gap>0.5% + 2-red",            (g > 0.5) & f['all2_red']),
        ("gap>0.5% + 3-red",            (g > 0.5) & f['all3_red']),
        ("gap>1% + 2-red",              (g > 1.0) & f['all2_red']),
        ("gap>1% + 3-red",              (g > 1.0) & f['all3_red']),
        ("gap>2% + 2-red",              (g > 2.0) & f['all2_red']),
        ("gap>2% + 3-red",              (g > 2.0) & f['all3_red']),

        # ── VWAP deviation ──
        ("gap>0.5% + price>VWAP+0.3%",  (g > 0.5) & (f['vwap_dev'] > 0.3)),
        ("gap>0.5% + price>VWAP+0.5%",  (g > 0.5) & (f['vwap_dev'] > 0.5)),
        ("gap>1% + price>VWAP+0.3%",    (g > 1.0) & (f['vwap_dev'] > 0.3)),

        # ── Volume spike ──
        ("gap>0.5% + b0_vol_ratio>3x",  (g > 0.5) & (f['b0_vol_ratio'] > 3.0)),
        ("gap>1% + b0_vol_ratio>3x",    (g > 1.0) & (f['b0_vol_ratio'] > 3.0)),
        ("gap>1% + b0_vol_ratio>5x",    (g > 1.0) & (f['b0_vol_ratio'] > 5.0)),

        # ── Overall move from open to 9:20 ──
        ("gap>0.5% + move_0_to_b5 < -0.3%",   (g > 0.5) & (f['move_open_b5'] < -0.3)),
        ("gap>0.5% + move_0_to_b5 < -0.5%",   (g > 0.5) & (f['move_open_b5'] < -0.5)),
        ("gap>1% + move_0_to_b5 < -0.3%",     (g > 1.0) & (f['move_open_b5'] < -0.3)),
        ("gap>1% + move_0_to_b5 < -0.5%",     (g > 1.0) & (f['move_open_b5'] < -0.5)),

        # ── ORB breakdown (gap up + breaks below opening range = strong reversal) ──
        ("gap>0.5% + ORB3_SELL_break",  (g > 0.5) & f['orb3_sell']),
        ("gap>0.5% + ORB5_SELL_break",  (g > 0.5) & f['orb5_sell']),
        ("gap>1% + ORB3_SELL_break",    (g > 1.0) & f['orb3_sell']),
        ("gap>0.5% + ORB3_BUY_break",   (g > 0.5) & f['orb3_buy']),  # continuation

        # ── Tight OR (narrow range = coiling for move) ──
        ("gap>0.5% + tight_OR3<0.3%",   (g > 0.5) & (f['or3_rng'] < 0.3)),
        ("gap>1% + tight_OR3<0.3%",     (g > 1.0) & (f['or3_rng'] < 0.3)),
        ("gap>0.5% + wide_OR3>1%",      (g > 0.5) & (f['or3_rng'] > 1.0)),

        # ── avg buy ratio across 6 buckets ──
        ("gap>0.5% + avg_br6<0.4",      (g > 0.5) & (f['avg_br6'] < 0.4)),
        ("gap>1% + avg_br6<0.4",        (g > 1.0) & (f['avg_br6'] < 0.4)),
        ("gap>1% + avg_br6<0.35",       (g > 1.0) & (f['avg_br6'] < 0.35)),

        # ── Kitchen sink combos ──
        ("gap>1% + b0_RED + 2-red",             (g > 1.0) & (f['b0_ret'] < 0) & f['all2_red']),
        ("gap>1% + b0_RED + avg_br6<0.4",       (g > 1.0) & (f['b0_ret'] < 0) & (f['avg_br6'] < 0.4)),
        ("gap>1% + 3-red + avg_br6<0.4",        (g > 1.0) & f['all3_red'] & (f['avg_br6'] < 0.4)),
        ("gap>2% + b0_RED + avg_br6<0.4",       (g > 2.0) & (f['b0_ret'] < 0) & (f['avg_br6'] < 0.4)),
        ("gap>1% + ORB3_SELL + avg_br6<0.4",    (g > 1.0) & f['orb3_sell'] & (f['avg_br6'] < 0.4)),
        ("gap>1% + move<-0.5% + avg_br6<0.4",   (g > 1.0) & (f['move_open_b5'] < -0.5) & (f['avg_br6'] < 0.4)),
        ("gap>1% + b0_RED + b0_br<0.3 + 2-red", (g > 1.0) & (f['b0_ret'] < 0) & (f['b0_br'] < 0.3) & f['all2_red']),
    ]

    pattern_rows = []
    for name, mask in sell_patterns:
        s, ex = best_exit(mask & f['valid'], outcomes, 'sell')
        if s:
            pattern_rows.append((s['expect'], name, 'sell', ex, s))

    pattern_rows.sort(key=lambda x: -x[0])
    for _, name, direction, ex, s in pattern_rows:
        out.write(fmt(name, direction, ex, s) + "\n")

    # ── SECTION: BUY patterns ──
    out.write("\n" + "="*90 + "\n")
    out.write("BUY PATTERNS (LONG)\n")
    out.write("="*90 + "\n")

    buy_patterns = [
        # ── Gap down reversal ──
        ("gap<-0.5%",                    (g < -0.5)),
        ("gap<-1%",                      (g < -1.0)),
        ("gap<-1.5%",                    (g < -1.5)),
        ("gap<-2%",                      (g < -2.0)),
        ("gap<-3%",                      (g < -3.0)),
        ("gap -0.5 to -1%",              (g < -0.5) & (g >= -1.0)),
        ("gap -1 to -2%",                (g < -1.0) & (g >= -2.0)),
        ("gap<-2% + b0_GREEN",           (g < -2.0) & (f['b0_ret'] > 0)),
        ("gap<-2% + b0_RED",             (g < -2.0) & (f['b0_ret'] < 0)),
        ("gap<-1% + b0_GREEN",           (g < -1.0) & (f['b0_ret'] > 0)),
        ("gap<-1% + b0_RED",             (g < -1.0) & (f['b0_ret'] < 0)),

        # ── Buy pressure on gap-down ──
        ("gap<-1% + b0_br>0.6",          (g < -1.0) & (f['b0_br'] > 0.6)),
        ("gap<-1% + b0_br>0.7",          (g < -1.0) & (f['b0_br'] > 0.7)),
        ("gap<-2% + b0_br>0.6",          (g < -2.0) & (f['b0_br'] > 0.6)),
        ("gap<-1% + b0_GREEN + b0_br>0.6", (g < -1.0) & (f['b0_ret'] > 0) & (f['b0_br'] > 0.6)),

        # ── Consecutive green candles on gap-down ──
        ("gap<-1% + 2-green",            (g < -1.0) & f['all2_green']),
        ("gap<-1% + 3-green",            (g < -1.0) & f['all3_green']),
        ("gap<-2% + 2-green",            (g < -2.0) & f['all2_green']),
        ("gap<-2% + 3-green",            (g < -2.0) & f['all3_green']),

        # ── avg buy ratio ──
        ("gap<-1% + avg_br6>0.55",       (g < -1.0) & (f['avg_br6'] > 0.55)),
        ("gap<-1% + avg_br6>0.6",        (g < -1.0) & (f['avg_br6'] > 0.6)),
        ("gap<-2% + avg_br6>0.55",       (g < -2.0) & (f['avg_br6'] > 0.55)),

        # ── ORB breakout on gap-down ──
        ("gap<-1% + ORB3_BUY_break",     (g < -1.0) & f['orb3_buy']),
        ("gap<-1% + ORB5_BUY_break",     (g < -1.0) & f['orb5_buy']),
        ("gap<-2% + ORB3_BUY_break",     (g < -2.0) & f['orb3_buy']),

        # ── Price below VWAP (reversion buy) ──
        ("gap<-0.5% + price<VWAP-0.3%",  (g < -0.5) & (f['vwap_dev'] < -0.3)),
        ("gap<-1% + price<VWAP-0.3%",    (g < -1.0) & (f['vwap_dev'] < -0.3)),

        # ── Momentum continuation (gap-up + green first 3 candles → buy) ──
        ("gap>0.5% + 3-green",           (g > 0.5) & f['all3_green']),
        ("gap>1% + 3-green",             (g > 1.0) & f['all3_green']),
        ("gap>0.5% + 5-green",           (g > 0.5) & f['all5_green']),

        # ── ORB breakout regardless of gap ──
        ("any_gap + ORB3_BUY_break",     f['orb3_buy']),
        ("any_gap + ORB5_BUY_break",     f['orb5_buy']),
        ("flat_gap(-0.3to0.3%) + ORB_BUY", (g > -0.3) & (g < 0.3) & f['orb3_buy']),

        # ── Combos ──
        ("gap<-1% + b0_GREEN + avg_br6>0.55",          (g < -1.0) & (f['b0_ret'] > 0) & (f['avg_br6'] > 0.55)),
        ("gap<-2% + b0_GREEN + avg_br6>0.55",          (g < -2.0) & (f['b0_ret'] > 0) & (f['avg_br6'] > 0.55)),
        ("gap<-1% + 2-green + avg_br6>0.55",           (g < -1.0) & f['all2_green'] & (f['avg_br6'] > 0.55)),
        ("gap<-1% + ORB3_BUY + avg_br6>0.55",          (g < -1.0) & f['orb3_buy'] & (f['avg_br6'] > 0.55)),
        ("gap<-1% + b0_GREEN + b0_br>0.6 + 2-green",   (g < -1.0) & (f['b0_ret'] > 0) & (f['b0_br'] > 0.6) & f['all2_green']),
    ]

    buy_rows = []
    for name, mask in buy_patterns:
        s, ex = best_exit(mask & f['valid'], outcomes, 'buy')
        if s:
            buy_rows.append((s['expect'], name, 'buy', ex, s))

    buy_rows.sort(key=lambda x: -x[0])
    for _, name, direction, ex, s in buy_rows:
        out.write(fmt(name, direction, ex, s) + "\n")

    # ── SECTION: No-gap momentum ──
    out.write("\n" + "="*90 + "\n")
    out.write("NO-GAP / FLAT-OPEN PATTERNS\n")
    out.write("="*90 + "\n")

    flat_patterns = [
        ("SELL: flat + 3-red",       'sell', (g > -0.3) & (g < 0.3) & f['all3_red']),
        ("SELL: flat + ORB3_SELL",   'sell', (g > -0.3) & (g < 0.3) & f['orb3_sell']),
        ("BUY:  flat + 3-green",     'buy',  (g > -0.3) & (g < 0.3) & f['all3_green']),
        ("BUY:  flat + ORB3_BUY",    'buy',  (g > -0.3) & (g < 0.3) & f['orb3_buy']),
        ("SELL: any + 5-red",        'sell', f['all5_red']),
        ("BUY:  any + 5-green",      'buy',  f['all5_green']),
        ("SELL: any + ORB3_SELL",    'sell', f['orb3_sell']),
        ("BUY:  any + ORB3_BUY",     'buy',  f['orb3_buy']),
        ("SELL: any + ORB5_SELL",    'sell', f['orb5_sell']),
        ("BUY:  any + ORB5_BUY",     'buy',  f['orb5_buy']),
    ]

    flat_rows = []
    for name, direction, mask in flat_patterns:
        s, ex = best_exit(mask & f['valid'], outcomes, direction)
        if s:
            flat_rows.append((s['expect'], name, direction, ex, s))

    flat_rows.sort(key=lambda x: -x[0])
    for _, name, direction, ex, s in flat_rows:
        out.write(fmt(name, direction, ex, s) + "\n")

    # ── SECTION: Best patterns by exit bucket (current b66 comparison) ──
    out.write("\n" + "="*90 + "\n")
    out.write(f"ALL PATTERNS vs CURRENT EXIT (b66=10:20AM)  [sorted by expect@b66]\n")
    out.write("="*90 + "\n")

    all_patterns_b66 = []
    for name, mask in sell_patterns:
        s = stats(mask & f['valid'], outcomes, 'sell', 'b66')
        if s:
            all_patterns_b66.append((s['expect'], name, 'sell', 'b66', s))
    for name, mask in buy_patterns:
        s = stats(mask & f['valid'], outcomes, 'buy', 'b66')
        if s:
            all_patterns_b66.append((s['expect'], name, 'buy', 'b66', s))

    all_patterns_b66.sort(key=lambda x: -x[0])
    out.write(f"\nTop 30 patterns at b66 exit:\n")
    for _, name, direction, ex, s in all_patterns_b66[:30]:
        out.write(fmt(name, direction, ex, s) + "\n")

    # ── SECTION: Gap threshold sensitivity ──
    out.write("\n" + "="*90 + "\n")
    out.write("GAP THRESHOLD SENSITIVITY (SELL, exit b66)\n")
    out.write("="*90 + "\n")
    for thr in [0.1, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0]:
        mask = (g > thr) & f['valid']
        s = stats(mask, outcomes, 'sell', 'b66')
        if s:
            out.write(fmt(f"gap>{thr}%", 'sell', 'b66', s) + "\n")

    out.write("\n" + "="*90 + "\n")
    out.write("GAP THRESHOLD SENSITIVITY (BUY reversal, exit b66)\n")
    out.write("="*90 + "\n")
    for thr in [0.1, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0]:
        mask = (g < -thr) & f['valid']
        s = stats(mask, outcomes, 'buy', 'b66')
        if s:
            out.write(fmt(f"gap<-{thr}%", 'buy', 'b66', s) + "\n")

    # ── SECTION: Exit timing for best patterns ──
    out.write("\n" + "="*90 + "\n")
    out.write("EXIT TIMING COMPARISON — Top SELL patterns across all exit buckets\n")
    out.write("="*90 + "\n")

    key_sell = [
        ("gap>1%",              (g > 1.0)),
        ("gap>1% + b0_RED",     (g > 1.0) & (f['b0_ret'] < 0)),
        ("gap>1% + avg_br6<0.4",(g > 1.0) & (f['avg_br6'] < 0.4)),
        ("gap>1% + 2-red",      (g > 1.0) & f['all2_red']),
        ("gap>2% + b0_RED",     (g > 2.0) & (f['b0_ret'] < 0)),
    ]

    for name, mask in key_sell:
        out.write(f"\n  {name}:\n")
        for ex_name in EXIT_POINTS:
            s = stats(mask & f['valid'], outcomes, 'sell', ex_name)
            if s:
                out.write("  " + fmt(name, 'sell', ex_name, s) + "\n")

    out.write("\n" + "="*90 + "\n")
    out.write("EXIT TIMING COMPARISON — Top BUY patterns across all exit buckets\n")
    out.write("="*90 + "\n")

    key_buy = [
        ("gap<-1%",               (g < -1.0)),
        ("gap<-1% + b0_GREEN",    (g < -1.0) & (f['b0_ret'] > 0)),
        ("gap<-1% + avg_br6>0.55",(g < -1.0) & (f['avg_br6'] > 0.55)),
        ("gap<-1% + 2-green",     (g < -1.0) & f['all2_green']),
        ("gap<-2% + b0_GREEN",    (g < -2.0) & (f['b0_ret'] > 0)),
    ]

    for name, mask in key_buy:
        out.write(f"\n  {name}:\n")
        for ex_name in EXIT_POINTS:
            s = stats(mask & f['valid'], outcomes, 'buy', ex_name)
            if s:
                out.write("  " + fmt(name, 'buy', ex_name, s) + "\n")


# ─────────────────────────────────────────────
# 6. SUMMARY: TOP PATTERNS
# ─────────────────────────────────────────────
def write_summary(out, gap_pct, feat, outcomes):
    out.write("\n" + "="*90 + "\n")
    out.write("★  TOP 20 PATTERNS OVERALL (by best-exit expectancy)\n")
    out.write("="*90 + "\n")

    g = gap_pct
    f = feat

    all_candidates = []

    def candidate(name, direction, mask):
        mask = mask & f['valid']
        s, ex = best_exit(mask, outcomes, direction)
        if s and s['n'] >= MIN_N:
            all_candidates.append((s['expect'], name, direction, ex, s))

    # Sell
    for thr in [0.5, 1.0, 1.5, 2.0, 3.0]:
        candidate(f"gap>{thr}%", 'sell', g > thr)
        candidate(f"gap>{thr}% + b0_RED", 'sell', (g > thr) & (f['b0_ret'] < 0))
        candidate(f"gap>{thr}% + avg_br6<0.4", 'sell', (g > thr) & (f['avg_br6'] < 0.4))
        candidate(f"gap>{thr}% + 2-red", 'sell', (g > thr) & f['all2_red'])
        candidate(f"gap>{thr}% + 3-red", 'sell', (g > thr) & f['all3_red'])
        candidate(f"gap>{thr}% + b0_RED + avg_br6<0.4", 'sell',
                  (g > thr) & (f['b0_ret'] < 0) & (f['avg_br6'] < 0.4))
        candidate(f"gap>{thr}% + ORB3_SELL", 'sell', (g > thr) & f['orb3_sell'])

    # Buy
    for thr in [0.5, 1.0, 1.5, 2.0, 3.0]:
        candidate(f"gap<-{thr}%", 'buy', g < -thr)
        candidate(f"gap<-{thr}% + b0_GREEN", 'buy', (g < -thr) & (f['b0_ret'] > 0))
        candidate(f"gap<-{thr}% + avg_br6>0.55", 'buy', (g < -thr) & (f['avg_br6'] > 0.55))
        candidate(f"gap<-{thr}% + 2-green", 'buy', (g < -thr) & f['all2_green'])
        candidate(f"gap<-{thr}% + ORB3_BUY", 'buy', (g < -thr) & f['orb3_buy'])
        candidate(f"gap<-{thr}% + b0_GREEN + avg_br6>0.55", 'buy',
                  (g < -thr) & (f['b0_ret'] > 0) & (f['avg_br6'] > 0.55))

    # ORB regardless of gap
    candidate("any + ORB3_BUY",  'buy',  f['orb3_buy'])
    candidate("any + ORB3_SELL", 'sell', f['orb3_sell'])
    candidate("any + ORB5_BUY",  'buy',  f['orb5_buy'])
    candidate("any + ORB5_SELL", 'sell', f['orb5_sell'])
    candidate("any + 5-green",   'buy',  f['all5_green'])
    candidate("any + 5-red",     'sell', f['all5_red'])

    all_candidates.sort(key=lambda x: -x[0])
    for rank, (_, name, direction, ex, s) in enumerate(all_candidates[:20], 1):
        out.write(f"\n  #{rank:02d}  " + fmt(name, direction, ex, s))

    out.write("\n\n")


# ─────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────
def main():
    t_start = time.time()

    print("Loading data...")
    gap_pct, day_open, bkt, symbols, dates = load_data()

    print("Computing features...")
    feat = compute_features(gap_pct, bkt)

    print("Computing forward outcomes...")
    outcomes = compute_outcomes(bkt, feat['entry_price'])

    print("Running pattern analysis...")
    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write("PATTERN DISCOVERY REPORT\n")
        out.write(f"Data: {len(gap_pct)} records, {len(set(dates))} trading days\n")
        out.write(f"Date range: {min(dates)} to {max(dates)}\n")
        out.write(f"Entry: bucket 7 open (9:21 AM) — conservative, NO lookahead\n")
        out.write(f"Cost assumption: {COST}% round-trip\n")
        out.write(f"Min samples per pattern: {MIN_N}\n")

        write_summary(out, gap_pct, feat, outcomes)
        run_all_patterns(gap_pct, feat, outcomes, out)

    print(f"\nDone in {time.time()-t_start:.1f}s")
    print(f"Report: {OUT_FILE}")


if __name__ == '__main__':
    main()
