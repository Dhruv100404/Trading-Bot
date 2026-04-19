"""
P5: First Candle Shape Analysis
=================================
Does the shape of the first 1-3 candles predict reversal?
Doji, hammer, engulfing, etc. — all from OHLC.
"""
import json, numpy as np, time
from pathlib import Path

DATA_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\data')
OUT = DATA_DIR / 'p5_candle_shape.txt'
O,H,L,C,V,VW,BR = 0,1,2,3,4,5,6

def main():
    t0 = time.time()
    liquid = set(json.loads((DATA_DIR/'liquid-5l-symbols.json').read_text()))
    files = [DATA_DIR/'candles-consolidated.ndjson', DATA_DIR/'candles-consolidated_new.ndjson']
    recs = []
    for fp in files:
        with open(fp) as f:
            for line in f:
                r = json.loads(line)
                if r['symbol'] not in liquid or r['gapPct'] <= 0.5: continue
                bkts = r['buckets']
                nb = min(len(bkts),100)
                bkt = np.zeros((100,7),dtype=np.float32)
                for j in range(nb):
                    b=bkts[j]
                    bkt[j,O]=b['o'];bkt[j,H]=b['h'];bkt[j,L]=b['l'];bkt[j,C]=b['c']
                    bkt[j,V]=b['v'];bkt[j,VW]=b.get('vw',b['c']);bkt[j,BR]=b.get('br',0.5)
                entry = bkt[6,O]
                if entry<=0 or bkt[65,C]<=0: continue
                ret = (entry - bkt[65,C])/entry*100 - 0.15

                # b0 candle features
                b0o,b0h,b0l,b0c = bkt[0,O],bkt[0,H],bkt[0,L],bkt[0,C]
                b1o,b1h,b1l,b1c = bkt[1,O],bkt[1,H],bkt[1,L],bkt[1,C]
                b2o,b2h,b2l,b2c = bkt[2,O],bkt[2,H],bkt[2,L],bkt[2,C]
                body0 = abs(b0c-b0o); rng0 = b0h-b0l
                body1 = abs(b1c-b1o); rng1 = b1h-b1l
                upper_wick0 = b0h - max(b0o,b0c)
                lower_wick0 = min(b0o,b0c) - b0l

                recs.append({
                    'gap':r['gapPct'],'ret':ret,'sym':r['symbol'],
                    'b0_red': b0c < b0o,
                    'b0_doji': body0 < rng0*0.1 if rng0>0 else False,  # tiny body
                    'b0_hammer': lower_wick0 > body0*2 and upper_wick0 < body0*0.5 if body0>0 else False,
                    'b0_shooting_star': upper_wick0 > body0*2 and lower_wick0 < body0*0.5 if body0>0 else False,
                    'b0_big_red': b0c < b0o and body0/b0o*100 > 1 if b0o>0 else False,
                    'b0_big_green': b0c > b0o and body0/b0o*100 > 1 if b0o>0 else False,
                    'b1_red': b1c < b1o,
                    'b1_engulf_bear': b1c < b0l and b1o > b0c,  # b1 engulfs b0 bearishly
                    'b0b1_both_red': b0c<b0o and b1c<b1o,
                    'b0b1b2_all_red': b0c<b0o and b1c<b1o and b2c<b2o,
                    'b0_green_b1_red': b0c>b0o and b1c<b1o,  # green then red = top reversal
                    'b0_red_b1_green': b0c<b0o and b1c>b1o,  # red then green = bounce
                    'b0_upper_wick_dom': upper_wick0 > body0 + lower_wick0 if (body0+lower_wick0)>0 else False,
                    'b0_lower_wick_dom': lower_wick0 > body0 + upper_wick0 if (body0+upper_wick0)>0 else False,
                    'b0_body_pct': body0/rng0*100 if rng0>0 else 50,
                    'win': 1 if ret>0 else 0,
                })

    with open(OUT,'w') as out:
        out.write(f"P5: FIRST CANDLE SHAPE ANALYSIS\n")
        out.write(f"Gap-up records (gap>0.5%): {len(recs)}\n\n")

        pats = {
            'b0 RED candle':                    lambda r: r['b0_red'],
            'b0 GREEN candle':                  lambda r: not r['b0_red'],
            'b0 DOJI (body<10% range)':         lambda r: r['b0_doji'],
            'b0 HAMMER (long lower wick)':      lambda r: r['b0_hammer'],
            'b0 SHOOTING STAR (long upper)':    lambda r: r['b0_shooting_star'],
            'b0 BIG RED (body>1%)':             lambda r: r['b0_big_red'],
            'b0 BIG GREEN (body>1%)':           lambda r: r['b0_big_green'],
            'b0 upper wick dominant':            lambda r: r['b0_upper_wick_dom'],
            'b0 lower wick dominant':            lambda r: r['b0_lower_wick_dom'],
            'b0+b1 both RED':                   lambda r: r['b0b1_both_red'],
            'b0+b1+b2 all RED':                 lambda r: r['b0b1b2_all_red'],
            'b0 GREEN then b1 RED (top reversal)':  lambda r: r['b0_green_b1_red'],
            'b0 RED then b1 GREEN (bounce)':    lambda r: r['b0_red_b1_green'],
            'b1 bearish engulfing':              lambda r: r['b1_engulf_bear'],
            'b0 BIG RED + b1 RED':              lambda r: r['b0_big_red'] and r['b1_red'],
            'SHOOTING STAR + b1 RED':           lambda r: r['b0_shooting_star'] and r['b1_red'],
            'b0 GREEN + b1 RED + gap>2%':       lambda r: r['b0_green_b1_red'] and r['gap']>2,
            'b0 BIG RED + gap>2%':              lambda r: r['b0_big_red'] and r['gap']>2,
            'DOJI + gap>2%':                    lambda r: r['b0_doji'] and r['gap']>2,
        }

        out.write(f"  {'Pattern':<50} {'N':>6} {'Win%':>6} {'AvgRet':>8}\n")
        out.write("  "+"-"*75+"\n")
        rows = []
        for name,filt in pats.items():
            m = [r for r in recs if filt(r)]
            if len(m)<20: continue
            wr = sum(r['win'] for r in m)/len(m)*100
            ar = np.mean([r['ret'] for r in m])
            rows.append((ar,name,len(m),wr))
        rows.sort(key=lambda x: -x[0])
        for ar,name,n,wr in rows:
            out.write(f"  {name:<50} {n:>6} {wr:>5.1f}% {ar:>+7.3f}%\n")

    print(f"P5 done in {time.time()-t0:.1f}s -> {OUT}")

if __name__=='__main__': main()
