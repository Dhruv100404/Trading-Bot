"""
P13: Consecutive Gap Patterns
================================
If a stock gapped up YESTERDAY too, is today's reversal stronger or weaker?
Multi-day momentum vs exhaustion.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p13_consecutive_gaps.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    # Build per-stock date-sorted gap history
    stock_days = defaultdict(list)  # sym -> [(date, gap, ret)]
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
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0:
                    stock_days[r['symbol']].append((r['date'], r['gapPct'], None, None))
                    continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15
                avg_br = float(np.mean(bkt[:6,BR]))
                stock_days[r['symbol']].append((r['date'], r['gapPct'], ret, avg_br))

    # Sort each stock's days chronologically
    for sym in stock_days:
        stock_days[sym].sort(key=lambda x: x[0])

    # Analyze: for each gap-up day, look at previous day's gap
    recs = []
    for sym, days in stock_days.items():
        for i in range(1, len(days)):
            date, gap, ret, avg_br = days[i]
            prev_date, prev_gap, prev_ret, _ = days[i-1]
            if gap <= 0.5 or ret is None: continue

            # Count consecutive gap-up days
            consec = 1
            for j in range(i-1, -1, -1):
                if days[j][1] > 0.1:
                    consec += 1
                else:
                    break

            recs.append({
                'sym':sym, 'date':date, 'gap':gap, 'ret':ret,
                'prev_gap': prev_gap,
                'prev_ret': prev_ret if prev_ret is not None else 0,
                'prev_was_gap_up': prev_gap > 0.1,
                'prev_was_gap_down': prev_gap < -0.1,
                'consec_gap_up': min(consec, 5),
                'gap_change': gap - prev_gap,  # is today's gap bigger or smaller?
                'avg_br': avg_br if avg_br is not None else 0.5,
                'win': 1 if ret > 0 else 0,
            })

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"P13: CONSECUTIVE GAP PATTERNS\n")
        out.write(f"Records: {len(recs)}\n\n")

        # Previous day gap direction
        out.write(f"{'='*90}\nPREVIOUS DAY GAP DIRECTION -> TODAY'S REVERSAL\n{'='*90}\n")
        pats = {
            'Prev day: gap UP (>0.1%)':      lambda r: r['prev_was_gap_up'],
            'Prev day: gap DOWN (<-0.1%)':    lambda r: r['prev_was_gap_down'],
            'Prev day: flat gap':              lambda r: not r['prev_was_gap_up'] and not r['prev_was_gap_down'],
            'Prev day: gap UP + today gap UP (consecutive)': lambda r: r['prev_was_gap_up'] and r['gap']>0.5,
            'Prev day reversed successfully (ret>0)': lambda r: r['prev_ret'] is not None and r['prev_ret']>0,
            'Prev day reversal FAILED (ret<0)': lambda r: r['prev_ret'] is not None and r['prev_ret']<0,
        }
        out.write(f"  {'Pattern':<55} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*80+"\n")
        for name, filt in pats.items():
            m = [r for r in recs if filt(r)]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {name:<55} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Consecutive gap-up days
        out.write(f"\n{'='*90}\nCONSECUTIVE GAP-UP DAYS -> EXHAUSTION?\n{'='*90}\n")
        out.write(f"  {'ConsecDays':>12} {'N':>6} {'Win%':>6} {'AvgRet':>8} {'AvgGap':>8}\n")
        out.write("  "+"-"*45+"\n")
        for c in range(1, 6):
            m = [r for r in recs if r['consec_gap_up'] == c]
            if len(m) < 20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            ag = np.mean([r['gap'] for r in m])
            out.write(f"  {c:>12} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}% {ag:>7.2f}%\n")

        # Gap change (today bigger or smaller than yesterday)
        out.write(f"\n{'='*90}\nGAP CHANGE (today's gap vs yesterday's gap)\n{'='*90}\n")
        gc_bins = [(-99,-2,'shrunk >2%'),(-2,-1,'shrunk 1-2%'),(-1,0,'shrunk 0-1%'),
                   (0,1,'grew 0-1%'),(1,2,'grew 1-2%'),(2,99,'grew >2%')]
        out.write(f"  {'GapChange':>15} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*40+"\n")
        for lo,hi,lbl in gc_bins:
            m = [r for r in recs if lo<=r['gap_change']<hi]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            out.write(f"  {lbl:>15} {len(m):>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Cross: consecutive x avg_br6
        out.write(f"\n{'='*90}\nHEATMAP: consecutive_gap_up x avg_br6 -> win rate\n{'='*90}\n")
        br_bins = [(0,0.45,'br<0.45'),(0.45,0.55,'br 0.45-0.55'),(0.55,1.01,'br>0.55')]
        out.write(f"  {'Consec':>8}")
        for _,_,bl in br_bins: out.write(f" {bl:>14}")
        out.write("\n  "+"-"*55+"\n")
        for c in range(1,5):
            out.write(f"  {c:>8}")
            for blo,bhi,_ in br_bins:
                m = [r for r in recs if r['consec_gap_up']==c and blo<=r['avg_br']<bhi]
                if len(m)<10:
                    out.write(f"{'--':>14}")
                else:
                    wr = sum(r['win'] for r in m)/len(m)*100
                    out.write(f"  {wr:>5.1f}%({len(m):>4})")
            out.write("\n")

    print(f"P13 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
