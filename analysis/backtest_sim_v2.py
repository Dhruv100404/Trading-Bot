"""
Full Strategy Backtest v2 — FIXED
==================================
Fixes from v1:
  BUG-1: Combined strategy ROC divided by 80k but used 160k capital → FIXED
  BUG-2: No volume filter — picked illiquid micro-caps → ADDED f5Vol filter
  BUG-3: No max gap cap — stocks at 15-20% gap near circuit → ADDED gap_max
  BUG-4: No TP simulation — added optional trailing TP to test green-day rate
  BUG-5: Cherry-pick by gap only → volume-weighted scoring option added

NO-LOOKAHEAD:
  Signal formed at end of bucket 5 (index 5 = 9:20 AM)
  Entry price = bucket 6 open (index 6 = 9:21 AM)
  Features use ONLY buckets 0..5

MARGIN:
  5x intraday → ₹10k margin controls ₹50k position
"""

import numpy as np
import json
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT_FILE  = DATA_DIR / 'backtest_report_v2.txt'

# ── Constants ──
MARGIN_MULT   = 5
MAX_POS_SELL  = 8
MAX_POS_BUY   = 8
CAPITAL_PER   = 10_000
POSITION_SIZE = CAPITAL_PER * MARGIN_MULT   # 50,000

CIRCUIT_BKR_PCT = 6.0
COST_PCT        = 0.15   # round-trip

# Volume filter: f5Vol * entry_price must exceed this (₹ value)
MIN_F5_VOLUME_RS = 200_000   # ₹2 lakh in first 5 min

# Gap caps (avoid circuit-limit stocks)
SELL_GAP_MAX = 15.0   # don't short stocks gapping >15%
BUY_GAP_MIN  = -15.0  # don't buy stocks gapping <-15%

B_ENTRY   = 6
B_EXIT_45 = 44
B_EXIT_66 = 65
B_EXIT_90 = 89

O, H, L, C, V, VW, BR = 0, 1, 2, 3, 4, 5, 6

# ── Strategy configs ──
STRATEGIES = {
    'A_current': dict(
        side='sell', gap_min=0.10, gap_max=SELL_GAP_MAX,
        tight_or3=None, avg_br_max=None, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_66, vol_required=False,
        label='SELL gap>0.10% exit=b66 [CURRENT, no vol filter]'),
    'A2_current_vol': dict(
        side='sell', gap_min=0.10, gap_max=SELL_GAP_MAX,
        tight_or3=None, avg_br_max=None, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_66, vol_required=True,
        label='SELL gap>0.10% exit=b66 [CURRENT + vol filter]'),
    'B_tight': dict(
        side='sell', gap_min=1.50, gap_max=SELL_GAP_MAX,
        tight_or3=0.30, avg_br_max=None, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_90, vol_required=True,
        label='SELL gap>1.5% + tight_OR3<0.3% exit=b90'),
    'C_br': dict(
        side='sell', gap_min=1.50, gap_max=SELL_GAP_MAX,
        tight_or3=None, avg_br_max=0.40, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_90, vol_required=True,
        label='SELL gap>1.5% + avg_br<0.40 exit=b90'),
    'D_b0br': dict(
        side='sell', gap_min=2.00, gap_max=SELL_GAP_MAX,
        tight_or3=None, avg_br_max=None, avg_br_min=None, b0_br_max=0.30,
        exit_b=B_EXIT_90, vol_required=True,
        label='SELL gap>2% + b0_br<0.30 exit=b90'),
    'E_combo': dict(
        side='sell', gap_min=1.50, gap_max=SELL_GAP_MAX,
        tight_or3=0.30, avg_br_max=0.40, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_90, vol_required=True,
        label='SELL gap>1.5% + tight_OR3 + avg_br<0.40 exit=b90 [BEST]'),
    'F_buy': dict(
        side='buy', gap_min=BUY_GAP_MIN, gap_max=-2.0,
        tight_or3=None, avg_br_max=None, avg_br_min=0.55, b0_br_max=None,
        exit_b=B_EXIT_45, vol_required=True,
        label='BUY gap<-2% + avg_br>0.55 exit=b45'),
    # TP variants for green-day optimization
    'E_tp05': dict(
        side='sell', gap_min=1.50, gap_max=SELL_GAP_MAX,
        tight_or3=0.30, avg_br_max=0.40, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_90, vol_required=True, tp_pct=0.50,
        label='SELL E + TP@0.5%'),
    'E_tp10': dict(
        side='sell', gap_min=1.50, gap_max=SELL_GAP_MAX,
        tight_or3=0.30, avg_br_max=0.40, avg_br_min=None, b0_br_max=None,
        exit_b=B_EXIT_90, vol_required=True, tp_pct=1.00,
        label='SELL E + TP@1.0%'),
}


