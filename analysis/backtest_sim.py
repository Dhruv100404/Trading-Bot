"""
Full Strategy Backtest Simulation
==================================
Simulates day-by-day cherry-pick trading with 5x intraday margin.

STRICT NO-LOOKAHEAD:
  Signal formed at end of bucket 6 (9:20 AM, 0-indexed)
  Entry price = bucket 6 open (0-indexed) = 9:21 AM open
  Features use ONLY buckets 0..5 (9:15-9:20 AM)

MARGIN:
  5x intraday margin — 10k capital controls 50k position
  P&L = position_size * price_move
  ROC = P&L / capital (not position)

STRATEGIES TESTED:
  SELL A: Current (gap>0.1%, exit b66)
  SELL B: gap>=1.5% + tight_OR3 (<0.3%), exit b90
  SELL C: gap>=1.5% + avg_br6<0.40, exit b90
  SELL D: gap>=2% + b0_br<0.30, exit b90
  SELL E: gap>=1.5% + tight_OR3 + avg_br6<0.40, exit b90  [BEST COMBO]
  BUY  F: gap<=-2% + avg_br6>=0.55, exit b45
  COMBINED: SELL E + BUY F (run both on same day, different stocks)
"""

import numpy as np
import json
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE  = DATA_DIR / 'backtest_report.txt'

# ── Constants ──
MARGIN_MULT   = 5       # 5x intraday margin
MAX_POSITIONS = 8       # cherry-pick top N
CAPITAL_PER   = 10_000  # per-trade capital (margin)
POSITION_SIZE = CAPITAL_PER * MARGIN_MULT   # = 50,000
TOTAL_CAPITAL = CAPITAL_PER * MAX_POSITIONS # = 80,000 total margin

CIRCUIT_BKR_PCT = 6.0   # force-close if daily loss > 6% of total capital
COST_PCT        = 0.15  # round-trip cost % (brokerage + slippage + STT)

# Bucket indices (0-indexed, b=1 in data is index 0)
B_ENTRY   = 6   # entry at open of this bucket (9:21 AM)
B_EXIT_45 = 44  # 9:59 AM
B_EXIT_66 = 65  # 10:20 AM
B_EXIT_90 = 89  # 10:44 AM

O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6

# ── Strategies definition ──
STRATEGIES = {
    'A_current':    dict(side='sell', gap_min=0.10,  gap_max=100,  tight_or3=None, avg_br_max=None, b0_br_max=None, exit_b=B_EXIT_66, label='SELL gap>0.10% [CURRENT]'),
    'B_sell_tight': dict(side='sell', gap_min=1.50,  gap_max=100,  tight_or3=0.30, avg_br_max=None, b0_br_max=None, exit_b=B_EXIT_90, label='SELL gap>1.5% + tight_OR3<0.3%'),
    'C_sell_br':    dict(side='sell', gap_min=1.50,  gap_max=100,  tight_or3=None, avg_br_max=0.40, b0_br_max=None, exit_b=B_EXIT_90, label='SELL gap>1.5% + avg_br<0.40'),
    'D_sell_b0br':  dict(side='sell', gap_min=2.00,  gap_max=100,  tight_or3=None, avg_br_max=None, b0_br_max=0.30, exit_b=B_EXIT_90, label='SELL gap>2% + b0_br<0.30'),
    'E_sell_combo': dict(side='sell', gap_min=1.50,  gap_max=100,  tight_or3=0.30, avg_br_max=0.40, b0_br_max=None, exit_b=B_EXIT_90, label='SELL gap>1.5% + tight_OR3 + avg_br<0.40'),
    'F_buy':        dict(side='buy',  gap_min=-100,  gap_max=-2.0, tight_or3=None, avg_br_min=0.55, b0_br_max=None, exit_b=B_EXIT_45, label='BUY  gap<-2% + avg_br>0.55'),
}


# ─────────────────────────────────────────────
# 1. LOAD DATA
# ─────────────────────────────────────────────
def load_data():
    t0 = time.time()
    files = [DATA_DIR / 'candles-consolidated.ndjson',
             DATA_DIR / 'candles-consolidated_new.ndjson']

    # Group by date first (one day = many stocks)
    by_date = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                date = rec['date']
                gap  = rec['gapPct']
                sym  = rec['symbol']
                bkts = rec['buckets']
                nb   = min(len(bkts), 100)

                # Pack into compact arrays
                bkt_arr = np.zeros((100, 7), dtype=np.float32)
                for j in range(nb):
                    b = bkts[j]
                    bkt_arr[j, O]  = b['o']
                    bkt_arr[j, H]  = b['h']
                    bkt_arr[j, L]  = b['l']
                    bkt_arr[j, C]  = b['c']
                    bkt_arr[j, V]  = b['v']
                    bkt_arr[j, VW] = b.get('vw', b['c'])
                    bkt_arr[j, BR] = b.get('br', 0.5)

                by_date[date].append({'sym': sym, 'gap': gap, 'bkt': bkt_arr})

    print(f"Loaded {sum(len(v) for v in by_date.values())} records "
          f"across {len(by_date)} days in {time.time()-t0:.1f}s")
    return by_date


