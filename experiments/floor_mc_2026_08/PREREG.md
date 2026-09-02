# PREREG — Liquidity-floor MC Stage-3 A/B (calibrated fills, survivor MISS_P)

STATUS: LOCKED 2026-08-11 before any floored-MC portfolio outcome exists (git commit = lock).
Owner directive: "Go" (2026-08-11). Feeds the LIQUIDITY_FLOOR default-ON ship decision
(knob exists, default-OFF); P2.B live fills remain the final arbiter regardless of outcome.

## 1. Question

Does an entry liquidity floor improve the Core book under calibrated-reality fills once its
two real effects are both priced: (benefit) survivor-measured fill-miss rates and shed
friction exposure; (cost) 30-43% supply reduction → under-filled cascade → possible DD/
compound degradation? Triage (`experiments/liquidity_floor_2026_08/`, committed 432f8ce5)
measured the entry-level tradeoffs; this campaign measures the PORTFOLIO consequence.

## 2. As-seen evidence (declared)

Triage tables (all seen): volume floor cuts no-print 17.1→7.1% at v≥10; survivor economic
never-fill 12.60% at {p≥0.25,v≥5}, 11.31% at {p≥0.50,v≥10}; EV flat across floors;
capacity 53.5%/75.2% clip failures. calibP grid (seen): shipped (0.10,−1.00) confirmed.
NOT seen: any MC portfolio outcome under a floored universe. The lock covers §4-§6.

## 3. Arms and mechanics

- **A0 baseline:** no floor; TP_FILL_MISS_P=0.15, TP_FILL_GAP_AWARE=1.
- **A1 gentle:** floor {entry_premium ≥ $0.25, entry_volume ≥ 5}; MISS_P=0.125, GAP_AWARE=1.
- **A2 working:** floor {entry_premium ≥ $0.50, entry_volume ≥ 10}; MISS_P=0.11, GAP_AWARE=1.
- Core profile as shipped (TP 0.10 / SL −1.00, cascade 0.20/0.15/0.08/0.03, mp14, g0.50).
  N=500, paired seeds via identical window labels, one subprocess per (arm, window).

**Floor variables + PIT justification (LOCKED):** realized same-day `entry_volume` and
`entry_premium` from `B:\polygon_derived\ledger_v2\ledger.parquet`, joined by
(ticker, entry_date). Justified: entries execute near the close, by which point the
contract-day volume is ~observable; this also matches the triage's measured supply/fill
numbers exactly. Caveat carried: last-minutes volume unseen (second-order). The ρ≈0.38
trailing alternative (`opt_vol_30d_atm`) is NOT used — it models an earlier-in-day entry
style we don't run.

**Coverage rules (LOCKED):**
- Signals with no ledger row (unbuildable chain/no ATM contract; ~533 of 4,936 in-archive)
  = FAIL the floor in A1/A2 (can't verify liquidity → don't trade it; matches live
  semantics), remain PRESENT in A0.
- Windows restricted to the archive (floor data exists 2022-08-05+): **22-now, 2023,
  2024, 2025, dip.** 2022/5y/10y/2018/2020_crash are OUT — floor variables don't exist
  pre-archive. Known limitation, stated: crash-window floored behavior is unmeasurable
  with current data; the floor only removes entries (its crash-mode risk is
  under-deployment, not new exposure), and live semantics are forward-only.
- TRIPWIRE (statistic+population+threshold): per covered window, the (ticker,entry_date)
  join-hit rate of MC signal events against ledger_v2 rows, computed over all A0 signal
  events in that window, must be ≥ 90%. Any window below 90% → STOP, report, no arm runs.

**Identity validation (required before arms):** an all-pass floor arm (thresholds 0/0,
ledger-missing signals INCLUDED, MISS_P=0.15) must reproduce A0 **bit-exactly** on
window 22-now. Mismatch = injection point is contaminated → STOP.

## 4. Metrics + decision lanes (LOCKED)

DD-primary house doctrine; per-window WorstDD, median compound, collapse. A floor arm is
SHIP-eligible (LIQUIDITY_FLOOR default-ON, portfolio-stage, no version bump) only via:

