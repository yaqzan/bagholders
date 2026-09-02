---
name: run-experiment
description: The A-to-Z lifecycle for a new research experiment in experiments/ — null-check against known-issues.md and NEW_LEADS before starting, worktree setup with the PYTHONPATH trap fix, staging-native variant testing (ScoreSimulator / monkey-patch, never env-gates), house-style FINDINGS.md evidence format, escalation to the ship gates, and closeout (classify, document, merge, remove worktree). Use when the user says "test this hypothesis", "run an experiment", "mine this signal", "set up a sweep for X", "build a new experiment dir", or wants to know how to structure/close out research work before it reaches a ship decision.
---

# /run-experiment — experiment lifecycle A→Z

The full arc for anything that lives under `experiments/<name>/`: a hypothesis
that might become a Stage 1 scoring ship, a Stage 3 portfolio-DD lever, or a
documented null. This skill covers the loop itself — for the gate math that
decides SHIP/FLAG/BLOCK, see `/ship-gates`; for the final scoring-version
elevation, see `/ship-version`.

## GUARDS (read before starting anything)

1. **Null-check FIRST, always.** Before writing a line of code, grep
   `.claude/docs/known-issues.md` `## WHAT NOT TO DO` (~40 hard-won rules,
   e.g. "never re-test breadth-collapse→cut-calls", "never smooth a component
   cliff without the dropped-rows-apex-WR test") and
   `.claude/docs/known-issues-archive.md` (CLOSED — NULL RESULTS / REVERTED
   timeline), and check `alpha_mining/NEW_LEADS.md` for the lead's current
   status. A retry is only legitimate with a **genuinely NEW mechanism
   class** — not a rephrasing of a closed idea. Most "new" ideas about
   breadth/regime/VIX/momentum/volume sizing levers are already closed; read
   the null before you re-derive it the hard way.
2. **No env-gates in production scoring, ever.** Never add `PHASE_X_ENABLED`
   flags, "default OFF" research knobs, or any conditional to
   `database/utils/scoring.py`, `database/models/core.py`, `simulator.py`,
   `api.py`, `trader.py`. A failed hypothesis must leave **zero trace**. Test
   via `ScoreSimulator(scoring_fn=variant_fn)` or a runtime monkey-patch with
   `try/finally` teardown — see Phase 4. Winners migrate into staging scoring
   code before validation; losers vanish with the worktree.
3. **The worktree PYTHONPATH trap produces false NULLs.** This box's
   `PYTHONPATH` includes the main checkout, so a bare
   `python experiments/<x>/<y>.py` run from inside a worktree silently
   imports **MAIN's** modules (script-dir, not cwd, lands at `sys.path[0]`).
   An A/B that returns 0 diffs full-universe is the classic tell (observed
   2026-06-11 on the WCF ramp: 0 diffs vs 52 diffs from cwd smoke). Fix in
   Phase 2 below — a 0-diff A/B without an origin assert is suspect, not
   clean.
4. **Small-screen and P0-approximation evidence do not license a ship.**
   Phase I (2026-05-08): a 60-stock screen showed +6/+7pp on 85+; the full
   772-stock universe showed −3.0/−0.3pp. v42 rolling-weekly: a P0
   `_ph_cache={}` approximation predicted +4.51pp WR15; full-faithful assess
   showed −13.2pp (bypassed rare-event lift bands). Mandate:
   `trader recalculate --force --full` + `trader assess --force` before any
   WR-lift claim — see `/data-ops` and `/run-assessment`.
5. **Holdout lock is live — gate every sweep on it.** Check the CURRENT value
   in `strategy_config.py` (`CALIBRATION_CUTOFF_DATE`, as of 2026-07 =
   `"2026-06-15"`) — never hardcode the date from memory or an old doc.
   `experiments/_holdout.py` exposes `assert_no_holdout_leak(df, context=...)`
   (defensive, at sweep entry) and `pre_cutoff_filter(df)` (preferred, at
   build time). `HOLDOUT_DISABLE=1` is for live-trading evaluation only —
   **never** during a calibration sweep.
6. **Look-ahead is the recurring root cause of false alpha.** Any weekly
   feature mined off a recalc'd historical row is contaminated —
   `WeeklyScore` stores the COMPLETE Mon–Fri bar keyed on the current week, so
   a mid-week historical signal sees future bars in a recalc. **Test:** split
   the cohort's option-TP by day-of-week; if Monday ≫ Friday (Friday ≈
   baseline), it's look-ahead, not alpha. This single test killed and then
   correctly re-diagnosed the v69 weekly-transition finding. Any
   point-in-time-attribute mechanism (market cap, sector) applied to history
   needs the equivalent PIT-proxy discipline (`mcap_t ≈ mcap_latest ×
   close_t / latest_close`) — MCD's 8.2pp mcap gradient collapsed to 2.6pp
   once PIT-corrected (it was survivorship).
