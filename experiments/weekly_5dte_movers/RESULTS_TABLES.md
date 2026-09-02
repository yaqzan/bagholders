# RESULTS_TABLES -- weekly_5dte_movers Stage C (machine-written by analyze.py --full)

Generated 2026-08-18 00:19:47 (local), seed=20260817, sklearn=yes 1.8.0
Analysis population: 1341534 rows (C=735743, P=605791)
Total metrics tested: 123 (111 numeric + 12 categorical)

## Section A -- Census

### growth_mult quantiles by view x side x expiry_year

| view | side | expiry_year | n | n_nonnull_growth | p50 | p90 | p99 | p995 | max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raw | C | 2022 | 531527 | 466267 | 1.23913 | 3.61991 | 16.3333 | 25 | 2000 |
| raw | C | 2023 | 1091387 | 967475 | 1.2381 | 3.99061 | 21.5714 | 34.1053 | 1370 |
| raw | C | 2024 | 1261753 | 1114566 | 1.25 | 4.05594 | 21 | 33 | 4432 |
| raw | C | 2025 | 1638511 | 1452577 | 1.25 | 4.25676 | 22.4576 | 34 | 3125 |
| raw | C | 2026 | 941781 | 851235 | 1.25 | 4.50455 | 25.4615 | 40.9444 | 2099 |
| raw | P | 2022 | 501286 | 433278 | 1.20959 | 3.52381 | 14 | 20.6857 | 15660 |
| raw | P | 2023 | 998399 | 868660 | 1.19582 | 3.67677 | 15.72 | 24 | 3970 |
| raw | P | 2024 | 1099668 | 955303 | 1.23662 | 4 | 20 | 30.875 | 2000 |
| raw | P | 2025 | 1427485 | 1236337 | 1.23529 | 4.2 | 23.2 | 37 | 2556 |
| raw | P | 2026 | 818522 | 721331 | 1.25674 | 4.81521 | 24 | 36 | 2100 |
| raw | both | 2022 | 1032813 | 899545 | 1.22396 | 3.57143 | 15.0556 | 23 | 15660 |
| raw | both | 2023 | 2089786 | 1836135 | 1.21495 | 3.81579 | 18.75 | 29.3846 | 3970 |
| raw | both | 2024 | 2361421 | 2069869 | 1.25 | 4.02632 | 20.5 | 32 | 4432 |
| raw | both | 2025 | 3065996 | 2688914 | 1.24888 | 4.23077 | 22.8571 | 35.3333 | 3125 |
| raw | both | 2026 | 1760303 | 1572566 | 1.25 | 4.66667 | 25 | 38.4 | 2100 |
| tradeable | C | 2022 | 88728 | 88281 | 1.5566 | 4.27059 | 13.08 | 17.4162 | 164.708 |
| tradeable | C | 2023 | 183757 | 183068 | 1.52108 | 4.55696 | 15.641 | 21.7391 | 344.84 |
| tradeable | C | 2024 | 231610 | 230777 | 1.51667 | 4.52326 | 15.6604 | 21.7162 | 196.735 |
| tradeable | C | 2025 | 291352 | 290540 | 1.51852 | 4.50704 | 15 | 20.3832 | 244.5 |
| tradeable | C | 2026 | 152589 | 152071 | 1.54468 | 4.84694 | 19.3939 | 27.5789 | 184.4 |
| tradeable | P | 2022 | 79878 | 79622 | 1.51695 | 4.18083 | 12.3514 | 16.1345 | 174.5 |
| tradeable | P | 2023 | 151230 | 150697 | 1.47222 | 4.34091 | 12.8571 | 17.2381 | 205.164 |
| tradeable | P | 2024 | 182596 | 181833 | 1.5 | 4.40625 | 16.52 | 22.8 | 254.25 |
| tradeable | P | 2025 | 236275 | 235619 | 1.47368 | 4.33333 | 15.7374 | 22.5 | 576 |
| tradeable | P | 2026 | 123934 | 123511 | 1.5 | 4.52941 | 15.2692 | 21.9211 | 236.8 |
| tradeable | both | 2022 | 168606 | 167903 | 1.53788 | 4.22857 | 12.7333 | 16.8 | 174.5 |
| tradeable | both | 2023 | 334987 | 333765 | 1.5 | 4.45455 | 14.4 | 19.75 | 344.84 |
| tradeable | both | 2024 | 414206 | 412610 | 1.51064 | 4.47368 | 16.0208 | 22.3077 | 254.25 |
| tradeable | both | 2025 | 527627 | 526159 | 1.5 | 4.43333 | 15.2913 | 21.1556 | 576 |
| tradeable | both | 2026 | 276523 | 275582 | 1.52424 | 4.70175 | 17.5826 | 25 | 236.8 |

