"""
P17: MFE/MAE Risk-Reward Profile
==================================
For each feature combination, compute MFE (max favorable) vs MAE (max adverse).
Find trades where MFE >> MAE (low risk, high reward).
The EDGE RATIO (MFE/MAE) is more important than win rate alone.
A 45% win rate with 3:1 edge ratio beats 60% win rate with 1:1 edge.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p17_mfe_mae_ratio.txt'
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
                ret66 = (entry - bkt[65,C])/entry*100 - 0.15
                mfe = (entry - float(np.min(bkt[6:66,L])))/entry*100
                mae = (float(np.max(bkt[6:66,H])) - entry)/entry*100
                edge = mfe/max(mae,0.01)

                # MFE at different time windows
                mfe_20 = (entry - float(np.min(bkt[6:27,L])))/entry*100
                mfe_45 = (entry - float(np.min(bkt[6:51,L])))/entry*100
                mae_20 = (float(np.max(bkt[6:27,H])) - entry)/entry*100
                mae_45 = (float(np.max(bkt[6:51,H])) - entry)/entry*100

                recs.append({
                    'gap':r['gapPct'],'ret':ret66,'sym':r['symbol'],
                    'mfe':mfe,'mae':mae,'edge':edge,
                    'mfe_20':mfe_20,'mae_20':mae_20,'mfe_45':mfe_45,'mae_45':mae_45,
                    'avg_br6':float(np.mean(bkt[:6,BR])),
                    'b0_br':float(bkt[0,BR]),
                    'b0_range':(bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'n_red':sum(1 for i in range(6) if bkt[i,C]<bkt[i,O]),
                    'momentum':(bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'price':r['dayOpen'],
                    'f5range':r.get('f5Range',0),
                    'win':ret66>0,
                })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P17: MFE/MAE RISK-REWARD PROFILE\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Overall MFE/MAE distribution
        out.write(f"{'='*90}\nOVERALL MFE/MAE DISTRIBUTION\n{'='*90}\n")
        mfes = [r['mfe'] for r in recs]
        maes = [r['mae'] for r in recs]
        edges = [r['edge'] for r in recs]
        out.write(f"  MFE:  mean={np.mean(mfes):.3f}%  median={np.median(mfes):.3f}%  p75={np.percentile(mfes,75):.3f}%  p90={np.percentile(mfes,90):.3f}%\n")
        out.write(f"  MAE:  mean={np.mean(maes):.3f}%  median={np.median(maes):.3f}%  p75={np.percentile(maes,75):.3f}%  p90={np.percentile(maes,90):.3f}%\n")
        out.write(f"  Edge: mean={np.mean(edges):.3f}  median={np.median(edges):.3f}\n")
        out.write(f"  Win rate: {sum(r['win'] for r in recs)/len(recs)*100:.1f}%\n\n")

        # Edge ratio by feature
        out.write(f"{'='*90}\nEDGE RATIO (MFE/MAE) BY FEATURE BUCKETS\n{'='*90}\n")
        features_bins = {
            'gap': [(0.5,1),(1,2),(2,3),(3,5),(5,10),(10,100)],
            'avg_br6': [(0,0.3),(0.3,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.7),(0.7,1.01)],
            'b0_range': [(0,0.5),(0.5,1),(1,2),(2,3),(3,5),(5,100)],
            'n_red': [(0,1),(1,2),(2,3),(3,4),(4,5),(5,7)],
            'momentum': [(-99,-2),(-2,-1),(-1,0),(0,1),(1,2),(2,99)],
            'price': [(0,50),(50,100),(100,200),(200,500),(500,1000),(1000,99999)],
            'f5range': [(0,1),(1,2),(2,3),(3,5),(5,100)],
        }

        for feat, bins in features_bins.items():
            out.write(f"\n  {feat}:\n")
            out.write(f"    {'Range':>15} {'N':>5} {'AvgMFE':>7} {'AvgMAE':>7} {'Edge':>6} {'Win%':>6} {'AvgRet':>8}\n")
            out.write("    "+"-"*60+"\n")
            for lo,hi in bins:
                m = [r for r in recs if lo<=r[feat]<hi]
                if len(m)<20: continue
                am = np.mean([r['mfe'] for r in m])
                aa = np.mean([r['mae'] for r in m])
                ed = am/max(aa,0.01)
                wr = sum(r['win'] for r in m)/len(m)*100
                ar = np.mean([r['ret'] for r in m])
                marker = " <<<" if ed > 1.5 and wr > 55 else ""
                out.write(f"    {f'{lo}-{hi}':>15} {len(m):>5} {am:>6.3f}% {aa:>6.3f}% {ed:>5.2f}x {wr:>5.1f}% {ar:>+7.3f}%{marker}\n")

        # SWEET SPOTS: feature combos with edge > 1.5 AND win > 55%
        out.write(f"\n{'='*90}\nSWEET SPOTS: Combos with edge > 1.3 AND win > 55% AND n >= 50\n{'='*90}\n")
        sweet = []
        for glo,ghi in [(0.5,2),(2,3),(3,5),(5,100)]:
            for blo,bhi in [(0,0.4),(0.4,0.5),(0.5,0.6),(0.6,1.01)]:
                for nlo,nhi in [(0,2),(2,4),(4,7)]:
                    m = [r for r in recs if glo<=r['gap']<ghi and blo<=r['avg_br6']<bhi and nlo<=r['n_red']<nhi]
                    if len(m)<50: continue
                    am = np.mean([r['mfe'] for r in m])
                    aa = np.mean([r['mae'] for r in m])
                    ed = am/max(aa,0.01)
                    wr = sum(r['win'] for r in m)/len(m)*100
                    ar = np.mean([r['ret'] for r in m])
                    if ed > 1.3 and wr > 55:
                        sweet.append((ed, f"gap{glo}-{ghi}% br{blo}-{bhi} nred{nlo}-{nhi}", len(m), wr, ar, am, aa))

        sweet.sort(key=lambda x: -x[0])
        out.write(f"  {'Combo':<40} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'MFE':>7} {'MAE':>7} {'Edge':>6}\n")
        out.write("  "+"-"*80+"\n")
        for ed,name,n,wr,ar,am,aa in sweet[:30]:
            out.write(f"  {name:<40} {n:>5} {wr:>5.1f}% {ar:>+7.3f}% {am:>6.3f}% {aa:>6.3f}% {ed:>5.2f}x\n")

        # Early MFE: which trades show profit in first 20 minutes?
        out.write(f"\n{'='*90}\nEARLY MFE: Trades profitable within 20 min (MFE_20 > 0.5%)\n{'='*90}\n")
        early_prof = [r for r in recs if r['mfe_20'] > 0.5]
        late_prof = [r for r in recs if r['mfe_20'] <= 0.5 and r['mfe'] > 0.5]
        no_prof = [r for r in recs if r['mfe'] <= 0.5]
        out.write(f"  Early movers (MFE>0.5% in 20min): {len(early_prof)} ({len(early_prof)/len(recs)*100:.1f}%)\n")
        out.write(f"    -> Final win rate: {sum(r['win'] for r in early_prof)/max(len(early_prof),1)*100:.1f}%\n")
        out.write(f"    -> Avg final ret: {np.mean([r['ret'] for r in early_prof]):+.3f}%\n")
        out.write(f"  Late movers (MFE>0.5% only after 20min): {len(late_prof)} ({len(late_prof)/len(recs)*100:.1f}%)\n")
        out.write(f"    -> Final win rate: {sum(r['win'] for r in late_prof)/max(len(late_prof),1)*100:.1f}%\n")
        out.write(f"  No profit (MFE<0.5%): {len(no_prof)} ({len(no_prof)/len(recs)*100:.1f}%)\n")

        # What predicts early movers?
        out.write(f"\n  Features of EARLY MOVERS vs others:\n")
        others = [r for r in recs if r['mfe_20'] <= 0.5]
        for f in ['gap','avg_br6','b0_range','n_red','momentum','f5range']:
            ea = np.mean([r[f] for r in early_prof])
            oa = np.mean([r[f] for r in others])
            out.write(f"    {f:<15}: early={ea:.3f}  others={oa:.3f}  delta={ea-oa:+.3f}\n")

    print(f"P17 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
