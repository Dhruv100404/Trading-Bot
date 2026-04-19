"""
MEGA ALL-PATTERNS ANALYSIS — Single load, every analysis
==========================================================
Loads 1268 liquid stocks ONCE, then runs:
  1. Deep BUY patterns (gap-down reversal)
  2. All-day entry patterns (every 30-min window)
  3. Dynamic exit signals (VWAP cross, 3-green, momentum stall)
  4. All trailing stop variants (fixed, stepped, time-decay, winner-only)
  5. Deeper SELL patterns (hidden features)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'mega_all_patterns.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
MAX_B = 375  # full day

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    # ═══ SINGLE LOAD: full day data for all liquid stocks ═══
    print("Loading full-day data for 1268 liquid stocks...")
    by_date_all = defaultdict(list)  # ALL stocks (for market context)
    by_date_gapup = defaultdict(list)  # gap > 0.5% (sell candidates)
    by_date_gapdn = defaultdict(list)  # gap < -0.5% (buy candidates)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = min(len(bkts), MAX_B)
                bkt = np.zeros((MAX_B, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)

                rec = {'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                       'bkt':bkt,'f5range':r.get('f5Range',0),'date':r['date']}
                by_date_all[r['date']].append(rec)
                if r['gapPct'] > 0.5: by_date_gapup[r['date']].append(rec)
                if r['gapPct'] < -0.5: by_date_gapdn[r['date']].append(rec)

    dates = sorted(by_date_all.keys())
    n_all = sum(len(v) for v in by_date_all.values())
    n_up = sum(len(v) for v in by_date_gapup.values())
    n_dn = sum(len(v) for v in by_date_gapdn.values())
    print(f"Loaded {n_all} total, {n_up} gap-up, {n_dn} gap-down across {len(dates)} days in {time.time()-t0:.1f}s")

    # Helper: extract features from first N buckets
    def extract_sell_features(bkt, entry_start=0, entry_end=5):
        entry = bkt[entry_end+1, O]
        if entry <= 0: return None
        b0o = bkt[entry_start, O]
        if b0o <= 0: return None
        close_pos_sum = 0; n_red = 0
        for i in range(entry_start, entry_end+1):
            rng = bkt[i,H]-bkt[i,L]
            close_pos_sum += (bkt[i,C]-bkt[i,L])/rng if rng>0 else 0.5
            if bkt[i,C] < bkt[i,O]: n_red += 1
        n_bkts = entry_end - entry_start + 1
        sell_pressure = 1 - close_pos_sum/n_bkts
        momentum = (bkt[entry_end,C]-bkt[entry_start,O])/bkt[entry_start,O]*100
        exhaust = (bkt[entry_start,H]-bkt[entry_start,C])/(bkt[entry_start,H]-bkt[entry_start,L]) if bkt[entry_start,H]>bkt[entry_start,L] else 0.5
        return {'entry':entry,'sell_pressure':sell_pressure,'momentum':momentum,'n_red':n_red,'exhaust':exhaust}

    def extract_buy_features(bkt, entry_start=0, entry_end=5):
        entry = bkt[entry_end+1, O]
        if entry <= 0: return None
        b0o = bkt[entry_start, O]
        if b0o <= 0: return None
        close_pos_sum = 0; n_green = 0
        for i in range(entry_start, entry_end+1):
            rng = bkt[i,H]-bkt[i,L]
            close_pos_sum += (bkt[i,C]-bkt[i,L])/rng if rng>0 else 0.5
            if bkt[i,C] > bkt[i,O]: n_green += 1
        n_bkts = entry_end - entry_start + 1
        buy_pressure = close_pos_sum/n_bkts
        momentum = (bkt[entry_end,C]-bkt[entry_start,O])/bkt[entry_start,O]*100
        exhaust_buy = (bkt[entry_start,C]-bkt[entry_start,L])/(bkt[entry_start,H]-bkt[entry_start,L]) if bkt[entry_start,H]>bkt[entry_start,L] else 0.5
        return {'entry':entry,'buy_pressure':buy_pressure,'momentum':momentum,'n_green':n_green,'exhaust_buy':exhaust_buy}

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write(f"MEGA ALL-PATTERNS ANALYSIS\n")
        out.write(f"Data: {n_all} total, {n_up} gap-up, {n_dn} gap-down, {len(dates)} days\n")
        out.write(f"Full day: {MAX_B} buckets per stock (9:15 AM - 3:30 PM)\n\n")

        # ══════════════════════════════════════════════════════════════
        # PART 1: DEEP BUY PATTERNS
        # ══════════════════════════════════════════════════════════════
        out.write("="*110+"\nPART 1: DEEP BUY PATTERNS (gap-down reversal)\n"+"="*110+"\n")

        # 1a. BUY baseline by gap size x exit bucket
        out.write("\n1a. BUY win rate by gap size x exit bucket\n"+"-"*80+"\n")
        exit_bkts = [19,29,44,59,65,89]
        gap_dn_ranges = [(-1,-0.5),(-2,-1),(-3,-2),(-5,-3),(-100,-5)]
        out.write(f"  {'Gap':>8}")
        for eb in exit_bkts:
            h=9+(15+eb)//60; m=(15+eb)%60
            out.write(f"  b{eb+1}({h}:{m:02d})".rjust(12))
        out.write("\n")
        for ghi,glo in gap_dn_ranges:
            glbl = f"{abs(ghi)}-{abs(glo)}%"
            recs = []
            for d in dates:
                for s in by_date_gapdn.get(d,[]):
                    if ghi<=s['gap']<glo:
                        entry = s['bkt'][6,O]
                        if entry>0: recs.append(s)
            if len(recs)<20: continue
            out.write(f"  {glbl:>8}")
            for eb in exit_bkts:
                rs = [(s['bkt'][eb,C]-s['bkt'][6,O])/s['bkt'][6,O]*100-COST for s in recs if s['bkt'][eb,C]>0 and s['bkt'][6,O]>0]
                if not rs: out.write(f"{'--':>12}"); continue
                wr = sum(1 for r in rs if r>0)/len(rs)*100
                ar = np.mean(rs)
                out.write(f"  {wr:.0f}%/{ar:+.2f}%".rjust(12))
            out.write("\n")

        # 1b. BUY feature patterns
        out.write("\n1b. BUY feature patterns (exit at b45)\n"+"-"*80+"\n")
        buy_recs = []
        for d in dates:
            for s in by_date_gapdn.get(d,[]):
                feat = extract_buy_features(s['bkt'])
                if feat is None: continue
                ret45 = (s['bkt'][44,C]-feat['entry'])/feat['entry']*100-COST if s['bkt'][44,C]>0 else None
                ret66 = (s['bkt'][65,C]-feat['entry'])/feat['entry']*100-COST if s['bkt'][65,C]>0 else None
                if ret45 is None: continue
                buy_recs.append({**feat, 'gap':s['gap'],'abs_gap':abs(s['gap']),
                                 'ret45':ret45,'ret66':ret66,'sym':s['sym'],'price':s['price']})

        out.write(f"  Total BUY records: {len(buy_recs)}\n\n")
        buy_patterns = {
            'buy_pressure>0.55': lambda r: r['buy_pressure']>0.55,
            'buy_pressure>0.60': lambda r: r['buy_pressure']>0.60,
            'n_green>=3': lambda r: r['n_green']>=3,
            'n_green>=4': lambda r: r['n_green']>=4,
            'momentum>0 (bouncing)': lambda r: r['momentum']>0,
            'momentum>0.3%': lambda r: r['momentum']>0.3,
            'exhaust_buy>0.6 (b0 close near high)': lambda r: r['exhaust_buy']>0.6,
            'gap<-2%': lambda r: r['gap']<-2,
            'gap<-3%': lambda r: r['gap']<-3,
            'gap<-2% + buy_pressure>0.55': lambda r: r['gap']<-2 and r['buy_pressure']>0.55,
            'gap<-2% + n_green>=3': lambda r: r['gap']<-2 and r['n_green']>=3,
            'gap<-2% + momentum>0': lambda r: r['gap']<-2 and r['momentum']>0,
            'gap<-2% + bp>0.55 + mom>0': lambda r: r['gap']<-2 and r['buy_pressure']>0.55 and r['momentum']>0,
            'gap<-3% + bp>0.55 + mom>0': lambda r: r['gap']<-3 and r['buy_pressure']>0.55 and r['momentum']>0,
            'gap<-2% + bp>0.55 + ngrn>=3 + mom>0': lambda r: r['gap']<-2 and r['buy_pressure']>0.55 and r['n_green']>=3 and r['momentum']>0,
            'gap<-1% + bp>0.60 + ngrn>=4': lambda r: r['gap']<-1 and r['buy_pressure']>0.60 and r['n_green']>=4,
            'gap<-2% + exhaust_buy>0.6 + bp>0.55': lambda r: r['gap']<-2 and r['exhaust_buy']>0.6 and r['buy_pressure']>0.55,
            'gap<-3% + bp>0.60': lambda r: r['gap']<-3 and r['buy_pressure']>0.60,
        }
        rows = []
        for name,filt in buy_patterns.items():
            sub = [r for r in buy_recs if filt(r)]
            if len(sub)<20: continue
            wr = sum(1 for r in sub if r['ret45']>0)/len(sub)*100
            ar = np.mean([r['ret45'] for r in sub])
            rows.append((ar,name,len(sub),wr))
        rows.sort(key=lambda x:-x[0])
        out.write(f"  {'Pattern':<55} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*80+"\n")
        for ar,name,n,wr in rows:
            out.write(f"  {name:<55} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # 1c. BUY scoring formulas
        out.write("\n1c. BUY scoring (cherry-pick top-8 gap-down, exit b45)\n"+"-"*80+"\n")
        buy_by_date = defaultdict(list)
        for r in buy_recs: buy_by_date[r.get('date', r['sym'][:10])].append(r)
        # Need dates — re-extract
        buy_by_date2 = defaultdict(list)
        for d in dates:
            for s in by_date_gapdn.get(d,[]):
                feat = extract_buy_features(s['bkt'])
                if feat is None: continue
                ret45 = (s['bkt'][44,C]-feat['entry'])/feat['entry']*100-COST if s['bkt'][44,C]>0 else None
                if ret45 is None: continue
                buy_by_date2[d].append({**feat,'gap':s['gap'],'abs_gap':abs(s['gap']),
                                        'ret45':ret45,'price':s['price']})

        def sim_buy(scorer, n_pos=8, exit_key='ret45'):
            total=0; dw=0; trades=0; wt=0; active=0
            for d in dates:
                pool = buy_by_date2.get(d,[])
                if len(pool)<1: continue
                for s in pool: s['_sc'] = scorer(s)
                pool.sort(key=lambda x:-x['_sc'])
                picks = pool[:n_pos]
                rs = [s[exit_key] for s in picks if s[exit_key] is not None]
                if not rs: continue
                active+=1; dr=sum(rs); trades+=len(rs); wt+=sum(1 for r in rs if r>0)
                total+=dr
                if dr>0: dw+=1
            if trades==0: return None
            return (total, active, dw/max(active,1)*100, wt/max(trades,1)*100, trades)

        buy_scorers = {
            'abs_gap': lambda s: s['abs_gap'],
            'abs_gap*bp': lambda s: s['abs_gap']*s['buy_pressure'],
            'abs_gap*(bp>.55?1:.3)*(mom>0?1.3:.7)': lambda s: (s['abs_gap'] if s['buy_pressure']>0.55 else s['abs_gap']*0.3)*(1.3 if s['momentum']>0 else 0.7),
            'abs_gap*bp*(mom>.3?1.4:mom>0?1.1:.7)': lambda s: s['abs_gap']*s['buy_pressure']*(1.4 if s['momentum']>0.3 else 1.1 if s['momentum']>0 else 0.7),
            'abs_gap*(bp>.6?1.5:bp>.5?1:.4)*(mom>0?1.3:.7)': lambda s: s['abs_gap']*(1.5 if s['buy_pressure']>0.6 else 1 if s['buy_pressure']>0.5 else 0.4)*(1.3 if s['momentum']>0 else 0.7),
            'abs_gap*bp*exhaust_buy': lambda s: s['abs_gap']*s['buy_pressure']*max(s['exhaust_buy'],0.2),
        }
        bresults = []
        for name, scorer in buy_scorers.items():
            r = sim_buy(scorer)
            if r:
                total, act, dw, tw, nt = r
                bresults.append((total, name, dw, tw, nt))
        bresults.sort(key=lambda x:-x[0])
        out.write(f"  {'Scorer':<60} {'TotRet':>8} {'DayW':>6} {'TrdW':>6}\n  "+"-"*85+"\n")
        for total, name, dw, tw, nt in bresults:
            out.write(f"  {name:<60} {total:>+7.1f}% {dw:>5.1f}% {tw:>5.1f}%\n")

        # ══════════════════════════════════════════════════════════════
        # PART 2: ALL-DAY ENTRY PATTERNS
        # ══════════════════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nPART 2: ALL-DAY ENTRY PATTERNS (when can we enter beyond 9:20?)\n"+"="*110+"\n")
        out.write("\n2a. SELL entries at different times (all liquid stocks, not just gap-up)\n"+"-"*80+"\n")

        # For each entry window, compute: if we enter SHORT at bucket N close,
        # and exit 30 buckets later, what's the win rate?
        windows = [(0,5,'9:15-9:20'),(5,10,'9:20-9:25'),(10,15,'9:25-9:30'),
                   (15,30,'9:30-9:45'),(30,45,'9:45-10:00'),(45,60,'10:00-10:15'),
                   (60,90,'10:15-10:44'),(90,120,'10:44-11:15'),
                   (120,180,'11:15-12:15'),(180,240,'12:15-1:15'),
                   (240,300,'1:15-2:15'),(300,360,'2:15-3:15')]

        out.write(f"  {'Window':<15} {'AllSELL':>12} {'TopDecile':>12} {'BotDecile':>12}\n")
        out.write("  "+"-"*55+"\n")

        for w_start, w_end, w_label in windows:
            hold = 30  # hold for 30 buckets
            sell_rets = []
            for d in dates:
                for s in by_date_all[d]:
                    bkt = s['bkt']
                    entry_b = w_end
                    exit_b = min(w_end + hold, MAX_B-1)
                    entry_p = bkt[entry_b, C]
                    exit_p = bkt[exit_b, C]
                    if entry_p <= 0 or exit_p <= 0: continue
                    # SELL return
                    sell_ret = (entry_p - exit_p) / entry_p * 100
                    # Feature: was this stock "moving down" in the window?
                    window_move = (bkt[w_end,C] - bkt[w_start,O]) / bkt[w_start,O] * 100 if bkt[w_start,O]>0 else 0
                    sell_rets.append((sell_ret, window_move))

            if not sell_rets: continue
            all_wr = sum(1 for r,_ in sell_rets if r>COST)/len(sell_rets)*100
            all_ar = np.mean([r for r,_ in sell_rets])

            # Top decile: stocks that dropped MOST in window (sell momentum)
            sorted_by_move = sorted(sell_rets, key=lambda x: x[1])
            n10 = max(len(sorted_by_move)//10, 20)
            top_dec = sorted_by_move[:n10]
            top_wr = sum(1 for r,_ in top_dec if r>COST)/len(top_dec)*100

            # Bottom decile: stocks that rose MOST (buy momentum → bad for sell)
            bot_dec = sorted_by_move[-n10:]
            bot_wr = sum(1 for r,_ in bot_dec if r>COST)/len(bot_dec)*100

            out.write(f"  {w_label:<15} {all_wr:>5.1f}%/{all_ar:+.2f}%  {top_wr:>5.1f}% (droppers)  {bot_wr:>5.1f}% (risers)\n")

        # 2b. BUY entries at different times
        out.write(f"\n2b. BUY entries at different times (hold 30 buckets)\n"+"-"*80+"\n")
        out.write(f"  {'Window':<15} {'AllBUY':>12} {'TopDecile':>12} {'BotDecile':>12}\n")
        out.write("  "+"-"*55+"\n")

        for w_start, w_end, w_label in windows:
            hold = 30
            buy_rets = []
            for d in dates:
                for s in by_date_all[d]:
                    bkt = s['bkt']
                    entry_b = w_end
                    exit_b = min(w_end + hold, MAX_B-1)
                    entry_p = bkt[entry_b, C]
                    exit_p = bkt[exit_b, C]
                    if entry_p <= 0 or exit_p <= 0: continue
                    buy_ret = (exit_p - entry_p) / entry_p * 100
                    window_move = (bkt[w_end,C] - bkt[w_start,O]) / bkt[w_start,O] * 100 if bkt[w_start,O]>0 else 0
                    buy_rets.append((buy_ret, window_move))

            if not buy_rets: continue
            all_wr = sum(1 for r,_ in buy_rets if r>COST)/len(buy_rets)*100
            sorted_by_move = sorted(buy_rets, key=lambda x: x[1])
            n10 = max(len(sorted_by_move)//10, 20)
            # For BUY: stocks that dropped most in window = oversold = buy opportunity
            bot_dec = sorted_by_move[:n10]
            bot_wr = sum(1 for r,_ in bot_dec if r>COST)/len(bot_dec)*100
            top_dec = sorted_by_move[-n10:]
            top_wr = sum(1 for r,_ in top_dec if r>COST)/len(top_dec)*100

            all_ar = np.mean([r for r,_ in buy_rets])
            out.write(f"  {w_label:<15} {all_wr:>5.1f}%/{all_ar:+.2f}%  {bot_wr:>5.1f}% (dippers)   {top_wr:>5.1f}% (risers)\n")

        # ══════════════════════════════════════════════════════════════
        # PART 3: DYNAMIC EXIT + TRAILING STOPS (SELL trades)
        # ══════════════════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nPART 3: DYNAMIC EXIT + TRAILING STOPS\n"+"="*110+"\n")

        sell_trades = []
        for d in dates:
            for s in by_date_gapup.get(d,[]):
                feat = extract_sell_features(s['bkt'])
                if feat is None: continue
                entry = feat['entry']
                bkt = s['bkt']
                # Price path
                path = []
                for b in range(7,min(90,MAX_B)):
                    if bkt[b,C]>0:
                        pnl = (entry-bkt[b,C])/entry*100
                        hi = (entry-bkt[b,L])/entry*100
                        lo = (entry-bkt[b,H])/entry*100
                        path.append((b,pnl,hi,lo))
                if not path: continue

                # VWAP cross: price goes above VWAP
                vwap_cross = None
                for b in range(7,90):
                    if bkt[b,VW]>0 and bkt[b,C]>bkt[b,VW]:
                        vwap_cross = b; break

                # 3 consecutive green
                consec_green = None
                for b in range(7,87):
                    if bkt[b,C]>bkt[b,O] and bkt[b+1,C]>bkt[b+1,O] and bkt[b+2,C]>bkt[b+2,O]:
                        consec_green = b; break

                ret66 = (entry-bkt[65,C])/entry*100-COST if bkt[65,C]>0 else 0
                ret90 = (entry-bkt[89,C])/entry*100-COST if bkt[89,C]>0 else 0

                sell_trades.append({
                    'path':path,'entry':entry,'gap':s['gap'],
                    'ret66':ret66,'ret90':ret90,
                    'vwap_cross':vwap_cross,'consec_green':consec_green,
                    **feat,
                })

        out.write(f"\n  Sell trades for exit analysis: {len(sell_trades)}\n")

        # 3a. VWAP cross as exit
        out.write(f"\n3a. VWAP CROSS EXIT\n"+"-"*60+"\n")
        has_vwap = sum(1 for t in sell_trades if t['vwap_cross'] and t['vwap_cross']<66)
        out.write(f"  Trades with VWAP cross before b66: {has_vwap} ({has_vwap/len(sell_trades)*100:.0f}%)\n")
        # Simulate
        vwap_total = 0
        for t in sell_trades:
            if t['vwap_cross'] and t['vwap_cross']<90:
                vwap_total += next((p[1]-COST for p in t['path'] if p[0]>=t['vwap_cross']),t['ret66'])
            else:
                vwap_total += t['ret90']
        b66_total = sum(t['ret66'] for t in sell_trades)
        b90_total = sum(t['ret90'] for t in sell_trades)
        out.write(f"  VWAP exit total:  {vwap_total:>+8.1f}%\n")
        out.write(f"  Fixed b66 total:  {b66_total:>+8.1f}%\n")
        out.write(f"  Fixed b90 total:  {b90_total:>+8.1f}%\n")

        # 3b. 3-green exit
        out.write(f"\n3b. 3 CONSECUTIVE GREEN EXIT\n"+"-"*60+"\n")
        has_3g = sum(1 for t in sell_trades if t['consec_green'] and t['consec_green']<66)
        out.write(f"  Trades with 3-green before b66: {has_3g}\n")
        g3_total = 0
        for t in sell_trades:
            if t['consec_green'] and t['consec_green']<90:
                g3_total += next((p[1]-COST for p in t['path'] if p[0]>=t['consec_green']),t['ret90'])
            else:
                g3_total += t['ret90']
        out.write(f"  3-green exit total:  {g3_total:>+8.1f}%\n")
        out.write(f"  Fixed b90 total:     {b90_total:>+8.1f}%\n")

        # 3c. ALL trailing stop variants
        out.write(f"\n3c. TRAILING STOP VARIANTS\n"+"-"*60+"\n")

        def trail_sim(trades, activate, trail_d, max_b=89):
            total=0; wins=0; n=0
            for t in trades:
                peak=0; active_ts=False; exited=False
                for b,pnl,hi,lo in t['path']:
                    if b>max_b: break
                    if hi>peak: peak=hi
                    if not active_ts and peak>=activate: active_ts=True
                    if active_ts and lo<=peak-trail_d:
                        total+=peak-trail_d-COST; n+=1
                        if peak-trail_d-COST>0: wins+=1
                        exited=True; break
                if not exited:
                    lp=t['path'][-1][1] if t['path'] else 0
                    total+=lp-COST; n+=1
                    if lp-COST>0: wins+=1
            return total, wins/max(n,1)*100, n

        out.write(f"  {'Variant':<55} {'TotRet':>8} {'Win%':>6}\n  "+"-"*75+"\n")
        out.write(f"  {'Fixed b66':<55} {b66_total:>+7.1f}% {sum(1 for t in sell_trades if t['ret66']>0)/len(sell_trades)*100:>5.1f}%\n")
        out.write(f"  {'Fixed b90':<55} {b90_total:>+7.1f}% {sum(1 for t in sell_trades if t['ret90']>0)/len(sell_trades)*100:>5.1f}%\n\n")

        for act in [0.2,0.3,0.5,0.7,1.0]:
            for td in [0.15,0.2,0.3,0.4]:
                if td>=act: continue
                total,wr,n = trail_sim(sell_trades,act,td)
                out.write(f"  Trail activate@+{act}% trail={td}%{'':<25} {total:>+7.1f}% {wr:>5.1f}%\n")

        # Stepped
        def stepped_sim(trades):
            total=0;wins=0;n=0
            for t in trades:
                peak=0;stop=-999;exited=False
                for b,pnl,hi,lo in t['path']:
                    if b>89: break
                    if hi>peak: peak=hi
                    if peak>=1.5: stop=max(stop,1.0)
                    elif peak>=1.0: stop=max(stop,0.5)
                    elif peak>=0.5: stop=max(stop,0.2)
                    elif peak>=0.3: stop=max(stop,0.0)
                    if stop>-999 and lo<=stop:
                        total+=stop-COST;n+=1
                        if stop-COST>0: wins+=1
                        exited=True;break
                if not exited:
                    lp=t['path'][-1][1] if t['path'] else 0
                    total+=lp-COST;n+=1;
                    if lp-COST>0: wins+=1
            return total,wins/max(n,1)*100,n
        total,wr,n = stepped_sim(sell_trades)
        out.write(f"\n  {'Stepped: +0.3->BE, +0.5->+0.2, +1->+0.5, +1.5->+1':<55} {total:>+7.1f}% {wr:>5.1f}%\n")

        # Time-decay
        def timedecay_sim(trades):
            total=0;wins=0;n=0
            for t in trades:
                peak=0;exited=False
                for b,pnl,hi,lo in t['path']:
                    if b>89: break
                    if hi>peak: peak=hi
                    td = 0.5 if b<30 else 0.3 if b<60 else 0.15
                    if peak>=0.3 and lo<=peak-td:
                        total+=peak-td-COST;n+=1
                        if peak-td-COST>0: wins+=1
                        exited=True;break
                if not exited:
                    lp=t['path'][-1][1] if t['path'] else 0
                    total+=lp-COST;n+=1
                    if lp-COST>0: wins+=1
            return total,wins/max(n,1)*100,n
        total,wr,n = timedecay_sim(sell_trades)
        out.write(f"  {'Time-decay: early=0.5, mid=0.3, late=0.15':<55} {total:>+7.1f}% {wr:>5.1f}%\n")

        # ══════════════════════════════════════════════════════════════
        # PART 4: MFE PATH — when do winners peak?
        # ══════════════════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nPART 4: MINUTE-BY-MINUTE PROFIT PATH\n"+"="*110+"\n")
        winners = [t for t in sell_trades if t['ret66']>0]
        losers = [t for t in sell_trades if t['ret66']<=0]
        out.write(f"\n  Winners: {len(winners)}, Losers: {len(losers)}\n")
        out.write(f"  {'Bucket':>7} {'AllPnl':>8} {'WinPnl':>8} {'LosePnl':>8} {'WinPeaked':>10}\n  "+"-"*50+"\n")
        for tb in range(10,90,5):
            a_pnl = [next((p[1] for p in t['path'] if p[0]>=tb),0) for t in sell_trades]
            w_pnl = [next((p[1] for p in t['path'] if p[0]>=tb),0) for t in winners]
            l_pnl = [next((p[1] for p in t['path'] if p[0]>=tb),0) for t in losers]
            mfe_peaked = sum(1 for t in winners if max(p[2] for p in t['path'] if p[0]<=tb)==max(p[2] for p in t['path']))/max(len(winners),1)*100
            h=9+(15+tb)//60;m=(15+tb)%60
            out.write(f"  b{tb+1:>4}({h}:{m:02d}) {np.mean(a_pnl):>+7.3f}% {np.mean(w_pnl):>+7.3f}% {np.mean(l_pnl):>+7.3f}% {mfe_peaked:>8.0f}%\n")

        # ══════════════════════════════════════════════════════════════
        # PART 5: HIDDEN SELL PATTERNS
        # ══════════════════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nPART 5: HIDDEN SELL PATTERNS\n"+"="*110+"\n")

        # 5a. Day-of-week effect
        out.write("\n5a. Day of week\n"+"-"*40+"\n")
        import datetime
        dow_stats = defaultdict(list)
        for t in sell_trades:
            # We don't have date in sell_trades, approximate from order
            pass
        # Skip — we don't have date in sell_trades. Use by_date_gapup instead
        dow_data = defaultdict(lambda: [0,0])
        for d in dates:
            dt = datetime.date.fromisoformat(d)
            dow = dt.strftime('%A')
            for s in by_date_gapup.get(d,[]):
                feat = extract_sell_features(s['bkt'])
                if feat is None: continue
                ret = (feat['entry']-s['bkt'][65,C])/feat['entry']*100-COST if s['bkt'][65,C]>0 else None
                if ret is None: continue
                dow_data[dow][1] += 1
                if ret > 0: dow_data[dow][0] += 1
        out.write(f"  {'Day':>12} {'Trades':>7} {'Win%':>6}\n  "+"-"*30+"\n")
        for dow in ['Monday','Tuesday','Wednesday','Thursday','Friday']:
            w,t = dow_data.get(dow,[0,0])
            if t>0: out.write(f"  {dow:>12} {t:>7} {w/t*100:>5.1f}%\n")

        # 5b. Gap size sweet spot (finer granularity)
        out.write(f"\n5b. Gap size sweet spot (finer bins)\n"+"-"*40+"\n")
        gap_fine = [(0.5,0.75),(0.75,1),(1,1.25),(1.25,1.5),(1.5,2),(2,2.5),(2.5,3),(3,4),(4,5),(5,7),(7,10),(10,15),(15,100)]
        out.write(f"  {'Gap':>10} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*35+"\n")
        for glo,ghi in gap_fine:
            sub = [t for t in sell_trades if glo<=t['gap']<ghi]
            if len(sub)<20: continue
            wr = sum(1 for t in sub if t['ret66']>0)/len(sub)*100
            ar = np.mean([t['ret66'] for t in sub])
            out.write(f"  {f'{glo}-{ghi}%':>10} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # 5c. Sell pressure x momentum x gap (3D heatmap)
        out.write(f"\n5c. 3D: sell_pressure x momentum x gap -> win rate at b66\n"+"-"*80+"\n")
        sp_bins = [(0.3,0.5,'sp.3-.5'),(0.5,0.6,'sp.5-.6'),(0.6,0.8,'sp.6-.8')]
        mom_bins = [(-99,-0.5,'m<-.5'),(-.5,0,'m-.5~0'),(0,99,'m>0')]
        gap_bins = [(0.5,2,'g.5-2'),(2,4,'g2-4'),(4,100,'g4+')]
        out.write(f"  {'SP+Mom':>15}")
        for _,_,gl in gap_bins: out.write(f" {gl:>12}")
        out.write("\n  "+"-"*55+"\n")
        for slo,shi,sl in sp_bins:
            for mlo,mhi,ml in mom_bins:
                out.write(f"  {sl+' '+ml:>15}")
                for glo,ghi,gl in gap_bins:
                    sub = [t for t in sell_trades if slo<=t['sell_pressure']<shi and mlo<=t['momentum']<mhi and glo<=t['gap']<ghi]
                    if len(sub)<15:
                        out.write(f"{'--':>12}")
                    else:
                        wr = sum(1 for t in sub if t['ret66']>0)/len(sub)*100
                        out.write(f" {wr:>4.0f}%({len(sub):>4})")
                out.write("\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