### winner counts by view x side x threshold

| view | side | threshold | n_total | n_winners | rate |
| --- | --- | --- | --- | --- | --- |
| raw | C | 3 | 5464959 | 763412 | 0.139692 |
| raw | C | 5 | 5464959 | 391523 | 0.0716424 |
| raw | C | 10 | 5464959 | 153561 | 0.0280992 |
| raw | P | 3 | 4845360 | 656798 | 0.135552 |
| raw | P | 5 | 4845360 | 329332 | 0.0679685 |
| raw | P | 10 | 4845360 | 124080 | 0.025608 |
| raw | both | 3 | 10310319 | 1420210 | 0.137746 |
| raw | both | 5 | 10310319 | 720855 | 0.0699159 |
| raw | both | 10 | 10310319 | 277641 | 0.0269285 |
| tradeable | C | 3 | 948036 | 184367 | 0.194473 |
| tradeable | C | 5 | 948036 | 81144 | 0.0855917 |
| tradeable | C | 10 | 948036 | 23392 | 0.0246742 |
| tradeable | P | 3 | 773913 | 144364 | 0.186538 |
| tradeable | P | 5 | 773913 | 61328 | 0.079244 |
| tradeable | P | 10 | 773913 | 16990 | 0.0219534 |
| tradeable | both | 3 | 1721949 | 328731 | 0.190906 |
| tradeable | both | 5 | 1721949 | 142472 | 0.0827388 |
| tradeable | both | 10 | 1721949 | 40382 | 0.0234513 |

## Section B -- Base rates

| dim | slice | n | n_winners_3x | rate_3x | n_winners_5x | rate_5x | n_winners_10x | rate_10x |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overall | all | 1341534 | 262457 | 0.195639 | 115068 | 0.0857735 | 33654 | 0.0250862 |
| side | C | 735743 | 146596 | 0.199249 | 65457 | 0.0889672 | 19457 | 0.0264454 |
| side | P | 605791 | 115861 | 0.191256 | 49611 | 0.0818946 | 14197 | 0.0234355 |
| entry_dow | Mon | 678532 | 138759 | 0.204499 | 61408 | 0.0905013 | 17913 | 0.0263996 |
| entry_dow | Tue | 663002 | 123698 | 0.186573 | 53660 | 0.0809349 | 15741 | 0.023742 |
| expiry_year | 2022 | 124252 | 23311 | 0.187611 | 9628 | 0.0774877 | 2421 | 0.0194846 |
| expiry_year | 2023 | 251955 | 49065 | 0.194737 | 21303 | 0.0845508 | 5777 | 0.0229287 |
| expiry_year | 2024 | 322864 | 63448 | 0.196516 | 27657 | 0.0856615 | 8236 | 0.0255092 |
| expiry_year | 2025 | 418462 | 80167 | 0.191575 | 35528 | 0.0849014 | 10550 | 0.0252114 |
| expiry_year | 2026 | 224001 | 46466 | 0.207437 | 20952 | 0.0935353 | 6670 | 0.0297767 |

## Section C -- Univariate

Thin-coverage numeric metrics excluded from D/E (>40% null, 1 metrics): ['score_ma20']

### Top-40 |z| pooled

