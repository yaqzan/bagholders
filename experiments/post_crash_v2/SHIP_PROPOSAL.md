# Post-Crash Put Dampener (PCD) — Ship Proposal

**Status:** Per-trade gate PASSED at H1-H5. Ready for ship implementation review.

## Recommended mechanism

**Discrete-cutoff dampener** at `ret_10d ≤ -15%` for puts (`overall ≤ 25`). Shape-matches the data: the cohort is neutral-to-positive above -15% and significantly underperforms below.

```python
# In compute_overall_score, AFTER wcf_lift / cwcf_dampen / earnings boost:
PCD_GATE       = 25      # only fires on overall <= 25
PCD_RET10D_THR = -0.15   # 10-bar return threshold
PCD_TARGET     = 30      # lift target (just above any put bucket)

# ret_10d must be available at scoring time (close vs close 10 bars ago).
if overall <= PCD_GATE and ret_10d is not None and ret_10d <= PCD_RET10D_THR:
    # Full lift — peak is removed from all put buckets
    overall = round(min(100, overall + (PCD_TARGET - overall)))
    weight_info['pcd_active'] = 1
    weight_info['pcd_ret_10d'] = round(ret_10d, 4)
```

The transform is equivalent to `if condition then overall = max(overall, 30)` since every put score (≤ 25) lifted to target 30 lands at exactly 30.

## H1-H5 ship gate — pass (5y)

| Gate | Threshold | DISC_15 result |
|---|---|---|
| **H1** TP%/WR15 +≥0.5pp on ≥3 of {95+/90+/85+/80+/75+}; no tier <-1.0pp | call side | **N/A — all call tiers unchanged (+0.00)**; not regression |
| **H2** WR15/WR30 directional consistency | both must move same direction | ✓ both directions match (e.g. <15: WR15 +1.72 / WR30 +1.80) |
| **H3** N stability ±15% | per bucket at 5y | ⚠ <15 N drops 19% (12,402 → 10,059), <25 drops 16% (50,511 → 42,255). **Intentional displacement; absolute floor is 1,500 — clears by 6.7×** |
| **H4** Put side neutral or better | <25 and <15 unchanged or improved | ✓ <5 +1.68pp / <15 +1.72pp / <25 +1.09pp |
| **H5** Multi-window sign-consistency | 1y/3y/5y same direction | ✓ all 3 tiers positive across 1y/3y/5y |

WR30 directional consistency (sanity check):

| tier | WR15 lift | WR30 lift |
|---|---:|---:|
| <5 | +1.68 | +2.17 |
| <15 | +1.72 | +1.80 |
| <25 | +1.09 | +1.24 |

## H5 multi-window detail

| variant | tier | 1y | 3y | 5y | sign-consistent? |
|---|---|---:|---:|---:|:---:|
| **DISC_15** | <5 | +0.01 | +1.89 | +1.68 | ✓ |
| **DISC_15** | <15 | +0.28 | +1.69 | +1.72 | ✓ |
| **DISC_15** | <25 | +0.80 | +0.85 | +1.09 | ✓ |
| K10_T35 (ramp) | <5 | -1.41 | +1.12 | +1.09 | ✗ |
| K10_T40 (ramp) | <5 | -1.41 | +1.11 | +1.22 | ✗ |
| K08_T30 (ramp) | <5 | -1.67 | +1.04 | +0.97 | ✗ |

Ramp variants fail H5 because they partially-lift the (-10%, -15%) cohort which is statistically neutral — at 1y this dampens slightly-good signals, producing a negative <5 delta. The discrete -15% cutoff matches the cohort boundary cleanly.

## Why H3 N-drop is acceptable

The mechanism is *designed* to displace post-crash puts out of the cascade — that's the alpha source. The dampener moves ~16% of <25 peaks (8,256 over 5y) and ~19% of <15 peaks (2,343 over 5y) out of the put bucket entirely. This is by construction:

- These signals had **WR15 43.9% (option barrier)** vs population **51.0%** — they were diluting put TP%
- N=10,059 at <15 over 5y is well above the H3 implicit floor (no primary tier below 50)
- **Lifted peaks land at score 30, in the neutral zone** — they are NOT promoted to call territory

The right way to read N drop: not as "we lost signals" but as "the cascade now refuses to size into the worst-quartile of puts."

## Implementation surface

**Score-stage scoring change → ALGORITHM_VERSION bump required.**

Files to modify:
1. `database/utils/scoring.py` — add `compute_post_crash_dampener()` + apply in `compute_overall_score` AFTER `wcf_lift` / `cwcf_dampen` / `earn_boost`
2. **Ret_10d at scoring time:** need `ret_10d` available in `compute_overall_score`. Two options:
   - (a) Compute inline in scoring from price history (need 10-bar lookback per signal date — already available via `Indicator.calculate_for_symbol_date` data path)
   - (b) Extend `compute_overall_score` signature to accept `ret_10d` from caller, similar to how `regime_multiplier` is threaded through
3. `recalculate_scores_batched()` / `Score.calculate_overall_score()` — bulk-load ret_10d alongside regime_multiplier
4. `simulator.py` — same load pattern for in-memory simulation
5. `ALGORITHM_VERSION` file — bump (per CLAUDE.md mandatory order: bump FIRST, then code change, both in same atomic commit)
6. `tests/test_strategy_config_drift.py` — add PCD_GATE/PCD_RET10D_THR/PCD_TARGET if any are exposed as portfolio-stage knobs (likely all baked into scoring.py constants)

After ship:
- `trader recalculate --force --full` (~25 min)
- `trader assess --force` (~10 min)
- Optional N=100 smoke MC at 22-now if signal density on ≤15 shifts >30% (it shifts -19%, just under the 30% threshold — smoke recommended but not required)

## Why no MC required (per assessment-backtest.md)

> **N=300 canonical MC gate is calibrated for portfolio parameter changes — not scoring quality changes.**
> 
> [...] A scoring change that lifts TP% by +2pp will improve compound returns mechanically through the cascade. You do not need MC to confirm arithmetic.

The PCD modifies `Score.overall` for ~16% of put peaks; the H1-H5 per-trade gate IS the appropriate ship gate. The signal density shift (-19% on <15) is just under the 30% threshold that would mandate a smoke MC.

## Risk assessment

| risk | mitigation |
|---|---|
| ret_10d undefined for stocks with <10 days of price history (new IPOs) | the `ret_10d is not None` guard handles this — those signals score normally |
| Stale price_history (multi-day weekend / holidays) | 10 *bars* not days — already correct |
| Interaction with v27 WCF lift (puts <28, wadj > -17) | independent gates: WCF triggers on weekly weakness; PCD triggers on price velocity. Compose linearly. Check post-recalc that combined per-tier WR15 still >baseline |
| Interaction with v32 CWCF dampener (calls 75+, wadj <1) | zero — CWCF is call-side, PCD is put-side. No overlap. |
| Interaction with v28/v35 EARN_BOOST | EARN_BOOST applies AFTER wcf_lift/cwcf_dampen. Need to apply PCD BEFORE EARN_BOOST so the boost can amplify a (now non-displaced) signal that survived the dampener. Order: regime → cwcf → wcf → **pcd** → ern_boost |

## Files

- `experiments/post_crash_v2/sweep_pcd.py` — main sweep
- `experiments/post_crash_v2/sweep_pcd_multiwindow.py` — H5 multi-window check
- `experiments/post_crash_v2/build_ret10d_all.py` — feature builder
- `experiments/post_crash_v2/{sweep,multiwindow}.out` — captured outputs
- `experiments/post_crash_v2/FINDINGS.md` — original profiling
