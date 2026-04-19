"""
INVISIBLE PATTERNS — What humans can't see
=============================================
Minute-by-minute path shapes, price trajectories, cross-day memory,
dynamic exit triggers based on LIVE evolving features.

Using S5 scorer, top-8 cherry-pick, only liquid stocks.
ALL analysis is NO-LOOKAHEAD.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_invisible.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    # Track per-stock history across days (for cross-day patterns)
    stock_history = defaultdict(list)  # sym -> [(date, gap, ret_b90)]

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                bkts = r['buckets']
                nb = min(len(bkts),150)
                bkt = np.zeros((150,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)

                entry = bkt[6,O]
                if entry<=0: continue

                # Entry features
                cp_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp_sum/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])

                # Full path: sell P&L at every bucket from entry to b120
                path_pnl = []  # (bucket, sell_pnl%)
                path_vwap = []  # (bucket, price_vs_vwap%)
                path_vol = []   # (bucket, volume)
                path_green = [] # (bucket, is_green)
                for b in range(7, min(nb,120)):
                    if bkt[b,C]<=0: continue
                    pnl = (entry-bkt[b,C])/entry*100
                    vd = (bkt[b,C]-bkt[b,VW])/bkt[b,VW]*100 if bkt[b,VW]>0 else 0
                    path_pnl.append((b, pnl))
                    path_vwap.append((b, vd))
                    path_vol.append((b, float(bkt[b,V])))
                    path_green.append((b, bkt[b,C]>bkt[b,O]))

                if len(path_pnl)<80: continue

                ret90 = (entry-bkt[89,C])/entry*100-COST if bkt[89,C]>0 else 0

                # Score (S5)
                score = (r['gapPct'] if sp>0.5 else r['gapPct']*0.3)*(1.3 if mom<0 else 0.7)

                rec = {
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'sp':sp,'mom':mom,'n_red':n_red,
                    'path_pnl':path_pnl,'path_vwap':path_vwap,
                    'path_vol':path_vol,'path_green':path_green,
                    'ret90':ret90,'score':score,'date':r['date'],
                    'win':ret90>0, 'bkt':bkt,
                }
                by_date[r['date']].append(rec)
                stock_history[r['symbol']].append((r['date'], r['gapPct'], ret90))

    dates = sorted(by_date.keys())
    # Sort stock histories chronologically
    for sym in stock_history: stock_history[sym].sort()

    # Cherry-pick top-8 per day using S5
    daily_picks = {}
    for d in dates:
        pool = by_date[d]
        pool.sort(key=lambda x:-x['score'])
        daily_picks[d] = pool[:8]

    all_picks = [t for picks in daily_picks.values() for t in picks]
    winners = [t for t in all_picks if t['win']]
    losers = [t for t in all_picks if not t['win']]

    print(f"Loaded {len(all_picks)} top-8 picks across {len(dates)} days in {time.time()-t0:.1f}s")
    print(f"Winners: {len(winners)}, Losers: {len(losers)}")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("INVISIBLE PATTERNS — Minute-by-minute path analysis\n")
        out.write(f"S5 scorer, top-8, {len(all_picks)} trades, {len(dates)} days\n")
        out.write(f"Winners: {len(winners)} ({len(winners)/len(all_picks)*100:.1f}%), Losers: {len(losers)}\n\n")

        # ═══════════════════════════════════════════
        # 1. PATH SHAPE: average P&L trajectory — winners vs losers
        # ═══════════════════════════════════════════
        out.write("="*110+"\n1. P&L TRAJECTORY: Winners vs Losers minute-by-minute\n"+"="*110+"\n")
        out.write(f"  {'Bucket':>7} {'Time':>8} {'AllPnl':>8} {'WinPnl':>8} {'LosePnl':>8} {'WinLead':>8} {'WinVsLose':>10}\n")
        out.write("  "+"-"*65+"\n")
        for target_b in range(8, 95, 2):
            a = [next((p for b,p in t['path_pnl'] if b>=target_b), 0) for t in all_picks]
            w = [next((p for b,p in t['path_pnl'] if b>=target_b), 0) for t in winners]
            l = [next((p for b,p in t['path_pnl'] if b>=target_b), 0) for t in losers]
            h=9+(15+target_b)//60; m=(15+target_b)%60
            # At what bucket do we FIRST see winners diverge from losers?
            w_avg = np.mean(w); l_avg = np.mean(l)
            diverge = w_avg - l_avg
            out.write(f"  b{target_b+1:>4} ({h}:{m:02d}) {np.mean(a):>+7.3f}% {w_avg:>+7.3f}% {l_avg:>+7.3f}% {diverge:>+7.3f}%  {'***' if diverge>0.5 else ''}\n")

        # ═══════════════════════════════════════════
        # 2. EARLY WARNING: features at b15, b20, b30 that predict final outcome
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n2. EARLY WARNING: live features at b15/b20/b30 predict b90 outcome\n"+"="*110+"\n")

        for check_b in [14, 19, 29, 44]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  At b{check_b+1} ({h}:{m:02d}):\n")

            # Feature 1: current P&L
            out.write(f"\n    Current P&L at b{check_b+1} -> final b90 outcome:\n")
            pnl_bins = [(-99,-0.5,'losing >0.5%'),(-0.5,-0.2,'losing 0.2-0.5%'),(-0.2,0,'losing 0-0.2%'),
                        (0,0.2,'winning 0-0.2%'),(0.2,0.5,'winning 0.2-0.5%'),(0.5,1,'winning 0.5-1%'),(1,99,'winning >1%')]
            out.write(f"    {'Status':>20} {'N':>5} {'b90Win%':>8} {'b90AvgRet':>10} {'Action':>10}\n")
            for plo,phi,plbl in pnl_bins:
                sub = [t for t in all_picks if any(b==check_b and plo<=p<phi for b,p in t['path_pnl'])]
                if len(sub)<15: continue
                wr = sum(t['win'] for t in sub)/len(sub)*100
                ar = np.mean([t['ret90'] for t in sub])
                action = "HOLD" if ar>0.1 else "EXIT" if ar<-0.2 else "HOLD"
                out.write(f"    {plbl:>20} {len(sub):>5} {wr:>7.1f}% {ar:>+9.3f}% {action:>10}\n")

            # Feature 2: price vs VWAP
            out.write(f"\n    Price vs VWAP at b{check_b+1}:\n")
            for vlo,vhi,vlbl in [(-99,-0.5,'below VWAP >0.5%'),(-0.5,0,'below VWAP 0-0.5%'),
                                  (0,0.5,'above VWAP 0-0.5%'),(0.5,99,'above VWAP >0.5%')]:
                sub = [t for t in all_picks if any(b==check_b and vlo<=v<vhi for b,v in t['path_vwap'])]
                if len(sub)<15: continue
                wr = sum(t['win'] for t in sub)/len(sub)*100
                ar = np.mean([t['ret90'] for t in sub])
                out.write(f"    {vlbl:>25} {len(sub):>5} {wr:>7.1f}% {ar:>+9.3f}%\n")

            # Feature 3: green candle count since entry
            out.write(f"\n    Green candles b7-b{check_b+1}:\n")
            for glo,ghi,glbl in [(0,3,'0-2 green'),(3,5,'3-4 green'),(5,8,'5-7 green'),(8,99,'8+ green')]:
                sub = []
                for t in all_picks:
                    n_g = sum(1 for b,g in t['path_green'] if b<=check_b and g)
                    if glo<=n_g<ghi: sub.append(t)
                if len(sub)<15: continue
                wr = sum(t['win'] for t in sub)/len(sub)*100
                ar = np.mean([t['ret90'] for t in sub])
                out.write(f"    {glbl:>25} {len(sub):>5} {wr:>7.1f}% {ar:>+9.3f}%\n")

        # ═══════════════════════════════════════════
        # 3. DYNAMIC EXIT RULES based on live features
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n3. DYNAMIC EXIT RULES: exit early if conditions met, else hold to b90\n"+"="*110+"\n")

        def sim_dynamic_exit(trades, rule_fn):
            """rule_fn(trade, bucket) -> True to exit at this bucket's close"""
            total=0; wins=0; n=0
            for t in trades:
                exited = False
                for b, pnl in t['path_pnl']:
                    if b > 89: break
                    if rule_fn(t, b):
                        total += pnl - COST; n += 1
                        if pnl-COST > 0: wins += 1
                        exited = True; break
                if not exited:
                    total += t['ret90']; n += 1
                    if t['ret90'] > 0: wins += 1
            return total, wins/max(n,1)*100, n

        dynamic_rules = {
            'Fixed b90 (baseline)':
                lambda t,b: b >= 89,

            'Exit if losing >0.5% at b20':
                lambda t,b: b==19 and any(bb==19 and p<-0.5 for bb,p in t['path_pnl']),

            'Exit if losing >0.3% at b30':
                lambda t,b: b==29 and any(bb==29 and p<-0.3 for bb,p in t['path_pnl']),

            'Exit if losing >0.5% at b30':
                lambda t,b: b==29 and any(bb==29 and p<-0.5 for bb,p in t['path_pnl']),

            'Exit if above VWAP at b30':
                lambda t,b: b==29 and any(bb==29 and v>0 for bb,v in t['path_vwap']),

            'Exit if above VWAP at b20':
                lambda t,b: b==19 and any(bb==19 and v>0 for bb,v in t['path_vwap']),

            'Exit if losing at b30 AND above VWAP':
                lambda t,b: b==29 and any(bb==29 and p<0 for bb,p in t['path_pnl']) and any(bb==29 and v>0 for bb,v in t['path_vwap']),

            'Exit if 5+ green candles by b20':
                lambda t,b: b==19 and sum(1 for bb,g in t['path_green'] if bb<=19 and g)>=5,

            'Exit if 8+ green candles by b30':
                lambda t,b: b==29 and sum(1 for bb,g in t['path_green'] if bb<=29 and g)>=8,

            'Exit if losing >0.3% at b20 OR above VWAP at b30':
                lambda t,b: (b==19 and any(bb==19 and p<-0.3 for bb,p in t['path_pnl'])) or
                            (b==29 and any(bb==29 and v>0.2 for bb,v in t['path_vwap'])),

            'Exit if losing >0.5% at b15':
                lambda t,b: b==14 and any(bb==14 and p<-0.5 for bb,p in t['path_pnl']),

            'Combo: exit at b30 if (losing>0.3% AND above VWAP AND 6+green)':
                lambda t,b: b==29 and
                    any(bb==29 and p<-0.3 for bb,p in t['path_pnl']) and
                    any(bb==29 and v>0 for bb,v in t['path_vwap']) and
                    sum(1 for bb,g in t['path_green'] if bb<=29 and g)>=6,

            'Progressive: exit at b20 if loss>1%, b30 if loss>0.5%, b45 if loss>0.3%':
                lambda t,b: (b==19 and any(bb==19 and p<-1.0 for bb,p in t['path_pnl'])) or
                            (b==29 and any(bb==29 and p<-0.5 for bb,p in t['path_pnl'])) or
                            (b==44 and any(bb==44 and p<-0.3 for bb,p in t['path_pnl'])),
        }

        out.write(f"  {'Rule':<65} {'TotRet':>8} {'Win%':>6} {'N':>5}\n")
        out.write("  "+"-"*85+"\n")
        for name, rule in dynamic_rules.items():
            total, wr, n = sim_dynamic_exit(all_picks, rule)
            marker = " <<<" if 'baseline' in name else ""
            out.write(f"  {name:<65} {total:>+7.1f}% {wr:>5.1f}% {n:>5}{marker}\n")

        # ═══════════════════════════════════════════
        # 4. CROSS-DAY PATTERNS: does yesterday's result predict today?
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n4. CROSS-DAY: does a stock's YESTERDAY result predict TODAY?\n"+"="*110+"\n")

        cross_day = []
        for sym, hist in stock_history.items():
            for i in range(1, len(hist)):
                prev_date, prev_gap, prev_ret = hist[i-1]
                curr_date, curr_gap, curr_ret = hist[i]
                # Only if consecutive trading days (approximate: date diff <= 4)
                from datetime import date as dt
                d1 = dt.fromisoformat(prev_date)
                d2 = dt.fromisoformat(curr_date)
                if (d2-d1).days > 4: continue
                cross_day.append({
                    'sym':sym, 'prev_ret':prev_ret, 'curr_ret':curr_ret,
                    'prev_gap':prev_gap, 'curr_gap':curr_gap,
                    'prev_win': prev_ret>0, 'curr_win': curr_ret>0,
                })

        out.write(f"  Consecutive gap-up day pairs: {len(cross_day)}\n\n")

        # If stock reversed yesterday, does it reverse today?
        out.write(f"  Yesterday's result -> today's result:\n")
        for prev_label, prev_filt in [('Yesterday WIN', lambda x: x['prev_win']),
                                       ('Yesterday LOSS', lambda x: not x['prev_win'])]:
            sub = [x for x in cross_day if prev_filt(x)]
            if len(sub)<30: continue
            wr = sum(x['curr_win'] for x in sub)/len(sub)*100
            ar = np.mean([x['curr_ret'] for x in sub])
            out.write(f"    {prev_label:>20}: N={len(sub):>5}, Today win={wr:.1f}%, TodayAvg={ar:+.3f}%\n")

        # Consecutive wins
        out.write(f"\n  Consecutive-day streaks:\n")
        for streak_type, filt in [
            ('Won 2 days in row', lambda x: x['prev_win']),
            ('Lost 2 days in row', lambda x: not x['prev_win']),
        ]:
            sub = [x for x in cross_day if filt(x)]
            if not sub: continue
            wr = sum(x['curr_win'] for x in sub)/len(sub)*100
            out.write(f"    {streak_type:>25}: N={len(sub)}, NextDayWin={wr:.1f}%\n")

        # ═══════════════════════════════════════════
        # 5. PATH SHAPE CLASSIFICATION
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n5. PATH SHAPES: classify how trades unfold\n"+"="*110+"\n")

        shapes = {'steady_drop':0, 'fast_drop_then_flat':0, 'drop_then_bounce':0,
                  'flat_then_drop':0, 'never_drops':0, 'V_shape':0}
        shape_rets = defaultdict(list)

        for t in all_picks:
            pnl_b20 = next((p for b,p in t['path_pnl'] if b>=19), 0)
            pnl_b45 = next((p for b,p in t['path_pnl'] if b>=44), 0)
            pnl_b66 = next((p for b,p in t['path_pnl'] if b>=65), 0)
            pnl_b90 = t['ret90'] + COST  # add back cost for raw pnl

            if pnl_b20 > 0.2 and pnl_b45 > pnl_b20 and pnl_b90 > pnl_b45*0.7:
                shape = 'steady_drop'
            elif pnl_b20 > 0.3 and abs(pnl_b90-pnl_b20) < 0.3:
                shape = 'fast_drop_then_flat'
            elif pnl_b45 > 0.3 and pnl_b90 < pnl_b45*0.5:
                shape = 'drop_then_bounce'
            elif pnl_b20 < 0.1 and pnl_b45 < 0.1 and pnl_b90 > 0.3:
                shape = 'flat_then_drop'
            elif pnl_b90 < -0.2 and pnl_b45 < -0.1:
                shape = 'never_drops'
            elif pnl_b45 > 0.5 and pnl_b90 < 0:
                shape = 'V_shape'
            else:
                shape = 'other'

            shapes[shape] = shapes.get(shape,0) + 1
            shape_rets[shape].append(t['ret90'])

        out.write(f"  {'Shape':<25} {'Count':>6} {'%':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*55+"\n")
        for shape in ['steady_drop','fast_drop_then_flat','flat_then_drop',
                      'drop_then_bounce','V_shape','never_drops','other']:
            n = shapes.get(shape,0)
            if n < 5: continue
            rets = shape_rets[shape]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            out.write(f"  {shape:<25} {n:>6} {n/len(all_picks)*100:>5.1f}% {wr:>5.1f}% {np.mean(rets):>+7.3f}%\n")

        # Which entry features predict each shape?
        out.write(f"\n  Entry features by path shape:\n")
        out.write(f"  {'Shape':<22} {'Gap':>6} {'SP':>5} {'Mom':>7} {'nRed':>5}\n")
        out.write("  "+"-"*50+"\n")
        for shape in ['steady_drop','fast_drop_then_flat','flat_then_drop','drop_then_bounce','never_drops']:
            sub = [t for t in all_picks if shape in str(shapes)]  # hack
            # Actually tag them
            pass

        # ═══════════════════════════════════════════
        # 6. VOLUME PROFILE: do winning trades have different volume patterns?
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n6. VOLUME PROFILE: winners vs losers post-entry\n"+"="*110+"\n")
        out.write(f"  Avg volume at each bucket (relative to b7 volume):\n")
        out.write(f"  {'Bucket':>7} {'WinVol':>10} {'LoseVol':>10} {'Ratio':>8}\n")
        out.write("  "+"-"*40+"\n")
        for target_b in range(10, 90, 10):
            w_vols = [next((v for b,v in t['path_vol'] if b>=target_b), 0) for t in winners]
            l_vols = [next((v for b,v in t['path_vol'] if b>=target_b), 0) for t in losers]
            w_avg = np.mean(w_vols) if w_vols else 0
            l_avg = np.mean(l_vols) if l_vols else 0
            ratio = w_avg/max(l_avg,1)
            out.write(f"  b{target_b+1:>5} {w_avg:>10.0f} {l_avg:>10.0f} {ratio:>7.2f}x\n")

        # ═══════════════════════════════════════════
        # 7. FINAL: BEST DYNAMIC EXIT COMBINED
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n7. BEST DYNAMIC EXIT vs FIXED b90\n"+"="*110+"\n")

        # Combine the best insights: use live P&L + VWAP + green count
        def best_dynamic(trade, bucket):
            # Progressive cut: exit early if clearly losing
            for b, pnl in trade['path_pnl']:
                if b != bucket: continue
                # At b15: exit if losing > 1%
                if b == 14 and pnl < -1.0: return True
                # At b20: exit if losing > 0.5% AND above VWAP
                if b == 19:
                    above_vwap = any(bb==19 and v>0 for bb,v in trade['path_vwap'])
                    if pnl < -0.5 and above_vwap: return True
                # At b30: exit if losing AND above VWAP AND 6+ green
                if b == 29:
                    above_vwap = any(bb==29 and v>0 for bb,v in trade['path_vwap'])
                    n_green = sum(1 for bb,g in trade['path_green'] if bb<=29 and g)
                    if pnl < -0.3 and above_vwap and n_green >= 6: return True
                # At b45: exit if losing > 0.3%
                if b == 44 and pnl < -0.3: return True
            # Otherwise hold to b90
            return bucket >= 89

        total_dyn, wr_dyn, n_dyn = sim_dynamic_exit(all_picks, best_dynamic)
        total_fix = sum(t['ret90'] for t in all_picks)
        wr_fix = sum(t['win'] for t in all_picks)/len(all_picks)*100

        out.write(f"  Fixed b90:        total={total_fix:>+8.1f}%  win={wr_fix:.1f}%\n")
        out.write(f"  Best dynamic:     total={total_dyn:>+8.1f}%  win={wr_dyn:.1f}%\n")
        out.write(f"  Improvement:      {total_dyn-total_fix:>+8.1f}%\n\n")

        # What does the dynamic exit SAVE? (trades that were losing at b90 but exited early)
        saved = 0; hurt = 0
        for t in all_picks:
            dyn_ret = None
            for b, pnl in t['path_pnl']:
                if best_dynamic(t, b):
                    dyn_ret = pnl - COST; break
            if dyn_ret is None: dyn_ret = t['ret90']
            if dyn_ret > t['ret90'] + 0.01: saved += 1
            elif dyn_ret < t['ret90'] - 0.01: hurt += 1

        out.write(f"  Trades SAVED by early exit: {saved} (would have lost more at b90)\n")
        out.write(f"  Trades HURT by early exit:  {hurt} (would have profited more at b90)\n")
        out.write(f"  Net benefit: {saved-hurt} trades improved\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
