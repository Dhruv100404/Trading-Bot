"""
Deep Cherry-Pick Analysis
==========================
For the 1268 Liquid5L margin stocks only.

Key question: Among the top-30 gap-up stocks each day, what separates
the ones that REVERSE (winners) from the ones that CONTINUE UP (losers)?

NO LOOKAHEAD: all features from buckets 0-5 only. Entry at bucket 6 open.
"""

import json
import numpy as np
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE = DATA_DIR / 'cherry_pick_analysis.txt'

O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6
B_ENTRY = 6
B_EXIT_66 = 65
B_EXIT_90 = 89
COST = 0.15

def main():
    t0 = time.time()

    # Load liquid symbols
    liquid_syms = set(json.loads((DATA_DIR / 'liquid-5l-symbols.json').read_text()))
    print(f"Liquid5L symbols: {len(liquid_syms)}")

    # Load candle data grouped by date
    files = [DATA_DIR / 'candles-consolidated.ndjson',
             DATA_DIR / 'candles-consolidated_new.ndjson']

    by_date = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                if rec['symbol'] not in liquid_syms:
                    continue
                bkts = rec['buckets']
                nb = min(len(bkts), 100)
                bkt = np.zeros((100, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j, O] = b['o']; bkt[j, H] = b['h']
                    bkt[j, L] = b['l']; bkt[j, C] = b['c']
                    bkt[j, V] = b['v']; bkt[j, VW] = b.get('vw', b['c'])
                    bkt[j, BR] = b.get('br', 0.5)

                by_date[rec['date']].append({
                    'sym': rec['symbol'], 'gap': rec['gapPct'],
                    'f5vol': rec.get('f5Vol', 0), 'f5range': rec.get('f5Range', 0),
                    'day_open': rec['dayOpen'], 'bkt': bkt,
                })

    n_rec = sum(len(v) for v in by_date.values())
    print(f"Loaded {n_rec} records across {len(by_date)} days in {time.time()-t0:.1f}s")

    # ── For each day: rank by gap, take top 30, compute features + outcomes ──
    all_top30 = []  # list of dicts with features + outcome

    for date in sorted(by_date.keys()):
        day = by_date[date]

        # Filter gap > 0.1% (sell candidates only)
        candidates = [s for s in day if s['gap'] > 0.1]
        if len(candidates) < 3:
            continue

        # Sort by gap DESC (current cherry-pick logic)
        candidates.sort(key=lambda x: -x['gap'])
        top30 = candidates[:30]

        for rank, s in enumerate(top30):
            bkt = s['bkt']
            entry = bkt[B_ENTRY, O]
            if entry <= 0:
                continue

            # ── Features (all from buckets 0-5, NO LOOKAHEAD) ──
            gap = s['gap']
            f5vol = s['f5vol']
            f5range = s['f5range']
            f5vol_rs = f5vol * s['day_open']
            price = s['day_open']

            # Bucket 0 (9:15)
            b0_o, b0_h, b0_l, b0_c = bkt[0, O], bkt[0, H], bkt[0, L], bkt[0, C]
            b0_v = bkt[0, V]
            b0_br = bkt[0, BR]
            b0_ret = (b0_c - b0_o) / b0_o * 100 if b0_o > 0 else 0
            b0_range = (b0_h - b0_l) / b0_o * 100 if b0_o > 0 else 0
            b0_green = 1 if b0_c > b0_o else 0

            # Bucket 1
            b1_ret = (bkt[1, C] - bkt[1, O]) / bkt[1, O] * 100 if bkt[1, O] > 0 else 0
            b1_br = bkt[1, BR]
            b1_green = 1 if bkt[1, C] > bkt[1, O] else 0

            # First 3 buckets
            or3_h = max(bkt[0, H], bkt[1, H], bkt[2, H])
            or3_l = min(bkt[0, L], bkt[1, L], bkt[2, L])
            or3_range = (or3_h - or3_l) / or3_h * 100 if or3_h > 0 else 0

            # First 6 buckets
            avg_br6 = float(np.mean(bkt[:6, BR]))
            avg_vol6 = float(np.mean(bkt[:6, V]))
            b0_vol_share = b0_v / max(float(np.sum(bkt[:6, V])), 1)

            # VWAP deviation at bucket 5
            b5_c = bkt[5, C]
            b5_vw = bkt[5, VW]
            vwap_dev = (b5_c - b5_vw) / b5_vw * 100 if b5_vw > 0 else 0

            # Move from open to bucket 5 close
            move_0_to_5 = (b5_c - b0_o) / b0_o * 100 if b0_o > 0 else 0

            # All 6 red?
            n_red = sum(1 for i in range(6) if bkt[i, C] < bkt[i, O])
            n_green = 6 - n_red

            # ── Outcomes (SELL direction) ──
            exit66 = bkt[B_EXIT_66, C]
            exit90 = bkt[B_EXIT_90, C]

            sell_ret_66 = (entry - exit66) / entry * 100 - COST if exit66 > 0 else 0
            sell_ret_90 = (entry - exit90) / entry * 100 - COST if exit90 > 0 else 0

            # MFE/MAE
            min_l_66 = float(np.min(bkt[B_ENTRY:B_EXIT_66+1, L]))
            max_h_66 = float(np.max(bkt[B_ENTRY:B_EXIT_66+1, H]))
            sell_mfe_66 = (entry - min_l_66) / entry * 100
            sell_mae_66 = (max_h_66 - entry) / entry * 100

            all_top30.append({
                'date': date, 'sym': s['sym'], 'rank': rank + 1,
                'gap': gap, 'price': price, 'f5vol_rs': f5vol_rs,
                'f5range': f5range,
                'b0_ret': b0_ret, 'b0_range': b0_range, 'b0_green': b0_green,
                'b0_br': b0_br, 'b0_vol_share': b0_vol_share,
                'b1_ret': b1_ret, 'b1_br': b1_br, 'b1_green': b1_green,
                'or3_range': or3_range, 'avg_br6': avg_br6,
                'vwap_dev': vwap_dev, 'move_0_to_5': move_0_to_5,
                'n_red': n_red, 'n_green': n_green,
                'sell_ret_66': sell_ret_66, 'sell_ret_90': sell_ret_90,
                'sell_mfe_66': sell_mfe_66, 'sell_mae_66': sell_mae_66,
                'is_top8': rank < 8,
                'win': 1 if sell_ret_66 > 0 else 0,
            })

    print(f"Top-30 entries: {len(all_top30)} across {len(by_date)} days")

    # ── Analysis ──
    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write("DEEP CHERRY-PICK ANALYSIS\n")
        out.write(f"Universe: {len(liquid_syms)} Liquid5L margin stocks\n")
        out.write(f"Data: {len(all_top30)} stock-day entries in top-30 across {len(by_date)} days\n")
        out.write(f"Entry: b7 open (9:21 AM), Exit: b66 close (10:20 AM), Cost: {COST}%\n\n")

        # Convert to numpy for vectorized analysis
        data = {k: np.array([r[k] for r in all_top30]) for k in all_top30[0].keys() if k not in ('date', 'sym')}

        # ── 1. RANK ANALYSIS: How does rank (1-30) correlate with win rate? ──
        out.write("="*100 + "\n")
        out.write("1. WIN RATE BY RANK (is higher gap = better pick?)\n")
        out.write("="*100 + "\n")
        out.write(f"  {'Rank':>6} {'Count':>6} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8} {'AvgMAE':>8} {'AvgGap':>8}\n")
        out.write("  " + "-"*60 + "\n")

        for r in range(1, 31):
            mask = data['rank'] == r
            n = np.sum(mask)
            if n == 0: continue
            wr = np.mean(data['win'][mask]) * 100
            ar = np.mean(data['sell_ret_66'][mask])
            mfe = np.mean(data['sell_mfe_66'][mask])
            mae = np.mean(data['sell_mae_66'][mask])
            ag = np.mean(data['gap'][mask])
            out.write(f"  {r:>6} {n:>6} {wr:>5.1f}% {ar:>+7.3f}% {mfe:>+7.3f}% {mae:>+7.3f}% {ag:>7.2f}%\n")

        # Grouped ranks
        out.write(f"\n  {'Group':>10} {'Count':>6} {'Win%':>6} {'AvgRet':>8} {'AvgGap':>8}\n")
        out.write("  " + "-"*45 + "\n")
        for lo, hi, label in [(1,8,"Top 1-8"), (9,15,"Rank 9-15"), (16,22,"Rank 16-22"), (23,30,"Rank 23-30")]:
            mask = (data['rank'] >= lo) & (data['rank'] <= hi)
            n = np.sum(mask)
            if n == 0: continue
            wr = np.mean(data['win'][mask]) * 100
            ar = np.mean(data['sell_ret_66'][mask])
            ag = np.mean(data['gap'][mask])
            out.write(f"  {label:>10} {n:>6} {wr:>5.1f}% {ar:>+7.3f}% {ag:>7.2f}%\n")

        # ── 2. WHAT KILLS TOP-8 PICKS? Feature comparison: winners vs losers in top 8 ──
        out.write("\n" + "="*100 + "\n")
        out.write("2. TOP-8 WINNERS vs LOSERS: What features differ?\n")
        out.write("="*100 + "\n")

        t8 = data['is_top8'].astype(bool)
        t8_win = t8 & (data['win'] == 1)
        t8_lose = t8 & (data['win'] == 0)

        features = ['gap', 'price', 'f5vol_rs', 'f5range', 'b0_ret', 'b0_range',
                     'b0_br', 'b0_vol_share', 'b1_ret', 'b1_br', 'or3_range',
                     'avg_br6', 'vwap_dev', 'move_0_to_5', 'n_red']

        out.write(f"  Top-8 total: {np.sum(t8)}, Winners: {np.sum(t8_win)}, Losers: {np.sum(t8_lose)}\n")
        out.write(f"  Win rate: {np.mean(data['win'][t8])*100:.1f}%\n\n")
        out.write(f"  {'Feature':<18} {'Winners':>10} {'Losers':>10} {'Delta':>10} {'Signal':>15}\n")
        out.write("  " + "-"*70 + "\n")

        for feat in features:
            w_avg = np.mean(data[feat][t8_win])
            l_avg = np.mean(data[feat][t8_lose])
            delta = w_avg - l_avg
            # Determine direction of signal
            if abs(delta) < 0.001:
                sig = "no signal"
            elif feat in ('b0_br', 'avg_br6', 'b0_green', 'n_green', 'vwap_dev', 'move_0_to_5', 'b0_ret', 'b1_ret'):
                sig = "LOWER = better" if delta < 0 else "HIGHER = better"
            else:
                sig = "LOWER = better" if delta < 0 else "HIGHER = better"
            out.write(f"  {feat:<18} {w_avg:>10.3f} {l_avg:>10.3f} {delta:>+10.3f} {sig:>15}\n")

        # ── 3. MISSED WINNERS: stocks ranked 9-30 that beat the top-8 losers ──
        out.write("\n" + "="*100 + "\n")
        out.write("3. MISSED WINNERS: Stocks ranked 9-30 that outperformed top-8 losers\n")
        out.write("="*100 + "\n")

        # Per day: count how many top-8 losers could have been replaced by rank 9-30 winners
        dates_list = sorted(set(r['date'] for r in all_top30))
        replace_count = 0
        total_days = 0
        better_picks_per_day = []

        for date in dates_list:
            day_entries = [r for r in all_top30 if r['date'] == date]
            t8_entries = [r for r in day_entries if r['rank'] <= 8]
            r9_30 = [r for r in day_entries if 9 <= r['rank'] <= 30]

            t8_losers = [r for r in t8_entries if r['sell_ret_66'] <= 0]
            r9_30_winners = [r for r in r9_30 if r['sell_ret_66'] > 0]
            r9_30_winners.sort(key=lambda x: -x['sell_ret_66'])

            n_replaceable = min(len(t8_losers), len(r9_30_winners))
            replace_count += n_replaceable

            if t8_entries:
                total_days += 1
                # Compute: what if we replaced top-8 losers with best rank-9-30 winners?
                actual_pnl = sum(r['sell_ret_66'] for r in t8_entries)
                improved_entries = [r for r in t8_entries if r['sell_ret_66'] > 0]
                improved_entries.extend(r9_30_winners[:len(t8_losers)])
                improved_entries = improved_entries[:8]
                improved_pnl = sum(r['sell_ret_66'] for r in improved_entries)
                better_picks_per_day.append({
                    'date': date,
                    'actual_pnl': actual_pnl,
                    'improved_pnl': improved_pnl,
                    'n_replaced': n_replaceable,
                    'n_t8_losers': len(t8_losers),
                })

        out.write(f"  Days with top-8 trades: {total_days}\n")
        out.write(f"  Total replaceable picks: {replace_count} (avg {replace_count/max(total_days,1):.1f}/day)\n")
        actual_total = sum(d['actual_pnl'] for d in better_picks_per_day)
        improved_total = sum(d['improved_pnl'] for d in better_picks_per_day)
        out.write(f"  Actual top-8 total ret:   {actual_total:+.1f}%\n")
        out.write(f"  Perfect hindsight ret:    {improved_total:+.1f}%\n")
        out.write(f"  Improvement potential:    {improved_total - actual_total:+.1f}%\n")

        # ── 4. FEATURE-BASED SCORING: Test alternative cherry-pick formulas ──
        out.write("\n" + "="*100 + "\n")
        out.write("4. ALTERNATIVE SCORING FORMULAS (pick top-8 by score, not just gap)\n")
        out.write("="*100 + "\n")

        scoring_formulas = {
            'S0: gap only (current)':         lambda r: r['gap'],
            'S1: gap * (1 - avg_br6)':        lambda r: r['gap'] * (1 - r['avg_br6']),
            'S2: gap * (1 - b0_br)':          lambda r: r['gap'] * (1 - r['b0_br']),
            'S3: gap * n_red/6':              lambda r: r['gap'] * r['n_red'] / 6,
            'S4: gap * (1 if b0_red else 0.5)': lambda r: r['gap'] * (0.5 if r['b0_green'] else 1.0),
            'S5: gap * (1-avg_br6) * n_red/6':  lambda r: r['gap'] * (1-r['avg_br6']) * max(r['n_red']/6, 0.1),
            'S6: gap - move_0_to_5':          lambda r: r['gap'] - r['move_0_to_5'],
            'S7: gap * (1-avg_br6) * (1 if b0_red else 0.5)': lambda r: r['gap'] * (1-r['avg_br6']) * (0.5 if r['b0_green'] else 1.0),
            'S8: gap * sqrt(f5vol_rs/5e5)':   lambda r: r['gap'] * min((r['f5vol_rs']/500000)**0.5, 3),
            'S9: gap*(1-avg_br6) + vwap_dev': lambda r: r['gap']*(1-r['avg_br6']) - r['vwap_dev'],
            'S10: gap*(1-b0_br)*(1-b1_br)':   lambda r: r['gap'] * (1-r['b0_br']) * (1-r['b1_br']),
            'S11: gap if avg_br6<0.5 else gap*0.3': lambda r: r['gap'] if r['avg_br6'] < 0.5 else r['gap']*0.3,
            'S12: gap * (1-avg_br6) * f5range': lambda r: r['gap'] * (1-r['avg_br6']) * max(r['f5range'], 0.1),
        }

        formula_results = []

        for name, scorer in scoring_formulas.items():
            day_pnls = []
            day_wins = 0
            total_trades = 0
            total_win_trades = 0

            for date in dates_list:
                day_entries = [r for r in all_top30 if r['date'] == date]
                if len(day_entries) < 3:
                    continue

                # Score and pick top 8
                for r in day_entries:
                    r['_score'] = scorer(r)
                day_entries.sort(key=lambda x: -x['_score'])
                picks = day_entries[:8]

                day_ret = sum(r['sell_ret_66'] for r in picks)
                day_pnls.append(day_ret)
                if day_ret > 0:
                    day_wins += 1
                total_trades += len(picks)
                total_win_trades += sum(1 for r in picks if r['sell_ret_66'] > 0)

            n_days = len(day_pnls)
            total_ret = sum(day_pnls)
            avg_daily = total_ret / max(n_days, 1)
            day_wr = day_wins / max(n_days, 1) * 100
            trade_wr = total_win_trades / max(total_trades, 1) * 100

            formula_results.append((total_ret, name, n_days, day_wr, trade_wr, avg_daily, total_trades))

        formula_results.sort(key=lambda x: -x[0])

        out.write(f"  {'Scoring Formula':<45} {'TotalRet':>9} {'DayWin%':>8} {'TrdWin%':>8} {'AvgDay':>8} {'Trades':>7}\n")
        out.write("  " + "-"*90 + "\n")
        for total_ret, name, n_days, day_wr, trade_wr, avg_daily, n_trades in formula_results:
            out.write(f"  {name:<45} {total_ret:>+8.1f}% {day_wr:>7.1f}% {trade_wr:>7.1f}% {avg_daily:>+7.3f}% {n_trades:>7}\n")

        # ── 5. BEST FORMULA: detailed day-by-day comparison vs current ──
        best_name = formula_results[0][1]
        worst_name = formula_results[-1][1]
        current_idx = next(i for i, (_, n, *_) in enumerate(formula_results) if 'current' in n)

        out.write(f"\n  BEST:    {formula_results[0][1]} (total {formula_results[0][0]:+.1f}%)\n")
        out.write(f"  CURRENT: {formula_results[current_idx][1]} (total {formula_results[current_idx][0]:+.1f}%)\n")
        out.write(f"  IMPROVEMENT: {formula_results[0][0] - formula_results[current_idx][0]:+.1f}%\n")

        # ── 6. FEATURE THRESHOLDS: optimal cutoffs for each feature ──
        out.write("\n" + "="*100 + "\n")
        out.write("5. FEATURE THRESHOLD ANALYSIS (within top-30 pool)\n")
        out.write("   For each feature, what threshold splits winners from losers best?\n")
        out.write("="*100 + "\n")

        for feat in features:
            vals = data[feat]
            wins = data['win']
            rets = data['sell_ret_66']

            percentiles = [10, 20, 30, 40, 50, 60, 70, 80, 90]
            out.write(f"\n  {feat}:\n")
            out.write(f"    {'Threshold':>12} {'Below':>8} {'BelowWin':>9} {'BelowRet':>9} {'Above':>8} {'AboveWin':>9} {'AboveRet':>9}\n")

            best_split = None
            best_diff = 0

            for pct in percentiles:
                thresh = np.percentile(vals, pct)
                below = vals <= thresh
                above = vals > thresh
                n_below = np.sum(below)
                n_above = np.sum(above)
                if n_below < 20 or n_above < 20:
                    continue
                wr_below = np.mean(wins[below]) * 100
                wr_above = np.mean(wins[above]) * 100
                ret_below = np.mean(rets[below])
                ret_above = np.mean(rets[above])

                diff = abs(wr_above - wr_below)
                if diff > best_diff:
                    best_diff = diff
                    best_split = (feat, thresh, pct, wr_below, wr_above, ret_below, ret_above, n_below, n_above)

                out.write(f"    {thresh:>12.3f} {n_below:>8} {wr_below:>8.1f}% {ret_below:>+8.3f}% {n_above:>8} {wr_above:>8.1f}% {ret_above:>+8.3f}%\n")

            if best_split:
                f, t, p, wb, wa, rb, ra, nb, na = best_split
                better = "ABOVE" if wa > wb else "BELOW"
                out.write(f"    >> Best split at p{p} ({t:.3f}): {better} wins {max(wa,wb):.1f}% vs {min(wa,wb):.1f}%\n")

        # ── 7. COMBO FILTERS: test multi-feature filters on top-30 pool ──
        out.write("\n" + "="*100 + "\n")
        out.write("6. COMBO FILTERS: Apply filters BEFORE cherry-pick, then pick top-8 by gap\n")
        out.write("="*100 + "\n")

        combo_filters = {
            'F0: no filter (current)':           lambda r: True,
            'F1: avg_br6 < 0.50':               lambda r: r['avg_br6'] < 0.50,
            'F2: avg_br6 < 0.45':               lambda r: r['avg_br6'] < 0.45,
            'F3: b0_br < 0.50':                  lambda r: r['b0_br'] < 0.50,
            'F4: b0_red (b0_ret < 0)':           lambda r: r['b0_ret'] < 0,
            'F5: move_0_to_5 < 0':              lambda r: r['move_0_to_5'] < 0,
            'F6: move_0_to_5 < -0.3%':          lambda r: r['move_0_to_5'] < -0.3,
            'F7: n_red >= 3':                    lambda r: r['n_red'] >= 3,
            'F8: n_red >= 4':                    lambda r: r['n_red'] >= 4,
            'F9: avg_br6<0.50 + n_red>=3':       lambda r: r['avg_br6'] < 0.50 and r['n_red'] >= 3,
            'F10: avg_br6<0.50 + b0_red':        lambda r: r['avg_br6'] < 0.50 and r['b0_ret'] < 0,
            'F11: avg_br6<0.45 + n_red>=3':      lambda r: r['avg_br6'] < 0.45 and r['n_red'] >= 3,
            'F12: avg_br6<0.50 + move<0':        lambda r: r['avg_br6'] < 0.50 and r['move_0_to_5'] < 0,
            'F13: b0_br<0.50 + b1_br<0.50':     lambda r: r['b0_br'] < 0.50 and r['b1_br'] < 0.50,
            'F14: vwap_dev > 0 (price>VWAP)':    lambda r: r['vwap_dev'] > 0,
            'F15: vwap_dev > 0.2':               lambda r: r['vwap_dev'] > 0.2,
            'F16: gap>1.5%':                     lambda r: r['gap'] > 1.5,
            'F17: gap>1.5% + avg_br6<0.50':      lambda r: r['gap'] > 1.5 and r['avg_br6'] < 0.50,
            'F18: gap>2% + avg_br6<0.50':        lambda r: r['gap'] > 2.0 and r['avg_br6'] < 0.50,
            'F19: gap>1% + n_red>=4 + avg_br6<0.45': lambda r: r['gap']>1 and r['n_red']>=4 and r['avg_br6']<0.45,
        }

        filter_results = []
        for name, filt in combo_filters.items():
            day_pnls = []
            day_wins = 0
            total_trades = 0
            total_win_trades = 0
            empty_days = 0

            for date in dates_list:
                day_all = [r for r in all_top30 if r['date'] == date]
                day_filtered = [r for r in day_all if filt(r)]

                if len(day_filtered) == 0:
                    empty_days += 1
                    day_pnls.append(0)
                    continue

                day_filtered.sort(key=lambda x: -x['gap'])
                picks = day_filtered[:8]

                day_ret = sum(r['sell_ret_66'] for r in picks)
                day_pnls.append(day_ret)
                if day_ret > 0:
                    day_wins += 1
                total_trades += len(picks)
                total_win_trades += sum(1 for r in picks if r['sell_ret_66'] > 0)

            n_days = len(day_pnls)
            active_days = n_days - empty_days
            total_ret = sum(day_pnls)
            day_wr = day_wins / max(active_days, 1) * 100
            trade_wr = total_win_trades / max(total_trades, 1) * 100
            avg_daily = total_ret / max(n_days, 1)

            filter_results.append((total_ret, name, active_days, day_wr, trade_wr, avg_daily, total_trades, empty_days))

        filter_results.sort(key=lambda x: -x[0])

        out.write(f"  {'Filter':<45} {'TotRet':>8} {'ActDays':>8} {'DayWin':>7} {'TrdWin':>7} {'AvgDay':>8} {'Trades':>6} {'Empty':>5}\n")
        out.write("  " + "-"*100 + "\n")
        for total_ret, name, act, dw, tw, ad, nt, empty in filter_results:
            out.write(f"  {name:<45} {total_ret:>+7.1f}% {act:>8} {dw:>6.1f}% {tw:>6.1f}% {ad:>+7.3f}% {nt:>6} {empty:>5}\n")

        # ── 8. BEST COMBINED: scoring + filter together ──
        out.write("\n" + "="*100 + "\n")
        out.write("7. BEST COMBINED: Top scoring formula + top filter\n")
        out.write("="*100 + "\n")

        # Test top 3 scorers with top 3 filters
        top_scorers = [(n, scoring_formulas[n]) for _, n, *_ in formula_results[:3]]
        top_filters = [(n, combo_filters[n]) for _, n, *_ in filter_results[:5] if 'no filter' not in n]

        combo_results = []
        for s_name, scorer in top_scorers:
            for f_name, filt in top_filters:
                day_pnls = []
                day_wins = 0
                total_trades = 0
                total_win_trades = 0

                for date in dates_list:
                    day_all = [r for r in all_top30 if r['date'] == date]
                    day_filtered = [r for r in day_all if filt(r)]
                    if not day_filtered: continue

                    for r in day_filtered:
                        r['_score'] = scorer(r)
                    day_filtered.sort(key=lambda x: -x['_score'])
                    picks = day_filtered[:8]

                    day_ret = sum(r['sell_ret_66'] for r in picks)
                    day_pnls.append(day_ret)
                    if day_ret > 0: day_wins += 1
                    total_trades += len(picks)
                    total_win_trades += sum(1 for r in picks if r['sell_ret_66'] > 0)

                n = len(day_pnls)
                total = sum(day_pnls)
                dw = day_wins / max(n, 1) * 100
                tw = total_win_trades / max(total_trades, 1) * 100

                combo_results.append((total, f"{s_name} + {f_name}", n, dw, tw, total_trades))

        combo_results.sort(key=lambda x: -x[0])

        out.write(f"  {'Combo':<80} {'TotRet':>8} {'Days':>5} {'DayWin':>7} {'TrdWin':>7}\n")
        out.write("  " + "-"*110 + "\n")
        for total, name, n, dw, tw, nt in combo_results[:15]:
            out.write(f"  {name:<80} {total:>+7.1f}% {n:>5} {dw:>6.1f}% {tw:>6.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")

    print(f"Done in {time.time()-t0:.1f}s -> {OUT_FILE}")


if __name__ == '__main__':
    main()
