# Moving Average Strategy Lab

Generated from local parquet daily bars. This test converts the moving-average concepts from Christian Qullamaggie / Lance Breitstein style discussion into explicit daily-bar rules.

## Pro-Trader Read

- Best candidates to paper trade: none passed strict promotion gates.
- Watchlist candidates: none.
- A moving-average idea is only interesting here if it survives costs, has enough trades, does not rely on one year, and stays positive in validation and out-of-sample windows.
- Daily bars cannot fully model opening-range breakout fills, partial exits after 3-5 days, intraday MA reclaims, or real slippage in fast leaders. Treat the best rows as paper-desk candidates, not live approval.

## Strategy Variants Tested

- `kk_breakout_sma10_trail` (Qualamagi breakout): 10SMA above rising 20SMA, liquid RS leader breaks prior 20D high on volume; exit on first close below 10SMA.
- `kk_breakout_sma20_trail` (Qualamagi breakout): Slower version for steadier leaders: same breakout, but trail on first close below 20SMA.
- `kk_stage2_55d_breakout_20trail` (Qualamagi breakout): Stage-2 style close above prior 55D high while above rising 10/20/50 SMAs; trail 20SMA.
- `kk_fast_ema10_20_breakout` (Fast EMA breakout): Fast-moving version using EMA10/EMA20 alignment and a prior-high breakout; trail EMA10.
- `ma_surf_10_20_continuation` (MA surf pullback): Leader remains above rising 10/20SMAs, tags one of them intraday, then closes strong; trail 20SMA.
- `ma_surf_10_tight_exit` (MA surf pullback): Same surf entry, but exits on close below 10SMA for more aggressive profit protection.
- `lance_sma20_reclaim_reversal` (SMA20 reclaim): Price was below SMA20, reclaims and closes above it with a strong candle; exit close below SMA20.
- `sma200_bounce_leader` (200SMA bounce): Long-term leader pulls into the 200SMA and closes green; exit below 200SMA or after trend resumes.
- `sma200_reclaim_breakout` (200SMA reclaim): Stock reclaims the 200SMA and clears prior 20D resistance with volume; trail SMA20.
- `ma_stretch_to_sma20_target` (Mean reversion to MA): Uptrend stock closes more than 2 ATR below SMA20; target is a snapback to SMA20.

## Ranked Results

| strategy                       | family               |   trades |   win_rate |   profit_factor |   expectancy_pct |   max_drawdown_proxy_pct |   positive_years_pct |   validation_expectancy_pct |   oos_expectancy_pct |   robustness_score | verdict   |
|:-------------------------------|:---------------------|---------:|-----------:|----------------:|-----------------:|-------------------------:|---------------------:|----------------------------:|---------------------:|-------------------:|:----------|
| kk_stage2_55d_breakout_20trail | Qualamagi breakout   |     4435 |      30.78 |           1.256 |            1.087 |                   -88.15 |                50    |                      -0.271 |               -0.785 |             -16.72 | reject    |
| ma_surf_10_20_continuation     | MA surf pullback     |     5675 |      28.65 |           1.215 |            0.731 |                   -89.88 |                50    |                       0.025 |               -0.727 |             -19.4  | reject    |
| ma_stretch_to_sma20_target     | Mean reversion to MA |     3600 |      38.08 |           0.978 |           -0.051 |                   -71.54 |                50    |                      -0.258 |               -0.869 |             -21.47 | reject    |
| ma_surf_10_tight_exit          | MA surf pullback     |     5454 |      30.23 |           1.044 |            0.127 |                   -79.62 |                50    |                      -0.038 |               -0.808 |             -22.12 | reject    |
| lance_sma20_reclaim_reversal   | SMA20 reclaim        |     5793 |      25.96 |           1.148 |            0.41  |                   -87.48 |                50    |                      -0.062 |               -0.981 |             -24.2  | reject    |
| kk_breakout_sma20_trail        | Qualamagi breakout   |     5291 |      29.96 |           1.239 |            1.004 |                   -93.5  |                33.33 |                      -0.355 |               -0.957 |             -26.86 | reject    |
| sma200_reclaim_breakout        | 200SMA reclaim       |     1330 |      31.13 |           1.006 |            0.023 |                   -72.99 |                50    |                       0.394 |               -2.541 |             -27.03 | reject    |
| kk_breakout_sma10_trail        | Qualamagi breakout   |     5291 |      32.24 |           1.005 |            0.02  |                   -87.6  |                33.33 |                      -0.258 |               -0.728 |             -33.09 | reject    |
| sma200_bounce_leader           | 200SMA bounce        |     4245 |      24.45 |           0.919 |           -0.264 |                   -94.63 |                66.67 |                      -0.796 |               -0.877 |             -36.68 | reject    |
| kk_fast_ema10_20_breakout      | Fast EMA breakout    |     6184 |      32.42 |           1.012 |            0.044 |                   -93.09 |                33.33 |                      -0.023 |               -1.109 |             -37.94 | reject    |

