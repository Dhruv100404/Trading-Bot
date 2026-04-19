"""
P11: Gap Fill Micro-Timing
============================
Minute-by-minute: HOW does the reversal unfold?
At which exact bucket does the gap start filling?
Does early fill (b1-b3) predict full reversal vs late fill?
What % of gap gets filled by each time point?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p11_gap_fill_timing.txt'
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
                day_open = bkt[0,O]
                if day_open <= 0: continue
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0: continue
                ret66 = (entry - bkt[65,C])/entry*100 - 0.15

                # Gap fill analysis: how far below day_open does price go by each bucket?
                # gap_fill_pct[b] = (day_open - min_low_b0_to_b) / day_open * 100
                # If positive = price went below open (gap filling)
                gap_pct = r['gapPct']

                # Track min low at each bucket milestone
                milestones = {}
                running_min = bkt[0,L]
                for b in range(91):
                    running_min = min(running_min, bkt[b,L])
                    if b in [0,1,2,3,4,5,6,10,15,20,30,45,60,65,75,89]:
                        # How much of the GAP has been filled?
                        # gap = day_open - prev_close (approx: day_open / (1 + gap_pct/100))
                        prev_close_approx = day_open / (1 + gap_pct/100)
                        gap_size = day_open - prev_close_approx
                        if gap_size > 0:
                            filled = day_open - running_min
                            fill_pct = filled / gap_size * 100
                        else:
                            fill_pct = 0
                        milestones[b] = fill_pct

                # First bucket where price drops below day_open (gap starts filling)
                first_fill_bucket = None
                for b in range(91):
                    if bkt[b,L] < day_open:
                        first_fill_bucket = b
                        break

                # How fast does the drop happen? (steepness)
                drop_b0_to_b5 = (day_open - min(bkt[i,L] for i in range(6))) / day_open * 100

                recs.append({
                    'gap':gap_pct, 'ret66':ret66, 'sym':r['symbol'],
                    'milestones': milestones,
                    'first_fill': first_fill_bucket,
                    'drop_0_5': drop_b0_to_b5,
                    'fill_at_b5': milestones.get(5, 0),
                    'fill_at_b6': milestones.get(6, 0),
                    'fill_at_b20': milestones.get(20, 0),
                    'fill_at_b65': milestones.get(65, 0),
                    'avg_br6': float(np.mean(bkt[:6,BR])),
                    'win': 1 if ret66>0 else 0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P11: GAP FILL MICRO-TIMING\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Average gap fill % at each milestone
        out.write(f"{'='*90}\nAVERAGE GAP FILL % AT EACH TIME POINT\n{'='*90}\n")
        bkts_show = [0,1,2,3,4,5,6,10,15,20,30,45,60,65,75,89]
        out.write(f"  {'Bucket':>8} {'Time':>8} {'AvgFill%':>9} {'Median':>8} {'p25':>6} {'p75':>6}\n")
        out.write("  "+"-"*50+"\n")
        for b in bkts_show:
            vals = [r['milestones'].get(b,0) for r in recs]
            out.write(f"  b{b+1:>6} {f'9:{15+b}':>8} {np.mean(vals):>8.1f}% {np.median(vals):>7.1f}% {np.percentile(vals,25):>5.0f}% {np.percentile(vals,75):>5.0f}%\n")

        # First fill bucket distribution
        out.write(f"\n{'='*90}\nFIRST BUCKET WHERE PRICE DROPS BELOW DAY OPEN (gap starts filling)\n{'='*90}\n")
        fill_dist = defaultdict(int)
        no_fill = 0
        for r in recs:
            if r['first_fill'] is None:
                no_fill += 1
            else:
                fill_dist[r['first_fill']] += 1
        out.write(f"  Never fills (price stays above open): {no_fill} ({no_fill/len(recs)*100:.1f}%)\n")
        out.write(f"  {'Bucket':>8} {'Count':>6} {'%':>6} {'Cumul%':>7}\n")
        cumul = 0
        for b in sorted(fill_dist.keys()):
            if b > 30: break
            cumul += fill_dist[b]
            out.write(f"  b{b+1:>6} {fill_dist[b]:>6} {fill_dist[b]/len(recs)*100:>5.1f}% {cumul/len(recs)*100:>6.1f}%\n")

        # KEY: does EARLY fill predict full reversal?
        out.write(f"\n{'='*90}\nEARLY GAP FILL -> DOES IT PREDICT FULL REVERSAL?\n{'='*90}\n")
        out.write(f"  If gap starts filling in bucket 0 (9:15), does it keep going?\n\n")
        fill_bins = [(0,0,'b1 (9:15)'),(1,1,'b2 (9:16)'),(2,2,'b3 (9:17)'),
                     (3,5,'b4-b6'),(6,10,'b7-b11'),(11,30,'b12-b31'),(None,None,'Never fills')]
        out.write(f"  {'First Fill':>15} {'N':>6} {'Win%':>6} {'AvgRet':>8} {'AvgFill@b66':>12}\n")
        out.write("  "+"-"*55+"\n")
        for blo, bhi, label in fill_bins:
            if blo is None:
                m = [r for r in recs if r['first_fill'] is None]
            else:
                m = [r for r in recs if r['first_fill'] is not None and blo <= r['first_fill'] <= bhi]
            if len(m) < 15: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret66'] for r in m])
            af = np.mean([r['fill_at_b65'] for r in m])
            out.write(f"  {label:>15} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}% {af:>10.1f}%\n")

        # Fill at b5 (pre-entry) vs outcome
        out.write(f"\n{'='*90}\nGAP FILL % AT BUCKET 5 (9:20 AM, pre-entry) -> OUTCOME\n{'='*90}\n")
        fill5_bins = [(0,0,'0% (no fill)'),(0.01,25,'1-25%'),(25,50,'25-50%'),(50,75,'50-75%'),
                      (75,100,'75-100%'),(100,999,'>100% (overshoot)')]
        out.write(f"  {'Fill@b5':>15} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*40+"\n")
        for flo, fhi, label in fill5_bins:
            m = [r for r in recs if flo <= r['fill_at_b5'] < fhi]
            if len(m)<15: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret66'] for r in m])
            out.write(f"  {label:>15} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Drop speed x gap size
        out.write(f"\n{'='*90}\nDROP SPEED (first 5 min) x GAP SIZE -> WIN RATE\n{'='*90}\n")
        drop_bins = [(0,0.5,'<0.5%'),(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,99,'>3%')]
        gap_bins = [(0.5,1),(1,2),(2,3),(3,5),(5,100)]
        out.write(f"  {'Drop':>8}")
        for glo,ghi in gap_bins: out.write(f" gap{glo}-{ghi}%".rjust(12))
        out.write("\n  "+"-"*75+"\n")
        for dlo,dhi,dlbl in drop_bins:
            out.write(f"  {dlbl:>8}")
            for glo,ghi in gap_bins:
                m = [r for r in recs if dlo<=r['drop_0_5']<dhi and glo<=r['gap']<ghi]
                if len(m)<10:
                    out.write(f"{'--':>12}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"  {wr:>5.1f}%({len(m):>3})")
            out.write("\n")

    print(f"P11 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
