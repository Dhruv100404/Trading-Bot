"""
DEEP CONFIRM-EXIT ANALYSIS
============================
The two-step approach:
  1. Entry at b6: score >= entry_thresh, cherry-pick top-N
  2. Confirm at b15: re-score, EXIT if score < confirm_thresh

Deeply analyze:
  - What exact confirm threshold maximizes win rate AND return?
  - What does the EXIT at b15 actually save vs cost?
  - Does confirm at b12 or b18 work better than b15?
  - Per-trade: what features separate confirm-pass from confirm-fail?
  - Position sizing: ADD to high-confirm, EXIT low-confirm
  - Full simulation with corrected P&L (exit uses actual b15 P&L)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_confirm_exit.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000
MARGIN = 5

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                if abs(r['gapPct']) > 10: continue
                f5v = r.get('f5Vol',0)*r['dayOpen']
                if f5v < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0: continue
                if bkt[0,H]==bkt[0,L]: continue

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'bkt':bkt,'date':r['date'],
                })

    dates = sorted(by_date.keys())
    print(f"Loaded {sum(len(v) for v in by_date.values())} records in {time.time()-t0:.1f}s")

    def score_at(bkt, scan_b, gap):
        if scan_b<2 or bkt[scan_b,C]<=0 or bkt[0,O]<=0: return None
        day_open = bkt[0,O]; current = bkt[scan_b,C]
        lb = min(scan_b, 6)
        cp=0; nr=0; nb_=0
        for i in range(max(scan_b-lb,0), scan_b+1):
            rng=bkt[i,H]-bkt[i,L]
            cp+=(bkt[i,C]-bkt[i,L])/rng if rng>0 else 0.5
            if bkt[i,C]<bkt[i,O]: nr+=1
            nb_+=1
        sp = 1-cp/max(nb_,1)
        lb_s = max(scan_b-5,0)
        mom = (bkt[scan_b,C]-bkt[lb_s,O])/bkt[lb_s,O]*100 if bkt[lb_s,O]>0 else 0
        vwap = (current-bkt[scan_b,VW])/bkt[scan_b,VW]*100 if bkt[scan_b,VW]>0 else 0
        # Trend
        pts = [bkt[i,C] for i in range(max(scan_b-5,0),scan_b+1) if bkt[i,C]>0]
        trend = 0
        if len(pts)>=3:
            x=np.arange(len(pts)); slope=np.polyfit(x,pts,1)[0]
            trend = -slope/current*100*10
        consec=0
        for i in range(scan_b, max(scan_b-10,0),-1):
            if bkt[i,C]<bkt[i,O]: consec+=1
            else: break
        gap_from_open = (current-day_open)/day_open*100

        sc=0.0
        if sp>0.6: sc+=3
        elif sp>0.5: sc+=1
        elif sp<0.4: sc-=2
        if mom<-0.5: sc+=3
        elif mom<-0.2: sc+=2
        elif mom<0: sc+=1
        elif mom>0.3: sc-=2
        if vwap<-0.3: sc+=2
        elif vwap<0: sc+=1
        elif vwap>0.3: sc-=2
        if gap>0.5 and gap_from_open<0: sc+=2
        elif gap>0.5 and gap_from_open>gap*0.8: sc-=1
        if trend>0.3: sc+=2
        elif trend>0.1: sc+=1
        elif trend<-0.1: sc-=1
        if consec>=3: sc+=2
        elif consec>=2: sc+=1
        avg_vol=np.mean([bkt[i,V] for i in range(max(scan_b-10,0),scan_b)]) if scan_b>1 else 1
        vs=bkt[scan_b,V]/max(avg_vol,1)
        if vs>2 and mom<0: sc+=1
        rr=nr/max(nb_,1)
        if rr>0.7: sc+=1
        elif rr<0.3: sc-=1
        if day_open<500: sc+=1
        return sc

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("DEEP CONFIRM-EXIT ANALYSIS\n")
        out.write(f"Days: {len(dates)}\n\n")

        # Pre-compute: for each day, score at b5 (entry) and at multiple confirm buckets
        daily_data = []
        for d in dates:
            stocks = by_date[d]
            day_trades = []
            for s in stocks:
                bkt = s['bkt']; entry = s['entry']
                sc_entry = score_at(bkt, 5, s['gap'])
                if sc_entry is None: continue
                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Scores at confirm checkpoints
                confirms = {}
                pnl_at = {}
                for cb in [8,9,10,11,12,13,14,15,17,20,25,29]:
                    confirms[cb] = score_at(bkt, cb, s['gap'])
                    pnl_at[cb] = (entry-bkt[cb,C])/entry*100-COST if bkt[cb,C]>0 else 0

                day_trades.append({
                    'sym':s['sym'],'gap':s['gap'],'price':s['price'],
                    'entry':entry,'sc_entry':sc_entry,'ret90':ret90,
                    'win':ret90>0,'confirms':confirms,'pnl_at':pnl_at,
                })
            daily_data.append((d, day_trades))

        # ═══════════════════════════════════════
        # 1. ENTRY SCORE THRESHOLD (no confirm)
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. ENTRY SCORE THRESHOLD — no confirm, top-8, exit b90\n"+"="*110+"\n")
        for et in [0, 5, 8, 10, 12, 15]:
            total=0; wins=0; dw=0; active=0; trades=0
            for d, pool in daily_data:
                qualified = [t for t in pool if t['sc_entry']>=et]
                qualified.sort(key=lambda x:-x['sc_entry'])
                picks = qualified[:8]
                if not picks: continue
                active+=1
                dr = sum(t['ret90'] for t in picks)
                for t in picks:
                    trades+=1; total+=t['ret90']
                    if t['ret90']>0: wins+=1
                if dr>0: dw+=1
            if trades<10: continue
            out.write(f"  Entry>={et:>3}: trades={trades:>4} win={wins/trades*100:.1f}% dayWin={dw/max(active,1)*100:.1f}% totalRet={total:+.1f}% days={active}\n")

        # ═══════════════════════════════════════
        # 2. CONFIRM BUCKET: which is best?
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. CONFIRM BUCKET OPTIMIZATION (entry>=8, confirm>=10, top-8)\n"+"="*110+"\n")
        out.write(f"  At entry (b6): pick top-8 with score>=8. At confirm bucket: exit if score<threshold\n\n")

        for confirm_b in [8,9,10,11,12,13,14,15,17,20,25,29]:
            h=9+(15+confirm_b)//60; m=(15+confirm_b)%60
            for ct in [5, 8, 10]:
                total=0; wins=0; dw=0; active=0; trades=0; exits=0
                for d, pool in daily_data:
                    qualified = [t for t in pool if t['sc_entry']>=8]
                    qualified.sort(key=lambda x:-x['sc_entry'])
                    picks = qualified[:8]
                    if not picks: continue
                    active+=1; dr=0
                    for t in picks:
                        trades+=1
                        cs = t['confirms'].get(confirm_b)
                        if cs is not None and cs < ct:
                            # EXIT at confirm bucket — actual P&L
                            ret = t['pnl_at'].get(confirm_b, 0)
                            exits+=1
                        else:
                            ret = t['ret90']
                        total+=ret
                        if ret>0: wins+=1
                        dr+=ret
                    if dr>0: dw+=1
                if trades<10: continue
                wr=wins/trades*100; dwp=dw/max(active,1)*100
                out.write(f"  Confirm@b{confirm_b+1}({h}:{m:02d}) thresh>={ct}: trades={trades:>4} exits={exits:>3} win={wr:.1f}% dayWin={dwp:.1f}% totalRet={total:+.1f}%\n")
            out.write("\n")

        # ═══════════════════════════════════════
        # 3. WHAT DOES THE EXIT SAVE?
        # ═══════════════════════════════════════
        out.write("="*110+"\n3. EXIT ANALYSIS: what happens to exited trades if we HAD held?\n"+"="*110+"\n")

        # Best confirm: b15, threshold 10
        confirm_b = 14; ct = 10
        exited_trades = []; held_trades = []
        for d, pool in daily_data:
            qualified = sorted([t for t in pool if t['sc_entry']>=8], key=lambda x:-x['sc_entry'])[:8]
            for t in qualified:
                cs = t['confirms'].get(confirm_b)
                if cs is not None and cs < ct:
                    exited_trades.append(t)
                else:
                    held_trades.append(t)

        out.write(f"\n  Confirm@b15, threshold>=10:\n")
        out.write(f"    Held trades: {len(held_trades)}, Exited: {len(exited_trades)}\n\n")
        if exited_trades:
            ex_actual = [t['pnl_at'][confirm_b] for t in exited_trades]
            ex_b90 = [t['ret90'] for t in exited_trades]
            out.write(f"    EXITED trades — if we exit at b15:\n")
            out.write(f"      Avg P&L at exit: {np.mean(ex_actual):+.3f}%\n")
            out.write(f"      Win rate at exit: {sum(1 for r in ex_actual if r>0)/len(ex_actual)*100:.1f}%\n")
            out.write(f"    EXITED trades — if we HAD held to b90:\n")
            out.write(f"      Avg P&L at b90: {np.mean(ex_b90):+.3f}%\n")
            out.write(f"      Win rate at b90: {sum(1 for r in ex_b90 if r>0)/len(ex_b90)*100:.1f}%\n")
            saved = sum(max(b90 - ex, 0) for ex, b90 in zip(ex_actual, ex_b90) if b90 < ex)
            hurt = sum(max(b90 - ex, 0) for ex, b90 in zip(ex_actual, ex_b90) if b90 > ex)
            out.write(f"      Trades where exit SAVED money: {sum(1 for ex,b90 in zip(ex_actual,ex_b90) if ex>b90)}\n")
            out.write(f"      Trades where exit HURT (would have recovered): {sum(1 for ex,b90 in zip(ex_actual,ex_b90) if b90>ex)}\n")

        if held_trades:
            out.write(f"\n    HELD trades (confirm passed):\n")
            out.write(f"      Avg P&L at b90: {np.mean([t['ret90'] for t in held_trades]):+.3f}%\n")
            out.write(f"      Win rate: {sum(t['win'] for t in held_trades)/len(held_trades)*100:.1f}%\n")

        # ═══════════════════════════════════════
        # 4. CONFIRM SCORE vs OUTCOME distribution
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. CONFIRM SCORE AT b15 vs OUTCOME (for entry>=8 top-8)\n"+"="*110+"\n")
        all_picks = []
        for d, pool in daily_data:
            qualified = sorted([t for t in pool if t['sc_entry']>=8], key=lambda x:-x['sc_entry'])[:8]
            all_picks.extend(qualified)

        out.write(f"  Total top-8 picks: {len(all_picks)}\n\n")
        out.write(f"  {'ConfirmScore':>13} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'Action':>10}\n  "+"-"*50+"\n")
        for slo,shi in [(-10,-3),(-3,0),(0,3),(3,5),(5,8),(8,10),(10,12),(12,15),(15,99)]:
            sub = [t for t in all_picks if t['confirms'].get(14) is not None and slo<=t['confirms'][14]<shi]
            if len(sub)<5: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            action = "EXIT" if wr<40 else "HOLD" if wr<60 else "ADD" if wr>70 else "HOLD"
            out.write(f"  {f'{slo} to {shi-1}':>13} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}% {action:>10}\n")

        # ═══════════════════════════════════════
        # 5. COMBINED: entry score + confirm score + sizing
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. FULL SIMULATION: entry + confirm + sizing (corrected P&L)\n"+"="*110+"\n")

        def full_sim(entry_thresh, confirm_b, confirm_exit_thresh, confirm_add_thresh, add_mult, n_pos=8):
            total_pnl=0; day_wins=0; active=0; trades=0; win_trades=0; exits=0; adds=0
            for d, pool in daily_data:
                qualified = sorted([t for t in pool if t['sc_entry']>=entry_thresh], key=lambda x:-x['sc_entry'])[:n_pos]
                if not qualified: continue
                active+=1; day_pnl=0
                for t in qualified:
                    trades+=1
                    cs = t['confirms'].get(confirm_b)
                    pnl_check = t['pnl_at'].get(confirm_b, 0)

                    if cs is not None and cs < confirm_exit_thresh:
                        # EXIT at confirm bucket
                        pnl_rs = BASE*MARGIN*pnl_check/100
                        exits+=1
                    elif cs is not None and cs >= confirm_add_thresh:
                        # ADD: 1x up to confirm + mult from confirm to b90
                        remaining = t['ret90'] - pnl_check
                        pnl_rs = BASE*MARGIN*(pnl_check + remaining*add_mult)/100
                        adds+=1
                    else:
                        pnl_rs = BASE*MARGIN*t['ret90']/100

                    day_pnl += pnl_rs
                    if pnl_rs>0: win_trades+=1

                total_pnl+=day_pnl
                if day_pnl>0: day_wins+=1

            roc = total_pnl/(BASE*n_pos)*100
            return roc, day_wins/max(active,1)*100, win_trades/max(trades,1)*100, trades, exits, adds, active

        out.write(f"  {'Strategy':<75} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'Exits':>5} {'Adds':>5}\n  "+"-"*110+"\n")

        configs = [
            # Baseline
            ("No confirm (baseline, entry>=8)", 8, 99, -99, 99, 1.0, 8),
            ("No confirm (entry>=10)", 10, 99, -99, 99, 1.0, 8),
            ("No confirm (entry>=12)", 12, 99, -99, 99, 1.0, 8),

            # Confirm EXIT only
            ("Entry>=8 + EXIT if confirm@b15<5", 8, 14, 5, 99, 1.0, 8),
            ("Entry>=8 + EXIT if confirm@b15<8", 8, 14, 8, 99, 1.0, 8),
            ("Entry>=8 + EXIT if confirm@b15<10", 8, 14, 10, 99, 1.0, 8),
            ("Entry>=8 + EXIT if confirm@b12<8", 8, 11, 8, 99, 1.0, 8),
            ("Entry>=8 + EXIT if confirm@b12<10", 8, 11, 10, 99, 1.0, 8),
            ("Entry>=8 + EXIT if confirm@b10<8", 8, 9, 8, 99, 1.0, 8),

            # Confirm EXIT + ADD
            ("Entry>=8 + EXIT<5 + ADD 2x>=10 @b15", 8, 14, 5, 10, 2.0, 8),
            ("Entry>=8 + EXIT<5 + ADD 3x>=10 @b15", 8, 14, 5, 10, 3.0, 8),
            ("Entry>=8 + EXIT<8 + ADD 2x>=12 @b15", 8, 14, 8, 12, 2.0, 8),
            ("Entry>=8 + EXIT<8 + ADD 3x>=12 @b15", 8, 14, 8, 12, 3.0, 8),
            ("Entry>=8 + EXIT<5 + ADD 2x>=8 @b15", 8, 14, 5, 8, 2.0, 8),
            ("Entry>=8 + EXIT<5 + ADD 3x>=8 @b15", 8, 14, 5, 8, 3.0, 8),

            # Earlier confirm
            ("Entry>=8 + EXIT<8 + ADD 2x>=10 @b12", 8, 11, 8, 10, 2.0, 8),
            ("Entry>=8 + EXIT<8 + ADD 3x>=10 @b12", 8, 11, 8, 10, 3.0, 8),

            # Later confirm
            ("Entry>=8 + EXIT<5 + ADD 2x>=10 @b20", 8, 19, 5, 10, 2.0, 8),
            ("Entry>=8 + EXIT<5 + ADD 3x>=10 @b20", 8, 19, 5, 10, 3.0, 8),

            # Higher entry + confirm
            ("Entry>=10 + EXIT<8 + ADD 2x>=10 @b15", 10, 14, 8, 10, 2.0, 8),
            ("Entry>=10 + EXIT<5 + ADD 3x>=12 @b15", 10, 14, 5, 12, 3.0, 8),
            ("Entry>=12 + EXIT<8 + ADD 2x>=10 @b15", 12, 14, 8, 10, 2.0, 8),

            # Position count
            ("Entry>=8 + EXIT<5 + ADD 2x>=10 @b15 top6", 8, 14, 5, 10, 2.0, 6),
            ("Entry>=8 + EXIT<5 + ADD 3x>=10 @b15 top6", 8, 14, 5, 10, 3.0, 6),
            ("Entry>=8 + EXIT<5 + ADD 2x>=10 @b15 top4", 8, 14, 5, 10, 2.0, 4),

            # ADD only (no exit)
            ("Entry>=8 + ADD 2x>=10 @b15 (no exit)", 8, 14, -99, 10, 2.0, 8),
            ("Entry>=8 + ADD 3x>=10 @b15 (no exit)", 8, 14, -99, 10, 3.0, 8),
            ("Entry>=8 + ADD 3x>=12 @b15 (no exit)", 8, 14, -99, 12, 3.0, 8),
        ]

        results = []
        for name, et, cb, cet, cat_, mult, npos in configs:
            roc, dw, tw, nt, exits, adds, active = full_sim(et, cb, cet, cat_, mult, npos)
            results.append((roc, name, dw, tw, nt, exits, adds, active))

        results.sort(key=lambda x:-x[0])
        for roc, name, dw, tw, nt, exits, adds, active in results:
            marker = " <<<" if 'baseline' in name else ""
            out.write(f"  {name:<75} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {exits:>5} {adds:>5}{marker}\n")

        # ═══════════════════════════════════════
        # 6. VERDICT
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. VERDICT\n"+"="*110+"\n")
        baseline = next(r for r in results if 'baseline' in r[1])
        best = results[0]
        out.write(f"\n  BASELINE:  {baseline[1]}\n    ROC={baseline[0]:+.1f}% DayWin={baseline[2]:.1f}% TrdWin={baseline[3]:.1f}%\n")
        out.write(f"\n  BEST:      {best[1]}\n    ROC={best[0]:+.1f}% DayWin={best[2]:.1f}% TrdWin={best[3]:.1f}%\n")
        out.write(f"\n  Improvement: {best[0]-baseline[0]:+.1f}% ROC\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
