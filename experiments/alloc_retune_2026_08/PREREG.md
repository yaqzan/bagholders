# PREREG — allocation re-sweep on the TP10 substrate (2026-08-10)

STATUS: **LOCKED before any outcome viewed** (git commit = the lock). Inherits the
tpsl_refine_2026_08 framework wholesale: metrics, acceptance lanes, noise floors, stop
rule, N-escalation, paired seeds, and execution mechanics are PREREG.md §§3/7 of
`experiments/tpsl_refine_2026_08/` applied verbatim unless overridden below.

## 0. Hypothesis + honesty header

- H1: the Core allocation structure (cascade 0.20/0.15/0.08/0.03, MaxPos 14, gross 0.50)
  and Apex structure (flat 0.10×10, gross 1.0) were tuned in the TP+30/SL−70 era
  (~4-day holds). Under the shipped TP+10 barriers (~3-day cycles, higher capital
  velocity, TP-rate ~85%), the optimum may have moved — plausibly toward more slots /
  different exposure / flatter or steeper tier shape.
- Baseline arms = the SHIPPED config re-run in-phase under identical seeds. TP/SL is
  FROZEN at the shipped values in every cell (one stage at a time).
- Prior evidence honestly stated: MaxPos 15+ breached DD floors and gross>0.5 hurt
  ("capital-velocity law") — both findings from the OLD barrier era and old fill canon;
  retesting them under new barriers is the point, with collapse=0 as the unchanged hard
  floor. The v73 selectivity retune (mid/low trimmed) is partially reversed by some
  candidate shapes — deliberate retest, prior named. Overflow tier stays 0 (old hard
  bound; not reopened — cells never set overflow>0).
- Hypotheses declared: Core ≤240 blast cells (shapes × MaxPos × gross), Apex ≤16
  (n × frac). Refine ≤40. Confirm ≤4/profile. Multiplicity control identical to the
  tpsl campaign.
- Falsification: no cell enters a lane at N=500 → "allocation confirmed optimal under
  new barriers," documented, stop.

## 1. Grids

**Core blast (N=150 × {2022, 2024, 22-now, 2020_crash}, paired seeds):**
- Tier shapes (ultra/top/mid/low), overflow always 0 — 10 shapes:
  S0=(0.20,0.15,0.08,0.03) SHIPPED · S1=(0.25,0.15,0.08,0.03) · S2=(0.20,0.15,0.10,0.05)
  · S3=(0.15,0.12,0.08,0.05) · S4=(0.25,0.20,0.10,0.03) · S5=(0.12,0.12,0.12,0.12)
  · S6=(0.15,0.15,0.15,0.15) · S7=(0.20,0.20,0.10,0.00) · S8=(0.30,0.15,0.05,0.00)
  · S9=(0.20,0.10,0.05,0.03)
- MaxPos ∈ {10, 12, 14, 16, 18, 20} (MAX_POSITIONS_CALL = MaxPos, puts stay off)
- Gross ∈ {0.40, 0.50, 0.65, 0.80} (CALL_PREMIUM_CAP = gross)
- 10×6×4 = 240 cells + in-phase baseline. Prune per tpsl §2 mechanics (collapse>0 drop;
  Pareto on (22-now DD, 22-now med) ∪ top-12 by 22-now DD ∪ baseline+neighbors; cap 15).
**Apex blast (same windows/N):** n ∈ {6,8,10,12,14} × flat frac ∈ {0.08,0.10,0.125,0.15},
gross/call caps 1.0, keep cells with n×frac ∈ [0.60, 1.05] (~16 cells) + baseline.
Rank on (worst-of-4 DD, 22-now med) Pareto.

**Refine (N=300 × 9 win):** carried cells ± one grid step per axis, ≤40/profile total.
**Confirm (N=500 × 12 win):** ≤4 finalists/profile + baseline, plus the standard
batteries: fill-probe TP_FILL_MISS_P=0.10, calibrated-reality arm (0.15+GAP_AWARE,
22-now+5y — differential must not invert; adopted as standard from the tpsl campaign),
survivor-only contrast (2022+22-now), cascade parity, and the 113-window 2x-race harness
for the Apex finalist. Lanes/floors verbatim from tpsl §3 (Core LANE-DD / LANE-GROWTH vs
shipped baseline; Apex paired-harness strictly-better rule).

## 2. Execution notes (delta vs tpsl §7)

- **Sizing cells SHARE one PREPARE per window** (barriers don't change): use
  `_prepare_window` once + `_apply_cell_params` (TIER_ALLOC / MAX_POSITIONS /
  MAX_POSITIONS_CALL are natively supported) + `_simulate_window(ctx, cell_params=...)`
  with a reused pool where safe — the concentration_2x pattern. Gross/call premium caps
  are NOT in `_apply_cell_params`: verify where they're read (import-time env vs
  sim-time global); if import-bound, shard queue jobs by gross value (env fixed per
  job); if sim-live module globals with MC_NO_MP or worker-propagated, set directly.
  Builder verifies with a two-value smoke that MUST show different deployment.
- TP/SL frozen at shipped values — cells never touch set_tpsl beyond the shipped pair.
- Reuse `experiments/tpsl_refine_2026_08/driver/` (mc_patch, phaseD_run patterns,
  phaseD_apex2x, analyze_* skeletons). New driver files live HERE
  (experiments/alloc_retune_2026_08/driver/); never edit the tpsl drivers.
- Frozen pins identical (version pin id=74, LIQUIDITY_FLOOR=0.0, MC_NO_DB_PERSIST=1,
  MC_WORKERS=6, PYTHONIOENCODING=utf-8). All compute via `trader queue submit`
  --priority high --db light --restartable, dedup alloc_retune_*.
- Ship path if a lane clears: /ship-portfolio — Core changes land in
  portfolio_profiles.json params (tier_*/max_positions/gross_cap/call_cap) + TIER_ALLOC
  defaults in strategy_config if the shared default moves; no version bump.
- Sentinel untouched. Owner's standing green light covers this axis ("proceed");
  autonomous ship only on a clean all-green lane pass per tpsl §6 rules — any FLAG
  presents instead.
