#!/usr/bin/env python3
"""OBS=5 Turbo — 100% vectorized, no Python date loops"""
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
    print("Loading obs=5...")

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

    exit_buckets = [20, 25, 30, 35, 40, 46, 50, 55, 60, 65, 70, 76]
    print(f"  Loading {len(exit_buckets)} exits...")
    for eb in exit_buckets:
        p = qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
            max(candle_high) as ph, min(candle_low) as pl, argMax(ltp,bucket) as ll
        FROM trading.snapshots WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}')
          AND bucket>{obs_end} AND bucket<={eb}
        GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")
        obs = obs.merge(p, on=['dt','sym'], how='left', suffixes=('',f'_{eb}'))
        buy = obs['mv'] > 0
        ph = obs.get(f'ph_{eb}', obs.get('ph')); pl = obs.get(f'pl_{eb}', obs.get('pl')); ll = obs.get(f'll_{eb}', obs.get('ll'))
        if ph is None: ph=obs['ph']; pl=obs['pl']; ll=obs['ll']
        obs[f'mfe_{eb}'] = np.where(buy, (ph-obs['ep'])/obs['ep']*100, (obs['ep']-pl)/obs['ep']*100)
        obs[f'mae_{eb}'] = np.where(buy, (obs['ep']-pl)/obs['ep']*100, (ph-obs['ep'])/obs['ep']*100)
        obs[f'ret_{eb}'] = np.where(buy, (ll-obs['ep'])/obs['ep']*100, (obs['ep']-ll)/obs['ep']*100)
        for c in [f'ph_{eb}',f'pl_{eb}',f'll_{eb}','ph','pl','ll']:
            if c in obs.columns: obs.drop(columns=[c], inplace=True, errors='ignore')

    g = qdf(f"""SELECT toString(t.trading_date) as dt, t.symbol as sym,
        toFloat32(if(p.dc>0,(t.do-p.dc)/p.dc*100,0)) as gap
    FROM (SELECT trading_date,symbol,argMin(ltp,bucket) as do FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) t
    ASOF LEFT JOIN (SELECT trading_date,symbol,argMax(ltp,bucket) as dc FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) p
    ON t.symbol=p.symbol AND t.trading_date>p.trading_date WHERE t.trading_date>=toDate('{FROM}')
    FORMAT TabSeparatedWithNames""")
    obs = obs.merge(g, on=['dt','sym'], how='left'); obs['gap']=obs['gap'].fillna(0)

    pv = qdf(f"""SELECT toString(trading_date+1) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as p1
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<=80
    GROUP BY trading_date,symbol FORMAT TabSeparatedWithNames""")
    obs = obs.merge(pv, on=['dt','sym'], how='left'); obs['p1']=obs['p1'].fillna(0)

    print(f"  Final: {len(obs)} rows, {time.time()-t0:.0f}s")
    return obs, exit_buckets

def build_day_matrix(sorted_dts, sorted_ep, sorted_qty, max_pos):
    """Build 2D padded matrix: (n_days, max_pos) for ep and qty — pure NumPy"""
    udt, starts = np.unique(sorted_dts, return_index=True)
    ends = np.append(starts[1:], len(sorted_dts))
    nd = len(udt)
    ep_mat = np.zeros((nd, max_pos))
    qty_mat = np.zeros((nd, max_pos), dtype=int)
    mask_mat = np.zeros((nd, max_pos), dtype=bool)

    for i in range(nd):
        s, e = starts[i], min(ends[i], starts[i]+max_pos)
        n = e - s
        if n <= 0: continue
        ep_mat[i, :n] = sorted_ep[s:e]
        qty_mat[i, :n] = sorted_qty[s:e]
        mask_mat[i, :n] = True

    return udt, starts, ends, ep_mat, qty_mat, mask_mat, nd

