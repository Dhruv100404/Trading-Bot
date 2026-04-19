"""
P8: Price Level & Market Cap Proxy
====================================
Do cheap stocks (< Rs 50) behave differently from expensive ones (> Rs 1000)?
"""
import json, numpy as np, time
from pathlib import Path

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p8_price_level.txt'
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
                if entry<=0 or bkt[65,C]<=0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                mfe = (entry - float(np.min(bkt[6:66,L])))/entry*100
                recs.append({
                    'gap':r['gapPct'],'ret':ret,'price':r['dayOpen'],
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                    'mfe':mfe,'sym':r['symbol'],
                    'win': 1 if ret>0 else 0,
                })

    with open(OUT,'w') as out:
        out.write(f"P8: PRICE LEVEL ANALYSIS\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Price bins
        price_bins = [(0,20,'<20'),(20,50,'20-50'),(50,100,'50-100'),(100,200,'100-200'),
                      (200,500,'200-500'),(500,1000,'500-1k'),(1000,2000,'1k-2k'),
                      (2000,5000,'2k-5k'),(5000,99999,'>5k')]

        out.write(f"  {'Price':>10} {'N':>6} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8} {'AvgGap':>7}\n")
        out.write("  "+"-"*55+"\n")
        for plo,phi,plbl in price_bins:
            m = [r for r in recs if plo<=r['price']<phi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            mfe = np.mean([r['mfe'] for r in m])
            ag = np.mean([r['gap'] for r in m])
            out.write(f"  {plbl:>10} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}% {mfe:>+7.3f}% {ag:>6.2f}%\n")

        # Cross: price x gap
        out.write(f"\nHEATMAP: price x gap -> win rate\n{'='*80}\n")
        gap_bins = [(0.5,1),(1,2),(2,3),(3,5),(5,100)]
        out.write(f"  {'Price':>10}")
        for glo,ghi in gap_bins: out.write(f" gap{glo}-{ghi}%".rjust(12))
        out.write("\n  "+"-"*75+"\n")
        for plo,phi,plbl in price_bins:
            out.write(f"  {plbl:>10}")
            for glo,ghi in gap_bins:
                m = [r for r in recs if plo<=r['price']<phi and glo<=r['gap']<ghi]
                if len(m)<10:
                    out.write(f"{'--':>12}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"  {wr:>5.1f}%({len(m):>3})")
            out.write("\n")

        # Best price band
        out.write(f"\nSWEET SPOT: which price band + gap range has highest win rate?\n{'='*80}\n")
        combos = []
        for plo,phi,plbl in price_bins:
            for glo,ghi in gap_bins:
                m = [r for r in recs if plo<=r['price']<phi and glo<=r['gap']<ghi]
                if len(m)<15: continue
                wr = sum(r['win'] for r in m)/len(m)*100
                ar = np.mean([r['ret'] for r in m])
                combos.append((wr,plbl,f"{glo}-{ghi}%",len(m),ar))
        combos.sort(key=lambda x: -x[0])
        out.write(f"  {'Price':>10} {'Gap':>10} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*45+"\n")
        for wr,pl,gl,n,ar in combos[:20]:
            out.write(f"  {pl:>10} {gl:>10} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

    print(f"P8 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