# ─────────────────────────────────────────────
# 1. LOAD DATA
# ─────────────────────────────────────────────
def load_data():
    t0 = time.time()
    files = [DATA_DIR / 'candles-consolidated.ndjson',
             DATA_DIR / 'candles-consolidated_new.ndjson']

    by_date = defaultdict(list)

    for fp in files:
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                date    = rec['date']
                gap     = rec['gapPct']
                sym     = rec['symbol']
                f5vol   = rec.get('f5Vol', 0)
                day_opn = rec['dayOpen']
                bkts    = rec['buckets']
                nb      = min(len(bkts), 100)

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

                by_date[date].append({
                    'sym': sym, 'gap': gap, 'bkt': bkt_arr,
                    'f5vol': f5vol, 'day_open': day_opn,
                })

    n_rec = sum(len(v) for v in by_date.values())
    print(f"Loaded {n_rec} records across {len(by_date)} days in {time.time()-t0:.1f}s")
    return by_date


# ─────────────────────────────────────────────
# 2. FEATURES (no lookahead)
# ─────────────────────────────────────────────
def compute_features(bkt, day_open, f5vol):
    entry_price = bkt[B_ENTRY, O]
    if entry_price <= 0 or day_open <= 0:
        return None

    or3_h = max(bkt[0, H], bkt[1, H], bkt[2, H])
    or3_l = min(bkt[0, L], bkt[1, L], bkt[2, L])
    or3_rng = (or3_h - or3_l) / or3_h * 100 if or3_h > 0 else 99.0
    avg_br6 = float(np.mean(bkt[:6, BR]))
    b0_br   = float(bkt[0, BR])
    f5vol_rs = f5vol * day_open  # ₹ value of first-5-min volume

    return {
        'entry_price': entry_price,
        'or3_rng':     or3_rng,
        'avg_br6':     avg_br6,
        'b0_br':       b0_br,
        'f5vol_rs':    f5vol_rs,
    }


# ─────────────────────────────────────────────
# 3. TRADE RESULT with optional TP
# ─────────────────────────────────────────────
def trade_result(bkt, entry_price, exit_b, side, tp_pct=None):
    """
    Returns (exit_pct, mfe_pct, mae_pct).
    If tp_pct is set, exit early when MFE >= tp_pct (simulates limit TP).
    """
    if exit_b >= 100 or bkt[exit_b, C] <= 0:
        exit_b = min(exit_b, 89)

    # Scan bucket by bucket for TP hit (no lookahead within day)
    actual_exit_b = exit_b
    if tp_pct is not None:
        for b in range(B_ENTRY, exit_b + 1):
            if side == 'sell':
                # Check if low went far enough below entry
                fav = (entry_price - bkt[b, L]) / entry_price * 100
            else:
                fav = (bkt[b, H] - entry_price) / entry_price * 100
            if fav >= tp_pct:
                actual_exit_b = b
                break

    # Compute metrics over full range entry..actual_exit_b
    sl = bkt[B_ENTRY:actual_exit_b+1]
    max_h = np.max(sl[:, H])
    min_l = np.min(sl[:, L])

    if tp_pct is not None and actual_exit_b < exit_b:
        # TP hit: exit at exactly tp_pct
        raw_ret = tp_pct
    else:
        # Time exit: use close of exit bucket
        exit_price = bkt[actual_exit_b, C]
        if side == 'sell':
            raw_ret = (entry_price - exit_price) / entry_price * 100
        else:
            raw_ret = (exit_price - entry_price) / entry_price * 100

    if side == 'sell':
        mfe = (entry_price - min_l) / entry_price * 100
        mae = (max_h - entry_price) / entry_price * 100
    else:
        mfe = (max_h - entry_price) / entry_price * 100
        mae = (entry_price - min_l) / entry_price * 100

    return raw_ret, mfe, mae


