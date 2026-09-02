# OSK Era-Layer -- FAITHFUL-PATH Results (Phase C, B4, RECOVERY RUN)

**Recommendation: BLOCK.** Do not ship the OSK-era B4 (or B3) score modifier on this
evidence. Full reasoning below.

## Resumed vs rebuilt (recovery-run bookkeeping)

- **Reused verbatim** (prior agent's completed, verified work): `STAGE1_VERDICT.md`,
  `PROPOSAL.md`, `stage1_sweep.py` + `stage1_results.json/.txt`, `build_osk_ledger.py`,
  `supply_check.py/.json`, `lag_robustness_sweep.py` + outputs, `.cache/osk_era/
  osk_era_ledger.parquet` (70-floor), `osk_era_ledger_lag1.parquet`, `discovery_panel_primary.parquet`.
- **Did not exist yet, built this run:**
  1. `.cache/osk_era/osk_era_ledger_65.parquet` (the 65-floor sidecar) -- confirmed absent
     via `python -m trader queue list --all` (no task with dedup `osk_era_ledger_65`, no
     output file). Added a `--score-min` CLI flag to `build_osk_ledger.py` (backward-compatible;
     default 70 unchanged, output filename only gets a `_<N>` suffix when N != 70) and queued
     it as task **#541** (task #540 failed first -- a Bash backslash-path escaping bug that
     mangled the script path into `C:DevelopmentTrader-...`, fixed by using forward slashes).
     Task #541: `db=heavy cpu=2`, ran 5m46s, N_requested=22,141 (65+, window), kept
     (chain-hit)=9,181, skew_cov=99.0% of kept, ripe=35.7% of kept.
  2. `experiments/osk_era/faithful_overlay.py` (new file, this run) -- the overlay engine.
  3. This file.

## Reconstruction

`adjusted_pre_regime = clip(pre_regime + lift, 0, 100)` (mirrors `scoring.py:1259`'s own
clamp); `implied_regime_mult = overall / pre_regime`; `adjusted_overall = overall +
implied_regime_mult * effective_lift`, algebraically identical to `(pre_regime +
effective_lift) * implied_regime_mult` since `overall == pre_regime * implied_regime_mult`
by construction of the ratio. `effective_lift` differs from raw `lift` only where the
[0,100] clamp binds (0 rows this run).

**RECONSTRUCTION IDENTITY CHECK: PASS, bit-exact.** On every uncovered row (skew null,
lift forced to 0.0 exactly) `adjusted_overall == overall` with **0 mismatches out of
13,050 uncovered rows**, for both B4 and B3.

- Population: `rs_ledger.parquet` (component_reweight 60-99 CALL universe) filtered to
  `overall>=65`, `2025-02-01..2026-06-15`, holdout-guarded (`context='osk_faithful'`,
  cutoff 2026-06-15 -- never trips, window end == cutoff). N=22,141 total; **0 excluded**
  (pre_regime is never null/<=0 in this universe -- always an int-truncated value in
  [64,98] for this population). Cross-check against the 70-floor build: overall>=70 in
  the 65-floor population = 4,667 (matches `osk_era_ledger.parquet`'s own n_requested
  exactly); >=75 = 860 (matches `supply_check.json`'s `n_75_full_stream` exactly); >=80 = 188.
- Joined to the 65-sidecar: N=22,141, skew-covered (chain-hit) = 9,091 (41.1%). z-score
  reference population = these 9,091 covered rows (pooled_mean_skew=0.0411,
  pooled_std_skew=0.1209) -- **not** reused from stage1_sweep's primary/corroboration
  panels, since this is a new join (65-floor direct rs_ledger join, not the Polygon
  discovery panel or the 70-floor corroboration ledger).
- `implied_regime_mult`: min=0.717, max=1.186, mean=0.969, median=0.970, **0 rows outside
  [0.5, 1.5]**, 0 non-finite -- sane, matches the "single-mechanism dampeners" scale this
  repo's own dampener stack typically produces.
- clamp_hits (pre_regime+lift would exceed [0,100]): **0** for both variants (only 7/22,141
  rows even have pre_regime>=95, and none of those happened to also carry a
  near-saturating positive lift).
- Known documented approximation (not corrected): `pre_regime` is already int-truncated
  at storage (`scoring.py:1259`), so this reconstructs from the post-truncation integer,
  not the untruncated `weighted_sum` (not stored anywhere). Sub-1-point rounding-order
  effect, immaterial to bucket aggregates below.

## K/M quoted (assess_scores.py, canonical WR15, CALL side)

`_peak_side()`: score >= 50 -> `'low'` side -> CALL. Lines 82-85: **`SWING_K_LOW = 2.0`**
(target: +2 sigma rise @ `SWING_REFERENCE_DAYS=30`), **`SWING_M_LOW = 5.0`** (stop: -5
sigma drop @ 30d). `_swing_walk()` (line 540: `scale = sqrt(W/SWING_REFERENCE_DAYS)`)
scales any other period by `sqrt(W/30)`. For W=15: `TP_SIGMA = 2.0*sqrt(0.5) =
1.414214`, `SL_SIGMA = 5.0*sqrt(0.5) = 3.535534` -- this is exactly `experiments/
component_reweight/label.py`'s own **"gen15"** label (which hardcodes the same values
rounded to 3dp: 1.414/3.536) -- i.e. the repo's "dashboard generic WR15" definition.
Grid-snapped (label.py's `UP_GRID`/`DN_GRID`, step 0.05, reimplemented bit-for-bit,
widths asserted == rs_ledger's `t_up`(43)/`t_dn`(93)): TP idx=20 (grid value 1.40 sigma),
SL idx=63 (grid value 3.55 sigma). `win15` computed directly from `t_up`/`t_dn`
List(Int64) + `fwd_caldays` via label.py's exact `win_expr` semantics (same-bar tie =
loss; unresolved if neither barrier touched and `fwd_caldays < 15`).

