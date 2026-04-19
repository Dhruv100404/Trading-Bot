import json
from pathlib import Path

DATA = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
margin = json.loads((DATA / 'margin-stocks.json').read_text())
margin_syms = {s['tradingSymbol'] for s in margin['stocks']}

vol_sums = {}
for fp in ['candles-consolidated.ndjson', 'candles-consolidated_new.ndjson']:
    with open(DATA / fp) as f:
        for line in f:
            r = json.loads(line)
            s = r['symbol']
            if s not in margin_syms:
                continue
            v = r.get('f5Vol', 0) * r.get('dayOpen', 0)
            if s not in vol_sums:
                vol_sums[s] = [0, 0]
            vol_sums[s][0] += v
            vol_sums[s][1] += 1

liquid = [s for s, (t, c) in vol_sums.items() if c > 0 and t / c >= 500_000]
liquid.sort()
(DATA / 'liquid-5l-symbols.json').write_text(json.dumps(liquid))
print(f'Done: {len(liquid)} symbols with avg vol >= 5L')
