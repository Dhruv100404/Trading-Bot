"""
P7: VWAP Deviation + Price Momentum
=====================================
How far is price from VWAP at entry? Does momentum in first 5 min predict?
"""
import json, numpy as np, time
from pathlib import Path

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p7_vwap_momentum.txt'
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
                b0o = bkt[0,O]
                b5c = bkt[5,C]; b5vw = bkt[5,VW]
                recs.append({
                    'gap':r['gapPct'],'ret':ret,
                    'vwap_dev': (b5c-b5vw)/b5vw*100 if b5vw>0 else 0,
                    'momentum': (b5c-b0o)/b0o*100 if b0o>0 else 0,
                    'entry_vs_open': (entry-b0o)/b0o*100 if b0o>0 else 0,
                    'gap_fill_pct': 0,  # computed below
                    'win': 1 if ret>0 else 0,
                })

    with open(OUT,'w') as out:
        out.write(f"P7: VWAP DEVIATION + MOMENTUM\n")
        out.write(f"Records: {len(recs)}\n\n")

        # VWAP deviation buckets
        out.write("VWAP DEVIATION AT ENTRY (bucket 5 close vs VWAP)\n")
        out.write("="*80+"\n")
        dev_bins = [(-99,-1),(-1,-0.5),(-0.5,-0.2),(-0.2,0),(0,0.2),(0.2,0.5),(0.5,1),(1,99)]
        out.write(f"  {'VwapDev':>12} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*40+"\n")
        for lo,hi in dev_bins:
            m = [r for r in recs if lo<=r['vwap_dev']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {lo:>+5.1f} to {hi:>+4.1f} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Momentum buckets
        out.write(f"\nMOMENTUM: close@b5 vs open@b0 (%)\n{'='*80}\n")
        mom_bins = [(-99,-2),(-2,-1),(-1,-0.5),(-0.5,0),(0,0.5),(0.5,1),(1,2),(2,99)]
        out.write(f"  {'Momentum':>12} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*40+"\n")
        for lo,hi in mom_bins:
            m = [r for r in recs if lo<=r['momentum']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {lo:>+5.1f} to {hi:>+4.1f} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Entry vs open
        out.write(f"\nENTRY vs OPEN: entry@b6 open vs day open (%)\n{'='*80}\n")
        evo_bins = [(-99,-2),(-2,-1),(-1,-0.5),(-0.5,0),(0,0.5),(0.5,1),(1,99)]
        out.write(f"  {'EntryVsOpen':>12} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*40+"\n")
        for lo,hi in evo_bins:
            m = [r for r in recs if lo<=r['entry_vs_open']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {lo:>+5.1f} to {hi:>+4.1f} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Cross: vwap_dev x momentum
        out.write(f"\nHEATMAP: vwap_dev x momentum -> win rate\n{'='*80}\n")
        v_bins = [(-99,-0.3,'<-0.3'),(-0.3,0,'-0.3~0'),(0,0.3,'0~0.3'),(0.3,99,'>0.3')]
        m_bins = [(-99,-1,'<-1%'),(-1,0,'-1~0%'),(0,1,'0~1%'),(1,99,'>1%')]
        out.write(f"  {'':>10}")
        for _,_,ml in m_bins: out.write(f" {ml:>12}")
        out.write("\n  "+"-"*65+"\n")
        for vlo,vhi,vl in v_bins:
            out.write(f"  {vl:>10}")
            for mlo,mhi,ml in m_bins:
                sub = [r for r in recs if vlo<=r['vwap_dev']<vhi and mlo<=r['momentum']<mhi]
                if len(sub)<15:
                    out.write(f"{'—':>12}")
                else:
                    wr = sum(r['win'] for r in sub)/len(sub)*100
                    out.write(f"  {wr:>5.1f}%({len(sub):>3})")
            out.write("\n")

    print(f"P7 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