| metric | side | family | z_max_abs | n_nonnull | null_share | merge_fired | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| otm_pct | C | F1 | 86.9246 | 734825 | 0.00124772 | False | numeric |
| moneyness_pct | C | F1 | 86.9246 | 734825 | 0.00124772 | False | numeric |
| premium_over_spot | C | F1 | 84.889 | 734825 | 0.00124772 | False | numeric |
| hl_range_pct | C | F2 | 81.0858 | 735743 | 0 | False | numeric |
| entry_close | C | F1 | 73.2144 | 735743 | 0 | False | numeric |
| close_vs_open_pct | C | F2 | 65.373 | 735743 | 0 | False | numeric |
| entry_dollar_vol | C | F2 | 51.7094 | 735743 | 0 | False | numeric |
| strike | P | F1 | 41.5637 | 605791 | 0 | False | numeric |
| entry_transactions | C | F2 | 35.3229 | 735743 | 0 | False | numeric |
| sector | C | F8 | 35.3098 | 735743 |  |  | categorical |
| mcclellan_oscillator | P | F7 | 33.3941 | 605791 | 0 | False | numeric |
| spy_ret_20d | P | F7 | 33.3288 | 605791 | 0 | False | numeric |
| regime_composite | P | F7 | 32.9724 | 605791 | 0 | False | numeric |
| regime_multiplier | P | F7 | 32.9724 | 605791 | 0 | False | numeric |
| spy_ret_5d | C | F7 | 30.1622 | 735743 | 0 | False | numeric |
| ret_1d | P | F3 | 29.8415 | 605275 | 0.000851779 | False | numeric |
| ema_5_pxrel | P | F4 | 29.5602 | 605273 | 0.00085508 | False | numeric |
| ema_8_pxrel | P | F4 | 29.4946 | 605242 | 0.000906253 | False | numeric |
| bb_pctb | P | F5 | 29.4641 | 604661 | 0.00186533 | False | numeric |
| ema_9_pxrel | P | F4 | 28.6568 | 605160 | 0.00104161 | False | numeric |
| ema_10_pxrel | P | F4 | 28.4338 | 605114 | 0.00111755 | False | numeric |
| sma_10_pxrel | P | F4 | 28.2775 | 605114 | 0.00111755 | False | numeric |
| sma_5_pxrel | P | F4 | 28.1853 | 605273 | 0.00085508 | False | numeric |
| ad_diff | P | F7 | 28.163 | 605791 | 0 | False | numeric |
| sma_9_pxrel | P | F4 | 28.0943 | 605160 | 0.00104161 | False | numeric |
| ema_12_pxrel | P | F4 | 27.7909 | 605094 | 0.00115056 | False | numeric |
| sma_12_pxrel | P | F4 | 27.7441 | 605094 | 0.00115056 | False | numeric |
| ret_3d | P | F3 | 27.5603 | 605274 | 0.00085343 | False | numeric |
| vix_close | P | F7 | 27.4122 | 605791 | 0 | False | numeric |
| ema_34_pxrel | P | F4 | 27.3937 | 604166 | 0.00268244 | False | numeric |
| sma_8_pxrel | P | F4 | 27.1196 | 605242 | 0.000906253 | False | numeric |
| days_since_50_200_cross | P | F4 | 27.0201 | 596594 | 0.0151818 | False | numeric |
| ret_2d | P | F3 | 26.8728 | 605275 | 0.000851779 | False | numeric |
| ema_20_pxrel | P | F4 | 26.713 | 604661 | 0.00186533 | False | numeric |
| is_monthly_opex | C | F1 | 26.4755 | 735743 |  |  | categorical |
| ema_26_pxrel | P | F4 | 26.4164 | 604528 | 0.00208488 | False | numeric |
| dollar_vol_20d_avg | P | F3 | 26.3222 | 604661 | 0.00186533 | False | numeric |
| sma_20_pxrel | P | F4 | 26.3222 | 604661 | 0.00186533 | False | numeric |
| ema_21_pxrel | P | F4 | 26.195 | 604645 | 0.00189174 | False | numeric |
| sma_100_pxrel | C | F4 | 25.9869 | 730416 | 0.0072403 | False | numeric |

### Top-40 |z| side=C

