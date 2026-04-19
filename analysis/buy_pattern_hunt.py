"""
BUY-SIDE PATTERN HUNT — Find high win-rate BUY setups for 5x margin
=====================================================================
Current system: SELL gap-up reversal works great (75-80% green days).
Goal: Find BUY patterns with similar TP-based exit strategy.

With 5x margin on Rs 10k capital = Rs 50k position.
Need: high TP hit rate (>65%), small TP target (~0.3-0.5%), tight time exit.

Patterns to test:
  1. Gap-down reversal (mirror of current SELL system)
  2. Gap-down bounce at open (B36 pattern already known)
  3. Morning dip-buy: stock opens flat/up, dips, then recovers
  4. VWAP reclaim: price drops below VWAP, then crosses back above
  5. Volume-exhaustion buy: heavy selling dries up, buyers step in
  6. Opening range breakout UP: breaks above first 5-min high
  7. Oversold bounce: large drop in first minutes, then mean-revert
  8. Momentum continuation BUY: gap-up stock keeps running

For each: measure TP hit rate at 0.2%, 0.3%, 0.5%, 1.0% targets.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'buy_pattern_hunt.txt'
COST = 0.15
BASE = 10000; MARGIN = 5
iO,iH,iL,iC,iV,iVW,iBR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    all_records = []
    loaded = 0

    for fp in files:
        if not fp.exists(): continue
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                if abs(r['gapPct']) > 10: continue
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue

                bkts = r['buckets']
                nb = min(len(bkts), 100)
                if nb < 80: continue
                bkt = np.zeros((100,7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j,iO]=b['o'];bkt[j,iH]=b['h'];bkt[j,iL]=b['l'];bkt[j,iC]=b['c']
                    bkt[j,iV]=b['v'];bkt[j,iVW]=b.get('vw',b['c']);bkt[j,iBR]=b.get('br',0.5)
                if bkt[0,iO] <= 0: continue

                all_records.append({
                    'sym': r['symbol'], 'date': r['date'],
                    'gap': r['gapPct'], 'price': r['dayOpen'],
                    'bkt': bkt, 'nb': nb,
                })
                loaded += 1
                if loaded % 50000 == 0:
                    print(f"  {loaded} loaded... {time.time()-t0:.0f}s")

    # Group by date
    by_date = defaultdict(list)
    for rec in all_records:
        by_date[rec['date']].append(rec)
    dates = sorted(by_date.keys())
    print(f"Loaded {loaded}, {len(dates)} days in {time.time()-t0:.0f}s")

    def compute_buy_features(bkt, nb, entry_start=2, entry_end=6):
        """Compute features from buckets entry_start to entry_end for BUY decision."""
        sl = slice(entry_start, entry_end+1)
        hl = bkt[sl, iH] - bkt[sl, iL]
        valid = hl > 0
        cp = np.where(valid, (bkt[sl,iC]-bkt[sl,iL])/np.maximum(hl,0.001), 0.5)
        buy_pressure = float(np.mean(cp))  # high = buyers winning
        sell_pressure = 1.0 - buy_pressure
        mom = float((bkt[entry_end,iC] - bkt[entry_start,iO]) / bkt[entry_start,iO] * 100) if bkt[entry_start,iO]>0 else 0
        n_green = int(np.sum(bkt[sl,iC] > bkt[sl,iO]))
        n_red = int(np.sum(bkt[sl,iC] < bkt[sl,iO]))
        vwap_dev = float((bkt[entry_end,iC] - bkt[entry_end,iVW]) / bkt[entry_end,iVW] * 100) if bkt[entry_end,iVW]>0 else 0
        avg_vol = float(np.mean(bkt[sl,iV]))
        return buy_pressure, sell_pressure, mom, n_green, n_red, vwap_dev, avg_vol

    def check_buy_tp(bkt, nb, entry_idx, tp_pct, exit_bucket=76):
        """Check if BUY TP hits: price rises tp_pct% from entry before exit_bucket."""
        entry = float(bkt[entry_idx, iO])
        if entry <= 0: return False, 0, 0
        tp_price = entry * (1 + tp_pct/100)
        for b in range(entry_idx+1, min(exit_bucket+1, nb)):
            if bkt[b,iH] >= tp_price:
                return True, b, tp_pct - COST
        # Time exit
        exit_price = float(bkt[min(exit_bucket, nb-1), iC])
        ret = (exit_price - entry) / entry * 100 - COST
        return False, exit_bucket, ret

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("BUY-SIDE PATTERN HUNT — High Win-Rate BUY Setups\n")
        out.write(f"Data: {loaded} stock-days, {len(dates)} days, 5x margin\n\n")

        # ═══════════════════════════════════════
        # 1. GAP-DOWN REVERSAL BUY (mirror of SELL)
        # ═══════════════════════════════════════
        print("Pattern 1: Gap-down reversal...")
        out.write("="*110+"\n1. GAP-DOWN REVERSAL BUY: buy gap-down stocks expecting bounce\n"+"="*110+"\n")
        out.write("  Mirror of SELL system: stock gaps DOWN, buy expecting mean-reversion UP.\n\n")

        for tp_target in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0]:
            for exit_b in [30, 45, 60, 76]:
                for gap_lo, gap_hi, gap_label in [(-2,-0.5,'0.5-2%'), (-4,-1,'1-4%'), (-6,-2,'2-6%'), (-10,-0.5,'0.5-10%')]:
                    dpnls=[]; trades=0; tp_hits=0; wins=0
                    for d in dates:
                        pool = []
                        for rec in by_date[d]:
                            gap = rec['gap']
                            if not (gap_lo <= gap <= gap_hi): continue
                            bkt = rec['bkt']; nb = rec['nb']
                            bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                            # Score for BUY: higher buy_pressure = better
                            score = abs(gap) * bp * 10
                            pool.append((rec, score, bp, mom, ng, vwap))
                        # Cherry-pick top by score
                        pool.sort(key=lambda x: -x[1])
                        picks = pool[:7]
                        dp = 0
                        for rec, score, bp, mom, ng, vwap in picks:
                            tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                            pnl = BASE * MARGIN * ret / 100
                            dp += pnl; trades += 1
                            if tp_hit: tp_hits += 1
                            if pnl > 0: wins += 1
                        dpnls.append(dp)
                    if trades < 50: continue
                    roc = sum(dpnls)/(BASE*7)*100
                    dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                    tw = wins/max(trades,1)*100
                    tpr = tp_hits/max(trades,1)*100
                    if tpr > 40:  # only show promising
                        out.write(f"  gap={gap_label:>8} TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. MORNING DIP-BUY: opens up/flat, dips, recovers
        # ═══════════════════════════════════════
        print("Pattern 2: Morning dip-buy...")
        out.write(f"\n\n"+"="*110+"\n2. MORNING DIP-BUY: stock opens up/flat, dips in first 5 min, then buy the dip\n"+"="*110+"\n")
        out.write(f"  Condition: gap >= 0%, first candles drop >0.3%, then buy at b6.\n\n")

        for tp_target in [0.2, 0.3, 0.5]:
            for exit_b in [30, 45, 66]:
                for dip_min in [0.2, 0.3, 0.5, 0.7]:
                    dpnls=[]; trades=0; tp_hits=0; wins=0
                    for d in dates:
                        pool = []
                        for rec in by_date[d]:
                            if rec['gap'] < -0.5: continue  # only flat/up gap
                            bkt = rec['bkt']; nb = rec['nb']
                            # Check if price dipped from open
                            open_price = float(bkt[0,iO])
                            if open_price <= 0: continue
                            low_5 = float(np.min(bkt[:6, iL]))
                            dip_pct = (open_price - low_5) / open_price * 100
                            if dip_pct < dip_min: continue
                            # Entry at b6
                            entry = float(bkt[6,iO])
                            if entry <= 0: continue
                            # Recovery: is price recovering at entry? (close > low)
                            recovering = bkt[5,iC] > bkt[5,iL] and bkt[5,iC] > bkt[4,iC]
                            if not recovering: continue
                            bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                            score = dip_pct * bp * 10
                            pool.append((rec, score))
                        pool.sort(key=lambda x: -x[1])
                        picks = pool[:5]
                        dp = 0
                        for rec, score in picks:
                            tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                            pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                            if tp_hit: tp_hits+=1
                            if pnl>0: wins+=1
                        dpnls.append(dp)
                    if trades < 30: continue
                    roc = sum(dpnls)/(BASE*5)*100
                    dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                    tw = wins/max(trades,1)*100
                    tpr = tp_hits/max(trades,1)*100
                    if tpr > 30:
                        out.write(f"  dip>{dip_min:.1f}% TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. VWAP RECLAIM BUY
        # ═══════════════════════════════════════
        print("Pattern 3: VWAP reclaim...")
        out.write(f"\n\n"+"="*110+"\n3. VWAP RECLAIM: price drops below VWAP, then crosses back above\n"+"="*110+"\n")
        out.write(f"  Entry: at b6 if price was below VWAP at b3-b4 but above VWAP at b5-b6.\n\n")

        for tp_target in [0.2, 0.3, 0.5]:
            for exit_b in [30, 45, 66]:
                dpnls=[]; trades=0; tp_hits=0; wins=0
                for d in dates:
                    pool = []
                    for rec in by_date[d]:
                        bkt = rec['bkt']; nb = rec['nb']
                        if bkt[3,iVW] <= 0 or bkt[5,iVW] <= 0: continue
                        # Was below VWAP at b3-b4
                        below_vwap_early = bkt[3,iC] < bkt[3,iVW] or bkt[4,iC] < bkt[4,iVW]
                        # Now above VWAP at b5-b6
                        above_vwap_now = bkt[5,iC] > bkt[5,iVW] and bkt[6,iC] > bkt[6,iVW]
                        if not (below_vwap_early and above_vwap_now): continue
                        bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                        score = bp * abs(mom+0.1) * 10
                        pool.append((rec, score))
                    pool.sort(key=lambda x: -x[1])
                    picks = pool[:5]
                    dp = 0
                    for rec, score in picks:
                        tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                        pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                        if tp_hit: tp_hits+=1
                        if pnl>0: wins+=1
                    dpnls.append(dp)
                if trades < 30: continue
                roc = sum(dpnls)/(BASE*5)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_hits/max(trades,1)*100
                out.write(f"  TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. OPENING RANGE BREAKOUT UP
        # ═══════════════════════════════════════
        print("Pattern 4: ORB up...")
        out.write(f"\n\n"+"="*110+"\n4. OPENING RANGE BREAKOUT: buy when price breaks above first 5-bar high\n"+"="*110+"\n")
        out.write(f"  Entry: at b6 if close(b5) > max(high b0-b4) = new intraday high.\n\n")

        for tp_target in [0.2, 0.3, 0.5]:
            for exit_b in [30, 45, 66]:
                dpnls=[]; trades=0; tp_hits=0; wins=0
                for d in dates:
                    pool = []
                    for rec in by_date[d]:
                        bkt = rec['bkt']; nb = rec['nb']
                        first5_high = float(np.max(bkt[:5, iH]))
                        if bkt[5,iC] <= first5_high: continue  # no breakout
                        if bkt[5,iC] <= 0: continue
                        bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                        # Volume confirmation
                        vol_spike = bkt[5,iV] > 1.5 * np.mean(bkt[:5,iV]) if np.mean(bkt[:5,iV]) > 0 else False
                        score = mom * bp * (1.5 if vol_spike else 1.0) * 10
                        pool.append((rec, score, vol_spike))
                    pool.sort(key=lambda x: -x[1])
                    picks = pool[:5]
                    dp = 0
                    for rec, score, vol_spike in picks:
                        tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                        pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                        if tp_hit: tp_hits+=1
                        if pnl>0: wins+=1
                    dpnls.append(dp)
                if trades < 30: continue
                roc = sum(dpnls)/(BASE*5)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_hits/max(trades,1)*100
                out.write(f"  TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. OVERSOLD BOUNCE: big early drop then buy
        # ═══════════════════════════════════════
        print("Pattern 5: Oversold bounce...")
        out.write(f"\n\n"+"="*110+"\n5. OVERSOLD BOUNCE: large drop in first 5 min, then buy expecting snap-back\n"+"="*110+"\n")
        out.write(f"  Condition: price drops >1% from open in first 5 bars, then buy at b6.\n\n")

        for tp_target in [0.3, 0.5, 0.7, 1.0]:
            for exit_b in [20, 30, 45, 66]:
                for drop_min in [0.5, 0.7, 1.0, 1.5, 2.0]:
                    dpnls=[]; trades=0; tp_hits=0; wins=0
                    for d in dates:
                        pool = []
                        for rec in by_date[d]:
                            bkt = rec['bkt']; nb = rec['nb']
                            open_p = float(bkt[0,iO])
                            if open_p <= 0: continue
                            low_5 = float(np.min(bkt[:6,iL]))
                            drop = (open_p - low_5) / open_p * 100
                            if drop < drop_min: continue
                            # Buy at b6 — check if showing recovery
                            bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                            # Want to see some recovery sign: last candle green or bp > 0.5
                            if bp < 0.45: continue  # still selling
                            score = drop * bp * 10
                            pool.append((rec, score))
                        pool.sort(key=lambda x: -x[1])
                        picks = pool[:5]
                        dp = 0
                        for rec, score in picks:
                            tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                            pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                            if tp_hit: tp_hits+=1
                            if pnl>0: wins+=1
                        dpnls.append(dp)
                    if trades < 20: continue
                    roc = sum(dpnls)/(BASE*5)*100
                    dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                    tw = wins/max(trades,1)*100
                    tpr = tp_hits/max(trades,1)*100
                    if tpr > 40:
                        out.write(f"  drop>{drop_min:.1f}% TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. GAP-UP CONTINUATION BUY
        # ═══════════════════════════════════════
        print("Pattern 6: Gap-up continuation...")
        out.write(f"\n\n"+"="*110+"\n6. GAP-UP CONTINUATION: stock gaps up AND keeps rising — ride the momentum\n"+"="*110+"\n")
        out.write(f"  Condition: gap>0.5%, first 5 bars are bullish (bp>0.6, mom>0), buy at b6.\n\n")

        for tp_target in [0.2, 0.3, 0.5, 0.7]:
            for exit_b in [20, 30, 45, 66]:
                dpnls=[]; trades=0; tp_hits=0; wins=0
                for d in dates:
                    pool = []
                    for rec in by_date[d]:
                        if rec['gap'] < 0.5: continue
                        bkt = rec['bkt']; nb = rec['nb']
                        bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                        if bp < 0.55 or mom < 0: continue  # not bullish enough
                        above_vwap = bkt[6,iC] > bkt[6,iVW] if bkt[6,iVW] > 0 else False
                        if not above_vwap: continue
                        score = rec['gap'] * bp * (1 + mom * 0.5) * 10
                        pool.append((rec, score))
                    pool.sort(key=lambda x: -x[1])
                    picks = pool[:5]
                    dp = 0
                    for rec, score in picks:
                        tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                        pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                        if tp_hit: tp_hits+=1
                        if pnl>0: wins+=1
                    dpnls.append(dp)
                if trades < 30: continue
                roc = sum(dpnls)/(BASE*5)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_hits/max(trades,1)*100
                if tpr > 35:
                    out.write(f"  TP={tp_target:.1f}% exit=b{exit_b}: trades={trades:>4} TP%={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+6.1f}%\n")
        print(f"  Pattern 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. EXHAUSTIVE COMBO SEARCH
        # ═══════════════════════════════════════
        print("Section 7: Exhaustive combos...")
        out.write(f"\n\n"+"="*110+"\n7. EXHAUSTIVE COMBO: sweep gap+bp+mom+vwap+exit for best BUY config\n"+"="*110+"\n\n")

        best_results = []

        for gap_filter in [('any', -10, 10), ('dn', -10, -0.3), ('flat', -0.5, 0.5), ('up', 0.5, 10)]:
            for bp_min in [0.45, 0.50, 0.55, 0.60]:
                for mom_filter in [('any', -99, 99), ('neg', -99, 0), ('pos', 0, 99), ('strong+', 0.3, 99)]:
                    for vwap_req in [False, True]:
                        for tp_target in [0.3, 0.5]:
                            for exit_b in [30, 45, 66]:
                                for n_pos in [3, 5]:
                                    dpnls=[]; trades=0; tp_hits=0; wins=0
                                    for d in dates:
                                        pool = []
                                        for rec in by_date[d]:
                                            gap = rec['gap']
                                            if not (gap_filter[1] <= gap <= gap_filter[2]): continue
                                            bkt = rec['bkt']; nb = rec['nb']
                                            bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                                            if bp < bp_min: continue
                                            if not (mom_filter[1] <= mom <= mom_filter[2]): continue
                                            if vwap_req and (bkt[6,iVW] <= 0 or bkt[6,iC] <= bkt[6,iVW]): continue
                                            score = abs(gap+0.1) * bp * max(abs(mom)+0.1, 0.1) * 10
                                            pool.append((rec, score))
                                        pool.sort(key=lambda x: -x[1])
                                        picks = pool[:n_pos]
                                        dp = 0
                                        for rec, score in picks:
                                            tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, tp_target, exit_b)
                                            pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                                            if tp_hit: tp_hits+=1
                                            if pnl>0: wins+=1
                                        dpnls.append(dp)
                                    if trades < 50: continue
                                    roc = sum(dpnls)/(BASE*n_pos)*100
                                    dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                                    tw = wins/max(trades,1)*100
                                    tpr = tp_hits/max(trades,1)*100
                                    if dw >= 60 and tpr >= 50:
                                        config_str = f"gap={gap_filter[0]} bp>{bp_min} mom={mom_filter[0]} vwap={'Y' if vwap_req else 'N'} TP={tp_target} exit=b{exit_b} n={n_pos}"
                                        best_results.append((dw, tpr, roc, tw, trades, config_str))

        best_results.sort(key=lambda x: (-x[0], -x[1]))
        out.write(f"  Top-30 BUY configs (sorted by day-win then TP%):\n\n")
        out.write(f"  {'DayW':>6} {'TP%':>6} {'TrdW':>6} {'ROC':>8} {'N':>5}  Config\n")
        out.write(f"  "+"-"*80+"\n")
        for dw, tpr, roc, tw, trades, cfg in best_results[:30]:
            out.write(f"  {dw:>4.1f}%  {tpr:>4.1f}%  {tw:>4.1f}%  {roc:>+7.1f}%  {trades:>4}  {cfg}\n")
        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. BEST BUY vs SELL COMBINED
        # ═══════════════════════════════════════
        print("Section 8: Combined...")
        out.write(f"\n\n"+"="*110+"\n8. COMBINED: best BUY + current SELL system together\n"+"="*110+"\n")
        out.write(f"  Run SELL (top-7 gap-up V2) + BUY (best pattern) on same days.\n\n")

        # Take the best pattern from above and simulate combined
        # For now, use gap-down reversal with bp>0.55 + TP=0.5% + exit=b45
        for sell_n, buy_n in [(7,0), (7,1), (7,2), (7,3), (7,5), (10,2), (12,0)]:
            dpnls=[]
            for d in dates:
                dp = 0
                # SELL side (gap-up reversal, V2 score)
                sell_pool = []
                for rec in by_date[d]:
                    if rec['gap'] < 0.5: continue
                    bkt = rec['bkt']; nb = rec['nb']
                    bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                    if sp < 0.45 and nr <= 1: continue  # V2 reject
                    mm = 1.4 if mom < -0.5 else (1.1 if mom < 0 else 0.7)
                    v2 = rec['gap'] * sp * mm * 15
                    sell_pool.append((rec, v2))
                sell_pool.sort(key=lambda x: -x[1])
                for rec, score in sell_pool[:sell_n]:
                    entry = float(rec['bkt'][6,iO])
                    if entry <= 0: continue
                    # SELL TP (0.33% scaled)
                    tp_price = entry * (1 - 0.33/100)
                    hit = False
                    for b in range(7, min(77, rec['nb'])):
                        if rec['bkt'][b,iL] <= tp_price:
                            dp += BASE*MARGIN*0.33/100 - COST/100*BASE*MARGIN; hit=True; break
                    if not hit:
                        exit_p = float(rec['bkt'][min(76,rec['nb']-1),iC])
                        ret = (entry - exit_p)/entry*100 - COST
                        dp += BASE*MARGIN*ret/100

                # BUY side (best pattern)
                buy_pool = []
                for rec in by_date[d]:
                    if rec['gap'] > -0.3: continue  # gap-down or flat
                    bkt = rec['bkt']; nb = rec['nb']
                    bp, sp, mom, ng, nr, vwap, avgv = compute_buy_features(bkt, nb)
                    if bp < 0.50: continue
                    above_vwap = bkt[6,iC] > bkt[6,iVW] if bkt[6,iVW] > 0 else False
                    score = abs(rec['gap']+0.1) * bp * 10
                    buy_pool.append((rec, score))
                buy_pool.sort(key=lambda x: -x[1])
                for rec, score in buy_pool[:buy_n]:
                    tp_hit, tp_b, ret = check_buy_tp(rec['bkt'], rec['nb'], 6, 0.5, 45)
                    dp += BASE*MARGIN*ret/100

                dpnls.append(dp)
            total_n = max(sell_n + buy_n, 1)
            roc = sum(dpnls)/(BASE*total_n)*100
            dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {sell_n}S+{buy_n}B: ROC={roc:>+7.1f}% dayWin={dw:>5.1f}%\n")

        out.write(f"\n\n"+"="*110+"\n9. SUMMARY\n"+"="*110+"\n")
        out.write(f"""
  Look at sections 1-6 for which BUY patterns have highest TP hit rate.
  Section 7 shows the exhaustive search results.
  Section 8 shows combined SELL+BUY performance.

  Key metrics for a good BUY pattern:
    - TP hit rate > 55% (matches your SELL system)
    - Day win rate > 60%
    - Positive ROC
    - At least 3-5 trades per day

  Remember: 5x margin means 0.5% TP on Rs 10k = Rs 250 profit per trade.
  With 5 BUY trades/day at 60% TP rate = ~Rs 500/day extra.
""")

    print(f"\nCompleted in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
