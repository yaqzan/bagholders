# ICH Put-Side Tail Recalibration — FINDINGS (2026-05-09)

**Status: MARGINAL / DO NOT SHIP YET after exact active-stack recalibration.**
The v43 isolation result remains useful for diagnosis. Active-stack refinement
found one mechanically passing candidate, but its edge is too small for the
blast radius: +0.28pp WR7 at `<=25`, only +0.04pp at 1y, and the `0-5` bucket
sits at -0.28pp vs the -0.30pp tolerance.

---

## Headline

The original v43 isolation found a real pathology: the current v44 ICH put
shape damages deep-tail puts while extracting little cumulative `<=25` value.
However, exact active-stack validation on v46 shows the Phase B asymK winner
is not sufficient. It restores the deep tail, but admits/regresses too many
11-20 puts. Do **not** ship the Phase B candidate as-is.

The correct decision is no production edit yet. Revisit only if a broader
mechanism family can clear +0.3pp with healthier 1y support and more slack in
the deep-tail bucket.

## Phase B isolation candidate parameters (FAILED active-stack shadow)

```
ICH_K_PUT          = 0.42      (was 0.278)
ICH_K_PUT_POWER    = 2.12      (NEW — power-law on score_norm)
ICH_GATE_PUT_LO    = 18        (was 10 — preserves deep tail by raising floor)
ICH_GATE_PUT_HI    = 25        (was 27 — boundary tightened)
ICH_KIJ_SAT_PUT    = 10.7      (was 8.8)
ICH_TARGET_PUT     = 33.4      (unchanged)
ICH_IND_RAMP_PUT   = 'log'     (unchanged)
score_norm direction: INVERTED — peaks at GATE_HI (boundary), 0 at GATE_LO
```

Note: many parameter combinations within the saturation region
(GATE_LO ∈ [15,20], GATE_HI ∈ [25,30], K ∈ [0.40,0.55], POWER ∈ [1.5,3.5])
deliver essentially identical +0.49 to +0.50pp lift. The mechanism shape
matters more than exact parameter values; the asymK_winner above is one of
~50 equivalent top candidates from the LHS sweep.

## Proposed code change (DO NOT APPLY until active-stack sweep passes)

In [database/utils/scoring.py](../../database/utils/scoring.py:1077-1088):

```python
# CURRENT (v44 — symmetric log, dampens deep tail hardest):
if ICH_GATE_PUT_HI > ICH_GATE_PUT_LO and overall <= ICH_GATE_PUT_HI:
    _sg_p = ramp_log( (GATE_HI - overall) / (GATE_HI - GATE_LO), ... )  # peak at LO
    _ig_p = ramp_log( -kijun_pct, ICH_KIJ_SAT_PUT )
    overall += ICH_K_PUT * _sg_p * _ig_p * (TARGET - overall)

# NEW (asymK — power-law on score_norm, dampens boundary hardest):
if ICH_GATE_PUT_HI > ICH_GATE_PUT_LO and overall <= ICH_GATE_PUT_HI \
        and overall > ICH_GATE_PUT_LO:
    _score_range_p = max(1, ICH_GATE_PUT_HI - ICH_GATE_PUT_LO)
    _score_norm_p = max(0.0, (overall - ICH_GATE_PUT_LO) / _score_range_p)
    _k_eff_p = ICH_K_PUT * (_score_norm_p ** ICH_K_PUT_POWER)
    _ig_p = ramp_log(-kijun_pct, ICH_KIJ_SAT_PUT)
    if _k_eff_p > 0 and _ig_p > 0:
        _ich_put_l = _k_eff_p * _ig_p * (ICH_TARGET_PUT - overall)
        if _ich_put_l > 0:
            overall = int(max(0, min(100, round(overall + _ich_put_l))))
```

Plus add `ICH_K_PUT_POWER` env-var constant near the existing ICH constants
(line 217 area). Update default values for `ICH_K_PUT`, `ICH_GATE_PUT_LO`,
`ICH_GATE_PUT_HI`, `ICH_KIJ_SAT_PUT`. `ICH_TARGET_PUT` unchanged.

## Stage 1 W1-W6 ship-gate scorecard after Phase E

