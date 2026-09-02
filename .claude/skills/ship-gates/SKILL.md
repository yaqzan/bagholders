---
name: ship-gates
description: Operationalize the three-stage ship-gate framework (Stage 1 W1-W6 WR15-primary scoring gate, Stage 2 B1-B5 barrier gate, Stage 3 T1-T7 DD-primary portfolio gate) — which stage a change belongs to, exact thresholds, the growth-gate tool sequence and its supply-fallback trap, the holdout lock, the waiver ledger, and what evidence licenses what claim. Use when deciding "does this ship", running `stage1_growth_gate.py` / `signal_supply.py`, reading a SHIP/FLAG/BLOCK verdict, sizing an N for a sweep, or asking "which gate applies to this change".
---

# /ship-gates — the three-stage ship-gate framework, operationalized

Every change to this codebase's trading behavior clears exactly one of three
independent gates before it ships: **Stage 1** (scoring, WR15-primary),
**Stage 2** (barriers, option-TP-primary), **Stage 3** (portfolio, DD-primary).
Each stage optimizes its own objective on a **frozen** output of the prior
stage — this is what stops MC/DD metrics from contaminating scoring sweeps and
vice versa. This skill is the decision layer: which gate, exact thresholds,
the tool sequence, and the traps that produce false verdicts. The barrier
mechanics and table-reading discipline behind Stage 1's evidence live in
[/run-assessment](../run-assessment/SKILL.md) — read that first if you need to
run `trader assess` or interpret its output; this skill assumes that evidence
already exists and focuses on the **verdict**.

**Read fully before a real ship decision:**
[assessment-backtest.md](../../docs/assessment-backtest.md) "Three-Stage
Calibration Framework" (the spec — this skill is the runbook) and
[deploy.md](../../docs/deploy.md) (the exact ship-sequence commands).

## GUARDS (read before trusting any verdict)

1. **Never chain Stage 1 → 2 → 3 sweeps in a single ship.** Each stage
   optimizes its own objective on a FROZEN prior-stage output; running all
   three sequentially in one sitting reintroduces the exact contamination
   (DD leaking into scoring calibration, barrier choice leaking into portfolio
   DD) this framework exists to eliminate. Ship one stage, validate it, THEN
   unfreeze the next.
2. **`signal_supply.py` MUST run before `stage1_growth_gate.py`, every time —
   no exceptions.** Without a supply row for a version, the growth gate falls
   back to `FALLBACK_COVERAGE=0.92` (verified `stage1_growth_gate.py:71`) —
   rosier than almost any real coverage — and can **false-SHIP** an
   N-cutting candidate (the historical v63/BBLT false-SHIP this constant was
   named for). The gate refuses to auto-SHIP on approximated supply since the
   2026-06-11 reform, but treat that as a backstop, not a substitute.
   `build_research_pack.py`'s default `--comparability` tail (since
   2026-06-15) runs `signal_supply.py` automatically — check its final
   printed line, `comparability_unit=COMPLETE|INCOMPLETE`.
3. **Regression-test the gate with `--selftest`, NEVER `--replay`.** The
   2026-06 honest-era recalcs (v69 weekly-blend fix onward) overwrote the
   pre-v69 research packs the documented `--replay` case notes (e.g.
   "v40→v42 BLOCK") were anchored to — those anchors **no longer exist on
   disk** (the tool prints its own invalidation warning at runtime; trust
   it). `--selftest [VERSION]` (default `v71`) synthesizes SHIP/FLAG/BLOCK/W4
   cases from whatever pack IS live, so it cannot rot the same way.
4. **The hardcoded version lists in gate-adjacent tooling are stale — always
   pass explicit tokens.** `signal_supply.py --versions` default and
   `score_versions.py`'s `VERSIONS` constant both hardcode `v44..v66`
   (verified in source, as of 2026-07); a version shipped after that range
   (active is v74 as of 2026-07 — verify via `trader algorithm active`) is
   silently absent unless you pass it explicitly. `score_versions.py` is a
   triage leaderboard, **never a ship gate** — don't cite its ranking as
   Stage 1 evidence.
