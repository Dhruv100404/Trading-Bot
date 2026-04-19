"""
REAL-TIME MINUTE-BY-MINUTE MARKET SCANNER
==========================================
Instead of fixed entry window (9:20 AM), scan EVERY MINUTE from 9:15 to 11:00 AM.
At each minute, compute a dynamic score for every liquid stock.
Find: when does the PERFECT trade appear? Can we catch it?

Combines the "100% win day" finding (high entry score) with
continuous scoring that adapts as new candle data arrives.

Filter: no circuit (gap<10%), liquid (f5vol*price>5L)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'realtime_scanner.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading full-day data...")
    by_date = defaultdict(list)
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                if abs(r['gapPct']) > 10: continue  # circuit filter
                f5v = r.get('f5Vol',0) * r['dayOpen']
                if f5v < 500000: continue  # liquidity filter
                bkts = r['buckets']
                nb = min(len(bkts), 120)  # up to bucket 120 (~11:15 AM)
                bkt = np.zeros((120,7), dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'bkt':bkt,'nb':nb,
                })

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} stocks across {len(dates)} days in {time.time()-t0:.1f}s")

    def compute_minute_score(bkt, scan_b, gap):
        """Compute dynamic score at scan_b using ALL data from b0 to scan_b.
        NO LOOKAHEAD — only uses buckets 0..scan_b.
        Returns (score, features_dict) or None if insufficient data."""
        if scan_b < 2 or bkt[scan_b,C] <= 0 or bkt[0,O] <= 0:
            return None

        day_open = bkt[0,O]
        current = bkt[scan_b,C]

        # 1. SELL PRESSURE: close position in range across last N candles
        lookback = min(scan_b, 6)
        cp_sum = 0; n_red = 0; n_bkts = 0
        for i in range(max(scan_b-lookback, 0), scan_b+1):
            rng = bkt[i,H] - bkt[i,L]
            cp_sum += (bkt[i,C]-bkt[i,L])/rng if rng > 0 else 0.5
            if bkt[i,C] < bkt[i,O]: n_red += 1
            n_bkts += 1
        sell_pressure = 1 - cp_sum/max(n_bkts,1)

        # 2. MOMENTUM: price change over last N buckets
        lb_start = max(scan_b - 5, 0)
        if bkt[lb_start,O] > 0:
            momentum = (bkt[scan_b,C] - bkt[lb_start,O]) / bkt[lb_start,O] * 100
        else:
            momentum = 0

        # 3. VWAP POSITION
        vwap_pos = (current - bkt[scan_b,VW])/bkt[scan_b,VW]*100 if bkt[scan_b,VW]>0 else 0

        # 4. VOLUME SURGE: current bucket vol vs average
        avg_vol = np.mean([bkt[i,V] for i in range(max(scan_b-10,0), scan_b)]) if scan_b > 1 else 1
        vol_surge = bkt[scan_b,V] / max(avg_vol, 1)

        # 5. GAP from open (how far from day open)
        gap_from_open = (current - day_open) / day_open * 100

        # 6. TREND: is price consistently dropping? (for SELL)
        if scan_b >= 3:
            trend_pts = [bkt[i,C] for i in range(max(scan_b-5,0), scan_b+1) if bkt[i,C]>0]
            if len(trend_pts) >= 3:
                # Linear trend: negative slope = dropping
                x = np.arange(len(trend_pts))
                slope = np.polyfit(x, trend_pts, 1)[0]
                trend_strength = -slope / current * 100 * 10  # normalize
            else:
                trend_strength = 0
        else:
            trend_strength = 0

        # 7. CONSECUTIVE RED: how many recent reds in a row?
        consec_red = 0
        for i in range(scan_b, max(scan_b-10, 0), -1):
            if bkt[i,C] < bkt[i,O]: consec_red += 1
            else: break

        # 8. RANGE EXPANSION: is current candle bigger than average?
        curr_range = (bkt[scan_b,H]-bkt[scan_b,L])/current*100 if current>0 else 0
        avg_range = np.mean([(bkt[i,H]-bkt[i,L])/max(bkt[i,O],1)*100 for i in range(max(scan_b-5,0),scan_b)]) if scan_b>1 else curr_range
        range_expansion = curr_range / max(avg_range, 0.001)

        # COMPOSITE SCORE for SELL trade potential
        score = 0.0

        # Sell pressure (0-1): higher = more sellers
        if sell_pressure > 0.6: score += 3
        elif sell_pressure > 0.5: score += 1
        elif sell_pressure < 0.4: score -= 2

        # Momentum: negative = price dropping (good for sell)
        if momentum < -0.5: score += 3
        elif momentum < -0.2: score += 2
        elif momentum < 0: score += 1
        elif momentum > 0.3: score -= 2

        # VWAP: below = sellers winning
        if vwap_pos < -0.3: score += 2
        elif vwap_pos < 0: score += 1
        elif vwap_pos > 0.3: score -= 2

        # Gap from open: if still above open after N minutes, gap not filled
        if gap > 0.5:  # gap-up stock
            if gap_from_open < 0: score += 2  # dropped below open = gap filling
            elif gap_from_open > gap * 0.8: score -= 1  # barely moved from gap

        # Trend strength
        if trend_strength > 0.3: score += 2
        elif trend_strength > 0.1: score += 1
        elif trend_strength < -0.1: score -= 1

        # Consecutive reds
        if consec_red >= 3: score += 2
        elif consec_red >= 2: score += 1

        # Volume surge on down move
        if vol_surge > 2 and momentum < 0: score += 1

        # Red candle ratio
        red_ratio = n_red / max(n_bkts, 1)
        if red_ratio > 0.7: score += 1
        elif red_ratio < 0.3: score -= 1

        # Price bonus (cheaper stocks reverse more)
        if day_open < 500: score += 1

        features = {
            'sp': sell_pressure, 'mom': momentum, 'vwap': vwap_pos,
            'vol_surge': vol_surge, 'gap_from_open': gap_from_open,
            'trend': trend_strength, 'consec_red': consec_red,
            'range_exp': range_expansion, 'red_ratio': red_ratio, 'score': score,
        }
        return (score, features)

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("REAL-TIME MINUTE-BY-MINUTE MARKET SCANNER\n")
        out.write(f"Stocks: {n}, Days: {len(dates)}\n")
        out.write(f"Scan range: b1 (9:15) to b105 (11:00 AM), every minute\n")
        out.write(f"Filter: no circuit, liquid>5L\n\n")

        # ═══════════════════════════════════════════
        # 1. At each minute: score ALL stocks, pick top-8, simulate SELL
        # ═══════════════════════════════════════════
        out.write("="*110+"\n1. MINUTE-BY-MINUTE WIN RATE: enter at scan_b, exit 30 buckets later\n"+"="*110+"\n")
        out.write(f"  At each minute, score all stocks, pick top-8 by score, SELL, exit +30 min\n\n")
        out.write(f"  {'Bucket':>7} {'Time':>8} {'AvgPool':>8} {'Top8Win':>8} {'Top8Ret':>8} {'Top1Win':>8} {'Score>=10':>10}\n")
        out.write("  "+"-"*70+"\n")

        # Scan every 3 minutes from b5 to b105
        all_scan_results = []
        for scan_b in range(5, 106, 3):
            h = 9 + (15+scan_b)//60; m = (15+scan_b)%60
            hold = 30
            exit_b = scan_b + hold

            day_wins_8 = 0; day_total_8 = 0; day_count = 0
            all_scores = []; top1_wins = 0; top1_count = 0
            high_score_wins = 0; high_score_total = 0

            for d in dates:
                stocks = by_date[d]
                scored = []
                for s in stocks:
                    bkt = s['bkt']
                    if exit_b >= s['nb'] or bkt[exit_b,C] <= 0: continue
                    result = compute_minute_score(bkt, scan_b, s['gap'])
                    if result is None: continue
                    sc, feat = result
                    entry = bkt[scan_b, C]
                    exit_price = bkt[exit_b, C]
                    if entry <= 0: continue
                    sell_ret = (entry - exit_price) / entry * 100 - COST
                    scored.append((sc, sell_ret, s['sym'], feat))

                if len(scored) < 8: continue
                scored.sort(key=lambda x: -x[0])
                day_count += 1

                # Top-8
                top8 = scored[:8]
                day_ret = sum(r for _,r,_,_ in top8)
                day_wins_8 += sum(1 for _,r,_,_ in top8 if r > 0)
                day_total_8 += len(top8)
                if day_ret > 0: pass  # day win tracking

                # Top-1
                if scored[0][1] > 0: top1_wins += 1
                top1_count += 1

                # High score (>=10)
                high = [x for x in scored if x[0] >= 10]
                for sc,r,_,_ in high:
                    high_score_total += 1
                    if r > 0: high_score_wins += 1

                all_scores.extend([(sc,r) for sc,r,_,_ in scored[:30]])

            if day_total_8 == 0: continue
            top8_wr = day_wins_8 / day_total_8 * 100
            top8_ar = 0  # computed below
            # Recalculate properly
            top8_rets = []
            for d in dates:
                stocks = by_date[d]
                scored = []
                for s in stocks:
                    bkt = s['bkt']
                    if exit_b >= s['nb'] or bkt[exit_b,C] <= 0: continue
                    result = compute_minute_score(bkt, scan_b, s['gap'])
                    if result is None: continue
                    sc, feat = result
                    entry = bkt[scan_b, C]
                    sell_ret = (entry - bkt[exit_b,C]) / entry * 100 - COST
                    scored.append((sc, sell_ret))
                if len(scored) < 8: continue
                scored.sort(key=lambda x: -x[0])
                top8_rets.extend([r for _,r in scored[:8]])

            if not top8_rets: continue
            wr8 = sum(1 for r in top8_rets if r>0)/len(top8_rets)*100
            ar8 = np.mean(top8_rets)
            t1wr = top1_wins/max(top1_count,1)*100
            hswr = high_score_wins/max(high_score_total,1)*100 if high_score_total > 0 else 0

            out.write(f"  b{scan_b+1:>4} ({h}:{m:02d}) {len(top8_rets)//max(day_count,1):>8} {wr8:>7.1f}% {ar8:>+7.3f}% {t1wr:>7.1f}% {hswr:>5.1f}%({high_score_total})\n")

            all_scan_results.append({
                'scan_b':scan_b, 'wr8':wr8, 'ar8':ar8, 't1wr':t1wr,
                'hswr':hswr, 'hs_n':high_score_total, 'n':len(top8_rets),
            })

        # ═══════════════════════════════════════════
        # 2. SCORE THRESHOLD: only trade when score is extremely high
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. ULTRA-SELECTIVE: only trade when score >= threshold\n"+"="*110+"\n")
        out.write(f"  Scan every 3 min from 9:20 to 11:00, SELL top-8 by score, exit +30min\n")
        out.write(f"  Only ENTER if score >= threshold\n\n")

        for min_score in [5, 7, 8, 10, 12, 15]:
            total_trades = 0; wins = 0; total_ret = 0; day_set = set()
            day_pnls = defaultdict(float)

            for scan_b in range(5, 106, 1):  # every minute
                exit_b = scan_b + 30
                for d in dates:
                    stocks = by_date[d]
                    scored = []
                    for s in stocks:
                        bkt = s['bkt']
                        if exit_b >= s['nb'] or bkt[exit_b,C] <= 0: continue
                        result = compute_minute_score(bkt, scan_b, s['gap'])
                        if result is None: continue
                        sc, _ = result
                        if sc < min_score: continue
                        entry = bkt[scan_b, C]
                        sell_ret = (entry - bkt[exit_b,C]) / entry * 100 - COST
                        scored.append((sc, sell_ret, s['sym'], d))

                    # Take top-8 qualifying stocks at this minute
                    scored.sort(key=lambda x: -x[0])
                    for sc, ret, sym, date in scored[:8]:
                        key = f"{date}_{sym}_{scan_b}"  # unique trade
                        total_trades += 1
                        total_ret += ret
                        if ret > 0: wins += 1
                        day_pnls[date] += ret
                        day_set.add(date)

                # Only take FIRST qualifying minute per stock per day
                break  # Actually, we need a smarter dedup

            # Simpler: for each day, scan minutes sequentially, take first qualifying set
            total_trades = 0; wins = 0; total_ret = 0
            day_wins = 0; active_days = 0
            for d in dates:
                stocks = by_date[d]
                traded_syms = set()
                day_ret = 0
                for scan_b in range(5, 106):
                    exit_b = scan_b + 30
                    qualifying = []
                    for s in stocks:
                        if s['sym'] in traded_syms: continue
                        bkt = s['bkt']
                        if exit_b >= s['nb'] or bkt[exit_b,C] <= 0: continue
                        result = compute_minute_score(bkt, scan_b, s['gap'])
                        if result is None: continue
                        sc, _ = result
                        if sc < min_score: continue
                        entry = bkt[scan_b, C]
                        sell_ret = (entry - bkt[exit_b,C]) / entry * 100 - COST
                        qualifying.append((sc, sell_ret, s['sym']))

                    qualifying.sort(key=lambda x: -x[0])
                    for sc, ret, sym in qualifying[:max(8 - len(traded_syms), 0)]:
                        traded_syms.add(sym)
                        total_trades += 1
                        total_ret += ret
                        if ret > 0: wins += 1
                        day_ret += ret

                    if len(traded_syms) >= 8: break  # got 8 trades for today

                if traded_syms:
                    active_days += 1
                    if day_ret > 0: day_wins += 1

            wr = wins/max(total_trades,1)*100
            dw = day_wins/max(active_days,1)*100
            ar = total_ret/max(total_trades,1)
            out.write(f"  Score>={min_score:>3}: trades={total_trades:>5} win={wr:.1f}% dayWin={dw:.1f}% avgRet={ar:+.3f}% days={active_days}\n")

        # ═══════════════════════════════════════════
        # 3. OPTIMAL SCAN TIME: which minute gives best signals?
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. WHICH MINUTE GIVES BEST TOP-8 WIN RATE? (hold 30 min)\n"+"="*110+"\n")

        best_results = sorted(all_scan_results, key=lambda x: -x['wr8'])[:15]
        out.write(f"  {'Bucket':>7} {'Time':>8} {'Top8Win':>8} {'Top8Ret':>8} {'Top1Win':>8}\n  "+"-"*45+"\n")
        for r in best_results:
            sb = r['scan_b']
            h = 9+(15+sb)//60; m = (15+sb)%60
            out.write(f"  b{sb+1:>4} ({h}:{m:02d}) {r['wr8']:>7.1f}% {r['ar8']:>+7.3f}% {r['t1wr']:>7.1f}%\n")

        # ═══════════════════════════════════════════
        # 4. DIFFERENT HOLD PERIODS at each scan time
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. HOLD PERIOD OPTIMIZATION per scan time\n"+"="*110+"\n")
        out.write(f"  {'ScanTime':>10}")
        for hold in [10, 15, 20, 30, 45, 60]:
            out.write(f" hold{hold}m".rjust(10))
        out.write("\n  "+"-"*75+"\n")

        for scan_b in [5, 8, 11, 14, 20, 29, 44, 59, 74, 89]:
            h = 9+(15+scan_b)//60; m = (15+scan_b)%60
            out.write(f"  b{scan_b+1}({h}:{m:02d})")
            for hold in [10, 15, 20, 30, 45, 60]:
                exit_b = scan_b + hold
                rets = []
                for d in dates:
                    scored = []
                    for s in by_date[d]:
                        bkt = s['bkt']
                        if exit_b >= s['nb'] or bkt[exit_b,C]<=0 or bkt[scan_b,C]<=0: continue
                        result = compute_minute_score(bkt, scan_b, s['gap'])
                        if result is None: continue
                        sc, _ = result
                        entry = bkt[scan_b, C]
                        ret = (entry - bkt[exit_b,C])/entry*100 - COST
                        scored.append((sc, ret))
                    if len(scored) < 8: continue
                    scored.sort(key=lambda x:-x[0])
                    rets.extend([r for _,r in scored[:8]])

                if rets:
                    wr = sum(1 for r in rets if r>0)/len(rets)*100
                    out.write(f" {wr:.0f}%/{np.mean(rets):+.2f}%".rjust(10))
                else:
                    out.write(f"{'--':>10}")
            out.write("\n")

        # ═══════════════════════════════════════════
        # 5. SCORE EVOLUTION: how does a stock's score change over time?
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. SCORE EVOLUTION: do winning trades have rising scores?\n"+"="*110+"\n")

        # Track score trajectory for top-8 picks at b5 (entry), then check at b8, b11, b14, b20
        win_trajectories = []; lose_trajectories = []
        for d in dates:
            stocks = by_date[d]
            # Score at b5
            scored_b5 = []
            for s in stocks:
                bkt = s['bkt']
                if bkt[89,C]<=0 or bkt[5,C]<=0: continue
                r5 = compute_minute_score(bkt, 5, s['gap'])
                if r5 is None: continue
                sc5, _ = r5
                entry = bkt[6,O] if bkt[6,O]>0 else bkt[5,C]
                ret90 = (entry - bkt[89,C])/entry*100 - COST
                # Score at later points
                scores_over_time = [sc5]
                for cb in [8, 11, 14, 20, 29]:
                    r_cb = compute_minute_score(bkt, cb, s['gap'])
                    scores_over_time.append(r_cb[0] if r_cb else sc5)

                scored_b5.append((sc5, ret90, scores_over_time, s['sym']))

            scored_b5.sort(key=lambda x:-x[0])
            for sc, ret, traj, sym in scored_b5[:8]:
                if ret > 0: win_trajectories.append(traj)
                else: lose_trajectories.append(traj)

        if win_trajectories and lose_trajectories:
            out.write(f"  Score trajectory (avg) for entry at b6, exit b90:\n")
            out.write(f"  {'CheckPoint':>12} {'Winners':>10} {'Losers':>10} {'Gap':>8}\n  "+"-"*45+"\n")
            labels = ['b6 (entry)', 'b9', 'b12', 'b15', 'b21', 'b30']
            for i, label in enumerate(labels):
                w_avg = np.mean([t[i] for t in win_trajectories])
                l_avg = np.mean([t[i] for t in lose_trajectories])
                out.write(f"  {label:>12} {w_avg:>10.2f} {l_avg:>10.2f} {w_avg-l_avg:>+7.2f}\n")

            # Do winners have RISING scores? Do losers have FALLING?
            w_rising = sum(1 for t in win_trajectories if t[-1] > t[0]) / len(win_trajectories) * 100
            l_rising = sum(1 for t in lose_trajectories if t[-1] > t[0]) / len(lose_trajectories) * 100
            out.write(f"\n  Winners with rising score: {w_rising:.1f}%\n")
            out.write(f"  Losers with rising score: {l_rising:.1f}%\n")

        # ═══════════════════════════════════════════
        # 6. THE 100% STRATEGY: ultra-high score + continuous confirmation
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. ULTRA-SELECTIVE: high entry score + score must HOLD/RISE by b15\n"+"="*110+"\n")

        for entry_thresh in [8, 10, 12, 15]:
            for confirm_thresh in [5, 8, 10]:
                total = 0; wins = 0; day_wins = 0; active = 0
                for d in dates:
                    stocks = by_date[d]
                    picks = []
                    for s in stocks:
                        bkt = s['bkt']
                        if bkt[89,C]<=0 or bkt[5,C]<=0: continue
                        r5 = compute_minute_score(bkt, 5, s['gap'])
                        if r5 is None or r5[0] < entry_thresh: continue
                        # Confirm at b14 (9 minutes later)
                        r14 = compute_minute_score(bkt, 14, s['gap'])
                        if r14 is None or r14[0] < confirm_thresh: continue
                        entry = bkt[6,O] if bkt[6,O]>0 else bkt[5,C]
                        ret = (entry - bkt[89,C])/entry*100 - COST
                        picks.append((r5[0]+r14[0], ret, s['sym']))

                    if not picks: continue
                    picks.sort(key=lambda x:-x[0])
                    active += 1
                    day_ret = sum(r for _,r,_ in picks[:8])
                    for _,r,_ in picks[:8]:
                        total += 1
                        if r > 0: wins += 1
                    if day_ret > 0: day_wins += 1

                if total < 10: continue
                wr = wins/total*100
                dw = day_wins/max(active,1)*100
                out.write(f"  Entry>={entry_thresh} + Confirm>={confirm_thresh}: trades={total:>4} win={wr:.1f}% dayWin={dw:.1f}% days={active}\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
