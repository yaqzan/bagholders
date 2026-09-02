# ETF Overall Call Influence Sweep

Run:

```bash
python -u experiments/sector_etf_alpha/sweep_etf_overall_call_influence.py --n 12000 --workers 12 --batch-size 128
```

Artifacts:

- `experiments/sector_etf_alpha/dd_probe/etf_call_influence_all_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_call_influence_top_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_call_influence_strict_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_call_influence_arch_summary_v46_1825.csv`
- `experiments/sector_etf_alpha/dd_probe/etf_call_influence_knobs_v46_1825.csv`

## Setup

- Score source: v46 stock call cohort, 2021-05-12 through 2026-05-11.
- Rows after joining sector ETF `Score.overall`: 22,081 stock CALL signals.
- Holdout guard applied through `experiments._holdout`.
- Hot path: cached NumPy matrix + `ProcessPoolExecutor`; 12,005 variants evaluated in about 27 seconds with 12 workers.

Architectures tested:

- `ETF_TREND_BOTH`: high ETF score boosts stock call scores; low ETF score dampens them.
- `ETF_TREND_DAMP_ONLY`: only low ETF score dampens stock call scores.
- `ETF_TREND_BOOST_ONLY`: only high ETF score boosts stock call scores.
- `ETF_MEANREV`: high ETF score dampens; low ETF score boosts.
- `PHASE_MEANREV`: existing `sector_phase` semantics; overheated dampens, oversold boosts.
- `PHASE_TREND`: opposite phase direction.
- `ETF_PHASE_BLEND`: ETF trend confirmation blended with existing phase mean-reversion.

## Architecture Result

| Architecture | Best utility | Mean utility | Best 75+ dWR7 | Best 80+ dWR7 | Median changed |
|---|---:|---:|---:|---:|---:|
| ETF trend both | 20.84 | -0.81 | +2.23pp | +2.76pp | 910 |
| ETF trend damp only | 19.69 | +1.11 | +2.70pp | +2.54pp | 600 |
| ETF/phase blend | 16.87 | -0.52 | +2.29pp | +2.42pp | 917 |
| Phase mean-revert | 10.28 | -2.74 | +2.10pp | +1.30pp | 1,108 |
| Phase trend | 10.27 | -6.97 | +0.12pp | +1.40pp | 1,106 |
| ETF mean-revert | 7.13 | -7.16 | +2.05pp | +1.53pp | 823 |
| ETF trend boost only | 4.61 | -3.58 | +0.13pp | +0.04pp | 110 |

Interpretation: ETF `overall` is materially more useful than `sector_phase` for CALL score influence, but the useful direction is not broad boosting. The benefit is confirmation and pruning: weak sector ETF scores should dampen or cap high stock CALL scores. Pure boost-only is weak, and ETF mean-reversion is worse.

## Strict Screen

Strict filter:

- no breach penalties;
- no negative W7/W15/W30 at 80+ and 85+;
- non-negative W7 at 90+;
- minimum post-transform sample: 80+ N >= 1,000, 85+ N >= 300, 90+ N >= 70.

Best strict candidates:

| Candidate | 80+ dWR7/W15/W30 | 85+ dWR7/W15/W30 | 90+ dWR7/W15/W30 | Changed |
|---|---:|---:|---:|---:|
| ETF/phase blend | +0.00 / +0.00 / +0.00 | +0.41 / +1.16 / +0.93 | +6.73 / +4.83 / +4.20 | 151 |
| ETF damp only | +0.58 / +0.13 / +0.06 | +1.14 / +1.35 / +2.48 | +2.31 / +1.02 / +3.37 | 1,803 |
| ETF damp only | +0.75 / +0.40 / +0.27 | +1.14 / +1.35 / +2.48 | +2.38 / +0.95 / +2.38 | 214 |
| ETF trend both | +0.41 / +0.18 / +0.08 | +0.88 / +1.16 / +1.99 | +3.49 / +1.69 / +5.96 | 182 |
| Phase mean-revert | +0.00 / +0.00 / +0.00 | +0.54 / +1.05 / +0.57 | +5.71 / +4.95 / +3.05 | 226 |

## Narrowed Knob Basin

What works:

- Use ETF `overall` as a trend-confirmation dampener. Low or non-confirming sector ETF scores should pull high stock CALL scores down.
- Keep the transform mostly above stock score 75. The best rows rarely promote 70+ membership; they mostly reshape 80+/85+/90+ quality.
- Prefer a wide upper gate, usually `gate_hi = 92`, so the transform can affect the high-score cascade rather than only the 70-84 band.
- Useful ETF center is usually below 60 in the best dampening rows, roughly 55-59, with wide saturation width around 18-23.
- Useful score power is usually high, around 1.8-2.4, concentrating the effect on stronger stock CALL scores.
- Useful negative alpha is meaningful; positive alpha is optional and weaker.

What does not work:

- Pure ETF boost-only is weak. It barely improves 80+ and underperforms every other architecture.
- ETF mean-reversion is worse than ETF trend confirmation. This differs from `sector_phase`, which can still work as a mean-reversion/crowding oscillator.
- Broadly promoting lower scores into the 70+ call universe is not the clean edge. The clean edge is high-tier pruning/reranking.

## Comparison To `sector_phase`

`sector_phase` and ETF `overall` are not redundant.

- `sector_phase` is a stretch/crowding oscillator from pct-from-EMA50 and RSI. Its best direction remains mean-reversion: overheated sectors dampen, oversold sectors lift.
- ETF `overall` behaves more like a trend/participation confirmation score. Its best direction is trend-following dampening: if the sector ETF score does not confirm, reduce stock CALL conviction.
- A blend can work, but the strongest blend rows were ETF-dominant and modest. This suggests the next ship candidate should start with ETF-overall dampening alone, then test a small phase companion only if portfolio DD improves.

## Next Experiment

Portfolio-scale replay/MC candidate:

- CALL-only sector ETF dampener.
- Gate stock CALL scores from 75 to 92.
- ETF wave center around 56-59 and width around 18-23.
- Saturating power around 1.6-2.1.
- Dampening alpha around 0.25-0.80, target down around 56-64.
- No broad boost path initially.

This should be tested as a portfolio allocation/scoring overlay against production and raw V4. The scoring-stage result is promising, but the earlier V4 lesson still applies: a high WR transform is not a ship candidate until DD and compound survive portfolio MC.
