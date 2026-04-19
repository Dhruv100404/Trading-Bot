"""
OVERNIGHT PATTERN HUNT — Find 90% green days
==============================================
V2 scorer + position sizing is the current best.
This script exhaustively tests every combination to maximize green day rate.

STRICT RULES:
  - NO lookahead (all features from data BEFORE entry/decision)
  - CORRECTED P&L (exits use actual P&L at exit point)
  - ADD: remaining return from check to b90 multiplied by mult + 1x up to check
  - Bug check: verify results make sense (no impossible numbers)

Tests:
  1. V2 scorer vs S6 vs S5 vs plain (confirm which is actually best on snapshot data)
  2. Position sizing: check bucket sweep (b10 to b30)
  3. ADD multiplier sweep (1.5x to 5x)
  4. EXIT threshold sweep
  5. Position count (4 to 10)
  6. Combined: scorer x sizing x positions
  7. Capital calculation for sizing
  8. Path to 90% green days
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'overnight_hunt.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000; MARGIN = 5

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
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0 or bkt[0,H]==bkt[0,L]: continue

                # All scores
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                price = r['dayOpen']
                gap = r['gapPct']

                scores = {
                    'plain': gap * 10,
                    'v2': gap * sp * (1.4 if mom<-0.5 else 1.1 if mom<0 else 0.7) * 15,
                    's5': gap * (1.0 if sp>0.5 else 0.3) * (1.3 if mom<0 else 0.7) * 10,
                    's6': gap * (1.0 if sp>0.5 else 0.3) * (1.2 if price<500 else 0.9) * 10,
                }

                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Live data at multiple check points
                live = {}
                for cb in [9,11,14,19,24,29]:
                    if bkt[cb,C]<=0: continue
                    pnl = (entry-bkt[cb,C])/entry*100
                    vwap = (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0
                    ng = sum(1 for b in range(7,cb+1) if bkt[b,C]>bkt[b,O])
                    live[cb] = {'pnl':pnl-COST, 'vwap':vwap, 'ng':ng}

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':gap,'price':price,'entry':entry,
                    'sp':sp,'mom':mom,'n_red':n_red,
                    'scores':scores,'ret90':ret90,'win':ret90>0,
                    'live':live,'bkt':bkt,'date':r['date'],
                })

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} records across {len(dates)} days in {time.time()-t0:.1f}s")

    def sim(scorer_key, n_pos, sizing_fn=None, check_b=14):
        """Full simulation with corrected sizing P&L."""
        total_pnl=0; day_wins=0; active=0; trades=0; wt=0
        max_day_capital=0; total_adds=0; total_exits=0

        for d in dates:
            pool = by_date[d]
            if len(pool)<1: continue
            pool.sort(key=lambda x:-x['scores'][scorer_key])
            picks = pool[:n_pos]
            active+=1; day_pnl=0; day_capital=BASE*n_pos

            for t in picks:
                trades+=1
                if sizing_fn and check_b in t['live']:
                    action = sizing_fn(t['live'][check_b])
                    pnl_at_check = t['live'][check_b]['pnl']

                    if action[0] == 'exit':
                        total_exits+=1
                        pnl_rs = BASE*MARGIN*pnl_at_check/100
                    elif action[0] == 'add':
                        mult = action[1]
                        total_adds+=1
                        remaining = t['ret90'] - pnl_at_check
                        pnl_rs = BASE*MARGIN*(pnl_at_check + remaining*mult)/100
                        day_capital += BASE*(mult-1)  # extra capital needed
                    else:
                        pnl_rs = BASE*MARGIN*t['ret90']/100
                else:
                    pnl_rs = BASE*MARGIN*t['ret90']/100

                day_pnl += pnl_rs
                if pnl_rs>0: wt+=1

            if day_capital > max_day_capital: max_day_capital = day_capital
            total_pnl += day_pnl
            if day_pnl>0: day_wins+=1

        roc = total_pnl/(BASE*n_pos)*100
        dw = day_wins/max(active,1)*100
        tw = wt/max(trades,1)*100
        return {
            'roc':roc,'dw':dw,'tw':tw,'trades':trades,'days':active,
            'max_capital':max_day_capital,'adds':total_adds,'exits':total_exits,
            'total_pnl':total_pnl,
        }

    # Sizing functions
    def no_sizing(live): return ('hold',)
    def make_add_only(min_pnl, mult):
        def fn(live):
            if live['pnl']>=min_pnl and live['vwap']<0:
                return ('add', mult)
            return ('hold',)
        return fn
    def make_add_exit(min_pnl, mult, exit_loss, exit_vwap):
        def fn(live):
            if live['pnl']<-exit_loss and live['vwap']>exit_vwap:
                return ('exit',)
            if live['pnl']>=min_pnl and live['vwap']<0:
                return ('add', mult)
            return ('hold',)
        return fn

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("OVERNIGHT PATTERN HUNT — Target: 90% green days\n")
        out.write(f"Data: {n} records, {len(dates)} days\n\n")

        # ═══════════════════════════════════════
        # 1. SCORER COMPARISON (no sizing)
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. SCORER COMPARISON (no sizing, top-8, b90 exit)\n"+"="*110+"\n")
        for scorer in ['plain','v2','s5','s6']:
            r = sim(scorer, 8)
            out.write(f"  {scorer:>6}: ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  trdWin={r['tw']:.1f}%\n")

        # ═══════════════════════════════════════
        # 2. POSITION COUNT (each scorer)
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. POSITION COUNT x SCORER\n"+"="*110+"\n")
        for scorer in ['v2','s6']:
            out.write(f"\n  {scorer}:\n")
            for npos in [3,4,5,6,7,8,10]:
                r = sim(scorer, npos)
                out.write(f"    top-{npos:>2}: ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  trdWin={r['tw']:.1f}%\n")

        # ═══════════════════════════════════════
        # 3. SIZING: CHECK BUCKET SWEEP (V2 scorer)
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. SIZING CHECK BUCKET SWEEP (V2, ADD 3x if pnl>0.3%+belowVWAP, top-8)\n"+"="*110+"\n")
        add3 = make_add_only(0.3, 3.0)
        for cb in [9,11,14,19,24,29]:
            h=9+(15+cb)//60; m=(15+cb)%60
            r = sim('v2', 8, add3, cb)
            out.write(f"  b{cb+1}({h}:{m:02d}): ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  adds={r['adds']}  maxCap=₹{r['max_capital']:,.0f}\n")

        # Same for S6
        out.write(f"\n  S6 scorer:\n")
        for cb in [9,11,14,19,24,29]:
            h=9+(15+cb)//60; m=(15+cb)%60
            r = sim('s6', 8, add3, cb)
            out.write(f"  b{cb+1}({h}:{m:02d}): ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  adds={r['adds']}  maxCap=₹{r['max_capital']:,.0f}\n")

        # ═══════════════════════════════════════
        # 4. ADD MULTIPLIER SWEEP
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. ADD MULTIPLIER SWEEP (V2, check@b15, top-8)\n"+"="*110+"\n")
        for min_pnl in [0.2, 0.3, 0.5]:
            out.write(f"\n  min_pnl>={min_pnl}%:\n")
            for mult in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
                fn = make_add_only(min_pnl, mult)
                r = sim('v2', 8, fn, 14)
                out.write(f"    {mult}x: ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  adds={r['adds']}  maxCap=₹{r['max_capital']:,.0f}\n")

        # ═══════════════════════════════════════
        # 5. EXIT + ADD COMBINED
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. EXIT + ADD COMBINED (V2, check@b15, top-8)\n"+"="*110+"\n")
        for exit_loss in [0.3, 0.5, 0.7, 99]:
            for exit_vwap in [0, 0.3]:
                for min_pnl in [0.3]:
                    for mult in [2.0, 3.0]:
                        fn = make_add_exit(min_pnl, mult, exit_loss, exit_vwap)
                        r = sim('v2', 8, fn, 14)
                        ex_label = f"exit>{exit_loss}%+vwap>{exit_vwap}" if exit_loss<50 else "no exit"
                        out.write(f"  ADD{mult}x(pnl>{min_pnl}%)+{ex_label}: ROC={r['roc']:>+7.1f}%  dayWin={r['dw']:.1f}%  exits={r['exits']}  adds={r['adds']}\n")

        # ═══════════════════════════════════════
        # 6. MEGA SWEEP: all combos
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. MEGA SWEEP — find highest green day rate\n"+"="*110+"\n")

        results = []
        for scorer in ['v2','s6','plain']:
            for npos in [4,6,7,8]:
                # No sizing
                r = sim(scorer, npos)
                results.append((r['dw'], f"{scorer}|top{npos}|no_sizing", r['roc'], r['tw'], r['trades'], r['max_capital']))

                # ADD only at various check buckets
                for cb in [9,14,19]:
                    for min_pnl in [0.2, 0.3, 0.5]:
                        for mult in [2.0, 3.0]:
                            fn = make_add_only(min_pnl, mult)
                            r = sim(scorer, npos, fn, cb)
                            results.append((r['dw'], f"{scorer}|top{npos}|ADD{mult}x@b{cb+1}(pnl>{min_pnl}%)", r['roc'], r['tw'], r['trades'], r['max_capital']))

                    # ADD + EXIT
                    for mult in [2.0, 3.0]:
                        fn = make_add_exit(0.3, mult, 0.5, 0.3)
                        r = sim(scorer, npos, fn, cb)
                        results.append((r['dw'], f"{scorer}|top{npos}|ADD{mult}x+EXIT@b{cb+1}", r['roc'], r['tw'], r['trades'], r['max_capital']))

        results.sort(key=lambda x: (-x[0], -x[2]))

        out.write(f"  {'Strategy':<70} {'DayWin':>7} {'ROC':>8} {'TrdWin':>7} {'MaxCap':>10}\n  "+"-"*105+"\n")
        for dw, name, roc, tw, nt, mc in results[:60]:
            out.write(f"  {name:<70} {dw:>6.1f}% {roc:>+7.1f}% {tw:>6.1f}% ₹{mc:>9,.0f}\n")

        # ═══════════════════════════════════════
        # 7. MAX CAPITAL NEEDED
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n7. CAPITAL REQUIREMENT for best strategies\n"+"="*110+"\n")
        # Show top 10 by day win with capital info
        top10 = results[:10]
        out.write(f"  Base capital (no sizing): ₹{BASE*8:,} (8 positions × ₹{BASE:,})\n")
        out.write(f"  With 5x margin: ₹{BASE*8//5:,} actual cash needed\n\n")
        for dw, name, roc, tw, nt, mc in top10:
            margin_needed = mc // 5
            out.write(f"  {name:<60} maxCap=₹{mc:,.0f} margin=₹{margin_needed:,.0f}\n")

        # ═══════════════════════════════════════
        # 8. PATH TO 90% GREEN DAYS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n8. PATH TO 90% GREEN DAYS\n"+"="*110+"\n")

        best_dw = results[0][0]
        out.write(f"\n  Best day win rate achieved: {best_dw:.1f}%\n")
        if best_dw >= 90:
            out.write(f"  TARGET ACHIEVED!\n")
        elif best_dw >= 80:
            out.write(f"  CLOSE (80%+). Gap to 90%: {90-best_dw:.1f}pp\n")
        elif best_dw >= 70:
            out.write(f"  MODERATE (70%+). Gap to 90%: {90-best_dw:.1f}pp\n")
        else:
            out.write(f"  FAR from 90%. Current best: {best_dw:.1f}%\n")

        # What would it take?
        out.write(f"\n  Top strategies by day win:\n")
        for dw, name, roc, tw, nt, mc in results[:20]:
            out.write(f"    {dw:.1f}% | {name}\n")

        # Analysis: on losing days, what happened?
        out.write(f"\n\n  LOSING DAY ANALYSIS (best strategy):\n")
        best_scorer = results[0][1].split('|')[0]
        best_npos = int(results[0][1].split('|')[1].replace('top',''))
        losing_days = []
        winning_days = []
        for d in dates:
            pool = sorted(by_date[d], key=lambda x:-x['scores'][best_scorer])[:best_npos]
            if not pool: continue
            day_ret = sum(t['ret90'] for t in pool)
            avg_gap = np.mean([t['gap'] for t in pool])
            avg_sp = np.mean([t['sp'] for t in pool])
            n_stocks = len(pool)
            if day_ret > 0:
                winning_days.append({'date':d,'ret':day_ret,'gap':avg_gap,'sp':avg_sp,'n':n_stocks})
            else:
                losing_days.append({'date':d,'ret':day_ret,'gap':avg_gap,'sp':avg_sp,'n':n_stocks})

        out.write(f"    Winning days: {len(winning_days)}, Losing days: {len(losing_days)}\n\n")
        if losing_days:
            out.write(f"    LOSING DAYS detail:\n")
            out.write(f"    {'Date':>12} {'DayRet':>8} {'AvgGap':>8} {'AvgSP':>6} {'N':>3}\n")
            for d in sorted(losing_days, key=lambda x:x['ret']):
                out.write(f"    {d['date']:>12} {d['ret']:>+7.2f}% {d['gap']:>7.2f}% {d['sp']:>.3f} {d['n']:>3}\n")

            out.write(f"\n    LOSING vs WINNING day features:\n")
            out.write(f"    {'Feature':<15} {'WinDays':>10} {'LoseDays':>10}\n")
            for feat in ['gap','sp']:
                wv = np.mean([d[feat] for d in winning_days])
                lv = np.mean([d[feat] for d in losing_days])
                out.write(f"    {feat:<15} {wv:>10.3f} {lv:>10.3f}\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
