"""
S6 DEEP INVESTIGATION — Hidden failures, scoring thresholds, TP scaling
=========================================================================
Top-30 cherry-picked using S6: gap*(sp>.5?1:.3)*(p<500?1.2:.9)
Deep tick-level analysis of WHY high-score trades fail.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 's6_deep_investigation.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                if abs(r['gapPct']) > 10 or r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0 or bkt[0,H]==bkt[0,L]: continue

                # S6 score
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6
                s6 = r['gapPct'] * (1.0 if sp>0.5 else 0.3) * (1.2 if r['dayOpen']<500 else 0.9)
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Tick path
                ticks = []
                for b in range(7,90):
                    if bkt[b,C]<=0: continue
                    ticks.append({
                        'b':b,
                        'pnl':(entry-bkt[b,C])/entry*100,
                        'green':bkt[b,C]>bkt[b,O],
                        'body':abs(bkt[b,C]-bkt[b,O])/max(bkt[b,O],1)*100,
                        'range':(bkt[b,H]-bkt[b,L])/entry*100,
                        'above_vwap':bkt[b,C]>bkt[b,VW] if bkt[b,VW]>0 else False,
                        'vol':float(bkt[b,V]),
                    })
                if len(ticks)<70: continue

                # MFE/MAE
                mfe = max(t['pnl'] for t in ticks)
                mae = min(t['pnl'] for t in ticks)
                mfe_bucket = max(ticks, key=lambda t:t['pnl'])['b']

                # Derived tick features
                f3_pnl = ticks[2]['pnl'] if len(ticks)>2 else 0
                f5_pnl = ticks[4]['pnl'] if len(ticks)>4 else 0
                f10_pnl = ticks[9]['pnl'] if len(ticks)>9 else 0
                f5_green = sum(t['green'] for t in ticks[:5])
                f10_green = sum(t['green'] for t in ticks[:10])
                f3_max_green_body = max((t['body'] for t in ticks[:3] if t['green']), default=0)

                # Momentum: favorable ticks / total in first 15
                fav_ticks = sum(1 for i in range(1,min(15,len(ticks))) if ticks[i]['pnl']>ticks[i-1]['pnl'])
                mom_ratio = fav_ticks/14 if len(ticks)>14 else 0.5

                # Sell vol ratio first 10
                rv = sum(t['vol'] for t in ticks[:10] if not t['green'])
                gv = sum(t['vol'] for t in ticks[:10] if t['green'])
                svr = rv/max(rv+gv,1)

                # VWAP: ticks to go below VWAP
                vwap_ticks = None
                for i,t in enumerate(ticks[:20]):
                    if not t['above_vwap']: vwap_ticks=i; break

                # TP analysis: at which bucket does price reach various TP levels?
                tp_buckets = {}
                for tp in [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
                    for t in ticks:
                        if t['pnl'] >= tp:
                            tp_buckets[tp] = t['b']; break

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'s6':s6,'sp':sp,'mom':mom,'n_red':n_red,
                    'ret90':ret90,'win':ret90>0,'mfe':mfe,'mae':mae,'mfe_bucket':mfe_bucket,
                    'ticks':ticks,'tp_buckets':tp_buckets,
                    'f3_pnl':f3_pnl,'f5_pnl':f5_pnl,'f10_pnl':f10_pnl,
                    'f5_green':f5_green,'f10_green':f10_green,
                    'f3_max_green':f3_max_green_body,'mom_ratio':mom_ratio,
                    'svr':svr,'vwap_ticks':vwap_ticks,'date':r['date'],
                })

    dates = sorted(by_date.keys())
    # Top-30 per day
    all_top30 = []
    for d in dates:
        pool = sorted(by_date[d], key=lambda x:-x['s6'])
        for rank,t in enumerate(pool[:30]):
            t['rank'] = rank+1
            all_top30.append(t)

    top8 = [t for t in all_top30 if t['rank']<=8]
    w8 = [t for t in top8 if t['win']]
    l8 = [t for t in top8 if not t['win']]
    print(f"Top-30: {len(all_top30)}, Top-8: {len(top8)} ({len(w8)}W/{len(l8)}L) in {time.time()-t0:.1f}s")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("S6 DEEP INVESTIGATION\n")
        out.write(f"Top-30: {len(all_top30)}, Top-8: {len(top8)} ({len(w8)}W/{len(l8)}L, {len(w8)/len(top8)*100:.1f}%)\n\n")

        # ═══════════════════════════════════════
        # 1. HIGH-SCORE FAILURE ANALYSIS
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. HIGH-SCORE FAILURE: why do top-ranked S6 trades lose?\n"+"="*110+"\n")

        # Score distribution: winners vs losers
        out.write(f"\n  S6 score distribution:\n")
        out.write(f"  {'S6 Score':>12} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8} {'AvgMAE':>8}\n  "+"-"*55+"\n")
        all_s6 = [t['s6'] for t in top8]
        for pct in [0,20,40,60,80]:
            lo = np.percentile(all_s6, pct)
            hi = np.percentile(all_s6, min(pct+20,100))
            sub = [t for t in top8 if lo<=t['s6']<hi+(0.001 if pct==80 else 0)]
            if len(sub)<5: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            mfe = np.mean([t['mfe'] for t in sub])
            mae = np.mean([t['mae'] for t in sub])
            out.write(f"  {f'p{pct}-p{pct+20}({lo:.1f}-{hi:.1f})':>12} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}% {mfe:>+7.3f}% {mae:>7.3f}%\n")

        # High score losers: what went wrong?
        out.write(f"\n  HIGH-SCORE LOSERS (top quartile S6 that lost):\n")
        s6_p75 = np.percentile(all_s6, 75)
        hi_losers = [t for t in top8 if t['s6']>=s6_p75 and not t['win']]
        hi_winners = [t for t in top8 if t['s6']>=s6_p75 and t['win']]
        out.write(f"    High-score trades: {len(hi_losers)+len(hi_winners)}, Winners: {len(hi_winners)}, Losers: {len(hi_losers)}\n\n")

        if hi_losers and hi_winners:
            out.write(f"    {'Feature':<20} {'HiWinners':>10} {'HiLosers':>10} {'Delta':>10} {'Signal':>15}\n    "+"-"*70+"\n")
            for feat in ['s6','gap','price','sp','mom','n_red','f3_pnl','f5_pnl','f10_pnl',
                         'f5_green','f10_green','f3_max_green','mom_ratio','svr','mfe','mae']:
                wv = np.mean([t[feat] for t in hi_winners if t[feat] is not None])
                lv = np.mean([t[feat] for t in hi_losers if t[feat] is not None])
                sig = ""
                if abs(wv-lv) > 0.01:
                    sig = "W higher" if wv>lv else "L higher"
                out.write(f"    {feat:<20} {wv:>10.3f} {lv:>10.3f} {wv-lv:>+10.3f} {sig:>15}\n")

        # Time to failure for high-score losers
        out.write(f"\n  HIGH-SCORE LOSER PATH (minute-by-minute):\n")
        out.write(f"    {'Tick':>6} {'HiWinPnl':>10} {'HiLosePnl':>10} {'Gap':>8}\n    "+"-"*40+"\n")
        for tb in range(0, min(30, min(len(t['ticks']) for t in hi_losers+hi_winners) if hi_losers+hi_winners else 30)):
            wp = [t['ticks'][tb]['pnl'] for t in hi_winners if tb<len(t['ticks'])]
            lp = [t['ticks'][tb]['pnl'] for t in hi_losers if tb<len(t['ticks'])]
            if not wp or not lp: continue
            out.write(f"    b{tb+8:>4} {np.mean(wp):>+9.3f}% {np.mean(lp):>+9.3f}% {np.mean(wp)-np.mean(lp):>+7.3f}%\n")

        # ═══════════════════════════════════════
        # 2. WHAT S6 IS MISSING
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. WHAT S6 IS MISSING — features that separate W from L within top-8\n"+"="*110+"\n")

        out.write(f"\n  S6 uses: gap * sp_threshold * price_bonus\n")
        out.write(f"  S6 does NOT use: momentum, n_red, volume, VWAP, candle shape\n\n")

        # Test additional features as filters ON TOP of S6
        filters = {
            'mom<0 (negative momentum)': lambda t: t['mom']<0,
            'mom<-0.5': lambda t: t['mom']<-0.5,
            'mom<-1.0': lambda t: t['mom']<-1.0,
            'n_red>=3': lambda t: t['n_red']>=3,
            'n_red>=4': lambda t: t['n_red']>=4,
            'sp>0.55': lambda t: t['sp']>0.55,
            'sp>0.60': lambda t: t['sp']>0.60,
            'gap<5%': lambda t: t['gap']<5,
            'gap<3%': lambda t: t['gap']<3,
            'gap 1.5-5%': lambda t: 1.5<=t['gap']<5,
            'price<300': lambda t: t['price']<300,
            'mom<0 + n_red>=3': lambda t: t['mom']<0 and t['n_red']>=3,
            'mom<-0.5 + sp>0.55': lambda t: t['mom']<-0.5 and t['sp']>0.55,
            'mom<0 + gap<5%': lambda t: t['mom']<0 and t['gap']<5,
            'sp>0.55 + n_red>=3 + gap<5%': lambda t: t['sp']>0.55 and t['n_red']>=3 and t['gap']<5,
        }

        out.write(f"  {'Filter (on top of S6 top-8)':>45} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*70+"\n")
        out.write(f"  {'ALL (no filter)':>45} {len(top8):>5} {len(w8)/len(top8)*100:>5.1f}% {np.mean([t['ret90'] for t in top8]):>+7.3f}%\n")
        filt_results = []
        for name, filt in filters.items():
            sub = [t for t in top8 if filt(t)]
            if len(sub)<20: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            filt_results.append((wr, name, len(sub), ar))
        filt_results.sort(key=lambda x:-x[0])
        for wr,name,n,ar in filt_results:
            out.write(f"  {name:>45} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════
        # 3. SCORING THRESHOLD
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. S6 SCORE THRESHOLD — does a minimum score improve results?\n"+"="*110+"\n")
        out.write(f"  Cherry-pick top-8 from pool where S6 >= threshold\n\n")

        for thresh in [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]:
            total=0;wins=0;dw=0;active=0;trades=0
            for d in dates:
                pool = [t for t in by_date[d] if t['s6']>=thresh]
                pool.sort(key=lambda x:-x['s6'])
                picks = pool[:8]
                if not picks: continue
                active+=1
                dr = sum(t['ret90'] for t in picks)
                for t in picks: trades+=1; total+=t['ret90']; wins+=1 if t['ret90']>0 else 0
                if dr>0: dw+=1
            if trades<20: continue
            out.write(f"  S6>={thresh:>4.1f}: trades={trades:>4} win={wins/trades*100:.1f}% dayWin={dw/max(active,1)*100:.1f}% totalRet={total:+.1f}% days={active}\n")

        # Dynamic threshold: only trade if top-8 average score is high
        out.write(f"\n  DYNAMIC: only trade if average top-8 score >= threshold\n")
        for avg_thresh in [1.0, 2.0, 3.0, 4.0, 5.0]:
            total=0;wins=0;dw=0;active=0;trades=0
            for d in dates:
                pool = sorted(by_date[d], key=lambda x:-x['s6'])[:8]
                if not pool: continue
                avg_sc = np.mean([t['s6'] for t in pool])
                if avg_sc < avg_thresh: continue
                active+=1
                dr = sum(t['ret90'] for t in pool)
                for t in pool: trades+=1; total+=t['ret90']; wins+=1 if t['ret90']>0 else 0
                if dr>0: dw+=1
            if trades<20: continue
            out.write(f"  AvgScore>={avg_thresh:.1f}: trades={trades:>4} win={wins/trades*100:.1f}% dayWin={dw/max(active,1)*100:.1f}% totalRet={total:+.1f}% days={active}\n")

        # ═══════════════════════════════════════
        # 4. TP SCALING — when to take profit
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. TP SCALING — MFE distribution and optimal profit-taking\n"+"="*110+"\n")

        # MFE distribution
        out.write(f"\n  MFE distribution (max favorable excursion):\n")
        out.write(f"    {'MFE Range':>12} {'N':>5} {'%':>6} {'AvgRet@b90':>11} {'AvgMAE':>8}\n    "+"-"*50+"\n")
        for mlo,mhi,mlbl in [(0,0.3,'<0.3%'),(0.3,0.5,'0.3-0.5%'),(0.5,1,'0.5-1%'),(1,1.5,'1-1.5%'),(1.5,2,'1.5-2%'),(2,3,'2-3%'),(3,99,'3%+')]:
            sub = [t for t in top8 if mlo<=t['mfe']<mhi]
            if len(sub)<5: continue
            ar = np.mean([t['ret90'] for t in sub])
            mae = np.mean([t['mae'] for t in sub])
            out.write(f"    {mlbl:>12} {len(sub):>5} {len(sub)/len(top8)*100:>5.1f}% {ar:>+10.3f}% {mae:>7.3f}%\n")

        # TP hit rate: what % of trades reach each TP level?
        out.write(f"\n  TP HIT RATE: what % reach each profit level?\n")
        out.write(f"    {'TP Level':>10} {'HitRate':>8} {'AvgBucket':>10} {'Time':>8}\n    "+"-"*40+"\n")
        for tp in [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
            hits = [t for t in top8 if tp in t['tp_buckets']]
            if not hits: continue
            hr = len(hits)/len(top8)*100
            avg_b = np.mean([t['tp_buckets'][tp] for t in hits])
            h = 9+(15+int(avg_b))//60; m = (15+int(avg_b))%60
            out.write(f"    {f'+{tp}%':>10} {hr:>7.1f}% {f'b{avg_b:.0f}':>10} {h}:{m:02d}\n")

        # TP simulation: if we take profit at X%, what's the total return?
        out.write(f"\n  TP SIMULATION: exit at TP if hit, else hold to b90\n")
        out.write(f"    {'TP%':>6} {'TotalRet':>10} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*35+"\n")
        for tp in [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 0]:
            total=0; wins=0
            for t in top8:
                if tp > 0 and tp in t['tp_buckets']:
                    total += tp - COST; wins += 1
                else:
                    total += t['ret90']
                    if t['ret90'] > 0: wins += 1
            wr = wins/len(top8)*100
            ar = total/len(top8)
            label = f"+{tp}%" if tp > 0 else "b90"
            out.write(f"    {label:>6} {total:>+9.1f}% {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Partial TP: exit 50% at TP, hold rest to b90
        out.write(f"\n  PARTIAL TP: exit 50% at TP, hold 50% to b90\n")
        out.write(f"    {'TP%':>6} {'TotalRet':>10} {'Win%':>6}\n    "+"-"*25+"\n")
        for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
            total=0; wins=0
            for t in top8:
                if tp in t['tp_buckets']:
                    ret = (tp-COST)*0.5 + t['ret90']*0.5
                else:
                    ret = t['ret90']
                total += ret
                if ret > 0: wins += 1
            out.write(f"    +{tp}% {total:>+9.1f}% {wins/len(top8)*100:>5.1f}%\n")

        # MFE timing: when do winners reach peak?
        out.write(f"\n  MFE TIMING: when does the best price occur?\n")
        out.write(f"    {'Window':>15} {'Winners':>8} {'Losers':>8}\n    "+"-"*35+"\n")
        for blo,bhi,blbl in [(7,15,'b7-b15 (early)'),(15,30,'b15-b30'),(30,45,'b30-b45'),
                              (45,60,'b45-b60'),(60,75,'b60-b75'),(75,90,'b75-b90 (late)')]:
            wc = sum(1 for t in w8 if blo<=t['mfe_bucket']<bhi)
            lc = sum(1 for t in l8 if blo<=t['mfe_bucket']<bhi)
            out.write(f"    {blbl:>15} {wc/max(len(w8),1)*100:>7.1f}% {lc/max(len(l8),1)*100:>7.1f}%\n")

        # ═══════════════════════════════════════
        # 5. IMPROVED SCORING: S6 + additional factors
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. IMPROVED SCORERS: S6 + additional factors\n"+"="*110+"\n")

        improved = {
            'S6 (baseline)': lambda t: t['s6'],
            'S6 * (mom<0?1.3:0.7)': lambda t: t['s6']*(1.3 if t['mom']<0 else 0.7),
            'S6 * (mom<-0.5?1.4:mom<0?1.1:0.7)': lambda t: t['s6']*(1.4 if t['mom']<-0.5 else 1.1 if t['mom']<0 else 0.7),
            'S6 * (nred>=3?1.2:0.8)': lambda t: t['s6']*(1.2 if t['n_red']>=3 else 0.8),
            'S6 * (sp>0.55?1.2:1)': lambda t: t['s6']*(1.2 if t['sp']>0.55 else 1),
            'S6 * (gap<5?1:0.5)': lambda t: t['s6']*(1 if t['gap']<5 else 0.5),
            'S6 * (mom<0?1.3:0.7) * (nred>=3?1.1:0.9)': lambda t: t['s6']*(1.3 if t['mom']<0 else 0.7)*(1.1 if t['n_red']>=3 else 0.9),
            'S6 * (mom<0?1.3:0.7) * (gap<5?1:0.5)': lambda t: t['s6']*(1.3 if t['mom']<0 else 0.7)*(1 if t['gap']<5 else 0.5),
            'S6 * (sp>0.55?1.2:1) * (mom<0?1.2:0.8)': lambda t: t['s6']*(1.2 if t['sp']>0.55 else 1)*(1.2 if t['mom']<0 else 0.8),
        }

        out.write(f"  {'Scorer':<55} {'TotRet':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*80+"\n")
        imp_results = []
        for name, scorer in improved.items():
            total=0;wins=0;dw=0;active=0;trades=0
            for d in dates:
                pool = by_date[d]
                for t in pool: t['_isc'] = scorer(t)
                pool.sort(key=lambda x:-x['_isc'])
                picks = pool[:8]
                if not picks: continue
                active+=1
                dr = sum(t['ret90'] for t in picks)
                for t in picks: trades+=1; total+=t['ret90']; wins+=1 if t['ret90']>0 else 0
                if dr>0: dw+=1
            if trades<20: continue
            imp_results.append((total, name, wins/trades*100, dw/max(active,1)*100, trades))
        imp_results.sort(key=lambda x:-x[0])
        for total,name,tw,dw,nt in imp_results:
            marker = " <<<" if 'baseline' in name else ""
            out.write(f"  {name:<55} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%{marker}\n")

        # ═══════════════════════════════════════
        # 6. COMBINED: best scorer + TP + sizing
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. BEST COMBINED STRATEGY\n"+"="*110+"\n")

        best_scorer_name = imp_results[0][1]
        best_scorer = improved[best_scorer_name]
        out.write(f"  Best scorer: {best_scorer_name}\n")
        out.write(f"  + ADD 3x at b15 if pnl>0.3% + below VWAP\n")
        out.write(f"  + Optional TP at various levels\n\n")

        BASE=10000;MARGIN=5
        # Simulate
        for tp_level in [0, 0.5, 0.7, 1.0]:
            total_pnl=0;dw=0;active=0;trades=0;wt=0
            for d in dates:
                pool = by_date[d]
                for t in pool: t['_isc'] = best_scorer(t)
                pool.sort(key=lambda x:-x['_isc'])
                picks = pool[:8]
                if not picks: continue
                active+=1; day_pnl=0
                for t in picks:
                    trades+=1
                    # Check at b15
                    if len(t['ticks'])>8:
                        pnl_b15 = t['ticks'][7]['pnl']
                        vwap_b15 = t['ticks'][7]['above_vwap']
                        if pnl_b15 > 0.3 and not vwap_b15:
                            mult = 3.0
                        else:
                            mult = 1.0
                    else:
                        mult = 1.0

                    # TP check
                    if tp_level > 0 and tp_level in t['tp_buckets']:
                        ret = tp_level - COST
                    else:
                        ret = t['ret90']

                    pnl_b15_actual = t['ticks'][7]['pnl']-COST if len(t['ticks'])>8 else 0
                    remaining = ret - pnl_b15_actual if mult>1 else 0
                    pnl_rs = BASE*MARGIN*(pnl_b15_actual + remaining*mult)/100 if mult>1 else BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1

                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            roc = total_pnl/(BASE*8)*100
            tp_label = f"TP@{tp_level}%" if tp_level>0 else "no TP"
            out.write(f"  {best_scorer_name} + ADD3x@b15 + {tp_label}: ROC={roc:+.1f}% dayWin={dw/max(active,1)*100:.1f}% trdWin={wt/max(trades,1)*100:.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
