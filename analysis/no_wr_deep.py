"""
NO-WR DEEP ANALYSIS — Find best scoring WITHOUT stock win rate
================================================================
Only uses features observable at 9:20 AM on that day:
  gap, avg_br6, b0_br, n_red, exhaust, momentum, b0_range,
  vol_ratio, vwap_dev, price, f5range, mkt_context
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'no_wr_deep_analysis.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading data...")
    by_date = defaultdict(list)
    all_gaps_day = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                all_gaps_day[r['date']].append(r['gapPct'])
                if r['gapPct'] <= 0.1: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0: continue

                b0o,b0h,b0l,b0c = bkt[0,O],bkt[0,H],bkt[0,L],bkt[0,C]
                rng0 = b0h-b0l
                body0 = abs(b0c-b0o)
                upper_wick0 = b0h - max(b0o,b0c)

                rets = {}
                for eb in [29,44,59,65,74,89]:
                    ec = bkt[eb,C]
                    if ec > 0: rets[eb] = (entry-ec)/entry*100 - COST

                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_v6 = sum(vol6)
                br_seq = [float(bkt[i,BR]) for i in range(6)]
                exhaust = (b0h-b0c)/rng0 if rng0>0 else 0.5

                by_date[r['date']].append({
                    'sym':r['symbol'], 'gap':r['gapPct'], 'price':r['dayOpen'],
                    'rets':rets, 'entry':entry,
                    'b0_ret':(b0c-b0o)/b0o*100 if b0o>0 else 0,
                    'b0_range':rng0/b0o*100 if b0o>0 else 0,
                    'b0_br':br_seq[0],
                    'b0_green': b0c>b0o,
                    'b0_vol_share': vol6[0]/total_v6 if total_v6>0 else 0,
                    'exhaust': exhaust,
                    'upper_wick_pct': upper_wick0/rng0*100 if rng0>0 else 0,
                    'avg_br6': np.mean(br_seq),
                    'br_trend': br_seq[5]-br_seq[0],
                    'n_red': sum(1 for i in range(6) if bkt[i,C]<bkt[i,O]),
                    'vol_ratio': vol6[0]/np.mean(vol6[1:6]) if np.mean(vol6[1:6])>0 else 0,
                    'momentum': (bkt[5,C]-b0o)/b0o*100 if b0o>0 else 0,
                    'vwap_dev': (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0,
                    'f5range':r.get('f5Range',0),
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                    'b0_green_b1_red': b0c>b0o and bkt[1,C]<bkt[1,O],
                    'seq3': ''.join('D' if bkt[i,C]<bkt[i,O] else 'U' for i in range(3)),
                    'b1_br': float(bkt[1,BR]),
                })

    # Market context
    for date, stocks in by_date.items():
        mc = all_gaps_day.get(date,{})
        avg_gap = np.mean(mc) if mc else 0
        pct_up = sum(1 for g in mc if g>0)/len(mc)*100 if mc else 50
        for s in stocks:
            s['mkt_avg_gap'] = avg_gap
            s['mkt_pct_up'] = pct_up
            s['rel_gap'] = s['gap'] - avg_gap

    dates = sorted(by_date.keys())
    n_recs = sum(len(v) for v in by_date.values())
    print(f"Loaded {n_recs} records across {len(dates)} days in {time.time()-t0:.1f}s")

    def sim(scorer, reject=None, n_pos=8, exit_b=65):
        total=0; dw=0; trades=0; wt=0; active=0
        for date in dates:
            pool = by_date[date]
            if reject: pool = [s for s in pool if not reject(s)]
            if len(pool)<1: continue
            for s in pool: s['_sc'] = scorer(s)
            pool.sort(key=lambda x:-x['_sc'])
            picks = pool[:n_pos]
            rs = [s['rets'].get(exit_b,0) for s in picks if exit_b in s['rets']]
            if not rs: continue
            active+=1; dr=sum(rs); trades+=len(rs); wt+=sum(1 for r in rs if r>0)
            total+=dr
            if dr>0: dw+=1
        if trades==0: return None
        return (total, active, dw/max(active,1)*100, wt/max(trades,1)*100, total/max(trades,1), trades)

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("NO-WR DEEP ANALYSIS: Best scoring without stock win rate\n")
        out.write(f"Universe: {len(liquid)} stocks, {n_recs} gap-up records, {len(dates)} days\n\n")

        # ════════════════════════════════════════════
        # 1. MASSIVE SCORER TEST (200+ formulas, no stock_wr)
        # ════════════════════════════════════════════
        out.write("="*110+"\n1. SCORING FORMULAS (no stock_wr) — top 8, exit b66 and b90\n"+"="*110+"\n")

        scorers = {
            # Baseline
            'gap': lambda s: s['gap'],

            # BR-based
            'gap*(1-br6)': lambda s: s['gap']*(1-s['avg_br6']),
            'gap*(1-b0_br)': lambda s: s['gap']*(1-s['b0_br']),
            'gap*(1-b0_br)*(1-b1_br)': lambda s: s['gap']*(1-s['b0_br'])*(1-s['b1_br']),
            'gap*(br6<.5?1:.3)': lambda s: s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3,
            'gap*(br6<.45?1.2:br6<.55?1:.3)': lambda s: s['gap']*(1.2 if s['avg_br6']<0.45 else 1 if s['avg_br6']<0.55 else 0.3),
            'gap*(1-br6)^2': lambda s: s['gap']*(1-s['avg_br6'])**2,

            # Exhaustion
            'gap*exhaust': lambda s: s['gap']*max(s['exhaust'],0.2),
            'gap*(1-br6)*exhaust': lambda s: s['gap']*(1-s['avg_br6'])*max(s['exhaust'],0.2),
            'gap*(exhaust>.5?1.3:.7)': lambda s: s['gap']*(1.3 if s['exhaust']>0.5 else 0.7),
            'gap*(1-br6)*(exhaust>.5?1.3:.8)': lambda s: s['gap']*(1-s['avg_br6'])*(1.3 if s['exhaust']>0.5 else 0.8),
            'gap*(1-br6)*(exhaust>.7?1.5:exhaust>.4?1:.6)': lambda s: s['gap']*(1-s['avg_br6'])*(1.5 if s['exhaust']>0.7 else 1 if s['exhaust']>0.4 else 0.6),

            # n_red
            'gap*(nred/6)': lambda s: s['gap']*max(s['n_red']/6,0.15),
            'gap*(1-br6)*(nred>=3?1.2:.8)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['n_red']>=3 else 0.8),
            'gap*(nred>=4?1.5:nred>=3?1.2:nred>=2?1:.5)': lambda s: s['gap']*(1.5 if s['n_red']>=4 else 1.2 if s['n_red']>=3 else 1 if s['n_red']>=2 else 0.5),

            # Momentum
            'gap*(mom<0?1.3:.7)': lambda s: s['gap']*(1.3 if s['momentum']<0 else 0.7),
            'gap*(1-br6)*(mom<0?1.2:.8)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['momentum']<0 else 0.8),
            'gap-momentum': lambda s: s['gap'] - s['momentum'],
            'gap*(1-br6)*(mom<-.5?1.4:mom<0?1.1:.7)': lambda s: s['gap']*(1-s['avg_br6'])*(1.4 if s['momentum']<-0.5 else 1.1 if s['momentum']<0 else 0.7),

            # Price
            'gap*(p<200?1.3:p<500?1.1:p<1k?1:.7)': lambda s: s['gap']*(1.3 if s['price']<200 else 1.1 if s['price']<500 else 1 if s['price']<1000 else 0.7),
            'gap*(1-br6)*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['price']<500 else 0.9),

            # Volume
            'gap*(volrat>2?1.3:1)': lambda s: s['gap']*(1.3 if s['vol_ratio']>2 else 1),
            'gap*(1-br6)*(volrat>1.5?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['vol_ratio']>1.5 else 1),
            'gap*(b0_vol_share>.3?1.2:1)': lambda s: s['gap']*(1.2 if s['b0_vol_share']>0.3 else 1),

            # f5range
            'gap*max(f5rng/3,.5)': lambda s: s['gap']*max(s['f5range']/3,0.5),
            'gap*(1-br6)*max(f5rng/3,.5)': lambda s: s['gap']*(1-s['avg_br6'])*max(s['f5range']/3,0.5),
            'gap*(f5rng>2?1.2:.9)': lambda s: s['gap']*(1.2 if s['f5range']>2 else 0.9),

            # VWAP
            'gap-vwap_dev': lambda s: s['gap'] - s['vwap_dev'],
            'gap*(vwap_dev<0?1.3:.8)': lambda s: s['gap']*(1.3 if s['vwap_dev']<0 else 0.8),

            # Relative gap
            'rel_gap': lambda s: max(s['rel_gap'],0.1),
            'rel_gap*(1-br6)': lambda s: max(s['rel_gap'],0.1)*(1-s['avg_br6']),

            # Candle pattern
            'gap*(b0grn_b1red?1.5:1)': lambda s: s['gap']*(1.5 if s['b0_green_b1_red'] else 1),
            'gap*(seq3==DDD?1.3:UDD?1.2:1)': lambda s: s['gap']*(1.3 if s['seq3']=='DDD' else 1.2 if s['seq3']=='UDD' else 1),

            # b0_range
            'gap*(b0rng>2?1.3:1)': lambda s: s['gap']*(1.3 if s['b0_range']>2 else 1),
            'gap*(1-br6)*(b0rng>2?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['b0_range']>2 else 1),

            # br_trend
            'gap*(br_trend<-.1?1.3:1)': lambda s: s['gap']*(1.3 if s['br_trend']<-0.1 else 1),
            'gap*(1-br6)*(br_trend<0?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['br_trend']<0 else 1),

            # ── MEGA COMBOS (3-4 features) ──
            'gap*(1-br6)*exhaust*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*max(s['exhaust'],0.2)*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*(mom<0?1.2:.8)*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['momentum']<0 else 0.8)*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*(exhaust>.5?1.3:.8)*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*(1.3 if s['exhaust']>0.5 else 0.8)*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*(nred>=3?1.2:.8)*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['n_red']>=3 else 0.8)*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*exhaust*(volrat>1.5?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*max(s['exhaust'],0.2)*(1.2 if s['vol_ratio']>1.5 else 1),
            'gap*(1-br6)*exhaust*(mom<0?1.2:.8)': lambda s: s['gap']*(1-s['avg_br6'])*max(s['exhaust'],0.2)*(1.2 if s['momentum']<0 else 0.8),
            'gap*(1-br6)*(exhaust>.5?1.3:.8)*(mom<0?1.2:.8)': lambda s: s['gap']*(1-s['avg_br6'])*(1.3 if s['exhaust']>0.5 else 0.8)*(1.2 if s['momentum']<0 else 0.8),
            'gap*(br6<.45?1.2:br6<.55?1:.3)*(exhaust>.5?1.3:.8)': lambda s: s['gap']*(1.2 if s['avg_br6']<0.45 else 1 if s['avg_br6']<0.55 else 0.3)*(1.3 if s['exhaust']>0.5 else 0.8),
            'gap*(br6<.45?1.2:br6<.55?1:.3)*(p<500?1.2:.9)': lambda s: s['gap']*(1.2 if s['avg_br6']<0.45 else 1 if s['avg_br6']<0.55 else 0.3)*(1.2 if s['price']<500 else 0.9),
            'gap*(br6<.5?1:.3)*(exhaust>.5?1.3:.8)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.3 if s['exhaust']>0.5 else 0.8),
            'gap*(br6<.5?1:.3)*(p<500?1.2:.9)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.2 if s['price']<500 else 0.9),
            'gap*(br6<.5?1:.3)*(mom<0?1.2:.8)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.2 if s['momentum']<0 else 0.8),
            'gap*(br6<.5?1:.3)*(nred>=3?1.2:.8)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.2 if s['n_red']>=3 else 0.8),
            'gap*(br6<.5?1:.3)*(exhaust>.5?1.3:.8)*(p<500?1.2:.9)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.3 if s['exhaust']>0.5 else 0.8)*(1.2 if s['price']<500 else 0.9),
            'gap*(br6<.5?1:.3)*(mom<0?1.2:.8)*(p<500?1.2:.9)': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.2 if s['momentum']<0 else 0.8)*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*(1-b0_br)': lambda s: s['gap']*(1-s['avg_br6'])*(1-s['b0_br']),
            'gap*(1-br6)*(1-b0_br)*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*(1-s['b0_br'])*(1.2 if s['price']<500 else 0.9),
            'gap*(1-br6)*(1-b0_br)*exhaust': lambda s: s['gap']*(1-s['avg_br6'])*(1-s['b0_br'])*max(s['exhaust'],0.2),

            # ── MARKET CONTEXT ──
            'gap*(mkt_pct_up<50?1.3:1)': lambda s: s['gap']*(1.3 if s['mkt_pct_up']<50 else 1),
            'gap*(1-br6)*(mkt_pct_up<50?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*(1.2 if s['mkt_pct_up']<50 else 1),
        }

        reject_filters = {
            'none': None,
            'R:br>.60': lambda s: s['avg_br6']>0.60,
            'R:br>.55+nred<=1': lambda s: s['avg_br6']>0.55 and s['n_red']<=1,
            'R:gap>15': lambda s: s['gap']>15,
            'R:br>.60+nred<=1+gap>15': lambda s: s['avg_br6']>0.60 and s['n_red']<=1 or s['gap']>15,
            'R:mom>0.5+br>.55': lambda s: s['momentum']>0.5 and s['avg_br6']>0.55,
            'R:exhaust<.3+br>.55': lambda s: s['exhaust']<0.3 and s['avg_br6']>0.55,
            'R:combo(br>.6|nred<=1&br>.55|gap>15)': lambda s: s['avg_br6']>0.60 or (s['n_red']<=1 and s['avg_br6']>0.55) or s['gap']>15,
        }

        results = []
        for s_name, scorer in scorers.items():
            for r_name, reject in reject_filters.items():
                for exit_b in [65, 89]:
                    name = f"{s_name} | {r_name} | b{exit_b+1}"
                    r = sim(scorer, reject, 8, exit_b)
                    if r:
                        total, act, dw, tw, pt, nt = r
                        results.append((total, name, act, dw, tw, pt, nt))

        results.sort(key=lambda x: -x[0])

        out.write(f"  {'Strategy':<80} {'TotRet':>8} {'DayW':>6} {'TrdW':>6} {'Trds':>5}\n")
        out.write("  "+"-"*105+"\n")
        for total, name, act, dw, tw, pt, nt in results[:60]:
            marker = " <<<" if 'gap |' in name and 'none' in name and 'b66' in name else ""
            out.write(f"  {name:<80} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {nt:>5}{marker}\n")

        # Find current baseline
        current = next((r for r in results if r[1]=='gap | none | b66'), None)
        best = results[0]
        out.write(f"\n  CURRENT: gap | none | b66 = {current[0]:+.1f}%\n" if current else "")
        out.write(f"  BEST:    {best[1]} = {best[0]:+.1f}%\n")
        if current:
            out.write(f"  IMPROVEMENT: {best[0]-current[0]:+.1f}%\n")

        # ════════════════════════════════════════════
        # 2. POSITION COUNT with best scorer
        # ════════════════════════════════════════════
        best_scorer_name = best[1].split(' | ')[0]
        best_scorer = scorers.get(best_scorer_name, scorers['gap*(1-br6)'])
        best_reject_name = best[1].split(' | ')[1].strip()
        best_reject = reject_filters.get(best_reject_name, None)
        best_exit = int(best[1].split('b')[-1]) - 1

        out.write(f"\n{'='*110}\n2. POSITION COUNT with best scorer\n{'='*110}\n")
        for n_pos in [2,3,4,5,6,7,8,10,12]:
            r = sim(best_scorer, best_reject, n_pos, best_exit)
            if r:
                total, act, dw, tw, pt, nt = r
                out.write(f"  Top-{n_pos:>2}: totalRet={total:>+7.1f}%  dayWin={dw:>5.1f}%  trdWin={tw:>5.1f}%  perTrade={pt:>+.3f}%  trades={nt}\n")

        # ════════════════════════════════════════════
        # 3. MUST-PICK CONDITIONS (no stock_wr, win>65%, n>=30)
        # ════════════════════════════════════════════
        out.write(f"\n{'='*110}\n3. MUST-PICK: combos with WIN>65% (no stock_wr, n>=30)\n{'='*110}\n")
        all_recs = [s for stocks in by_date.values() for s in stocks]
        must_picks = []
        for br_hi in [0.55, 0.50, 0.45, 0.40]:
            for gap_lo in [1.0, 1.5, 2.0, 3.0]:
                for nr_lo in [0, 2, 3, 4]:
                    for ex_lo in [0, 0.3, 0.5, 0.7]:
                        for mom_hi in [99, 0, -0.3]:
                            sub = [s for s in all_recs if
                                   s['avg_br6']<br_hi and s['gap']>=gap_lo and
                                   s['n_red']>=nr_lo and s['exhaust']>=ex_lo and
                                   s['momentum']<mom_hi and 65 in s['rets']]
                            if len(sub)<30: continue
                            wr = sum(1 for s in sub if s['rets'][65]>0)/len(sub)*100
                            ar = np.mean([s['rets'][65] for s in sub])
                            if wr >= 65:
                                desc = f"br<{br_hi} + gap>={gap_lo} + nred>={nr_lo} + exhaust>={ex_lo} + mom<{mom_hi}"
                                must_picks.append((wr, ar, len(sub), desc))

        must_picks.sort(key=lambda x: (-x[0], -x[1]))
        seen = set()
        out.write(f"  {'Condition':<70} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*95+"\n")
        count = 0
        for wr, ar, n, desc in must_picks:
            if desc in seen: continue
            seen.add(desc)
            out.write(f"  {desc:<70} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")
            count += 1
            if count >= 40: break

        # ════════════════════════════════════════════
        # 4. REJECT CONDITIONS (no stock_wr, win<35%)
        # ════════════════════════════════════════════
        out.write(f"\n{'='*110}\n4. REJECT: combos with WIN<35% (no stock_wr)\n{'='*110}\n")
        rejects = []
        for br_lo in [0.50, 0.55, 0.60, 0.65]:
            for nr_hi in [0, 1, 2, 3]:
                for ex_hi in [1.01, 0.5, 0.3]:
                    for mom_lo in [-99, 0, 0.3]:
                        sub = [s for s in all_recs if
                               s['avg_br6']>=br_lo and s['n_red']<=nr_hi and
                               s['exhaust']<ex_hi and s['momentum']>mom_lo and
                               65 in s['rets']]
                        if len(sub)<30: continue
                        wr = sum(1 for s in sub if s['rets'][65]>0)/len(sub)*100
                        ar = np.mean([s['rets'][65] for s in sub])
                        if wr < 40:
                            desc = f"br>={br_lo} + nred<={nr_hi} + exhaust<{ex_hi} + mom>{mom_lo}"
                            rejects.append((wr, ar, len(sub), desc))

        rejects.sort(key=lambda x: (x[0], x[1]))
        seen = set()
        out.write(f"  {'Condition':<65} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*90+"\n")
        count = 0
        for wr, ar, n, desc in rejects:
            if desc in seen: continue
            seen.add(desc)
            out.write(f"  {desc:<65} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")
            count += 1
            if count >= 30: break

        # ════════════════════════════════════════════
        # 5. BEST COMBO: scorer + filter + position count + exit
        # ════════════════════════════════════════════
        out.write(f"\n{'='*110}\n5. BEST COMBO: scorer + reject + positions + exit\n{'='*110}\n")
        top_scorers = [n.split(' | ')[0] for _,n,*_ in results[:5]]
        top_scorers = list(dict.fromkeys(top_scorers))[:4]  # unique top 4
        top_rejects = ['none','R:combo(br>.6|nred<=1&br>.55|gap>15)','R:br>.55+nred<=1','R:br>.60']

        combo_results = []
        for s_name in top_scorers:
            sc = scorers[s_name]
            for r_name in top_rejects:
                rj = reject_filters[r_name]
                for n_pos in [4, 6, 8]:
                    for exit_b in [65, 89]:
                        name = f"{s_name} | {r_name} | top{n_pos} | b{exit_b+1}"
                        r = sim(sc, rj, n_pos, exit_b)
                        if r:
                            total, act, dw, tw, pt, nt = r
                            combo_results.append((total, name, dw, tw, pt, nt))

        combo_results.sort(key=lambda x: -x[0])
        out.write(f"  {'Strategy':<90} {'TotRet':>8} {'DayW':>6} {'TrdW':>6}\n")
        out.write("  "+"-"*115+"\n")
        for total, name, dw, tw, pt, nt in combo_results[:30]:
            out.write(f"  {name:<90} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%\n")

        out.write(f"\n\nDone in {time.time()-t0:.1f}s\n")

    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
