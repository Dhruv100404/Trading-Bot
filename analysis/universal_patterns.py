"""
UNIVERSAL PATTERN DISCOVERY — Fibonacci, Golden Ratio, Harmonics, Fractals
===========================================================================
Test whether mathematical constants from nature appear in stock price action.

Sections:
  1. Fibonacci Retracements: after a swing, do bounces cluster at 23.6/38.2/50/61.8/78.6%?
  2. Fibonacci Extensions: do target moves hit 1.272/1.618/2.618 of prior swing?
  3. Golden Ratio Swings: do consecutive swing sizes relate by phi (1.618)?
  4. Fibonacci Time: do reversals cluster at fib minutes (1,2,3,5,8,13,21,34)?
  5. Harmonic ABCD: does AB=CD or AB*1.618=CD produce tradeable patterns?
  6. Hurst Exponent: is price trending (H>0.5) or mean-reverting (H<0.5)?
  7. Fractal Dimension: do stocks with extreme fractal dims move differently?
  8. Combined: best fib pattern + volume + VWAP for actual edge
  9. Full backtest of best pattern
"""
import json, numpy as np, time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'universal_patterns.txt'
COST = 0.05
PHI = (1 + np.sqrt(5)) / 2  # 1.6180339...
FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
FIB_EXT = [1.272, 1.618, 2.0, 2.618]
FIB_TIMES = [1, 2, 3, 5, 8, 13, 21, 34, 55]

