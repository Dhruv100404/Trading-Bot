"""
P3: Buy Ratio Microstructure
==============================
Deep dive into the br (buy ratio) field across first 6 buckets.
Is there a specific BR PATTERN (e.g. declining BR) that predicts reversal?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p3_buy_ratio_pattern.txt'
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
                if entry <= 0 or bkt[65,C] <= 0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                br_seq = [float(bkt[i,BR]) for i in range(6)]
                recs.append({
                    'gap':r['gapPct'], 'ret':ret, 'sym':r['symbol'],
                    'br':br_seq, 'avg_br':np.mean(br_seq),
                    'br_trend': br_seq[5]-br_seq[0],  # declining BR = sellers growing
                    'br_min': min(br_seq), 'br_max': max(br_seq),
                    'all_low': all(b < 0.5 for b in br_seq),
                    'all_high': all(b >= 0.5 for b in br_seq),
                    'declining': br_seq[0]>br_seq[2]>br_seq[4],
                    'b0_br':br_seq[0], 'b5_br':br_seq[5],
                    'win': 1 if ret > 0 else 0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P3: BUY RATIO MICROSTRUCTURE PATTERNS\n")
        out.write(f"Gap-up records (gap>0.5%): {len(recs)}\n\n")

        # BR patterns
        patterns = {
            'All 6 buckets BR < 0.5 (sellers dominate entire window)': lambda r: r['all_low'],
            'All 6 buckets BR >= 0.5 (buyers dominate entire window)': lambda r: r['all_high'],
            'Declining BR (b0>b2>b4) — sellers growing':              lambda r: r['declining'],
            'b0_br < 0.3 (heavy selling at open)':                     lambda r: r['b0_br'] < 0.3,
            'b0_br < 0.4':                                             lambda r: r['b0_br'] < 0.4,
            'b0_br > 0.7 (heavy buying at open)':                      lambda r: r['b0_br'] > 0.7,
            'avg_br < 0.35':                                           lambda r: r['avg_br'] < 0.35,
            'avg_br < 0.45':                                           lambda r: r['avg_br'] < 0.45,
            'avg_br 0.45-0.55 (balanced)':                             lambda r: 0.45 <= r['avg_br'] < 0.55,
            'avg_br > 0.55':                                           lambda r: r['avg_br'] > 0.55,
            'avg_br > 0.65':                                           lambda r: r['avg_br'] > 0.65,
            'br_trend < -0.2 (strong seller growth)':                  lambda r: r['br_trend'] < -0.2,
            'br_trend < -0.1':                                         lambda r: r['br_trend'] < -0.1,
            'br_trend > 0.1 (buyer growth)':                           lambda r: r['br_trend'] > 0.1,
            'br_trend > 0.2':                                          lambda r: r['br_trend'] > 0.2,
            'br_min < 0.15 (at least 1 bucket heavy sell)':            lambda r: r['br_min'] < 0.15,
            'br_max > 0.85 (at least 1 bucket heavy buy)':             lambda r: r['br_max'] > 0.85,
            'b0_br<0.4 + avg_br<0.45':                                 lambda r: r['b0_br']<0.4 and r['avg_br']<0.45,
            'b0_br<0.4 + declining':                                   lambda r: r['b0_br']<0.4 and r['declining'],
            'all_low + declining':                                     lambda r: r['all_low'] and r['declining'],
        }

        out.write(f"  {'Pattern':<55} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*80+"\n")
        rows = []
        for name, filt in patterns.items():
            matching = [r for r in recs if filt(r)]
            if len(matching) < 20: continue
            wr = sum(r['win'] for r in matching)/len(matching)*100
            ar = np.mean([r['ret'] for r in matching])
            rows.append((ar, name, len(matching), wr))
        rows.sort(key=lambda x: -x[0])
        for ar,name,n,wr in rows:
            out.write(f"  {name:<55} {n:>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Heatmap: avg_br buckets x gap buckets
        out.write(f"\n{'='*100}\nHEATMAP: avg_br6 x gap_range -> win rate\n{'='*100}\n")
        br_bins = [(0,0.3),(0.3,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.7),(0.7,1.01)]
        gap_bins = [(0.5,1),(1,2),(2,3),(3,5),(5,100)]
        out.write(f"  {'':>12}")
        for glo,ghi in gap_bins:
            out.write(f" gap{glo}-{ghi}%".rjust(10))
        out.write("\n  "+"-"*70+"\n")
        for blo,bhi in br_bins:
            out.write(f"  br{blo:.1f}-{bhi:.1f}  ")
            for glo,ghi in gap_bins:
                m = [r for r in recs if blo<=r['avg_br']<bhi and glo<=r['gap']<ghi]
                if len(m) < 10:
                    out.write(f"{'—':>10}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"{wr:>8.1f}%({len(m)})")
            out.write("\n")

    print(f"P3 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
