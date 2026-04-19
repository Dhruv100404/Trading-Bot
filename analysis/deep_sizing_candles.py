"""
SIZING + GREEN CANDLE COMBO — can candle signals improve sizing decisions?
===========================================================================
At b20 (9:34 AM), combine:
  - P&L direction
  - VWAP position
  - Green candle count (0 to 13)
  - Max green body size
  - Green momentum (sum of green bodies)
  - Consecutive green streak

Test: does adding candle data to the sizing rule improve results?
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_sizing_candles.txt'
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
                score = (r['gapPct'] if sp>0.5 else r['gapPct']*0.3)*(1.3 if mom<0 else 0.7)
                ret90 = (entry-bkt[89,C])/entry*100-COST

                # Per-bucket live features (computed at various check points)
                def features_at(cb):
                    pnl = (entry-bkt[cb,C])/entry*100 if bkt[cb,C]>0 else 0
                    vwap = (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0
                    # Green candle metrics from b7 to cb
                    n_green = 0; green_mom = 0; max_green_body = 0; streak = 0; max_streak = 0
                    for b in range(7, cb+1):
                        if bkt[b,C] > bkt[b,O]:
                            body = (bkt[b,C]-bkt[b,O])/bkt[b,O]*100 if bkt[b,O]>0 else 0
                            n_green += 1; green_mom += body; streak += 1
                            if body > max_green_body: max_green_body = body
                            if streak > max_streak: max_streak = streak
                        else:
                            streak = 0
                    # Volume since entry
                    vol_sum = sum(float(bkt[b,V]) for b in range(7, cb+1))
                    return {
                        'pnl':pnl, 'vwap':vwap, 'n_green':n_green, 'green_mom':green_mom,
                        'max_green_body':max_green_body, 'max_streak':max_streak, 'vol_sum':vol_sum,
                    }

                f15 = features_at(14)
                f20 = features_at(19)
                f25 = features_at(24)
                f30 = features_at(29)

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'score':score,'entry':entry,
                    'ret90':ret90,'win':ret90>0,'date':r['date'],
                    'f15':f15,'f20':f20,'f25':f25,'f30':f30,
                })

    dates = sorted(by_date.keys())
    daily_picks = {d: sorted(by_date[d], key=lambda x:-x['score'])[:8] for d in dates}
    all_picks = [t for picks in daily_picks.values() for t in picks]
    print(f"Loaded {len(all_picks)} picks in {time.time()-t0:.1f}s")

    def sim_sizing(sizing_fn, check='f20'):
        total_pnl_rs = 0; day_wins = 0; days = 0
        for d in dates:
            picks = daily_picks.get(d,[])
            if not picks: continue
            days += 1; day_pnl = 0
            for t in picks:
                f = t[check]
                mult = sizing_fn(f)
                pnl_rs = BASE_CAP * MARGIN * mult * t['ret90'] / 100
                day_pnl += pnl_rs
            total_pnl_rs += day_pnl
            if day_pnl > 0: day_wins += 1
        roc = total_pnl_rs / (BASE_CAP*8) * 100
        return roc, day_wins/max(days,1)*100, days

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("SIZING + GREEN CANDLE COMBO ANALYSIS\n")
        out.write(f"Picks: {len(all_picks)}, Days: {len(dates)}\n\n")

        # ═══════════════════════════════════════════
        # 1. BASELINE sizing strategies (no candle data)
        # ═══════════════════════════════════════════
        out.write("="*110+"\n1. BASELINE SIZING (no candle data)\n"+"="*110+"\n")
        baselines = {
            'EQUAL 1x': lambda f: 1.0,
            'PnL only: ADD 2x win, EXIT lose>0.5%': lambda f: 2.0 if f['pnl']>0.3 else 0 if f['pnl']<-0.5 else 1.0,
            'PnL+VWAP: ADD 2x win+belowVWAP, EXIT lose+aboveVWAP': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,
        }
        out.write(f"  {'Strategy':<65} {'ROC':>8} {'DayWin':>7}\n  "+"-"*85+"\n")
        for name, fn in baselines.items():
            roc, dw, _ = sim_sizing(fn, 'f20')
            out.write(f"  {name:<65} {roc:>+7.1f}% {dw:>6.1f}%\n")

        # ═══════════════════════════════════════════
        # 2. GREEN CANDLE as ADDITIONAL EXIT signal
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n2. ADD GREEN CANDLE DATA to sizing decision\n"+"="*110+"\n")

        strategies = {
            # Base: PnL + VWAP
            'BASE: PnL+VWAP': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,

            # Add green candle count to EXIT decision
            'PnL+VWAP + EXIT if 6+green': lambda f: 0 if f['n_green']>=6 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if 5+green+losing': lambda f: 0 if f['n_green']>=5 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if 7+green': lambda f: 0 if f['n_green']>=7 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),

            # Add max green body to EXIT
            'PnL+VWAP + EXIT if maxGreenBody>1%': lambda f: 0 if f['max_green_body']>1.0 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if maxGreenBody>0.7%': lambda f: 0 if f['max_green_body']>0.7 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if maxGreenBody>0.5%+losing': lambda f: 0 if f['max_green_body']>0.5 and f['pnl']<-0.2 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),

            # Add consecutive streak to EXIT
            'PnL+VWAP + EXIT if 3+streak': lambda f: 0 if f['max_streak']>=3 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if 4+streak': lambda f: 0 if f['max_streak']>=4 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if 4+streak+losing>0.3%': lambda f: 0 if f['max_streak']>=4 and f['pnl']<-0.3 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),

            # Add green momentum to EXIT
            'PnL+VWAP + EXIT if greenMom>2%+losing': lambda f: 0 if f['green_mom']>2.0 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),
            'PnL+VWAP + EXIT if greenMom>1.5%+losing': lambda f: 0 if f['green_mom']>1.5 and f['pnl']<0 else (2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0),

            # Add candle data to ADD decision (only add if few greens = sellers still dominating)
            'PnL+VWAP + ADD only if n_green<5': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['n_green']<5 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,
            'PnL+VWAP + ADD only if n_green<6': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['n_green']<6 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,
            'PnL+VWAP + ADD only if maxBody<0.5%': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['max_green_body']<0.5 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,
            'PnL+VWAP + ADD only if streak<3': lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['max_streak']<3 else 0 if f['pnl']<-0.5 and f['vwap']>0.3 else 1.0,

            # MEGA COMBOS: candle data for BOTH add and exit
            'MEGA: ADD if win+belowVWAP+green<5, EXIT if lose>0.5%+aboveVWAP OR 6+green+losing':
                lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['n_green']<5 else 0 if (f['pnl']<-0.5 and f['vwap']>0.3) or (f['n_green']>=6 and f['pnl']<0) else 1.0,
            'MEGA: ADD if win+belowVWAP+maxBody<0.5%, EXIT if lose+aboveVWAP OR maxBody>1%+losing':
                lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['max_green_body']<0.5 else 0 if (f['pnl']<-0.5 and f['vwap']>0.3) or (f['max_green_body']>1.0 and f['pnl']<0) else 1.0,
            'MEGA: ADD if win+belowVWAP+streak<3, EXIT if lose>0.3%+aboveVWAP OR 4+streak+losing':
                lambda f: 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 and f['max_streak']<3 else 0 if (f['pnl']<-0.3 and f['vwap']>0.3) or (f['max_streak']>=4 and f['pnl']<-0.3) else 1.0,
            'MEGA: ADD 2x if win>0.5%+belowVWAP+green<4, EXIT if (lose>0.5%+aboveVWAP) OR (maxBody>0.7%+lose) OR (5+green+lose>0.2%)':
                lambda f: 2.0 if f['pnl']>0.5 and f['vwap']<-0.3 and f['n_green']<4 else 0 if (f['pnl']<-0.5 and f['vwap']>0.3) or (f['max_green_body']>0.7 and f['pnl']<-0.2) or (f['n_green']>=5 and f['pnl']<-0.2) else 1.0,
            # Progressive ADD: 3x for strongest signals
            'MEGA: ADD 3x if win>1%+belowVWAP+green<3, ADD 2x if win+belowVWAP, EXIT if lose+signals':
                lambda f: 3.0 if f['pnl']>1.0 and f['vwap']<-0.5 and f['n_green']<3 else 2.0 if f['pnl']>0.3 and f['vwap']<-0.3 else 0 if (f['pnl']<-0.5 and f['vwap']>0.3) or (f['n_green']>=6 and f['pnl']<0) else 1.0,
        }

        # Test at b15, b20, b25, b30
        for check_key, check_label in [('f15','b15 (9:29)'),('f20','b20 (9:34)'),('f25','b25 (9:39)'),('f30','b30 (9:44)')]:
            out.write(f"\n  --- Check at {check_label} ---\n")
            out.write(f"  {'Strategy':<80} {'ROC':>8} {'DayW':>6}\n  "+"-"*100+"\n")
            results = []
            for name, fn in strategies.items():
                roc, dw, _ = sim_sizing(fn, check_key)
                results.append((roc, name, dw))
            results.sort(key=lambda x:-x[0])
            for roc, name, dw in results:
                marker = " <<<" if 'BASE' in name else ""
                out.write(f"  {name:<80} {roc:>+7.1f}% {dw:>5.1f}%{marker}\n")

        # ═══════════════════════════════════════════
        # 3. PER-SHARE: candle features of ADD vs EXIT vs HOLD trades
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n3. WHAT CANDLE FEATURES LOOK LIKE for winners vs losers at b20\n"+"="*110+"\n")

        out.write(f"  {'Feature':<20} {'Winners':>10} {'Losers':>10} {'Delta':>10}\n  "+"-"*55+"\n")
        w = [t for t in all_picks if t['win']]
        l = [t for t in all_picks if not t['win']]
        for feat in ['n_green','green_mom','max_green_body','max_streak','pnl','vwap','vol_sum']:
            wv = np.mean([t['f20'][feat] for t in w])
            lv = np.mean([t['f20'][feat] for t in l])
            out.write(f"  {feat:<20} {wv:>10.3f} {lv:>10.3f} {wv-lv:>+10.3f}\n")

        # 3D: pnl x vwap x green_count -> sizing decision
        out.write(f"\n  3D HEATMAP: pnl x vwap x green_count -> win rate at b20\n")
        for pnl_label, pnl_filt in [('WINNING', lambda f: f['pnl']>0), ('LOSING', lambda f: f['pnl']<=0)]:
            for vwap_label, vwap_filt in [('belowVWAP', lambda f: f['vwap']<0), ('aboveVWAP', lambda f: f['vwap']>=0)]:
                out.write(f"\n    {pnl_label} + {vwap_label}:\n")
                out.write(f"    {'GreenCount':>12} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'Decision':>10}\n    "+"-"*50+"\n")
                for glo,ghi,glbl in [(0,3,'0-2'),(3,5,'3-4'),(5,7,'5-6'),(7,99,'7+')]:
                    sub = [t for t in all_picks if pnl_filt(t['f20']) and vwap_filt(t['f20']) and glo<=t['f20']['n_green']<ghi]
                    if len(sub)<10: continue
                    wr = sum(t['win'] for t in sub)/len(sub)*100
                    ar = np.mean([t['ret90'] for t in sub])
                    dec = "ADD 2x" if wr>70 else "HOLD" if wr>50 else "CUT" if wr>35 else "EXIT"
                    out.write(f"    {glbl:>12} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}% {dec:>10}\n")

        # 3D: pnl x vwap x max_streak -> sizing decision
        out.write(f"\n  3D HEATMAP: pnl x vwap x max_consec_streak -> win rate at b20\n")
        for pnl_label, pnl_filt in [('WINNING', lambda f: f['pnl']>0), ('LOSING', lambda f: f['pnl']<=0)]:
            for vwap_label, vwap_filt in [('belowVWAP', lambda f: f['vwap']<0), ('aboveVWAP', lambda f: f['vwap']>=0)]:
                out.write(f"\n    {pnl_label} + {vwap_label}:\n")
                out.write(f"    {'MaxStreak':>12} {'N':>5} {'Win%':>6} {'AvgRet':>8} {'Decision':>10}\n    "+"-"*50+"\n")
                for slo,shi,slbl in [(0,2,'0-1'),(2,3,'2'),(3,4,'3'),(4,99,'4+')]:
                    sub = [t for t in all_picks if pnl_filt(t['f20']) and vwap_filt(t['f20']) and slo<=t['f20']['max_streak']<shi]
                    if len(sub)<10: continue
                    wr = sum(t['win'] for t in sub)/len(sub)*100
                    ar = np.mean([t['ret90'] for t in sub])
                    dec = "ADD 2x" if wr>70 else "HOLD" if wr>50 else "CUT" if wr>35 else "EXIT"
                    out.write(f"    {slbl:>12} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}% {dec:>10}\n")

        # ═══════════════════════════════════════════
        # 4. FINAL VERDICT
        # ═══════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n4. FINAL VERDICT: does candle data improve sizing?\n"+"="*110+"\n")

        # Compare best candle-enhanced vs base at b20
        base_roc, base_dw, _ = sim_sizing(strategies['BASE: PnL+VWAP'], 'f20')
        best_roc = -999; best_name = ""
        for name, fn in strategies.items():
            if 'BASE' in name: continue
            roc, dw, _ = sim_sizing(fn, 'f20')
            if roc > best_roc: best_roc = roc; best_name = name; best_dw = dw

        out.write(f"\n  BASE (PnL+VWAP only):         ROC={base_roc:>+8.1f}%  DayWin={base_dw:.1f}%\n")
        out.write(f"  BEST (with candle data):       ROC={best_roc:>+8.1f}%  DayWin={best_dw:.1f}%\n")
        out.write(f"  Strategy: {best_name}\n")
        out.write(f"  Improvement:                   {best_roc-base_roc:>+8.1f}%\n")

        if best_roc > base_roc:
            out.write(f"\n  YES — candle data IMPROVES sizing by {best_roc-base_roc:+.1f}%\n")
        else:
            out.write(f"\n  NO — candle data does NOT improve sizing at b20\n")
            out.write(f"  PnL + VWAP alone is already the optimal signal\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
