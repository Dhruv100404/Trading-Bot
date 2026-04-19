#!/bin/bash
# Pre-flight check for live trading
set -e
CQ() { docker compose exec -T clickhouse clickhouse-client --query "$1" 2>/dev/null; }
API="http://localhost:8080/api"
P=0; F=0; W=0
ok() { P=$((P+1)); echo "  ✅ $1"; }
fail() { F=$((F+1)); echo "  ❌ $1"; }
warn() { W=$((W+1)); echo "  ⚠️ $1"; }

echo "============================================================"
echo "  PRE-FLIGHT CHECK"
echo "============================================================"

echo ""
echo "━━━ 1. ENGINE ━━━"
curl -sf $API/status >/dev/null && ok "Engine healthy" || fail "Engine down"
HEAD=$(git rev-parse --short HEAD)
ok "Code: $HEAD"

echo ""
echo "━━━ 2. CONFIG ━━━"
CQ "SELECT 'gap_rev='||toString(gap_reversal_mode)||' cherry='||toString(cherry_pick_enabled)||' smart='||toString(smart_score_mode)||' smc_sell='||toString(smc_trend_sell)||' pos='||toString(max_positions)||' entry='||toString(sell_entry_start)||'-'||toString(sell_entry_end)||' exit='||toString(sell_hard_exit_bucket)||' tp='||toString(sell_tp_pct)||' sl='||toString(sell_sl_pct)||' loss='||toString(max_loss_pct) FROM trading.config FINAL WHERE account_client_id='1100896497' FORMAT TabSeparated"

echo ""
echo "━━━ 3. WATCHLIST ━━━"
EN=$(CQ "SELECT count() FROM trading.watchlist FINAL WHERE enabled=1 FORMAT TabSeparated")
[ "$EN" -gt 1000 ] && ok "Enabled: $EN" || fail "Only $EN"
TIERS=$(CQ "SELECT groupArray(tier_name) FROM trading.tier_state FINAL WHERE enabled=1 FORMAT TabSeparated")
echo "  Active tiers: $TIERS"

echo ""
echo "━━━ 4. TOKEN ━━━"
TLEN=$(CQ "SELECT length(value) FROM trading.system_settings FINAL WHERE key='market_data_token' FORMAT TabSeparated")
[ "$TLEN" -gt 100 ] && ok "Token: $TLEN chars" || fail "Token missing"

echo ""
echo "━━━ 5. DAILY_REF (prev trading day) ━━━"
PREV=$(CQ "SELECT max(trading_date) FROM trading.daily_ref FINAL WHERE day_open > 0 AND trading_date < today() FORMAT TabSeparated")
echo "  Prev trading day: $PREV"
D=$(CQ "SELECT countIf(day_open>0), countIf(prev_close>0), countIf(closing_price>0), countIf(prev_day_high>0) FROM trading.daily_ref FINAL WHERE trading_date='$PREV' FORMAT TabSeparated")
echo "  day_open=$(echo $D|cut -f1) prev_close=$(echo $D|cut -f2) close=$(echo $D|cut -f3) hi=$(echo $D|cut -f4)"
DOPEN=$(echo $D|cut -f1)
[ "$DOPEN" -gt 1000 ] && ok "Prev day has $DOPEN stocks with day_open" || warn "Prev day: $DOPEN day_open"

echo ""
echo "━━━ 6. DIRECTION ━━━"
TODAY_PC=$(CQ "SELECT countIf(prev_close>0) FROM trading.daily_ref FINAL WHERE trading_date=today() FORMAT TabSeparated")
echo "  Today prev_close: $TODAY_PC (seeded at 7AM)"
[ "$TODAY_PC" -gt 1000 ] && ok "Direction: today has $TODAY_PC prev_close" || warn "Direction: $TODAY_PC (7AM seed will fix)"

echo ""
echo "━━━ 7. HISTORICAL API ━━━"
TOKEN=$(CQ "SELECT value FROM trading.system_settings FINAL WHERE key='market_data_token' FORMAT TabSeparated" | tr -d '\r\n')
HIST=$(curl -sf -X POST "https://api.dhan.co/v2/charts/historical" -H "Content-Type: application/json" -H "access-token: $TOKEN" -H "client-id: 1100896497" -d '{"securityId":"1333","exchangeSegment":"NSE_EQ","instrument":"EQUITY","expiryCode":0,"fromDate":"2026-03-25","toDate":"2026-03-31"}' 2>/dev/null | head -c 30)
echo "$HIST" | grep -q "open" && ok "Historical API works" || warn "Historical API: $HIST"

echo ""
echo "━━━ 8. SNAPSHOT FORMAT ━━━"
SCOLS=$(CQ "SELECT count() FROM system.columns WHERE database='trading' AND table='snapshots' FORMAT TabSeparated")
[ "$SCOLS" -lt 20 ] && ok "Snapshot cols: $SCOLS (cleaned)" || warn "Snapshot cols: $SCOLS (extra columns remain)"

echo ""
echo "━━━ 9. WIN RATES ━━━"
WRC=$(CQ "SELECT count() FROM trading.stock_win_rate FINAL FORMAT TabSeparated")
[ "$WRC" -gt 0 ] && ok "Win rates: $WRC stocks" || warn "No win rates (smart_score defaults to 0.5)"

echo ""
echo "━━━ 10. BACKTEST ━━━"
BT=$(curl -sf -X POST "$API/backtest/compute" -H "Content-Type: application/json" -d "$(curl -sf "$API/config?account_id=1100896497" | python3 -c "import json,sys;c=json.load(sys.stdin);c['from']='2026-03-27';c['to']='2026-03-30';c['smart_score_mode']=False;print(json.dumps(c))")" 2>/dev/null)
SIGS=$(echo "$BT" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d.get('signals',[])))" 2>/dev/null || echo 0)
CANDS=$(echo "$BT" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('total_candidates',0))" 2>/dev/null || echo 0)
[ "$SIGS" -gt 0 ] && ok "Backtest: $SIGS signals, $CANDS candidates" || warn "Backtest: $SIGS signals"

echo ""
echo "━━━ 11. HOLIDAY CLEANUP ━━━"
S31=$(CQ "SELECT count() FROM trading.signals FINAL WHERE trading_date='2026-03-31' FORMAT TabSeparated")
[ "$S31" = "0" ] && ok "No Mar 31 signals" || warn "$S31 Mar 31 signals exist"

echo ""
echo "============================================================"
echo "  RESULTS: ✅ $P | ❌ $F | ⚠️ $W"
if [ "$F" -gt 0 ]; then echo "  *** FIX BEFORE GOING LIVE ***"
elif [ "$W" -gt 0 ]; then echo "  Warnings — most resolve at 7 AM seed"
else echo "  ALL CLEAR"; fi
echo "============================================================"
