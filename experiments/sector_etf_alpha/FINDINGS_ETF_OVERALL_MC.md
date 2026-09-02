# ETF Overall CALL Dampener MC Test

Run directory:

- `experiments/sector_etf_alpha/dd_probe/runs/etfd_mc_20260511_132505/`

Command:

```bash
python -u experiments/sector_etf_alpha/compare_prod_etf_call_damp_mc.py \
  --windows 2022,2024,22-now,5y \
  --variants PROD,ETFD_CONSERVE,ETFD_WIDE,V4 \
  --n 120 \
  --version-id 46 \
  --out experiments/sector_etf_alpha/dd_probe/runs/etfd_mc_20260511_132505/prod_vs_etfd_mc.csv
```

Artifacts:

- `prod_vs_etfd_mc.csv`
- `prod_vs_etfd_mc_deltas.csv`
- `run.log`
- `status.json`
- `done.json`

The run completed with 16 rows and empty stderr.

## Candidates

`ETFD_CONSERVE`

- CALL-only ETF overall dampener.
- Stock score gate: 75-92.
- ETF score center: 57.7028.
- ETF width: 22.8578.
- Changed 228 lookup entries, dropped 4 below 70.

`ETFD_WIDE`

- CALL-only ETF overall dampener.
- Stock score gate: 68-92.
- ETF score center: 55.1370.
- ETF width: 21.7117.
- Changed 1,878 lookup entries, dropped 4 below 70.

`V4`

- Prior raw sector_phase + RSU control.

## Worst DD Deltas vs PROD

| Variant | 2022 | 2024 | 22-now | 5y | Avg |
|---|---:|---:|---:|---:|---:|
| ETFD_CONSERVE | +0.90pp | +0.00pp | +3.56pp | -1.16pp | +0.82pp |
| ETFD_WIDE | +6.29pp | -3.53pp | +0.55pp | +1.25pp | +1.14pp |
| V4 | +0.61pp | -3.85pp | -0.96pp | -1.84pp | -1.51pp |

Mean DD deltas:

| Variant | Avg dMeanDD |
|---|---:|
| ETFD_CONSERVE | +0.63pp |
| ETFD_WIDE | +3.73pp |
| V4 | -1.15pp |

## Verdict

No ship candidate.

ETF `overall` was real at the scoring-stage CALL WR level, but CALL-only score dampening failed portfolio validation. The failure mode is clear:

- `ETFD_CONSERVE` barely changes the tape but still worsens 2022 and 22-now DD.
- `ETFD_WIDE` over-prunes CALL exposure, especially in 2022, and worsens the PUT-dominant DD shape.
- Raw V4 still has the better portfolio/DD profile among tested variants, despite not being a ship candidate previously.

This is another case where Stage 1 WR alpha is not enough. The portfolio does not want unilateral CALL pruning unless the PUT book is also controlled. The earlier DD autopsy remains consistent: reducing calls during stress can make the worst episode more PUT-dominated, not safer.

## Next Constraint

Do not continue with pure CALL-only ETF-overall score dampening as a ship path.

Next viable experiment should be allocation-scale, not score-stage:

- use sector ETF `overall` as a CALL allocation confidence scalar, not a signal dropper;
- pair any CALL reduction with a PUT exposure cap or PUT allocation wave so the book does not become PUT-heavy in the same drawdown state;
- test this at the portfolio layer first, before any scoring-stage recalibration.