## Chronological Robustness

| strategy                       | split         |   trades |   win_rate |   profit_factor |   expectancy_pct | range_start   | range_end   |
|:-------------------------------|:--------------|---------:|-----------:|----------------:|-----------------:|:--------------|:------------|
| kk_breakout_sma10_trail        | in_sample     |     3279 |      33.39 |           1.092 |            0.325 | 2021-01-01    | 2024-03-29  |
| kk_breakout_sma10_trail        | out_of_sample |      941 |      31.46 |           0.79  |           -0.728 | 2025-04-27    | 2026-05-27  |
| kk_breakout_sma10_trail        | validation    |     1071 |      29.41 |           0.932 |           -0.258 | 2024-03-29    | 2025-04-27  |
| kk_breakout_sma20_trail        | in_sample     |     3298 |      31.66 |           1.48  |            1.996 | 2021-01-01    | 2024-03-29  |
| kk_breakout_sma20_trail        | out_of_sample |      935 |      28.34 |           0.753 |           -0.957 | 2025-04-27    | 2026-05-27  |
| kk_breakout_sma20_trail        | validation    |     1058 |      26.09 |           0.923 |           -0.355 | 2024-03-29    | 2025-04-27  |
| kk_fast_ema10_20_breakout      | in_sample     |     3851 |      33.52 |           1.106 |            0.392 | 2021-01-01    | 2024-03-29  |
| kk_fast_ema10_20_breakout      | out_of_sample |     1093 |      29.73 |           0.703 |           -1.109 | 2025-04-27    | 2026-05-27  |
| kk_fast_ema10_20_breakout      | validation    |     1240 |      31.37 |           0.994 |           -0.023 | 2024-03-29    | 2025-04-27  |
| kk_stage2_55d_breakout_20trail | in_sample     |     2761 |      32.27 |           1.487 |            2.056 | 2021-01-01    | 2024-03-29  |
| kk_stage2_55d_breakout_20trail | out_of_sample |      787 |      29.73 |           0.798 |           -0.785 | 2025-04-27    | 2026-05-27  |
| kk_stage2_55d_breakout_20trail | validation    |      887 |      27.06 |           0.941 |           -0.271 | 2024-03-29    | 2025-04-27  |
| lance_sma20_reclaim_reversal   | in_sample     |     3505 |      27.9  |           1.378 |            0.995 | 2021-01-01    | 2024-03-29  |
| lance_sma20_reclaim_reversal   | out_of_sample |     1056 |      21.59 |           0.645 |           -0.981 | 2025-04-27    | 2026-05-27  |
| lance_sma20_reclaim_reversal   | validation    |     1232 |      24.19 |           0.98  |           -0.062 | 2024-03-29    | 2025-04-27  |
| ma_stretch_to_sma20_target     | in_sample     |     1827 |      43.95 |           1.197 |            0.412 | 2021-01-01    | 2024-03-29  |
| ma_stretch_to_sma20_target     | out_of_sample |      782 |      29.28 |           0.638 |           -0.869 | 2025-04-27    | 2026-05-27  |
| ma_stretch_to_sma20_target     | validation    |      991 |      34.21 |           0.9   |           -0.258 | 2024-03-29    | 2025-04-27  |
| ma_surf_10_20_continuation     | in_sample     |     3528 |      29.59 |           1.411 |            1.375 | 2021-01-01    | 2024-03-29  |
| ma_surf_10_20_continuation     | out_of_sample |     1002 |      27.64 |           0.774 |           -0.727 | 2025-04-27    | 2026-05-27  |
| ma_surf_10_20_continuation     | validation    |     1145 |      26.64 |           1.007 |            0.025 | 2024-03-29    | 2025-04-27  |
| ma_surf_10_tight_exit          | in_sample     |     3355 |      31.15 |           1.161 |            0.458 | 2021-01-01    | 2024-03-29  |
| ma_surf_10_tight_exit          | out_of_sample |      993 |      28.5  |           0.709 |           -0.808 | 2025-04-27    | 2026-05-27  |
| ma_surf_10_tight_exit          | validation    |     1106 |      29.02 |           0.987 |           -0.038 | 2024-03-29    | 2025-04-27  |
| sma200_bounce_leader           | in_sample     |     1921 |      28.27 |           1.15  |            0.432 | 2021-01-01    | 2024-03-29  |
| sma200_bounce_leader           | out_of_sample |     1287 |      22.07 |           0.753 |           -0.877 | 2025-04-27    | 2026-05-27  |
| sma200_bounce_leader           | validation    |     1037 |      20.35 |           0.778 |           -0.796 | 2024-03-29    | 2025-04-27  |
| sma200_reclaim_breakout        | in_sample     |      678 |      36.58 |           1.417 |            1.298 | 2021-01-01    | 2024-03-29  |
| sma200_reclaim_breakout        | out_of_sample |      377 |      22.81 |           0.394 |           -2.541 | 2025-04-27    | 2026-05-27  |
| sma200_reclaim_breakout        | validation    |      275 |      29.09 |           1.097 |            0.394 | 2024-03-29    | 2025-04-27  |

