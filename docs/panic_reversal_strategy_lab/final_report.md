# Panic Reversal Strategy Lab

Data window: 2021-01-01 to 2026-05-27. Entries are next-session opens after a daily signal.

## Strategy thesis

This lab translates the Lance Brightstein panic trade into systematic rules: broad or symbol-level forced liquidation, large multi-day decline, volatility expansion, reclaim/off-low confirmation, defined low-of-panic risk, and an exit into mean reversion or a prior-daily-low trail.

## Tested variants

- `lance_3day_capitulation_reclaim`: Liquid stock is down hard over three days, prints a large ATR range, reclaims from the low, and closes in the upper half.
- `historic_panic_basket_reversal`: Only trades during broad market panic; buys the most liquid symbols hit hard but closing off lows.
- `failed_breakdown_20d_panic`: Undercuts the prior 20-day low during a selloff, then closes back above that level with volume.
- `gap_down_reclaim_panic`: Big gap down after a multi-day drop, then closes green/off lows; this approximates forced open liquidation.
- `washout_close_strong_next_open`: Largest down days that reject the lows and close very strong; faster exit into the first snapback.
- `deep_stretch_to_sma20_mean_revert`: Stock is deeply stretched below the 20SMA after a panic; target is a reflex move back toward equilibrium.
- `sma200_panic_undercut_reclaim`: Panic undercuts or nears the 200SMA and closes back above it; tries to catch institutional support.
- `late_confirmation_higher_close`: Waits one day after the panic for a higher close, reducing knife-catching but entering later.
- `relative_strength_panic_survivor`: Broad market panic but symbol loses less than the tape and reclaims intraday lows; buys relative strength.
- `knife_catch_no_confirmation_control`: Naive version: buys deep multi-day selloffs without requiring reclaim/close strength. Included as a warning baseline.

## Headline metrics, base cost model

| strategy                            | family                  |   trades |   trades_per_month |   win_rate |   profit_factor |   expectancy_pct |   median_return_pct |   avg_win_pct |   avg_loss_pct |   tail_5pct_return_pct |   total_return_proxy_pct |   max_drawdown_proxy_pct |   sharpe_trade |   sortino_trade |   avg_hold_days |
|:------------------------------------|:------------------------|---------:|-------------------:|-----------:|----------------:|-----------------:|--------------------:|--------------:|---------------:|-----------------------:|-------------------------:|-------------------------:|---------------:|----------------:|----------------:|
| deep_stretch_to_sma20_mean_revert   | MA snapback             |      394 |               6.19 |      45.94 |           1.464 |            1.414 |              -1.942 |         9.71  |         -5.636 |                 -9.861 |                    71.96 |                    -9.97 |          2.615 |           7.63  |            6.38 |
| historic_panic_basket_reversal      | Broad panic basket      |      232 |               3.75 |      43.1  |           1.569 |            1.092 |              -0.772 |         6.988 |         -3.375 |                 -6.317 |                    28.2  |                    -7.24 |          2.7   |           7.384 |            2.71 |
| lance_3day_capitulation_reclaim     | Capitulation reversal   |      136 |               2.14 |      39.71 |           1.411 |            0.993 |              -1.121 |         8.583 |         -4.005 |                 -8.857 |                    13.95 |                    -6.34 |          1.942 |           4.991 |            3.33 |
| sma200_panic_undercut_reclaim       | Long-term level reclaim |      250 |               4.22 |      39.2  |           1.058 |            0.117 |              -1.208 |         5.44  |         -3.314 |                 -6.502 |                     2.64 |                   -10.86 |          0.363 |           0.833 |            2.62 |
| knife_catch_no_confirmation_control | Control group           |     1621 |              25.25 |      30.23 |           0.909 |           -0.217 |              -2.539 |         7.136 |         -3.402 |                 -5.981 |                   -31.85 |                   -44.95 |         -0.549 |          -1.795 |            2.58 |

## Robustness scorecard

