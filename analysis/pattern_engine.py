"""
PATTERN ENGINE — 60+ patterns, BUY + SELL signals, full market analysis
=========================================================================
No bearish bias. Discovers patterns across all conditions.
Uses full 375-bucket data for each stock.
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'pattern_engine.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6
COST = 0.15

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
                if abs(r['gapPct']) > 10: continue
                if r.get('f5Vol',0)*r['dayOpen'] < 500000: continue
                bkts = r['buckets']
                nb = min(len(bkts), 100)
                bkt = np.zeros((100,7), dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                if bkt[0,O]<=0 or bkt[0,H]==bkt[0,L]: continue

                by_date[r['date']].append({
                    'sym':r['symbol'],'gap':r['gapPct'],'price':r['dayOpen'],'bkt':bkt,'nb':nb,
                })

    dates = sorted(by_date.keys())
    n = sum(len(v) for v in by_date.values())
    print(f"Loaded {n} stock-days across {len(dates)} days in {time.time()-t0:.1f}s")

    # ── FEATURE EXTRACTION ──
    def extract(bkt, nb, gap, price):
        """Extract ALL features for a stock-day. Returns dict or None."""
        if nb < 90 or bkt[89,C]<=0 or bkt[6,O]<=0: return None
        entry = bkt[6,O]; day_open = bkt[0,O]

        # Pre-entry features (buckets 0-5, NO LOOKAHEAD)
        cp = sum((bkt[i,C]-bkt[i,L])/(bkt[i,H]-bkt[i,L]) if bkt[i,H]>bkt[i,L] else 0.5 for i in range(6))
        sp = 1-cp/6  # sell pressure
        bp = cp/6    # buy pressure (inverse)
        mom = (bkt[5,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
        n_red = sum(1 for i in range(6) if bkt[i,C]<bkt[i,O])
        n_green = 6 - n_red

        # First candle features
        b0_rng = (bkt[0,H]-bkt[0,L])/bkt[0,O]*100 if bkt[0,O]>0 else 0
        b0_body = abs(bkt[0,C]-bkt[0,O])/bkt[0,O]*100 if bkt[0,O]>0 else 0
        b0_green = bkt[0,C] > bkt[0,O]
        exhaust_sell = (bkt[0,H]-bkt[0,C])/(bkt[0,H]-bkt[0,L]) if bkt[0,H]>bkt[0,L] else 0.5
        exhaust_buy = (bkt[0,C]-bkt[0,L])/(bkt[0,H]-bkt[0,L]) if bkt[0,H]>bkt[0,L] else 0.5

        # Volume features
        vol6 = [float(bkt[i,V]) for i in range(6)]
        total_v = sum(vol6)
        vol_b0_share = vol6[0]/max(total_v,1)
        avg_vol = np.mean(vol6) if vol6 else 0
        vol_trend = (sum(vol6[3:])-sum(vol6[:3]))/max(total_v,1)*100

        # VWAP
        vwap_dev = (bkt[5,C]-bkt[5,VW])/bkt[5,VW]*100 if bkt[5,VW]>0 else 0

        # Buy ratio
        br_seq = [float(bkt[i,BR]) for i in range(6)]
        avg_br = np.mean(br_seq)
        br_trend = br_seq[5]-br_seq[0] if len(br_seq)==6 else 0

        # OR (Opening Range) first 5 buckets
        or5_h = float(np.max(bkt[:5,H]))
        or5_l = float(np.min(bkt[:5,L]))
        or5_rng = (or5_h-or5_l)/or5_h*100 if or5_h>0 else 0

        # At entry: price relative to OR
        price_vs_or_h = (entry-or5_h)/or5_h*100 if or5_h>0 else 0
        price_vs_or_l = (entry-or5_l)/or5_l*100 if or5_l>0 else 0

        # Outcomes at multiple exits (for BOTH directions)
        sell_ret = {}; buy_ret = {}
        for eb in [19,29,44,59,65,74,89]:
            if bkt[eb,C]>0:
                sell_ret[eb] = (entry-bkt[eb,C])/entry*100-COST
                buy_ret[eb] = (bkt[eb,C]-entry)/entry*100-COST

        # S6 and V2 scores
        s6_score = gap*(1.0 if sp>0.5 else 0.3)*(1.2 if price<500 else 0.9)
        v2_score = gap*sp*(1.4 if mom<-0.5 else 1.1 if mom<0 else 0.7)
        # BUY scores (mirror)
        s6_buy = abs(gap)*(1.0 if bp>0.5 else 0.3)*(1.2 if price<500 else 0.9) if gap<0 else 0
        v2_buy = abs(gap)*bp*(1.4 if mom>0.5 else 1.1 if mom>0 else 0.7) if gap<0 else 0

        return {
            'gap':gap,'price':price,'entry':entry,'sp':sp,'bp':bp,
            'mom':mom,'n_red':n_red,'n_green':n_green,
            'b0_rng':b0_rng,'b0_body':b0_body,'b0_green':b0_green,
            'exhaust_sell':exhaust_sell,'exhaust_buy':exhaust_buy,
            'vol_b0_share':vol_b0_share,'vol_trend':vol_trend,
            'vwap_dev':vwap_dev,'avg_br':avg_br,'br_trend':br_trend,
            'or5_rng':or5_rng,'price_vs_or_h':price_vs_or_h,'price_vs_or_l':price_vs_or_l,
            's6':s6_score,'v2':v2_score,'s6_buy':s6_buy,'v2_buy':v2_buy,
            'sell_ret':sell_ret,'buy_ret':buy_ret,
        }

    # Extract all features
    print("Extracting features...")
    all_records = []
    for d in dates:
        for s in by_date[d]:
            feat = extract(s['bkt'], s['nb'], s['gap'], s['price'])
            if feat is None: continue
            feat['sym'] = s['sym']; feat['date'] = d
            all_records.append(feat)

    print(f"Extracted {len(all_records)} records")

    # ── PATTERN DEFINITIONS ──
    # Each pattern: (name, direction, filter_fn, description)
    # direction: 'sell' or 'buy'
    # filter_fn takes a record, returns True if pattern matches

    patterns = [
        # ═══ SELL PATTERNS (gap-up reversal) ═══
        ("S01: Gap-up reversal (basic)", 'sell', lambda r: r['gap']>1, "Gap up >1%, sell at open"),
        ("S02: Gap-up + sell pressure", 'sell', lambda r: r['gap']>1 and r['sp']>0.5, "Gap up + sellers dominating first 6 min"),
        ("S03: Gap-up + negative momentum", 'sell', lambda r: r['gap']>1 and r['mom']<0, "Gap up but price already dropping"),
        ("S04: Gap-up + 3+ red candles", 'sell', lambda r: r['gap']>1 and r['n_red']>=3, "Gap up with 3+ red candles in first 6"),
        ("S05: Gap-up + exhaust sell >0.7", 'sell', lambda r: r['gap']>1 and r['exhaust_sell']>0.7, "First candle closed near low (exhaustion)"),
        ("S06: Gap-up + below VWAP", 'sell', lambda r: r['gap']>1 and r['vwap_dev']<-0.3, "Price below VWAP at entry"),
        ("S07: Gap-up + low buy ratio", 'sell', lambda r: r['gap']>1 and r['avg_br']<0.4, "Buyers absent in first 6 min"),
        ("S08: Gap-up + declining BR", 'sell', lambda r: r['gap']>1 and r['br_trend']<-0.1, "Buy ratio declining (sellers growing)"),
        ("S09: Big gap-up (>3%)", 'sell', lambda r: r['gap']>3, "Large gap = high reversal probability"),
        ("S10: Gap-up + cheap stock", 'sell', lambda r: r['gap']>1 and r['price']<500, "Cheaper stocks reverse more"),
        ("S11: Gap-up + wide OR + breakdown", 'sell', lambda r: r['gap']>1 and r['or5_rng']>1 and r['price_vs_or_l']<0.3, "Price near OR low after wide range"),
        ("S12: Gap-up + vol spike b0", 'sell', lambda r: r['gap']>1 and r['vol_b0_share']>0.4, "Volume concentrated in first candle (exhaustion)"),
        ("S13: Gap-up + S6 high score", 'sell', lambda r: r['s6']>3, "S6 score above 3 (strong sell signal)"),
        ("S14: Gap-up + V2 high score", 'sell', lambda r: r['v2']>3, "V2 score above 3"),
        ("S15: Gap 2-5% sweet spot", 'sell', lambda r: 2<=r['gap']<=5, "Optimal gap range for reversal"),
        ("S16: Gap-up + sp>0.6 + mom<-0.5", 'sell', lambda r: r['gap']>1 and r['sp']>0.6 and r['mom']<-0.5, "Strong sell pressure + momentum"),
        ("S17: Gap-up + all 6 red", 'sell', lambda r: r['gap']>1 and r['n_red']==6, "All 6 candles red (extreme selling)"),
        ("S18: Gap-up + b0 big red body>1%", 'sell', lambda r: r['gap']>1 and not r['b0_green'] and r['b0_body']>1, "First candle = big red"),
        ("S19: Gap-up + vol declining", 'sell', lambda r: r['gap']>1 and r['vol_trend']<-20, "Volume drying up (buyers exhausted)"),
        ("S20: Gap-up + OR breakdown", 'sell', lambda r: r['gap']>1 and r['price_vs_or_l']<0, "Price broke below opening range"),

        # ═══ BUY PATTERNS (gap-down reversal) ═══
        ("B01: Gap-down reversal (basic)", 'buy', lambda r: r['gap']<-1, "Gap down >1%, buy the bounce"),
        ("B02: Gap-down + buy pressure", 'buy', lambda r: r['gap']<-1 and r['bp']>0.5, "Gap down + buyers dominating"),
        ("B03: Gap-down + positive momentum", 'buy', lambda r: r['gap']<-1 and r['mom']>0, "Gap down but price bouncing up"),
        ("B04: Gap-down + 3+ green candles", 'buy', lambda r: r['gap']<-1 and r['n_green']>=3, "Gap down with buying in first 6 min"),
        ("B05: Gap-down + exhaust buy >0.7", 'buy', lambda r: r['gap']<-1 and r['exhaust_buy']>0.7, "First candle closed near high (recovery)"),
        ("B06: Gap-down + above VWAP", 'buy', lambda r: r['gap']<-1 and r['vwap_dev']>0.3, "Price recovered above VWAP"),
        ("B07: Gap-down + high buy ratio", 'buy', lambda r: r['gap']<-1 and r['avg_br']>0.6, "Strong buyer activity"),
        ("B08: Gap-down + rising BR", 'buy', lambda r: r['gap']<-1 and r['br_trend']>0.1, "Buy ratio increasing (buyers arriving)"),
        ("B09: Big gap-down (<-3%)", 'buy', lambda r: r['gap']<-3, "Large gap down = oversold bounce"),
        ("B10: Gap-down + cheap stock", 'buy', lambda r: r['gap']<-1 and r['price']<500, "Cheap oversold stocks bounce hard"),
        ("B11: Gap-down + OR breakout up", 'buy', lambda r: r['gap']<-1 and r['price_vs_or_h']>0, "Price broke above opening range"),
        ("B12: Gap-down + vol spike b0", 'buy', lambda r: r['gap']<-1 and r['vol_b0_share']>0.4, "High volume first candle = capitulation"),
        ("B13: Gap-down + S6 buy high", 'buy', lambda r: r['s6_buy']>2, "S6 BUY score above 2"),
        ("B14: Gap-down + V2 buy high", 'buy', lambda r: r['v2_buy']>2, "V2 BUY score above 2"),
        ("B15: Gap -2 to -5% sweet spot", 'buy', lambda r: -5<=r['gap']<=-2, "Optimal gap-down range for bounce"),
        ("B16: Gap-down + bp>0.6 + mom>0.5", 'buy', lambda r: r['gap']<-1 and r['bp']>0.6 and r['mom']>0.5, "Strong buying pressure + upward momentum"),
        ("B17: Gap-down + all 6 green", 'buy', lambda r: r['gap']<-1 and r['n_green']==6, "All 6 candles green (strong recovery)"),
        ("B18: Gap-down + b0 big green>1%", 'buy', lambda r: r['gap']<-1 and r['b0_green'] and r['b0_body']>1, "First candle = big green"),
        ("B19: Gap-down + vol increasing", 'buy', lambda r: r['gap']<-1 and r['vol_trend']>20, "Volume rising (buyers stepping in)"),
        ("B20: Gap-down + OR breakout", 'buy', lambda r: r['gap']<-1 and r['price_vs_or_h']>0.3, "Strong OR breakout upward"),

        # ═══ NON-GAP SELL PATTERNS ═══
        ("S21: Flat open + sell pressure>0.6", 'sell', lambda r: abs(r['gap'])<0.5 and r['sp']>0.6, "No gap but sellers dominating"),
        ("S22: Flat open + 4+ red candles", 'sell', lambda r: abs(r['gap'])<0.5 and r['n_red']>=4, "Quiet open but persistent selling"),
        ("S23: Flat open + below VWAP>0.3%", 'sell', lambda r: abs(r['gap'])<0.5 and r['vwap_dev']<-0.3, "Price slipping below VWAP"),
        ("S24: Flat + negative momentum>0.5%", 'sell', lambda r: abs(r['gap'])<0.5 and r['mom']<-0.5, "Quiet open, accelerating selloff"),
        ("S25: Any gap + sp>0.65 + mom<-1%", 'sell', lambda r: r['sp']>0.65 and r['mom']<-1, "Extreme sell pressure regardless of gap"),

        # ═══ NON-GAP BUY PATTERNS ═══
        ("B21: Flat open + buy pressure>0.6", 'buy', lambda r: abs(r['gap'])<0.5 and r['bp']>0.6, "No gap but buyers dominating"),
        ("B22: Flat open + 4+ green candles", 'buy', lambda r: abs(r['gap'])<0.5 and r['n_green']>=4, "Quiet open but persistent buying"),
        ("B23: Flat open + above VWAP>0.3%", 'buy', lambda r: abs(r['gap'])<0.5 and r['vwap_dev']>0.3, "Price climbing above VWAP"),
        ("B24: Flat + positive momentum>0.5%", 'buy', lambda r: abs(r['gap'])<0.5 and r['mom']>0.5, "Quiet open, accelerating rally"),
        ("B25: Any gap + bp>0.65 + mom>1%", 'buy', lambda r: r['bp']>0.65 and r['mom']>1, "Extreme buy pressure regardless of gap"),

        # ═══ MOMENTUM CONTINUATION ═══
        ("S26: Gap-up continues (mom>0+sp<0.4)", 'sell', lambda r: r['gap']>1 and r['mom']>0 and r['sp']<0.4,
         "Gap-up stock still rising — AVOID sell (or go opposite: buy)"),
        ("B26: Gap-up momentum buy", 'buy', lambda r: r['gap']>1 and r['mom']>0.5 and r['bp']>0.55,
         "Gap-up + buyers still active = momentum continuation BUY"),
        ("B27: Gap-down continues (mom<0+bp<0.4)", 'buy', lambda r: r['gap']<-1 and r['mom']<0 and r['bp']<0.4,
         "Gap-down stock still falling — AVOID buy"),
        ("S27: Gap-down momentum sell", 'sell', lambda r: r['gap']<-1 and r['mom']<-0.5 and r['sp']>0.55,
         "Gap-down + sellers still active = momentum sell"),

        # ═══ VOLATILITY PATTERNS ═══
        ("S28: High OR range + breakdown", 'sell', lambda r: r['or5_rng']>2 and r['price_vs_or_l']<0.2 and r['sp']>0.5,
         "Wide opening range, price near low = sell"),
        ("B28: High OR range + breakout", 'buy', lambda r: r['or5_rng']>2 and r['price_vs_or_h']>-0.2 and r['bp']>0.5,
         "Wide opening range, price near high = buy"),
        ("S29: Tight OR + sell pressure", 'sell', lambda r: r['or5_rng']<0.5 and r['sp']>0.55 and r['gap']>0.5,
         "Tight range coiling + sellers = breakdown coming"),
        ("B29: Tight OR + buy pressure", 'buy', lambda r: r['or5_rng']<0.5 and r['bp']>0.55 and r['gap']<-0.5,
         "Tight range coiling + buyers = breakout coming"),

        # ═══ VOLUME-PRICE DIVERGENCE ═══
        ("S30: Rising price + falling volume", 'sell', lambda r: r['mom']>0 and r['vol_trend']<-30,
         "Price up but volume drying = fake rally, sell"),
        ("B30: Falling price + falling volume", 'buy', lambda r: r['mom']<0 and r['vol_trend']<-30,
         "Price down but volume drying = selling exhausted, buy"),
        ("S31: Volume spike + price rejection", 'sell', lambda r: r['vol_b0_share']>0.4 and r['exhaust_sell']>0.6 and r['gap']>0.5,
         "Big volume but rejected at high = distribution"),
        ("B31: Volume spike + price recovery", 'buy', lambda r: r['vol_b0_share']>0.4 and r['exhaust_buy']>0.6 and r['gap']<-0.5,
         "Big volume but recovered from low = accumulation"),

        # ═══ MEAN REVERSION ═══
        ("S32: Overbought (vwap>0.5% + gap>2%)", 'sell', lambda r: r['vwap_dev']>0.5 and r['gap']>2,
         "Price stretched above VWAP on gap-up = revert down"),
        ("B32: Oversold (vwap<-0.5% + gap<-2%)", 'buy', lambda r: r['vwap_dev']<-0.5 and r['gap']<-2,
         "Price stretched below VWAP on gap-down = revert up"),

        # ═══ CANDLE PATTERN COMBOS ═══
        ("S33: b0 green then 2+ red (top reversal)", 'sell', lambda r: r['b0_green'] and r['n_red']>=3 and r['gap']>0.5,
         "First candle green then selling takes over"),
        ("B33: b0 red then 2+ green (bottom reversal)", 'buy', lambda r: not r['b0_green'] and r['n_green']>=3 and r['gap']<-0.5,
         "First candle red then buying takes over"),
        ("S34: All 6 red + big b0 range", 'sell', lambda r: r['n_red']==6 and r['b0_rng']>1.5,
         "Maximum selling + volatile open = strong reversal"),
        ("B34: All 6 green + big b0 range", 'buy', lambda r: r['n_green']==6 and r['b0_rng']>1.5,
         "Maximum buying + volatile open = strong bounce"),

        # ═══ MULTI-FACTOR HIGH CONFIDENCE ═══
        ("S35: ULTRA SELL (gap>2%+sp>0.6+mom<-0.5+4red)", 'sell',
         lambda r: r['gap']>2 and r['sp']>0.6 and r['mom']<-0.5 and r['n_red']>=4,
         "All sell signals aligned — highest conviction"),
        ("B35: ULTRA BUY (gap<-2%+bp>0.6+mom>0.5+4green)", 'buy',
         lambda r: r['gap']<-2 and r['bp']>0.6 and r['mom']>0.5 and r['n_green']>=4,
         "All buy signals aligned — highest conviction"),
        ("S36: S6>5 + sp>0.55 + below VWAP", 'sell',
         lambda r: r['s6']>5 and r['sp']>0.55 and r['vwap_dev']<0,
         "High S6 score + confirmation signals"),
        ("B36: S6buy>3 + bp>0.55 + above VWAP", 'buy',
         lambda r: r['s6_buy']>3 and r['bp']>0.55 and r['vwap_dev']>0,
         "High BUY S6 score + confirmation"),
    ]

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("PATTERN ENGINE — 60+ Patterns, BUY + SELL\n")
        out.write(f"Records: {len(all_records)}, Days: {len(dates)}\n")
        out.write(f"Patterns tested: {len(patterns)}\n\n")

        # ═══ TEST ALL PATTERNS ═══
        out.write("="*120+"\n1. ALL PATTERNS — win rate at multiple exit points\n"+"="*120+"\n\n")

        sell_results = []
        buy_results = []

        for name, direction, filt, desc in patterns:
            matching = [r for r in all_records if filt(r)]
            if len(matching) < 30: continue

            if direction == 'sell':
                exits = {eb: [r['sell_ret'].get(eb,0) for r in matching if eb in r['sell_ret']] for eb in [29,44,65,89]}
            else:
                exits = {eb: [r['buy_ret'].get(eb,0) for r in matching if eb in r['buy_ret']] for eb in [29,44,65,89]}

            best_exit = max(exits.keys(), key=lambda eb: np.mean(exits[eb]) if exits[eb] else -99)
            best_rets = exits[best_exit]
            if not best_rets: continue

            wr = sum(1 for r in best_rets if r>0)/len(best_rets)*100
            ar = np.mean(best_rets)
            avg_s6 = np.mean([r['s6'] for r in matching]) if direction=='sell' else np.mean([r['s6_buy'] for r in matching])
            avg_v2 = np.mean([r['v2'] for r in matching]) if direction=='sell' else np.mean([r['v2_buy'] for r in matching])

            result = {
                'name':name, 'dir':direction, 'desc':desc,
                'n':len(matching), 'best_exit':best_exit,
                'wr':wr, 'ar':ar, 'avg_s6':avg_s6, 'avg_v2':avg_v2,
                'exits': {eb: (sum(1 for r in rets if r>0)/len(rets)*100 if rets else 0, np.mean(rets) if rets else 0) for eb,rets in exits.items()},
            }
            if direction == 'sell':
                sell_results.append(result)
            else:
                buy_results.append(result)

        # Sort by win rate at best exit
        sell_results.sort(key=lambda x: -x['wr'])
        buy_results.sort(key=lambda x: -x['wr'])

        # Print SELL patterns
        out.write("SELL PATTERNS (sorted by win rate at best exit)\n"+"-"*120+"\n")
        out.write(f"{'Pattern':<50} {'N':>5} {'BestExit':>8} {'Win%':>6} {'AvgRet':>8} {'S6':>5} {'V2':>5} {'b30':>10} {'b45':>10} {'b66':>10} {'b90':>10}\n")
        out.write("-"*120+"\n")
        for r in sell_results:
            b30 = f"{r['exits'][29][0]:.0f}%/{r['exits'][29][1]:+.2f}%" if 29 in r['exits'] else "--"
            b45 = f"{r['exits'][44][0]:.0f}%/{r['exits'][44][1]:+.2f}%" if 44 in r['exits'] else "--"
            b66 = f"{r['exits'][65][0]:.0f}%/{r['exits'][65][1]:+.2f}%" if 65 in r['exits'] else "--"
            b90 = f"{r['exits'][89][0]:.0f}%/{r['exits'][89][1]:+.2f}%" if 89 in r['exits'] else "--"
            out.write(f"{r['name']:<50} {r['n']:>5} b{r['best_exit']+1:>5} {r['wr']:>5.1f}% {r['ar']:>+7.3f}% {r['avg_s6']:>4.1f} {r['avg_v2']:>4.1f} {b30:>10} {b45:>10} {b66:>10} {b90:>10}\n")

        # Print BUY patterns
        out.write(f"\n\nBUY PATTERNS (sorted by win rate at best exit)\n"+"-"*120+"\n")
        out.write(f"{'Pattern':<50} {'N':>5} {'BestExit':>8} {'Win%':>6} {'AvgRet':>8} {'S6b':>5} {'V2b':>5} {'b30':>10} {'b45':>10} {'b66':>10} {'b90':>10}\n")
        out.write("-"*120+"\n")
        for r in buy_results:
            b30 = f"{r['exits'][29][0]:.0f}%/{r['exits'][29][1]:+.2f}%" if 29 in r['exits'] else "--"
            b45 = f"{r['exits'][44][0]:.0f}%/{r['exits'][44][1]:+.2f}%" if 44 in r['exits'] else "--"
            b66 = f"{r['exits'][65][0]:.0f}%/{r['exits'][65][1]:+.2f}%" if 65 in r['exits'] else "--"
            b90 = f"{r['exits'][89][0]:.0f}%/{r['exits'][89][1]:+.2f}%" if 89 in r['exits'] else "--"
            out.write(f"{r['name']:<50} {r['n']:>5} b{r['best_exit']+1:>5} {r['wr']:>5.1f}% {r['ar']:>+7.3f}% {r['avg_s6']:>4.1f} {r['avg_v2']:>4.1f} {b30:>10} {b45:>10} {b66:>10} {b90:>10}\n")

        # ═══ PATTERN EFFECTIVENESS SUMMARY ═══
        out.write(f"\n\n"+"="*120+"\n2. TOP 10 SELL + TOP 10 BUY PATTERNS\n"+"="*120+"\n")
        out.write(f"\n  TOP 10 SELL:\n")
        for i, r in enumerate(sell_results[:10]):
            out.write(f"    {i+1:>2}. {r['name']:<45} Win={r['wr']:.1f}% Ret={r['ar']:+.3f}% N={r['n']} Exit=b{r['best_exit']+1}\n")
            out.write(f"        {r['desc']}\n")

        out.write(f"\n  TOP 10 BUY:\n")
        for i, r in enumerate(buy_results[:10]):
            out.write(f"    {i+1:>2}. {r['name']:<45} Win={r['wr']:.1f}% Ret={r['ar']:+.3f}% N={r['n']} Exit=b{r['best_exit']+1}\n")
            out.write(f"        {r['desc']}\n")

        # ═══ BULLISH vs BEARISH EFFECTIVENESS ═══
        out.write(f"\n\n"+"="*120+"\n3. BULLISH vs BEARISH MARKET EFFECTIVENESS\n"+"="*120+"\n")
        out.write(f"  (Bearish proxy: avg market gap < 0, Bullish: > 0)\n\n")

        # Compute daily market avg gap
        day_regime = {}
        for d in dates:
            avg_gap = np.mean([s['gap'] for s in by_date[d]])
            day_regime[d] = 'bull' if avg_gap > 0 else 'bear'

        bull_recs = [r for r in all_records if day_regime.get(r['date'])=='bull']
        bear_recs = [r for r in all_records if day_regime.get(r['date'])=='bear']
        out.write(f"  Bull days: {sum(1 for v in day_regime.values() if v=='bull')}, Bear days: {sum(1 for v in day_regime.values() if v=='bear')}\n\n")

        # Test key patterns in each regime
        key_patterns = sell_results[:5] + buy_results[:5]
        out.write(f"  {'Pattern':<45} {'BullWin':>8} {'BearWin':>8} {'Better':>8}\n  "+"-"*75+"\n")
        for pinfo in key_patterns:
            name = pinfo['name']; direction = pinfo['dir']
            pat = next((p for p in patterns if p[0]==name), None)
            if pat is None: continue
            filt = pat[2]; eb = pinfo['best_exit']

            bull_match = [r for r in bull_recs if filt(r)]
            bear_match = [r for r in bear_recs if filt(r)]

            if direction == 'sell':
                bull_rets = [r['sell_ret'].get(eb,0) for r in bull_match if eb in r['sell_ret']]
                bear_rets = [r['sell_ret'].get(eb,0) for r in bear_match if eb in r['sell_ret']]
            else:
                bull_rets = [r['buy_ret'].get(eb,0) for r in bull_match if eb in r['buy_ret']]
                bear_rets = [r['buy_ret'].get(eb,0) for r in bear_match if eb in r['buy_ret']]

            bull_wr = sum(1 for r in bull_rets if r>0)/max(len(bull_rets),1)*100
            bear_wr = sum(1 for r in bear_rets if r>0)/max(len(bear_rets),1)*100
            better = "BULL" if bull_wr>bear_wr else "BEAR"
            out.write(f"  {name:<45} {bull_wr:>7.1f}% {bear_wr:>7.1f}% {better:>8}\n")

        # ═══ COMBINED SCORING ═══
        out.write(f"\n\n"+"="*120+"\n4. COMBINED S6+V2 SCORING FOR TOP CANDIDATES\n"+"="*120+"\n")

        # For each day, rank by combined score, show top 5 sell + top 5 buy
        out.write(f"\n  Example day rankings (last 5 trading days):\n")
        for d in dates[-5:]:
            out.write(f"\n  === {d} ===\n")
            recs = [r for r in all_records if r['date']==d]

            # Top sell candidates
            sell_cands = [r for r in recs if r['gap']>0.5 and r['sp']>0.45]
            sell_cands.sort(key=lambda r: -(r['s6']+r['v2']))
            out.write(f"  Top SELL:\n")
            for r in sell_cands[:5]:
                ret = r['sell_ret'].get(89,0)
                outcome = "WIN" if ret>0 else "LOSS"
                out.write(f"    {r['sym']:<12} gap={r['gap']:+.1f}% S6={r['s6']:.1f} V2={r['v2']:.1f} sp={r['sp']:.2f} -> {ret:+.2f}% {outcome}\n")

            # Top buy candidates
            buy_cands = [r for r in recs if r['gap']<-0.5 and r['bp']>0.45]
            buy_cands.sort(key=lambda r: -(r['s6_buy']+r['v2_buy']))
            out.write(f"  Top BUY:\n")
            for r in buy_cands[:5]:
                ret = r['buy_ret'].get(44,0)
                outcome = "WIN" if ret>0 else "LOSS"
                out.write(f"    {r['sym']:<12} gap={r['gap']:+.1f}% S6b={r['s6_buy']:.1f} V2b={r['v2_buy']:.1f} bp={r['bp']:.2f} -> {ret:+.2f}% {outcome}\n")

        out.write(f"\n\nCompleted in {time.time()-t0:.1f}s\n")
    print(f"Done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
