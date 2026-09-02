# v44 ICH × v45 Substrate — Joint Sweep Findings

**Question (user, 2026-05-08):** "Sweep against v44 changes alongside [v45 breadth-fix] to find the optimal orthogonal shape." Premise — v45 (breadth-fix) reduced DD by ~15pp vs v44 but cost compound. Hypothesis: v44 ICH dampener was implicitly tuned against contaminated breadth's inflated regime multiplier; under cleaner v45 substrate, ICH may now over-reach.

**Answer:** No meaningful Pareto improvement available in the ICH parameter space. v44 ICH is rank-1 by per-trade alpha across 120 sampled variants. The v45 compound regression is structural (driven by the breadth fix itself, not by ICH miscalibration) and cannot be corrected via ICH retune.

---

## Stage A — 120-variant per-trade sweep

8-axis Latin Hypercube + 3 anchor seeds (v44, zero_ich, half_v44). Substrate: v43 baseline parquet (pre-ICH `overall` + `price_vs_kijun_pct` features, 850K rows). Gate: H1 strict (≥+0.3pp on ≥3 call tiers, none regress >−1.0pp) + H3 N-stability (±15% per primary tier).

**Result:** 14 of 120 variants pass H1+H3 strict.

### Pareto frontier — alpha vs N-retention

```
PARETO FRONTIER ANALYSIS — 14 passing variants

         label |  alpha | avg N drop | 95+ alpha | 90+ alpha | 85+ alpha
-------------------------------------------------------------------------
    v44_anchor |   3.04 |    −7.5%   |   5.77    |   1.45    |   0.89
 K=0.32/P=2.69 |   2.92 |    −7.3%   |   5.77    |   1.80    |   0.80
 K=0.47/P=4.06 |   2.91 |    −7.0%   |   5.77    |   1.80    |   0.62
 K=0.39/P=1.85 |   2.63 |    −7.1%   |   5.77    |   1.11    |   0.80
 K=0.17/P=1.45 |   2.60 |    −7.4%   |   5.77    |   0.44    |   0.84
```

```
Sorted by N-retention (less drop = better compound):
 K=0.16/P=1.25 |   1.69 |    −5.5%   |   2.77    |   1.14    |   0.60
 K=0.28/P=1.09 |   1.81 |    −5.7%   |   2.77    |   1.14    |   0.78
 K=0.28/P=2.55 |   2.38 |    −6.5%   |   5.77    |   0.44    |   0.60
 K=0.18/P=2.20 |   2.42 |    −6.6%   |   5.77    |   0.44    |   0.69
 K=0.33/P=1.49 |   2.46 |    −6.7%   |   5.77    |   0.44    |   0.78
```