5. **A version isn't comparable/gateable until the pack, supply, and PRF
   trio all exist.** Confirm `comparability_unit=COMPLETE` before feeding a
   candidate's numbers into a gate verdict; see
   [/run-assessment](../run-assessment/SKILL.md) section 4 for the exact
   auto-chain and what `INCOMPLETE` means to fix.
6. **`assess_scores.run()` resolves version via `get_or_create_current()`
   (HEAD/`ALGORITHM_VERSION` file), NOT `get_active_scores_version()`
   (what's served).** On a dirty tree or mid-worktree state these diverge —
   the evidence you gate on can silently be for the wrong version. Pass
   `--version vNN` explicitly whenever the checkout isn't guaranteed
   clean-at-HEAD. (Full detail: `/run-assessment` GUARD 2.)
7. **The N-capacity floor table is REPORT-ONLY, not a veto** (demoted
   2026-06-11 — it was calibrated against v46-era supply and fired spurious
   REVIEW on every honest-era ship, tripping the waiver ledger's 3-strikes
   rule). W5's binding-window `lambda_eff` already catches supply droughts
   with live data. Read the freshest `experiments/n_floor_v46/summary_v2.json`
   for context only; never block a ship on the static table printed in
   assessment-backtest.md.
8. **No MC at Stage 1 — period, with one narrow smoke exception.** A scoring
   change is ranked on WR15/cohort-z/growth-gate alone. The only sanctioned
   MC touch: if the Stage 1 winner shifts signal density >30% in any binding
   tier, run a quick N=100 smoke MC on `22-now` only to confirm cascade fill
   dynamics didn't break — this is a fill-dynamics smoke test, not a ranking
   objective, and never a hard gate.
9. **FLAG is shippable, not a failure — but it has back-end teeth.** Most
   historically good ships (MCD, ICH) were FLAG-grade. A FLAG ship still
   REQUIRES: (a) one-line documented justification, (b) a **named post-ship
   watch metric** (which number, which surface, what value means "revert"),
   (c) Stage 2/3 confirmation when the claimed value is downstream (the v58
   lesson: Stage-1-clean ≠ portfolio-safe) — except neutrality-track ships,
   which substitute N1-N3 for (c). Never auto-green a FLAG without these.
10. **Never fold W4 into W5 — it's a separate hard guard by design.** W5's
    scalar `g` can mask within-tier asymmetry (the canonical case: cumulative
    `<25` put WR +0.72pp while discrete `<10` lost 2.4pp and 60% of its N —
    the ICH put-`<10` trap). W4 exists specifically so a scalar growth
    verdict cannot paper over a tail-tier loss.
11. **Waiver-ledger discipline: three waivers in the same class → fix or
    retire the gate before the next ship.** A gate that always fails and
    always gets waived isn't a gate, it's training everyone to ignore the
    alarm. The N-floor table's 3rd `stale-calibration` strike (v71,
    2026-06-10) is the precedent that forced its report-only demotion —
    that's the mechanism working as designed, not a one-off exception.

## Which stage applies? (the routing decision)

Ask: **what does the change touch?**

| Change touches | Stage | Gate set | Version bump? |
|---|---|---|---|
| `Score.overall` — `compute_overall_score`, any score-stage dampener/weight/lift/gate | **1** | W1-W6, WR15-primary | Yes — new `AlgorithmVersion` |
| `TP_BASE`, `TP_STRESS`, `SL_BASE`, `SL_STRESS`, `HOLD_DAYS`, `PREMIUM_MULT`, `BREADTH_THRESHOLD` (stress switch), `PUT_TP`/`PUT_SL` | **2** | B1-B5, option-TP-primary | No — `barrier_outcomes` rebuild instead |
| Anything else portfolio: F3F, MaxPos, cascade allocation (TIER_ALLOC/PUT_TIER_ALLOC), DD soft-band, sector-concentration exposure waves, dead-hold, slippage, regime slopes (REGIME_SLOPE_UP/DOWN), EARN_SUPP_PUT, CT_PROMOTE, SAW Put U-curve, hard-sell timing/P&L, dampener knobs like RXDD/SVR/MWDD/TVDD/BDIV | **3** | T1-T7, DD-primary MC | No |

