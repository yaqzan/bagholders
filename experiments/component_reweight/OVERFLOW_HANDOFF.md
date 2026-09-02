# Handoff → Portfolio agent: enable 70-74 OVERFLOW on Apex (idle-book utilization lever)

**From:** scoring/alpha research (component-reweight + weekly + 15DTE threads)
**Type:** portfolio-stage, Apex profile (NO `ALGORITHM_VERSION` bump, NO recalc)
**Status:** VALIDATED at N=300/400 apex-faithful; needs your N=500 × canonical-window sign-off before enable.
**One-line:** the Apex 75+ book sits **idle most days** (3204 of ~33,917 eligible fills); promoting the
70-74 overflow tier fills that idle space **inside the 50% gross cap** → **+15× 10y, collapse-safe**.

---

## What to change (when you agree)

`strategy_config.STRATEGY_30DTE.TIER_ALLOC['overflow']`: **0.0 → 0.035** (Apex; leave Core/Sentinel at 0).
That's it. `load_signals` already pulls `overall >= 70`; the cascade already fills overflow after 75+ by
conviction-sort. The 2026-04 disable predates honest-v70 / wide-SL / 50%-cap apex, so it was never tested
on this engine.

Reversible: set back to 0.0. Drift-guard + registry already cover `TIER_ALLOC`.

## Why it's not "over-deployment" (the one thing to sanity-check)

Your finding #2 is "exposure peaks at 50%, over-deployment HURTS" — that warns against **raising the cap**.
Overflow does **not** raise it: `GROSS_PREMIUM_CAP=0.50` stays active (verified my MC ran with it on), and
75+ (higher conviction) always fill **first**. Overflow only consumes the **idle** portion of the 50% budget
that 75+ leaves empty on the ~majority of days the book isn't full. It never displaces a 75+ signal.

## Evidence — SHIP-GRADE N=500 × 9 windows (apex canon: TP+30/SL−70 HOLD-15, gross 0.50, uncapped, MaxPos14, puts off, SLIP −0.015)

`run_overflow_shipgrade.py` (N=500). **collapse-safe on EVERY config × EVERY window** (max 0.2% =
baseline; 0.040 even 0.0% on COVID-crash). Return-optimal at **0.035** (10y peak):

| alloc | 10y MedRet | 5y | 22-now | 2022-bear | 2025 | 2020_crash col | maxCol | 10y DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline (0) | +2.38M% | +100,784% | +43,212% | +19.9% | +307.8% | 0.2% | 0.2% | 83.5% |
| 0.030 | +28.6M% | +178,193% | +65,671% | +46.5% | +551.2% | 0.2% | 0.2% | 85.8% |
| **0.035** | **+35.9M% (15×)** | +214,160% | +74,833% | +41.2% | +569.4% | 0.2% | 0.2% | 86.1% |
| 0.040 | +33.6M% | +218,564% | +79,704% | +36.6% | +553.1% | 0.0% | 0.0% | 85.1% |

(Cliff confirmed at 0.045 by the N=300 `run_overflow_edge.py` sweep: 2020_crash collapse 1.3% — the
real 2026-04 disable reason, now *bounded*, not avoided.)

- collapse=0 on every window incl 2020-COVID at ≤0.040; DD inside Apex's ~86% budget.
- Return is MC model-scale leveraged compounding of more +EV trades filling idle slots — NOT new alpha;
  the translatable metrics (collapse=0, DD-in-budget) are what hold.
- Two windows slightly LOWER return (dip −25% low-N tail; 2024 mixed) — DD-in-budget, collapse=0, fine.
- **Recommend alloc 0.035** (10y-return peak, collapse-safe, furthest below the 0.045 cliff). 0.030 =
  conservative fallback; 0.040 = upper bound (best 5y/22-now, marginally lower 10y).

## What did NOT make it (so you don't re-test)

- **Weekly-maturity filter on the overflow** (drop the contrarian mature-weekly 70-74, see FINDINGS Thread 1):
  nearly **doubles** 5y/10y return but **breaches the COVID collapse floor** (0→2-5%) — dropping ~34
  crash-window positions removes diversification/cash-buffer. **Breadth beats per-trade-WR for a
  ruin-floored leveraged book.** Disqualified unless a milder cut is collapse-safe (strength sweep pending).
- **Component / weekly / ICH scoring reweight**: null — honest v70 scoring is already well-calibrated for the
  funded sleeve (within-band signals are supply-for-noise; don't transfer). No scoring change recommended.

## Your sign-off step

Re-run your N=500 frontier harness (`profile_frontier.py` / `n300_confirm.py` style) with
`TIER_ALLOC['overflow']=0.035` across your canonical windows incl 2020_crash, confirm collapse=0 and
DD-in-budget, then flip `strategy_config` + `portfolio_profiles.json` (Apex only). I did not touch
`strategy_config`, the profiles, or any scoring — this is yours to land.

(Experimental MC knob `WEEKLY_OVF_FILTER` was added to `monte_carlo.py` for the filter test — default OFF,
inert, read-only; remove or leave, your call.)
