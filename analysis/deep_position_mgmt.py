"""
DEEP POSITION MANAGEMENT — 5 Points Combined
================================================
#A: Per-share mid-trade signals (P&L is per individual stock)
#B: Green candle COUNT × SIZE (momentum sensing)
#C: Position sizing: add to winners, cut losers — exact amounts
#D: Half-exit at TP + trail rest with SL
#E: BUY signals for high-score days

Using S5 scorer, top-8, exit b90.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_position_mgmt.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE_CAPITAL = 10000  # per trade capital
MARGIN = 5  # 5x

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date_sell = defaultdict(list)
    by_date_buy = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = min(len(bkts),150)
                bkt = np.zeros((150,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0: continue

                cp_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp_sum/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                score_sell = (r['gapPct'] if sp>0.5 else r['gapPct']*0.3)*(1.3 if mom<0 else 0.7)

                # Pre-compute per-bucket data
                pnl_sell = {}; pnl_buy = {}
                green_bodies = {}  # bucket -> body size if green (for momentum)
                for b in range(7,min(nb,100)):
                    if bkt[b,C]<=0: continue
                    pnl_sell[b] = (entry-bkt[b,C])/entry*100
                    pnl_buy[b] = (bkt[b,C]-entry)/entry*100
                    if bkt[b,C]>bkt[b,O]:
                        green_bodies[b] = (bkt[b,C]-bkt[b,O])/bkt[b,O]*100
                    else:
                        green_bodies[b] = 0  # red candle

                vwap_pos = {}
                for b in range(7,min(nb,100)):
                    if bkt[b,VW]>0: vwap_pos[b] = (bkt[b,C]-bkt[b,VW])/bkt[b,VW]*100
                    else: vwap_pos[b] = 0

                rec = {
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'sp':sp,'mom':mom,'n_red':n_red,'score':score_sell,
                    'pnl_sell':pnl_sell,'pnl_buy':pnl_buy,'vwap_pos':vwap_pos,
                    'green_bodies':green_bodies,'bkt':bkt,'date':r['date'],
                    'ret90_sell': pnl_sell.get(89,0)-COST,
                    'ret90_buy': pnl_buy.get(89,0)-COST,
                }
                if r['gapPct'] > 0.5:
                    by_date_sell[r['date']].append(rec)
                if r['gapPct'] < -0.5:
                    by_date_buy[r['date']].append(rec)

    dates = sorted(set(list(by_date_sell.keys())+list(by_date_buy.keys())))

    # Cherry-pick sell top-8
    daily_sell_picks = {}
    for d in dates:
        pool = sorted(by_date_sell.get(d,[]), key=lambda x:-x['score'])
        daily_sell_picks[d] = pool[:8]

    all_sell = [t for picks in daily_sell_picks.values() for t in picks]
    print(f"Loaded {len(all_sell)} sell picks in {time.time()-t0:.1f}s")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("DEEP POSITION MANAGEMENT\n")
        out.write(f"Sell picks: {len(all_sell)}, Days: {len(dates)}\n\n")

        # ═══════════════════════════════════════════
        # #A: PER-SHARE mid-trade signals
        # ═══════════════════════════════════════════
        out.write("="*110+"\n#A: PER-SHARE MID-TRADE DECISION (each of 8 stocks individually)\n"+"="*110+"\n")
        out.write("  Yes, P&L direction is PER SHARE. At b20, each stock has its own P&L.\n")
        out.write("  Decision: for EACH stock separately — hold, add, or cut?\n\n")

        for check_b in [14, 19, 29]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  At b{check_b+1} ({h}:{m:02d}) — per-share decision matrix:\n")

            # Combine: P&L state + VWAP position
            combos = [
                ('WIN + below VWAP', lambda t: t['pnl_sell'].get(check_b,0)>0 and t['vwap_pos'].get(check_b,0)<0),
                ('WIN + above VWAP', lambda t: t['pnl_sell'].get(check_b,0)>0 and t['vwap_pos'].get(check_b,0)>=0),
                ('LOSE <0.3% + below VWAP', lambda t: -0.3<=t['pnl_sell'].get(check_b,0)<=0 and t['vwap_pos'].get(check_b,0)<0),
                ('LOSE <0.3% + above VWAP', lambda t: -0.3<=t['pnl_sell'].get(check_b,0)<=0 and t['vwap_pos'].get(check_b,0)>=0),
                ('LOSE 0.3-0.5%', lambda t: -0.5<=t['pnl_sell'].get(check_b,0)<-0.3),
                ('LOSE >0.5%', lambda t: t['pnl_sell'].get(check_b,0)<-0.5),
                ('WIN >0.5%', lambda t: t['pnl_sell'].get(check_b,0)>0.5),
                ('WIN >1%', lambda t: t['pnl_sell'].get(check_b,0)>1.0),
            ]
            out.write(f"    {'State':<30} {'N':>5} {'b90Win%':>8} {'b90Ret':>8} {'Action':>10}\n    "+"-"*65+"\n")
            for label, filt in combos:
                sub = [t for t in all_sell if filt(t)]
                if len(sub)<15: continue
                wr = sum(1 for t in sub if t['ret90_sell']>0)/len(sub)*100
                ar = np.mean([t['ret90_sell'] for t in sub])
                if ar > 0.3: action = "ADD MORE"
                elif ar > 0.05: action = "HOLD"
                elif ar > -0.2: action = "HOLD/CUT"
                else: action = "CUT/EXIT"
                out.write(f"    {label:<30} {len(sub):>5} {wr:>7.1f}% {ar:>+7.3f}% {action:>10}\n")

        # ═══════════════════════════════════════════
        # #B: Green candle COUNT × SIZE (momentum)
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#B: GREEN CANDLE COUNT × BODY SIZE (momentum detection)\n"+"="*110+"\n")
        out.write("  A big green candle = strong buying. Many small greens = slow drift.\n\n")

        for check_b in [14, 19, 29]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  At b{check_b+1} ({h}:{m:02d}):\n")

            # Compute green momentum score = sum of green body sizes from b7 to check_b
            for t in all_sell:
                green_mom = sum(t['green_bodies'].get(b,0) for b in range(7, check_b+1))
                n_green = sum(1 for b in range(7, check_b+1) if t['green_bodies'].get(b,0)>0)
                max_green = max((t['green_bodies'].get(b,0) for b in range(7, check_b+1)), default=0)
                t[f'_gm_{check_b}'] = green_mom
                t[f'_ng_{check_b}'] = n_green
                t[f'_mg_{check_b}'] = max_green

            # Green count alone
            out.write(f"    Green COUNT:\n")
            out.write(f"    {'Count':>7} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*30+"\n")
            n_bkts = check_b - 6
            for glo,ghi in [(0,2),(2,3),(3,4),(4,5),(5,6),(6,n_bkts+1)]:
                sub = [t for t in all_sell if glo<=t[f'_ng_{check_b}']<ghi]
                if len(sub)<15: continue
                wr = sum(1 for t in sub if t['ret90_sell']>0)/len(sub)*100
                out.write(f"    {f'{glo}-{ghi-1}':>7} {len(sub):>5} {wr:>5.1f}% {np.mean([t['ret90_sell'] for t in sub]):>+7.3f}%\n")

            # Green momentum (sum of body sizes)
            out.write(f"\n    Green MOMENTUM (sum of green body %):\n")
            out.write(f"    {'Momentum':>10} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*35+"\n")
            vals = [t[f'_gm_{check_b}'] for t in all_sell]
            for pct in [0, 25, 50, 75]:
                lo = np.percentile(vals, pct)
                hi = np.percentile(vals, min(pct+25, 100))
                sub = [t for t in all_sell if lo<=t[f'_gm_{check_b}']<hi+(0.001 if pct==75 else 0)]
                if len(sub)<15: continue
                wr = sum(1 for t in sub if t['ret90_sell']>0)/len(sub)*100
                out.write(f"    p{pct}-p{pct+25}({lo:.2f}-{hi:.2f}) {len(sub):>5} {wr:>5.1f}% {np.mean([t['ret90_sell'] for t in sub]):>+7.3f}%\n")

            # MAX single green candle
            out.write(f"\n    MAX single green body:\n")
            for mlo,mhi,mlbl in [(0,0.2,'<0.2%'),(0.2,0.5,'0.2-0.5%'),(0.5,1,'0.5-1%'),(1,99,'>1%')]:
                sub = [t for t in all_sell if mlo<=t[f'_mg_{check_b}']<mhi]
                if len(sub)<15: continue
                wr = sum(1 for t in sub if t['ret90_sell']>0)/len(sub)*100
                out.write(f"    {mlbl:>10} {len(sub):>5} {wr:>5.1f}% {np.mean([t['ret90_sell'] for t in sub]):>+7.3f}%\n")

        # ═══════════════════════════════════════════
        # #C: POSITION SIZING — add to winners, cut losers
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#C: POSITION SIZING — ADD to winners, CUT losers at b20\n"+"="*110+"\n")
        out.write("  Base: 10k per trade. At b20, adjust based on per-share signals.\n\n")

        def sim_sizing(trades_by_day, sizing_fn, check_b=19):
            """sizing_fn(trade, check_b) -> capital multiplier (1.0=no change, 1.5=add, 0.5=cut, 0=exit)"""
            total_pnl_rs = 0; days = 0; day_wins = 0
            for d in dates:
                picks = trades_by_day.get(d, [])
                if not picks: continue
                days += 1
                day_pnl = 0
                for t in picks:
                    mult = sizing_fn(t, check_b)
                    position = BASE_CAPITAL * MARGIN * mult  # actual position value
                    ret_pct = t['ret90_sell']
                    pnl_rs = position * ret_pct / 100
                    day_pnl += pnl_rs
                total_pnl_rs += day_pnl
                if day_pnl > 0: day_wins += 1
            total_capital = BASE_CAPITAL * 8  # 80k
            roc = total_pnl_rs / total_capital * 100
            return roc, day_wins/max(days,1)*100, days

        sizing_strategies = {
            'EQUAL (baseline, 1x all)':
                lambda t, cb: 1.0,
            'CUT losers to 0.5x at b20':
                lambda t, cb: 0.5 if t['pnl_sell'].get(cb,0)<0 else 1.0,
            'CUT losers to 0x (EXIT) at b20':
                lambda t, cb: 0.0 if t['pnl_sell'].get(cb,0)<-0.3 else 1.0,
            'ADD winners 1.5x, CUT losers 0.5x':
                lambda t, cb: 1.5 if t['pnl_sell'].get(cb,0)>0.3 else 0.5 if t['pnl_sell'].get(cb,0)<-0.3 else 1.0,
            'ADD winners 2x, CUT losers 0.5x':
                lambda t, cb: 2.0 if t['pnl_sell'].get(cb,0)>0.3 else 0.5 if t['pnl_sell'].get(cb,0)<-0.3 else 1.0,
            'ADD winners 1.5x + below VWAP, CUT above VWAP losers':
                lambda t, cb: 1.5 if t['pnl_sell'].get(cb,0)>0.2 and t['vwap_pos'].get(cb,0)<0 else 0.3 if t['pnl_sell'].get(cb,0)<-0.3 and t['vwap_pos'].get(cb,0)>0 else 1.0,
            'ADD 2x if WIN+belowVWAP, EXIT if LOSE>0.5%+aboveVWAP':
                lambda t, cb: 2.0 if t['pnl_sell'].get(cb,0)>0.3 and t['vwap_pos'].get(cb,0)<-0.3 else 0.0 if t['pnl_sell'].get(cb,0)<-0.5 and t['vwap_pos'].get(cb,0)>0.3 else 1.0,
            'Progressive: WIN>1%->2x, WIN>0.3%->1.5x, LOSE>0.5%->0.3x':
                lambda t, cb: 2.0 if t['pnl_sell'].get(cb,0)>1.0 else 1.5 if t['pnl_sell'].get(cb,0)>0.3 else 0.3 if t['pnl_sell'].get(cb,0)<-0.5 else 1.0,
        }

        out.write(f"  {'Strategy':<60} {'ROC%':>7} {'DayWin':>7}\n  "+"-"*80+"\n")
        for name, sfn in sizing_strategies.items():
            roc, dw, _ = sim_sizing(daily_sell_picks, sfn, 19)
            marker = " <<<" if 'baseline' in name.lower() else ""
            out.write(f"  {name:<60} {roc:>+6.1f}% {dw:>6.1f}%{marker}\n")

        # Test at different check buckets
        out.write(f"\n  Best sizing at different check times:\n")
        best_fn = lambda t, cb: 2.0 if t['pnl_sell'].get(cb,0)>0.3 and t['vwap_pos'].get(cb,0)<-0.3 else 0.0 if t['pnl_sell'].get(cb,0)<-0.5 and t['vwap_pos'].get(cb,0)>0.3 else 1.0
        for cb in [9, 14, 19, 24, 29]:
            h=9+(15+cb)//60; m=(15+cb)%60
            roc, dw, _ = sim_sizing(daily_sell_picks, best_fn, cb)
            roc_base, _, _ = sim_sizing(daily_sell_picks, lambda t,cb:1.0, cb)
            out.write(f"    b{cb+1} ({h}:{m:02d}): sized={roc:+.1f}% vs equal={roc_base:+.1f}% delta={roc-roc_base:+.1f}%\n")

        # ═══════════════════════════════════════════
        # #D: HALF-EXIT AT TP + TRAIL REST
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#D: HALF-EXIT AT TARGET + TRAIL REST WITH SL\n"+"="*110+"\n")

        def sim_half_exit(trades, tp_pct, sl_pct, trail_pct, max_b=89):
            """
            Half exit: when MFE reaches tp_pct, exit 50% at tp_pct.
            Trail remaining 50%: trail by trail_pct behind peak.
            SL on entire position: if loss exceeds sl_pct, exit all.
            NO LOOKAHEAD: check bucket-by-bucket.
            """
            total_pnl = 0; wins = 0; n = 0; green_days = 0; total_days = 0
            by_day = defaultdict(list)
            for t in trades: by_day[t['date']].append(t)

            for d, day_trades in by_day.items():
                day_pnl = 0; total_days += 1
                for t in day_trades:
                    half1_done = False; half1_ret = 0
                    peak_pnl = 0; trail_active = False
                    position_mult = 1.0  # 1.0 = full, 0.5 = half exited
                    final_ret = 0; exited = False

                    for b in sorted(t['pnl_sell'].keys()):
                        if b > max_b: break
                        pnl = t['pnl_sell'][b]

                        # SL check (on remaining position)
                        if pnl < -sl_pct:
                            final_ret = half1_ret * 0.5 + pnl * position_mult - COST
                            exited = True; break

                        # Track peak for trailing
                        if pnl > peak_pnl: peak_pnl = pnl

                        # Half exit at TP
                        if not half1_done and pnl >= tp_pct:
                            half1_ret = tp_pct
                            half1_done = True
                            position_mult = 0.5
                            trail_active = True
                            continue

                        # Trail stop on remaining half
                        if trail_active and peak_pnl - pnl >= trail_pct:
                            trail_exit = peak_pnl - trail_pct
                            final_ret = half1_ret * 0.5 + trail_exit * 0.5 - COST
                            exited = True; break

                    if not exited:
                        last_pnl = t['pnl_sell'].get(max_b, 0)
                        final_ret = half1_ret * 0.5 + last_pnl * position_mult - COST if half1_done else last_pnl - COST

                    day_pnl += final_ret * BASE_CAPITAL * MARGIN / 100
                    n += 1
                    if final_ret > 0: wins += 1

                total_pnl += day_pnl
                if day_pnl > 0: green_days += 1

            roc = total_pnl / (BASE_CAPITAL * 8) * 100
            return roc, wins/max(n,1)*100, green_days/max(total_days,1)*100, n

        out.write(f"  HALF-EXIT strategies:\n")
        out.write(f"  {'TP%':>4} {'SL%':>4} {'Trail%':>6} {'ROC':>8} {'TrdWin':>7} {'DayWin':>7}\n  "+"-"*45+"\n")

        # Baseline: no half exit, just fixed b90
        roc_base = sum(t['ret90_sell'] for t in all_sell) / len(all_sell) * len(all_sell) / (BASE_CAPITAL*8) * BASE_CAPITAL * MARGIN
        # Actually compute properly
        base_total = sum(t['ret90_sell'] * BASE_CAPITAL * MARGIN / 100 for t in all_sell)
        base_roc = base_total / (BASE_CAPITAL * 8) * 100
        base_wr = sum(1 for t in all_sell if t['ret90_sell']>0)/len(all_sell)*100
        out.write(f"  {'—':>4} {'—':>4} {'b90':>6} {base_roc:>+7.1f}% {base_wr:>6.1f}% {'—':>7}  (baseline)\n\n")

        for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
            for sl in [0.5, 1.0, 1.5, 2.0]:
                for trail in [0.2, 0.3, 0.5]:
                    roc, tw, dw, n = sim_half_exit(all_sell, tp, sl, trail)
                    if roc > base_roc * 0.5:  # only show decent ones
                        out.write(f"  {tp:>4.1f} {sl:>4.1f} {trail:>6.1f} {roc:>+7.1f}% {tw:>6.1f}% {dw:>6.1f}%\n")

        # Best half-exit
        out.write(f"\n  Finding BEST half-exit combo...\n")
        best_roc = -999; best_params = ""
        for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
            for sl in [0.3, 0.5, 1.0, 1.5, 2.0, 3.0]:
                for trail in [0.15, 0.2, 0.3, 0.5]:
                    roc, tw, dw, _ = sim_half_exit(all_sell, tp, sl, trail)
                    if roc > best_roc:
                        best_roc = roc; best_params = f"TP={tp} SL={sl} Trail={trail} -> ROC={roc:+.1f}% TrdWin={tw:.1f}% DayWin={dw:.1f}%"
        out.write(f"  BEST: {best_params}\n")
        out.write(f"  vs BASELINE (fixed b90): ROC={base_roc:+.1f}%\n")

        # ═══════════════════════════════════════════
        # #E: BUY SIGNALS on high-score sell days
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#E: BUY SIGNAL — can we also buy on days we don't sell?\n"+"="*110+"\n")

        # BUY scoring: mirror of sell — gap-down + buy pressure
        for d in dates:
            for t in by_date_buy.get(d,[]):
                bp = 1 - t['sp']  # invert: for buy, high close_pos = buyers winning
                buy_mom = -t['mom']  # for buy, positive momentum = bouncing up
                t['buy_score'] = abs(t['gap']) * bp * (1.3 if buy_mom>0 else 0.7)

        out.write(f"\n  BUY gap-down reversal (b45 exit):\n")
        buy_scorers = {
            'abs_gap': lambda t: abs(t['gap']),
            'abs_gap * buy_pressure': lambda t: abs(t['gap']) * (1-t['sp']),
            'abs_gap * bp * (mom_up?1.3:.7)': lambda t: abs(t['gap']) * (1-t['sp']) * (1.3 if t['mom']>0 else 0.7),
        }

        for s_name, scorer in buy_scorers.items():
            total=0; dw=0; trades=0; wt=0
            for d in dates:
                pool = by_date_buy.get(d,[])
                if len(pool)<3: continue
                for t in pool: t['_bsc'] = scorer(t)
                pool.sort(key=lambda x:-x['_bsc'])
                picks = pool[:8]
                for t in picks:
                    ret = t['pnl_buy'].get(44, 0) - COST  # BUY, exit b45
                    total += ret; trades += 1
                    if ret > 0: wt += 1
                if sum(t['pnl_buy'].get(44,0)-COST for t in picks) > 0: dw += 1
            nd = len(dates)
            out.write(f"    {s_name:<45} total={total:>+7.1f}% dayW={dw/max(nd,1)*100:.1f}% trdW={wt/max(trades,1)*100:.1f}%\n")

        # Combined: SELL on sell days + BUY on buy days (separate capital)
        out.write(f"\n  COMBINED: SELL top-8 + BUY top-8 (separate capital, 80k each):\n")
        combined_total = 0; combined_days = 0; combined_wins = 0
        for d in dates:
            sell_picks = daily_sell_picks.get(d,[])
            buy_pool = sorted(by_date_buy.get(d,[]), key=lambda x:-abs(x['gap'])*(1-x['sp']))[:8]

            sell_ret = sum(t['ret90_sell'] for t in sell_picks)
            buy_ret = sum(t['pnl_buy'].get(44,0)-COST for t in buy_pool)
            day_ret = sell_ret + buy_ret
            combined_total += day_ret
            combined_days += 1
            if day_ret > 0: combined_wins += 1

        out.write(f"    Total: {combined_total:+.1f}%  DayWin: {combined_wins/combined_days*100:.1f}%\n")
        out.write(f"    vs SELL only: {sum(t['ret90_sell'] for t in all_sell):+.1f}%\n")

        # BUY only on days with score >= 15 (high confidence)
        out.write(f"\n  HIGH-SCORE BUY (only when sell pool has score>=15 stocks):\n")
        for score_thresh in [10, 15, 20]:
            total_b=0; days_b=0; wins_b=0
            for d in dates:
                sell_pool = by_date_sell.get(d,[])
                high_score = any(t['score']>=score_thresh for t in sell_pool)
                if not high_score: continue
                buy_pool = sorted(by_date_buy.get(d,[]), key=lambda x:-abs(x['gap'])*(1-x['sp']))[:4]
                if not buy_pool: continue
                days_b += 1
                day_ret = sum(t['pnl_buy'].get(44,0)-COST for t in buy_pool)
                total_b += day_ret
                if day_ret > 0: wins_b += 1
            if days_b > 0:
                out.write(f"    SellScore>={score_thresh}: {days_b} days, total={total_b:+.1f}%, dayWin={wins_b/days_b*100:.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