# ─────────────────────────────────────────────
# 2. COMPUTE FEATURES (no lookahead)
# ─────────────────────────────────────────────
def compute_stock_features(bkt):
    """Given 100×7 bucket array, return scalar features using ONLY b0..b5."""
    entry_price = bkt[B_ENTRY, O]
    if entry_price <= 0:
        return None

    # OR3 range from first 3 candles
    or3_h = max(bkt[0, H], bkt[1, H], bkt[2, H])
    or3_l = min(bkt[0, L], bkt[1, L], bkt[2, L])
    or3_rng = (or3_h - or3_l) / or3_h * 100 if or3_h > 0 else 0.0

    # Average buy ratio across first 6 buckets
    avg_br6 = float(np.mean(bkt[:6, BR]))

    # First candle buy ratio
    b0_br = float(bkt[0, BR])

    return {
        'entry_price': entry_price,
        'or3_rng':     or3_rng,
        'avg_br6':     avg_br6,
        'b0_br':       b0_br,
    }


# ─────────────────────────────────────────────
# 3. COMPUTE TRADE RESULT
# ─────────────────────────────────────────────
def trade_result(bkt, entry_price, exit_b, side):
    """
    Returns (exit_pct, mfe_pct, mae_pct) — all as % of entry price.
    entry_price = bkt[B_ENTRY, O]
    exit_price  = bkt[exit_b, C]
    mfe/mae measured over buckets B_ENTRY .. exit_b
    """
    if exit_b >= len(bkt) or bkt[exit_b, C] <= 0:
        exit_b = 89  # fallback

    exit_price = bkt[exit_b, C]
    max_h = np.max(bkt[B_ENTRY:exit_b+1, H])
    min_l = np.min(bkt[B_ENTRY:exit_b+1, L])

    if side == 'sell':
        raw_ret = (entry_price - exit_price) / entry_price * 100
        mfe     = (entry_price - min_l)     / entry_price * 100
        mae     = (max_h - entry_price)     / entry_price * 100
    else:  # buy
        raw_ret = (exit_price - entry_price) / entry_price * 100
        mfe     = (max_h - entry_price)     / entry_price * 100
        mae     = (entry_price - min_l)     / entry_price * 100

    return raw_ret, mfe, mae


# ─────────────────────────────────────────────
# 4. FILTER STOCKS FOR A STRATEGY
# ─────────────────────────────────────────────
def filter_stocks(day_stocks, strat):
    """Return list of (score, stock) passing the strategy filters."""
    side     = strat['side']
    gap_min  = strat['gap_min']
    gap_max  = strat['gap_max']
    exit_b   = strat['exit_b']

    candidates = []
    for stock in day_stocks:
        gap = stock['gap']

        # Gap filter
        if side == 'sell' and not (gap_min <= gap <= gap_max):
            continue
        if side == 'buy' and not (gap_min <= gap <= gap_max):
            continue

        bkt  = stock['bkt']
        feat = compute_stock_features(bkt)
        if feat is None:
            continue

        # tight_OR3 filter
        if strat.get('tight_or3') and feat['or3_rng'] >= strat['tight_or3']:
            continue

        # avg_br_max filter (sell: want low buy ratio)
        if strat.get('avg_br_max') and feat['avg_br6'] >= strat['avg_br_max']:
            continue

        # avg_br_min filter (buy: want high buy ratio)
        if strat.get('avg_br_min') and feat['avg_br6'] < strat['avg_br_min']:
            continue

        # b0_br_max filter
        if strat.get('b0_br_max') and feat['b0_br'] >= strat['b0_br_max']:
            continue

        # Score = abs(gap) for cherry-pick ranking
        score = abs(gap)
        candidates.append((score, stock, feat))

    # Sort by score DESC, take top MAX_POSITIONS
    candidates.sort(key=lambda x: -x[0])
    return candidates[:MAX_POSITIONS]


