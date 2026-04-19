"""
B36 SCORER HUNT — Find the perfect BUY scorer
================================================
667 B36 qualifying stocks. Current cherry-pick by S6buy drops win from 72% to 50%.
The scorer is WRONG. Find what actually predicts which B36 trades profit most.

Step 1: Minute-by-minute analysis of all 667 B36 trades
Step 2: Feature extraction — what separates big winners from losers?
Step 3: Build new BUY scorer from discovered features
Step 4: Re-analyze minute-by-minute with new scorer's top picks
Step 5: Hidden failure patterns

Filter: gap -2% to -8% only (exclude extreme gaps = potential circuits)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'b36_scorer_hunt.txt'
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
                if r['symbol'] not in liquid: continue
                gap = r['gapPct']
                if gap > -2 or gap < -8: continue  # only gap -2% to -8% (no extreme/circuit)
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                if bkt[0,O]<=0 or bkt[0,H]==bkt[0,L]: continue
                entry = bkt[6,O]
                if entry<=0 or bkt[44,C]<=0: continue

                price = r['dayOpen']
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                bp = cp/6; sp = 1-bp
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_green = sum(1 for i in range(6) if bkt[i,C]>bkt[i,O])
                n_red = 6-n_green
                vwap_dev = (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0
                avg_br = np.mean([float(bkt[i,BR]) for i in range(6)])
                br_trend = float(bkt[5,BR])-float(bkt[0,BR])

                # First candle features
                b0_rng = (bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                b0_body = abs(bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                b0_green = bkt[0,C]>bkt[0,O]
                # Recovery from low: (close - low) / (high - low)
                b0_recovery = (bkt[0,C]-bkt[0,L])/(bkt[0,H]-bkt[0,L]) if bkt[0,H]>bkt[0,L] else 0.5

                # Volume
                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_v = sum(vol6)
                vol_b0_share = vol6[0]/max(total_v,1)
                # Volume on green vs red candles
                green_vol = sum(vol6[i] for i in range(6) if bkt[i,C]>bkt[i,O])
                buy_vol_ratio = green_vol/max(total_v,1)

                # Opening range
                or5_h = float(np.max(bkt[:5,H]))
                or5_l = float(np.min(bkt[:5,L]))
                price_vs_or = (entry-or5_l)/(or5_h-or5_l)*100 if or5_h>or5_l else 50

                # S6buy score (current — baseline)
                s6_buy = abs(gap)*(1.0 if bp>0.5 else 0.3)*(1.2 if price<500 else 0.9)

                # B36 filter
                is_b36 = bp>0.55 and vwap_dev>0 and s6_buy>3

                ret45 = (bkt[44,C]-entry)/entry*100-COST

                # Tick path for minute analysis
                ticks = []
                for b in range(7,45):
                    if bkt[b,C]<=0: continue
                    ticks.append({
                        'b':b,
                        'pnl':(bkt[b,C]-entry)/entry*100,
                        'green':bkt[b,C]>bkt[b,O],
                        'body':abs(bkt[b,C]-bkt[b,O])/max(bkt[b,O],1)*100,
                        'above_vwap':bkt[b,C]>bkt[b,VW] if bkt[b,VW]>0 else False,
                        'vol':float(bkt[b,V]),
                    })

                if len(ticks)<30: continue

                # Tick-derived (first 5 ticks after entry = b7-b11)
                f3_pnl = ticks[2]['pnl'] if len(ticks)>2 else 0
                f5_pnl = ticks[4]['pnl'] if len(ticks)>4 else 0
                f5_green = sum(t['green'] for t in ticks[:5])
                f5_max_red_body = max((t['body'] for t in ticks[:5] if not t['green']), default=0)
                f10_pnl = ticks[9]['pnl'] if len(ticks)>9 else 0

                # Momentum ratio (for buy: favorable = price going UP)
                fav = sum(1 for i in range(1,min(15,len(ticks))) if ticks[i]['pnl']>ticks[i-1]['pnl'])
                mom_ratio = fav/14 if len(ticks)>14 else 0.5

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':gap,'abs_gap':abs(gap),'price':price,'entry':entry,
                    'bp':bp,'sp':sp,'mom':mom,'n_green':n_green,'n_red':n_red,
                    'vwap_dev':vwap_dev,'avg_br':avg_br,'br_trend':br_trend,
                    'b0_rng':b0_rng,'b0_body':b0_body,'b0_green':b0_green,'b0_recovery':b0_recovery,
                    'vol_b0_share':vol_b0_share,'buy_vol_ratio':buy_vol_ratio,
                    'price_vs_or':price_vs_or,
                    's6_buy':s6_buy,'is_b36':is_b36,'ret45':ret45,'win':ret45>0,
                    'ticks':ticks,'f3_pnl':f3_pnl,'f5_pnl':f5_pnl,
                    'f5_green':f5_green,'f5_max_red':f5_max_red_body,
                    'f10_pnl':f10_pnl,'mom_ratio':mom_ratio,
                    'date':r['date'],
                })

    dates = sorted(by_date.keys())
    all_b36 = [r for stocks in by_date.values() for r in stocks if r['is_b36']]
    all_recs = [r for stocks in by_date.values() for r in stocks]
    b36_w = [r for r in all_b36 if r['win']]
    b36_l = [r for r in all_b36 if not r['win']]
    print(f"B36 qualifying (gap -2 to -8%): {len(all_b36)} ({len(b36_w)}W/{len(b36_l)}L) in {time.time()-t0:.1f}s")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("B36 SCORER HUNT\n")
        out.write(f"B36 (gap -2 to -8%): {len(all_b36)} trades ({len(b36_w)}W/{len(b36_l)}L = {len(b36_w)/len(all_b36)*100:.1f}%)\n\n")

        # ═══════════════════════════════════════
        # 1. MINUTE-BY-MINUTE: winners vs losers path
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. MINUTE-BY-MINUTE: B36 winners vs losers\n"+"="*110+"\n")
        out.write(f"  {'Tick':>6} {'WinPnl':>8} {'LosePnl':>8} {'Gap':>8} {'WinGreen':>9} {'LoseGreen':>10}\n  "+"-"*55+"\n")
        for tb in range(0, min(30, min(len(t['ticks']) for t in all_b36))):
            wp = [t['ticks'][tb]['pnl'] for t in b36_w if tb<len(t['ticks'])]
            lp = [t['ticks'][tb]['pnl'] for t in b36_l if tb<len(t['ticks'])]
            wg = [sum(1 for tick in t['ticks'][:tb+1] if tick['green']) for t in b36_w if tb<len(t['ticks'])]
            lg = [sum(1 for tick in t['ticks'][:tb+1] if tick['green']) for t in b36_l if tb<len(t['ticks'])]
            if not wp or not lp: continue
            gap = np.mean(wp)-np.mean(lp)
            marker = " *** SPLIT" if gap>0.2 else ""
            out.write(f"  b{tb+8:>4} {np.mean(wp):>+7.3f}% {np.mean(lp):>+7.3f}% {gap:>+7.3f}% {np.mean(wg):>8.1f} {np.mean(lg):>9.1f}{marker}\n")

        # ═══════════════════════════════════════
        # 2. FEATURE IMPORTANCE: what predicts B36 winners?
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. FEATURE IMPORTANCE: winners vs losers\n"+"="*110+"\n")
        out.write(f"  {'Feature':<18} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Direction':>15}\n  "+"-"*70+"\n")
        for f in ['abs_gap','price','bp','mom','n_green','vwap_dev','avg_br','br_trend',
                  'b0_rng','b0_body','b0_recovery','vol_b0_share','buy_vol_ratio',
                  'price_vs_or','s6_buy']:
            wv = np.mean([r[f] for r in b36_w])
            lv = np.mean([r[f] for r in b36_l])
            d = wv-lv
            direction = "W higher" if d>0.01 else "L higher" if d<-0.01 else "same"
            out.write(f"  {f:<18} {wv:>10.3f} {lv:>10.3f} {d:>+10.3f} {direction:>15}\n")

        # ═══════════════════════════════════════
        # 3. THRESHOLD SEARCH: per feature
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. THRESHOLD SEARCH: which thresholds split winners from losers?\n"+"="*110+"\n")
        for feat in ['abs_gap','price','bp','mom','n_green','vwap_dev','avg_br',
                     'b0_recovery','buy_vol_ratio','price_vs_or','b0_body']:
            vals = [r[feat] for r in all_b36]
            out.write(f"\n  {feat}:\n")
            best_split = 0; best_thresh = 0
            for pct in [20,30,40,50,60,70,80]:
                thresh = np.percentile(vals, pct)
                above = [r for r in all_b36 if r[feat]>thresh]
                below = [r for r in all_b36 if r[feat]<=thresh]
                if len(above)<20 or len(below)<20: continue
                wr_above = sum(r['win'] for r in above)/len(above)*100
                wr_below = sum(r['win'] for r in below)/len(below)*100
                split = abs(wr_above-wr_below)
                if split>best_split: best_split=split; best_thresh=thresh
                better = "ABOVE" if wr_above>wr_below else "BELOW"
                out.write(f"    p{pct}({thresh:>7.2f}): above={wr_above:.1f}%(n={len(above)}) below={wr_below:.1f}%(n={len(below)}) {better}\n")

        # ═══════════════════════════════════════
        # 4. NEW BUY SCORERS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. NEW BUY SCORERS: test 30+ formulas\n"+"="*110+"\n")

        scorers = {
            'S6buy (baseline: gap*bp_thresh*price)': lambda r: r['s6_buy'],
            'abs_gap only': lambda r: r['abs_gap'],
            'abs_gap * mom (positive=good)': lambda r: r['abs_gap']*max(r['mom'],0.1),
            'abs_gap * (mom>0.5?1.5:mom>0?1.1:0.5)': lambda r: r['abs_gap']*(1.5 if r['mom']>0.5 else 1.1 if r['mom']>0 else 0.5),
            'abs_gap * b0_recovery': lambda r: r['abs_gap']*max(r['b0_recovery'],0.2),
            'abs_gap * buy_vol_ratio': lambda r: r['abs_gap']*max(r['buy_vol_ratio'],0.2),
            'abs_gap * (price<500?1.3:1)': lambda r: r['abs_gap']*(1.3 if r['price']<500 else 1),
            'abs_gap * (vwap>0.3?1.3:1)': lambda r: r['abs_gap']*(1.3 if r['vwap_dev']>0.3 else 1),
            'abs_gap * avg_br': lambda r: r['abs_gap']*max(r['avg_br'],0.2),
            'mom * bp': lambda r: max(r['mom'],0)*r['bp'],
            'abs_gap * mom * bp': lambda r: r['abs_gap']*max(r['mom'],0.1)*r['bp'],
            'abs_gap * (bp>0.6?1.5:1) * (mom>0?1.3:0.7)': lambda r: r['abs_gap']*(1.5 if r['bp']>0.6 else 1)*(1.3 if r['mom']>0 else 0.7),
            'abs_gap * b0_recovery * (mom>0?1.3:0.7)': lambda r: r['abs_gap']*max(r['b0_recovery'],0.2)*(1.3 if r['mom']>0 else 0.7),
            'abs_gap * buy_vol_ratio * (mom>0?1.3:0.7)': lambda r: r['abs_gap']*max(r['buy_vol_ratio'],0.2)*(1.3 if r['mom']>0 else 0.7),
            # Price-aware
            'abs_gap * (price<300?1.5:price<700?1:0.7)': lambda r: r['abs_gap']*(1.5 if r['price']<300 else 1 if r['price']<700 else 0.7),
            'abs_gap * b0_recovery * (price<500?1.3:1)': lambda r: r['abs_gap']*max(r['b0_recovery'],0.2)*(1.3 if r['price']<500 else 1),
            # Candle count
            'abs_gap * (ngreen>=4?1.3:ngreen>=3?1:0.7)': lambda r: r['abs_gap']*(1.3 if r['n_green']>=4 else 1 if r['n_green']>=3 else 0.7),
            # Mega combos
            'abs_gap * b0_recovery * buy_vol * (mom>0?1.3:.7)': lambda r: r['abs_gap']*max(r['b0_recovery'],0.2)*max(r['buy_vol_ratio'],0.2)*(1.3 if r['mom']>0 else 0.7),
            'abs_gap * (bp>0.6?1.5:1) * (price<500?1.2:.9) * (mom>0?1.3:.7)': lambda r: r['abs_gap']*(1.5 if r['bp']>0.6 else 1)*(1.2 if r['price']<500 else 0.9)*(1.3 if r['mom']>0 else 0.7),
            'INVERSE gap (smaller gap = better?)': lambda r: 1.0/max(r['abs_gap'],0.5) * r['bp'] * max(r['mom'],0.1),
            # BR trend
            'abs_gap * (br_trend>0?1.3:0.7)': lambda r: r['abs_gap']*(1.3 if r['br_trend']>0 else 0.7),
            'abs_gap * (br_trend>0.1?1.5:1)': lambda r: r['abs_gap']*(1.5 if r['br_trend']>0.1 else 1),
            # OR position
            'abs_gap * (price_vs_or>60?1.3:price_vs_or>40?1:0.7)': lambda r: r['abs_gap']*(1.3 if r['price_vs_or']>60 else 1 if r['price_vs_or']>40 else 0.7),
        }

        def sim_scorer(scorer_fn, n_pos=4):
            total=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                pool = [r for r in by_date[d] if r['is_b36']]
                if len(pool)<1: continue
                for r in pool: r['_sc'] = scorer_fn(r)
                pool.sort(key=lambda x:-x['_sc'])
                picks = pool[:n_pos]
                active+=1
                dr = sum(r['ret45'] for r in picks)
                for r in picks:
                    trades+=1; total+=r['ret45']
                    if r['ret45']>0: wt+=1
                if dr>0: dw+=1
            return total, dw/max(active,1)*100, wt/max(trades,1)*100, trades, active

        out.write(f"  Cherry-pick top-4 from B36 pool, exit b45:\n\n")
        out.write(f"  {'Scorer':<60} {'TotRet':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*85+"\n")
        scorer_results = []
        for name, fn in scorers.items():
            total, dw, tw, nt, act = sim_scorer(fn, 4)
            scorer_results.append((total, name, dw, tw, nt, act))
        scorer_results.sort(key=lambda x:-x[0])
        for total, name, dw, tw, nt, act in scorer_results:
            marker = " <<<" if 'baseline' in name else ""
            out.write(f"  {name:<60} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%{marker}\n")

        # Also test top-2 and top-3
        out.write(f"\n  Best scorer at different position counts:\n")
        best_name = scorer_results[0][1]
        best_fn = scorers[best_name]
        for npos in [1,2,3,4,5,6]:
            total, dw, tw, nt, act = sim_scorer(best_fn, npos)
            out.write(f"    top-{npos}: totalRet={total:+.1f}% dayWin={dw:.1f}% trdWin={tw:.1f}% trades={nt}\n")

        # ═══════════════════════════════════════
        # 5. TICK ANALYSIS WITH NEW SCORER's TOP PICKS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. TICK ANALYSIS: new scorer's top-4 winners vs losers\n"+"="*110+"\n")

        top4_w = []; top4_l = []
        for d in dates:
            pool = [r for r in by_date[d] if r['is_b36']]
            if not pool: continue
            for r in pool: r['_sc'] = best_fn(r)
            pool.sort(key=lambda x:-x['_sc'])
            for r in pool[:4]:
                if r['win']: top4_w.append(r)
                else: top4_l.append(r)

        out.write(f"  Top-4 picks: {len(top4_w)}W / {len(top4_l)}L\n\n")

        if top4_w and top4_l:
            out.write(f"  {'Feature':<18} {'Winners':>10} {'Losers':>10}\n  "+"-"*40+"\n")
            for f in ['abs_gap','price','bp','mom','n_green','vwap_dev','b0_recovery','buy_vol_ratio','f3_pnl','f5_pnl','f5_green','mom_ratio']:
                wv = np.mean([r[f] for r in top4_w])
                lv = np.mean([r[f] for r in top4_l])
                out.write(f"  {f:<18} {wv:>10.3f} {lv:>10.3f}\n")

            # Tick path
            out.write(f"\n  Tick path (top-4 picks):\n")
            out.write(f"  {'Tick':>6} {'WinPnl':>8} {'LosePnl':>8} {'Gap':>8}\n  "+"-"*35+"\n")
            for tb in range(0, min(25, min(len(t['ticks']) for t in top4_w+top4_l))):
                wp = [t['ticks'][tb]['pnl'] for t in top4_w if tb<len(t['ticks'])]
                lp = [t['ticks'][tb]['pnl'] for t in top4_l if tb<len(t['ticks'])]
                if not wp or not lp: continue
                out.write(f"  b{tb+8:>4} {np.mean(wp):>+7.3f}% {np.mean(lp):>+7.3f}% {np.mean(wp)-np.mean(lp):>+7.3f}%\n")

        # ═══════════════════════════════════════
        # 6. HIDDEN FAILURE PATTERNS in top-4
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. HIDDEN FAILURE PATTERNS: why do top-4 B36 picks lose?\n"+"="*110+"\n")

        all_top4 = top4_w + top4_l
        fail_patterns = {
            'f3_pnl < -0.3% (instant drop after entry)': lambda r: r['f3_pnl']<-0.3,
            'f5_pnl < -0.2%': lambda r: r['f5_pnl']<-0.2,
            'f5_green < 2 (sellers still active)': lambda r: r['f5_green']<2,
            'f5_max_red_body > 0.5% (big red candle)': lambda r: r['f5_max_red']>0.5,
            'mom_ratio < 0.4 (weak follow-through)': lambda r: r['mom_ratio']<0.4,
            'price > 1000 (expensive stock)': lambda r: r['price']>1000,
            'gap > -2.5% (small gap)': lambda r: r['abs_gap']<2.5,
            'b0_recovery < 0.4 (first candle closed near low)': lambda r: r['b0_recovery']<0.4,
            'buy_vol_ratio < 0.45 (sellers have more volume)': lambda r: r['buy_vol_ratio']<0.45,
            'vwap_dev < 0.2% (barely above VWAP)': lambda r: r['vwap_dev']<0.2,
            # Combos
            'f3_pnl<0 + f5_green<2': lambda r: r['f3_pnl']<0 and r['f5_green']<2,
            'price>700 + b0_recovery<0.5': lambda r: r['price']>700 and r['b0_recovery']<0.5,
            'mom_ratio<0.4 + buy_vol<0.45': lambda r: r['mom_ratio']<0.4 and r['buy_vol_ratio']<0.45,
        }

        out.write(f"  {'Pattern':<55} {'N':>4} {'LoseRate':>9} {'AvgRet':>8}\n  "+"-"*80+"\n")
        for name, filt in fail_patterns.items():
            sub = [r for r in all_top4 if filt(r)]
            if len(sub)<5: continue
            lr = sum(1 for r in sub if not r['win'])/len(sub)*100
            ar = np.mean([r['ret45'] for r in sub])
            out.write(f"  {name:<55} {len(sub):>4} {lr:>8.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════
        # 7. VERDICT
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n7. VERDICT: best B36 scorer\n"+"="*110+"\n")
        baseline_total = next(x for x in scorer_results if 'baseline' in x[1])
        best = scorer_results[0]
        out.write(f"\n  BASELINE (S6buy): totalRet={baseline_total[0]:+.1f}% dayWin={baseline_total[2]:.1f}%\n")
        out.write(f"  BEST:             totalRet={best[0]:+.1f}% dayWin={best[2]:.1f}%\n")
        out.write(f"  Scorer:           {best[1]}\n")
        out.write(f"  Improvement:      {best[0]-baseline_total[0]:+.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