7. **Queue everything minutes+.** A full-universe `ScoreSimulator` capturing
   run, a recalc, an MC sweep, or a research-pack build is a bulk MySQL
   load — **always** `trader queue submit`, even read-only, even from a
   worktree. The harness's own `run_in_background` flag is NOT the queue and
   bypasses CPU/DB admission entirely, letting a scheduled `trader update`
   collide on top of your job. **Submit from MAIN, not from inside the
   worktree**: the queue's SQLite state (`.cache/task_queue.db`) resolves
   relative to cwd, so a task submitted while cd'd into the worktree lands in
   a separate, unwatched queue and silently never runs. Always run
   `trader queue submit` from `C:/Development/Trader` and pass
   `--cwd ../Trader-exp-<name>` so the child process executes inside the
   worktree while the daemon (tracking MAIN's queue DB) watches it. See
   `/queue-ops` and `.claude/docs/traps.md`.

## Phase 0 — Null-check (do this before anything else)

```bash
# grep the hard-rule registry — read every match, don't skim
grep -n "<your topic keywords>" "C:/Development/Trader/.claude/docs/known-issues.md"
grep -n "<your topic keywords>" "C:/Development/Trader/.claude/docs/known-issues-archive.md"
```

Also read `alpha_mining/NEW_LEADS.md` (ranked backlog — check if your idea is
already a resolved/tested-null lead) and skim `## Low-Priority /
Do-Not-Retry Traps`. If your idea maps to a closed line, stop unless you can
name the specific new mechanism class that distinguishes your hypothesis from
the closed one. `.claude/docs/traps.md` is the consolidated trap registry —
check it too if it exists in this checkout.

## Phase 1 — Hypothesis note

Before writing code, write down (even just in your own working notes, not
necessarily a committed file yet):

- **Predictand** — which barrier/outcome you're measuring against (option-TP
  `30dte_opt` w=15d is the primary tradable barrier; `30dte_apex` TP+30/SL−70/
  day-15 is the funded-book barrier; generic K·σ/M·σ is sanity-only — see
  `/run-assessment`).
- **Mechanism class** — one sentence naming the generic market mechanism
  (e.g. "pre-top breadth divergence", not "the BDIV ticker case"). Ticker- or
  symbol-specific names are a review trigger (`tests/test_strategy_config_drift.py`
  scans for the failure class, e.g. `POET_*`).
- **A cheap, decisive pre-test** that can kill the idea in minutes before you
  build anything expensive — full catalog with worked examples in
  [references/cheap-pretests.md](references/cheap-pretests.md). Quick index:
  EV-by-band on the existing tape; dropped-rows apex-WR test (cliff-smoothing
  ideas); component-tercile sign-consistency across regime classifiers
  (reweight ideas); day-of-week look-ahead split (weekly-feature ideas,
  GUARD 6); all-levers-off orthogonality slice (new DD-lever ideas);
  N-escalation stability (100→300→500).

If the cheap pre-test kills it, write the null (Phase 7) and stop — this is
the fast path, not a shortcut around rigor.

## Phase 2 — Worktree (scoring-code changes only)

Per CLAUDE.md's algorithm-experiment trigger: **any hypothesis touching
sweeps, parquet cache builds, or multi-variant assessment on scoring code**
gets an isolated worktree BEFORE you touch scoring. Read-only cohort mines
(Phase 0/1) can run from the main checkout; only create the worktree once
you're about to edit `database/utils/scoring.py` / `strategy_config.SCORING`
or run repeated sweeps.

```bash
cd C:/Development/Trader
git worktree add ../Trader-exp-<name> -b algo-exp/<name>
```

**Why a worktree, not a branch:** production `trader update` keeps scoring on
`main` while your sweeps run concurrently, and `.cache/` artifacts stay
isolated per worktree (no cross-contamination of parquet caches).