# ─────────────────────────────────────────────
# 5. SIMULATE ONE DAY
# ─────────────────────────────────────────────
def simulate_day(day_stocks, strat_sell, strat_buy):
    """
    Run combined SELL+BUY for one day.
    Returns dict with per-strategy and combined results.
    """
    results = {}

    def run_strategy(strat, label):
        selected = filter_stocks(day_stocks, strat)
        trades   = []
        day_pnl  = 0.0
        circuit_hit = False

        for _, stock, feat in selected:
            bkt          = stock['bkt']
            entry_price  = feat['entry_price']
            exit_b       = strat['exit_b']
            side         = strat['side']

            raw_ret, mfe, mae = trade_result(bkt, entry_price, exit_b, side)

            # Qty (shares) for POSITION_SIZE at entry price
            qty     = int(POSITION_SIZE / entry_price)
            if qty == 0:
                continue

            # P&L in rupees (SELL = positive when price falls)
            pnl_pct = raw_ret - COST_PCT  # deduct round-trip cost
            pnl_rs  = POSITION_SIZE * pnl_pct / 100

            # Circuit breaker check
            day_pnl += pnl_rs
            if day_pnl < -(TOTAL_CAPITAL * CIRCUIT_BKR_PCT / 100):
                circuit_hit = True
                # Force close: assume 0 exit (cut at entry) — conservative
                # This is simplified; real CB exits at current price
                break

            trades.append({
                'sym':    stock['sym'],
                'gap':    stock['gap'],
                'entry':  entry_price,
                'raw_ret': raw_ret,
                'pnl_pct': pnl_pct,
                'pnl_rs':  pnl_rs,
                'mfe':    mfe,
                'mae':    mae,
                'qty':    qty,
            })

        total_pnl    = sum(t['pnl_rs'] for t in trades)
        total_capital_used = len(trades) * CAPITAL_PER
        roc = total_pnl / TOTAL_CAPITAL * 100 if trades else 0.0
        wins = sum(1 for t in trades if t['pnl_rs'] > 0)

        return {
            'trades':         trades,
            'n':              len(trades),
            'pnl_rs':         total_pnl,
            'roc':            roc,
            'wins':           wins,
            'circuit':        circuit_hit,
        }

    results['sell'] = run_strategy(strat_sell, 'SELL') if strat_sell else {'n':0,'pnl_rs':0,'roc':0,'wins':0,'trades':[],'circuit':False}
    results['buy']  = run_strategy(strat_buy,  'BUY')  if strat_buy  else {'n':0,'pnl_rs':0,'roc':0,'wins':0,'trades':[],'circuit':False}

    # Combined P&L
    total_pnl = results['sell']['pnl_rs'] + results['buy']['pnl_rs']
    roc_combined = total_pnl / TOTAL_CAPITAL * 100

    results['combined_pnl'] = total_pnl
    results['combined_roc'] = roc_combined
    return results


# ─────────────────────────────────────────────
# 6. FULL SIMULATION OVER ALL DAYS
# ─────────────────────────────────────────────
def run_simulation(by_date, sell_strat, buy_strat, label):
    sorted_dates = sorted(by_date.keys())
    daily = []

    for date in sorted_dates:
        day_stocks = by_date[date]
        res = simulate_day(day_stocks, sell_strat, buy_strat)
        res['date'] = date
        daily.append(res)

    return daily


# ─────────────────────────────────────────────
# 7. METRICS COMPUTATION
# ─────────────────────────────────────────────
def compute_metrics(daily, use_combined=True):
    key_pnl = 'combined_pnl' if use_combined else 'combined_pnl'
    key_roc = 'combined_roc' if use_combined else 'combined_roc'

    pnls = [d[key_pnl] for d in daily]
    rocs = [d[key_roc] for d in daily]

    cum_pnl = 0
    peak    = 0
    max_dd  = 0
    cum_dd_days = 0
    in_dd   = False
    daily_cum = []

    for pnl in pnls:
        cum_pnl += pnl
        daily_cum.append(cum_pnl)
        if cum_pnl > peak:
            peak = cum_pnl
            in_dd = False
        dd = (peak - cum_pnl) / TOTAL_CAPITAL * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if dd > 0:
            in_dd = True

    wins = sum(1 for r in rocs if r > 0)
    n    = len(rocs)

    all_trades = []
    for d in daily:
        all_trades.extend(d['sell']['trades'])
        all_trades.extend(d['buy']['trades'])

    trade_rets = [t['pnl_pct'] for t in all_trades]
    win_trades = sum(1 for r in trade_rets if r > 0)

    avg_win  = np.mean([r for r in trade_rets if r > 0]) if any(r>0 for r in trade_rets) else 0
    avg_loss = np.mean([r for r in trade_rets if r <= 0]) if any(r<=0 for r in trade_rets) else 0

    # Calmar = annual return / max drawdown
    total_roc   = cum_pnl / TOTAL_CAPITAL * 100
    n_days      = len(daily)
    annual_roc  = total_roc / n_days * 252  # annualized
    calmar      = annual_roc / max_dd if max_dd > 0 else 0

    # Sharpe (daily)
    roc_arr = np.array(rocs)
    sharpe  = (np.mean(roc_arr) / np.std(roc_arr) * np.sqrt(252)) if np.std(roc_arr) > 0 else 0

    return {
        'total_pnl':    cum_pnl,
        'total_roc':    total_roc,
        'annual_roc':   annual_roc,
        'max_dd':       max_dd,
        'calmar':       calmar,
        'sharpe':       sharpe,
        'day_win_rate': wins / n * 100 if n > 0 else 0,
        'trade_win_rate': win_trades / len(trade_rets) * 100 if trade_rets else 0,
        'n_days':       n_days,
        'n_trades':     len(trade_rets),
        'avg_win_pct':  avg_win,
        'avg_loss_pct': avg_loss,
        'daily_cum':    daily_cum,
    }


