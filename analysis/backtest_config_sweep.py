"""
Config Sweep: test different scorer + position + TP combos against the ACTUAL backtest API.
This uses YOUR data, YOUR watchlist, YOUR engine — no mismatch with Python analysis.
"""
import json, time, requests, itertools, sys

API = "http://localhost:8080/api/backtest/compute"

# Your current base config (known good)
BASE = {
    "from": "2026-01-01", "to": "2026-04-03",
    "entry_bucket_start": 2, "entry_bucket_end": 3,
    "min_move_pct": 0.15, "min_volume": 500, "min_score": 5,
    "tp_pct": 0.7, "sl_pct": 0.6, "hard_exit_bucket": 66,
    "quantity": 1, "gap_filter_min_pct": -100, "gap_filter_max_pct": 100,
    "sell_gap_min_pct": -100, "min_vol_rate": 0,
    "sell_hard_exit_bucket": 76, "buy_gap_max_pct": 100,
    "direction_filter": "BOTH", "capital_per_trade": 20500,
    "buy_tp_pct": 0, "buy_sl_pct": 0,
    "sell_tp_pct": 0.3285, "sell_sl_pct": 0,
    "buy_min_move_pct": 0, "sell_min_move_pct": 0.25,
    "buy_min_vol_rate": 0, "sell_min_vol_rate": 0,
    "buy_capital_per_trade": 10000, "sell_capital_per_trade": 10000,
    "buy_qty_multiplier": 1, "sell_qty_multiplier": 1,
    "buy_entry_start": 2, "buy_entry_end": 6,
    "sell_entry_start": 2, "sell_entry_end": 6,
    "buy_min_volume": 300, "sell_min_volume": 450,
    "buy_min_score": 4, "sell_min_score": 4,
    "buy_gap_min_pct": 0, "sell_gap_max_pct": 10,
    "cherry_pick_enabled": True, "total_capital": 50000,
    "max_positions": 12, "min_position_value": 500,
    "tp_score_scaling": True, "max_loss_pct": 4,
    "volume_rank_mode": False, "vr_min_move_pct": 0.3, "vr_min_vol_rate": 0,
    "gap_reversal_mode": True, "gap_reversal_buy_mode": False,
    "smc_trend_sell": False, "smc_trend_buy": False,
    "smart_score_mode": False, "smart_score_v2": False,
    "smart_score_s5": False, "smart_score_s6": False,
    "smart_score_gap_sp_mom": False, "smart_score_gap_bell": False,
    "sell_max_positions": 7, "buy_max_positions": 1,
    "sizing_enabled": False, "sizing_check_bucket": 20,
    "sizing_3x_min_pnl": 1, "sizing_3x_max_green": 3,
    "sizing_2x_min_pnl": 0.3, "sizing_exit_loss": 99,
    "sizing_exit_vwap": 0.3, "sizing_exit_green": 99,
    "sizing_3x_mult": 2, "sizing_2x_mult": 1,
    "buy_sizing_check_bucket": 10,
    "stock_filter": "WATCHLIST",
}

def run_backtest(overrides: dict) -> dict:
    cfg = {**BASE, **overrides}
    resp = requests.post(API, json=cfg, timeout=300)
    resp.raise_for_status()
    return resp.json()