## Bucket tables -- B4 (cap=5, scale=1.5)

| bucket | N base | N adj | WR15 base | WR15 adj | delta | promoted | demoted | net migration |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 70-74 | 3,807 | 3,749 | 64.3% | 64.3% | -0.05pp | 563 | 621 | -58 |
| 75-79 | 672 | 661 | 67.7% | 67.9% | +0.22pp | 81 | 92 | -11 |
| 80+ | 188 | 191 | 72.3% | 70.7% | -1.66pp | 19 | 16 | +3 |
| **70+ cum** | 4,667 | 4,601 | 65.1% | 65.1% | -0.07pp | 490 | 556 | -66 |
| **75+ cum** | 860 | 852 | 68.7% | 68.5% | -0.18pp | 65 | 73 | -8 |
| **80+ cum** | 188 | 191 | 72.3% | 70.7% | -1.66pp | 19 | 16 | +3 |

Discrete-bucket promoted/demoted is a **mix of both directions** for a middle bucket
(e.g. 70-74 "promoted" includes both risers-from-below-70 and fallers-from-75-79) --
the **cumulative** rows are the clean directional read (promoted = pure upward
threshold-crossers, demoted = pure downward). W4 two-proportion z on the largest
discrete shift (80+, -1.66pp, N=188/191): pooled p=0.715, SE=0.0464, **z=-0.35** -- far
from the -2 flag / -3+1.5pp+N>=100 block thresholds. No bucket regresses in a
statistically real sense.

**Date-clustered gap test (promoted vs demoted cohort), B4:**

| bucket | WR15 gap | t_clust | pnl15(secondary) gap | t_clust | N_p / N_d (WR15) |
|---|---:|---:|---:|---:|---|
| 70-74 | -0.0037 | -0.13 | **-0.3850** | **-2.26** | 563/621 |
| 75-79 | +0.0160 | 0.24 | +0.2926 | 0.81 | 81/92 |
| 80+ | SKIP N<30 | -- | SKIP N<30 | -- | 19/16 |
| **70+ cum** | -0.0086 | -0.27 | **-0.4076** | **-2.19** | 490/556 (WR15); 183/189 (pnl15) |
| **75+ cum** | -0.0234 | -0.32 | SKIP N<30 | -- | 65/73 |
| 80+ cum | SKIP N<30 | -- | SKIP N<30 | -- | 19/16 |

**Blended full-stream 75+ (coverage_fraction_75 = 50.0%, ground-truth chain-hit, from
`supply_check.json`; n_75_full_stream measured here = 860, matches supply_check exactly):**
supply delta (within-chainhit net=-8/430=-1.9%, full-stream) = **-0.93%**. WR15 shift
within-covered = -0.0018 -> blended = **-0.09pp**. pnl15(secondary) shift = +0.0367 ->
blended = **+1.83pp**.

This last (positive) number looks like it contradicts the negative gap test above, but
it is answering a different question. `blended_pnl15` compares `mean(pnl15 | adjusted>=75)`
against `mean(pnl15 | baseline>=75)` -- two heavily-OVERLAPPING sets whose common
**incumbent** block (rows in the bucket both before and after -- 787 of 860, ~91.5%,
whether skew-covered or not) contributes identically to both means and so dominates and
mutes the comparison; only the small promoted (65) and demoted (73) margins actually move
it, diluted ~11:1 by the unchanging incumbent mass. The promoted-vs-demoted **gap** test
isolates exactly those margins against each other directly (mean(promoted) -
mean(demoted)) and is the statistic that actually answers "are the rows this modifier
is adding better or worse than the rows it's removing" -- that is the decision-relevant
read, and it is negative and significant.

## Bucket tables -- B3 (cap=3, scale=1.5) -- conditional fallback, run because B4's WR15 was negative

Trigger fired on point-estimate sign only (blended WR15 -0.09pp, 75+cum WR15 -0.18pp --
both negative but **not** statistically significant; see below).

| bucket | N base | N adj | WR15 base | WR15 adj | delta | promoted | demoted | net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 70-74 | 3,807 | 3,552 | 64.3% | 64.4% | +0.06pp | 238 | 493 | -255 |
| 75-79 | 672 | 648 | 67.7% | 67.7% | +0.04pp | 41 | 65 | -24 |
| 80+ | 188 | 183 | 72.3% | 70.5% | -1.85pp | 10 | 15 | -5 |
| **70+ cum** | 4,667 | 4,383 | 65.1% | 65.1% | -0.00pp | 183 | 467 | -284 |
| **75+ cum** | 860 | 831 | 68.7% | 68.4% | -0.37pp | 26 | 55 | -29 |