| strategy                            | family                  |   robustness_score |   positive_years_pct |   validation_expectancy_pct |   oos_expectancy_pct | verdict          |
|:------------------------------------|:------------------------|-------------------:|---------------------:|----------------------------:|---------------------:|:-----------------|
| historic_panic_basket_reversal      | Broad panic basket      |              71.4  |               100    |                       2.088 |                0.259 | promote_to_paper |
| deep_stretch_to_sma20_mean_revert   | MA snapback             |              64.75 |                83.33 |                       3.415 |               -0.599 | watch            |
| lance_3day_capitulation_reclaim     | Capitulation reversal   |              46.96 |                66.67 |                       3.345 |               -3.149 | watch            |
| relative_strength_panic_survivor    | Panic survivor          |              11.95 |                33.33 |                       0.152 |                0.06  | reject           |
| sma200_panic_undercut_reclaim       | Long-term level reclaim |               9.94 |                16.67 |                       1.598 |               -1.978 | reject           |
| gap_down_reclaim_panic              | Gap panic reclaim       |               8.52 |                33.33 |                       0.108 |               -0.48  | reject           |
| washout_close_strong_next_open      | Washout momentum        |              -2.56 |                16.67 |                       1.486 |               -2.475 | reject           |
| failed_breakdown_20d_panic          | Failed breakdown        |              -3.04 |                16.67 |                       0.121 |               -0.396 | reject           |
| knife_catch_no_confirmation_control | Control group           |              -3.56 |                50    |                      -0.486 |               -0.572 | reject           |
| late_confirmation_higher_close      | Confirmation entry      |             -28.91 |                 0    |                      -0.198 |               -2.057 | reject           |

## Chronological split read

| strategy                            |   in_sample |   out_of_sample |   validation |
|:------------------------------------|------------:|----------------:|-------------:|
| deep_stretch_to_sma20_mean_revert   |       0.802 |          -0.599 |        3.415 |
| failed_breakdown_20d_panic          |      -0.663 |          -0.396 |        0.121 |
| gap_down_reclaim_panic              |      -0.504 |          -0.48  |        0.108 |
| historic_panic_basket_reversal      |       0.532 |           0.259 |        2.088 |
| knife_catch_no_confirmation_control |       0.081 |          -0.572 |       -0.486 |
| lance_3day_capitulation_reclaim     |      -0.413 |          -3.149 |        3.345 |
| late_confirmation_higher_close      |      -0.346 |          -2.057 |       -0.198 |
| relative_strength_panic_survivor    |      -1.481 |           0.06  |        0.152 |
| sma200_panic_undercut_reclaim       |      -0.238 |          -1.978 |        1.598 |
| washout_close_strong_next_open      |      -2.348 |          -2.475 |        1.486 |

## Cost sensitivity

| strategy                            |   base |   optimistic |   stress |
|:------------------------------------|-------:|-------------:|---------:|
| deep_stretch_to_sma20_mean_revert   |  1.414 |        1.574 |    1.134 |
| failed_breakdown_20d_panic          | -0.354 |       -0.194 |   -0.634 |
| gap_down_reclaim_panic              | -0.31  |       -0.15  |   -0.59  |
| historic_panic_basket_reversal      |  1.092 |        1.252 |    0.812 |
| knife_catch_no_confirmation_control | -0.217 |       -0.057 |   -0.497 |
| lance_3day_capitulation_reclaim     |  0.993 |        1.153 |    0.713 |
| late_confirmation_higher_close      | -0.564 |       -0.404 |   -0.844 |
| relative_strength_panic_survivor    | -0.464 |       -0.304 |   -0.744 |
| sma200_panic_undercut_reclaim       |  0.117 |        0.277 |   -0.163 |
| washout_close_strong_next_open      | -0.766 |       -0.606 |   -1.046 |

## Exit behavior

