#!/bin/bash
# Run pending migrations against ClickHouse
# Usage: ./migrate.sh [clickhouse_url]
# Default URL: http://localhost:8123

CH_URL="${1:-http://localhost:8123}"
DB="trading"

echo "Running migrations against $CH_URL..."

run_sql() {
  local sql="$1"
  local result
  result=$(curl -s -X POST "$CH_URL" \
    -H "Content-Type: text/plain" \
    --data-binary "$sql")
  if echo "$result" | grep -qi "exception\|error"; then
    echo "  ❌ FAILED: $sql"
    echo "  $result"
    return 1
  else
    echo "  ✅ OK"
  fi
}

echo ""
echo "=== Migration: volume_group_state table ==="
run_sql "CREATE TABLE IF NOT EXISTS trading.volume_group_state (
    group_name   String,
    enabled      UInt8 DEFAULT 1,
    inserted_at  DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY group_name"

echo ""
echo "=== Seeding volume groups (idempotent) ==="
run_sql "INSERT INTO trading.volume_group_state (group_name, enabled)
SELECT * FROM (SELECT 'MEGA' AS group_name, 1 AS enabled UNION ALL SELECT 'LARGE', 1 UNION ALL SELECT 'MID', 0 UNION ALL SELECT 'SMALL', 0)
WHERE group_name NOT IN (SELECT group_name FROM trading.volume_group_state FINAL)"

echo ""
echo "=== Verifying ==="
result=$(curl -s -X POST "$CH_URL" --data-binary "SELECT group_name, enabled FROM trading.volume_group_state FINAL ORDER BY group_name FORMAT PrettyCompact")
echo "$result"

echo ""
echo "Done."
