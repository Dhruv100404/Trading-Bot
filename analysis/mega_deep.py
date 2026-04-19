"""
MEGA DEEP ANALYSIS — One script, all insights
================================================
Loads data ONCE, runs every analysis needed:
1. Per-stock win rate (stock personality)
2. Simulate cherry-pick top 30 per day with ALL features
3. Find REJECT conditions (features that predict loss 70%+)
4. Find MUST-PICK conditions (features that predict win 80%+)
5. Adaptive exit: optimal exit per stock profile
6. Multi-feature interaction (3-4 features together)
7. Position count optimization (top 4 vs 6 vs 8 vs 10)
8. Final composite scorer testing (100+ formulas)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'mega_deep_analysis.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    # ── LOAD ALL DATA ONCE ──
    print("Loading data...")
    by_date = defaultdict(list)
    all_gaps = defaultdict(list)  # sym -> list of gap days (for market context)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                all_gaps[r['date']].append(r['gapPct'])
                if r['gapPct'] <= 0.1: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0: continue

                b0o,b0h,b0l,b0c = bkt[0,O],bkt[0,H],bkt[0,L],bkt[0,C]
                rng0 = b0h-b0l
                body0 = abs(b0c-b0o)
                upper_wick0 = b0h - max(b0o,b0c)

                # Compute returns at multiple exits
                rets = {}
                for eb in [19,29,44,59,65,74,89]:
                    ec = bkt[eb,C]
                    if ec > 0:
                        rets[eb] = (entry - ec)/entry*100 - COST

                # MFE/MAE at b66
                if bkt[65,C] > 0:
                    mfe66 = (entry - float(np.min(bkt[6:66,L])))/entry*100
                    mae66 = (float(np.max(bkt[6:66,H])) - entry)/entry*100
                else:
                    mfe66 = mae66 = 0

                # MFE timing
                mfe_bucket = 6
                running_min = entry
                for eb in range(7,90):
                    if bkt[eb,L] > 0 and bkt[eb,L] < running_min:
                        running_min = bkt[eb,L]
                        mfe_bucket = eb

                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_v6 = sum(vol6)
                br_seq = [float(bkt[i,BR]) for i in range(6)]

                # Exhaustion index
                exhaust = (b0h-b0c)/rng0 if rng0>0 else 0.5

                by_date[r['date']].append({
                    'sym':r['symbol'], 'gap':r['gapPct'], 'price':r['dayOpen'],
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                    'f5range':r.get('f5Range',0),
                    'rets':rets,
                    'mfe66':mfe66, 'mae66':mae66, 'mfe_bucket':mfe_bucket,
                    'entry':entry,
                    # Bucket 0 features
                    'b0_ret':(b0c-b0o)/b0o*100 if b0o>0 else 0,
                    'b0_range':rng0/b0o*100 if b0o>0 else 0,
                    'b0_br':br_seq[0],
                    'b0_green': b0c>b0o,
                    'b0_vol_share': vol6[0]/total_v6 if total_v6>0 else 0,
                    'exhaust': exhaust,
                    'upper_wick_pct': upper_wick0/rng0*100 if rng0>0 else 0,
                    # Multi-bucket features
                    'avg_br6': np.mean(br_seq),
                    'br_trend': br_seq[5]-br_seq[0],
                    'n_red': sum(1 for i in range(6) if bkt[i,C]<bkt[i,O]),
                    'vol_ratio': vol6[0]/np.mean(vol6[1:6]) if np.mean(vol6[1:6])>0 else 0,
                    'momentum': (bkt[5,C]-b0o)/b0o*100 if b0o>0 else 0,
                    'vwap_dev': (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0,
                    # Sequence
                    'seq3': ''.join('D' if bkt[i,C]<bkt[i,O] else 'U' for i in range(3)),
                    'b0_shooting': upper_wick0 > body0*1.5 if body0>0 else False,
                    'b0_green_b1_red': b0c>b0o and bkt[1,C]<bkt[1,O],
                })

    # Market context per day
    mkt_ctx = {}
    for date, gaps in all_gaps.items():
        mkt_ctx[date] = {
            'avg_gap': np.mean(gaps),
            'pct_up': sum(1 for g in gaps if g>0)/len(gaps)*100,
            'n_up': sum(1 for g in gaps if g>0.1),
        }

    # ── COMPUTE STOCK WIN RATES ──
    stock_stats = defaultdict(lambda: [0,0,0.0]) # sym -> [wins, total, sum_ret]
    for date, stocks in by_date.items():
        for s in stocks:
            r66 = s['rets'].get(65,None)
            if r66 is None: continue
            stock_stats[s['sym']][1] += 1
            stock_stats[s['sym']][2] += r66
            if r66 > 0: stock_stats[s['sym']][0] += 1

    stock_wr = {}
    for sym,(w,t,sr) in stock_stats.items():
        if t >= 5:
            stock_wr[sym] = w/t

    # Attach stock_wr and market context to each record
    for date, stocks in by_date.items():
        mc = mkt_ctx.get(date, {})
        for s in stocks:
            s['stock_wr'] = stock_wr.get(s['sym'], 0.5)
            s['mkt_avg_gap'] = mc.get('avg_gap',0)
            s['mkt_pct_up'] = mc.get('pct_up',50)
            s['mkt_n_up'] = mc.get('n_up',100)
            s['rel_gap'] = s['gap'] - mc.get('avg_gap',0)

    dates = sorted(by_date.keys())
    n_recs = sum(len(v) for v in by_date.values())
    print(f"Loaded {n_recs} gap-up records across {len(dates)} days in {time.time()-t0:.1f}s")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("MEGA DEEP ANALYSIS\n")
        out.write(f"Universe: {len(liquid)} Liquid5L stocks, {n_recs} gap-up records, {len(dates)} days\n\n")

        # ═══════════════════════════════════════════════════════════
        # 1. POSITION COUNT: what's optimal top-N?
        # ═══════════════════════════════════════════════════════════
        out.write("="*100+"\n1. OPTIMAL POSITION COUNT (top-N by gap)\n"+"="*100+"\n")
        for n_pos in [1,2,3,4,5,6,7,8,10,12,15,20]:
            day_pnls=[]; wins=0; total_trades=0; win_trades=0
            for date in dates:
                top = sorted(by_date[date], key=lambda x:-x['gap'])[:n_pos]
                dr = sum(s['rets'].get(65,0) for s in top if 65 in s['rets'])
                n = sum(1 for s in top if 65 in s['rets'])
                day_pnls.append(dr)
                if dr>0: wins+=1
                total_trades+=n
                win_trades+=sum(1 for s in top if s['rets'].get(65,0)>0)
            total = sum(day_pnls)
            dw = wins/len(dates)*100
            tw = win_trades/max(total_trades,1)*100
            avg = total/max(total_trades,1)
            out.write(f"  Top-{n_pos:>2}: totalRet={total:>+7.1f}%  dayWin={dw:>5.1f}%  trdWin={tw:>5.1f}%  avgPerTrade={avg:>+.3f}%  trades={total_trades}\n")

        # ═══════════════════════════════════════════════════════════
        # 2. EXIT BUCKET OPTIMIZATION (per gap range)
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n2. EXIT BUCKET x GAP RANGE (avg return per trade)\n"+"="*100+"\n")
        exit_bkts = [19,29,44,59,65,74,89]
        gap_ranges = [(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,5,'3-5%'),(5,10,'5-10%'),(10,100,'10%+')]
        # Flatten all records
        all_recs = [s for stocks in by_date.values() for s in stocks]
        out.write(f"  {'Gap':>8}")
        for eb in exit_bkts:
            h=9+(15+eb)//60; m=(15+eb)%60
            out.write(f"  b{eb+1}({h}:{m:02d})".rjust(12))
        out.write("  BEST\n  "+"-"*100+"\n")
        for glo,ghi,glbl in gap_ranges:
            sub = [s for s in all_recs if glo<=s['gap']<ghi]
            out.write(f"  {glbl:>8}")
            best_ret=-99; best_eb=65
            for eb in exit_bkts:
                rets=[s['rets'][eb] for s in sub if eb in s['rets']]
                if not rets: out.write(f"{'--':>12}"); continue
                ar = np.mean(rets)
                wr = sum(1 for r in rets if r>0)/len(rets)*100
                if ar>best_ret: best_ret=ar; best_eb=eb
                out.write(f"  {wr:.0f}%/{ar:+.2f}%".rjust(12))
            out.write(f"  b{best_eb+1}\n")

        # ═══════════════════════════════════════════════════════════
        # 3. ADAPTIVE EXIT: different exit per stock_wr
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n3. ADAPTIVE EXIT: optimal exit by stock_wr\n"+"="*100+"\n")
        wr_bins = [(0,0.4,'wr<0.4'),(0.4,0.5,'wr 0.4-0.5'),(0.5,0.6,'wr 0.5-0.6'),(0.6,0.7,'wr 0.6-0.7'),(0.7,1.01,'wr>0.7')]
        for wlo,whi,wlbl in wr_bins:
            sub = [s for s in all_recs if wlo<=s['stock_wr']<whi]
            if len(sub)<50: continue
            out.write(f"\n  {wlbl} ({len(sub)} trades):")
            best_ret=-99; best_eb=65
            for eb in exit_bkts:
                rets=[s['rets'][eb] for s in sub if eb in s['rets']]
                if not rets: continue
                ar = np.mean(rets); wr = sum(1 for r in rets if r>0)/len(rets)*100
                marker = ""
                if ar > best_ret: best_ret=ar; best_eb=eb; marker=" <<<"
                out.write(f"\n    b{eb+1:>2}: wr={wr:.1f}% ret={ar:+.3f}%{marker}")
            out.write(f"\n    BEST: b{best_eb+1}\n")

        # ═══════════════════════════════════════════════════════════
        # 4. MUST-PICK CONDITIONS (win rate > 65%)
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n4. MUST-PICK: feature combos with WIN RATE > 65% (n>=30)\n"+"="*100+"\n")

        conditions = []
        # Test thousands of 2-3 feature combos
        for wr_lo in [0.55, 0.60, 0.65, 0.70]:
            for br_hi in [0.55, 0.50, 0.45, 0.40]:
                for gap_lo in [0.5, 1.0, 1.5, 2.0, 3.0]:
                    for nr_lo in [0, 2, 3, 4]:
                        for ex_lo in [0, 0.5, 0.7]:
                            filt = lambda s, wr=wr_lo, br=br_hi, g=gap_lo, nr=nr_lo, ex=ex_lo: (
                                s['stock_wr']>=wr and s['avg_br6']<br and s['gap']>=g and s['n_red']>=nr and s['exhaust']>=ex
                            )
                            sub = [s for s in all_recs if filt(s) and 65 in s['rets']]
                            if len(sub)<30: continue
                            wr_val = sum(1 for s in sub if s['rets'][65]>0)/len(sub)*100
                            ar = np.mean([s['rets'][65] for s in sub])
                            if wr_val >= 65:
                                conditions.append((wr_val, ar, len(sub),
                                    f"wr>={wr_lo} + br<{br_hi} + gap>={gap_lo} + nred>={nr_lo} + exhaust>={ex_lo}"))

        conditions.sort(key=lambda x: (-x[0], -x[1]))
        seen = set()
        out.write(f"  {'Condition':<65} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*90+"\n")
        count = 0
        for wr_val, ar, n, desc in conditions:
            if desc in seen: continue
            seen.add(desc)
            out.write(f"  {desc:<65} {n:>5} {wr_val:>5.1f}% {ar:>+7.3f}%\n")
            count += 1
            if count >= 50: break

        # ═══════════════════════════════════════════════════════════
        # 5. REJECT CONDITIONS (win rate < 35%, n>=30)
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n5. REJECT: feature combos with WIN RATE < 35% (always lose)\n"+"="*100+"\n")

        rejects = []
        for wr_hi in [0.50, 0.45, 0.40, 0.35]:
            for br_lo in [0.50, 0.55, 0.60, 0.65]:
                for gap_lo in [0.5, 1.0, 2.0, 5.0, 10.0]:
                    for nr_hi in [0, 1, 2, 3]:
                        filt = lambda s, wr=wr_hi, br=br_lo, g=gap_lo, nr=nr_hi: (
                            s['stock_wr']<wr and s['avg_br6']>=br and s['gap']>=g and s['n_red']<=nr
                        )
                        sub = [s for s in all_recs if filt(s) and 65 in s['rets']]
                        if len(sub)<30: continue
                        wr_val = sum(1 for s in sub if s['rets'][65]>0)/len(sub)*100
                        ar = np.mean([s['rets'][65] for s in sub])
                        if wr_val < 35:
                            rejects.append((wr_val, ar, len(sub),
                                f"wr<{wr_hi} + br>={br_lo} + gap>={gap_lo} + nred<={nr_hi}"))

        rejects.sort(key=lambda x: (x[0], x[1]))
        seen = set()
        out.write(f"  {'Condition':<60} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*85+"\n")
        count = 0
        for wr_val, ar, n, desc in rejects:
            if desc in seen: continue
            seen.add(desc)
            out.write(f"  {desc:<60} {n:>5} {wr_val:>5.1f}% {ar:>+7.3f}%\n")
            count += 1
            if count >= 30: break

        # ═══════════════════════════════════════════════════════════
        # 6. MEGA SCORER: 100+ formulas including rejection
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n6. MEGA SCORER: 100+ formulas with rejection filters\n"+"="*100+"\n")

        def sim_day(date, scorer, reject=None, n_pos=8, exit_b=65):
            pool = by_date[date]
            if reject:
                pool = [s for s in pool if not reject(s)]
            if len(pool) < 1: return None
            for s in pool: s['_sc'] = scorer(s)
            pool.sort(key=lambda x:-x['_sc'])
            picks = pool[:n_pos]
            rets = [s['rets'].get(exit_b,0) for s in picks if exit_b in s['rets']]
            if not rets: return None
            return sum(rets), len(rets), sum(1 for r in rets if r>0)

        def test_strategy(name, scorer, reject=None, n_pos=8, exit_b=65):
            total=0; day_wins=0; trades=0; win_trades=0; active=0
            for date in dates:
                r = sim_day(date, scorer, reject, n_pos, exit_b)
                if r is None: continue
                active += 1
                total += r[0]; trades += r[1]; win_trades += r[2]
                if r[0] > 0: day_wins += 1
            if trades == 0: return None
            return (total, name, active, day_wins/max(active,1)*100,
                    win_trades/max(trades,1)*100, total/max(trades,1), trades)

        # Rejection filters
        R_none = None
        R_low_wr = lambda s: s['stock_wr'] < 0.40
        R_low_wr_hi_br = lambda s: s['stock_wr'] < 0.45 and s['avg_br6'] > 0.55
        R_extreme_gap = lambda s: s['gap'] > 15
        R_buyers_active = lambda s: s['avg_br6'] > 0.60
        R_no_sell = lambda s: s['n_red'] <= 1 and s['avg_br6'] > 0.55
        R_combo = lambda s: s['stock_wr'] < 0.45 or (s['avg_br6'] > 0.60 and s['n_red'] <= 1) or s['gap'] > 15

        # Scorers
        scorers = {
            'gap': lambda s: s['gap'],
            'gap*(1-br6)': lambda s: s['gap']*(1-s['avg_br6']),
            'gap*wr': lambda s: s['gap']*s['stock_wr'],
            'gap*(1-br6)*wr': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr'],
            'gap*(br6<.5?1:.3)*wr_mult': lambda s: (s['gap'] if s['avg_br6']<0.5 else s['gap']*0.3)*(1.5 if s['stock_wr']>0.6 else 1 if s['stock_wr']>0.5 else 0.5),
            'gap*wr*exhaust': lambda s: s['gap']*s['stock_wr']*max(s['exhaust'],0.3),
            'gap*(1-br6)*wr*exhaust': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr']*max(s['exhaust'],0.3),
            'gap*wr*(nred/6)': lambda s: s['gap']*s['stock_wr']*max(s['n_red']/6,0.2),
            'gap*(1-br6)*wr*(p<500?1.2:.9)': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr']*(1.2 if s['price']<500 else 0.9),
            'gap*wr*(mom<0?1.3:.7)': lambda s: s['gap']*s['stock_wr']*(1.3 if s['momentum']<0 else 0.7),
            'gap*(1-br6)*wr*(mom<0?1.2:.8)': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr']*(1.2 if s['momentum']<0 else 0.8),
            'rel_gap*(1-br6)*wr': lambda s: max(s['rel_gap'],0.1)*(1-s['avg_br6'])*s['stock_wr'],
            'gap*(1-br6)*wr*(f5rng/3)': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr']*max(s['f5range']/3,0.3),
            'gap*wr*(b0grn_b1red?1.5:1)': lambda s: s['gap']*s['stock_wr']*(1.5 if s['b0_green_b1_red'] else 1),
            'gap*(1-br6)*wr*(vol_rat>2?1.2:1)': lambda s: s['gap']*(1-s['avg_br6'])*s['stock_wr']*(1.2 if s['vol_ratio']>2 else 1),
        }

        reject_filters = {
            'none': R_none,
            'R:wr<.40': R_low_wr,
            'R:wr<.45+br>.55': R_low_wr_hi_br,
            'R:gap>15': R_extreme_gap,
            'R:br>.60': R_buyers_active,
            'R:nred<=1+br>.55': R_no_sell,
            'R:combo': R_combo,
        }

        results = []
        for s_name, scorer in scorers.items():
            for r_name, reject in reject_filters.items():
                for n_pos in [4, 6, 8]:
                    for exit_b in [65, 74, 89]:
                        name = f"{s_name} | {r_name} | top{n_pos} | b{exit_b+1}"
                        r = test_strategy(name, scorer, reject, n_pos, exit_b)
                        if r: results.append(r)

        results.sort(key=lambda x: -x[0])

        out.write(f"  {'Strategy':<75} {'TotRet':>8} {'Days':>5} {'DayWin':>7} {'TrdWin':>7} {'PerTrd':>7}\n")
        out.write("  "+"-"*115+"\n")
        for total, name, act, dw, tw, pt, nt in results[:80]:
            out.write(f"  {name:<75} {total:>+7.1f}% {act:>5} {dw:>6.1f}% {tw:>6.1f}% {pt:>+6.3f}%\n")

        # ═══════════════════════════════════════════════════════════
        # 7. MFE TIMING: when does the peak happen for winners?
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n7. MFE TIMING: when do winning trades peak? (for adaptive exit)\n"+"="*100+"\n")
        mfe_dist = defaultdict(int)
        for s in all_recs:
            if s['rets'].get(65,0) > 0:  # only winners
                mfe_dist[s['mfe_bucket']] += 1
        total_w = sum(mfe_dist.values())
        cumul = 0
        out.write(f"  {'Bucket':>7} {'Count':>6} {'%':>6} {'Cumul':>7}\n")
        out.write("  "+"-"*30+"\n")
        for b in sorted(mfe_dist.keys()):
            if b > 90: break
            cumul += mfe_dist[b]
            if mfe_dist[b] >= 5:
                h=9+(15+b)//60; m=(15+b)%60
                out.write(f"  b{b+1:>5} {mfe_dist[b]:>6} {mfe_dist[b]/total_w*100:>5.1f}% {cumul/total_w*100:>6.1f}%\n")

        # ═══════════════════════════════════════════════════════════
        # 8. STOCK BLACKLIST/WHITELIST
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n8. STOCK WHITELIST (>65% wr, 10+ trades) vs BLACKLIST (<40% wr)\n"+"="*100+"\n")
        whitelist = [(wr,sym) for sym,wr in stock_wr.items() if wr>=0.65 and stock_stats[sym][1]>=10]
        blacklist = [(wr,sym) for sym,wr in stock_wr.items() if wr<0.40 and stock_stats[sym][1]>=10]
        whitelist.sort(key=lambda x: -x[0])
        blacklist.sort(key=lambda x: x[0])

        out.write(f"\n  WHITELIST ({len(whitelist)} stocks, always reverse):\n")
        for wr,sym in whitelist[:40]:
            n = stock_stats[sym][1]; ar = stock_stats[sym][2]/n
            out.write(f"    {sym:<15} wr={wr:.0%}  n={n:>3}  avgRet={ar:>+.3f}%\n")

        out.write(f"\n  BLACKLIST ({len(blacklist)} stocks, never reverse):\n")
        for wr,sym in blacklist[:40]:
            n = stock_stats[sym][1]; ar = stock_stats[sym][2]/n
            out.write(f"    {sym:<15} wr={wr:.0%}  n={n:>3}  avgRet={ar:>+.3f}%\n")

        # Test: what if we ONLY trade whitelist stocks?
        out.write(f"\n  WHITELIST-ONLY STRATEGY:\n")
        wl_syms = {sym for _,sym in whitelist}
        for n_pos in [4, 6, 8]:
            total=0; dw=0; trades=0; wt=0
            for date in dates:
                pool = [s for s in by_date[date] if s['sym'] in wl_syms]
                pool.sort(key=lambda x:-x['gap'])
                picks = pool[:n_pos]
                rets = [s['rets'].get(65,0) for s in picks if 65 in s['rets']]
                if not rets: continue
                dr = sum(rets); trades += len(rets); wt += sum(1 for r in rets if r>0)
                total += dr
                if dr > 0: dw += 1
            twr = wt/max(trades,1)*100
            out.write(f"    Top-{n_pos} whitelist: totalRet={total:>+.1f}%  trdWin={twr:.1f}%  trades={trades}\n")

        # ═══════════════════════════════════════════════════════════
        # 9. MARKET REGIME: skip days when market is too bullish
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n9. MARKET REGIME: skip days when broad market is bullish\n"+"="*100+"\n")
        for mkt_thresh in [40, 45, 50, 55, 60, 65, 70]:
            total=0; dw=0; active=0; trades=0; wt=0
            for date in dates:
                mc = mkt_ctx.get(date,{})
                if mc.get('pct_up',50) > mkt_thresh: continue  # SKIP bullish days
                pool = sorted(by_date[date], key=lambda x:-x['gap'])[:8]
                rets = [s['rets'].get(65,0) for s in pool if 65 in s['rets']]
                if not rets: continue
                active += 1
                dr = sum(rets); trades += len(rets); wt += sum(1 for r in rets if r>0)
                total += dr
                if dr > 0: dw += 1
            twr = wt/max(trades,1)*100
            dwp = dw/max(active,1)*100
            out.write(f"  Skip if mkt_pct_up > {mkt_thresh}%: days={active:>3}  dayWin={dwp:>5.1f}%  trdWin={twr:>5.1f}%  totalRet={total:>+7.1f}%  trades={trades}\n")

        # ═══════════════════════════════════════════════════════════
        # 10. FINAL VERDICT: absolute best strategy found
        # ═══════════════════════════════════════════════════════════
        out.write("\n"+"="*100+"\n10. FINAL VERDICT\n"+"="*100+"\n")
        out.write(f"\n  CURRENT: {results[-1][1] if results else 'N/A'}\n") # gap only will be near bottom
        # Find current (gap | none | top8 | b66)
        current = next((r for r in results if 'gap |' in r[1] and 'none' in r[1] and 'top8' in r[1] and 'b66' in r[1]), None)
        best = results[0] if results else None
        if current:
            out.write(f"\n  CURRENT:  {current[1]}\n")
            out.write(f"            totalRet={current[0]:>+.1f}%  dayWin={current[3]:.1f}%  trdWin={current[4]:.1f}%\n")
        if best:
            out.write(f"\n  BEST:     {best[1]}\n")
            out.write(f"            totalRet={best[0]:>+.1f}%  dayWin={best[3]:.1f}%  trdWin={best[4]:.1f}%\n")
            if current:
                out.write(f"\n  IMPROVEMENT: {best[0]-current[0]:>+.1f}% total return\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")

    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
