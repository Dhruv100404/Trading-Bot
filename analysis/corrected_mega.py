"""
CORRECTED MEGA ANALYSIS — Fixed zero-P&L exit bug
====================================================
BUG FIX: EXIT trades now use actual P&L at check bucket (not 0).

Re-tests EVERYTHING:
  1. All scoring formulas (S5, v2, plain, combos)
  2. Position sizing with CORRECT exit P&L
  3. Combined scorer + sizing
  4. Position count optimization
  5. Dynamic exit rules
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'corrected_mega.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE_CAP = 10000
MARGIN = 5

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
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0: continue

                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Pre-compute at check buckets
                def at_bucket(cb):
                    if bkt[cb,C]<=0: return None
                    pnl = (entry-bkt[cb,C])/entry*100
                    vwap = (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0
                    ng = sum(1 for b in range(7,cb+1) if bkt[b,C]>bkt[b,O])
                    return {'pnl':pnl, 'vwap':vwap, 'n_green':ng}

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'sp':sp,'mom':mom,'n_red':n_red,
                    'ret90':ret90,'date':r['date'],
                    'at15':at_bucket(14),'at20':at_bucket(19),'at25':at_bucket(24),'at30':at_bucket(29),
                    # P&L at check buckets (for EXIT trades — the ACTUAL loss)
                    'pnl_at_15': (entry-bkt[14,C])/entry*100-COST if bkt[14,C]>0 else 0,
                    'pnl_at_20': (entry-bkt[19,C])/entry*100-COST if bkt[19,C]>0 else 0,
                    'pnl_at_25': (entry-bkt[24,C])/entry*100-COST if bkt[24,C]>0 else 0,
                    'pnl_at_30': (entry-bkt[29,C])/entry*100-COST if bkt[29,C]>0 else 0,
                })

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} records across {len(dates)} days in {time.time()-t0:.1f}s")

    # ── CORRECTED SIZING SIMULATION ──
    # When EXIT: use ACTUAL P&L at check bucket, NOT zero
    # When ADD: multiply the REMAINING return (from check to b90) by multiplier + add the return from entry to check at 1x
    def sim_corrected(scorer, sizing_fn, n_pos=8, check_b_key='at20', pnl_at_key='pnl_at_20'):
        total_pnl_rs = 0; day_wins = 0; days = 0; trades = 0; win_trades = 0
        for d in dates:
            pool = by_date[d]
            if len(pool) < 1: continue
            for t in pool: t['_sc'] = scorer(t)
            pool.sort(key=lambda x:-x['_sc'])
            picks = pool[:n_pos]
            days += 1; day_pnl = 0

            for t in picks:
                cb_data = t[check_b_key]
                if cb_data is None:
                    # No data at check bucket — use full b90 return at 1x
                    pnl_rs = BASE_CAP * MARGIN * t['ret90'] / 100
                else:
                    action = sizing_fn(cb_data)  # returns ('hold',1.0) or ('exit',) or ('add',mult)

                    if action[0] == 'exit':
                        # EXIT at check bucket — take ACTUAL loss/profit at that point
                        pnl_rs = BASE_CAP * MARGIN * t[pnl_at_key] / 100
                    elif action[0] == 'add':
                        mult = action[1]
                        # P&L from entry to check at 1x + P&L from check to b90 at mult
                        pnl_at_check = t[pnl_at_key]
                        remaining_ret = t['ret90'] - pnl_at_check  # return from check to b90
                        pnl_rs = BASE_CAP * MARGIN * (pnl_at_check + remaining_ret * mult) / 100
                    else:
                        # HOLD at 1x
                        pnl_rs = BASE_CAP * MARGIN * t['ret90'] / 100

                day_pnl += pnl_rs
                trades += 1
                if pnl_rs > 0: win_trades += 1

            total_pnl_rs += day_pnl
            if day_pnl > 0: day_wins += 1

        roc = total_pnl_rs / (BASE_CAP * n_pos) * 100
        return roc, day_wins/max(days,1)*100, win_trades/max(trades,1)*100, trades, days

    # ── SIZING FUNCTIONS (corrected) ──
    def no_sizing(cb):
        return ('hold', 1.0)

    def make_sizing(add3_pnl=1.0, add3_green=3, add2_pnl=0.3, exit_loss=0.5, exit_vwap=0.3, exit_green=6, mult3=3.0, mult2=2.0):
        def fn(cb):
            pnl = cb['pnl']; vwap = cb['vwap']; ng = cb['n_green']
            # EXIT conditions
            if pnl < -exit_loss and vwap > exit_vwap:
                return ('exit',)
            if ng >= exit_green and pnl < 0:
                return ('exit',)
            # ADD conditions
            if pnl >= add3_pnl and vwap < -exit_vwap and ng < add3_green:
                return ('add', mult3)
            if pnl >= add2_pnl and vwap < 0:
                return ('add', mult2)
            return ('hold', 1.0)
        return fn

    # ── SCORERS ──
    scorers = {
        'gap (plain)': lambda t: t['gap'],
        'S5: gap*(sp>.5?1:.3)*(m<0?1.3:.7)': lambda t: (t['gap'] if t['sp']>0.5 else t['gap']*0.3)*(1.3 if t['mom']<0 else 0.7),
        'v2: gap*sp*(m<-.5?1.4:m<0?1.1:.7)': lambda t: t['gap']*t['sp']*(1.4 if t['mom']<-0.5 else 1.1 if t['mom']<0 else 0.7),
        'gap*(1-sp)*(m<-.5?1.4:m<0?1.1:.7)': lambda t: t['gap']*(1-t['sp'])*(1.4 if t['mom']<-0.5 else 1.1 if t['mom']<0 else 0.7),
        'gap*(sp>.5?1:.3)*(m<-.5?1.4:m<0?1.1:.7)': lambda t: (t['gap'] if t['sp']>0.5 else t['gap']*0.3)*(1.4 if t['mom']<-0.5 else 1.1 if t['mom']<0 else 0.7),
        'gap*(1-sp)': lambda t: t['gap']*(1-t['sp']),
        'gap*(sp>.5?1:.3)': lambda t: t['gap'] if t['sp']>0.5 else t['gap']*0.3,
        'gap*(m<0?1.3:.7)': lambda t: t['gap']*(1.3 if t['mom']<0 else 0.7),
        'gap*(sp>.5?1:.3)*(p<500?1.2:.9)': lambda t: (t['gap'] if t['sp']>0.5 else t['gap']*0.3)*(1.2 if t['price']<500 else 0.9),
        'gap*sp*(m<0?1.3:.7)*(p<500?1.2:.9)': lambda t: t['gap']*t['sp']*(1.3 if t['mom']<0 else 0.7)*(1.2 if t['price']<500 else 0.9),
    }

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("CORRECTED MEGA ANALYSIS (zero-P&L exit bug FIXED)\n")
        out.write(f"Records: {n}, Days: {len(dates)}\n")
        out.write("FIX: EXIT trades now use ACTUAL P&L at check bucket\n")
        out.write("FIX: ADD trades use 1x return up to check + mult*return after check\n\n")

        # ═══════════════════════════════════════════
        # 1. SCORING FORMULAS (no sizing, fixed b90)
        # ═══════════════════════════════════════════
        out.write("="*110+"\n1. SCORING FORMULAS — no sizing, top-8, exit b90\n"+"="*110+"\n")
        out.write(f"  {'Scorer':<55} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*80+"\n")
        score_results = []
        for name, scorer in scorers.items():
            roc, dw, tw, nt, nd = sim_corrected(scorer, no_sizing, 8)
            score_results.append((roc, name, dw, tw, nt))
        score_results.sort(key=lambda x:-x[0])
        for roc, name, dw, tw, nt in score_results:
            out.write(f"  {name:<55} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%\n")

        # ═══════════════════════════════════════════
        # 2. POSITION COUNT with best scorer
        # ═══════════════════════════════════════════
        best_scorer_name = score_results[0][1]
        best_scorer = scorers[best_scorer_name]
        out.write(f"\n\n"+"="*110+f"\n2. POSITION COUNT with {best_scorer_name}\n"+"="*110+"\n")
        for n_pos in [3,4,5,6,7,8,10]:
            roc, dw, tw, nt, nd = sim_corrected(best_scorer, no_sizing, n_pos)
            out.write(f"  Top-{n_pos:>2}: ROC={roc:>+7.1f}%  dayW={dw:.1f}%  trdW={tw:.1f}%  trades={nt}\n")

        # ═══════════════════════════════════════════
        # 3. SIZING with CORRECTED P&L (the real test)
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. POSITION SIZING — CORRECTED (exit uses actual P&L)\n"+"="*110+"\n")

        sizing_configs = {
            'No sizing (baseline)': no_sizing,
            'EXIT only: lose>0.5%+aboveVWAP': make_sizing(add3_pnl=999, add2_pnl=999, exit_loss=0.5, exit_vwap=0.3, exit_green=99),
            'EXIT only: lose>0.3%+aboveVWAP': make_sizing(add3_pnl=999, add2_pnl=999, exit_loss=0.3, exit_vwap=0.3, exit_green=99),
            'EXIT: lose>0.5%+aboveVWAP OR 6+green+losing': make_sizing(add3_pnl=999, add2_pnl=999),
            'ADD 2x + EXIT': make_sizing(add3_pnl=999, mult3=2.0),
            'ADD 2x+3x + EXIT': make_sizing(),
            'ADD 2x+3x + EXIT (check b15)': make_sizing(),
            'ADD 2x+3x + EXIT (check b25)': make_sizing(),
            'ADD 2x+3x + EXIT (check b30)': make_sizing(),
            'Only ADD 2x (no exit)': make_sizing(add3_pnl=999, exit_loss=999, exit_green=99),
            'Only ADD 3x (no exit, no 2x)': make_sizing(add2_pnl=999, exit_loss=999, exit_green=99),
            'Aggressive: 3x@0.5%+2x@0.2%+exit@0.3%': make_sizing(add3_pnl=0.5, add2_pnl=0.2, exit_loss=0.3),
            'Conservative: 3x@1.5%+2x@0.5%+exit@0.7%': make_sizing(add3_pnl=1.5, add2_pnl=0.5, exit_loss=0.7),
        }

        # Test each scorer x sizing combo
        for s_name in [best_scorer_name, 'gap (plain)']:
            sc = scorers[s_name]
            out.write(f"\n  Scorer: {s_name}\n")
            out.write(f"  {'Sizing':<55} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*80+"\n")

            for sz_name, sz_fn in sizing_configs.items():
                if 'b15' in sz_name:
                    roc, dw, tw, nt, nd = sim_corrected(sc, sz_fn, 8, 'at15', 'pnl_at_15')
                elif 'b25' in sz_name:
                    roc, dw, tw, nt, nd = sim_corrected(sc, sz_fn, 8, 'at25', 'pnl_at_25')
                elif 'b30' in sz_name:
                    roc, dw, tw, nt, nd = sim_corrected(sc, sz_fn, 8, 'at30', 'pnl_at_30')
                else:
                    roc, dw, tw, nt, nd = sim_corrected(sc, sz_fn, 8)
                marker = " <<<" if 'baseline' in sz_name else ""
                out.write(f"  {sz_name:<55} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%{marker}\n")

        # ═══════════════════════════════════════════
        # 4. BEST COMBO: scorer x sizing x position count
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. BEST OVERALL COMBO\n"+"="*110+"\n")

        best_sizing = make_sizing()  # default params
        combo_results = []
        for s_name, sc in scorers.items():
            for n_pos in [6, 7, 8]:
                for sz_name, sz_fn, cb_key, pnl_key in [
                    ('no_sizing', no_sizing, 'at20', 'pnl_at_20'),
                    ('sizing_b20', best_sizing, 'at20', 'pnl_at_20'),
                    ('sizing_b15', best_sizing, 'at15', 'pnl_at_15'),
                    ('sizing_b25', best_sizing, 'at25', 'pnl_at_25'),
                    ('exit_only', make_sizing(add3_pnl=999, add2_pnl=999), 'at20', 'pnl_at_20'),
                ]:
                    roc, dw, tw, nt, nd = sim_corrected(sc, sz_fn, n_pos, cb_key, pnl_key)
                    combo_results.append((roc, f"{s_name} | {sz_name} | top{n_pos}", dw, tw))

        combo_results.sort(key=lambda x:-x[0])
        out.write(f"  {'Combo':<85} {'ROC':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*105+"\n")
        for roc, name, dw, tw in combo_results[:40]:
            out.write(f"  {name:<85} {roc:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%\n")

        # ═══════════════════════════════════════════
        # 5. HONEST COMPARISON: baseline vs best
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. HONEST FINAL VERDICT\n"+"="*110+"\n")

        # Baseline: plain gap, no sizing, top-8
        roc_base, dw_base, tw_base, _, _ = sim_corrected(scorers['gap (plain)'], no_sizing, 8)
        out.write(f"\n  BASELINE: gap (plain), no sizing, top-8\n")
        out.write(f"    ROC={roc_base:+.1f}%  DayWin={dw_base:.1f}%  TrdWin={tw_base:.1f}%\n")

        # Best scoring only (no sizing)
        roc_bs, dw_bs, tw_bs, _, _ = sim_corrected(best_scorer, no_sizing, 8)
        out.write(f"\n  BEST SCORER (no sizing): {best_scorer_name}\n")
        out.write(f"    ROC={roc_bs:+.1f}%  DayWin={dw_bs:.1f}%  TrdWin={tw_bs:.1f}%\n")
        out.write(f"    vs baseline: {roc_bs-roc_base:+.1f}% ROC improvement\n")

        # Best combo overall
        best = combo_results[0]
        out.write(f"\n  BEST OVERALL: {best[1]}\n")
        out.write(f"    ROC={best[0]:+.1f}%  DayWin={best[2]:.1f}%  TrdWin={best[3]:.1f}%\n")
        out.write(f"    vs baseline: {best[0]-roc_base:+.1f}% ROC improvement\n")

        # Exit-only sizing (safest approach)
        roc_eo, dw_eo, tw_eo, _, _ = sim_corrected(best_scorer, make_sizing(add3_pnl=999, add2_pnl=999), 8)
        out.write(f"\n  EXIT-ONLY SIZING (safest): {best_scorer_name} + exit losers only\n")
        out.write(f"    ROC={roc_eo:+.1f}%  DayWin={dw_eo:.1f}%  TrdWin={tw_eo:.1f}%\n")
        out.write(f"    vs baseline: {roc_eo-roc_base:+.1f}% ROC improvement\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
