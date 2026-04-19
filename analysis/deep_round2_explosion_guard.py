"""
DEEP ANALYSIS ROUND 2: Explosion Guard + Optimal Scorer Refinement
====================================================================
Round 1 findings:
  - gap_sp_mom scorer: ROC +99.3% (top-8), +232.4% (top-3!) — formula: gap * sp * |mom| * 15
  - trap_bonus scorer: highest dayWin 68.4% — formula: V2 * (1 + min(trap,2)*0.3) * 12
  - gsmv scorer: ROC +80.9%, dayWin 61.8% — formula: gap * sp * |mom| * vwap_mult * gap_penalty
  - EXPLOSION failure mode: 78 trades, avg -3.5%, avg MAE 5.5% — BIGGEST DAMAGE
  - Key features of losers: gap too big (4.4% avg), price too high (915 avg)

Round 2 goals:
  1. Can we PREDICT explosions before they happen? What b0-b6 features signal danger?
  2. Refine gap_sp_mom with explosion guard
  3. Test dynamic top-N: use fewer positions on "dangerous" days
  4. Adaptive TP: should TP be different based on features?
  5. Early exit: can we detect losing trades at b10-b15 and cut losses?

NO LOOKAHEAD: all decisions from b0-b6 features only.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_round2.txt'
COST = 0.15
BASE = 10000; MARGIN = 5
iO,iH,iL,iC,iV,iVW,iBR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading data...")
    by_date = defaultdict(list)
    loaded = 0

    for fp in files:
        if not fp.exists(): continue
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                if r['gapPct'] < 0.1 or r['gapPct'] > 10: continue
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

                gap = r['gapPct']
                price = r['dayOpen']
                entry = float(bkt[6,iO])
                if entry <= 0: continue

                # ═══ FEATURES (b0-b6 only) ═══
                hl = bkt[2:7, iH] - bkt[2:7, iL]
                valid = hl > 0
                cp = np.where(valid, (bkt[2:7,iC]-bkt[2:7,iL])/np.maximum(hl,0.001), 0.5)
                sp = float(1.0 - np.mean(cp))
                mom = float((bkt[6,iC] - bkt[2,iO]) / bkt[2,iO] * 100) if bkt[2,iO] > 0 else 0
                n_red = int(np.sum(bkt[2:7,iC] < bkt[2:7,iO]))

                if sp < 0.45 and n_red <= 1: continue

                mom_mult = 1.4 if mom < -0.5 else (1.1 if mom < 0 else 0.7)
                v2_score = min(gap * sp * mom_mult * 15.0, 255)

                # Extended features
                vwap_dev = float((bkt[6,iC] - bkt[6,iVW]) / bkt[6,iVW] * 100) if bkt[6,iVW] > 0 else 0
                exhaust_range = bkt[2,iH] - bkt[2,iL]
                exhaust = float((bkt[2,iH] - bkt[2,iC]) / exhaust_range) if exhaust_range > 0 else 0.5

                early_high = float(np.max(bkt[0:3, iH]))
                trap_magnitude = (early_high - float(bkt[6,iC])) / entry * 100 if entry > 0 else 0

                consec_red_end = 0
                for bi in range(6, 1, -1):
                    if bkt[bi,iC] < bkt[bi,iO]: consec_red_end += 1
                    else: break

                # Volatility features (for explosion prediction)
                ranges = []
                for bi in range(0, 7):
                    r_val = bkt[bi,iH] - bkt[bi,iL]
                    if r_val > 0: ranges.append(r_val / entry * 100)
                avg_range_pct = float(np.mean(ranges)) if ranges else 0
                max_range_pct = float(np.max(ranges)) if ranges else 0

                # Volume acceleration
                vol_early = float(np.mean(bkt[0:3, iV]))
                vol_late = float(np.mean(bkt[4:7, iV]))
                vol_accel = float(vol_late / max(vol_early, 1))

                # Price distance from VWAP (absolute, for volatility proxy)
                vwap_dist_abs = abs(vwap_dev)

                # b0 candle character
                b0_range = float(bkt[0,iH] - bkt[0,iL])
                b0_range_pct = b0_range / entry * 100 if entry > 0 else 0

                # Gap fill ratio
                gap_amount = price * gap / 100
                gap_fill = float((bkt[0,iO] - entry) / max(gap_amount, 0.01)) if gap_amount > 0 else 0

                # ═══ OUTCOME (for analysis) ═══
                base_tp = 0.3285
                tp_hit = False; tp_bucket = 0
                for eb in range(7, min(77, nb)):
                    if bkt[eb,iL] > 0:
                        max_drop = (entry - bkt[eb,iL]) / entry * 100
                        if max_drop >= base_tp:
                            tp_hit = True; tp_bucket = eb; break

                exit_price = float(bkt[min(76, nb-1), iC])
                final_ret = (entry - exit_price) / entry * 100 - COST if exit_price > 0 else 0

                mfe = 0.0; mae = 0.0
                for eb in range(7, min(77, nb)):
                    if bkt[eb,iL] > 0:
                        fav = (entry - bkt[eb,iL]) / entry * 100
                        if fav > mfe: mfe = fav
                    if bkt[eb,iH] > 0:
                        adv = (bkt[eb,iH] - entry) / entry * 100
                        if adv > mae: mae = adv

                # Early buckets PnL (for early exit analysis)
                pnl_b10 = float((entry - bkt[min(10,nb-1),iC]) / entry * 100) if bkt[min(10,nb-1),iC] > 0 else 0
                pnl_b15 = float((entry - bkt[min(15,nb-1),iC]) / entry * 100) if bkt[min(15,nb-1),iC] > 0 else 0
                pnl_b20 = float((entry - bkt[min(20,nb-1),iC]) / entry * 100) if bkt[min(20,nb-1),iC] > 0 else 0
                pnl_b25 = float((entry - bkt[min(25,nb-1),iC]) / entry * 100) if bkt[min(25,nb-1),iC] > 0 else 0

                # Check if TP hit by certain buckets
                tp_by_b15 = False; tp_by_b25 = False; tp_by_b40 = False
                for eb in range(7, min(77, nb)):
                    if bkt[eb,iL] > 0:
                        md = (entry - bkt[eb,iL]) / entry * 100
                        if md >= base_tp:
                            if eb <= 15: tp_by_b15 = True
                            if eb <= 25: tp_by_b25 = True
                            if eb <= 40: tp_by_b40 = True
                            break

                rec = {
                    'sym': r['symbol'], 'date': r['date'], 'gap': gap, 'price': price,
                    'entry': entry, 'sp': sp, 'mom': mom, 'n_red': n_red, 'exhaust': exhaust,
                    'v2_score': v2_score, 'vwap_dev': vwap_dev, 'trap_magnitude': trap_magnitude,
                    'consec_red_end': consec_red_end,
                    'avg_range_pct': avg_range_pct, 'max_range_pct': max_range_pct,
                    'vol_accel': vol_accel, 'vwap_dist_abs': vwap_dist_abs,
                    'b0_range_pct': b0_range_pct, 'gap_fill': gap_fill,
                    'tp_hit': tp_hit, 'tp_bucket': tp_bucket,
                    'final_ret': final_ret, 'mfe': mfe, 'mae': mae,
                    'pnl_b10': pnl_b10, 'pnl_b15': pnl_b15, 'pnl_b20': pnl_b20, 'pnl_b25': pnl_b25,
                    'tp_by_b15': tp_by_b15, 'tp_by_b25': tp_by_b25, 'tp_by_b40': tp_by_b40,
                    'bkt': bkt, 'nb': nb,
                }
                by_date[r['date']].append(rec)
                loaded += 1
                if loaded % 50000 == 0:
                    print(f"  {loaded} loaded... {time.time()-t0:.0f}s")

    dates = sorted(by_date.keys())
    total = sum(len(v) for v in by_date.values())
    print(f"Total: {total} qualifying stocks, {len(dates)} days in {time.time()-t0:.0f}s")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("DEEP ANALYSIS ROUND 2: Explosion Guard + Scorer Refinement\n")
        out.write(f"Data: {total} stocks, {len(dates)} days\n\n")

        # ═══════════════════════════════════════
        # 1. EXPLOSION PREDICTION: what features predict MAE > 3%?
        # ═══════════════════════════════════════
        print("Section 1: Explosion prediction...")
        out.write("="*120+"\n")
        out.write("1. EXPLOSION PREDICTION: Can we predict MAE > 3% from b0-b6 features?\n")
        out.write("="*120+"\n\n")

        # Use gap_sp_mom scorer, top-8
        explosions = []; safe_trades = []
        for d in dates:
            pool = sorted(by_date[d], key=lambda x: -(x['gap'] * x['sp'] * max(abs(x['mom']),0.1) * 15))[:8]
            for r in pool:
                if r['mae'] > 3.0:
                    explosions.append(r)
                else:
                    safe_trades.append(r)

        out.write(f"  Explosions (MAE>3%): {len(explosions)} trades ({len(explosions)/(len(explosions)+len(safe_trades))*100:.1f}%)\n")
        out.write(f"  Safe trades: {len(safe_trades)} trades\n\n")

        out.write(f"  {'Feature':>20} {'Explosion':>10} {'Safe':>10} {'Delta':>10} {'Discriminant':>12}\n")
        out.write(f"  "+"-"*70+"\n")
        for feat in ['gap', 'sp', 'mom', 'n_red', 'exhaust', 'vwap_dev', 'trap_magnitude',
                     'consec_red_end', 'avg_range_pct', 'max_range_pct', 'vol_accel',
                     'vwap_dist_abs', 'b0_range_pct', 'gap_fill', 'price']:
            e_avg = np.mean([r[feat] for r in explosions])
            s_avg = np.mean([r[feat] for r in safe_trades])
            delta = e_avg - s_avg
            # Discriminant power (Cohen's d-like)
            e_std = np.std([r[feat] for r in explosions])
            s_std = np.std([r[feat] for r in safe_trades])
            pooled_std = np.sqrt((e_std**2 + s_std**2) / 2)
            cohens_d = abs(delta) / max(pooled_std, 0.001)
            out.write(f"  {feat:>20}  {e_avg:>9.3f}  {s_avg:>9.3f}  {delta:>+9.3f}  d={cohens_d:>6.3f}\n")

        # Best single-feature explosion detector
        out.write(f"\n  Single-feature explosion detectors (from selected top-8):\n\n")
        all_top8 = explosions + safe_trades
        detect_filters = [
            ('gap > 4%', lambda r: r['gap'] > 4.0),
            ('gap > 5%', lambda r: r['gap'] > 5.0),
            ('gap > 3%', lambda r: r['gap'] > 3.0),
            ('b0_range > 2%', lambda r: r['b0_range_pct'] > 2.0),
            ('b0_range > 1.5%', lambda r: r['b0_range_pct'] > 1.5),
            ('avg_range > 1%', lambda r: r['avg_range_pct'] > 1.0),
            ('avg_range > 0.8%', lambda r: r['avg_range_pct'] > 0.8),
            ('max_range > 2%', lambda r: r['max_range_pct'] > 2.0),
            ('price > 1000', lambda r: r['price'] > 1000),
            ('vwap_dist > 0.5', lambda r: r['vwap_dist_abs'] > 0.5),
            ('mom < -1.5%', lambda r: r['mom'] < -1.5),
            ('gap>3 + b0_range>1.5', lambda r: r['gap']>3 and r['b0_range_pct']>1.5),
            ('gap>3 + avg_range>0.8', lambda r: r['gap']>3 and r['avg_range_pct']>0.8),
            ('gap>4 + price>500', lambda r: r['gap']>4 and r['price']>500),
            ('gap>3 + vwap_dist>0.5', lambda r: r['gap']>3 and r['vwap_dist_abs']>0.5),
        ]

        out.write(f"  {'Detector':>30} {'Flagged':>7} {'Expl%':>6} {'FalsePos%':>9} {'Saved$':>8}\n")
        out.write(f"  "+"-"*65+"\n")
        for name, fn in detect_filters:
            flagged = [r for r in all_top8 if fn(r)]
            true_expl = [r for r in flagged if r['mae'] > 3.0]
            false_pos = [r for r in flagged if r['mae'] <= 3.0]
            if not flagged: continue
            expl_pct = len(true_expl)/len(flagged)*100
            fp_pct = len(false_pos)/len(flagged)*100
            # Savings: losses avoided by rejecting flagged trades
            avoided_loss = sum(r['final_ret'] for r in flagged if r['final_ret'] < 0)
            missed_win = sum(r['final_ret'] for r in flagged if r['final_ret'] > 0)
            net_saved = -avoided_loss - missed_win  # positive = net benefit
            out.write(f"  {name:>30}  {len(flagged):>5}  {expl_pct:>4.1f}%  {fp_pct:>7.1f}%  {net_saved:>+7.2f}%\n")

        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. REFINED SCORERS WITH EXPLOSION GUARD
        # ═══════════════════════════════════════
        print("Section 2: Refined scorers...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("2. REFINED SCORERS: gap_sp_mom + explosion guards\n")
        out.write("="*120+"\n\n")

        def compute_refined_scores(rec):
            g = rec['gap']; sp = rec['sp']; m = rec['mom']
            p = rec['price']; vw = rec['vwap_dev']
            trap = rec['trap_magnitude']; cre = rec['consec_red_end']
            nr = rec['n_red']; b0r = rec['b0_range_pct']
            avgr = rec['avg_range_pct']

            scores = {}
            # Base: gap_sp_mom (Round 1 winner)
            gsm = g * sp * max(abs(m), 0.1) * 15
            scores['gsm_base'] = min(gsm, 255)

            # GSM + gap cap: penalize gap > 3%
            gap_pen = 1.0 / (1.0 + max(g - 3.0, 0) * 0.3)
            scores['gsm_gapcap'] = min(gsm * gap_pen, 255)

            # GSM + volatility guard: penalize high b0 range
            vol_pen = 1.0 / (1.0 + max(b0r - 1.5, 0) * 0.5)
            scores['gsm_volguard'] = min(gsm * gap_pen * vol_pen, 255)

            # GSM + VWAP: bonus for below VWAP
            vwap_mult = 1.3 if vw < -0.3 else (1.0 if vw < 0 else 0.7)
            scores['gsm_vwap'] = min(gsm * vwap_mult, 255)

            # GSM + trap bonus
            trap_mult = 1.0 + min(trap, 2.0) * 0.2
            scores['gsm_trap'] = min(gsm * trap_mult, 255)

            # GSM + consec red
            cre_mult = 1.0 + cre * 0.1
            scores['gsm_cre'] = min(gsm * cre_mult, 255)

            # FULL: GSM + gapcap + vwap + trap + consec_red
            scores['gsm_full'] = min(gsm * gap_pen * vwap_mult * trap_mult * cre_mult * 0.5, 255)

            # GSM + price penalty (expensive stocks explode more)
            price_pen = 1.0 / (1.0 + max(p - 800, 0) / 1000)
            scores['gsm_price'] = min(gsm * gap_pen * price_pen, 255)

            # V3: completely new — sqrt(gap) * sp * |mom| * vwap * trap * n_red_bonus
            nr_mult = 0.8 + nr * 0.08
            scores['V3'] = min(np.sqrt(g) * sp * max(abs(m),0.1) * vwap_mult * trap_mult * nr_mult * 10, 255)

            # V3_safe: V3 + all guards
            scores['V3_safe'] = min(np.sqrt(g) * sp * max(abs(m),0.1) * vwap_mult * trap_mult * nr_mult * gap_pen * vol_pen * price_pen * 15, 255)

            # Trap-focused: trap * sp * consec_red (ignore gap entirely!)
            scores['trap_only'] = min(max(trap, 0.1) * sp * (1 + cre * 0.2) * 30, 255)

            # Low-gap sweet spot: bonus for gap 1-3%
            gap_bell = np.exp(-((g - 2.0) ** 2) / 2.0)  # peaks at 2%
            scores['gap_bell'] = min(gap_bell * sp * max(abs(m),0.1) * vwap_mult * trap_mult * 50, 255)

            return scores

        for d in dates:
            for r in by_date[d]:
                r['ref_scores'] = compute_refined_scores(r)

        scorer_keys = list(compute_refined_scores(by_date[dates[0]][0]).keys())

        out.write(f"  {'Scorer':>15} {'N':>3} {'Trades':>7} {'TP%':>6} {'Win%':>6} {'DayW':>6} {'ROC':>8} {'ExplosionN':>10} {'AvgMAE':>7}\n")
        out.write(f"  "+"-"*90+"\n")

        for sname in scorer_keys:
            for n in [5, 7, 8]:
                dpnls=[]; trades=0; tp_count=0; wins=0; expl_count=0; maes=[]
                for d in dates:
                    picks = sorted(by_date[d], key=lambda x: -x['ref_scores'].get(sname, 0))[:n]
                    dp = 0
                    for r in picks:
                        trades += 1; ret = r['final_ret']
                        pnl = BASE*MARGIN*ret/100; dp += pnl
                        if r['tp_hit']: tp_count += 1
                        if pnl > 0: wins += 1
                        if r['mae'] > 3.0: expl_count += 1
                        maes.append(r['mae'])
                    dpnls.append(dp)
                roc = sum(dpnls)/(BASE*n)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_count/max(trades,1)*100
                avg_mae = np.mean(maes)
                out.write(f"  {sname:>15}  {n:>2}  {trades:>6}  {tpr:>4.1f}%  {tw:>4.1f}%  {dw:>4.1f}%  {roc:>+7.1f}%  {expl_count:>9}  {avg_mae:>5.3f}%\n")
            out.write("\n")

        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. EARLY EXIT ANALYSIS: cut losers at b10/b15/b20
        # ═══════════════════════════════════════
        print("Section 3: Early exit analysis...")
        out.write("="*120+"\n")
        out.write("3. EARLY EXIT: what if we cut losing trades at b10/b15/b20?\n")
        out.write("="*120+"\n\n")
        out.write("  For each exit bucket, if P&L at that point < threshold, exit immediately.\n")
        out.write("  Uses gap_sp_mom scorer top-8.\n\n")

        for exit_check_b in [10, 12, 15, 18, 20, 25]:
            for threshold in [-0.1, -0.2, -0.3, -0.5, -0.7, -1.0]:
                dpnls=[]; trades=0; tp_count=0; wins=0; early_exits=0
                for d in dates:
                    picks = sorted(by_date[d], key=lambda x: -(x['gap']*x['sp']*max(abs(x['mom']),0.1)*15))[:8]
                    dp = 0
                    for r in picks:
                        trades += 1
                        pnl_key = f'pnl_b{exit_check_b}'
                        pnl_at_check = r.get(pnl_key, 0)

                        # Check TP before exit check
                        if r['tp_hit'] and r['tp_bucket'] <= exit_check_b:
                            ret = r['final_ret']  # already hit TP
                            tp_count += 1
                        elif pnl_at_check < threshold:
                            ret = pnl_at_check - COST  # early exit
                            early_exits += 1
                        else:
                            ret = r['final_ret']  # hold to end
                            if r['tp_hit']: tp_count += 1

                        pnl = BASE*MARGIN*ret/100; dp += pnl
                        if pnl > 0: wins += 1
                    dpnls.append(dp)
                roc = sum(dpnls)/(BASE*8)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_count/max(trades,1)*100
                if dw >= 55:  # only show promising
                    out.write(f"  check=b{exit_check_b:>2} thresh={threshold:>+5.1f}%: early={early_exits:>3} TP={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+7.1f}%\n")

        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. ADAPTIVE TP: different TP targets for different setups
        # ═══════════════════════════════════════
        print("Section 4: Adaptive TP...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("4. ADAPTIVE TP: should TP vary based on entry features?\n")
        out.write("="*120+"\n\n")

        # For each trade, test multiple TP levels and find optimal
        out.write("  MFE distribution by feature buckets (what's the max profit available?):\n\n")

        # By gap bucket
        for label, fn in [('gap<2%', lambda r: r['gap']<2), ('gap 2-3%', lambda r: 2<=r['gap']<3),
                          ('gap 3-5%', lambda r: 3<=r['gap']<5), ('gap>5%', lambda r: r['gap']>=5)]:
            all_trades = []
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -(x['gap']*x['sp']*max(abs(x['mom']),0.1)*15))[:8]
                all_trades.extend([r for r in picks if fn(r)])
            if len(all_trades) < 10: continue
            mfes = [r['mfe'] for r in all_trades]
            out.write(f"  {label:>12}: N={len(all_trades):>3} MFE: p25={np.percentile(mfes,25):.3f}% "
                      f"p50={np.percentile(mfes,50):.3f}% p75={np.percentile(mfes,75):.3f}% "
                      f"mean={np.mean(mfes):.3f}%\n")

        # By sp bucket
        out.write("\n")
        for label, fn in [('sp<0.5', lambda r: r['sp']<0.5), ('sp 0.5-0.6', lambda r: 0.5<=r['sp']<0.6),
                          ('sp 0.6-0.7', lambda r: 0.6<=r['sp']<0.7), ('sp>0.7', lambda r: r['sp']>=0.7)]:
            all_trades = []
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -(x['gap']*x['sp']*max(abs(x['mom']),0.1)*15))[:8]
                all_trades.extend([r for r in picks if fn(r)])
            if len(all_trades) < 10: continue
            mfes = [r['mfe'] for r in all_trades]
            out.write(f"  {label:>12}: N={len(all_trades):>3} MFE: p25={np.percentile(mfes,25):.3f}% "
                      f"p50={np.percentile(mfes,50):.3f}% p75={np.percentile(mfes,75):.3f}% "
                      f"mean={np.mean(mfes):.3f}%\n")

        # Simulate different TP levels
        out.write(f"\n  TP level sweep (gap_sp_mom top-8):\n\n")
        for tp in [0.20, 0.25, 0.30, 0.33, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]:
            dpnls=[]; trades=0; tp_count=0; wins=0
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -(x['gap']*x['sp']*max(abs(x['mom']),0.1)*15))[:8]
                dp = 0
                for r in picks:
                    trades += 1
                    bkt = r['bkt']; nb = r['nb']; entry = r['entry']
                    hit = False
                    for eb in range(7, min(77, nb)):
                        if bkt[eb,iL] > 0:
                            max_drop = (entry - bkt[eb,iL]) / entry * 100
                            if max_drop >= tp:
                                hit = True; break
                    if hit:
                        ret = tp - COST
                        tp_count += 1
                    else:
                        ret = r['final_ret']
                    pnl = BASE*MARGIN*ret/100; dp += pnl
                    if pnl > 0: wins += 1
                dpnls.append(dp)
            roc = sum(dpnls)/(BASE*8)*100
            dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw = wins/max(trades,1)*100
            tpr = tp_count/max(trades,1)*100
            out.write(f"  TP={tp:.2f}%: hitRate={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+7.1f}%\n")

        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. DYNAMIC POSITION COUNT: use fewer positions on dangerous days
        # ═══════════════════════════════════════
        print("Section 5: Dynamic position count...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("5. DYNAMIC POSITION COUNT: adapt N based on day quality\n")
        out.write("="*120+"\n\n")

        # Day quality = average score of top-N pool. High quality = confident = more positions
        for base_n, min_n in [(8, 3), (8, 5), (7, 3), (7, 5), (10, 5)]:
            for quality_metric in ['avg_score', 'avg_sp', 'min_sp']:
                dpnls=[]; trades=0; tp_count=0; wins=0
                for d in dates:
                    pool = sorted(by_date[d], key=lambda x: -(x['gap']*x['sp']*max(abs(x['mom']),0.1)*15))[:30]
                    top_n_pool = pool[:base_n]

                    if quality_metric == 'avg_score':
                        q = np.mean([r['gap']*r['sp']*max(abs(r['mom']),0.1)*15 for r in top_n_pool])
                        # High avg score = more positions, low = fewer
                        if q > 30: n = base_n
                        elif q > 20: n = max(base_n - 1, min_n)
                        elif q > 15: n = max(base_n - 2, min_n)
                        else: n = min_n
                    elif quality_metric == 'avg_sp':
                        q = np.mean([r['sp'] for r in top_n_pool])
                        if q > 0.65: n = base_n
                        elif q > 0.55: n = max(base_n - 1, min_n)
                        else: n = min_n
                    elif quality_metric == 'min_sp':
                        q = min(r['sp'] for r in top_n_pool)
                        if q > 0.50: n = base_n
                        elif q > 0.40: n = max(base_n - 2, min_n)
                        else: n = min_n

                    picks = pool[:n]
                    dp = 0
                    for r in picks:
                        trades += 1; ret = r['final_ret']
                        pnl = BASE*MARGIN*ret/100; dp += pnl
                        if r['tp_hit']: tp_count += 1
                        if pnl > 0: wins += 1
                    dpnls.append(dp)
                roc = sum(dpnls)/(BASE*base_n)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_count/max(trades,1)*100
                out.write(f"  base={base_n} min={min_n} metric={quality_metric:>10}: trades={trades:>4} TP={tpr:>4.1f}% win={tw:>4.1f}% dayW={dw:>4.1f}% ROC={roc:>+7.1f}%\n")

        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. THE PERFECT CONFIG: combine everything
        # ═══════════════════════════════════════
        print("Section 6: Perfect config search...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("6. PERFECT CONFIG: best scorer + filter + N + TP + early exit\n")
        out.write("="*120+"\n\n")

        configs = []
        for sname in ['gsm_base', 'gsm_gapcap', 'gsm_vwap', 'gsm_full', 'V3', 'V3_safe', 'gap_bell']:
            for reject_fn_name, reject_fn in [
                ('none', lambda r: True),
                ('gap<=5', lambda r: r['gap']<=5),
                ('gap<=4', lambda r: r['gap']<=4),
                ('b0r<2', lambda r: r['b0_range_pct']<2),
                ('gap<=4+b0r<2', lambda r: r['gap']<=4 and r['b0_range_pct']<2),
            ]:
                for n in [5, 7, 8]:
                    for tp in [0.30, 0.33, 0.40, 0.50]:
                        dpnls=[]; trades=0; tp_count=0; wins=0; expl=0
                        for d in dates:
                            filtered = [r for r in by_date[d] if reject_fn(r)]
                            picks = sorted(filtered, key=lambda x: -x['ref_scores'].get(sname, 0))[:n]
                            dp = 0
                            for r in picks:
                                trades += 1
                                bkt = r['bkt']; nb = r['nb']; entry = r['entry']
                                hit = False
                                for eb in range(7, min(77, nb)):
                                    if bkt[eb,iL] > 0:
                                        md = (entry - bkt[eb,iL]) / entry * 100
                                        if md >= tp: hit = True; break
                                if hit:
                                    ret = tp - COST; tp_count += 1
                                else:
                                    ret = r['final_ret']
                                pnl = BASE*MARGIN*ret/100; dp += pnl
                                if pnl > 0: wins += 1
                                if r['mae'] > 3.0: expl += 1
                            dpnls.append(dp)
                        if trades < 50: continue
                        roc = sum(dpnls)/(BASE*n)*100
                        dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                        tw = wins/max(trades,1)*100
                        tpr = tp_count/max(trades,1)*100
                        configs.append((roc, dw, tpr, tw, trades, expl, sname, reject_fn_name, n, tp))

        configs.sort(key=lambda x: (-x[1], -x[0]))
        out.write(f"  Top-50 configs (sorted by dayWin, then ROC):\n\n")
        out.write(f"  {'Scorer':>12} {'Filter':>12} {'N':>2} {'TP':>5} {'ROC':>8} {'DayW':>6} {'TP%':>6} {'TrdW':>6} {'Expl':>5} {'Trades':>6}\n")
        out.write(f"  "+"-"*95+"\n")
        for roc, dw, tpr, tw, trades, expl, sname, fname, n, tp in configs[:50]:
            out.write(f"  {sname:>12} {fname:>12}  {n:>1}  {tp:>4.2f}  {roc:>+7.1f}%  {dw:>4.1f}%  {tpr:>4.1f}%  {tw:>4.1f}%  {expl:>4}  {trades:>5}\n")

        # ═══════════════════════════════════════
        # 7. STABILITY TEST: does the best config work in ALL months?
        # ═══════════════════════════════════════
        print("Section 7: Stability test...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("7. STABILITY TEST: per-month performance of top-5 configs\n")
        out.write("="*120+"\n\n")

        # Take top-5 configs and show per-month breakdown
        for idx, (roc, dw, tpr, tw, trades, expl, sname, fname, n, tp) in enumerate(configs[:5]):
            out.write(f"  Config #{idx+1}: {sname}+{fname} top-{n} TP={tp:.2f}% (overall ROC={roc:+.1f}%, dayW={dw:.1f}%)\n")

            # Get the reject function
            reject_fns = {'none': lambda r: True, 'gap<=5': lambda r: r['gap']<=5,
                         'gap<=4': lambda r: r['gap']<=4, 'b0r<2': lambda r: r['b0_range_pct']<2,
                         'gap<=4+b0r<2': lambda r: r['gap']<=4 and r['b0_range_pct']<2}
            reject_fn = reject_fns[fname]

            months = defaultdict(lambda: {'pnls': [], 'trades': 0, 'wins': 0, 'tp': 0})
            for d in dates:
                month = d[:7]  # YYYY-MM
                filtered = [r for r in by_date[d] if reject_fn(r)]
                picks = sorted(filtered, key=lambda x: -x['ref_scores'].get(sname, 0))[:n]
                dp = 0
                for r in picks:
                    months[month]['trades'] += 1
                    bkt = r['bkt']; nb = r['nb']; entry = r['entry']
                    hit = False
                    for eb in range(7, min(77, nb)):
                        if bkt[eb,iL] > 0:
                            md = (entry - bkt[eb,iL]) / entry * 100
                            if md >= tp: hit = True; break
                    if hit:
                        ret = tp - COST; months[month]['tp'] += 1
                    else:
                        ret = r['final_ret']
                    pnl = BASE*MARGIN*ret/100; dp += pnl
                    if pnl > 0: months[month]['wins'] += 1
                months[month]['pnls'].append(dp)

            out.write(f"    {'Month':>8} {'Days':>5} {'Trades':>7} {'TP%':>6} {'Win%':>6} {'DayW':>6} {'ROC':>8}\n")
            out.write(f"    "+"-"*55+"\n")
            for month in sorted(months.keys()):
                m = months[month]
                pnls = m['pnls']
                roc_m = sum(pnls)/(BASE*n)*100
                dw_m = sum(1 for p in pnls if p>0)/len(pnls)*100 if pnls else 0
                tw_m = m['wins']/max(m['trades'],1)*100
                tpr_m = m['tp']/max(m['trades'],1)*100
                out.write(f"    {month:>8}  {len(pnls):>4}  {m['trades']:>6}  {tpr_m:>4.1f}%  {tw_m:>4.1f}%  {dw_m:>4.1f}%  {roc_m:>+7.1f}%\n")
            out.write("\n")

        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. IMPLEMENTATION BLUEPRINT
        # ═══════════════════════════════════════
        out.write("="*120+"\n")
        out.write("8. IMPLEMENTATION BLUEPRINT: what to change in backtest.rs / smart_score.rs\n")
        out.write("="*120+"\n\n")
        out.write("  Based on Round 2 analysis, here's what to implement:\n\n")
        if configs:
            best = configs[0]
            out.write(f"  BEST CONFIG: {best[6]}+{best[7]} top-{best[8]} TP={best[9]:.2f}%\n")
            out.write(f"  ROC={best[0]:+.1f}% dayWin={best[1]:.1f}% TP%={best[2]:.1f}% tradeWin={best[3]:.1f}%\n\n")
            out.write(f"  In smart_score.rs:\n")
            out.write(f"    1. Add compute_{best[6]}_score() function\n")
            out.write(f"    2. Formula: see scorer definition above\n")
            out.write(f"    3. Add {best[7]} reject filter\n\n")
            out.write(f"  In backtest.rs:\n")
            out.write(f"    1. Use new scorer in gap_reversal_candidate()\n")
            out.write(f"    2. Set sell_max_positions = {best[8]}\n")
            out.write(f"    3. Set sell_tp_pct = {best[9]:.2f}\n\n")

    print(f"\nRound 2 complete in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
