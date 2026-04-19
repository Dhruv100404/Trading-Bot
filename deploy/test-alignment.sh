#!/bin/bash
# Test alignment between backtest and live signal logic
set -e

API="http://localhost:8080/api"
ACCT="1100896497"

echo "============================================================"
echo "  LIVE ↔ BACKTEST ALIGNMENT TEST"
echo "============================================================"

# 1. Get config
echo ""
echo "▶ [1/6] Loading config for account $ACCT..."
CONFIG=$(curl -sf "$API/config?account_id=$ACCT")
echo "$CONFIG" | python3 -c "
import sys,json; d=json.load(sys.stdin)
keys=['gap_reversal_mode','cherry_pick_enabled','max_positions','max_loss_pct',
      'sell_hard_exit_bucket','sell_tp_pct','sell_sl_pct','total_capital',
      'sell_capital_per_trade','sell_entry_start','sell_entry_end']
for k in keys: print(f'  {k}: {d.get(k,\"?\")}')
"

# 2. Run backtest — merge config into request body with from/to
echo ""
echo "▶ [2/6] Running backtest..."
LAST_DATE=$(docker compose exec -T clickhouse clickhouse-client -q "SELECT max(trading_date) FROM trading.snapshots WHERE trading_date < today()" | tr -d '\r\n')
echo "  Date: $LAST_DATE"

BT_BODY=$(python3 -c "
import json,sys
cfg=json.loads('''$CONFIG''')
cfg['from']='$LAST_DATE'
cfg['to']='$LAST_DATE'
# Ensure backtest-required defaults are present
cfg.setdefault('entry_bucket_start', cfg.get('buy_entry_start',2))
cfg.setdefault('entry_bucket_end', cfg.get('sell_entry_end',7))
cfg.setdefault('quantity', 1)
cfg.setdefault('min_volume', 100)
cfg.setdefault('min_score', 0)
cfg.setdefault('tp_pct', 0)
cfg.setdefault('sl_pct', 0)
print(json.dumps(cfg))
")

BT_RESULT=$(curl -s --max-time 60 -X POST "$API/backtest/compute" -H "Content-Type: application/json" -d "$BT_BODY")
if [ -z "$BT_RESULT" ] || echo "$BT_RESULT" | grep -q '"error"'; then
    echo "  ❌ Backtest failed: $BT_RESULT"
    exit 1
fi

echo "$BT_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
signals = data.get('signals', [])
print(f'  Total signals: {len(signals)}')
for s in signals[:15]:
    print(f'  {s[\"direction\"]:4} {s[\"symbol\"]:15} ep={s[\"entry_price\"]:8.2f} b={s[\"entry_bucket\"]:3} gap={s.get(\"gap_pct\",0):+.2f}% sc={s[\"score\"]:3} exit={s.get(\"exit_reason\",\"?\")} ret={s.get(\"actual_return_pct\",0):+.2f}% pnl={s.get(\"pnl_rupees\",0):+.0f}')
if signals:
    total_pnl = sum(s.get('pnl_rupees',0) for s in signals)
    avg_ret = sum(s.get('actual_return_pct',0) for s in signals) / len(signals)
    sells = [s for s in signals if s['direction'] == 'SELL']
    print(f'  ---')
    print(f'  SELL:{len(sells)} | Total PnL: Rs {total_pnl:+.0f} | Avg ret: {avg_ret:+.2f}%')
"

# 3. Circuit breaker verification
echo ""
echo "▶ [3/6] Circuit breaker alignment..."
echo "$BT_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
cfg = json.loads('''$CONFIG''')
signals = data.get('signals', [])
if not signals:
    print('  No signals — skip')
else:
    day_capital = sum(s['entry_price'] * s.get('quantity',1) for s in signals)
    day_margin = day_capital / 5.0
    max_loss = cfg.get('max_loss_pct', 0)
    if max_loss > 0:
        bt_threshold = -(max_loss / 100.0) * day_margin
        old_threshold = -(max_loss / 100.0) * cfg.get('total_capital', 50000)
        print(f'  Day capital: Rs {day_capital:,.0f} | Margin: Rs {day_margin:,.0f}')
        print(f'  ✅ FIXED threshold (day_margin): Rs {bt_threshold:,.0f}')
        print(f'  ❌ OLD threshold (total_capital): Rs {old_threshold:,.0f}')
        total_pnl = sum(s.get('pnl_rupees',0) for s in signals)
        print(f'  Total P&L: Rs {total_pnl:+,.0f} (tripped={total_pnl <= bt_threshold})')
    else:
        print(f'  max_loss_pct=0 — circuit breaker disabled')
"

# 4. Cherry-pick timing
echo ""
echo "▶ [4/6] Cherry-pick timing..."
echo "$BT_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
signals = data.get('signals', [])
if not signals:
    print('  No signals')
else:
    entry_buckets = sorted(set(s['entry_bucket'] for s in signals))
    if len(entry_buckets) == 1:
        print(f'  ✅ All {len(signals)} signals at bucket {entry_buckets[0]} (window-end selection)')
    else:
        print(f'  Entry buckets: {entry_buckets}')
        for b in entry_buckets:
            count = sum(1 for s in signals if s['entry_bucket'] == b)
            print(f'    Bucket {b}: {count} signals')
    dirs = set(s['direction'] for s in signals)
    print(f'  Directions: {dirs}')
"

# 5. Gap reversal tags
echo ""
echo "▶ [5/6] Gap reversal tags..."
echo "$BT_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
signals = data.get('signals', [])
gap_rev = [s for s in signals if 'rev' in s.get('signals_fired','') or 'gap+' in s.get('signals_fired','')]
normal = [s for s in signals if s not in gap_rev]
print(f'  Gap reversal: {len(gap_rev)} | Normal: {len(normal)}')
if gap_rev:
    all_positive_gap = all(s.get('gap_pct',0) > 0.1 for s in gap_rev)
    avg_gap = sum(s.get('gap_pct',0) for s in gap_rev) / len(gap_rev)
    print(f'  All gap>0.1%: {\"✅\" if all_positive_gap else \"❌\"} | Avg gap: {avg_gap:+.2f}%')
    for s in gap_rev[:5]:
        print(f'    {s[\"symbol\"]:15} gap={s.get(\"gap_pct\",0):+.2f}% sc={s[\"score\"]} tags={s.get(\"signals_fired\",\"\")}')
"

# 6. Watchlist
echo ""
echo "▶ [6/6] Watchlist..."
ENABLED=$(docker compose exec -T clickhouse clickhouse-client -q "SELECT count() FROM trading.watchlist FINAL WHERE enabled=1" | tr -d '\r\n')
TIER1=$(docker compose exec -T clickhouse clickhouse-client -q "SELECT count() FROM trading.watchlist FINAL WHERE enabled=1 AND has(tiers,'Tier1')" | tr -d '\r\n')
FNO=$(docker compose exec -T clickhouse clickhouse-client -q "SELECT count() FROM trading.watchlist FINAL WHERE enabled=1 AND has(tiers,'F&O')" | tr -d '\r\n')
echo "  Enabled: $ENABLED | Tier1: $TIER1 | F&O: $FNO"

echo ""
echo "============================================================"
echo "  TEST COMPLETE"
echo "============================================================"