**The PYTHONPATH fix — do this in every script you run inside the
worktree.** The guarded idiom (`if _ROOT not in sys.path: sys.path.insert(0,
_ROOT)`) is NOT sufficient — if the main checkout's path is already present
anywhere in `sys.path` (it usually is, via this box's global `PYTHONPATH`),
the guard no-ops and Python resolves imports to whichever copy comes first.
Use the FORCE-insert pattern from the canonical harness
(`experiments/integrity_audit_2026_06/ab_eval.py`):

```python
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # adjust "..".."s to your dir depth
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
```

Then **assert the origin** right after importing the scoring module, so a
silent wrong-checkout import fails loudly instead of producing a false NULL:

```python
import database.utils.scoring as sc
assert os.path.abspath(sc.__file__).lower().startswith(_ROOT.lower()), \
    f"scoring module resolved from wrong checkout: {sc.__file__}"
```

Simpler builders that only read data (not scoring code) can use the lighter
`build_features.py` convention instead — `ROOT = Path(__file__).resolve().parents[2]`
+ `sys.path.insert(0, str(ROOT))` — since they don't import
`database.utils.scoring` and the collision risk is lower, but the assert is
still cheap insurance.

**A 0-diff full-universe A/B without this fix and an origin assert is
suspect, not clean.** Treat it as unverified until you've confirmed the
scoring module actually loaded from the worktree.

## Phase 3 — Data via build_features.py / bulk_cache

For any experiment pulling bulk MySQL data (PriceHistory/Indicator/Score) and
sweeping more than one variant against it, materialize a parquet once, then
sweep against the parquet:

```python
# experiments/my_thing/build_features.py
from database.bulk_cache import materialize_polars, chunked_query_by_year
import polars as pl

def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id={vid} AND date BETWEEN '{y_start}' AND '{y_end}' "
        "AND overall >= 70",
        start_year=2016, end_year=2027, vid=74,   # look up the current version id, don't hardcode 74 blindly
    )
    return pl.DataFrame({...})

parquet_path = materialize_polars('my_thing_v74_10y', _build)   # name encodes (experiment, version, lookback)
```

Naming convention: `{experiment}_{tag}_v{version}_{lookback}` — the version
number in the name means a later ship invalidates the cache by construction
(new name, not overwrite). Look up the active version via
`python trader.py algorithm active` rather than assuming.

**Holdout enforcement is mandatory** if the experiment sweeps under the
calibration lock: call `assert_no_holdout_leak(df, context='<name>')` right
after `pl.read_parquet()`, or better, `pre_cutoff_filter()` inside `_build()`
so post-cutoff rows never enter the cache at all.

For barrier-touch lookups specifically, the shared `barrier_outcomes` cache
(SQLite canonical + DuckDB mirror, ~50-100× faster) is usually the right
substrate — see `/data-ops` and `/run-assessment` for the exact API
(`peaks_to_swing_results`, `forward_walk_subset`). The cache only holds a
recent window; use the forward walk directly for a full 5y+ mine.

## Phase 4 — Variants, staging-native

Three acceptable patterns, in order of preference for a candidate that might
ship:

1. **`ScoreSimulator(scoring_fn=variant_fn)`** — quick probe when the
   hypothesis reduces to "given these inputs, return a different `overall`."
2. **Runtime monkey-patch with `try/finally` teardown** — for hypotheses
   touching an internal helper not exposed via `scoring_fn` (e.g.
   `calculate_weekly_adjustment`). Canonical reference:
   `experiments/weekly_avwap/phase_i_wadj_blend_sweep.py`. Patch BOTH
   `database.utils.scoring` and every module that imported the function by
   name, or the patch silently misses call sites.
3. **`trader simulate`** — the CLI entrypoint, good for quick compare/assess/
   diff-assess passes without writing a custom runner.

The proven multi-arm vehicle for "does dampener/component X still earn its
keep" questions is the **ReSim A/B harness**
(`experiments/integrity_audit_2026_06/ab_eval.py`, cloned by
`dampener_ablation_v72/ab_eval.py`): build one `ScoreSimulator` per shard,
run every arm in-process via module-constant patches with try/finally
restore, persist `(symbol, date, overall)` per arm to parquet, join to the
`barrier_outcomes` DuckDB mirror, judge by delta-cohort WR on the option
barrier. Validate the harness itself before trusting it: baseline arm vs
stored production rows should land ~98.4-98.5% exact match (the known gap is
the simulator's missing continuation-echo term — this is the accepted band,
not a bug to chase).

If a variant wins, **migrate it into the real staging scoring path**
(`database/utils/scoring.py`, `strategy_config.SCORING`) inside the worktree
BEFORE running W1-W6 validation — GUARD 2 above. A monkey-patch or
`scoring_fn` override is never itself the shipped mechanism.

## Phase 5 — Evidence format (FINDINGS.md)

`experiments/<name>/FINDINGS.md` is the canonical evidence + verdict record
(~88 of 134 experiment dirs carry one; the dir-per-experiment convention with
FINDINGS.md is the modern (post-~v60) house style — it is NOT uniform across
the older top-level `bayes_phase*.py` scripts, so don't promise every legacy
dir follows this shape). House format, distilled from
`experiments/dampener_ablation_v72/FINDINGS.md` and
`experiments/concentration_2x/FINDINGS.md` (read one in full before writing
your first FINDINGS.md — they are the style exemplars):

- **Header**: title + date, bold **Status** line stating the outcome in one
  clause (e.g. "COMPLETE — 2 clear RETIRE candidates, 3 marginal, 2 KEEP; v73
  candidate STAGED, not shipped").
- **`## Question`** — cite the motivating lead ID if one exists (e.g. "NEW_LEADS
  lead N1").
- **`## Method`** — harness lineage (which prior harness you cloned/reused),
  shard count, `EVAL_START` window, which barrier is primary vs sanity.
- **Arm/patch table** — `Arm | Patch | Mechanism` with the exact
  `strategy_config` constant + line number each arm flips, so the ship
  commit is traceable back to this table.
- **`## Run facts`** — queue task IDs, per-shard runtimes, validation %
  exact-match.
- **`## Results`** — table with `N`, WR/EV, and `z` per cohort. Every claim
  needs an N and a z; "z=−0.43 = no discrimination" is the house reading
  style for a null cell.
- **`## Verdicts`** — numbered, per-mechanism, using house vocabulary:
  **RETIRE / KEEP / MARGINAL retire-leaning / STAGED / SHIPPED / TESTED-NULL /
  CLOSED-NULL / NO SHIP / FLAG / BLOCK / PASS**. Cite the bias-to-retire
  doctrine where relevant (mechanisms re-earn their seat on honest evidence;
  ties go to retirement).
- **`## Bottom line`** — one paragraph, plain language.
- **`## Artifacts`** — paths to the harness, analyzer, and `.cache/<name>/`
  parquet/report locations (never commit the parquets themselves — see the
  `.cache/` convention in `/data-ops`).

**Windows to report**: 2022 / 2023 / 2024 / 2025 / dip / 22-now / 5y always;
add **2020_crash/COVID mandatory** for any Stage-3 screen (never rank a
portfolio candidate without it — a Phase-B screen once hid a collapse that
only showed at Phase C/D). N floors: MC N=500 ship gate, N=300 screen
(expect ±5-8pp DD noise / 1.6-1.8× compound noise at this N), N=100 probe.
Per-trade cohort z is evidence toward a mechanism, but it is **not** a
portfolio result by itself — a correlated-fill DD can offset per-trade alpha
exactly (documented 3+ times).

**Portfolio-stage exemplar note** (concentration_2x): state the objective in
one sentence up front (e.g. "minimum time-to-2x, collapse-TOLERANT —
portfolio-stage only, NO scoring change, NO version bump"), define the metric
precisely (P(2x within 2y), median days-to-2x, P(collapse), worst DD), and
close with a `## SHIPPED <date>` section listing every touched file and every
gate re-run (drift-guard count, registry count, profile-load smoke, `/health`).

**Overnight/autonomous-run variant**: `OVERNIGHT_FINDINGS.md` (only one
exists today, `experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md`) adds a
scope banner up front ("No shipping, no scoring/strategy_config mutation...")
and a deliverables table with ✅/📋 status — use this shape only for a
genuinely unattended run, not a normal interactive session.

## Phase 6 — Escalate

Once evidence exists, route to the right gate — do not eyeball it yourself:

- **Stage 1 (modifies `Score.overall`)** → `/ship-gates` W1-W6, WR15-primary.
  Run `signal_supply.py` BEFORE `stage1_growth_gate.py` (missing supply rows
  fall back to a rosy `FALLBACK_COVERAGE=0.92` and can false-SHIP).
- **Stage 2 (TP/SL/HOLD/PREMIUM barrier params)** → `/ship-gates` B1-B5,
  option-TP%-primary.
- **Stage 3 (anything portfolio: cascade, MaxPos, DD levers, dead-hold)** →
  `/ship-gates` T1-T7, DD-primary, N=500 incl COVID, collapse=0 hard floor.

If a clean SHIP → continue to `/ship-version` (scoring) or `/ship-portfolio`
(portfolio). If FLAG → still requires a named post-ship watch metric and
(unless it's a Stage 1-N neutrality-track ship) a downstream Stage 2/3
confirmation before it's ship-safe. If BLOCK → back to Phase 1/4 to refine,
or Phase 7 to close it out as a null.

## Phase 7 — Closeout (part of the task, not optional cleanup)

Every experiment gets classified before you walk away:

1. **Classify**: shipped / null-rejected / still active.
2. **Finish `FINDINGS.md`** with the final verdict (Phase 5 format).
3. **Merge the outcome to `main`.**
   - **Shipped**: the normal ship-version/ship-portfolio commit sequence
     already lands the code; make sure the worktree branch is merged or its
     useful artifacts are preserved, then remove the worktree.
   - **Null/rejected**: this is a **docs-only** merge — no scoring/config
     code should reach `main`. If the worktree branch has no code worth
     keeping reachable, an `ours` merge (or simply not merging the branch,
     just committing `FINDINGS.md` + docs updates directly on `main`) avoids
     replaying stale `database/utils/scoring.py` / `strategy_config.py` /
     `ALGORITHM_VERSION` changes into history.
4. **Update the ledgers in the same turn**:
   - `alpha_mining/NEW_LEADS.md` — move the lead to Resolved/Shipped or the
     Do-Not-Retry Traps section with the blocker + artifact path. Follow the
     file's own `## Agent Update Rule` (rank movement, stage, why, evidence
     artifacts, next experiment, ship gates/stop rules).
   - `.claude/docs/known-issues.md` `## WHAT NOT TO DO` — for a strong null,
     append a rule in house style (see the file for the exact voice: bold
     one-line claim, date, experiment path, then the evidence in 2-4
     sentences, then what a valid retry would require).
   - `alpha_mining/MISS_CANDIDATES.md` if the experiment was miss-led.
5. **Remove the worktree** once merged/documented:
   ```bash
   cd C:/Development/Trader
   git worktree remove ../Trader-exp-<name>
   ```
   If a candidate should stay inspectable but must never re-enter `main`
   (an interesting-but-rejected scoring variant), keep the branch reachable
   via an `ours` merge rather than deleting it outright, and still remove the
   worktree directory.
6. **`gitnexus_detect_changes()` before any commit** — verify the diff only
   touches the expected FINDINGS/docs/ledger files. Do not run
   `npx gitnexus analyze` after a local commit or merge; that's post-push
   maintenance only (see CLAUDE.md's loop-break rule).

An abandoned worktree with uncommitted scoring edits and no FINDINGS.md is
the thing this phase exists to prevent — closeout is part of the experiment,
not a separate chore.

## Evidence / see also

- `.claude/docs/process.md` — doc-update timing, git workflow for
  algorithm-refinement worktrees, compute-resource heuristics, the
  gradient-over-threshold ethos.
- `.claude/docs/known-issues.md` `## WHAT NOT TO DO` + `.claude/docs/known-issues-archive.md`
  — the null registry.
- `experiments/dampener_ablation_v72/FINDINGS.md` and
  `experiments/concentration_2x/FINDINGS.md` — full style exemplars (a
  scoring-stage ablation and a portfolio-stage sweep, respectively).
- `experiments/_holdout.py` — holdout API; `strategy_config.py` for the live
  `CALIBRATION_CUTOFF_DATE`.
- `/ship-gates` — the W1-W6 / B1-B5 / T1-T7 gate math.
- `/ship-version`, `/ship-portfolio` — what happens after a clean SHIP.
- `/queue-ops` — how to submit the sweep instead of running it raw.
- `/data-ops` — recalculate, rebuild-parquets, bulk_cache pattern in depth.
- `/find-and-ship-alpha` — a fully worked end-to-end Stage 1 loop built on
  top of this lifecycle.

## Self-update

If you hit a trap this skill missed, append it to GUARDS above and to
`.claude/docs/traps.md` in the same session.
