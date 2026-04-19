"""
FINAL HUNT — Finding what everyone missed
============================================
Focus: things NOT yet tested.
1. QUALITY DAY detection (when to trade vs SKIP entirely)
2. Score THRESHOLD (don't force 8 picks — only trade HIGH confidence)
3. Gap range sweet spot (minimum gap, not just maximum)
4. Speed of first-candle drop (fast vs slow reversal start)
5. Price band diversification (don't pick 8 similar-priced stocks)
6. Entry DELAY for borderline picks (wait for confirmation)
7. Ratio: gap vs first-candle-range (gap absorbed or not?)
8. Perfect day anatomy (what 8/8 win days look like)
9. Per-stock frequency (stocks appearing in top-8 too often)
10. Combined: all best insights into one final formula
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict, Counter

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'deep_final_hunt.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading...")
    by_date = defaultdict(list)
    all_gaps_day = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                all_gaps_day[r['date']].append(r['gapPct'])
                if r['gapPct'] <= 0.5: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[89,C]<=0: continue
                ret90 = (entry-bkt[89,C])/entry*100-COST

                cp_sum = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
                sp = 1-cp_sum/6
                mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
                score = (r['gapPct'] if sp>0.5 else r['gapPct']*0.3)*(1.3 if mom<0 else 0.7)

                b0_rng = (bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                b0_body = abs(bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
                gap_vs_b0rng = r['gapPct']/b0_rng if b0_rng>0.1 else 10  # how many times gap > first candle range

                # Speed: how fast does price drop in first 3 buckets?
                drop_speed = 0
                if bkt[0,O]>0:
                    min_3 = min(bkt[i,L] for i in range(3) if bkt[i,L]>0) if any(bkt[i,L]>0 for i in range(3)) else bkt[0,O]
                    drop_speed = (bkt[0,O]-min_3)/bkt[0,O]*100

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],
                    'entry':entry,'sp':sp,'mom':mom,'n_red':n_red,'score':score,
                    'ret90':ret90,'win':ret90>0,'date':r['date'],
                    'b0_rng':b0_rng,'b0_body':b0_body,
                    'gap_vs_b0rng':gap_vs_b0rng,'drop_speed':drop_speed,
                    'f5vol_rs':r.get('f5Vol',0)*r['dayOpen'],
                })

    dates = sorted(by_date.keys())
    # Market context
    for d in dates:
        gaps = all_gaps_day[d]
        mkt_avg = np.mean(gaps); mkt_pct_up = sum(1 for g in gaps if g>0)/len(gaps)*100
        n_qualify = len(by_date[d])
        # Pool quality metrics
        pool = by_date[d]
        avg_score = np.mean([t['score'] for t in pool]) if pool else 0
        avg_sp = np.mean([t['sp'] for t in pool]) if pool else 0.5
        top8_avg_score = np.mean([t['score'] for t in sorted(pool, key=lambda x:-x['score'])[:8]]) if len(pool)>=8 else 0
        for t in pool:
            t['mkt_avg']=mkt_avg; t['mkt_pct_up']=mkt_pct_up
            t['n_qualify']=n_qualify; t['pool_avg_score']=avg_score
            t['pool_avg_sp']=avg_sp; t['top8_avg_score']=top8_avg_score

    print(f"Loaded {sum(len(v) for v in by_date.values())} records in {time.time()-t0:.1f}s")

    # Cherry-pick top-8
    daily_picks = {}; daily_rets = {}
    for d in dates:
        pool = sorted(by_date[d], key=lambda x:-x['score'])
        daily_picks[d] = pool[:8]
        daily_rets[d] = sum(t['ret90'] for t in pool[:8])

    all_picks = [t for picks in daily_picks.values() for t in picks]
    winners = [t for t in all_picks if t['win']]
    losers = [t for t in all_picks if not t['win']]

    with open(OUT,'w',encoding='utf-8') as out:
        out.write("FINAL HUNT — finding the hidden edge\n")
        out.write(f"Top-8 S5: {len(all_picks)} trades, {len(winners)}W/{len(losers)}L, {len(winners)/len(all_picks)*100:.1f}% win\n")
        out.write(f"Total return: {sum(t['ret90'] for t in all_picks):+.1f}%\n\n")

        # ═══════════════════════════════════════════════
        # 1. PERFECT DAY vs DISASTER DAY anatomy
        # ═══════════════════════════════════════════════
        out.write("="*110+"\n1. PERFECT DAY vs DISASTER DAY — what's different?\n"+"="*110+"\n")

        day_results = []
        for d in dates:
            picks = daily_picks[d]
            if len(picks)<5: continue
            day_ret = daily_rets[d]
            day_wr = sum(t['win'] for t in picks)/len(picks)*100
            day_results.append({'date':d,'ret':day_ret,'wr':day_wr,'picks':picks,
                               'n_qualify':picks[0]['n_qualify'],
                               'mkt_pct_up':picks[0]['mkt_pct_up'],
                               'pool_avg_score':picks[0]['pool_avg_score'],
                               'pool_avg_sp':picks[0]['pool_avg_sp'],
                               'top8_avg_score':picks[0]['top8_avg_score'],
                               'avg_gap':np.mean([t['gap'] for t in picks]),
                               'avg_sp':np.mean([t['sp'] for t in picks]),
                               'avg_mom':np.mean([t['mom'] for t in picks]),
                               })

        perfect = sorted(day_results, key=lambda x:-x['ret'])[:10]
        disaster = sorted(day_results, key=lambda x:x['ret'])[:10]

        out.write(f"\n  TOP 10 BEST DAYS:\n")
        out.write(f"  {'Date':>12} {'DayRet':>8} {'WR':>5} {'NQual':>6} {'MktUp%':>7} {'AvgGap':>7} {'AvgSP':>6} {'AvgMom':>7}\n")
        for d in perfect:
            out.write(f"  {d['date']:>12} {d['ret']:>+7.1f}% {d['wr']:>4.0f}% {d['n_qualify']:>6} {d['mkt_pct_up']:>6.0f}% {d['avg_gap']:>6.1f}% {d['avg_sp']:>.3f} {d['avg_mom']:>+6.2f}%\n")

        out.write(f"\n  TOP 10 WORST DAYS:\n")
        for d in disaster:
            out.write(f"  {d['date']:>12} {d['ret']:>+7.1f}% {d['wr']:>4.0f}% {d['n_qualify']:>6} {d['mkt_pct_up']:>6.0f}% {d['avg_gap']:>6.1f}% {d['avg_sp']:>.3f} {d['avg_mom']:>+6.2f}%\n")

        # Average features: best vs worst days
        out.write(f"\n  FEATURE COMPARISON:\n")
        out.write(f"  {'Feature':<20} {'Best10':>10} {'Worst10':>10} {'AllDays':>10}\n  "+"-"*55+"\n")
        for f in ['n_qualify','mkt_pct_up','avg_gap','avg_sp','avg_mom','pool_avg_score','top8_avg_score']:
            bv = np.mean([d[f] for d in perfect])
            wv = np.mean([d[f] for d in disaster])
            av = np.mean([d[f] for d in day_results])
            out.write(f"  {f:<20} {bv:>10.2f} {wv:>10.2f} {av:>10.2f}\n")

        # ═══════════════════════════════════════════════
        # 2. SKIP-DAY DETECTION: when to NOT trade at all
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n2. SKIP-DAY: when to NOT trade (day-level filters)\n"+"="*110+"\n")

        day_filters = {
            'ALL days (baseline)': lambda d: True,
            'Skip if mkt_pct_up > 55%': lambda d: d['mkt_pct_up']<=55,
            'Skip if mkt_pct_up > 50%': lambda d: d['mkt_pct_up']<=50,
            'Skip if n_qualify < 20': lambda d: d['n_qualify']>=20,
            'Skip if n_qualify < 30': lambda d: d['n_qualify']>=30,
            'Skip if avg_sp < 0.50': lambda d: d['avg_sp']>=0.50,
            'Skip if avg_sp < 0.52': lambda d: d['avg_sp']>=0.52,
            'Skip if avg_mom > -0.3%': lambda d: d['avg_mom']<=-0.3,
            'Skip if avg_mom > -0.5%': lambda d: d['avg_mom']<=-0.5,
            'Skip if top8_score < 3': lambda d: d['top8_avg_score']>=3,
            'Skip if top8_score < 5': lambda d: d['top8_avg_score']>=5,
            'Combo: sp>=0.52 + mom<=-0.3': lambda d: d['avg_sp']>=0.52 and d['avg_mom']<=-0.3,
            'Combo: n_qual>=20 + sp>=0.50': lambda d: d['n_qualify']>=20 and d['avg_sp']>=0.50,
            'Combo: n_qual>=20 + sp>=0.50 + mom<=-0.3': lambda d: d['n_qualify']>=20 and d['avg_sp']>=0.50 and d['avg_mom']<=-0.3,
        }

        out.write(f"  {'Filter':<50} {'Days':>5} {'TotRet':>8} {'DayWin':>7} {'AvgDay':>8}\n  "+"-"*85+"\n")
        for name, filt in day_filters.items():
            active = [d for d in day_results if filt(d)]
            if not active: continue
            total = sum(d['ret'] for d in active)
            dw = sum(1 for d in active if d['ret']>0)/len(active)*100
            out.write(f"  {name:<50} {len(active):>5} {total:>+7.1f}% {dw:>6.1f}% {total/len(active):>+7.3f}%\n")

        # ═══════════════════════════════════════════════
        # 3. SCORE THRESHOLD: don't always take 8
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n3. SCORE THRESHOLD: take only stocks ABOVE minimum score\n"+"="*110+"\n")

        for min_score in [0, 1, 2, 3, 5, 7, 10, 15, 20]:
            total=0; dw=0; trades=0; wt=0; active=0
            for d in dates:
                pool = sorted(by_date[d], key=lambda x:-x['score'])
                picks = [t for t in pool[:8] if t['score']>=min_score]
                if not picks: continue
                active += 1
                dr = sum(t['ret90'] for t in picks)
                total += dr; trades += len(picks)
                wt += sum(t['win'] for t in picks)
                if dr>0: dw += 1
            if trades==0: continue
            out.write(f"  MinScore>={min_score:>3}: days={active:>3} trades={trades:>4} total={total:>+7.1f}% dayW={dw/max(active,1)*100:.1f}% trdW={wt/trades*100:.1f}% perTrd={total/trades:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # 4. GAP RANGE: minimum + maximum gap
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n4. GAP RANGE OPTIMIZATION: what if we set min AND max gap?\n"+"="*110+"\n")

        for gap_min in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
            for gap_max in [5, 7, 10, 15, 100]:
                if gap_min >= gap_max: continue
                total=0; dw=0; trades=0; wt=0; active=0
                for d in dates:
                    pool = [t for t in by_date[d] if gap_min<=t['gap']<=gap_max]
                    pool.sort(key=lambda x:-x['score'])
                    picks = pool[:8]
                    if not picks: continue
                    active += 1
                    dr = sum(t['ret90'] for t in picks)
                    total+=dr; trades+=len(picks); wt+=sum(t['win'] for t in picks)
                    if dr>0: dw+=1
                if trades<50: continue
                out.write(f"  Gap {gap_min}-{gap_max}%: days={active:>3} trades={trades:>4} total={total:>+7.1f}% dayW={dw/max(active,1)*100:.1f}% trdW={wt/trades*100:.1f}%\n")

        # ═══════════════════════════════════════════════
        # 5. DROP SPEED: fast first-candle drop = better?
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n5. DROP SPEED: does fast initial drop predict deeper reversal?\n"+"="*110+"\n")

        for dlo,dhi,dlbl in [(0,0.3,'slow <0.3%'),(0.3,0.5,'0.3-0.5%'),(0.5,1,'0.5-1%'),(1,2,'1-2%'),(2,5,'fast 2-5%'),(5,99,'extreme >5%')]:
            sub = [t for t in all_picks if dlo<=t['drop_speed']<dhi]
            if len(sub)<20: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            out.write(f"  {dlbl:>15}: N={len(sub):>4} Win={wr:.1f}% Ret={ar:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # 6. GAP vs B0 RANGE ratio
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n6. GAP vs FIRST-CANDLE RANGE: was the gap 'absorbed' in b0?\n"+"="*110+"\n")
        out.write("  gap_vs_b0rng = gap / b0_range. High = gap NOT absorbed. Low = gap absorbed in first candle.\n\n")

        for rlo,rhi,rlbl in [(0,1,'<1x (absorbed)'),(1,2,'1-2x'),(2,3,'2-3x'),(3,5,'3-5x'),(5,10,'5-10x'),(10,999,'>10x (not absorbed)')]:
            sub = [t for t in all_picks if rlo<=t['gap_vs_b0rng']<rhi]
            if len(sub)<20: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            out.write(f"  {rlbl:>20}: N={len(sub):>4} Win={wr:.1f}% Ret={ar:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # 7. STOCK FREQUENCY: stocks picked too often
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n7. STOCK FREQUENCY: are some stocks over-represented in top-8?\n"+"="*110+"\n")

        sym_counts = Counter(t['sym'] for t in all_picks)
        sym_wins = defaultdict(int)
        for t in all_picks:
            if t['win']: sym_wins[t['sym']] += 1

        out.write(f"  {'Symbol':<15} {'Picks':>5} {'Wins':>5} {'WinRate':>8}\n  "+"-"*40+"\n")
        for sym, cnt in sym_counts.most_common(30):
            wr = sym_wins[sym]/cnt*100
            marker = " AVOID" if wr < 40 and cnt >= 5 else " STAR" if wr > 70 and cnt >= 5 else ""
            out.write(f"  {sym:<15} {cnt:>5} {sym_wins[sym]:>5} {wr:>7.1f}%{marker}\n")

        # ═══════════════════════════════════════════════
        # 8. PRICE DIVERSIFICATION
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n8. PRICE BAND: do trades cluster in one price range?\n"+"="*110+"\n")

        for plo,phi,plbl in [(0,50,'<50'),(50,100,'50-100'),(100,200,'100-200'),(200,500,'200-500'),(500,1000,'500-1k'),(1000,99999,'>1k')]:
            sub = [t for t in all_picks if plo<=t['price']<phi]
            if len(sub)<20: continue
            wr = sum(t['win'] for t in sub)/len(sub)*100
            ar = np.mean([t['ret90'] for t in sub])
            out.write(f"  {plbl:>10}: N={len(sub):>4} Win={wr:.1f}% Ret={ar:+.3f}%\n")

        # ═══════════════════════════════════════════════
        # 9. MEGA COMBINED STRATEGY: every insight together
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n9. MEGA COMBINED: all best insights together\n"+"="*110+"\n")

        def mega_sim(scorer, reject=None, min_score=0, gap_min=0.5, gap_max=100,
                     skip_day=None, n_pos=8, max_b=89):
            total=0; dw=0; trades=0; wt=0; active=0; skipped=0
            for d in dates:
                if skip_day:
                    dr_info = next((x for x in day_results if x['date']==d), None)
                    if dr_info and not skip_day(dr_info):
                        skipped += 1; continue

                pool = [t for t in by_date[d] if gap_min<=t['gap']<=gap_max]
                if reject: pool = [t for t in pool if not reject(t)]
                pool.sort(key=lambda x:-scorer(x))
                picks = [t for t in pool[:n_pos] if scorer(t)>=min_score]
                if not picks: continue
                active += 1
                dr = sum(t['ret90'] for t in picks)
                total+=dr; trades+=len(picks); wt+=sum(t['win'] for t in picks)
                if dr>0: dw+=1
            if trades==0: return None
            return (total, active, skipped, dw/max(active,1)*100, wt/trades*100, total/trades, trades)

        S5 = lambda t: (t['gap'] if t['sp']>0.5 else t['gap']*0.3)*(1.3 if t['mom']<0 else 0.7)

        strategies = [
            # Baseline
            ("BASELINE: S5, top8, b90", S5, None, 0, 0.5, 100, None, 8),

            # Gap range
            ("S5 + gap 2-10%", S5, None, 0, 2.0, 10, None, 8),
            ("S5 + gap 2.5-7%", S5, None, 0, 2.5, 7, None, 8),
            ("S5 + gap 2-7%", S5, None, 0, 2.0, 7, None, 8),
            ("S5 + gap 1.5-10%", S5, None, 0, 1.5, 10, None, 8),

            # Score threshold
            ("S5 + minScore>=3", S5, None, 3, 0.5, 100, None, 8),
            ("S5 + minScore>=5", S5, None, 5, 0.5, 100, None, 8),
            ("S5 + gap 2-10% + minScore>=3", S5, None, 3, 2.0, 10, None, 8),

            # Skip day
            ("S5 + skip mktUp>55%", S5, None, 0, 0.5, 100, lambda d: d['mkt_pct_up']<=55, 8),
            ("S5 + skip avgSP<0.50", S5, None, 0, 0.5, 100, lambda d: d['avg_sp']>=0.50, 8),
            ("S5 + skip avgMom>-0.3", S5, None, 0, 0.5, 100, lambda d: d['avg_mom']<=-0.3, 8),

            # Position count
            ("S5 + top6", S5, None, 0, 0.5, 100, None, 6),
            ("S5 + top7", S5, None, 0, 0.5, 100, None, 7),

            # Combined
            ("S5 + gap 2-10% + top7", S5, None, 0, 2.0, 10, None, 7),
            ("S5 + gap 2-7% + top7", S5, None, 0, 2.0, 7, None, 7),
            ("S5 + gap 2-10% + skip mktUp>55%", S5, None, 0, 2.0, 10, lambda d: d['mkt_pct_up']<=55, 8),
            ("S5 + gap 2-10% + top7 + skip avgMom>-0.3", S5, None, 0, 2.0, 10, lambda d: d['avg_mom']<=-0.3, 7),
            ("S5 + gap 2-7% + top7 + skip avgSP<0.50", S5, None, 0, 2.0, 7, lambda d: d['avg_sp']>=0.50, 7),
            ("S5 + gap 2-10% + minSc>=3 + top7", S5, None, 3, 2.0, 10, None, 7),
            ("S5 + gap 2-10% + minSc>=3 + top7 + skipMkt55", S5, None, 3, 2.0, 10, lambda d: d['mkt_pct_up']<=55, 7),
            ("S5 + gap 1.5-10% + top7 + skip avgSP<0.52", S5, None, 0, 1.5, 10, lambda d: d['avg_sp']>=0.52, 7),

            # Ultra-selective
            ("S5 + gap 2.5-7% + minSc>=5 + top6", S5, None, 5, 2.5, 7, None, 6),
            ("S5 + gap 2-7% + minSc>=3 + top6 + skipMkt55", S5, None, 3, 2.0, 7, lambda d: d['mkt_pct_up']<=55, 6),
        ]

        out.write(f"  {'Strategy':<60} {'TotRet':>8} {'Days':>5} {'Skip':>4} {'DayW':>6} {'TrdW':>6} {'PerTrd':>7} {'Trds':>5}\n")
        out.write("  "+"-"*105+"\n")
        strat_results = []
        for name, scorer, reject, ms, gmin, gmax, skip, npos in strategies:
            r = mega_sim(scorer, reject, ms, gmin, gmax, skip, npos)
            if r:
                total, act, skipped, dw, tw, pt, nt = r
                strat_results.append((total, name, act, skipped, dw, tw, pt, nt))

        strat_results.sort(key=lambda x:-x[0])
        for total, name, act, skipped, dw, tw, pt, nt in strat_results:
            marker = " <<<" if 'BASELINE' in name else ""
            out.write(f"  {name:<60} {total:>+7.1f}% {act:>5} {skipped:>4} {dw:>5.1f}% {tw:>5.1f}% {pt:>+6.3f}% {nt:>5}{marker}\n")

        # ═══════════════════════════════════════════════
        # 10. THE ABSOLUTE BEST
        # ═══════════════════════════════════════════════
        out.write("\n\n"+"="*110+"\n10. THE ABSOLUTE BEST STRATEGY FOUND\n"+"="*110+"\n")
        baseline = next(x for x in strat_results if 'BASELINE' in x[1])
        best = strat_results[0]
        out.write(f"\n  BASELINE:  {baseline[1]}\n")
        out.write(f"             total={baseline[0]:+.1f}% dayWin={baseline[4]:.1f}% trdWin={baseline[5]:.1f}% perTrade={baseline[6]:+.3f}% trades={baseline[7]}\n")
        out.write(f"\n  BEST:      {best[1]}\n")
        out.write(f"             total={best[0]:+.1f}% dayWin={best[4]:.1f}% trdWin={best[5]:.1f}% perTrade={best[6]:+.3f}% trades={best[7]}\n")
        out.write(f"\n  IMPROVEMENT: {best[0]-baseline[0]:+.1f}% total, {best[6]-baseline[6]:+.3f}% per trade\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
