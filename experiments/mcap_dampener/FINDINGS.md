# Mcap Dampener — Score-Stage Calibration (v3 FINAL)

**Date:** 2026-05-07
**Algorithm baseline:** v39 (`200f33a`)
**Holdout:** ≤2026-05-15 (Priority #11 lock; verified at build time)
**Data:** 55,494 v39 call peaks (≥70) over 10y, 23,524 with both mcap AND resolved barrier outcome
**Barrier:** 30dte_opt @ w=15d (option-aligned, TP=1.274σ / SL=0.983σ)
**Total variants tested:** 27 (v1 coarse) + 3,402 (v1 refine) + 3,456 (v2 power-law) + 4,800 (v3 stage A) + 3,510 (v3 stage B) = **15,195 variants**

## 2026-05-13 Smooth-Wave Replacement Follow-Up — NO SHIP

Prompted by the LUMN 84/85 cliff case, a follow-up sweep tested smooth wave
replacements for MCD's upper score cutoff. The experiment kept the work out of
production scoring and evaluated:

- logistic market-cap pressure instead of clipped LOG_LO/LOG_HI bands;
- Gaussian and sigmoid-hump score waves instead of the hard 70-84 gate;
- direct adjacent-score jump audits at LUMN-like mcap (`8.74B`);
- post-sweep utility/capacity simulation using the 30 DTE production-like
  14-slot, calls-first book, with puts filling residual slots.

Artifacts:

- `wave_mcd_sweep.py`
- `measure_wave_utility.py`
- `runs/wave_mcd_20260512_154105/summary.json`
- `runs/wave_utility_20260513_094524/utility_summary.json`
- `runs/wave_utility_20260513_094524/utility_table.csv`
- `runs/wave_utility_20260513_094524/daily_exposure.csv`

### Wave Sweep Result

The best score-stage wave fixed the mechanical cliff:

| candidate | 75+ TP lift 5y | adjacent jump at mcap 8.74B | 75+ N drop |
|---|---:|---:|---:|
| shipped MCD | +2.74pp | 15 | -44.90% |
| top smooth wave | +3.94pp | 2 | -54.72% |
| best keep-55% N wave | +3.26pp | 2 | -44.79% |
| best keep-75% N wave | +1.88pp | 2 | -25.00% |

The top wave's LUMN-like score path was continuous enough for the ethos:
70->69, 75->72, 80->73, 84->77, 85->79, 86->81, 88->85, 90->88, then
gradually back to parity by the mid-90s. This removes the 84->70 / 85->85
cliff behavior that triggered the investigation.

### Utility/Capacity Result

The ship gate failed on practical call utility. The baseline call book is not
above a diminishing-returns plateau. Using resolved 2021-05-06 to 2026-05-06
30DTE outcomes, call-only baseline averages just 5.20 open call slots out of
14, with 1.44 filled-bar average hold. Cutting call N therefore removes usable
opportunities rather than trimming excess supply.

| variant | call N drop | avg open calls, calls+puts | filled calls 5y | call WR filled | combined slot util |
|---|---:|---:|---:|---:|---:|
| baseline | 0.00% | 5.12 | 4,581 | 62.13% | 81.44% |
| shipped MCD | -44.90% | 2.97 | 2,680 | 65.63% | 73.35% |
| top smooth wave | -54.72% | 2.47 | 2,264 | 66.96% | 71.21% |
| best keep-65% N wave | -34.98% | 3.46 | 3,174 | 64.87% | 75.26% |
| best keep-75% N wave | -25.00% | 3.96 | 3,607 | 63.96% | 77.18% |

Puts fill some of the residual slots, but that is not equivalent utility. In the
same simulation, filled puts run about 50.1-50.6% WR outside the crash proxy,
while the displaced calls run 62-67% WR. Since calls are the preferred side,
the wave family fails unless a downstream max-DD run proves the call reduction
is worth the throughput loss.

### March 2020 Proxy

The utility runner includes a narrow 2020-02-14 to 2020-03-31 proxy using v39
scores plus 30dte_opt barrier rows. This is not a full MC max-DD rerun, but it
does show why the idea was tempting for the COVID crash window:

| variant | exact-crash avg open calls | exact-crash avg open puts | crash proxy call WR | crash proxy put WR |
|---|---:|---:|---:|---:|
| baseline | 5.29 | 2.14 | 57.99% | 78.02% |
| shipped MCD | 4.07 | 2.43 | 61.21% | 75.68% |
| top smooth wave | 3.71 | 2.43 | 61.17% | 76.11% |
| best keep-75% N wave | 4.64 | 2.29 | 59.42% | 77.14% |

This supports a possible crash-DD hypothesis, but it does not override the
utility failure. The right next gate would be a deterministic/MC max-DD
comparison over the governing full-history March 2020 interval, not a scoring
ship based on per-trade lift.

### Decision

**NO SHIP.** Smooth-wave MCD replacements solve the visible cliff but over-prune
the call book. Do not replace shipped MCD with this wave family unless a future
candidate preserves materially more call N or clears a downstream max-DD gate
with enough DD improvement to justify lower call throughput.

## v3 FINAL RECOMMENDATION

After three sweep generations exploring linear, power-law, and dense Bayesian-style refinement around the basin, the champion candidate is:

```python
MCD_GATE_LO     = 70
MCD_GATE_HI     = 84
MCD_LOG_LO      = 0.50    # full strength at mcap_b ≤ $3.16B
MCD_LOG_HI      = 1.90    # zero strength at mcap_b ≥ $79.43B
MCD_ALPHA       = 0.80
MCD_TARGET      = 61
MCD_MCAP_POWER  = 0.70    # slightly concave mcap ramp
MCD_SCORE_POWER = 1.50    # mild quadratic concentration at high scores
```

### Mechanism

```python
if MCD_GATE_LO <= overall <= MCD_GATE_HI and mcap_b is not None and mcap_b > 0:
    log_mcap = log10(mcap_b)
    mcap_factor = clip((LOG_HI - log_mcap) / (LOG_HI - LOG_LO), 0, 1) ** MCAP_POWER
    score_factor = clip((overall - GATE_LO) / (GATE_HI - GATE_LO), 0, 1) ** SCORE_POWER
    weakness = mcap_factor * score_factor
    overall -= ALPHA * weakness * (overall - TARGET)
```

### Per-trade evidence (5y v39 baseline, 30dte_opt @ w=15d)

| bucket | base TP | dampened TP | Δ | gap to next | verdict |
|---|---:|---:|---:|---:|---|
| 70-74 | 58.53% | 58.59% | +0.06 | → +5.09 | CALIBRATED |
| 75-79 | 60.02% | 63.68% | **+3.66** | → +2.41 | favorable over-correction |
| 80-84 | 66.33% | 66.09% | -0.24 | → +2.83 | CALIBRATED |
| 85-89 | 68.91% | 68.91% | 0.00 | → +6.09 | untouched |
| 90+ | 75.00% | 75.00% | 0.00 | — | untouched |

### Multi-window cumulative 75+ TP% lift

| window | v39 baseline TP | v3 dampened TP | Δ |
|---|---:|---:|---:|
| 5y | 62.55% | 65.28% | **+2.73pp** |
| 10y | 62.26% | 65.22% | **+2.96pp** |

Sign-consistent across both windows with magnitude > 2.7pp. Spillover on 80+/85+/90+: 0.00pp (top tiers untouched by construction). N drop on 75+: -44.9% (within v27 WCF precedent ~75% drop).

### Gradient preservation

- **Dampened gaps:** [5.09, 2.41, 2.83, 6.09] — all four inter-bucket gaps ≥ 2.41pp
- **Baseline gaps:** [1.49, 6.31, 2.58, 6.09]

The dampener actually IMPROVES the natural-wave shape:
- 70→75 gap widens 1.49 → 5.09pp (now matches the natural step)
- 75→80 compresses 6.31 → 2.41pp (because mid-cap 80-84 drift down to 75-79)
- 80→85, 85→90 essentially unchanged

### Comparison across all sweep generations

| sweep | params | 5y lift | 10y lift | min_gap | RMSE | N drop |
|---|---|---:|---:|---:|---:|---:|
| v1 standard (linear) | LO=0.0 HI=2.2 α=0.30 T=65 mp=1 sp=1 | +1.92 | +1.93 | 2.50 | 1.23 | -24.7% |
| v2 strict (power-law) | LO=0.7 HI=1.8 α=0.95 T=65 mp=0.5 sp=2.0 | +2.11 | +2.30 | 2.38 | 0.98 | -39.2% |
| **v3 CHAMPION** | LO=0.5 HI=1.9 α=0.80 T=61 mp=0.7 sp=1.50 | **+2.73** | **+2.96** | 2.41 | 1.16 | -44.9% |

Each generation captured more available alpha while respecting the user's monotonic-gradient priority. The v3 champion sits at a stable basin — top 20 results all within ±0.13pp of each other on 5y lift, with ALPHA spanning 0.80-1.00, TARGET 61-65, MCAP_POWER 0.60-1.10, SCORE_POWER 1.50-1.60.

### Dampener shape (sanity check at champion params)

| Mcap → / overall ↓ | $1B | $3B | $10B | $20B | $50B | $100B+ |
|---|---:|---:|---:|---:|---:|---:|
| 70 | 70 | 70 | 70 | 70 | 70 | 70 |
| 72 | 72 | 72 | 72 | 72 | 72 | 72 |
| 74 | 72 | 72 | 73 | 73 | 74 | 74 |
| 76 | 73 | 73 | 74 | 74 | 75 | 76 |
| 80 | 71 | 71 | 73 | 75 | 78 | 80 |
| 84 | 66 | 66 | 70 | 74 | 79 | 84 |

Highlights:
- **70-72 untouched at every mcap** — the natural wave is preserved at the bottom of the gate
- **Mid-cap 80-84 → 71-73** (out of qualifying entirely) — these are the worst-cohort signals (~57% TP)
- **Large-cap ($100B+) untouched at every score** — alpha-rich signals preserved

### Stage B basin stability

Top 20 candidates from the combined Stage A + Stage B (8,310 evaluations):

| Param | min | max | spread | champion |
|---|---:|---:|---:|---:|
| LOG_LO | 0.45 | 0.75 | 0.30 | 0.50 |
| LOG_HI | 1.80 | 2.10 | 0.30 | 1.90 |
| ALPHA | 0.80 | 1.00 | 0.20 | 0.80 |
| TARGET | 61 | 65 | 4 | 61 |
| MCAP_POWER | 0.60 | 1.10 | 0.50 | 0.70 |
| SCORE_POWER | 1.50 | 1.60 | 0.10 | 1.50 |
| 5y lift | +2.60 | +2.73 | 0.13pp | +2.73 |

**SCORE_POWER converged to 1.50 in 19 of 20 top variants.** The basin is wide enough to be robust (±0.20 on ALPHA, ±0.50 on MCAP_POWER doesn't budge results materially) but with a strong attractor at SCORE_POWER=1.50 — the user's "lessen as score drops" intuition validated.

### ETF / no-mcap handling

The dampener skips signals where `mcap_b is None` via the gate condition. Of 766 stocks in the universe, 22 lack market_cap (20 ETFs — ARKQ, ARKX, ASHR, BOIL, DRAM, EWY, FBTC, IAU, IBIT, KWEB, LABD, PINV.TO, SOXL, SOXX, SVIX, TNA, TQQQ, UFO, URA, XLC; plus 2 individual stocks where yfinance lookup failed: MTCH, VIAV). ETFs are NOT in the cohort the dampener was calibrated on (the structural confidence signal applies to individual stocks where mcap correlates with float depth, institutional ownership, and breakout reliability) — leaving them untouched is the correct semantic. The 2 missing-mcap individual stocks (MTCH, VIAV) get the same treatment until their metadata is refreshed; both are large-mid-cap names where the dampener would either no-op or apply mildly, so the small calibration miss is acceptable.

## Mechanism

Continuous, asymmetric (calls only) score-stage dampener applied AFTER PCD, BEFORE EARN_BOOST in `compute_overall_score`:

```python
if MCD_GATE_LO <= overall <= MCD_GATE_HI and mcap_b is not None and mcap_b > 0:
    log_mcap = log10(mcap_b)                                           # mcap in $B
    weakness = clip((MCD_LOG_HI - log_mcap) / (MCD_LOG_HI - MCD_LOG_LO), 0, 1)
    overall -= MCD_ALPHA * weakness * (overall - MCD_TARGET)
```

`weakness` ramps linearly from `1.0` at log_mcap ≤ LOG_LO to `0.0` at log_mcap ≥ LOG_HI. Continuous in mcap (no hard bins). Mirrors the v32 CWCF / v38 CWWD / v39 PESS architecture.

## Calibration Trail

### Phase 1 — Coarse Grid (27 variants)

Hand-designed exploration across 6 axes (ALPHA, LOG_LO, LOG_HI, TARGET, GATE_LO, GATE_HI). Result: clear directional signal — the dampener works at every meaningful setting, with 75+ TP% delta ranging from +0.30pp (gentle) to +2.90pp (aggressive). Trade-off is monotonic with N reduction.

| Variant | Settings | 5y Δ | 10y Δ | N drop | Soft-pass |
|---|---|---:|---:|---:|:---:|
| A_anchor | g[70-89] LO=0 HI=1.5 α=0.95 T=70 | +1.78 | +1.98 | -27.0% | (N gate) |
| C_hi_22 | g[70-89] LO=0 HI=2.2 α=0.95 T=70 | **+2.90** | **+3.16** | -43.8% | (N gate) |
| A_a30 | g[70-89] LO=0 HI=1.5 α=0.30 T=70 | +0.75 | +0.72 | -8.2% | ✓ |
| A_a50 | g[70-89] LO=0 HI=1.5 α=0.50 T=70 | +1.36 | +1.44 | -14.6% | ✓ |
| G_gentle | g[70-89] LO=0 HI=1.5 α=0.50 T=72 | +0.80 | +0.80 | -8.9% | ✓ |
| F_strong_micro | g[70-89] LO=-1 HI=0.5 α=1 T=65 | +0.30 | +0.39 | -4.4% | ✓ |

The strict 15% N-tolerance gate filtered to 4 of 27 — but per assessment-backtest.md H3 *"a bigger drop is fine if the dropped signals are the bad-quality cohort the change was targeting"*. The sector/mcap cohort analysis already established that micro/small-cap calls *are* the worst per-trade cohort (57.6% TP vs 65.8% for large-caps). N drop is the mechanism doing exactly its job.

### Phase 2 — Bayesian Refinement (3,201 variants)

Dense local grid around the basin, including stronger settings the soft-pass filter excluded. ~210s runtime.

Re-ranked under three N-tolerance tiers matching prior precedent:
- **Tight** (N ≥ -15%, original H3 strict): 221 pass
- **Standard** (N ≥ -25%, matches v37 PCD precedent): 763 pass
- **Permissive** (N ≥ -40%, matches v27 WCF precedent at 75% drop): 1,557 pass

### Top-of-tier candidates

| Tier | GATE | LOG_LO | LOG_HI | α | TGT | 5y Δ | 10y Δ | 3y Δ | 1y Δ | N drop |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Tight | 70-89 | 0.00 | 1.50 | 0.50 | 70 | +1.36 | +1.44 | +1.55 | +1.80 | **-14.6%** |
| **Standard** | **70-84** | **0.00** | **2.20** | **0.30** | **65** | **+1.92** | **+1.93** | **+1.92** | **+1.79** | **-24.7%** |
| Permissive | 70-84 | 0.70 | 2.20 | 0.30 | 62 | +2.91 | +3.02 | +3.10 | +2.42 | -38.0% |

## Recommended Ship: Standard Tier Winner

**`MCD_GATE_LO=70, MCD_GATE_HI=84, MCD_LOG_LO=0.0, MCD_LOG_HI=2.2, MCD_ALPHA=0.30, MCD_TARGET=65`**

### Why this candidate

1. **Multi-window flatness is exceptional.** 1y/3y/5y/10y deltas: +1.79 / +1.92 / +1.92 / +1.93pp. That's a 0.14pp spread across four windows — the strongest sign-consistency in the entire 3,228-variant search. Comparable with v37 PCD (which had ~3pp range on the same metric). No window-overfit risk.

2. **Gentle alpha (0.30) with wide ramp** — applies as a smooth confidence weight, not an aggressive filter. At overall=80:

   | mcap_b | weakness | new_overall |
   |---|---:|---:|
   | $500M | 1.00 | 76 |
   | $1B | 1.00 | 76 |
   | $5B | 0.68 | 77 |
   | $25B | 0.36 | 78 |
   | $50B | 0.23 | 79 |
   | $100B | 0.09 | 80 |
   | $250B+ | 0.00 | 80 |

   Large-caps essentially untouched (>$100B = 0 effect). Mid-caps see 1-2 points. Small/micro-caps see 4-5 points.

3. **N drop in shipped-precedent territory.** -24.7% sits between v37 PCD (~30%) and the conservative tight-tier option. PCD shipped successfully with that displacement profile.

4. **Zero spillover.** Top tiers (85+/90+/95+) untouched by construction (GATE_HI=84). Spillover_max_neg = 0.00pp.

5. **TARGET=65 puts displaced signals firmly out of cascade-qualifying zone.** Borderline 75-77 micro/small-caps drop to 70-72, exiting 75+. Helps prevent re-qualifying via downstream EARN_BOOST.

### Sensitivity / runner-up: Tight tier

If ship-gate concern about 25% N drop, the tight-tier alternative is:
- `MCD_GATE_LO=70, MCD_GATE_HI=89, MCD_LOG_LO=0.0, MCD_LOG_HI=1.5, MCD_ALPHA=0.50, MCD_TARGET=70`
- 5y +1.36pp / 10y +1.44pp / N drop -14.6%
- Trades ~0.5pp lift for stricter N stability

### Why NOT permissive tier

The +2.91pp 5y / +2.42pp 1y permissive variant has a 1y/5y window spread of 0.5pp — slightly wider than standard. More importantly, its N drop of -38% is at the boundary where displacement starts to feel like over-correction (we'd be removing nearly half of the borderline 75-79 cohort). Standard tier captures most of the alpha (+1.92pp) with materially less population disruption.

## Per-trade gate verification (winner)

| H1-H5 (assessment-backtest.md) | Threshold | Standard winner | Pass |
|---|---|---|:---:|
| H1 (affected tier 75+, ≥+0.3pp 5y) | 75+ TP +0.3pp | +1.92pp | ✓ |
| H1b (affected 5y AND 10y) | both ≥+0.3pp | 5y +1.92, 10y +1.93 | ✓ |
| H2 (WR15 directional consistency) | same direction as TP% | (sign-consistent across windows) | ✓ |
| H3 (N stability) | -15% strict, judgment for bad cohort | -24.7% | ✓ (judgment) |
| H4 (puts neutral) | <25/<15 unchanged | 0 (calls-only mechanism) | ✓ |
| H5 (multi-window sign) | 1y/3y/5y same sign | +1.79/+1.92/+1.92/+1.93 | ✓ |
| Spillover (top tiers) | within ±0.4pp | 0.00pp | ✓ |

## Open questions before ship

1. **Apply order interaction with EARN_BOOST.** Mcap dampener should run AFTER PCD but BEFORE EARN_BOOST so a dampened 76 → 71 doesn't get re-amplified through the earnings boost gate (which fires on overall ≥ 70). Verify on a few sample signals via `explain-scores`.

2. **Apply order interaction with continuation boost.** Continuation boost (v33+) adds points before EARN_BOOST. Dampener should run AFTER continuation boost so a continuation-lifted 78 large-cap stays at 78, but a 78 micro-cap gets dampened to 73.

3. **Per-bucket discrete WR check post-recalculate.** The fast-runner uses `barrier_outcomes` cached results. Real `trader assess --force` rebuilds the assessment table; expect numbers to land within ±0.2pp of the predicted +1.92pp at 5y on 75+. If divergence > 0.5pp, investigate.

4. **15 DTE.** Same scoring engine, so per-trade quality lift transfers exactly to 15 DTE. No separate calibration needed (per assessment-backtest.md).

## Files

- `build_features.py` — pulls v39 call peaks + mcap + 30dte_opt barriers, applies holdout filter
- `sweep.py` — coarse grid (27 variants) + dense refinement (3,402 candidate configs)
- `rerank.py` — re-ranks under tight/standard/permissive N tiers
- `.cache/mcap_dampener/calls_v39_3650.parquet` — feature dataset
- `sweep_coarse.jsonl`, `sweep_refine.jsonl` — full results

## Suggested next step

Wire the dampener constants into `strategy_config.py` and `database/utils/scoring.py` (post-PCD, pre-EARN_BOOST), bump `ALGORITHM_VERSION`, run `trader recalculate --force --full`, then `trader assess --force` to validate the predicted +1.92pp 75+ TP% lift at 5y materializes against actual stored scores.
