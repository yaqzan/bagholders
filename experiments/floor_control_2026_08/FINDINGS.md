# FINDINGS — Exposure-matched control for the A2 liquidity floor

**STATUS: COMPLETE 2026-08-11. VERDICT: FAIL — the floor's DD benefit is an EXPOSURE
effect, not a selection effect. A2 does NOT ship.** Prereg `dc7192dd`, locked rule
applied verbatim; queue #468 (20 cells, N=500×5 subsets×4 windows, exit 0).

## The attribution (locked rule: attributable mean ≥ +2.0pp AND positive ≥3/4 windows)

WorstDD; C1 = mean of 5 random subsets sized exactly to A2's supply, MISS_P held at 0.15:

| window | A0 | C1 mean (random cut) | A2 (floor) | A2 − vs-random attributable |
|---|---|---|---|---|
| 2023 | 45.10 | 34.17 | 44.36 | **−10.19pp (floor WORSE than random)** |
| 2024 | 25.23 | 29.19 | 34.33 | **−5.14pp (floor worse)** |
| 2025 | 44.41 | 39.47 | 29.74 | +9.73pp |
| dip | 33.22 | 25.26 | 21.92 | +3.34pp |

Attributable mean = **−0.57pp** (bar: ≥ +2.0). Breadth: positive in **2/4** (bar: ≥3/4).
**Both prongs FAIL.** Brutal summary stat: the random cut improved mean DD vs A0 by
**+4.97pp — MORE than the floor's +4.40pp.** The "smart" floor underperformed blind
size reduction on average.

Secondary read (locked): 2024 harvest give-back — C1 2024 median compound mean ≈ +22.4%
(range −7.9 to +46.7) vs A0 +68.1 / A2 +5.1. The give-back is predominantly a size
effect; A2 sits below even the random mean.

Consistency: C1 `n_call_signals` reconciles exactly with tripwire populations minus
subset removals in all 4 windows (e.g. 2023: 2576−339=2237 ✓). No anomalies.

## What was actually discovered

1. **Under calibrated fills, the Core book's DD improves by trading less — almost
   regardless of WHICH entries you drop.** Verified liquidity adds no attributable DD
   edge beyond the size cut (and was worse than random in 2/4 windows).
2. **Subset variance is enormous at annual-window scale** (2023 median across 5 same-size
   random books: −30.5% to +19.3%; dip DD: 16.9 to 32.1). At ~300-750 signals/window,
   book composition luck dominates — a caution against reading any single-cohort annual
   result as mechanism.
3. The measured fill truth (tier-monotone miss rates) is NOT invalidated — it lives on as
   MEASUREMENT (survivor-measured MISS_P in the realism default flip), just not as an
   entry rule.

## Disposition

- **LIQUIDITY_FLOOR stays default-OFF.** floor_mc's SHIP-ELIGIBLE verdict is SUPERSEDED
  by this attribution; the ship package is cancelled.
- If the calibrated book's DD shape is ever wanted, the honest lever is explicit exposure
  sizing (gross cap / MaxPos) — an existing, controllable knob — evaluated as its own
  Stage-3 question inside the MC-realism default-flip campaign (the one remaining open
  ship item from this program).
- Method banked to traps.md: any supply-cutting mechanism must beat an exposure-matched
  random control before its DD win counts as selection. Guards catch cohort artifacts
  (survivor/delisted); only the control catches size-down wearing a selection costume.