| strategy                            | exit_reason           |   trades |   win_rate |   expectancy_pct |   avg_hold_days |
|:------------------------------------|:----------------------|---------:|-----------:|-----------------:|----------------:|
| deep_stretch_to_sma20_mean_revert   | sma20_target          |      155 |     100    |           10.819 |         7.06452 |
| deep_stretch_to_sma20_mean_revert   | time                  |       64 |      40.62 |           -0.761 |        12       |
| deep_stretch_to_sma20_mean_revert   | panic_low_stop        |      175 |       0    |           -6.122 |         3.71429 |
| failed_breakdown_20d_panic          | time                  |        3 |     100    |           22.224 |        14       |
| failed_breakdown_20d_panic          | close_below_sma10     |      697 |      47.2  |            0.087 |         1.1033  |
| failed_breakdown_20d_panic          | panic_low_stop        |      103 |       0    |           -3.996 |         1       |
| gap_down_reclaim_panic              | pre_panic_close3      |        8 |     100    |            7.85  |         1.625   |
| gap_down_reclaim_panic              | close_below_sma10     |      295 |      43.05 |           -0.08  |         1.04746 |
| gap_down_reclaim_panic              | panic_low_stop        |       31 |       0    |           -4.607 |         1       |
| historic_panic_basket_reversal      | pre_panic_close3      |       50 |     100    |           11.112 |         3.06    |
| historic_panic_basket_reversal      | prior_daily_low_trail |      131 |      38.17 |           -0.469 |         3.06107 |
| historic_panic_basket_reversal      | panic_low_stop        |       51 |       0    |           -4.723 |         1.47059 |
| knife_catch_no_confirmation_control | time                  |        3 |      66.67 |            9.894 |         5       |
| knife_catch_no_confirmation_control | prior_daily_low_trail |      786 |      62.09 |            3.663 |         3.80407 |
| knife_catch_no_confirmation_control | panic_low_stop        |      832 |       0    |           -3.919 |         1.41587 |
| lance_3day_capitulation_reclaim     | prior_daily_low_trail |      118 |      45.76 |            2.308 |         3.59322 |
| lance_3day_capitulation_reclaim     | panic_low_stop        |       18 |       0    |           -7.626 |         1.61111 |
| late_confirmation_higher_close      | prior_daily_low_trail |      510 |      45.88 |            1.428 |         3.46471 |
| late_confirmation_higher_close      | panic_low_stop        |      267 |       0    |           -4.37  |         1.56554 |
| relative_strength_panic_survivor    | time                  |        6 |     100    |           11.218 |        10       |
| relative_strength_panic_survivor    | close_below_sma10     |      126 |      39.68 |           -0.214 |         1.61111 |
| relative_strength_panic_survivor    | panic_low_stop        |       36 |       0    |           -3.286 |         1.36111 |
| sma200_panic_undercut_reclaim       | sma20_target          |       54 |      98.15 |            6.848 |         2.62963 |
| sma200_panic_undercut_reclaim       | prior_daily_low_trail |      133 |      33.83 |           -0.201 |         3.01504 |
| sma200_panic_undercut_reclaim       | panic_low_stop        |       63 |       0    |           -4.98  |         1.79365 |
| washout_close_strong_next_open      | two_atr_target        |        5 |     100    |           10.589 |         3.2     |
| washout_close_strong_next_open      | prior_daily_low_trail |       43 |      13.95 |           -1.816 |         2.72093 |
| washout_close_strong_next_open      | panic_low_stop        |        2 |       0    |           -6.565 |         3       |

## Panic-depth anatomy

