# Stage 1 Phase A — Put-side wadj tanh-amp cohort validation (NULL)

**Status: NULL at W1 gate** (2026-05-08). Cohort z-score on the targeted
feature is +1.23 — below the +3 threshold required by the new Stage 1
methodology to start a calibration sweep.

## Hypothesis

From `known-issues.md` Priority #4 leg-2 (open after v32 CWCF call-side ship):

> Currently put-amp inside `calculate_weekly_adjustment` is a static 1.5×
> regardless of |wadj| magnitude. This inflates noise — puts with mildly
> negative wadj (|wadj| < 13) get the same boost as strong-wadj puts
> (|wadj| > 30). The original cross-bucket evidence on earlier versions
> showed weak-wadj puts carrying 7-15pp lower WR15 than strong-wadj
> siblings.

**Proposed mechanism (untested):**
```python
# current: total *= 1.5 if total < 0 else 1.0
# tanh:    total *= 1.0 + 0.5 * tanh(|total|/k)  for total < 0  (puts only)
```

## W1 cohort z-score gate (5y v46 substrate)

| Cohort | N (resolved WR7) | WR7 |
|---|---:|---:|
| Weak wadj (puts ≤25, wadj ∈ [-13, 0)) | 776 | 73.07% |
| Strong wadj (puts ≤25, wadj < -13) | 9,332 | 75.10% |
| **Δ miss rate** | | **+2.03pp** |
| **z-score** | | **+1.23** |

W1 gate (z ≥ +3): **FAIL.** Per methodology, do not proceed to Phase B.

## Per-bucket × wadj-bin breakdown

| bucket | all WR7 | weak[-13,0) WR7 | strong<-13 WR7 | Δ | z |
|---|---:|---:|---:|---:|---:|
| 16-20 | 75.94% (N=2,839) | **61.04%** (N=77) | 76.55% (N=2,712) | -15.51pp | **+2.76** |
| 21-25 | 73.49% (N=6,650) | 74.31% (N=685) | 73.64% (N=5,581) | +0.66pp | -0.37 |
| <16 | sparse N |

The [16-20] bucket cohort comes RIGHT TO THE EDGE of the W1 gate (z=+2.76)
on small N=77. But this cohort overlaps v39 PESS coverage (PESS lifts puts
in [16,20] when d_ern ≤ 5 toward 28). The "untouched" residual is even
smaller than N=77.

## Why the original signal is now too weak to ship

Three score-stage mechanisms shipped between the original cross-bucket
evidence (~v25 era) and the v46 active substrate that target the same
weak-wadj cohort:

1. **v27 WCF** (2026-04-27) — lifts puts (overall < 28 ∧ wadj > -17)
   toward 50. Removes the weakest-weekly puts entirely.
2. **v37 PCD** (2026-05-05) — lifts puts (overall ≤ 25 ∧ ret_10d_sigma
   ≤ -1.0σ) toward 30. Removes vol-normalized post-crash puts.
3. **v39 PESS** (2026-05-06) — lifts puts (overall ∈ [16,20] ∧ d_ern
   ≤ 5) toward 28. Removes pre-earnings puts.

Together these handle the bulk of the noise the tanh-amp would have
filtered. The cross-bucket "weak-wadj underperforms by 7-15pp" pattern
that motivated this hypothesis was diluted by the cumulative stack.

## Decision

**Do not proceed to Phase B.** Per methodology W1: "Cohort z < +3 —
abandon, don't calibrate noise. The miss-ledger workflow is the canonical
pre-flight tool."

This is a clean null result. The signal that existed in earlier versions
has been captured by the v27/v37/v39 stack additions.

## Lessons

- **The new Stage 1 W1 gate is doing its job.** Without the gate, this
  hypothesis would have proceeded to a 50-150 variant LHS sweep that
  would have produced noise wins (small N=77 cohort with z=+2.76 is
  fragile to MC variance). Cost saved: ~3h compute + risk of shipping
  a noise-fit dampener.
- **Old cohort signals decay as the score stack densifies.** Each
  score-stage addition (PCD, PESS, MCD, ICH, WVD) absorbs some of the
  original "weak weekly drag = bad signal" alpha. New investigations
  must validate cohort z UNDER THE CURRENT STACK, not against historical
  evidence from earlier versions.

## Files

- `phase_a_cohort.py` — feature builder + cohort z-score gate
- `phase_a.out` — full output
- `.cache/wadj_put_tanh/puts_v46_1825.parquet` — v46 put feature cache (11,165 rows)
