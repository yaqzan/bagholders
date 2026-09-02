# Phase 4 — Canonical MC Findings: Score-Stage Put Gradient

## Verdict: NO SHIP — Score-stage put gradient interventions do not reduce portfolio DD

---

## Results Table (N=200 × 8 windows, WorstDD per window)

| Candidate | 2021 | 2022 | 2023 | 2024 | 2025 | dip | 22-now | 5y | MaxDD | vs baseline |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0_baseline | 70.1 | 71.2 | 55.0 | 54.7 | 68.8 | 67.4 | 73.5 | 73.5 | **73.5%** | — |
| C1_redist_G15_T22 | 74.8+ | 73.9+ | 66.5+ | 53.9- | 73.2+ | 67.0 | 72.7- | 73.3 | **74.8%** | +1.3pp ✗ |
| C2_redist_G20_T22 | 71.5+ | 71.3 | 64.5+ | 48.0- | 71.8+ | 68.8+ | 71.8- | 74.8+ | **74.8%** | +1.3pp ✗ |
| C3_partial_G15_T35 | 69.0- | 71.0 | 59.0+ | 45.8- | 72.5+ | 69.2+ | 72.1- | 72.3- | **72.5%** | -1.0pp (noise) |
| C4_partial_G20_T50 | 72.2+ | 71.6 | **76.0+** | 53.3- | 70.5+ | 56.7- | 71.7- | 74.3+ | **76.0%** | +2.5pp ✗ |
| C5_aggressive_G25_T50 | 71.5+ | 73.7+ | 68.4+ | 51.6- | 68.7 | 63.8- | 72.0- | 73.3 | **73.7%** | +0.2pp ✗ |

`+` = worse than baseline by >0.5pp, `-` = better, space = within noise

---

## Candidate Descriptions

| # | Mechanism | K | gate | target | shape |
|---|---|---|---|---|---|
| C0 | Baseline (no lift) | 0.0 | 25 | 0 | linear |
| C1 | Redistribute deep<15 within cascade → score 22 | 1.0 | 15 | 22 | log |
| C2 | Redistribute deep<20 within cascade → score 22 | 1.0 | 20 | 22 | log |
| C3 | Lift deep<15 partially toward 35 (drops most via PUT_THRESHOLD>25) | 1.0 | 15 | 35 | log |
| C4 | Lift deep<20 out of cascade → score 50 | 0.5 | 20 | 50 | log |
| C5 | Lift all puts<25 aggressively toward 50 | 0.7 | 25 | 50 | log |

---

## Failure Modes

### C1/C2 — Redistribution within cascade
Lifting deep puts (overall 0-15) toward score 22 keeps them in the cascade but at the 21-25 tier allocation (12%). On high-density bear days (e.g. 2022-08-30: 124 concurrent puts), these redistributed signals still cluster and still hit correlated SLs together. The correlated-DD mechanism is unchanged; distribution is shuffled, not reduced.

Result: **max DD +1.3pp worse** (4/8 windows regress).

### C4 — Moderate lift out of cascade (target=50)
The K=0.5 scaling means signals near gate=20 lift modestly while signals at overall=5 lift dramatically. The non-uniform shaping creates a 2023 anomaly: **2023 DD spikes to 76.0% (+21pp vs baseline 55.0%)**. 2023 was a recovery year where put N was relatively low; the remapping of moderate puts (overall 16-20) toward 50 dramatically changes cascade fill order on certain recovery-year days.

Result: **max DD +2.5pp worse**.

### C5 — Aggressive lift (gate=25, target=50)
Lifts ALL puts with overall ≤ 25. The gate matching the PUT_THRESHOLD means every qualifying put signal gets some lift, reducing overall put N significantly. However, the pattern matches the Phase 1C finding that dropping puts WORSENS some windows (deep puts as partial hedges). Per-year results are inconsistent.

Result: **max DD +0.2pp** — effectively tied but no improvement.

### C3 — Marginal apparent winner
The only candidate showing max DD improvement (-1pp on 72.5% vs 73.5% baseline). However:
- 2023 DD +4.0pp worse (59.0% vs 55.0%)
- 2025 DD +3.7pp worse (72.5% vs 68.8%)
- dip DD +1.8pp worse (69.2% vs 67.4%)

At N=200 single-window, DD noise is estimated at ±2-3pp per window (per the Phase v32 noise floor finding). The -1pp max DD improvement is within this noise band and should not be interpreted as a reliable signal.

---

## Why Score-Stage Interventions Fail: Structural Diagnosis

Phase 1C established that hard-dropping deep puts (V3: drop overall ≤ 15) **worsens** 5y DD by +0.7pp. Phase 4 confirms this at scale across 6 gradient variants.

The underlying mechanism (from Phase 1 FINDINGS.md):

> **Portfolio level**: DD damage comes from macro-correlated days (50+ concurrent puts) where many positions move together. Top 20 highest put-density days are ALL in 2022 (Aug-Sep + Dec) and Feb 2023 — sustained bear-tape clusters.

Deep puts in a sustained bear tape ARE partially correlated with the correction being hedged. When you:
- Redistribute them within the cascade → same correlated exposure, different tier allocation
- Lift them out → reduce hedge, unbalanced signal removal hurts recovery years

Score-stage mechanisms operate on per-signal quality. The DD problem is a per-DAY density problem. These are two different dimensions that a scoring change cannot cleanly separate.

---

## Phase 5 Decision: Pivot to Portfolio-Stage

The correct lever is a **portfolio-stage daily put cap** — limit the number of puts admitted per calendar day to the cascade, sorted by conviction (lower overall = higher priority). This directly targets the Phase 1B finding:

> 2.1% of days have ≥50 put fires. These days produce correlated SL clusters → DD spikes.
> Top 20 highest put-density days: ALL in 2022 (Aug-Sep) and Feb 2023.

A daily cap of N=15-25 puts/day would fire only on ~2-5% of extreme days while leaving typical days (median 11/day) completely unaffected.

Properties:
- Portfolio-stage only (no Score.overall change, no recalculate, no ALGORITHM_VERSION bump)
- Directly targets the mechanism (correlated daily clustering)
- Preserves per-signal quality intact
- Adds one parameter: `MAX_PUTS_PER_DAY`

Next: Phase 6 — sweep MAX_PUTS_PER_DAY ∈ {10, 15, 20, 25, 30, ∞} at N=300 × 8 windows.
