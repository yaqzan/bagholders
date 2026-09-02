# Polygon BS-IV Panel Audit -- adjusted-vs-as-traded spot contamination

Measurement only. No verdict on prior findings is offered here.

Source panel : `.cache/polygon_iv/iv_ledger_polygon.parquet`
Built by     : `experiments/data_ingest/polygon_iv_ingest.py`
Audit script : `experiments/polygon_real_premium/panel_audit.py`
Spot method  : `experiments/polygon_real_premium/pull.py: load_unadj_spots`
(yfinance `auto_adjust=False` Close x cumulative product of FORWARD split
ratios), reused verbatim -- the same function the corrected real-premium
ledger uses.

==============================================================================
POLYGON BS-IV PANEL AUDIT -- adjusted-vs-as-traded spot contamination
==============================================================================

panel        : C:/Development/Trader/.cache/polygon_iv/iv_ledger_polygon.parquet
rows         : 8643   symbols: 669   dates: 2022-08-01 .. 2026-05-15

[timing] as-traded spot resolution: 2.0s for 669 panel symbols (666 resolved; cache holds 699 symbols incl. the seeded real-premium universe)
[timing] price_history read: 3.0s (669 symbols)

## 0. Coverage

| quantity | rows | pct of panel |
|---|---|---|
| panel rows | 8643 | 100.0% |
| price_history adjusted close resolved | 8643 | 100.0% |
| yfinance as-traded spot resolved | 8529 | 98.7% |
| BOTH resolved (audit sample) | 8529 | 98.7% |

Symbols with no yfinance as-traded series (delisted / renamed): 3 -- e.g. AL, APLS, CSGS

## 1. Adjustment drift: adj_factor = as_traded_close / price_history.close

A no-dividend, no-split name has adj_factor exactly 1.0, so the MEDIAN row
is uncontaminated by construction; the damage lives in the upper tail. The
'DRIFTED ONLY' row below conditions on adj_factor > 1.001.

| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 8529 | 0.9616 | 1.0000 | 1.0000 | 1.0000 | 1.0073 | 1.0411 | 1.5221 | 1.0139 |
| DRIFTED ONLY (>1.001) | 2679 | 1.0010 | 1.0039 | 1.0096 | 1.0220 | 1.0530 | 1.1052 | 1.5221 | 1.0443 |
| 2022 | 679 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0874 | 1.1459 | 1.3367 | 1.0465 |
| 2023 | 1008 | 0.9999 | 1.0000 | 1.0000 | 1.0000 | 1.0256 | 1.0849 | 1.4007 | 1.0234 |
| 2024 | 2510 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0165 | 1.0527 | 1.5221 | 1.0178 |
| 2025 | 2809 | 0.9616 | 1.0000 | 1.0000 | 1.0000 | 1.0063 | 1.0187 | 1.5065 | 1.0060 |
| 2026 | 1523 | 0.9970 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0033 | 1.5001 | 1.0013 |

Rows with ANY upward drift (adj_factor > 1.001): 2679 / 8529 (31.4%)
Distinct symbols with median adj_factor > 1.001: 210 of 662

| threshold | rows above |
|---|---|
| adj_factor > 1.02 | 1389 / 8529 (16.3%) |
| adj_factor > 1.05 | 727 / 8529 (8.5%) |
| adj_factor > 1.10 | 310 / 8529 (3.6%) |
| adj_factor > 1.25 | 45 / 8529 (0.5%) |
| adj_factor < 0.98 (adjusted ABOVE as-traded) | 1 / 8529 (0.0%) |

Rows by year and threshold (blast radius):

| year | n | >1.02 | >1.05 | >1.10 | >1.25 | median |
|---|---|---|---|---|---|---|
| 2022 | 679 | 44.5% | 35.1% | 20.6% | 1.8% | 1.0000 |
| 2023 | 1008 | 27.1% | 16.5% | 7.8% | 1.0% | 1.0000 |
| 2024 | 2510 | 22.0% | 10.6% | 3.3% | 0.8% | 1.0000 |
| 2025 | 2809 | 9.0% | 2.0% | 0.3% | 0.1% | 1.0000 |
| 2026 | 1523 | 0.6% | 0.1% | 0.1% | 0.1% | 1.0000 |

## 2. Strike error: what the 'ATM' contract actually was

| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| true_moneyness (strike / as_traded_spot) | 8529 | 0.6485 | 0.9563 | 0.9853 | 0.9979 | 1.0048 | 1.0173 | 1.9685 | 0.9925 |
| intended_moneyness (strike / adjusted_close) | 8529 | 0.6596 | 0.9855 | 0.9953 | 1.0011 | 1.0086 | 1.0265 | 1.9685 | 1.0055 |