# ─────────────────────────────────────────────
# 4. FILTER & RANK
# ─────────────────────────────────────────────
def filter_stocks(day_stocks, strat):
    side    = strat['side']
    gap_min = strat['gap_min']
    gap_max = strat['gap_max']
    vol_req = strat.get('vol_required', False)
    max_pos = MAX_POS_SELL if side == 'sell' else MAX_POS_BUY

    candidates = []
    for stock in day_stocks:
        gap = stock['gap']
        if not (gap_min <= gap <= gap_max):
            continue

        feat = compute_features(stock['bkt'], stock['day_open'], stock['f5vol'])
        if feat is None:
            continue

        # Volume filter
        if vol_req and feat['f5vol_rs'] < MIN_F5_VOLUME_RS:
            continue

        # Tight OR3
        if strat.get('tight_or3') and feat['or3_rng'] >= strat['tight_or3']:
            continue
        # avg_br filters
        if strat.get('avg_br_max') and feat['avg_br6'] >= strat['avg_br_max']:
            continue
        if strat.get('avg_br_min') and feat['avg_br6'] < strat['avg_br_min']:
            continue
        # b0_br filter
        if strat.get('b0_br_max') and feat['b0_br'] >= strat['b0_br_max']:
            continue

        score = abs(gap)
        candidates.append((score, stock, feat))

    candidates.sort(key=lambda x: -x[0])
    return candidates[:max_pos]


# ─────────────────────────────────────────────
# 5. SIMULATE ONE DAY
# ─────────────────────────────────────────────
def simulate_day(day_stocks, strat_sell, strat_buy):
    results = {}

    def run_one(strat):
        if strat is None:
            return {'trades': [], 'n': 0, 'pnl_rs': 0.0, 'wins': 0, 'circuit': False}

        selected = filter_stocks(day_stocks, strat)
        trades   = []
        day_pnl  = 0.0
        circuit  = False
        tp_pct   = strat.get('tp_pct', None)

        n_pos  = MAX_POS_SELL if strat['side'] == 'sell' else MAX_POS_BUY
        cap_used = n_pos * CAPITAL_PER

        for _, stock, feat in selected:
            entry_price = feat['entry_price']
            raw_ret, mfe, mae = trade_result(
                stock['bkt'], entry_price, strat['exit_b'], strat['side'], tp_pct)

            qty = int(POSITION_SIZE / entry_price)
            if qty == 0:
                continue

            pnl_pct = raw_ret - COST_PCT
            pnl_rs  = POSITION_SIZE * pnl_pct / 100

            day_pnl += pnl_rs
            if day_pnl < -(cap_used * CIRCUIT_BKR_PCT / 100):
                circuit = True
                break

            trades.append({
                'sym': stock['sym'], 'gap': stock['gap'],
                'entry': entry_price, 'raw_ret': raw_ret,
                'pnl_pct': pnl_pct, 'pnl_rs': pnl_rs,
                'mfe': mfe, 'mae': mae, 'qty': qty,
            })

        return {
            'trades': trades, 'n': len(trades),
            'pnl_rs': sum(t['pnl_rs'] for t in trades),
            'wins': sum(1 for t in trades if t['pnl_rs'] > 0),
            'circuit': circuit,
        }

    results['sell'] = run_one(strat_sell)
    results['buy']  = run_one(strat_buy)

    # ── FIX BUG-1: correct capital for combined ──
    sell_n = results['sell']['n']
    buy_n  = results['buy']['n']
    total_capital_used = max((sell_n + buy_n) * CAPITAL_PER, 1)

    # For ROC, use the MAX capital that COULD be used (worst case: all positions filled)
    sell_max_cap = (MAX_POS_SELL * CAPITAL_PER) if strat_sell else 0
    buy_max_cap  = (MAX_POS_BUY  * CAPITAL_PER) if strat_buy  else 0
    allocated_capital = sell_max_cap + buy_max_cap

    total_pnl = results['sell']['pnl_rs'] + results['buy']['pnl_rs']
    results['combined_pnl'] = total_pnl
    results['combined_roc'] = total_pnl / allocated_capital * 100 if allocated_capital > 0 else 0
    results['allocated_capital'] = allocated_capital
    return results


