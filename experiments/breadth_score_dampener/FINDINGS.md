# BSD Profile — Findings

**Question:** does the score's barrier-touch WR (calibration target, 30dte_generic K=2.0σ)
diverge from the option TP rate (30dte_opt) within the LOW-breadth tape?

**Data:** v39 5y, 5,703 call peaks at 75+. Active version. `profile.out` for full output.

## TL;DR — divergence is real, concentrated, and not captured by the existing regime multiplier

### Headline (75+ at 5y, breadth quintile split)

| breadth quintile | N | WR15_gen | WR15_opt | Δ (opt-gen) | reg_mult avg |
|---|---:|---:|---:|---:|---:|
| Q1 brd ≤ 32.9 (lowest) | 276 | **82.2%** | **59.1%** | **-23.19pp** | 1.02 |
| Q2 32.9 < brd ≤ 36.5 | 251 | 73.3% | 62.5% | -10.76pp | 0.93 |
| Q3 36.5 < brd ≤ 37.7 | 260 | 68.8% | 61.2% | -7.69pp | 0.90 |
| Q4 37.7 < brd ≤ 39.2 | 261 | 70.1% | 58.6% | -11.49pp | 0.90 |
| Q5 brd > 39.2 (highest) | 258 | 77.5% | **71.7%** | -5.81pp | 0.89 |

- Breadth Q1 has the **largest divergence** (-23pp opt vs gen) and the **lowest option WR15** (59.1%)
- Breadth Q5 has the **smallest divergence** (-6pp) and the **highest option WR15** (71.7%)
- 12.6pp spread in option WR15 across breadth quintiles, while gen-WR is approximately flat or even higher in low breadth
- **The score's calibration target (barrier-touch WR) does NOT track the option outcome in low-breadth tape** — it's actually slightly HIGHER in low breadth (82.2% Q1 vs 77.5% Q5) while option outcome is much LOWER

### F3F threshold split (75+, matches deployed knob)

| breadth band | N | WR15_gen | WR15_opt | Δ | reg_mult |
|---|---:|---:|---:|---:|---:|
| brd ≤ 30 (F3F floor) | 167 | 79.6% | **58.1%** | -21.56pp | 1.03 |
| 30 < brd ≤ 40 (F3F ramp) | 960 | 72.1% | 61.5% | -10.62pp | 0.92 |
| 40 < brd ≤ 50 (stress band) | 179 | 82.7% | **72.6%** | -10.06pp | 0.89 |

The brd ≤ 30 cohort:
- 167 of 5616 call peaks (~3% of cascade flow)
- 21pp option-vs-barrier divergence
- 14.5pp lower option WR15 than brd > 40 cohort
- Critically: **regime_multiplier averages 1.03 in this cohort** — labeled NEUTRAL/HEALTHY despite breadth being in the F3F floor zone. This is the inverted-composite bug F3F was shipped to bypass.

### Regime multiplier is NOT capturing this signal

| reg_mult bucket | N | WR15_gen | WR15_opt | Δ |
|---|---:|---:|---:|---:|
| ≤ 0.78 (stress) | 364 | 74.5% | 62.6% | -11.81pp |
| 0.78–0.88 (caut) | 1,776 | 75.5% | 63.1% | -12.39pp |
| 0.88–1.00 (neutral) | 1,759 | 78.2% | 63.3% | -14.89pp |
| 1.00–1.05 (healthy) | 829 | 77.0% | 62.0% | -14.96pp |
| > 1.05 (bull) | 888 | 81.1% | 63.9% | -17.23pp |

Option WR15 is essentially flat (~63%) across regime multiplier buckets. The regime multiplier does NOT discriminate option outcomes — it's the wrong signal.

### Residual breadth signal AFTER regime multiplier already applied

Filter to reg_mult ∈ [0.88, 1.10] (neutral/healthy), then split by breadth (N=3,510 = 62% of cohort):