| metric | side | family | z_max_abs | n_nonnull | null_share | merge_fired | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| otm_pct | C | F1 | 86.9246 | 734825 | 0.00124772 | False | numeric |
| moneyness_pct | C | F1 | 86.9246 | 734825 | 0.00124772 | False | numeric |
| premium_over_spot | C | F1 | 84.889 | 734825 | 0.00124772 | False | numeric |
| hl_range_pct | C | F2 | 81.0858 | 735743 | 0 | False | numeric |
| entry_close | C | F1 | 73.2144 | 735743 | 0 | False | numeric |
| close_vs_open_pct | C | F2 | 65.373 | 735743 | 0 | False | numeric |
| entry_dollar_vol | C | F2 | 51.7094 | 735743 | 0 | False | numeric |
| strike | C | F1 | 41.3289 | 735743 | 0 | False | numeric |
| entry_transactions | C | F2 | 35.3229 | 735743 | 0 | False | numeric |
| sector | C | F8 | 35.3098 | 735743 |  |  | categorical |
| spy_ret_5d | C | F7 | 30.1622 | 735743 | 0 | False | numeric |
| spy_ret_20d | C | F7 | 27.4001 | 735743 | 0 | False | numeric |
| is_monthly_opex | C | F1 | 26.4755 | 735743 |  |  | categorical |
| sma_100_pxrel | C | F4 | 25.9869 | 730416 | 0.0072403 | False | numeric |
| ema_100_pxrel | C | F4 | 24.6311 | 730416 | 0.0072403 | False | numeric |
| sma_50_slope5d | C | F4 | 24.4714 | 732483 | 0.0044309 | False | numeric |
| sma_50_pxrel | C | F4 | 24.3989 | 732713 | 0.00411829 | False | numeric |
| ret_60d | C | F3 | 24.1717 | 732173 | 0.00485224 | False | numeric |
| ema_50_slope5d | C | F4 | 24.02 | 732483 | 0.0044309 | False | numeric |
| ema_50_pxrel | C | F4 | 23.9613 | 732713 | 0.00411829 | False | numeric |
| score_stoch | C | F6 | 23.7407 | 723942 | 0.0160396 | False | numeric |
| dist_from_52w_high | C | F3 | 23.62 | 719649 | 0.0218745 | False | numeric |
| dist_from_20d_high | C | F3 | 23.2759 | 734285 | 0.00198167 | False | numeric |
| ema_26_pxrel | C | F4 | 22.0546 | 734078 | 0.00226302 | False | numeric |
| ema_34_pxrel | C | F4 | 21.7367 | 733597 | 0.00291678 | False | numeric |
| sma_34_slope5d | C | F4 | 21.4874 | 733320 | 0.00329327 | False | numeric |
| trin | C | F7 | 21.2214 | 735743 | 0 | False | numeric |
| atr14_pct | C | F3 | 21.1718 | 734524 | 0.00165683 | False | numeric |
| bb_pctb | C | F5 | 21.049 | 734285 | 0.00198167 | False | numeric |
| ema_34_slope5d | C | F4 | 20.6398 | 733320 | 0.00329327 | False | numeric |
| ema_100_slope5d | C | F4 | 20.6276 | 730146 | 0.00760728 | False | numeric |
| sma_34_pxrel | C | F4 | 20.4793 | 733597 | 0.00291678 | False | numeric |
| vix_close | C | F7 | 20.3478 | 735743 | 0 | False | numeric |
| ema_21_pxrel | C | F4 | 20.0876 | 734273 | 0.00199798 | False | numeric |
| macd_hist | C | F5 | 20.0046 | 733597 | 0.00291678 | False | numeric |
| ema_20_pxrel | C | F4 | 19.936 | 734285 | 0.00198167 | False | numeric |
| sma_26_pxrel | C | F4 | 19.8684 | 734078 | 0.00226302 | False | numeric |
| sma_150_pxrel | C | F4 | 19.7638 | 727592 | 0.0110786 | False | numeric |
| regime_composite | C | F7 | 19.3797 | 735743 | 0 | False | numeric |
| regime_multiplier | C | F7 | 19.3797 | 735743 | 0 | False | numeric |

### Top-40 |z| side=P

| metric | side | family | z_max_abs | n_nonnull | null_share | merge_fired | kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| otm_pct | P | F1 | 76.8137 | 605127 | 0.00109609 | False | numeric |
| moneyness_pct | P | F1 | 76.8129 | 605127 | 0.00109609 | False | numeric |
| premium_over_spot | P | F1 | 75.8129 | 605127 | 0.00109609 | False | numeric |
| entry_close | P | F1 | 59.7272 | 605791 | 0 | False | numeric |
| hl_range_pct | P | F2 | 56.2451 | 605791 | 0 | False | numeric |
| close_vs_open_pct | P | F2 | 47.0091 | 605791 | 0 | False | numeric |
| strike | P | F1 | 41.5637 | 605791 | 0 | False | numeric |
| mcclellan_oscillator | P | F7 | 33.3941 | 605791 | 0 | False | numeric |
| spy_ret_20d | P | F7 | 33.3288 | 605791 | 0 | False | numeric |
| entry_dollar_vol | P | F2 | 33.2336 | 605791 | 0 | False | numeric |
| regime_composite | P | F7 | 32.9724 | 605791 | 0 | False | numeric |
| regime_multiplier | P | F7 | 32.9724 | 605791 | 0 | False | numeric |
| ret_1d | P | F3 | 29.8415 | 605275 | 0.000851779 | False | numeric |
| ema_5_pxrel | P | F4 | 29.5602 | 605273 | 0.00085508 | False | numeric |
| ema_8_pxrel | P | F4 | 29.4946 | 605242 | 0.000906253 | False | numeric |
| bb_pctb | P | F5 | 29.4641 | 604661 | 0.00186533 | False | numeric |
| ema_9_pxrel | P | F4 | 28.6568 | 605160 | 0.00104161 | False | numeric |
| ema_10_pxrel | P | F4 | 28.4338 | 605114 | 0.00111755 | False | numeric |
| sma_10_pxrel | P | F4 | 28.2775 | 605114 | 0.00111755 | False | numeric |
| sma_5_pxrel | P | F4 | 28.1853 | 605273 | 0.00085508 | False | numeric |
| ad_diff | P | F7 | 28.163 | 605791 | 0 | False | numeric |
| sma_9_pxrel | P | F4 | 28.0943 | 605160 | 0.00104161 | False | numeric |
| ema_12_pxrel | P | F4 | 27.7909 | 605094 | 0.00115056 | False | numeric |
| sma_12_pxrel | P | F4 | 27.7441 | 605094 | 0.00115056 | False | numeric |
| ret_3d | P | F3 | 27.5603 | 605274 | 0.00085343 | False | numeric |
| vix_close | P | F7 | 27.4122 | 605791 | 0 | False | numeric |
| ema_34_pxrel | P | F4 | 27.3937 | 604166 | 0.00268244 | False | numeric |
| sma_8_pxrel | P | F4 | 27.1196 | 605242 | 0.000906253 | False | numeric |
| days_since_50_200_cross | P | F4 | 27.0201 | 596594 | 0.0151818 | False | numeric |
| ret_2d | P | F3 | 26.8728 | 605275 | 0.000851779 | False | numeric |
| ema_20_pxrel | P | F4 | 26.713 | 604661 | 0.00186533 | False | numeric |
| ema_26_pxrel | P | F4 | 26.4164 | 604528 | 0.00208488 | False | numeric |
| dollar_vol_20d_avg | P | F3 | 26.3222 | 604661 | 0.00186533 | False | numeric |
| sma_20_pxrel | P | F4 | 26.3222 | 604661 | 0.00186533 | False | numeric |
| ema_21_pxrel | P | F4 | 26.195 | 604645 | 0.00189174 | False | numeric |
| gap_1d | P | F3 | 25.826 | 605275 | 0.000851779 | False | numeric |
| rsi | P | F5 | 25.7021 | 604853 | 0.00154839 | False | numeric |
| sma_21_pxrel | P | F4 | 25.2727 | 604645 | 0.00189174 | False | numeric |
| dist_from_20d_high | P | F3 | 24.4621 | 604661 | 0.00186533 | False | numeric |
| stoch | P | F5 | 24.2989 | 604748 | 0.00172172 | False | numeric |

