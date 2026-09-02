# Allocation re-sweep on the TP10 substrate — cold-boot doc

Goal: re-optimize Core (cascade shape × MaxPos × gross) and Apex (n × flat-frac) under
the 2026-08-10 shipped barriers (TP+10; Core SL−100, Apex SL−60), which changed cycle
speed ~4d→~3d and may have moved the sizing optimum. TP/SL frozen; one stage at a time.
Design + lanes: PREREG.md here (inherits tpsl_refine framework). Rig reuse:
experiments/tpsl_refine_2026_08/driver/ (read its LESSONS.md first — sigma trap
irrelevant here, but pool/cache/queue traps apply; sizing cells share PREPARE).

## Status (overwrite at every stopping point)

**Confirm-phase decision rules (locked BEFORE any N=500 confirm number was seen):**
CORE candidate ships autonomously ONLY if at N=500×12 vs in-phase baseline: dd_5y ≤
base−1.5pp AND med_5y ≥ 0.75× AND dd_22now ≤ base+0.5pp AND no {2021..2025} window
regresses >5pp AND collapse 0/12 AND crash-family (2018/2020/2020_crash/10y) regression
≤3pp each (worse = FLAG, present don't ship) AND fill-probe 0.10 + calibrated
0.15+GAP_AWARE differentials non-inverting AND survivor-robust. Between the two Core
finalists: higher-gross variant wins only if its crash-family reads are within the 3pp
tolerance; else the g0.50 variant.
APEX candidate ships ONLY if its 2x-race (vs the tpsl #379 baseline arm — IDENTICAL
config to this campaign's baseline, reuse its on-disk per-window JSONs, zero recompute):
P(2x ever) ≥ 99.0% AND worst DD strictly better than 65.4% AND med days ≤ 200 AND
collapse 0. Anything less = NO-SHIP (today's TP/SL cert config stands; same-day re-touch
bar is deliberately high). Sentinel untouched.

**2026-08-10 ~22:00 — CONFIRM DONE; APEX SHIPPED (profile v6, n12×6%); CORE FLAGGED
no-ship; campaign CLOSED.** Verdicts + evidence: FINDINGS.md + out/allocC_summary.md.
Propagation #441/#442 queued; API restarted; docs + memory updated. Open follow-up
(known-issues): understand WHY Core-sized steep-shape edges concentrate in the delisted
cohort before any re-mine.

**(superseded) 2026-08-10 ~20:15 — Phase B DONE + READ (tasks #417-421, 468 rows).** Core lane
entrants: S9/mp20/g0.50 (dd_5y 30.2 vs 40.4) + S9/mp20/g0.65 (31.3 at 0.99× med; crash
+5.9pp flag to watch). Apex Pareto finalists: n10f0.06 / n12f0.06 / n10f0.08 (big DD
cuts, big flat-med giveback — 2x harness adjudicates). Baseline bit-exact vs tpsl
shipped-cell numbers (paired-seed cross-campaign integrity check PASSED). Confirm
builder dispatched.

**(superseded) 2026-08-10 ~19:15 — Phase A DONE + READ; Phase B builder dispatched.** All 5 jobs exit
0 (996 rows, ~6min). Read (out/allocA_summary.md): gross 0.80 mass-collapses (40 cells —
velocity law holds); shipped Core shape NOT in top-20 — steeper S9(0.20/0.10/0.05/0.03)
@g0.40 and S8(0.30/0.15/0.05/0.00)@g0.50 cut 22-now DD ~39→29-30%; S2@mp20/g0.50 =
growth star (+68.5k% med at flat DD; slots pay under TP10 recycling). Apex: n12×f0.08
beats live n10×f0.10 by −6.5pp DD. Both profiles walking to grid edges (apex frac floor,
core gross floor) — Phase B one-step rule extends per tpsl precedent (0.30 gross / 0.06
frac enter). Builder building allocB (N=300×9, carried+neighbors, lanes vs in-phase
baselines). Orchestrator owns the watch. Next: allocB summary → finalists → Phase C
confirm (N=500×12 + batteries incl calibrated arm + apex 2x) → ship decision.

**(superseded) 2026-08-10 ~18:30 — Phase A RUNNING (tasks #406-410).** Builder done: driver/ built
(allocA_run.py sharded by gross — see LESSONS for the worker-liveness reason;
analyze_allocA.py unit-tested), smoke #401-405 all PASS (G1 gross-cap saturates exactly
at nominal; Apex env TP/SL pin fix — LESSONS; baselines match tpsl N=500 tightly;
resume clean; zero puts). Core 60 cells × 4 gross jobs × 4 windows (960 pairs) + Apex
9 cells (locked filter yields 9, not ~16 — LESSONS). Orchestrator watches #406-410;
on terminal → run analyze_allocA.py → read out/allocA_summary.md ONLY → inherited §2
prune → Phase B refine brief (N=300×9, ±1 grid step, ≤40/profile). Cold resume:
`trader queue list`, out/allocA_*.csv, driver/state/.