For a CALL, moneyness < 1 means IN the money.

| condition | rows |
|---|---|
| ITM at all (true_moneyness < 1.000) | 4922 / 8529 (57.7%) |
| more than 5% ITM (true_moneyness < 0.95) | 733 / 8529 (8.6%) |
| more than 10% ITM (true_moneyness < 0.90) | 252 / 8529 (3.0%) |
| more than 25% ITM (true_moneyness < 0.75) | 11 / 8529 (0.1%) |
| more than 5% OTM (true_moneyness > 1.05) | 261 / 8529 (3.1%) |

| year | n | median true_moneyness | >5% ITM | >10% ITM | >25% ITM |
|---|---|---|---|---|---|
| 2022 | 679 | 0.9866 | 30.3% | 16.1% | 0.3% |
| 2023 | 1008 | 0.9986 | 13.3% | 4.8% | 0.0% |
| 2024 | 2510 | 0.9970 | 10.2% | 2.5% | 0.1% |
| 2025 | 2809 | 0.9980 | 4.0% | 1.0% | 0.2% |
| 2026 | 1523 | 0.9999 | 1.6% | 0.3% | 0.0% |

## 3. Calm-name hypothesis: is drift concentrated in low-volatility names?

Volatility measure: causal 60-trading-day realized vol of the price_history
close series ending on the signal date, annualized (x sqrt(252)), in percent.
Cross-checked against the panel's own stored `vol_pct` (daily sigma, percent).

Row level (n=8528):  pearson(adj_factor, realized_vol) = -0.1627   spearman = -0.3244
Row level (n=8529):  pearson(adj_factor, panel vol_pct) = -0.1627   spearman = -0.3243
Symbol level (n=661 symbols, per-symbol medians):  pearson = -0.1979   spearman = -0.4016

Symbol-level realized-vol terciles (cuts at 29.8% / 51.1% annualized):

| tercile | symbols | median ann.vol | median adj_factor | mean adj_factor | pct symbols adj_factor>1.05 |
|---|---|---|---|---|---|
| T1 calmest | 221 | 23.9% | 1.0061 | 1.0258 | 14.9% |
| T2 mid | 220 | 38.2% | 1.0000 | 1.0129 | 9.1% |
| T3 most volatile | 220 | 70.8% | 1.0000 | 1.0032 | 1.8% |

Row-level distribution of adj_factor within each symbol tercile:

| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| T1 calmest | 2515 | 0.9835 | 1.0000 | 1.0000 | 1.0043 | 1.0266 | 1.0662 | 1.5221 | 1.0252 |
| T2 mid | 3124 | 0.9830 | 1.0000 | 1.0000 | 1.0000 | 1.0070 | 1.0396 | 1.5001 | 1.0140 |
| T3 most volatile | 2889 | 0.9616 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0027 | 1.1867 | 1.0039 |

15 highest-drift symbols (median adj_factor), with their realized vol:

| symbol | rows | median adj_factor | median ann.vol | vol tercile |
|---|---|---|---|---|
| BN | 26 | 1.5163 | 26.0% | T1 calmest |
| PFE | 1 | 1.2293 | 24.4% | T1 calmest |
| WPC | 5 | 1.2233 | 19.4% | T1 calmest |
| ABR | 1 | 1.2068 | 26.1% | T1 calmest |
| APLE | 3 | 1.1924 | 26.6% | T1 calmest |
| ET | 17 | 1.1892 | 18.2% | T1 calmest |
| APAM | 4 | 1.1816 | 27.1% | T1 calmest |
| BKE | 14 | 1.1662 | 31.3% | T2 mid |
| STLA | 13 | 1.1629 | 26.4% | T1 calmest |
| DVN | 12 | 1.1599 | 54.9% | T3 most volatile |
| UWMC | 14 | 1.1521 | 43.0% | T2 mid |
| GIS | 4 | 1.1512 | 22.1% | T1 calmest |
| OKE | 19 | 1.1503 | 20.8% | T1 calmest |
| CALM | 22 | 1.1480 | 36.3% | T2 mid |
| CCOI | 9 | 1.1467 | 35.3% | T2 mid |

## 4. Cross-check against the corrected real-premium ledger

Overlap: 1848 (symbol, date) rows appear in BOTH the panel and the kept rows
of the corrected real-premium ledger.

NOTE ON COMPARABILITY: the two harnesses are not selection-identical.
The panel queries expiries in [d+20, d+45] and takes the nearest priced
strike at whatever expiry the chain returns first; the real ledger picks
the expiry whose calendar DTE is closest to 30 within [18,50] and then the
nearest strike. Rows are therefore split below into ALL overlap and the
SAME-EXPIRATION subset, which isolates the strike-selection effect.

### ALL overlap (n=1848)