Blended 75+: supply **-3.37%**, WR15 blended **-0.18pp**, pnl15(secondary) blended
**+2.65pp**. 70+cum pnl15 gap = -0.1443, t=-0.64 (weaker than B4's -2.19, but still
negative-signed, not flipped positive). B3 shrinks the negative secondary signal's
magnitude/significance (smaller cap = fewer/less-marginal migrators) but does **not**
reverse it, and it makes WR15 at 75+ slightly worse (-0.37pp vs B4's -0.18pp). **B3 is
not a rescue.**

## W1-W6 checklist

| Gate | Status | Note |
|---|---|---|
| W1 (cohort z >= +3, targeted feature) | **MET** (pre-existing) | osk_validation/VERDICT.md Polygon D2 replication, clustered t=+3.38, correct direction (higher skew -> more positive call pnl15). Not re-derived this run. |
| W2 (multi-barrier WR3/5/7/15/30 consistency) | **NOT TESTABLE PRE-SHIP** | Only WR15 was reconstructed this run; no WR3/WR5/WR7/WR30 barrier grid indices were pulled. |
| W3 (multi-time-window 1y/3y/5y consistency) | **NOT TESTABLE PRE-SHIP** | Era window is a single ~16-month span (2025-02..2026-06); not sliced into 1y/3y/5y, and the option-data history doesn't reach a full 5y anyway. |
| W4 (per-discrete-bucket non-regression) | **MET** | Largest discrete shift (80+, -1.66pp) has two-proportion z=-0.35 -- far above the -2 flag threshold. No bucket regresses in a statistically real sense (B4 or B3). |
| W5 (Hydration-Adjusted Growth verdict) | **NOT TESTABLE PRE-SHIP** | `stage1_growth_gate.py` needs a version-keyed `research_pack/utility_5y_wr15.json`; this modifier has no `ALGORITHM_VERSION` (pre-ship ledger evidence, not in scoring.py). `signal_supply.py` additionally needs a live `scores` table query -- barred by HARD STOPS (no MySQL beyond the one queued sidecar job). Same conclusion stage1_sweep.py already reached; still true for this reconstruction. |
| W6 (no candidate-introduced gradient inversion) | **MET** | Baseline WR15 by bucket: 64.3% -> 67.7% -> 72.3% (monotone). Adjusted: 64.3% -> 67.9% -> 70.7% (still monotone). No inversion introduced. |

3 of 6 gates are structurally untestable without a real recalculation/ScoreSimulator
pass (W2/W3/W5) -- this reconstruction cannot complete Stage 1 on its own; it can only
license or kill the decision to spend that compute.

## Verdict

**BLOCK.** The faithful (pre-regime, regime-mult-scaled) reconstruction does not
support shipping B4 (or the more conservative B3).

**Strongest FOR:**
1. Reconstruction identity check passes bit-exact (0/13,050 mismatches) -- the
   engineering is verified sound, this is a real test of the mechanism, not a broken one.
2. No gross damage: blended 75+ supply delta is small (-0.93%, B4), WR15 shift is tiny
   in magnitude either way, and W4/W6 both cleanly pass (no real bucket regression, no
   gradient inversion).

**Strongest AGAINST:**
1. On the clean cumulative-threshold migration test (the only test whose promoted/demoted
   split has an unambiguous direction), the **secondary option-pnl15 gap is negative and
   significant at the best-powered cell**: 70+cum gap=-0.4076, t_clust=-2.19 (N_p=183,
   N_d=189, both clear the repo's own N>=100 "real, not noise" bar) -- rows the modifier
   PROMOTES into 70+ have *worse* realized option returns than rows it demotes out. This
   **reverses the sign** of stage1_sweep.py's naive ("overall + lift", unscaled by the
   regime multiplier) reconstruction of the same B4 variant, which reported **+0.52,
   t_clust=2.51** and was STAGE1_VERDICT.md's "best-of-6" pick. The more faithful the
   reconstruction, the worse this candidate looks.
2. The **WR15-primary gap is statistically null** on the same clean cells (70+cum
   t=-0.27, 75+cum t=-0.32) -- B4 demonstrates no WR15 edge whatsoever on the exact rows
   it moves, so even setting aside the negative secondary signal, there is no positive
   primary-metric case to weigh it against.

**If this axis is revisited:** the sign-reversal between naive and faithful
reconstructions is the load-bearing finding, not a detail -- it means Stage-1 "gap/t"
headline numbers computed by flat-adding a lift to `overall` (as stage1_sweep.py's whole
6-variant grid did) are not a reliable stand-in for where the lift actually plugs into
the pipeline once the regime multiplier is honestly carried through. Any future OSK-era
candidate should be screened through this pre-regime-scaled reconstruction BEFORE a
multi-variant sweep is run on the cheaper naive shortcut, not after.