| Gate | Status | Evidence |
|---|---|---|
| **W1** cohort z ≥ +3 | PASS | z=-6.17 at boundary [21,30] cohort, v46 baseline |
| **W2** multi-barrier-window | PARTIAL | v43 isolation passed W7/W15/W30, but WR3/WR5 await period-list migration and active-stack W2 not re-run. |
| **W3** multi-time-window WR7 | PARTIAL | v43 isolation passed; active-stack exact shadow was 5y only. |
| **W4** per-discrete-tier non-regression | **FAIL** | Exact v46 shadow: 11-15 -2.43pp, 16-20 -1.75pp for Phase B winner. |
| **W5** N capacity floor | MIXED | Phase B winner passes N-flow; stronger conservative variant fails p16-20 floor (389/yr vs 398 floor). |
| **W6** gradient preservation | FAIL | Deep tail improves, but 11-20 ordering/regression breaks. |

## Phase E exact v46 active-stack shadow (2026-05-09)

Method: dynamically patch only the ICH put block in memory, run the normal
`ScoreSimulator` path over the current v46 stack, compare against active v46 DB
scores. Holdout assertion enforced via `experiments._holdout`. Script:
`experiments/ich_put_recal/phase_e_v46_shadow.py`.

### Phase B winner on exact v46

Parameters: `K=0.42, POWER=2.12, LO=18, HI=25, SAT=10.7, TARGET=33.4`.

| Bucket | Baseline WR7/N | Candidate WR7/N | ΔWR | ΔN |
|---|---:|---:|---:|---:|
| <=5 | 72.73 / 33 | 78.55 / 415 | +5.83 | +382 |
| <=10 | 76.74 / 258 | 78.77 / 1102 | +2.02 | +844 |
| <=15 | 78.88 / 1070 | 77.79 / 2733 | -1.09 | +1663 |
| <=20 | 76.75 / 3909 | 75.83 / 6023 | -0.92 | +2114 |
| <=25 | 74.69 / 10559 | 74.87 / 10043 | +0.17 | -516 |
| 16-20 | 75.94 / 2839 | 74.19 / 3290 | -1.75 | +451 |
| 21-25 | 73.49 / 6650 | 73.43 / 4020 | -0.06 | -2630 |

Verdict: **FAIL W4**. Deep tail restoration is real, but the 11-20 middle
bucket regression is too large.

### Conservative active-stack variant

Parameters: `K=0.35, POWER=1.0, LO=10, HI=25, SAT=8.8, TARGET=33.4`.

| Bucket | Baseline WR7/N | Candidate WR7/N | ΔWR | ΔN |
|---|---:|---:|---:|---:|
| <=5 | 72.73 / 33 | 77.86 / 384 | +5.14 | +351 |
| <=10 | 76.74 / 258 | 78.79 / 1094 | +2.05 | +836 |
| <=15 | 78.88 / 1070 | 77.74 / 2147 | -1.14 | +1077 |
| <=20 | 76.75 / 3909 | 75.99 / 4507 | -0.75 | +598 |
| <=25 | 74.69 / 10559 | 74.92 / 9416 | +0.22 | -1143 |
| 16-20 | 75.94 / 2839 | 74.41 / 2360 | -1.54 | -479 |
| 21-25 | 73.49 / 6650 | 73.93 / 4909 | +0.44 | -1741 |

Verdict: **FAIL W4**. Boundary improves, middle still regresses.

### Stronger active-stack variant

Parameters: `K=0.55, POWER=0.60, LO=10, HI=25, SAT=6.0, TARGET=33.4`.

| Bucket | Baseline WR7/N | Candidate WR7/N | ΔWR | ΔN |
|---|---:|---:|---:|---:|
| <=5 | 72.73 / 33 | 77.94 / 349 | +5.21 | +316 |
| <=10 | 76.74 / 258 | 78.78 / 1037 | +2.04 | +779 |
| <=15 | 78.88 / 1070 | 78.34 / 1611 | -0.54 | +541 |
| <=20 | 76.75 / 3909 | 76.39 / 3558 | -0.35 | -351 |
| <=25 | 74.69 / 10559 | 75.00 / 7329 | +0.31 | -3230 |
| 16-20 | 75.94 / 2839 | 74.78 / 1947 | -1.16 | -892 |
| 21-25 | 73.49 / 6650 | 73.69 / 3771 | +0.21 | -2879 |