# ─────────────────────────────────────────────
# 8. PRINT RESULTS
# ─────────────────────────────────────────────
def print_strategy_header(out, label):
    out.write('\n' + '='*90 + '\n')
    out.write(f'  {label}\n')
    out.write('='*90 + '\n')


def print_metrics(out, m, label=''):
    out.write(f'\n{label}\n')
    out.write(f'  Days:              {m["n_days"]}\n')
    out.write(f'  Trades:            {m["n_trades"]}\n')
    out.write(f'  Total P&L:         ₹{m["total_pnl"]:>10,.0f}\n')
    out.write(f'  Total ROC:         {m["total_roc"]:>8.1f}%  (on ₹{TOTAL_CAPITAL:,} capital)\n')
    out.write(f'  Annualized ROC:    {m["annual_roc"]:>8.1f}%\n')
    out.write(f'  Max Drawdown:      {m["max_dd"]:>8.2f}%\n')
    out.write(f'  Calmar Ratio:      {m["calmar"]:>8.2f}\n')
    out.write(f'  Sharpe Ratio:      {m["sharpe"]:>8.2f}\n')
    out.write(f'  Day Win Rate:      {m["day_win_rate"]:>8.1f}%\n')
    out.write(f'  Trade Win Rate:    {m["trade_win_rate"]:>8.1f}%\n')
    out.write(f'  Avg Win:           {m["avg_win_pct"]:>+8.3f}% per trade  (on position)\n')
    out.write(f'  Avg Win (margin):  {m["avg_win_pct"]*MARGIN_MULT:>+8.3f}% per trade  (on capital)\n')
    out.write(f'  Avg Loss:          {m["avg_loss_pct"]:>+8.3f}% per trade  (on position)\n')
    out.write(f'  Avg Loss (margin): {m["avg_loss_pct"]*MARGIN_MULT:>+8.3f}% per trade  (on capital)\n')

    # Equity curve (bar chart style)
    out.write(f'\n  Equity Curve (daily cumulative P&L, ₹):\n')
    dc = m['daily_cum']
    peak = max(dc) if dc else 1
    bar_w = 50
    for i, v in enumerate(dc):
        bar = int(v / peak * bar_w) if peak > 0 else 0
        bar = max(0, bar)
        out.write(f'  Day {i+1:3d}: {"█"*bar}{"░"*(bar_w-bar)} ₹{v:>10,.0f}\n')


def print_daily_breakdown(out, daily, strat_label):
    out.write(f'\n  Daily breakdown ({strat_label}):\n')
    out.write(f'  {"Date":<12} {"N":>3} {"PnL":>9} {"ROC":>7}  Top picks\n')
    out.write(f'  {"-"*80}\n')
    for d in daily:
        n   = d['sell']['n'] + d['buy']['n']
        pnl = d['combined_pnl']
        roc = d['combined_roc']
        top = []
        for t in (d['sell']['trades'] + d['buy']['trades'])[:3]:
            arrow = '▼' if d['sell']['trades'] and t in d['sell']['trades'] else '▲'
            top.append(f"{arrow}{t['sym']}({t['gap']:+.1f}%→{t['pnl_pct']:+.2f}%)")
        line = f"  {d['date']:<12} {n:>3} ₹{pnl:>8,.0f} {roc:>+6.2f}%  {', '.join(top)}"
        out.write(line + '\n')


