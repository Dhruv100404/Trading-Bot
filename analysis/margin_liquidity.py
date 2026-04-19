"""
Margin Stocks Liquidity Analysis
=================================
Cross-references data/margin-stocks.json (1521 stocks, 4-10x margin)
with candle data to find which ones have good liquidity.

Output: ranked list by avg f5Vol × price, with gap-reversal performance.
"""

import json
import numpy as np
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE = DATA_DIR / 'margin_liquidity_report.txt'

O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6
B_ENTRY = 6
B_EXIT_66 = 65
COST = 0.15

def main():
    t0 = time.time()

    # Load margin stocks
    with open(DATA_DIR / 'margin-stocks.json') as f:
        mdata = json.load(f)
    margin_syms = {s['tradingSymbol']: s for s in mdata['stocks']}
    print(f"Margin stocks: {len(margin_syms)}")

    # Load candle data, keep only margin stocks
    files = [DATA_DIR / 'candles-consolidated.ndjson',
             DATA_DIR / 'candles-consolidated_new.ndjson']

    stock_data = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                sym = rec['symbol']
                if sym not in margin_syms:
                    continue
                bkts = rec['buckets']
                nb = min(len(bkts), 100)
                bkt = np.zeros((100, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt[j, O] = b['o']; bkt[j, H] = b['h']
                    bkt[j, L] = b['l']; bkt[j, C] = b['c']
                    bkt[j, V] = b['v']; bkt[j, VW] = b.get('vw', b['c'])
                    bkt[j, BR] = b.get('br', 0.5)

                stock_data[sym].append({
                    'date': rec['date'], 'gap': rec['gapPct'],
                    'f5vol': rec.get('f5Vol', 0), 'day_open': rec['dayOpen'],
                    'bkt': bkt,
                })

    print(f"Loaded {sum(len(v) for v in stock_data.values())} records "
          f"for {len(stock_data)} margin stocks in {time.time()-t0:.1f}s")

    # Stocks in margin list but NOT in candle data
    missing = set(margin_syms.keys()) - set(stock_data.keys())

    results = []
    for sym, days in stock_data.items():
        n_days = len(days)
        f5vol_rs_list = []
        sell_rets = []
        buy_rets = []
        gap_ups = 0
        gap_downs = 0

        for d in days:
            entry = d['bkt'][B_ENTRY, O]
            if entry <= 0:
                continue
            f5vol_rs = d['f5vol'] * d['day_open']
            f5vol_rs_list.append(f5vol_rs)

            if d['gap'] > 0.1:
                gap_ups += 1
                exit_c = d['bkt'][B_EXIT_66, C]
                if exit_c > 0:
                    sell_rets.append((entry - exit_c) / entry * 100 - COST)

            if d['gap'] < -0.1:
                gap_downs += 1
                exit_c = d['bkt'][B_EXIT_66, C]
                if exit_c > 0:
                    buy_rets.append((exit_c - entry) / entry * 100 - COST)

        if n_days < 5:
            continue

        avg_vol = np.mean(f5vol_rs_list) if f5vol_rs_list else 0
        med_vol = np.median(f5vol_rs_list) if f5vol_rs_list else 0
        sell_n = len(sell_rets)
        sell_win = sum(1 for r in sell_rets if r > 0) / sell_n * 100 if sell_n else 0
        sell_avg = np.mean(sell_rets) if sell_rets else 0
        buy_n = len(buy_rets)
        buy_win = sum(1 for r in buy_rets if r > 0) / buy_n * 100 if buy_n else 0
        buy_avg = np.mean(buy_rets) if buy_rets else 0

        lev = margin_syms[sym]['leverage']
        price = margin_syms[sym]['price']

        results.append({
            'sym': sym, 'n_days': n_days, 'lev': lev, 'price': price,
            'avg_vol': avg_vol, 'med_vol': med_vol,
            'gap_ups': gap_ups, 'gap_downs': gap_downs,
            'sell_n': sell_n, 'sell_win': sell_win, 'sell_avg': sell_avg,
            'buy_n': buy_n, 'buy_win': buy_win, 'buy_avg': buy_avg,
        })

    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write(f"MARGIN STOCKS LIQUIDITY REPORT\n")
        out.write(f"Margin stocks: {len(margin_syms)} | Found in candle data: {len(stock_data)} | Missing: {len(missing)}\n")
        out.write(f"Entry: b7 open (9:21 AM), Exit: b66 close (10:20 AM), Cost: {COST}%\n\n")

        # Volume distribution
        out.write("="*100 + "\n")
        out.write("LIQUIDITY DISTRIBUTION (avg f5Vol x price)\n")
        out.write("="*100 + "\n")
        vol_vals = [r['avg_vol'] for r in results]
        for name, thresh in [("100L+ (ultra)", 10_000_000), ("50L+", 5_000_000),
                              ("20L+", 2_000_000), ("10L+", 1_000_000),
                              ("5L+", 500_000), ("2L+", 200_000),
                              ("1L+", 100_000), ("<1L", 0)]:
            if thresh > 0:
                cnt = sum(1 for v in vol_vals if v >= thresh)
            else:
                cnt = sum(1 for v in vol_vals if v < 100_000)
            out.write(f"  {name:>15}: {cnt:>4} stocks\n")

        # GOOD LIQUID: vol >= 5L, sell_n >= 5, sell_win > 50%
        out.write("\n" + "="*100 + "\n")
        out.write("SELL-READY: Liquid margin stocks (vol >= 5L, sell >= 5 trades, win > 50%)\n")
        out.write("Sorted by avg sell return\n")
        out.write("="*100 + "\n")

        good = [r for r in results if r['avg_vol'] >= 500_000 and r['sell_n'] >= 5 and r['sell_win'] > 50]
        good.sort(key=lambda x: -x['sell_avg'])

        hdr = f"  {'Symbol':<15} {'Lev':>4} {'Price':>8} {'AvgVol':>10} {'Days':>4} {'GUp':>4} {'SN':>3} {'SWin%':>6} {'SRet':>8}\n"
        out.write(hdr)
        out.write("  " + "-"*75 + "\n")
        for r in good:
            vol_s = f"{r['avg_vol']/100_000:.1f}L"
            out.write(f"  {r['sym']:<15} {r['lev']:>3}x {r['price']:>8.1f} {vol_s:>10} "
                      f"{r['n_days']:>4} {r['gap_ups']:>4} {r['sell_n']:>3} "
                      f"{r['sell_win']:>5.1f}% {r['sell_avg']:>+7.3f}%\n")
        out.write(f"\n  Total: {len(good)} stocks\n")

        # BUY-READY
        out.write("\n" + "="*100 + "\n")
        out.write("BUY-READY: Liquid margin stocks (vol >= 5L, buy >= 5 trades, win > 50%)\n")
        out.write("="*100 + "\n")

        good_buy = [r for r in results if r['avg_vol'] >= 500_000 and r['buy_n'] >= 5 and r['buy_win'] > 50]
        good_buy.sort(key=lambda x: -x['buy_avg'])

        hdr_b = f"  {'Symbol':<15} {'Lev':>4} {'Price':>8} {'AvgVol':>10} {'Days':>4} {'GDn':>4} {'BN':>3} {'BWin%':>6} {'BRet':>8}\n"
        out.write(hdr_b)
        out.write("  " + "-"*75 + "\n")
        for r in good_buy:
            vol_s = f"{r['avg_vol']/100_000:.1f}L"
            out.write(f"  {r['sym']:<15} {r['lev']:>3}x {r['price']:>8.1f} {vol_s:>10} "
                      f"{r['n_days']:>4} {r['gap_downs']:>4} {r['buy_n']:>3} "
                      f"{r['buy_win']:>5.1f}% {r['buy_avg']:>+7.3f}%\n")
        out.write(f"\n  Total: {len(good_buy)} stocks\n")

        # TOP 50 by volume (regardless of performance)
        out.write("\n" + "="*100 + "\n")
        out.write("TOP 50 MARGIN STOCKS BY LIQUIDITY\n")
        out.write("="*100 + "\n")
        by_vol = sorted(results, key=lambda x: -x['avg_vol'])[:50]
        hdr_all = (f"  {'#':>3} {'Symbol':<15} {'Lev':>4} {'Price':>8} {'AvgVol':>10} {'MedVol':>10} "
                   f"{'GUp':>4} {'SN':>3} {'SWin':>5} {'SRet':>7} "
                   f"{'GDn':>4} {'BN':>3} {'BWin':>5} {'BRet':>7}\n")
        out.write(hdr_all)
        out.write("  " + "-"*100 + "\n")
        for i, r in enumerate(by_vol):
            vol_a = f"{r['avg_vol']/100_000:.0f}L"
            vol_m = f"{r['med_vol']/100_000:.0f}L"
            sw = f"{r['sell_win']:.0f}%" if r['sell_n'] else "-"
            sr = f"{r['sell_avg']:+.2f}%" if r['sell_n'] else "-"
            bw = f"{r['buy_win']:.0f}%" if r['buy_n'] else "-"
            br_ = f"{r['buy_avg']:+.2f}%" if r['buy_n'] else "-"
            out.write(f"  {i+1:>3} {r['sym']:<15} {r['lev']:>3}x {r['price']:>8.1f} {vol_a:>10} {vol_m:>10} "
                      f"{r['gap_ups']:>4} {r['sell_n']:>3} {sw:>5} {sr:>7} "
                      f"{r['gap_downs']:>4} {r['buy_n']:>3} {bw:>5} {br_:>7}\n")

        # FINAL RECOMMENDED: vol >= 10L + sell_win > 55% + sell_n >= 10
        out.write("\n" + "="*100 + "\n")
        out.write("RECOMMENDED SHORTLIST: vol >= 10L, sell trades >= 10, sell win > 55%\n")
        out.write("These are your BEST margin stocks for gap-reversal SELL\n")
        out.write("="*100 + "\n")

        elite = [r for r in results if r['avg_vol'] >= 1_000_000 and r['sell_n'] >= 10 and r['sell_win'] > 55]
        elite.sort(key=lambda x: -x['sell_avg'])

        out.write(hdr)
        out.write("  " + "-"*75 + "\n")
        for r in elite:
            vol_s = f"{r['avg_vol']/100_000:.0f}L"
            out.write(f"  {r['sym']:<15} {r['lev']:>3}x {r['price']:>8.1f} {vol_s:>10} "
                      f"{r['n_days']:>4} {r['gap_ups']:>4} {r['sell_n']:>3} "
                      f"{r['sell_win']:>5.1f}% {r['sell_avg']:>+7.3f}%\n")
        out.write(f"\n  Total: {len(elite)} stocks\n")

    print(f"Done in {time.time()-t0:.1f}s -> {OUT_FILE}")


if __name__ == '__main__':
    main()
