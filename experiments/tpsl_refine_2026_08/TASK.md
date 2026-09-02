# TP/SL Blast-Radius Refinement — Core + Apex (2026-08)

Cold-boot doc. A fresh agent must be able to resume from this file + PREREG.md + LESSONS.md
alone. Bulk results stay in out/ and logs/ — never read raw logs into context.

## Goal

Jointly re-sweep option-level TP% / SL% for the **Core** (long-run compounder, DD-primary)
and **Apex** (fast-2x sprint, P(2x)/days-to-2x objective) profiles on the CURRENT honest
stack — v74 scores, calendar-hold MC (seeded bounded fill), repaired 1997+ substrate,
~3% spread — then test pre-registered regime/market-wave-conditional TP/SL variants.
Ship via /ship-portfolio only if a candidate clears Stage-3 T1-T7 at N=500.

**Why now:** TP+30 was locked 2026-04 on the pre-honest substrate + old fill model;
SL−70 came from the 2026-05/06 HOLD-era put-DD work. The JOINT surface has never been
swept on the current engine. Since then: honest universe rebuild (2026-07-29), real
Polygon fill/premium validation, FF-4 SL gap-overshoot measurements, calendar-hold ship.

**Failure condition (pre-registered):** if no candidate beats the live (TP30/SL−70) cell
beyond the N=500 noise floor per PREREG.md §Metrics, we STOP and bank "re-certified
optimal on new substrate" — that is a valid, complete outcome, not a reason to keep mining.

## Current live baseline (verified strategy_config.py 2026-08-10)

- 30-DTE: `TP_BASE=0.30`, `TP_STRESS=0.30`, `SL_BASE=-0.70`, `SL_STRESS=-0.70`,
  `BREADTH_THRESHOLD=40` (inert — base==stress), hard-sell day 15, calls-only, puts OFF.
- Core profile: cascade 0.20/0.15/0.08/0.03, gross 0.5, MaxPos 14, uncapped.
- Apex profile: flat 0.10 tiers, MaxPos 10, gross 1.0, `NOMINAL_CAL_DTE=30`,
  `HOLD_CAL_DAYS=27`, sl_base/sl_stress −0.70 (profile-pinned).
- Stress machinery (TP_STRESS/SL_STRESS × BREADTH_THRESHOLD) exists in-engine, currently
  flat → Phase C reuses it, no new plumbing for the breadth arm.

## Design (detail + locked grid in PREREG.md)

- **Phase A — blast:** coarse TP×SL grid (~80 cells/profile, stress=base), windows
  2022 / 2024 / 22-now / 2020_crash, N=150, paired seeds. Locate ridge/plateau.
- **Phase B — refine:** top plateau at finer granularity, N=300, 9 windows (8 T3 +
  2020_crash).
- **Phase C — regime/market-wave conditional:** pre-registered signal set ONLY (breadth
  stress band, MWDD band, RXDD VIX band, regime_mult band; TVDD/BDIV if plumbing is free).
  CLOSED AXIS GUARD: no signal-count/density classifiers (2026-05-09 dynamic-TPSL null).
- **Phase D — confirm:** finalists + baseline, N=500, 12 windows, T1-T7 read,
  survivorship arm spot-check (MC_UNIVERSE_FILE), backtest_cascade parity, Apex 2x-race
  harness for the Apex finalist.

## Hard rules (from repo canon — do not violate)

- ALL sweep compute via `trader queue submit` (high; `--window off_market` during market
  hours). NEVER harness run_in_background for MC. `MC_NO_DB_PERSIST=1` on sweep cells.
- Paired seeds = identical window LABEL strings across arms. Never rename labels per arm.
- N floors: 150 blast-rank only, 300 screen, 500 ship claim. DD-primary; compound is
  T7 sanity. collapse=0 on EVERY window incl 2020_crash is non-negotiable for Core.
- 30-DTE engine (`monte_carlo.py`) only; `monte_carlo_15dte.py` is not calendar-honest.
- No scoring code touched, no ALGORITHM_VERSION bump, no worktree needed.

## Status (overwrite at every stopping point)