def matrix_simulate(ep_mat, qty_mat, mask_mat, ret_mat, nd):
    """100% vectorized: no Python loops"""
    pnl_mat = ep_mat * (ret_mat / 100) * qty_mat * mask_mat
    day_pnl = pnl_mat.sum(axis=1)  # (nd,)
    day_cap = (ep_mat * qty_mat * mask_mat).sum(axis=1)
    day_margin = day_cap / 5
    with np.errstate(divide='ignore', invalid='ignore'):
        day_roc = np.where(day_margin > 0, day_pnl / day_margin * 100, 0)
        day_roc = np.nan_to_num(day_roc, 0)

    green = int((day_pnl > 0).sum())
    avg_roc = float(day_roc.mean())
    total_pnl = float(day_pnl.sum())
    total_sigs = int(mask_mat.sum())
    total_wins = int(((ret_mat > 0.05) & mask_mat).sum())
    wr = total_wins / total_sigs * 100 if total_sigs else 0
    gpct = green / nd * 100 if nd else 0
    return total_sigs, wr, avg_roc, total_pnl, green, nd, gpct

def run(df, exit_buckets):
    t0 = time.time()
    mv=df['mv'].values; gap=df['gap'].values; p1=df['p1'].values
    mvr=df['mvr'].values; body=df['body'].values; cn=df['cn'].values; vd=df['vd'].values
    ep=df['ep'].values; n=len(df)
    qty_all = np.maximum(np.floor(CAP/ep).astype(int), 0)
    dts = df['dt'].values

    dirs = {'S':mv<0, 'B':mv>0, 'A':np.ones(n,bool)}
    gaps_f = {'_':np.ones(n,bool),'gU05':gap>0.5,'gU1':gap>1,'gU2':gap>2,'gD05':gap<-0.5,'gD1':gap<-1,'bG2':np.abs(gap)>2}
    quals = {'_':np.ones(n,bool),'v2':mvr>200,'v5':mvr>500,'b6':body>0.6,'s6':cn>0.6,
             'm3':np.abs(mv)>0.3,'m5':np.abs(mv)>0.5,'vA':((mv>0)&(vd>0.1))|((mv<0)&(vd<-0.1)),'pD':p1<0,'pU':p1>0}
    ranks = {'gp':-np.abs(gap), 'vr':-mvr, 'vx':-(mvr*np.abs(mv)), 'mv':-np.abs(mv)}
    positions = [3, 5, 7, 10, 12, 15]
    tpsl = [(0,0),(0.7,1),(0.7,1.5)]

    total_sel = len(dirs)*len(gaps_f)*len(quals)*len(ranks)*len(positions)
    total = total_sel * len(tpsl) * len(exit_buckets)
    print(f"  {total_sel:,} selections x {len(tpsl)} TP/SL x {len(exit_buckets)} exits = {total:,} combos")

    results = []
    sel_count = 0

    for dn, dm in dirs.items():
        for gn, gm in gaps_f.items():
            for qn, qm in quals.items():
                mask = dm & gm & qm
                if mask.sum() < 15: continue
                sub_idx = np.where(mask)[0]

                for rn, rv in ranks.items():
                    order = np.argsort(rv[sub_idx])
                    sorted_idx = sub_idx[order]
                    sorted_dts = dts[sorted_idx]
                    sorted_ep = ep[sorted_idx]
                    sorted_qty = qty_all[sorted_idx]

                    for ps in positions:
                        sel_count += 1
                        if sel_count % 1000 == 0:
                            elapsed = time.time()-t0
                            rate = sel_count/elapsed if elapsed > 0 else 0
                            eta = (total_sel-sel_count)/rate if rate > 0 else 0
                            print(f"  {sel_count:,}/{total_sel:,} sel | {len(results):,} found | {elapsed:.0f}s | ETA {eta:.0f}s", end='\r')

                        # Build day matrix ONCE for this selection
                        udt, starts, ends, ep_mat, qty_mat, mask_mat, nd = build_day_matrix(
                            sorted_dts, sorted_ep, sorted_qty, ps)

                        for eb in exit_buckets:
                            mc = f'mfe_{eb}'; ac = f'mae_{eb}'; rc = f'ret_{eb}'
                            if mc not in df.columns: continue
                            mfe_all = df[mc].values[sorted_idx]
                            mae_all = df[ac].values[sorted_idx]
                            tret_all = df[rc].values[sorted_idx]

                            for tp, sl in tpsl:
                                # Vectorized return
                                if tp>0 and sl>0:
                                    both=(mfe_all>=tp)&(mae_all>=sl)
                                    t1=mfe_all/np.maximum(mae_all,0.01)>tp/sl
                                    ret_all=np.where(both,np.where(t1,tp,-sl),np.where(mfe_all>=tp,tp,np.where(mae_all>=sl,-sl,tret_all)))
                                elif tp>0: ret_all=np.where(mfe_all>=tp,tp,tret_all)
                                elif sl>0: ret_all=np.where(mae_all>=sl,-sl,tret_all)
                                else: ret_all=tret_all

                                # Build ret matrix (same shape as ep_mat)
                                ret_mat = np.zeros_like(ep_mat)
                                for i in range(nd):
                                    s,e = starts[i], min(ends[i], starts[i]+ps)
                                    nn = e-s
                                    if nn > 0: ret_mat[i,:nn] = ret_all[s:e]

                                tsig,wr,avg_roc,tpnl,green,nd2,gpct = matrix_simulate(ep_mat,qty_mat,mask_mat,ret_mat,nd)
                                if tsig<15 or avg_roc<=0: continue
                                results.append({
                                    'label':f"{dn} {gn}+{qn} rk={rn} p={ps} x={eb} TP={tp} SL={sl}",
                                    'sigs':tsig,'wr':round(wr,1),'roc':round(avg_roc,2),
                                    'pnl':round(tpnl),'green':green,'days':nd2,'gpct':round(gpct)
                                })

    print(f"\n  {sel_count:,} selections, {len(results):,} profitable in {time.time()-t0:.0f}s")
    return results