Worked examples (verbatim from assessment-backtest.md "Decision"): editing
`compute_overall_score` weights → Stage 1. Adding a new score-stage dampener
→ Stage 1. `TP_BASE` 0.30→0.35 → Stage 2. Adding `DEAD_HOLD_TRIGGER_PNL` →
Stage 3. Slippage −0.013→0.0 → Stage 3. `F3F_CALL_FLOOR` adjustment →
Stage 3. Toggling `EARN_SUPP_PUT` → Stage 3. Shipping SAW Put U-curve →
Stage 3. A sector-concentration allocation wave → Stage 3 (must not rewrite
scores; only routes to Stage 1 if the sector feature independently predicts
WR15).

If genuinely unsure, default to the **more restrictive** stage (Stage 1 is
the strictest; when in doubt, gate as if `Score.overall` might change) and
confirm with a drift-guard run before touching anything.

---

## Stage 1 — Scoring Calibration Gate (WR15-primary, barrier-independent)

**Applies to:** any change modifying `Score.overall` on any historical date.
**Why barrier-independent:** Stage 1 answers "did the score predict
directional truth" — an answer that must not depend on which TP/SL was
picked, or scoring sweeps converge to barrier-overfit local optima that
re-tune away once Stage 2 runs.

**Primary objective:** cohort z-score on the targeted feature (≥+3 required
to even start calibrating), then **WR15 on the affected cohort** (5y, 30 DTE
assessment, generic K=2σ/M=5σ barriers). "Affected cohort" = the subset of
signals the change actually modifies — NOT the global tier (which dilutes a
focused dampener with thousands of untouched signals).

| Gate | Constraint | Threshold |
|---|---|---|
| **W1** | Cohort z-score | ≥ +3 on targeted feature, same direction as proposed change — pre-flight, before any sweep starts |
| **W2** | Multi-barrier-window directional consistency at 5y (noise-aware) | WR3/WR5/WR7/WR15/WR30 do not *confidently* contradict the WR15 conclusion on the affected cohort — a contradiction counts only at two-proportion z ≥ 2 **and** cohort N ≥ 100 at that window; small-N sign flips are noise |
| **W3** | Multi-time-window WR15 consistency (noise-aware) | 1y/3y/5y directions agree on affected cohort; a disagreeing window blocks only at z ≥ 2 **and** N ≥ 100 |
| **W4** | Per-discrete-bucket non-regression (noise-aware, separate hard guard) | No discrete bucket (95+…<5) regresses WR15 or option-TP by a statistically real margin: `z ≤ -2` = flag, `z ≤ -3 ∧ ≥1.5pp ∧ N≥100` = block. Never folded into W5 (GUARD 10) |
| **W5** | Hydration-Adjusted Growth verdict | `g = ebar · lambda_eff` on both barriers + binding window → SHIP/FLAG/BLOCK (mechanics below) |
| **W6** | Gradient preservation (noise-aware, FLAG-only) | Bands with N<100 pool upward (95-100→90-94); an inversion counts only at pairwise two-proportion z ≤ −2 on shrunk values; only **candidate-introduced** inversions escalate (FLAG, never BLOCK) — baseline-inherited inversions report without escalating |

**Explicitly OUT of Stage 1:** option TP%/avg_option_pnl (Stage 2); 5y
compound, WorstDD, collapse rate, F3F-eligible density (Stage 3);
cumulative-tier WR15 alone (the retired old "H1" gate — see GUARD 10).

### W5 mechanics — the auto-decision (`g = ebar · lambda_eff`)