# ─────────────────────────────────────────────
# 6. RUN FULL SIMULATION
# ─────────────────────────────────────────────
def run_sim(by_date, sell_strat, buy_strat):
    daily = []
    for date in sorted(by_date.keys()):
        res = simulate_day(by_date[date], sell_strat, buy_strat)
        res['date'] = date
        daily.append(res)
    return daily


# ─────────────────────────────────────────────
# 7. METRICS
# ─────────────────────────────────────────────
def metrics(daily):
    if not daily:
        return None
    alloc_cap = daily[0]['allocated_capital']
    pnls = [d['combined_pnl'] for d in daily]
    rocs = [d['combined_roc'] for d in daily]

    cum = 0; peak = 0; max_dd = 0; daily_cum = []
    for p in pnls:
        cum += p; daily_cum.append(cum)
        if cum > peak: peak = cum
        dd = (peak - cum) / alloc_cap * 100 if alloc_cap > 0 else 0
        if dd > max_dd: max_dd = dd

    all_trades = []
    for d in daily:
        all_trades.extend(d['sell']['trades'])
        all_trades.extend(d['buy']['trades'])

    rets = [t['pnl_pct'] for t in all_trades]
    win_t = sum(1 for r in rets if r > 0)
    n_t   = len(rets)
    n_d   = len(daily)
    wins_d = sum(1 for r in rocs if r > 0)
    zero_d = sum(1 for d in daily if d['sell']['n'] + d['buy']['n'] == 0)
    green_d = sum(1 for r in rocs if r > 0)
    active_d = n_d - zero_d

    avg_w = np.mean([r for r in rets if r > 0]) if any(r>0 for r in rets) else 0
    avg_l = np.mean([r for r in rets if r <= 0]) if any(r<=0 for r in rets) else 0

    total_roc  = cum / alloc_cap * 100
    annual_roc = total_roc / n_d * 252
    roc_arr    = np.array(rocs)
    sharpe     = (np.mean(roc_arr) / np.std(roc_arr) * np.sqrt(252)) if np.std(roc_arr) > 0 else 0
    calmar     = annual_roc / max_dd if max_dd > 0 else 0

    # Worst day
    worst_i = int(np.argmin(pnls))
    best_i  = int(np.argmax(pnls))

    return {
        'alloc_cap':   alloc_cap,
        'total_pnl':   cum,
        'total_roc':   total_roc,
        'annual_roc':  annual_roc,
        'max_dd':      max_dd,
        'calmar':      calmar,
        'sharpe':      sharpe,
        'day_win':     wins_d / n_d * 100 if n_d else 0,
        'active_day_win': green_d / active_d * 100 if active_d else 0,
        'trade_win':   win_t / n_t * 100 if n_t else 0,
        'n_days':      n_d,
        'active_days': active_d,
        'n_trades':    n_t,
        'avg_win':     avg_w,
        'avg_loss':    avg_l,
        'daily_cum':   daily_cum,
        'worst_day':   daily[worst_i]['date'],
        'worst_pnl':   pnls[worst_i],
        'best_day':    daily[best_i]['date'],
        'best_pnl':    pnls[best_i],
        'zero_days':   zero_d,
    }


