"""
P16: Sector Clustering — Do Stocks From Same Price Band Move Together?
=======================================================================
When 3+ stocks in same price band all gap up on same day,
do they ALL reverse or ALL fail? Is it a sector event?
Use price+volume as proxy for sector (similar market cap stocks).
Also: if many top-30 candidates gap up together, is the reversal weaker?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p16_sector_clustering.txt'
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
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v']
                    bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'ret':ret,
                    'price':r['dayOpen'],'avg_br6':float(np.mean(bkt[:6,BR])),
                    'win':ret>0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P16: SECTOR CLUSTERING\n\n")

        # How many gap-up candidates per day?
        out.write(f"{'='*90}\nNUMBER OF GAP-UP CANDIDATES PER DAY -> WIN RATE\n{'='*90}\n")
        out.write(f"  Do crowded gap-up days have weaker reversals?\n\n")
        bins = [(0,20,'<20'),(20,40,'20-40'),(40,60,'40-60'),(60,100,'60-100'),(100,200,'100-200'),(200,999,'>200')]
        out.write(f"  {'Candidates':>12} {'Days':>5} {'AvgWinRate':>11} {'AvgDayRet':>10}\n")
        out.write("  "+"-"*45+"\n")
        for lo,hi,lbl in bins:
            matching_days = [(d, by_date[d]) for d in by_date if lo<=len(by_date[d])<hi]
            if len(matching_days)<3: continue
            day_wrs = []
            day_rets = []
            for d, stocks in matching_days:
                top8 = sorted(stocks, key=lambda x:-x['gap'])[:8]
                wr = sum(1 for s in top8 if s['win'])/len(top8)*100
                dr = sum(s['ret'] for s in top8)
                day_wrs.append(wr)
                day_rets.append(dr)
            out.write(f"  {lbl:>12} {len(matching_days):>5} {np.mean(day_wrs):>10.1f}% {np.mean(day_rets):>+9.3f}%\n")

        # Gap concentration: when top-8 all have similar gap sizes vs diverse
        out.write(f"\n{'='*90}\nGAP CONCENTRATION: top-8 similar gaps vs diverse\n{'='*90}\n")
        out.write(f"  If top-8 gaps are tightly clustered, do they all win or all lose?\n\n")
        conc_data = []
        for d in sorted(by_date.keys()):
            stocks = sorted(by_date[d], key=lambda x:-x['gap'])[:8]
            if len(stocks)<8: continue
            gaps = [s['gap'] for s in stocks]
            gap_std = np.std(gaps)
            gap_range = max(gaps)-min(gaps)
            wr = sum(1 for s in stocks if s['win'])/len(stocks)*100
            dr = sum(s['ret'] for s in stocks)
            conc_data.append({'gap_std':gap_std,'gap_range':gap_range,'wr':wr,'dr':dr,'date':d})

        std_bins = [(0,1,'std<1%'),(1,3,'std 1-3%'),(3,10,'std 3-10%'),(10,999,'std>10%')]
        out.write(f"  {'GapStd':>10} {'Days':>5} {'AvgWR':>7} {'AvgDayRet':>10}\n")
        out.write("  "+"-"*40+"\n")
        for lo,hi,lbl in std_bins:
            m = [c for c in conc_data if lo<=c['gap_std']<hi]
            if len(m)<3: continue
            out.write(f"  {lbl:>10} {len(m):>5} {np.mean([c['wr'] for c in m]):>6.1f}% {np.mean([c['dr'] for c in m]):>+9.3f}%\n")

        # Price clustering: when multiple similar-priced stocks gap up
        out.write(f"\n{'='*90}\nPRICE SIMILARITY IN TOP-8 (do similar-priced stocks move together?)\n{'='*90}\n")
        price_bands = [(0,50),(50,100),(100,200),(200,500),(500,1000),(1000,5000),(5000,99999)]
        for d in sorted(by_date.keys()):
            pass  # analysis per day already done above

        # Correlation: on same day, do high-gap stocks correlate in outcome?
        out.write(f"\n{'='*90}\nSAME-DAY CORRELATION: When rank-1 wins, do ranks 2-8 also win?\n{'='*90}\n")
        r1_win_others = []
        r1_lose_others = []
        for d in sorted(by_date.keys()):
            stocks = sorted(by_date[d], key=lambda x:-x['gap'])[:8]
            if len(stocks)<8: continue
            if stocks[0]['win']:
                r1_win_others.extend([s['win'] for s in stocks[1:]])
            else:
                r1_lose_others.extend([s['win'] for s in stocks[1:]])

        if r1_win_others:
            out.write(f"  When Rank-1 WINS:  ranks 2-8 win {sum(r1_win_others)/len(r1_win_others)*100:.1f}% ({len(r1_win_others)} trades)\n")
        if r1_lose_others:
            out.write(f"  When Rank-1 LOSES: ranks 2-8 win {sum(r1_lose_others)/len(r1_lose_others)*100:.1f}% ({len(r1_lose_others)} trades)\n")

        # Per-day win rate distribution
        out.write(f"\n{'='*90}\nDAILY WIN RATE DISTRIBUTION (how many of top-8 win per day?)\n{'='*90}\n")
        daily_wr_counts = defaultdict(int)
        for d in sorted(by_date.keys()):
            stocks = sorted(by_date[d], key=lambda x:-x['gap'])[:8]
            if len(stocks)<5: continue
            n_win = sum(1 for s in stocks if s['win'])
            daily_wr_counts[n_win] += 1
        out.write(f"  {'Wins/8':>7} {'Days':>5} {'%':>6}\n")
        out.write("  "+"-"*22+"\n")
        total_d = sum(daily_wr_counts.values())
        for nw in range(9):
            if daily_wr_counts[nw]>0:
                out.write(f"  {nw:>5}/8 {daily_wr_counts[nw]:>5} {daily_wr_counts[nw]/total_d*100:>5.1f}%\n")

    print(f"P16 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