def find_swings(closes, highs, lows, min_swing_pct=0.3, lookback=5):
    """Find swing highs and lows using simple pivot detection (numpy-friendly)."""
    n = len(closes)
    swings = []  # (index, price, type='H'|'L')
    i = lookback
    while i < n - lookback:
        # Swing high: highest in window
        window_h = highs[i-lookback:i+lookback+1]
        if highs[i] == np.max(window_h) and highs[i] > 0:
            swings.append((i, float(highs[i]), 'H'))
            i += lookback
            continue
        # Swing low: lowest in window
        window_l = lows[i-lookback:i+lookback+1]
        if lows[i] == np.min(window_l) and lows[i] > 0:
            swings.append((i, float(lows[i]), 'L'))
            i += lookback
            continue
        i += 1
    # Filter alternating H/L and min swing size
    filtered = []
    for s in swings:
        if not filtered:
            filtered.append(s)
            continue
        if s[2] == filtered[-1][2]:
            # Same type: keep the more extreme one
            if s[2] == 'H' and s[1] > filtered[-1][1]:
                filtered[-1] = s
            elif s[2] == 'L' and s[1] < filtered[-1][1]:
                filtered[-1] = s
        else:
            # Check min swing size
            swing_pct = abs(s[1] - filtered[-1][1]) / filtered[-1][1] * 100
            if swing_pct >= min_swing_pct:
                filtered.append(s)
    return filtered

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']

    print("Loading 1-min candle data...")
    O,H,L,C,V,VW = 0,1,2,3,4,5
    all_data = []
    loaded = 0

    for fp in files:
        if not fp.exists(): continue
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid: continue
                bkts = r['buckets']
                nb = len(bkts)
                if nb < 100: continue

                arr = np.zeros((nb, 6), dtype=np.float32)
                for j, b in enumerate(bkts):
                    arr[j,O]=b['o']; arr[j,H]=b['h']; arr[j,L]=b['l']
                    arr[j,C]=b['c']; arr[j,V]=b['v']; arr[j,VW]=b.get('vw',b['c'])
                if arr[0,O] <= 0: continue
                all_data.append((r['symbol'], r['date'], arr))
                loaded += 1
                if loaded % 20000 == 0:
                    print(f"  {loaded} loaded... {time.time()-t0:.0f}s")

    # Sample for speed
    np.random.seed(42)
    sample_size = min(len(all_data), 25000)
    sample_idx = np.random.choice(len(all_data), sample_size, replace=False)
    dates = sorted(set(d for _,d,_ in all_data))
    print(f"Loaded {loaded}, sampling {sample_size}, {len(dates)} days in {time.time()-t0:.0f}s")

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write(f"UNIVERSAL PATTERN DISCOVERY — Fibonacci, Golden Ratio, Harmonics, Fractals\n")
        out.write(f"Data: {loaded} stock-days, {len(dates)} days, sampled {sample_size}\n")
        out.write(f"Golden Ratio (phi): {PHI:.6f}\n\n")

        # ═══════════════════════════════════════
        # 1. FIBONACCI RETRACEMENTS
        # ═══════════════════════════════════════
        print("Section 1: Fibonacci Retracements...")
        out.write("="*110+"\n1. FIBONACCI RETRACEMENTS: do bounces cluster at fib levels?\n"+"="*110+"\n")
        out.write("  After a swing move, check where the retracement lands.\n")
        out.write("  If fib levels are real, retracements should CLUSTER at 38.2/50/61.8%.\n\n")

        # Bucket retracement depths and measure bounce success
        retrace_buckets = defaultdict(lambda: {'n':0, 'bounce':0, 'fwd_ret':[]})
        total_retracements = 0

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            closes = arr[:,C]; highs = arr[:,H]; lows = arr[:,L]
            swings = find_swings(closes, highs, lows, min_swing_pct=0.3, lookback=3)

            for i in range(2, len(swings)):
                s0, s1, s2 = swings[i-2], swings[i-1], swings[i]
                # s0->s1 is the impulse, s1->s2 is the retracement
                impulse = abs(s1[1] - s0[1])
                if impulse < 0.001: continue
                retrace = abs(s2[1] - s1[1])
                retrace_pct = retrace / impulse

                # Which fib zone does this fall in?
                for lo, hi, label in [
                    (0.00, 0.15, '0-15%'), (0.15, 0.30, '15-30%'),
                    (0.20, 0.26, '~23.6%'), (0.35, 0.42, '~38.2%'),
                    (0.47, 0.53, '~50.0%'), (0.58, 0.65, '~61.8%'),
                    (0.75, 0.82, '~78.6%'), (0.82, 1.00, '82-100%'),
                    (1.00, 1.50, '>100%'),
                ]:
                    if lo <= retrace_pct < hi:
                        total_retracements += 1
                        b = retrace_buckets[label]
                        b['n'] += 1
                        # Did price bounce from retrace level? Check next swing
                        if i < len(swings) - 1:
                            s3 = swings[i+1]
                            # Bounce = next swing goes back toward impulse direction
                            if s1[2] == 'H':  # impulse was up, retrace was down
                                bounce_pct = (s3[1] - s2[1]) / s2[1] * 100
                            else:  # impulse was down, retrace was up
                                bounce_pct = (s2[1] - s3[1]) / s2[1] * 100
                            if bounce_pct > 0.2: b['bounce'] += 1
                            b['fwd_ret'].append(bounce_pct)

        out.write(f"  Total swing retracements analyzed: {total_retracements}\n\n")
        out.write(f"  {'Level':>10} {'Count':>7} {'Freq%':>7} {'Bounce%':>8} {'AvgFwd':>8} {'MedianFwd':>10}\n")
        out.write(f"  "+"-"*55+"\n")
        for label in ['0-15%','15-30%','~23.6%','~38.2%','~50.0%','~61.8%','~78.6%','82-100%','>100%']:
            b = retrace_buckets[label]
            if b['n'] < 10: continue
            freq = b['n']/max(total_retracements,1)*100
            br = b['bounce']/b['n']*100
            avg = np.mean(b['fwd_ret']) if b['fwd_ret'] else 0
            med = np.median(b['fwd_ret']) if b['fwd_ret'] else 0
            fib_marker = " <-- FIB" if label.startswith('~') else ""
            out.write(f"  {label:>10}  {b['n']:>6}  {freq:>5.1f}%  {br:>6.1f}%  {avg:>+6.3f}%  {med:>+8.3f}%{fib_marker}\n")
        print(f"  Section 1 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 2. FIBONACCI EXTENSIONS
        # ═══════════════════════════════════════
        print("Section 2: Fibonacci Extensions...")
        out.write(f"\n\n"+"="*110+"\n2. FIBONACCI EXTENSIONS: do moves hit 1.272/1.618/2.618 of prior swing?\n"+"="*110+"\n")
        out.write(f"  After retracement, does the next impulse extend to fib ratios of the first impulse?\n\n")

        ext_hits = defaultdict(lambda: {'n':0, 'hit':0})
        total_extensions = 0

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            closes = arr[:,C]; highs = arr[:,H]; lows = arr[:,L]
            swings = find_swings(closes, highs, lows, min_swing_pct=0.3, lookback=3)

            for i in range(2, len(swings)-1):
                s0, s1, s2, s3 = swings[i-2], swings[i-1], swings[i], swings[i+1] if i+1<len(swings) else None
                if s3 is None: continue
                impulse1 = abs(s1[1] - s0[1])
                if impulse1 < 0.01: continue
                impulse2 = abs(s3[1] - s2[1])
                ext_ratio = impulse2 / impulse1
                total_extensions += 1

                # Check which extension level it reached
                for target in [1.0, 1.272, 1.618, 2.0, 2.618]:
                    tol = 0.08  # 8% tolerance
                    if abs(ext_ratio - target) / target < tol:
                        ext_hits[f'{target:.3f}']['hit'] += 1
                    ext_hits[f'{target:.3f}']['n'] = total_extensions

        out.write(f"  Total extension swings: {total_extensions}\n\n")
        out.write(f"  {'Extension':>10} {'Hits':>7} {'Hit%':>7} {'Expected':>9}  {'Signal?':>8}\n")
        out.write(f"  "+"-"*50+"\n")
        for target in [1.0, 1.272, 1.618, 2.0, 2.618]:
            key = f'{target:.3f}'
            h = ext_hits[key]
            hit_pct = h['hit']/max(total_extensions,1)*100
            # Expected: if random, what % would fall in ±8% band?
            # Band width = 0.16 * target, range is ~0 to ~5, so expected = 0.16*target/5*100
            expected = 0.16 * target / 5.0 * 100
            signal = "YES" if hit_pct > expected * 1.5 else "no"
            out.write(f"  {target:>9.3f}x  {h['hit']:>6}  {hit_pct:>5.2f}%  {expected:>7.2f}%   {signal:>7}\n")
        print(f"  Section 2 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 3. GOLDEN RATIO IN CONSECUTIVE SWINGS
        # ═══════════════════════════════════════
        print("Section 3: Golden Ratio Swings...")
        out.write(f"\n\n"+"="*110+"\n3. GOLDEN RATIO SWINGS: do consecutive swing sizes relate by phi?\n"+"="*110+"\n")
        out.write(f"  Phi = {PHI:.6f}. Test: is swing_n+1 / swing_n clustered around 0.618 or 1.618?\n\n")

        swing_ratios = []
        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            swings = find_swings(arr[:,C], arr[:,H], arr[:,L], min_swing_pct=0.2, lookback=3)
            for i in range(1, len(swings)):
                s_prev = abs(swings[i][1] - swings[i-1][1])
                if i > 1:
                    s_prev2 = abs(swings[i-1][1] - swings[i-2][1])
                    if s_prev2 > 0.01:
                        swing_ratios.append(s_prev / s_prev2)

        if swing_ratios:
            ratios = np.array(swing_ratios)
            out.write(f"  Total consecutive swing pairs: {len(ratios)}\n\n")

            # Distribution of ratios
            out.write(f"  Ratio distribution (how often does ratio land near fib?):\n")
            out.write(f"  {'Range':>15} {'Count':>7} {'Freq%':>7}  {'Near Fib?':>10}\n")
            out.write(f"  "+"-"*45+"\n")
            for lo, hi, label, is_fib in [
                (0.0, 0.3, '0.0-0.3', ''),
                (0.3, 0.5, '0.3-0.5', ''),
                (0.5, 0.7, '0.5-0.7', '<-0.618'),
                (0.7, 0.9, '0.7-0.9', ''),
                (0.9, 1.1, '0.9-1.1', '<-1.000'),
                (1.1, 1.4, '1.1-1.4', '<-1.272'),
                (1.4, 1.8, '1.4-1.8', '<-1.618'),
                (1.8, 2.3, '1.8-2.3', '<-2.000'),
                (2.3, 3.0, '2.3-3.0', '<-2.618'),
                (3.0, 99, '>3.0', ''),
            ]:
                count = np.sum((ratios >= lo) & (ratios < hi))
                freq = count / len(ratios) * 100
                out.write(f"  {label:>15}  {count:>6}  {freq:>5.1f}%   {is_fib:>10}\n")

            # Exact fib proximity test
            out.write(f"\n  Exact proximity test (within 5% of fib ratio):\n")
            for fib_val, name in [(0.382,'0.382'), (0.500,'0.500'), (0.618,'0.618'),
                                   (1.000,'1.000'), (1.272,'1.272'), (1.618,'1.618'), (2.618,'2.618')]:
                near = np.sum(np.abs(ratios - fib_val) / fib_val < 0.05)
                pct = near / len(ratios) * 100
                # Expected if uniform over 0-3 range
                expected = 0.10 * fib_val / 3.0 * 100
                excess = pct / max(expected, 0.01)
                out.write(f"    {name}: {near}/{len(ratios)} ({pct:.2f}%) expected={expected:.2f}% excess={excess:.1f}x\n")
        print(f"  Section 3 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 4. FIBONACCI TIME ZONES
        # ═══════════════════════════════════════
        print("Section 4: Fibonacci Time Zones...")
        out.write(f"\n\n"+"="*110+"\n4. FIBONACCI TIME ZONES: do reversals cluster at fib minute intervals?\n"+"="*110+"\n")
        out.write(f"  From each swing point, count minutes to next reversal.\n")
        out.write(f"  Fib minutes: {FIB_TIMES}\n\n")

        time_to_reversal = []
        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            swings = find_swings(arr[:,C], arr[:,H], arr[:,L], min_swing_pct=0.3, lookback=3)
            for i in range(1, len(swings)):
                dt = swings[i][0] - swings[i-1][0]
                if 0 < dt < 60:
                    time_to_reversal.append(dt)

        if time_to_reversal:
            times = np.array(time_to_reversal)
            out.write(f"  Total swing-to-swing intervals: {len(times)}\n")
            out.write(f"  Mean: {np.mean(times):.1f} min, Median: {np.median(times):.0f} min\n\n")

            out.write(f"  {'Minutes':>8} {'Count':>7} {'Freq%':>7} {'IsFib':>6}\n")
            out.write(f"  "+"-"*35+"\n")
            for m in range(1, 40):
                count = np.sum(times == m)
                freq = count / len(times) * 100
                is_fib = " *FIB*" if m in FIB_TIMES else ""
                if freq > 0.5 or m in FIB_TIMES:
                    out.write(f"  {m:>7}m  {count:>6}  {freq:>5.2f}%{is_fib}\n")

            # Statistical test: are fib minutes over-represented?
            fib_count = np.sum(np.isin(times, FIB_TIMES))
            fib_pct = fib_count / len(times) * 100
            # Expected if uniform: 9 fib values out of 55 possible = 16.4%
            expected_pct = len(FIB_TIMES) / 55 * 100
            out.write(f"\n  Fib minutes total: {fib_count}/{len(times)} ({fib_pct:.1f}%) vs expected {expected_pct:.1f}%\n")
            out.write(f"  Enrichment: {fib_pct/expected_pct:.2f}x\n")
        print(f"  Section 4 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 5. HARMONIC ABCD PATTERN
        # ═══════════════════════════════════════
        print("Section 5: Harmonic ABCD...")
        out.write(f"\n\n"+"="*110+"\n5. HARMONIC ABCD: does AB=CD or AB*1.618=CD produce tradeable setups?\n"+"="*110+"\n")
        out.write(f"  ABCD: 4 swing points where CD leg relates to AB leg by fib ratio.\n")
        out.write(f"  Test: when CD completes at a fib extension of AB, does price reverse?\n\n")

        harmonic_results = defaultdict(lambda: {'n':0, 'win':0, 'ret':[]})

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes = arr[:,C]; highs = arr[:,H]; lows = arr[:,L]; vols = arr[:,V]; vwaps = arr[:,VW]
            swings = find_swings(closes, highs, lows, min_swing_pct=0.3, lookback=3)

            for i in range(3, len(swings)):
                A, B, Cp, D = swings[i-3], swings[i-2], swings[i-1], swings[i]
                AB = abs(B[1] - A[1])
                CD = abs(D[1] - Cp[1])
                BC = abs(Cp[1] - B[1])
                if AB < 0.01 or BC < 0.01: continue

                # Check fib ratios
                cd_ab_ratio = CD / AB
                bc_ab_ratio = BC / AB

                for ratio_name, target_ratio, tol in [
                    ('AB=CD (1.0)', 1.0, 0.10),
                    ('CD=1.27*AB', 1.272, 0.10),
                    ('CD=1.618*AB', 1.618, 0.10),
                    ('CD=0.618*AB', 0.618, 0.10),
                ]:
                    if abs(cd_ab_ratio - target_ratio) / target_ratio < tol:
                        # Pattern found! Check forward return
                        d_idx = D[0]
                        if d_idx + 10 < nb:
                            # Direction: if D is a low, expect bounce up; if D is high, expect drop
                            if D[2] == 'L':
                                fwd = (closes[min(d_idx+5, nb-1)] - D[1]) / D[1] * 100
                            else:
                                fwd = (D[1] - closes[min(d_idx+5, nb-1)]) / D[1] * 100

                            h = harmonic_results[ratio_name]
                            h['n'] += 1
                            h['ret'].append(fwd)
                            if fwd > 0: h['win'] += 1

                # Also check BC retracement of AB
                bc_retrace = BC / AB
                for retrace_name, target, tol in [
                    ('BC=38.2%AB', 0.382, 0.08),
                    ('BC=50%AB', 0.500, 0.08),
                    ('BC=61.8%AB', 0.618, 0.08),
                ]:
                    if abs(bc_retrace - target) / target < tol and abs(cd_ab_ratio - 1.0) < 0.15:
                        d_idx = D[0]
                        if d_idx + 10 < nb:
                            if D[2] == 'L':
                                fwd = (closes[min(d_idx+5, nb-1)] - D[1]) / D[1] * 100
                            else:
                                fwd = (D[1] - closes[min(d_idx+5, nb-1)]) / D[1] * 100
                            h = harmonic_results[retrace_name + '+AB=CD']
                            h['n'] += 1
                            h['ret'].append(fwd)
                            if fwd > 0: h['win'] += 1

        out.write(f"  {'Pattern':>25} {'N':>7} {'Win%':>7} {'AvgRet':>8} {'Median':>8}\n")
        out.write(f"  "+"-"*60+"\n")
        for name, h in sorted(harmonic_results.items(), key=lambda x: -np.mean(x[1]['ret']) if x[1]['ret'] else 0):
            if h['n'] < 20: continue
            wr = h['win']/h['n']*100
            ar = np.mean(h['ret'])
            mr = np.median(h['ret'])
            out.write(f"  {name:>25}  {h['n']:>6}  {wr:>5.1f}%  {ar:>+6.3f}%  {mr:>+6.3f}%\n")
        print(f"  Section 5 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 6. HURST EXPONENT
        # ═══════════════════════════════════════
        print("Section 6: Hurst Exponent...")
        out.write(f"\n\n"+"="*110+"\n6. HURST EXPONENT: trending (H>0.5) or mean-reverting (H<0.5)?\n"+"="*110+"\n")
        out.write(f"  H=0.5: random walk. H>0.5: trending. H<0.5: mean-reverting.\n\n")

        hurst_values = []
        hurst_fwd = defaultdict(list)  # bucket -> forward returns

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            closes = arr[:90, C]  # first 90 min
            valid = closes > 0
            if np.sum(valid) < 50: continue
            prices = closes[valid]
            log_ret = np.diff(np.log(prices))
            if len(log_ret) < 30: continue

            # R/S method for Hurst
            n = len(log_ret)
            max_k = min(n // 2, 40)
            if max_k < 4: continue

            RS_list = []
            ns = []
            for k in [4, 8, 16, 32]:
                if k > max_k: break
                n_chunks = n // k
                if n_chunks < 1: continue
                rs_vals = []
                for j in range(n_chunks):
                    chunk = log_ret[j*k:(j+1)*k]
                    mean_c = np.mean(chunk)
                    dev = np.cumsum(chunk - mean_c)
                    R = np.max(dev) - np.min(dev)
                    S = np.std(chunk)
                    if S > 0:
                        rs_vals.append(R / S)
                if rs_vals:
                    RS_list.append(np.mean(rs_vals))
                    ns.append(k)

            if len(ns) >= 3:
                log_ns = np.log(ns)
                log_rs = np.log(RS_list)
                # Hurst = slope of log(R/S) vs log(n)
                slope = np.polyfit(log_ns, log_rs, 1)[0]
                hurst = float(slope)
                hurst_values.append(hurst)

                # Forward 30-min return for this stock-day
                if len(arr) > 120:
                    fwd_ret = (arr[120, C] - arr[90, C]) / arr[90, C] * 100 if arr[90,C] > 0 and arr[120,C] > 0 else 0
                    # Bucket by Hurst
                    if hurst < 0.3: hurst_fwd['H<0.3'].append(fwd_ret)
                    elif hurst < 0.4: hurst_fwd['0.3-0.4'].append(fwd_ret)
                    elif hurst < 0.5: hurst_fwd['0.4-0.5'].append(fwd_ret)
                    elif hurst < 0.6: hurst_fwd['0.5-0.6'].append(fwd_ret)
                    elif hurst < 0.7: hurst_fwd['0.6-0.7'].append(fwd_ret)
                    else: hurst_fwd['H>0.7'].append(fwd_ret)

        if hurst_values:
            h_arr = np.array(hurst_values)
            out.write(f"  Hurst values computed: {len(h_arr)}\n")
            out.write(f"  Mean: {np.mean(h_arr):.3f}, Median: {np.median(h_arr):.3f}\n")
            out.write(f"  Std: {np.std(h_arr):.3f}\n")
            out.write(f"  H<0.5 (mean-revert): {np.sum(h_arr<0.5)/len(h_arr)*100:.1f}%\n")
            out.write(f"  H>0.5 (trending): {np.sum(h_arr>0.5)/len(h_arr)*100:.1f}%\n\n")

            out.write(f"  Forward return by Hurst bucket:\n")
            out.write(f"  {'Hurst':>10} {'N':>7} {'AvgAbs':>8} {'Std':>8}\n")
            out.write(f"  "+"-"*40+"\n")
            for label in ['H<0.3', '0.3-0.4', '0.4-0.5', '0.5-0.6', '0.6-0.7', 'H>0.7']:
                rets = hurst_fwd.get(label, [])
                if len(rets) < 10: continue
                out.write(f"  {label:>10}  {len(rets):>6}  {np.mean(np.abs(rets)):>6.3f}%  {np.std(rets):>6.3f}%\n")
        print(f"  Section 6 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 7. FIB RETRACEMENT TRADING STRATEGY
        # ═══════════════════════════════════════
        print("Section 7: Fib Retracement Trading...")
        out.write(f"\n\n"+"="*110+"\n7. FIB RETRACEMENT TRADING: enter at fib level, target extension\n"+"="*110+"\n")
        out.write(f"  Strategy: after impulse+retrace to 50-61.8%, enter expecting bounce.\n")
        out.write(f"  Test various SL and target combinations.\n\n")

        fib_trades = defaultdict(lambda: {'n':0, 'win':0, 'ret':[]})

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            closes = arr[:,C]; highs = arr[:,H]; lows = arr[:,L]; vols = arr[:,V]; vwaps = arr[:,VW]
            swings = find_swings(closes, highs, lows, min_swing_pct=0.4, lookback=3)

            for i in range(2, len(swings)):
                s0, s1, s2 = swings[i-2], swings[i-1], swings[i]
                impulse = s1[1] - s0[1]  # signed
                retrace = s2[1] - s1[1]  # signed (opposite)
                if abs(impulse) < 0.01: continue
                retrace_pct = abs(retrace) / abs(impulse)

                # Only trade at golden zone (50-61.8% retrace)
                for zone_name, zone_lo, zone_hi in [
                    ('38-50%', 0.38, 0.50),
                    ('50-62% (golden)', 0.50, 0.62),
                    ('62-79%', 0.62, 0.79),
                ]:
                    if not (zone_lo <= retrace_pct < zone_hi): continue

                    entry_idx = s2[0]
                    entry_price = s2[1]
                    if entry_idx + 20 >= nb: continue

                    # Volume check at entry
                    avg_vol = np.mean(vols[max(entry_idx-20,0):entry_idx]) if entry_idx > 20 else 1
                    vol_spike = vols[entry_idx] / max(avg_vol, 1) > 2

                    # Direction of expected bounce
                    direction = 1 if impulse > 0 else -1  # bounce back toward impulse direction

                    # Simulate with trailing stop
                    sl_pct = 0.3
                    sl = entry_price * (1 - direction * sl_pct / 100)
                    best = entry_price
                    exit_pnl = None

                    for j in range(entry_idx + 1, min(entry_idx + 20, nb)):
                        h_j = highs[j]; l_j = lows[j]
                        if direction == 1:
                            if l_j <= sl:
                                exit_pnl = (sl - entry_price) / entry_price * 100
                                break
                            if h_j > best:
                                best = h_j
                                if (best - entry_price) / entry_price * 100 > 0.2:
                                    sl = max(sl, entry_price)  # breakeven
                                if (best - entry_price) / entry_price * 100 > 0.5:
                                    sl = max(sl, best * (1 - 0.2/100))  # trail
                        else:
                            if h_j >= sl:
                                exit_pnl = (entry_price - sl) / entry_price * 100
                                break
                            if l_j < best:
                                best = l_j
                                if (entry_price - best) / entry_price * 100 > 0.2:
                                    sl = min(sl, entry_price)
                                if (entry_price - best) / entry_price * 100 > 0.5:
                                    sl = min(sl, best * (1 + 0.2/100))

                    if exit_pnl is None:
                        # Time exit
                        exit_p = closes[min(entry_idx + 20, nb-1)]
                        exit_pnl = direction * (exit_p - entry_price) / entry_price * 100

                    net = exit_pnl - COST
                    fib_trades[zone_name]['n'] += 1
                    fib_trades[zone_name]['ret'].append(net)
                    if net > 0: fib_trades[zone_name]['win'] += 1

                    if vol_spike:
                        fib_trades[zone_name + '+vol']['n'] += 1
                        fib_trades[zone_name + '+vol']['ret'].append(net)
                        if net > 0: fib_trades[zone_name + '+vol']['win'] += 1

                    # VWAP confirm
                    if vwaps[entry_idx] > 0:
                        vwap_aligned = (direction == 1 and entry_price > vwaps[entry_idx]) or \
                                       (direction == -1 and entry_price < vwaps[entry_idx])
                        if vwap_aligned:
                            fib_trades[zone_name + '+vwap']['n'] += 1
                            fib_trades[zone_name + '+vwap']['ret'].append(net)
                            if net > 0: fib_trades[zone_name + '+vwap']['win'] += 1

                        if vol_spike and vwap_aligned:
                            fib_trades[zone_name + '+vol+vwap']['n'] += 1
                            fib_trades[zone_name + '+vol+vwap']['ret'].append(net)
                            if net > 0: fib_trades[zone_name + '+vol+vwap']['win'] += 1

        out.write(f"  {'Strategy':>30} {'N':>7} {'Win%':>7} {'AvgRet':>8} {'PF':>6}\n")
        out.write(f"  "+"-"*65+"\n")
        for name, h in sorted(fib_trades.items(), key=lambda x: -np.mean(x[1]['ret']) if x[1]['ret'] else -99):
            if h['n'] < 30: continue
            wr = h['win']/h['n']*100
            ar = np.mean(h['ret'])
            wins = [r for r in h['ret'] if r > 0]
            losses = [r for r in h['ret'] if r <= 0]
            pf = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else 0
            out.write(f"  {name:>30}  {h['n']:>6}  {wr:>5.1f}%  {ar:>+6.3f}%  {pf:>5.2f}\n")
        print(f"  Section 7 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 8. GOLDEN RATIO IN CANDLE BODIES
        # ═══════════════════════════════════════
        print("Section 8: Golden Ratio Candles...")
        out.write(f"\n\n"+"="*110+"\n8. GOLDEN RATIO IN CANDLE STRUCTURE\n"+"="*110+"\n")
        out.write(f"  Test: candles where body/range = 0.618 or wick/body = 0.618\n\n")

        golden_candle = defaultdict(lambda: {'n':0, 'win':0, 'ret':[]})
        total_candles = 0

        for idx in sample_idx:
            sym, date, arr = all_data[idx]
            nb = len(arr)
            opens = arr[:,O]; closes = arr[:,C]; highs = arr[:,H]; lows = arr[:,L]

            for i in range(25, min(nb-10, 120)):
                rng = highs[i] - lows[i]
                if rng < 0.01: continue
                body = abs(closes[i] - opens[i])
                body_ratio = body / rng
                total_candles += 1

                # Forward 5-bar return
                fwd = (closes[min(i+5,nb-1)] - closes[i]) / closes[i] * 100 if closes[i] > 0 else 0
                direction = 1 if closes[i] > opens[i] else -1
                fwd_dir = fwd * direction  # positive = continuation

                # Golden body ratio (body = 61.8% of range)
                if abs(body_ratio - 0.618) < 0.05:
                    golden_candle['body=0.618']['n'] += 1
                    golden_candle['body=0.618']['ret'].append(fwd_dir)
                    if fwd_dir > 0: golden_candle['body=0.618']['win'] += 1

                # Small body (body = 38.2% of range = indecision)
                if abs(body_ratio - 0.382) < 0.05:
                    golden_candle['body=0.382 (doji-ish)']['n'] += 1
                    golden_candle['body=0.382 (doji-ish)']['ret'].append(abs(fwd))
                    if abs(fwd) > 0.2: golden_candle['body=0.382 (doji-ish)']['win'] += 1

                # Big body (body > 78.6% of range = strong)
                if body_ratio > 0.786:
                    golden_candle['body>0.786 (marubozu)']['n'] += 1
                    golden_candle['body>0.786 (marubozu)']['ret'].append(fwd_dir)
                    if fwd_dir > 0: golden_candle['body>0.786 (marubozu)']['win'] += 1

                # Upper wick = 61.8% of range (rejection)
                upper_wick = highs[i] - max(opens[i], closes[i])
                if rng > 0 and abs(upper_wick/rng - 0.618) < 0.05:
                    golden_candle['upper_wick=0.618']['n'] += 1
                    golden_candle['upper_wick=0.618']['ret'].append(-fwd)  # expect reversal down
                    if fwd < 0: golden_candle['upper_wick=0.618']['win'] += 1

        out.write(f"  Total candles analyzed: {total_candles}\n\n")
        out.write(f"  {'Pattern':>30} {'N':>7} {'Win%':>7} {'AvgRet':>8}\n")
        out.write(f"  "+"-"*55+"\n")
        for name, h in sorted(golden_candle.items(), key=lambda x: -np.mean(x[1]['ret']) if x[1]['ret'] else -99):
            if h['n'] < 50: continue
            wr = h['win']/h['n']*100
            ar = np.mean(h['ret'])
            out.write(f"  {name:>30}  {h['n']:>6}  {wr:>5.1f}%  {ar:>+6.3f}%\n")
        print(f"  Section 8 done {time.time()-t0:.0f}s")

        # ═══════════════════════════════════════
        # 9. SUMMARY
        # ═══════════════════════════════════════
        out.write(f"\n\n"+"="*110+"\n9. SUMMARY: which universal patterns have real edge?\n"+"="*110+"\n")
        out.write(f"""
  FINDINGS:

  1. FIBONACCI RETRACEMENTS: Check section 1 for clustering evidence.
     If bounces cluster at 38.2/50/61.8% more than random, fib levels are real.

  2. FIBONACCI EXTENSIONS: Check section 2 for hit rates.
     If moves consistently reach 1.272/1.618x of prior swing, extensions work.

  3. GOLDEN RATIO SWINGS: Check section 3 for ratio distribution.
     If swing ratios cluster near 0.618/1.618, golden ratio governs price.

  4. FIB TIME ZONES: Check section 4 for temporal clustering.
     If reversals happen more often at 3/5/8/13/21 min intervals, time is fractal.

  5. HARMONIC ABCD: Check section 5 for positive expectancy.
     If AB=CD or AB*1.618=CD predicts reversals, harmonics work.

  6. HURST EXPONENT: Check section 6 for regime detection.
     If H<0.5 stocks mean-revert and H>0.5 stocks trend, Hurst is predictive.

  7. FIB LEVEL TRADING: Check section 7 for profit factor.
     If entering at 50-61.8% retrace with volume produces PF>1, it's tradeable.

  8. GOLDEN CANDLES: Check section 8 for body/range ratio edge.
     If 0.618 body ratio predicts continuation, golden candles work.
""")

    print(f"\nCompleted in {time.time()-t0:.1f}s. Output: {OUT}")

if __name__ == '__main__':
    main()
