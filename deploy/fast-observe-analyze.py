#!/usr/bin/env python3
"""
FAST Observe-Then-Trade Analysis — NumPy vectorized
Tests multiple observation windows × hundreds of filter/rank/TP/SL combos
50-100x faster than the JS version
"""
import numpy as np
import pandas as pd
import requests
import time
import sys
from itertools import product

CH = 'http://localhost:8123'
FROM, TO = '2025-12-01', '2026-03-28'
CAPITAL_PER_TRADE = 100_000

def ch_query(sql):
    r = requests.post(CH, data=sql)
    return r.text

def ch_df(sql):
    txt = ch_query(sql + " FORMAT TabSeparatedWithNames")
    lines = txt.strip().split('\n')
    if len(lines) < 2: return pd.DataFrame()
    cols = lines[0].split('\t')
    rows = [l.split('\t') for l in lines[1:]]
    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        try: df[c] = pd.to_numeric(df[c])
        except (ValueError, TypeError): pass
    return df

# ===============================================================================
def load_data(obs_end):
    """Load feature matrix for given observation window"""
    sql = f"""
    WITH
      obs AS (
        SELECT trading_date, symbol,
          argMin(ltp, bucket) as open_ltp,
          argMax(ltp, bucket) as end_ltp,
          min(candle_low) as obs_low, max(candle_high) as obs_high,
          sum(volume_delta) as obs_vol,
          avg(volume_rate) as avg_vr, max(volume_rate) as max_vr,
          sumIf(volume_delta, bucket <= {obs_end//2+1}) as vol_h1,
          sumIf(volume_delta, bucket > {obs_end//2+1}) as vol_h2,
          avg(candle_body_ratio) as avg_body,
          avg(vwap) as obs_vwap
        FROM trading.snapshots
        WHERE trading_date >= toDate('{FROM}') AND trading_date <= toDate('{TO}')
          AND bucket >= 1 AND bucket <= {obs_end}
        GROUP BY trading_date, symbol
        HAVING count() >= {max(2, obs_end-1)} AND open_ltp > 0 AND end_ltp > 0
      ),
      post AS (
        SELECT trading_date, symbol, bucket,
          candle_high, candle_low, ltp
        FROM trading.snapshots
        WHERE trading_date >= toDate('{FROM}') AND trading_date <= toDate('{TO}')
          AND bucket > {obs_end} AND bucket <= {obs_end + 70}
      ),
      post_agg AS (
        SELECT trading_date, symbol,
          -- Short exit (20 buckets after obs)
          maxIf(candle_high, bucket <= {obs_end+20}) as ph_s,
          minIf(candle_low, bucket <= {obs_end+20}) as pl_s,
          argMaxIf(ltp, bucket, bucket <= {obs_end+20}) as last_s,
          -- Medium exit (40 buckets)
          maxIf(candle_high, bucket <= {obs_end+40}) as ph_m,
          minIf(candle_low, bucket <= {obs_end+40}) as pl_m,
          argMaxIf(ltp, bucket, bucket <= {obs_end+40}) as last_m,
          -- Long exit (65 buckets)
          max(candle_high) as ph_l, min(candle_low) as pl_l,
          argMax(ltp, bucket) as last_l
        FROM post GROUP BY trading_date, symbol
      ),
      gaps AS (
        SELECT toString(t.trading_date) as td, t.symbol as sym,
          toFloat32(if(p.dc>0,(t.do-p.dc)/p.dc*100,0)) as gap_pct
        FROM (SELECT trading_date, symbol, argMin(ltp, bucket) as do
              FROM trading.snapshots WHERE trading_date >= toDate('{FROM}')-10 AND trading_date <= toDate('{TO}')
              GROUP BY trading_date, symbol) t
        ASOF LEFT JOIN (SELECT trading_date, symbol, argMax(ltp, bucket) as dc
              FROM trading.snapshots WHERE trading_date >= toDate('{FROM}')-10 AND trading_date <= toDate('{TO}')
              GROUP BY trading_date, symbol) p ON t.symbol=p.symbol AND t.trading_date>p.trading_date
      ),
      pd1 AS (
        SELECT trading_date, symbol,
          if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as prev_dir,
          (argMax(ltp,bucket)-argMin(ltp,bucket))/argMin(ltp,bucket)*100 as prev_range
        FROM trading.snapshots WHERE trading_date >= toDate('{FROM}')-10 AND trading_date <= toDate('{TO}') AND bucket>=1 AND bucket<=80
        GROUP BY trading_date, symbol
      ),
      pd2 AS (
        SELECT trading_date, symbol, if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as prev2_dir
        FROM trading.snapshots WHERE trading_date >= toDate('{FROM}')-15 AND trading_date <= toDate('{TO}') AND bucket>=1 AND bucket<=80
        GROUP BY trading_date, symbol
      )
    SELECT
      toString(o.trading_date) as dt, o.symbol,
      o.end_ltp as ep,
      (o.end_ltp-o.open_ltp)/o.open_ltp*100 as move,
      (o.obs_high-o.obs_low)/o.open_ltp*100 as rng,
      o.obs_vol as vol, o.avg_vr, o.max_vr, o.avg_body as body,
      if(o.obs_vwap>0,(o.end_ltp-o.obs_vwap)/o.obs_vwap*100,0) as vwap_d,
      if(o.vol_h1>0,o.vol_h2/o.vol_h1,0) as vol_acc,
      if(o.obs_high>o.obs_low,abs(o.end_ltp-o.open_ltp)/(o.obs_high-o.obs_low),0) as consistency,
      g.gap_pct as gap, p1.prev_dir as pd1, p1.prev_range as pr1, p2.prev2_dir as pd2,
      -- Directional outcomes (BUY perspective for move>0, SELL for move<0)
      if(o.end_ltp>o.open_ltp, (pa.ph_s-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.pl_s)/o.end_ltp*100) as mfe_s,
      if(o.end_ltp>o.open_ltp, (o.end_ltp-pa.pl_s)/o.end_ltp*100, (pa.ph_s-o.end_ltp)/o.end_ltp*100) as mae_s,
      if(o.end_ltp>o.open_ltp, (pa.last_s-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.last_s)/o.end_ltp*100) as ret_s,
      if(o.end_ltp>o.open_ltp, (pa.ph_m-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.pl_m)/o.end_ltp*100) as mfe_m,
      if(o.end_ltp>o.open_ltp, (o.end_ltp-pa.pl_m)/o.end_ltp*100, (pa.ph_m-o.end_ltp)/o.end_ltp*100) as mae_m,
      if(o.end_ltp>o.open_ltp, (pa.last_m-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.last_m)/o.end_ltp*100) as ret_m,
      if(o.end_ltp>o.open_ltp, (pa.ph_l-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.pl_l)/o.end_ltp*100) as mfe_l,
      if(o.end_ltp>o.open_ltp, (o.end_ltp-pa.pl_l)/o.end_ltp*100, (pa.ph_l-o.end_ltp)/o.end_ltp*100) as mae_l,
      if(o.end_ltp>o.open_ltp, (pa.last_l-o.end_ltp)/o.end_ltp*100, (o.end_ltp-pa.last_l)/o.end_ltp*100) as ret_l
    FROM obs o
    JOIN post_agg pa ON o.trading_date=pa.trading_date AND o.symbol=pa.symbol
    LEFT JOIN gaps g ON toString(o.trading_date)=g.td AND o.symbol=g.sym
    LEFT JOIN pd1 p1 ON o.trading_date-1=p1.trading_date AND o.symbol=p1.symbol
    LEFT JOIN pd2 p2 ON o.trading_date-2=p2.trading_date AND o.symbol=p2.symbol
    WHERE abs((o.end_ltp-o.open_ltp)/o.open_ltp*100) >= 0.05
    """
    return ch_df(sql)

