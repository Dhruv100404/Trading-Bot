#!/usr/bin/env python3
"""Optimize obs=5min window — focus on green days, exit times, position sizes
Fully NumPy vectorized with pre-computed date indices"""
import numpy as np
import pandas as pd
import requests, time, sys
import functools
print = functools.partial(print, flush=True)

CH = 'http://localhost:8123'
FROM, TO = '2025-12-01', '2026-03-28'
CAP = 100_000

def qdf(sql):
    r = requests.post(CH, data=sql, timeout=300)
    lines = r.text.strip().split('\n')
    if len(lines) < 2: return pd.DataFrame()
    cols = lines[0].split('\t')
    data = [l.split('\t') for l in lines[1:]]
    df = pd.DataFrame(data, columns=cols)
    for c in df.columns:
        try: df[c] = pd.to_numeric(df[c])
        except: pass
    return df

def load_data():
    t0 = time.time()
    obs_end = 5
    print("Loading obs=5 data...")

    obs = qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
        argMin(ltp,bucket) as oltp, argMax(ltp,bucket) as eltp,
        max(candle_high) as hi, min(candle_low) as lo,
        sum(volume_delta) as vol, max(volume_rate) as mvr, avg(candle_body_ratio) as body, avg(vwap) as vwap
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<={obs_end}
    GROUP BY trading_date, symbol HAVING count()>=3 AND oltp>0 AND eltp>0 FORMAT TabSeparatedWithNames""")

    obs['mv'] = (obs['eltp']-obs['oltp'])/obs['oltp']*100
    obs['vd'] = np.where(obs['vwap']>0,(obs['eltp']-obs['vwap'])/obs['vwap']*100,0)
    obs['cn'] = np.where(obs['hi']>obs['lo'],np.abs(obs['eltp']-obs['oltp'])/(obs['hi']-obs['lo']),0)
    obs['ep'] = obs['eltp']
    obs = obs[np.abs(obs['mv'])>=0.05].reset_index(drop=True)
    print(f"  obs: {len(obs)} rows")

    # Load outcomes at MANY exit points (bucket 16 to 80, step 5)
    exit_buckets = list(range(16, 81, 5)) + [25, 30, 35, 40, 46, 50, 55, 60, 65, 70, 76]
    exit_buckets = sorted(set(exit_buckets))
    print(f"  Loading {len(exit_buckets)} exit points: {exit_buckets}")

    for eb in exit_buckets:
        p = qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
            max(candle_high) as ph, min(candle_low) as pl, argMax(ltp,bucket) as ll
        FROM trading.snapshots WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}')
          AND bucket>{obs_end} AND bucket<={eb}
        GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")
        obs = obs.merge(p, on=['dt','sym'], how='left', suffixes=('',f'_{eb}'))
        buy = obs['mv'] > 0
        ph = obs.get(f'ph_{eb}', obs.get('ph'))
        pl = obs.get(f'pl_{eb}', obs.get('pl'))
        ll = obs.get(f'll_{eb}', obs.get('ll'))
        if ph is None: ph = obs['ph']; pl = obs['pl']; ll = obs['ll']
        ep = obs['ep']
        obs[f'mfe_{eb}'] = np.where(buy, (ph-ep)/ep*100, (ep-pl)/ep*100)
        obs[f'mae_{eb}'] = np.where(buy, (ep-pl)/ep*100, (ph-ep)/ep*100)
        obs[f'ret_{eb}'] = np.where(buy, (ll-ep)/ep*100, (ep-ll)/ep*100)
        # Drop raw columns
        for c in [f'ph_{eb}',f'pl_{eb}',f'll_{eb}','ph','pl','ll']:
            if c in obs.columns: obs.drop(columns=[c], inplace=True, errors='ignore')

    # Gaps
    g = qdf(f"""SELECT toString(t.trading_date) as dt, t.symbol as sym,
        toFloat32(if(p.dc>0,(t.do-p.dc)/p.dc*100,0)) as gap
    FROM (SELECT trading_date,symbol,argMin(ltp,bucket) as do FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) t
    ASOF LEFT JOIN (SELECT trading_date,symbol,argMax(ltp,bucket) as dc FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) p
    ON t.symbol=p.symbol AND t.trading_date>p.trading_date WHERE t.trading_date>=toDate('{FROM}')
    FORMAT TabSeparatedWithNames""")
    obs = obs.merge(g, on=['dt','sym'], how='left'); obs['gap'] = obs['gap'].fillna(0)

    # Prev day
    pv = qdf(f"""SELECT toString(trading_date+1) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as p1
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<=80
    GROUP BY trading_date,symbol FORMAT TabSeparatedWithNames""")
    obs = obs.merge(pv, on=['dt','sym'], how='left'); obs['p1'] = obs['p1'].fillna(0)

    print(f"  Final: {len(obs)} rows, {time.time()-t0:.0f}s")
    return obs, exit_buckets

def fast_simulate(dts, ep, qty, ret, day_starts, day_ends, max_pos):
    """Ultra-fast simulation using pre-computed day boundaries"""
    n_days = len(day_starts)
    rocs = np.zeros(n_days)
    green = 0
    tsig = 0; twin = 0; tpnl = 0.0

    for i in range(n_days):
        s, e = day_starts[i], min(day_ends[i], day_starts[i] + max_pos)
        if s >= e: continue
        dp = np.sum(ep[s:e] * (ret[s:e]/100) * qty[s:e])
        dc = np.sum(ep[s:e] * qty[s:e])
        dm = dc / 5
        rocs[i] = dp / dm * 100 if dm > 0 else 0
        if dp > 0: green += 1
        tsig += e - s
        twin += np.sum(ret[s:e] > 0.05)
        tpnl += dp

    return tsig, twin, tpnl, green, n_days, rocs

def run_optimization(df, exit_buckets):
    t0 = time.time()
    mv = df['mv'].values; gap = df['gap'].values; p1 = df['p1'].values
    mvr = df['mvr'].values; body = df['body'].values
    cn = df['cn'].values; vd = df['vd'].values; ep = df['ep'].values
    n = len(df)

    # Direction: focus on SELL (proven) but also test BOTH
    dir_masks = {'S': mv < 0, 'B': mv > 0, 'A': np.ones(n, bool)}

    # Gap filters
    gap_masks = {
        '_': np.ones(n, bool),
        'gU05': gap > 0.5, 'gU1': gap > 1, 'gU2': gap > 2,
        'gD05': gap < -0.5, 'gD1': gap < -1,
        'bG2': np.abs(gap) > 2, 'bG3': np.abs(gap) > 3,
    }

    # Quality filters
    qual_masks = {
        '_': np.ones(n, bool),
        'v2': mvr > 200, 'v5': mvr > 500,
        'b6': body > 0.6, 's6': cn > 0.6,
        'm3': np.abs(mv) > 0.3, 'm5': np.abs(mv) > 0.5,
        'vA': ((mv>0)&(vd>0.1)) | ((mv<0)&(vd<-0.1)),
        'pD': p1 < 0, 'pU': p1 > 0,
    }

    # Rankings
    rank_vals = {
        'gp': -np.abs(gap),
        'vr': -mvr,
        'vx': -(mvr * np.abs(mv)),
        'mv': -np.abs(mv),
    }

    # Position sizes — user wants 7, 10, 12, 15 too
    positions = [3, 5, 7, 10, 12, 15, 20]

    # TP/SL combos
    tpsl = [(0,0), (0.5,0.3), (0.7,0.5), (1.0,0.5), (1.0,0.7), (1.5,0.7), (1.5,1.0), (2.0,1.0)]

    total_sel = len(dir_masks)*len(gap_masks)*len(qual_masks)*len(rank_vals)*len(positions)
    total = total_sel * len(tpsl) * len(exit_buckets)
    print(f"  {total_sel:,} selections x {len(tpsl)} TP/SL x {len(exit_buckets)} exits = {total:,} combos")

    results = []
    tested = 0
    qty_arr = np.maximum(np.floor(CAP / ep).astype(int), 0)
    dts = df['dt'].values

    for dn, dm in dir_masks.items():
        for gn, gm in gap_masks.items():
            for qn, qm in qual_masks.items():
                mask = dm & gm & qm
                nc = mask.sum()
                if nc < 15: continue

                sub_idx = np.where(mask)[0]

                for rn, rv in rank_vals.items():
                    # Sort by rank
                    sub_rank = rv[sub_idx]
                    order = np.argsort(sub_rank)
                    sorted_idx = sub_idx[order]

                    # Pre-compute day boundaries for sorted data
                    sorted_dts = dts[sorted_idx]
                    sorted_ep = ep[sorted_idx]
                    sorted_qty = qty_arr[sorted_idx]
                    udt, day_start_idx = np.unique(sorted_dts, return_index=True)
                    day_end_idx = np.append(day_start_idx[1:], len(sorted_idx))

                    for ps in positions:
                        tested += 1
                        if tested % 2000 == 0:
                            print(f"  {tested:,}/{total_sel:,} sel ({len(results):,} found) {time.time()-t0:.0f}s", end='\r')

                        for eb in exit_buckets:
                            mfe_col = f'mfe_{eb}'
                            mae_col = f'mae_{eb}'
                            ret_col = f'ret_{eb}'
                            if mfe_col not in df.columns: continue

                            mfe = df[mfe_col].values[sorted_idx]
                            mae = df[mae_col].values[sorted_idx]
                            tret = df[ret_col].values[sorted_idx]

                            for tp, sl in tpsl:
                                # Vectorized return calc
                                if tp > 0 and sl > 0:
                                    both = (mfe>=tp)&(mae>=sl)
                                    t1 = mfe/np.maximum(mae,0.01) > tp/sl
                                    ret = np.where(both, np.where(t1,tp,-sl), np.where(mfe>=tp,tp, np.where(mae>=sl,-sl,tret)))
                                elif tp > 0: ret = np.where(mfe>=tp, tp, tret)
                                elif sl > 0: ret = np.where(mae>=sl, -sl, tret)
                                else: ret = tret

                                # Fast simulation
                                tsig, twin, tpnl, green, nd, rocs = fast_simulate(
                                    sorted_dts, sorted_ep, sorted_qty, ret,
                                    day_start_idx, day_end_idx, ps
                                )
                                if tsig < 15: continue
                                avg_roc = float(np.mean(rocs))
                                if avg_roc <= 0: continue

                                wr = twin/tsig*100 if tsig else 0
                                gpct = green/nd*100 if nd else 0
                                results.append({
                                    'label': f"{dn} {gn}+{qn} rk={rn} p={ps} x={eb} TP={tp} SL={sl}",
                                    'sigs': int(tsig), 'wr': round(wr,1), 'roc': round(avg_roc,2),
                                    'pnl': round(tpnl), 'green': green, 'days': nd, 'gpct': round(gpct),
                                })

    print(f"  {tested:,} selections done, {len(results):,} profitable in {time.time()-t0:.0f}s")
    return results

def main():
    t0 = time.time()
    print("="*100)
    print("  OBS=5 OPTIMIZER — exit times, position sizes, green days focus")
    print("="*100)

    df, exit_buckets = load_data()
    results = run_optimization(df, exit_buckets)

    # Sort by green day % first, then ROC
    results.sort(key=lambda x: (-x['gpct'], -x['roc']))
    print(f"\n{'='*120}")
    print(f"  TOP 30 BY GREEN DAYS %")
    print(f"{'='*120}")
    print(f"  {'#':>3} {'Config':<80} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>8}")
    for i,r in enumerate(results[:30]):
        print(f"  {i+1:3} {r['label'][:80]:<80} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    # Sort by ROC
    results.sort(key=lambda x: -x['roc'])
    print(f"\n{'='*120}")
    print(f"  TOP 30 BY ROC")
    print(f"{'='*120}")
    print(f"  {'#':>3} {'Config':<80} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>8}")
    for i,r in enumerate(results[:30]):
        print(f"  {i+1:3} {r['label'][:80]:<80} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    # Best per exit bucket
    print(f"\n{'='*80}")
    print(f"  BEST CONFIG PER EXIT TIME")
    print(f"{'='*80}")
    for eb in exit_buckets:
        bucket_res = [r for r in results if f'x={eb} ' in r['label']]
        if bucket_res:
            b = bucket_res[0]
            exit_time = f"{9*60+15+eb-1}"; h=int(exit_time)//60; m=int(exit_time)%60
            ist = f"{h:02d}:{m:02d}"
            print(f"  Exit {eb:3} ({ist}): {b['label'][:65]:<65} ROC:{b['roc']:+5.2f}% W:{b['wr']:.0f}% G:{b['green']}/{b['days']} ({b['gpct']}%)")

    # Best per position size
    print(f"\n{'='*80}")
    print(f"  BEST CONFIG PER POSITION SIZE")
    print(f"{'='*80}")
    for ps in [3,5,7,10,12,15,20]:
        pos_res = [r for r in results if f'p={ps} ' in r['label']]
        if pos_res:
            b = pos_res[0]
            print(f"  Pos={ps:2}: {b['label'][:65]:<65} ROC:{b['roc']:+5.2f}% W:{b['wr']:.0f}% G:{b['green']}/{b['days']} ({b['gpct']}%) PnL:{b['pnl']:+.0f}")

    # Stats
    p = sum(1 for r in results if r['roc']>0)
    g1 = sum(1 for r in results if r['roc']>=1)
    g2 = sum(1 for r in results if r['roc']>=2)
    g80 = sum(1 for r in results if r['gpct']>=80)
    print(f"\n  Profitable: {p:,} | >=1%ROC: {g1:,} | >=2%ROC: {g2:,} | >=80%green: {g80:,}")
    print(f"  Total time: {time.time()-t0:.0f}s")

if __name__ == '__main__': main()
