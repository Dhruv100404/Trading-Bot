#!/usr/bin/env python3
"""Backfill daily_ref on EC2 from Dhan Historical API. Run on EC2 directly."""
import requests, time, datetime, sys

CH = 'http://localhost:8123'
CID = '1100896497'

def cq(sql):
    r = requests.post(CH, data=sql, timeout=60)
    r.raise_for_status()
    return r.text

# Get token from DB
TOKEN = cq("SELECT value FROM trading.system_settings FINAL WHERE key='market_data_token' FORMAT TabSeparated").strip()
print(f"Token len: {len(TOKEN)}")

# Get symbols
syms = []
for l in cq("SELECT security_id, symbol FROM trading.watchlist FINAL WHERE enabled=1 FORMAT TabSeparated").strip().split('\n'):
    parts = l.split('\t')
    if len(parts) == 2:
        syms.append((parts[0], parts[1]))
print(f"Symbols: {len(syms)}")

count = 0
failed = 0
batch = []

for i in range(0, len(syms), 4):
    chunk = syms[i:i+4]
    for sid, sym in chunk:
        ok = False
        for attempt in range(3):
            try:
                r = requests.post(
                    'https://api.dhan.co/v2/charts/historical',
                    headers={'Content-Type': 'application/json', 'access-token': TOKEN, 'client-id': CID},
                    json={'securityId': sid, 'exchangeSegment': 'NSE_EQ', 'instrument': 'EQUITY',
                          'expiryCode': 0, 'fromDate': '2026-03-20', 'toDate': '2026-03-30'},
                    timeout=15
                )
                if r.status_code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    break
                d = r.json()
                if not d.get('open'):
                    break
                pc, ph, pl = 0, 0, 0
                for j in range(len(d['open'])):
                    o, h, l, c, t = d['open'][j], d['high'][j], d['low'][j], d['close'][j], d['timestamp'][j]
                    ist = datetime.datetime.utcfromtimestamp(t + 5 * 3600 + 1800)
                    dt = ist.strftime('%Y-%m-%d')
                    gap = round((o - pc) / pc * 100, 2) if pc > 0 else 0
                    batch.append(f"('{dt}','{sym}','{sid}',{pc},{o},{o},{gap},{ph},{pl},{c})")
                    pc, ph, pl = c, h, l
                count += 1
                ok = True
                break
            except Exception as e:
                time.sleep(2)
        if not ok:
            failed += 1

    if len(batch) >= 500:
        cq("INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES " + ",".join(batch))
        batch = []

    time.sleep(1)
    if count % 100 == 0 and count > 0:
        print(f"  {count}/{len(syms)}...", flush=True)

if batch:
    cq("INSERT INTO trading.daily_ref (trading_date,symbol,security_id,prev_close,pre_open_price,day_open,gap_pct,prev_day_high,prev_day_low,closing_price) VALUES " + ",".join(batch))

print(f"\nDone: {count} ok, {failed} failed")
v = cq("SELECT trading_date, count(), countIf(day_open>0), countIf(prev_close>0), countIf(closing_price>0) FROM trading.daily_ref FINAL WHERE trading_date >= '2026-03-24' GROUP BY trading_date ORDER BY trading_date FORMAT TabSeparated")
print(v)