| strategy                            | panic_depth_bucket   |   trades |   win_rate |   expectancy_pct |   avg_recovery_from_low_pct |
|:------------------------------------|:---------------------|---------:|-----------:|-----------------:|----------------------------:|
| deep_stretch_to_sma20_mean_revert   | <= -20%              |       34 |      26.47 |           -2.253 |                       11.54 |
| deep_stretch_to_sma20_mean_revert   | -20%..-15%           |       50 |      44    |            1.074 |                        6.99 |
| deep_stretch_to_sma20_mean_revert   | -15%..-10%           |      145 |      51.72 |            2.825 |                        6.44 |
| deep_stretch_to_sma20_mean_revert   | -10%..-7.5%          |       86 |      45.35 |            1.57  |                        5.76 |
| deep_stretch_to_sma20_mean_revert   | > -7.5%              |       72 |      44.44 |            0.738 |                        6.36 |
| failed_breakdown_20d_panic          | <= -20%              |       33 |      30.3  |           -0.944 |                       10.28 |
| failed_breakdown_20d_panic          | -20%..-15%           |       34 |      41.18 |           -0.815 |                        6.6  |
| failed_breakdown_20d_panic          | -15%..-10%           |      177 |      40.11 |           -0.23  |                        6.25 |
| failed_breakdown_20d_panic          | -10%..-7.5%          |      182 |      40.66 |           -0.502 |                        5.19 |
| failed_breakdown_20d_panic          | > -7.5%              |      364 |      43.41 |           -0.212 |                        5.45 |
| gap_down_reclaim_panic              | <= -20%              |       64 |      39.06 |           -0.582 |                        9.13 |
| gap_down_reclaim_panic              | -20%..-15%           |       36 |      58.33 |            0.668 |                        5.11 |
| gap_down_reclaim_panic              | -15%..-10%           |      111 |      38.74 |           -0.325 |                        6.41 |
| gap_down_reclaim_panic              | -10%..-7.5%          |      123 |      37.4  |           -0.441 |                        5.56 |
| historic_panic_basket_reversal      | <= -20%              |        5 |      20    |            0.63  |                       15.27 |
| historic_panic_basket_reversal      | -20%..-15%           |       19 |      21.05 |           -0.437 |                        8.1  |
| historic_panic_basket_reversal      | -15%..-10%           |       78 |      47.44 |            1.803 |                        7.74 |
| historic_panic_basket_reversal      | -10%..-7.5%          |      130 |      44.62 |            0.906 |                        6.54 |
| knife_catch_no_confirmation_control | <= -20%              |      208 |      30.77 |           -0.325 |                        3.92 |
| knife_catch_no_confirmation_control | -20%..-15%           |      450 |      30.67 |           -0.206 |                        2.9  |
| knife_catch_no_confirmation_control | -15%..-10%           |      963 |      29.91 |           -0.199 |                        2.52 |
| lance_3day_capitulation_reclaim     | <= -20%              |       10 |      20    |           -0.718 |                       17.78 |
| lance_3day_capitulation_reclaim     | -20%..-15%           |       12 |      25    |           -1.364 |                       10.67 |
| lance_3day_capitulation_reclaim     | -15%..-10%           |      114 |      42.98 |            1.392 |                        9.65 |
| late_confirmation_higher_close      | <= -20%              |       30 |      40    |           -0.164 |                        3.74 |
| late_confirmation_higher_close      | -20%..-15%           |       24 |      16.67 |           -2.744 |                        3.78 |
| late_confirmation_higher_close      | -15%..-10%           |       98 |      34.69 |           -0.059 |                        3.81 |
| late_confirmation_higher_close      | -10%..-7.5%          |      126 |      24.6  |           -1.133 |                        4.2  |
| late_confirmation_higher_close      | > -7.5%              |      395 |      28.61 |           -0.544 |                        5.18 |
| relative_strength_panic_survivor    | <= -20%              |        1 |     100    |            1.396 |                       15.78 |
| relative_strength_panic_survivor    | -15%..-10%           |        6 |       0    |           -3.224 |                        8.13 |
| relative_strength_panic_survivor    | -10%..-7.5%          |       17 |      11.76 |           -1.462 |                        3.51 |
| relative_strength_panic_survivor    | > -7.5%              |      117 |      38.46 |           -0.404 |                        5.56 |
| sma200_panic_undercut_reclaim       | <= -20%              |        8 |      50    |            3.83  |                        9.36 |
| sma200_panic_undercut_reclaim       | -20%..-15%           |       17 |      29.41 |           -0.338 |                        5.98 |
| sma200_panic_undercut_reclaim       | -15%..-10%           |       43 |      37.21 |           -0.43  |                        6.22 |
| sma200_panic_undercut_reclaim       | -10%..-7.5%          |       52 |      46.15 |            0.578 |                        6.21 |
| sma200_panic_undercut_reclaim       | > -7.5%              |      109 |      40.37 |            0.298 |                        5.16 |
| washout_close_strong_next_open      | <= -20%              |        1 |       0    |           -6.195 |                       14.41 |
| washout_close_strong_next_open      | -20%..-15%           |        3 |       0    |           -2.328 |                       11.53 |
| washout_close_strong_next_open      | -15%..-10%           |       11 |      45.45 |            1.997 |                       13.87 |
| washout_close_strong_next_open      | -10%..-7.5%          |       11 |       9.09 |           -1.875 |                       10.3  |
| washout_close_strong_next_open      | > -7.5%              |       23 |      21.74 |           -1.01  |                       11.55 |

## Pro-trader interpretation

Promote candidates:
- `historic_panic_basket_reversal` passed the strict scorecard and deserves paper routing.

The key production upgrade is intraday confirmation. Lance's described trade was not a blind next-day buy; it used a failed breakdown, reclaim of lows, trend break, and higher-low confirmation while liquidity was stressed. Daily bars can approximate the setup but cannot prove the exact timing edge.

## Recommended next algorithm

1. Require broad panic: index/breadth collapse, volatility expansion, and multi-asset stress if available.
2. Build a liquid watch list: index futures/ETFs plus the largest, cleanest single names.
3. Wait for failed breakdown: new low attempt fails, price reclaims the low, and a higher low forms.
4. Enter on reclaim/break of short intraday downtrend, not on the first falling print.
5. Initial stop at panic low; after a full retrace of the panic leg, scale out heavily.
6. Trail the remaining core with prior daily lows.

## Generated visuals

- `charts/expectancy_by_strategy.png`
- `charts/pf_vs_sample_size.png`
- `charts/top_equity_curves.png`
- `charts/panic_depth_vs_return.png`
- `charts/yearly_expectancy_heatmap.png`
- `charts/expectancy_by_panic_depth.png`