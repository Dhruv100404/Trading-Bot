"""
P14: Minute-by-Minute Exit Optimization
=========================================
For each minute from entry (b7) to b90, compute:
- Cumulative win rate if we exit at that minute
- Average return
- What's the OPTIMAL exit time for different stock profiles?
Also: adaptive TP — at what price level should you take profit?
"""
import json, numpy as np, time
from pathlib import Path

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p14_exit_optimization.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']
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
                if entry<=0: continue
                # Compute return at every exit bucket
                exit_rets = {}
                for eb in range(7,91):
                    ec = bkt[eb,C]
                    if ec > 0:
                        exit_rets[eb] = (entry - ec)/entry*100 - 0.15
                if not exit_rets: continue

                # MFE timing: at which bucket does MFE occur?
                mfe_bucket = 7
                mfe_val = 0
                running_min = entry
                for eb in range(7,91):
                    if bkt[eb,L] < running_min:
                        running_min = bkt[eb,L]
                        mfe_bucket = eb
                        mfe_val = (entry - running_min)/entry*100

                recs.append({
                    'gap':r['gapPct'],'sym':r['symbol'],
                    'exit_rets':exit_rets,
                    'mfe_bucket':mfe_bucket, 'mfe_val':mfe_val,
                    'avg_br6':float(np.mean(bkt[:6,BR])),
                    'price':r['dayOpen'],
                })

    with open(OUT,'w') as out:
        out.write(f"P14: EXIT TIMING OPTIMIZATION\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Minute-by-minute exit performance
        out.write(f"{'='*90}\nMINUTE-BY-MINUTE EXIT PERFORMANCE (all gap-up stocks)\n{'='*90}\n")
        out.write(f"  {'Bucket':>7} {'Time':>8} {'Win%':>6} {'AvgRet':>8} {'MedRet':>8}\n")
        out.write("  "+"-"*45+"\n")
        for eb in range(7,91,1):
            rets = [r['exit_rets'].get(eb, 0) for r in recs if eb in r['exit_rets']]
            if not rets: continue
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            mr = np.median(rets)
            h = 9 + (15+eb)//60
            m = (15+eb)%60
            tstr = f"{h}:{m:02d}"
            out.write(f"  b{eb+1:>5} {tstr:>8} {wr:>5.1f}% {ar:>+7.3f}% {mr:>+7.3f}%\n")

        # MFE timing distribution: when does the BEST price occur?
        out.write(f"\n{'='*90}\nMFE TIMING: When does the reversal peak? (best price for sell)\n{'='*90}\n")
        mfe_dist = [0]*91
        for r in recs:
            mfe_dist[r['mfe_bucket']] += 1
        out.write(f"  {'Bucket':>7} {'Time':>8} {'Count':>6} {'%':>6} {'Cumul%':>7}\n")
        out.write("  "+"-"*40+"\n")
        cumul = 0
        for b in range(7,91):
            if mfe_dist[b] == 0: continue
            cumul += mfe_dist[b]
            h = 9 + (15+b)//60; m = (15+b)%60
            out.write(f"  b{b+1:>5} {h}:{m:02d}   {mfe_dist[b]:>6} {mfe_dist[b]/len(recs)*100:>5.1f}% {cumul/len(recs)*100:>6.1f}%\n")

        # Optimal exit by gap range
        out.write(f"\n{'='*90}\nOPTIMAL EXIT BUCKET BY GAP RANGE (highest avg return)\n{'='*90}\n")
        gap_bins = [(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,5,'3-5%'),(5,100,'5%+')]
        for glo,ghi,glbl in gap_bins:
            sub = [r for r in recs if glo<=r['gap']<ghi]
            if len(sub) < 20: continue
            best_eb = 7; best_ret = -99
            out.write(f"\n  Gap {glbl} ({len(sub)} trades):\n")
            out.write(f"    {'Exit':>6} {'Win%':>6} {'AvgRet':>8}\n")
            for eb in [10,15,20,25,30,40,50,60,65,70,75,80,89]:
                rets = [r['exit_rets'].get(eb,0) for r in sub if eb in r['exit_rets']]
                if not rets: continue
                wr = sum(1 for r in rets if r>0)/len(rets)*100
                ar = np.mean(rets)
                marker = ""
                if ar > best_ret: best_ret = ar; best_eb = eb; marker = " <<<"
                out.write(f"    b{eb+1:>4} {wr:>5.1f}% {ar:>+7.3f}%{marker}\n")
            out.write(f"    BEST: b{best_eb+1} ({best_ret:+.3f}%)\n")

        # Optimal exit by avg_br6
        out.write(f"\n{'='*90}\nOPTIMAL EXIT BY AVG_BR6\n{'='*90}\n")
        br_bins = [(0,0.4,'br<0.4'),(0.4,0.5,'br 0.4-0.5'),(0.5,0.6,'br 0.5-0.6'),(0.6,1.01,'br>0.6')]
        for blo,bhi,blbl in br_bins:
            sub = [r for r in recs if blo<=r['avg_br6']<bhi]
            if len(sub)<20: continue
            best_eb = 7; best_ret = -99
            out.write(f"\n  {blbl} ({len(sub)} trades):\n")
            for eb in [15,20,30,45,60,65,75,89]:
                rets = [r['exit_rets'].get(eb,0) for r in sub if eb in r['exit_rets']]
                if not rets: continue
                ar = np.mean(rets)
                wr = sum(1 for r in rets if r>0)/len(rets)*100
                marker = ""
                if ar > best_ret: best_ret = ar; best_eb = eb; marker = " <<<"
                out.write(f"    b{eb+1:>4} wr={wr:.1f}% ret={ar:+.3f}%{marker}\n")

        # TP level analysis
        out.write(f"\n{'='*90}\nTP LEVEL: What % profit should you lock in?\n{'='*90}\n")
        out.write(f"  If TP hits, exit at TP. If not, time-exit at b66.\n\n")
        for tp in [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
            wins = 0; total = 0; sum_ret = 0
            for r in recs:
                total += 1
                # Did MFE reach TP?
                if r['mfe_val'] >= tp:
                    sum_ret += tp - 0.15
                    wins += 1
                else:
                    # Time exit at b66
                    ret66 = r['exit_rets'].get(65, 0)
                    sum_ret += ret66
                    if ret66 > 0: wins += 1
            wr = wins/total*100
            ar = sum_ret/total
            out.write(f"  TP={tp:.1f}%: win={wr:.1f}%, avgRet={ar:+.3f}%, total={sum_ret:+.1f}%\n")

    print(f"P14 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
