"""
CONFIRM-EXIT ON TOP OF S6 SCORER
===================================
S6 scorer already gives +87% ROC baseline.
NOW: add confirm scoring at b12/b15/b20 to EXIT weak trades.
Does it improve or hurt?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'confirm_on_s6.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000; MARGIN = 5

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
                if abs(r['gapPct']) > 10: continue
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0 or bkt[0,H]==bkt[0,L]: continue

                # S6 entry score: gap * (sp>0.5?1:0.3) * (price<500?1.2:0.9)
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1 - cp/6
                sp_mult = 1.0 if sp > 0.5 else 0.3
                price_mult = 1.2 if r['dayOpen'] < 500 else 0.9
                s6_score = r['gapPct'] * sp_mult * price_mult

                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Live features at multiple confirm points (NO LOOKAHEAD)
                def live_at(cb):
                    if bkt[cb,C]<=0: return None
                    pnl = (entry-bkt[cb,C])/entry*100
                    vwap = (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0
                    n_green = sum(1 for b in range(7,cb+1) if bkt[b,C]>bkt[b,O])
                    n_total = cb - 6
                    mom = (bkt[cb,C]-bkt[7,O])/bkt[7,O]*100 if bkt[7,O]>0 else 0
                    # Sell vol ratio
                    red_vol = sum(float(bkt[b,V]) for b in range(7,cb+1) if bkt[b,C]<bkt[b,O])
                    green_vol = sum(float(bkt[b,V]) for b in range(7,cb+1) if bkt[b,C]>=bkt[b,O])
                    svr = red_vol/max(red_vol+green_vol,1)
                    return {'pnl':pnl,'vwap':vwap,'n_green':n_green,'n_total':n_total,'mom':mom,'svr':svr}

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'s6':s6_score,'sp':sp,'ret90':ret90,'win':ret90>0,
                    'bkt':bkt,
                    'live10':live_at(9),'live12':live_at(11),'live15':live_at(14),
                    'live20':live_at(19),'live25':live_at(24),'live30':live_at(29),
                    'pnl10':(entry-bkt[9,C])/entry*100-COST if bkt[9,C]>0 else 0,
                    'pnl12':(entry-bkt[11,C])/entry*100-COST if bkt[11,C]>0 else 0,
                    'pnl15':(entry-bkt[14,C])/entry*100-COST if bkt[14,C]>0 else 0,
                    'pnl20':(entry-bkt[19,C])/entry*100-COST if bkt[19,C]>0 else 0,
                    'pnl25':(entry-bkt[24,C])/entry*100-COST if bkt[24,C]>0 else 0,
                    'pnl30':(entry-bkt[29,C])/entry*100-COST if bkt[29,C]>0 else 0,
                })

    dates = sorted(by_date.keys())
    print(f"Loaded {sum(len(v) for v in by_date.values())} records in {time.time()-t0:.1f}s")

    # Cherry-pick top-8 per day using S6
    daily = {}
    for d in dates:
        pool = sorted(by_date[d], key=lambda x:-x['s6'])[:8]
        daily[d] = pool
    all_picks = [t for picks in daily.values() for t in picks]

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("CONFIRM-EXIT ON TOP OF S6 SCORER\n")
        out.write(f"S6 baseline: {len(all_picks)} trades, {sum(t['win'] for t in all_picks)/len(all_picks)*100:.1f}% win\n\n")

        # ═══════════════════════════════════════
        # 1. S6 BASELINE (no confirm)
        # ═══════════════════════════════════════
        base_ret = sum(t['ret90'] for t in all_picks)
        base_roc = sum(t['ret90']*BASE*MARGIN/100 for t in all_picks)/(BASE*8)*100
        base_wr = sum(t['win'] for t in all_picks)/len(all_picks)*100
        base_dw = sum(1 for d in dates if sum(t['ret90'] for t in daily[d])>0)/len(dates)*100
        out.write("="*110+f"\n1. S6 BASELINE: top-8, exit b90, no confirm\n"+"="*110+"\n")
        out.write(f"  ROC={base_roc:+.1f}%  TrdWin={base_wr:.1f}%  DayWin={base_dw:.1f}%  TotalRet={base_ret:+.1f}%\n")

        # ═══════════════════════════════════════
        # 2. LIVE FEATURES at confirm points — winners vs losers
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. LIVE FEATURES: winners vs losers at each confirm point\n"+"="*110+"\n")
        winners = [t for t in all_picks if t['win']]
        losers = [t for t in all_picks if not t['win']]

        for cb_key, cb_label in [('live10','b10 (9:24)'),('live12','b12 (9:26)'),('live15','b15 (9:29)'),('live20','b20 (9:34)')]:
            out.write(f"\n  At {cb_label}:\n")
            out.write(f"    {'Feature':<15} {'Winners':>10} {'Losers':>10} {'Delta':>10}\n    "+"-"*50+"\n")
            for feat in ['pnl','vwap','n_green','mom','svr']:
                wv = np.mean([t[cb_key][feat] for t in winners if t[cb_key]])
                lv = np.mean([t[cb_key][feat] for t in losers if t[cb_key]])
                out.write(f"    {feat:<15} {wv:>10.3f} {lv:>10.3f} {wv-lv:>+10.3f}\n")

        # ═══════════════════════════════════════
        # 3. SIMPLE CONFIRM RULES (feature-based exit)
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. CONFIRM RULES: exit if condition met at check bucket\n"+"="*110+"\n")

        def sim_confirm(exit_fn, cb_key, pnl_key, add_fn=None, add_mult=1.0):
            total_pnl=0; dw=0; active=0; trades=0; wt=0; exits=0; adds=0
            for d in dates:
                picks = daily[d]
                if not picks: continue
                active+=1; day_pnl=0
                for t in picks:
                    trades+=1
                    live = t[cb_key]
                    if live and exit_fn(t, live):
                        pnl_rs = BASE*MARGIN*t[pnl_key]/100
                        exits+=1
                    elif add_fn and live and add_fn(t, live):
                        remaining = t['ret90'] - t[pnl_key]
                        pnl_rs = BASE*MARGIN*(t[pnl_key] + remaining*add_mult)/100
                        adds+=1
                    else:
                        pnl_rs = BASE*MARGIN*t['ret90']/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1
                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            roc = total_pnl/(BASE*8)*100
            return roc, dw/max(active,1)*100, wt/max(trades,1)*100, exits, adds

        out.write(f"  {'Rule':<70} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'Exits':>5}\n  "+"-"*100+"\n")

        # Baseline
        out.write(f"  {'S6 baseline (no confirm)':<70} {base_roc:>+7.1f}% {base_dw:>5.1f}% {base_wr:>5.1f}%     0\n\n")

        # P&L based exits
        for cb_key, pnl_key, cb_label in [('live10','pnl10','b10'),('live12','pnl12','b12'),('live15','pnl15','b15'),('live20','pnl20','b20'),('live25','pnl25','b25')]:
            for thresh in [-0.3, -0.5, -0.7]:
                roc,dw,tw,ex,_ = sim_confirm(lambda t,l: l['pnl']<thresh, cb_key, pnl_key)
                out.write(f"  EXIT if pnl<{thresh}% at {cb_label:<40} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>5}\n")
            out.write("\n")

        # VWAP based exits
        out.write("  VWAP-based exits:\n")
        for cb_key, pnl_key, cb_label in [('live15','pnl15','b15'),('live20','pnl20','b20')]:
            for vthresh in [0, 0.3, 0.5]:
                roc,dw,tw,ex,_ = sim_confirm(lambda t,l,vt=vthresh: l['vwap']>vt and l['pnl']<0, cb_key, pnl_key)
                out.write(f"  EXIT if aboveVWAP>{vthresh}%+losing at {cb_label:<30} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>5}\n")
            out.write("\n")

        # Green candle exits
        out.write("  Green candle exits:\n")
        for cb_key, pnl_key, cb_label in [('live12','pnl12','b12'),('live15','pnl15','b15'),('live20','pnl20','b20')]:
            for gthresh in [3, 4, 5]:
                roc,dw,tw,ex,_ = sim_confirm(lambda t,l,gt=gthresh: l['n_green']>=gt and l['pnl']<0, cb_key, pnl_key)
                out.write(f"  EXIT if {gthresh}+green+losing at {cb_label:<35} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>5}\n")
            out.write("\n")

        # Sell vol ratio exits
        out.write("  Sell vol ratio exits:\n")
        for cb_key, pnl_key, cb_label in [('live15','pnl15','b15'),('live20','pnl20','b20')]:
            roc,dw,tw,ex,_ = sim_confirm(lambda t,l: l['svr']<0.4 and l['pnl']<0, cb_key, pnl_key)
            out.write(f"  EXIT if sell_vol<0.4+losing at {cb_label:<35} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>5}\n")

        # COMBO exits
        out.write("\n  COMBO exits:\n")
        combos = [
            ("pnl<-0.5% + aboveVWAP @b15", 'live15','pnl15', lambda t,l: l['pnl']<-0.5 and l['vwap']>0),
            ("pnl<-0.5% + aboveVWAP>0.3% @b15", 'live15','pnl15', lambda t,l: l['pnl']<-0.5 and l['vwap']>0.3),
            ("pnl<-0.3% + aboveVWAP + 4+green @b15", 'live15','pnl15', lambda t,l: l['pnl']<-0.3 and l['vwap']>0 and l['n_green']>=4),
            ("pnl<-0.5% + sell_vol<0.4 @b15", 'live15','pnl15', lambda t,l: l['pnl']<-0.5 and l['svr']<0.4),
            ("pnl<-0.3% + aboveVWAP>0.3% @b20", 'live20','pnl20', lambda t,l: l['pnl']<-0.3 and l['vwap']>0.3),
            ("pnl<-0.5% + aboveVWAP @b20", 'live20','pnl20', lambda t,l: l['pnl']<-0.5 and l['vwap']>0),
            ("pnl<-0.3% + 5+green @b20", 'live20','pnl20', lambda t,l: l['pnl']<-0.3 and l['n_green']>=5),
            ("pnl<-0.5% + 4+green + aboveVWAP @b15", 'live15','pnl15', lambda t,l: l['pnl']<-0.5 and l['n_green']>=4 and l['vwap']>0),
            ("pnl<-0.3% + aboveVWAP + svr<0.45 @b20", 'live20','pnl20', lambda t,l: l['pnl']<-0.3 and l['vwap']>0 and l['svr']<0.45),
        ]
        for name, cb_key, pnl_key, fn in combos:
            roc,dw,tw,ex,_ = sim_confirm(fn, cb_key, pnl_key)
            out.write(f"  {name:<55} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>5}\n")

        # ═══════════════════════════════════════
        # 4. EXIT + ADD combined
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. EXIT + ADD COMBINED on S6 top-8\n"+"="*110+"\n")

        combined = [
            # EXIT only
            ("EXIT pnl<-0.5%+aboveVWAP @b15", 'live15','pnl15',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0, None, 1),
            ("EXIT pnl<-0.5%+aboveVWAP @b20", 'live20','pnl20',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0, None, 1),

            # ADD only
            ("ADD 2x if pnl>0.3%+belowVWAP @b15", 'live15','pnl15',
             lambda t,l: False, lambda t,l: l['pnl']>0.3 and l['vwap']<0, 2.0),
            ("ADD 3x if pnl>0.3%+belowVWAP @b15", 'live15','pnl15',
             lambda t,l: False, lambda t,l: l['pnl']>0.3 and l['vwap']<0, 3.0),
            ("ADD 2x if pnl>0.5%+belowVWAP @b20", 'live20','pnl20',
             lambda t,l: False, lambda t,l: l['pnl']>0.5 and l['vwap']<-0.3, 2.0),
            ("ADD 3x if pnl>0.5%+belowVWAP @b20", 'live20','pnl20',
             lambda t,l: False, lambda t,l: l['pnl']>0.5 and l['vwap']<-0.3, 3.0),
            ("ADD 2x if pnl>0.3%+belowVWAP+svr>0.5 @b15", 'live15','pnl15',
             lambda t,l: False, lambda t,l: l['pnl']>0.3 and l['vwap']<0 and l['svr']>0.5, 2.0),
            ("ADD 3x if pnl>0.5%+belowVWAP+svr>0.5 @b20", 'live20','pnl20',
             lambda t,l: False, lambda t,l: l['pnl']>0.5 and l['vwap']<-0.3 and l['svr']>0.5, 3.0),

            # EXIT + ADD
            ("EXIT<-0.5%aboveVWAP + ADD2x>0.3%belowVWAP @b15", 'live15','pnl15',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0,
             lambda t,l: l['pnl']>0.3 and l['vwap']<0, 2.0),
            ("EXIT<-0.5%aboveVWAP + ADD3x>0.3%belowVWAP @b15", 'live15','pnl15',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0,
             lambda t,l: l['pnl']>0.3 and l['vwap']<0, 3.0),
            ("EXIT<-0.5%aboveVWAP + ADD2x>0.5%belowVWAP @b20", 'live20','pnl20',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0,
             lambda t,l: l['pnl']>0.5 and l['vwap']<-0.3, 2.0),
            ("EXIT<-0.5%aboveVWAP + ADD3x>0.5%belowVWAP @b20", 'live20','pnl20',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0,
             lambda t,l: l['pnl']>0.5 and l['vwap']<-0.3, 3.0),
            ("EXIT(<-0.5%+aboveVWAP+4green) + ADD3x(>0.3%+belowVWAP) @b15", 'live15','pnl15',
             lambda t,l: l['pnl']<-0.5 and l['vwap']>0 and l['n_green']>=4,
             lambda t,l: l['pnl']>0.3 and l['vwap']<0, 3.0),
            ("EXIT(<-0.3%+aboveVWAP+svr<0.45) + ADD3x(>0.3%+belowVWAP+svr>0.5) @b20", 'live20','pnl20',
             lambda t,l: l['pnl']<-0.3 and l['vwap']>0 and l['svr']<0.45,
             lambda t,l: l['pnl']>0.3 and l['vwap']<0 and l['svr']>0.5, 3.0),
        ]

        out.write(f"  {'Strategy':<75} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'Ex':>4} {'Add':>4}\n  "+"-"*105+"\n")
        out.write(f"  {'S6 baseline (no confirm)':<75} {base_roc:>+7.1f}% {base_dw:>5.1f}% {base_wr:>5.1f}%    0    0\n\n")

        comb_results = []
        for name, cb_key, pnl_key, exit_fn, add_fn, add_mult in combined:
            roc,dw,tw,ex,adds = sim_confirm(exit_fn, cb_key, pnl_key, add_fn, add_mult)
            comb_results.append((roc, name, dw, tw, ex, adds))

        comb_results.sort(key=lambda x:-x[0])
        for roc, name, dw, tw, ex, adds in comb_results:
            out.write(f"  {name:<75} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {ex:>4} {adds:>4}\n")

        # ═══════════════════════════════════════
        # 5. VERDICT
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. VERDICT\n"+"="*110+"\n")
        best = comb_results[0]
        out.write(f"\n  BASELINE:  S6 top-8, no confirm\n    ROC={base_roc:+.1f}%  DayWin={base_dw:.1f}%  TrdWin={base_wr:.1f}%\n")
        out.write(f"\n  BEST:      {best[1]}\n    ROC={best[0]:+.1f}%  DayWin={best[2]:.1f}%  TrdWin={best[3]:.1f}%  Exits={best[4]} Adds={best[5]}\n")
        out.write(f"\n  Improvement: {best[0]-base_roc:+.1f}% ROC\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
