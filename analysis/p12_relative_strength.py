"""
P12: Relative Strength — Stock Gap vs Market Average
======================================================
If market average gap is +1% and a stock gaps +3%, its RELATIVE gap is +2%.
Does relative gap predict reversal better than absolute gap?
Also: is the stock an outlier or part of a broad move?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p12_relative_strength.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    # Pass 1: compute market stats per day (all liquid stocks, not just gap-up)
    day_gaps = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                day_gaps[r['date']].append(r['gapPct'])

    market_stats = {}
    for date, gaps in day_gaps.items():
        market_stats[date] = {
            'avg': np.mean(gaps), 'med': np.median(gaps),
            'std': np.std(gaps), 'pct_up': sum(1 for g in gaps if g>0)/len(gaps)*100,
            'n': len(gaps),
        }

    # Pass 2: load gap-up records with trade outcomes
    recs = []
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
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                ms = market_stats.get(r['date'], {})
                mkt_avg = ms.get('avg', 0)
                mkt_std = ms.get('std', 1)
                recs.append({
                    'gap':r['gapPct'], 'ret':ret, 'sym':r['symbol'], 'date':r['date'],
                    'rel_gap': r['gapPct'] - mkt_avg,  # gap above market average
                    'z_gap': (r['gapPct'] - mkt_avg)/max(mkt_std, 0.1),  # z-score of gap
                    'mkt_avg': mkt_avg, 'mkt_pct_up': ms.get('pct_up', 50),
                    'avg_br6': float(np.mean(bkt[:6,BR])),
                    'win': 1 if ret>0 else 0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P12: RELATIVE STRENGTH ANALYSIS\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Relative gap bins
        out.write(f"{'='*90}\nRELATIVE GAP (stock gap - market avg gap) -> WIN RATE\n{'='*90}\n")
        rg_bins = [(-99,0,'Below avg'),(0,1,'0-1% above'),(1,2,'1-2% above'),
                   (2,3,'2-3% above'),(3,5,'3-5% above'),(5,10,'5-10% above'),(10,999,'>10% above')]
        out.write(f"  {'RelGap':>15} {'N':>6} {'Win%':>6} {'AvgRet':>8} {'AvgAbsGap':>10}\n")
        out.write("  "+"-"*50+"\n")
        for lo,hi,lbl in rg_bins:
            m = [r for r in recs if lo<=r['rel_gap']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            ag = np.mean([r['gap'] for r in m])
            out.write(f"  {lbl:>15} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}% {ag:>9.2f}%\n")

        # Z-score bins
        out.write(f"\n{'='*90}\nGAP Z-SCORE (standard deviations from market avg) -> WIN RATE\n{'='*90}\n")
        z_bins = [(-99,0,'z<0'),(0,0.5,'z 0-0.5'),(0.5,1,'z 0.5-1'),(1,1.5,'z 1-1.5'),
                  (1.5,2,'z 1.5-2'),(2,3,'z 2-3'),(3,999,'z>3')]
        out.write(f"  {'Z-score':>10} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*35+"\n")
        for lo,hi,lbl in z_bins:
            m = [r for r in recs if lo<=r['z_gap']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {lbl:>10} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Cross: relative gap x avg_br6
        out.write(f"\n{'='*90}\nHEATMAP: relative_gap x avg_br6 -> win rate\n{'='*90}\n")
        rg2 = [(0,2,'rel 0-2%'),(2,5,'rel 2-5%'),(5,999,'rel>5%')]
        br2 = [(0,0.35,'br<0.35'),(0.35,0.45,'br 0.35-0.45'),(0.45,0.55,'br 0.45-0.55'),(0.55,1.01,'br>0.55')]
        out.write(f"  {'':>12}")
        for _,_,bl in br2: out.write(f" {bl:>14}")
        out.write("\n  "+"-"*75+"\n")
        for rlo,rhi,rlbl in rg2:
            out.write(f"  {rlbl:>12}")
            for blo,bhi,blbl in br2:
                m = [r for r in recs if rlo<=r['rel_gap']<rhi and blo<=r['avg_br6']<bhi]
                if len(m)<15:
                    out.write(f"{'--':>14}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"  {wr:>5.1f}%({len(m):>4})")
            out.write("\n")

        # SCORING: test relative gap as cherry-pick scorer
        out.write(f"\n{'='*90}\nSCORING COMPARISON: abs gap vs relative gap vs z-score\n{'='*90}\n")
        scorers = {
            'Absolute gap (current)': lambda r: r['gap'],
            'Relative gap (gap - mkt_avg)': lambda r: r['rel_gap'],
            'Z-score gap': lambda r: r['z_gap'],
            'Relative * (1-avg_br6)': lambda r: r['rel_gap']*(1-r['avg_br6']),
            'Z-score * (1-avg_br6)': lambda r: r['z_gap']*(1-r['avg_br6']),
            'Gap * (avg_br6<0.5?1:0.3) [prev best]': lambda r: r['gap'] if r['avg_br6']<0.5 else r['gap']*0.3,
            'RelGap * (avg_br6<0.5?1:0.3)': lambda r: (r['rel_gap'] if r['avg_br6']<0.5 else r['rel_gap']*0.3),
        }

        dates = sorted(set(r['date'] for r in recs))
        by_d = defaultdict(list)
        for r in recs: by_d[r['date']].append(r)

        results = []
        for name, scorer in scorers.items():
            day_wins = 0; total_ret = 0; total_trades = 0; win_trades = 0
            for date in dates:
                entries = by_d[date]
                if len(entries) < 3: continue
                for e in entries: e['_sc'] = scorer(e)
                entries.sort(key=lambda x: -x['_sc'])
                picks = entries[:8]
                dr = sum(p['ret'] for p in picks)
                total_ret += dr
                if dr > 0: day_wins += 1
                total_trades += len(picks)
                win_trades += sum(1 for p in picks if p['ret'] > 0)
            n = len(dates)
            results.append((total_ret, name, day_wins/max(n,1)*100, win_trades/max(total_trades,1)*100))

        results.sort(key=lambda x: -x[0])
        out.write(f"  {'Scorer':<45} {'TotRet':>8} {'DayWin':>7} {'TrdWin':>7}\n")
        out.write("  "+"-"*70+"\n")
        for tr, name, dw, tw in results:
            out.write(f"  {name:<45} {tr:>+7.1f}% {dw:>6.1f}% {tw:>6.1f}%\n")

    print(f"P12 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
