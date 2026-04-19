"""
Tier1 Liquidity & Performance Analysis
=======================================
For each tier1 stock, compute:
  1. Average f5Vol × dayOpen (₹ liquidity in first 5 mins)
  2. Number of gap-up days (gap>0.1%) and gap-down days
  3. Sell reversal win rate (gap>0.1%, entry b7 open, exit b66 close)
  4. Buy reversal win rate (gap<-0.1%, same timing)
  5. Average return per trade

Outputs ranked list: liquid + high win-rate stocks for the current strategy.
NO lookahead.
"""

import json
import numpy as np
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE = DATA_DIR / 'tier1_liquidity_report.txt'

O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6
B_ENTRY = 6   # 9:21 AM open
B_EXIT_66 = 65
B_EXIT_90 = 89
COST = 0.15

def main():
    t0 = time.time()

    # Load tier1 list
    with open(DATA_DIR / 'recommended-watchlist.json') as f:
        wl = json.load(f)
    tier1 = set(wl['tier1'])
    print(f"Tier1: {len(tier1)} stocks")

    # Load candle data, keep only tier1
    files = [DATA_DIR / 'candles-consolidated.ndjson',
             DATA_DIR / 'candles-consolidated_new.ndjson']

    stock_data = defaultdict(list)  # symbol -> list of day records

    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                sym = rec['symbol']
                if sym not in tier1:
                    continue
                gap     = rec['gapPct']
                f5vol   = rec.get('f5Vol', 0)
                day_opn = rec['dayOpen']
                bkts    = rec['buckets']
                nb      = min(len(bkts), 100)

                bkt = np.zeros((100, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j, O]  = b['o']
                    bkt[j, H]  = b['h']
                    bkt[j, L]  = b['l']
                    bkt[j, C]  = b['c']
                    bkt[j, V]  = b['v']
                    bkt[j, VW] = b.get('vw', b['c'])
                    bkt[j, BR] = b.get('br', 0.5)

                stock_data[sym].append({
                    'date': rec['date'], 'gap': gap,
                    'f5vol': f5vol, 'day_open': day_opn, 'bkt': bkt,
                })

    print(f"Loaded {sum(len(v) for v in stock_data.values())} tier1 records "
          f"for {len(stock_data)} symbols in {time.time()-t0:.1f}s")

    # Analyze each stock
    results = []
    for sym, days in stock_data.items():
        n_days = len(days)
        f5vol_rs_list = []
        sell_rets = []
        buy_rets = []
        sell_mfes = []
        buy_mfes = []
        gap_ups = 0
        gap_downs = 0

        for d in days:
            entry = d['bkt'][B_ENTRY, O]
            if entry <= 0:
                continue

            f5vol_rs = d['f5vol'] * d['day_open']
            f5vol_rs_list.append(f5vol_rs)

            # SELL analysis (gap up)
            if d['gap'] > 0.1:
                gap_ups += 1
                exit_c = d['bkt'][B_EXIT_66, C]
                if exit_c > 0:
                    ret = (entry - exit_c) / entry * 100 - COST
                    sell_rets.append(ret)
                    # MFE
                    min_l = np.min(d['bkt'][B_ENTRY:B_EXIT_66+1, L])
                    mfe = (entry - min_l) / entry * 100
                    sell_mfes.append(mfe)

            # BUY analysis (gap down)
            if d['gap'] < -0.1:
                gap_downs += 1
                exit_c = d['bkt'][B_EXIT_66, C]
                if exit_c > 0:
                    ret = (exit_c - entry) / entry * 100 - COST
                    buy_rets.append(ret)
                    max_h = np.max(d['bkt'][B_ENTRY:B_EXIT_66+1, H])
                    mfe = (max_h - entry) / entry * 100
                    buy_mfes.append(mfe)

        if n_days < 10:
            continue

        avg_f5vol_rs = np.mean(f5vol_rs_list) if f5vol_rs_list else 0
        med_f5vol_rs = np.median(f5vol_rs_list) if f5vol_rs_list else 0

        # Sell stats
        sell_n = len(sell_rets)
        sell_win = sum(1 for r in sell_rets if r > 0) / sell_n * 100 if sell_n > 0 else 0
        sell_avg = np.mean(sell_rets) if sell_rets else 0
        sell_avg_mfe = np.mean(sell_mfes) if sell_mfes else 0

        # Buy stats
        buy_n = len(buy_rets)
        buy_win = sum(1 for r in buy_rets if r > 0) / buy_n * 100 if buy_n > 0 else 0
        buy_avg = np.mean(buy_rets) if buy_rets else 0
        buy_avg_mfe = np.mean(buy_mfes) if buy_mfes else 0

        results.append({
            'sym': sym,
            'n_days': n_days,
            'avg_f5vol_rs': avg_f5vol_rs,
            'med_f5vol_rs': med_f5vol_rs,
            'gap_ups': gap_ups,
            'gap_downs': gap_downs,
            'sell_n': sell_n,
            'sell_win': sell_win,
            'sell_avg': sell_avg,
            'sell_avg_mfe': sell_avg_mfe,
            'buy_n': buy_n,
            'buy_win': buy_win,
            'buy_avg': buy_avg,
            'buy_avg_mfe': buy_avg_mfe,
        })

    # Write report
    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write(f"TIER1 LIQUIDITY & GAP-REVERSAL REPORT\n")
        out.write(f"Data: {sum(len(v) for v in stock_data.values())} records, {len(stock_data)} tier1 stocks\n")
        out.write(f"Entry: bucket 7 open (9:21 AM), Exit: bucket 66 close (10:20 AM)\n")
        out.write(f"Cost: {COST}% per trade\n\n")

        # ── Volume tiers ──
        out.write("="*100 + "\n")
        out.write("LIQUIDITY DISTRIBUTION (avg f5Vol × price, ₹)\n")
        out.write("="*100 + "\n")
        vol_vals = [r['avg_f5vol_rs'] for r in results]
        for thresh_name, thresh in [("₹50L+", 5_000_000), ("₹20L+", 2_000_000),
                                     ("₹10L+", 1_000_000), ("₹5L+", 500_000),
                                     ("₹2L+", 200_000), ("₹1L+", 100_000),
                                     ("<₹1L", 0)]:
            if thresh > 0:
                cnt = sum(1 for v in vol_vals if v >= thresh)
            else:
                cnt = sum(1 for v in vol_vals if v < 100_000)
            out.write(f"  {thresh_name:>8}: {cnt:>4} stocks\n")

        # ── SELL: Top stocks by liquidity + performance ──
        # Filter: avg_f5vol >= 2L, sell_n >= 5, sell_win > 50%
        out.write("\n" + "="*100 + "\n")
        out.write("SELL GAP-REVERSAL: BEST TIER1 STOCKS (liquid + high win rate)\n")
        out.write("Filter: avg_f5vol ≥ ₹2L, sell trades ≥ 5, sell win > 50%\n")
        out.write("="*100 + "\n")

        good_sell = [r for r in results
                     if r['avg_f5vol_rs'] >= 200_000 and r['sell_n'] >= 5 and r['sell_win'] > 50]
        good_sell.sort(key=lambda x: -x['sell_avg'])

        hdr = (f"  {'Symbol':<15} {'AvgVol₹':>10} {'Days':>4} {'GapUp':>5} "
               f"{'SellN':>5} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8}\n")
        out.write(hdr)
        out.write("  " + "-"*70 + "\n")
        for r in good_sell:
            vol_str = f"₹{r['avg_f5vol_rs']/100_000:.1f}L"
            out.write(f"  {r['sym']:<15} {vol_str:>10} {r['n_days']:>4} {r['gap_ups']:>5} "
                      f"{r['sell_n']:>5} {r['sell_win']:>5.1f}% {r['sell_avg']:>+7.3f}% "
                      f"{r['sell_avg_mfe']:>+7.3f}%\n")

        out.write(f"\n  Total: {len(good_sell)} stocks pass filter\n")

        # ── Same but with higher volume (5L+) ──
        out.write("\n" + "="*100 + "\n")
        out.write("SELL: HIGH LIQUIDITY TIER (avg_f5vol ≥ ₹5L, sell ≥ 5, win > 50%)\n")
        out.write("="*100 + "\n")

        hi_sell = [r for r in results
                   if r['avg_f5vol_rs'] >= 500_000 and r['sell_n'] >= 5 and r['sell_win'] > 50]
        hi_sell.sort(key=lambda x: -x['sell_avg'])

        out.write(hdr)
        out.write("  " + "-"*70 + "\n")
        for r in hi_sell:
            vol_str = f"₹{r['avg_f5vol_rs']/100_000:.1f}L"
            out.write(f"  {r['sym']:<15} {vol_str:>10} {r['n_days']:>4} {r['gap_ups']:>5} "
                      f"{r['sell_n']:>5} {r['sell_win']:>5.1f}% {r['sell_avg']:>+7.3f}% "
                      f"{r['sell_avg_mfe']:>+7.3f}%\n")
        out.write(f"\n  Total: {len(hi_sell)} stocks pass filter\n")

        # ── BUY: Top stocks ──
        out.write("\n" + "="*100 + "\n")
        out.write("BUY GAP-REVERSAL: BEST TIER1 STOCKS (liquid + high win rate)\n")
        out.write("Filter: avg_f5vol ≥ ₹2L, buy trades ≥ 5, buy win > 50%\n")
        out.write("="*100 + "\n")

        good_buy = [r for r in results
                    if r['avg_f5vol_rs'] >= 200_000 and r['buy_n'] >= 5 and r['buy_win'] > 50]
        good_buy.sort(key=lambda x: -x['buy_avg'])

        hdr_b = (f"  {'Symbol':<15} {'AvgVol₹':>10} {'Days':>4} {'GapDn':>5} "
                 f"{'BuyN':>5} {'Win%':>6} {'AvgRet':>8} {'AvgMFE':>8}\n")
        out.write(hdr_b)
        out.write("  " + "-"*70 + "\n")
        for r in good_buy:
            vol_str = f"₹{r['avg_f5vol_rs']/100_000:.1f}L"
            out.write(f"  {r['sym']:<15} {vol_str:>10} {r['n_days']:>4} {r['gap_downs']:>5} "
                      f"{r['buy_n']:>5} {r['buy_win']:>5.1f}% {r['buy_avg']:>+7.3f}% "
                      f"{r['buy_avg_mfe']:>+7.3f}%\n")
        out.write(f"\n  Total: {len(good_buy)} stocks pass filter\n")

        # ── ALL tier1 sorted by volume (full list) ──
        out.write("\n" + "="*100 + "\n")
        out.write("ALL TIER1 STOCKS SORTED BY LIQUIDITY (avg f5Vol × price)\n")
        out.write("="*100 + "\n")
        results.sort(key=lambda x: -x['avg_f5vol_rs'])

        hdr_all = (f"  {'#':>3} {'Symbol':<15} {'AvgVol₹':>10} {'MedVol₹':>10} {'Days':>4} "
                   f"{'GUp':>4} {'SN':>3} {'SWin':>5} {'SRet':>7} "
                   f"{'GDn':>4} {'BN':>3} {'BWin':>5} {'BRet':>7}\n")
        out.write(hdr_all)
        out.write("  " + "-"*95 + "\n")
        for i, r in enumerate(results):
            vol_a = f"₹{r['avg_f5vol_rs']/100_000:.1f}L"
            vol_m = f"₹{r['med_f5vol_rs']/100_000:.1f}L"
            s_w = f"{r['sell_win']:.0f}%" if r['sell_n'] > 0 else "—"
            s_r = f"{r['sell_avg']:+.2f}%" if r['sell_n'] > 0 else "—"
            b_w = f"{r['buy_win']:.0f}%" if r['buy_n'] > 0 else "—"
            b_r = f"{r['buy_avg']:+.2f}%" if r['buy_n'] > 0 else "—"
            out.write(f"  {i+1:>3} {r['sym']:<15} {vol_a:>10} {vol_m:>10} {r['n_days']:>4} "
                      f"{r['gap_ups']:>4} {r['sell_n']:>3} {s_w:>5} {s_r:>7} "
                      f"{r['gap_downs']:>4} {r['buy_n']:>3} {b_w:>5} {b_r:>7}\n")

        out.write(f"\nDone in {time.time()-t0:.1f}s\n")

    print(f"Done in {time.time()-t0:.1f}s -> {OUT_FILE}")


if __name__ == '__main__':
    main()