## Section D -- Factors

35 clusters from 110 usable numeric metrics.

| cluster_rep | member | family | z_max_abs |
| --- | --- | --- | --- |
| ad_diff | ad_diff | F7 | 28.163 |
| atr14_pct | atr14_pct | F3 | 21.1718 |
| atr14_pct | realized_vol_10d | F3 | 18.6892 |
| atr14_pct | realized_vol_20d | F3 | 19.027 |
| atr14_pct | realized_vol_5d | F3 | 13.9487 |
| atr14_pct | realized_vol_60d | F3 | 18.3169 |
| close_vs_open_pct | close_vs_open_pct | F2 | 65.373 |
| days_since_50_200_cross | days_since_50_200_cross | F4 | 27.0201 |
| days_to_next_earnings | days_since_last_earnings | F8 | 13.335 |
| days_to_next_earnings | days_to_next_earnings | F8 | 17.1298 |
| dist_from_20d_high | dist_from_20d_high | F3 | 24.4621 |
| dist_from_20d_low | dist_from_20d_low | F3 | 23.2272 |
| dist_from_52w_high | dist_from_52w_high | F3 | 23.62 |
| dollar_vol_20d_avg | dollar_vol_20d_avg | F3 | 26.3222 |
| dollar_vol_ratio | dollar_vol_ratio | F3 | 9.32188 |
| dte_trading | dte_calendar | F1 | 22.0677 |
| dte_trading | dte_trading | F1 | 22.9892 |
| ema_5_pxrel | bb_pctb | F5 | 29.4641 |
| ema_5_pxrel | dist_from_52w_low | F3 | 17.3458 |
| ema_5_pxrel | ema_100_pxrel | F4 | 24.6311 |
| ema_5_pxrel | ema_100_slope5d | F4 | 20.6276 |
| ema_5_pxrel | ema_10_pxrel | F4 | 28.4338 |
| ema_5_pxrel | ema_10_slope5d | F4 | 21.0601 |
| ema_5_pxrel | ema_12_pxrel | F4 | 27.7909 |
| ema_5_pxrel | ema_12_slope5d | F4 | 22.0571 |
| ema_5_pxrel | ema_150_pxrel | F4 | 20.4322 |
| ema_5_pxrel | ema_150_slope5d | F4 | 21.154 |
| ema_5_pxrel | ema_200_pxrel | F4 | 23.2864 |
| ema_5_pxrel | ema_200_slope5d | F4 | 21.2543 |
| ema_5_pxrel | ema_20_pxrel | F4 | 26.713 |
| ema_5_pxrel | ema_20_slope5d | F4 | 20.2413 |
| ema_5_pxrel | ema_21_pxrel | F4 | 26.195 |
| ema_5_pxrel | ema_21_slope5d | F4 | 20.6165 |
| ema_5_pxrel | ema_26_pxrel | F4 | 26.4164 |
| ema_5_pxrel | ema_26_slope5d | F4 | 20.5974 |
| ema_5_pxrel | ema_34_pxrel | F4 | 27.3937 |
| ema_5_pxrel | ema_34_slope5d | F4 | 20.6398 |
| ema_5_pxrel | ema_50_pxrel | F4 | 24.2852 |
| ema_5_pxrel | ema_50_slope5d | F4 | 24.02 |
| ema_5_pxrel | ema_5_pxrel | F4 | 29.5602 |
| ema_5_pxrel | ema_5_slope5d | F4 | 20.2768 |
| ema_5_pxrel | ema_8_pxrel | F4 | 29.4946 |
| ema_5_pxrel | ema_8_slope5d | F4 | 20.4977 |
| ema_5_pxrel | ema_9_pxrel | F4 | 28.6568 |
| ema_5_pxrel | ema_9_slope5d | F4 | 20.6248 |
| ema_5_pxrel | ma_stack_count | F4 | 18.5803 |
| ema_5_pxrel | prior_week_return | F3 | 14.3748 |
| ema_5_pxrel | ret_10d | F3 | 22.729 |
| ema_5_pxrel | ret_20d | F3 | 19.2232 |
| ema_5_pxrel | ret_2d | F3 | 26.8728 |
| ema_5_pxrel | ret_3d | F3 | 27.5603 |
| ema_5_pxrel | ret_5d | F3 | 19.9188 |
| ema_5_pxrel | ret_60d | F3 | 24.1717 |
| ema_5_pxrel | rsi | F5 | 25.7021 |
| ema_5_pxrel | score_stoch | F6 | 23.7407 |
| ema_5_pxrel | score_trend | F6 | 19.9289 |
| ema_5_pxrel | sma_100_pxrel | F4 | 25.9869 |
| ema_5_pxrel | sma_100_slope5d | F4 | 11.8591 |
| ema_5_pxrel | sma_10_pxrel | F4 | 28.2775 |
| ema_5_pxrel | sma_10_slope5d | F4 | 17.5592 |
| ema_5_pxrel | sma_12_pxrel | F4 | 27.7441 |
| ema_5_pxrel | sma_12_slope5d | F4 | 22.1508 |
| ema_5_pxrel | sma_150_pxrel | F4 | 19.7638 |
| ema_5_pxrel | sma_150_slope5d | F4 | 11.3401 |
| ema_5_pxrel | sma_200_pxrel | F4 | 18.2858 |
| ema_5_pxrel | sma_200_slope5d | F4 | 19.6422 |
| ema_5_pxrel | sma_20_pxrel | F4 | 26.3222 |
| ema_5_pxrel | sma_20_slope5d | F4 | 14.5096 |
| ema_5_pxrel | sma_21_pxrel | F4 | 25.2727 |
| ema_5_pxrel | sma_21_slope5d | F4 | 16.7804 |
| ema_5_pxrel | sma_26_pxrel | F4 | 23.962 |
| ema_5_pxrel | sma_26_slope5d | F4 | 15.9921 |
| ema_5_pxrel | sma_34_pxrel | F4 | 24.0161 |
| ema_5_pxrel | sma_34_slope5d | F4 | 21.4874 |
| ema_5_pxrel | sma_50_pxrel | F4 | 24.3989 |
| ema_5_pxrel | sma_50_slope5d | F4 | 24.4714 |
| ema_5_pxrel | sma_5_pxrel | F4 | 28.1853 |
| ema_5_pxrel | sma_5_slope5d | F4 | 18.8236 |
| ema_5_pxrel | sma_8_pxrel | F4 | 27.1196 |
| ema_5_pxrel | sma_8_slope5d | F4 | 17.0127 |
| ema_5_pxrel | sma_9_pxrel | F4 | 28.0943 |
| ema_5_pxrel | sma_9_slope5d | F4 | 15.4828 |
| ema_5_pxrel | stoch | F5 | 24.2989 |
| entry_close | entry_close | F1 | 73.2144 |
| entry_dollar_vol | entry_dollar_vol | F2 | 51.7094 |
| entry_transactions | entry_transactions | F2 | 35.3229 |
| entry_transactions | entry_volume | F2 | 17.7495 |
| gap_1d | gap_1d | F3 | 25.826 |
| hl_range_pct | hl_range_pct | F2 | 81.0858 |
| macd_hist | macd_hist | F5 | 20.0046 |
| mcclellan_oscillator | mcclellan_oscillator | F7 | 33.3941 |
| moneyness_pct | moneyness_pct | F1 | 86.9246 |
| otm_pct | otm_pct | F1 | 86.9246 |
| pre_regime | overall | F6 | 11.9296 |
| pre_regime | pre_boost | F6 | 11.9296 |
| pre_regime | pre_regime | F6 | 13.5193 |
| premium_over_spot | premium_over_spot | F1 | 84.889 |
| regime_multiplier | regime_composite | F7 | 32.9724 |
| regime_multiplier | regime_multiplier | F7 | 32.9724 |
| ret_1d | ret_1d | F3 | 29.8415 |
| score_bb | score_bb | F6 | 11.6655 |
| score_macd | score_macd | F6 | 12.5284 |
| score_rsi | score_rsi | F6 | 23.8396 |
| score_technical_alignment | score_technical_alignment | F6 | 14.2401 |
| score_volume | score_volume | F6 | 16.899 |
| spy_ret_20d | spy_ret_20d | F7 | 33.3288 |
| spy_ret_5d | spy_ret_5d | F7 | 30.1622 |
| strike | strike | F1 | 41.5637 |
| trin | trin | F7 | 23.7106 |
| vix_close | vix_close | F7 | 27.4122 |