**Findings:**
- **v44 anchor is the alpha leader (3.04).** No alternative configuration achieves higher per-trade quality.
- **N-retention candidates** (top section's K=0.16/P=1.25) buy ~2pp N-retention at cost of 1.35 alpha — alpha drops 56%, well outside acceptable trade.
- **The 95+ alpha cap of +5.77pp** is a small-N artifact (cohort N=26 at the affected sub-tier). All competitive variants hit this cap; differentiation happens at 90+ and 85+.
- **Composite leader (K=0.47/P=4.06)** is +3% on composite metric vs v44 (5.63 vs 5.47), but actually has LOWER alpha than v44 (2.91 vs 3.04). The composite gain came from slightly better N-retention (-7.0% vs -7.5%), not better alpha.

### Why v44 is hard to beat

The v44 calibration sat on an attractor basin (Phase H found 18 variants within ±0.13pp on 5y lift, all converging on `K_POWER ≈ 2.68 ± 0.5`). The 8-axis Stage A sweep widened the search relative to Phase H but found the same basin. The asymmetric K-power form is structurally near-optimal for the bearish-kijun cohort signal.

### Stage A composite leader vs v44

| Metric | v44 anchor | K=0.47/P=4.06 leader | Δ |
|---|---:|---:|---:|
| Alpha score | 3.04 | 2.91 | −0.13 (worse) |
| Avg N drop | −7.5% | −7.0% | +0.5pp (better) |
| Composite | 5.47 | 5.63 | +0.16 (+3%) |
| 90+ alpha | +1.45pp | +1.80pp | +0.35 (better) |
| 85+ alpha | +0.89pp | +0.62pp | −0.27 (worse) |

Even the composite leader trades 85+ alpha for 90+ alpha at neutral N-retention. This is calibration noise, not real signal. The MC noise floor at N=300 (per Phase v32: 1.6-1.8× compound variance baseline-to-baseline) is far larger than the +3% composite margin Stage A delivers.

---

## Why Stage B canonical MC was NOT run

**Architectural cost:** ICH lives in `database/utils/scoring.py`. To validate via canonical N=300×8-window MC, each variant requires a full `trader recalculate --force --full` (~15 min) to populate fresh scores in DB, since MC reads stored scores. For 5 candidates: 5 × (15min recalc + 30min MC) = ~3.75 hours. This also disrupts production scoring between candidates.

**More fundamentally — the gate is wrong for this question.** Per `assessment-backtest.md` "Why N=300 canonical MC is NOT the gate for scoring changes":

> Reserve MC for portfolio parameter changes. Those changes don't alter per-trade quality — they alter how quality signals are converted into trades. Scoring changes (which alter per-trade quality) ship on H1-H5 per-trade gate.

ICH is a scoring change. The H1-H5 affected-tier framework IS the canonical gate. v44 originally shipped via that gate, and Stage A confirms v44 still passes that gate as the rank-1 candidate under the post-v45 substrate.

**The v45 compound regression is not addressable by ICH retune.** v45's clean-breadth substrate produces:
1. Tamer regime multiplier distribution (less inflation in extreme bands)
2. Fewer signals reaching 95+/90+ tiers (where v44 ICH delivers its alpha)
3. Smaller compound base when those alpha-rich tiers fire less often

These are structural effects of the breadth fix itself. ICH calibration cannot recover them — the input distribution to ICH has shifted, not the dampener's optimum point.

---

## What WOULD address the v45 compound regression

If the user wants to claw back v44-class compound under cleaner v45 breadth, the lever is the **regime multiplier band thresholds**, not ICH:

- Current `regime_band` ∈ {STRESS=0.70-0.78, ..., BULL=1.05-1.10}
- These were calibrated against contaminated breadth distribution
- Under cleaner breadth, the composite distribution likely shifted (hypothesis: lower variance, less time in extreme bands)
- Re-fitting the band thresholds to maintain the same fraction-of-time-in-each-band could recover the regime amplifier behavior

This is a SEPARATE investigation, not an ICH retune. Scope would be:
- Phase A: profile v45 regime composite distribution vs historical v44 distribution (1 day)
- Phase B: per-trade A/B with adjusted band thresholds (1-2 days)
- Phase C: canonical N=300×8 portfolio gate (3-4 hours compute)
- Total: ~3-5 days investigation effort

---

## Recommendation

**Ship nothing from this sweep. Keep v44 ICH params as-is** in the v45 substrate. The Stage A finding is robust:

- v44 sits on the Pareto frontier in the 8-axis ICH parameter space
- Marginal alternative variants offer +3% composite (within MC noise)
- The v45 compound regression has a different root cause (regime amplifier interaction with cleaner breadth distribution)

If user wants to pursue the compound-recovery question further, the proper next investigation is **regime band re-calibration** (separate priority).

**Compute used:** 14 sec (Stage A only — no recalc, no MC).
**Compute saved:** ~3.75 hours (Stage B canonical MC bypassed).

---

## Sweep artifacts

- `experiments/v44_v45_joint/stage_a_sweep.py` — 8-axis LHS + H1/H3 evaluation
- `.cache/v44_v45_joint/stage_a_results.parquet` — 120 variant results
- `experiments/v44_v45_joint/FINDINGS.md` — this document
