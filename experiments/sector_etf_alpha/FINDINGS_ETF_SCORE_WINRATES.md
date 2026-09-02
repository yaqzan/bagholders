# ETF Score Winrate Diagnostic

Run:

```bash
python -u experiments/sector_etf_alpha/analyze_etf_score_winrates.py --version-id 46 --lookback-days 1825 --end-date 2026-05-11
```

Artifacts:

- `experiments/sector_etf_alpha/dd_probe/etf_score_thresholds_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_score_by_symbol_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_score_buckets_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_score_ranges_v46_1825.csv`

## Setup

- Score source: v46, because active v48 only had current-day ETF score rows.
- Window: 2021-05-12 through 2026-05-11.
- Outcome source:
  - ETFs: locally computed `30dte_generic` barrier outcomes for W7/W15/W30.
  - Stocks: `.cache/runner/barriers_1825.parquet`, deduped on `(symbol,date,side,w_days)`.
- Side mapping:
  - high score -> CALL -> barrier side `low`.
  - low score -> PUT -> barrier side `high`.

## Primary W7 Results

| Asset | Side | Threshold | N | WR | Avg exit return |
|---|---:|---:|---:|---:|---:|
| Sector ETFs | CALL | >=60 | 3,031 | 66.48% | 0.116% |
| Sector ETFs | CALL | >=70 | 234 | 70.94% | 0.223% |
| Stocks | CALL | >=60 | 178,526 | 57.75% | 0.323% |
| Stocks | CALL | >=70 | 22,683 | 62.22% | 0.472% |
| Sector ETFs | PUT | <=40 | 1,979 | 64.33% | -0.065% |
| Sector ETFs | PUT | <=30 | 353 | 63.46% | -0.091% |
| Stocks | PUT | <=40 | 265,112 | 50.70% | 0.451% |
| Stocks | PUT | <=30 | 71,727 | 51.67% | 0.505% |

## Interpretation

The production score appears to be directionally meaningful on sector ETFs for CALL/high-score states. It is cleaner by WR than the stock baseline, but the average realized move is smaller, which is expected for ETFs and matters for portfolio sizing.

The ETF CALL signal starts well below 70. A hard 90 threshold is unusable on a 5-year sector-ETF sample: only one sector ETF observation reached 90+, and only ten sector ETF observations reached 80+ at W7 coverage. A soft allocation ramp beginning near 60 is empirically justified for constructive sector participation.

The PUT side should not be mirrored mechanically. Sector ETF low scores at <=40 and <=30 have superficially high WR, but average exit return is negative at W7/W15/W30. The only positive sector ETF PUT threshold is <=25, with N=29, too small to trust. This looks more like sector oversold/chop behavior than a clean bearish allocation wave.

## Next Experiment

Use ETF overall score as a portfolio allocation modifier, not as a stock-score replacement:

- CALL sector participation ramp: start around ETF score 60, saturate around 75-80.
- PUT sector participation: do not amplify puts from ETF low scores yet; test low ETF score as a put cap/risk-off warning instead.
- Compare against the existing `sector_phase` oscillator to see whether ETF `overall` adds a cleaner allocation-scale law or is just a lower-frequency proxy for the same wave.
