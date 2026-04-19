#!/usr/bin/env python3
"""FAST Observe-Then-Trade v3 — batch TP/SL, pre-grouped dates"""
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

def build_matrix(obs_end):
    t0 = time.time()
    # Obs features
    obs = qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
        argMin(ltp,bucket) as oltp, argMax(ltp,bucket) as eltp,
        min(candle_low) as lo, max(candle_high) as hi,
        sum(volume_delta) as vol, avg(volume_rate) as avr, max(volume_rate) as mvr,
        avg(candle_body_ratio) as body, avg(vwap) as vwap,
        sumIf(volume_delta,bucket<={obs_end//2+1}) as vh1, sumIf(volume_delta,bucket>{obs_end//2+1}) as vh2
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<={obs_end}
    GROUP BY trading_date, symbol HAVING count()>={max(2,obs_end-1)} AND oltp>0 AND eltp>0 FORMAT TabSeparatedWithNames""")
    print(f"  obs:{len(obs)}", end='')

    obs['mv'] = (obs['eltp']-obs['oltp'])/obs['oltp']*100
    obs['rng'] = (obs['hi']-obs['lo'])/obs['oltp']*100
    obs['vd'] = np.where(obs['vwap']>0,(obs['eltp']-obs['vwap'])/obs['vwap']*100,0)
    obs['va'] = np.where(obs['vh1']>0,obs['vh2']/obs['vh1'],0)
    obs['cn'] = np.where(obs['hi']>obs['lo'],np.abs(obs['eltp']-obs['oltp'])/(obs['hi']-obs['lo']),0)
    obs['ep'] = obs['eltp']
    obs = obs[np.abs(obs['mv'])>=0.05].reset_index(drop=True)

    # Post outcomes at 3 durations
    for dur,tag in [(20,'s'),(40,'m'),(65,'l')]:
        p = qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
            max(candle_high) as ph, min(candle_low) as pl, argMax(ltp,bucket) as ll
        FROM trading.snapshots WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}')
          AND bucket>{obs_end} AND bucket<={obs_end+dur}
        GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")
        obs = obs.merge(p, on=['dt','sym'], how='left')
        buy = obs['mv']>0
        obs[f'mfe_{tag}'] = np.where(buy,(obs['ph']-obs['ep'])/obs['ep']*100,(obs['ep']-obs['pl'])/obs['ep']*100)
        obs[f'mae_{tag}'] = np.where(buy,(obs['ep']-obs['pl'])/obs['ep']*100,(obs['ph']-obs['ep'])/obs['ep']*100)
        obs[f'ret_{tag}'] = np.where(buy,(obs['ll']-obs['ep'])/obs['ep']*100,(obs['ep']-obs['ll'])/obs['ep']*100)
        obs.drop(columns=['ph','pl','ll'],inplace=True,errors='ignore')

    # Gaps
    g = qdf(f"""SELECT toString(t.trading_date) as dt, t.symbol as sym,
        toFloat32(if(p.dc>0,(t.do-p.dc)/p.dc*100,0)) as gap
    FROM (SELECT trading_date,symbol,argMin(ltp,bucket) as do FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) t
    ASOF LEFT JOIN (SELECT trading_date,symbol,argMax(ltp,bucket) as dc FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) p
    ON t.symbol=p.symbol AND t.trading_date>p.trading_date WHERE t.trading_date>=toDate('{FROM}')
    FORMAT TabSeparatedWithNames""")
    obs = obs.merge(g,on=['dt','sym'],how='left'); obs['gap']=obs['gap'].fillna(0)

    # Prev days
    pv = qdf(f"""SELECT toString(trading_date+1) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as p1,
        (argMax(ltp,bucket)-argMin(ltp,bucket))/argMin(ltp,bucket)*100 as pr
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<=80
    GROUP BY trading_date,symbol FORMAT TabSeparatedWithNames""")
    obs = obs.merge(pv,on=['dt','sym'],how='left'); obs['p1']=obs['p1'].fillna(0); obs['pr']=obs['pr'].fillna(0)

    p2 = qdf(f"""SELECT toString(trading_date+2) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as p2
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-15 AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<=80
    GROUP BY trading_date,symbol FORMAT TabSeparatedWithNames""")
    obs = obs.merge(p2,on=['dt','sym'],how='left'); obs['p2']=obs['p2'].fillna(0)

    print(f" -> {len(obs)} ({time.time()-t0:.0f}s)")
    return obs

def batch_simulate(df, max_pos, tp_sl_combos, exits):
    """For ONE filter+rank combo (already sorted), test ALL TP/SL × exit combos at once"""
    ep = df['ep'].values
    dts = df['dt'].values
    qty = np.maximum(np.floor(CAP/ep).astype(int), 0)
    udts = np.unique(dts)

    # Pre-select top-N per date (indices)
    day_indices = []
    for dt in udts:
        idx = np.where(dts==dt)[0][:max_pos]
        day_indices.append(idx)

    results = []
    for ex in exits:
        mfe = df[f'mfe_{ex}'].values
        mae = df[f'mae_{ex}'].values
        tret = df[f'ret_{ex}'].values

        for tp, sl in tp_sl_combos:
            # Compute return vector
            if tp>0 and sl>0:
                both=(mfe>=tp)&(mae>=sl); t1=mfe/np.maximum(mae,0.01)>tp/sl
                ret=np.where(both,np.where(t1,tp,-sl),np.where(mfe>=tp,tp,np.where(mae>=sl,-sl,tret)))
            elif tp>0: ret=np.where(mfe>=tp,tp,tret)
            elif sl>0: ret=np.where(mae>=sl,-sl,tret)
            else: ret=tret

            pnl = ep*(ret/100)*qty
            rocs=[]; green=0; tsig=0; twin=0; tpnl=0.0
            for idx in day_indices:
                dp=pnl[idx].sum(); dc=(ep[idx]*qty[idx]).sum(); dm=dc/5
                rocs.append(dp/dm*100 if dm>0 else 0)
                if dp>0: green+=1
                tsig+=len(idx); twin+=(ret[idx]>0.05).sum(); tpnl+=dp

            nd=len(udts)
            results.append({
                'tp':tp,'sl':sl,'ex':ex,
                'sigs':tsig,'wr':round(twin/tsig*100,1) if tsig else 0,
                'roc':round(np.mean(rocs),2) if rocs else 0,
                'pnl':round(tpnl),'green':green,'days':nd,'gpct':round(green/nd*100) if nd else 0
            })
    return results

def search(df):
    mv=df['mv'].values; gap=df['gap'].values; p1=df['p1'].values; p2=df['p2'].values
    mvr=df['mvr'].values; body=df['body'].values; va=df['va'].values
    cn=df['cn'].values; vd=df['vd'].values; pr=df['pr'].values
    n=len(df)

    dirs={'S':mv<0,'B':mv>0,'A':np.ones(n,bool)}
    gfs={'_':np.ones(n,bool),'gD1':gap<-1,'gD05':gap<-0.5,'gU05':gap>0.5,'gU1':gap>1,'bG2':np.abs(gap)>2}
    mfs={'_':np.ones(n,bool),'pD':p1<0,'pU':p1>0,'2D':(p1<0)&(p2<0),'2U':(p1>0)&(p2>0),'pS':pr>1.5}
    qfs={'_':np.ones(n,bool),'v2':mvr>200,'v5':mvr>500,'b6':body>0.6,'s6':cn>0.6,'s8':cn>0.8,
         'va':va>1.5,'m3':np.abs(mv)>0.3,'m5':np.abs(mv)>0.5,'vA':((mv>0)&(vd>0.1))|((mv<0)&(vd<-0.1))}
    rks={'vr':-mvr,'mv':-np.abs(mv),'gp':-np.abs(gap),'vx':-(mvr*np.abs(mv)),'cn':-cn,'ev':-df['vol'].values.astype(float)}
    tpsl=[(0,0),(0.7,0.3),(0.7,0.5),(1.0,0.5),(1.0,0.7),(1.5,0.5),(1.5,0.7),(1.5,1.0),(2.0,0.7),(2.0,1.0)]
    poss=[3,5,8]
    exs=['s','m','l']

    all_results=[]; tested=0
    total = len(dirs)*len(gfs)*len(mfs)*len(qfs)*len(rks)*len(poss)
    print(f"  {total:,} filter+rank+pos combos x {len(tpsl)*len(exs)} TP/SL/exit = {total*len(tpsl)*len(exs):,} total")

    for dn,dm in dirs.items():
        for gn,gm in gfs.items():
            for mn,mm in mfs.items():
                for qn,qm in qfs.items():
                    c=dm&gm&mm&qm
                    nc=c.sum()
                    if nc<20: continue
                    sub=df[c]
                    for rn,rv in rks.items():
                        sr=rv[c]; order=np.argsort(sr); sd=sub.iloc[order]
                        for ps in poss:
                            tested+=1
                            if tested%5000==0: print(f"  {tested:,}/{total:,}...",end='\r')
                            # Batch: test ALL tp/sl/exit at once for this selection
                            batch = batch_simulate(sd, ps, tpsl, exs)
                            for r in batch:
                                if r['sigs']<20 or r['roc']<=0: continue
                                r['label']=f"{dn} {gn}+{mn}+{qn} rk={rn} TP={r['tp']} SL={r['sl']} p={ps} x={r['ex']}"
                                all_results.append(r)

    print(f"  {tested:,} selection combos, {len(all_results):,} profitable configs")
    return all_results

def main():
    t0=time.time()
    print("="*100)
    print(f"  OBSERVE-THEN-TRADE v3 (batch) | {FROM} to {TO}")
    print("="*100)

    all_res=[]
    for obs in [3,5,7,10,15,20]:
        print(f"\n{'#'*80}")
        print(f"  OBS={obs}min | entry@{obs+1} | exits:{obs+20}/{obs+40}/{obs+65}")
        print(f"{'#'*80}")
        df=build_matrix(obs)
        if len(df)<100: print("  skip"); continue
        t1=time.time()
        res=search(df)
        res.sort(key=lambda x:-x['roc'])
        print(f"  Done in {time.time()-t1:.0f}s")
        for r in res[:300]: r['label']=f"o{obs} "+r['label']
        all_res.extend(res[:300])

        print(f"\n  TOP 15 (obs={obs}):")
        print(f"  {'#':>3} {'Config':<85} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>8} {'G/D':>6}")
        for i,r in enumerate(res[:15]):
            print(f"  {i+1:3} {r['label'][:85]:<85} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+8.0f} {r['green']}/{r['days']}")

    all_res.sort(key=lambda x:-x['roc'])
    print(f"\n{'='*120}")
    print(f"  OVERALL TOP 50")
    print(f"{'='*120}")
    print(f"  {'#':>3} {'Config':<95} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>6}")
    for i,r in enumerate(all_res[:50]):
        print(f"  {i+1:3} {r['label'][:95]:<95} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']}/{r['days']}")

    p=sum(1 for r in all_res if r['roc']>0)
    g1=sum(1 for r in all_res if r['roc']>=1)
    g2=sum(1 for r in all_res if r['roc']>=2)
    print(f"\n  Profitable:{p} | >=1%:{g1} | >=2%:{g2}")
    for d in ['S','B','A']:
        nm={'S':'SELL','B':'BUY','A':'BOTH'}[d]
        b=[r for r in all_res if r['label'].split()[1]==d]
        if b: print(f"  Best {nm}: {b[0]['label']} -> ROC:{b[0]['roc']:.2f}% W:{b[0]['wr']:.0f}% PnL:{b[0]['pnl']:.0f} G:{b[0]['green']}/{b[0]['days']}")
    print(f"\n  Total: {time.time()-t0:.0f}s")

if __name__=='__main__': main()
