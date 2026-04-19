"""
MEGA FULL-DAY ANALYSIS — All remaining brainstorm ideas
=========================================================
Single load, covers:
  1. ORB (Opening Range Breakout) — 15min / 30min
  2. VWAP bands — reversion when price deviates from VWAP
  3. Intraday dip buying — buy stocks that drop X% from day open
  4. Buy after sell-reversal completes (second leg)
  5. First-hour support/resistance
  6. Pattern triggers (VWAP cross, volume spike, range break)
  7. Momentum persistence (which stocks trend vs mean-revert)
  8. Full-day sell patterns (beyond gap reversal)
  9. Adaptive exit: how features at b30 predict remaining move
  10. Cross-stock confirmation (when many gap-ups reverse together)
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'mega_fullday_analysis.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15
MAX_B = 375

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading full-day data...")
    by_date = defaultdict(list)
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
                by_date[r['date']].append({'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],'bkt':bkt})

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} records across {len(dates)} days in {time.time()-t0:.1f}s")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write(f"MEGA FULL-DAY ANALYSIS\nData: {n} records, {len(dates)} days, {MAX_B} buckets/stock\n\n")

        # ═══════════════════════════════════════════════════
        # 1. ORB — Opening Range Breakout
        # ═══════════════════════════════════════════════════
        out.write("="*110+"\n1. OPENING RANGE BREAKOUT (ORB)\n"+"="*110+"\n")
        for or_len, or_label in [(15,'OR15 (9:15-9:30)'),(30,'OR30 (9:15-9:45)'),(45,'OR45 (9:15-10:00)')]:
            out.write(f"\n  {or_label}:\n")
            sells=[]; buys=[]
            for d in dates:
                for s in by_date[d]:
                    bkt=s['bkt']
                    if bkt[0,O]<=0: continue
                    or_h = float(np.max(bkt[:or_len,H]))
                    or_l = float(np.min(bkt[:or_len,L]))
                    if or_h<=0 or or_l<=0 or or_h==or_l: continue
                    or_range = (or_h-or_l)/or_h*100

                    # Check breakout in next 5 buckets after OR
                    for b in range(or_len, min(or_len+5, MAX_B)):
                        if bkt[b,H] > or_h:  # BUY breakout
                            entry = bkt[b,C]
                            if entry<=0: continue
                            for hold in [15,30,45,60]:
                                eb = min(b+hold, MAX_B-1)
                                if bkt[eb,C]>0:
                                    ret = (bkt[eb,C]-entry)/entry*100-COST
                                    buys.append((hold, ret, or_range, s['gap']))
                            break
                        if bkt[b,L] < or_l:  # SELL breakout
                            entry = bkt[b,C]
                            if entry<=0: continue
                            for hold in [15,30,45,60]:
                                eb = min(b+hold, MAX_B-1)
                                if bkt[eb,C]>0:
                                    ret = (entry-bkt[eb,C])/entry*100-COST
                                    sells.append((hold, ret, or_range, s['gap']))
                            break

            out.write(f"    SELL breakdowns (below OR low):\n")
            out.write(f"    {'Hold':>6} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
            for hold in [15,30,45,60]:
                sub = [r for h,r,_,_ in sells if h==hold]
                if len(sub)<30: continue
                wr=sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"    {hold:>4}m {len(sub):>6} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")

            out.write(f"    BUY breakouts (above OR high):\n")
            for hold in [15,30,45,60]:
                sub = [r for h,r,_,_ in buys if h==hold]
                if len(sub)<30: continue
                wr=sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"    {hold:>4}m {len(sub):>6} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 2. VWAP REVERSION — enter when price deviates from VWAP
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n2. VWAP REVERSION TRADES\n"+"="*110+"\n")
        out.write("  When price deviates X% from VWAP, does it snap back?\n\n")

        vwap_trades = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                for scan_b in range(15, 300, 5):  # scan every 5 buckets from 9:30 to 2:15
                    if bkt[scan_b,VW]<=0 or bkt[scan_b,C]<=0: continue
                    dev = (bkt[scan_b,C]-bkt[scan_b,VW])/bkt[scan_b,VW]*100
                    if abs(dev) < 0.5: continue  # need meaningful deviation

                    entry = bkt[scan_b,C]
                    # If price ABOVE VWAP: SELL (expect snap back down to VWAP)
                    # If price BELOW VWAP: BUY (expect snap back up to VWAP)
                    direction = 'sell' if dev > 0 else 'buy'
                    for hold in [15, 30]:
                        eb = min(scan_b+hold, MAX_B-1)
                        if bkt[eb,C]<=0: continue
                        if direction == 'sell':
                            ret = (entry-bkt[eb,C])/entry*100-COST
                        else:
                            ret = (bkt[eb,C]-entry)/entry*100-COST
                        vwap_trades.append((direction, abs(dev), hold, ret, scan_b))

        out.write(f"  Total VWAP deviation entries: {len(vwap_trades)}\n\n")
        for direction in ['sell','buy']:
            out.write(f"  {direction.upper()} when price {'above' if direction=='sell' else 'below'} VWAP:\n")
            out.write(f"    {'Deviation':>10} {'Hold':>5} {'N':>7} {'Win%':>6} {'AvgRet':>8}\n")
            for dev_lo,dev_hi,dlbl in [(0.5,1,'0.5-1%'),(1,1.5,'1-1.5%'),(1.5,2,'1.5-2%'),(2,3,'2-3%'),(3,99,'3%+')]:
                for hold in [15,30]:
                    sub = [r for dir,dv,h,r,_ in vwap_trades if dir==direction and dev_lo<=dv<dev_hi and h==hold]
                    if len(sub)<50: continue
                    wr = sum(1 for r in sub if r>0)/len(sub)*100
                    out.write(f"    {dlbl:>10} {hold:>4}m {len(sub):>7} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")
            out.write("\n")

        # ═══════════════════════════════════════════════════
        # 3. INTRADAY DIP BUYING
        # ═══════════════════════════════════════════════════
        out.write("="*110+"\n3. INTRADAY DIP BUYING (buy when stock drops X% from day open)\n"+"="*110+"\n")
        dip_buys = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                day_open = bkt[0,O]
                if day_open<=0: continue
                for scan_b in range(15, 300, 5):
                    if bkt[scan_b,C]<=0: continue
                    drop = (day_open-bkt[scan_b,C])/day_open*100  # positive = stock dropped
                    if drop < 1: continue  # need at least 1% drop
                    entry = bkt[scan_b,C]
                    for hold in [15,30,60]:
                        eb = min(scan_b+hold, MAX_B-1)
                        if bkt[eb,C]<=0: continue
                        ret = (bkt[eb,C]-entry)/entry*100-COST
                        dip_buys.append((drop, hold, ret, scan_b))
                    break  # only first dip per stock per day

        out.write(f"  Dip buy entries: {len(dip_buys)}\n")
        out.write(f"  {'Drop%':>8} {'Hold':>5} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        for dlo,dhi,dlbl in [(1,1.5,'1-1.5%'),(1.5,2,'1.5-2%'),(2,3,'2-3%'),(3,5,'3-5%'),(5,99,'5%+')]:
            for hold in [15,30,60]:
                sub = [r for dp,h,r,_ in dip_buys if dlo<=dp<dhi and h==hold]
                if len(sub)<30: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"  {dlbl:>8} {hold:>4}m {len(sub):>6} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 4. BUY AFTER GAP-UP REVERSAL COMPLETES
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n4. BUY AFTER SELL-REVERSAL COMPLETES (second leg bounce)\n"+"="*110+"\n")
        second_leg = []
        for d in dates:
            for s in by_date[d]:
                if s['gap'] <= 1: continue  # need gap-up stock
                bkt = s['bkt']
                entry_sell = bkt[6,O]
                if entry_sell<=0: continue
                # Find the MFE point (lowest price from b7 to b90)
                min_price = entry_sell
                mfe_bucket = 7
                for b in range(7,90):
                    if bkt[b,L]>0 and bkt[b,L]<min_price:
                        min_price = bkt[b,L]; mfe_bucket = b
                if mfe_bucket < 20 or mfe_bucket > 80: continue  # MFE too early or too late
                sell_profit = (entry_sell-min_price)/entry_sell*100
                if sell_profit < 0.5: continue  # need actual reversal

                # NOW: buy at MFE bucket close, hold for 30-60 buckets
                buy_entry = bkt[mfe_bucket, C]
                if buy_entry<=0: continue
                for hold in [15,30,45,60]:
                    eb = min(mfe_bucket+hold, MAX_B-1)
                    if bkt[eb,C]<=0: continue
                    buy_ret = (bkt[eb,C]-buy_entry)/buy_entry*100-COST
                    second_leg.append((sell_profit, hold, buy_ret, mfe_bucket))

        out.write(f"  Second-leg buy entries: {len(second_leg)}\n")
        out.write(f"  {'SellProfit':>12} {'Hold':>5} {'N':>6} {'Win%':>6} {'BuyRet':>8}\n")
        for slo,shi,slbl in [(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,3,'2-3%'),(3,99,'3%+')]:
            for hold in [15,30,60]:
                sub = [r for sp,h,r,_ in second_leg if slo<=sp<shi and h==hold]
                if len(sub)<20: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"  {slbl:>12} {hold:>4}m {len(sub):>6} {wr:>5.1f}% {np.mean(sub):>+7.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 5. FIRST-HOUR SUPPORT/RESISTANCE
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n5. FIRST-HOUR HIGH/LOW AS SUPPORT/RESISTANCE\n"+"="*110+"\n")
        sr_trades = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                # First hour: buckets 0-59 (9:15-10:15)
                fh_high = float(np.max(bkt[:60,H]))
                fh_low = float(np.min(bkt[:60,L]))
                if fh_high<=0 or fh_low<=0 or fh_high==fh_low: continue

                # After first hour, check for touches
                for scan_b in range(60, 300):
                    if bkt[scan_b,C]<=0: continue
                    # Touch support (first-hour low)
                    if bkt[scan_b,L] <= fh_low * 1.002:  # within 0.2% of fh_low
                        entry = bkt[scan_b,C]
                        for hold in [15,30]:
                            eb = min(scan_b+hold, MAX_B-1)
                            if bkt[eb,C]<=0: continue
                            ret = (bkt[eb,C]-entry)/entry*100-COST  # BUY at support
                            sr_trades.append(('support', hold, ret))
                        break
                for scan_b in range(60, 300):
                    if bkt[scan_b,C]<=0: continue
                    if bkt[scan_b,H] >= fh_high * 0.998:
                        entry = bkt[scan_b,C]
                        for hold in [15,30]:
                            eb = min(scan_b+hold, MAX_B-1)
                            if bkt[eb,C]<=0: continue
                            ret = (entry-bkt[eb,C])/entry*100-COST  # SELL at resistance
                            sr_trades.append(('resistance', hold, ret))
                        break

        out.write(f"  S/R touches: {len(sr_trades)}\n")
        for sr_type in ['support','resistance']:
            out.write(f"\n  {'BUY' if sr_type=='support' else 'SELL'} at first-hour {sr_type}:\n")
            for hold in [15,30]:
                sub = [r for tp,h,r in sr_trades if tp==sr_type and h==hold]
                if len(sub)<30: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"    Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 6. VOLUME SPIKE DETECTION
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n6. VOLUME SPIKE ENTRIES (3x normal volume = something happening)\n"+"="*110+"\n")
        vol_spike = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                for scan_b in range(30, 300):
                    if bkt[scan_b,V]<=0: continue
                    # Avg volume of last 20 buckets
                    avg_vol = float(np.mean(bkt[max(scan_b-20,0):scan_b, V]))
                    if avg_vol <= 0: continue
                    spike = bkt[scan_b,V]/avg_vol
                    if spike < 3: continue  # need 3x spike

                    # Direction: was the spike candle up or down?
                    candle_dir = 'up' if bkt[scan_b,C] > bkt[scan_b,O] else 'down'
                    entry = bkt[scan_b,C]
                    if entry<=0: continue

                    for hold in [15,30]:
                        eb = min(scan_b+hold, MAX_B-1)
                        if bkt[eb,C]<=0: continue
                        # Follow the spike direction
                        if candle_dir == 'up':
                            ret = (bkt[eb,C]-entry)/entry*100-COST  # BUY
                        else:
                            ret = (entry-bkt[eb,C])/entry*100-COST  # SELL
                        vol_spike.append((candle_dir, spike, hold, ret, scan_b))
                    break  # one spike per stock per day

        out.write(f"  Volume spike entries: {len(vol_spike)}\n")
        for direction in ['up','down']:
            out.write(f"\n  {'BUY' if direction=='up' else 'SELL'} on {direction} spike:\n")
            for hold in [15,30]:
                sub = [r for dir,sp,h,r,_ in vol_spike if dir==direction and h==hold]
                if len(sub)<30: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"    Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

            # By spike magnitude
            out.write(f"    By spike size (hold 30m):\n")
            for slo,shi,slbl in [(3,5,'3-5x'),(5,10,'5-10x'),(10,99,'10x+')]:
                sub = [r for dir,sp,h,r,_ in vol_spike if dir==direction and slo<=sp<shi and h==30]
                if len(sub)<20: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                out.write(f"      {slbl}: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 7. MOMENTUM PERSISTENCE (trend vs mean-revert stocks)
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n7. MOMENTUM PERSISTENCE — which stocks TREND vs REVERT?\n"+"="*110+"\n")
        stock_autocorr = defaultdict(list)
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                # First half return (9:15-12:15) vs second half return (12:15-3:15)
                mid = 180  # bucket 180 = 12:15 PM
                if bkt[0,O]<=0 or bkt[mid,C]<=0 or bkt[360,C]<=0: continue
                first_half = (bkt[mid,C]-bkt[0,O])/bkt[0,O]*100
                second_half = (bkt[360,C]-bkt[mid,C])/bkt[mid,C]*100
                # Same direction = momentum persistence, opposite = mean reversion
                same_dir = (first_half > 0 and second_half > 0) or (first_half < 0 and second_half < 0)
                stock_autocorr[s['sym']].append((first_half, second_half, same_dir))

        # Aggregate: what % of stocks continue vs revert?
        persist_counts = {'continue':0, 'reverse':0, 'flat':0}
        all_pairs = []
        for sym, pairs in stock_autocorr.items():
            for fh, sh, same in pairs:
                if abs(fh) < 0.3: persist_counts['flat'] += 1; continue
                if same: persist_counts['continue'] += 1
                else: persist_counts['reverse'] += 1
                all_pairs.append((fh, sh, same))

        total_meaningful = persist_counts['continue'] + persist_counts['reverse']
        out.write(f"  Stocks with meaningful first-half move (>0.3%):\n")
        out.write(f"    Continue (trend): {persist_counts['continue']} ({persist_counts['continue']/max(total_meaningful,1)*100:.1f}%)\n")
        out.write(f"    Reverse (revert): {persist_counts['reverse']} ({persist_counts['reverse']/max(total_meaningful,1)*100:.1f}%)\n")
        out.write(f"    Flat first half:  {persist_counts['flat']}\n\n")

        # By first-half move size
        out.write(f"  By first-half move size:\n")
        out.write(f"  {'1stHalf':>10} {'N':>6} {'Continue%':>10} {'AvgCont':>8} {'AvgRevert':>10}\n")
        for flo,fhi,flbl in [(-99,-2,'<-2%'),(-2,-1,'-2~-1%'),(-1,-0.3,'-1~-0.3%'),(0.3,1,'0.3~1%'),(1,2,'1~2%'),(2,99,'>2%')]:
            sub = [(fh,sh,sd) for fh,sh,sd in all_pairs if flo<=fh<fhi]
            if len(sub)<30: continue
            cont = sum(1 for _,_,sd in sub if sd)/len(sub)*100
            cont_rets = [sh for _,sh,sd in sub if sd]
            rev_rets = [sh for _,sh,sd in sub if not sd]
            out.write(f"  {flbl:>10} {len(sub):>6} {cont:>9.1f}% {np.mean(cont_rets) if cont_rets else 0:>+7.3f}% {np.mean(rev_rets) if rev_rets else 0:>+9.3f}%\n")

        # Strategy: BUY stocks that dropped >1% in morning, SELL stocks that rose >1%
        out.write(f"\n  STRATEGY: After morning move, trade the REVERSAL:\n")
        for direction, flo, fhi, label in [('buy',-99,-1,'Morning drop >1% -> BUY afternoon'),
                                            ('sell',1,99,'Morning rise >1% -> SELL afternoon')]:
            sub = [(fh,sh) for fh,sh,_ in all_pairs if flo<=fh<fhi]
            if not sub: continue
            if direction == 'buy':
                rets = [sh for _,sh in sub]
            else:
                rets = [-sh for _,sh in sub]
            wr = sum(1 for r in rets if r>0)/len(rets)*100
            out.write(f"    {label}: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(rets):+.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 8. ADAPTIVE EXIT: features at b30 predict remaining move
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n8. ADAPTIVE EXIT: what does mid-trade data tell us?\n"+"="*110+"\n")
        out.write("  For gap-up sell trades: at b30 (9:44 AM), check live features -> decide exit\n\n")

        exit_data = []
        for d in dates:
            for s in by_date[d]:
                if s['gap'] <= 0.5: continue
                bkt = s['bkt']
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0: continue
                pnl_b30 = (entry-bkt[29,C])/entry*100 if bkt[29,C]>0 else 0
                pnl_b66 = (entry-bkt[65,C])/entry*100-COST if bkt[65,C]>0 else 0
                pnl_b90 = (entry-bkt[89,C])/entry*100-COST if bkt[89,C]>0 else 0
                # Features at b30
                above_vwap_b30 = bkt[29,C]>bkt[29,VW] if bkt[29,VW]>0 else False
                n_green_since_entry = sum(1 for b in range(7,30) if bkt[b,C]>bkt[b,O])
                exit_data.append({
                    'pnl_b30':pnl_b30, 'pnl_b66':pnl_b66, 'pnl_b90':pnl_b90,
                    'profitable_b30': pnl_b30>0,
                    'above_vwap_b30': above_vwap_b30,
                    'n_green_since': n_green_since_entry,
                    'remaining_b66': pnl_b66-pnl_b30,
                    'remaining_b90': pnl_b90-pnl_b30,
                })

        out.write(f"  Gap-up sell trades: {len(exit_data)}\n\n")

        # If profitable at b30, should we hold or exit?
        prof = [e for e in exit_data if e['profitable_b30']]
        loss = [e for e in exit_data if not e['profitable_b30']]
        out.write(f"  Profitable at b30: {len(prof)} ({len(prof)/len(exit_data)*100:.0f}%)\n")
        out.write(f"    Hold to b66: avg remaining = {np.mean([e['remaining_b66'] for e in prof]):+.3f}%\n")
        out.write(f"    Hold to b90: avg remaining = {np.mean([e['remaining_b90'] for e in prof]):+.3f}%\n")
        out.write(f"    -> {'HOLD' if np.mean([e['remaining_b90'] for e in prof])>0 else 'EXIT at b30'} for winners\n\n")

        out.write(f"  Losing at b30: {len(loss)} ({len(loss)/len(exit_data)*100:.0f}%)\n")
        out.write(f"    Hold to b66: avg remaining = {np.mean([e['remaining_b66'] for e in loss]):+.3f}%\n")
        out.write(f"    Hold to b90: avg remaining = {np.mean([e['remaining_b90'] for e in loss]):+.3f}%\n")
        out.write(f"    -> {'HOLD' if np.mean([e['remaining_b90'] for e in loss])>0 else 'EXIT at b30'} for losers\n\n")

        # Above VWAP at b30 (thesis broken)
        above = [e for e in exit_data if e['above_vwap_b30']]
        below = [e for e in exit_data if not e['above_vwap_b30']]
        out.write(f"  Price ABOVE VWAP at b30 (thesis broken): {len(above)}\n")
        out.write(f"    Final b66 avg: {np.mean([e['pnl_b66'] for e in above]):+.3f}%\n")
        out.write(f"    Final b90 avg: {np.mean([e['pnl_b90'] for e in above]):+.3f}%\n")
        out.write(f"  Price BELOW VWAP at b30 (thesis intact): {len(below)}\n")
        out.write(f"    Final b66 avg: {np.mean([e['pnl_b66'] for e in below]):+.3f}%\n")
        out.write(f"    Final b90 avg: {np.mean([e['pnl_b90'] for e in below]):+.3f}%\n\n")

        # Green candles count since entry
        out.write(f"  Green candles b7-b30 vs outcome:\n")
        for glo,ghi,glbl in [(0,5,'0-4 green'),(5,8,'5-7 green'),(8,12,'8-11 green'),(12,24,'12+ green')]:
            sub = [e for e in exit_data if glo<=e['n_green_since']<ghi]
            if len(sub)<50: continue
            wr66 = sum(1 for e in sub if e['pnl_b66']>0)/len(sub)*100
            out.write(f"    {glbl:>12}: N={len(sub)}, b66 win={wr66:.1f}%, avg={np.mean([e['pnl_b66'] for e in sub]):+.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 9. CROSS-STOCK CONFIRMATION
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n9. CROSS-STOCK: when many gap-ups reverse, does it help individual picks?\n"+"="*110+"\n")
        for d in dates:
            gapup = [s for s in by_date[d] if s['gap']>0.5]
            if len(gapup)<5: continue
            # Count how many reversed by b30
            reversing = 0
            for s in gapup:
                bkt = s['bkt']
                if bkt[0,O]>0 and bkt[29,C]>0 and bkt[29,C]<bkt[0,O]:
                    reversing += 1
            reversal_pct = reversing/len(gapup)*100
            # Tag each stock with the day's reversal rate
            for s in gapup:
                s['_day_reversal_pct'] = reversal_pct

        out.write(f"  When X% of gap-up stocks are reversing at b30, what's the top-8 win rate at b66?\n")
        for rlo,rhi,rlbl in [(0,40,'<40%'),(40,50,'40-50%'),(50,60,'50-60%'),(60,70,'60-70%'),(70,100,'>70%')]:
            pool = []
            for d in dates:
                gapup = [s for s in by_date[d] if s['gap']>0.5 and hasattr(s,'get') and '_day_reversal_pct' in s]
                if not gapup: continue
                if not (rlo <= gapup[0].get('_day_reversal_pct',50) < rhi): continue
                top8 = sorted(gapup, key=lambda x:-x['gap'])[:8]
                for s in top8:
                    bkt=s['bkt']; entry=bkt[6,O]
                    if entry>0 and bkt[65,C]>0:
                        ret = (entry-bkt[65,C])/entry*100-COST
                        pool.append(ret)
            if len(pool)<20: continue
            wr = sum(1 for r in pool if r>0)/len(pool)*100
            out.write(f"    {rlbl:>8} reversal days: N={len(pool)}, Win={wr:.1f}%, AvgRet={np.mean(pool):+.3f}%\n")

        # ═══════════════════════════════════════════════════
        # 10. NON-GAP PATTERNS: pure intraday without gap requirement
        # ═══════════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n10. NON-GAP PATTERNS (no gap requirement)\n"+"="*110+"\n")

        # 10a. 3 consecutive red candles at any time -> SELL for 15m
        out.write("\n10a. 3 consecutive red candles -> SELL (hold 15-30m)\n")
        red3_sells = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                for b in range(15, 300):
                    if bkt[b,C]<bkt[b,O] and bkt[b+1,C]<bkt[b+1,O] and bkt[b+2,C]<bkt[b+2,O]:
                        entry = bkt[b+3,O]
                        if entry<=0: continue
                        for hold in [15,30]:
                            eb = min(b+3+hold, MAX_B-1)
                            if bkt[eb,C]<=0: continue
                            ret = (entry-bkt[eb,C])/entry*100-COST
                            red3_sells.append((hold, ret, b))
                        break
        for hold in [15,30]:
            sub = [r for h,r,_ in red3_sells if h==hold]
            if len(sub)<50: continue
            wr = sum(1 for r in sub if r>0)/len(sub)*100
            out.write(f"  Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

        # 10b. 3 consecutive green candles -> BUY
        out.write("\n10b. 3 consecutive green candles -> BUY (hold 15-30m)\n")
        green3_buys = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                for b in range(15, 300):
                    if bkt[b,C]>bkt[b,O] and bkt[b+1,C]>bkt[b+1,O] and bkt[b+2,C]>bkt[b+2,O]:
                        entry = bkt[b+3,O]
                        if entry<=0: continue
                        for hold in [15,30]:
                            eb = min(b+3+hold, MAX_B-1)
                            if bkt[eb,C]<=0: continue
                            ret = (bkt[eb,C]-entry)/entry*100-COST
                            green3_buys.append((hold, ret, b))
                        break
        for hold in [15,30]:
            sub = [r for h,r,_ in green3_buys if h==hold]
            if len(sub)<50: continue
            wr = sum(1 for r in sub if r>0)/len(sub)*100
            out.write(f"  Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

        # 10c. Price crosses below day-open for first time -> SELL
        out.write("\n10c. Price crosses BELOW day-open for first time -> SELL\n")
        cross_below = []
        for d in dates:
            for s in by_date[d]:
                bkt = s['bkt']
                day_open = bkt[0,O]
                if day_open<=0: continue
                if bkt[0,C] < day_open: continue  # already below at open, skip
                for b in range(1, 300):
                    if bkt[b,C]<=0: continue
                    if bkt[b,C] < day_open:
                        entry = bkt[b,C]
                        for hold in [15,30]:
                            eb = min(b+hold, MAX_B-1)
                            if bkt[eb,C]<=0: continue
                            ret = (entry-bkt[eb,C])/entry*100-COST
                            cross_below.append((hold, ret, b))
                        break
        for hold in [15,30]:
            sub = [r for h,r,_ in cross_below if h==hold]
            if len(sub)<50: continue
            wr = sum(1 for r in sub if r>0)/len(sub)*100
            out.write(f"  Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={np.mean(sub):+.3f}%\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