side=C: mean AUC logistic=0.726701877987231, mean AUC gbt=0.7121616445964877, shuffled control=0.5042198310554098 (n_permutations=10), base_rate=0.08896720729928793
side=P: mean AUC logistic=0.7094215222123947, mean AUC gbt=0.7040390127997622, shuffled control=0.48602364888250044 (n_permutations=10), base_rate=0.08189458080427078

## Section E -- Ablation

### E1 leave-family-out

| family | side | cols_dropped | n_features_full | n_features_ablated | base_auc | ablated_auc | delta_auc | base_lift_top_decile | ablated_lift_top_decile | delta_lift_top_decile | delta_direction | n_rows_used |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | C | 7 | 35 | 29 | 0.712162 | 0.655915 | -0.0562465 | 1.61937 | 1.4558 | -0.163572 | down | 218889 |
| F1 | P | 7 | 35 | 29 | 0.704039 | 0.660937 | -0.0431016 | 1.6721 | 1.74247 | 0.0703734 | down | 181111 |
| F2 | C | 5 | 35 | 31 | 0.712162 | 0.713726 | 0.00156412 | 1.61937 | 1.62188 | 0.00250231 | up | 218889 |
| F2 | P | 5 | 35 | 31 | 0.704039 | 0.70409 | 5.11838e-05 | 1.6721 | 1.74204 | 0.0699438 | up | 181111 |
| F3 | C | 20 | 35 | 27 | 0.712162 | 0.710612 | -0.00154984 | 1.61937 | 1.55751 | -0.0618636 | down | 218889 |
| F3 | P | 20 | 35 | 27 | 0.704039 | 0.705886 | 0.00184687 | 1.6721 | 1.79246 | 0.120365 | up | 181111 |
| F4 | C | 54 | 35 | 40 | 0.712162 | 0.719048 | 0.00688618 | 1.61937 | 1.67789 | 0.0585166 | up | 218889 |
| F4 | P | 54 | 35 | 40 | 0.704039 | 0.703462 | -0.00057684 | 1.6721 | 1.78088 | 0.108789 | down | 181111 |
| F5 | C | 4 | 35 | 34 | 0.712162 | 0.712129 | -3.29604e-05 | 1.61937 | 1.59175 | -0.0276202 | down | 218889 |
| F5 | P | 4 | 35 | 34 | 0.704039 | 0.711108 | 0.00706877 | 1.6721 | 1.8613 | 0.189205 | up | 181111 |
| F6 | C | 10 | 35 | 29 | 0.712162 | 0.711674 | -0.000487431 | 1.61937 | 1.64367 | 0.0242964 | down | 218889 |
| F6 | P | 10 | 35 | 29 | 0.704039 | 0.706089 | 0.0020503 | 1.6721 | 1.77648 | 0.104387 | up | 181111 |
| F7 | C | 8 | 35 | 28 | 0.712162 | 0.745196 | 0.0330341 | 1.61937 | 2.04691 | 0.427535 | up | 218889 |
| F7 | P | 8 | 35 | 28 | 0.704039 | 0.714385 | 0.010346 | 1.6721 | 1.8372 | 0.165106 | up | 181111 |
| F8 | C | 2 | 35 | 34 | 0.712162 | 0.714985 | 0.00282346 | 1.61937 | 1.64879 | 0.0294196 | up | 218889 |
| F8 | P | 2 | 35 | 34 | 0.704039 | 0.710045 | 0.0060063 | 1.6721 | 1.79311 | 0.12101 | up | 181111 |

