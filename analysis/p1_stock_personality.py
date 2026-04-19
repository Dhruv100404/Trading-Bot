"""
P1: Stock Personality Profiling
================================
Which stocks ALWAYS reverse vs NEVER reverse?
Build a per-stock win rate profile across 78 days.
Find "reliable reversers" — stocks that reverse 70%+ of the time.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p1_stock_personality.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']
    stock = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                if r['gapPct'] <= 0.1: continue
                entry = bkt[6,O]
                if entry <= 0: continue
                exit66 = bkt[65,C]
                if exit66 <= 0: continue
                ret = (entry - exit66)/entry*100 - 0.15
                mfe = (entry - float(np.min(bkt[6:66,L])))/entry*100
                mae = (float(np.max(bkt[6:66,H])) - entry)/entry*100
                stock[r['symbol']].append({
                    'date':r['date'],'gap':r['gapPct'],'ret':ret,'mfe':mfe,'mae':mae,
                    'avg_br6':float(np.mean(bkt[:6,BR])),
                    'b0_range':(bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0,
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                })

    with open(OUT,'w') as out:
        out.write(f"P1: STOCK PERSONALITY (which stocks always reverse?)\n")
        out.write(f"Universe: {len(liquid)} liquid stocks, gap-up days only\n\n")

        results = []
        for sym, trades in stock.items():
            if len(trades) < 8: continue
            wins = sum(1 for t in trades if t['ret']>0)
            wr = wins/len(trades)*100
            avg_ret = np.mean([t['ret'] for t in trades])
            avg_mfe = np.mean([t['mfe'] for t in trades])
            avg_mae = np.mean([t['mae'] for t in trades])
            avg_gap = np.mean([t['gap'] for t in trades])
            avg_vol = np.mean([t['f5vol_rs'] for t in trades])
            consistency = np.std([t['ret'] for t in trades])
            results.append((wr, sym, len(trades), avg_ret, avg_mfe, avg_mae, avg_gap, avg_vol, consistency))

        # RELIABLE REVERSERS (win rate >= 65%)
        results.sort(key=lambda x: (-x[0], -x[3]))
        out.write("="*110+"\n")
        out.write("RELIABLE REVERSERS: stocks with >= 65% sell win rate (8+ gap-up days)\n")
        out.write("="*110+"\n")
        out.write(f"  {'Symbol':<15} {'Trades':>6} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8} {'AvgMAE':>8} {'AvgGap':>7} {'AvgVol':>10} {'StdRet':>7}\n")
        out.write("  "+"-"*85+"\n")
        reliable = [r for r in results if r[0] >= 65]
        for wr,sym,n,ar,mfe,mae,ag,av,std in reliable:
            vs = f"{av/100000:.0f}L"
            out.write(f"  {sym:<15} {n:>6} {wr:>5.1f}% {ar:>+7.3f}% {mfe:>+7.3f}% {mae:>+7.3f}% {ag:>6.2f}% {vs:>10} {std:>6.2f}\n")
        out.write(f"\n  Total reliable: {len(reliable)} stocks\n")

        # AVOID LIST (win rate < 40%)
        avoid = [r for r in results if r[0] < 40]
        avoid.sort(key=lambda x: x[0])
        out.write(f"\n{'='*110}\nAVOID LIST: stocks with < 40% sell win rate\n{'='*110}\n")
        out.write(f"  {'Symbol':<15} {'Trades':>6} {'Win%':>6} {'AvgRet':>8} {'AvgGap':>7}\n")
        out.write("  "+"-"*50+"\n")
        for wr,sym,n,ar,mfe,mae,ag,av,std in avoid:
            out.write(f"  {sym:<15} {n:>6} {wr:>5.1f}% {ar:>+7.3f}% {ag:>6.2f}%\n")
        out.write(f"\n  Total avoid: {len(avoid)} stocks\n")

        # ALL stocks sorted by win rate
        out.write(f"\n{'='*110}\nALL STOCKS BY WIN RATE (8+ trades)\n{'='*110}\n")
        out.write(f"  {'Symbol':<15} {'N':>4} {'Win%':>6} {'AvgRet':>8} {'MFE':>7} {'MAE':>7} {'Gap':>6} {'Vol':>8}\n")
        out.write("  "+"-"*70+"\n")
        for wr,sym,n,ar,mfe,mae,ag,av,std in results:
            vs = f"{av/100000:.0f}L"
            out.write(f"  {sym:<15} {n:>4} {wr:>5.1f}% {ar:>+7.3f}% {mfe:>+6.2f}% {mae:>+6.2f}% {ag:>5.1f}% {vs:>8}\n")

    print(f"P1 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