N-flow: p_le15 PASS, p16-20 **FAIL** (389/yr vs 398 floor), p21-25 PASS.
Verdict: **FAIL W4 + W5**.

## Phase F/G/H/I active-stack cache + refinement (2026-05-09)

Phase F built an exact ICH-entry cache by instrumenting the current simulator
path in memory. Cache: `.cache/ich_put_recal/phase_f_active_ich_entry_v46_1825d.parquet`.

Important discovery: current simulator output differs from stored active v46 DB
rows in 149,998 / 929,011 rows. Therefore active-stack candidate comparisons
must use `old_final` from the same simulator pass as baseline, not DB `Score`
rows. This is expected drift/staleness risk in a dirty worktree and is exactly
why the cache is the right substrate.

Phase G broad active-stack sweep:

- 305 variants
- W4+W5 pass: 1 / 305
- best broad pass: +0.11pp `<=25`

Phase H narrow refinement around the only passing basin:

- 1,200 variants
- W4+W5 pass: 65 / 1,200
- best refined candidate:

```
tail_floor      = 6
mid_cut         = 19
mid_scale       = 1.1363988623065646
boundary_k      = 0.29077629295205154
boundary_power  = 0.80868096663292
boundary_lo     = 19
boundary_hi     = 25
sat             = 16.114586262593352
target          = 38.909110933858486
```

Phase I validation of the best refined candidate:

| Bucket | Baseline WR7/N | Candidate WR7/N | ΔWR | ΔN |
|---|---:|---:|---:|---:|
| 0-5 | 79.41 / 136 | 79.13 / 369 | -0.28 | +233 |
| 6-10 | 77.20 / 307 | 79.27 / 275 | +2.07 | -32 |
| 11-15 | 77.93 / 897 | 78.14 / 645 | +0.21 | -252 |
| 16-20 | 75.52 / 2868 | 75.49 / 2787 | -0.03 | -81 |
| 21-25 | 73.65 / 6802 | 73.75 / 5380 | +0.10 | -1422 |
| <=25 | 74.66 / 11010 | 74.94 / 9456 | +0.28 | -1554 |

W2 multi-barrier check:

| W | Baseline WR/N | Candidate WR/N | ΔWR |
|---:|---:|---:|---:|
| 7 | 74.66 / 11010 | 74.94 / 9456 | +0.28 |
| 15 | 75.48 / 11007 | 75.81 / 9453 | +0.33 |
| 30 | 75.79 / 11004 | 76.07 / 9450 | +0.28 |

W3 time-window check:

| Window | Baseline WR/N | Candidate WR/N | ΔWR |
|---|---:|---:|---:|
| 1y | 73.93 / 2083 | 73.97 / 1840 | +0.04 |
| 3y | 72.57 / 6408 | 72.93 / 5607 | +0.36 |
| 5y | 74.66 / 11010 | 74.94 / 9456 | +0.28 |

N-flow/year:

- `p<=15`: 257.8/yr vs floor 196 PASS
- `p16-20`: 557.4/yr vs floor 398 PASS
- `p21-25`: 1076.0/yr vs floor 541 PASS

Interpretation: technically passable but not worth shipping. The edge is below
the intended +0.3pp materiality bar, the 1y lift is effectively flat, and the
deep-tail `0-5` bucket has only 0.02pp of tolerance slack.

## Phase A finding (cohort signal at v46 baseline)

| Score zone | N | WR7 (kijun<0) | Lift vs non-cohort | z | Interpretation |
|---|---:|---:|---:|---:|---|
| All puts ≤30 | 61,336 | 68.38% | -3.19pp | -6.46 | strong signal |
| **Boundary [21,30]** | 58,467 | 67.94% | -3.20pp | **-6.17** | strong, concentrated here |
| Mid [11,20] | 2,704 | 77.22% | +1.51pp | +0.94 | no signal |
| **Deep tail [0,10]** | 165 | 78.79% | +5.96pp | +1.08 | no signal — slight INVERSION |