# ===============================================================================
def vectorized_simulate(df, tp, sl, max_pos, exit_col):
    """Fully vectorized simulation — no Python loops over rows"""
    mfe_col = f'mfe_{exit_col}'
    mae_col = f'mae_{exit_col}'
    ret_col = f'ret_{exit_col}'

    mfe = df[mfe_col].values.astype(float)
    mae = df[mae_col].values.astype(float)
    tret = df[ret_col].values.astype(float)
    ep = df['ep'].values.astype(float)

    # Compute return per trade
    if tp > 0 and sl > 0:
        both = (mfe >= tp) & (mae >= sl)
        tp_first = mfe / np.maximum(mae, 0.01) > (tp / sl)
        ret = np.where(both, np.where(tp_first, tp, -sl),
              np.where(mfe >= tp, tp,
              np.where(mae >= sl, -sl, tret)))
    elif tp > 0:
        ret = np.where(mfe >= tp, tp, tret)
    elif sl > 0:
        ret = np.where(mae >= sl, -sl, tret)
    else:
        ret = tret

    qty = np.floor(CAPITAL_PER_TRADE / ep).astype(int)
    qty = np.maximum(qty, 0)
    pnl = ep * (ret / 100) * qty

    # Group by date — need to loop over dates but vectorized within each date
    dates = df['dt'].values
    unique_dates = np.unique(dates)
    daily_rocs = []
    green = 0
    total_sigs = 0
    total_wins = 0
    total_pnl = 0.0

    for dt in unique_dates:
        mask = dates == dt
        day_idx = np.where(mask)[0][:max_pos]  # already sorted by rank
        day_pnl = pnl[day_idx].sum()
        day_cap = (ep[day_idx] * qty[day_idx]).sum()
        day_margin = day_cap / 5
        roc = (day_pnl / day_margin * 100) if day_margin > 0 else 0
        daily_rocs.append(roc)
        if day_pnl > 0: green += 1
        total_sigs += len(day_idx)
        total_wins += (ret[day_idx] > 0.05).sum()
        total_pnl += day_pnl

    n_days = len(unique_dates)
    avg_roc = np.mean(daily_rocs) if daily_rocs else 0
    wr = total_wins / total_sigs * 100 if total_sigs > 0 else 0
    return {
        'sigs': int(total_sigs), 'wr': round(wr, 1), 'roc': round(avg_roc, 2),
        'pnl': round(total_pnl), 'green': green, 'days': n_days,
        'gpct': round(green / n_days * 100) if n_days > 0 else 0
    }

