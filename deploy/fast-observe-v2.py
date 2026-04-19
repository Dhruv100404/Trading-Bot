#!/usr/bin/env python3
"""FAST Observe-Then-Trade v2 — separate queries, NumPy vectorized"""
import numpy as np
import pandas as pd
import requests, time, sys

CH = 'http://localhost:8123'
FROM, TO = '2025-12-01', '2026-03-28'
CAP = 100_000

def qdf(sql):
    r = requests.post(CH, data=sql, timeout=120)
    lines = r.text.strip().split('\n')
    if len(lines) < 2: return pd.DataFrame()
    cols = lines[0].split('\t')
    data = [l.split('\t') for l in lines[1:]]
    df = pd.DataFrame(data, columns=cols)
    for c in df.columns:
        try: df[c] = pd.to_numeric(df[c])
        except: pass
    return df

def load_obs(obs_end):
    return qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
        argMin(ltp,bucket) as open_ltp, argMax(ltp,bucket) as end_ltp,
        min(candle_low) as lo, max(candle_high) as hi,
        sum(volume_delta) as vol, avg(volume_rate) as avg_vr, max(volume_rate) as max_vr,
        avg(candle_body_ratio) as body, avg(vwap) as vwap,
        sumIf(volume_delta,bucket<={obs_end//2+1}) as vh1, sumIf(volume_delta,bucket>{obs_end//2+1}) as vh2
    FROM trading.snapshots
    WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}') AND bucket>=1 AND bucket<={obs_end}
    GROUP BY trading_date, symbol HAVING count()>={max(2,obs_end-1)} AND open_ltp>0 AND end_ltp>0
    FORMAT TabSeparatedWithNames""")

def load_post(obs_end, dur):
    return qdf(f"""SELECT toString(trading_date) as dt, symbol as sym,
        max(candle_high) as ph, min(candle_low) as pl, argMax(ltp,bucket) as lltp
    FROM trading.snapshots
    WHERE trading_date>=toDate('{FROM}') AND trading_date<=toDate('{TO}') AND bucket>{obs_end} AND bucket<={obs_end+dur}
    GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")

def load_gaps():
    return qdf(f"""SELECT toString(t.trading_date) as dt, t.symbol as sym,
        toFloat32(if(p.dc>0,(t.do-p.dc)/p.dc*100,0)) as gap
    FROM (SELECT trading_date,symbol,argMin(ltp,bucket) as do FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) t
    ASOF LEFT JOIN (SELECT trading_date,symbol,argMax(ltp,bucket) as dc FROM trading.snapshots
          WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}') GROUP BY trading_date,symbol) p
    ON t.symbol=p.symbol AND t.trading_date>p.trading_date
    WHERE t.trading_date>=toDate('{FROM}')
    FORMAT TabSeparatedWithNames""")

def load_prev():
    return qdf(f"""SELECT toString(trading_date+1) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as pd1,
        (argMax(ltp,bucket)-argMin(ltp,bucket))/argMin(ltp,bucket)*100 as pr1
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-10 AND trading_date<=toDate('{TO}')
      AND bucket>=1 AND bucket<=80 GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")

def load_prev2():
    return qdf(f"""SELECT toString(trading_date+2) as dt, symbol as sym,
        if(argMax(ltp,bucket)>argMin(ltp,bucket),1,-1) as pd2
    FROM trading.snapshots WHERE trading_date>=toDate('{FROM}')-15 AND trading_date<=toDate('{TO}')
      AND bucket>=1 AND bucket<=80 GROUP BY trading_date, symbol FORMAT TabSeparatedWithNames""")

def build_matrix(obs_end):
    t0 = time.time()
    obs = load_obs(obs_end)
    print(f"    obs: {len(obs)} rows ({time.time()-t0:.1f}s)", end='')

    # Compute features from obs
    obs['move'] = (obs['end_ltp'] - obs['open_ltp']) / obs['open_ltp'] * 100
    obs['rng'] = (obs['hi'] - obs['lo']) / obs['open_ltp'] * 100
    obs['vwap_d'] = np.where(obs['vwap']>0, (obs['end_ltp']-obs['vwap'])/obs['vwap']*100, 0)
    obs['vacc'] = np.where(obs['vh1']>0, obs['vh2']/obs['vh1'], 0)
    obs['cons'] = np.where(obs['hi']>obs['lo'], np.abs(obs['end_ltp']-obs['open_ltp'])/(obs['hi']-obs['lo']), 0)
    obs['ep'] = obs['end_ltp']
    obs = obs[np.abs(obs['move']) >= 0.05]

    # Load post-entry outcomes
    for dur, tag in [(20,'s'),(40,'m'),(65,'l')]:
        p = load_post(obs_end, dur)
        obs = obs.merge(p, on=['dt','sym'], how='left', suffixes=('', f'_{tag}'))
        is_buy = obs['move'] > 0
        ph, pl, ll, ep = obs[f'ph_{tag}' if f'ph_{tag}' in obs.columns else 'ph'], obs[f'pl_{tag}' if f'pl_{tag}' in obs.columns else 'pl'], obs[f'lltp_{tag}' if f'lltp_{tag}' in obs.columns else 'lltp'], obs['ep']
        if f'ph_{tag}' not in obs.columns:
            ph, pl, ll = obs['ph'], obs['pl'], obs['lltp']
        else:
            ph, pl, ll = obs[f'ph_{tag}'], obs[f'pl_{tag}'], obs[f'lltp_{tag}']
        obs[f'mfe_{tag}'] = np.where(is_buy, (ph-ep)/ep*100, (ep-pl)/ep*100)
        obs[f'mae_{tag}'] = np.where(is_buy, (ep-pl)/ep*100, (ph-ep)/ep*100)
        obs[f'ret_{tag}'] = np.where(is_buy, (ll-ep)/ep*100, (ep-ll)/ep*100)
        obs.drop(columns=[c for c in obs.columns if c.startswith('ph') or c.startswith('pl') or c.startswith('lltp')], inplace=True, errors='ignore')

    # Merge gaps + prev day
    gaps = load_gaps()
    obs = obs.merge(gaps, on=['dt','sym'], how='left')
    obs['gap'] = obs['gap'].fillna(0)

    prev = load_prev()
    obs = obs.merge(prev, on=['dt','sym'], how='left')
    obs['pd1'] = obs['pd1'].fillna(0)
    obs['pr1'] = obs['pr1'].fillna(0)

    prev2 = load_prev2()
    obs = obs.merge(prev2, on=['dt','sym'], how='left')
    obs['pd2'] = obs['pd2'].fillna(0)

    print(f" -> {len(obs)} final ({time.time()-t0:.1f}s)")
    return obs

def simulate(df, tp, sl, max_pos, ex):
    mfe, mae, tret = df[f'mfe_{ex}'].values, df[f'mae_{ex}'].values, df[f'ret_{ex}'].values
    ep = df['ep'].values
    if tp > 0 and sl > 0:
        both = (mfe>=tp)&(mae>=sl)
        tp1st = mfe/np.maximum(mae,0.01) > tp/sl
        ret = np.where(both, np.where(tp1st,tp,-sl), np.where(mfe>=tp,tp, np.where(mae>=sl,-sl,tret)))
    elif tp > 0: ret = np.where(mfe>=tp,tp,tret)
    elif sl > 0: ret = np.where(mae>=sl,-sl,tret)
    else: ret = tret

    qty = np.maximum(np.floor(CAP/ep).astype(int), 0)
    pnl = ep*(ret/100)*qty
    dts = df['dt'].values
    udts = np.unique(dts)
    rocs, green, tsigs, twins, tpnl = [], 0, 0, 0, 0.0
    for dt in udts:
        idx = np.where(dts==dt)[0][:max_pos]
        dp = pnl[idx].sum()
        dc = (ep[idx]*qty[idx]).sum()
        dm = dc/5
        rocs.append(dp/dm*100 if dm>0 else 0)
        if dp>0: green+=1
        tsigs+=len(idx); twins+=(ret[idx]>0.05).sum(); tpnl+=dp
    return {'sigs':tsigs,'wr':round(twins/tsigs*100,1) if tsigs else 0,'roc':round(np.mean(rocs),2) if rocs else 0,
            'pnl':round(tpnl),'green':green,'days':len(udts),'gpct':round(green/len(udts)*100) if udts.size else 0}

def search(df, obs):
    results = []
    move = df['move'].values; gap = df['gap'].values; p1 = df['pd1'].values; p2 = df['pd2'].values
    mvr = df['max_vr'].values; body = df['body'].values; vacc = df['vacc'].values
    cons = df['cons'].values; vwap = df['vwap_d'].values; pr1 = df['pr1'].values

    dirs = {'SELL':move<0, 'BUY':move>0, 'BOTH':np.ones(len(df),bool)}
    gaps_f = {'all':np.ones(len(df),bool),'gDn1':gap<-1,'gDn05':gap<-0.5,'gUp05':gap>0.5,'gUp1':gap>1,'bGap2':np.abs(gap)>2}
    moms = {'all':np.ones(len(df),bool),'pDn':p1<0,'pUp':p1>0,'2dDn':(p1<0)&(p2<0),'2dUp':(p1>0)&(p2>0),'pStr':pr1>1.5}
    quals = {'all':np.ones(len(df),bool),'v200':mvr>200,'v500':mvr>500,'b06':body>0.6,'s06':cons>0.6,
             's08':cons>0.8,'va15':vacc>1.5,'m03':np.abs(move)>0.3,'m05':np.abs(move)>0.5,
             'vA':((move>0)&(vwap>0.1))|((move<0)&(vwap<-0.1))}
    ranks = {'vr':-mvr,'mv':-np.abs(move),'gp':-np.abs(gap),'vxm':-(mvr*np.abs(move)),'cn':-cons,'ev':-df['vol'].values.astype(float)}
    tpsl = [(0,0),(0.7,0.3),(0.7,0.5),(1.0,0.5),(1.0,0.7),(1.5,0.5),(1.5,0.7),(1.5,1.0),(2.0,0.7),(2.0,1.0)]
    poss = [3,5,8]
    exs = ['s','m','l']
    tested = 0

    for dn,dm in dirs.items():
        for gn,gm in gaps_f.items():
            for mn,mm in moms.items():
                for qn,qm in quals.items():
                    c = dm&gm&mm&qm
                    if c.sum()<20: continue
                    sub = df[c].copy()
                    for rn,rv in ranks.items():
                        sr = rv[c]
                        order = np.argsort(sr)
                        sd = sub.iloc[order]
                        for tp,sl in tpsl:
                            for ps in poss:
                                for ex in exs:
                                    tested+=1
                                    if tested%100000==0: print(f"    {tested:,}...",end='\r',file=sys.stderr)
                                    r = simulate(sd,tp,sl,ps,ex)
                                    if r['sigs']<20 or r['roc']<=0: continue
                                    r['label']=f"{dn} {gn}+{mn}+{qn} rk={rn} TP={tp} SL={sl} p={ps} x={ex}"
                                    results.append(r)
    print(f"    {tested:,} tested, {len(results):,} profitable")
    return results

def main():
    t0 = time.time()
    print("="*100)
    print(f"  FAST OBSERVE-THEN-TRADE v2 (NumPy) | {FROM} to {TO}")
    print("="*100)

    all_res = []
    for obs in [3, 5, 7, 10, 15, 20]:
        print(f"\n{'#'*100}")
        print(f"  OBS={obs}min | entry@bucket {obs+1} | exits: {obs+20}/{obs+40}/{obs+65}")
        print(f"{'#'*100}")
        df = build_matrix(obs)
        if len(df)<100:
            print("  Too few, skip"); continue
        t1=time.time()
        res = search(df, obs)
        res.sort(key=lambda x:-x['roc'])
        print(f"    Done in {time.time()-t1:.0f}s")
        for r in res[:200]: r['label']=f"obs={obs} "+r['label']
        all_res.extend(res[:200])

        print(f"\n  TOP 15 (obs={obs}):")
        print(f"  {'#':>3} {'Config':<90} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>8} {'G/D':>6}")
        for i,r in enumerate(res[:15]):
            print(f"  {i+1:3} {r['label'][:90]:<90} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+8.0f} {r['green']}/{r['days']}")

    all_res.sort(key=lambda x:-x['roc'])
    print(f"\n{'='*130}")
    print(f"  OVERALL TOP 50")
    print(f"{'='*130}")
    print(f"  {'#':>3} {'Config':<100} {'Sg':>4} {'W%':>4} {'ROC':>7} {'PnL':>9} {'G/D':>6}")
    for i,r in enumerate(all_res[:50]):
        print(f"  {i+1:3} {r['label'][:100]:<100} {r['sigs']:4} {r['wr']:3.0f}% {r['roc']:+6.2f}% {r['pnl']:+9.0f} {r['green']}/{r['days']}")

    p=sum(1 for r in all_res if r['roc']>0)
    g1=sum(1 for r in all_res if r['roc']>=1)
    g2=sum(1 for r in all_res if r['roc']>=2)
    print(f"\n  Profitable: {p} | >=1%: {g1} | >=2%: {g2}")
    for d in ['SELL','BUY','BOTH']:
        b=[r for r in all_res if f' {d} ' in r['label']]
        if b: print(f"  Best {d}: {b[0]['label']} -> ROC:{b[0]['roc']:.2f}% Win:{b[0]['wr']:.0f}% PnL:{b[0]['pnl']:.0f} Green:{b[0]['green']}/{b[0]['days']}")
    print(f"\n  Total: {time.time()-t0:.0f}s")

if __name__=='__main__':
    # Force unbuffered output
    import functools
    print = functools.partial(print, flush=True)
    main()
