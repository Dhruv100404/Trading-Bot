"""
DEEP EXIT + TRAILING STOP ANALYSIS
====================================
For every gap-reversal sell trade:
1. Track minute-by-minute price path
2. Find when reversal stalls (dynamic exit signals)
3. Simulate all trailing stop variants
4. Find optimal exit per entry-feature profile
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_exit_analysis.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    trades = []
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

                # Price path from entry to b90 (for SELL: profit when price drops)
                path = []  # list of (bucket, sell_pnl_pct)
                for b in range(7,90):
                    if bkt[b,C]>0:
                        pnl = (entry - bkt[b,C])/entry*100
                        hi_pnl = (entry - bkt[b,L])/entry*100  # best within bucket (for sell = low is best)
                        lo_pnl = (entry - bkt[b,H])/entry*100  # worst within bucket
                        path.append((b, pnl, hi_pnl, lo_pnl))

                if not path: continue

                # Entry features
                br_seq = [float(bkt[i,BR]) for i in range(6)]
                close_pos_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sell_pressure = 1 - close_pos_sum/6
                momentum = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])

                # MFE and MAE
                mfe_pct = max(p[2] for p in path)  # best hi_pnl
                mae_pct = min(p[3] for p in path)  # worst lo_pnl (negative = loss)
                mfe_bucket = max(path, key=lambda p: p[2])[0]

                # Final return at different exits
                ret_b45 = (entry-bkt[44,C])/entry*100-COST if bkt[44,C]>0 else 0
                ret_b66 = (entry-bkt[65,C])/entry*100-COST if bkt[65,C]>0 else 0
                ret_b90 = (entry-bkt[89,C])/entry*100-COST if bkt[89,C]>0 else 0

                # VWAP cross detection: does price go above VWAP after entry?
                vwap_cross_bucket = None
                for b in range(7,90):
                    if bkt[b,VW]>0 and bkt[b,C]>bkt[b,VW]:  # price above VWAP = thesis broken
                        vwap_cross_bucket = b
                        break

                # 3 consecutive green candles detection
                consec_green_bucket = None
                for b in range(7,88):
                    if bkt[b,C]>bkt[b,O] and bkt[b+1,C]>bkt[b+1,O] and bkt[b+2,C]>bkt[b+2,O]:
                        consec_green_bucket = b
                        break

                trades.append({
                    'sym':r['symbol'], 'gap':r['gapPct'], 'entry':entry,
                    'path':path, 'mfe_pct':mfe_pct, 'mae_pct':mae_pct, 'mfe_bucket':mfe_bucket,
                    'ret_b45':ret_b45, 'ret_b66':ret_b66, 'ret_b90':ret_b90,
                    'sell_pressure':sell_pressure, 'momentum':momentum, 'n_red':n_red,
                    'vwap_cross':vwap_cross_bucket, 'consec_green':consec_green_bucket,
                    'win_b66': ret_b66>0,
                })

    print(f"Loaded {len(trades)} gap-up sell trades in {time.time()-t0:.1f}s")

    with open(OUT,'w',encoding='utf-8') as out:
        out.write(f"DEEP EXIT + TRAILING STOP ANALYSIS\n")
        out.write(f"Trades: {len(trades)} (gap>0.5%, liquid stocks)\n\n")

        # 1. VWAP CROSS AS EXIT SIGNAL
        out.write("="*100+"\n1. VWAP CROSS: Exit when price goes above VWAP\n"+"="*100+"\n")
        has_cross = [t for t in trades if t['vwap_cross'] is not None]
        no_cross = [t for t in trades if t['vwap_cross'] is None]
        out.write(f"  Trades with VWAP cross (thesis broken): {len(has_cross)} ({len(has_cross)/len(trades)*100:.0f}%)\n")
        out.write(f"  Trades without VWAP cross: {len(no_cross)} ({len(no_cross)/len(trades)*100:.0f}%)\n\n")

        if has_cross:
            # When VWAP cross happens, what's the outcome at b66?
            cross_win = sum(1 for t in has_cross if t['win_b66'])/len(has_cross)*100
            no_cross_win = sum(1 for t in no_cross if t['win_b66'])/len(no_cross)*100
            out.write(f"  With VWAP cross: b66 win rate = {cross_win:.1f}%, avgRet = {np.mean([t['ret_b66'] for t in has_cross]):+.3f}%\n")
            out.write(f"  Without VWAP cross: b66 win rate = {no_cross_win:.1f}%, avgRet = {np.mean([t['ret_b66'] for t in no_cross]):+.3f}%\n\n")

            # Exit at VWAP cross vs hold to b66
            out.write(f"  Strategy comparison:\n")
            # Sim: exit at VWAP cross bucket close, or b66 if no cross
            vwap_exit_total = 0
            for t in trades:
                if t['vwap_cross'] and t['vwap_cross'] < 66:
                    # Exit at cross bucket
                    cross_ret = next((p[1] for p in t['path'] if p[0]==t['vwap_cross']), t['ret_b66']) - COST
                    vwap_exit_total += cross_ret
                else:
                    vwap_exit_total += t['ret_b66']
            out.write(f"    VWAP exit strategy: totalRet = {vwap_exit_total:+.1f}%\n")
            out.write(f"    Fixed b66 exit:     totalRet = {sum(t['ret_b66'] for t in trades):+.1f}%\n")
            out.write(f"    Fixed b90 exit:     totalRet = {sum(t['ret_b90'] for t in trades):+.1f}%\n")

        # 2. 3 CONSECUTIVE GREEN CANDLES AS EXIT
        out.write("\n"+"="*100+"\n2. EXIT ON 3 CONSECUTIVE GREEN CANDLES (buyers returning)\n"+"="*100+"\n")
        has_3g = [t for t in trades if t['consec_green'] is not None]
        no_3g = [t for t in trades if t['consec_green'] is None]
        out.write(f"  Trades with 3-green signal: {len(has_3g)} ({len(has_3g)/len(trades)*100:.0f}%)\n")
        if has_3g:
            g3_win = sum(1 for t in has_3g if t['win_b66'])/len(has_3g)*100
            no_g3_win = sum(1 for t in no_3g if t['win_b66'])/len(no_3g)*100
            out.write(f"  With 3-green: b66 win = {g3_win:.1f}%\n")
            out.write(f"  Without 3-green: b66 win = {no_g3_win:.1f}%\n")
            # Sim exit at 3-green bucket
            g3_total = 0
            for t in trades:
                if t['consec_green'] and t['consec_green'] < 66:
                    cr = next((p[1] for p in t['path'] if p[0]==t['consec_green']), t['ret_b66']) - COST
                    g3_total += cr
                else:
                    g3_total += t['ret_b66']
            out.write(f"  Exit at 3-green: totalRet = {g3_total:+.1f}%\n")
            out.write(f"  Fixed b66 exit:  totalRet = {sum(t['ret_b66'] for t in trades):+.1f}%\n")

        # 3. TRAILING STOP VARIANTS
        out.write("\n"+"="*100+"\n3. TRAILING STOP SIMULATIONS\n"+"="*100+"\n")

        def sim_trailing(trades, activate_at, trail_dist, max_exit_b=89):
            """Simulate trailing stop. activate_at = min profit to activate. trail_dist = distance behind peak."""
            total = 0; wins = 0; n = 0
            for t in trades:
                peak_pnl = 0
                trailing_active = False
                exited = False
                for b, pnl, hi_pnl, lo_pnl in t['path']:
                    if b > max_exit_b: break
                    if hi_pnl > peak_pnl: peak_pnl = hi_pnl
                    if not trailing_active and peak_pnl >= activate_at:
                        trailing_active = True
                    if trailing_active:
                        stop_level = peak_pnl - trail_dist
                        if lo_pnl <= stop_level:  # price bounced back, hit trailing stop
                            exit_pnl = stop_level - COST
                            total += exit_pnl
                            if exit_pnl > 0: wins += 1
                            n += 1
                            exited = True
                            break
                if not exited:
                    # Time exit
                    last_pnl = t['path'][-1][1] if t['path'] else 0
                    total += last_pnl - COST
                    if last_pnl - COST > 0: wins += 1
                    n += 1
            return total, wins/max(n,1)*100, n

        out.write(f"  {'Variant':<50} {'TotRet':>8} {'Win%':>6} {'Trades':>6}\n")
        out.write("  "+"-"*75+"\n")

        # Fixed time exits for comparison
        out.write(f"  {'Fixed b45 exit':<50} {sum(t['ret_b45'] for t in trades):>+7.1f}% {sum(1 for t in trades if t['ret_b45']>0)/len(trades)*100:>5.1f}% {len(trades):>6}\n")
        out.write(f"  {'Fixed b66 exit':<50} {sum(t['ret_b66'] for t in trades):>+7.1f}% {sum(1 for t in trades if t['ret_b66']>0)/len(trades)*100:>5.1f}% {len(trades):>6}\n")
        out.write(f"  {'Fixed b90 exit':<50} {sum(t['ret_b90'] for t in trades):>+7.1f}% {sum(1 for t in trades if t['ret_b90']>0)/len(trades)*100:>5.1f}% {len(trades):>6}\n\n")

        # B4a: Fixed trailing
        for act in [0.2, 0.3, 0.5, 0.7, 1.0]:
            for trail in [0.2, 0.3, 0.4, 0.5]:
                if trail >= act: continue
                total, wr, n = sim_trailing(trades, act, trail)
                out.write(f"  Trail: activate@+{act}%, trail={trail}%            {total:>+7.1f}% {wr:>5.1f}% {n:>6}\n")

        out.write("\n")
        # B4c: Stepped trailing
        def sim_stepped(trades, max_exit_b=89):
            total=0; wins=0; n=0
            for t in trades:
                peak=0; stop=-999
                exited=False
                for b,pnl,hi,lo in t['path']:
                    if b>max_exit_b: break
                    if hi>peak: peak=hi
                    # Stepped stops
                    if peak>=1.5: stop=max(stop, 1.0)
                    elif peak>=1.0: stop=max(stop, 0.5)
                    elif peak>=0.5: stop=max(stop, 0.2)
                    elif peak>=0.3: stop=max(stop, 0.0)
                    if stop>-999 and lo<=stop:
                        total+=stop-COST; n+=1
                        if stop-COST>0: wins+=1
                        exited=True; break
                if not exited:
                    lp = t['path'][-1][1] if t['path'] else 0
                    total+=lp-COST; n+=1
                    if lp-COST>0: wins+=1
            return total, wins/max(n,1)*100, n

        total,wr,n = sim_stepped(trades)
        out.write(f"  {'Stepped: +0.3->BE, +0.5->+0.2, +1->+0.5, +1.5->+1':<50} {total:>+7.1f}% {wr:>5.1f}% {n:>6}\n")

        # B4d: Time-decaying trailing
        def sim_time_decay(trades, max_exit_b=89):
            total=0; wins=0; n=0
            for t in trades:
                peak=0; exited=False
                for b,pnl,hi,lo in t['path']:
                    if b>max_exit_b: break
                    if hi>peak: peak=hi
                    # Wider trail early, tighter late
                    if b<30: trail=0.5
                    elif b<60: trail=0.3
                    else: trail=0.15
                    if peak>=0.3:  # activate after +0.3%
                        stop=peak-trail
                        if lo<=stop:
                            total+=stop-COST; n+=1
                            if stop-COST>0: wins+=1
                            exited=True; break
                if not exited:
                    lp = t['path'][-1][1] if t['path'] else 0
                    total+=lp-COST; n+=1
                    if lp-COST>0: wins+=1
            return total, wins/max(n,1)*100, n

        total,wr,n = sim_time_decay(trades)
        out.write(f"  {'Time-decay: early=0.5, mid=0.3, late=0.15':<50} {total:>+7.1f}% {wr:>5.1f}% {n:>6}\n")

        # B4e: Winner-only trailing
        def sim_winner_only(trades, check_bucket=29, act=0.3, trail=0.3, max_exit_b=89):
            total=0; wins=0; n=0
            for t in trades:
                pnl_at_check = next((p[1] for p in t['path'] if p[0]>=check_bucket), 0)
                if pnl_at_check > 0:
                    # Profitable at check → activate trailing
                    peak=0; exited=False
                    for b,pnl,hi,lo in t['path']:
                        if b<=check_bucket: continue
                        if b>max_exit_b: break
                        if hi>peak: peak=hi
                        if peak>=act:
                            stop=peak-trail
                            if lo<=stop:
                                total+=stop-COST; n+=1
                                if stop-COST>0: wins+=1
                                exited=True; break
                    if not exited:
                        lp = t['path'][-1][1] if t['path'] else 0
                        total+=lp-COST; n+=1
                        if lp-COST>0: wins+=1
                else:
                    # Losing at check → keep time exit
                    total+=t['ret_b90']; n+=1
                    if t['ret_b90']>0: wins+=1
            return total, wins/max(n,1)*100, n

        total,wr,n = sim_winner_only(trades)
        out.write(f"  {\"Winner-only: check@b30, trail if profitable\":<50} {total:>+7.1f}% {wr:>5.1f}% {n:>6}\n")

        # 4. MFE PATH: when do winners peak vs when do losers start losing?
        out.write("\n"+"="*100+"\n4. MFE PATH: average profit at each bucket (winners vs losers)\n"+"="*100+"\n")
        winners = [t for t in trades if t['ret_b66']>0]
        losers = [t for t in trades if t['ret_b66']<=0]
        out.write(f"  {'Bucket':>7} {'AllAvg':>8} {'WinAvg':>8} {'LoseAvg':>8} {'WinPeak':>8}\n")
        out.write("  "+"-"*45+"\n")
        for target_b in range(10,90,5):
            all_pnl = [next((p[1] for p in t['path'] if p[0]>=target_b), 0) for t in trades]
            win_pnl = [next((p[1] for p in t['path'] if p[0]>=target_b), 0) for t in winners]
            lose_pnl = [next((p[1] for p in t['path'] if p[0]>=target_b), 0) for t in losers]
            # What % of winners have peaked by this bucket?
            win_peaked = sum(1 for t in winners if t['mfe_bucket']<=target_b)/max(len(winners),1)*100
            h=9+(15+target_b)//60; m=(15+target_b)%60
            out.write(f"  b{target_b+1:>4} ({h}:{m:02d}) {np.mean(all_pnl):>+7.3f}% {np.mean(win_pnl):>+7.3f}% {np.mean(lose_pnl):>+7.3f}% {win_peaked:>6.0f}%\n")

        # 5. DYNAMIC EXIT FEATURES
        out.write("\n"+"="*100+"\n5. ADAPTIVE EXIT: what entry features predict optimal exit time?\n"+"="*100+"\n")
        sp_bins = [(0,0.4,'sp<0.4'),(0.4,0.5,'sp 0.4-0.5'),(0.5,0.6,'sp 0.5-0.6'),(0.6,1,'sp>0.6')]
        mom_bins = [(-99,-0.5,'mom<-0.5'),(-.5,0,'mom -0.5~0'),(0,99,'mom>0')]
        out.write(f"  {'Profile':<25}")
        for eb in [29,44,59,65,89]:
            out.write(f"  b{eb+1:>2}".rjust(10))
        out.write("  BEST\n  "+"-"*80+"\n")
        for slo,shi,slbl in sp_bins:
            for mlo,mhi,mlbl in mom_bins:
                sub = [t for t in trades if slo<=t['sell_pressure']<shi and mlo<=t['momentum']<mhi]
                if len(sub)<30: continue
                out.write(f"  {slbl+' '+mlbl:<25}")
                best_ret=-99; best_eb=65
                for eb_key, eb_field in [(29,'ret_b45'),(44,'ret_b45'),(59,'ret_b66'),(65,'ret_b66'),(89,'ret_b90')]:
                    eb_rets = {'ret_b45':44,'ret_b66':65,'ret_b90':89}
                    actual_eb = eb_key
                    rs = [next((p[1]-COST for p in t['path'] if p[0]>=actual_eb),0) for t in sub]
                    ar = np.mean(rs)
                    if ar>best_ret: best_ret=ar; best_eb=actual_eb
                    out.write(f"  {ar:>+7.3f}%".rjust(10))
                out.write(f"  b{best_eb+1}\n")

        out.write(f"\n\nDone in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
