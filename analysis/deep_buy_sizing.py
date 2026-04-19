"""
DEEP BUY-SIDE POSITION SIZING ANALYSIS
========================================
B36 BUY: gap<-2% + bp>0.55 + vwap>0 + S6buy>3, exit b45
Uses CORRECT weighted-entry math for ADD sizing.
All heavy work done via numpy — no per-bucket Python loops.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict
import datetime

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_buy_sizing.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
BASE = 10000; MARGIN = 5
CHECK_BUCKETS = [7,8,9,10,11,12,14,16,19,24,29]

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading data...")
    by_date_buy = defaultdict(list)
    by_date_sell = defaultdict(list)
    all_gaps = defaultdict(list)
    loaded = 0

    for fp in files:
        if not fp.exists(): continue
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
                entry = float(bkt[6,O])
                if entry<=0: continue

                gap = r['gapPct']; price = r['dayOpen']

                # Vectorized close_position for b0-b5
                hl = bkt[:6,H] - bkt[:6,L]
                valid = hl > 0
                cp_arr = np.where(valid, (bkt[:6,C]-bkt[:6,L])/hl, 0.5)
                bp = float(np.mean(cp_arr))
                sp = 1.0 - bp

                mom = float((bkt[5,C]-bkt[0,O])/bkt[0,O]*100) if bkt[0,O]>0 else 0.0
                n_green_pre = int(np.sum(bkt[:6,C] > bkt[:6,O]))
                vwap_dev = float((bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100) if bkt[5,VW]>0 else 0.0
                avg_br = float(np.mean(bkt[:6,BR]))

                s6_buy = abs(gap)*(1.0 if bp>0.5 else 0.3)*(1.2 if price<500 else 0.9)
                s6_sell = gap*(1.0 if sp>0.5 else 0.3)*(1.2 if price<500 else 0.9) if gap>0 else 0

                # Numpy vectorized: compute tick data at CHECK_BUCKETS only
                # Plus trajectory buckets 7..44
                all_cb = sorted(set(CHECK_BUCKETS + list(range(7,45))))
                tick = {}
                closes = bkt[6:45, C]  # buckets 6..44
                highs  = bkt[6:45, H]
                lows   = bkt[6:45, L]
                opens  = bkt[6:45, O]
                vwaps  = bkt[6:45, VW]
                greens = (closes > opens).astype(np.int32)

                if closes[0] > 0:
                    # cumulative MFE/MAE from entry (numpy)
                    cum_max_h = np.maximum.accumulate((highs - entry) / entry * 100)
                    cum_min_l = np.minimum.accumulate((lows - entry) / entry * 100)
                    cum_green = np.cumsum(greens)
                    # 3-bar momentum
                    mom3 = np.zeros(len(closes), dtype=np.float32)
                    for i in range(3, len(closes)):
                        if opens[i-3] > 0:
                            mom3[i] = (closes[i] - opens[i-3]) / opens[i-3] * 100

                    for cb in all_cb:
                        idx = cb - 6  # offset into our arrays
                        if idx < 0 or idx >= len(closes) or closes[idx] <= 0: continue
                        tick[cb] = {
                            'price': float(closes[idx]),
                            'pnl': float((closes[idx] - entry) / entry * 100),
                            'vwap': float((closes[idx] - vwaps[idx]) / vwaps[idx] * 100) if vwaps[idx] > 0 else 0.0,
                            'n_green': int(cum_green[idx]),
                            'n_total': idx + 1,
                            'mom3': float(mom3[idx]),
                            'mfe': float(cum_max_h[idx]),
                            'mae': float(cum_min_l[idx]),
                        }

                # Exit prices
                buy_exit = {}
                for eb in [19,29,34,39,44,49,59,65,89]:
                    if eb < nb and bkt[eb,C]>0: buy_exit[eb] = float(bkt[eb,C])

                sell_exit = {}
                for eb in [44,65,89]:
                    if eb < nb and bkt[eb,C]>0: sell_exit[eb] = float(bkt[eb,C])

                rec = {
                    'sym':r['symbol'],'gap':gap,'price':price,'entry':entry,
                    'sp':sp,'bp':bp,'mom':mom,'n_green_pre':n_green_pre,
                    'vwap_dev':vwap_dev,'avg_br':avg_br,
                    's6_buy':s6_buy,'s6_sell':s6_sell,
                    'buy_exit':buy_exit,'sell_exit':sell_exit,
                    'tick':tick,'date':r['date'],
                }

                if gap < -0.5: by_date_buy[r['date']].append(rec)
                if gap > 0.5:  by_date_sell[r['date']].append(rec)

                loaded += 1
                if loaded % 50000 == 0:
                    print(f"  {loaded} stocks loaded... {time.time()-t0:.0f}s")

    dates = sorted(set(list(by_date_buy.keys())+list(by_date_sell.keys())))
    mkt = {d: np.mean(all_gaps[d]) for d in dates}
    n_buy = sum(len(v) for v in by_date_buy.values())
    n_sell = sum(len(v) for v in by_date_sell.values())
    print(f"Buy pool: {n_buy}, Sell pool: {n_sell}, Days: {len(dates)} in {time.time()-t0:.1f}s")

    def is_b36(r):
        return r['gap']<-2 and r['s6_buy']>3 and r['bp']>0.55 and r['vwap_dev']>0

    # ─── CORRECT WEIGHTED-ENTRY SIZING ────────────────────────────────
    def sim_buy_sized(picks, check_bucket, exit_bucket, add_rule, exit_rule=None):
        results = []
        for r in picks:
            entry = r['entry']
            if exit_bucket not in r['buy_exit']:
                results.append((0.0, False, False)); continue
            exit_price = r['buy_exit'][exit_bucket]
            if check_bucket in r['tick']:
                tk = r['tick'][check_bucket]
                # EXIT check
                if exit_rule and exit_rule(tk):
                    pnl_pct = (tk['price'] - entry) / entry * 100 - COST
                    results.append((BASE*MARGIN*pnl_pct/100, False, True)); continue
                # ADD check
                add_mult = add_rule(tk)
                if add_mult > 0:
                    total_qty = 1.0 + add_mult
                    w_entry = (entry + tk['price'] * add_mult) / total_qty
                    pnl_pct = (exit_price - w_entry) / w_entry * 100 - COST
                    results.append((BASE*MARGIN*total_qty*pnl_pct/100, True, False)); continue
            pnl_pct = (exit_price - entry) / entry * 100 - COST
            results.append((BASE*MARGIN*pnl_pct/100, False, False))
        return results

    def sim_sell_sized(picks, check_bucket, exit_bucket, add_rule, exit_rule=None):
        results = []
        for r in picks:
            entry = r['entry']
            if exit_bucket not in r['sell_exit']:
                results.append((0.0, False, False)); continue
            exit_price = r['sell_exit'][exit_bucket]
            if check_bucket in r['tick']:
                tk = r['tick'][check_bucket]
                sell_pnl = (entry - tk['price']) / entry * 100
                sell_vwap = -tk['vwap']
                stk = {'pnl':sell_pnl,'vwap':sell_vwap,'n_green':tk['n_green'],
                       'n_total':tk['n_total'],'mom3':-tk['mom3'],'price':tk['price'],
                       'mfe':tk.get('mfe',0),'mae':tk.get('mae',0)}
                if exit_rule and exit_rule(stk):
                    pnl_pct = (entry - tk['price']) / entry * 100 - COST
                    results.append((BASE*MARGIN*pnl_pct/100, False, True)); continue
                add_mult = add_rule(stk)
                if add_mult > 0:
                    total_qty = 1.0 + add_mult
                    w_entry = (entry + tk['price']*add_mult) / total_qty
                    pnl_pct = (w_entry - exit_price) / w_entry * 100 - COST
                    results.append((BASE*MARGIN*total_qty*pnl_pct/100, True, False)); continue
            pnl_pct = (entry - exit_price) / entry * 100 - COST
            results.append((BASE*MARGIN*pnl_pct/100, False, False))
        return results

    def day_stats(day_pnls):
        if not day_pnls: return 0,0,0
        roc = sum(day_pnls)
        dw = sum(1 for p in day_pnls if p>0)/len(day_pnls)*100
        cum = np.cumsum(day_pnls)
        maxdd = float(min(cum - np.maximum.accumulate(cum)))
        return roc, dw, maxdd

    # ═══════════════════════════════════════════════════════════════════
    print("Running analysis sections...")
    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("DEEP BUY-SIDE POSITION SIZING ANALYSIS\n")
        out.write(f"B36: gap<-2% + S6buy>3 + bp>0.55 + above VWAP, exit b45\n")
        out.write(f"Buy pool: {n_buy}, Sell pool: {n_sell}, Days: {len(dates)}\n")
        out.write(f"Weighted-entry math: w_entry=(entry+check*mult)/(1+mult)\n\n")

        # ═══════════════════════════════════════
        # 1. BASELINE
        # ═══════════════════════════════════════
        out.write("="*110+"\n1. BASELINE B36 (no sizing)\n"+"="*110+"\n")
        all_b36 = [r for stocks in by_date_buy.values() for r in stocks if is_b36(r)]
        out.write(f"  Total B36 signals: {len(all_b36)} across {len(dates)} days\n\n")
        out.write(f"  {'TopN':>5} {'Trades':>7} {'TrdWin':>7} {'DayWin':>7} {'ROC':>8} {'AvgPnL':>8} {'MaxDD':>8}\n")
        out.write(f"  "+"-"*60+"\n")

        for n_pos in [1,2,3,4,5]:
            dpnls=[]; trades=0; wins=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:n_pos]
                if not pool: continue
                dp=0
                for r in pool:
                    if 44 in r['buy_exit']:
                        trades+=1
                        ret=(r['buy_exit'][44]-r['entry'])/r['entry']*100-COST
                        pnl=BASE*MARGIN*ret/100; dp+=pnl
                        if pnl>0: wins+=1
                dpnls.append(dp)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*n_pos)*100
            dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            cum=np.cumsum(dpnls); maxdd=float(min(cum-np.maximum.accumulate(cum)))
            out.write(f"  Top-{n_pos:>1}  {trades:>6}  {wins/max(trades,1)*100:>5.1f}%  {dw:>5.1f}%  {roc:>+7.1f}%  {sum(dpnls)/max(trades,1):>+7.0f}  {maxdd:>+7.0f}\n")
        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. CHECK BUCKET SWEEP
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n2. CHECK BUCKET SWEEP: when to check for ADD? (ADD 2x if pnl>0.3%)\n"+"="*110+"\n")
        out.write(f"  Top-3 B36\n\n")
        out.write(f"  {'CB':>4} {'Time':>6} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7} {'UnsW':>6}\n")
        out.write(f"  "+"-"*55+"\n")

        for cb in CHECK_BUCKETS:
            n_pos=3; dpnls=[]; n_sized=0;sw=0;uw=0;ul=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:n_pos]
                if not pool: continue
                res = sim_buy_sized(pool, cb, 44, lambda tk:2.0 if tk['pnl']>0.3 else 0)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws: n_sized+=1; sw+=(1 if pnl>0 else 0)
                    else: uw+=(1 if pnl>0 else 0); ul+=(1 if pnl<=0 else 0)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*n_pos)*100
            dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            h=9+(15+cb)//60; m=(15+cb)%60
            out.write(f"  b{cb:<3} {h}:{m:02d}  {roc:>+7.1f}%  {dw:>4.1f}%  {n_sized/max(total,1)*100:>5.1f}%  {sw/max(n_sized,1)*100:>5.1f}%  {uw/max(total-n_sized,1)*100:>4.1f}%\n")
        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. PNL THRESHOLD SWEEP
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n3. PNL THRESHOLD SWEEP: min P&L to trigger ADD 2x at b10\n"+"="*110+"\n\n")
        out.write(f"  {'Thresh':>8} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7}\n")
        out.write(f"  "+"-"*42+"\n")

        for thresh in [0.0,0.1,0.2,0.3,0.4,0.5,0.7,1.0,1.5,2.0]:
            dpnls=[]; ns=0;sw=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,lambda tk,t=thresh:2.0 if tk['pnl']>t else 0)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws: ns+=1; sw+=(1 if pnl>0 else 0)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100
            dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  >{thresh:>5.1f}%  {roc:>+7.1f}%  {dw:>4.1f}%  {ns/max(total,1)*100:>5.1f}%  {sw/max(ns,1)*100:>5.1f}%\n")
        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. VWAP CONDITION
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n4. VWAP CONDITION: does VWAP position matter for ADD?\n"+"="*110+"\n")
        out.write(f"  b10, pnl>0.3%, ADD 2x, top-3\n\n")
        out.write(f"  {'Rule':>25} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7}\n")
        out.write(f"  "+"-"*58+"\n")

        for name,rule in [
            ('No VWAP check',       lambda tk:2.0 if tk['pnl']>0.3 else 0),
            ('Above VWAP (>0%)',    lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0),
            ('Above VWAP (>0.2%)',  lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0.2 else 0),
            ('Above VWAP (>0.5%)',  lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0.5 else 0),
            ('Below VWAP (<0%)',    lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']<0 else 0),
        ]:
            dpnls=[]; ns=0;sw=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,rule)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws: ns+=1; sw+=(1 if pnl>0 else 0)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {name:>25}  {roc:>+7.1f}%  {dw:>4.1f}%  {ns/max(total,1)*100:>5.1f}%  {sw/max(ns,1)*100:>5.1f}%\n")
        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. MOMENTUM CONDITION
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n5. MOMENTUM / MFE / MAE AT CHECK: additional filters\n"+"="*110+"\n")
        out.write(f"  b10, pnl>0.3%, ADD 2x, top-3\n\n")
        out.write(f"  {'Rule':>30} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7}\n")
        out.write(f"  "+"-"*63+"\n")

        for name,rule in [
            ('No extra check',          lambda tk:2.0 if tk['pnl']>0.3 else 0),
            ('3-bar mom > 0%',          lambda tk:2.0 if tk['pnl']>0.3 and tk['mom3']>0 else 0),
            ('3-bar mom > 0.2%',        lambda tk:2.0 if tk['pnl']>0.3 and tk['mom3']>0.2 else 0),
            ('MFE > 0.5%',              lambda tk:2.0 if tk['pnl']>0.3 and tk['mfe']>0.5 else 0),
            ('MFE > 1.0%',              lambda tk:2.0 if tk['pnl']>0.3 and tk['mfe']>1.0 else 0),
            ('MAE > -0.3% (no dip)',    lambda tk:2.0 if tk['pnl']>0.3 and tk['mae']>-0.3 else 0),
            ('MAE > -0.5%',             lambda tk:2.0 if tk['pnl']>0.3 and tk['mae']>-0.5 else 0),
        ]:
            dpnls=[]; ns=0;sw=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,rule)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws: ns+=1; sw+=(1 if pnl>0 else 0)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {name:>30}  {roc:>+7.1f}%  {dw:>4.1f}%  {ns/max(total,1)*100:>5.1f}%  {sw/max(ns,1)*100:>5.1f}%\n")
        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. GREEN CANDLE COUNT
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n6. GREEN CANDLE COUNT AT CHECK\n"+"="*110+"\n")
        out.write(f"  b10, pnl>0.3%, ADD 2x, top-3\n\n")
        out.write(f"  {'Rule':>30} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7}\n")
        out.write(f"  "+"-"*63+"\n")

        for name,rule in [
            ('No green check',          lambda tk:2.0 if tk['pnl']>0.3 else 0),
            ('>=2 green (of ~4)',       lambda tk:2.0 if tk['pnl']>0.3 and tk['n_green']>=2 else 0),
            ('>=3 green',               lambda tk:2.0 if tk['pnl']>0.3 and tk['n_green']>=3 else 0),
            ('<2 green (mostly red)',   lambda tk:2.0 if tk['pnl']>0.3 and tk['n_green']<2 else 0),
        ]:
            dpnls=[]; ns=0;sw=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,rule)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws: ns+=1; sw+=(1 if pnl>0 else 0)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {name:>30}  {roc:>+7.1f}%  {dw:>4.1f}%  {ns/max(total,1)*100:>5.1f}%  {sw/max(ns,1)*100:>5.1f}%\n")
        print(f"  Section 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. MULTIPLIER SWEEP
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n7. MULTIPLIER SWEEP: how much extra to add?\n"+"="*110+"\n")
        out.write(f"  b10, pnl>0.3%+aboveVWAP, top-3\n\n")
        out.write(f"  {'Mult':>6} {'ROC':>8} {'DayW':>6} {'Sized%':>7} {'SizedW':>7} {'AvgWin':>8} {'AvgLoss':>8}\n")
        out.write(f"  "+"-"*60+"\n")

        for mult in [0.5,1.0,1.5,2.0,3.0,4.0,5.0]:
            dpnls=[]; ns=0;sw=0;total=0; s_wins=[]; s_loss=[]
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,lambda tk,m=mult:m if tk['pnl']>0.3 and tk['vwap']>0 else 0)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if ws:
                        ns+=1
                        if pnl>0: sw+=1; s_wins.append(pnl)
                        else: s_loss.append(pnl)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            aw=np.mean(s_wins) if s_wins else 0; al=np.mean(s_loss) if s_loss else 0
            out.write(f"  {mult:>5.1f}x  {roc:>+7.1f}%  {dw:>4.1f}%  {ns/max(total,1)*100:>5.1f}%  {sw/max(ns,1)*100:>5.1f}%  {aw:>+7.0f}  {al:>+7.0f}\n")
        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. EXIT LOGIC FOR LOSERS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n8. EXIT LOGIC: cut losers early at check bucket\n"+"="*110+"\n")
        out.write(f"  b10, no ADD, top-3\n\n")
        out.write(f"  {'Exit Rule':>35} {'ROC':>8} {'DayW':>6} {'Exit%':>6} {'Saved':>9}\n")
        out.write(f"  "+"-"*70+"\n")

        no_add = lambda tk:0
        for name,exit_r in [
            ('No exit (baseline)',              None),
            ('pnl < -0.2%',                    lambda tk:tk['pnl']<-0.2),
            ('pnl < -0.3%',                    lambda tk:tk['pnl']<-0.3),
            ('pnl < -0.5%',                    lambda tk:tk['pnl']<-0.5),
            ('pnl < -1.0%',                    lambda tk:tk['pnl']<-1.0),
            ('pnl<-0.2% + below VWAP',         lambda tk:tk['pnl']<-0.2 and tk['vwap']<0),
            ('pnl<-0.3% + below VWAP',         lambda tk:tk['pnl']<-0.3 and tk['vwap']<0),
            ('pnl<-0.5% + below VWAP',         lambda tk:tk['pnl']<-0.5 and tk['vwap']<0),
            ('mae < -1% (deep dip)',            lambda tk:tk['mae']<-1.0),
            ('mae < -1.5%',                     lambda tk:tk['mae']<-1.5),
        ]:
            dpnls=[]; nexited=0;total=0;saved=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,no_add,exit_r)
                dpnls.append(sum(r[0] for r in res))
                for idx,(pnl,ws,we) in enumerate(res):
                    total+=1
                    if we:
                        nexited+=1
                        r2=pool[idx]
                        if 44 in r2['buy_exit']:
                            plain=(r2['buy_exit'][44]-r2['entry'])/r2['entry']*100-COST
                            saved+=(pnl-BASE*MARGIN*plain/100)
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {name:>35}  {roc:>+7.1f}%  {dw:>4.1f}%  {nexited/max(total,1)*100:>4.1f}%  {saved:>+8.0f}\n")
        print(f"  Section 8 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 9. ADD vs EXIT vs BOTH
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n9. ADD-ONLY vs EXIT-ONLY vs ADD+EXIT\n"+"="*110+"\n")
        out.write(f"  b10, top-3\n\n")
        out.write(f"  {'Strategy':>30} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'MaxDD':>8}\n")
        out.write(f"  "+"-"*65+"\n")

        combos = [
            ('Plain (no sizing)',       lambda tk:0, None),
            ('ADD-only 2x pnl>0.3',    lambda tk:2.0 if tk['pnl']>0.3 else 0, None),
            ('ADD-only 2x pnl>0.3+vw', lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0, None),
            ('EXIT-only pnl<-0.3+vw',  lambda tk:0, lambda tk:tk['pnl']<-0.3 and tk['vwap']<0),
            ('ADD(2x)+EXIT',            lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0,
                                        lambda tk:tk['pnl']<-0.3 and tk['vwap']<0),
            ('ADD(3x)+EXIT',            lambda tk:3.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0,
                                        lambda tk:tk['pnl']<-0.3 and tk['vwap']<0),
            ('ADD(2x pnl>0)+EXIT',      lambda tk:2.0 if tk['pnl']>0 and tk['vwap']>0 else 0,
                                        lambda tk:tk['pnl']<-0.3 and tk['vwap']<0),
            ('ADD(2x)+EXIT(pnl<-0.5)',  lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0,
                                        lambda tk:tk['pnl']<-0.5 and tk['vwap']<0),
        ]
        for name,ar,er in combos:
            dpnls=[]; wins=0; total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,ar,er)
                dpnls.append(sum(r[0] for r in res))
                for pnl,ws,we in res:
                    total+=1
                    if pnl>0: wins+=1
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            tw=wins/max(total,1)*100
            cum=np.cumsum(dpnls); maxdd=float(min(cum-np.maximum.accumulate(cum)))
            out.write(f"  {name:>30}  {roc:>+7.1f}%  {dw:>4.1f}%  {tw:>4.1f}%  {maxdd:>+7.0f}\n")
        print(f"  Section 9 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 10. GRADUATED TIERS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n10. GRADUATED TIERS: 3x strong + 2x moderate\n"+"="*110+"\n")
        out.write(f"  b10, top-3\n\n")
        out.write(f"  {'Tier Rule':>45} {'ROC':>8} {'DayW':>6} {'3x%':>5} {'2x%':>5} {'1x%':>5}\n")
        out.write(f"  "+"-"*80+"\n")

        tiers = [
            ('Flat 2x if pnl>0.3',                  lambda tk:2.0 if tk['pnl']>0.3 else 0),
            ('Flat 3x if pnl>0.3',                  lambda tk:3.0 if tk['pnl']>0.3 else 0),
            ('3x pnl>0.5, 2x pnl>0.2',             lambda tk:3.0 if tk['pnl']>0.5 else (2.0 if tk['pnl']>0.2 else 0)),
            ('3x pnl>0.7, 2x pnl>0.3',             lambda tk:3.0 if tk['pnl']>0.7 else (2.0 if tk['pnl']>0.3 else 0)),
            ('3x pnl>1.0+vw>0, 2x pnl>0.3',       lambda tk:3.0 if tk['pnl']>1.0 and tk['vwap']>0 else (2.0 if tk['pnl']>0.3 else 0)),
            ('3x pnl>0.5+vw>0.3, 2x pnl>0.2',     lambda tk:3.0 if tk['pnl']>0.5 and tk['vwap']>0.3 else (2.0 if tk['pnl']>0.2 else 0)),
            ('3x pnl>0.5+green>=3, 2x pnl>0.2',   lambda tk:3.0 if tk['pnl']>0.5 and tk['n_green']>=3 else (2.0 if tk['pnl']>0.2 else 0)),
        ]
        for name,rule in tiers:
            dpnls=[]; n3=0;n2=0;n1=0;total=0
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                res = sim_buy_sized(pool,10,44,rule)
                dpnls.append(sum(r[0] for r in res))
                for idx,(pnl,ws,we) in enumerate(res):
                    total+=1
                    if ws:
                        r2=pool[idx]
                        if 10 in r2['tick']:
                            m=rule(r2['tick'][10])
                            if m>=3: n3+=1
                            elif m>=2: n2+=1
                            else: n1+=1
                        else: n1+=1
                    else: n1+=1
            if not dpnls: continue
            roc=sum(dpnls)/(BASE*3)*100; dw=sum(1 for p in dpnls if p>0)/len(dpnls)*100
            out.write(f"  {name:>45}  {roc:>+7.1f}%  {dw:>4.1f}%  {n3/max(total,1)*100:>3.1f}%  {n2/max(total,1)*100:>3.1f}%  {n1/max(total,1)*100:>3.1f}%\n")
        print(f"  Section 10 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 11. PROFIT TRAJECTORY
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n11. PROFIT TRAJECTORY: bucket-by-bucket avg P&L for top-3 B36\n"+"="*110+"\n")
        out.write(f"  When does the bounce happen? Crucial for timing ADD.\n\n")
        out.write(f"  {'Bucket':>7} {'Time':>6} {'AvgPnL':>8} {'Win%':>7} {'Median':>8} {'Std':>7}\n")
        out.write(f"  "+"-"*52+"\n")

        for bc in range(7,45):
            pnls=[]
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:3]
                for r in pool:
                    if bc in r['tick']: pnls.append(r['tick'][bc]['pnl'])
            if len(pnls)<10: continue
            h=9+(15+bc)//60; m=(15+bc)%60
            wr=sum(1 for p in pnls if p>0)/len(pnls)*100
            out.write(f"  b{bc:>4}   {h}:{m:02d}  {np.mean(pnls):>+7.3f}%  {wr:>5.1f}%  {np.median(pnls):>+7.3f}%  {np.std(pnls):>6.3f}%\n")
        print(f"  Section 11 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 12. CHERRY-PICK + SIZING
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n12. CHERRY-PICK TOP-N WITH SIZING\n"+"="*110+"\n")
        out.write(f"  ADD 2x@b10 if pnl>0.3%+aboveVWAP\n\n")
        out.write(f"  {'TopN':>5} {'PlainROC':>9} {'SizedROC':>9} {'Delta':>7} {'DayW':>6} {'MaxDD':>8}\n")
        out.write(f"  "+"-"*52+"\n")

        best_add = lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0
        for n_pos in [1,2,3,4,5]:
            plain_d=[]; sized_d=[]
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)], key=lambda x:-x['s6_buy'])[:n_pos]
                if not pool: continue
                dp=0
                for r in pool:
                    if 44 in r['buy_exit']:
                        dp+=BASE*MARGIN*((r['buy_exit'][44]-r['entry'])/r['entry']*100-COST)/100
                plain_d.append(dp)
                res = sim_buy_sized(pool,10,44,best_add)
                sized_d.append(sum(r[0] for r in res))
            if not plain_d: continue
            pr=sum(plain_d)/(BASE*n_pos)*100; sr=sum(sized_d)/(BASE*n_pos)*100
            dw=sum(1 for p in sized_d if p>0)/len(sized_d)*100
            cum=np.cumsum(sized_d); maxdd=float(min(cum-np.maximum.accumulate(cum)))
            out.write(f"  Top-{n_pos:>1}  {pr:>+8.1f}%  {sr:>+8.1f}%  {sr-pr:>+6.1f}%  {dw:>4.1f}%  {maxdd:>+7.0f}\n")
        print(f"  Section 12 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 13. COMBINED SELL+BUY MATRIX
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n13. COMBINED: SELL(sized) + BUY(sized) MATRIX\n"+"="*110+"\n")
        out.write(f"  SELL: ADD 2x@b15 if pnl>0.3%+belowVWAP, exit b90\n")
        out.write(f"  BUY:  ADD 2x@b10 if pnl>0.3%+aboveVWAP, exit b45\n\n")

        sell_ar = lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0
        buy_ar  = lambda tk:2.0 if tk['pnl']>0.3 and tk['vwap']>0 else 0

        out.write(f"  {'Combo':>8} {'Plain':>8} {'S-sized':>8} {'B-sized':>8} {'Both':>8} {'BothDW':>7} {'BothDD':>8}\n")
        out.write(f"  "+"-"*62+"\n")

        for ns,nb in [(7,0),(6,1),(6,2),(5,2),(5,3),(4,3),(7,1),(7,2),(7,3)]:
            vals={}
            for vari in ['plain','s_sized','b_sized','both']:
                dpnls=[]
                for d in dates:
                    sp2 = sorted(by_date_sell.get(d,[]),key=lambda x:-x['s6_sell'])[:ns]
                    bp2 = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)],key=lambda x:-x['s6_buy'])[:nb]
                    if not sp2 and not bp2: continue
                    dp=0
                    if vari in ('s_sized','both'):
                        dp+=sum(r[0] for r in sim_sell_sized(sp2,14,89,sell_ar))
                    else:
                        for r in sp2:
                            if 89 in r['sell_exit']:
                                dp+=BASE*MARGIN*((r['entry']-r['sell_exit'][89])/r['entry']*100-COST)/100
                    if vari in ('b_sized','both'):
                        dp+=sum(r[0] for r in sim_buy_sized(bp2,10,44,buy_ar))
                    else:
                        for r in bp2:
                            if 44 in r['buy_exit']:
                                dp+=BASE*MARGIN*((r['buy_exit'][44]-r['entry'])/r['entry']*100-COST)/100
                    dpnls.append(dp)
                tp=max(ns+nb,1)
                roc=sum(dpnls)/(BASE*tp)*100 if dpnls else 0
                dw=sum(1 for p in dpnls if p>0)/max(len(dpnls),1)*100
                cum=np.cumsum(dpnls) if dpnls else np.array([0])
                maxdd=float(min(cum-np.maximum.accumulate(cum)))
                vals[vari]=(roc,dw,maxdd)
            combo=f"{ns}S+{nb}B"
            out.write(f"  {combo:>8}  {vals['plain'][0]:>+7.1f}%  {vals['s_sized'][0]:>+7.1f}%  {vals['b_sized'][0]:>+7.1f}%  {vals['both'][0]:>+7.1f}%  {vals['both'][1]:>5.1f}%  {vals['both'][2]:>+7.0f}\n")
        print(f"  Section 13 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 14. RISK ANALYSIS
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n14. RISK ANALYSIS: plain vs sized for top-3 B36 buy-only\n"+"="*110+"\n\n")

        for label,use_sizing in [('Plain',False),('Sized (2x@b10 pnl>0.3+vw)',True)]:
            dpnls=[]
            for d in dates:
                pool = sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)],key=lambda x:-x['s6_buy'])[:3]
                if not pool: continue
                if use_sizing:
                    res = sim_buy_sized(pool,10,44,best_add)
                    dpnls.append((d,sum(r[0] for r in res)))
                else:
                    dp=0
                    for r in pool:
                        if 44 in r['buy_exit']:
                            dp+=BASE*MARGIN*((r['buy_exit'][44]-r['entry'])/r['entry']*100-COST)/100
                    dpnls.append((d,dp))

            pnls=[p for _,p in dpnls]
            cum=np.cumsum(pnls)
            maxdd=float(min(cum-np.maximum.accumulate(cum)))
            maxdd_pct=maxdd/(BASE*3)*100
            sharpe=np.mean(pnls)/np.std(pnls)*np.sqrt(252) if np.std(pnls)>0 else 0
            wdays=sum(1 for p in pnls if p>0)
            avg_w=np.mean([p for p in pnls if p>0]) if any(p>0 for p in pnls) else 0
            avg_l=np.mean([p for p in pnls if p<=0]) if any(p<=0 for p in pnls) else 0
            pf=abs(sum(p for p in pnls if p>0)/min(sum(p for p in pnls if p<=0),-1))
            roc=sum(pnls)/(BASE*3)*100

            out.write(f"  {label}:\n")
            out.write(f"    Total ROC:      {roc:+.1f}%\n")
            out.write(f"    Day Win Rate:   {wdays}/{len(pnls)} ({wdays/len(pnls)*100:.1f}%)\n")
            out.write(f"    Avg Win Day:    Rs {avg_w:+.0f}\n")
            out.write(f"    Avg Loss Day:   Rs {avg_l:+.0f}\n")
            out.write(f"    Best Day:       Rs {max(pnls):+.0f}\n")
            out.write(f"    Worst Day:      Rs {min(pnls):+.0f}\n")
            out.write(f"    Max Drawdown:   Rs {maxdd:+.0f} ({maxdd_pct:+.1f}%)\n")
            out.write(f"    Profit Factor:  {pf:.2f}\n")
            out.write(f"    Sharpe (ann.):  {sharpe:.2f}\n\n")

        # Worst/best days
        for d2,p in sorted(dpnls,key=lambda x:x[1])[:5]:
            dow=datetime.date.fromisoformat(d2).strftime('%A')
            out.write(f"  Worst: {d2} ({dow}): Rs {p:+.0f}  mktGap={mkt.get(d2,0):+.1f}%\n")
        out.write("\n")
        for d2,p in sorted(dpnls,key=lambda x:-x[1])[:5]:
            dow=datetime.date.fromisoformat(d2).strftime('%A')
            out.write(f"  Best:  {d2} ({dow}): Rs {p:+.0f}  mktGap={mkt.get(d2,0):+.1f}%\n")

        # ═══════════════════════════════════════
        # 15. FINAL COMBINED RECOMMENDATION
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n15. FINAL RECOMMENDATION: combined with risk\n"+"="*110+"\n\n")

        for ns,nb,label in [(6,1,'6S+1B'),(7,0,'7S+0B'),(7,2,'7S+2B'),(6,2,'6S+2B')]:
            for lbl2,do_size in [('plain',False),('sized',True)]:
                dpnls=[]
                for d in dates:
                    sp2=sorted(by_date_sell.get(d,[]),key=lambda x:-x['s6_sell'])[:ns]
                    bp2=sorted([r for r in by_date_buy.get(d,[]) if is_b36(r)],key=lambda x:-x['s6_buy'])[:nb]
                    if not sp2 and not bp2: continue
                    dp=0
                    if do_size:
                        dp+=sum(r[0] for r in sim_sell_sized(sp2,14,89,sell_ar))
                        dp+=sum(r[0] for r in sim_buy_sized(bp2,10,44,buy_ar))
                    else:
                        for r in sp2:
                            if 89 in r['sell_exit']:
                                dp+=BASE*MARGIN*((r['entry']-r['sell_exit'][89])/r['entry']*100-COST)/100
                        for r in bp2:
                            if 44 in r['buy_exit']:
                                dp+=BASE*MARGIN*((r['buy_exit'][44]-r['entry'])/r['entry']*100-COST)/100
                    dpnls.append(dp)
                tp=max(ns+nb,1)
                roc=sum(dpnls)/(BASE*tp)*100 if dpnls else 0
                dw=sum(1 for p in dpnls if p>0)/max(len(dpnls),1)*100
                cum=np.cumsum(dpnls) if dpnls else np.array([0])
                maxdd=float(min(cum-np.maximum.accumulate(cum)))
                sharpe=np.mean(dpnls)/np.std(dpnls)*np.sqrt(252) if len(dpnls)>1 and np.std(dpnls)>0 else 0
                out.write(f"  {label:>6} {lbl2:>6}: ROC={roc:>+7.1f}% dayWin={dw:>5.1f}% maxDD=Rs{maxdd:>+7.0f} sharpe={sharpe:.2f}\n")
            out.write("\n")

        # ═══════════════════════════════════════
        # 16. IMPLEMENTATION
        # ═══════════════════════════════════════
        out.write("="*110+"\n16. IMPLEMENTATION GUIDE\n"+"="*110+"\n")
        out.write("""
  BUY-SIDE POSITION SIZING (to add to engine):
    Check bucket: b10 (9:25 AM)
    ADD: if pnl > 0.3% AND price > VWAP -> add 2x (total 3x position)
    EXIT: if pnl < -0.3% AND price < VWAP -> close at check price
    Weighted entry: w = (entry*1 + check*2) / 3

  ENGINE CHANGE NEEDED:
    In simulate_day_circuit_breaker, the sizing check currently skips BUY:
      if cfg.sizing_enabled && signal.direction == Direction::Sell { ... }
    Change to: remove direction filter, but use direction-aware VWAP:
      BUY:  vwap_pos > 0 is good (above VWAP = recovery)
      SELL: vwap_pos < 0 is good (below VWAP = weakness)

  NEW CONFIG FIELDS (optional):
    buy_sizing_check_bucket = 10 (separate from sell's 15/20)
""")

    print(f"\nCompleted in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
