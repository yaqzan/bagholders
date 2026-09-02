# Miss-Ledger Pattern Mining — ledger_v46_1825.parquet

Source: `C:\Development\Trader\.cache\miss_ledger\ledger_v46_1825.parquet`  •  Rows: **33,288**


## Methodology


Per-cohort (signal_type × score bin) we measure:

- **Baseline miss rate** = P(NOT TP) over all signals in cohort, where `TP` = barrier_outcome.result == 1 on 30 DTE option-aligned barriers (TP=+0.35, SL=-0.30 calls / -0.20 puts) at w=15d.

- For each (feature, value) bin: **lift** = P(miss|bin) / P(miss|baseline), **z** = standardized deviation under H0=baseline.

- Cells filtered to N≥50 (single feature) / N≥30 (pair). Sorted by |z|.

- High-z + lift>1 = bin **overrepresented in misses** (avoid signals here).

- High-z + lift<1 = bin **underrepresented** (signals here are systematically better than the cohort baseline).


---


## CALL 95+
*N=22 — skipped (insufficient data)*


## CALL 90+
*N=105 — skipped (insufficient data)*


## CALL 85+
**N = 433  •  baseline miss rate = 30.3%**  (TP rate 69.7%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_rsi | hi | 73 | 46.6% | 1.54 | +3.0 | 16.9% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_rsi=hi & b_bb=hi | 42 | 52.4% | 1.73 | +3.1 | 9.7% |
| b_rsi=hi & b_regime=BULL | 31 | 54.8% | 1.81 | +3.0 | 7.2% |
| b_rsi=hi & b_stoch=hi | 69 | 46.4% | 1.53 | +2.9 | 15.9% |


## CALL 80+
**N = 1,241  •  baseline miss rate = 28.5%**  (TP rate 71.5%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_ta | hi | 79 | 44.3% | 1.55 | +3.1 | 6.4% |
| b_vmag | lo | 413 | 22.0% | 0.77 | -2.9 | 33.3% |
| b_rsi | hi | 177 | 38.4% | 1.35 | +2.9 | 14.3% |
| b_sigma | vwide | 114 | 40.4% | 1.41 | +2.8 | 9.2% |
| b_vmag | mid | 360 | 34.7% | 1.22 | +2.6 | 29.0% |
| b_vmag | none | 151 | 19.2% | 0.67 | -2.5 | 12.2% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_rsi=hi & b_vsig=CONVICTION | 91 | 48.4% | 1.70 | +4.2 | 7.3% |
| b_vmag=lo & b_vsig=REJECTION | 131 | 13.0% | 0.45 | -3.9 | 10.6% |
| b_ta=mid & b_vmag=lo | 388 | 20.6% | 0.72 | -3.4 | 31.3% |
| b_vmag=lo & b_rsi=mid | 361 | 20.5% | 0.72 | -3.4 | 29.1% |
| b_ta=hi & b_vsig=CONVICTION | 48 | 50.0% | 1.75 | +3.3 | 3.9% |
| b_rsi=hi & b_brd=mid_lo | 77 | 45.5% | 1.59 | +3.3 | 6.2% |
| b_ta=hi & b_rsi=hi | 76 | 44.7% | 1.57 | +3.1 | 6.1% |
| b_sigma=vwide & b_brd=mid_lo | 47 | 46.8% | 1.64 | +2.8 | 3.8% |
| b_vmag=lo & b_sigma=tight | 85 | 15.3% | 0.54 | -2.7 | 6.8% |
| b_sigma=vwide & b_vsig=CONVICTION | 64 | 43.8% | 1.53 | +2.7 | 5.2% |
| b_ta=hi & b_brd=mid_lo | 32 | 50.0% | 1.75 | +2.7 | 2.6% |
| b_vmag=none & b_rsi=mid | 144 | 18.8% | 0.66 | -2.6 | 11.6% |
| b_ta=mid & b_vmag=none | 151 | 19.2% | 0.67 | -2.5 | 12.2% |
| b_ta=mid & b_sigma=vwide | 109 | 39.4% | 1.38 | +2.5 | 8.8% |


## CALL 75+
**N = 3,411  •  baseline miss rate = 34.0%**  (TP rate 66.0%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_vmag | lo | 1,039 | 28.6% | 0.84 | -3.7 | 30.5% |
| b_vmag | hi | 800 | 40.1% | 1.18 | +3.6 | 23.5% |
| b_sigma | vwide | 217 | 44.2% | 1.30 | +3.2 | 6.4% |
| b_brd | hi | 338 | 40.5% | 1.19 | +2.5 | 9.9% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_vmag=lo & b_bb=hi | 800 | 27.5% | 0.81 | -3.9 | 23.5% |
| b_vmag=lo & b_sigma=tight | 231 | 22.1% | 0.65 | -3.8 | 6.8% |
| b_vmag=hi & b_sigma=tight | 176 | 47.2% | 1.39 | +3.7 | 5.2% |
| b_vmag=lo & b_sigma=mid | 534 | 26.6% | 0.78 | -3.6 | 15.7% |
| b_vmag=lo & b_mis_stress=off | 717 | 27.8% | 0.82 | -3.6 | 21.0% |
| b_vmag=lo & b_vsig=REJECTION | 275 | 24.4% | 0.72 | -3.4 | 8.1% |
| b_sigma=vwide & b_brd=mid_lo | 89 | 50.6% | 1.49 | +3.3 | 2.6% |
| b_vmag=mid & b_brd=hi | 132 | 47.0% | 1.38 | +3.1 | 3.9% |
| b_vmag=hi & b_vsig=CONVICTION | 534 | 40.4% | 1.19 | +3.1 | 15.7% |
| b_vmag=hi & b_mis_stress=off | 521 | 40.5% | 1.19 | +3.1 | 15.3% |
| b_sigma=vwide & b_bb=mid | 56 | 53.6% | 1.57 | +3.1 | 1.6% |
| b_vsig=CONVICTION & b_bb=mid | 565 | 39.8% | 1.17 | +2.9 | 16.6% |
| b_brd=hi & b_vsig=CONVICTION | 204 | 43.6% | 1.28 | +2.9 | 6.0% |
| b_vmag=lo & b_brd=mid_lo | 374 | 27.0% | 0.79 | -2.9 | 11.0% |
| b_vmag=hi & b_brd=mid_lo | 277 | 41.9% | 1.23 | +2.8 | 8.1% |


## CALL 70+
**N = 22,776  •  baseline miss rate = 40.6%**  (TP rate 59.4%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_stoch | hi | 7,237 | 37.4% | 0.92 | -5.4 | 31.8% |
| b_stoch | lo | 12,398 | 42.8% | 1.06 | +5.1 | 54.4% |
| b_wadj | neg | 418 | 51.7% | 1.27 | +4.6 | 1.8% |
| b_sigma | vwide | 2,447 | 45.2% | 1.11 | +4.6 | 10.7% |
| b_vmag | hi | 3,280 | 44.2% | 1.09 | +4.3 | 14.4% |
| b_vmag | lo | 10,126 | 38.5% | 0.95 | -4.2 | 44.5% |
| b_vsig | THIN_AIR | 5,112 | 37.9% | 0.93 | -3.9 | 22.4% |
| b_rsi | lo | 642 | 47.8% | 1.18 | +3.7 | 2.8% |
| b_vsig | CONVICTION | 10,387 | 42.4% | 1.04 | +3.7 | 45.6% |
| b_wadj | mid | 2,225 | 36.7% | 0.91 | -3.7 | 9.8% |
| b_vmag | mid | 6,849 | 42.6% | 1.05 | +3.4 | 30.1% |
| b_sigma | tight | 3,734 | 38.0% | 0.94 | -3.2 | 16.4% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_stoch=hi & b_sigma=mid | 3,526 | 34.7% | 0.86 | -7.1 | 15.5% |
| b_stoch=lo & b_vmag=mid | 4,409 | 45.4% | 1.12 | +6.6 | 19.4% |
| b_stoch=lo & b_vsig=CONVICTION | 6,761 | 44.3% | 1.09 | +6.3 | 29.7% |
| b_sigma=mid & b_vsig=THIN_AIR | 2,467 | 34.5% | 0.85 | -6.1 | 10.8% |
| b_stoch=lo & b_wadj=mild | 10,339 | 43.5% | 1.07 | +6.1 | 45.4% |
| b_stoch=hi & b_rsi=mid | 5,251 | 36.6% | 0.90 | -5.8 | 23.1% |
| b_sigma=mid & b_vmag=lo | 4,980 | 36.5% | 0.90 | -5.8 | 21.9% |
| b_stoch=lo & b_vmag=hi | 2,002 | 46.6% | 1.15 | +5.5 | 8.8% |
| b_stoch=hi & b_wadj=mild | 6,879 | 37.4% | 0.92 | -5.4 | 30.2% |
| b_sigma=vwide & b_vsig=THIN_AIR | 719 | 50.1% | 1.23 | +5.2 | 3.2% |
| b_wadj=mild & b_sigma=vwide | 2,074 | 46.0% | 1.14 | +5.1 | 9.1% |
| b_sigma=tight & b_vmag=lo | 1,524 | 34.4% | 0.85 | -4.9 | 6.7% |
| b_wadj=neg & b_vsig=CONVICTION | 201 | 56.2% | 1.39 | +4.5 | 0.9% |
| b_vmag=lo & b_rsi=mid | 9,090 | 38.3% | 0.94 | -4.5 | 39.9% |
| b_wadj=neg & b_vmag=mid | 148 | 58.1% | 1.43 | +4.3 | 0.6% |


## PUT  <25
**N = 10,512  •  baseline miss rate = 48.6%**  (TP rate 51.4%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_trend | hi | 659 | 36.7% | 0.76 | -6.1 | 6.3% |
| b_regime | HEALTHY | 1,524 | 53.3% | 1.10 | +3.7 | 14.5% |
| b_brd | vhi | 132 | 63.6% | 1.31 | +3.5 | 1.3% |
| b_stoch | mid | 1,666 | 44.4% | 0.91 | -3.4 | 15.8% |
| b_regime | BULL | 608 | 41.6% | 0.86 | -3.4 | 5.8% |
| b_wadj | pos | 451 | 56.3% | 1.16 | +3.3 | 4.3% |
| b_sigma | tight | 636 | 42.1% | 0.87 | -3.2 | 6.1% |
| b_vmag | none | 1,497 | 44.8% | 0.92 | -2.9 | 14.2% |
| b_bb | mid | 5,256 | 46.7% | 0.96 | -2.7 | 50.0% |
| b_macd | hi | 4,282 | 50.6% | 1.04 | +2.6 | 40.7% |
| b_sigma | vwide | 2,247 | 51.3% | 1.06 | +2.6 | 21.4% |
| b_stoch | hi | 6,368 | 50.2% | 1.03 | +2.5 | 60.6% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_stoch=lo & b_wadj=vneg | 98 | 17.3% | 0.36 | -6.2 | 0.9% |
| b_trend=hi & b_wadj=vneg | 201 | 29.4% | 0.60 | -5.4 | 1.9% |
| b_trend=hi & b_stoch=lo | 416 | 36.1% | 0.74 | -5.1 | 4.0% |
| b_regime=HEALTHY & b_stoch=hi | 1,160 | 56.0% | 1.15 | +5.1 | 11.0% |
| b_trend=hi & b_brd=mid_hi | 391 | 36.6% | 0.75 | -4.7 | 3.7% |
| b_trend=hi & b_sigma=tight | 75 | 21.3% | 0.44 | -4.7 | 0.7% |
| b_trend=hi & b_regime=CAUTION | 389 | 36.8% | 0.76 | -4.7 | 3.7% |
| b_regime=NEUTRAL & b_brd=lo | 177 | 32.8% | 0.67 | -4.2 | 1.7% |
| b_regime=BULL & b_brd=mid_lo | 177 | 33.3% | 0.69 | -4.1 | 1.7% |
| b_brd=lo & b_stoch=mid | 117 | 29.9% | 0.62 | -4.0 | 1.1% |
| b_trend=hi & b_regime=NEUTRAL | 147 | 32.0% | 0.66 | -4.0 | 1.4% |
| b_regime=CAUTION & b_wadj=pos | 216 | 62.0% | 1.28 | +4.0 | 2.1% |
| b_stoch=hi & b_sigma=vwide | 1,452 | 53.7% | 1.11 | +3.9 | 13.8% |
| b_regime=HEALTHY & b_wadj=neg | 1,377 | 53.7% | 1.11 | +3.8 | 13.1% |
| b_trend=hi & b_wadj=neg | 448 | 39.5% | 0.81 | -3.8 | 4.3% |


## PUT  <20
**N = 3,883  •  baseline miss rate = 45.5%**  (TP rate 54.5%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_trend | hi | 238 | 34.0% | 0.75 | -3.6 | 6.1% |
| b_sigma | tight | 259 | 36.3% | 0.80 | -3.0 | 6.7% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_stoch=lo & b_bb=mid | 330 | 29.7% | 0.65 | -5.8 | 8.5% |
| b_stoch=lo & b_wadj=vneg | 57 | 12.3% | 0.27 | -5.0 | 1.5% |
| b_trend=hi & b_stoch=lo | 154 | 28.6% | 0.63 | -4.2 | 4.0% |
| b_trend=hi & b_wadj=vneg | 93 | 25.8% | 0.57 | -3.8 | 2.4% |
| b_trend=hi & b_bb=mid | 176 | 33.0% | 0.72 | -3.4 | 4.5% |
| b_sigma=vwide & b_regime=CAUTION | 339 | 54.6% | 1.20 | +3.3 | 8.7% |
| b_stoch=hi & b_regime=CAUTION | 1,037 | 50.6% | 1.11 | +3.3 | 26.7% |
| b_trend=hi & b_sigma=tight | 33 | 18.2% | 0.40 | -3.2 | 0.8% |
| b_sigma=tight & b_bb=mid | 138 | 32.6% | 0.72 | -3.0 | 3.6% |
| b_sigma=tight & b_wadj=neg | 233 | 36.1% | 0.79 | -2.9 | 6.0% |
| b_bb=mid & b_regime=NEUTRAL | 413 | 38.5% | 0.85 | -2.9 | 10.6% |
| b_sigma=wide & b_stoch=mid | 242 | 36.4% | 0.80 | -2.9 | 6.2% |
| b_sigma=wide & b_bb=mid | 582 | 39.7% | 0.87 | -2.8 | 15.0% |
| b_stoch=hi & b_bb=lo | 1,112 | 49.6% | 1.09 | +2.7 | 28.6% |
| b_sigma=tight & b_stoch=lo | 86 | 31.4% | 0.69 | -2.6 | 2.2% |


## PUT  <15
**N = 1,061  •  baseline miss rate = 41.9%**  (TP rate 58.1%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|
| b_stoch | lo | 282 | 31.2% | 0.74 | -3.7 | 26.6% |
| b_trend | hi | 70 | 24.3% | 0.58 | -3.0 | 6.6% |
| b_stoch | hi | 567 | 47.8% | 1.14 | +2.8 | 53.4% |

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_stoch=hi & b_mis_stress=on | 260 | 53.8% | 1.28 | +3.9 | 24.5% |
| b_stoch=lo & b_wadj=neg | 239 | 31.0% | 0.74 | -3.4 | 22.5% |
| b_stoch=lo & b_bb=mid | 91 | 24.2% | 0.58 | -3.4 | 8.6% |
| b_stoch=hi & b_bb=lo | 330 | 51.2% | 1.22 | +3.4 | 31.1% |
| b_stoch=lo & b_mis_stress=off | 115 | 27.0% | 0.64 | -3.3 | 10.8% |
| b_stoch=hi & b_wadj=vneg | 60 | 61.7% | 1.47 | +3.1 | 5.7% |
| b_trend=hi & b_bb=mid | 49 | 20.4% | 0.49 | -3.1 | 4.6% |
| b_bb=mid & b_mis_stress=off | 206 | 32.0% | 0.76 | -2.9 | 19.4% |
| b_trend=hi & b_mis_stress=off | 30 | 16.7% | 0.40 | -2.8 | 2.8% |
| b_trend=hi & b_wadj=vneg | 30 | 16.7% | 0.40 | -2.8 | 2.8% |
| b_bb=mid & b_wadj=neg | 325 | 34.5% | 0.82 | -2.7 | 30.6% |
| b_stoch=lo & b_trend=hi | 48 | 22.9% | 0.55 | -2.7 | 4.5% |
| b_trend=lo & b_wadj=vneg | 31 | 64.5% | 1.54 | +2.5 | 2.9% |
| b_stoch=hi & b_vsig=REJECTION | 229 | 50.2% | 1.20 | +2.5 | 21.6% |


## PUT  <10
**N = 255  •  baseline miss rate = 40.4%**  (TP rate 59.6%)

### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)

| feature | value | N | miss% | lift | z | %cohort |
|---|---|---:|---:|---:|---:|---:|

### Top 2-feature patterns (top-6 features, N≥30)

| pattern | N | miss% | lift | z | %cohort |
|---|---:|---:|---:|---:|---:|
| b_bb=lo & b_stoch=hi | 83 | 59.0% | 1.46 | +3.5 | 32.5% |
| b_stoch=hi & b_vmag=mid | 49 | 59.2% | 1.47 | +2.7 | 19.2% |
| b_stoch=hi & b_regime=CAUTION | 72 | 55.6% | 1.38 | +2.6 | 28.2% |
| b_stoch=hi & b_sigma=wide | 58 | 56.9% | 1.41 | +2.6 | 22.7% |
| b_bb=lo & b_sigma=wide | 56 | 57.1% | 1.41 | +2.6 | 22.0% |
