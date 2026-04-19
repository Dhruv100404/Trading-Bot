"""
P6: Price Action Sequence — First 6 Buckets
=============================================
Pattern: sequence of UP/DOWN across first 6 candles (e.g. DDDUDD).
Which sequences predict the strongest reversal?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p6_price_sequence.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']
    seqs = defaultdict(list)
    seqs3 = defaultdict(list)
    total = 0
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
                total += 1

                # Build sequence: U=up, D=down for each of first 6 buckets
                seq = ''
                for i in range(6):
                    seq += 'U' if bkt[i,C] >= bkt[i,O] else 'D'
                seqs[seq].append(ret)
                seqs3[seq[:3]].append(ret)

    with open(OUT,'w') as out:
        out.write(f"P6: PRICE ACTION SEQUENCE (first 6 candles)\n")
        out.write(f"Total gap-up records: {total}\n")
        out.write(f"Unique 6-candle sequences: {len(seqs)}\n\n")

        # Top full sequences
        out.write(f"{'='*80}\n6-CANDLE SEQUENCES (min 20 occurrences)\n{'='*80}\n")
        out.write(f"  {'Sequence':<10} {'N':>6} {'Win%':>6} {'AvgRet':>8} {'Signal':>20}\n")
        out.write("  "+"-"*55+"\n")
        rows = []
        for seq, rets in seqs.items():
            if len(rets) < 20: continue
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            n_d = seq.count('D')
            rows.append((ar, seq, len(rets), wr, n_d))
        rows.sort(key=lambda x: -x[0])
        for ar,seq,n,wr,nd in rows:
            sig = "STRONG SELL" if wr>60 else "WEAK SELL" if wr>55 else "NEUTRAL" if wr>45 else "AVOID"
            out.write(f"  {seq:<10} {n:>6} {wr:>5.1f}% {ar:>+7.3f}% {sig:>20}\n")

        # Top 3-candle sequences
        out.write(f"\n{'='*80}\n3-CANDLE SEQUENCES (first 3 only, min 50 occurrences)\n{'='*80}\n")
        out.write(f"  {'Seq':>5} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*30+"\n")
        rows3 = []
        for seq, rets in seqs3.items():
            if len(rets) < 50: continue
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            rows3.append((ar, seq, len(rets), wr))
        rows3.sort(key=lambda x: -x[0])
        for ar,seq,n,wr in rows3:
            out.write(f"  {seq:>5} {n:>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

    print(f"P6 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