### E2 leave-metric-out (top-10 by |z|)

| metric | side | base_auc | ablated_auc | delta_auc | base_lift_top_decile | ablated_lift_top_decile | delta_lift_top_decile | n_rows_used |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| otm_pct | C | 0.712162 | 0.713584 | 0.00142271 | 1.61937 | 1.62869 | 0.00931413 | 218889 |
| otm_pct | P | 0.704039 | 0.701215 | -0.00282416 | 1.6721 | 1.69245 | 0.0203563 | 181111 |
| moneyness_pct | C | 0.712162 | 0.713584 | 0.00142271 | 1.61937 | 1.62869 | 0.00931413 | 218889 |
| moneyness_pct | P | 0.704039 | 0.704582 | 0.000542896 | 1.6721 | 1.77842 | 0.106324 | 181111 |
| premium_over_spot | C | 0.712162 | 0.709431 | -0.00273023 | 1.61937 | 1.51761 | -0.101762 | 218889 |
| premium_over_spot | P | 0.704039 | 0.706377 | 0.00233806 | 1.6721 | 1.8081 | 0.136008 | 181111 |
| hl_range_pct | C | 0.712162 | 0.712671 | 0.000509443 | 1.61937 | 1.64113 | 0.0217596 | 218889 |
| hl_range_pct | P | 0.704039 | 0.703702 | -0.000336586 | 1.6721 | 1.84877 | 0.176671 | 181111 |
| entry_close | C | 0.712162 | 0.713314 | 0.00115241 | 1.61937 | 1.64039 | 0.0210164 | 218889 |
| entry_close | P | 0.704039 | 0.708788 | 0.00474941 | 1.6721 | 1.81748 | 0.145386 | 181111 |
| close_vs_open_pct | C | 0.712162 | 0.715025 | 0.00286341 | 1.61937 | 1.64246 | 0.0230832 | 218889 |
| close_vs_open_pct | P | 0.704039 | 0.708822 | 0.00478319 | 1.6721 | 1.80557 | 0.133471 | 181111 |
| entry_dollar_vol | C | 0.712162 | 0.713582 | 0.00142022 | 1.61937 | 1.62869 | 0.00931413 | 218889 |
| entry_dollar_vol | P | 0.704039 | 0.704998 | 0.000959273 | 1.6721 | 1.79345 | 0.121356 | 181111 |
| strike | C | 0.712162 | 0.715602 | 0.00343987 | 1.61937 | 1.63198 | 0.0126087 | 218889 |
| strike | P | 0.704039 | 0.710445 | 0.0064055 | 1.6721 | 1.88944 | 0.217341 | 181111 |
| entry_transactions | C | 0.712162 | 0.714274 | 0.0021122 | 1.61937 | 1.59324 | -0.0261342 | 218889 |
| entry_transactions | P | 0.704039 | 0.706238 | 0.00219916 | 1.6721 | 1.79857 | 0.126473 | 181111 |
| mcclellan_oscillator | C | 0.712162 | 0.716138 | 0.00397683 | 1.61937 | 1.67751 | 0.0581364 | 218889 |
| mcclellan_oscillator | P | 0.704039 | 0.703379 | -0.000659538 | 1.6721 | 1.77594 | 0.10384 | 181111 |

