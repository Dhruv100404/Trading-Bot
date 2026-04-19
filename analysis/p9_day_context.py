"""
P9: Day-Level Context
======================
Does the MARKET context (how many stocks gapped up, avg gap) predict
which days are good for gap reversal vs which days to skip?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p9_day_context.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    by_date = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                by_date[r['date']].append({
                    'sym':r['symbol'], 'gap':r['gapPct'],
                    'day_open':r['dayOpen'], 'f5vol':r.get('f5Vol',0),
                })

    # Now load full bkt data only for gap-up stocks for performance calc
    trade_data = defaultdict(dict)  # date -> {sym -> ret}
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                trade_data[r['date']][r['symbol']] = ret

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P9: DAY-LEVEL CONTEXT ANALYSIS\n")
        out.write(f"Days: {len(by_date)}\n\n")

        days = []
        for date in sorted(by_date.keys()):
            stocks = by_date[date]
            gaps = [s['gap'] for s in stocks]
            n_up = sum(1 for g in gaps if g > 0.1)
            n_down = sum(1 for g in gaps if g < -0.1)
            avg_gap = np.mean(gaps)
            med_gap = np.median(gaps)
            breadth = n_up / max(n_up + n_down, 1)  # % of stocks that gapped up

            # Performance of top-8 sell picks this day
            sells = trade_data.get(date, {})
            if sells:
                sorted_sells = sorted(sells.items(), key=lambda x: -abs(x[1]))  # sort by abs(ret)
                # Actually use the by_date gap info
                gap_sorted = [(s['sym'], s['gap']) for s in stocks if s['gap'] > 0.5]
                gap_sorted.sort(key=lambda x: -x[1])
                top8_rets = [sells[sym] for sym, _ in gap_sorted[:8] if sym in sells]
                day_ret = sum(top8_rets) if top8_rets else 0
                day_wr = sum(1 for r in top8_rets if r > 0) / max(len(top8_rets), 1) * 100
            else:
                day_ret = 0; day_wr = 0; top8_rets = []

            days.append({
                'date': date, 'n_up': n_up, 'n_down': n_down,
                'avg_gap': avg_gap, 'med_gap': med_gap, 'breadth': breadth,
                'day_ret': day_ret, 'day_wr': day_wr, 'n_trades': len(top8_rets),
                'win': 1 if day_ret > 0 else 0,
            })

        # Breadth analysis
        out.write(f"{'='*90}\nMARKET BREADTH (% stocks gap-up) vs DAY PERFORMANCE\n{'='*90}\n")
        breadth_bins = [(0,0.3,'<30%'),(0.3,0.4,'30-40%'),(0.4,0.5,'40-50%'),
                        (0.5,0.6,'50-60%'),(0.6,0.7,'60-70%'),(0.7,1.01,'>70%')]
        out.write(f"  {'Breadth':>10} {'Days':>5} {'DayWin':>7} {'AvgDayRet':>10} {'AvgTradeWR':>11}\n")
        out.write("  "+"-"*50+"\n")
        for blo,bhi,blbl in breadth_bins:
            m = [d for d in days if blo<=d['breadth']<bhi and d['n_trades']>0]
            if len(m)<3: continue
            dw = sum(d['win'] for d in m)/len(m)*100
            ar = np.mean([d['day_ret'] for d in m])
            twr = np.mean([d['day_wr'] for d in m])
            out.write(f"  {blbl:>10} {len(m):>5} {dw:>6.1f}% {ar:>+9.3f}% {twr:>10.1f}%\n")

        # Avg gap analysis
        out.write(f"\n{'='*90}\nAVG MARKET GAP vs DAY PERFORMANCE\n{'='*90}\n")
        ag_bins = [(-99,-0.5,'<-0.5%'),(-0.5,0,'-0.5~0%'),(0,0.5,'0~0.5%'),(0.5,1,'0.5~1%'),(1,99,'>1%')]
        out.write(f"  {'AvgGap':>10} {'Days':>5} {'DayWin':>7} {'AvgDayRet':>10}\n")
        out.write("  "+"-"*40+"\n")
        for glo,ghi,glbl in ag_bins:
            m = [d for d in days if glo<=d['avg_gap']<ghi and d['n_trades']>0]
            if len(m)<3: continue
            dw = sum(d['win'] for d in m)/len(m)*100
            ar = np.mean([d['day_ret'] for d in m])
            out.write(f"  {glbl:>10} {len(m):>5} {dw:>6.1f}% {ar:>+9.3f}%\n")

        # Number of gap-up stocks vs performance
        out.write(f"\n{'='*90}\nNUMBER OF GAP-UP STOCKS vs DAY PERFORMANCE\n{'='*90}\n")
        nu_bins = [(0,50,'<50'),(50,100,'50-100'),(100,200,'100-200'),(200,400,'200-400'),(400,999,'>400')]
        out.write(f"  {'GapUpCount':>12} {'Days':>5} {'DayWin':>7} {'AvgDayRet':>10}\n")
        out.write("  "+"-"*40+"\n")
        for nlo,nhi,nlbl in nu_bins:
            m = [d for d in days if nlo<=d['n_up']<nhi and d['n_trades']>0]
            if len(m)<3: continue
            dw = sum(d['win'] for d in m)/len(m)*100
            ar = np.mean([d['day_ret'] for d in m])
            out.write(f"  {nlbl:>12} {len(m):>5} {dw:>6.1f}% {ar:>+9.3f}%\n")

        # Full day breakdown
        out.write(f"\n{'='*90}\nFULL DAY BREAKDOWN\n{'='*90}\n")
        out.write(f"  {'Date':<12} {'GapUp':>5} {'Breadth':>8} {'AvgGap':>7} {'Trades':>6} {'DayRet':>8} {'DayWR':>6}\n")
        out.write("  "+"-"*60+"\n")
        for d in days:
            marker = " ***" if d['day_ret'] < -2 else ""
            out.write(f"  {d['date']:<12} {d['n_up']:>5} {d['breadth']:>7.1%} {d['avg_gap']:>+6.2f}% {d['n_trades']:>6} {d['day_ret']:>+7.2f}% {d['day_wr']:>5.0f}%{marker}\n")

    print(f"P9 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