- **`ebar`** — per-trade log edge of the *filled* book:
  `e(p) = p·ln(1 + f·w) + (1-p)·ln(1 - f·l)`, averaged over the slots a
  conviction-priority cascade actually fills (`demand` = MAX_POS /
  avg_hold_bars ≈ 6.2/day, best tier first). `(f, w, l)` are held **CONSTANT**
  at nominal 30 DTE values reproducing the documented break-evens exactly
  (call ≈45.0%, put ≈36.4% — verify current values against
  `experiments/version_scorecard/STAGE1_GROWTH_GATE.md`, they're calibration
  constants, not physical laws) — this is what stops a sweep from tuning a
  barrier to win.
- **`lambda_eff`** = `demand · recycle_coverage` — velocity. **Saturates** at
  the full book (extra supply earns zero credit — the v58-volume-bug guard)
  and is drought-aware (`recycle_coverage` = mean per-day
  `min(supply, demand)`; bursty supply scores below steady supply).
- Computed on **both barriers** — `g_option` (`p = tp_shrunk`, PRIMARY
  tradable) and `g_generic` (`p = wr_shrunk`, directional sanity; closes the
  SVD generic-vs-option divergence) — and on **each window** (2022/2023/
  2024/2025/dip/5y). Gates on the **binding (worst-`g`) window** — the 5y
  mean is never the constraint (supply typically 12-14/day vs ~6.2 demand);
  bear-tape droughts are.

**Verdict** (`eps = 1%`, a tunable knob — verify current value in
`stage1_growth_gate.py`):

| Verdict | Condition on the binding window |
|---|---|
| **BLOCK** | W4-severe (z ≤ -3, ≥1.5pp, N≥100 on a band) **OR** a barrier's best-case `dG` (p95) still < -eps |
| **SHIP** | option-barrier point `dG ≥ -eps` **AND** p05 ≥ -2·eps **AND** no real W4 dip |
| **FLAG** | otherwise — scoring-neutral tie / wide CI / small real W4 dip → route to Stage 2/3, do NOT auto-green |

BLOCK keys on *either* barrier (dual veto); SHIP keys on the PRIMARY/option
barrier only (a flat directional cross-check must not veto a confident
tradable win). **Scoring-neutral ties FLAG, not SHIP.**

### Stage 1-N — the neutrality-proof track (scoring-neutral / stability / honesty ships)

W5 returns FLAG by construction for a ship with no growth motivation (a
leak-fix, a stability fix, a refactor) — a true zero-tradable-change dG ≈ 0
ties FLAG by design. For this class, the FLAG is satisfied by **N1-N3**
instead of downstream Stage 2/3 confirmation:

| Gate | Requirement | Template |
|---|---|---|
| **N1** | Tradable-bucket invariance — full-faithful `ScoreSimulator` A/B over ≥1y full universe: ZERO bucket-crossing diffs in any tradable band (call tiers ≥70, put bands ≤30) | v72: 194,526 pairs, 0 violations |
| **N2** | Reconstruction/bit-exactness proof on the subset the mechanism claims to preserve | v72: 11,492/11,492 stored fired rows bit-exact |
| **N3** | Direct evidence on the actual value metric the ship is FOR — measured, not asserted | v72: WCF fakeout groups −60% on `score_intraday_logs` replay |
| **N4** | W4 still runs on any diagnostic buckets that DO change | v72: `<30` bucket +0.8pp, reported |

**If N1 finds ANY tradable-bucket diff, the candidate is not neutral** —
route it through the normal W1-W6 growth path instead. This track exists
because 3 of the last 4 version ships before it (v69 weekly-honesty, v71 leak
fixes, v72 fakeout-stability) were evaluated ad-hoc under gates that assumed
growth motivation.

### Tool sequence (order matters — GUARD 2)

```bash
# 0. Prerequisite: candidate is comparable (pack + supply + PRF trio exist)
python tools/build_research_pack.py --version vNN --run-portfolio-windows
# ^ auto-chains signal_supply.py + portfolio_response.py --materialize;
#   last printed line MUST read comparability_unit=COMPLETE

# 1. Supply FIRST, always — never skip even if you "already ran it earlier"
python experiments/version_scorecard/signal_supply.py --versions <base>,<cand>

# 2. The real gate
python experiments/version_scorecard/stage1_growth_gate.py --baseline <base> --candidate <cand>

# 3. Regression-test the gate itself (never --replay — GUARD 3)
python experiments/version_scorecard/stage1_growth_gate.py --selftest
```

