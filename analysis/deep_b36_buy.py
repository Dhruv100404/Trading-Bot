"""
DEEP B36 BUY ANALYSIS — The 72% win pattern
==============================================
B36: gap<-2% + S6buy>3 + buy_pressure>0.55 + above VWAP at entry
Exit at b45 (9:59 AM)

Deep dive:
  1. Why does this work? What makes it different from other buy patterns?
  2. Per-day analysis: how many signals per day? Can we cherry-pick?
  3. Exit optimization: is b45 really the best?
  4. Feature deep dive: what separates B36 winners from losers?
  5. Can we improve it further?
  6. How to combine with SELL strategy?
  7. Market regime: when does B36 work vs fail?
  8. Position sizing on B36
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict
import datetime

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_b36_buy.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000; MARGIN = 5

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date_buy = defaultdict(list)
    by_date_sell = defaultdict(list)
    all_gaps = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                if abs(r['gapPct']) > 10: continue
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                all_gaps[r['date']].append(r['gapPct'])

                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                if bkt[0,O]<=0 or bkt[0,H]==bkt[0,L]: continue
                entry = bkt[6,O]
                if entry<=0: continue

                gap = r['gapPct']; price = r['dayOpen']
                cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp/6; bp = cp/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_green = sum(1 for i in range(6) if bkt[i,C]>bkt[i,O])
                n_red = 6-n_green
                vwap_dev = (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0
                avg_br = np.mean([float(bkt[i,BR]) for i in range(6)])
                b0_body = abs(bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                b0_green = bkt[0,C]>bkt[0,O]

                # BUY returns
                buy_ret = {}
                for eb in [19,29,44,59,65,89]:
                    if bkt[eb,C]>0: buy_ret[eb] = (bkt[eb,C]-entry)/entry*100-COST

                # SELL returns
                sell_ret = {}
                for eb in [44,65,89]:
                    if bkt[eb,C]>0: sell_ret[eb] = (entry-bkt[eb,C])/entry*100-COST

                # S6 buy score
                s6_buy = abs(gap)*(1.0 if bp>0.5 else 0.3)*(1.2 if price<500 else 0.9)
                s6_sell = gap*(1.0 if sp>0.5 else 0.3)*(1.2 if price<500 else 0.9) if gap>0 else 0

                # Live features at check points
                live = {}
                for cb in [9,14,19]:
                    if bkt[cb,C]>0:
                        live[cb] = {
                            'pnl': (bkt[cb,C]-entry)/entry*100,
                            'vwap': (bkt[cb,C]-bkt[cb,VW])/bkt[cb,VW]*100 if bkt[cb,VW]>0 else 0,
                        }

                rec = {
                    'sym':r['symbol'],'gap':gap,'price':price,'entry':entry,
                    'sp':sp,'bp':bp,'mom':mom,'n_green':n_green,'n_red':n_red,
                    'vwap_dev':vwap_dev,'avg_br':avg_br,'b0_body':b0_body,'b0_green':b0_green,
                    's6_buy':s6_buy,'s6_sell':s6_sell,
                    'buy_ret':buy_ret,'sell_ret':sell_ret,'live':live,
                    'date':r['date'],'bkt':bkt,
                }

                if gap < -0.5:
                    by_date_buy[r['date']].append(rec)
                if gap > 0.5:
                    by_date_sell[r['date']].append(rec)

    dates = sorted(set(list(by_date_buy.keys())+list(by_date_sell.keys())))
    mkt = {d: np.mean(all_gaps[d]) for d in dates}

    n_buy = sum(len(v) for v in by_date_buy.values())
    n_sell = sum(len(v) for v in by_date_sell.values())
    print(f"Buy pool: {n_buy}, Sell pool: {n_sell} in {time.time()-t0:.1f}s")

    # B36 filter
    def is_b36(r):
        return r['gap']<-2 and r['s6_buy']>3 and r['bp']>0.55 and r['vwap_dev']>0

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("DEEP B36 BUY ANALYSIS\n")
        out.write(f"B36: gap<-2% + S6buy>3 + buy_pressure>0.55 + above VWAP\n")
        out.write(f"Buy pool: {n_buy}, Sell pool: {n_sell}, Days: {len(dates)}\n\n")

        # ═══════════════════════════════════════
        # 1. B36 BASIC STATS
        # ═══════════════════════════════════════
        all_b36 = [r for stocks in by_date_buy.values() for r in stocks if is_b36(r)]
        out.write("="*110+"\n1. B36 BASIC STATS\n"+"="*110+"\n")
        out.write(f"  Total B36 signals: {len(all_b36)} across {len(dates)} days\n")
        out.write(f"  Avg per day: {len(all_b36)/len(dates):.1f}\n\n")

        # Per exit bucket
        out.write(f"  Exit bucket analysis:\n")
        for eb in [19,29,44,59,65,89]:
            rets = [r['buy_ret'].get(eb,0) for r in all_b36 if eb in r['buy_ret']]
            if not rets: continue
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            h=9+(15+eb)//60; m=(15+eb)%60
            out.write(f"    b{eb+1}({h}:{m:02d}): N={len(rets)} Win={wr:.1f}% Ret={ar:+.3f}%\n")

        # ═══════════════════════════════════════
        # 2. PER-DAY SIGNAL COUNT + CHERRY-PICK
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. CHERRY-PICK B36: top-N by S6buy score\n"+"="*110+"\n")

        for n_pos in [1,2,3,4,5,6,8]:
            total=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                b36_pool = [r for r in by_date_buy.get(d,[]) if is_b36(r)]
                b36_pool.sort(key=lambda x:-x['s6_buy'])
                picks = b36_pool[:n_pos]
                if not picks: continue
                active+=1
                dr = sum(r['buy_ret'].get(44,0) for r in picks)
                for r in picks:
                    ret = r['buy_ret'].get(44,0)
                    trades+=1; total+=ret
                    if ret>0: wt+=1
                if dr>0: dw+=1
            if trades<10: continue
            out.write(f"  Top-{n_pos}: days={active:>3} trades={trades:>4} win={wt/trades*100:.1f}% dayWin={dw/max(active,1)*100:.1f}% totalRet={total:+.1f}%\n")

        # ═══════════════════════════════════════
        # 3. B36 WINNERS vs LOSERS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. B36 WINNERS vs LOSERS at b45\n"+"="*110+"\n")
        b36_w = [r for r in all_b36 if r['buy_ret'].get(44,0)>0]
        b36_l = [r for r in all_b36 if r['buy_ret'].get(44,0)<=0]
        out.write(f"  Winners: {len(b36_w)} ({len(b36_w)/len(all_b36)*100:.1f}%), Losers: {len(b36_l)}\n\n")

        out.write(f"  {'Feature':<16} {'Winners':>10} {'Losers':>10} {'Delta':>10}\n  "+"-"*50+"\n")
        for f in ['gap','price','bp','mom','n_green','vwap_dev','avg_br','b0_body','s6_buy']:
            wv = np.mean([r[f] for r in b36_w])
            lv = np.mean([r[f] for r in b36_l])
            out.write(f"  {f:<16} {wv:>10.3f} {lv:>10.3f} {wv-lv:>+10.3f}\n")

        # ═══════════════════════════════════════
        # 4. IMPROVE B36: additional filters
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. IMPROVE B36: additional filters on top\n"+"="*110+"\n")

        filters = {
            'B36 (baseline)': lambda r: True,
            '+ mom > 0.3%': lambda r: r['mom']>0.3,
            '+ mom > 0.5%': lambda r: r['mom']>0.5,
            '+ n_green >= 3': lambda r: r['n_green']>=3,
            '+ n_green >= 4': lambda r: r['n_green']>=4,
            '+ bp > 0.60': lambda r: r['bp']>0.60,
            '+ bp > 0.65': lambda r: r['bp']>0.65,
            '+ avg_br > 0.55': lambda r: r['avg_br']>0.55,
            '+ vwap_dev > 0.3%': lambda r: r['vwap_dev']>0.3,
            '+ gap < -3%': lambda r: r['gap']<-3,
            '+ b0 green': lambda r: r['b0_green'],
            '+ b0 big green >0.5%': lambda r: r['b0_green'] and r['b0_body']>0.5,
            '+ mom>0.3 + n_green>=3': lambda r: r['mom']>0.3 and r['n_green']>=3,
            '+ bp>0.60 + mom>0.3': lambda r: r['bp']>0.60 and r['mom']>0.3,
            '+ gap<-3% + bp>0.60': lambda r: r['gap']<-3 and r['bp']>0.60,
            '+ bp>0.60 + vwap>0.3 + mom>0.3': lambda r: r['bp']>0.60 and r['vwap_dev']>0.3 and r['mom']>0.3,
        }

        out.write(f"  {'Filter':<45} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n  "+"-"*70+"\n")
        for name, filt in filters.items():
            sub = [r for r in all_b36 if filt(r)]
            if len(sub)<20: continue
            rets = [r['buy_ret'].get(44,0) for r in sub]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            out.write(f"  {name:<45} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # ═══════════════════════════════════════
        # 5. MARKET REGIME: when does B36 work?
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. MARKET REGIME: when does B36 work vs fail?\n"+"="*110+"\n")

        # Day of week
        out.write(f"\n  Day of week:\n")
        for dow in ['Monday','Tuesday','Wednesday','Thursday','Friday']:
            sub = [r for r in all_b36 if datetime.date.fromisoformat(r['date']).strftime('%A')==dow]
            if len(sub)<10: continue
            rets = [r['buy_ret'].get(44,0) for r in sub]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            out.write(f"    {dow:>12}: N={len(sub):>4} Win={wr:.1f}%\n")

        # Market avg gap
        out.write(f"\n  Market avg gap (bearish vs bullish day):\n")
        for lo,hi,lbl in [(-99,-0.5,'Bear (<-0.5%)'),(-0.5,0,'Mild bear'),
                          (0,0.5,'Mild bull'),(0.5,99,'Bull (>0.5%)')]:
            sub = [r for r in all_b36 if lo<=mkt.get(r['date'],0)<hi]
            if len(sub)<10: continue
            rets = [r['buy_ret'].get(44,0) for r in sub]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            ar = np.mean(rets)
            out.write(f"    {lbl:>20}: N={len(sub):>4} Win={wr:.1f}% Ret={ar:+.3f}%\n")

        # Number of B36 signals that day (crowded vs sparse)
        out.write(f"\n  Number of B36 signals that day:\n")
        for lo,hi,lbl in [(0,3,'0-2'),(3,6,'3-5'),(6,10,'6-9'),(10,20,'10-19'),(20,999,'20+')]:
            sub = []
            for d in dates:
                n_b36 = len([r for r in by_date_buy.get(d,[]) if is_b36(r)])
                if lo<=n_b36<hi:
                    sub.extend([r for r in by_date_buy.get(d,[]) if is_b36(r)])
            if len(sub)<10: continue
            rets = [r['buy_ret'].get(44,0) for r in sub]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            out.write(f"    {lbl:>8} signals: N={len(sub):>4} Win={wr:.1f}%\n")

        # ═══════════════════════════════════════
        # 6. POSITION SIZING ON B36
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. POSITION SIZING: ADD at b10 if profitable + above VWAP\n"+"="*110+"\n")

        def sim_b36_sizing(n_pos, add_fn=None, check_b=9):
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                pool = [r for r in by_date_buy.get(d,[]) if is_b36(r)]
                pool.sort(key=lambda x:-x['s6_buy'])
                picks = pool[:n_pos]
                if not picks: continue
                active+=1; day_pnl=0
                for r in picks:
                    trades+=1
                    if add_fn and check_b in r['live']:
                        action = add_fn(r['live'][check_b])
                        if action[0]=='add':
                            mult = action[1]
                            early_pnl = r['live'][check_b]['pnl']
                            final_ret = r['buy_ret'].get(44,0)
                            remaining = final_ret - (early_pnl-COST)
                            ret = (early_pnl-COST) + remaining*mult
                        else:
                            ret = r['buy_ret'].get(44,0)
                    else:
                        ret = r['buy_ret'].get(44,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1
                total_pnl+=day_pnl
                if day_pnl>0: dw+=1
            roc = total_pnl/(BASE*n_pos)*100
            return roc, dw/max(active,1)*100, wt/max(trades,1)*100, trades, active

        add_fn = lambda live: ('add',3.0) if live['pnl']>0.3 and live['vwap']>0 else ('hold',)

        for npos in [2,3,4]:
            roc,dw,tw,nt,act = sim_b36_sizing(npos)
            roc_s,dw_s,tw_s,_,_ = sim_b36_sizing(npos, add_fn, 9)
            out.write(f"  Top-{npos}: plain ROC={roc:+.1f}% dayW={dw:.1f}% | sized ROC={roc_s:+.1f}% dayW={dw_s:.1f}% | days={act}\n")

        # ═══════════════════════════════════════
        # 7. COMBINED: SELL S6 + BUY B36 on same day
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n7. COMBINED: SELL S6 top-N + BUY B36 top-M on same day\n"+"="*110+"\n")

        for n_sell, n_buy in [(7,0),(6,1),(6,2),(5,2),(5,3),(4,3),(4,4),(7,2),(7,3)]:
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                # Sell picks (S6)
                sell_pool = sorted(by_date_sell.get(d,[]), key=lambda x:-x['s6_sell'])[:n_sell]
                # Buy picks (B36)
                buy_pool = [r for r in by_date_buy.get(d,[]) if is_b36(r)]
                buy_pool.sort(key=lambda x:-x['s6_buy'])
                buy_picks = buy_pool[:n_buy]

                all_picks = sell_pool + buy_picks
                if not all_picks: continue
                active+=1; day_pnl=0
                for r in sell_pool:
                    ret = r['sell_ret'].get(89,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs; trades+=1
                    if pnl_rs>0: wt+=1
                for r in buy_picks:
                    ret = r['buy_ret'].get(44,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs; trades+=1
                    if pnl_rs>0: wt+=1

                total_pnl+=day_pnl
                if day_pnl>0: dw+=1

            total_pos = n_sell+n_buy
            roc = total_pnl/(BASE*total_pos)*100
            dwp = dw/max(active,1)*100
            twp = wt/max(trades,1)*100
            out.write(f"  {n_sell}S+{n_buy}B: ROC={roc:>+7.1f}% dayWin={dwp:.1f}% trdWin={twp:.1f}% trades={trades}\n")

        # ═══════════════════════════════════════
        # 8. COMBINED + SIZING on both sides
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n8. COMBINED + SIZING: S6 sell (ADD 3x@b15) + B36 buy (ADD 3x@b10)\n"+"="*110+"\n")

        sell_add = lambda live: ('add',3.0) if live['pnl']>0.3 and live['vwap']<0 else ('hold',)
        buy_add = lambda live: ('add',3.0) if live['pnl']>0.3 and live['vwap']>0 else ('hold',)

        for n_sell, n_buy in [(7,0),(7,2),(7,3),(6,2),(5,3)]:
            total_pnl=0; dw=0; active=0; trades=0; wt=0
            for d in dates:
                sell_pool = sorted(by_date_sell.get(d,[]), key=lambda x:-x['s6_sell'])[:n_sell]
                buy_pool = [r for r in by_date_buy.get(d,[]) if is_b36(r)]
                buy_pool.sort(key=lambda x:-x['s6_buy'])
                buy_picks = buy_pool[:n_buy]

                if not sell_pool and not buy_picks: continue
                active+=1; day_pnl=0

                for r in sell_pool:
                    trades+=1
                    if 14 in r['live']:
                        action = sell_add(r['live'][14])
                        if action[0]=='add':
                            early = r['live'][14]['pnl']
                            final = r['sell_ret'].get(89,0)
                            remaining = final-(early-COST)
                            ret = (early-COST)+remaining*3.0
                        else:
                            ret = r['sell_ret'].get(89,0)
                    else:
                        ret = r['sell_ret'].get(89,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1

                for r in buy_picks:
                    trades+=1
                    if 9 in r['live']:
                        action = buy_add(r['live'][9])
                        if action[0]=='add':
                            early = r['live'][9]['pnl']
                            final = r['buy_ret'].get(44,0)
                            remaining = final-(early-COST)
                            ret = (early-COST)+remaining*3.0
                        else:
                            ret = r['buy_ret'].get(44,0)
                    else:
                        ret = r['buy_ret'].get(44,0)
                    pnl_rs = BASE*MARGIN*ret/100
                    day_pnl+=pnl_rs
                    if pnl_rs>0: wt+=1

                total_pnl+=day_pnl
                if day_pnl>0: dw+=1

            total_pos = n_sell+n_buy
            roc = total_pnl/(BASE*total_pos)*100
            dwp = dw/max(active,1)*100
            out.write(f"  {n_sell}S+{n_buy}B (sized): ROC={roc:>+7.1f}% dayWin={dwp:.1f}% trades={trades}\n")

        # ═══════════════════════════════════════
        # 9. IMPLEMENTATION GUIDE
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n9. IMPLEMENTATION GUIDE\n"+"="*110+"\n")
        out.write(f"""
  B36 BUY SIGNAL:
    Condition (ALL must be true, NO lookahead):
      1. gap < -2% (stock gapped down significantly)
      2. S6_buy > 3 where S6_buy = |gap| * (bp>0.5?1:0.3) * (price<500?1.2:0.9)
      3. buy_pressure > 0.55 (close_position avg > 0.55 = buyers winning)
      4. price > VWAP at bucket 5 (9:20 AM) = price recovered above VWAP

    Entry: bucket 6 open (9:21 AM) — same as SELL entry
    Exit: bucket 44 close (9:59 AM) — 38 minutes hold, NOT b90
    Direction: BUY (long position)

  Cherry-pick: rank by S6_buy score, take top-N
  Position sizing: at b10 (9:24 AM), if profitable >0.3% AND above VWAP → ADD 3x

  COMBINED with SELL:
    - Run SELL S6 top-7 (gap-up reversal, exit b90)
    - Run BUY B36 top-2 or top-3 (gap-down bounce, exit b45)
    - Separate capital pools (sell uses sell_capital, buy uses buy_capital)
    - BUY positions exit 45 min BEFORE sell positions

  In the engine:
    - gap_reversal_buy_mode already exists
    - Need: B36 filter (gap<-2% + bp>0.55 + vwap>0 + S6buy>3)
    - Need: buy_hard_exit_bucket = 44 (not 66 or 90)
    - Need: separate cherry-pick pool for buy side
""")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