- **LANE-DD:** beats A0 WorstDD in ≥4/5 windows AND mean DD improvement ≥ 2.0pp AND
  median compound not worse than A0 by >10% relative in more than 1 window AND collapse 0.
- **LANE-COMP:** WorstDD within ±1.0pp of A0 on every window AND mean median-compound
  improvement ≥ +5.0pp AND collapse 0.
- **Guards (both lanes):** survivor arm (delisted-excluded) on the winning floor arm — DD/
  compound edge must not evaporate (S9 lesson); TP/SL neighborhood stability (§5).

Arm selection if both pass: the better WorstDD arm; tie (≤0.5pp) → A1 (gentler supply cut).
If neither lane passes → **verdict CONFIRMED-BASELINE: floor stays default-OFF**, and the
floor's fill-fidelity benefit is pursued only through P2.B live reconciliation.

## 5. TP/SL neighborhood check (LOCKED)

In the winning floor arm (or A2 if none wins): cells {(0.10,−1.00), (0.10,−0.90),
(0.10,−0.80), (0.075,−0.90)} × windows {22-now, 2024}, N=500, same knobs as that arm.
STABLE = anchor (0.10,−1.00) within 3.0pp median of the best cell on both windows AND
best-cell DD within 1.0pp of anchor. Violation → a full TP/SL-under-floor re-sweep fires
as its own prereg'd campaign; no ship of the floor before it resolves.

## 6. Stop rule

One battery as specified (3 arms × 5 windows + identity + ≤8 neighborhood cells + survivor
follow-up on ≤1 arm). No new arms, thresholds, lanes, or windows after any §3 outcome is
seen. Campaign closes with FINDINGS.md either way.

## AMENDMENT-1 (2026-08-11, pre-outcome, selection-neutral)

The §3 tripwire fired on the original population spec. No arm/portfolio outcome was seen
(prepare-stage counts + n=5 smokes only); lanes, thresholds, N, knobs, and arms are
unchanged. Two spec errors corrected:

1. **Population:** tripwire denominator AND floor-filter scope = **75+ PRIMARY-tier signal
   events only**. Rationale: the 70-74 overflow tier carries `TIER_ALLOC['overflow']=0.0`
   in shipped Core (never receives capital) and is outside ledger_v2's build scope
   (SCORE_MIN=75) — the original spec measured coverage of a non-trading population
   (~65-73% of raw counts). The floor filter MUST leave overflow candidates untouched:
   removing them would shift RNG tiebreaker draws in `_do_calls()` — gratuitous stream
   divergence from candidates inert in every arm. Primary-tier removals DO shift
   downstream draws; that is the treatment itself, not artifact.
2. **Windows:** `22-now` DROPPED — its 2022-01→2022-08 head predates the archive by
   construction (the same defect `2022` was excluded for pre-lock; spec error, confirmed
   by its 79.87% rate under the most charitable reading vs 98-99.7% for the others).
   Primary set = **{2023, 2024, 2025, dip}**. LANE-DD breadth rescales ≥4/5 → **≥3/4**;
   all other lane arithmetic unchanged. Identity-arm window: **2024**. Neighborhood-check
   windows (§5): {2024, dip} replace {22-now, 2024}.
3. **Tripwire numerator clarified:** any-status ledger rows count as joined (a skip-reason
   row IS a determination — the chain was examined and legitimately fails the floor; only
   signals with NO row are unknown). Under the amended population the already-computed
   rates are 98.11 / 99.74 / 99.52 / 99.20 — PASS on all 4 remaining windows.

This amendment is committed before any arm outcome exists.

## 7. Compute

~25 cells × ~20-25s (N=500, single window each) + exclusion-set build ≈ well under 1 hour.
Queue: high, **--db light** (read-pattern precedent; the calibP `--db heavy` was flagged as
the deviation), PYTHONIOENCODING=utf-8. Runner cribs `tpsl_calib_primary_2026_08/driver/`
+ tpsl `mc_patch.py` read-only; new driver lives in THIS dir; never edit closed campaigns.
