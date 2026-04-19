"""
DEEP BUY ANALYSIS — Find profitable BUY patterns in 1268 liquid stocks
=======================================================================
Even in bearish Dec-Mar, SOME gap-down stocks bounce. Find which ones and why.
Mirror the entire sell analysis for the buy side.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_buy_analysis.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    stock_buy_stats = defaultdict(lambda: [0,0])

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] >= -0.1: continue
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
                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_v6 = sum(vol6)
                br_seq = [float(bkt[i,BR]) for i in range(6)]

                rets = {}
                for eb in [19,29,44,59,65,74,89]:
                    ec = bkt[eb,C]
                    if ec>0: rets[eb] = (ec-entry)/entry*100 - COST  # BUY: profit when price goes UP

                mfe = {}
                for eb in [44,65,89]:
                    if bkt[eb,C]>0:
                        mfe[eb] = (float(np.max(bkt[6:eb+1,H]))-entry)/entry*100

                # Buy pressure = avg close position (close near high = buyers winning)
                close_pos_sum = 0.0
                n_green = 0
                for i in range(6):
                    rng = bkt[i,H]-bkt[i,L]
                    close_pos_sum += (bkt[i,C]-bkt[i,L])/rng if rng>0 else 0.5
                    if bkt[i,C]>bkt[i,O]: n_green += 1
                buy_pressure = close_pos_sum / 6

                # Recovery: how much of gap has been recovered by b6?
                gap_size = abs(r['gapPct'])
                recovery = (bkt[5,C]-b0o)/b0o*100 if b0o>0 else 0  # positive = bouncing up

                exhaust_buy = (b0c-b0l)/rng0 if rng0>0 else 0.5  # close near high = buyers won first candle

                rec = {
                    'sym':r['symbol'], 'gap':r['gapPct'], 'abs_gap':gap_size,
                    'price':r['dayOpen'], 'rets':rets, 'mfe':mfe, 'entry':entry,
                    'buy_pressure': buy_pressure,
                    'avg_br6': np.mean(br_seq),
                    'b0_br': br_seq[0],
                    'b0_ret': (b0c-b0o)/b0o*100 if b0o>0 else 0,
                    'b0_green': b0c>b0o,
                    'n_green': n_green,
                    'momentum': (bkt[5,C]-b0o)/b0o*100 if b0o>0 else 0,
                    'vwap_dev': (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0,
                    'exhaust_buy': exhaust_buy,
                    'vol_ratio': vol6[0]/np.mean(vol6[1:6]) if np.mean(vol6[1:6])>0 else 0,
                    'f5range': r.get('f5Range',0),
                    'recovery': recovery,
                }
                by_date[r['date']].append(rec)

                # Stock buy stats
                r45 = rets.get(44,None)
                if r45 is not None:
                    stock_buy_stats[r['symbol']][1] += 1
                    if r45 > 0: stock_buy_stats[r['symbol']][0] += 1

    dates = sorted(by_date.keys())
    all_recs = [s for stocks in by_date.values() for s in stocks]
    print(f"Loaded {len(all_recs)} gap-down records across {len(dates)} days in {time.time()-t0:.1f}s")

    def sim(scorer, reject=None, n_pos=8, exit_b=44):
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
        return (total, active, dw/max(active,1)*100, wt/max(trades,1)*100, trades)

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"DEEP BUY ANALYSIS\n")
        out.write(f"Gap-down records (gap<-0.1%): {len(all_recs)} across {len(dates)} days\n\n")

        # 1. BUY baseline by gap size and exit
        out.write("="*100+"\n1. BUY BASELINE: gap-down reversal by gap size and exit bucket\n"+"="*100+"\n")
        exit_bkts = [19,29,44,59,65,89]
        gap_ranges = [(-1,-0.1,'0.1-1%'),(-2,-1,'1-2%'),(-3,-2,'2-3%'),(-5,-3,'3-5%'),(-10,-5,'5-10%'),(-100,-10,'10%+')]
        out.write(f"  {'Gap':>8}")
        for eb in exit_bkts:
            h=9+(15+eb)//60; m=(15+eb)%60
            out.write(f"  b{eb+1}({h}:{m:02d})".rjust(12))
        out.write("\n  "+"-"*85+"\n")
        for ghi,glo,glbl in gap_ranges:
            sub = [s for s in all_recs if ghi<=s['gap']<glo]
            if len(sub)<20: continue
            out.write(f"  {glbl:>8}")
            for eb in exit_bkts:
                rs=[s['rets'][eb] for s in sub if eb in s['rets']]
                if not rs: out.write(f"{'--':>12}"); continue
                wr=sum(1 for r in rs if r>0)/len(rs)*100
                ar=np.mean(rs)
                out.write(f"  {wr:.0f}%/{ar:+.2f}%".rjust(12))
            out.write("\n")

        # 2. BUY features analysis
        out.write("\n"+"="*100+"\n2. BUY FEATURE PATTERNS (which features predict bounce?)\n"+"="*100+"\n")
        for exit_b in [44, 65]:
            out.write(f"\n  Exit at b{exit_b+1}:\n")
            patterns = {
                'buy_pressure > 0.55 (buyers in first 6 candles)': lambda s: s['buy_pressure']>0.55,
                'buy_pressure > 0.60': lambda s: s['buy_pressure']>0.60,
                'buy_pressure > 0.65': lambda s: s['buy_pressure']>0.65,
                'avg_br6 > 0.55': lambda s: s['avg_br6']>0.55,
                'avg_br6 > 0.60': lambda s: s['avg_br6']>0.60,
                'b0_green (first candle green)': lambda s: s['b0_green'],
                'b0_br > 0.60': lambda s: s['b0_br']>0.60,
                'b0_br > 0.70': lambda s: s['b0_br']>0.70,
                'n_green >= 3': lambda s: s['n_green']>=3,
                'n_green >= 4': lambda s: s['n_green']>=4,
                'n_green >= 5': lambda s: s['n_green']>=5,
                'momentum > 0 (price recovering)': lambda s: s['momentum']>0,
                'momentum > 0.3%': lambda s: s['momentum']>0.3,
                'momentum > 0.5%': lambda s: s['momentum']>0.5,
                'exhaust_buy > 0.6 (b0 close near high)': lambda s: s['exhaust_buy']>0.6,
                'exhaust_buy > 0.7': lambda s: s['exhaust_buy']>0.7,
                'vwap_dev < -0.3% (price below VWAP)': lambda s: s['vwap_dev']<-0.3,
                'gap < -2% (big gap down)': lambda s: s['gap']<-2,
                'gap < -3%': lambda s: s['gap']<-3,
                'vol_ratio > 2 (high first-candle volume)': lambda s: s['vol_ratio']>2,
                'recovery > 0 (already bouncing)': lambda s: s['recovery']>0,
                # Combos
                'gap<-2% + buy_pressure>0.55': lambda s: s['gap']<-2 and s['buy_pressure']>0.55,
                'gap<-2% + n_green>=3': lambda s: s['gap']<-2 and s['n_green']>=3,
                'gap<-2% + momentum>0': lambda s: s['gap']<-2 and s['momentum']>0,
                'gap<-2% + buy_pressure>0.55 + momentum>0': lambda s: s['gap']<-2 and s['buy_pressure']>0.55 and s['momentum']>0,
                'gap<-3% + buy_pressure>0.55': lambda s: s['gap']<-3 and s['buy_pressure']>0.55,
                'gap<-3% + n_green>=3 + momentum>0': lambda s: s['gap']<-3 and s['n_green']>=3 and s['momentum']>0,
                'gap<-2% + exhaust_buy>0.6 + buy_pressure>0.55': lambda s: s['gap']<-2 and s['exhaust_buy']>0.6 and s['buy_pressure']>0.55,
                'gap<-1% + buy_pressure>0.60 + n_green>=4': lambda s: s['gap']<-1 and s['buy_pressure']>0.60 and s['n_green']>=4,
            }
            rows = []
            for name, filt in patterns.items():
                sub = [s for s in all_recs if filt(s) and exit_b in s['rets']]
                if len(sub)<20: continue
                wr = sum(1 for s in sub if s['rets'][exit_b]>0)/len(sub)*100
                ar = np.mean([s['rets'][exit_b] for s in sub])
                rows.append((ar, name, len(sub), wr))
            rows.sort(key=lambda x: -x[0])
            out.write(f"    {'Pattern':<55} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
            out.write("    "+"-"*80+"\n")
            for ar,name,n,wr in rows:
                out.write(f"    {name:<55} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # 3. BUY MUST-PICK (win > 60%, no stock_wr)
        out.write("\n"+"="*100+"\n3. BUY MUST-PICK: combos with WIN>60% at b45 (n>=30)\n"+"="*100+"\n")
        must = []
        for bp_lo in [0.50, 0.55, 0.60, 0.65]:
            for gap_hi in [-1, -1.5, -2, -3]:
                for ng_lo in [0, 2, 3, 4]:
                    for mom_lo in [-99, 0, 0.3]:
                        for ex_lo in [0, 0.5, 0.6]:
                            sub = [s for s in all_recs if
                                   s['buy_pressure']>=bp_lo and s['gap']<=gap_hi and
                                   s['n_green']>=ng_lo and s['momentum']>=mom_lo and
                                   s['exhaust_buy']>=ex_lo and 44 in s['rets']]
                            if len(sub)<30: continue
                            wr = sum(1 for s in sub if s['rets'][44]>0)/len(sub)*100
                            ar = np.mean([s['rets'][44] for s in sub])
                            if wr >= 60:
                                must.append((wr, ar, len(sub),
                                    f"bp>={bp_lo} + gap<={gap_hi} + ngrn>={ng_lo} + mom>={mom_lo} + exb>={ex_lo}"))
        must.sort(key=lambda x: (-x[0], -x[1]))
        seen = set()
        out.write(f"  {'Condition':<70} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*95+"\n")
        cnt = 0
        for wr,ar,n,desc in must:
            if desc in seen: continue
            seen.add(desc)
            out.write(f"  {desc:<70} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")
            cnt += 1
            if cnt >= 40: break

        # 4. BUY SCORING FORMULAS
        out.write("\n"+"="*100+"\n4. BUY SCORING FORMULAS (cherry-pick top-8 gap-down stocks)\n"+"="*100+"\n")
        scorers = {
            'abs_gap': lambda s: s['abs_gap'],
            'abs_gap*buy_pressure': lambda s: s['abs_gap']*s['buy_pressure'],
            'abs_gap*(bp>.55?1:.3)': lambda s: s['abs_gap'] if s['buy_pressure']>0.55 else s['abs_gap']*0.3,
            'abs_gap*buy_pressure*(mom>0?1.3:.7)': lambda s: s['abs_gap']*s['buy_pressure']*(1.3 if s['momentum']>0 else 0.7),
            'abs_gap*(bp>.55?1:.3)*(mom>0?1.3:.7)': lambda s: (s['abs_gap'] if s['buy_pressure']>0.55 else s['abs_gap']*0.3)*(1.3 if s['momentum']>0 else 0.7),
            'abs_gap*buy_pressure*exhaust_buy': lambda s: s['abs_gap']*s['buy_pressure']*max(s['exhaust_buy'],0.2),
            'abs_gap*buy_pressure*(ngrn/6)': lambda s: s['abs_gap']*s['buy_pressure']*max(s['n_green']/6,0.2),
            'abs_gap*(bp>.55?1:.3)*(ngrn>=3?1.2:.8)': lambda s: (s['abs_gap'] if s['buy_pressure']>0.55 else s['abs_gap']*0.3)*(1.2 if s['n_green']>=3 else 0.8),
            'abs_gap*buy_pressure*(mom>0?1.3:.7)*(p<500?1.2:.9)': lambda s: s['abs_gap']*s['buy_pressure']*(1.3 if s['momentum']>0 else 0.7)*(1.2 if s['price']<500 else 0.9),
            'abs_gap*(bp>.6?1.5:bp>.5?1:.4)*(mom>.3?1.4:mom>0?1.1:.7)': lambda s: s['abs_gap']*(1.5 if s['buy_pressure']>0.6 else 1 if s['buy_pressure']>0.5 else 0.4)*(1.4 if s['momentum']>0.3 else 1.1 if s['momentum']>0 else 0.7),
        }
        rejects = {
            'none': None,
            'R:bp<.45': lambda s: s['buy_pressure']<0.45,
            'R:bp<.45+ngrn<=1': lambda s: s['buy_pressure']<0.45 and s['n_green']<=1,
            'R:mom<-0.5': lambda s: s['momentum']<-0.5,
        }

        results = []
        for s_name, scorer in scorers.items():
            for r_name, reject in rejects.items():
                for exit_b in [44, 65]:
                    for n_pos in [4, 8]:
                        name = f"{s_name} | {r_name} | top{n_pos} | b{exit_b+1}"
                        r = sim(scorer, reject, n_pos, exit_b)
                        if r:
                            total, act, dw, tw, nt = r
                            results.append((total, name, act, dw, tw, nt))

        results.sort(key=lambda x: -x[0])
        out.write(f"  {'Strategy':<85} {'TotRet':>8} {'DayW':>6} {'TrdW':>6} {'Trds':>5}\n")
        out.write("  "+"-"*115+"\n")
        for total, name, act, dw, tw, nt in results[:40]:
            out.write(f"  {name:<85} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {nt:>5}\n")

        # 5. Reliable BUY stocks
        out.write("\n"+"="*100+"\n5. RELIABLE BUY STOCKS (win>=60% at b45, n>=8)\n"+"="*100+"\n")
        stock_list = []
        for sym,(w,t) in stock_buy_stats.items():
            if t >= 8:
                wr = w/t*100
                if wr >= 55:
                    stock_list.append((wr, sym, t))
        stock_list.sort(key=lambda x: -x[0])
        out.write(f"  {'Symbol':<15} {'Win%':>6} {'Trades':>6}\n")
        out.write("  "+"-"*30+"\n")
        for wr,sym,n in stock_list[:50]:
            out.write(f"  {sym:<15} {wr:>5.1f}% {n:>6}\n")
        out.write(f"\n  Total: {len(stock_list)} stocks with >=55% buy win rate\n")

        out.write(f"\n\nDone in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