| breadth band | N | WR15_gen | WR15_opt | Δ |
|---|---:|---:|---:|---:|
| brd ≤ 30 | 147 | 83.0% | 57.8% | -25.17pp |
| 30 < brd ≤ 40 | 592 | 71.1% | 62.3% | -8.78pp |
| 40 < brd ≤ 50 | 101 | **89.1%** | **79.2%** | -9.90pp |

Within already-neutralized regime, breadth still moves option WR15 by **21.4pp** (57.8% → 79.2%). The regime composite is averaging out the breadth signal because of VIX's structural overweight; breadth carries information that the regime multiplier discards.

## Implications for BSD design

1. **Encodable as a score-stage dampener.** The signal is signal-date breadth, which is known at score time and not portfolio-state-dependent.

2. **Should operate AFTER the regime multiplier**, since the regime multiplier doesn't capture this. Mirror the placement of v37 PCD / v32 CWCF in `compute_overall_score`.

3. **Sub-cohort scope is small (3% of flow at brd ≤ 30) but per-trade impact is large** (~14pp lower option WR15 vs healthy-breadth peer). Slot-displacement gain in the 14-slot cascade pool likely large.

4. **Caveat — directional asymmetry.** Q1 (brd ≤ 32.9) gen-WR is 82.2% (HIGHER than baseline 77.4%). The barrier hits MORE often in low breadth, but the option doesn't fire TP. Theta drag + gap-through SL likely. Score is structurally over-stating expected option outcome in this cohort.

5. **F3F redundancy check needed at MC.** F3F currently halves alloc on brd ≤ 30. If BSD ships, F3F and BSD jointly would over-correct. Plan: ship BSD, set F3F call floor to OFF in the same change.

## Proposed mechanism (preliminary)

```python
# In compute_overall_score, AFTER regime_multiplier and AFTER existing dampeners
# (v37 PCD / v32 CWCF / v38 CWWD / v27 WCF / v25 mis-stress / etc.).

BSD_GATE_BREADTH = 30          # F3F call floor consistency
BSD_GATE_SCORE   = 75          # only fires on cascade-eligible calls
BSD_TARGET       = 70          # drift to 70-74 (overflow=disabled = no trade)
BSD_ALPHA        = 0.95        # mirror v32 CWCF / v27 WCF

if overall >= BSD_GATE_SCORE and breadth_score is not None and breadth_score <= BSD_GATE_BREADTH:
    weakness  = clip((BSD_GATE_BREADTH - breadth_score) / BSD_GATE_BREADTH, 0, 1)
    overall  -= BSD_ALPHA * weakness * (overall - BSD_TARGET)
```

A 78-score call on a brd=20 day → weakness = 0.33 → drifts to 78 - 0.95×0.33×8 = 75.5 (still tradable but at lowest tier).
A 78-score call on a brd=10 day → weakness = 0.67 → drifts to 78 - 0.95×0.67×8 = 73 (out of cascade).
An 88-score call on a brd=10 day → weakness = 0.67 → drifts to 88 - 0.95×0.67×18 = 76.5 (drops 11pts, into 75-79 lowest tier).

## Next steps

1. **Calibration sweep** (`sweep_bsd.py`) on (BSD_GATE_BREADTH ∈ {25, 30, 35, 40}, BSD_TARGET ∈ {65, 70, 72}, BSD_ALPHA ∈ {0.50, 0.75, 0.95}) via fast_variant_runner if available, or direct simulator+diff-assess.
2. **H1-H5 ship gate** at 5y on v39 with affected-tier framework: 75+ cumulative TP%, 80+, 85+, 90+, 95+; puts neutral; multi-window sign consistency.
3. **Smoke MC** if signal density on 75+ shifts >30% (it will — Q1 cohort is 3% of 75+ flow but redistribution into 70-79 may shift other tiers).
4. **F3F retirement.** If BSD passes, set F3F call floor to OFF in same ship.