# ===============================================================================
def run_search(df, obs_end):
    """Run exhaustive search on a dataframe"""
    results = []
    tested = 0

    # Pre-compute filter masks (vectorized boolean arrays — FAST)
    move = df['move'].values.astype(float)
    gap = df['gap'].values.astype(float)
    pd1 = df['pd1'].values.astype(float)
    pd2 = df['pd2'].values.astype(float)
    mvr = df['max_vr'].values.astype(float)
    body = df['body'].values.astype(float)
    vacc = df['vol_acc'].values.astype(float)
    cons = df['consistency'].values.astype(float)
    vwap = df['vwap_d'].values.astype(float)
    pr1 = df['pr1'].values.astype(float)

    dir_masks = {
        'SELL': move < 0,
        'BUY': move > 0,
        'BOTH': np.ones(len(df), dtype=bool),
    }

    gap_masks = {
        'all': np.ones(len(df), dtype=bool),
        'gapDn<-0.5': gap < -0.5,
        'gapDn<-1': gap < -1,
        'gapUp>0.5': gap > 0.5,
        'gapUp>1': gap > 1,
        'bigGap>2': np.abs(gap) > 2,
    }

    mom_masks = {
        'all': np.ones(len(df), dtype=bool),
        'prevDn': pd1 < 0,
        'prevUp': pd1 > 0,
        '2dayDn': (pd1 < 0) & (pd2 < 0),
        '2dayUp': (pd1 > 0) & (pd2 > 0),
        'prevStrong': pr1 > 1.5,
    }

    qual_masks = {
        'all': np.ones(len(df), dtype=bool),
        'vol>200': mvr > 200,
        'vol>500': mvr > 500,
        'body>0.6': body > 0.6,
        'steady>0.6': cons > 0.6,
        'steady>0.8': cons > 0.8,
        'vAccel>1.5': vacc > 1.5,
        'mv>0.3': np.abs(move) > 0.3,
        'mv>0.5': np.abs(move) > 0.5,
        'mv<0.5': np.abs(move) < 0.5,
        'vwapAlign': ((move > 0) & (vwap > 0.1)) | ((move < 0) & (vwap < -0.1)),
    }

    # Ranking: pre-sort indices for each ranker
    rank_keys = {
        'volRate': -mvr,
        'absMove': -np.abs(move),
        'gapSize': -np.abs(gap),
        'volXmove': -(mvr * np.abs(move)),
        'consistency': -cons,
        'entryVol': -df['vol'].values.astype(float),
    }

    tp_sl = [(0,0), (0.5,0.3), (0.7,0.3), (0.7,0.5), (1.0,0.5), (1.0,0.7),
             (1.5,0.5), (1.5,0.7), (1.5,1.0), (2.0,0.7), (2.0,1.0)]
    positions = [3, 5, 8]
    exits = ['s', 'm', 'l']

    total = len(dir_masks) * len(gap_masks) * len(mom_masks) * len(qual_masks) * len(rank_keys) * len(tp_sl) * len(positions) * len(exits)
    print(f"  Testing ~{total:,} combos for obs={obs_end}min...")

    for dir_name, dir_mask in dir_masks.items():
        for gap_name, gap_mask in gap_masks.items():
            for mom_name, mom_mask in mom_masks.items():
                for qual_name, qual_mask in qual_masks.items():
                    combined = dir_mask & gap_mask & mom_mask & qual_mask
                    n = combined.sum()
                    if n < 30: continue

                    sub_df = df[combined].copy()

                    for rank_name, rank_vals in rank_keys.items():
                        # Sort by rank
                        sub_rank = rank_vals[combined]
                        order = np.argsort(sub_rank)
                        sorted_df = sub_df.iloc[order]

                        for tp, sl in tp_sl:
                            for pos in positions:
                                for ex in exits:
                                    tested += 1
                                    if tested % 50000 == 0:
                                        print(f"  {tested:,} tested, {len(results):,} profitable...", end='\r', file=sys.stderr)

                                    r = vectorized_simulate(sorted_df, tp, sl, pos, ex)
                                    if r['sigs'] < 20: continue
                                    if r['roc'] > 0:
                                        r['label'] = f"{dir_name} {gap_name}+{mom_name}+{qual_name} rank={rank_name} TP={tp} SL={sl} pos={pos} ex={ex}"
                                        results.append(r)

    print(f"  {tested:,} tested, {len(results):,} profitable                    ")
    return results

