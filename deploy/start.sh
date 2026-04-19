#!/bin/bash
# Start dhan-trader (build + deploy)
set -e
cd "$(dirname "$0")/.."

echo "=== Building containers ==="
docker compose build

echo "=== Starting services ==="
docker compose up -d

echo "=== Waiting for ClickHouse ==="
for i in $(seq 1 30); do
  if docker compose exec clickhouse wget -qO- http://127.0.0.1:8123/ping 2>/dev/null | grep -q "Ok"; then
    echo "ClickHouse healthy"
    break
  fi
  echo "  waiting... ($i)"
  sleep 2
done

echo "=== Waiting for Engine ==="
for i in $(seq 1 30); do
  if curl -sf http://localhost:8080/api/status >/dev/null 2>&1; then
    echo "Engine healthy"
    break
  fi
  echo "  waiting... ($i)"
  sleep 3
done

echo "=== Service Status ==="
docker compose ps
echo ""
echo "=== Engine Logs ==="
docker compose logs engine --tail 10
echo ""
echo "UI:     http://$(curl -s ifconfig.me 2>/dev/null || echo 'localhost'):3000"
echo "API:    http://localhost:8080/api/status"
echo ""
echo "NEXT: Run 'bash deploy/test.sh' to verify everything works"
