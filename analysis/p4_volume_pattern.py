"""
P4: Volume Pattern Analysis
=============================
Does volume in the first candle predict reversal strength?
Volume spike + gap = exhaustion or continuation?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p4_volume_pattern.txt'
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
                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_vol6 = sum(vol6)
                recs.append({
                    'gap':r['gapPct'],'ret':ret,'sym':r['symbol'],
                    'f5vol':r.get('f5Vol',0),'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                    'b0_vol':vol6[0], 'total_vol6':total_vol6,
                    'b0_vol_pct': vol6[0]/total_vol6*100 if total_vol6>0 else 0,
                    'vol_trend': (sum(vol6[3:])-sum(vol6[:3]))/max(total_vol6,1)*100,  # rising or falling vol
                    'vol_ratio': vol6[0]/np.mean(vol6[1:6]) if np.mean(vol6[1:6])>0 else 0,
                    'price': r['dayOpen'],
                    'b0_range': (bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'win': 1 if ret > 0 else 0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P4: VOLUME PATTERN ANALYSIS\n")
        out.write(f"Gap-up records (gap>0.5%): {len(recs)}\n\n")

        # Volume patterns
        pats = {
            'b0 has >40% of 6-bucket volume (spike open)':    lambda r: r['b0_vol_pct'] > 40,
            'b0 has >50% of 6-bucket volume':                  lambda r: r['b0_vol_pct'] > 50,
            'b0 has <20% of 6-bucket volume (quiet open)':     lambda r: r['b0_vol_pct'] < 20,
            'Volume falling (trend < -20%)':                   lambda r: r['vol_trend'] < -20,
            'Volume rising (trend > 20%)':                     lambda r: r['vol_trend'] > 20,
            'b0 vol > 3x avg(b1-b5) — massive spike':         lambda r: r['vol_ratio'] > 3,
            'b0 vol > 2x avg(b1-b5)':                         lambda r: r['vol_ratio'] > 2,
            'b0 vol < 0.5x avg(b1-b5) — no spike':            lambda r: r['vol_ratio'] < 0.5,
            'f5vol_rs > 50L (high liquidity)':                 lambda r: r['f5vol_rs'] > 5_000_000,
            'f5vol_rs > 100L':                                 lambda r: r['f5vol_rs'] > 10_000_000,
            'f5vol_rs 5-20L (mid liquidity)':                  lambda r: 500_000 <= r['f5vol_rs'] < 2_000_000,
            'b0_range > 3% + b0_vol spike':                    lambda r: r['b0_range']>3 and r['vol_ratio']>2,
            'b0_range > 3% + b0_vol normal':                   lambda r: r['b0_range']>3 and r['vol_ratio']<=2,
            'b0_range < 1% + high vol':                        lambda r: r['b0_range']<1 and r['vol_ratio']>2,
            'gap>2% + vol spike + b0_range>2%':                lambda r: r['gap']>2 and r['vol_ratio']>2 and r['b0_range']>2,
        }

        out.write(f"  {'Pattern':<55} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*80+"\n")
        rows = []
        for name, filt in pats.items():
            m = [r for r in recs if filt(r)]
            if len(m) < 20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            rows.append((ar,name,len(m),wr))
        rows.sort(key=lambda x: -x[0])
        for ar,name,n,wr in rows:
            out.write(f"  {name:<55} {n:>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Heatmap: vol_ratio x gap
        out.write(f"\n{'='*100}\nHEATMAP: b0_vol_ratio x gap -> win rate\n{'='*100}\n")
        vr_bins = [(0,0.5,'<0.5x'),(0.5,1,'0.5-1x'),(1,2,'1-2x'),(2,3,'2-3x'),(3,5,'3-5x'),(5,100,'>5x')]
        gap_bins = [(0.5,1),(1,2),(2,3),(3,5),(5,100)]
        out.write(f"  {'VolRatio':>10}")
        for glo,ghi in gap_bins:
            out.write(f" gap{glo}-{ghi}%".rjust(12))
        out.write("\n  "+"-"*75+"\n")
        for vlo,vhi,vlbl in vr_bins:
            out.write(f"  {vlbl:>10}")
            for glo,ghi in gap_bins:
                m = [r for r in recs if vlo<=r['vol_ratio']<vhi and glo<=r['gap']<ghi]
                if len(m)<10:
                    out.write(f"{'—':>12}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"  {wr:>5.1f}%({len(m):>3})")
            out.write("\n")

    print(f"P4 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
