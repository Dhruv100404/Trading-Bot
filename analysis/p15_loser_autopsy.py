"""
P15: Loser Autopsy — WHY do losing trades fail?
==================================================
For every losing trade in the top-8, dissect:
- Did the stock continue UP (momentum beat reversal)?
- Did it reverse then bounce back (stopped out by volatility)?
- Was it a gap continuation (institutional buying)?
- What features ALWAYS appear on losers but NEVER on winners?
Build a REJECT filter: if a stock has these features, SKIP IT.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p15_loser_autopsy.txt'
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
                if r['symbol'] not in liquid or r['gapPct'] <= 0.1: continue
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
                mae = (float(np.max(bkt[6:66,H])) - entry)/entry*100

                # How the loss unfolded:
                # Did price go UP continuously from entry (never dipped below)?
                ever_below_entry = any(bkt[b,L] < entry for b in range(7,66))
                # Max consecutive up buckets from entry
                consec_up = 0
                for b in range(7,66):
                    if bkt[b,C] >= bkt[b,O]: consec_up += 1
                    else: break

                by_date[r['date']].append({
                    'sym':r['symbol'], 'gap':r['gapPct'], 'ret':ret, 'mfe':mfe, 'mae':mae,
                    'avg_br6':float(np.mean(bkt[:6,BR])),
                    'b0_br':float(bkt[0,BR]),
                    'b0_ret':(bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'b0_range':(bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'n_red':sum(1 for i in range(6) if bkt[i,C]<bkt[i,O]),
                    'price':r['dayOpen'],
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                    'ever_below': ever_below_entry,
                    'consec_up': consec_up,
                    'momentum':(bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'win': ret > 0,
                })

    # Pick top-8 per day (by gap, current logic)
    all_top8 = []
    for date in sorted(by_date.keys()):
        day = sorted(by_date[date], key=lambda x: -x['gap'])[:8]
        all_top8.extend(day)

    winners = [r for r in all_top8 if r['win']]
    losers = [r for r in all_top8 if not r['win']]

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P15: LOSER AUTOPSY\n")
        out.write(f"Top-8 picks (current scoring): {len(all_top8)} trades, {len(winners)} wins, {len(losers)} losses\n")
        out.write(f"Win rate: {len(winners)/len(all_top8)*100:.1f}%\n\n")

        # Loss classification
        out.write(f"{'='*90}\nHOW DO LOSERS FAIL?\n{'='*90}\n")
        # Type 1: Never dipped below entry (pure continuation up)
        type1 = [r for r in losers if not r['ever_below']]
        # Type 2: Dipped below but bounced back (volatility whipsaw)
        type2 = [r for r in losers if r['ever_below'] and r['mfe'] > 0.3]
        # Type 3: Tiny reversal, then continued up
        type3 = [r for r in losers if r['ever_below'] and r['mfe'] <= 0.3]

        out.write(f"  Type 1: PURE CONTINUATION (never went below entry): {len(type1)} ({len(type1)/max(len(losers),1)*100:.0f}%)\n")
        out.write(f"    -> Stock gapped up and kept going. Reversal never started.\n")
        out.write(f"    Avg gap: {np.mean([r['gap'] for r in type1]):.2f}%, Avg loss: {np.mean([r['ret'] for r in type1]):+.3f}%\n")
        out.write(f"    Avg avg_br6: {np.mean([r['avg_br6'] for r in type1]):.3f}\n\n")

        out.write(f"  Type 2: REVERSED THEN BOUNCED (MFE>0.3% but closed up): {len(type2)} ({len(type2)/max(len(losers),1)*100:.0f}%)\n")
        out.write(f"    -> Stock started reversing but bounced back. Volatility killed the trade.\n")
        out.write(f"    Avg MFE: {np.mean([r['mfe'] for r in type2]):+.3f}%, Avg loss: {np.mean([r['ret'] for r in type2]):+.3f}%\n\n")

        out.write(f"  Type 3: TINY DIP THEN UP (MFE<=0.3%): {len(type3)} ({len(type3)/max(len(losers),1)*100:.0f}%)\n")
        out.write(f"    -> Stock barely dipped, then continued up strongly.\n")
        out.write(f"    Avg loss: {np.mean([r['ret'] for r in type3]):+.3f}%\n\n")

        # Feature comparison: Losers by type vs Winners
        out.write(f"{'='*90}\nFEATURE COMPARISON: Winners vs Each Loser Type\n{'='*90}\n")
        feats = ['gap','price','avg_br6','b0_br','b0_ret','b0_range','n_red','momentum','f5vol_rs']
        out.write(f"  {'Feature':<15} {'Winners':>10} {'Type1(cont)':>12} {'Type2(bounce)':>14} {'Type3(tiny)':>12}\n")
        out.write("  "+"-"*65+"\n")
        for f in feats:
            wv = np.mean([r[f] for r in winners])
            t1v = np.mean([r[f] for r in type1]) if type1 else 0
            t2v = np.mean([r[f] for r in type2]) if type2 else 0
            t3v = np.mean([r[f] for r in type3]) if type3 else 0
            out.write(f"  {f:<15} {wv:>10.3f} {t1v:>12.3f} {t2v:>14.3f} {t3v:>12.3f}\n")

        # REJECT FILTERS: features that predict losers
        out.write(f"\n{'='*90}\nREJECT FILTERS: Skip stocks matching these conditions\n{'='*90}\n")
        reject_filters = {
            'R1: avg_br6 > 0.55':           lambda r: r['avg_br6'] > 0.55,
            'R2: avg_br6 > 0.60':           lambda r: r['avg_br6'] > 0.60,
            'R3: b0_br > 0.70':              lambda r: r['b0_br'] > 0.70,
            'R4: n_red == 0 (all green)':    lambda r: r['n_red'] == 0,
            'R5: n_red <= 1':                lambda r: r['n_red'] <= 1,
            'R6: momentum > 0.5% (still going up)': lambda r: r['momentum'] > 0.5,
            'R7: momentum > 0 (any upward)':  lambda r: r['momentum'] > 0,
            'R8: price > 2000':               lambda r: r['price'] > 2000,
            'R9: gap > 10% (extreme)':        lambda r: r['gap'] > 10,
            'R10: gap > 20%':                 lambda r: r['gap'] > 20,
            'R11: b0_range < 0.5% (no vol)':  lambda r: r['b0_range'] < 0.5,
            'R12: avg_br6>0.55 + momentum>0': lambda r: r['avg_br6']>0.55 and r['momentum']>0,
            'R13: n_red<=1 + avg_br6>0.50':   lambda r: r['n_red']<=1 and r['avg_br6']>0.50,
            'R14: gap>10% + avg_br6>0.50':    lambda r: r['gap']>10 and r['avg_br6']>0.50,
        }

        out.write(f"  {'Reject Rule':<45} {'Rejected':>8} {'RejWin%':>8} {'KeptWin%':>8} {'Improvement':>12}\n")
        out.write("  "+"-"*85+"\n")
        base_wr = len(winners)/len(all_top8)*100
        for name, filt in reject_filters.items():
            rejected = [r for r in all_top8 if filt(r)]
            kept = [r for r in all_top8 if not filt(r)]
            if len(rejected) < 10 or len(kept) < 100: continue
            rej_wr = sum(1 for r in rejected if r['win'])/len(rejected)*100
            kept_wr = sum(1 for r in kept if r['win'])/len(kept)*100
            imp = kept_wr - base_wr
            out.write(f"  {name:<45} {len(rejected):>8} {rej_wr:>7.1f}% {kept_wr:>7.1f}% {imp:>+10.1f}pp\n")

        # BEST REJECT COMBOS
        out.write(f"\n{'='*90}\nBEST REJECT COMBOS (apply multiple reject rules)\n{'='*90}\n")
        combos = [
            ('R9+R1: gap<10% + avg_br6<0.55',      lambda r: r['gap']<=10 and r['avg_br6']<=0.55),
            ('R10+R2: gap<20% + avg_br6<0.60',     lambda r: r['gap']<=20 and r['avg_br6']<=0.60),
            ('R5+R1: n_red>=2 + avg_br6<0.55',     lambda r: r['n_red']>=2 and r['avg_br6']<=0.55),
            ('R9+R7: gap<10% + momentum<=0',        lambda r: r['gap']<=10 and r['momentum']<=0),
            ('R9+R1+R7: gap<10% + br<0.55 + mom<=0', lambda r: r['gap']<=10 and r['avg_br6']<=0.55 and r['momentum']<=0),
            ('R10+R5: gap<20% + n_red>=2',          lambda r: r['gap']<=20 and r['n_red']>=2),
        ]
        out.write(f"  {'Combo':<50} {'Kept':>6} {'KeptWin%':>9} {'KeptAvgRet':>11}\n")
        out.write("  "+"-"*80+"\n")
        for name, filt in combos:
            kept = [r for r in all_top8 if filt(r)]
            if len(kept) < 50: continue
            kwr = sum(1 for r in kept if r['win'])/len(kept)*100
            kar = np.mean([r['ret'] for r in kept])
            out.write(f"  {name:<50} {len(kept):>6} {kwr:>8.1f}% {kar:>+10.3f}%\n")

    print(f"P15 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