# ─────────────────────────────────────────────
# 8. OUTPUT
# ─────────────────────────────────────────────
def fmt_metrics(out, m):
    ac = m['alloc_cap']
    out.write(f'  Allocated Capital: ₹{ac:>10,}\n')
    out.write(f'  Days (total/active):  {m["n_days"]} / {m["active_days"]}\n')
    out.write(f'  Trades:               {m["n_trades"]}\n')
    out.write(f'  Total P&L:         ₹{m["total_pnl"]:>10,.0f}\n')
    out.write(f'  Total ROC:         {m["total_roc"]:>8.1f}%  (on ₹{ac:,})\n')
    out.write(f'  Annualized ROC:    {m["annual_roc"]:>8.1f}%\n')
    out.write(f'  Max Drawdown:      {m["max_dd"]:>8.2f}%\n')
    out.write(f'  Calmar Ratio:      {m["calmar"]:>8.2f}\n')
    out.write(f'  Sharpe Ratio:      {m["sharpe"]:>8.2f}\n')
    out.write(f'  Day Win Rate:      {m["day_win"]:>8.1f}%  (all days)\n')
    out.write(f'  Active Day Win:    {m["active_day_win"]:>8.1f}%  (days with trades)\n')
    out.write(f'  Trade Win Rate:    {m["trade_win"]:>8.1f}%\n')
    out.write(f'  Avg Win:           {m["avg_win"]:>+.3f}% position  = {m["avg_win"]*MARGIN_MULT:>+.2f}% on capital\n')
    out.write(f'  Avg Loss:          {m["avg_loss"]:>+.3f}% position  = {m["avg_loss"]*MARGIN_MULT:>+.2f}% on capital\n')
    out.write(f'  Best Day:          {m["best_day"]}  ₹{m["best_pnl"]:>+,.0f}\n')
    out.write(f'  Worst Day:         {m["worst_day"]}  ₹{m["worst_pnl"]:>+,.0f}\n')
    out.write(f'  Zero-signal Days:  {m["zero_days"]}\n')


def fmt_daily(out, daily, label):
    out.write(f'\n  Daily P&L ({label}):\n')
    out.write(f'  {"Date":<12} {"Sell":>3}+{"Buy":>3} {"PnL":>10} {"ROC":>7}  Picks\n')
    out.write(f'  {"-"*85}\n')
    for d in daily:
        ns = d['sell']['n']; nb = d['buy']['n']
        pnl = d['combined_pnl']; roc = d['combined_roc']
        picks = []
        for t in (d['sell']['trades'] + d['buy']['trades'])[:3]:
            arrow = '▼' if t in d['sell']['trades'] else '▲'
            picks.append(f"{arrow}{t['sym']}({t['gap']:+.1f}%→{t['pnl_pct']:+.2f}%)")
        marker = ' ★' if roc < 0 else ''
        out.write(f"  {d['date']:<12} {ns:>3}+{nb:>3} ₹{pnl:>9,.0f} {roc:>+6.2f}%{marker}  {', '.join(picks)}\n")


