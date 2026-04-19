"""
P2: Gap Size x Entry Bucket Heatmap
=====================================
For each (gap_range, entry_bucket) combo, compute win rate.
Find the OPTIMAL entry timing for each gap size.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p2_gap_bucket_heatmap.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']
    records = []
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.1: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                records.append({'gap':r['gapPct'],'bkt':bkt,'sym':r['symbol']})

    gap_ranges = [(0.1,1,'0.1-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,5,'3-5%'),(5,10,'5-10%'),(10,100,'10%+')]
    entry_buckets = [2,3,4,5,6,7,8,9,10,12,15]  # 0-indexed entry points
    exit_buckets = [30,45,60,65,75,89]

    with open(OUT,'w') as out:
        out.write(f"P2: GAP SIZE x ENTRY BUCKET x EXIT BUCKET HEATMAP\n")
        out.write(f"Records: {len(records)} gap-up days on liquid stocks\n\n")

        for ex_b in exit_buckets:
            ex_time = f"9:{15+ex_b}" if ex_b < 45 else f"10:{ex_b-45:02d}"
            out.write(f"\n{'='*120}\nEXIT at bucket {ex_b+1} (~{ex_time})\n{'='*120}\n")
            out.write(f"  {'Gap Range':<10}")
            for eb in entry_buckets:
                et = f"b{eb+1}"
                out.write(f" {et:>8}")
            out.write(f" {'(best)':>10}\n")
            out.write("  "+"-"*120+"\n")

            for glo,ghi,glabel in gap_ranges:
                out.write(f"  {glabel:<10}")
                best_wr = 0; best_eb = 0
                for eb in entry_buckets:
                    wins = 0; total = 0
                    for rec in records:
                        if not (glo <= rec['gap'] < ghi): continue
                        entry = rec['bkt'][eb, O]
                        exit_c = rec['bkt'][ex_b, C]
                        if entry <= 0 or exit_c <= 0 or eb >= ex_b: continue
                        ret = (entry - exit_c)/entry*100 - 0.15
                        total += 1
                        if ret > 0: wins += 1
                    wr = wins/total*100 if total > 10 else 0
                    if wr > best_wr: best_wr = wr; best_eb = eb
                    marker = "*" if total <= 10 else ""
                    out.write(f" {wr:>6.1f}%{marker}")
                out.write(f"   b{best_eb+1}={best_wr:.0f}%\n")

    print(f"P2 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