def analyze(result: dict, label: str, n_pos: int):
    signals = result.get("signals", [])
    if not signals:
        return None

    closed = [s for s in signals if s.get("exit_reason")]
    if not closed:
        return None

    total_pnl = sum(s.get("pnl_rupees", 0) or 0 for s in closed)
    wins = sum(1 for s in closed if (s.get("pnl_rupees", 0) or 0) > 0)
    tp_count = sum(1 for s in closed if s.get("exit_reason") == "TP")
    capital = sum(s["entry_price"] * s["quantity"] for s in signals)
    margin = capital / 5.0 if capital > 0 else 1

    # Day-level stats
    day_pnl = {}
    for s in closed:
        d = s["trading_date"]
        day_pnl[d] = day_pnl.get(d, 0) + (s.get("pnl_rupees", 0) or 0)
    n_days = len(day_pnl)
    day_wins = sum(1 for p in day_pnl.values() if p > 0)

    roc = total_pnl / margin * 100 if margin > 0 else 0
    tp_pct = tp_count / len(closed) * 100 if closed else 0
    win_pct = wins / len(closed) * 100 if closed else 0
    day_win_pct = day_wins / n_days * 100 if n_days > 0 else 0

    return {
        "label": label,
        "trades": len(closed),
        "days": n_days,
        "pnl": total_pnl,
        "roc": roc,
        "tp_pct": tp_pct,
        "win_pct": win_pct,
        "day_win_pct": day_win_pct,
        "day_wins": day_wins,
        "candidates": result.get("total_candidates", 0),
        "selected": result.get("total_selected", 0),
        "elapsed": result.get("elapsed_ms", 0),
    }