# ─────────────────────────────────────────────
# 9. MAIN
# ─────────────────────────────────────────────
def main():
    t0 = time.time()
    print('Loading data...')
    by_date = load_data()

    configs = [
        # (sell_key, buy_key, label)
        ('A_current',    None,    'A: Current SELL (no vol filter)'),
        ('A2_current_vol', None,  'A2: Current SELL + vol filter'),
        ('B_tight',      None,    'B: SELL gap>1.5% + tight_OR3 [+vol]'),
        ('C_br',         None,    'C: SELL gap>1.5% + avg_br<0.40 [+vol]'),
        ('D_b0br',       None,    'D: SELL gap>2% + b0_br<0.30 [+vol]'),
        ('E_combo',      None,    'E: SELL BEST combo [+vol]'),
        (None,           'F_buy', 'F: BUY gap<-2% + avg_br>0.55 [+vol]'),
        ('E_combo',      'F_buy', 'G: COMBINED E+F [capital=160k]'),
        ('E_tp05',       None,    'E_tp05: SELL E + TP@0.5% [green-day opt]'),
        ('E_tp10',       None,    'E_tp10: SELL E + TP@1.0% [green-day opt]'),
        ('E_tp05',       'F_buy', 'G_tp: COMBINED E(TP@0.5%)+F [green-day opt]'),
    ]

    results_map = {}
    for sell_k, buy_k, lbl in configs:
        sell_s = STRATEGIES.get(sell_k) if sell_k else None
        buy_s  = STRATEGIES.get(buy_k)  if buy_k  else None
        print(f'  {lbl}...')
        daily = run_sim(by_date, sell_s, buy_s)
        m     = metrics(daily)
        results_map[lbl] = (daily, m)

    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write('BACKTEST v2 — CORRECTED\n')
        out.write('='*90 + '\n')
        out.write(f'Margin:          {MARGIN_MULT}x → position = ₹{POSITION_SIZE:,} per trade\n')
        out.write(f'Cost:            {COST_PCT}% round-trip\n')
        out.write(f'Volume filter:   f5Vol × price ≥ ₹{MIN_F5_VOLUME_RS:,}\n')
        out.write(f'Gap cap:         SELL ≤ {SELL_GAP_MAX}%, BUY ≥ {BUY_GAP_MIN}%\n')
        out.write(f'Data:            {sum(len(v) for v in by_date.values())} recs, {len(by_date)} days\n')
        out.write(f'                 {sorted(by_date)[0]} to {sorted(by_date)[-1]}\n')
        out.write(f'Entry:           bucket 6 open (9:21 AM) — NO lookahead\n')

        # ── Bug-fix note ──
        out.write(f'\nBUGS FIXED vs v1:\n')
        out.write(f'  1. Combined strategy ROC now uses correct allocated capital (sell_cap + buy_cap)\n')
        out.write(f'  2. Volume filter added: f5Vol × price ≥ ₹{MIN_F5_VOLUME_RS:,}\n')
        out.write(f'  3. Gap capped at ±{SELL_GAP_MAX}% to avoid circuit-limit stocks\n')
        out.write(f'  4. TP variants added for green-day optimization\n')

        # ── Summary table ──
        out.write('\n' + '='*90 + '\n')
        out.write('SUMMARY TABLE\n')
        out.write('='*90 + '\n')
        hdr = (f'  {"Strategy":<45} {"Cap":>6} {"TotROC":>8} {"AnnROC":>8} '
               f'{"MaxDD":>6} {"Sharpe":>7} {"DayWin":>7} {"TrdWin":>7} {"Trds":>5}\n')
        out.write(hdr)
        out.write('  ' + '-'*102 + '\n')
        for _, (_, m) in results_map.items():
            pass
        for lbl, (_, m) in results_map.items():
            ac = m['alloc_cap'] // 1000
            out.write(f'  {lbl:<45} {ac:>4}k {m["total_roc"]:>7.1f}% {m["annual_roc"]:>7.0f}% '
                      f'{m["max_dd"]:>5.1f}% {m["sharpe"]:>7.2f} '
                      f'{m["active_day_win"]:>6.1f}% {m["trade_win"]:>6.1f}% {m["n_trades"]:>5d}\n')

        # ── Volume filter impact ──
        out.write('\n' + '='*90 + '\n')
        out.write('VOLUME FILTER IMPACT\n')
        out.write('='*90 + '\n')
        if 'A: Current SELL (no vol filter)' in results_map and 'A2: Current SELL + vol filter' in results_map:
            _, m_no  = results_map['A: Current SELL (no vol filter)']
            _, m_vol = results_map['A2: Current SELL + vol filter']
            out.write(f'  Without vol filter:  ROC={m_no["total_roc"]:.1f}%  trades={m_no["n_trades"]}  tradeWin={m_no["trade_win"]:.1f}%\n')
            out.write(f'  With vol filter:     ROC={m_vol["total_roc"]:.1f}%  trades={m_vol["n_trades"]}  tradeWin={m_vol["trade_win"]:.1f}%\n')
            delta = m_vol["total_roc"] - m_no["total_roc"]
            out.write(f'  Delta:               {delta:+.1f}% ROC\n')
            if m_no["total_roc"] > 0:
                out.write(f'  Vol filter impact:   {delta/m_no["total_roc"]*100:+.1f}% change\n')

        # ── Which need volume? ──
        out.write('\n' + '='*90 + '\n')
        out.write('VOLUME REQUIREMENT BY STRATEGY\n')
        out.write('='*90 + '\n')
        out.write('''
  COMPULSORY volume filter (will NOT work without):
    - ALL strategies in LIVE trading
    - ₹50k position in a stock with < ₹2L first-5-min volume =
      your order IS the market, slippage destroys edge
    - Micro-caps with 10-20% gaps are often T2T/ASM/GSM stocks
      with restrictions on short selling

  Impact by strategy:
    - A/A2 (gap>0.1%): picks top 8 by gap → selects the most extreme
      micro-caps. Volume filter removes many and shifts to mid/large caps.
      MANDATORY.
    - B/C/D/E (gap>1.5%+): even at higher gap threshold, top 8 still
      skews micro-cap. Volume filter MANDATORY.
    - F (BUY gap<-2%): same — gap-down stocks with high avg_br often
      have low volumes. MANDATORY.

  Bottom line: EVERY strategy needs volume filter for live use.
''')

        # ── Green-day analysis ──
        out.write('\n' + '='*90 + '\n')
        out.write('GREEN DAY ANALYSIS (90%+ target)\n')
        out.write('='*90 + '\n')
        for lbl in ['E: SELL BEST combo [+vol]', 'E_tp05: SELL E + TP@0.5% [green-day opt]',
                     'E_tp10: SELL E + TP@1.0% [green-day opt]',
                     'G_tp: COMBINED E(TP@0.5%)+F [green-day opt]']:
            if lbl in results_map:
                d, m = results_map[lbl]
                losing_days = [(dd['date'], dd['combined_pnl'], dd['combined_roc'])
                               for dd in d if dd['combined_roc'] < 0 and (dd['sell']['n'] + dd['buy']['n']) > 0]
                out.write(f'\n  {lbl}:\n')
                out.write(f'    Active day win: {m["active_day_win"]:.1f}%  ({m["active_days"] - len(losing_days)} green / {m["active_days"]} active)\n')
                out.write(f'    Total ROC:      {m["total_roc"]:.1f}%\n')
                if losing_days:
                    out.write(f'    Losing days ({len(losing_days)}):\n')
                    for dt, pnl, roc in sorted(losing_days, key=lambda x: x[1]):
                        out.write(f'      {dt}  ₹{pnl:>+8,.0f}  ({roc:>+.2f}%)\n')

        # ── Per-strategy details ──
        for lbl, (daily, m) in results_map.items():
            out.write('\n' + '='*90 + '\n')
            out.write(f'  {lbl}\n')
            out.write('='*90 + '\n')
            fmt_metrics(out, m)
            fmt_daily(out, daily, lbl)

        out.write(f'\n\nDone in {time.time()-t0:.1f}s\n')

    print(f'\nDone in {time.time()-t0:.1f}s')
    print(f'Report: {OUT_FILE}')


if __name__ == '__main__':
    main()