The cohort signal lives entirely at the boundary. Deep tail z=+1.08 means
puts with kijun<0 in [0,10] actually OUTPERFORM puts with kijun>=0 — the
v44 ICH mechanism's deep-tail dampening is iatrogenic.

## Phase B/C apples-to-apples (variants applied to same v43 baseline)

| Variant | N kept | WR7 kept | <=5 / <=10 / <=15 / <=20 / <=25 (WR7) |
|---|---:|---:|---|
| v43 baseline (no ICH) | 10,641 | 74.84% | 77.56 / 77.52 / 77.69 / 76.02 / 74.84 |
| v44 current (sym log) | 10,641 | 74.84% | **65.71** / 77.02 / 78.76 / 76.79 / 74.84 |
| **asymK winner** | **9,064** | **75.34%** | 77.56 / 77.52 / 77.69 / 76.02 / **75.34** |

v44 current ICH redistributes alpha within the put cascade (gains at <15/<20)
but destroys it at <5 (-11.85pp WR7) and contributes zero at <=25 cumulative.
asymK winner cleanly displaces the boundary kijun<0 cohort (-1,577 puts) and
preserves the rest.

## Next step (not a ship procedure)

Do not edit production scoring from this experiment. The next research branch,
if revisited, should test a different mechanism family rather than tuning this
piecewise hybrid further. The current family appears saturated around
`+0.1pp` to `+0.28pp` once W4/W5 are enforced.

## Reproducibility

```bash
# Phase A: cohort z pre-flight (v46 baseline, ~110 sec barrier_outcomes load)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_a_cohort.py

# Phase B: 150-variant LHS sweep (~40 sec after parquet build)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_b_lhs.py

# Phase B compare: apples-to-apples (~5 sec)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_b_compare.py

# Phase D: W2 + W3 multi-window validation (~15 sec)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_d_w2_validate.py

# Phase E: exact active-stack shadow (~17 min per uncached candidate)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_e_v46_shadow.py

# Phase F: build exact ICH-entry cache (~25 min first run, cached after)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_f_active_cache.py

# Phase G/H/I: active-stack sweep, refine, validate (seconds once cached)
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_g_active_sweep.py
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_h_active_refine.py
PYTHONIOENCODING=utf-8 python experiments/ich_put_recal/phase_i_active_validate.py
```

Cached parquets at `.cache/ich_put_recal/`:
- `puts_v46_1825d.parquet` — Phase A v46 cohort data
- `puts_v43_1825d.parquet` — v43 pre-ICH baseline (used for variant simulation)
- `phase_b_lhs_results.parquet` — 300-variant LHS results
- `phase_e_v46_candidate_scores_*.parquet` — active-stack candidate checkpoints
- `phase_e_v46_shadow.json` — latest exact active-stack comparison summary
- `phase_f_active_ich_entry_v46_1825d.parquet` — exact active-stack ICH-entry cache
- `phase_g_active_sweep_results.parquet` — broad active-stack sweep results
- `phase_h_active_refine_results.parquet` — refined active-stack sweep results

## Lessons for future Stage 1 experiments

1. **Per-discrete-tier (W4) is load-bearing.** v44 ICH passed cumulative
   `<=25` at ship time; the within-tier asymmetry was invisible until the
   archaeology survey (2026-05-08) surfaced it. The new W4 gate explicitly
   checks every discrete bucket and would have caught the v44 issue.
2. **Cohort z by score zone is a fast pre-flight.** Phase A took ~110 sec
   (mostly barrier_outcomes load) and gave a definitive direction signal.
   Run this BEFORE building any sweep infrastructure.
3. **Asymmetric-K as a default put-side template.** The call side already
   uses asymmetric-K (v44 ICH calls, K_eff = 0.359 × score_norm^2.68). The
   put side should mirror it for any cohort signal that's concentrated at
   the gate boundary (typically the case — peripheral signals are noisier
   than core signals).
4. **Variant saturation is informative.** The Phase B LHS sweep showed ~50
   variants achieving essentially identical lift (~+0.50pp). This means
   the mechanism shape is what matters, not narrow parameter tuning. Don't
   over-fit Phase D — pick the most parsimonious passing variant.
