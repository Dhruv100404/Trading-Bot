"""
DEEP 3-GREEN EXIT + CHERRY-PICK PROFIT ANALYSIS
==================================================
1. 3-green exit on cherry-picked top-8 (not full pool)
2. Combined: best scorer + 3-green exit + trailing stop
3. Top-30 deep analysis: WHY profitable stocks don't get picked
4. Feature-by-feature: what makes a stock ACTUALLY profit

STRICT NO-LOOKAHEAD:
  - 3-green exit: detected at bucket N from candles N-2, N-1, N (all closed)
  - Exit at bucket N+1 OPEN (conservative — can't exit at N close)
  - All scoring features from buckets 0-5 only (before entry at b6 open)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_3green_exit.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

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
                bkts = r['buckets']
                nb = min(len(bkts), 200)
                bkt = np.zeros((200,7), dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)

                entry = bkt[6,O]
                if entry <= 0: continue

                # Entry features (NO LOOKAHEAD: buckets 0-5 only)
                cp_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sell_pressure = 1 - cp_sum/6
                momentum = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])

                # Build minute-by-minute price path from entry
                path = []
                for b in range(7, min(nb, 150)):
                    if bkt[b,C] <= 0: continue
                    sell_pnl = (entry - bkt[b,C]) / entry * 100
                    sell_hi = (entry - bkt[b,L]) / entry * 100  # best for sell (low price)
                    sell_lo = (entry - bkt[b,H]) / entry * 100  # worst for sell (high price)
                    is_green = bkt[b,C] > bkt[b,O]
                    above_vwap = bkt[b,C] > bkt[b,VW] if bkt[b,VW] > 0 else False
                    path.append({'b':b, 'pnl':sell_pnl, 'hi':sell_hi, 'lo':sell_lo,
                                 'green':is_green, 'above_vwap':above_vwap, 'close':bkt[b,C],
                                 'next_open': bkt[b+1,O] if b+1<nb else bkt[b,C]})

                if len(path) < 20: continue

                by_date[r['date']].append({
                    'sym':r['symbol'], 'gap':r['gapPct'], 'price':r['dayOpen'],
                    'entry':entry, 'path':path, 'bkt':bkt,
                    'sell_pressure':sell_pressure, 'momentum':momentum, 'n_red':n_red,
                    'date':r['date'],
                })

    dates = sorted(by_date.keys())
    n_total = sum(len(v) for v in by_date.values())
    print(f"Loaded {n_total} gap-up records in {time.time()-t0:.1f}s")

    # ── EXIT SIMULATION FUNCTIONS (all NO-LOOKAHEAD) ──

    def exit_fixed(trade, exit_b):
        """Fixed time exit at bucket exit_b"""
        for p in trade['path']:
            if p['b'] >= exit_b:
                return p['pnl'] - COST
        return trade['path'][-1]['pnl'] - COST if trade['path'] else 0

    def exit_3green(trade, max_b=89):
        """Exit at NEXT OPEN after 3 consecutive green candles detected.
        Detection at bucket N means candles N-2, N-1, N are all green.
        Exit at N+1 open (no lookahead — can't exit at N close)."""
        for i in range(2, len(trade['path'])):
            if trade['path'][i]['b'] > max_b: break
            if (trade['path'][i]['green'] and
                trade['path'][i-1]['green'] and
                trade['path'][i-2]['green']):
                # 3 greens detected at bucket i. Exit at next bucket open.
                exit_price = trade['path'][i]['next_open']
                if exit_price > 0:
                    ret = (trade['entry'] - exit_price) / trade['entry'] * 100 - COST
                    return ret
        # No 3-green detected: time exit at max_b
        return exit_fixed(trade, max_b)

    def exit_2green(trade, max_b=89):
        """Exit after 2 consecutive green candles"""
        for i in range(1, len(trade['path'])):
            if trade['path'][i]['b'] > max_b: break
            if trade['path'][i]['green'] and trade['path'][i-1]['green']:
                exit_price = trade['path'][i]['next_open']
                if exit_price > 0:
                    return (trade['entry'] - exit_price) / trade['entry'] * 100 - COST
        return exit_fixed(trade, max_b)

    def exit_vwap_cross(trade, max_b=89):
        """Exit when price crosses above VWAP (thesis broken)"""
        for p in trade['path']:
            if p['b'] > max_b: break
            if p['above_vwap']:
                exit_price = p['next_open']
                if exit_price > 0:
                    return (trade['entry'] - exit_price) / trade['entry'] * 100 - COST
        return exit_fixed(trade, max_b)

    def exit_trailing(trade, activate_at, trail_dist, max_b=89):
        """Trailing stop: activate after +activate_at%, trail by trail_dist%"""
        peak = 0
        for p in trade['path']:
            if p['b'] > max_b: break
            if p['hi'] > peak: peak = p['hi']
            if peak >= activate_at and p['lo'] <= peak - trail_dist:
                return peak - trail_dist - COST
        return exit_fixed(trade, max_b)

    def exit_stepped(trade, max_b=89):
        """Stepped trailing: +0.3->BE, +0.5->+0.2, +1->+0.5, +1.5->+1"""
        peak = 0; stop = -999
        for p in trade['path']:
            if p['b'] > max_b: break
            if p['hi'] > peak: peak = p['hi']
            if peak >= 1.5: stop = max(stop, 1.0)
            elif peak >= 1.0: stop = max(stop, 0.5)
            elif peak >= 0.5: stop = max(stop, 0.2)
            elif peak >= 0.3: stop = max(stop, 0.0)
            if stop > -999 and p['lo'] <= stop:
                return stop - COST
        return exit_fixed(trade, max_b)

    def exit_3green_then_trail(trade, trail_activate=0.3, trail_dist=0.2, max_b=89):
        """Hybrid: trail from start, but also exit on 3-green regardless"""
        peak = 0; trail_active = False
        for i in range(len(trade['path'])):
            p = trade['path'][i]
            if p['b'] > max_b: break
            if p['hi'] > peak: peak = p['hi']

            # Trailing stop
            if not trail_active and peak >= trail_activate: trail_active = True
            if trail_active and p['lo'] <= peak - trail_dist:
                return peak - trail_dist - COST

            # 3-green exit
            if i >= 2 and p['green'] and trade['path'][i-1]['green'] and trade['path'][i-2]['green']:
                exit_price = p['next_open']
                if exit_price > 0:
                    return (trade['entry'] - exit_price) / trade['entry'] * 100 - COST
        return exit_fixed(trade, max_b)

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("DEEP 3-GREEN EXIT + CHERRY-PICK PROFIT ANALYSIS\n")
        out.write(f"Gap-up stocks: {n_total}, Days: {len(dates)}\n")
        out.write("ALL exits are NO-LOOKAHEAD\n\n")

        # ═════════════════════════════════════════════════
        # PART 1: EXIT STRATEGIES ON FULL POOL
        # ═════════════════════════════════════════════════
        out.write("="*110+"\n1. EXIT STRATEGIES ON FULL POOL (all gap>0.5% stocks)\n"+"="*110+"\n")
        all_trades = [t for stocks in by_date.values() for t in stocks]

        exit_methods = {
            'Fixed b45': lambda t: exit_fixed(t, 44),
            'Fixed b66': lambda t: exit_fixed(t, 65),
            'Fixed b90': lambda t: exit_fixed(t, 89),
            '3-green (max b90)': lambda t: exit_3green(t, 89),
            '3-green (max b66)': lambda t: exit_3green(t, 65),
            '2-green (max b90)': lambda t: exit_2green(t, 89),
            'VWAP cross (max b90)': lambda t: exit_vwap_cross(t, 89),
            'Trail +0.3% / 0.2%': lambda t: exit_trailing(t, 0.3, 0.2, 89),
            'Trail +0.5% / 0.3%': lambda t: exit_trailing(t, 0.5, 0.3, 89),
            'Trail +1.0% / 0.3%': lambda t: exit_trailing(t, 1.0, 0.3, 89),
            'Stepped trail': lambda t: exit_stepped(t, 89),
            '3-green + trail(0.3/0.2)': lambda t: exit_3green_then_trail(t, 0.3, 0.2, 89),
            '3-green + trail(0.5/0.3)': lambda t: exit_3green_then_trail(t, 0.5, 0.3, 89),
        }

        out.write(f"  {'Exit Method':<35} {'TotalRet':>10} {'Win%':>6} {'AvgRet':>8} {'N':>6}\n")
        out.write("  "+"-"*70+"\n")
        for name, method in exit_methods.items():
            rets = [method(t) for t in all_trades]
            total = sum(rets)
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            out.write(f"  {name:<35} {total:>+9.1f}% {wr:>5.1f}% {ar:>+7.3f}% {len(rets):>6}\n")

        # ═════════════════════════════════════════════════
        # PART 2: EXIT STRATEGIES ON CHERRY-PICKED TOP-8
        # ═════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n2. EXIT ON CHERRY-PICKED TOP-8 (scorer x exit combos)\n"+"="*110+"\n")

        scorers = {
            'S0:gap': lambda t: t['gap'],
            'S5:gap*(sp>.5?1:.3)*(mom<0?1.3:.7)': lambda t: (t['gap'] if t['sell_pressure']>0.5 else t['gap']*0.3)*(1.3 if t['momentum']<0 else 0.7),
            'Sv2:gap*sp*(mom<-.5?1.4:m<0?1.1:.7)': lambda t: t['gap']*t['sell_pressure']*(1.4 if t['momentum']<-0.5 else 1.1 if t['momentum']<0 else 0.7),
        }

        exit_short = {
            'Fixed b66': lambda t: exit_fixed(t, 65),
            'Fixed b90': lambda t: exit_fixed(t, 89),
            '3-green(b90)': lambda t: exit_3green(t, 89),
            '3-green(b66)': lambda t: exit_3green(t, 65),
            'Trail+0.5/0.3': lambda t: exit_trailing(t, 0.5, 0.3, 89),
            'Stepped': lambda t: exit_stepped(t, 89),
            '3green+trail': lambda t: exit_3green_then_trail(t, 0.5, 0.3, 89),
        }

        results = []
        for s_name, scorer in scorers.items():
            for e_name, exit_fn in exit_short.items():
                day_total = 0; day_wins = 0; trades = 0; win_trades = 0
                for d in dates:
                    pool = by_date[d]
                    if len(pool) < 3: continue
                    for t in pool: t['_sc'] = scorer(t)
                    pool.sort(key=lambda x: -x['_sc'])
                    picks = pool[:8]
                    for t in picks:
                        ret = exit_fn(t)
                        day_total += ret; trades += 1
                        if ret > 0: win_trades += 1
                    if sum(exit_fn(t) for t in picks) > 0: day_wins += 1
                n_d = len(dates)
                dw = day_wins/max(n_d,1)*100
                tw = win_trades/max(trades,1)*100
                results.append((day_total, f"{s_name} | {e_name}", n_d, dw, tw, trades))

        results.sort(key=lambda x: -x[0])
        out.write(f"  {'Strategy':<65} {'TotRet':>8} {'DayW':>6} {'TrdW':>6} {'Trds':>5}\n")
        out.write("  "+"-"*95+"\n")
        for total, name, nd, dw, tw, nt in results:
            out.write(f"  {name:<65} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {nt:>5}\n")

        # ═════════════════════════════════════════════════
        # PART 3: TOP-30 DEEP ANALYSIS — WHY LOSERS GET PICKED
        # ═════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n3. TOP-30 DEEP: WHY PROFITABLE STOCKS DON'T GET PICKED\n"+"="*110+"\n")

        best_scorer = lambda t: (t['gap'] if t['sell_pressure']>0.5 else t['gap']*0.3)*(1.3 if t['momentum']<0 else 0.7)
        best_exit = lambda t: exit_3green(t, 89)

        # For each day: rank by best scorer, show top-30 with outcomes
        picked_wins = 0; picked_losses = 0
        missed_wins = 0; missed_losses = 0
        picked_features = []; missed_features = []
        loser_details = []

        for d in dates:
            pool = by_date[d]
            if len(pool) < 3: continue
            for t in pool: t['_sc'] = best_scorer(t)
            pool.sort(key=lambda x: -x['_sc'])

            top8 = pool[:8]
            rest = pool[8:30]

            for t in top8:
                ret = best_exit(t)
                if ret > 0:
                    picked_wins += 1
                    picked_features.append(t)
                else:
                    picked_losses += 1
                    loser_details.append({**t, 'ret':ret, 'date':d})

            for t in rest:
                ret = best_exit(t)
                if ret > 0:
                    missed_wins += 1
                    missed_features.append(t)
                else:
                    missed_losses += 1

        out.write(f"\n  With S5 scorer + 3-green exit:\n")
        out.write(f"    Top-8 picked: {picked_wins} wins + {picked_losses} losses = {picked_wins/(picked_wins+picked_losses)*100:.1f}% win\n")
        out.write(f"    Rank 9-30: {missed_wins} wins + {missed_losses} losses = {missed_wins/(missed_wins+missed_losses)*100:.1f}% win\n")
        out.write(f"    Missed profitable trades: {missed_wins}\n\n")

        # Feature comparison
        out.write(f"  PICKED WINNERS vs PICKED LOSERS vs MISSED WINNERS:\n")
        out.write(f"  {'Feature':<16} {'PickedWins':>12} {'PickedLose':>12} {'MissedWins':>12}\n")
        out.write("  "+"-"*55+"\n")
        for f in ['gap','sell_pressure','momentum','n_red','price']:
            pw = np.mean([t[f] for t in picked_features]) if picked_features else 0
            pl = np.mean([t[f] for t in loser_details]) if loser_details else 0
            mw = np.mean([t[f] for t in missed_features]) if missed_features else 0
            out.write(f"  {f:<16} {pw:>12.3f} {pl:>12.3f} {mw:>12.3f}\n")

        # WHY losers got picked: their scores were high but they didn't reverse
        out.write(f"\n  TOP-8 LOSER PROFILE (what they have in common):\n")
        if loser_details:
            out.write(f"    Count: {len(loser_details)}\n")
            out.write(f"    Avg gap: {np.mean([t['gap'] for t in loser_details]):.2f}%\n")
            out.write(f"    Avg sell_pressure: {np.mean([t['sell_pressure'] for t in loser_details]):.3f}\n")
            out.write(f"    Avg momentum: {np.mean([t['momentum'] for t in loser_details]):+.3f}%\n")
            out.write(f"    Avg n_red: {np.mean([t['n_red'] for t in loser_details]):.1f}\n")
            out.write(f"    Avg score: {np.mean([t['_sc'] for t in loser_details]):.2f}\n")
            out.write(f"    Avg loss: {np.mean([t['ret'] for t in loser_details]):+.3f}%\n\n")

            # What SINGLE feature best separates picked winners from picked losers?
            out.write(f"  FEATURE DISCRIMINANT POWER (AUC-like):\n")
            out.write(f"  Which feature best separates winners from losers in the top-8?\n\n")
            all_picked = picked_features + loser_details
            for feat in ['gap','sell_pressure','momentum','n_red','price']:
                # Sort by feature, check if wins cluster at one end
                sorted_picks = sorted(all_picked, key=lambda t: t[feat])
                # Split at median
                mid = len(sorted_picks)//2
                low_half = sorted_picks[:mid]
                high_half = sorted_picks[mid:]
                low_wr = sum(1 for t in low_half if t.get('ret',exit_fixed(t,65))>0)/max(len(low_half),1)*100
                high_wr = sum(1 for t in high_half if t.get('ret',exit_fixed(t,65))>0)/max(len(high_half),1)*100
                gap_str = f"low={low_wr:.1f}%, high={high_wr:.1f}%"
                better = "LOW" if low_wr > high_wr else "HIGH"
                out.write(f"    {feat:<16}: {gap_str:>30} -> {better} half wins more\n")

        # ═════════════════════════════════════════════════
        # PART 4: CAN WE IMPROVE? Test reject filters on losers
        # ═════════════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. REJECT FILTERS TO REMOVE LOSERS (with S5 scorer + 3-green exit)\n"+"="*110+"\n")

        reject_filters = {
            'none': lambda t: False,
            'gap>15%': lambda t: t['gap']>15,
            'gap>10%': lambda t: t['gap']>10,
            'sp<0.40': lambda t: t['sell_pressure']<0.40,
            'sp<0.45': lambda t: t['sell_pressure']<0.45,
            'sp<0.45 & nred<=1': lambda t: t['sell_pressure']<0.45 and t['n_red']<=1,
            'sp<0.40 | gap>10': lambda t: t['sell_pressure']<0.40 or t['gap']>10,
            'sp<0.45 | gap>15': lambda t: t['sell_pressure']<0.45 or t['gap']>15,
            'mom>0 & sp<0.50': lambda t: t['momentum']>0 and t['sell_pressure']<0.50,
            'mom>0.3': lambda t: t['momentum']>0.3,
            'nred<=1 & sp<0.50': lambda t: t['n_red']<=1 and t['sell_pressure']<0.50,
            'price>2000 & sp<0.50': lambda t: t['price']>2000 and t['sell_pressure']<0.50,
        }

        out.write(f"  {'Reject':<35} {'TotRet':>8} {'DayW':>6} {'TrdW':>6} {'Trds':>5}\n")
        out.write("  "+"-"*65+"\n")
        for r_name, reject in reject_filters.items():
            total=0; dw=0; trades=0; wt=0
            for d in dates:
                pool = [t for t in by_date[d] if not reject(t)]
                if len(pool)<1: continue
                for t in pool: t['_sc'] = best_scorer(t)
                pool.sort(key=lambda x:-x['_sc'])
                picks = pool[:8]
                day_ret = sum(best_exit(t) for t in picks)
                total += day_ret; trades += len(picks)
                wt += sum(1 for t in picks if best_exit(t)>0)
                if day_ret>0: dw+=1
            nd=len(dates)
            out.write(f"  {r_name:<35} {total:>+7.1f}% {dw/max(nd,1)*100:>5.1f}% {wt/max(trades,1)*100:>5.1f}% {trades:>5}\n")

        # ═════════════════════════════════════════════════
        # PART 5: POSITION COUNT OPTIMIZATION with best combo
        # ═════════════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. POSITION COUNT with S5 scorer + 3-green exit\n"+"="*110+"\n")
        for n_pos in [2,3,4,5,6,7,8,10,12]:
            total=0; dw=0; trades=0; wt=0
            for d in dates:
                pool = by_date[d]
                if len(pool)<1: continue
                for t in pool: t['_sc'] = best_scorer(t)
                pool.sort(key=lambda x:-x['_sc'])
                picks = pool[:n_pos]
                day_ret = sum(best_exit(t) for t in picks)
                total+=day_ret; trades+=len(picks)
                wt+=sum(1 for t in picks if best_exit(t)>0)
                if day_ret>0: dw+=1
            nd=len(dates)
            out.write(f"  Top-{n_pos:>2}: total={total:>+8.1f}%  dayW={dw/max(nd,1)*100:>5.1f}%  trdW={wt/max(trades,1)*100:>5.1f}%  trades={trades}\n")

        # ═════════════════════════════════════════════════
        # PART 6: BEST OVERALL STRATEGY
        # ═════════════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. FINAL: BEST OVERALL STRATEGY\n"+"="*110+"\n")
        # Run the absolute best combo: S5 + best reject + best exit + best position count
        best_reject = lambda t: t['sell_pressure']<0.40 or t['gap']>10
        for n_pos in [4,6,7,8]:
            for exit_name, exit_fn in [('3green(b90)', lambda t: exit_3green(t,89)),
                                        ('3green+trail', lambda t: exit_3green_then_trail(t,0.5,0.3,89)),
                                        ('Fixed b90', lambda t: exit_fixed(t,89))]:
                total=0;dw=0;trades=0;wt=0
                for d in dates:
                    pool = [t for t in by_date[d] if not best_reject(t)]
                    if len(pool)<1: continue
                    for t in pool: t['_sc'] = best_scorer(t)
                    pool.sort(key=lambda x:-x['_sc'])
                    picks = pool[:n_pos]
                    day_ret = sum(exit_fn(t) for t in picks)
                    total+=day_ret;trades+=len(picks);wt+=sum(1 for t in picks if exit_fn(t)>0)
                    if day_ret>0: dw+=1
                nd=len(dates)
                out.write(f"  S5 + R:sp<.4|gap>10 + {exit_name} + top{n_pos}: total={total:>+8.1f}%  dayW={dw/max(nd,1)*100:.1f}%  trdW={wt/max(trades,1)*100:.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
