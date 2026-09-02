# ICH Put-Side Tail Recalibration — Stage 1 Experiment

**Status:** PLAN. First Stage 1 experiment under the three-stage framework
shipped 2026-05-08.

**Hypothesis:** v44 ICH's symmetric log-ramp on the put side over-displaces
the deep-tail puts (<5, <10) — the cumulative `<25` H1 metric used at ship
time masked within-tier asymmetry. An asymmetric-K mirror of the call-side
shape (concentrate dampening at the gate boundary, taper toward the tail)
will preserve `<5` and `<10` N while removing the bad-quality boundary
puts.

**Stage 1 framework:** primary metric = WR7 cohort lift on the affected
cohort (puts with `kijun_pct < 0`); hard constraints W1-W6 from
[assessment-backtest.md](../../.claude/docs/assessment-backtest.md).

---

## Evidence motivating the recalibration

From the WR7 archaeology (`experiments/wr7_archaeology/survey.py`,
2026-05-08):

| Bucket | v43 WR7 / N | v44 WR7 / N | Δ WR7 | Δ N |
|---|---|---|---:|---:|
| <5 | 81.2 / 170 | 80.0 / 30 | -1.2 | **-82%** |
| <10 | 79.0 / 519 | 76.6 / 206 | -2.4 | -60% |
| <15 | 79.1 / 1263 | 80.2 / 694 | +1.1 | -45% |
| <20 | 77.2 / 2520 | 78.2 / 1972 | +1.0 | -22% |
| <25 | 77.1 / 4619 | 77.4 / 4600 | +0.3 | -0% |

Cumulative `<25` looked clean (the H1 metric used at ship time). Discrete
`<5` and `<10` lost both N and quality — a textbook within-tier asymmetry
that the new W4 gate explicitly checks for.

## Mechanism review

Current ICH put-side block ([scoring.py:1077-1088](../../database/utils/scoring.py)):

```python
if ICH_GATE_PUT_HI > ICH_GATE_PUT_LO and overall <= ICH_GATE_PUT_HI:
    _sg_p = ramp_log( (ICH_GATE_PUT_HI - overall) / (ICH_GATE_PUT_HI - ICH_GATE_PUT_LO), ... )
    _ig_p = ramp_log( -kijun_pct, ICH_KIJ_SAT_PUT )
    _w_p = _sg_p * _ig_p
    overall += ICH_K_PUT * _w_p * (ICH_TARGET_PUT - overall)
```

Constants: `ICH_GATE_PUT_LO=10`, `ICH_GATE_PUT_HI=27`, `ICH_K_PUT=0.278`,
`ICH_KIJ_SAT_PUT=8.8`, `ICH_TARGET_PUT=33.4`.

**Problem:** `_sg_p` uses log saturation on `(27 - overall) / 17` — peaks at
`overall=10` (deep tail), tapers to 0 at boundary. Combined with the
displacement `(33.4 - overall)` which is also LARGER for deep puts, the
total dampening force on `overall=5` is maximal:

```
overall=5  ->  sg_p≈1.0  *  ig_p≈1.0  *  K_PUT=0.278  *  (33.4-5)=28.4  ->  +7.9pts  ->  drift to 13
overall=20 ->  sg_p≈0.4  *  ig_p≈1.0  *  K_PUT=0.278  *  (33.4-20)=13.4 ->  +1.5pts  ->  drift to 21
```

**Deep tail gets pulled hardest, exactly the opposite of the v44 cohort
evidence** — which showed the bearish-kijun signal was concentrated at the
boundary (overall=21-27), not the deep tail.

## Proposed mechanism (asymmetric-K, mirror of call side)

Mirror the v44 call-side `K_eff = ICH_K_CALL × score_norm^2.68` shape on the
put side, but with `score_norm` defined to PEAK at the gate boundary and
TAPER toward the tail floor:

```python
if ICH_GATE_PUT_HI > ICH_GATE_PUT_LO and overall <= ICH_GATE_PUT_HI:
    # NEW: score_norm peaks at boundary (overall=27), 0 at floor (overall=10)
    _score_range_p = max(1, ICH_GATE_PUT_HI - ICH_GATE_PUT_LO)
    _score_norm_p = max(0.0, (overall - ICH_GATE_PUT_LO) / _score_range_p)  # INVERTED vs current
    _k_eff_p = ICH_K_PUT * (_score_norm_p ** ICH_K_PUT_POWER)               # NEW: power-law concentration
    _ig_p = ramp_linear( -kijun_pct, ICH_KIJ_SAT_PUT )                      # consider linear, not log
    if _k_eff_p > 0 and _ig_p > 0:
        overall += _k_eff_p * _ig_p * (ICH_TARGET_PUT - overall)
```

With `ICH_K_PUT_POWER` > 1, dampening concentrates near `overall=25-27`
(where the cohort signal lives) and tapers smoothly toward `overall=10-15`
(preserves deep tail). `<5` is below `ICH_GATE_PUT_LO` and untouched by
construction.

**Sweep parameters** for Phase B (LHS blast radius):
- `ICH_K_PUT` ∈ [0.15, 0.50]
- `ICH_K_PUT_POWER` ∈ [1.0, 4.0]
- `ICH_GATE_PUT_HI` ∈ [25, 30]
- `ICH_GATE_PUT_LO` ∈ [10, 18]
- `ICH_KIJ_SAT_PUT` ∈ [4.0, 14.0]
- `ICH_TARGET_PUT` ∈ [28, 38]
- `ICH_IND_RAMP_PUT` ∈ {linear, log}

