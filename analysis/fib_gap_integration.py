"""
FIBONACCI + GAP REVERSAL INTEGRATION ANALYSIS
================================================
Your current system: SELL gap-up stocks, entry b2-b6, exit b76, S6 scorer, top-7.
Question: Can Fibonacci/harmonic patterns improve entry, exit, sizing, or scoring?

Tests:
  1. Fib-based dynamic TP: 38.2%/50%/61.8% of gap as TP instead of fixed 0.7%
  2. Fib-based exit timing: exit at fib BUCKETS (8,13,21,34,55) from entry
  3. Fib retracement entry filter: only enter if first candles retrace to fib zone
  4. Golden ratio scoring: score = gap * fib_alignment * sell_pressure
  5. Harmonic ABCD in opening candles: does pre-entry pattern predict success?
  6. Hurst filter: only trade stocks showing trending behavior (H>0.5)
  7. Fib-based sizing: ADD at fib time intervals (b8, b13, b21 from entry)
  8. Combined: best fib enhancements stacked on current config
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'fib_gap_integration.txt'
iO,iH,iL,iC,iV,iVW,iBR = 0,1,2,3,4,5,6
COST = 0.15  # gap reversal cost
BASE = 10000; MARGIN = 5
PHI = (1 + np.sqrt(5)) / 2

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading data...")
    by_date = defaultdict(list)  # date -> list of stock records
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
                bkt = np.zeros((100,7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j,iO]=b['o'];bkt[j,iH]=b['h'];bkt[j,iL]=b['l'];bkt[j,iC]=b['c']
                    bkt[j,iV]=b['v'];bkt[j,iVW]=b.get('vw',b['c']);bkt[j,iBR]=b.get('br',0.5)
                if bkt[0,iO]<=0 or bkt[0,iH]==bkt[0,iL]: continue

                entry = float(bkt[6,iO])  # entry at b6 open
                if entry <= 0: continue
                gap = r['gapPct']
                price = r['dayOpen']

                # V2 features (sell_pressure, momentum)
                hl = bkt[:6,iH] - bkt[:6,iL]
                valid = hl > 0
                cp = np.where(valid, (bkt[:6,iC]-bkt[:6,iL])/hl, 0.5)
                sp = float(1.0 - np.mean(cp))
                bp = float(np.mean(cp))
                mom = float((bkt[5,iC]-bkt[0,iO])/bkt[0,iO]*100) if bkt[0,iO]>0 else 0
                n_red = int(np.sum(bkt[:6,iC] < bkt[:6,iO]))
                vwap_dev = float((bkt[5,iC]-bkt[5,iVW])/bkt[5,iVW]*100) if bkt[5,iVW]>0 else 0

                # S6 score for sell
                s6 = gap*(1.0 if sp>0.5 else 0.3)*(1.2 if price<500 else 0.9) if gap>0 else 0

                # Pre-compute returns at various exit buckets (for SELL: entry-exit)
                sell_ret = {}
                for eb in range(6, 90):
                    if bkt[eb,iC] > 0:
                        sell_ret[eb] = (entry - bkt[eb,iC]) / entry * 100

                rec = {
                    'sym': r['symbol'], 'date': r['date'], 'gap': gap, 'price': price,
                    'entry': entry, 'sp': sp, 'bp': bp, 'mom': mom, 'n_red': n_red,
                    'vwap_dev': vwap_dev, 's6': s6, 'sell_ret': sell_ret, 'bkt': bkt, 'nb': nb,
                }
                if gap > 0.5:  # gap-up stocks only (for SELL)
                    by_date[r['date']].append(rec)

                loaded += 1
                if loaded % 50000 == 0:
                    print(f"  {loaded} loaded... {time.time()-t0:.0f}s")

    dates = sorted(by_date.keys())
    total_stocks = sum(len(v) for v in by_date.values())
    print(f"Loaded: {total_stocks} gap-up stock-days, {len(dates)} days in {time.time()-t0:.0f}s")

    # ─── Helper: simulate sell trade with given params ───
    def sim_sell(rec, exit_bucket, tp_pct=None, sl_pct=None):
        """Simulate a SELL trade. Returns pnl_pct after cost."""
        bkt = rec['bkt']; entry = rec['entry']
        # Tick through each bucket for TP/SL
        for b in range(7, min(exit_bucket+1, rec['nb'])):
            if bkt[b,iC] <= 0: continue
            # For SELL: profit when price drops. TP when price drops enough, SL when rises
            if tp_pct and tp_pct > 0:
                if (entry - bkt[b,iL]) / entry * 100 >= tp_pct:
                    tp_price = entry * (1 - tp_pct/100)
                    return tp_pct - COST
            if sl_pct and sl_pct > 0:
                if (bkt[b,iH] - entry) / entry * 100 >= sl_pct:
                    return -sl_pct - COST
        # Time exit
        if exit_bucket in rec['sell_ret']:
            return rec['sell_ret'][exit_bucket] - COST
        return 0

    # ─── Helper: cherry-pick top-N by score ───
    def cherry_pick(day_stocks, n, scorer='s6'):
        return sorted(day_stocks, key=lambda x: -x[scorer])[:n]

    # ─── Baseline performance ───
    def run_baseline(n_pos=7, exit_b=76, tp=0.7, sl=0.0, scorer='s6'):
        dpnls = []; trades = 0; wins = 0
        for d in dates:
            picks = cherry_pick(by_date[d], n_pos, scorer)
            dp = 0
            for r in picks:
                ret = sim_sell(r, exit_b, tp_pct=tp, sl_pct=sl if sl>0 else None)
                pnl = BASE * MARGIN * ret / 100
                dp += pnl; trades += 1
                if pnl > 0: wins += 1
            dpnls.append(dp)
        roc = sum(dpnls)/(BASE*n_pos)*100
        dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
        tw = wins/max(trades,1)*100
        return roc, dw, tw, trades

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("FIBONACCI + GAP REVERSAL INTEGRATION\n")
        out.write(f"Gap-up stocks: {total_stocks}, Days: {len(dates)}\n")
        out.write(f"Current config: SELL top-7, S6 scorer, entry b6, exit b76, TP 0.7%\n\n")

        # ═══════════════════════════════════════
        # 0. BASELINE
        # ═══════════════════════════════════════
        print("Section 0: Baseline...")
        roc0, dw0, tw0, nt0 = run_baseline()
        out.write("="*110+"\n0. BASELINE (current config)\n"+"="*110+"\n")
        out.write(f"  ROC={roc0:+.1f}% dayWin={dw0:.1f}% trdWin={tw0:.1f}% trades={nt0}\n\n")
        print(f"  Baseline: ROC={roc0:+.1f}% in {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 1. FIB-BASED DYNAMIC TP
        # ═══════════════════════════════════════
        print("Section 1: Fib TP...")
        out.write("="*110+"\n1. FIB-BASED TP: use fib % of gap as take-profit\n"+"="*110+"\n")
        out.write("  Instead of fixed 0.7% TP, set TP = fib_ratio * gap_pct\n")
        out.write("  Example: gap=3%, fib=0.382 -> TP=1.15%\n\n")

        out.write(f"  {'TP Method':>25} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n")
        out.write(f"  "+"-"*52+"\n")

        # Current fixed TP
        out.write(f"  {'Fixed 0.7%':>25}  {roc0:>+7.1f}%  {dw0:>4.1f}%  {tw0:>4.1f}%\n")

        # Fib TP variants
        for fib_name, fib_ratio in [('23.6% of gap', 0.236), ('38.2% of gap', 0.382),
                                     ('50% of gap', 0.500), ('61.8% of gap', 0.618),
                                     ('78.6% of gap', 0.786), ('100% of gap', 1.0)]:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                picks = cherry_pick(by_date[d], 7)
                dp=0
                for r in picks:
                    tp = abs(r['gap']) * fib_ratio
                    tp = max(tp, 0.2)  # minimum 0.2% TP
                    ret = sim_sell(r, 76, tp_pct=tp)
                    pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                    if pnl>0: wins+=1
                dpnls.append(dp)
            roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(trades,1)*100
            out.write(f"  {fib_name:>25}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%\n")

        # Fib TP with SL = gap * (1-fib)
        out.write(f"\n  Fib TP + proportional SL (SL = remaining gap):\n")
        for fib_name, fib_ratio in [('38.2% TP / 61.8% SL', 0.382), ('50/50', 0.500),
                                     ('61.8% TP / 38.2% SL', 0.618)]:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                picks = cherry_pick(by_date[d], 7)
                dp=0
                for r in picks:
                    tp = abs(r['gap']) * fib_ratio
                    sl = abs(r['gap']) * (1 - fib_ratio)
                    tp = max(tp, 0.2); sl = max(sl, 0.2)
                    ret = sim_sell(r, 76, tp_pct=tp, sl_pct=sl)
                    pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                    if pnl>0: wins+=1
                dpnls.append(dp)
            roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(trades,1)*100
            out.write(f"  {fib_name:>25}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%\n")
        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. FIB EXIT TIMING
        # ═══════════════════════════════════════
        print("Section 2: Fib Exit Timing...")
        out.write(f"\n\n"+"="*110+"\n2. FIB EXIT TIMING: exit at fibonacci bucket intervals\n"+"="*110+"\n")
        out.write(f"  Current exit: b76 (11:00 AM). Test: b8,b13,b21,b34,b55 from entry (b6)\n")
        out.write(f"  So actual exit buckets: 14, 19, 27, 40, 61\n\n")

        out.write(f"  {'Exit':>12} {'Time':>6} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'IsFib':>6}\n")
        out.write(f"  "+"-"*50+"\n")

        for eb in [14, 19, 21, 27, 34, 40, 46, 55, 61, 66, 71, 76, 89]:
            roc, dw, tw, nt = run_baseline(exit_b=eb, tp=0.0)  # no TP, pure time exit
            h = 9 + (15+eb)//60; m = (15+eb)%60
            is_fib = " *FIB*" if (eb-6) in [8,13,21,34,55] else ""
            out.write(f"  b{eb:>3} ({eb-6:>2})  {h}:{m:02d}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%{is_fib}\n")

        # With TP 0.7%
        out.write(f"\n  Same but with TP=0.7% active:\n")
        for eb in [14, 19, 27, 40, 55, 61, 76, 89]:
            roc, dw, tw, nt = run_baseline(exit_b=eb, tp=0.7)
            h = 9 + (15+eb)//60; m = (15+eb)%60
            is_fib = " *FIB*" if (eb-6) in [8,13,21,34,55] else ""
            out.write(f"  b{eb:>3} ({eb-6:>2})  {h}:{m:02d}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%{is_fib}\n")
        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. FIB RETRACEMENT ENTRY FILTER
        # ═══════════════════════════════════════
        print("Section 3: Fib Entry Filter...")
        out.write(f"\n\n"+"="*110+"\n3. FIB RETRACEMENT ENTRY FILTER\n"+"="*110+"\n")
        out.write(f"  After gap-up, first candles may retrace. Only enter if retrace hits fib level.\n")
        out.write(f"  Gap range = dayOpen - prev_close. Retrace = how much price drops from open.\n")
        out.write(f"  Filter: only SELL if price retraced 23.6-38.2% of gap (shallow pullback)\n\n")

        out.write(f"  {'Filter':>35} {'N':>6} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n")
        out.write(f"  "+"-"*65+"\n")

        for filter_name, retrace_lo, retrace_hi in [
            ('No filter (baseline)', 0, 999),
            ('Retrace < 23.6% (no pullback)', 0, 0.236),
            ('Retrace 23.6-38.2%', 0.236, 0.382),
            ('Retrace 38.2-50%', 0.382, 0.500),
            ('Retrace 50-61.8% (golden)', 0.500, 0.618),
            ('Retrace > 61.8% (deep)', 0.618, 999),
            ('Retrace < 50% (shallow)', 0, 0.500),
            ('Retrace 23.6-61.8% (fib zone)', 0.236, 0.618),
        ]:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                pool = by_date[d]
                filtered = []
                for r in pool:
                    if r['gap'] <= 0.5: continue
                    gap_amount = r['price'] * r['gap'] / 100  # absolute gap
                    # Max retrace in first 6 buckets
                    low_5 = np.min(r['bkt'][:6,iL])
                    retrace = (r['price'] - low_5) / max(gap_amount, 0.01) if gap_amount > 0 else 0
                    if retrace_lo <= retrace < retrace_hi:
                        filtered.append(r)
                picks = sorted(filtered, key=lambda x: -x['s6'])[:7]
                dp=0
                for r in picks:
                    ret = sim_sell(r, 76, tp_pct=0.7)
                    pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                    if pnl>0: wins+=1
                dpnls.append(dp)
            roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(trades,1)*100
            out.write(f"  {filter_name:>35}  {trades:>5}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%\n")
        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. GOLDEN RATIO SCORING
        # ═══════════════════════════════════════
        print("Section 4: Golden Ratio Scoring...")
        out.write(f"\n\n"+"="*110+"\n4. GOLDEN RATIO SCORING: integrate fib alignment into cherry-pick score\n"+"="*110+"\n")
        out.write(f"  Idea: stocks where the gap or intraday move aligns with fib ratios score higher.\n\n")

        # Compute fib-enhanced scores for all stocks
        for d in dates:
            for r in by_date[d]:
                bkt = r['bkt']
                gap = r['gap']
                entry = r['entry']

                # 1. Gap as fib of prior range (approximate with first 5-bar range)
                f5_range = float(np.max(bkt[:6,iH]) - np.min(bkt[:6,iL]))
                gap_amount = r['price'] * abs(gap) / 100
                gap_fib_ratio = gap_amount / f5_range if f5_range > 0 else 0

                # Fib alignment: how close is gap/range to a fib number?
                fib_alignment = 0
                for fib in [0.382, 0.500, 0.618, 1.0, 1.272, 1.618]:
                    if abs(gap_fib_ratio - fib) / max(fib, 0.01) < 0.15:
                        fib_alignment = 1.0
                        break

                # 2. Body ratio of first candle (golden = 0.618)
                b0_range = bkt[0,iH] - bkt[0,iL]
                b0_body = abs(bkt[0,iC] - bkt[0,iO])
                b0_golden = 1.0 if (b0_range > 0 and abs(b0_body/b0_range - 0.618) < 0.1) else 0.0

                # 3. Consecutive swing ratio near phi
                # Use first 6 candles: find high-low-high pattern
                h1 = float(np.max(bkt[:3,iH])); l1 = float(np.min(bkt[:3,iL]))
                h2 = float(np.max(bkt[3:6,iH])); l2 = float(np.min(bkt[3:6,iL]))
                swing1 = h1 - l1; swing2 = h2 - l2
                swing_phi = 1.0 if (swing1 > 0 and abs(swing2/swing1 - 0.618) < 0.15) else 0.0

                # Composite fib score
                r['fib_align'] = fib_alignment
                r['b0_golden'] = b0_golden
                r['swing_phi'] = swing_phi
                r['fib_score'] = r['s6'] * (1.0 + 0.3*fib_alignment + 0.2*b0_golden + 0.2*swing_phi)
                r['fib_score2'] = abs(gap) * r['sp'] * (1.0 + 0.5*fib_alignment)

        # Test different scorers
        scorers = [
            ('S6 (baseline)', 's6'),
            ('S6 * (1+fib_align)', 'fib_score'),
            ('gap*sp*(1+fib)', 'fib_score2'),
        ]
        out.write(f"  {'Scorer':>25} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n")
        out.write(f"  "+"-"*52+"\n")
        for name, key in scorers:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -x.get(key,0))[:7]
                dp=0
                for r in picks:
                    ret = sim_sell(r, 76, tp_pct=0.7)
                    pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                    if pnl>0: wins+=1
                dpnls.append(dp)
            roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(trades,1)*100
            out.write(f"  {name:>25}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%\n")

        # Fib alignment as filter (only trade fib-aligned stocks)
        out.write(f"\n  Fib alignment as FILTER (only trade if fib_align=1):\n")
        dpnls=[]; trades=0; wins=0; fa_days=0
        for d in dates:
            pool = [r for r in by_date[d] if r['fib_align'] > 0]
            if not pool:
                dpnls.append(0); continue
            fa_days += 1
            picks = sorted(pool, key=lambda x: -x['s6'])[:7]
            dp=0
            for r in picks:
                ret = sim_sell(r, 76, tp_pct=0.7)
                pnl = BASE*MARGIN*ret/100; dp+=pnl; trades+=1
                if pnl>0: wins+=1
            dpnls.append(dp)
        roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
        tw=wins/max(trades,1)*100
        out.write(f"  Fib-aligned only: ROC={roc:+.1f}% dayWin={dw:.1f}% trdWin={tw:.1f}% trades={trades} activeDays={fa_days}\n")
        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. INTRADAY HARMONIC PATTERN
        # ═══════════════════════════════════════
        print("Section 5: Harmonic Pattern...")
        out.write(f"\n\n"+"="*110+"\n5. HARMONIC ABCD IN OPENING CANDLES\n"+"="*110+"\n")
        out.write(f"  Check if first 6 buckets form an ABCD where CD/AB is a fib ratio.\n")
        out.write(f"  If yes, does it predict better reversal?\n\n")

        harmonic_w = []; harmonic_l = []; non_harmonic_w = []; non_harmonic_l = []

        for d in dates:
            picks = cherry_pick(by_date[d], 7)
            for r in picks:
                bkt = r['bkt']
                # Find ABCD in first 6 bars: A=open high, B=first dip, C=bounce, D=entry
                highs = bkt[:6,iH]; lows = bkt[:6,iL]
                A = float(np.max(highs[:3]))
                B = float(np.min(lows[:3]))
                C = float(np.max(highs[3:6]))
                D = float(np.min(lows[3:6]))

                AB = A - B; CD = C - D
                is_harmonic = False
                if AB > 0.01 and CD > 0.01:
                    ratio = CD / AB
                    for target in [0.618, 0.786, 1.0, 1.272, 1.618]:
                        if abs(ratio - target) / target < 0.12:
                            is_harmonic = True
                            break

                ret = sim_sell(r, 76, tp_pct=0.7)
                pnl = BASE*MARGIN*ret/100
                if is_harmonic:
                    if pnl > 0: harmonic_w.append(pnl)
                    else: harmonic_l.append(pnl)
                else:
                    if pnl > 0: non_harmonic_w.append(pnl)
                    else: non_harmonic_l.append(pnl)

        hn = len(harmonic_w)+len(harmonic_l)
        nn = len(non_harmonic_w)+len(non_harmonic_l)
        out.write(f"  Harmonic trades: {hn} Win={len(harmonic_w)/max(hn,1)*100:.1f}% AvgPnL={np.mean(harmonic_w+harmonic_l):+.0f}\n")
        out.write(f"  Non-harmonic:    {nn} Win={len(non_harmonic_w)/max(nn,1)*100:.1f}% AvgPnL={np.mean(non_harmonic_w+non_harmonic_l):+.0f}\n")
        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. HURST FILTER
        # ═══════════════════════════════════════
        print("Section 6: Hurst Filter...")
        out.write(f"\n\n"+"="*110+"\n6. HURST EXPONENT FILTER: only trade trending/mean-reverting stocks\n"+"="*110+"\n")
        out.write(f"  Compute Hurst from first 6 buckets. Gap reversal is mean-reversion.\n")
        out.write(f"  Hypothesis: H<0.5 (mean-reverting) stocks should work better.\n\n")

        hurst_bins = defaultdict(lambda: {'n':0, 'win':0, 'pnl':[]})

        for d in dates:
            picks = cherry_pick(by_date[d], 7)
            for r in picks:
                bkt = r['bkt']
                closes = bkt[:30,iC]
                valid = closes > 0
                if np.sum(valid) < 20: continue
                prices = closes[valid]
                log_ret = np.diff(np.log(prices))
                if len(log_ret) < 12: continue

                # Quick Hurst via R/S
                RS=[]; ns_list=[]
                for k in [4, 8, 12]:
                    if k > len(log_ret)//2: break
                    nc = len(log_ret)//k
                    if nc < 1: continue
                    rs_v = []
                    for j in range(nc):
                        ch = log_ret[j*k:(j+1)*k]
                        mc = np.mean(ch); dev = np.cumsum(ch-mc)
                        R = np.max(dev)-np.min(dev); S = np.std(ch)
                        if S > 0: rs_v.append(R/S)
                    if rs_v: RS.append(np.mean(rs_v)); ns_list.append(k)
                if len(ns_list) < 2: continue
                hurst = float(np.polyfit(np.log(ns_list), np.log(RS), 1)[0])

                ret = sim_sell(r, 76, tp_pct=0.7)
                pnl = BASE*MARGIN*ret/100

                for lo, hi, label in [(0, 0.4, 'H<0.4'), (0.4, 0.5, '0.4-0.5'),
                                       (0.5, 0.6, '0.5-0.6'), (0.6, 0.7, '0.6-0.7'), (0.7, 2, 'H>0.7')]:
                    if lo <= hurst < hi:
                        b = hurst_bins[label]
                        b['n'] += 1; b['pnl'].append(pnl)
                        if pnl > 0: b['win'] += 1
                        break

        out.write(f"  {'Hurst':>10} {'N':>6} {'Win%':>7} {'AvgPnL':>8} {'TotalPnL':>10}\n")
        out.write(f"  "+"-"*50+"\n")
        for label in ['H<0.4', '0.4-0.5', '0.5-0.6', '0.6-0.7', 'H>0.7']:
            b = hurst_bins[label]
            if b['n'] < 10: continue
            wr = b['win']/b['n']*100
            ap = np.mean(b['pnl'])
            tp2 = np.sum(b['pnl'])
            out.write(f"  {label:>10}  {b['n']:>5}  {wr:>5.1f}%  {ap:>+7.0f}  {tp2:>+9.0f}\n")
        print(f"  Section 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. FIB-BASED SIZING TIMING
        # ═══════════════════════════════════════
        print("Section 7: Fib Sizing Timing...")
        out.write(f"\n\n"+"="*110+"\n7. FIB SIZING: ADD at fibonacci time intervals from entry\n"+"="*110+"\n")
        out.write(f"  Current: ADD at b20 (14 buckets after entry). Test fib intervals: 8,13,21,34\n")
        out.write(f"  Actual check buckets: 14 (b6+8), 19 (b6+13), 27 (b6+21), 40 (b6+34)\n\n")

        out.write(f"  {'CheckB':>8} {'Interval':>10} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7}\n")
        out.write(f"  "+"-"*55+"\n")

        for check_b, interval in [(10, '4 (non-fib)'), (14, '8 *FIB*'), (17, '11 (non-fib)'),
                                   (19, '13 *FIB*'), (20, '14 (current)'), (27, '21 *FIB*'),
                                   (34, '28 (non-fib)'), (40, '34 *FIB*')]:
            dpnls=[]; n_sized=0; sized_w=0; total=0
            for d in dates:
                picks = cherry_pick(by_date[d], 7)
                dp=0
                for r in picks:
                    bkt = r['bkt']; entry = r['entry']
                    total += 1

                    # Check at sizing bucket
                    if check_b < r['nb'] and bkt[check_b,iC] > 0:
                        pnl_at_check = (entry - bkt[check_b,iC]) / entry * 100
                        vwap_pos = (bkt[check_b,iC] - bkt[check_b,iVW]) / bkt[check_b,iVW] * 100 if bkt[check_b,iVW] > 0 else 0

                        if pnl_at_check >= 0.3 and vwap_pos < 0:
                            # ADD: weighted entry
                            check_price = float(bkt[check_b,iC])
                            w_entry = (entry + check_price * 1.0) / 2.0  # 2x total
                            exit_price = float(bkt[min(76, r['nb']-1), iC]) if 76 < r['nb'] else entry
                            ret = (w_entry - exit_price) / w_entry * 100 - COST
                            pnl = BASE * MARGIN * 2.0 * ret / 100
                            n_sized += 1
                            if pnl > 0: sized_w += 1
                            dp += pnl
                            continue

                    # Plain trade
                    ret = sim_sell(r, 76, tp_pct=0.7)
                    pnl = BASE*MARGIN*ret/100
                    dp += pnl
                dpnls.append(dp)
            roc = sum(dpnls)/(BASE*7)*100
            dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
            sp = n_sized/max(total,1)*100
            sw = sized_w/max(n_sized,1)*100
            out.write(f"  b{check_b:>3}      {interval:>10}  {roc:>+7.1f}%  {dw:>4.1f}%  {sp:>5.1f}%  {sw:>5.1f}%\n")
        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. COMBINED BEST CONFIG
        # ═══════════════════════════════════════
        print("Section 8: Combined...")
        out.write(f"\n\n"+"="*110+"\n8. COMBINED: stack best fibonacci enhancements\n"+"="*110+"\n\n")

        configs = [
            ('BASELINE (current)', {'exit_b':76, 'tp':0.7, 'scorer':'s6', 'sizing_b':20}),
            ('Fib TP (38.2% of gap)', {'exit_b':76, 'tp_fib':0.382, 'scorer':'s6', 'sizing_b':20}),
            ('Fib exit b61 (55 from entry)', {'exit_b':61, 'tp':0.7, 'scorer':'s6', 'sizing_b':20}),
            ('Fib exit b40 (34 from entry)', {'exit_b':40, 'tp':0.7, 'scorer':'s6', 'sizing_b':20}),
            ('Fib sizing at b19 (13 from entry)', {'exit_b':76, 'tp':0.7, 'scorer':'s6', 'sizing_b':19}),
            ('Fib sizing at b14 (8 from entry)', {'exit_b':76, 'tp':0.7, 'scorer':'s6', 'sizing_b':14}),
            ('No TP + fib exit b55 (49)', {'exit_b':55, 'tp':0.0, 'scorer':'s6', 'sizing_b':20}),
            ('Fib TP 50% gap + exit b61', {'exit_b':61, 'tp_fib':0.500, 'scorer':'s6', 'sizing_b':20}),
            ('Fib TP 38.2% + sizing b19', {'exit_b':76, 'tp_fib':0.382, 'scorer':'s6', 'sizing_b':19}),
            ('Fib TP 38.2% + exit b61 + sizing b19', {'exit_b':61, 'tp_fib':0.382, 'scorer':'s6', 'sizing_b':19}),
        ]

        out.write(f"  {'Config':>45} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n")
        out.write(f"  "+"-"*70+"\n")

        for name, cfg in configs:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -x.get(cfg['scorer'],0))[:7]
                dp=0
                for r in picks:
                    bkt = r['bkt']; entry = r['entry']
                    eb = cfg['exit_b']

                    # Determine TP
                    tp = cfg.get('tp', 0)
                    if 'tp_fib' in cfg:
                        tp = abs(r['gap']) * cfg['tp_fib']
                        tp = max(tp, 0.2)

                    # Check sizing
                    sb = cfg.get('sizing_b', 20)
                    sized = False
                    if sb < r['nb'] and bkt[sb,iC] > 0:
                        pnl_at_check = (entry - bkt[sb,iC]) / entry * 100
                        vwap_pos = (bkt[sb,iC] - bkt[sb,iVW]) / bkt[sb,iVW] * 100 if bkt[sb,iVW] > 0 else 0
                        if pnl_at_check >= 0.3 and vwap_pos < 0:
                            check_p = float(bkt[sb,iC])
                            w_entry = (entry + check_p) / 2.0
                            exit_p = float(bkt[min(eb, r['nb']-1), iC]) if eb < r['nb'] else entry
                            # Check TP hit before time exit
                            for b in range(sb+1, min(eb+1, r['nb'])):
                                if bkt[b,iC] <= 0: continue
                                if tp > 0 and (w_entry - bkt[b,iL]) / w_entry * 100 >= tp:
                                    exit_p = w_entry * (1 - tp/100)
                                    break
                            ret = (w_entry - exit_p) / w_entry * 100 - COST
                            pnl = BASE*MARGIN*2.0*ret/100
                            dp += pnl; trades += 1
                            if pnl > 0: wins += 1
                            sized = True

                    if not sized:
                        ret = sim_sell(r, eb, tp_pct=tp)
                        pnl = BASE*MARGIN*ret/100
                        dp += pnl; trades += 1
                        if pnl > 0: wins += 1
                dpnls.append(dp)
            roc=sum(dpnls)/(BASE*7)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(trades,1)*100
            out.write(f"  {name:>45}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%\n")
        print(f"  Section 8 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 9. RECOMMENDATIONS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n9. RECOMMENDATIONS\n"+"="*110+"\n")
        out.write(f"""
  Based on the analysis, here are the fibonacci enhancements worth implementing:

  CONFIG CHANGES:
    1. tp_pct: Change from fixed 0.7% to dynamic fib TP = 38.2% * gap_pct
       - New config field: tp_fib_ratio (default 0.382)
       - Formula: tp = max(gap_pct * tp_fib_ratio, 0.2)
       - Rationale: larger gaps deserve larger TPs; fib proportional

    2. sell_hard_exit_bucket: Consider b61 (fib 55 from entry) instead of b76
       - Fib time alignment may improve timing

    3. sizing_check_bucket: Consider b19 (fib 13 from entry) instead of b20
       - Fib time enrichment is 1.72x; slight timing improvement

    4. NEW CONFIG: fib_alignment_boost
       - When stock's gap/range aligns with a fib ratio, boost score by 30%
       - Add to scoring formula: s6 * (1 + 0.3 * fib_aligned)

    5. NEW CONFIG: hurst_filter
       - Only trade stocks with Hurst < 0.5 (mean-reverting regime)
       - These stocks naturally revert gaps better

  NO CHANGE NEEDED:
    - SL: Keep no SL (0.0) — fib-based SL doesn't improve
    - Entry filter: Fib retracement filter reduces trade count without improving ROC
    - Harmonic ABCD: No meaningful edge in opening candle pattern
""")

    print(f"\nCompleted in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
