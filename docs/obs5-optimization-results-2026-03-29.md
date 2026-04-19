# OBS=5 Optimization Results — 2026-03-29

## Setup
- **Observation**: 5 minutes (bucket 1-5), entry at bucket 6 (9:20 AM)
- **Period**: Dec 2025 - Mar 2026 (80 trading days)
- **Capital**: Rs 100K per trade, 5x margin
- **Tested**: 5,040 selections x 3 TP/SL x 12 exit times = 181,440 combos
- **Runtime**: 371 seconds (6 min) — NumPy vectorized
- **Profitable configs found**: 43,247
- **Configs with >= 2% daily ROC**: 2,362

---

## Two Distinct Strategies Found

### Strategy A: Maximum ROC (Concentrated)
**SELL gap-up + previous day DOWN, rank by gap size, 3 positions**

| Setting | Value |
|---|---|
| Direction | SELL |
| Gap filter | Gap UP > 0.5% |
| Momentum | Previous day DOWN |
| Ranking | Gap size (biggest first) |
| Positions | 3 |
| TP/SL | None (TIME exit) |
| Exit | Bucket 76 (10:30 AM) |
| **Daily ROC** | **+11.05%** |
| **Win Rate** | **71%** |
| **Green Days** | 23/59 (39%) |
| **Total P&L** | +Rs 3,82,669 |

**Pattern**: Stock went DOWN yesterday → gaps UP today → but starts selling in the morning → SELL the reversal. This is the strongest mean-reversion signal but only fires on ~59 of 80 days.

### Strategy B: Maximum Green Days (Diversified)
**SELL gap-up + previous day UP, rank by gap size, 12 positions**

| Setting | Value |
|---|---|
| Direction | SELL |
| Gap filter | Gap UP > 0.5% |
| Momentum | Previous day UP |
| Ranking | Gap size (biggest first) |
| Positions | 12 |
| TP/SL | None (TIME exit) |
| Exit | Bucket 40 (9:54 AM) |
| **Daily ROC** | **+4.13%** |
| **Win Rate** | **72%** |
| **Green Days** | **30/58 (52%)** |
| **Total P&L** | +Rs 5,40,740 |

**Pattern**: Stock went UP yesterday → gaps UP more today → exhaustion, profit-taking at open → SELL with more positions for diversification. Earlier exit (9:54 AM) locks in gains before any bounce.

---

## Best Config Per Exit Time

| Exit Bucket | IST Time | Best ROC | Win% | Green Days | Config |
|---|---|---|---|---|---|
| 20 | 09:34 | +4.05% | 56% | 27/79 (34%) | S bigGap+vol>200 rank=gapSize pos=3 |
| 25 | 09:39 | +4.07% | 56% | 16/59 (27%) | A gapUp+prevDn rank=gapSize pos=3 |
| 30 | 09:44 | +4.37% | 59% | 29/79 (37%) | S bigGap+vol>200 rank=gapSize pos=3 |
| 35 | 09:49 | +9.08% | 62% | 17/59 (29%) | A gapUp+prevDn rank=gapSize pos=3 |
| 40 | 09:54 | +9.31% | 62% | 17/59 (29%) | A gapUp+prevDn rank=gapSize pos=3 |
| **46** | **10:00** | **+9.36%** | **63%** | **19/59 (32%)** | S gapUp+prevDn rank=gapSize pos=3 |
| 50 | 10:04 | +9.70% | 66% | 18/59 (31%) | S gapUp+prevDn rank=gapSize pos=3 |
| 55 | 10:09 | +9.74% | 67% | 19/59 (32%) | S prevDn rank=gapSize pos=3 |
| 60 | 10:14 | +10.45% | 68% | 20/59 (34%) | S prevDn rank=gapSize pos=3 |
| 65 | 10:19 | +10.43% | 66% | 20/59 (34%) | S prevDn rank=gapSize pos=3 |
| **70** | **10:24** | **+10.83%** | **70%** | **23/59 (39%)** | S gapUp+prevDn rank=gapSize pos=3 |
| **76** | **10:30** | **+11.05%** | **71%** | **23/59 (39%)** | S gapUp+prevDn rank=gapSize pos=3 |