def main():
    t0 = time.time()
    print("="*100)
    print("  OBS=5 TURBO OPTIMIZER")
    print("="*100)
    df, ebs = load_data()
    results = run(df, ebs)

    results.sort(key=lambda x: (-x['gpct'], -x['roc']))
    print(f"\n{'='*120}")
    print(f"  TOP 30 BY GREEN DAYS")
    print(f"{'='*120}")
    print(f"  {'#':>3} {'Config':<75} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>8}")
    for i,r in enumerate(results[:30]):
        print(f"  {i+1:3} {r['label'][:75]:<75} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    results.sort(key=lambda x: -x['roc'])
    print(f"\n{'='*120}")
    print(f"  TOP 30 BY ROC")
    print(f"{'='*120}")
    print(f"  {'#':>3} {'Config':<75} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>8}")
    for i,r in enumerate(results[:30]):
        print(f"  {i+1:3} {r['label'][:75]:<75} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']:2}/{r['days']:2} ({r['gpct']}%)")

    print(f"\n{'='*80}")
    print(f"  BEST PER EXIT TIME")
    print(f"{'='*80}")
    for eb in ebs:
        br = [r for r in results if f'x={eb} ' in r['label']]
        if br:
            b=br[0]; h=(9*60+15+eb-1)//60; m=(9*60+15+eb-1)%60
            print(f"  Exit {eb:3} ({h:02d}:{m:02d}): ROC:{b['roc']:+5.2f}% W:{b['wr']:.0f}% G:{b['green']}/{b['days']}({b['gpct']}%) | {b['label'][:60]}")

    print(f"\n{'='*80}")
    print(f"  BEST PER POSITION SIZE")
    print(f"{'='*80}")
    for ps in [3,5,7,10,12,15,20]:
        pr = [r for r in results if f'p={ps} ' in r['label']]
        if pr:
            b=pr[0]
            print(f"  Pos={ps:2}: ROC:{b['roc']:+5.2f}% W:{b['wr']:.0f}% G:{b['green']}/{b['days']}({b['gpct']}%) PnL:{b['pnl']:+.0f} | {b['label'][:55]}")

    p=sum(1 for r in results if r['roc']>0); g2=sum(1 for r in results if r['roc']>=2); g80=sum(1 for r in results if r['gpct']>=80)
    print(f"\n  Profitable:{p:,} | >=2%ROC:{g2:,} | >=80%green:{g80:,} | Total:{time.time()-t0:.0f}s")

if __name__=='__main__': main()
