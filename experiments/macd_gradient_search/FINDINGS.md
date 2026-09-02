# MACD Gradient Replacement Findings

Status: NULL / NO SHIP
Date: 2026-05-24
Baseline: active scoring v60 (`d4a3e9fec`)

## Question

Can the hard `PUT_MACD_GATE = 45` cliff be replaced by a smooth gradient or
wave without making the algorithm worse?

## Core Finding

No. A pure MACD gradient replacement is not ship-worthy on v60.

The earlier v62-style attempt failed because `PUT_MACD_GATE` is not a final
PUT-only switch. It changes component weights early in `compute_overall_score`,
before weekly adjustment, volume, regime, and later score-stage waves. Low
`_pre_no_macd` rows can later become call candidates, so a naive gradient can
damage call capacity.

This run fixed that design flaw by making the search call-preserving by
construction:

1. Compute the v60 baseline cliff score.
2. Compute a full-MACD restored score with the cliff disabled.
3. Blend only from baseline toward full-MACD when the move is put-beneficial
   and the baseline final score is still on the put side.
4. Reject any candidate that drops 75+/80+/85+/90+/95+ call buckets.

That produced many score-stage survivors, but MC validation still rejected them.
The added put signals were not clean enough: aggressive gradients worsened
drawdown or put TP, conservative gradients became too small to matter.

## Artifacts

| Run | Purpose | Result |
| --- | --- | --- |
| `.codex/runs/macd_gradient_guarded_sweep_20260523_224405` | 469-variant 5y pre-holdout guarded sweep | 237 score-stage survivors, call guardrails exact |
| `.codex/runs/macd_gradient_guarded_mc_20260524_004604` | MC on top sweep-objective survivors | Rejected: DD and put TP damage |
| `.codex/runs/macd_gradient_conservative_mc_20260524_021548` | MC on smaller-disturbance survivors | Rejected: no clean return/DD/put-TP edge |
| `.codex/runs/macd_gradient_ultra_mc_20260524_030834` | MC on tiny-disturbance survivors | Rejected: mostly noise, not enough edge |

The reusable harnesses are:

- `experiments/macd_gradient_search/sweep.py`
- `experiments/macd_gradient_search/mc_validate.py`

They use UTF-8-safe run logging, `status.json`, `run.recent.log`, and
`done.json` / `failed.json` terminal artifacts.

## Score-Stage Sweep

The broad guarded sweep evaluated 931,060 score rows and 469 variants. It found
237 variants that preserved the high-call buckets exactly while improving put
WR15/wins on option-aligned 30DTE WR15 barriers.

Top score-stage candidates versus `baseline_cliff`:

| Candidate | <25 WR15 delta | <25 wins delta | <15 WR15 delta | <15 wins delta | 75+/80+/85+ call delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sig_s50_c39_w5_a1` | +0.24pp | +28 | +0.29pp | +4 | 0 |
| `gauss_s40_c38_w7_a1` | +0.38pp | +55 | +0.21pp | +10 | 0 |
| `gauss_s40_c34_w7_a1` | +0.03pp | +42 | +0.41pp | +10 | 0 |
| `sig_s50_c41_w5_a1` | +0.24pp | +24 | +0.04pp | +2 | 0 |

This proved that the v62 failure mode was avoidable. It did not prove the MACD
replacement should ship.

## MC Validation

Baseline MC (`baseline_cliff`) in the validation runs:

| Window | Mean return | Worst DD | Call TP | Put TP | Call trades | Put trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| COVID crash | +392.5% | 50.0% | 52.94% | 48.04% | 191.0 | 85.7 |
| 2020-now | +1,981,889.7% | 57.8% | 61.02% | 45.38% | 3,795.5 | 2,661.2 |
| 2022-now | +1,297,911.2% | 62.6% | 62.29% | 45.67% | 2,287.3 | 2,102.0 |

Aggressive top-sweep candidates were rejected:

| Candidate | Main reason |
| --- | --- |
| `sig_s50_c39_w5_a1` | `2020_now` worst DD +4.5pp and put TP -0.14pp; `22_now` put TP -0.22pp |
| `gauss_s40_c38_w7_a1` | `2020_now` worst DD +4.8pp, COVID put TP -1.24pp |
| `gauss_s40_c34_w7_a1` | `2020_now` worst DD +10.4pp |
| `sig_s50_c41_w5_a1` | `2020_now` worst DD +4.9pp; weak put TP deltas |

Conservative candidates were also rejected:

| Candidate | Main reason |
| --- | --- |
| `gauss_s30_c34_w2_a1` | Best put TP deltas, but `2020_now` worst DD +7.0pp |
| `gauss_s30_c38_w4_a0.5` | `22_now` improved, but `2020_now` worst DD +5.3pp and put TP -0.06pp |
| `left_s30_w6_a1` | `22_now` DD -2.1pp, but COVID put TP -0.39pp and `2020_now` DD +3.3pp |
| `sig_s35_c43_w3_a1` | Returns and put TP worsened across validation windows |

Ultra-conservative candidates did not create enough edge:

| Candidate | Main reason |
| --- | --- |
| `gauss_s30_c34_w2_a0.5` | `2020_now` put TP +0.01pp, but mean return -464.3% and worst DD +2.8pp |
| `left_s25_w2_a0.25` | DD neutral, but put TP flat/down and returns mixed |
| `gauss_s25_c34_w4_a0.5` | Near no-op; `2020_now` put TP -0.03pp and `22_now` worst DD +0.7pp |

## Mechanism Lesson

The cliff is ugly but load-bearing. The issue is not simply discontinuity at
45. MACD remains a lagging input on bearish setups. Restoring it smoothly below
the gate changes put timing and peak selection; the resulting extra/different
puts do not improve portfolio sequencing.

Future work should not retry pure MACD smoothing, widening, leakage, or
full-MACD blend families around `PUT_MACD_GATE`. A retry needs a new
discriminator that identifies when restored MACD is independently useful for
put timing, not another smoother curve.