`stage1_growth_gate.py` other invocation forms (verified against its
argparse): `--cohort v44,v52,v57,...` (rank a whole cohort at once),
`--demo-drought VERSION` (illustrate the drought-aware `lambda_eff` term).
`signal_supply.py` extra flags: `--lookback-days` (default 1825), `--out`
(default `.cache/algorithm_versions/_scorecard/supply_burstiness.json`).
Both run in seconds — no MC, no recalc, pack-only reads.

**Sweep cadence** (staged-Bayesian: cohort-z → LHS blast radius 50-150 → Optuna
drill 100-400 → fine grid 30-80 → validate): see
[references/sweep-cadences.md](references/sweep-cadences.md) for the full
phase table, preflight checklist, and anti-patterns — the methodology is
shared across all three stages, detailed once there rather than three times.

---

## Stage 2 — Barrier Optimization Gate (option-TP-primary)

**Applies to:** `TP_BASE`, `TP_STRESS`, `SL_BASE`, `SL_STRESS`, `HOLD_DAYS`,
`PREMIUM_MULT`, `BREADTH_THRESHOLD` (stress switch), `PUT_TP`/`PUT_SL`.
**Why separate:** these parameters define the option-aligned barrier set —
changing them invalidates the `barrier_outcomes` cache (keyed on K/M/W/
PREMIUM_MULT) and forces forward-walk recomputation; they drive option TP%,
avg_option_pnl, and the WR15→option-capture tax, none of which appear in
Stage 1 (barrier-independent) or Stage 3 (DD-primary).

**Primary objective:** Option TP% on full population at 5y, 30 DTE — bound
the gap between what scoring admitted (Stage 1 winners) and what the option
strategy actually captures. **Secondary:** `avg_option_pnl_15d` on the same
buckets (catches theta-trap configurations where TP% looks fine but P&L lags
from late-day-14 fires under heavy theta drag).

| Gate | Constraint | Threshold |
|---|---|---|
| **B1** | Stage 1 frozen | Scoring stack must not change — run drift-guard before sweep |
| **B2** | Smoke MC DD bound | N=300 × 22-now only: 5y WorstDD ≤ baseline +1.5pp |
| **B3** | Smoke MC collapse | 0% on 22-now × N=300 |
| **B4** | Per-tier option TP% non-regression | No call tier (95+…75+) regresses option TP% by >1pp on full 5y |
| **B5** | Capital recycling efficiency | Median bars-to-resolution ≤ baseline +1 bar |

**Each candidate requires a `barrier_outcomes` cache rebuild** for the new
K/M/HOLD set (~30-60 min on 30 DTE, longer on 15 DTE) — this is why the
sweep cadence is a coarse Bayesian (5-10 candidates → top-3 fine refinement →
validate), not a full Bayesian search; full phase table in
[references/sweep-cadences.md](references/sweep-cadences.md).

**No `ALGORITHM_VERSION` bump. No `trader recalculate`.** Edit
`strategy_config.py`, run drift-guard
(`python tests/test_strategy_config_drift.py`).

---

## Stage 3 — Tertiary Portfolio Gate (DD-primary)

**Applies to:** F3F (call/put), MaxPos, cascade allocation (TIER_ALLOC,
PUT_TIER_ALLOC), DD soft-band, sector-concentration exposure waves,
dead-hold, slippage, regime slopes (REGIME_SLOPE_UP/DOWN), EARN_SUPP_PUT,
CT_PROMOTE, SAW Put U-curve, hard-sell timing, hard-sell P&L.

**Why DD primary, compound secondary:** MC compound at scale (1e26%, 1e36%)
assumes unlimited broker capacity, free option liquidity on rare 95+
signals, no bid-ask drag at scale, no IV crush — beyond ~1e10% (≈$50k→$5T)
real-world execution caps in; anything past ~1e15% is fantasy. Those numbers
measure *model dynamics*, not *trader outcomes*. **DD is the metric that
survives translation to real-world execution** — the number a trader would
actually feel on the equity curve at any portfolio size.