7 parameters → ~150 LHS variants for Phase B.

---

## Stage 1 W1-W6 hard constraints (ship gate)

**Affected cohort** = puts (overall ≤ 30) with `kijun_pct < 0` at v46 baseline.

| # | Constraint | Threshold | Notes |
|---|---|---|---|
| **W1** | Cohort z-score | ≥ +3 on (overall<=30, kijun_pct<0) → put-WR7 lift | Phase A pre-flight |
| **W2** | Multi-barrier-window directional consistency | WR3/WR5/WR7/WR15/WR30 all positive on cohort | 5-window check |
| **W3** | Multi-time-window WR7 consistency | 1y/3y/5y signs agree | |
| **W4** | Per-discrete-tier non-regression | **<5 WR7 ≥ baseline -0.5pp**, **<10 WR7 ≥ baseline -0.5pp**, plus all other discrete buckets | THE KEY constraint — what v44 ICH failed |
| **W5** | N capacity floor | <5 N ≥ ?, <10 N ≥ 100/yr (provisional — see below) | |
| **W6** | Gradient preservation | <5 ≥ <10 ≥ <15 ≥ <20 ≥ <25 ≥ <30 WR7 ordering | |

**N capacity floor for <5 / <10:** the H6 table only specifies `p≤15`
(196/yr floor). Provisional sub-tier floors:
- `<5`: 25/yr (sparse, allow drop but require non-zero flow)
- `<10`: 100/yr (need cascade signal)

These are guesses — the W5 gate should soft-flag rather than hard-veto. If
the candidate's <5 drops below 25/yr but <10 holds at 100+ and `<25`
cumulative is healthy, that's an acceptable trade.

---

## Execution plan

### Phase A — Build features + cohort validation (~30 min)

1. **`build_puts_features.py`** — adapt `experiments/weekly_avwap/build_features.py`
   for put peaks (overall ≤ 30) at v46. Pull `kijun_pct` per peak +
   barrier-walk outcomes at W=3, 5, 7, 15, 30 with both generic
   (K=1σ/M=2σ for puts) and option-aligned (15 DTE: K=0.903σ/M=0.516σ;
   30 DTE: K=1.274σ/M=0.983σ) barriers.
2. **`phase_a_cohort.py`** — compute cohort z-score for the affected cohort
   on v46 baseline. **Pre-flight gate: if z < +3, abandon.** The v44 ICH
   signal must still be present at v46 baseline (the v45 ETF de-contamination
   and v46 WVD-Wave changes may have shifted the cohort statistics).

### Phase B — LHS blast radius (~15 min)

3. **`phase_b_lhs.py`** — 150 LHS samples over the 7-parameter space.
   Evaluate WR7 lift on cohort + per-discrete-tier WR7 + N retention. Rank
   by composite objective: `cohort_lift × indicator(W4_pass)`.

### Phase C — Bayesian drill (~30 min)

4. **`phase_c_drill.py`** — 200 Optuna evals on top-quartile basin from
   Phase B. Same objective. Output: top 5 candidates.

### Phase D — Fine-tune + W1-W6 validation (~10 min)

5. **`phase_d_validate.py`** — 50 dense-grid variants within ±10% of Phase
   C winner. Compute full W1-W6 on each. Output: ship candidate or "no
   candidate clears W1-W6 — abandon."

### Total estimated compute: ~85 min

---

## Risks

1. **The kijun_pct signal may have weakened at v46 baseline.** v45 (breadth
   ETF de-contamination) and v46 (WVD-Wave) shifted the score distribution.
   If the v44 cohort signal no longer survives at v46, recalibration is
   pointless — the right answer is to disable ICH put-side entirely, not
   tune it. Phase A guards against this.

2. **Asymmetric-K may not match the cohort shape.** If the v44 cohort signal
   is genuinely uniform across the put score range (not concentrated at
   boundary), a power-law concentration won't fit better than the current
   log saturation. Phase B will reveal this — if best-of-150 LHS doesn't
   beat current params on cohort lift, the asymmetric-K hypothesis fails
   and we should investigate alternatives (e.g. tighter `ICH_GATE_PUT_LO`
   raised to 15).

3. **Stage 2 interaction.** v46 was assessed under the current barrier set;
   if Stage 2 (TP/SL) is later re-tuned, the WR7 anchor will still track
   option TP% (per the 2026-05-09 correlation analysis), so Stage 1
   conclusions remain valid. No risk here — this is what the framework
   factorization is designed for.

## Success criteria

- Ship candidate clears all W1-W6 hard constraints at 5y
- 5y `<5` WR7 ≥ baseline -0.5pp AND `<10` WR7 ≥ baseline -0.5pp (the v44
  failure mode is fixed)
- `<25` cumulative WR7 ≥ baseline (no regression on the original ICH
  affected tier)
- `<5` N ≥ 25/yr AND `<10` N ≥ 100/yr at 5y (capacity preserved)
- Multi-window directional consistency (W2, W3) clean
- Cohort z ≥ +3 on the post-recalibration cohort
