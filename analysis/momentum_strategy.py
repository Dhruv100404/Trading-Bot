"""
MOMENTUM MICRO-SCALP STRATEGY ANALYSIS
========================================
1-minute candles, 1268 liquid stocks, tight 0.1% SL + trailing stop.

Strategy concept:
  Detect short-term momentum bursts (volume spike + price breakout),
  enter immediately, trail aggressively, exit on reversal.

Sections:
  1. Data overview: how often do 0.3%+ moves happen in 1-5 minutes?
  2. Volume spike detection: what threshold predicts momentum?
  3. Breakout patterns: range break, VWAP cross, consecutive candles
  4. Entry signal design: combine filters
  5. Stop loss analysis: does 0.1% SL survive?
  6. Trailing stop variants: fixed %, ATR-based, structure-based
  7. Full backtest: best strategy with realistic costs
  8. Time-of-day filter: when do momentum moves cluster?
  9. Risk metrics: win rate, avg win/loss, Sharpe, max DD
  10. Implementation guide
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'momentum_strategy.txt'
COST = 0.05  # 0.05% per side (tight for momentum scalping)

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading 1-min candle data for liquid stocks...")
    # Store per stock-day: numpy array of (O, H, L, C, V, VC, VW) x 375 buckets
    O,H,L,C,V,VC,VW = 0,1,2,3,4,5,6
    all_data = []  # list of (symbol, date, array[375,7])
    loaded = 0

    for fp in files:
        if not fp.exists(): continue
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = len(bkts)
                if nb < 100: continue  # need at least first 100 minutes

                arr = np.zeros((nb, 7), dtype=np.float32)
                for j, b in enumerate(bkts):
                    arr[j, O] = b['o']
                    arr[j, H] = b['h']
                    arr[j, L] = b['l']
                    arr[j, C] = b['c']
                    arr[j, V] = b['v']
                    arr[j, VC] = b.get('vc', 0)
                    arr[j, VW] = b.get('vw', b['c'])

                if arr[0, O] <= 0: continue
                all_data.append((r['symbol'], r['date'], arr))
                loaded += 1
                if loaded % 20000 == 0:
                    print(f"  {loaded} stock-days loaded... {time.time()-t0:.0f}s")

    dates = sorted(set(d for _,d,_ in all_data))
    print(f"Loaded {loaded} stock-days, {len(dates)} trading days in {time.time()-t0:.0f}s")

    # ═══════════════════════════════════════════════════════════════════
    with open(OUT, 'w', encoding='utf-8') as out:
        out.write("MOMENTUM MICRO-SCALP STRATEGY — DEEP ANALYSIS\n")
        out.write(f"Data: {loaded} stock-days, {len(dates)} trading days, 1-min candles\n")
        out.write(f"Liquid stocks: {len(liquid)}\n\n")

        # ═══════════════════════════════════════
        # 1. HOW OFTEN DO FAST MOVES HAPPEN?
        # ═══════════════════════════════════════
        print("Section 1: Move frequency...")
        out.write("="*110+"\n1. MOVE FREQUENCY: how often does price move X% in N minutes?\n"+"="*110+"\n")
        out.write("  Only counting moves starting from minute 5+ (skip opening auction)\n\n")

        # For each stock-day, scan for max move in N-minute windows
        move_counts = defaultdict(int)  # (pct_threshold, window) -> count
        total_windows = defaultdict(int)

        sample_size = min(len(all_data), 30000)  # sample for speed
        np.random.seed(42)
        sample_idx = np.random.choice(len(all_data), sample_size, replace=False)

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes = arr[:, C]
            highs = arr[:, H]
            lows = arr[:, L]

            for window in [1, 2, 3, 5, 10]:
                for start in range(5, min(nb - window, 90)):  # first 90 minutes focus
                    total_windows[window] += 1
                    # Max UP move in window
                    max_high = np.max(highs[start:start+window])
                    up_move = (max_high - closes[start]) / closes[start] * 100
                    # Max DOWN move in window
                    min_low = np.min(lows[start:start+window])
                    dn_move = (closes[start] - min_low) / closes[start] * 100

                    for thresh in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
                        if up_move >= thresh: move_counts[('UP', thresh, window)] += 1
                        if dn_move >= thresh: move_counts[('DN', thresh, window)] += 1

        out.write(f"  {'Threshold':>10} {'1min':>8} {'2min':>8} {'3min':>8} {'5min':>8} {'10min':>8}\n")
        out.write(f"  "+"-"*50+"\n")
        for thresh in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
            row = f"  >={thresh:.1f}% UP  "
            for w in [1,2,3,5,10]:
                pct = move_counts.get(('UP',thresh,w), 0) / max(total_windows[w],1) * 100
                row += f"  {pct:>5.2f}%"
            out.write(row + "\n")
        out.write("\n")
        for thresh in [0.1, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
            row = f"  >={thresh:.1f}% DN  "
            for w in [1,2,3,5,10]:
                pct = move_counts.get(('DN',thresh,w), 0) / max(total_windows[w],1) * 100
                row += f"  {pct:>5.2f}%"
            out.write(row + "\n")
        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. VOLUME SPIKE AS PREDICTOR
        # ═══════════════════════════════════════
        print("Section 2: Volume spike analysis...")
        out.write(f"\n\n"+"="*110+"\n2. VOLUME SPIKE: does a volume spike predict a move?\n"+"="*110+"\n")
        out.write(f"  Volume spike = current bar volume / avg(last 20 bars volume)\n")
        out.write(f"  Measure: what % of the time does price move >0.3% in next 3 bars after spike?\n\n")

        out.write(f"  {'Spike':>8} {'Freq%':>7} {'UP>0.3':>8} {'DN>0.3':>8} {'UP>0.5':>8} {'DN>0.5':>8} {'AvgMove':>8}\n")
        out.write(f"  "+"-"*60+"\n")

        spike_stats = defaultdict(lambda: {'n':0, 'up3':0, 'dn3':0, 'up5':0, 'dn5':0, 'moves':[]})
        total_bars = 0

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            vols = arr[:, V]
            closes = arr[:, C]
            highs = arr[:, H]
            lows = arr[:, L]

            for i in range(25, min(nb-5, 90)):
                avg_vol = np.mean(vols[i-20:i])
                if avg_vol <= 0: continue
                spike = vols[i] / avg_vol
                total_bars += 1

                # Next 3 bars move
                max_up = np.max(highs[i+1:i+4]) if i+4 <= nb else 0
                max_dn = np.min(lows[i+1:i+4]) if i+4 <= nb else 0
                up_pct = (max_up - closes[i]) / closes[i] * 100 if closes[i] > 0 else 0
                dn_pct = (closes[i] - max_dn) / closes[i] * 100 if closes[i] > 0 else 0
                net_move = (closes[min(i+3, nb-1)] - closes[i]) / closes[i] * 100

                # Bucket by spike level
                for lo, hi, label in [(0,1,'<1x'),(1,2,'1-2x'),(2,3,'2-3x'),(3,5,'3-5x'),(5,10,'5-10x'),(10,999,'>10x')]:
                    if lo <= spike < hi:
                        s = spike_stats[label]
                        s['n'] += 1
                        if up_pct > 0.3: s['up3'] += 1
                        if dn_pct > 0.3: s['dn3'] += 1
                        if up_pct > 0.5: s['up5'] += 1
                        if dn_pct > 0.5: s['dn5'] += 1
                        s['moves'].append(abs(net_move))
                        break

        for label in ['<1x','1-2x','2-3x','3-5x','5-10x','>10x']:
            s = spike_stats[label]
            if s['n'] == 0: continue
            freq = s['n']/total_bars*100
            avg_m = np.mean(s['moves']) if s['moves'] else 0
            out.write(f"  {label:>8}  {freq:>5.1f}%  {s['up3']/s['n']*100:>6.1f}%  {s['dn3']/s['n']*100:>6.1f}%  {s['up5']/s['n']*100:>6.1f}%  {s['dn5']/s['n']*100:>6.1f}%  {avg_m:>6.3f}%\n")
        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. BREAKOUT PATTERNS
        # ═══════════════════════════════════════
        print("Section 3: Breakout patterns...")
        out.write(f"\n\n"+"="*110+"\n3. BREAKOUT PATTERNS: range break, VWAP cross, consecutive candles\n"+"="*110+"\n\n")

        patterns = {
            'high_break_5': {'n':0,'win':0,'ret':[]},
            'high_break_10': {'n':0,'win':0,'ret':[]},
            'high_break_20': {'n':0,'win':0,'ret':[]},
            'low_break_5': {'n':0,'win':0,'ret':[]},
            'low_break_10': {'n':0,'win':0,'ret':[]},
            'low_break_20': {'n':0,'win':0,'ret':[]},
            'vwap_cross_up': {'n':0,'win':0,'ret':[]},
            'vwap_cross_dn': {'n':0,'win':0,'ret':[]},
            '3_green': {'n':0,'win':0,'ret':[]},
            '3_red': {'n':0,'win':0,'ret':[]},
            'big_green_bar': {'n':0,'win':0,'ret':[]},
            'big_red_bar': {'n':0,'win':0,'ret':[]},
            'vol_spike_green': {'n':0,'win':0,'ret':[]},
            'vol_spike_red': {'n':0,'win':0,'ret':[]},
        }

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes = arr[:, C]; opens = arr[:, O]; highs = arr[:, H]; lows = arr[:, L]
            vols = arr[:, V]; vwaps = arr[:, VW]

            for i in range(25, min(nb-10, 90)):
                if closes[i] <= 0: continue
                # Forward 5-bar return (direction of pattern)
                fwd_close = closes[min(i+5, nb-1)]
                fwd_up = (fwd_close - closes[i]) / closes[i] * 100
                fwd_dn = -fwd_up

                avg_vol = np.mean(vols[i-20:i]) if np.mean(vols[i-20:i]) > 0 else 1
                bar_body = (closes[i] - opens[i]) / opens[i] * 100 if opens[i] > 0 else 0
                avg_body = np.mean(np.abs(closes[i-20:i] - opens[i-20:i]) / np.maximum(opens[i-20:i], 1)) * 100

                # High breakout
                for lookback, key in [(5,'high_break_5'),(10,'high_break_10'),(20,'high_break_20')]:
                    prev_high = np.max(highs[i-lookback:i])
                    if closes[i] > prev_high and closes[i-1] <= prev_high:
                        patterns[key]['n'] += 1
                        patterns[key]['ret'].append(fwd_up)
                        if fwd_up > 0: patterns[key]['win'] += 1

                # Low breakout
                for lookback, key in [(5,'low_break_5'),(10,'low_break_10'),(20,'low_break_20')]:
                    prev_low = np.min(lows[i-lookback:i])
                    if closes[i] < prev_low and closes[i-1] >= prev_low:
                        patterns[key]['n'] += 1
                        patterns[key]['ret'].append(fwd_dn)
                        if fwd_dn > 0: patterns[key]['win'] += 1

                # VWAP cross
                if closes[i] > vwaps[i] and closes[i-1] < vwaps[i-1] and vwaps[i] > 0:
                    patterns['vwap_cross_up']['n'] += 1
                    patterns['vwap_cross_up']['ret'].append(fwd_up)
                    if fwd_up > 0: patterns['vwap_cross_up']['win'] += 1
                if closes[i] < vwaps[i] and closes[i-1] > vwaps[i-1] and vwaps[i] > 0:
                    patterns['vwap_cross_dn']['n'] += 1
                    patterns['vwap_cross_dn']['ret'].append(fwd_dn)
                    if fwd_dn > 0: patterns['vwap_cross_dn']['win'] += 1

                # 3 consecutive green/red
                if all(closes[i-j] > opens[i-j] for j in range(3)):
                    patterns['3_green']['n'] += 1
                    patterns['3_green']['ret'].append(fwd_up)
                    if fwd_up > 0: patterns['3_green']['win'] += 1
                if all(closes[i-j] < opens[i-j] for j in range(3)):
                    patterns['3_red']['n'] += 1
                    patterns['3_red']['ret'].append(fwd_dn)
                    if fwd_dn > 0: patterns['3_red']['win'] += 1

                # Big bar (body > 2x avg body)
                if bar_body > 0 and abs(bar_body) > 2 * avg_body:
                    if bar_body > 0:
                        patterns['big_green_bar']['n'] += 1
                        patterns['big_green_bar']['ret'].append(fwd_up)
                        if fwd_up > 0: patterns['big_green_bar']['win'] += 1
                    else:
                        patterns['big_red_bar']['n'] += 1
                        patterns['big_red_bar']['ret'].append(fwd_dn)
                        if fwd_dn > 0: patterns['big_red_bar']['win'] += 1

                # Volume spike + direction
                if vols[i] > 3 * avg_vol:
                    if bar_body > 0.1:
                        patterns['vol_spike_green']['n'] += 1
                        patterns['vol_spike_green']['ret'].append(fwd_up)
                        if fwd_up > 0: patterns['vol_spike_green']['win'] += 1
                    elif bar_body < -0.1:
                        patterns['vol_spike_red']['n'] += 1
                        patterns['vol_spike_red']['ret'].append(fwd_dn)
                        if fwd_dn > 0: patterns['vol_spike_red']['win'] += 1

        out.write(f"  {'Pattern':>20} {'N':>8} {'Win%':>7} {'AvgRet':>8} {'Median':>8}\n")
        out.write(f"  "+"-"*58+"\n")
        for key, s in sorted(patterns.items(), key=lambda x: -np.mean(x[1]['ret']) if x[1]['ret'] else 0):
            if s['n'] < 50: continue
            wr = s['win']/s['n']*100
            ar = np.mean(s['ret'])
            mr = np.median(s['ret'])
            out.write(f"  {key:>20}  {s['n']:>7}  {wr:>5.1f}%  {ar:>+6.3f}%  {mr:>+6.3f}%\n")
        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. COMBINED ENTRY SIGNALS
        # ═══════════════════════════════════════
        print("Section 4: Combined signals...")
        out.write(f"\n\n"+"="*110+"\n4. COMBINED ENTRY SIGNALS: stack filters for higher probability\n"+"="*110+"\n\n")

        combos = {
            'high5_vol3x': {'n':0,'win':0,'ret':[]},
            'high10_vol3x': {'n':0,'win':0,'ret':[]},
            'high5_vol3x_aboveVWAP': {'n':0,'win':0,'ret':[]},
            'high10_vol5x': {'n':0,'win':0,'ret':[]},
            'low5_vol3x': {'n':0,'win':0,'ret':[]},
            'low10_vol3x': {'n':0,'win':0,'ret':[]},
            'low5_vol3x_belowVWAP': {'n':0,'win':0,'ret':[]},
            '3green_vol3x_aboveVWAP': {'n':0,'win':0,'ret':[]},
            '3red_vol3x_belowVWAP': {'n':0,'win':0,'ret':[]},
            'biggreen_vol3x': {'n':0,'win':0,'ret':[]},
            'bigred_vol3x': {'n':0,'win':0,'ret':[]},
            'vwap_up_vol3x': {'n':0,'win':0,'ret':[]},
            'vwap_dn_vol3x': {'n':0,'win':0,'ret':[]},
            'high10_3green_vol3x': {'n':0,'win':0,'ret':[]},
            'low10_3red_vol3x': {'n':0,'win':0,'ret':[]},
        }

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes=arr[:,C]; opens=arr[:,O]; highs=arr[:,H]; lows=arr[:,L]
            vols=arr[:,V]; vwaps=arr[:,VW]

            for i in range(25, min(nb-10, 90)):
                if closes[i] <= 0: continue
                fwd = closes[min(i+5, nb-1)]
                fwd_up = (fwd - closes[i]) / closes[i] * 100
                fwd_dn = -fwd_up
                avg_vol = np.mean(vols[i-20:i])
                if avg_vol <= 0: avg_vol = 1
                vol_ratio = vols[i] / avg_vol
                bar_body = (closes[i] - opens[i]) / opens[i] * 100 if opens[i] > 0 else 0
                avg_body = np.mean(np.abs(closes[i-20:i] - opens[i-20:i]) / np.maximum(opens[i-20:i], 1)) * 100
                above_vwap = closes[i] > vwaps[i] and vwaps[i] > 0
                below_vwap = closes[i] < vwaps[i] and vwaps[i] > 0
                high5 = closes[i] > np.max(highs[i-5:i]) and closes[i-1] <= np.max(highs[i-5:i-1]) if i >= 5 else False
                high10 = closes[i] > np.max(highs[i-10:i]) and closes[i-1] <= np.max(highs[i-10:i-1]) if i >= 10 else False
                low5 = closes[i] < np.min(lows[i-5:i]) and closes[i-1] >= np.min(lows[i-5:i-1]) if i >= 5 else False
                low10 = closes[i] < np.min(lows[i-10:i]) and closes[i-1] >= np.min(lows[i-10:i-1]) if i >= 10 else False
                g3 = all(closes[i-j] > opens[i-j] for j in range(3))
                r3 = all(closes[i-j] < opens[i-j] for j in range(3))
                big_g = bar_body > 0 and abs(bar_body) > 2*avg_body
                big_r = bar_body < 0 and abs(bar_body) > 2*avg_body
                vwap_up = closes[i] > vwaps[i] and closes[i-1] < vwaps[i-1] if vwaps[i] > 0 else False
                vwap_dn = closes[i] < vwaps[i] and closes[i-1] > vwaps[i-1] if vwaps[i] > 0 else False

                def record(key, ret):
                    combos[key]['n'] += 1
                    combos[key]['ret'].append(ret)
                    if ret > 0: combos[key]['win'] += 1

                if high5 and vol_ratio >= 3: record('high5_vol3x', fwd_up)
                if high10 and vol_ratio >= 3: record('high10_vol3x', fwd_up)
                if high5 and vol_ratio >= 3 and above_vwap: record('high5_vol3x_aboveVWAP', fwd_up)
                if high10 and vol_ratio >= 5: record('high10_vol5x', fwd_up)
                if low5 and vol_ratio >= 3: record('low5_vol3x', fwd_dn)
                if low10 and vol_ratio >= 3: record('low10_vol3x', fwd_dn)
                if low5 and vol_ratio >= 3 and below_vwap: record('low5_vol3x_belowVWAP', fwd_dn)
                if g3 and vol_ratio >= 3 and above_vwap: record('3green_vol3x_aboveVWAP', fwd_up)
                if r3 and vol_ratio >= 3 and below_vwap: record('3red_vol3x_belowVWAP', fwd_dn)
                if big_g and vol_ratio >= 3: record('biggreen_vol3x', fwd_up)
                if big_r and vol_ratio >= 3: record('bigred_vol3x', fwd_dn)
                if vwap_up and vol_ratio >= 3: record('vwap_up_vol3x', fwd_up)
                if vwap_dn and vol_ratio >= 3: record('vwap_dn_vol3x', fwd_dn)
                if high10 and g3 and vol_ratio >= 3: record('high10_3green_vol3x', fwd_up)
                if low10 and r3 and vol_ratio >= 3: record('low10_3red_vol3x', fwd_dn)

        out.write(f"  {'Signal':>28} {'N':>7} {'Win%':>7} {'AvgRet':>8} {'Median':>8}\n")
        out.write(f"  "+"-"*62+"\n")
        for key, s in sorted(combos.items(), key=lambda x: -np.mean(x[1]['ret']) if x[1]['ret'] else 0):
            if s['n'] < 20: continue
            wr = s['win']/s['n']*100
            ar = np.mean(s['ret'])
            mr = np.median(s['ret'])
            out.write(f"  {key:>28}  {s['n']:>6}  {wr:>5.1f}%  {ar:>+6.3f}%  {mr:>+6.3f}%\n")
        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. SL SURVIVAL: can 0.1% SL work?
        # ═══════════════════════════════════════
        print("Section 5: Stop loss survival...")
        out.write(f"\n\n"+"="*110+"\n5. STOP LOSS SURVIVAL: what % of entries survive 0.1% SL?\n"+"="*110+"\n")
        out.write(f"  For each breakout signal, check if price dips 0.1% adverse before moving 0.3% favorable\n\n")

        for sl_pct in [0.05, 0.1, 0.15, 0.2, 0.3, 0.5]:
            survived = 0; stopped = 0; won = 0
            # Use high10 + vol3x as representative signal
            for idx in sample_idx:
                sym, date, arr = all_data[idx]
                nb = len(arr)
                closes=arr[:,C]; highs=arr[:,H]; lows=arr[:,L]; vols=arr[:,V]
                for i in range(25, min(nb-20, 90)):
                    if closes[i] <= 0: continue
                    avg_vol = np.mean(vols[i-20:i])
                    if avg_vol <= 0: continue
                    if vols[i] < 3 * avg_vol: continue
                    if closes[i] <= np.max(highs[i-10:i]): continue  # not a breakout

                    entry = closes[i]
                    sl_price = entry * (1 - sl_pct/100)
                    # Simulate forward
                    hit_sl = False
                    for j in range(i+1, min(i+20, nb)):
                        if lows[j] <= sl_price:
                            stopped += 1; hit_sl = True; break
                        if highs[j] >= entry * (1 + 0.3/100):
                            survived += 1; won += 1; break
                    else:
                        survived += 1  # neither SL nor target hit in 20 bars

            total = survived + stopped
            if total > 0:
                out.write(f"  SL={sl_pct:.2f}%: survived={survived}/{total} ({survived/total*100:.1f}%), stopped={stopped} ({stopped/total*100:.1f}%), won_0.3%={won} ({won/total*100:.1f}%)\n")
        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. TRAILING STOP VARIANTS
        # ═══════════════════════════════════════
        print("Section 6: Trailing stop variants...")
        out.write(f"\n\n"+"="*110+"\n6. TRAILING STOP SIMULATION: various trailing methods\n"+"="*110+"\n")
        out.write(f"  Entry: high-10 breakout + vol>=3x. Hold up to 20 bars.\n\n")

        def simulate_trailing(arr, entry_idx, direction, initial_sl_pct, trail_method, max_bars=20):
            """
            direction: 1=long, -1=short
            trail_method: 'fixed_pct', 'atr', 'breakeven_then_trail', 'step'
            Returns: (pnl_pct, exit_reason, bars_held)
            """
            nb = len(arr)
            entry = arr[entry_idx, C]
            if entry <= 0: return (0, 'bad', 0)

            sl = entry * (1 - direction * initial_sl_pct / 100)
            best = entry

            # ATR for adaptive trailing
            if entry_idx >= 20:
                ranges = arr[entry_idx-20:entry_idx, H] - arr[entry_idx-20:entry_idx, L]
                atr = float(np.mean(ranges))
            else:
                atr = entry * 0.002  # fallback 0.2%

            for j in range(entry_idx + 1, min(entry_idx + max_bars + 1, nb)):
                h = arr[j, H]; l = arr[j, L]; c = arr[j, C]

                if direction == 1:  # LONG
                    # Check SL hit
                    if l <= sl:
                        pnl = (sl - entry) / entry * 100
                        return (pnl, 'SL', j - entry_idx)
                    # Update best
                    if h > best:
                        best = h
                        # Trail logic
                        if trail_method == 'fixed_pct':
                            sl = max(sl, best * (1 - initial_sl_pct / 100))
                        elif trail_method == 'atr':
                            sl = max(sl, best - 1.5 * atr)
                        elif trail_method == 'breakeven_then_trail':
                            if best >= entry * (1 + 0.15/100):  # moved 0.15% in favor
                                sl = max(sl, entry)  # breakeven
                            if best >= entry * (1 + 0.3/100):  # moved 0.3% in favor
                                sl = max(sl, best * (1 - 0.15/100))  # trail at 0.15%
                        elif trail_method == 'step':
                            move = (best - entry) / entry * 100
                            if move >= 0.5: sl = max(sl, entry * (1 + 0.3/100))
                            elif move >= 0.3: sl = max(sl, entry * (1 + 0.15/100))
                            elif move >= 0.15: sl = max(sl, entry)
                else:  # SHORT
                    if h >= sl:
                        pnl = (entry - sl) / entry * 100
                        return (pnl, 'SL', j - entry_idx)
                    if l < best:
                        best = l
                        if trail_method == 'fixed_pct':
                            sl = min(sl, best * (1 + initial_sl_pct / 100))
                        elif trail_method == 'atr':
                            sl = min(sl, best + 1.5 * atr)
                        elif trail_method == 'breakeven_then_trail':
                            if best <= entry * (1 - 0.15/100):
                                sl = min(sl, entry)
                            if best <= entry * (1 - 0.3/100):
                                sl = min(sl, best * (1 + 0.15/100))
                        elif trail_method == 'step':
                            move = (entry - best) / entry * 100
                            if move >= 0.5: sl = min(sl, entry * (1 - 0.3/100))
                            elif move >= 0.3: sl = min(sl, entry * (1 - 0.15/100))
                            elif move >= 0.15: sl = min(sl, entry)

            # Time exit
            exit_price = arr[min(entry_idx + max_bars, nb-1), C]
            pnl = direction * (exit_price - entry) / entry * 100
            return (pnl, 'TIME', max_bars)

        # Run trailing stop comparison
        out.write(f"  {'Method':>25} {'SL%':>5} {'Trades':>7} {'Win%':>7} {'AvgRet':>8} {'AvgWin':>8} {'AvgLoss':>8} {'PF':>6}\n")
        out.write(f"  "+"-"*80+"\n")

        for sl_pct in [0.1, 0.15, 0.2, 0.3]:
            for method in ['fixed_pct', 'atr', 'breakeven_then_trail', 'step']:
                results = []
                for idx in sample_idx:
                    sym, date, arr = all_data[idx]
                    nb = len(arr)
                    closes=arr[:,C]; highs=arr[:,H]; lows=arr[:,L]; vols=arr[:,V]; vwaps=arr[:,VW]
                    for i in range(25, min(nb-25, 90)):
                        if closes[i] <= 0: continue
                        avg_vol = np.mean(vols[i-20:i])
                        if avg_vol <= 0: continue
                        if vols[i] < 3 * avg_vol: continue

                        # LONG: high-10 breakout + above VWAP
                        if i >= 10 and closes[i] > np.max(highs[i-10:i]) and closes[i] > vwaps[i]:
                            pnl, reason, bars = simulate_trailing(arr, i, 1, sl_pct, method, 20)
                            results.append(pnl - COST)

                        # SHORT: low-10 breakout + below VWAP
                        if i >= 10 and closes[i] < np.min(lows[i-10:i]) and closes[i] < vwaps[i]:
                            pnl, reason, bars = simulate_trailing(arr, i, -1, sl_pct, method, 20)
                            results.append(pnl - COST)

                if len(results) < 20: continue
                results = np.array(results)
                wins = results[results > 0]
                losses = results[results <= 0]
                wr = len(wins)/len(results)*100
                aw = np.mean(wins) if len(wins)>0 else 0
                al = np.mean(losses) if len(losses)>0 else 0
                pf = abs(np.sum(wins)/np.sum(losses)) if np.sum(losses) != 0 else 99
                out.write(f"  {method:>25} {sl_pct:>4.1f}%  {len(results):>6}  {wr:>5.1f}%  {np.mean(results):>+6.3f}%  {aw:>+6.3f}%  {al:>+6.3f}%  {pf:>5.2f}\n")
            out.write("\n")
        print(f"  Section 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. TIME-OF-DAY ANALYSIS
        # ═══════════════════════════════════════
        print("Section 7: Time of day...")
        out.write(f"\n"+"="*110+"\n7. TIME OF DAY: when do momentum trades work best?\n"+"="*110+"\n")
        out.write(f"  Using best trailing method from above, bucket by entry minute\n\n")

        time_stats = defaultdict(lambda: {'n':0, 'ret':[], 'win':0})
        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes=arr[:,C]; highs=arr[:,H]; lows=arr[:,L]; vols=arr[:,V]; vwaps=arr[:,VW]
            for i in range(25, min(nb-25, 300)):  # scan full day
                if closes[i] <= 0: continue
                avg_vol = np.mean(vols[i-20:i])
                if avg_vol <= 0: continue
                if vols[i] < 3 * avg_vol: continue
                if i < 10: continue

                # Time bucket (15-min windows)
                minute = i  # bucket = minute since 9:15
                h = 9 + (15 + minute) // 60
                m = (15 + minute) % 60
                time_key = f"{h}:{m//15*15:02d}"

                entry_triggered = False
                if closes[i] > np.max(highs[i-10:i]) and closes[i] > vwaps[i]:
                    pnl, _, _ = simulate_trailing(arr, i, 1, 0.15, 'breakeven_then_trail', 20)
                    entry_triggered = True
                elif closes[i] < np.min(lows[i-10:i]) and closes[i] < vwaps[i]:
                    pnl, _, _ = simulate_trailing(arr, i, -1, 0.15, 'breakeven_then_trail', 20)
                    entry_triggered = True

                if entry_triggered:
                    net = pnl - COST
                    time_stats[time_key]['n'] += 1
                    time_stats[time_key]['ret'].append(net)
                    if net > 0: time_stats[time_key]['win'] += 1

        out.write(f"  {'Time':>8} {'N':>7} {'Win%':>7} {'AvgRet':>8} {'TotalRet':>9}\n")
        out.write(f"  "+"-"*45+"\n")
        for tk in sorted(time_stats.keys()):
            s = time_stats[tk]
            if s['n'] < 10: continue
            wr = s['win']/s['n']*100
            ar = np.mean(s['ret'])
            tr = np.sum(s['ret'])
            out.write(f"  {tk:>8}  {s['n']:>6}  {wr:>5.1f}%  {ar:>+6.3f}%  {tr:>+7.2f}%\n")
        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. FULL BACKTEST: BEST STRATEGY
        # ═══════════════════════════════════════
        print("Section 8: Full backtest...")
        out.write(f"\n\n"+"="*110+"\n8. FULL BACKTEST: best config across all data\n"+"="*110+"\n")
        out.write(f"  Entry: 10-bar high/low breakout + vol>=3x + VWAP confirm\n")
        out.write(f"  SL: 0.15%, Trail: breakeven@0.15% then trail@0.15%, max hold: 20 bars\n")
        out.write(f"  Cost: 0.05% per trade (both sides)\n\n")

        day_pnls = defaultdict(float)
        day_trades = defaultdict(int)
        all_trades = []

        for sym, date, arr in all_data:
            nb = len(arr)
            closes=arr[:,C]; highs=arr[:,H]; lows=arr[:,L]; vols=arr[:,V]; vwaps=arr[:,VW]

            for i in range(25, min(nb-25, 120)):  # first 2 hours
                if closes[i] <= 0: continue
                avg_vol = np.mean(vols[i-20:i])
                if avg_vol <= 0: continue
                if vols[i] < 3 * avg_vol: continue
                if i < 10: continue

                if closes[i] > np.max(highs[i-10:i]) and closes[i] > vwaps[i]:
                    pnl, reason, bars = simulate_trailing(arr, i, 1, 0.15, 'breakeven_then_trail', 20)
                    net = pnl - COST
                    day_pnls[date] += net
                    day_trades[date] += 1
                    all_trades.append({'date':date,'sym':sym,'dir':'LONG','min':i,'pnl':net,'reason':reason,'bars':bars})

                elif closes[i] < np.min(lows[i-10:i]) and closes[i] < vwaps[i]:
                    pnl, reason, bars = simulate_trailing(arr, i, -1, 0.15, 'breakeven_then_trail', 20)
                    net = pnl - COST
                    day_pnls[date] += net
                    day_trades[date] += 1
                    all_trades.append({'date':date,'sym':sym,'dir':'SHORT','min':i,'pnl':net,'reason':reason,'bars':bars})

        all_pnls = [t['pnl'] for t in all_trades]
        if all_pnls:
            pnls_arr = np.array(all_pnls)
            wins = pnls_arr[pnls_arr > 0]
            losses = pnls_arr[pnls_arr <= 0]
            out.write(f"  Total trades: {len(all_trades)}\n")
            out.write(f"  Avg trades/day: {len(all_trades)/max(len(dates),1):.1f}\n")
            out.write(f"  Win rate: {len(wins)/len(pnls_arr)*100:.1f}%\n")
            out.write(f"  Avg return: {np.mean(pnls_arr):+.4f}%\n")
            out.write(f"  Avg WIN: {np.mean(wins):+.4f}%\n")
            out.write(f"  Avg LOSS: {np.mean(losses):+.4f}%\n")
            out.write(f"  Profit factor: {abs(np.sum(wins)/np.sum(losses)):.2f}\n")
            out.write(f"  Total cumulative return: {np.sum(pnls_arr):+.2f}%\n")

            # Per-day stats
            dpnl_list = sorted(day_pnls.items())
            day_rets = [v for _,v in dpnl_list]
            out.write(f"\n  Day-level stats:\n")
            out.write(f"    Win days: {sum(1 for r in day_rets if r>0)}/{len(day_rets)} ({sum(1 for r in day_rets if r>0)/max(len(day_rets),1)*100:.1f}%)\n")
            out.write(f"    Avg day P&L: {np.mean(day_rets):+.3f}%\n")
            out.write(f"    Best day: {max(day_rets):+.3f}%\n")
            out.write(f"    Worst day: {min(day_rets):+.3f}%\n")

            # Sharpe
            if np.std(day_rets) > 0:
                sharpe = np.mean(day_rets)/np.std(day_rets)*np.sqrt(252)
                out.write(f"    Sharpe (annualized): {sharpe:.2f}\n")

            # Max drawdown
            cum = np.cumsum(day_rets)
            maxdd = float(min(cum - np.maximum.accumulate(cum)))
            out.write(f"    Max drawdown: {maxdd:+.3f}%\n")

            # Long vs Short breakdown
            longs = [t['pnl'] for t in all_trades if t['dir']=='LONG']
            shorts = [t['pnl'] for t in all_trades if t['dir']=='SHORT']
            out.write(f"\n  Direction breakdown:\n")
            out.write(f"    LONG:  N={len(longs)} Win={sum(1 for p in longs if p>0)/max(len(longs),1)*100:.1f}% Avg={np.mean(longs):+.4f}%\n")
            out.write(f"    SHORT: N={len(shorts)} Win={sum(1 for p in shorts if p>0)/max(len(shorts),1)*100:.1f}% Avg={np.mean(shorts):+.4f}%\n")

            # Exit reason breakdown
            reasons = defaultdict(list)
            for t in all_trades: reasons[t['reason']].append(t['pnl'])
            out.write(f"\n  Exit reason breakdown:\n")
            for reason, pnls in sorted(reasons.items()):
                out.write(f"    {reason:>6}: N={len(pnls)} Avg={np.mean(pnls):+.4f}%\n")

            # Bars held distribution
            bars_held = [t['bars'] for t in all_trades]
            out.write(f"\n  Bars held: avg={np.mean(bars_held):.1f} median={np.median(bars_held):.0f}\n")

        print(f"  Section 8 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 9. IMPLEMENTATION GUIDE
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n9. IMPLEMENTATION GUIDE\n"+"="*110+"\n")
        out.write("""
  MOMENTUM MICRO-SCALP STRATEGY:

    ENTRY (LONG):
      1. Price closes above 10-bar high (breakout)
      2. Current bar volume >= 3x avg(last 20 bars)
      3. Price above VWAP (trend confirmation)
      4. Time: first 2 hours of trading (9:40 AM to 11:15 AM)

    ENTRY (SHORT):
      1. Price closes below 10-bar low (breakdown)
      2. Current bar volume >= 3x avg(last 20 bars)
      3. Price below VWAP
      4. Same time window

    STOP LOSS:
      Initial: 0.15% from entry
      Move to breakeven when price moves 0.15% in favor
      Trail at 0.15% from peak when price moves 0.3%+ in favor

    EXIT:
      1. Trailing stop hit (primary)
      2. Time exit: 20 minutes max hold
      3. No fixed take-profit (let winners run via trail)

    FILTERS:
      - Only liquid stocks (5L+ daily volume)
      - Skip first 25 minutes (9:15-9:40 = opening noise)
      - Max 1 trade per stock per day (avoid re-entries)

    EDGE CASES:
      - Low volume day: vol spike threshold auto-adjusts (3x of THAT day's avg)
      - Choppy market: VWAP filter removes most false breakouts
      - Fake breakout: tight 0.15% SL + breakeven trail limits damage
""")

    print(f"\nCompleted in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
