#!/bin/bash
# WebSocket Integration Test — run after deploy to verify WS feed works
# Tests: connection, subscription, tick parsing, depth parsing, OI feed, signal flow
set -e

API="http://localhost:8080"
PASS=0
FAIL=0

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

echo "=== WEBSOCKET INTEGRATION TEST ==="
echo ""

# 1. Get market data token
echo "1. Token"
TOKEN=$(docker compose exec -T clickhouse clickhouse-client --query "SELECT value FROM trading.system_settings FINAL WHERE key='market_data_token'" 2>/dev/null || echo "")
CID=$(docker compose exec -T clickhouse clickhouse-client --query "SELECT value FROM trading.system_settings FINAL WHERE key='market_data_client_id'" 2>/dev/null || echo "")
check "Market data token exists" "$([ ${#TOKEN} -gt 50 ] && echo ok || echo "missing or too short: ${#TOKEN} chars")"
check "Client ID exists" "$([ ${#CID} -gt 5 ] && echo ok || echo "missing: $CID")"

# 2. Test WebSocket connection
echo ""
echo "2. WebSocket Connection"
WS_TEST=$(timeout 10 bun -e "
const ws = new WebSocket('wss://api-feed.dhan.co?version=2&token=${TOKEN}&clientId=${CID}&authType=2');
ws.binaryType = 'arraybuffer';
ws.onopen = () => { console.log('CONNECTED'); ws.close(); process.exit(0); };
ws.onerror = (e) => { console.log('ERROR:' + (e.message||'')); process.exit(1); };
setTimeout(() => { console.log('TIMEOUT'); process.exit(1); }, 8000);
" 2>&1 || echo "FAILED")
check "WebSocket connects to Dhan" "$(echo "$WS_TEST" | grep -q CONNECTED && echo ok || echo "$WS_TEST")"

# 3. Test tick reception + parsing
echo ""
echo "3. Tick Reception & Parsing"
TICK_TEST=$(timeout 15 bun -e "
const ws = new WebSocket('wss://api-feed.dhan.co?version=2&token=${TOKEN}&clientId=${CID}&authType=2');
ws.binaryType = 'arraybuffer';
let ticks = 0, parsed = 0, depth_ok = 0, oi_ticks = 0;
const results = { types: {} };

ws.onopen = () => {
  // Subscribe 3 equity stocks (Full mode)
  ws.send(JSON.stringify({
    RequestCode: 21, InstrumentCount: 3,
    InstrumentList: [
      { ExchangeSegment: 'NSE_EQ', SecurityId: '1333' },
      { ExchangeSegment: 'NSE_EQ', SecurityId: '2885' },
      { ExchangeSegment: 'NSE_EQ', SecurityId: '11536' },
    ]
  }));
  // Subscribe 1 FNO futures (OI mode)
  ws.send(JSON.stringify({
    RequestCode: 19, InstrumentCount: 1,
    InstrumentList: [{ ExchangeSegment: 'NSE_FNO', SecurityId: '52023' }]
  }));

  setTimeout(() => {
    console.log(JSON.stringify({
      ticks, parsed, depth_ok, oi_ticks,
      types: results.types,
      status: ticks > 0 ? 'OK' : 'NO_TICKS'
    }));
    ws.close();
    process.exit(0);
  }, 10000);
};

ws.onmessage = (event) => {
  const buf = Buffer.from(event.data);
  if (buf.length < 8) return;
  ticks++;
  const type = buf.readUInt8(0);
  results.types[type] = (results.types[type] || 0) + 1;

  if (type === 8 && buf.length === 162) {
    const secId = buf.readUInt32LE(4);
    const ltp = buf.readFloatLE(8);
    if (ltp > 0 && ltp < 100000) parsed++;

    // Check depth parsing
    const bidPrice = buf.readFloatLE(68); // offset 62+6 = bid L1 price
    const askPrice = buf.readFloatLE(72); // offset 62+10 = ask L1 price
    if (bidPrice > 0 && askPrice > 0 && askPrice > bidPrice) depth_ok++;
  }

  if (type === 5 && buf.length === 12) {
    const oi = buf.readUInt32LE(8);
    if (oi > 0) oi_ticks++;
  }
};

ws.onerror = () => { console.log('{\"status\":\"WS_ERROR\"}'); process.exit(1); };
" 2>&1 || echo '{"status":"TIMEOUT"}')

TICK_STATUS=$(echo "$TICK_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(d.status||'UNKNOWN')" 2>/dev/null || echo "PARSE_ERROR")
TICK_COUNT=$(echo "$TICK_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(String(d.ticks||0))" 2>/dev/null || echo "0")
PARSED_COUNT=$(echo "$TICK_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(String(d.parsed||0))" 2>/dev/null || echo "0")
DEPTH_COUNT=$(echo "$TICK_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(String(d.depth_ok||0))" 2>/dev/null || echo "0")
OI_COUNT=$(echo "$TICK_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(String(d.oi_ticks||0))" 2>/dev/null || echo "0")

check "Receives ticks ($TICK_COUNT in 10s)" "$([ "$TICK_COUNT" -gt 0 ] 2>/dev/null && echo ok || echo "no ticks — market may be closed")"
check "LTP parsing correct ($PARSED_COUNT parsed)" "$([ "$PARSED_COUNT" -gt 0 ] 2>/dev/null && echo ok || echo "0 parsed")"
check "Depth parsing correct ($DEPTH_COUNT with valid bid/ask)" "$([ "$DEPTH_COUNT" -gt 0 ] 2>/dev/null && echo ok || echo "0 — check depth offsets")"
check "OI ticks from FNO ($OI_COUNT)" "$([ "$OI_COUNT" -gt 0 ] 2>/dev/null && echo ok || echo "0 — may need market hours")"

# 4. Test LTP matches REST API
echo ""
echo "4. Data Accuracy (WS vs REST)"
ACCURACY_TEST=$(timeout 15 bun -e "
const TOKEN='${TOKEN}', CID='${CID}';
const ws = new WebSocket('wss://api-feed.dhan.co?version=2&token='+TOKEN+'&clientId='+CID+'&authType=2');
ws.binaryType = 'arraybuffer';
let wsLtp = {};

ws.onopen = () => {
  ws.send(JSON.stringify({
    RequestCode: 17, InstrumentCount: 1,
    InstrumentList: [{ ExchangeSegment: 'NSE_EQ', SecurityId: '1333' }]
  }));

  setTimeout(async () => {
    // Get REST API LTP
    const resp = await fetch('https://api.dhan.co/v2/marketfeed/ltp', {
      method: 'POST',
      headers: { 'access-token': TOKEN, 'client-id': CID, 'Content-Type': 'application/json' },
      body: JSON.stringify({NSE_EQ:[1333]})
    });
    const api = await resp.json();
    const restLtp = api.data?.NSE_EQ?.['1333']?.last_price || 0;
    const diff = Math.abs((wsLtp['1333'] || 0) - restLtp);
    console.log(JSON.stringify({ wsLtp: wsLtp['1333']||0, restLtp, diff, match: diff < 1.0 }));
    ws.close();
    process.exit(0);
  }, 5000);
};

ws.onmessage = (event) => {
  const buf = Buffer.from(event.data);
  if (buf.readUInt8(0) === 4 && buf.length >= 12) {
    const secId = buf.readUInt32LE(4);
    const ltp = buf.readFloatLE(8);
    if (secId === 1333 && ltp > 0) wsLtp['1333'] = ltp;
  }
};
ws.onerror = () => { console.log('{\"match\":false,\"error\":\"ws_error\"}'); process.exit(1); };
" 2>&1 || echo '{"match":false,"error":"timeout"}')

LTP_MATCH=$(echo "$ACCURACY_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(d.match?'ok':'diff='+d.diff)" 2>/dev/null || echo "parse_error")
check "WS LTP matches REST API" "$LTP_MATCH"

# 5. Test futures mapping
echo ""
echo "5. Futures OI Mapping"
FUT_TEST=$(timeout 20 bun -e "
const resp = await fetch('https://images.dhan.co/api-data/api-scrip-master.csv');
const text = await resp.text();
let count = 0;
const today = new Date().toISOString().split('T')[0];
for (const line of text.split('\n').slice(1)) {
  const cols = line.split(',');
  if (cols.length < 9) continue;
  if (cols[0] === 'NSE' && cols[3] === 'FUTSTK' && cols[8] >= today) count++;
}
console.log(JSON.stringify({ futures_found: count }));
" 2>&1 || echo '{"futures_found":0}')

FUT_COUNT=$(echo "$FUT_TEST" | bun -e "const d=JSON.parse(await Bun.stdin.text());process.stdout.write(String(d.futures_found||0))" 2>/dev/null || echo "0")
check "Scrip master has futures ($FUT_COUNT active)" "$([ "$FUT_COUNT" -gt 100 ] 2>/dev/null && echo ok || echo "only $FUT_COUNT")"

# 6. Test engine WebSocket logs
echo ""
echo "6. Engine WebSocket Status"
WS_LOG=$(docker compose logs engine --tail 50 2>/dev/null | grep -i "\[WS\]" | tail -5)
if [ -n "$WS_LOG" ]; then
  echo "$WS_LOG" | while read line; do echo "  $line"; done
  WS_CONNECTED=$(echo "$WS_LOG" | grep -c "Connected\|Subscriptions sent" || true)
  check "Engine WS connected" "$([ "$WS_CONNECTED" -gt 0 ] && echo ok || echo "no connection logs")"
else
  check "Engine WS connected" "no WS logs yet — will connect at 9:14 IST"
fi

# 7. Test snapshot enrichment (depth + OI populated)
echo ""
echo "7. Snapshot Enrichment"
SNAP_DATA=$(docker compose exec -T clickhouse clickhouse-client --query "
  SELECT
    countIf(bid > 0) as has_bid,
    countIf(ask > 0) as has_ask,
    countIf(bid_qty > 0) as has_bid_qty,
    countIf(oi_total > 0) as has_oi,
    count() as total
  FROM trading.snapshots
  WHERE trading_date = today() AND bucket > 0
" 2>/dev/null || echo "0	0	0	0	0")

HAS_BID=$(echo "$SNAP_DATA" | awk '{print $1}')
HAS_OI=$(echo "$SNAP_DATA" | awk '{print $4}')
TOTAL=$(echo "$SNAP_DATA" | awk '{print $5}')

if [ "${TOTAL:-0}" -gt 0 ]; then
  check "Snapshots have bid/ask data ($HAS_BID/$TOTAL)" "$([ "${HAS_BID:-0}" -gt 0 ] && echo ok || echo "all zeros — WS not enriching yet")"
  check "Snapshots have OI data ($HAS_OI/$TOTAL)" "$([ "${HAS_OI:-0}" -gt 0 ] && echo ok || echo "all zeros — FNO OI not flowing yet")"
else
  check "Snapshots exist today" "no data yet — run during market hours"
fi

echo ""
echo "============================================"
echo "RESULTS: $PASS passed, $FAIL failed"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Notes:"
  echo "  - Tick/OI tests require market hours (9:15-15:30 IST)"
  echo "  - Depth/OI enrichment in snapshots requires at least 1 poll cycle (60s)"
  echo "  - Engine WS connects at 9:14 IST, not before"
fi
