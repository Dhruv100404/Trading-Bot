"""
DEEP 6-POINT ANALYSIS — All points combined
==============================================
#1: Second-leg BUY with REALISTIC no-lookahead detection
#2: VWAP exit + exact green candle count (minute-by-minute)
#3: S5 scorer with all exit rules combined
#4: "Never drops" 26% — predict at entry, feature-by-feature
#5: b15 P&L predictor — combine with VWAP + volume
#6: Volume as live signal — threshold detection

ALL in one load, cherry-picked top-8 with S5 scorer.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_6points.txt'
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
                bkts = r['buckets']
                nb = min(len(bkts), 200)
                bkt = np.zeros((200,7), dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0: continue

                # Entry features (NO LOOKAHEAD)
                cp_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp_sum/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                score = (r['gapPct'] if sp>0.5 else r['gapPct']*0.3)*(1.3 if mom<0 else 0.7)

                # Pre-compute minute-by-minute data for speed
                pnl_at = {}  # bucket -> sell P&L %
                vwap_at = {}  # bucket -> price vs VWAP %
                green_at = {}  # bucket -> is_green
                vol_at = {}   # bucket -> volume
                cum_green = {}  # bucket -> cumulative green count from b7
                running_green = 0
                running_min_price = entry
                mfe_at = {}  # bucket -> MFE so far (best sell P&L)
                consec_green_at = {}  # bucket -> current consecutive green streak

                streak = 0
                for b in range(7, min(nb, 150)):
                    if bkt[b,C]<=0: continue
                    pnl_at[b] = (entry-bkt[b,C])/entry*100
                    vwap_at[b] = (bkt[b,C]-bkt[b,VW])/bkt[b,VW]*100 if bkt[b,VW]>0 else 0
                    is_g = bkt[b,C]>bkt[b,O]
                    green_at[b] = is_g
                    vol_at[b] = float(bkt[b,V])
                    if is_g: running_green+=1; streak+=1
                    else: streak=0
                    cum_green[b] = running_green
                    consec_green_at[b] = streak
                    if bkt[b,L]>0 and bkt[b,L]<running_min_price:
                        running_min_price = bkt[b,L]
                    mfe_at[b] = (entry-running_min_price)/entry*100

                ret90 = pnl_at.get(89,0) - COST

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'sp':sp,'mom':mom,'n_red':n_red,'score':score,
                    'pnl_at':pnl_at,'vwap_at':vwap_at,'green_at':green_at,
                    'vol_at':vol_at,'cum_green':cum_green,'consec_green_at':consec_green_at,
                    'mfe_at':mfe_at,'ret90':ret90,'win':ret90>0,'bkt':bkt,'date':r['date'],
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                })

    dates = sorted(by_date.keys())

    # Cherry-pick top-8 per day
    all_picks = []
    for d in dates:
        pool = sorted(by_date[d], key=lambda x:-x['score'])
        all_picks.extend(pool[:8])

    winners = [t for t in all_picks if t['win']]
    losers = [t for t in all_picks if not t['win']]
    print(f"Loaded {len(all_picks)} top-8 picks ({len(winners)}W/{len(losers)}L) in {time.time()-t0:.1f}s")

    # Classify path shapes
    for t in all_picks:
        mfe_b90 = t['mfe_at'].get(89, 0)
        pnl_b20 = t['pnl_at'].get(19, 0)
        if mfe_b90 < 0.2:
            t['shape'] = 'never_drops'
        elif mfe_b90 >= 0.5 and t['ret90'] < 0:
            t['shape'] = 'drop_then_bounce'
        elif t['ret90'] > 0.5:
            t['shape'] = 'steady_drop'
        else:
            t['shape'] = 'other'

    never_drops = [t for t in all_picks if t['shape']=='never_drops']

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("DEEP 6-POINT ANALYSIS\n")
        out.write(f"Top-8 picks: {len(all_picks)} ({len(winners)}W/{len(losers)}L) across {len(dates)} days\n\n")

        # ═══════════════════════════════════════════════
        # POINT #4: "Never drops" — predict at ENTRY
        # ═══════════════════════════════════════════════
        out.write("="*110+"\n#4: 'NEVER DROPS' PREDICTION — what entry features predict this?\n"+"="*110+"\n")
        out.write(f"  Never-drops trades: {len(never_drops)} ({len(never_drops)/len(all_picks)*100:.1f}%)\n")
        out.write(f"  Avg loss: {np.mean([t['ret90'] for t in never_drops]):+.3f}%\n\n")

        # Compare entry features: never_drops vs rest
        rest = [t for t in all_picks if t['shape']!='never_drops']
        out.write(f"  {'Feature':<16} {'NeverDrops':>12} {'Rest':>10} {'Delta':>10}\n  "+"-"*50+"\n")
        for f in ['gap','sp','mom','n_red','price','f5vol_rs','score']:
            nd = np.mean([t[f] for t in never_drops])
            rs = np.mean([t[f] for t in rest])
            out.write(f"  {f:<16} {nd:>12.3f} {rs:>10.3f} {nd-rs:>+10.3f}\n")

        # Threshold search: what single feature best predicts never_drops?
        out.write(f"\n  THRESHOLD SEARCH: what predicts never_drops at entry?\n")
        for feat in ['gap','sp','mom','n_red','price']:
            vals_nd = [t[feat] for t in never_drops]
            vals_rest = [t[feat] for t in rest]
            all_vals = [t[feat] for t in all_picks]
            for pct in [10,20,30,40,50,60,70,80,90]:
                thresh = np.percentile(all_vals, pct)
                above = [t for t in all_picks if t[feat]>thresh]
                below = [t for t in all_picks if t[feat]<=thresh]
                nd_above = sum(1 for t in above if t['shape']=='never_drops')
                nd_below = sum(1 for t in below if t['shape']=='never_drops')
                if len(above)>10 and len(below)>10:
                    rate_above = nd_above/len(above)*100
                    rate_below = nd_below/len(below)*100
                    if abs(rate_above-rate_below) > 10:
                        out.write(f"    {feat}>{thresh:.2f} (p{pct}): never_drops rate {rate_above:.1f}% vs {rate_below:.1f}% below\n")

        # Combo detection
        out.write(f"\n  COMBO FILTERS to detect never_drops at entry:\n")
        combos = [
            ('gap>10 & sp<0.55', lambda t: t['gap']>10 and t['sp']<0.55),
            ('gap>10', lambda t: t['gap']>10),
            ('gap>5 & sp<0.50', lambda t: t['gap']>5 and t['sp']<0.50),
            ('gap>5 & mom>-0.3', lambda t: t['gap']>5 and t['mom']>-0.3),
            ('gap>3 & sp<0.45 & mom>0', lambda t: t['gap']>3 and t['sp']<0.45 and t['mom']>0),
            ('sp<0.45 & n_red<=1', lambda t: t['sp']<0.45 and t['n_red']<=1),
            ('sp<0.45 & mom>0', lambda t: t['sp']<0.45 and t['mom']>0),
            ('gap>10 | (sp<0.45 & mom>0)', lambda t: t['gap']>10 or (t['sp']<0.45 and t['mom']>0)),
            ('gap>10 | (sp<0.45 & nred<=1)', lambda t: t['gap']>10 or (t['sp']<0.45 and t['n_red']<=1)),
        ]
        out.write(f"  {'Combo':<40} {'Caught':>6} {'ND%':>6} {'FalsePos':>9} {'Precision':>10}\n  "+"-"*75+"\n")
        for name, filt in combos:
            caught = [t for t in all_picks if filt(t)]
            if not caught: continue
            nd_caught = sum(1 for t in caught if t['shape']=='never_drops')
            nd_missed = len(never_drops) - nd_caught
            false_pos = len(caught) - nd_caught
            precision = nd_caught/len(caught)*100
            recall = nd_caught/max(len(never_drops),1)*100
            rest_wr = sum(1 for t in caught if t['win'])/len(caught)*100
            out.write(f"  {name:<40} {len(caught):>6} {nd_caught:>5} {false_pos:>9} {precision:>9.1f}%  recall={recall:.0f}% winRate={rest_wr:.1f}%\n")

        # ═══════════════════════════════════════════════
        # POINT #5: b15 P&L + VWAP + Volume COMBINED
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#5: COMBINED MID-TRADE SIGNALS (P&L + VWAP + Volume + Green)\n"+"="*110+"\n")

        # Minute-by-minute: at each bucket, combine ALL signals
        for check_b in [9, 11, 14, 19, 24, 29]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  === At b{check_b+1} ({h}:{m:02d}) ===\n")

            # Multi-signal combos
            combos_mid = []
            for pnl_thresh in [(-99,0,'losing'),(0,99,'winning')]:
                for vwap_dir in [('below', lambda t,b: t['vwap_at'].get(b,0)<0),
                                  ('above', lambda t,b: t['vwap_at'].get(b,0)>=0)]:
                    for vol_level in [('loVol', lambda t,b: t['vol_at'].get(b,0)<np.median([t['vol_at'].get(b,0) for t in all_picks if b in t['vol_at']])),
                                       ('hiVol', lambda t,b: t['vol_at'].get(b,0)>=np.median([t['vol_at'].get(b,0) for t in all_picks if b in t['vol_at']]))]:
                        plo,phi,plbl = pnl_thresh
                        vlbl,vfilt = vwap_dir
                        vollbl,volfilt = vol_level

                        sub = [t for t in all_picks if
                               check_b in t['pnl_at'] and
                               plo<=t['pnl_at'][check_b]<phi and
                               vfilt(t, check_b) and
                               volfilt(t, check_b)]
                        if len(sub)<15: continue
                        wr = sum(t['win'] for t in sub)/len(sub)*100
                        ar = np.mean([t['ret90'] for t in sub])
                        label = f"{plbl}+{vlbl}VWAP+{vollbl}"
                        combos_mid.append((ar, label, len(sub), wr))

            combos_mid.sort(key=lambda x:-x[0])
            out.write(f"    {'Combo':<35} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*60+"\n")
            for ar,label,n,wr in combos_mid:
                marker = " EXIT" if wr < 40 else " HOLD" if wr > 60 else ""
                out.write(f"    {label:<35} {n:>5} {wr:>5.1f}% {ar:>+7.3f}%{marker}\n")

        # ═══════════════════════════════════════════════
        # POINT #2: Exact consecutive green candle threshold
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#2: CONSECUTIVE GREEN CANDLE COUNT — minute by minute\n"+"="*110+"\n")

        for check_b in [14, 19, 24, 29, 44]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  At b{check_b+1} ({h}:{m:02d}) — max consecutive green streak so far:\n")
            out.write(f"    {'MaxStreak':>10} {'N':>5} {'Win%':>6} {'AvgRet':>8}\n    "+"-"*35+"\n")
            for slo,shi,slbl in [(0,1,'0'),(1,2,'1'),(2,3,'2'),(3,4,'3'),(4,5,'4'),(5,7,'5-6'),(7,99,'7+')]:
                sub = []
                for t in all_picks:
                    # Max consecutive green streak up to check_b
                    max_streak = max((t['consec_green_at'].get(b,0) for b in range(7,check_b+1) if b in t['consec_green_at']), default=0)
                    if slo <= max_streak < shi:
                        sub.append(t)
                if len(sub)<15: continue
                wr = sum(t['win'] for t in sub)/len(sub)*100
                ar = np.mean([t['ret90'] for t in sub])
                out.write(f"    {slbl:>10} {len(sub):>5} {wr:>5.1f}% {ar:>+7.3f}%\n")

        # Also: cumulative green count (not streak)
        out.write(f"\n  CUMULATIVE green count (total, not consecutive):\n")
        for check_b in [14, 19, 29]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            out.write(f"\n  At b{check_b+1} ({h}:{m:02d}):\n")
            for glo,ghi,glbl in [(0,2,'0-1'),(2,3,'2'),(3,4,'3'),(4,5,'4'),(5,6,'5'),(6,8,'6-7'),(8,99,'8+')]:
                sub = [t for t in all_picks if glo<=t['cum_green'].get(check_b,0)<ghi]
                if len(sub)<15: continue
                wr = sum(t['win'] for t in sub)/len(sub)*100
                ar = np.mean([t['ret90'] for t in sub])
                out.write(f"    {glbl:>5} green: N={len(sub):>4}, Win={wr:.1f}%, Ret={ar:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # POINT #6: Volume thresholds — live signal
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#6: VOLUME AS LIVE SIGNAL\n"+"="*110+"\n")

        # At each check bucket, compare volume of winners vs losers
        out.write(f"  Average volume at each bucket (winners vs losers):\n")
        out.write(f"  {'Bucket':>7} {'WinVol':>10} {'LoseVol':>10} {'Ratio':>7}\n  "+"-"*40+"\n")
        for b in range(8, 90, 3):
            wv = [t['vol_at'].get(b,0) for t in winners if b in t['vol_at']]
            lv = [t['vol_at'].get(b,0) for t in losers if b in t['vol_at']]
            if not wv or not lv: continue
            wa = np.mean(wv); la = np.mean(lv)
            out.write(f"  b{b+1:>5} {wa:>10.0f} {la:>10.0f} {wa/max(la,1):>6.2f}x\n")

        # Can we use volume threshold as EXIT signal?
        out.write(f"\n  Volume-based EXIT signal:\n")
        out.write(f"  At b15: if volume since entry is high -> likely loser\n")
        for check_b in [14, 19, 29]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            # Sum volume from b7 to check_b
            vol_sums = []
            for t in all_picks:
                vs = sum(t['vol_at'].get(b,0) for b in range(7, check_b+1))
                vol_sums.append((vs, t['win'], t['ret90']))
            if not vol_sums: continue
            med_vol = np.median([v for v,_,_ in vol_sums])
            hi_vol = [r for v,w,r in vol_sums if v > med_vol]
            lo_vol = [r for v,w,r in vol_sums if v <= med_vol]
            hi_wr = sum(1 for r in hi_vol if r>0)/max(len(hi_vol),1)*100
            lo_wr = sum(1 for r in lo_vol if r>0)/max(len(lo_vol),1)*100
            out.write(f"  b{check_b+1} ({h}:{m:02d}): HIGH vol win={hi_wr:.1f}% (n={len(hi_vol)})  LOW vol win={lo_wr:.1f}% (n={len(lo_vol)})\n")

        # ═══════════════════════════════════════════════
        # POINT #1: Second-leg BUY — REALISTIC detection
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#1: SECOND-LEG BUY — using MFE detection (no perfect lookahead)\n"+"="*110+"\n")
        out.write("  Approach: after sell trade is profitable at check bucket,\n")
        out.write("  if price bounces X% from the low, BUY at next open.\n\n")

        # For winning sell trades only: at which bucket does MFE occur?
        out.write(f"  For WINNING trades, when does MFE (lowest price) occur?\n")
        mfe_timing = defaultdict(int)
        for t in winners:
            # Find bucket with max MFE
            best_b = max(t['mfe_at'].keys(), key=lambda b: t['mfe_at'][b])
            mfe_timing[best_b//10*10] += 1  # group by 10s
        out.write(f"  {'BucketRange':>15} {'Count':>6}\n  "+"-"*25+"\n")
        for bgroup in sorted(mfe_timing.keys()):
            out.write(f"  b{bgroup+1}-b{bgroup+10:>12} {mfe_timing[bgroup]:>6}\n")

        # Realistic second-leg: at bucket C, check if profitable AND bouncing
        out.write(f"\n  REALISTIC SECOND-LEG BUY:\n")
        out.write(f"  At check bucket, if (1) sell profitable >0.5% AND (2) price bounced 0.3% from low\n")
        out.write(f"  -> BUY at next bucket open, hold for N minutes\n\n")

        for check_b in [29, 44, 59, 65, 74, 89]:
            h=9+(15+check_b)//60; m=(15+check_b)%60
            buys = []
            for t in all_picks:
                sell_pnl = t['pnl_at'].get(check_b, 0)
                mfe_so_far = t['mfe_at'].get(check_b, 0)
                current_price = t['bkt'][check_b, C]
                if sell_pnl < 0.5 or mfe_so_far < 0.5 or current_price <= 0: continue

                # Bounce detection: current price vs running min
                running_min = t['entry']
                for b in range(7, check_b+1):
                    if t['bkt'][b,L]>0 and t['bkt'][b,L]<running_min:
                        running_min = t['bkt'][b,L]
                bounce = (current_price-running_min)/running_min*100 if running_min>0 else 0
                if bounce < 0.2: continue  # not bouncing yet

                # BUY entry at next bucket open
                buy_entry_b = check_b + 1
                if buy_entry_b >= 195 or t['bkt'][buy_entry_b,O]<=0: continue
                buy_entry = t['bkt'][buy_entry_b, O]

                for hold in [10, 15, 30]:
                    exit_b = min(buy_entry_b + hold, 195)
                    if t['bkt'][exit_b,C]<=0: continue
                    buy_ret = (t['bkt'][exit_b,C]-buy_entry)/buy_entry*100 - COST
                    buys.append((hold, buy_ret, bounce, sell_pnl))

            if not buys: continue
            out.write(f"  Check at b{check_b+1} ({h}:{m:02d}): {len([b for b in buys if b[0]==15])} qualifying trades\n")
            for hold in [10, 15, 30]:
                sub = [r for h,r,_,_ in buys if h==hold]
                if len(sub)<10: continue
                wr = sum(1 for r in sub if r>0)/len(sub)*100
                ar = np.mean(sub)
                out.write(f"    Hold {hold}m: N={len(sub)}, Win={wr:.1f}%, AvgRet={ar:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # POINT #3: BEST COMBINED EXIT RULE
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n#3: BEST EXIT RULES COMBINED (on S5 top-8)\n"+"="*110+"\n")

        def sim_exit(trades, rule_name, rule_fn, max_b=89):
            total=0;wins=0;n=0
            for t in trades:
                exited=False
                for b in sorted(t['pnl_at'].keys()):
                    if b > max_b: break
                    if rule_fn(t, b):
                        total += t['pnl_at'][b]-COST; n+=1
                        if t['pnl_at'][b]-COST>0: wins+=1
                        exited=True; break
                if not exited:
                    total += t['ret90']; n+=1
                    if t['ret90']>0: wins+=1
            return total, wins/max(n,1)*100, n

        rules = {
            'Fixed b90 (baseline)': lambda t,b: b>=89,

            # P&L based
            'Exit@b15 if loss>0.5%': lambda t,b: b==14 and t['pnl_at'].get(14,0)<-0.5,
            'Exit@b20 if loss>0.5%': lambda t,b: b==19 and t['pnl_at'].get(19,0)<-0.5,
            'Exit@b20 if loss>0.3%': lambda t,b: b==19 and t['pnl_at'].get(19,0)<-0.3,

            # VWAP based
            'Exit@b15 if above VWAP': lambda t,b: b==14 and t['vwap_at'].get(14,0)>0,
            'Exit@b20 if above VWAP>0.5%': lambda t,b: b==19 and t['vwap_at'].get(19,0)>0.5,
            'Exit@b30 if above VWAP>0.5%': lambda t,b: b==29 and t['vwap_at'].get(29,0)>0.5,

            # Volume based
            'Exit@b15 if hi vol + losing': lambda t,b: b==14 and sum(t['vol_at'].get(bb,0) for bb in range(7,15))>np.median([sum(tt['vol_at'].get(bb,0) for bb in range(7,15)) for tt in all_picks]) and t['pnl_at'].get(14,0)<0,

            # Green candle based
            'Exit if 4+ consec green by b20': lambda t,b: b==19 and max((t['consec_green_at'].get(bb,0) for bb in range(7,20)), default=0)>=4,
            'Exit if 5+ consec green by b30': lambda t,b: b==29 and max((t['consec_green_at'].get(bb,0) for bb in range(7,30)), default=0)>=5,

            # COMBINED rules
            'b15: loss>0.5% + aboveVWAP': lambda t,b: b==14 and t['pnl_at'].get(14,0)<-0.5 and t['vwap_at'].get(14,0)>0,
            'b20: loss>0.3% + aboveVWAP>0.3%': lambda t,b: b==19 and t['pnl_at'].get(19,0)<-0.3 and t['vwap_at'].get(19,0)>0.3,
            'b20: loss>0.5% + aboveVWAP': lambda t,b: b==19 and t['pnl_at'].get(19,0)<-0.5 and t['vwap_at'].get(19,0)>0,
            'b30: loss>0.3% + aboveVWAP>0.5%': lambda t,b: b==29 and t['pnl_at'].get(29,0)<-0.3 and t['vwap_at'].get(29,0)>0.5,

            # PROGRESSIVE: check at multiple points
            'Progressive: b15 loss>1% OR b20 loss>0.5%+aboveVWAP OR b30 aboveVWAP>0.5%':
                lambda t,b: (b==14 and t['pnl_at'].get(14,0)<-1.0) or
                            (b==19 and t['pnl_at'].get(19,0)<-0.5 and t['vwap_at'].get(19,0)>0) or
                            (b==29 and t['vwap_at'].get(29,0)>0.5),

            'Progressive v2: b12 loss>1% OR b20 loss>0.5% OR b30 aboveVWAP>0.5%+loss':
                lambda t,b: (b==11 and t['pnl_at'].get(11,0)<-1.0) or
                            (b==19 and t['pnl_at'].get(19,0)<-0.5) or
                            (b==29 and t['vwap_at'].get(29,0)>0.5 and t['pnl_at'].get(29,0)<0),

            'Surgical: b15 loss>1% OR b20 (loss>0.5%+aboveVWAP+4consecGreen)':
                lambda t,b: (b==14 and t['pnl_at'].get(14,0)<-1.0) or
                            (b==19 and t['pnl_at'].get(19,0)<-0.5 and t['vwap_at'].get(19,0)>0 and max((t['consec_green_at'].get(bb,0) for bb in range(7,20)),default=0)>=4),
        }

        out.write(f"  {'Rule':<70} {'TotRet':>8} {'Win%':>6}\n  "+"-"*90+"\n")
        rule_results = []
        for name, rule in rules.items():
            total, wr, n = sim_exit(all_picks, name, rule)
            rule_results.append((total, name, wr))
        rule_results.sort(key=lambda x:-x[0])
        for total, name, wr in rule_results:
            marker = " <<<" if 'baseline' in name else ""
            out.write(f"  {name:<70} {total:>+7.1f}% {wr:>5.1f}%{marker}\n")

        # ═══════════════════════════════════════════════
        # FINAL VERDICT
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\nFINAL VERDICT\n"+"="*110+"\n")
        baseline = next(x for x in rule_results if 'baseline' in x[1])
        best = rule_results[0]
        out.write(f"  Baseline (Fixed b90):  {baseline[0]:>+8.1f}%  Win={baseline[2]:.1f}%\n")
        out.write(f"  Best exit rule:        {best[0]:>+8.1f}%  Win={best[2]:.1f}%\n")
        out.write(f"  Rule: {best[1]}\n")
        if best[0] > baseline[0]:
            out.write(f"  Improvement:           {best[0]-baseline[0]:>+8.1f}%\n")
        else:
            out.write(f"  Fixed b90 remains BEST. Dynamic exits hurt cherry-picked stocks.\n")
            out.write(f"  REASON: cherry-pick already filters quality. Early exits cut winners.\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
