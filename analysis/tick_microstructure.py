"""
TICK-LEVEL MICROSTRUCTURE ANALYSIS
====================================
Deep tick-by-tick study on cherry-picked top-30 trades.

Pre-filter:
  - Exclude circuit stocks (gap > 10% or first candle = 0 range)
  - Exclude low liquidity (f5vol*price < 5L)
  - Only liquid, freely tradable stocks

Analyze:
  - Tick-by-tick momentum shifts post-entry
  - First N buckets: follow-through vs rejection
  - Drawdown progression patterns
  - Microstructure of winners vs losers
  - Loss pattern extraction -> scoring rules
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'tick_microstructure.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

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

                # FILTER: exclude circuit stocks (gap > 10%)
                if abs(r['gapPct']) > 10: continue

                # FILTER: exclude low liquidity (f5vol*price < 5L)
                f5vol_rs = r.get('f5Vol', 0) * r['dayOpen']
                if f5vol_rs < 500000: continue

                bkts = r['buckets']
                nb = min(len(bkts), 100)
                bkt = np.zeros((100, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)

                entry = bkt[6, O]
                if entry <= 0 or bkt[89, C] <= 0: continue

                # FILTER: exclude zero-range first candle (circuit locked)
                if bkt[0, H] == bkt[0, L]: continue

                ret90 = (entry - bkt[89, C]) / entry * 100 - COST

                # Entry features (NO LOOKAHEAD)
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1 - cp / 6
                mom = (bkt[5,C] - bkt[0,O]) / bkt[0,O] * 100 if bkt[0,O] > 0 else 0

                # Tick-by-tick path from entry (b7) to b90
                ticks = []
                for b in range(7, 90):
                    if bkt[b, C] <= 0: continue
                    sell_pnl = (entry - bkt[b, C]) / entry * 100
                    is_green = bkt[b, C] > bkt[b, O]
                    body_pct = abs(bkt[b, C] - bkt[b, O]) / bkt[b, O] * 100 if bkt[b, O] > 0 else 0
                    range_pct = (bkt[b, H] - bkt[b, L]) / entry * 100
                    above_vwap = bkt[b, C] > bkt[b, VW] if bkt[b, VW] > 0 else False
                    vol = float(bkt[b, V])
                    ticks.append({
                        'b': b, 'pnl': sell_pnl, 'green': is_green,
                        'body': body_pct, 'range': range_pct,
                        'above_vwap': above_vwap, 'vol': vol,
                        'close': bkt[b, C], 'open': bkt[b, O],
                        'high': bkt[b, H], 'low': bkt[b, L],
                    })

                if len(ticks) < 70: continue

                # Derived tick metrics
                # First 3 ticks after entry (b7, b8, b9): immediate reaction
                first3 = ticks[:3]
                first3_pnl = first3[-1]['pnl'] if first3 else 0
                first3_all_red = all(not t['green'] for t in first3)
                first3_any_big_green = any(t['green'] and t['body'] > 0.3 for t in first3)
                first3_max_green_body = max((t['body'] for t in first3 if t['green']), default=0)

                # First 5 ticks (b7-b11): follow-through
                first5 = ticks[:5]
                first5_pnl = first5[-1]['pnl'] if first5 else 0
                first5_green_count = sum(t['green'] for t in first5)
                first5_vol_sum = sum(t['vol'] for t in first5)

                # First 10 ticks (b7-b16): confirmation or rejection
                first10 = ticks[:10]
                first10_pnl = first10[-1]['pnl'] if first10 else 0
                first10_green_count = sum(t['green'] for t in first10)
                first10_max_drawup = max((t['pnl'] for t in first10 if t['pnl'] < 0), default=0)  # worst for sell = highest price

                # Drawdown progression: how quickly does the trade go wrong?
                max_adverse_b10 = min((t['pnl'] for t in first10), default=0)  # most negative pnl in first 10
                max_adverse_b20 = min((t['pnl'] for t in ticks[:14]), default=0)

                # Momentum quality: is the drop steady or choppy?
                pnl_changes = [ticks[i]['pnl'] - ticks[i-1]['pnl'] for i in range(1, min(15, len(ticks)))]
                n_favorable = sum(1 for c in pnl_changes if c > 0)  # positive = price dropping (good for sell)
                n_adverse = sum(1 for c in pnl_changes if c < 0)
                momentum_ratio = n_favorable / max(n_favorable + n_adverse, 1)

                # Breakout quality: did price break below b0 low in first 10 ticks?
                b0_low = bkt[0, L]
                broke_below_b0 = any(t['low'] < b0_low for t in first10) if b0_low > 0 else False

                # Fake breakout detection: went favorable then reversed sharply
                peak_favorable = max((t['pnl'] for t in first10), default=0)
                reversal_from_peak = peak_favorable - first10_pnl if peak_favorable > 0 else 0

                # Volatility clustering: is range expanding or contracting?
                early_ranges = [t['range'] for t in ticks[:5]]
                late_ranges = [t['range'] for t in ticks[5:10]]
                vol_expansion = np.mean(late_ranges) / max(np.mean(early_ranges), 0.001) if early_ranges else 1

                # VWAP behavior: how quickly does price get below VWAP?
                ticks_to_below_vwap = None
                for i, t in enumerate(ticks[:20]):
                    if not t['above_vwap']:
                        ticks_to_below_vwap = i
                        break

                # Volume profile: is volume higher on red candles (selling) or green (buying)?
                red_vol = sum(t['vol'] for t in first10 if not t['green'])
                green_vol = sum(t['vol'] for t in first10 if t['green'])
                sell_vol_ratio = red_vol / max(red_vol + green_vol, 1)

                by_date[r['date']].append({
                    'sym': r['symbol'], 'gap': r['gapPct'], 'price': r['dayOpen'],
                    'entry': entry, 'sp': sp, 'mom': mom, 'ret90': ret90,
                    'win': ret90 > 0, 'date': r['date'], 'ticks': ticks,
                    'f5vol_rs': f5vol_rs,
                    # Score for cherry-pick (using best scorer from corrected analysis)
                    'score': (r['gapPct'] if sp > 0.5 else r['gapPct'] * 0.3) * (1.2 if r['dayOpen'] < 500 else 0.9),
                    # Tick-derived features (all NO LOOKAHEAD — from first N ticks only)
                    'first3_pnl': first3_pnl,
                    'first3_all_red': first3_all_red,
                    'first3_big_green': first3_any_big_green,
                    'first3_max_green': first3_max_green_body,
                    'first5_pnl': first5_pnl,
                    'first5_green': first5_green_count,
                    'first5_vol': first5_vol_sum,
                    'first10_pnl': first10_pnl,
                    'first10_green': first10_green_count,
                    'max_adverse_b10': max_adverse_b10,
                    'max_adverse_b20': max_adverse_b20,
                    'momentum_ratio': momentum_ratio,
                    'broke_below_b0': broke_below_b0,
                    'peak_then_reverse': reversal_from_peak,
                    'vol_expansion': vol_expansion,
                    'ticks_to_below_vwap': ticks_to_below_vwap,
                    'sell_vol_ratio': sell_vol_ratio,
                })

    dates = sorted(by_date.keys())

    # Cherry-pick top-30 per day
    all_top30 = []
    for d in dates:
        pool = sorted(by_date[d], key=lambda x: -x['score'])
        for rank, t in enumerate(pool[:30]):
            t['rank'] = rank + 1
            all_top30.append(t)

    # Split top-8 (picked) and rest (9-30)
    top8 = [t for t in all_top30 if t['rank'] <= 8]
    winners = [t for t in top8 if t['win']]
    losers = [t for t in top8 if not t['win']]

    print(f"After filtering: {len(all_top30)} top-30 entries, {len(top8)} top-8 ({len(winners)}W/{len(losers)}L)")
    print(f"Filtered out circuit + illiquid stocks")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("TICK-LEVEL MICROSTRUCTURE ANALYSIS\n")
        out.write(f"Clean data: circuit excluded, liquidity>5L, gap<10%\n")
        out.write(f"Top-30: {len(all_top30)}, Top-8: {len(top8)} ({len(winners)}W/{len(losers)}L, {len(winners)/len(top8)*100:.1f}% win)\n\n")

        # ═══════════════════════════════════════════
        # 1. IMMEDIATE REACTION: First 3 ticks after entry
        # ═══════════════════════════════════════════
        out.write("="*110+"\n1. IMMEDIATE REACTION (first 3 ticks: b7-b9, 3 minutes after entry)\n"+"="*110+"\n")
        out.write(f"\n  WINNERS vs LOSERS — first 3 ticks:\n")
        out.write(f"  {'Metric':<30} {'Winners':>10} {'Losers':>10} {'Signal':>20}\n  "+"-"*75+"\n")
        metrics = [
            ('first3_pnl', 'P&L after 3 ticks (%)'),
            ('first3_max_green', 'Max green body (%)'),
            ('first5_pnl', 'P&L after 5 ticks (%)'),
            ('first5_green', 'Green count in 5 ticks'),
            ('first10_pnl', 'P&L after 10 ticks (%)'),
            ('first10_green', 'Green count in 10 ticks'),
            ('max_adverse_b10', 'Max adverse in 10 ticks (%)'),
            ('max_adverse_b20', 'Max adverse in 20 ticks (%)'),
            ('momentum_ratio', 'Momentum ratio (0-1)'),
            ('peak_then_reverse', 'Peak then reverse (%)'),
            ('vol_expansion', 'Vol expansion ratio'),
            ('sell_vol_ratio', 'Sell vol ratio'),
        ]
        for key, label in metrics:
            wv = np.mean([t[key] for t in winners if t[key] is not None])
            lv = np.mean([t[key] for t in losers if t[key] is not None])
            delta = wv - lv
            sig = ""
            if abs(delta) > 0.01:
                if key in ('first3_pnl','first5_pnl','first10_pnl','momentum_ratio','sell_vol_ratio'):
                    sig = "HIGHER = winner" if delta > 0 else "LOWER = winner"
                elif key in ('first5_green','first10_green','first3_max_green','max_adverse_b10','peak_then_reverse','vol_expansion'):
                    sig = "LOWER = winner" if delta < 0 else "HIGHER = winner"
            out.write(f"  {label:<30} {wv:>10.3f} {lv:>10.3f} {sig:>20}\n")

        # ═══════════════════════════════════════════
        # 2. FOLLOW-THROUGH QUALITY
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. FOLLOW-THROUGH: does price continue dropping after entry?\n"+"="*110+"\n")

        # P&L at each checkpoint -> win rate
        out.write(f"\n  P&L at checkpoint -> final win rate:\n")
        for checkpoint_name, ticks_n in [('3 ticks (b9)', 'first3_pnl'), ('5 ticks (b11)', 'first5_pnl'), ('10 ticks (b16)', 'first10_pnl')]:
            out.write(f"\n  After {checkpoint_name}:\n")
            out.write(f"    {'Status':>20} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*45+"\n")
            for plo, phi, plbl in [(-99,-0.5,'losing >0.5%'),(-0.5,-0.2,'losing 0.2-0.5%'),(-0.2,0,'losing 0-0.2%'),
                                    (0,0.2,'winning 0-0.2%'),(0.2,0.5,'winning 0.2-0.5%'),(0.5,1,'winning 0.5-1%'),(1,99,'winning >1%')]:
                sub = [t for t in top8 if plo <= t[ticks_n] < phi]
                if len(sub) < 5: continue
                wr = sum(t['win'] for t in sub) / len(sub) * 100
                ar = np.mean([t['ret90'] for t in sub])
                out.write(f"    {plbl:>20} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════════
        # 3. LOSS PATTERN DETECTION — tick sequences before loss
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. LOSS PATTERNS — what tick sequences predict failure?\n"+"="*110+"\n")

        loss_patterns = {
            'Instant rejection: first3 all green': lambda t: t['first5_green'] >= 3 and t['first3_pnl'] < -0.1,
            'Big green in first 3 (body>0.3%)': lambda t: t['first3_big_green'],
            'No follow-through: first5 P&L < -0.2%': lambda t: t['first5_pnl'] < -0.2,
            'Immediate adverse: max_adverse_b10 < -0.5%': lambda t: t['max_adverse_b10'] < -0.5,
            'Momentum collapse: ratio < 0.35': lambda t: t['momentum_ratio'] < 0.35,
            'Fake breakout: peak>0.3% then reverse>0.4%': lambda t: t['peak_then_reverse'] > 0.4,
            'Vol expansion >1.5x (volatility spike against)': lambda t: t['vol_expansion'] > 1.5,
            'Never below VWAP in 20 ticks': lambda t: t['ticks_to_below_vwap'] is None,
            'High green vol ratio (buyers>60%)': lambda t: t['sell_vol_ratio'] < 0.4,
            '5+ greens in first 10': lambda t: t['first10_green'] >= 5,
            '3+ greens in first 5': lambda t: t['first5_green'] >= 3,
            'First 3 ticks: max green body > 0.5%': lambda t: t['first3_max_green'] > 0.5,
            'First 5 ticks: all losing (pnl<0)': lambda t: t['first5_pnl'] < 0,
            'First 10 ticks: adverse > 0.3% & 4+ green': lambda t: t['max_adverse_b10'] < -0.3 and t['first10_green'] >= 4,
            # Combos
            'COMBO: first5_green>=3 + max_adverse<-0.3%': lambda t: t['first5_green'] >= 3 and t['max_adverse_b10'] < -0.3,
            'COMBO: never_below_vwap + first5_pnl<0': lambda t: t['ticks_to_below_vwap'] is None and t['first5_pnl'] < 0,
            'COMBO: sell_vol<0.4 + first10_green>=5': lambda t: t['sell_vol_ratio'] < 0.4 and t['first10_green'] >= 5,
            'COMBO: big_green_b3 + not_below_vwap': lambda t: t['first3_big_green'] and (t['ticks_to_below_vwap'] is None or t['ticks_to_below_vwap'] > 10),
            'COMBO: momentum<0.35 + vol_expand>1.3': lambda t: t['momentum_ratio'] < 0.35 and t['vol_expansion'] > 1.3,
        }

        out.write(f"  {'Pattern':<60} {'Total':>5} {'LoseRate':>9} {'AvgRet':>8} {'InLosers':>9}\n  "+"-"*95+"\n")
        pattern_results = []
        for name, filt in loss_patterns.items():
            matching = [t for t in top8 if filt(t)]
            if len(matching) < 5: continue
            lose_rate = sum(1 for t in matching if not t['win']) / len(matching) * 100
            avg_ret = np.mean([t['ret90'] for t in matching])
            in_losers = sum(1 for t in losers if filt(t)) / max(len(losers), 1) * 100
            pattern_results.append((lose_rate, name, len(matching), avg_ret, in_losers))

        pattern_results.sort(key=lambda x: -x[0])
        for lr, name, n, ar, il in pattern_results:
            out.write(f"  {name:<60} {n:>5} {lr:>8.1f}% {ar:>+7.3f}% {il:>8.1f}%\n")

        # ═══════════════════════════════════════════
        # 4. WINNER PATTERNS — what's consistently present?
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. WINNER PATTERNS — what's ALWAYS present in winners?\n"+"="*110+"\n")

        winner_patterns = {
            'First 3 ticks profitable (pnl>0)': lambda t: t['first3_pnl'] > 0,
            'First 5 ticks profitable (pnl>0)': lambda t: t['first5_pnl'] > 0,
            'First 10 ticks profitable (pnl>0.2%)': lambda t: t['first10_pnl'] > 0.2,
            'Momentum ratio > 0.55': lambda t: t['momentum_ratio'] > 0.55,
            'Below VWAP within 5 ticks': lambda t: t['ticks_to_below_vwap'] is not None and t['ticks_to_below_vwap'] <= 5,
            'Below VWAP within 10 ticks': lambda t: t['ticks_to_below_vwap'] is not None and t['ticks_to_below_vwap'] <= 10,
            'Sell vol ratio > 0.55': lambda t: t['sell_vol_ratio'] > 0.55,
            'Max adverse < 0.3% in first 10': lambda t: t['max_adverse_b10'] > -0.3,
            'First 5 green count <= 2': lambda t: t['first5_green'] <= 2,
            'First 10 green count <= 4': lambda t: t['first10_green'] <= 4,
            'Broke below b0 low in first 10': lambda t: t['broke_below_b0'],
            'No big green in first 3 (body<0.3%)': lambda t: t['first3_max_green'] < 0.3,
            # Combos
            'COMBO: first5_pnl>0 + momentum>0.5 + sell_vol>0.5': lambda t: t['first5_pnl'] > 0 and t['momentum_ratio'] > 0.5 and t['sell_vol_ratio'] > 0.5,
            'COMBO: below_vwap_5 + first5_pnl>0': lambda t: (t['ticks_to_below_vwap'] is not None and t['ticks_to_below_vwap'] <= 5) and t['first5_pnl'] > 0,
            'COMBO: first10_pnl>0.2 + green<=4 + sell_vol>0.5': lambda t: t['first10_pnl'] > 0.2 and t['first10_green'] <= 4 and t['sell_vol_ratio'] > 0.5,
        }

        out.write(f"  {'Pattern':<65} {'Total':>5} {'WinRate':>8} {'AvgRet':>8}\n  "+"-"*90+"\n")
        win_results = []
        for name, filt in winner_patterns.items():
            matching = [t for t in top8 if filt(t)]
            if len(matching) < 5: continue
            win_rate = sum(t['win'] for t in matching) / len(matching) * 100
            avg_ret = np.mean([t['ret90'] for t in matching])
            win_results.append((win_rate, name, len(matching), avg_ret))

        win_results.sort(key=lambda x: -x[0])
        for wr, name, n, ar in win_results:
            out.write(f"  {name:<65} {n:>5} {wr:>7.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════════
        # 5. DECISIVE DIVERGENCE: at which tick do W/L paths split?
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. DECISIVE DIVERGENCE — at which tick do winners/losers split?\n"+"="*110+"\n")
        out.write(f"  {'Tick':>6} {'WinPnl':>8} {'LosePnl':>8} {'Gap':>8} {'WinGreenCum':>12} {'LoseGreenCum':>13}\n  "+"-"*60+"\n")
        for tb in range(7, 30):
            w_pnl = [t['ticks'][tb-7]['pnl'] if tb-7 < len(t['ticks']) else 0 for t in winners]
            l_pnl = [t['ticks'][tb-7]['pnl'] if tb-7 < len(t['ticks']) else 0 for t in losers]
            w_gc = [sum(1 for tick in t['ticks'][:tb-6] if tick['green']) for t in winners]
            l_gc = [sum(1 for tick in t['ticks'][:tb-6] if tick['green']) for t in losers]
            gap = np.mean(w_pnl) - np.mean(l_pnl)
            marker = " *** SPLIT" if gap > 0.3 else ""
            out.write(f"  b{tb+1:>4} {np.mean(w_pnl):>+7.3f}% {np.mean(l_pnl):>+7.3f}% {gap:>+7.3f}% {np.mean(w_gc):>11.1f} {np.mean(l_gc):>12.1f}{marker}\n")

        # ═══════════════════════════════════════════
        # 6. MULTI-FACTOR SCORING SYSTEM
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. MULTI-FACTOR SCORING SYSTEM\n"+"="*110+"\n")

        # Build score from tick features (computed at b16, 10 ticks after entry)
        def compute_tick_score(t):
            score = 0
            # Factor 1: First 5 ticks P&L direction (+2 if profitable, -2 if losing >0.2%)
            if t['first5_pnl'] > 0.2: score += 3
            elif t['first5_pnl'] > 0: score += 1
            elif t['first5_pnl'] < -0.2: score -= 2
            # Factor 2: Momentum ratio (+2 if >0.55, -2 if <0.35)
            if t['momentum_ratio'] > 0.55: score += 2
            elif t['momentum_ratio'] < 0.35: score -= 2
            # Factor 3: VWAP (+2 if below within 5 ticks, -1 if never below in 20)
            if t['ticks_to_below_vwap'] is not None and t['ticks_to_below_vwap'] <= 5: score += 2
            elif t['ticks_to_below_vwap'] is None: score -= 1
            # Factor 4: Green candle control (+1 if <=2 in first 5, -1 if >=4)
            if t['first5_green'] <= 2: score += 1
            elif t['first5_green'] >= 4: score -= 1
            # Factor 5: Sell volume dominance (+1 if >0.55, -1 if <0.4)
            if t['sell_vol_ratio'] > 0.55: score += 1
            elif t['sell_vol_ratio'] < 0.4: score -= 1
            # Factor 6: No big green candle (+1 if max<0.3%, -2 if >0.5%)
            if t['first3_max_green'] < 0.3: score += 1
            elif t['first3_max_green'] > 0.5: score -= 2
            # Factor 7: Max adverse control (+1 if >-0.2%, -1 if <-0.5%)
            if t['max_adverse_b10'] > -0.2: score += 1
            elif t['max_adverse_b10'] < -0.5: score -= 1
            return score

        # Apply score to top-8
        for t in top8:
            t['tick_score'] = compute_tick_score(t)

        out.write(f"\n  Scoring formula (computed at b16, 10 ticks after entry):\n")
        out.write(f"    Factor 1: first5_pnl     (+3 if >0.2%, +1 if >0, -2 if <-0.2%)\n")
        out.write(f"    Factor 2: momentum_ratio  (+2 if >0.55, -2 if <0.35)\n")
        out.write(f"    Factor 3: VWAP position   (+2 if below in 5 ticks, -1 if never)\n")
        out.write(f"    Factor 4: green control   (+1 if <=2 greens, -1 if >=4)\n")
        out.write(f"    Factor 5: sell vol ratio  (+1 if >0.55, -1 if <0.4)\n")
        out.write(f"    Factor 6: big green check (+1 if max<0.3%, -2 if >0.5%)\n")
        out.write(f"    Factor 7: adverse control (+1 if >-0.2%, -1 if <-0.5%)\n")
        out.write(f"    Range: -10 to +11\n\n")

        # Score distribution: win rate by score bucket
        out.write(f"  Score -> Win Rate:\n")
        out.write(f"  {'Score':>7} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*30+"\n")
        for slo, shi in [(-10, -3), (-3, -1), (-1, 1), (1, 3), (3, 5), (5, 8), (8, 12)]:
            sub = [t for t in top8 if slo <= t['tick_score'] < shi]
            if len(sub) < 3: continue
            wr = sum(t['win'] for t in sub) / len(sub) * 100
            ar = np.mean([t['ret90'] for t in sub])
            out.write(f"  {f'{slo} to {shi-1}':>7} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════════
        # 7. ACTIONABLE RULES
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n7. ACTIONABLE RULES (if-then logic, all NO LOOKAHEAD)\n"+"="*110+"\n")

        rules = {
            'HOLD: tick_score >= 5 (strong)': lambda t: t['tick_score'] >= 5,
            'HOLD: tick_score >= 3': lambda t: t['tick_score'] >= 3,
            'HOLD: tick_score >= 1': lambda t: t['tick_score'] >= 1,
            'EXIT: tick_score <= -3': lambda t: t['tick_score'] <= -3,
            'EXIT: tick_score <= -1': lambda t: t['tick_score'] <= -1,
            'ADD: tick_score >= 5 + first10_pnl > 0.3': lambda t: t['tick_score'] >= 5 and t['first10_pnl'] > 0.3,
            'ADD: tick_score >= 3 + below_vwap_5': lambda t: t['tick_score'] >= 3 and t['ticks_to_below_vwap'] is not None and t['ticks_to_below_vwap'] <= 5,
        }

        out.write(f"  {'Rule':<55} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*80+"\n")
        for name, filt in rules.items():
            sub = [t for t in top8 if filt(t)]
            if len(sub) < 3: continue
            wr = sum(t['win'] for t in sub) / len(sub) * 100
            ar = np.mean([t['ret90'] for t in sub])
            out.write(f"  {name:<55} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════════
        # 8. SIZING with TICK SCORE (corrected — exit uses actual P&L)
        # ═══════════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n8. TICK-SCORE SIZING SIMULATION (corrected)\n"+"="*110+"\n")

        # Simulate: at b16 (10 ticks), compute tick_score, then ADD/HOLD/EXIT
        _BASE = 10000; _MARGIN = 5
        def sim_tick_sizing(add_thresh, exit_thresh, add_mult=2.0, n_pos=8, base=_BASE, margin=_MARGIN):
            total_pnl = 0; day_wins = 0; days = 0
            for d in dates:
                pool = sorted(by_date[d], key=lambda x: -x['score'])[:n_pos]
                if not pool: continue
                days += 1; day_pnl = 0
                for t in pool:
                    ts = compute_tick_score(t)
                    if ts <= exit_thresh:
                        # EXIT at b16: actual P&L at that point
                        pnl = base * margin * t['first10_pnl'] / 100
                    elif ts >= add_thresh:
                        # ADD: 1x up to b16 + add_mult from b16 to b90
                        remaining = t['ret90'] - t['first10_pnl']
                        pnl = base * margin * (t['first10_pnl'] + remaining * add_mult) / 100
                    else:
                        pnl = base * margin * t['ret90'] / 100
                    day_pnl += pnl
                total_pnl += day_pnl
                if day_pnl > 0: day_wins += 1
            roc = total_pnl / (base * n_pos) * 100
            return roc, day_wins / max(days, 1) * 100

        out.write(f"  {'Strategy':<50} {'ROC':>8} {'DayWin':>7}\n  "+"-"*70+"\n")
        # Baseline
        roc, dw = sim_tick_sizing(999, -999)  # no sizing
        out.write(f"  {'No sizing (baseline)':<50} {roc:>+7.1f}% {dw:>6.1f}%\n\n")

        for add_t in [3, 5, 7]:
            for exit_t in [-3, -1, 0]:
                for mult in [1.5, 2.0, 3.0]:
                    roc, dw = sim_tick_sizing(add_t, exit_t, mult)
                    name = f"ADD({mult}x) if score>={add_t}, EXIT if score<={exit_t}"
                    out.write(f"  {name:<50} {roc:>+7.1f}% {dw:>6.1f}%\n")

        # ADD only (no exit)
        out.write(f"\n  ADD only (no exit):\n")
        for add_t in [3, 5]:
            for mult in [2.0, 3.0]:
                roc, dw = sim_tick_sizing(add_t, -999, mult)
                out.write(f"  ADD({mult}x) if score>={add_t}:{'':>25} {roc:>+7.1f}% {dw:>6.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__ == '__main__': main()