| quantity | value |
|---|---|
| same expiration | 1032 / 1848 (55.8%) |
| SAME strike | 1334 / 1848 (72.2%) |
| DIFFERENT strike | 514 / 1848 (27.8%) |

| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| panel_strike / real_strike | 1848 | 0.7200 | 0.9619 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.1000 | 0.9896 |
| panel_entry_premium / real_entry_premium | 1848 | 0.3596 | 0.8412 | 0.9386 | 1.0000 | 1.0000 | 1.7223 | 8.2400 | 1.1746 |

| premium-ratio band | rows |
|---|---|
| panel premium > 1.25x real | 311 / 1848 (16.8%) |
| panel premium > 1.50x real | 222 / 1848 (12.0%) |
| panel premium > 2.00x real | 139 / 1848 (7.5%) |
| panel premium < 0.80x real | 106 / 1848 (5.7%) |

### SAME-EXPIRATION subset (n=1032)

| quantity | value |
|---|---|
| same expiration | 1032 / 1032 (100.0%) |
| SAME strike | 844 / 1032 (81.8%) |
| DIFFERENT strike | 188 / 1032 (18.2%) |

| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|---|---|
| panel_strike / real_strike | 1032 | 0.7200 | 0.9565 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9882 |
| panel_entry_premium / real_entry_premium | 1032 | 0.9884 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.8606 | 8.2400 | 1.2313 |

| premium-ratio band | rows |
|---|---|
| panel premium > 1.25x real | 172 / 1032 (16.7%) |
| panel premium > 1.50x real | 139 / 1032 (13.5%) |
| panel premium > 2.00x real | 88 / 1032 (8.5%) |
| panel premium < 0.80x real | 0 / 1032 (0.0%) |

Spot sanity: this audit's as-traded spot vs the real ledger's, on the
1816 overlapping rows -- max |ratio-1| = 0.00e+00, p99 = 0.00e+00 (they use the same
function, so this only checks the cache seeding).

Correlation between adj_factor and the panel/real premium ratio (n=1816): pearson 0.6711, spearman 0.5212

Strike disagreement CONDITIONED on drift (the headline cross-check):

| adj_factor filter | n | different strike | median premium ratio |
|---|---|---|---|
| <= 1.001 (undrifted) | 1205 | 137 (11.4%) | 1.0000 |
| > 1.001 | 611 | 375 (61.4%) | 1.2074 |
| > 1.02 | 322 | 256 (79.5%) | 1.7539 |
| > 1.05 | 156 | 138 (88.5%) | 2.4528 |
| > 1.10 | 64 | 57 (89.1%) | 2.9748 |

| adj_factor band | n | median premium ratio | median panel_strike/real_strike |
|---|---|---|---|
| (0.00, 1.02] | 1494 | 1.0000 | 1.0000 |
| (1.02, 1.05] | 166 | 1.3599 | 0.9730 |
| (1.05, 1.10] | 92 | 2.1143 | 0.9444 |
| (1.10, 1.25] | 54 | 2.6379 | 0.8929 |
| (1.25, 99.00] | 10 | 3.7127 | 0.8750 |

## 5. Blast radius by year

| year | panel rows | audited | >2% drift | >5% drift | >10% drift | ATM actually >5% ITM | ATM actually >10% ITM |
|---|---|---|---|---|---|---|---|
| 2022 | 687 | 679 | 302 (44.5%) | 238 (35.1%) | 140 (20.6%) | 206 (30.3%) | 109 (16.1%) |
| 2023 | 1027 | 1008 | 273 (27.1%) | 166 (16.5%) | 79 (7.8%) | 134 (13.3%) | 48 (4.8%) |
| 2024 | 2527 | 2510 | 553 (22.0%) | 267 (10.6%) | 82 (3.3%) | 257 (10.2%) | 64 (2.5%) |
| 2025 | 2858 | 2809 | 252 (9.0%) | 55 (2.0%) | 8 (0.3%) | 111 (4.0%) | 27 (1.0%) |
| 2026 | 1544 | 1523 | 9 (0.6%) | 1 (0.1%) | 1 (0.1%) | 25 (1.6%) | 4 (0.3%) |

| overall band | rows | audited | median adj_factor | >5% drift | median true_moneyness |
|---|---|---|---|---|---|
| 70-74 | 7293 | 7193 | 1.0000 | 8.9% | 0.9979 |
| 75-79 | 1092 | 1083 | 1.0000 | 6.5% | 0.9978 |
| 80-84 | 176 | 173 | 1.0000 | 4.0% | 0.9989 |
| 85+ | 82 | 80 | 1.0000 | 10.0% | 0.9970 |

Total wall clock: 5.3s
Generated: 2026-07-25 02:38:59
