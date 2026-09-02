# FINDINGS — TP/SL blast-radius joint refinement, Core + Apex (2026-08-10)

Pre-registration: [PREREG.md](PREREG.md) (locked 030be93b before any outcome; amendments
were tightening-only and dated). Trap log: [LESSONS.md](LESSONS.md). All evidence
N=500 × 12 canonical windows, paired seeds, v74 pinned (f9fb7b934), calls-only,
CALENDAR_HOLD on, LIQUIDITY_FLOOR=0.0 pinned, MC_NO_DB_PERSIST on sweep cells.

## Verdict

**SHIPPED 2026-08-10 (both profiles) after owner re-adjudication of the May sub-20%-TP
veto** ("as long as the strategy feasibly works and there's no underlying gotcha I'm
good to use any tp") — feasibility measured by the fill-fidelity study, and the
gotcha screen (calibrated-reality battery, below) hit the INCUMBENT hardest while the
winners' edge survived every fill model tested.

**Core winner (TP +0.10 / SL −1.00) — "scalp-and-dead-hold".** TP tightens 0.30→0.10;
SL −1.00 = a ~3.64σ disaster stop (|SL|×PREMIUM_MULT/DELTA); deep-SL fires mostly
reroute into the dead-hold path, so it is economically ≈ dead-hold, mechanically still
a deep-tail stop. Exits in practice = TP or day-27 calendar hold. Formal Phase D verdict
SHIP with every pre-registered gate and probe green (table below).

**Apex winner (TP +0.10 / SL −0.60), 2x-race harness (113 paired rolled windows,
N=500, 56,500 paths/arm):**

| arm | P(2x ever) | P(2x before 50% DD) | med cal-days to 2x | worst DD | collapse |
|---|---|---|---|---|---|
| baseline 0.30/−0.70 | 61.3% | 54.2% | 136.2 | 69.7% | 0.0% |
| **0.10/−0.60** | **99.4%** | **94.4%** | **127.5** | **65.4%** | **0.0%** |

3/3 harness metrics strictly better; floors passed; fill-probe/survivor/parity green;
formal verdict SHIP. (Frame note: this harness's baseline reads P(2x)=61.3% vs the
remembered P0.3 cert's 72% — different roll construction; all conclusions here are
PAIRED in-harness, never vs remembered numbers, per PREREG §1.)

**Why the ship is HELD — a standing human execution-realism ruling.**
`strategy_config.py`'s own comment block (2026-05-11, the PUT_TP 0.14→0.35 revert)
records: *"the user-directed execution-realism pass treats sub-20% TP candidates as too
close to option mark/intraday noise."* The reasoning is instrument-generic and both
winners are TP=0.10. House rule: human judgments win and surface as conflicts. The
model-side sensitivity (TP_FILL_MISS_P=0.10 probe) passed, but the actual 30-DTE fill
fidelity is unmeasured (standing Polygon task chip). Quantified context that postdates
the ruling: FF-2 measured real round-trip spreads at 1.04–2.84% by tier — a +10% TP is
3.5–10× the measured spread. The re-decision belongs to the owner. A TP≥0.20 alternates
battery (Core (0.20,−0.70)/(0.20,−1.00)/(0.25,−0.90); Apex (0.20,−0.30)/(0.25,−0.50))
was run at full N=500 rigor. Resolution of the menu:

- The owner lifted the veto conditionally (2026-08-10); the fill-fidelity study
  (`experiments/tp_fill_fidelity_30dte/FINDINGS.md`, N=2,257/2,688 declared TP events vs
  real Polygon prints) measured NO tight-TP fill cliff: economic never-fill 14.1% @TP15
  vs 15.8% @TP30; late fills at the same limit price heal most same-day misses; misses
  are liquidity-tier-monotone (thin names don't print at all).
- TP≥0.20 alternates at N=500: Apex 2x-race (0.25,−0.50) P(2x) 81.4% / (0.20,−0.30)
  41.4% — both dominated by the TP10 winner's 99.4%. Core alternates dominated under
  both canon and calibrated fills (calibrated 5y: TP20 cells DD 59.9-61.8 / med −43..−49
  vs TP10's 57.2 / −29).

## Calibrated-reality battery (miss=0.15 + GAP_AWARE, the measured knob values; N=500, 22-now+5y) — and the program-level escalation

| arm | 5y DD | 5y med | 22-now DD | p_coll |
|---|---|---|---|---|
| Core baseline 0.30/−0.70 | 64.9% | **−53.2%** | 65.4% | 0 |
| **Core winner 0.10/−1.00** | **57.2%** | **−28.7%** | 57.2% | 0 |
| Apex baseline 0.30/−0.70 | 84.6% | −80.5% | 84.2% | **98.0%** |
| **Apex winner 0.10/−0.60** | 82.5% | −74.3% | 82.2% | **0.8%** |

TP10's edge also holds at the miss=0.20 extrapolation buffer. **The differential is
fill-model-robust everywhere; the ABSOLUTES are not: under measured-reality fill knobs,
every configuration in this family — including the previously-live baseline — reads
negative, and the pessimistic model tracks the live ledger's −52%-since-June far better
than the canon model.** This is a pre-existing program-level exposure this campaign
quantified, not created. Standing follow-ups: (1) the fidelity report's
"standing-MC-realism candidate" (GAP_AWARE + MISS_P=0.15 default flip) needs its own
Stage-3 A/B; (2) P2.B live-fill reconciliation; (3) no capital-plan gate should read MC
absolute compound/DD at face value until (1)-(2) land. Escalated in known-issues.md
CURRENT SHIP STATE.

## Ship record (2026-08-10)

`strategy_config.py` OPT_30DTE: TP_BASE/TP_STRESS 0.30→0.10, SL_BASE/SL_STRESS
−0.70→−1.00. `portfolio_profiles.json` Apex v5: tp_base/tp_stress pinned 0.10
(previously inherited), sl_base/sl_stress −0.70→−0.60, selection_metrics = paired
2x-harness cert. Drift guard green (655 constants); guard-8 derived pricing verified
(net_tp 0.10 / net_sl −0.615 / σ 0.364/2.184); alloc display runtime-correct; parity: 2
pre-existing sub-cent entry-premium rounding deltas (exits exact); N-floor soft gate =
pre-existing task-611 state (TP/SL cannot move supply). Research pack + temporal-refresh
queued (#393/#394). Live ledger (Apex): re-qualification sweep fires on next update —
TP/SL is threshold-neutral (no forced exits expected from qualification), but open
positions now exit at TP+10%/SL−60% going forward; realized history frozen.

**Regime / market-wave conditional TP/SL: CLOSED — nothing ships.** MWDD band, RXDD VIX
band, and regime-mult conditioning died at the N=300 screen (0/66 passes, flat-to-harmful).
The lone screen survivor (breadth@40 stress-loosen-TP, both profiles) died at N=500
confirm: Core's variant fails the annual-regression rule (2024 +3.5pp) and gives back on
every crash-family window while probe-inverting on 5y; Apex's variant is probe-clean and
better on 11/12 windows but its edge inverts in the survivor-only universe
(delisted-dependent). Screen-level N=300 conditional gains did not survive the confirm
battery — another member of the "cohort z ≠ portfolio alpha" family.

## Hypotheses tested (honesty header)

Phase A 160 cells (N=150×4win) → B 60 cells (N=300×9win) → C 112 conditional cells
(N=300×5win) + 44-cell 9-window follow-up → D 4 flat arms + 2 conditional arms at
N=500×12win + probe/survivor/parity/2x evidence. Selection was lane- and rule-locked
before outcomes at every stage; two candidate-shaped results were REJECTED by
pre-registered rules after passing earlier screens (both conditionals). C2 (per-tier
TP/SL) never triggered (both profiles share one optimum) and stays unopened.

## Core evidence (N=500, vs live baseline TP+0.30/SL−0.70 re-run under identical seeds)

| cell | 5y WorstDD | 22-now WorstDD | med_5y | collapse (12 win) | fill-probe | survivor | parity | verdict |
|---|---|---|---|---|---|---|---|---|
| baseline 0.30/−0.70 | 53.2% | 53.2% | ×1.0 | 0 everywhere | — | — | — | — |
| **0.10/−1.00** | **40.8% (−12.4pp)** | **39.0% (−14.1pp)** | **113×** | **0 everywhere** | ok | ok | ok | **SHIP** |
| 0.10/−0.90 | 41.6% (−11.6pp) | 39.7% (−13.4pp) | 134× | 0 everywhere | ok | ok | ok | SHIP (runner-up) |
| 0.10/−0.60 | 38.9% (−14.4pp) | 39.5% (−13.7pp) | 85× | 0 everywhere | ok | **FLAG** | ok | FLAG |

Per-window: the winner improves WorstDD on **all 12 of 12 windows** (largest: 2023
−21.3pp, 2018 −12.5pp, 22-now −14.1pp; crash windows 2020/2020_crash/10y ≈ −1pp each).
Winner pick between the two SHIP cells: −1.00 beats −0.90 on 5y and 22-now and is the
more parsimonious semantics (SL removed as a mechanism rather than set to a level that
almost never fires).

**The survivor FLAG is the mechanism story:** the only variant whose SL actually fires
(−0.60) loses its edge when delisted names are excluded — SL-exits were harvesting the
death-spiral cohort. The dead-hold variants are survivor-robust. This is the house
dead-hold law re-derived at N=500 from a third independent direction.

## Why this wasn't found before (and why it holds now)

TP+30 was locked 2026-04 on the pre-repair substrate under a cost model charging −1% at
entry (which made TP≤20 catastrophic there); SL−70 came from the 2026-05/06 HOLD-era.
The joint surface was never re-swept after: the honest-universe rebuild, the calendar-hold
engine, the asymmetric execution-cost canon (entry/limit-TP free — ratified against
measured fills), and the seeded bounded-fill rewrite. Under the current canon the
mechanism is coherent: take small profits fast (high WR at 0.364σ barriers), never
realize wick-noise losses, let day-27 + dead-hold machinery clean up. Audit
(LESSONS 2026-08-10): the TP15-vs-TP30 ranking survives friction to 2× the worst
FF-2-measured real spread; turnover is only 1.22× the incumbent (683 vs 558 trades/yr —
the incumbent already cycles fast); the tight-vs-wide differential is fill-assumption-
neutral (close-confirm collapses ALL cells identically, baseline included).

## Standing caveats (documented, not waived — none is a pre-registered gate)

1. **Grid-boundary status:** the optimum sits AT the pre-registered TP floor (+0.10);
   the gradient at the boundary still points down. Going lower was not pre-registered
   and stays closed. A +10% premium TP is a 0.364σ underlying barrier.
2. **Absolute magnitudes are model-optimistic** (engine-wide intrabar-fill assumption;
   ~3.2pp TP-rate optimism measured on the 15-DTE real-contract ledger; 30-DTE unmeasured
   — standing follow-up task chip: Polygon 30-DTE TP-fill fidelity). Selection here is
   differential-based throughout; the pre-registered fill-pessimism probe
   (TP_FILL_MISS_P=0.10) passed for every shipped cell.
3. **DTE-router slice** (~0.3–0.5% of signals) keeps its own 15-DTE barriers in every
   arm AND in a real ship — consistent, but the sweep's TP/SL applies to ~99.6% of
   signals, not 100%.
4. **Live-execution cadence:** median hold drops ~4d → ~3d at ~680 trades/yr (14 slots ≈
   4-5 exits+entries/trading day). Same daily-bar granularity the engine models, and TP
   exits are resting limit orders, but the Allocator/portfolio-engine surface will be
   busier. Watch the forward ledger's fill parity after ship (P2.B evidence stream).

## Reproduction

Everything under `experiments/tpsl_refine_2026_08/`: PREREG.md (design), driver/
(runners; SUBMIT_PLAN.md has the exact Phase D commands), out/*.csv + out/*_summary.md
(evidence), logs/ (raw). Queue task ids: A #324-327, audit #328-330, B #331-338,
C-validate #340-341, C #343-346, C follow-up #369-370, D flat/probe/survivor/parity
#353-366 + #371-372, conditional-D #373-378, apex2x #367 + chained finalist.
