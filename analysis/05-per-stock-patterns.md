# Per-Stock Pattern Analysis — 206 F&O Stocks, 59 Trading Days

## Key Discovery: SELL Continuation Rate is the #1 Per-Stock Predictor

When a stock drops in the first 4 minutes, how often does it KEEP dropping?

### Tier Classification

| Tier | Continuation % | Stocks | Avg SELL Return | Avg Daily Vol |
|------|---------------|--------|-----------------|---------------|
| MOMENTUM (>=75%) | 75-90% | 23 | +0.50% | 81k (LOW) |
| GOOD (60-75%) | 60-74% | 83 | +0.30% | 171k |
| AVERAGE (50-60%) | 50-59% | 62 | +0.13% | 585k (HIGH) |
| REVERTER (<50%) | 29-49% | 38 | -0.03% | 207k |

**Low daily volume = momentum stock. High volume = institutional/mean-reverting.**

### TOP 23 Momentum Stocks (SELL continuation >= 75%)

```
AMBER(90%), PIIND(88%), TECHM(83%), INDIANB(82%), BDL(82%),
GODREJCP(80%), FEDERALBNK(80%), AMBUJACEM(80%), MAXHEALTH(80%),
GLENMARK(79%), SWIGGY(79%), DELHIVERY(79%), BANKBARODA(79%), RBLBANK(79%),
BOSCHLTD(78%), GODREJPROP(78%), COLPAL(78%), KPITTECH(78%),
HUDCO(77%), LTM(77%), MAZDOCK(77%), SUPREMEIND(77%), COFORGE(75%)
```

### 38 Mean-Reverter Stocks (NEVER SHORT these — SELL continuation < 50%)

```
AUROPHARMA(29%), COALINDIA(32%), NTPC(33%), LT(35%), MANAPPURAM(36%),
JSWSTEEL(37%), PREMIERENE(38%), BLUESTARCO(38%), JIOFIN(39%),
TATASTEEL(40%), APOLLOHOSP(40%), RELIANCE(41%), BPCL(41%), GRASIM(41%),
HDFCAMC(41%), POWERINDIA(41%), TATAPOWER(42%), ICICIBANK(42%),
ICICIPRULI(42%), ASIANPAINT(44%), LICI(43%), ONGC(44%), SHRIRAMFIN(44%),
INDHOTEL(44%), MOTHERSON(45%), DRREDDY(46%), VEDL(47%), SBIN(47%),
BHARTIARTL(47%), ADANIPORTS(47%), KOTAKBANK(48%), DMART(47%),
UNIONBANK(47%), POLICYBZR(47%), PHOENIXLTD(48%), VBL(48%),
NUVAMA(48%), CUMMINSIND(48%)
```

### Per-Stock Patterns Worth Implementing

**1. Gap-up SELL win rate (stocks that reverse after gapping up):**
- AMBER: 100% win (9 signals)
- INDIANB: 100% (3 signals)
- SWIGGY: 100% (5 signals)
- FEDERALBNK: 100% (1 signal)
- MAXHEALTH: 100% (3 signals)

**2. High vol_rate improves SELL for some stocks:**
- COFORGE: 1.989% return with high vr vs 0.340% without
- KPITTECH: 1.888% vs 0.644%
- MCX: 2.084% vs -0.014% (ONLY trade MCX with high volume!)
- PGEL: 2.543% vs 0.024%

**3. Some stocks work better in first half (exit b30) vs full hour (exit b71):**
- BDL: 0.705% at b30, 1.073% at b71 — keeps trending
- GLENMARK: 0.545% at b30, 0.772% at b71 — keeps trending
- DELHIVERY: -0.045% at b30, 0.353% at b71 — SLOW starter, needs time

**4. Stocks where SELL is ONLY profitable with high vol_rate:**
- AMBUJACEM: 1.169% with high vr vs 0.311% without
- FEDERALBNK: 0.84% vs 0.169%
- NAUKRI: 1.713% vs 0.131%
- RECLTD: 1.03% vs 0.081%
