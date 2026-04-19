# Brainstorm: Strategy Improvements & New Ideas

**Date:** 2026-04-01  
**Status:** Ideas only — needs data analysis before implementation  
**Data available:** 1268 liquid stocks, 78 days (Dec 2025 - Mar 2026), 375 buckets/day (9:15 AM - 3:30 PM)

---

## A. ALL-TIME MARKET ENTRY (not just first 6 minutes)

### A1. Continuous scanning — enter when pattern aligns at ANY time
- Current: locked into 9:15-9:20 window, entry at 9:21
- Idea: scan every bucket from 9:15 to 3:15, enter when conditions match
- Different time periods have different patterns:
  - 9:15-9:30: gap reversal (current strategy)
  - 9:30-10:00: ORB breakout / failed breakout
  - 10:00-11:00: VWAP reversion
  - 11:00-1:00: lunch fade (low volume mean reversion)
  - 2:00-3:30: power hour momentum
- Each time window may need its OWN set of features and scoring
- **Analysis needed:** For each 30-min window across the day, what patterns predict movement?
  - Bucket-by-bucket: at bucket N, using data from N-10 to N, what predicts the next 30-60 min?
  - This is a massive analysis: 375 buckets × 1268 stocks × 78 days

### A2. Pattern-triggered entry
- Don't scan continuously — define specific trigger patterns
- Triggers could be:
  - VWAP cross (price crosses below VWAP = sell trigger, above = buy trigger)
  - Volume spike (3x normal volume in a single bucket = something happening)
  - Range breakout (price breaks above/below the last-30-min high/low)
  - Momentum shift (3 consecutive red/green candles after a period of the opposite)
- Each trigger fires independently, system evaluates and enters if score is high enough
- **Analysis needed:** For each trigger type, what's the win rate and optimal exit?

### A3. Time-of-day feature importance
- Features that matter at 9:20 might NOT matter at 11:00
- At open: gap size, sell pressure, momentum dominate
- Mid-morning: VWAP deviation, volume profile might dominate
- Afternoon: trend strength, support/resistance levels might dominate
- **Analysis needed:** Feature importance analysis per time window

---

## B. DYNAMIC EXIT (system detects when to exit)

### B1. VWAP-based logical stop loss
- Current: no SL, pure time exit at b90 (10:44 AM)
- Idea: if stock crosses ABOVE VWAP after entry → thesis broken → exit immediately
- Not a fixed % stop — a LOGICAL stop based on market structure
- "I sold because sellers were winning. If price goes above VWAP, buyers won. I'm wrong."
- **Analysis needed:** For past trades, what happened after VWAP cross? Did exits improve?

### B2. Momentum reversal detection (exit when reversal stalls)
- After entry, monitor the ongoing price action
- Exit signals:
  - 3 consecutive green candles after the initial drop (buyers coming back)
  - Volume spike on an up-candle (institutional buying)
  - Price recovers 50% of the drop from entry (reversal is fading)
- **Analysis needed:** For winning trades that later became losers, what was the earliest warning sign?

### B3. Adaptive exit based on ENTRY features
- Mega analysis showed: stocks with different profiles peak at different times
- High sell_pressure stocks: reversal is fast, exit at b45-b60
- Low sell_pressure stocks: reversal is slow/weak, exit at b30 (take what you can)
- Idea: at entry time, compute expected_exit_bucket from the entry features
- **Analysis needed:** For each feature bucket (sell_pressure, momentum, gap), what's the optimal exit?

### B4. Trailing stop loss (multiple approaches)
- Current: no SL at all — pure time exit. Losers bleed full duration.

**B4a. Fixed trailing stop:**
- Once trade reaches +0.5% profit, set stop at +0.1% (lock in some profit)
- As price moves further in our favor, trail the stop behind by 0.4%
- Example: entry 100, price drops to 99.2 (+0.8% for sell), stop trails at 99.6 (+0.4%)
- If price bounces back to 99.6 → exit with +0.4% locked

