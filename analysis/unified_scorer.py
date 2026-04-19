"""
UNIFIED SCORER — One score for both BUY and SELL
===================================================
Instead of separate sell/buy pools, score ALL stocks on one scale.
Higher score = higher conviction trade, regardless of direction.

The scorer must:
  1. Detect direction (sell gap-up reversal OR buy gap-down reversal)
  2. Score based on pattern strength
  3. Cherry-pick top N from the unified pool
  4. Each pick has its own direction + exit

NO LOOKAHEAD. All features from buckets 0-5.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'unified_scorer.txt'
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
                if r['symbol'] not in liquid: continue
                if abs(r['gapPct']) > 10 or abs(r['gapPct']) < 0.5: continue
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                if bkt[0,O]<=0 or bkt[0,H]==bkt[0,L]: continue
                entry = bkt[6,O]
                if entry<=0: continue

                gap = r['gapPct']; price = r['dayOpen']
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6; bp = cp/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                n_green = 6-n_red
                vwap_dev = (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0
                avg_br = np.mean([float(bkt[i,BR]) for i in range(6)])

                # Returns for SELL direction (gap-up reversal)
                sell_ret = {}
                for eb in [44,65,89]:
                    if bkt[eb,C]>0: sell_ret[eb] = (entry-bkt[eb,C])/entry*100-COST

                # Returns for BUY direction (gap-down reversal)
                buy_ret = {}
                for eb in [29,44,65]:
                    if bkt[eb,C]>0: buy_ret[eb] = (bkt[eb,C]-entry)/entry*100-COST

                # Determine natural direction
                direction = 'sell' if gap > 0 else 'buy'

                # Direction-specific pressure
                if direction == 'sell':
                    pressure = sp  # sell pressure (high = sellers dominating)
                    momentum_aligned = mom < 0  # price dropping = good for sell
                    candle_aligned = n_red >= 3  # red candles = sellers
                    vwap_aligned = vwap_dev < 0  # below VWAP = sellers winning
                    br_aligned = avg_br < 0.5  # low buy ratio = sellers
                else:
                    pressure = bp  # buy pressure (high = buyers dominating)
                    momentum_aligned = mom > 0  # price rising = good for buy
                    candle_aligned = n_green >= 3  # green candles = buyers
                    vwap_aligned = vwap_dev > 0  # above VWAP = buyers winning
                    br_aligned = avg_br > 0.5  # high buy ratio = buyers

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':gap,'price':price,'entry':entry,
                    'dir':direction,'sp':sp,'bp':bp,'mom':mom,
                    'n_red':n_red,'n_green':n_green,'vwap_dev':vwap_dev,'avg_br':avg_br,
                    'pressure':pressure,'mom_aligned':momentum_aligned,
                    'candle_aligned':candle_aligned,'vwap_aligned':vwap_aligned,
                    'br_aligned':br_aligned,
                    'sell_ret':sell_ret,'buy_ret':buy_ret,
                    'date':r['date'],
                })

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} records in {time.time()-t0:.1f}s")

    # ── UNIFIED SCORERS ──
    # All return (score, direction, exit_bucket)
    # Score is ALWAYS positive — higher = better trade

    def U1_gap_pressure(r):
        """abs(gap) * pressure * (pressure>0.5?1:0.3)"""
        sc = abs(r['gap']) * r['pressure'] * (1.0 if r['pressure']>0.5 else 0.3) * 10
        eb = 89 if r['dir']=='sell' else 44
        return sc, r['dir'], eb

    def U2_gap_pressure_price(r):
        """abs(gap) * (pressure>0.5?1:0.3) * (price<500?1.2:0.9)"""
        sc = abs(r['gap']) * (1.0 if r['pressure']>0.5 else 0.3) * (1.2 if r['price']<500 else 0.9) * 10
        eb = 89 if r['dir']=='sell' else 44
        return sc, r['dir'], eb

    def U3_gap_aligned(r):
        """abs(gap) * (all_aligned?2:partial?1.2:0.3)"""
        aligned = sum([r['pressure']>0.5, r['mom_aligned'], r['candle_aligned'], r['vwap_aligned']])
        mult = 2.0 if aligned>=3 else 1.2 if aligned>=2 else 0.5 if aligned>=1 else 0.1
        sc = abs(r['gap']) * mult * 10
        eb = 89 if r['dir']=='sell' else 44
        return sc, r['dir'], eb

    def U4_conviction(r):
        """Multi-factor conviction score"""
        sc = 0
        sc += abs(r['gap']) * 2  # gap magnitude
        if r['pressure'] > 0.6: sc += 3
        elif r['pressure'] > 0.5: sc += 1
        else: sc -= 2
        if r['mom_aligned']: sc += 2
        if r['candle_aligned']: sc += 1.5
        if r['vwap_aligned']: sc += 1.5
        if r['br_aligned']: sc += 1
        if r['price'] < 500: sc += 0.5
        eb = 89 if r['dir']=='sell' else 44
        return max(sc, 0), r['dir'], eb

    def U5_gap_pressure_mom(r):
        """abs(gap) * (pressure>0.5?1:0.3) * (mom_aligned?1.3:0.7)"""
        sc = abs(r['gap']) * (1.0 if r['pressure']>0.5 else 0.3) * (1.3 if r['mom_aligned'] else 0.7) * 10
        eb = 89 if r['dir']=='sell' else 44
        return sc, r['dir'], eb

    def U6_s6_mirror(r):
        """S6 for sell, S6-buy for buy — unified"""
        if r['dir']=='sell':
            sc = r['gap']*(1.0 if r['sp']>0.5 else 0.3)*(1.2 if r['price']<500 else 0.9)*10
        else:
            sc = abs(r['gap'])*(1.0 if r['bp']>0.5 else 0.3)*(1.2 if r['price']<500 else 0.9)*10
        eb = 89 if r['dir']=='sell' else 44
        return sc, r['dir'], eb

    def U7_adaptive_exit(r):
        """Same as U4 but adaptive exit: sell b90, buy b45 if big gap, b30 if small"""
        sc = 0
        sc += abs(r['gap']) * 2
        if r['pressure'] > 0.6: sc += 3
        elif r['pressure'] > 0.5: sc += 1
        else: sc -= 2
        if r['mom_aligned']: sc += 2
        if r['candle_aligned']: sc += 1.5
        if r['vwap_aligned']: sc += 1.5
        if r['br_aligned']: sc += 1
        if r['price'] < 500: sc += 0.5
        if r['dir']=='sell':
            eb = 89
        else:
            eb = 44 if abs(r['gap'])>2 else 29
        return max(sc, 0), r['dir'], eb

    def U8_pure_conviction(r):
        """Ignore gap size, score purely on conviction signals"""
        sc = 0
        if r['pressure'] > 0.65: sc += 4
        elif r['pressure'] > 0.55: sc += 2
        elif r['pressure'] > 0.5: sc += 1
        if r['mom_aligned']:
            sc += 3 if abs(r['mom'])>0.5 else 1.5
        if r['candle_aligned']: sc += 2
        if r['vwap_aligned']: sc += 2
        if r['br_aligned']: sc += 1
        # Gap as tiebreaker only
        sc += min(abs(r['gap'])*0.5, 2)
        eb = 89 if r['dir']=='sell' else 44
        return max(sc, 0), r['dir'], eb

    scorers = {
        'U1: gap*pressure': U1_gap_pressure,
        'U2: gap*pressure*price (S6-like)': U2_gap_pressure_price,
        'U3: gap*aligned_count': U3_gap_aligned,
        'U4: multi-factor conviction': U4_conviction,
        'U5: gap*pressure*momentum': U5_gap_pressure_mom,
        'U6: S6 mirror (sell/buy)': U6_s6_mirror,
        'U7: conviction + adaptive exit': U7_adaptive_exit,
        'U8: pure conviction (gap=tiebreaker)': U8_pure_conviction,
    }

    def sim_unified(scorer_fn, n_pos):
        total_pnl=0; dw=0; active=0; trades=0; wt=0
        sell_trades=0; buy_trades=0; sell_wins=0; buy_wins=0

        for d in dates:
            pool = by_date[d]
            if len(pool)<1: continue
            # Score each stock
            scored = []
            for r in pool:
                sc, direction, eb = scorer_fn(r)
                scored.append((sc, direction, eb, r))
            scored.sort(key=lambda x:-x[0])
            picks = scored[:n_pos]
            active+=1; day_pnl=0

            for sc, direction, eb, r in picks:
                trades+=1
                if direction == 'sell':
                    ret = r['sell_ret'].get(eb, 0)
                    sell_trades+=1
                    if ret>0: sell_wins+=1
                else:
                    ret = r['buy_ret'].get(eb, 0)
                    buy_trades+=1
                    if ret>0: buy_wins+=1

                pnl_rs = BASE*MARGIN*ret/100
                day_pnl+=pnl_rs
                if pnl_rs>0: wt+=1

            total_pnl+=day_pnl
            if day_pnl>0: dw+=1

        roc = total_pnl/(BASE*n_pos)*100
        return {
            'roc':roc,'dw':dw/max(active,1)*100,'tw':wt/max(trades,1)*100,
            'trades':trades,'sell_n':sell_trades,'buy_n':buy_trades,
            'sell_wr':sell_wins/max(sell_trades,1)*100,
            'buy_wr':buy_wins/max(buy_trades,1)*100,
        }

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("UNIFIED SCORER — One score for BUY and SELL\n")
        out.write(f"Records: {n}, Days: {len(dates)}\n\n")

        # ═══════════════════════════════════════
        # 1. SCORER COMPARISON
        # ═══════════════════════════════════════
        out.write("="*120+"\n1. UNIFIED SCORER COMPARISON (top-8, mixed BUY+SELL)\n"+"="*120+"\n\n")
        out.write(f"  {'Scorer':<50} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'Sells':>5} {'Buys':>5} {'SellWR':>7} {'BuyWR':>7}\n")
        out.write("  "+"-"*100+"\n")

        results = []
        for name, fn in scorers.items():
            for npos in [6,7,8,10]:
                r = sim_unified(fn, npos)
                results.append((r['dw'], f"{name}|top{npos}", r['roc'], r['tw'],
                               r['sell_n'], r['buy_n'], r['sell_wr'], r['buy_wr']))

        results.sort(key=lambda x: (-x[0], -x[2]))
        for dw, name, roc, tw, sn, bn, swr, bwr in results[:40]:
            out.write(f"  {name:<50} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {sn:>5} {bn:>5} {swr:>6.1f}% {bwr:>6.1f}%\n")

        # ═══════════════════════════════════════
        # 2. SELL-ONLY vs BUY-ONLY vs MIXED
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*120+"\n2. SELL-ONLY vs BUY-ONLY vs MIXED comparison\n"+"="*120+"\n")

        # Sell only: filter to gap>0 stocks
        def sim_direction_only(scorer_fn, n_pos, direction_filter):
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                pool = [r for r in by_date[d] if r['dir']==direction_filter]
                if len(pool)<1: continue
                scored = [(scorer_fn(r)[0], scorer_fn(r)[1], scorer_fn(r)[2], r) for r in pool]
                scored.sort(key=lambda x:-x[0])
                picks = scored[:n_pos]
                active+=1; day_pnl=0
                for sc, direction, eb, r in picks:
                    trades+=1
                    ret = r['sell_ret'].get(eb,0) if direction=='sell' else r['buy_ret'].get(eb,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1
                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            roc = total_pnl/(BASE*n_pos)*100
            return roc, dw/max(active,1)*100, wt/max(trades,1)*100, trades

        best_fn = U4_conviction  # will determine from results
        best_name = "U4: multi-factor conviction"

        out.write(f"\n  Using {best_name}, top-8:\n")
        for mode, filt in [('SELL only','sell'),('BUY only','buy'),('MIXED (unified)','both')]:
            if filt=='both':
                r = sim_unified(best_fn, 8)
                out.write(f"    {mode:<20}: ROC={r['roc']:+.1f}%  dayWin={r['dw']:.1f}%  trdWin={r['tw']:.1f}%  trades={r['trades']}\n")
            else:
                roc,dw,tw,nt = sim_direction_only(best_fn, 8, filt)
                out.write(f"    {mode:<20}: ROC={roc:+.1f}%  dayWin={dw:.1f}%  trdWin={tw:.1f}%  trades={nt}\n")

        # ═══════════════════════════════════════
        # 3. SPLIT ALLOCATION: N sell + M buy
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*120+"\n3. SPLIT ALLOCATION: N sell + M buy from unified pool\n"+"="*120+"\n")

        def sim_split(scorer_fn, n_sell, n_buy):
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                pool = by_date[d]
                sell_pool = [(scorer_fn(r), r) for r in pool if r['dir']=='sell']
                buy_pool = [(scorer_fn(r), r) for r in pool if r['dir']=='buy']
                sell_pool.sort(key=lambda x:-x[0][0])
                buy_pool.sort(key=lambda x:-x[0][0])

                picks = []
                for (sc,direction,eb),r in sell_pool[:n_sell]:
                    picks.append((direction,eb,r))
                for (sc,direction,eb),r in buy_pool[:n_buy]:
                    picks.append((direction,eb,r))

                if not picks: continue
                active+=1; day_pnl=0
                for direction, eb, r in picks:
                    trades+=1
                    ret = r['sell_ret'].get(eb,0) if direction=='sell' else r['buy_ret'].get(eb,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1
                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            total_pos = n_sell+n_buy
            roc = total_pnl/(BASE*total_pos)*100
            return roc, dw/max(active,1)*100, wt/max(trades,1)*100, trades

        out.write(f"  {'Allocation':<20} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'Trades':>6}\n  "+"-"*50+"\n")
        for ns, nb in [(8,0),(7,1),(6,2),(5,3),(4,4),(3,5),(2,6),(0,8)]:
            roc,dw,tw,nt = sim_split(best_fn, ns, nb)
            out.write(f"  {f'{ns}S+{nb}B':>20} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}% {nt:>6}\n")

        # ═══════════════════════════════════════
        # 4. UNIFIED POOL: let scorer decide mix naturally
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*120+"\n4. NATURAL MIX: what ratio does the unified scorer pick?\n"+"="*120+"\n")

        for name, fn in [('U4: conviction', U4_conviction), ('U6: S6 mirror', U6_s6_mirror),
                          ('U8: pure conviction', U8_pure_conviction)]:
            sell_per_day = []; buy_per_day = []
            for d in dates:
                pool = by_date[d]
                scored = [(fn(r), r) for r in pool]
                scored.sort(key=lambda x:-x[0][0])
                picks = scored[:8]
                ns = sum(1 for (sc,d_,eb),r in picks if d_=='sell')
                nb = sum(1 for (sc,d_,eb),r in picks if d_=='buy')
                sell_per_day.append(ns); buy_per_day.append(nb)

            out.write(f"  {name}: avg sell/day={np.mean(sell_per_day):.1f}, avg buy/day={np.mean(buy_per_day):.1f}\n")
            out.write(f"    Sell range: {min(sell_per_day)}-{max(sell_per_day)}, Buy range: {min(buy_per_day)}-{max(buy_per_day)}\n\n")

        # ═══════════════════════════════════════
        # 5. BEST OVERALL + SIZING
        # ═══════════════════════════════════════
        out.write("="*120+"\n5. BEST UNIFIED + SIZING\n"+"="*120+"\n")

        # ADD sizing on unified picks
        def sim_unified_sizing(scorer_fn, n_pos, check_b=14):
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                pool = by_date[d]
                scored = [(scorer_fn(r), r) for r in pool]
                scored.sort(key=lambda x:-x[0][0])
                picks = scored[:n_pos]
                active+=1; day_pnl=0

                for (sc, direction, exit_b), r in picks:
                    trades+=1
                    bkt = None  # we don't have bkt in this simplified version
                    # Use the ret directly, but simulate ADD check
                    if direction == 'sell':
                        if check_b in r.get('sell_ret',{}):
                            early_pnl = r['sell_ret'].get(check_b, 0) + COST  # raw
                        else:
                            early_pnl = 0
                        final_ret = r['sell_ret'].get(exit_b, 0)
                    else:
                        if check_b in r.get('buy_ret',{}):
                            early_pnl = r['buy_ret'].get(check_b, 0) + COST
                        else:
                            early_pnl = 0
                        final_ret = r['buy_ret'].get(exit_b, 0)

                    # Simple ADD: if early_pnl > 0.3%, multiply remaining by 3x
                    if early_pnl > 0.3:
                        remaining = final_ret - (early_pnl - COST)
                        ret = (early_pnl - COST) + remaining * 3.0
                    else:
                        ret = final_ret

                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1

                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            roc = total_pnl/(BASE*n_pos)*100
            return roc, dw/max(active,1)*100, wt/max(trades,1)*100

        out.write(f"\n  Unified scorer + ADD 3x at b15 if early_pnl>0.3%:\n")
        for name, fn in scorers.items():
            roc,dw,tw = sim_unified_sizing(fn, 8)
            out.write(f"    {name:<50} ROC={roc:>+7.1f}%  dayWin={dw:.1f}%\n")

        # ═══════════════════════════════════════
        # 6. VERDICT
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*120+"\n6. VERDICT\n"+"="*120+"\n")

        # Find best
        best = max(results, key=lambda x: x[0])
        sell_only = sim_direction_only(U6_s6_mirror, 8, 'sell')
        out.write(f"\n  SELL-ONLY (S6, top-8): ROC={sell_only[0]:+.1f}%  dayWin={sell_only[1]:.1f}%\n")
        out.write(f"  BEST UNIFIED: {best[1]}\n")
        out.write(f"    ROC={best[2]:+.1f}%  dayWin={best[0]:.1f}%  sellWin={best[6]:.1f}%  buyWin={best[7]:.1f}%\n")
        out.write(f"    Sells={best[4]}, Buys={best[5]}\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