## Exit Behavior

| strategy                       | exit_reason        |   trades |   win_rate |   expectancy_pct |   avg_hold_days |
|:-------------------------------|:-------------------|---------:|-----------:|-----------------:|----------------:|
| kk_breakout_sma10_trail        | close_below_sma10  |     3411 |      48.93 |            3.152 |        10.7845  |
| kk_breakout_sma10_trail        | initial_stop       |     1827 |       0    |           -6.092 |         3.11385 |
| kk_breakout_sma10_trail        | time               |       53 |      69.81 |            9.1   |         7.62264 |
| kk_breakout_sma20_trail        | close_below_sma20  |     2670 |      57.45 |            7.704 |        21.6184  |
| kk_breakout_sma20_trail        | initial_stop       |     2547 |       0    |           -6.525 |         4.87868 |
| kk_breakout_sma20_trail        | time               |       74 |      68.92 |           18.434 |        15.2568  |
| kk_fast_ema10_20_breakout      | close_below_ema10  |     4410 |      44.22 |            2.457 |        12.2478  |
| kk_fast_ema10_20_breakout      | initial_stop       |     1700 |       0    |           -6.816 |         3.72176 |
| kk_fast_ema10_20_breakout      | time               |       74 |      74.32 |           13.808 |        13.6757  |
| kk_stage2_55d_breakout_20trail | close_below_sma20  |     2273 |      57.94 |            7.634 |        21.733   |
| kk_stage2_55d_breakout_20trail | initial_stop       |     2098 |       0    |           -6.687 |         4.87798 |
| kk_stage2_55d_breakout_20trail | time               |       64 |      75    |           23.406 |        16.1875  |
| lance_sma20_reclaim_reversal   | close_below_sma20  |     3916 |      29.24 |           -0.082 |         9.82661 |
| lance_sma20_reclaim_reversal   | initial_stop       |     1507 |       0    |           -4.821 |         4.14068 |
| lance_sma20_reclaim_reversal   | time               |      370 |      97.03 |           26.928 |        27.0811  |
| ma_stretch_to_sma20_target     | initial_stop       |     2162 |       0    |           -3.739 |         3.33904 |
| ma_stretch_to_sma20_target     | sma20              |     1306 |     100    |            6.044 |         5.94946 |
| ma_stretch_to_sma20_target     | time               |      132 |      49.24 |            0.053 |        11.5     |
| ma_surf_10_20_continuation     | close_below_sma20  |     3262 |      45.8  |            3.425 |        15.6689  |
| ma_surf_10_20_continuation     | initial_stop       |     2267 |       0    |           -5.56  |         4.13939 |
| ma_surf_10_20_continuation     | time               |      146 |      90.41 |           38.231 |        30.7808  |
| ma_surf_10_tight_exit          | close_below_sma10  |     3973 |      40.3  |            1.782 |         8.35515 |
| ma_surf_10_tight_exit          | initial_stop       |     1423 |       0    |           -5.187 |         2.86788 |
| ma_surf_10_tight_exit          | time               |       58 |      82.76 |           17.092 |        12.5862  |
| sma200_bounce_leader           | initial_stop       |     2071 |       0    |           -4.629 |         6.6958  |
| sma200_bounce_leader           | close_below_sma200 |     1075 |       2.79 |           -3.618 |         5.90326 |
| sma200_bounce_leader           | atr4               |      811 |     100    |           14.322 |        13.82    |
| sma200_bounce_leader           | time               |      288 |      68.4  |            2.559 |        30.8854  |
| sma200_reclaim_breakout        | close_below_sma20  |      770 |      50.26 |            3.242 |        19.6052  |
| sma200_reclaim_breakout        | initial_stop       |      525 |       0    |           -5.73  |         5.2381  |
| sma200_reclaim_breakout        | time               |       35 |      77.14 |           15.502 |        22.0571  |