def main():
    results = []
    test_id = 0

    # =========================================================══
    # SWEEP 1: Scorers (keep your current positions=7, TP=0.3285)
    # =========================================================══
    scorer_configs = [
        ("V2 (current)", {"smart_score_v2": True}),
        ("S6", {"smart_score_s6": True}),
        ("S5", {"smart_score_s5": True}),
        ("gap_sp_mom", {"smart_score_gap_sp_mom": True}),
        ("gap_bell", {"smart_score_gap_bell": True}),
        ("v1 (win_rate)", {"smart_score_mode": True}),
        ("plain (gap only)", {}),
    ]

    print("=== SWEEP 1: Scorers (positions=7, TP=0.3285, WATCHLIST) ===")
    for name, scorer_flags in scorer_configs:
        test_id += 1
        flags = {
            "smart_score_mode": False, "smart_score_v2": False,
            "smart_score_s5": False, "smart_score_s6": False,
            "smart_score_gap_sp_mom": False, "smart_score_gap_bell": False,
            **scorer_flags,
        }
        try:
            r = run_backtest(flags)
            a = analyze(r, f"scorer:{name}", 7)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. {name:>20}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. {name:>20}: ERROR {e}")

    # =========================================================══
    # SWEEP 2: Positions (best scorer from sweep 1)
    # =========================================================══
    print("\n=== SWEEP 2: Position count (V2 scorer, TP=0.3285, WATCHLIST) ===")
    for n_pos in [3, 4, 5, 6, 7, 8, 10, 12]:
        test_id += 1
        try:
            r = run_backtest({
                "smart_score_v2": True, "smart_score_gap_sp_mom": False, "smart_score_gap_bell": False,
                "sell_max_positions": n_pos,
            })
            a = analyze(r, f"V2 top-{n_pos}", n_pos)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. V2 top-{n_pos:>2}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. V2 top-{n_pos:>2}: ERROR {e}")

    # =========================================================══
    # SWEEP 3: TP levels (V2, positions=7)
    # =========================================================══
    print("\n=== SWEEP 3: TP levels (V2 scorer, positions=7, WATCHLIST) ===")
    for tp in [0.20, 0.25, 0.30, 0.3285, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]:
        test_id += 1
        try:
            r = run_backtest({
                "smart_score_v2": True, "sell_tp_pct": tp, "tp_score_scaling": False,
            })
            a = analyze(r, f"V2 TP={tp:.2f}%", 7)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. V2 TP={tp:.2f}%: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. V2 TP={tp:.2f}%: ERROR {e}")

    # With tp_score_scaling
    print("\n=== SWEEP 3b: TP levels WITH score scaling (V2 scorer, positions=7, WATCHLIST) ===")
    for tp in [0.25, 0.30, 0.3285, 0.35, 0.40, 0.50, 0.60]:
        test_id += 1
        try:
            r = run_backtest({
                "smart_score_v2": True, "sell_tp_pct": tp, "tp_score_scaling": True,
            })
            a = analyze(r, f"V2 TP={tp:.2f}%+scale", 7)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. V2 TP={tp:.2f}%+scale: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. V2 TP={tp:.2f}%+scale: ERROR {e}")

    # =========================================================══
    # SWEEP 4: gap_sp_mom with different positions + TP
    # =========================================================══
    print("\n=== SWEEP 4: gap_sp_mom position + TP combos (WATCHLIST) ===")
    for n_pos in [3, 5, 7, 8, 10]:
        for tp in [0.3285, 0.40, 0.50, 0.60]:
            for scaling in [True, False]:
                test_id += 1
                label = f"GSM top-{n_pos} TP={tp:.2f}{'s' if scaling else ''}"
                try:
                    r = run_backtest({
                        "smart_score_v2": False, "smart_score_gap_sp_mom": True,
                        "sell_max_positions": n_pos, "sell_tp_pct": tp,
                        "tp_score_scaling": scaling,
                    })
                    a = analyze(r, label, n_pos)
                    if a:
                        results.append(a)
                        print(f"  {test_id:>2}. {label:>30}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
                except Exception as e:
                    print(f"  {test_id:>2}. {label:>30}: ERROR {e}")

    # =========================================================══
    # SWEEP 5: gap_bell with different positions + TP
    # =========================================================══
    print("\n=== SWEEP 5: gap_bell position + TP combos (WATCHLIST) ===")
    for n_pos in [3, 5, 7, 8, 10]:
        for tp in [0.3285, 0.40, 0.50, 0.60]:
            for scaling in [True, False]:
                test_id += 1
                label = f"Bell top-{n_pos} TP={tp:.2f}{'s' if scaling else ''}"
                try:
                    r = run_backtest({
                        "smart_score_v2": False, "smart_score_gap_bell": True,
                        "sell_max_positions": n_pos, "sell_tp_pct": tp,
                        "tp_score_scaling": scaling,
                    })
                    a = analyze(r, label, n_pos)
                    if a:
                        results.append(a)
                        print(f"  {test_id:>2}. {label:>30}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
                except Exception as e:
                    print(f"  {test_id:>2}. {label:>30}: ERROR {e}")

    # =========================================================══
    # SWEEP 6: V2 with different positions + TP (full grid)
    # =========================================================══
    print("\n=== SWEEP 6: V2 position + TP full grid (WATCHLIST) ===")
    for n_pos in [3, 4, 5, 6, 7, 8, 10]:
        for tp in [0.3285, 0.40, 0.50, 0.60]:
            for scaling in [True, False]:
                test_id += 1
                label = f"V2 top-{n_pos} TP={tp:.2f}{'s' if scaling else ''}"
                try:
                    r = run_backtest({
                        "smart_score_v2": True,
                        "sell_max_positions": n_pos, "sell_tp_pct": tp,
                        "tp_score_scaling": scaling,
                    })
                    a = analyze(r, label, n_pos)
                    if a:
                        results.append(a)
                        print(f"  {test_id:>2}. {label:>30}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
                except Exception as e:
                    print(f"  {test_id:>2}. {label:>30}: ERROR {e}")

    # =========================================================══
    # SWEEP 7: S6 with different positions + TP
    # =========================================================══
    print("\n=== SWEEP 7: S6 position + TP combos (WATCHLIST) ===")
    for n_pos in [3, 5, 7, 8]:
        for tp in [0.3285, 0.40, 0.50, 0.60]:
            for scaling in [True, False]:
                test_id += 1
                label = f"S6 top-{n_pos} TP={tp:.2f}{'s' if scaling else ''}"
                try:
                    r = run_backtest({
                        "smart_score_v2": False, "smart_score_s6": True,
                        "sell_max_positions": n_pos, "sell_tp_pct": tp,
                        "tp_score_scaling": scaling,
                    })
                    a = analyze(r, label, n_pos)
                    if a:
                        results.append(a)
                        print(f"  {test_id:>2}. {label:>30}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} pnl={a['pnl']:>+8.0f}")
                except Exception as e:
                    print(f"  {test_id:>2}. {label:>30}: ERROR {e}")

    # =========================================================══
    # SWEEP 8: Stock filter comparison
    # =========================================================══
    print("\n=== SWEEP 8: Stock filter comparison (V2 scorer, positions=7) ===")
    for filt in ["WATCHLIST", "Liquid5L", "ALL", "FNO", "Nifty500"]:
        test_id += 1
        try:
            r = run_backtest({"smart_score_v2": True, "stock_filter": filt})
            a = analyze(r, f"V2 filter={filt}", 7)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. V2 {filt:>12}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} cand={a['candidates']:>5} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. V2 {filt:>12}: ERROR {e}")

    # GSM on different filters
    print("\n=== SWEEP 8b: Stock filter comparison (GSM scorer, positions=7) ===")
    for filt in ["WATCHLIST", "Liquid5L", "ALL"]:
        test_id += 1
        try:
            r = run_backtest({"smart_score_gap_sp_mom": True, "smart_score_v2": False, "stock_filter": filt})
            a = analyze(r, f"GSM filter={filt}", 7)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. GSM {filt:>12}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} cand={a['candidates']:>5} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. GSM {filt:>12}: ERROR {e}")

    # Bell on different filters
    print("\n=== SWEEP 8c: Stock filter comparison (Bell scorer, positions=5) ===")
    for filt in ["WATCHLIST", "Liquid5L", "ALL"]:
        test_id += 1
        try:
            r = run_backtest({"smart_score_gap_bell": True, "smart_score_v2": False, "stock_filter": filt, "sell_max_positions": 5, "sell_tp_pct": 0.50, "tp_score_scaling": False})
            a = analyze(r, f"Bell filter={filt}", 5)
            if a:
                results.append(a)
                print(f"  {test_id:>2}. Bell {filt:>12}: ROC={a['roc']:>+7.1f}% dayW={a['day_win_pct']:>5.1f}% trdW={a['win_pct']:>5.1f}% TP={a['tp_pct']:>5.1f}% trades={a['trades']:>4} cand={a['candidates']:>5} pnl={a['pnl']:>+8.0f}")
        except Exception as e:
            print(f"  {test_id:>2}. Bell {filt:>12}: ERROR {e}")

    # =========================================================══
    # FINAL RANKING
    # =========================================================══
    print("\n" + "="*120)
    print("FINAL RANKING — Top 20 by Day Win Rate (then ROC)")
    print("="*120)

    ranked = sorted(results, key=lambda x: (-x['day_win_pct'], -x['roc']))
    print(f"  {'#':>3} {'Config':>35} {'ROC':>8} {'DayW':>6} {'TrdW':>6} {'TP%':>6} {'Trades':>7} {'PnL':>9}")
    print(f"  "+"-"*90)
    for i, r in enumerate(ranked[:20]):
        print(f"  {i+1:>3}. {r['label']:>35}: ROC={r['roc']:>+7.1f}% dayW={r['day_win_pct']:>5.1f}% trdW={r['win_pct']:>5.1f}% TP={r['tp_pct']:>5.1f}% trades={r['trades']:>4} pnl={r['pnl']:>+8.0f}")

    print(f"\n  Total configs tested: {len(results)}")
    print(f"\n  YOUR CURRENT CONFIG: V2, top-7, TP=0.3285+scaling, WATCHLIST")
    # Find your current config in results
    current = next((r for r in results if 'V2 (current)' in r['label']), None)
    if current:
        your_rank = ranked.index(current) + 1
        print(f"  YOUR RANK: #{your_rank} of {len(results)}")

if __name__ == '__main__':
    main()
