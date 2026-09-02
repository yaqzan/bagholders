# PREREG — Exposure-matched control for the A2 liquidity floor

STATUS: LOCKED 2026-08-11 before any control outcome exists (git commit = lock).
Companion to `experiments/floor_mc_2026_08/` (A2 SHIP-ELIGIBLE, ship deferred on this).

## Question

How much of A2's WorstDD improvement (+4.40pp mean, 3/4 windows) is SELECTION (verified
liquidity → better fills → shed unreliability) vs merely TRADING LESS (supply −45-53%)?

## Arm (LOCKED)

**C1 random-cut control:** per window, uniform-random subsets of the 75+ primary-tier
candidate population, sized EXACTLY to A2's realized per-window supply (2023: 297,
2024: 766, 2025: 676, dip: 359). **MISS_P stays 0.15** (a random cut earns no fill
improvement) + GAP_AWARE=1. Five fixed seeded subsets per window (subset seeds 1..5),
N=500 paired sim seeds each → report per-window MEAN and RANGE across subsets.
Overflow tier untouched (AMENDMENT-1 semantics). Baseline A0 and floored A2 rows are
NOT re-run — comparison uses `floorMC_main.csv` (same engine, same paired window labels).

## Decision rule (LOCKED)

Selection-attributable DD edge per window = [A0 − A2] − [A0 − mean(C1)] = mean(C1) − A2
(WorstDD, positive = floor better than random cut). The floor ships as a SELECTION
mechanism only if: attributable mean over the 4 windows ≥ **2.0pp** (the same LANE-DD
materiality bar) AND attributable > 0 in ≥ 3/4 windows. Secondary read (recorded, not
gating): 2024 harvest give-back — if C1's 2024 median compound collapse matches A2's
(±10pp), the give-back is a size effect, not a floor defect.

If the rule fails → floor does NOT ship as-is; disposition becomes "exposure is the real
lever": the honest knob is explicit sizing (gross cap / MaxPos), not a liquidity floor
wearing a selection costume — and the floor's fill-fidelity truth stays expressed via
survivor-measured MISS_P in the realism default flip only.

## Stop rule

C1 as specified (20 cells: 5 subsets × 4 windows), no new arms/thresholds after any
outcome is seen. Closes with FINDINGS either way. Compute ≈ 20 × ~25s + overheads ≈ 15 min.
Queue high / --db light. Runner extends `floor_mc_2026_08/driver/floorMC_run.py` pattern
(new stage in THIS dir's driver; closed campaigns read-only).