# ===============================================================================
def main():
    t0 = time.time()
    print("=" * 100)
    print(f"  FAST OBSERVE-THEN-TRADE ANALYSIS (NumPy vectorized)")
    print(f"  {FROM} to {TO}")
    print("=" * 100)

    all_results = []
    obs_windows = [3, 5, 7, 10, 15, 20]

    for obs in obs_windows:
        print(f"\n{'#' * 100}")
        print(f"  OBS WINDOW: {obs} min (bucket 1-{obs}), entry at bucket {obs+1}")
        print(f"  Exit windows: short={obs+20}, med={obs+40}, long={obs+65}")
        print(f"{'#' * 100}")

        t1 = time.time()
        df = load_data(obs)
        buy_n = (df['move'].astype(float) > 0).sum()
        sell_n = (df['move'].astype(float) < 0).sum()
        print(f"  Loaded {len(df):,} candidates ({buy_n:,} BUY, {sell_n:,} SELL) in {time.time()-t1:.1f}s")

        if len(df) < 100:
            print("  Too few candidates, skipping")
            continue

        t2 = time.time()
        results = run_search(df, obs)
        results.sort(key=lambda x: -x['roc'])
        elapsed = time.time() - t2
        print(f"  Search done in {elapsed:.0f}s")

        # Tag and keep top 200
        for r in results[:200]:
            r['label'] = f"obs={obs} " + r['label']
        all_results.extend(results[:200])

        # Show top 15
        print(f"\n  TOP 15 for obs={obs}min:")
        print(f"  {'#':>3} {'Label':<95} {'Sigs':>4} {'Win%':>5} {'ROC':>7} {'PnL':>9} {'Grn':>7}")
        for i, r in enumerate(results[:15]):
            roc = f"+{r['roc']:.2f}%" if r['roc'] >= 0 else f"{r['roc']:.2f}%"
            print(f"  {i+1:3} {r['label'][:95]:<95} {r['sigs']:4} {r['wr']:4.0f}%  {roc:>7} {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    # Overall
    all_results.sort(key=lambda x: -x['roc'])
    print(f"\n{'=' * 130}")
    print(f"  OVERALL TOP 50 — ALL OBSERVATION WINDOWS")
    print(f"{'=' * 130}")
    print(f"  {'#':>3} {'Label':<105} {'Sigs':>4} {'Win%':>5} {'ROC':>7} {'PnL':>9} {'Grn':>7}")
    print(f"  {'-' * 125}")
    for i, r in enumerate(all_results[:50]):
        roc = f"+{r['roc']:.2f}%" if r['roc'] >= 0 else f"{r['roc']:.2f}%"
        print(f"  {i+1:3} {r['label'][:105]:<105} {r['sigs']:4} {r['wr']:4.0f}%  {roc:>7} {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    # Stats
    profitable = sum(1 for r in all_results if r['roc'] > 0)
    gt1 = sum(1 for r in all_results if r['roc'] >= 1)
    gt2 = sum(1 for r in all_results if r['roc'] >= 2)
    print(f"\n  Profitable: {profitable} | >=1% ROC: {gt1} | >=2% ROC: {gt2}")

    for d in ['SELL', 'BUY', 'BOTH']:
        best = [r for r in all_results if f' {d} ' in r['label']]
        if best:
            b = best[0]
            print(f"\n  Best {d}: {b['label']}")
            print(f"    ROC: {b['roc']:.2f}%, Win: {b['wr']:.0f}%, PnL: {b['pnl']:.0f}, Green: {b['green']}/{b['days']}")

    print(f"\n  Total time: {time.time()-t0:.0f}s")

if __name__ == '__main__':
    main()
