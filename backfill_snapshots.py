"""Backfill snapshot buckets 106-375 for recent trading days using Dhan intraday API."""
import requests, subprocess, sys, time

TOKEN = sys.argv[1]
CLIENT_ID = sys.argv[2]
DATES = sys.argv[3].split(",")  # e.g. "2026-04-01,2026-04-02,2026-04-06,2026-04-07"
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json"}
CH_CMD = ["docker", "exec", "-i", "40-minute-auto-trader-clickhouse-1", "clickhouse-client", "--query"]

def ch(q):
    r = subprocess.run(CH_CMD + [q], capture_output=True, text=True)
    return r.stdout.strip()

def ch_insert(data):
    r = subprocess.run(
        ["docker", "exec", "-i", "40-minute-auto-trader-clickhouse-1", "clickhouse-client", "--query",
         "INSERT INTO trading.snapshots (trading_date, symbol, security_id, bucket, ltp, candle_open, candle_high, candle_low, volume_cum, volume_delta, vwap, volume_rate, candle_body_ratio) FORMAT TSV"],
        input=data, capture_output=True, text=True)
    return r.returncode == 0

# Get enabled stocks
rows = ch("SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled=1 FORMAT TSV")
stocks = []
for line in rows.splitlines():
    parts = line.split("\t")
    if len(parts) == 2:
        stocks.append((parts[0], parts[1]))
print("Stocks: %d" % len(stocks))

for date_str in DATES:
    print("\n=== Backfilling %s ===" % date_str)

    # Get existing max bucket per symbol for this date
    existing = ch("SELECT symbol, max(bucket) FROM trading.snapshots WHERE trading_date = toDate('%s') GROUP BY symbol FORMAT TSV" % date_str)
    max_buckets = {}
    for line in existing.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            max_buckets[parts[0]] = int(parts[1])

    # Filter to stocks needing backfill (max_bucket < 345)
    needs_fill = [(sid, sym) for sid, sym in stocks if max_buckets.get(sym, 0) < 345]
    print("Need backfill: %d/%d stocks (max_bucket < 345)" % (len(needs_fill), len(stocks)))

    if not needs_fill:
        print("Skipping - all stocks have bucket >= 345")
        continue

    filled = 0
    failed = 0
    insert_buf = []

    for i, (sec_id, symbol) in enumerate(needs_fill):
        payload = {
            "securityId": sec_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "fromDate": date_str,
            "toDate": date_str,
        }

        ok = False
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)
            try:
                r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=HEADERS, json=payload, timeout=15)
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    continue
                data = r.json()
                opens = data.get("open", [])
                highs = data.get("high", [])
                lows = data.get("low", [])
                closes = data.get("close", [])
                timestamps = data.get("timestamp", [])
                volumes = data.get("volume", [])

                existing_set = set()
                ex_rows = max_buckets.get(symbol, 0)

                for j in range(len(opens)):
                    ts = timestamps[j]
                    # Convert to IST bucket
                    utc_h = (ts // 3600) % 24
                    utc_m = (ts // 60) % 60
                    ist_h = utc_h + 5
                    ist_m = utc_m + 30
                    if ist_m >= 60:
                        ist_h += 1
                        ist_m -= 60
                    ist_h = ist_h % 24
                    total_mins = ist_h * 60 + ist_m
                    open_mins = 9 * 60 + 15
                    if total_mins < open_mins or total_mins >= 15 * 60 + 30:
                        continue
                    bucket = total_mins - open_mins + 1
                    if bucket <= 0 or bucket > 375:
                        continue

                    o = opens[j]
                    h = highs[j]
                    l = lows[j]
                    c = closes[j]
                    v = volumes[j]
                    rng = h - l
                    body_ratio = abs(c - o) / rng if rng > 0 else 0
                    vol_rate = v / 60.0
                    vwap = c  # approximate

                    insert_buf.append("%s\t%s\t%s\t%d\t%f\t%f\t%f\t%f\t%d\t%d\t%f\t%f\t%f" % (
                        date_str, symbol, sec_id, bucket, c, o, h, l, v, v, vwap, vol_rate, body_ratio
                    ))

                filled += 1
                ok = True
                break
            except Exception as e:
                continue

        if not ok:
            failed += 1

        # Flush every 50 stocks
        if len(insert_buf) >= 5000 or (i == len(needs_fill) - 1 and insert_buf):
            if ch_insert("\n".join(insert_buf)):
                pass
            else:
                print("INSERT failed!")
            insert_buf = []

        if (i + 1) % 100 == 0:
            print("  %d/%d done, %d filled, %d failed" % (i + 1, len(needs_fill), filled, failed))

        time.sleep(0.2)

    print("Done %s: %d filled, %d failed" % (date_str, filled, failed))

# Final verify
for date_str in DATES:
    v = ch("SELECT count(DISTINCT symbol), max(bucket) FROM trading.snapshots WHERE trading_date = toDate('%s')" % date_str)
    print("Verify %s: %s" % (date_str, v))