# ─────────────────────────────────────────────
# 9. MAIN
# ─────────────────────────────────────────────
def main():
    t_start = time.time()
    print('Loading data...')
    by_date = load_data()

    configs = [
        # (sell_key, buy_key, label)
        ('A_current',    None,       'STRATEGY A: Current SELL (gap>0.1%, exit b66)'),
        ('B_sell_tight', None,       'STRATEGY B: SELL gap>1.5% + tight_OR3<0.3%, exit b90'),
        ('C_sell_br',    None,       'STRATEGY C: SELL gap>1.5% + avg_br<0.40, exit b90'),
        ('D_sell_b0br',  None,       'STRATEGY D: SELL gap>2% + b0_br<0.30, exit b90'),
        ('E_sell_combo', None,       'STRATEGY E: SELL gap>1.5% + tight_OR3 + avg_br<0.40, exit b90 [BEST SELL]'),
        (None,           'F_buy',    'STRATEGY F: BUY gap<-2% + avg_br>0.55, exit b45'),
        ('E_sell_combo', 'F_buy',    'STRATEGY G: COMBINED E(SELL) + F(BUY)'),
        ('A_current',    'F_buy',    'STRATEGY H: Current SELL + New BUY'),
    ]

    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write('BACKTEST SIMULATION REPORT\n')
        out.write(f'Capital: ₹{TOTAL_CAPITAL:,} total  (₹{CAPITAL_PER:,} × {MAX_POSITIONS} positions)\n')
        out.write(f'Margin:  {MARGIN_MULT}x intraday  →  Position size = ₹{POSITION_SIZE:,} per trade\n')
        out.write(f'Cost:    {COST_PCT}% round-trip assumed\n')
        out.write(f'Data:    {sum(len(v) for v in by_date.values())} records, {len(by_date)} days\n')
        out.write(f'         {sorted(by_date)[0]} to {sorted(by_date)[-1]}\n')
        out.write(f'\nEntry:   bucket 6 open (9:21 AM)  — STRICT no-lookahead\n')
        out.write(f'Signal:  uses ONLY buckets 0-5 (9:15-9:20 AM data)\n')

        # ── Summary table first ──
        summary_rows = []
        all_results = {}

        for sell_k, buy_k, lbl in configs:
            sell_s = STRATEGIES[sell_k] if sell_k else None
            buy_s  = STRATEGIES[buy_k]  if buy_k  else None
            print(f'  Simulating: {lbl}...')
            daily  = run_simulation(by_date, sell_s, buy_s, lbl)
            m      = compute_metrics(daily)
            all_results[lbl] = (daily, m)
            summary_rows.append((lbl, m))

        out.write('\n' + '='*90 + '\n')
        out.write('SUMMARY TABLE\n')
        out.write('='*90 + '\n')
        hdr = f'  {"Strategy":<52} {"TotalROC":>9} {"AnnROC":>8} {"MaxDD":>7} {"Sharpe":>7} {"Calmar":>7} {"TradeWin":>9} {"Trades":>7}\n'
        out.write(hdr)
        out.write('  ' + '-'*87 + '\n')
        for lbl, m in summary_rows:
            row = (f'  {lbl:<52} {m["total_roc"]:>8.1f}% {m["annual_roc"]:>7.1f}% '
                   f'{m["max_dd"]:>6.1f}% {m["sharpe"]:>7.2f} {m["calmar"]:>7.2f} '
                   f'{m["trade_win_rate"]:>8.1f}% {m["n_trades"]:>7d}\n')
            out.write(row)

        # ── Detailed breakdown per strategy ──
        for sell_k, buy_k, lbl in configs:
            daily, m = all_results[lbl]
            print_strategy_header(out, lbl)

            # Config
            if sell_k:
                s = STRATEGIES[sell_k]
                out.write(f'  SELL: gap>={s["gap_min"]}%, tight_OR3<{s.get("tight_or3","—")}, '
                          f'avg_br<{s.get("avg_br_max","—")}, b0_br<{s.get("b0_br_max","—")}, '
                          f'exit=b{s["exit_b"]+1}\n')
            if buy_k:
                s = STRATEGIES[buy_k]
                out.write(f'  BUY:  gap<={s["gap_max"]}%, avg_br>={s.get("avg_br_min","—")}, '
                          f'exit=b{s["exit_b"]+1}\n')

            print_metrics(out, m, 'Combined Metrics:')
            print_daily_breakdown(out, daily, lbl)

        out.write(f'\n\nCompleted in {time.time()-t_start:.1f}s\n')

    print(f'\nDone in {time.time()-t_start:.1f}s')
    print(f'Report: {OUT_FILE}')


if __name__ == '__main__':
    main()
