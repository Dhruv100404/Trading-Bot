#!/bin/bash
# Test script — run after deploy to verify everything works before live trading
set -e
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
API="http://localhost:8080"

check() {
  local name="$1"
  local result="$2"
  if [ "$result" = "ok" ]; then
    echo "  ✅ $name"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $name: $result"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== DHAN-TRADER PRE-LIVE TEST SUITE ==="
echo ""

# 1. Services running
echo "1. Services"
docker compose ps --format "{{.Name}}: {{.Status}}" | while read line; do echo "  $line"; done
running=$(docker compose ps --format "{{.Status}}" | grep -c "Up" || true)
check "All 3 services running" "$([ "$running" -ge 3 ] && echo ok || echo "only $running up")"

# 2. ClickHouse
echo ""
echo "2. ClickHouse"
ch_ping=$(curl -sf "$API/../8123/ping" 2>/dev/null || curl -sf "http://localhost:8123/ping" 2>/dev/null || echo "fail")
check "ClickHouse ping" "$([ "$ch_ping" = "Ok." ] && echo ok || echo "$ch_ping")"

# 3. Engine API
echo ""
echo "3. Engine API"
status=$(curl -sf "$API/api/status" 2>/dev/null)
check "API /status" "$([ -n "$status" ] && echo ok || echo "no response")"

# 4. System Settings (market data token)
echo ""
echo "4. Market Data Token"
settings=$(curl -sf "$API/api/settings" 2>/dev/null)
token_masked=$(echo "$settings" | grep -o '"market_data_token_masked":"[^"]*"' | cut -d'"' -f4)
check "Market data token set" "$([ -n "$token_masked" ] && [ "$token_masked" != "NOT SET" ] && echo ok || echo "NOT SET — go to Accounts UI and paste token")"
echo "  Token: $token_masked"

# 5. Accounts
echo ""
echo "5. Accounts"
accounts=$(curl -sf "$API/api/accounts" 2>/dev/null)
acc_count=$(echo "$accounts" | grep -o '"client_id"' | wc -l)
check "Accounts configured" "$([ "$acc_count" -gt 0 ] && echo ok || echo "no accounts — add in UI")"
echo "  Count: $acc_count"

# 6. Per-account config
echo ""
echo "6. Per-Account Config"
for cid in $(echo "$accounts" | grep -o '"client_id":"[^"]*"' | cut -d'"' -f4); do
  cfg=$(curl -sf "$API/api/config?account_id=$cid" 2>/dev/null)
  dir=$(echo "$cfg" | grep -o '"direction_filter":"[^"]*"' | cut -d'"' -f4)
  buy_entry=$(echo "$cfg" | grep -o '"buy_entry_start":[0-9]*' | cut -d: -f2)
  sell_exit=$(echo "$cfg" | grep -o '"sell_hard_exit_bucket":[0-9]*' | cut -d: -f2)
  check "Config for $cid" "$([ -n "$dir" ] && echo ok || echo "missing")"
  echo "    direction=$dir buy_entry=$buy_entry sell_exit=$sell_exit"
done

# 7. Watchlist
echo ""
echo "7. Watchlist"
wl_active=$(docker compose exec clickhouse clickhouse-client --query "SELECT count() FROM trading.watchlist FINAL WHERE enabled = 1" 2>/dev/null)
check "Active watchlist stocks" "$([ "${wl_active:-0}" -gt 0 ] && echo ok || echo "0 stocks")"
echo "  Active: $wl_active"

# 8. Tier state
echo ""
echo "8. Tiers"
fno=$(docker compose exec clickhouse clickhouse-client --query "SELECT enabled FROM trading.tier_state FINAL WHERE tier_name = 'F&O'" 2>/dev/null)
check "F&O tier enabled" "$([ "${fno:-0}" = "1" ] && echo ok || echo "disabled")"

# 9. Test Dhan API connectivity (market data)
echo ""
echo "9. Dhan API Connectivity"
md_token=$(docker compose exec clickhouse clickhouse-client --query "SELECT value FROM trading.system_settings FINAL WHERE key = 'market_data_token'" 2>/dev/null)
md_cid=$(docker compose exec clickhouse clickhouse-client --query "SELECT value FROM trading.system_settings FINAL WHERE key = 'market_data_client_id'" 2>/dev/null)
if [ -n "$md_token" ]; then
  quote_test=$(curl -sf "https://api.dhan.co/v2/marketfeed/quote" \
    -H "access-token: $md_token" \
    -H "client-id: $md_cid" \
    -H "Content-Type: application/json" \
    -d '{"NSE_EQ":[1333]}' 2>/dev/null | head -c 100)
  check "Dhan Quote API" "$(echo "$quote_test" | grep -q "success\|last_price" && echo ok || echo "failed: $quote_test")"
else
  check "Dhan Quote API" "no token set"
fi

# 10. Test Dhan order API connectivity (account token)
echo ""
echo "10. Dhan Account API"
for cid in $(echo "$accounts" | grep -o '"client_id":"[^"]*"' | cut -d'"' -f4); do
  acc_token=$(docker compose exec clickhouse clickhouse-client --query "SELECT access_token FROM trading.accounts FINAL WHERE client_id = '$cid'" 2>/dev/null)
  if [ -n "$acc_token" ]; then
    pos_test=$(curl -sf "https://api.dhan.co/v2/positions" \
      -H "access-token: $acc_token" \
      -H "client-id: $cid" 2>/dev/null | head -c 50)
    check "Account $cid Dhan API" "$(echo "$pos_test" | grep -q "tradingSymbol\|\[\]" && echo ok || echo "failed")"
  fi
done

# 11. Engine poller status
echo ""
echo "11. Engine Logs"
last_log=$(docker compose logs engine --tail 5 2>/dev/null | tail -1)
check "Engine running" "$(echo "$last_log" | grep -qi "poller\|INFO\|poll_done" && echo ok || echo "check logs")"

echo ""
echo "============================================"
echo "RESULTS: $PASS passed, $FAIL failed"
echo "============================================"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "⚠️  FIX the failures above before going live!"
  exit 1
else
  echo ""
  echo "✅ ALL CHECKS PASSED — Ready for live trading!"
fi