| Gate | Constraint | Threshold |
|---|---|---|
| **T1** | Stage 1 + Stage 2 frozen | Scoring stack and barrier set must not change |
| **T2** | Iteration count | N=500+ per (window × mode) — N=150 4-window is insufficient (the Phase OP1 lesson) |
| **T3** | Window coverage | All 8 canonical windows: 2021, 2022, 2023, 2024, 2025, dip, 22-now, 5y |
| **T4** | **5y WorstDD primary** | 5y WorstDD ≤ baseline within +1.0pp — "DD reduction is the real ship reason" |
| **T5** | Per-window DD stability | No annual window regresses DD by >5pp vs baseline |
| **T6** | Collapse rate | P(collapse) = 0% on every (window × mode) cell — non-negotiable, no profile exception |
| **T7** | Compound non-regression sanity | 5y compound order-of-magnitude within ±3 OOMs of baseline (catches strategy-breaking changes; ignores MC-noise compound swings at 1e26+% scale) |

**Soft constraints (judgment, don't auto-reject):** TS1 per-trade WR15/
option-TP% drift should be invariant — if it isn't, scoring is leaking
through a supposedly Stage-3-only change; TS2 slot displacement should be
documented; TS3 per-window DD asymmetry (bull improves, bear worsens) — flag,
don't auto-reject; **TS4 "compound win at DD cost"** — a candidate that lifts
compound >50% but worsens 5y DD by >1pp is the Phase OP1 trap, reject
reflexively unless the compound win crosses ~1e10% AND the DD cost is
bounded; TS5 confirm a DD improvement isn't accidental dampening
(CT_PROMOTE-class) crowding high-conviction signals out of MaxPos; TS6
report per-sector/per-side exposure before/after — DD wins should come from
reduced clustering, not hidden per-trade-quality drift.

**Sector-concentration exposure waves** are Stage 3 shaping, never Stage 1:
`sector_share_after = sector_exposure_after / total_book_exposure; pressure =
smoothstep(start, full, sector_share_after); scale = max(floor, 1 -
max_cut * pressure)`. Must not rewrite scores or become part of the WR15
objective unless the sector feature independently proves directional WR15
signal value.

**Sweep cadence** (baseline N=500×8 with `PYTHONHASHSEED=0..2` noise
quantification → Bayesian LHS+Optuna N=100×8, 50-150 variants → drill top-5
at N=300×8 → ship gate at N=500×8): full phase/compute table in
[references/sweep-cadences.md](references/sweep-cadences.md). Full MC re-run
mechanics (env knobs, seeded model, window list, N floors, smoke recipe) live
in [/run-monte-carlo](../run-monte-carlo/SKILL.md) — this skill states the
gate thresholds, not the invocation. Every Stage 3 sweep and screen must
always include a 2020/2020_crash window even though it's outside the 8
canonical windows — never rank a screening phase without it.

**Sequence: strategy_config.py edit → drift-guard → N=500×8 T1-T7 validation
→ `trader temporal-refresh --profiles all` → post-ship audit checklist →
W5 N-floor signal check (should read SAFE on every tier — a non-SAFE reading
here means something OTHER than the portfolio change also moved) →
recalibrate the N-floor table only if the ship shifted ≥30% offered/year on
a binding tier.** No `ALGORITHM_VERSION` bump; no `trader recalculate`.

---

## Holdout lock

Every calibration sweep — Stage 1, 2, or 3 — gates on
`CALIBRATION_CUTOFF_DATE` in `strategy_config.py` (currently `"2026-06-15"`,
re-locked 2026-06-15; verify the live value, it has moved before — OOS
re-eval target ≈2026-12-15):

```python
from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter, cutoff_iso, CUTOFF
assert_no_holdout_leak(df, context="my_sweep")   # defensive, at sweep entry
df = pre_cutoff_filter(df)                        # preferred, at build time
```

`assert_no_holdout_leak` accepts polars/pandas DataFrames with a `date`
column, iterables, or single date-likes; the cutoff date itself is in-sample
(`<=`); raises `AssertionError("[HOLDOUT LEAK] ...")` naming the offending
max date. Escape hatch: `HOLDOUT_DISABLE=1` env var — for live-trading
evaluation only, **never during a calibration sweep**; portfolio-stage
backtests on already-frozen score rows are the one documented exempt
context (e.g. `concentration_2x`'s README "Holdout note": the lock governs
scoring-lift fits, not portfolio-stage backtests replaying existing rows).
**Toggling the cutoff breaks the scoring-version lock** — if you must change
it, re-run `capture_lock` and commit the resulting `scoring_locks.json` or
`trader update` will refuse to run.

---

## Gate waiver ledger (normalization-of-deviance guard)

Every gate waived or excepted at ship time gets ONE ledger row in
assessment-backtest.md: date, version, gate, class, reason. Classes:
`stale-calibration` (gate's reference data outdated), `tiny-N` (gate
statistically incoherent at the bucket's N), `inherited-baseline` (failure
pre-exists in baseline), `scoring-neutral` (gate assumes growth motivation
that doesn't apply), `other`. **Rule: three waivers in the same class → fix
or retire the offending gate before the next ship** (GUARD 11) — this is not
a suggestion, it's how the N-floor table got demoted to report-only.

---

## The evidence ladder — what evidence licenses what claim

A claim resting on evidence one rung below what it needs is the single most
common way a "clean" ship turns out not to be (the v58 lesson: Stage-1-clean
is not portfolio-safe without the downstream DD check). Full ladder table
(9 claim/evidence pairs from "this cohort might carry signal" up to "this
portfolio change reduces real-world risk") moved to
[references/evidence-ladder.md](references/evidence-ladder.md) — consult it
when you're not sure whether the evidence in hand actually licenses the
claim you're about to make.

## Evidence / see also

- [references/sweep-cadences.md](references/sweep-cadences.md) — full
  phase/variant-count/compute tables for all three stages' sweeps, the
  preflight checklist, anti-patterns, and canonical exemplar scripts.
- [references/evidence-ladder.md](references/evidence-ladder.md) — the
  claim-to-minimum-evidence table.
- [assessment-backtest.md](../../docs/assessment-backtest.md) — full spec:
  Three-Stage Calibration Framework, barrier methodology, staged-Bayesian
  sweep methodology, "What NOT to do", the live waiver ledger table.
- [deploy.md](../../docs/deploy.md) — exact ship-sequence commands per stage,
  the post-ship audit checklist, the new-mechanism 13-consumer checklist.
- [/run-assessment](../run-assessment/SKILL.md) — how to actually run
  `trader assess`, read WR15 vs option-TP vs hold-TP tables honestly, and
  build research packs. This skill assumes that evidence exists; that one
  produces it.
- [/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) — the end-to-end
  discover-and-ship loop that calls this gate at its Phase 5/6; its GUARDS
  1-2 are the option-TP-vs-cumulative and real-supply traps in mining context.
- [/ship-version](../ship-version/SKILL.md) and
  [/ship-portfolio](../ship-portfolio/SKILL.md) — the full ship mechanics
  (silo checkpoints, ALGORITHM_VERSION bump order, consumer-wiring
  checklists) once a gate has passed.
- `.claude/docs/known-issues.md` "SHIP GATE REFERENCE" — the condensed
  gate-threshold table (this skill is the fuller operational version) and
  "WHAT NOT TO DO" for the null-check reflex before proposing a new sweep.
- `experiments/version_scorecard/STAGE1_GROWTH_GATE.md` — growth-gate
  mechanics and current calibration constants in full.
- [traps.md](../../docs/traps.md) — canonical trap registry this skill's
  GUARDS are drawn from and should feed back into.

## Self-update

If you hit a trap this skill missed — a new stale hardcoded version list, a
gate threshold that changed, a waiver-ledger class hitting three strikes —
append it here as a new numbered GUARD and to
[traps.md](../../docs/traps.md) in the same session.