### E3 rule distillation: 255 candidates generated, 210 meet N>=1000 floor, 6 selected

| label | conjuncts | n | winner_rate | lift | verdict | year_same_dir | n_years | thresholds_ok | earnings_ok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| moneyness_pct>=P80(0.03958) AND hl_range_pct>=P80(127.3) AND sector==technology AND is_monthly_opex==false | 4 | 25663 | 0.207419 | 2.41822 | HOLD | 5 | 5 | True | True |
| otm_pct>=P80(0.0576) AND moneyness_pct>=P80(0.03958) AND hl_range_pct>=P80(127.3) AND sector==technology | 4 | 25835 | 0.200542 | 2.33804 | HOLD | 5 | 5 | True | True |
| otm_pct>=P80(0.0576) AND hl_range_pct>=P80(127.3) AND cp==C AND sector==technology | 4 | 25835 | 0.200542 | 2.33804 | HOLD | 5 | 5 | True | True |
| moneyness_pct>=P80(0.03958) AND hl_range_pct>=P80(127.3) AND cp==C AND sector==technology | 4 | 33438 | 0.200072 | 2.33256 | HOLD | 5 | 5 | True | True |
| otm_pct>=P80(0.0576) AND moneyness_pct>=P80(0.03958) AND hl_range_pct>=P80(127.3) AND is_monthly_opex==false | 4 | 44342 | 0.193834 | 2.25984 | HOLD | 5 | 5 | True | True |
| otm_pct>=P80(0.0576) AND hl_range_pct>=P80(127.3) AND cp==C AND is_monthly_opex==false | 4 | 44342 | 0.193834 | 2.25984 | HOLD | 5 | 5 | True | True |

