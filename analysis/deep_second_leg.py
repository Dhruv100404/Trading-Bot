"""
DEEP SECOND-LEG ANALYSIS — The 80% Win Pattern
=================================================
After a gap-up stock reverses and hits bottom, BUY the bounce.

STRICT NO-LOOKAHEAD PROTOCOL:
  - We do NOT know where the MFE (bottom) is in advance
  - We must detect "reversal completed" from observable real-time signals
  - Signals: price starts going UP after going DOWN, VWAP cross upward,
    consecutive green candles, momentum shift

This script:
  1. For each gap-up stock, track the FULL minute-by-minute sell path
  2. At each minute, compute: can we DETECT the bottom has been hit?
  3. Test real-time BUY triggers (no lookahead)
  4. Also: deep cherry-pick analysis — top 30, why losers fail, what winners have
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_second_leg.txt'
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
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = min(len(bkts), 375)
                bkt = np.zeros((375,7), dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],'bkt':bkt,
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],'date':r['date'],
                })

    dates = sorted(by_date.keys())
    print(f"Loaded {sum(len(v) for v in by_date.values())} records in {time.time()-t0:.1f}s")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("DEEP SECOND-LEG + CHERRY-PICK ANALYSIS\n")
        out.write(f"Dates: {len(dates)}, Stocks: {len(liquid)}\n")
        out.write("ALL triggers are NO-LOOKAHEAD: computed from data BEFORE entry\n\n")

        # ══════════════════════════════════════════════════════════
        # PART A: SECOND-LEG BUY — Real-time trigger detection
        # ══════════════════════════════════════════════════════════
        out.write("="*110+"\nPART A: SECOND-LEG BUY — REAL-TIME TRIGGERS (no lookahead)\n"+"="*110+"\n\n")

        # For each gap-up stock, track sell path and find NO-LOOKAHEAD buy triggers
        all_triggers = []

        for d in dates:
            for s in by_date[d]:
                if s['gap'] <= 0.5: continue
                bkt = s['bkt']
                sell_entry = bkt[6,O]
                if sell_entry <= 0: continue

                # Track price path from sell entry
                running_min = sell_entry
                running_min_bucket = 6
                sell_profit_from_entry = 0

                for b in range(7, 200):
                    if bkt[b,C] <= 0: continue

                    # Update running minimum (the "bottom so far" — this is NO lookahead)
                    if bkt[b,L] < running_min:
                        running_min = bkt[b,L]
                        running_min_bucket = b
                        sell_profit_from_entry = (sell_entry - running_min) / sell_entry * 100

                    current_price = bkt[b,C]
                    bounce_from_min = (current_price - running_min) / running_min * 100 if running_min > 0 else 0

                    # ── NO-LOOKAHEAD BUY TRIGGERS ──
                    # We don't know if the bottom is in. We detect patterns that SUGGEST it.

                    triggers_fired = []

                    # T1: Price bounced 0.3%+ from running minimum
                    if bounce_from_min >= 0.3 and sell_profit_from_entry >= 0.5:
                        triggers_fired.append('bounce_0.3')

                    # T2: Price bounced 0.5%+ from running minimum
                    if bounce_from_min >= 0.5 and sell_profit_from_entry >= 0.5:
                        triggers_fired.append('bounce_0.5')

                    # T3: 2 consecutive green candles after a drop
                    if b >= 9 and sell_profit_from_entry >= 0.3:
                        if bkt[b-1,C]>bkt[b-1,O] and bkt[b,C]>bkt[b,O]:
                            triggers_fired.append('2green')

                    # T4: 3 consecutive green candles
                    if b >= 10 and sell_profit_from_entry >= 0.3:
                        if bkt[b-2,C]>bkt[b-2,O] and bkt[b-1,C]>bkt[b-1,O] and bkt[b,C]>bkt[b,O]:
                            triggers_fired.append('3green')

                    # T5: Price crosses above VWAP (from below)
                    if bkt[b,VW] > 0 and bkt[b,C] > bkt[b,VW] and sell_profit_from_entry >= 0.3:
                        if b > 7 and bkt[b-1,C] < bkt[b-1,VW]:  # was below VWAP last bucket
                            triggers_fired.append('vwap_cross_up')

                    # T6: Bounce from min + 2 green candles
                    if bounce_from_min >= 0.3 and sell_profit_from_entry >= 0.5:
                        if b >= 9 and bkt[b-1,C]>bkt[b-1,O] and bkt[b,C]>bkt[b,O]:
                            triggers_fired.append('bounce+2green')

                    # T7: Volume spike on green candle (institutional buying)
                    if b >= 20 and bkt[b,C] > bkt[b,O] and sell_profit_from_entry >= 0.3:
                        avg_vol = float(np.mean(bkt[max(b-10,7):b, V]))
                        if avg_vol > 0 and bkt[b,V] > avg_vol * 2:
                            triggers_fired.append('vol_spike_green')

                    # T8: Bounce + price above b0 VWAP (strong recovery)
                    if bounce_from_min >= 0.3 and bkt[b,VW] > 0 and bkt[b,C] > bkt[b,VW]:
                        triggers_fired.append('bounce+above_vwap')

                    if not triggers_fired: continue

                    # ── BUY entry at NEXT bucket open (no lookahead) ──
                    buy_entry_b = b + 1
                    if buy_entry_b >= 370 or bkt[buy_entry_b, O] <= 0: continue
                    buy_entry = bkt[buy_entry_b, O]

                    # Compute returns at various hold periods
                    for trigger in triggers_fired:
                        for hold in [10, 15, 20, 30, 45, 60]:
                            exit_b = min(buy_entry_b + hold, 374)
                            if bkt[exit_b, C] <= 0: continue
                            ret = (bkt[exit_b, C] - buy_entry) / buy_entry * 100 - COST

                            all_triggers.append({
                                'trigger': trigger,
                                'hold': hold,
                                'ret': ret,
                                'buy_bucket': buy_entry_b,
                                'sell_profit': sell_profit_from_entry,
                                'bounce': bounce_from_min,
                                'gap': s['gap'],
                                'sym': s['sym'],
                                'date': d,
                                'buy_entry': buy_entry,
                                # Features at trigger time (for scoring)
                                'time_since_min': b - running_min_bucket,
                            })

                    break  # only FIRST trigger per stock per day (avoid duplicate entries)

        out.write(f"Total trigger entries: {len(all_triggers)}\n\n")

        # A1. Trigger comparison
        out.write("A1. TRIGGER COMPARISON (which signal best detects the bottom?)\n"+"-"*80+"\n")
        trigger_names = sorted(set(t['trigger'] for t in all_triggers))
        out.write(f"  {'Trigger':<25}")
        for hold in [10,15,20,30,45,60]:
            out.write(f"  {hold}m".rjust(12))
        out.write("\n  "+"-"*100+"\n")
        for tname in trigger_names:
            out.write(f"  {tname:<25}")
            for hold in [10,15,20,30,45,60]:
                sub = [t['ret'] for t in all_triggers if t['trigger']==tname and t['hold']==hold]
                if len(sub) < 30:
                    out.write(f"{'--':>12}")
                else:
                    wr = sum(1 for r in sub if r>0)/len(sub)*100
                    ar = np.mean(sub)
                    out.write(f" {wr:.0f}%/{ar:+.2f}%".rjust(12))
            out.write(f"  (n={len([t for t in all_triggers if t['trigger']==tname and t['hold']==15])})\n")

        # A2. Best trigger by sell_profit range (how deep was the reversal before buy?)
        out.write(f"\nA2. BEST TRIGGER x SELL PROFIT DEPTH (deeper reversal = stronger bounce?)\n"+"-"*80+"\n")
        best_trigger = 'bounce+2green'  # will determine from data
        # Find which trigger has best avg ret at 30m hold
        best_ret = -99
        for tname in trigger_names:
            sub = [t['ret'] for t in all_triggers if t['trigger']==tname and t['hold']==30]
            if len(sub) >= 30:
                ar = np.mean(sub)
                if ar > best_ret:
                    best_ret = ar; best_trigger = tname
        out.write(f"  Best trigger overall: {best_trigger} (avgRet@30m = {best_ret:+.3f}%)\n\n")

        for tname in [best_trigger, 'bounce_0.3', '2green', '3green', 'vwap_cross_up']:
            out.write(f"\n  {tname} by sell depth (hold=30m):\n")
            out.write(f"    {'SellProfit':>12} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
            for slo,shi,slbl in [(0.3,0.5,'0.3-0.5%'),(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,99,'3%+')]:
                sub = [t['ret'] for t in all_triggers if t['trigger']==tname and t['hold']==30 and slo<=t['sell_profit']<shi]
                if len(sub)<20: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"    {slbl:>12} {len(sub):>6} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")

        # A3. Timing: at which bucket do triggers fire?
        out.write(f"\nA3. TRIGGER TIMING: when do buy triggers fire?\n"+"-"*80+"\n")
        out.write(f"  {'Bucket Range':>15} {'N':>6} {'Win%@30m':>9} {'AvgRet':>8}\n")
        for blo,bhi,blbl in [(7,20,'b8-b20 (9:22-9:35)'),(20,40,'b21-b40 (9:35-9:55)'),
                              (40,60,'b41-b60 (9:55-10:15)'),(60,90,'b61-b90 (10:15-10:44)'),
                              (90,150,'b91-b150 (10:44-11:45)'),(150,200,'b151-b200 (11:45-12:35)')]:
            sub = [t for t in all_triggers if t['hold']==30 and blo<=t['buy_bucket']<bhi]
            if len(sub)<30: continue
            wr = sum(1 for t in sub if t['ret']>0)/len(sub)*100
            ar = np.mean([t['ret'] for t in sub])
            out.write(f"  {blbl:>15} {len(sub):>6} {wr:>8.1f}% {ar:>+7.3f}%\n")

        # A4. Gap size effect on second-leg buy
        out.write(f"\nA4. GAP SIZE effect on second-leg buy (hold=30m, best trigger)\n"+"-"*80+"\n")
        for glo,ghi,glbl in [(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,5,'3-5%'),(5,100,'5%+')]:
            sub = [t for t in all_triggers if t['trigger']==best_trigger and t['hold']==30 and glo<=t['gap']<ghi]
            if len(sub)<20: continue
            wr = sum(1 for t in sub if t['ret']>0)/len(sub)*100
            ar = np.mean([t['ret'] for t in sub])
            out.write(f"  Gap {glbl:>8}: N={len(sub):>5}, Win={wr:.1f}%, AvgRet={ar:+.3f}%\n")

        # ══════════════════════════════════════════════════════════
        # PART B: SELL CHERRY-PICK — TOP 30 DEEP ANALYSIS
        # ══════════════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nPART B: SELL CHERRY-PICK — WHY TOP-8 LOSERS FAIL\n"+"="*110+"\n")

        all_top30 = []
        for d in dates:
            gapup = [s for s in by_date[d] if s['gap'] > 0.5]
            if len(gapup) < 3: continue
            gapup.sort(key=lambda x: -x['gap'])

            for rank, s in enumerate(gapup[:30]):
                bkt = s['bkt']
                entry = bkt[6,O]
                if entry <= 0 or bkt[65,C] <= 0: continue
                ret66 = (entry - bkt[65,C])/entry*100 - COST
                ret90 = (entry - bkt[89,C])/entry*100 - COST if bkt[89,C]>0 else ret66

                # Features
                close_pos_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sell_pressure = 1 - close_pos_sum/6
                momentum = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])

                # MFE/MAE for sell
                mfe = (entry - float(np.min(bkt[6:66,L])))/entry*100
                mae = (float(np.max(bkt[6:66,H])) - entry)/entry*100

                # b0 features
                b0_rng = (bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                b0_ret = (bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0

                # Volume features
                vol6 = [float(bkt[i,V]) for i in range(6)]
                total_v6 = sum(vol6)
                vol_ratio = vol6[0]/np.mean(vol6[1:6]) if np.mean(vol6[1:6])>0 else 0

                # At what bucket did MAX ADVERSE happen? (worst point for sell = highest price)
                worst_bucket = 6
                running_max = entry
                for b in range(7,66):
                    if bkt[b,H] > running_max:
                        running_max = bkt[b,H]; worst_bucket = b

                # At what bucket did MFE happen? (best point for sell = lowest price)
                best_bucket = 6
                running_min = entry
                for b in range(7,66):
                    if bkt[b,L] > 0 and bkt[b,L] < running_min:
                        running_min = bkt[b,L]; best_bucket = b

                all_top30.append({
                    'rank':rank+1,'sym':s['sym'],'gap':s['gap'],'date':d,
                    'ret66':ret66,'ret90':ret90,'mfe':mfe,'mae':mae,
                    'sell_pressure':sell_pressure,'momentum':momentum,'n_red':n_red,
                    'b0_rng':b0_rng,'b0_ret':b0_ret,'vol_ratio':vol_ratio,
                    'price':s['price'],'f5vol_rs':s['f5vol_rs'],
                    'is_top8':rank<8,'win':ret66>0,
                    'worst_bucket':worst_bucket,'best_bucket':best_bucket,
                })

        out.write(f"\nTotal top-30 entries: {len(all_top30)}\n")

        # B1. Rank vs win rate (expanded)
        out.write(f"\nB1. WIN RATE BY RANK with feature averages\n"+"-"*90+"\n")
        out.write(f"  {'Rank':>4} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'AvgGap':>8} {'SP':>5} {'Mom':>7} {'nRed':>5}\n")
        out.write("  "+"-"*55+"\n")
        for r in range(1,31):
            sub = [t for t in all_top30 if t['rank']==r]
            if not sub: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret66'] for t in sub])
            ag = np.mean([t['gap'] for t in sub])
            sp = np.mean([t['sell_pressure'] for t in sub])
            mom = np.mean([t['momentum'] for t in sub])
            nr = np.mean([t['n_red'] for t in sub])
            out.write(f"  {r:>4} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}% {ag:>7.2f}% {sp:>4.2f} {mom:>+6.2f}% {nr:>4.1f}\n")

        # B2. Top-8 winners vs losers — feature deep dive
        t8 = [t for t in all_top30 if t['is_top8']]
        t8_win = [t for t in t8 if t['win']]
        t8_lose = [t for t in t8 if not t['win']]

        out.write(f"\nB2. TOP-8 WINNERS vs LOSERS — detailed feature comparison\n"+"-"*90+"\n")
        out.write(f"  Top-8: {len(t8)} trades, {len(t8_win)} wins ({len(t8_win)/len(t8)*100:.1f}%), {len(t8_lose)} losses\n\n")

        feats = ['gap','price','sell_pressure','momentum','n_red','b0_rng','b0_ret','vol_ratio','f5vol_rs','mfe','mae']
        out.write(f"  {'Feature':<15} {'Winners':>12} {'Losers':>12} {'Delta':>10} {'Meaning':>25}\n")
        out.write("  "+"-"*80+"\n")
        for f in feats:
            wv = np.mean([t[f] for t in t8_win])
            lv = np.mean([t[f] for t in t8_lose])
            delta = wv - lv
            meaning = ""
            if f == 'gap': meaning = "smaller gap wins" if delta < 0 else "bigger gap wins"
            elif f == 'sell_pressure': meaning = "more sellers win" if delta > 0 else "more buyers win"
            elif f == 'momentum': meaning = "more drop wins" if delta < 0 else "less drop wins"
            elif f == 'n_red': meaning = "more red wins" if delta > 0 else "less red wins"
            elif f == 'price': meaning = "cheaper wins" if delta < 0 else "expensive wins"
            elif f == 'mfe': meaning = "higher MFE = deeper reversal"
            elif f == 'mae': meaning = "lower MAE = less risk"
            out.write(f"  {f:<15} {wv:>12.3f} {lv:>12.3f} {delta:>+10.3f} {meaning:>25}\n")

        # B3. WHY losers fail — classification
        out.write(f"\nB3. HOW DO TOP-8 LOSERS FAIL?\n"+"-"*90+"\n")
        type_never = [t for t in t8_lose if t['mfe']<0.3]  # never really reversed
        type_bounce = [t for t in t8_lose if t['mfe']>=0.3 and t['mfe']<1.0]  # reversed then bounced back
        type_deep_bounce = [t for t in t8_lose if t['mfe']>=1.0]  # reversed a LOT then bounced

        out.write(f"  Type 1: NEVER REVERSED (MFE < 0.3%): {len(type_never)} ({len(type_never)/max(len(t8_lose),1)*100:.0f}%)\n")
        if type_never:
            out.write(f"    Avg gap: {np.mean([t['gap'] for t in type_never]):.2f}%, Avg SP: {np.mean([t['sell_pressure'] for t in type_never]):.3f}\n")
            out.write(f"    Avg momentum: {np.mean([t['momentum'] for t in type_never]):+.3f}%, Avg n_red: {np.mean([t['n_red'] for t in type_never]):.1f}\n\n")

        out.write(f"  Type 2: REVERSED THEN BOUNCED (MFE 0.3-1%): {len(type_bounce)} ({len(type_bounce)/max(len(t8_lose),1)*100:.0f}%)\n")
        if type_bounce:
            out.write(f"    Avg MFE: {np.mean([t['mfe'] for t in type_bounce]):.3f}% (had profit, then lost it)\n")
            out.write(f"    Avg worst_bucket: b{np.mean([t['worst_bucket'] for t in type_bounce]):.0f}\n\n")

        out.write(f"  Type 3: DEEP REVERSAL THEN HUGE BOUNCE (MFE > 1%): {len(type_deep_bounce)} ({len(type_deep_bounce)/max(len(t8_lose),1)*100:.0f}%)\n")
        if type_deep_bounce:
            out.write(f"    Avg MFE: {np.mean([t['mfe'] for t in type_deep_bounce]):.3f}% (was very profitable, then gave it ALL back)\n")
            out.write(f"    THIS is where trailing stop would help most\n\n")

        # B4. Missed winners: rank 9-30 stocks that beat top-8 losers
        out.write(f"\nB4. MISSED WINNERS — what rank 9-30 winners look like vs top-8 losers\n"+"-"*90+"\n")
        r9_30_winners = [t for t in all_top30 if 9<=t['rank']<=30 and t['win']]
        out.write(f"  Top-8 losers: {len(t8_lose)}, Rank 9-30 winners: {len(r9_30_winners)}\n\n")
        if r9_30_winners and t8_lose:
            out.write(f"  {'Feature':<15} {'T8 Losers':>12} {'R9-30 Winners':>14}\n")
            out.write("  "+"-"*45+"\n")
            for f in feats:
                lv = np.mean([t[f] for t in t8_lose])
                rv = np.mean([t[f] for t in r9_30_winners])
                out.write(f"  {f:<15} {lv:>12.3f} {rv:>14.3f}\n")

        # B5. IMPROVED SCORING: test v2 formula on top-30 pool
        out.write(f"\nB5. SCORING COMPARISON on top-30 pool\n"+"-"*90+"\n")

        scorers = {
            'S0: gap only (current)': lambda t: t['gap'],
            'S1: gap*(1-sp)*(mom<-.5?1.4:mom<0?1.1:.7) [v2]': lambda t: t['gap']*(1-t['sell_pressure'] if t['sell_pressure']<1 else 0.01)*(1.4 if t['momentum']<-0.5 else 1.1 if t['momentum']<0 else 0.7),
            'S2: gap*sp*(mom<-.5?1.4:mom<0?1.1:.7)': lambda t: t['gap']*t['sell_pressure']*(1.4 if t['momentum']<-0.5 else 1.1 if t['momentum']<0 else 0.7),
            'S3: gap*sp*(nred>=4?1.5:nred>=3?1.2:1)': lambda t: t['gap']*t['sell_pressure']*(1.5 if t['n_red']>=4 else 1.2 if t['n_red']>=3 else 1),
            'S4: gap*sp*(mom<0?1.3:.7)*(b0rng>2?1.2:1)': lambda t: t['gap']*t['sell_pressure']*(1.3 if t['momentum']<0 else 0.7)*(1.2 if t['b0_rng']>2 else 1),
            'S5: gap*(sp>.5?1:.3)*(mom<0?1.3:.7)': lambda t: (t['gap'] if t['sell_pressure']>0.5 else t['gap']*0.3)*(1.3 if t['momentum']<0 else 0.7),
        }

        for s_name, scorer in scorers.items():
            day_total = 0; day_wins = 0; trades = 0; win_trades = 0
            for d in dates:
                day_pool = [t for t in all_top30 if t['date']==d]
                if len(day_pool) < 3: continue
                for t in day_pool: t['_sc'] = scorer(t)
                day_pool.sort(key=lambda x: -x['_sc'])
                picks = day_pool[:8]
                dr = sum(t['ret66'] for t in picks)
                day_total += dr
                if dr > 0: day_wins += 1
                trades += len(picks)
                win_trades += sum(1 for t in picks if t['win'])
            n_days = len(set(t['date'] for t in all_top30))
            dw = day_wins/max(n_days,1)*100
            tw = win_trades/max(trades,1)*100
            out.write(f"  {s_name:<55} total={day_total:>+7.1f}%  dayW={dw:.1f}%  trdW={tw:.1f}%\n")

        # B6. REJECT FILTER on top-30 pool then pick top-8
        out.write(f"\nB6. REJECT FILTERS + best scorer\n"+"-"*90+"\n")
        rejects = {
            'none': lambda t: False,
            'sp<0.4': lambda t: t['sell_pressure']<0.4,
            'mom>0.5': lambda t: t['momentum']>0.5,
            'nred<=1': lambda t: t['n_red']<=1,
            'gap>15': lambda t: t['gap']>15,
            'sp<0.4 | nred<=1&sp<0.45': lambda t: t['sell_pressure']<0.4 or (t['n_red']<=1 and t['sell_pressure']<0.45),
            'sp<0.45 | mom>0.3': lambda t: t['sell_pressure']<0.45 or t['momentum']>0.3,
        }
        # Use the v2 scorer
        v2_scorer = lambda t: t['gap']*t['sell_pressure']*(1.4 if t['momentum']<-0.5 else 1.1 if t['momentum']<0 else 0.7)

        for r_name, reject in rejects.items():
            day_total=0; day_wins=0; trades=0; wt=0
            for d in dates:
                pool = [t for t in all_top30 if t['date']==d and not reject(t)]
                if len(pool)<1: continue
                for t in pool: t['_sc'] = v2_scorer(t)
                pool.sort(key=lambda x:-x['_sc'])
                picks = pool[:8]
                dr = sum(t['ret66'] for t in picks)
                day_total+=dr; trades+=len(picks); wt+=sum(1 for t in picks if t['win'])
                if dr>0: day_wins+=1
            n_d = len(dates)
            out.write(f"  R:{r_name:<40} total={day_total:>+7.1f}%  dayW={day_wins/max(n_d,1)*100:.1f}%  trdW={wt/max(trades,1)*100:.1f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