**B4b. ATR-based trailing stop:**
- Compute Average True Range of last 10 buckets at entry time
- Set trailing distance = 1.5 × ATR (adapts to stock's volatility)
- Volatile stock (ATR=0.5%): trail at 0.75% behind
- Calm stock (ATR=0.15%): trail at 0.22% behind — tighter stop, less giveback

**B4c. Stepped trailing stop:**
- +0.3% profit → activate stop at breakeven (0%)
- +0.5% profit → move stop to +0.2%
- +1.0% profit → move stop to +0.5%
- +1.5% profit → move stop to +1.0%
- Never gives back more than 0.5% from peak profit

**B4d. Time-decaying trailing stop:**
- Early in trade (b7-b30): wide trailing (0.5%), give it room to develop
- Mid trade (b30-b60): medium trailing (0.3%), tighten up
- Late in trade (b60-b90): tight trailing (0.15%), protect what you have
- Rationale: most of the reversal move happens early, later = diminishing returns

**B4e. Trailing stop only for winners (hybrid):**
- If trade is profitable at bucket 30 → activate trailing stop
- If trade is losing at bucket 30 → keep time exit (don't stop out early, reversal might still come)
- This protects winners without prematurely killing slow reversals

- **Analysis needed for ALL B4 variants:**
  - Minute-by-minute price path for all trades
  - After reaching +X%, what % of trades come back to +Y%?
  - Optimal trailing distance per gap size and sell_pressure
  - Compare: trailing stop total return vs fixed time exit total return
  - Critical question: does trailing stop IMPROVE total return, or just improve win rate at cost of avg win size?

### B5. Real-time feature monitoring for exit
- Every 5 minutes after entry, recompute sell_pressure and momentum
- If sell_pressure drops (buyers returning) → exit
- If momentum turns positive (stock going back up) → exit
- This turns exit from a TIMER into a DETECTOR
- **Analysis needed:** How do real-time features evolve during winning vs losing trades?

---

## C. BUY SIDE PATTERNS (gap-down reversal + beyond)

### C1. Deep BUY analysis on existing 4-month data
- We have 1268 stocks × 78 days × full day data
- Gap-DOWN stocks exist in this data — we just haven't analyzed them deeply
- Even in a bearish market, SOME gap-down stocks DO bounce
- Question: which ones? What features distinguish gap-down bouncers from gap-down fallers?
- The answer might be: buy_pressure (close near high), volume surge on first green candle
- **Analysis needed:** Mirror the entire sell analysis for buy side:
  - Per-stock BUY win rate
  - BUY scoring formulas (gap_down * buy_pressure * momentum_up)
  - BUY reject conditions
  - Optimal BUY entry bucket and exit bucket
  - BUY MUST-PICK conditions (when does BUY win 70%+?)
  - The BUY edge will be smaller but might still be profitable for specific conditions

### C2. BUY on intraday dips (not just gap-down)
- Stock doesn't need to gap down — it can drop 2% during the day from its open
- At any point during the day, if a stock drops X% from its VWAP/open → buy the dip
- This is a different signal from gap reversal — it's intraday mean reversion
- Works on strong stocks that temporarily dip (institutional buying on dips)
- **Analysis needed:** When a stock drops X% from day open, what's the probability of bouncing back Y%?

### C3. BUY after gap-up reversal completes
- After a gap-up stock reverses and hits bottom → BUY for the bounce back
- The same stocks we SHORT at 9:20, we could BUY at 10:30 after they've dropped
- This is the "second leg" — the oversold bounce
- **Analysis needed:** After gap-up stocks hit MFE (lowest point), do they bounce? How much? How reliably?

### C4. Sector rotation BUY
- When one sector drops, another sector rises (money flows between sectors)
- BUY the strongest stock in the sector that's receiving flows
- Need sector data for this
- **Analysis needed:** Requires sector tagging first (G3)

---

## D. FULL-DAY STRATEGY IDEAS

### D1. Opening Range Breakout (ORB)
- Define range from first 15 or 30 minutes (OR15 / OR30)
- BUY when price breaks above OR high
- SELL when price breaks below OR low
- Momentum continuation strategy (opposite of mean reversion)
- Works better on trending days, fails on range-bound days
- **Analysis needed:** ORB profitability by timeframe, stock type, gap size

### D2. VWAP bands strategy
- Compute VWAP + bands (like Bollinger but on VWAP)
- SELL when price hits upper VWAP band (overextended up)
- BUY when price hits lower VWAP band (overextended down)
- Works throughout the day, not just at open
- **Analysis needed:** VWAP deviation distribution, mean reversion speed

### D3. Volume profile analysis
- At what price levels did most volume trade?
- High-volume price levels = support/resistance
- When price approaches a high-volume level → expect bounce
- When price breaks through with high volume → expect continuation
- **Analysis needed:** Build volume profiles per stock per day, test predictions

### D4. Intraday support/resistance from first-hour
- The high and low of the first hour (9:15-10:15) often act as support/resistance for rest of day
- BUY at first-hour low, SELL at first-hour high
- Simple but requires waiting 1 hour to define levels
- **Analysis needed:** How often does first-hour high/low hold for the rest of the day?

---

## E. SCORING & SELECTION IMPROVEMENTS

### E1. Adaptive position sizing (score-weighted capital)
- Current: equal capital per trade (10k × 8)
- Idea: high-score picks get 15-20k, low-score picks get 5k
- Total capital unchanged, concentrated on best opportunities
- **Analysis needed:** Simulate score-weighted allocation vs equal allocation

### E2. Fewer but better positions (top-4 to top-7)
- Mega analysis: top-7 (+156%) beats top-8 (+147%)
- Top-4: 59.9% trade win vs top-8: 55.8%
- Already analyzed. Ready to implement as config change.

### E3. Dynamic position count based on market conditions
- Strong reversal day (many high-score candidates): take 8 positions
- Weak reversal day (few qualifying candidates): take 2-4 positions only
- Don't force 8 picks when only 3 stocks qualify well
- **Analysis needed:** Correlation between candidate pool quality and daily return

### E4. Score threshold (minimum score to enter)
- Don't take a position just because it's in top-8 — require minimum score
- If only 3 stocks pass score threshold, take only 3
- Better to miss a day than take low-quality trades
- **Analysis needed:** What minimum score threshold maximizes total return?

---

## F. RISK MANAGEMENT

### F1. Correlation-aware selection
- If 5 of top-8 are banking stocks, they'll all move together = no diversification
- Diversify across price bands or sector proxies
- **Analysis needed:** Sector/price-band correlation of top-8 picks

### F2. Progressive de-risking
- If first 3 trades of the day all lose → reduce size for remaining trades
- Don't wait for circuit breaker (6% loss) — start reducing earlier
- **Analysis needed:** Sequential correlation of trade outcomes within a day

### F3. Market breadth pre-filter
- Mega analysis showed: when market is broadly bullish, SELL reversal is weaker
- Skip SELL trades on extreme bullish days
- Already analyzed (mega_deep section 9). Ready to implement.

### F4. Worst-case scenario planning
- What's the maximum theoretical loss in a single day?
- What if ALL 8 positions go against you?
- Design position sizing so max daily loss stays below X% even in worst case
- **Analysis needed:** Tail risk analysis — distribution of worst days

---

## G. DATA & INFRASTRUCTURE

### G1. Collect more historical data
- Current: 4 months (Dec-Mar), bearish period only
- Need: 6-12 months covering both bullish and bearish periods
- Critical for: BUY validation, regime detection, strategy robustness
- **Action:** Backfill candle data for Jul-Nov 2025

### G2. Add buy_ratio (br) to live snapshots
- V2 scoring uses close_position as proxy for br
- Real br data would improve scoring accuracy
- **Action:** Check if Dhan WebSocket/API provides buy_ratio, add to snapshot table

### G3. Add sector/industry tagging
- Enable sector diversification and sector-relative analysis
- Source: NSE industry classification
- **Action:** Download NSE sector CSV, add to watchlist table

### G4. Add Nifty 50 index data
- Enable regime detection, relative strength analysis
- **Action:** Poll Nifty 50 candles alongside stock candles

### G5. Real-time order book depth
- Bid/ask spread and order book depth predict short-term direction
- Dhan provides bid_qty, ask_qty in snapshots (already collected!)
- **Analysis needed:** Does bid/ask ratio predict next-5-min direction?

---

## H. ADDITIONAL IDEAS

### H1. Earnings/event calendar filter
- Stocks with earnings that day or next day gap for fundamental reasons
- These gaps may NOT reverse (they're information-driven, not euphoria-driven)
- Filter out stocks with upcoming earnings from the candidate pool
- **Need:** Earnings calendar data source

### H2. ASM/GSM/T2T filter
- Stocks under additional surveillance (ASM/GSM) behave differently
- They may have restricted circuit limits or trading restrictions
- The scrip master CSV has ASM_GSM_FLAG — use it as a filter
- Already in data, just need to incorporate

### H3. Intraday momentum persistence
- Some stocks trend all day (momentum persists), others mean-revert
- Classify stocks into "trendy" vs "reverting" based on historical behavior
- Use classification to decide: trade momentum or reversal on this stock?
- **Analysis needed:** Autocorrelation of intraday returns per stock

### H4. Cross-stock signal confirmation
- If 3+ stocks in similar price band all gap up and reverse → stronger signal
- If only 1 stock gaps up while peers don't → might be news-driven, less reliable
- Cluster analysis on same-day gap-up stocks
- **Analysis needed:** Does group behavior improve individual predictions?

### H5. Machine learning scoring
- Replace hand-crafted formulas with a trained model
- Features: all the ones we've identified (gap, sell_pressure, momentum, etc.)
- Target: binary win/loss or continuous return
- Risk: overfitting on 78 days of data. Need rigorous cross-validation.
- **Analysis needed:** Walk-forward validation, feature importance from the model

---

## Priority Ranking (updated)

| # | Idea | Impact | Effort | Data? | Notes |
|---|---|---|---|---|---|
| 1 | **C1: Deep BUY analysis** | High | Medium | Yes | 4 months data available, mirror the sell analysis |
| 2 | **A1: All-time market entry** | Very High | High | Yes | Full 375-bucket data exists, needs per-window analysis |
| 3 | **B2: Momentum reversal exit** | High | Medium | Yes | Detect when to exit dynamically |
| 4 | **B5: Real-time feature monitoring** | High | Medium | Yes | Recompute features after entry for exit decision |
| 5 | **B1: VWAP stop loss** | High | Low | Yes | Simple to analyze and implement |
| 6 | **E4: Score threshold** | Medium | Low | Yes | Don't force bad trades |
| 7 | **E2: Fewer positions** | Medium | Zero | Done | Already analyzed, just config change |
| 8 | **F3: Market breadth filter** | Medium | Low | Done | Already analyzed in mega_deep |
| 9 | **A2: Pattern-triggered entry** | High | High | Yes | Define triggers, test each independently |
| 10 | **D1: ORB strategy** | High | High | Yes | Completely new strategy, needs full analysis |
| 11 | **C3: BUY after reversal completes** | Medium | Medium | Yes | Second-leg trade |
| 12 | **B4: Trailing stop** | Medium | Medium | Yes | MFE path analysis needed |
| 13 | **E1: Score-weighted sizing** | Medium | Low | Yes | Simulate vs equal allocation |
| 14 | **D2: VWAP bands** | Medium | High | Yes | Full-day VWAP analysis |
| 15 | **H2: ASM/GSM filter** | Low | Low | Yes | Data already in scrip master |
| 16 | **G1: More historical data** | Critical | Medium | Need backfill | Enables BUY validation + regime detection |
| 17 | **G5: Order book analysis** | Medium | Medium | Yes | bid/ask already in snapshots |
| 18 | **H5: ML scoring** | Unknown | Very High | Yes | Risk of overfitting on small dataset |
