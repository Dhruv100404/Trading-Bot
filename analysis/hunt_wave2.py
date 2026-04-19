"""
HUNT WAVE 2 — After sizing bug fix, re-validate everything
=============================================================
The sizing bug inflated ADD returns. Now with correct weighted-entry fix,
re-test all scorers and sizing combos on clean data.

Then go DEEPER:
  - Intraday regime detection (market-wide signal)
  - Per-stock momentum persistence
  - Cross-day patterns
  - Volume-at-time patterns
  - Bid-ask spread signals (if available)
  - Multi-timeframe confirmation
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict
import datetime

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'hunt_wave2.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000; MARGIN = 5

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    all_by_date = defaultdict(list)  # ALL stocks (for market context)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                all_by_date[r['date']].append(r['gapPct'])
                if r['gapPct'] <= 0.5: continue
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

                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                price = r['dayOpen']; gap = r['gapPct']
                ret90 = (entry-bkt[89,C])/entry*100-COST

                scores = {
                    'plain': gap*10,
                    'v2': gap*sp*(1.4 if mom<-0.5 else 1.1 if mom<0 else 0.7)*15,
                    's6': gap*(1.0 if sp>0.5 else 0.3)*(1.2 if price<500 else 0.9)*10,
                }

                # Live data at check points (CORRECTED: P&L includes COST)
                live = {}
                for cb in [9,11,14,19,24,29]:
                    if bkt[cb,C]<=0: continue
                    pnl = (entry-bkt[cb,C])/entry*100  # raw pnl without cost
                    vwap = (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0
                    ng = sum(1 for b in range(7,cb+1) if bkt[b,C]>bkt[b,O])
                    live[cb] = {'pnl':pnl, 'vwap':vwap, 'ng':ng, 'price':bkt[cb,C]}

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':gap,'price':price,'entry':entry,
                    'sp':sp,'mom':mom,'n_red':n_red,
                    'scores':scores,'ret90':ret90,'win':ret90>0,
                    'live':live,'bkt':bkt,'date':r['date'],
                })

    dates = sorted(by_date.keys())
    # Market context
    mkt = {}
    for d in dates:
        gaps = all_by_date[d]
        mkt[d] = {
            'avg_gap': np.mean(gaps),
            'pct_up': sum(1 for g in gaps if g>0)/len(gaps)*100,
            'n_gapup': sum(1 for g in gaps if g>0.5),
        }

    print(f"Loaded {sum(len(v) for v in by_date.values())} records in {time.time()-t0:.1f}s")

    def sim_corrected(scorer_key, n_pos, sizing_fn=None, check_b=14):
        """CORRECTED simulation: ADD uses weighted entry, EXIT uses actual check P&L."""
        total_pnl=0; dw=0; active=0; trades=0; wt=0

        for d in dates:
            pool = by_date[d]
            if len(pool)<1: continue
            pool.sort(key=lambda x:-x['scores'][scorer_key])
            picks = pool[:n_pos]
            active+=1; day_pnl=0

            for t in picks:
                trades+=1
                if sizing_fn and check_b in t['live']:
                    action = sizing_fn(t['live'][check_b])
                    check_pnl_raw = t['live'][check_b]['pnl']  # without cost
                    check_price = t['live'][check_b]['price']

                    if action[0] == 'exit':
                        pnl_rs = BASE*MARGIN*(check_pnl_raw-COST)/100
                    elif action[0] == 'add':
                        mult = action[1]
                        # CORRECTED: weighted entry approach
                        # orig shares: entry -> exit = ret90 + COST (add cost back for raw)
                        # extra shares: check_price -> exit
                        raw_ret90 = t['ret90'] + COST
                        exit_price = t['entry'] * (1 - raw_ret90/100)
                        # Original: (entry - exit) * orig_qty
                        # Extra: (check_price - exit) * extra_qty
                        orig_pnl = raw_ret90  # % from entry
                        extra_pnl = (check_price - exit_price)/check_price*100 if check_price>0 else 0
                        total_raw = orig_pnl + extra_pnl * (mult-1)
                        pnl_rs = BASE*MARGIN*(total_raw - COST)/100
                    else:
                        pnl_rs = BASE*MARGIN*t['ret90']/100
                else:
                    pnl_rs = BASE*MARGIN*t['ret90']/100

                day_pnl+=pnl_rs
                if pnl_rs>0: wt+=1

            total_pnl+=day_pnl
            if day_pnl>0: dw+=1

        roc = total_pnl/(BASE*n_pos)*100
        return roc, dw/max(active,1)*100, wt/max(trades,1)*100, trades

    def no_sizing(live): return ('hold',)
    def make_add(min_pnl, mult):
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
        out.write("HUNT WAVE 2 — Post sizing-bug-fix validation\n")
        out.write(f"Data: {sum(len(v) for v in by_date.values())} records, {len(dates)} days\n")
        out.write(f"Sizing bug FIXED: ADD uses weighted entry, not full-trade multiplier\n\n")

        # ═══════════════════════════════════════
        # 1. CORRECTED SCORER COMPARISON
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. SCORER COMPARISON (corrected, no sizing)\n"+"="*110+"\n")
        for scorer in ['plain','v2','s6']:
            for npos in [6,7,8]:
                roc,dw,tw,nt = sim_corrected(scorer, npos)
                out.write(f"  {scorer:>6} top-{npos}: ROC={roc:>+7.1f}%  dayWin={dw:.1f}%  trdWin={tw:.1f}%\n")

        # ═══════════════════════════════════════
        # 2. CORRECTED SIZING (all combos)
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. CORRECTED SIZING (weighted entry fix)\n"+"="*110+"\n")
        results = []
        for scorer in ['v2','s6','plain']:
            for npos in [6,7,8]:
                # No sizing baseline
                roc,dw,tw,nt = sim_corrected(scorer, npos)
                results.append((dw, f"{scorer}|top{npos}|no_sizing", roc, tw))

                for cb in [9,14,19]:
                    for min_pnl in [0.3, 0.5]:
                        for mult in [2.0, 3.0]:
                            fn = make_add(min_pnl, mult)
                            roc,dw,tw,nt = sim_corrected(scorer, npos, fn, cb)
                            results.append((dw, f"{scorer}|top{npos}|ADD{mult}x@b{cb+1}(>{min_pnl}%)", roc, tw))

                        fn = make_add_exit(0.3, mult, 0.5, 0.3)
                        roc,dw,tw,nt = sim_corrected(scorer, npos, fn, cb)
                        results.append((dw, f"{scorer}|top{npos}|ADD{mult}x+EXIT@b{cb+1}", roc, tw))

        results.sort(key=lambda x: (-x[0], -x[2]))
        out.write(f"  {'Strategy':<65} {'DayWin':>7} {'ROC':>8} {'TrdWin':>7}\n  "+"-"*90+"\n")
        for dw,name,roc,tw in results[:50]:
            out.write(f"  {name:<65} {dw:>6.1f}% {roc:>+7.1f}% {tw:>6.1f}%\n")

        # ═══════════════════════════════════════
        # 3. MARKET REGIME: does market-wide data predict day quality?
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. MARKET REGIME — can we predict good vs bad days?\n"+"="*110+"\n")

        day_results = []
        for d in dates:
            pool = sorted(by_date[d], key=lambda x:-x['scores']['s6'])[:7]
            if not pool: continue
            day_ret = sum(t['ret90'] for t in pool)
            mc = mkt[d]
            dow = datetime.date.fromisoformat(d).strftime('%A')
            day_results.append({
                'date':d, 'ret':day_ret, 'win':day_ret>0,
                'mkt_pct_up':mc['pct_up'], 'mkt_avg_gap':mc['avg_gap'],
                'n_gapup':mc['n_gapup'], 'dow':dow,
                'avg_sp':np.mean([t['sp'] for t in pool]),
                'avg_mom':np.mean([t['mom'] for t in pool]),
                'avg_gap':np.mean([t['gap'] for t in pool]),
            })

        # Day of week
        out.write(f"\n  Day of week:\n")
        for dow in ['Monday','Tuesday','Wednesday','Thursday','Friday']:
            sub = [d for d in day_results if d['dow']==dow]
            if len(sub)<5: continue
            wr = sum(d['win'] for d in sub)/len(sub)*100
            ar = np.mean([d['ret'] for d in sub])
            out.write(f"    {dow:>12}: N={len(sub):>3} dayWin={wr:.1f}% avgRet={ar:+.2f}%\n")

        # Market breadth
        out.write(f"\n  Market breadth (% stocks gapped up):\n")
        for lo,hi,lbl in [(0,40,'<40%'),(40,50,'40-50%'),(50,60,'50-60%'),(60,100,'>60%')]:
            sub = [d for d in day_results if lo<=d['mkt_pct_up']<hi]
            if len(sub)<3: continue
            wr = sum(d['win'] for d in sub)/len(sub)*100
            out.write(f"    {lbl:>10}: N={len(sub):>3} dayWin={wr:.1f}%\n")

        # Number of qualifying gap-up stocks
        out.write(f"\n  Number of gap-up stocks (>0.5%):\n")
        for lo,hi,lbl in [(0,100,'<100'),(100,200,'100-200'),(200,400,'200-400'),(400,999,'>400')]:
            sub = [d for d in day_results if lo<=d['n_gapup']<hi]
            if len(sub)<3: continue
            wr = sum(d['win'] for d in sub)/len(sub)*100
            out.write(f"    {lbl:>10}: N={len(sub):>3} dayWin={wr:.1f}%\n")

        # Avg pool momentum
        out.write(f"\n  Pool avg momentum (top-7):\n")
        for lo,hi,lbl in [(-99,-1.5,'<-1.5%'),(-1.5,-0.5,'-1.5 to -0.5%'),(-0.5,0,'-0.5 to 0%'),(0,99,'>0%')]:
            sub = [d for d in day_results if lo<=d['avg_mom']<hi]
            if len(sub)<3: continue
            wr = sum(d['win'] for d in sub)/len(sub)*100
            out.write(f"    {lbl:>18}: N={len(sub):>3} dayWin={wr:.1f}%\n")

        # Pool avg sell pressure
        out.write(f"\n  Pool avg sell pressure (top-7):\n")
        for lo,hi,lbl in [(0,0.55,'<0.55'),(0.55,0.60,'0.55-0.60'),(0.60,0.65,'0.60-0.65'),(0.65,1,'>=0.65')]:
            sub = [d for d in day_results if lo<=d['avg_sp']<hi]
            if len(sub)<3: continue
            wr = sum(d['win'] for d in sub)/len(sub)*100
            out.write(f"    {lbl:>12}: N={len(sub):>3} dayWin={wr:.1f}%\n")

        # ═══════════════════════════════════════
        # 4. SKIP-DAY FILTERS combined with sizing
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. SKIP-DAY + SIZING combined\n"+"="*110+"\n")

        skip_filters = {
            'none': lambda d: True,
            'skip Thu': lambda d: d['dow']!='Thursday',
            'skip if mkt_up>60%': lambda d: d['mkt_pct_up']<=60,
            'skip if mom>-0.5%': lambda d: d['avg_mom']<=-0.5,
            'skip if sp<0.55': lambda d: d['avg_sp']>=0.55,
            'skip if n_gapup<100': lambda d: d['n_gapup']>=100,
            'skip if sp<0.55 + mom>-0.5': lambda d: d['avg_sp']>=0.55 and d['avg_mom']<=-0.5,
        }

        # Build day_results dict for quick lookup
        dr_dict = {d['date']:d for d in day_results}

        for skip_name, skip_fn in skip_filters.items():
            # S6 top-7, ADD 3x at b15 if pnl>0.3%
            total_pnl=0;dw_count=0;active=0;trades=0;wt=0
            for d in dates:
                dr = dr_dict.get(d)
                if dr is None: continue
                if not skip_fn(dr): continue

                pool = sorted(by_date[d], key=lambda x:-x['scores']['s6'])[:7]
                if not pool: continue
                active+=1; day_pnl=0

                add_fn = make_add(0.3, 3.0)
                for t in pool:
                    trades+=1
                    if 14 in t['live']:
                        action = add_fn(t['live'][14])
                        if action[0]=='add':
                            mult = action[1]
                            raw_ret = t['ret90']+COST
                            check_price = t['live'][14]['price']
                            exit_price = t['entry']*(1-raw_ret/100)
                            extra_pnl = (check_price-exit_price)/check_price*100 if check_price>0 else 0
                            total_raw = raw_ret + extra_pnl*(mult-1)
                            pnl_rs = BASE*MARGIN*(total_raw-COST)/100
                        else:
                            pnl_rs = BASE*MARGIN*t['ret90']/100
                    else:
                        pnl_rs = BASE*MARGIN*t['ret90']/100

                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1

                total_pnl+=day_pnl
                if day_pnl>0: dw_count+=1

            roc = total_pnl/(BASE*7)*100
            dw = dw_count/max(active,1)*100
            tw = wt/max(trades,1)*100
            out.write(f"  {skip_name:<35} days={active:>3} dayWin={dw:.1f}% ROC={roc:+.1f}% trdWin={tw:.1f}%\n")

        # ═══════════════════════════════════════
        # 5. PER-STOCK FREQUENCY ANALYSIS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. STOCK FREQUENCY — which stocks appear in top-7 most often?\n"+"="*110+"\n")
        from collections import Counter
        all_top7 = []
        for d in dates:
            pool = sorted(by_date[d], key=lambda x:-x['scores']['s6'])[:7]
            all_top7.extend(pool)

        sym_counts = Counter(t['sym'] for t in all_top7)
        sym_wins = defaultdict(int)
        for t in all_top7:
            if t['win']: sym_wins[t['sym']]+=1

        out.write(f"  {'Symbol':<15} {'Picks':>5} {'Wins':>5} {'WinRate':>8}\n  "+"-"*40+"\n")
        for sym,cnt in sym_counts.most_common(25):
            wr = sym_wins[sym]/cnt*100
            out.write(f"  {sym:<15} {cnt:>5} {sym_wins[sym]:>5} {wr:>7.1f}%\n")

        # ═══════════════════════════════════════
        # 6. FINAL: honest assessment
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. HONEST FINAL ASSESSMENT\n"+"="*110+"\n")

        # Best no-sizing
        best_nosizing = max(
            [(dw,name,roc,tw) for dw,name,roc,tw in results if 'no_sizing' in name],
            key=lambda x:x[0]
        )
        # Best with sizing
        best_sizing = results[0]  # already sorted

        out.write(f"\n  BEST NO SIZING:   {best_nosizing[1]}\n")
        out.write(f"    DayWin={best_nosizing[0]:.1f}%  ROC={best_nosizing[2]:+.1f}%\n")
        out.write(f"\n  BEST WITH SIZING: {best_sizing[1]}\n")
        out.write(f"    DayWin={best_sizing[0]:.1f}%  ROC={best_sizing[2]:+.1f}%\n")
        out.write(f"\n  SIZING BENEFIT:   DayWin {best_sizing[0]-best_nosizing[0]:+.1f}pp  ROC {best_sizing[2]-best_nosizing[2]:+.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