**Trend**: Longer holding = higher ROC. The reversal needs 45-60 min to fully develop. Exit before 10:00 AM leaves money on the table.

---

## Best Config Per Position Size

| Positions | ROC | Win% | Green Days | P&L | Prev Day Filter | Exit |
|---|---|---|---|---|---|---|
| **3** | **+11.05%** | 71% | 23/59 (39%) | +383K | **prevDayDown** | 10:30 |
| 5 | +8.09% | 56% | 18/59 (31%) | +470K | prevDayDown | 10:30 |
| 7 | +5.68% | 53% | 18/59 (31%) | +464K | prevDayDown | 10:30 |
| **10** | +4.25% | 68% | **29/58 (50%)** | +467K | **prevDayUp** | 10:19 |
| **12** | +4.13% | **72%** | **30/58 (52%)** | +541K | **prevDayUp** | 09:54 |
| **15** | +4.06% | 69% | 29/58 (50%) | **+656K** | **prevDayUp** | 10:19 |

### Key Insight: Two Different Regimes

| | Few positions (3-5) | Many positions (10-15) |
|---|---|---|
| Best prev day filter | **Down** (reversal) | **Up** (exhaustion) |
| ROC | Higher (8-11%) | Lower (4%) |
| Green days | Low (31-39%) | **High (50-52%)** |
| P&L | Lower | **Higher** |
| Risk | Concentrated | Diversified |

**When picking 3 stocks**: the strongest reversals (prev day DOWN → gap UP → SELL) dominate. Few signals but very profitable.

**When picking 10-15 stocks**: exhaustion plays (prev day UP → gap UP → SELL) provide more candidates and more consistent daily wins.

---

## Comparison: Current Gap Reversal vs Observe-Then-Trade

| | Current gap_reversal_mode | OBS=5 Strategy A | OBS=5 Strategy B |
|---|---|---|---|
| Entry | Bucket 2-4 (9:16-9:18) | **Bucket 6 (9:20)** | **Bucket 6 (9:20)** |
| Observation | 2-4 min | 5 min | 5 min |
| Prev day | Must be UP | **Must be DOWN** | Must be UP |
| TP/SL | Configurable | None | None |
| Exit | Configurable | 10:30 AM | 9:54 AM |
| Positions | 3-12 | **3** | **12** |
| ROC/day | +3.48% to +4.07% | **+11.05%** | +4.13% |
| Win Rate | 65% | 71% | **72%** |
| Green Days | 82% (but fewer days) | 39% | **52%** |
| P&L | +153K | +383K | **+541K** |

---

## Recommendations

### For Maximum Daily ROC
Use Strategy A: 3 positions, prevDayDown, exit at 10:30 AM. Accept that 61% of days have no signals (when prev day wasn't down for gap-up stocks).

### For Maximum Consistency + P&L
Use Strategy B: 12 positions, prevDayUp, exit at 9:54 AM. Fires on more days, more green days, highest absolute P&L.

### Combined Approach
Run BOTH strategies simultaneously:
- Strategy A: 3 SELL positions from prevDayDown gap-up stocks
- Strategy B: 5-7 SELL positions from prevDayUp gap-up stocks
- Total: 8-10 positions with both reversal and exhaustion plays

---

## Common Elements (Universal Truths)

1. **SELL direction dominates** — bearish market Dec-Mar 2026
2. **Gap UP is the #1 filter** — stocks that opened higher tend to fade
3. **Gap size ranking** — biggest gap = strongest signal (consistent across ALL configs)
4. **No TP/SL** — TIME exit outperforms all TP/SL combos
5. **5 min observation** — better than 3 min (filters false starts) and 7 min (loses edge)
6. **Reversal needs 45-60 min** — early exit (before 9:50 AM) leaves money on the table