## Top Symbol Contribution

| strategy                       | symbol     |   trades |   net_return_sum |   avg_net_return |   win_rate |
|:-------------------------------|:-----------|---------:|-----------------:|-----------------:|-----------:|
| kk_breakout_sma10_trail        | JAIBALAJI  |        4 |         4.03375  |        1.00844   |      75    |
| kk_breakout_sma10_trail        | GNA        |        7 |         2.32794  |        0.332563  |      71.43 |
| kk_breakout_sma10_trail        | TTML       |        7 |         2.12074  |        0.302963  |      57.14 |
| kk_breakout_sma10_trail        | GALLANTT   |       12 |         1.80023  |        0.150019  |      50    |
| kk_breakout_sma10_trail        | MARINE     |        5 |         1.74105  |        0.34821   |      40    |
| kk_breakout_sma20_trail        | JAIBALAJI  |        4 |        13.983    |        3.49575   |      75    |
| kk_breakout_sma20_trail        | BSE        |       22 |         7.55765  |        0.34353   |      50    |
| kk_breakout_sma20_trail        | AURIONPRO  |        5 |         5.65531  |        1.13106   |      20    |
| kk_breakout_sma20_trail        | TTML       |        8 |         3.93626  |        0.492032  |      50    |
| kk_breakout_sma20_trail        | PREMEXPLN  |       15 |         3.32669  |        0.221779  |      46.67 |
| kk_fast_ema10_20_breakout      | PREMEXPLN  |       16 |         3.55387  |        0.222117  |      43.75 |
| kk_fast_ema10_20_breakout      | JAIBALAJI  |        2 |         2.30138  |        1.15069   |     100    |
| kk_fast_ema10_20_breakout      | GOODLUCK   |        8 |         2.15605  |        0.269506  |      75    |
| kk_fast_ema10_20_breakout      | TTML       |        8 |         2.10895  |        0.263618  |      50    |
| kk_fast_ema10_20_breakout      | BBOX       |        8 |         2.01658  |        0.252072  |      62.5  |
| kk_stage2_55d_breakout_20trail | BSE        |       22 |        11.2664   |        0.512111  |      59.09 |
| kk_stage2_55d_breakout_20trail | AURIONPRO  |        5 |         5.65531  |        1.13106   |      20    |
| kk_stage2_55d_breakout_20trail | JAIBALAJI  |        5 |         4.17097  |        0.834195  |      40    |
| kk_stage2_55d_breakout_20trail | PREMEXPLN  |       14 |         4.01185  |        0.286561  |      50    |
| kk_stage2_55d_breakout_20trail | GPTINFRA   |        7 |         2.51841  |        0.359773  |      28.57 |
| lance_sma20_reclaim_reversal   | OLECTRA    |        8 |         2.80573  |        0.350717  |      12.5  |
| lance_sma20_reclaim_reversal   | PREMEXPLN  |        6 |         1.93714  |        0.322857  |      66.67 |
| lance_sma20_reclaim_reversal   | JAIBALAJI  |        2 |         1.55677  |        0.778383  |      50    |
| lance_sma20_reclaim_reversal   | SKMEGGPROD |        9 |         1.40271  |        0.155857  |      44.44 |
| lance_sma20_reclaim_reversal   | COCHINSHIP |        7 |         1.28103  |        0.183005  |      71.43 |
| ma_stretch_to_sma20_target     | TRIVENI    |        5 |         0.558995 |        0.111799  |     100    |
| ma_stretch_to_sma20_target     | CAMLINFINE |       13 |         0.480533 |        0.0369641 |      61.54 |
| ma_stretch_to_sma20_target     | JSWENERGY  |        7 |         0.473022 |        0.0675746 |      85.71 |
| ma_stretch_to_sma20_target     | KEI        |       10 |         0.457811 |        0.0457811 |      80    |
| ma_stretch_to_sma20_target     | JSLL       |        6 |         0.453457 |        0.0755761 |      50    |
| ma_surf_10_20_continuation     | AURIONPRO  |        5 |         4.79913  |        0.959826  |      20    |
| ma_surf_10_20_continuation     | REFEX      |       11 |         3.15023  |        0.286384  |      72.73 |
| ma_surf_10_20_continuation     | GPTINFRA   |        7 |         3.04184  |        0.434548  |      42.86 |
| ma_surf_10_20_continuation     | MAZDOCK    |       11 |         2.43257  |        0.221143  |      45.45 |
| ma_surf_10_20_continuation     | TTML       |        2 |         2.36596  |        1.18298   |     100    |
| ma_surf_10_tight_exit          | REFEX      |       11 |         1.82315  |        0.165741  |      63.64 |
| ma_surf_10_tight_exit          | JSLL       |       14 |         1.70553  |        0.121823  |      50    |
| ma_surf_10_tight_exit          | GOODLUCK   |       15 |         1.51845  |        0.10123   |      40    |
| ma_surf_10_tight_exit          | MAZDOCK    |       11 |         1.40443  |        0.127676  |      45.45 |
| ma_surf_10_tight_exit          | KHADIM     |        3 |         1.33819  |        0.446062  |      66.67 |
| sma200_bounce_leader           | SATIN      |        8 |         0.831263 |        0.103908  |      50    |
| sma200_bounce_leader           | HEG        |        7 |         0.660724 |        0.0943891 |      71.43 |
| sma200_bounce_leader           | HIMATSEIDE |        9 |         0.599642 |        0.0666269 |      44.44 |
| sma200_bounce_leader           | DREDGECORP |        5 |         0.594879 |        0.118976  |      80    |
| sma200_bounce_leader           | LUMAXTECH  |        4 |         0.572258 |        0.143065  |     100    |
| sma200_reclaim_breakout        | SKYGOLD    |        2 |         1.51372  |        0.756861  |     100    |
| sma200_reclaim_breakout        | CHENNPETRO |        2 |         1.11295  |        0.556476  |      50    |
| sma200_reclaim_breakout        | INDIACEM   |        3 |         0.964692 |        0.321564  |     100    |
| sma200_reclaim_breakout        | KITEX      |        2 |         0.961158 |        0.480579  |      50    |
| sma200_reclaim_breakout        | INDOTHAI   |        1 |         0.956374 |        0.956374  |     100    |

## Visual Outputs

- `charts/expectancy_by_strategy.png`
- `charts/pf_vs_sample_size.png`
- `charts/top_equity_curves.png`
- `charts/yearly_expectancy_heatmap.png`

## What I Would Analyze Next

1. Add true opening-range entries for breakout variants instead of next-session open proxies.
2. Model Qullamaggie partial exits: sell 1/3 to 1/2 after day 3-5, move stop to breakeven, then trail 10/20SMA.
3. Split by market regime: 10SMA above 20SMA for benchmark/index, breadth above/below 50%, and high/low volatility windows.
4. Add short-side Lance variants: downtrend MA resistance, breakdown through SMA20, and mean reversion target to SMA20.
5. Wire the top one or two variants into Scanner/Paper Desk as `Candidate`, not as live-trading approval.
