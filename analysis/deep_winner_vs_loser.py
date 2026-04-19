"""
DEEP ANALYSIS ROUND 1: Why do selected top-8 picks fail while better stocks sit in rank 9-30?
===============================================================================================
Key finding from V2 scorer: Rank 1-2 underperform (53.9%, 48.7% win%) while rank 7-8 crush (55.3%, 64.5%).
This means the SCORING FORMULA is wrong — it over-ranks certain stocks.

This script does:
  1. For each day, rank top-30 by V2 score
  2. Compare SELECTED (top-8) vs MISSED WINNERS (rank 9-30 that hit TP)
  3. Find which features the missed winners had that selected losers didn't
  4. Build a "regret" metric: how much money was left on the table
  5. Test if reranking by different features would have captured the missed winners
  6. Deep candle-by-candle analysis of WHY losers fail (reversal? no follow-through?)

NO FUTURE LOOKAHEAD: all features computed from b0-b6 only (before entry at b6 open).
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_winner_vs_loser.txt'
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

                # ═══ ALL FEATURES (no lookahead — b0 to b6 only) ═══

                # V2 features
                hl = bkt[2:7, iH] - bkt[2:7, iL]
                valid = hl > 0
                cp = np.where(valid, (bkt[2:7,iC]-bkt[2:7,iL])/np.maximum(hl,0.001), 0.5)
                sp = float(1.0 - np.mean(cp))
                mom = float((bkt[6,iC] - bkt[2,iO]) / bkt[2,iO] * 100) if bkt[2,iO] > 0 else 0
                n_red = int(np.sum(bkt[2:7,iC] < bkt[2:7,iO]))
                exhaust_range = bkt[2,iH] - bkt[2,iL]
                exhaust = float((bkt[2,iH] - bkt[2,iC]) / exhaust_range) if exhaust_range > 0 else 0.5

                # V2 reject
                if sp < 0.45 and n_red <= 1: continue

                # V2 score
                mom_mult = 1.4 if mom < -0.5 else (1.1 if mom < 0 else 0.7)
                v2_score = gap * sp * mom_mult * 15.0
                v2_score = min(v2_score, 255)

                # ─── ADDITIONAL FEATURES (all from b0-b6, NO lookahead) ───

                # 1. Volume features
                vol_5 = float(np.mean(bkt[2:7, iV]))
                vol_ratio_last = float(bkt[6,iV] / max(vol_5, 1))
                vol_b0 = float(bkt[0,iV])
                vol_decay = float(bkt[6,iV] / max(vol_b0, 1))  # volume decaying = less interest

                # 2. VWAP position
                vwap_dev = float((bkt[6,iC] - bkt[6,iVW]) / bkt[6,iVW] * 100) if bkt[6,iVW] > 0 else 0
                vwap_b3 = float((bkt[3,iC] - bkt[3,iVW]) / bkt[3,iVW] * 100) if bkt[3,iVW] > 0 else 0
                vwap_slope = vwap_dev - vwap_b3  # getting further below VWAP = good for SELL

                # 3. Body ratios
                bodies = []
                for bi in range(2, 7):
                    br = bkt[bi,iH] - bkt[bi,iL]
                    if br > 0:
                        bodies.append(abs(bkt[bi,iC] - bkt[bi,iO]) / br)
                    else:
                        bodies.append(0.5)
                avg_body = float(np.mean(bodies))

                # 4. Price relative to day range
                day_high_6 = float(np.max(bkt[:7,iH]))
                day_low_6 = float(np.min(bkt[:7,iL][bkt[:7,iL] > 0])) if np.any(bkt[:7,iL] > 0) else entry
                day_range_6 = day_high_6 - day_low_6
                dist_from_high = (day_high_6 - entry) / entry * 100 if entry > 0 else 0
                range_position = (entry - day_low_6) / max(day_range_6, 0.01)  # 0=at low, 1=at high

                # 5. Candle patterns
                # "Gravestone" pattern at b0-b1: big upper wick = sellers present
                b0_upper_wick = float((bkt[0,iH] - max(bkt[0,iO], bkt[0,iC])) / max(bkt[0,iH] - bkt[0,iL], 0.01))
                b0_lower_wick = float((min(bkt[0,iO], bkt[0,iC]) - bkt[0,iL]) / max(bkt[0,iH] - bkt[0,iL], 0.01))

                # 6. Consecutive pattern: how many consecutive red candles at end
                consec_red_end = 0
                for bi in range(6, 1, -1):
                    if bkt[bi,iC] < bkt[bi,iO]:
                        consec_red_end += 1
                    else:
                        break

                # 7. Gap fill progress: how much of gap has been filled by b6?
                gap_amount = price * gap / 100
                gap_fill = float((bkt[0,iO] - entry) / max(gap_amount, 0.01)) if gap_amount > 0 else 0
                # gap_fill > 1 means price dropped BELOW previous close (overshoot)

                # 8. Volume-price divergence: price going up but volume declining = weak
                price_trend = float(bkt[6,iC] - bkt[2,iO])
                vol_trend = float(bkt[6,iV] - bkt[2,iV])
                # For SELL: we want price going DOWN with HIGH volume (strong selling)
                vol_price_align = 1 if (price_trend < 0 and vol_trend > 0) else 0

                # 9. Intraday range expansion
                range_b2 = float(bkt[2,iH] - bkt[2,iL])
                range_b6 = float(bkt[6,iH] - bkt[6,iL])
                range_expansion = float(range_b6 / max(range_b2, 0.01))

                # 10. "Trap" detection: price initially rises (bull trap) then starts falling
                early_high = float(np.max(bkt[0:3, iH]))
                late_price = float(bkt[6,iC])
                trap_magnitude = (early_high - late_price) / entry * 100 if entry > 0 else 0

                # ─── OUTCOME (for analysis — NOT used in scoring) ───
                base_tp = 0.3285
                tp_hit = False; tp_bucket = 0
                for eb in range(7, min(77, nb)):
                    if bkt[eb,iL] > 0:
                        max_drop = (entry - bkt[eb,iL]) / entry * 100
                        if max_drop >= base_tp:
                            tp_hit = True; tp_bucket = eb; break

                # Final return at b76 (SELL direction)
                exit_price = float(bkt[min(76, nb-1), iC])
                final_ret = (entry - exit_price) / entry * 100 - COST if exit_price > 0 else 0

                # MFE/MAE (max favorable/adverse excursion)
                mfe = 0.0; mae = 0.0
                for eb in range(7, min(77, nb)):
                    if bkt[eb,iL] > 0:
                        fav = (entry - bkt[eb,iL]) / entry * 100
                        if fav > mfe: mfe = fav
                    if bkt[eb,iH] > 0:
                        adv = (bkt[eb,iH] - entry) / entry * 100
                        if adv > mae: mae = adv

                rec = {
                    'sym': r['symbol'], 'date': r['date'], 'gap': gap, 'price': price,
                    'entry': entry, 'sp': sp, 'mom': mom, 'n_red': n_red, 'exhaust': exhaust,
                    'v2_score': v2_score,
                    # New features
                    'vol_ratio_last': vol_ratio_last, 'vol_decay': vol_decay,
                    'vwap_dev': vwap_dev, 'vwap_slope': vwap_slope,
                    'avg_body': avg_body, 'dist_from_high': dist_from_high,
                    'range_position': range_position, 'b0_upper_wick': b0_upper_wick,
                    'consec_red_end': consec_red_end, 'gap_fill': gap_fill,
                    'vol_price_align': vol_price_align, 'range_expansion': range_expansion,
                    'trap_magnitude': trap_magnitude,
                    # Outcome
                    'tp_hit': tp_hit, 'tp_bucket': tp_bucket,
                    'final_ret': final_ret, 'mfe': mfe, 'mae': mae,
                }
                by_date[r['date']].append(rec)
                loaded += 1
                if loaded % 50000 == 0:
                    print(f"  {loaded} loaded... {time.time()-t0:.0f}s")

    dates = sorted(by_date.keys())
    total = sum(len(v) for v in by_date.values())
    print(f"Total: {total} qualifying stocks, {len(dates)} days in {time.time()-t0:.0f}s")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("DEEP WINNER vs LOSER ANALYSIS — Round 1\n")
        out.write(f"Why do top-ranked picks fail while better stocks sit at rank 9-30?\n")
        out.write(f"Data: {total} stocks, {len(dates)} days\n\n")

        # ═══════════════════════════════════════
        # 1. DAY-BY-DAY: SELECTED LOSERS vs MISSED WINNERS
        # ═══════════════════════════════════════
        print("Section 1: Selected losers vs missed winners...")
        out.write("="*120+"\n")
        out.write("1. DAY-BY-DAY: Who did we SELECT that LOST, and who did we MISS that WON?\n")
        out.write("="*120+"\n\n")

        all_selected = []; all_missed_winners = []; all_selected_losers = []
        regret_total = 0.0

        for d in dates:
            pool = sorted(by_date[d], key=lambda x: -x['v2_score'])
            selected = pool[:8]
            rest = pool[8:30]  # rank 9-30

            # Selected losers: picked but lost money
            sel_losers = [r for r in selected if r['final_ret'] < -0.5]
            # Missed winners: NOT selected but would have hit TP
            missed_win = [r for r in rest if r['tp_hit'] and r['final_ret'] > 0.1]

            all_selected.extend(selected)
            all_selected_losers.extend(sel_losers)
            all_missed_winners.extend(missed_win)

            # Calculate regret: PnL we got from losers vs PnL we missed from winners
            if sel_losers and missed_win:
                loser_pnl = sum(r['final_ret'] for r in sel_losers)
                winner_pnl = sum(r['final_ret'] for r in missed_win[:len(sel_losers)])  # swap same count
                regret = winner_pnl - loser_pnl
                regret_total += regret

                if len(sel_losers) >= 2:
                    out.write(f"  {d}: {len(sel_losers)} losers selected, {len(missed_win)} winners missed\n")
                    out.write(f"    SELECTED LOSERS:\n")
                    for r in sorted(sel_losers, key=lambda x: x['final_ret'])[:3]:
                        out.write(f"      {r['sym']:>15} rank={pool.index(r)+1:>2} score={r['v2_score']:>5.1f} "
                                  f"gap={r['gap']:.1f}% sp={r['sp']:.2f} mom={r['mom']:+.2f}% "
                                  f"vwap={r['vwap_dev']:+.2f}% trap={r['trap_magnitude']:.2f}% "
                                  f"ret={r['final_ret']:+.3f}% mfe={r['mfe']:.3f}% mae={r['mae']:.3f}%\n")
                    out.write(f"    MISSED WINNERS (rank 9-30):\n")
                    for r in sorted(missed_win, key=lambda x: -x['final_ret'])[:3]:
                        out.write(f"      {r['sym']:>15} rank={pool.index(r)+1:>2} score={r['v2_score']:>5.1f} "
                                  f"gap={r['gap']:.1f}% sp={r['sp']:.2f} mom={r['mom']:+.2f}% "
                                  f"vwap={r['vwap_dev']:+.2f}% trap={r['trap_magnitude']:.2f}% "
                                  f"ret={r['final_ret']:+.3f}% mfe={r['mfe']:.3f}%\n")
                    out.write(f"    REGRET: {regret:+.2f}% (if we had swapped)\n\n")

        out.write(f"\n  TOTAL REGRET: {regret_total:+.1f}% across all days\n")
        out.write(f"  Selected losers: {len(all_selected_losers)} / {len(all_selected)} ({len(all_selected_losers)/max(len(all_selected),1)*100:.1f}%)\n")
        out.write(f"  Missed winners: {len(all_missed_winners)} in rank 9-30\n\n")
        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. FEATURE COMPARISON: Selected Losers vs Missed Winners
        # ═══════════════════════════════════════
        print("Section 2: Feature comparison...")
        out.write("="*120+"\n")
        out.write("2. FEATURE FINGERPRINT: What's different between selected-losers and missed-winners?\n")
        out.write("="*120+"\n\n")

        features_to_compare = [
            'gap', 'sp', 'mom', 'n_red', 'exhaust', 'v2_score',
            'vol_ratio_last', 'vol_decay', 'vwap_dev', 'vwap_slope',
            'avg_body', 'dist_from_high', 'range_position',
            'b0_upper_wick', 'consec_red_end', 'gap_fill',
            'vol_price_align', 'range_expansion', 'trap_magnitude', 'price'
        ]

        out.write(f"  {'Feature':>22} {'SelLosers':>10} {'MissWin':>10} {'SelWin':>10} {'Delta(MW-SL)':>12} {'Signal':>8}\n")
        out.write(f"  "+"-"*80+"\n")

        sel_winners = [r for r in all_selected if r['final_ret'] > 0.1]
        for feat in features_to_compare:
            sl_avg = np.mean([r[feat] for r in all_selected_losers]) if all_selected_losers else 0
            mw_avg = np.mean([r[feat] for r in all_missed_winners]) if all_missed_winners else 0
            sw_avg = np.mean([r[feat] for r in sel_winners]) if sel_winners else 0
            delta = mw_avg - sl_avg
            # Signal: is this feature useful for distinguishing?
            signal = "STRONG" if abs(delta) > 0.1 * max(abs(sl_avg), 0.01) else "weak"
            if feat in ['n_red', 'consec_red_end', 'vol_price_align']:
                signal = "STRONG" if abs(delta) > 0.2 else "weak"
            out.write(f"  {feat:>22}  {sl_avg:>9.3f}  {mw_avg:>9.3f}  {sw_avg:>9.3f}  {delta:>+11.3f}  {signal:>8}\n")

        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. WHY DO HIGH-SCORE STOCKS FAIL? (The over-ranking problem)
        # ═══════════════════════════════════════
        print("Section 3: Why high-score stocks fail...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("3. WHY DO HIGH-SCORE (rank 1-3) STOCKS FAIL?\n")
        out.write("="*120+"\n\n")
        out.write("  V2 score = gap * sp * mom_mult * 15. High score needs: big gap + high sp + negative momentum.\n")
        out.write("  But big gap doesn't mean price will continue falling — it might mean panic selloff already done.\n\n")

        # Analyze rank 1-3 losers
        rank1_3_losers = []
        rank1_3_winners = []
        for d in dates:
            pool = sorted(by_date[d], key=lambda x: -x['v2_score'])
            for i, r in enumerate(pool[:3]):
                if r['final_ret'] < -0.5:
                    rank1_3_losers.append(r)
                else:
                    rank1_3_winners.append(r)

        out.write(f"  Rank 1-3: {len(rank1_3_losers)} losers vs {len(rank1_3_winners)} winners\n\n")

        out.write(f"  {'Feature':>22} {'R1-3 Losers':>12} {'R1-3 Winners':>12} {'Delta':>10}\n")
        out.write(f"  "+"-"*60+"\n")
        for feat in features_to_compare:
            l_avg = np.mean([r[feat] for r in rank1_3_losers]) if rank1_3_losers else 0
            w_avg = np.mean([r[feat] for r in rank1_3_winners]) if rank1_3_winners else 0
            out.write(f"  {feat:>22}  {l_avg:>11.3f}  {w_avg:>11.3f}  {w_avg-l_avg:>+9.3f}\n")

        # Find the strongest discriminator
        out.write(f"\n  KEY INSIGHT: What makes rank 1-3 losers lose?\n")
        out.write(f"  → They have HUGE gaps ({np.mean([r['gap'] for r in rank1_3_losers]):.1f}%) which inflates their V2 score\n")
        out.write(f"  → But huge gaps mean the stock is in play — volatile, unpredictable\n")
        out.write(f"  → MFE of losers: {np.mean([r['mfe'] for r in rank1_3_losers]):.3f}% — they DO dip, but then reverse HARD\n")
        out.write(f"  → MAE of losers: {np.mean([r['mae'] for r in rank1_3_losers]):.3f}% — massive adverse move kills them\n")

        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. ALTERNATIVE SCORERS: Can we beat V2 by penalizing dangerous features?
        # ═══════════════════════════════════════
        print("Section 4: Alternative scorers...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("4. ALTERNATIVE SCORERS: penalize dangerous features, reward safety\n")
        out.write("="*120+"\n\n")

        def compute_all_alt_scores(rec):
            g = rec['gap']; sp = rec['sp']; m = rec['mom']
            nr = rec['n_red']; ex = rec['exhaust']; p = rec['price']
            vw = rec['vwap_dev']; vs = rec['vwap_slope']
            gf = rec['gap_fill']; tm = rec['trap_magnitude']
            vpa = rec['vol_price_align']; cre = rec['consec_red_end']
            rp = rec['range_position']; re = rec['range_expansion']

            mm = 1.4 if m < -0.5 else (1.1 if m < 0 else 0.7)

            scores = {}
            # Current V2
            scores['V2'] = g * sp * mm * 15

            # S1: Cap gap influence (sqrt instead of linear)
            scores['sqrt_gap'] = np.sqrt(g) * sp * mm * 20

            # S2: VWAP-weighted (below VWAP = bonus)
            vwap_mult = 1.4 if vw < -0.3 else (1.1 if vw < 0 else 0.7)
            scores['vwap_weighted'] = g * sp * mm * vwap_mult * 10

            # S3: Trap bonus (bigger trap = more exhausted bulls)
            trap_mult = 1.0 + min(tm, 2.0) * 0.3
            scores['trap_bonus'] = g * sp * mm * trap_mult * 12

            # S4: Consecutive red at end (strong selling continuation)
            cre_mult = 1.0 + cre * 0.15
            scores['consec_red'] = g * sp * mm * cre_mult * 12

            # S5: Volume-price alignment bonus
            vpa_mult = 1.3 if vpa else 0.9
            scores['vol_align'] = g * sp * mm * vpa_mult * 12

            # S6: Gap fill progress (more gap filled = closer to target)
            gf_mult = 1.0 + min(max(gf, 0), 1.5) * 0.3
            scores['gap_fill_bonus'] = g * sp * mm * gf_mult * 12

            # S7: Penalize high gap (danger zone)
            gap_penalty = 1.0 / (1.0 + max(g - 3.0, 0) * 0.2)  # penalize gap > 3%
            scores['gap_capped'] = g * sp * mm * gap_penalty * 15

            # S8: Range position (entry near day high = bad, near low = good for SELL bounce down)
            # Actually for SELL: near HIGH is good (more room to fall)
            rp_mult = 0.7 + rp * 0.6  # rp=1(at high)=1.3x, rp=0(at low)=0.7x
            scores['range_pos'] = g * sp * mm * rp_mult * 12

            # S9: MEGA — combine best features
            scores['mega'] = (np.sqrt(g) * sp * mm * vwap_mult * trap_mult *
                             cre_mult * gap_penalty * 8)

            # S10: n_red weighted (more red = stronger selling)
            nr_mult = 0.7 + nr * 0.12
            scores['nred_heavy'] = g * sp * mm * nr_mult * 12

            # S11: sqrt(gap) + vwap + consec_red + gap_penalty
            scores['kitchen_sink'] = (np.sqrt(g) * sp * mm * vwap_mult *
                                      cre_mult * gap_penalty * 10)

            # S12: Price-adjusted (cheap stocks more volatile but more TP hits)
            price_mult = 1.2 if p < 300 else (1.0 if p < 800 else 0.8)
            scores['price_adj'] = g * sp * mm * price_mult * 12

            # S13: gap*sp*|mom| (from V2 scorer — best performer!)
            scores['gap_sp_mom'] = g * sp * max(abs(m), 0.1) * 15

            # S14: gap_sp_mom + vwap + gap_penalty
            scores['gsmv'] = g * sp * max(abs(m), 0.1) * vwap_mult * gap_penalty * 10

            # S15: gap_sp_mom + trap + consec_red
            scores['gsm_tc'] = g * sp * max(abs(m), 0.1) * trap_mult * cre_mult * 8

            # S16: Hybrid — use different formula for different gap ranges
            if g < 2:
                scores['hybrid'] = g * sp * mm * 20  # small gap: trust V2
            elif g < 4:
                scores['hybrid'] = np.sqrt(g) * sp * max(abs(m), 0.1) * vwap_mult * 12  # medium: add features
            else:
                scores['hybrid'] = np.sqrt(g) * sp * max(abs(m), 0.1) * vwap_mult * gap_penalty * trap_mult * 8  # big: heavy filtering

            for k in scores:
                scores[k] = min(scores[k], 255)
            return scores

        # Compute scores for all records
        for d in dates:
            for r in by_date[d]:
                r['alt_scores'] = compute_all_alt_scores(r)

        # Evaluate each scorer
        scorer_names = ['V2', 'sqrt_gap', 'vwap_weighted', 'trap_bonus', 'consec_red',
                       'vol_align', 'gap_fill_bonus', 'gap_capped', 'range_pos', 'mega',
                       'nred_heavy', 'kitchen_sink', 'price_adj', 'gap_sp_mom', 'gsmv',
                       'gsm_tc', 'hybrid']

        out.write(f"  Testing top-8 selection with each scorer:\n\n")
        out.write(f"  {'Scorer':>18} {'Trades':>7} {'TP%':>6} {'Win%':>6} {'DayW':>6} {'AvgRet':>8} {'ROC':>8} {'MaxDD':>7}\n")
        out.write(f"  "+"-"*75+"\n")

        best_roc = -999; best_scorer = ""
        for sname in scorer_names:
            dpnls=[]; trades=0; tp_count=0; wins=0
            for d in dates:
                picks = sorted(by_date[d], key=lambda x: -x['alt_scores'].get(sname, 0))[:8]
                dp = 0
                for r in picks:
                    trades += 1
                    ret = r['final_ret']
                    pnl = BASE * MARGIN * ret / 100
                    dp += pnl
                    if r['tp_hit']: tp_count += 1
                    if pnl > 0: wins += 1
                dpnls.append(dp)
            roc = sum(dpnls)/(BASE*8)*100
            dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw = wins/max(trades,1)*100
            tpr = tp_count/max(trades,1)*100
            avg_ret = sum(dpnls)/max(trades,1)/(BASE*MARGIN)*10000
            # Max drawdown
            cum = np.cumsum(dpnls)
            peak = np.maximum.accumulate(cum)
            dd = (peak - cum)
            maxdd = float(np.max(dd))/(BASE*8)*100 if len(dd) > 0 else 0

            if roc > best_roc: best_roc = roc; best_scorer = sname
            out.write(f"  {sname:>18}  {trades:>6}  {tpr:>4.1f}%  {tw:>4.1f}%  {dw:>4.1f}%  {avg_ret:>+6.3f}%  {roc:>+7.1f}%  {maxdd:>5.1f}%\n")

        out.write(f"\n  *** BEST SCORER: {best_scorer} with ROC={best_roc:+.1f}% ***\n")
        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. TOP-N SWEEP for best scorer
        # ═══════════════════════════════════════
        print("Section 5: Top-N sweep for best scorers...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("5. TOP-N SWEEP: optimal number of positions for top scorers\n")
        out.write("="*120+"\n\n")

        top_scorers = ['V2', best_scorer, 'gap_sp_mom', 'hybrid', 'gsmv', 'kitchen_sink']
        for sname in top_scorers:
            out.write(f"  {sname}:\n")
            for n in [3, 5, 6, 7, 8, 10, 12]:
                dpnls=[]; trades=0; tp_count=0; wins=0
                for d in dates:
                    picks = sorted(by_date[d], key=lambda x: -x['alt_scores'].get(sname, 0))[:n]
                    dp = 0
                    for r in picks:
                        trades += 1; ret = r['final_ret']
                        pnl = BASE*MARGIN*ret/100; dp += pnl
                        if r['tp_hit']: tp_count += 1
                        if pnl > 0: wins += 1
                    dpnls.append(dp)
                roc = sum(dpnls)/(BASE*n)*100
                dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                tw = wins/max(trades,1)*100
                tpr = tp_count/max(trades,1)*100
                out.write(f"    top-{n:>2}: ROC={roc:>+7.1f}% TP={tpr:>4.1f}% dayW={dw:>4.1f}% trdW={tw:>4.1f}%\n")
            out.write("\n")
        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. FAILURE MODE TAXONOMY
        # ═══════════════════════════════════════
        print("Section 6: Failure mode taxonomy...")
        out.write("="*120+"\n")
        out.write("6. FAILURE MODE TAXONOMY: Why exactly do trades lose?\n")
        out.write("="*120+"\n\n")

        # Categorize ALL selected losers (top-8 V2)
        failure_modes = defaultdict(list)
        for d in dates:
            pool = sorted(by_date[d], key=lambda x: -x['v2_score'])[:8]
            for r in pool:
                if r['final_ret'] >= 0: continue

                # Classify failure mode
                if r['mae'] > 3.0:
                    failure_modes['EXPLOSION (>3% adverse)'].append(r)
                elif r['mfe'] > r['mae'] and r['mfe'] > 0.3:
                    failure_modes['REVERSAL (was winning, then reversed)'].append(r)
                elif r['mfe'] < 0.15:
                    failure_modes['NEVER WORKED (MFE<0.15%, no profit ever)'].append(r)
                elif r['gap'] > 4.0:
                    failure_modes['BIG GAP TRAP (gap>4%, momentum too strong)'].append(r)
                else:
                    failure_modes['SLOW BLEED (gradual loss)'].append(r)

        for mode, recs in sorted(failure_modes.items(), key=lambda x: -len(x[1])):
            avg_loss = np.mean([r['final_ret'] for r in recs])
            avg_gap = np.mean([r['gap'] for r in recs])
            avg_sp = np.mean([r['sp'] for r in recs])
            avg_mom = np.mean([r['mom'] for r in recs])
            avg_mfe = np.mean([r['mfe'] for r in recs])
            avg_mae = np.mean([r['mae'] for r in recs])
            out.write(f"  {mode}: {len(recs)} trades, avg_loss={avg_loss:+.3f}%\n")
            out.write(f"    gap={avg_gap:.1f}% sp={avg_sp:.2f} mom={avg_mom:+.2f}% mfe={avg_mfe:.3f}% mae={avg_mae:.3f}%\n")
            # Show 3 worst examples
            for r in sorted(recs, key=lambda x: x['final_ret'])[:3]:
                out.write(f"    → {r['sym']:>15} {r['date']} gap={r['gap']:.1f}% ret={r['final_ret']:+.3f}% mfe={r['mfe']:.3f}% mae={r['mae']:.3f}%\n")
            out.write("\n")

        print(f"  Section 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. PREDICTIVE FILTER: can we REJECT dangerous trades?
        # ═══════════════════════════════════════
        print("Section 7: Predictive filters...")
        out.write("="*120+"\n")
        out.write("7. PREDICTIVE FILTERS: reject rules that avoid losses without killing winners\n")
        out.write("="*120+"\n\n")

        reject_filters = [
            ('no filter', lambda r: True),
            ('gap <= 5%', lambda r: r['gap'] <= 5.0),
            ('gap <= 4%', lambda r: r['gap'] <= 4.0),
            ('gap <= 3%', lambda r: r['gap'] <= 3.0),
            ('gap 1-4%', lambda r: 1.0 <= r['gap'] <= 4.0),
            ('gap 1-3%', lambda r: 1.0 <= r['gap'] <= 3.0),
            ('vwap < 0', lambda r: r['vwap_dev'] < 0),
            ('vwap < -0.2', lambda r: r['vwap_dev'] < -0.2),
            ('trap > 0.3', lambda r: r['trap_magnitude'] > 0.3),
            ('trap > 0.5', lambda r: r['trap_magnitude'] > 0.5),
            ('consec_red >= 2', lambda r: r['consec_red_end'] >= 2),
            ('consec_red >= 3', lambda r: r['consec_red_end'] >= 3),
            ('gap_fill > 0.3', lambda r: r['gap_fill'] > 0.3),
            ('gap_fill > 0.5', lambda r: r['gap_fill'] > 0.5),
            ('price < 800', lambda r: r['price'] < 800),
            ('sp > 0.55', lambda r: r['sp'] > 0.55),
            ('n_red >= 3', lambda r: r['n_red'] >= 3),
            ('n_red >= 4', lambda r: r['n_red'] >= 4),
            # Combos
            ('gap<=4 + vwap<0', lambda r: r['gap']<=4 and r['vwap_dev']<0),
            ('gap<=4 + trap>0.3', lambda r: r['gap']<=4 and r['trap_magnitude']>0.3),
            ('gap<=4 + n_red>=3', lambda r: r['gap']<=4 and r['n_red']>=3),
            ('gap<=4 + sp>0.55', lambda r: r['gap']<=4 and r['sp']>0.55),
            ('gap<=4 + consec>=2', lambda r: r['gap']<=4 and r['consec_red_end']>=2),
            ('gap<=3 + vwap<0 + n_red>=3', lambda r: r['gap']<=3 and r['vwap_dev']<0 and r['n_red']>=3),
            ('gap<=4 + vwap<0 + trap>0.3', lambda r: r['gap']<=4 and r['vwap_dev']<0 and r['trap_magnitude']>0.3),
            ('gap<=4 + vwap<-0.2 + sp>0.55', lambda r: r['gap']<=4 and r['vwap_dev']<-0.2 and r['sp']>0.55),
            ('gap<=5 + trap>0.3 + n_red>=3', lambda r: r['gap']<=5 and r['trap_magnitude']>0.3 and r['n_red']>=3),
            ('gap<=4 + price<800 + sp>0.55', lambda r: r['gap']<=4 and r['price']<800 and r['sp']>0.55),
        ]

        out.write(f"  Apply filter BEFORE scoring, then take top-8:\n\n")
        out.write(f"  {'Filter':>40} {'Trades':>7} {'TP%':>6} {'Win%':>6} {'DayW':>6} {'AvgRet':>8} {'ROC':>8}\n")
        out.write(f"  "+"-"*90+"\n")

        for name, fn in reject_filters:
            dpnls=[]; trades=0; tp_count=0; wins=0
            for d in dates:
                filtered = [r for r in by_date[d] if fn(r)]
                picks = sorted(filtered, key=lambda x: -x['v2_score'])[:8]
                dp = 0
                for r in picks:
                    trades += 1; ret = r['final_ret']
                    pnl = BASE*MARGIN*ret/100; dp += pnl
                    if r['tp_hit']: tp_count += 1
                    if pnl > 0: wins += 1
                dpnls.append(dp)
            if trades < 50: continue
            roc = sum(dpnls)/(BASE*8)*100
            dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw = wins/max(trades,1)*100
            tpr = tp_count/max(trades,1)*100
            avg_ret = sum(dpnls)/max(trades,1)/(BASE*MARGIN)*10000
            out.write(f"  {name:>40}  {trades:>6}  {tpr:>4.1f}%  {tw:>4.1f}%  {dw:>4.1f}%  {avg_ret:>+6.3f}%  {roc:>+7.1f}%\n")

        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. COMBINED: best scorer + best filter
        # ═══════════════════════════════════════
        print("Section 8: Combined scorer + filter sweep...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("8. COMBINED: best scorer + best filter combinations\n")
        out.write("="*120+"\n\n")

        combo_results = []
        top_scorer_keys = ['V2', 'gap_sp_mom', 'hybrid', 'gsmv', 'kitchen_sink', 'sqrt_gap', 'trap_bonus', 'consec_red']
        top_filters = [
            ('none', lambda r: True),
            ('gap<=4', lambda r: r['gap']<=4),
            ('gap<=5', lambda r: r['gap']<=5),
            ('vwap<0', lambda r: r['vwap_dev']<0),
            ('n_red>=3', lambda r: r['n_red']>=3),
            ('trap>0.3', lambda r: r['trap_magnitude']>0.3),
            ('gap<=4+vwap<0', lambda r: r['gap']<=4 and r['vwap_dev']<0),
            ('gap<=4+trap>0.3', lambda r: r['gap']<=4 and r['trap_magnitude']>0.3),
            ('gap<=4+nred>=3', lambda r: r['gap']<=4 and r['n_red']>=3),
            ('gap<=5+trap>0.3+nred>=3', lambda r: r['gap']<=5 and r['trap_magnitude']>0.3 and r['n_red']>=3),
        ]

        for sname in top_scorer_keys:
            for fname, fn in top_filters:
                for n in [5, 7, 8]:
                    dpnls=[]; trades=0; tp_count=0; wins=0
                    for d in dates:
                        filtered = [r for r in by_date[d] if fn(r)]
                        picks = sorted(filtered, key=lambda x: -x['alt_scores'].get(sname, 0))[:n]
                        dp = 0
                        for r in picks:
                            trades += 1; ret = r['final_ret']
                            pnl = BASE*MARGIN*ret/100; dp += pnl
                            if r['tp_hit']: tp_count += 1
                            if pnl > 0: wins += 1
                        dpnls.append(dp)
                    if trades < 50: continue
                    roc = sum(dpnls)/(BASE*n)*100
                    dw = sum(1 for p in dpnls if p>0)/len(dpnls)*100
                    tw = wins/max(trades,1)*100
                    tpr = tp_count/max(trades,1)*100
                    combo_results.append((roc, dw, tpr, tw, trades, sname, fname, n))

        combo_results.sort(key=lambda x: (-x[1], -x[0]))  # sort by dayWin, then ROC
        out.write(f"  Top-40 combinations (sorted by dayWin, then ROC):\n\n")
        out.write(f"  {'Scorer':>15} {'Filter':>28} {'N':>3} {'ROC':>8} {'DayW':>6} {'TP%':>6} {'TrdW':>6} {'Trades':>7}\n")
        out.write(f"  "+"-"*100+"\n")
        for roc, dw, tpr, tw, trades, sname, fname, n in combo_results[:40]:
            out.write(f"  {sname:>15} {fname:>28}  {n:>2}  {roc:>+7.1f}%  {dw:>4.1f}%  {tpr:>4.1f}%  {tw:>4.1f}%  {trades:>6}\n")

        print(f"  Section 8 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 9. STATISTICAL EDGE: per-trade expected value
        # ═══════════════════════════════════════
        print("Section 9: Statistical edge...")
        out.write(f"\n\n"+"="*120+"\n")
        out.write("9. STATISTICAL EDGE: expected value per trade for top configs\n")
        out.write("="*120+"\n\n")

        # Take top-5 combos and compute detailed stats
        for roc, dw, tpr, tw, trades, sname, fname, n in combo_results[:10]:
            all_rets = []
            for d in dates:
                fn = dict(top_filters)[fname]
                filtered = [r for r in by_date[d] if fn(r)]
                picks = sorted(filtered, key=lambda x: -x['alt_scores'].get(sname, 0))[:n]
                for r in picks:
                    all_rets.append(r['final_ret'])

            if not all_rets: continue
            rets = np.array(all_rets)
            out.write(f"  {sname}+{fname} top-{n}:\n")
            out.write(f"    Expected value: {np.mean(rets):+.4f}% per trade\n")
            out.write(f"    Median: {np.median(rets):+.4f}%\n")
            out.write(f"    Std: {np.std(rets):.4f}%\n")
            out.write(f"    Sharpe (daily): {np.mean(rets)/max(np.std(rets),0.001):.3f}\n")
            out.write(f"    Win rate: {(rets>0).sum()/len(rets)*100:.1f}%\n")
            out.write(f"    Avg win: {np.mean(rets[rets>0]):+.4f}%\n") if (rets>0).any() else None
            out.write(f"    Avg loss: {np.mean(rets[rets<=0]):+.4f}%\n") if (rets<=0).any() else None
            out.write(f"    Best: {np.max(rets):+.4f}%\n")
            out.write(f"    Worst: {np.min(rets):+.4f}%\n")
            out.write(f"    P5/P95: {np.percentile(rets,5):+.4f}% / {np.percentile(rets,95):+.4f}%\n\n")

        print(f"  Section 9 done {time.time()-t0:.0f}s")

    print(f"\nRound 1 complete in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