**Pre-outcome decision rule (written before any Phase C result was seen):** a Phase C
conditional cell advances to Phase D ONLY if it beats the in-phase flat base pair beyond
the §3 provisional floors (dd_5y ≥1.5pp better at med_5y ≥0.75× flat, collapse 0,
confirmed on the 9-window follow-up). If one advances, D confirms it via the phaseC
injection machinery at N=500×12 (small builder task; flat D runner would misrepresent
it). If none advances, Phase C closes as "conditioning adds nothing at the new base
pair" and the flat finalists proceed alone. Apex cascade-parity runs under Core sizing
as a labeled direction-proxy (accepted; Apex's gate is the 2x-race harness).

**Conditional-pick rule (locked BEFORE any N=500 conditional number was seen):** the
conditional displaces the flat ship-candidate for its profile ONLY if, at N=500×12 with
probes green (fill-probe non-inverting, survivor-robust), its 5y WorstDD is ≥1.5pp better
than the BEST flat SHIP-verdict cell of that profile, AND no annual window regresses >2pp
vs that flat cell, AND med_5y ≥ 0.75× that flat cell. Ties/sub-floor margins → flat ships
on parsimony. Core note: the Core conditional inherits base (0.10,-0.60) whose FLAT form
is survivor-FLAGGED — its own survivor arm must independently pass or it is FLAGGED
regardless of DD.

**2026-08-10 ~16:00 — SHIPPED + CLOSED.** Owner lifted the May veto conditionally; the
gotcha screen (calibrated-reality battery #389-392) hit the INCUMBENT hardest while TP10
won under every fill model → shipped per the locked rule. strategy_config TP 0.10 /
SL −1.00; Apex profile v5 tp pinned 0.10 / sl −0.60. Drift green, guard-8 verified,
alloc display correct, parity = 2 pre-existing sub-cent deltas, N-floor soft gate =
pre-existing task-611 state. Research pack #393 + temporal-refresh #394 queued. API
restarted + healthy. Docs: known-issues (ship block + PROGRAM ESCALATION), trading-
strategy, version-history, CLAUDE.md, traps.md (3 promoted). FINDINGS.md complete.
Memory written. Experiment CLOSED — remaining open item is PROGRAM-LEVEL (MC-realism
default flip Stage-3 A/B + P2.B reconciliation), tracked in known-issues, NOT here.

**(superseded) 2026-08-10 ~15:00 — USER RULED: May sub-20% veto lifted CONDITIONALLY ("as long as
the strategy feasibly works and there's no underlying gotcha I'm good to use any tp").
Fill-fidelity measurement (experiments/tp_fill_fidelity_30dte/FINDINGS.md, N=2,257/2,688)
verifies feasibility: NO fill cliff at tight TP (economic never-fill 14.1% @TP15 vs
15.8% @TP30; late fills heal same-day misses; tier-monotone). NEW GOTCHA-CANDIDATE it
surfaced: engine TP overshoot credit ~1.5x generous (+10.6pp vs real +7.0pp) — NOT
differential-neutral (tight TP collects more fills). VERIFICATION IN FLIGHT (tasks
#389-392): finalists + TP>=0.20 alternates + baseline under calibrated reality
(TP_FILL_MISS_P=0.15 + TP_FILL_GAP_AWARE=1, 22-now+5y, N=500) + TP10 extrapolation
buffer at MISS_P=0.20. DECISION RULE (locked now, pre-outcome): TP10 winners ship iff
they beat baseline AND the TP>=0.20 alternates under calibrated reality on the DD-primary
read (5y then 22-now, med sanity); if TP10 dies under calibration while TP20 survives,
TP20 ships (that IS the gotcha); if all die, no ship + document. Alternates battery
(#380-387) also still completing. Then: coordinated ship per ship-portfolio skill
(strategy defaults + Apex pins, atomic) + closeout.

**(superseded) 2026-08-10 ~14:00 — SHIP HELD (human-ruling conflict); alternates battery running.**
Apex 2x verdict = SHIP (99.4% P2x vs 61.3% paired baseline, all green). BUT at edit time
found strategy_config's 2026-05-11 comment: USER ruled sub-20% TP "too close to option
mark/intraday noise" (put revert, instrument-generic reasoning). Both winners are TP=0.10
→ per house rule + PREREG §6 escape clause: PRESENT, don't autonomous-ship. No production
file was edited. TP≥0.20 alternates at N=500 (tasks #380-387: core 3 cells, apex 2 cells,
12-window + probe + survivor + 2x-race ×2) so the user decides from a complete menu:
(a) accept TP10 winners as-is, (b) ship best TP≥0.20, (c) hold for the Polygon 30-DTE
fill-fidelity measurement (task chip). On battery terminal → analyze (phaseD summary
covers alt cells via same CSVs? alt jobs share phaseD CSV naming — rerun analyze_phaseD +
hand-extract alt rows) → complete FINDINGS ALT20 table → final presentation to user →
closeout (commit drivers+FINDINGS+docs, memory, .horizon). NO strategy_config/profiles
edit until user rules.

**(superseded) 2026-08-10 ~13:00 — conditionals REJECTED at N=500 (locked rule; axis closed);
FINDINGS.md drafted (Apex 2x placeholder); awaiting apex2x finalist arm (#379).**
SHIP COORDINATION (critical): live ledger profile = APEX ("v70 Apex Live") and Apex
INHERITS TP_BASE from STRATEGY_30DTE (its profile pins only SL) — so the ship is ONE
atomic change after the Apex verdict: strategy defaults → Core winner (TP 0.10 /
SL −1.00); Apex params pinned explicitly per its own verdict (clears harness → its
winner config; fails → pin tp_base=0.30 to stay byte-identical). Apex harness read is
PAIRED vs in-harness baseline (p2x_ever 61.3%, before50dd 54.2%, med 136d, DD 69.7%,
collapse 0 — cert-frame numbers differ by construction; document both). Drift guard
green pre-ship (655 constants). Next: on #379 → rerun analyze_phaseD → Apex verdict →
complete FINDINGS → /ship-portfolio (coordinated) → closeout (commit drivers+FINDINGS,
docs rows, memory, .horizon).

**(superseded) 2026-08-10 ~12:00 — Core flat verdict IN (N=500×12): (0.10,-1.00) SHIP (5y DD −12.4pp,
all-12-window DD sweep, probes green), (0.10,-0.90) SHIP, (0.10,-0.60) FLAG (survivor-only
edge evaporates — SL-fires variant leans on delisted names; dead-hold variants robust).
Apex verdict pending apex2x chain. Conditional N=500 confirms submitting now.**

**(superseded) 2026-08-10 ~11:00 — Phase C READ; Phase D LAUNCHED (17 jobs).** Phase C verdict
(out/phaseC_summary.md): MWDD/RXDD/regime-mult conditioning = flat-to-harmful (0 screen
passes across 66 cells); breadth@40 stress-loosen-TP passes screen in BOTH profiles by
thin margins (Core stress (0.20,-0.50): dd_5y 36.9 vs flat 38.7; Apex stress (0.25,-0.75):
53.2 vs 54.9) — same mechanism family as the 2026-04 finding. Follow-up (missing 4
windows: 2021,2023,2025,dip) = tasks #351/#352; advance rule per the pre-outcome note
above. Phase D flat launched per driver/SUBMIT_PLAN.md with CORE_CELLS=
"0.10,-0.60;0.10,-1.00;0.10,-0.90", APEX_CELLS="0.10,-0.60": #353-360 (flat N=500×12),
#361-362 (fill-probe), #363-364 (survivor-only), #365-366 (cascade parity), #367
(apex2x baseline) + auto-chained finalist arm (background chain parses its ID).
On all-terminal: verify C follow-up numbers by hand (2 cells × 4 windows vs Phase B flat
rows), run analyze_phaseD.py, read out/phaseD_summary.md, decide conditional-arm D
confirm per the pre-outcome rule, then FINDINGS + ship decision per PREREG §6.

**(superseded) 2026-08-10 ~10:00 — Phase C SUBMITTED (tasks #343-346, base pair (0.10,-0.60) both
profiles, ~56 cells+baseline × 5 windows × N=300); D pre-build in flight.** C injection
validated bit-exact (task #340, all 5 sources); fired-fractions healthy; band cutoff
|z|<=1 approved; DTE-router 0.3-0.5% slice caveat recorded (consistent across all
phases + matches a real ship). Watch: #341,#343-346 (background). On C terminal → run
analyze_phaseC.py → pick per-profile conditional winner (or flat if no SCREEN-PASS
beats flat beyond §3 floors) → finalize D cell list → submit per driver/SUBMIT_PLAN.md
(D-builder producing it). Cold resume: out/phaseC_*.csv, queue list, LESSONS.md.

**(superseded) 2026-08-10 ~09:30 — Phase B DONE + READ; C validation pending; D pre-build dispatched.**
All 8 jobs (#331-338) exit 0, 540 rows. Read surface: out/phaseB_summary.md. Core
finalists (dd_5y): (0.10,-0.60) 38.7 / (0.10,-1.00) 40.4 / (0.10,-0.90) 41.6 vs baseline
52.8; Apex finalist (0.10,-0.60) worst-of-9 63.9 vs 70.1. PHASE C BASE PAIR = (0.10,-0.60)
for BOTH profiles (winner by pre-registered ranking keys). Optimum at pre-registered TP
floor — boundary status + fill-executability caveat recorded in LESSONS. Next: (1) when
Phase C builder reports validation PASS → submit Phase C on base (0.10,-0.60) both
profiles; (2) Phase D builder pre-building N=500×12 + fill probe + survivor-only contrast
+ cascade parity + Apex 2x-race; (3) after C summary → finalize D cell list → run D →
T1-T7 verdict. Cold resume: out/phaseB_summary.md, LESSONS.md, queue list.

**(superseded) 2026-08-10 (builder subagent, fulfilling the ~07:15 dispatch below) — Phase B BUILT + DISPATCHED.** Runner
`driver/phaseB_run.py` (thin variant of phaseA_run.py, same architecture) +
grid derivation `driver/build_phaseB_cells.py` (PREREG section 4 fill rule,
both profiles land at exactly 30/30 cells after the cap) + analyzer
`driver/analyze_phaseB.py` (PREREG section 3 lanes, core DD/GROWTH +
apex Phase-B Pareto proxy) + `driver/test_analyze_phaseB.py` (6 tests, all
green; caught and fixed a real INCUMBENT key-shape bug via the end-to-end
test before any real data touched it). 8 queue jobs submitted (windows-
sharded, all 30 cells/job, N=300, stress=base): core/apex x
{annuals(2021-2025), 22now, 5y, dipcrash(dip+2020_crash)} = task IDs
331-338, priority=high db=light cpu=6 timeout=2h, PYTHONIOENCODING=utf-8 set
at the queue-submit level (not just in-process -- that's too late for the
parent process's own stdout). Job #331 (core_annuals) confirmed running
correctly within seconds of submit (correct config, N=300, first cell
matches the locked grid, sane worst_dd/p_coll). DB budget (2 light slots)
serializes the 8 jobs into ~4 waves of 2 -- every INDIVIDUAL job is still
projected well under the 2h target (worst case ~29min), just the total
campaign wall is longer than "all 8 parallel" would be. `git diff --quiet`
confirmed clean on monte_carlo.py/strategy_config.py; no commits made.
Orchestrator owns the watch (`trader queue wait 331..338`) and runs
`analyze_phaseB.py` for real once all 8 land -- read out/phaseB_summary.md
only, never the raw CSVs/logs.

**(superseded) 2026-08-10 ~07:15 — audit PASSED (relative ranking honest), Phase B DISPATCHED.**
Audit verdict in LESSONS.md: no friction crossover to f=3%; turnover only 1.22x;
close-confirm arm collapses candidate AND baseline identically (engine-wide fill
dependence, differential-neutral; absolute magnitudes carry known ~3.2pp real-ledger
optimism). PREREG §6 tightened (TP_FILL_MISS_P=0.10 probe on finalists+baseline).
Phase B builder (Sonnet) implementing/submitting: carried cells + §4 ±0.05 fills,
N=300 × 9 windows (2021..2025, dip, 22-now, 5y, 2020_crash), lanes per §3 vs in-phase
baseline. Orchestrator owns the watch; read out/phaseB_summary.md only.

**(superseded) 2026-08-10 ~06:00 — Phase A DONE (tasks #324-#327, 640 rows, ~23min
wall); Phase B was HELD pending artifact audit.** Prune ran; out/phaseA_summary.md is the read surface.
Result shape: tight-TP corner (TP0.15 × wide SL) dominates both profiles (Core 22-now
DD 41.9% / med +10,418% at (0.15,-0.90) vs incumbent-class ~52%/+600); high-TP cells
mass-collapse (Apex ≥0.50, Core ≥0.85) and are pruned. SUSPICION (LESSONS.md): current
cost model charges SLIP_ENTRY=0.0 + free limit-TP → per-cycle friction ~0, which
structurally flatters the doubled turnover of tight TP; 2026-04-era sweeps found TP20
catastrophic under entry=-1%. AUDIT subagent in flight: (1) cash-math trace per exit
kind + where the claimed "~3% spread" lives + TP_FILL_MISS_P mechanics + trail-active
check; (2) trade-tape turnover + friction-sensitivity crossover f* for (0.15,-0.90) vs
(0.30,-0.70), Core/22-now/N=100, anchored to FF-2 measured real spreads; (3)
close-confirmed-TP arm to bound wick-fill optimism.
Decision rule on audit return: edge survives realistic friction AND close-confirm →
Phase B proceeds on carried cells (±0.05 fill per §4, incumbent always re-run per §1).
Edge dies → restrict Phase B to TP ≥ 0.25 (artifact exclusion, documented), and treat
any tight-TP claim as model-fragile in FINDINGS. Cold resume: audit outputs land in
out/ (audit_*); carried lists in out/phaseA_summary.md; queue `trader queue list`.
