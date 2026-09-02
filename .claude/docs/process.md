# Process — Working in This Codebase

Meta-process: how to update docs without blocking, run experiments without polluting production,
use compute, and the ethos behind mechanism design.

**Load this BEFORE any work.** Other sub-docs are topic-specific; this one shapes how work is done.

---

## Restarting the dev server — run it BACKGROUNDED, or it hangs

trader-api (`:5000`) caches imported modules — after editing `api.py`, `strategy_config.py`,
`backtest_cascade.py`, `portfolio_param_manifest.py`, or anything api.py imports, restart it for
the change to serve. (Dev frontend `:3000` hot-reloads `src/**`, no restart needed.)

**The trap:** `server.bat restart trader-api` (`C:\Development\server.ps1`) **hangs forever when an
agent runs it in the foreground.** The controller launches each service as a hidden persistent
child via `Start-Process -RedirectStandardOutput/-Error -PassThru` (≈line 396); that child holds
the agent shell's stdout pipe open, so a foreground tool never sees EOF and blocks until timeout. A
human's interactive terminal has a console stdout (not a captured pipe) so it returns fine — hence
it works for a human, hangs for an agent. Command form (`restart trader-api` vs `restart -Service
trader-api`) is irrelevant.

**Rule for agents:**
- Run via **PowerShell tool with `run_in_background: true`**: `& C:\Development\server.bat restart -Service trader-api`. The harness detaches and re-invokes on controller exit — only reliable path.
- **Never** run foreground/blocking.
- **Never** use Bash's `cmd.exe /c "server.bat ..."` — in Git-Bash it prints the cmd banner and no-ops (exit 0 in ~0s, no restart), silently leaving stale code loaded.
- Verify after: `GET http://127.0.0.1:5000/health` → 200.

Full root-cause + optional controller fix: auto-memory `feedback_server_restart.md`.

---

## Doc-update timing — avoid the blocker trap

`CLAUDE.md`/`.claude/docs/*.md` edits trigger a permission prompt every change (Cursor extension
quirk); approvals can take hours. Mid-task doc updates stall any long-running task that follows.

**Default rule: update docs ONLY after experimentation/ship is complete, OR immediately before
queueing a long-running task that won't depend on the doc edit landing.**

| Update docs NOW | Defer the doc update |
|---|---|
| Ship is complete (commit + recalc + assess all done) | Mid-experiment, before MC validation/recalc/assess |
| About to queue a >15 min task and the edit can wait in parallel | Next step depends on the doc edit being approved first |
| User explicitly asked for the update | "Cleanup" updates that don't change behavior |

**Patterns:** bundle doc edits at end of ship (one turn: CLAUDE.md + known-issues.md + version-history.md + sub-doc, one permission prompt). Queue-then-update: launch a long task first (`trader recalculate --force --full` ~25min, sweeps ~hours), THEN edit docs while it runs — neither blocks the other (market-hours scoring ships still follow [deploy.md](deploy.md): today rows first, 5y force, 10y full deferred off-hours). Never edit docs as the final step before a long task — if the user is away, the task never starts.

---

## Git workflow — task-type defaults

Routine non-scoring work stays on the current checkout (normally `main`); push each agent's scoped
work from there unless the user asks for checkout isolation.

Scoring-algorithm experiments are different: use an isolated algorithm-refinement branch/worktree
by default, starting from the production/staging scoring baseline, becoming that agent's own
staging ecosystem for scoring edits, sims, sweeps, caches, candidate snapshots.

Agents can research on a stale base if another agent ships first — merge/pull latest before
elevation, resolve conflicts, snapshot with `trader algorithm snapshot-staging`, then version bump
+ recalc + validation + silo promotion.

Worktree closeout is part of the task. Before leaving a worktree, classify it shipped, null/rejected,
or still active. Shipped/null: merge or document the outcome on `main`, push, delete merged remote
heads, remove clean contained worktrees. Abandoned-but-inspectable candidate: absorb the branch with
an `ours` merge into `main` after preserving useful artifacts — keeps history reachable without
replaying stale `scoring.py`/`strategy_config.py`/`ALGORITHM_VERSION` changes. Dirty worktrees must
be committed intentionally or have a salvage patch recorded before deletion.

GitNexus refreshes are post-push maintenance. Run `gitnexus_detect_changes()` before committing, but
don't run `npx gitnexus analyze` after a local commit/merge/stale-index warning/pre-push check — only
after a push (the signal the user is ready to ship), unless explicitly asked or a stale index blocks
the current query. Preserve embeddings with `--embeddings` when `.gitnexus/meta.json` shows existing
ones. **Stop condition:** a post-push `analyze` may update only generated count lines in
AGENTS.md/CLAUDE.md or index metadata — don't commit/amend analyzer-only changes, and don't re-run a
full analyze just because `gitnexus status` points at the previous commit (self-referential loop).
Report post-push index status and stop.

---

## CLAUDE.md growth policy (re-stated)

CLAUDE.md is the navigational index. Root file ≤200 lines. Detail lives in `.claude/docs/`.

| Add to CLAUDE.md | Add to a sub-doc |
|---|---|
| New file/CLI command/API endpoint | Sweep results, MC tables, mechanism descriptions |
| New invariant that applies to ALL tasks | Phase logs, version history, ship procedures |
| New sub-doc → one-line pointer | Audit checklists, post-ship procedures |

**Never add to CLAUDE.md:** phase logs, MC result tables, commit/version history, sweep findings, mechanism descriptions, ship procedures, post-ship checklists.

---

## Stale-data refresh — files that need updating per ship

Drift compounds silently. After every ship, walk this list:

| File | What gets stale | Refresh trigger |
|---|---|---|
| `known-issues.md` CURRENT SHIP STATE table | Active version + cascade + TP/SL params | Any version bump or portfolio change |
| `known-issues.md` CLOSED — SHIPPED timeline | New ship row | Each ship |
| `known-issues.md` CLOSED — NULL RESULTS / REVERTED | New null/revert lessons | Each failed experiment |
| `known-issues.md` WHAT NOT TO DO | New "do not retry" rule | Each null with strong evidence |
| `alpha_mining/NEW_LEADS.md` | Ranked future alpha / capacity backlog | Any run finding/reranking/retiring a lead |
| `alpha_mining/MISS_CANDIDATES.md` | Ranked harmful miss-candidate ledger | Any miss-led scoring run that resolves/reranks/adds a candidate |
| `version-history.md` | New version section + commit list | Each ALGORITHM_VERSION bump |
| `trading-strategy.md` Authoritative ship state | Param values | Any portfolio change |
| `scoring-algorithm.md` mechanism sections | New score-stage mechanism | Each scoring change |
| `assessment-backtest.md` ship gate criteria | Gate calibration drift | Major MC architecture change |
| Auto-memory `MEMORY.md` index | New shipped mechanism/null result | Each ship or significant null |
| `mechanism_registry.REGISTRY` | New portfolio mechanism/DTE status/retirement | Each portfolio ship — Step 0 in [deploy.md](deploy.md) |

**Drift check before claiming "shipped":** open CURRENT SHIP STATE — if a value disagrees with `strategy_config.py`, it isn't landed. Run the two-test ship sentinel:
```bash
python tests/test_strategy_config_drift.py    # ~1s — value drift
python tests/test_mechanism_registry.py       # <1s — registry vs engine consistency
python experiments/_dte_audit/audit.py        # ~5s — structural drift (e.g. a new mc15 constant with no registry/pairs entry); re-run after every portfolio ship
```
None of the three catch tooltips, docstrings, or fallback constants — those still need the manual scan in [deploy.md](deploy.md)'s post-ship audit checklist.

**Pre-commit hook installed:** [tools/git-hooks/pre-commit](../../tools/git-hooks/pre-commit) runs drift-guard + registry test on every commit, refuses on failure. Install via `bash tools/git-hooks/install.sh`. Do NOT bypass with `--no-verify` for a portfolio/scoring ship — fix the divergence or update the registry.

**Alpha ledgers:** update [`alpha_mining/NEW_LEADS.md`](../../alpha_mining/NEW_LEADS.md) when an experiment reveals a future research path; also [`alpha_mining/MISS_CANDIDATES.md`](../../alpha_mining/MISS_CANDIDATES.md) for miss-led score archaeology. Rank by expected WR15/useful-N potential, then practical portfolio/DD value. Merge into existing leads instead of duplicating; move exhausted ideas into the resolved/null-trap section with the blocker.

---

## Scoring vs portfolio experiments — production edits need a ship reason

**Default: scoring hypotheses belong in isolated staging worktrees, not the serving checkout** (supersedes the old "always work on main"/"never touch scoring" process). In an algorithm-refinement worktree, `database/utils/scoring.py`, `strategy_config.SCORING`, scoring helpers, simulator hooks, and experiment scripts are all editable staging code.

That freedom doesn't make the shared DB a sandbox. Plain `trader update`/`trader recalculate` write shared production `Score` rows keyed by the active `AlgorithmVersion`. Default to simulations/sweeps/parquet-cache/candidate snapshots; run production score writers only when explicitly elevating/shipping from the designated checkout. `trader recalculate` belongs to deploy/elevate/ship after the `ALGORITHM_VERSION` bump, not the normal refinement loop.

**Version-row isolation rule:** never use a staging/candidate checkout to write score rows for a prior shipped version — e.g. don't include `v57` in `trader recalculate --score-versions ...` while the checkout has potential `v59` code (shared component/context builders can still come from the candidate checkout and overwrite the older version's rows with non-identical inputs). Old-version repair/replay must run from that version's own pointer checkout/runtime silo, recalculating only the target version, after logging checkout commit, `ALGORITHM_VERSION`, DB version id, date window.

A production scoring edit is reasonable only when you can state the stage/gate, the evidence artifact, the generic mechanism name, expected touched files, whether `ALGORITHM_VERSION` changes, the rollback/disable surface, and the target silo key.

Use judgment: a legitimate scoring ship that began as a symbol-level miss investigation is fine; the motivating ticker becoming a production mechanism name is not — name mechanisms for the modeled behavior (e.g. sector breadth participation), not the case study. Ticker-specific production names are a review trigger, not an absolute ban — `tests/test_strategy_config_drift.py` scans for the known failure class (e.g. `POET_*`); if genuinely intentional, add `# symbol-specific-surface-ok: <reason>`, else rename to the mechanism or keep it in an experiment bucket.

**Acceptable patterns:** (1) Isolated algorithm-refinement worktree — default; edit real scoring path, sim/sweep in the sandbox, snapshot with `trader algorithm snapshot-staging`. (2) Staging-native simulation/sweep runners — preferred for ship candidates: `trader simulate`, `ScoreSimulator(scoring_fn=None)`, or an `experiments/<candidate>/` runner importing checkout scoring code — the validated formula should live in staging scoring, not only the experiment script. (3) `ScoreSimulator(scoring_fn=variant_fn)` override — quick probes; migrate into staging before W1-W6. (4) Runtime monkey-patch with `try/finally` teardown — quick probes touching an internal helper not exposed via `scoring_fn` (e.g. `calculate_weekly_adjustment`). Reference: `experiments/weekly_avwap/phase_i_wadj_blend_sweep.py`.

Staging-native ≠ DB-writing — refinement-worktree sims/sweeps read shared data/caches but write only local artifacts under `.cache/` or the experiment folder. `trader update`/`trader recalculate` remain ship-only.

**Why this matters:** hypotheses fail more often than succeed (Phase I 2026-05-08, v42 rolling weekly, v40 SVD, v22-v24 reverts). A failed env-gate or case-study constant left in shipped code creates strategy ambiguity, breaks bit-identical reproducibility, leaks into auto-memory/CLAUDE.md, slows future audits. The silo workflow preserves freedom to edit staging code while keeping shipped snapshots explicit.

**Full procedures live in topic docs (three-stage framework, shipped 2026-05-08):**

| Need | Doc |
|---|---|
| Stage 1 — Scoring Calibration Gate (W1-W6, WR15-primary, barrier-independent) | [assessment-backtest.md](assessment-backtest.md) "Stage 1: Scoring Calibration Gate" |
| Stage 2 — Barrier Optimization Gate (B1-B5, TP/SL/HOLD/PREMIUM_MULT) | [assessment-backtest.md](assessment-backtest.md) "Stage 2: Barrier Optimization Gate" |
| Stage 3 — Tertiary Portfolio Gate (T1-T7, DD primary) | [assessment-backtest.md](assessment-backtest.md) "Stage 3: Tertiary Portfolio Gate" |
| End-to-end ship procedure | [deploy.md](deploy.md) "Shipping a Scoring Change" / "Shipping a Portfolio Change" |
| New portfolio mechanism wiring | [deploy.md](deploy.md) "Adding a NEW Portfolio Mechanism" — 13-consumer checklist |
| Stage 1 hypothesis-formation loop | [assessment-backtest.md](assessment-backtest.md) "Iterative Score Improvement Workflow (Stage 1 anchor)" |

Sector clustering controls belong in Stage 3 as risk-weighted exposure waves — don't feed sector concentration into Stage 1 WR15 scoring unless it independently proves directional WR15 signal value.

**Old gate names retired:** H1-H6→W1-W6 (Stage 1), P1-P6→T1-T7 (Stage 3), new B1-B5 (Stage 2). Translate references in legacy commit messages/FINDINGS.md/experiment scripts.

---

## Compute resource maximization

Sweeps and recalcs are compute-bound — treat the local box as a heterogeneous compute fabric.

| Resource | Default usage pattern |
|---|---|
| **CPU cores** | Multi-process (`multiprocessing.Pool`/`ProcessPoolExecutor`) — most experiments are embarrassingly parallel over (sym, date). Default `cpu_count()-1`. On Windows MP-spawn, pass params via env vars at module load — module-global patches don't propagate to workers (v32_optim lesson). |
| **Storage** | Cache feature builds as parquet (`df.write_parquet()`); re-running the same sweep should be instant. `database.bulk_cache.materialize_polars()` handles cache-or-build with freshness checks. Canonical: `experiments/mcap_dampener/build_features.py`. |
| **RAM** | Bulk-load OHLCV+Indicator ONCE per run. `simulator.StockContext` is canonical — bulk-load, iterate variants in-memory, zero DB roundtrips in the inner loop. |
| **Barrier cache** | `barrier_outcomes` is dual-store: canonical SQLite `.cache/barrier_outcomes.db` (writer) + DuckDB read mirror `.cache/barrier_outcomes.duckdb` (~50-100× faster on bulk JOINs). `peaks_to_swing_results()` auto-routes to DuckDB when fresh, falls back to SQLite. Mirror auto-rebuilds at end of `refresh_recent`/`backfill_sets`; manual: `python -m database.barrier_cache rebuild-duck`. Override `BARRIER_CACHE_BACKEND=sqlite`. Bypass only when K/M genuinely differ from cached barriers. |
| **DuckDB ad-hoc** | `duckdb.read_parquet(str(path))` for JOIN/filter/aggregate over a cached parquet rather than pandas — same vectorized engine as the barrier_cache mirror. Polars also works, both zero-copy via Arrow. |
| **Numba JIT** | `database/barrier_walk_numba.py` — ~940× speedup on barrier walks vs pure Python. Apply to any new per-bar walk on >100k rows. |
| **GPU** | Only when a profile shows >50% runtime in a numerical inner loop CuPy/Numba CUDA can replace — most scoring work is branchy per-row logic CPU vectorization handles fine. |

**Default heuristic:** >2 min serial → parallelize. Touches the same 10y price history more than once → cache to parquet. Per-bar arithmetic on >100k rows → Numba.

**Windows redirected-output rule:** set `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` in every Python background runner (`trader.py`/`assess_scores.py` reconfigure stdout defensively, but detached scripts still need the env vars or Unicode banners crash under cp1252).

### N-ladder doctrine (ratified 2026-07-29 — the 9950X3D amendment)

From [gameplan-2026H2-DRAFT.md](gameplan-2026H2-DRAFT.md) §4 (P-1..P-6). Applies to all MC-gated work:

- **Ladder: B=N=300 screen** (old B=100 retired — 2-3× MC throughput makes 300 paths cost what 100 did, kills the "N=100 winner reverses at N=300" churn class), **C=N=500 drill**, **D=N=500-1000 ship gate** (N=500 remains the minimum ship standard for historical-gate comparability; run 1000 when wall-clock permits), **E=N=2000 certificate** (new, confirmatory-only).
- **E-tier is a certificate, never a search surface.** Runs only: (i) collapse-tail resolution (collapse-sensitive param ships; annual live-profile re-cert), (ii) marginal T-gate calls (|ΔDD| inside the N=500 noise band on ≥3 windows), (iii) inaugural/post-migration baselines. **Knob search never runs above C (N=500)** — more paths on a searched surface fits seed structure, not truth.
- **Collapse arithmetic (rule of three):** 0/500 ⇒ ≤0.6% (95%), 0/1000 ⇒ ≤0.3%, 0/2000 ⇒ ≤0.15% per (window×arm) cell. Ship language uses the bound, not just the zero.
- **Window doctrine unchanged:** 5y lock primary, 22-now confirmation-only, 2020_crash in EVERY phase. Deep-screen windows attach by default at D/E tiers; SCREEN-not-GATE unchanged (never calibrate on survivor-only windows). 10y row mandatory-reported at D (was intermittent).
- **Noise floors are measured, not inherited:** the P1.E empirical dispersion table (per N-tier, per window, current box — `experiments/newbox_rebaseline/`) supersedes the Phase-v32-era figures once landed.
- **Unchanged (restated so this can't be read as loosening):** DD-primary/compound-sanity, collapse=0 hard floor, paired seeds, staged ladder discipline (no stage-skipping on a strong cohort z), queue-everything, holdout lock, FLAG-teeth, the W/B/T gate structure, and a per-trade cohort z is never itself a portfolio result.

**MC job sizing on the 32-thread box:** `--cpu 8-12` per queued MC job (two heavy jobs + `trader update` headroom coexist under queue admission — raw parallelism outside the queue is still banned).

### On-demand bulk cache pattern (for new sweeps)

For any new experiment pulling bulk MySQL data (PriceHistory, Indicator, Score) run against more than one sweep variant: build a per-experiment parquet first, sweep against the parquet. Helper: `database.bulk_cache`.

```python
# experiments/my_thing/build_features.py
from database.bulk_cache import materialize_polars, chunked_query_by_year
import polars as pl

def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id={vid} AND date BETWEEN '{y_start}' AND '{y_end}' "
        "AND overall >= 70",
        start_year=2016, end_year=2027, vid=46,
    )
    return pl.DataFrame(
        {'symbol': [r[0] for r in rows],
         'date':   [r[1].isoformat() if hasattr(r[1], 'isoformat') else r[1] for r in rows],
         'overall':[int(r[2]) for r in rows]}
    )

# Call from each sweep entry point — first run builds, subsequent runs hit the cache.
parquet_path = materialize_polars('my_thing_peaks_v46_10y', _build)
```
Sweep scripts then read via `pl.read_parquet(parquet_path)` or `duckdb.read_parquet(str(parquet_path))`.

**Why on-demand, not a global nightly snapshot:** different experiments need different slices; a global snapshot costs minutes/night for data most experiments don't touch and introduces during-day staleness on the production loop. Per-experiment caches are smaller, never block the cron, make the data dependency explicit. (Full rationale: Q1 2026 DuckDB spike notes in `version-history.md`.)

**Naming convention:** encode (experiment, version, lookback) so a version bump invalidates by naming, e.g. `mcap_v43_5y`, `weekly_avwap_v44_10y`, `score_velocity_v39_3650`. Don't reuse names across versions.

**Holdout enforcement:** any experiment under the calibration holdout lock (`CALIBRATION_CUTOFF_DATE` in `strategy_config.py`) MUST call `assert_no_holdout_leak(df, context='...')` from `experiments/_holdout` after `pl.read_parquet()` and BEFORE aggregates. Materialization-time enforcement preferred: `pre_cutoff_filter()` inside `_build()`. See [deploy.md](deploy.md) "Calibration Holdout Lock".

---

### Seeing a sweep instead of summarizing it — `tools/dsr.py`

A median hides whether one window carried it, or whether the good number averages two different
behaviours. `tools/dsr.py` renders any long-format series set as a single static SVG of small
multiples — seed-level structure inspectable instead of inferred. Database-Side Rendering: DuckDB
does scaling/projection/polyline/ribbon-polygon/per-cell markup via `string_agg`; Python only writes
the SVG shell. No notebook/server/JS/matplotlib — one file, open in a browser, commit next to
`FINDINGS.md`.

```bash
MC_RESULTS_JSON=.cache/dsr/run.json MC_RETURN_PATHS=1 MC_EMIT_CURVE=1 py -3.11 monte_carlo.py
py -3.11 tools/dsr.py mc .cache/dsr/run.json -o .cache/dsr/sheet.svg --mode both
```
`MC_RETURN_PATHS=1` and `MC_EMIT_CURVE=1` are both required — `eq_weeklies` (per-seed weekly equity curve) is absent otherwise and the tool refuses rather than draw something else.

Any long-format parquet works: `py -3.11 tools/dsr.py parquet .cache/foo.parquet --facet window --series seed --x bar --y equity -o sheet.svg`

| Knob | Meaning |
|---|---|
| `--mode overlay` | one cell/facet: p5-p95/p25-p75 ribbons, median bold, individual paths over the top |
| `--mode grid` | one tiny cell per series, sorted worst-final first — the contact sheet |
| `--scale facet` (default) | seeds comparable within a window |
| `--scale global` | all windows on one axis — for comparing windows |
| `--scale series` | each cell autoscaled — reveals shape, hides magnitude (grid only) |
| `--linear` | default y is log — equity compounds; linear flattens every pre-2020 window into a line |

Two deliberate honesty properties: **collapse paths are never sampled away** (they're the pathology being hunted — overlay draws all of them, strides only wins/losses); **quantile ribbons compute over every seed** regardless of how many individual paths are drawn. Collapse classification uses the engine's own `COLLAPSE_THRESHOLD` from `strategy_config`, so colouring can't disagree with the summary table's `p_coll`. Sampling and `--max-cells` drops print, never silent.

**Not evidence, a viewing tool.** Nothing here changes an N floor, a gate, or a verdict — a spotted shape is a hypothesis to go measure, exactly as post-hoc as any other eyeballed pattern.

---

## Agent/model tiering — token economy

**Role model (user directive 2026-07-16): Fable is the ARCHITECT and STRATEGIST, not the implementer.** Fable designs (hypothesis selection, pre-registrations, builder briefs, wiring specs), decides (verdicts, ship/stage), and audits; implementation is delegated to cheaper agents from a locked spec. Compute goes through the queue; tokens go through tiered agents.

**Delegate to implementer agents when the subtask is:**
- **Harness/script builds from a locked spec** — orchestrator writes PREREGISTRATION.md, a Sonnet builder delivers the ~1,000-line self-testing mine. Precedents: `trend_ma_lattice` (~440k subagent tokens, ~39min), `peak_fakeout` (~580k, ~35min), both first-full-run green after smoke.
- **Broad read-only searches/many-file triage** where only the conclusion returns (Explore agent) — not code review or audits.
- **Mechanical hydration** — doc tables, boilerplate wiring drafts, format conversions — from data the orchestrator supplies.

**Fable-tier (never delegated):** hypothesis selection + null-check triage, pre-registration design, audit of the load-bearing statistical core (point-in-time loops, clustering/gating machinery), verdict rendering against a prereg, ship/stage decisions, user-facing conclusions. For engine wiring: a builder may draft from Fable's spec (e.g. the 13-consumer checklist), but Fable audits every consumer and runs the gates.

**Rules when offloading (research SKILL G47):**
1. **Forward the relevant trap registry into the brief verbatim** (G5 cp1252/ASCII-only stdout, G7 `infer_schema_length=None`, NaN-is-not-null choke point, finX finite-masking, peewee `.tuples()` batching, `experiments/_holdout` import). Agents re-discover traps expensively.
2. **Briefs must be self-contained** — exact paths, schemas, pinned base rates for self-tests, timeboxes, what NOT to read (e.g. prior SUMMARY files that would unblind a prereg).
3. **Audit the core, not the bulk** — review the PIT loop, gating stats, and SUMMARY; don't re-read the whole artifact line by line.
4. **Don't offload small judgment edits** — a 30-line doc change costs more via an agent than direct. Offload is for bulk-and-mechanical, not small-and-subtle.

---

## Ethos — gradient laws over threshold gates

**The market moves in wave-like oscillation**, producing gradient-style empirical laws, not crisp thresholds. Shipped examples: log-ramp dampeners (PCD, PESS), power-law magnitude scalers (MCD `mcap_factor^0.70×score_factor^1.50`), tanh-saturating amplifiers, asymmetric-K dampeners (v44 ICH `K=0.359×score_norm^2.68`) — each originally proposed as a hard threshold, improved by replacing it with a gradient.

**Design preference, in order:**
1. **Smooth gradient magnifier** — log, quadratic, power-law, tanh-saturating; continuous, monotonic, naturally bounded. Preferred for any new mechanism.
2. **Linear ramp with soft floor/ceiling** — acceptable when the empirical relationship is genuinely linear within a band; document the band + saturation rationale.
3. **Hard threshold gate** — concession only, when (a) the evidence is structurally discrete (e.g. v27 WCF: cohort z=+10.1 concentrated entirely at `wadj<0`, gradient versions tested and lost), or (b) a gradient form was tested and lost Stage 1 W1-W6 or Stage 3 T1-T7.

**Concrete gradient wins shipped:**

| Mechanism | Replaces | With |
|---|---|---|
| v44 ICH | binary "kijun_pct<0 → drop tier" | `K_eff = 0.359×score_norm^2.68` continuous |
| v43 MCD | binary "mcap<10B → −5 score" | dual power-law `mcap_factor^0.70×score_factor^1.50` |
| v37 PCD | binary "ret_10d<−10% → reject" | σ-normalized cutoff (vol-fair across stocks) |
| H3 DD soft-band | binary "DD≥0.60 → halt calls" | linear ramp 1.0→0.50 over [0.40,0.60] |
| v28 EARN_BOOST | binary "near earnings → +N points" | `proximity×strength` log-smoothed multiplier |

**Tech-debt candidates (not yet replaced):** `PUT_MACD_GATE=45` (binary cliff) — symmetric/directional gradient widening tested 2026-05-05, NULL across 24 variants; cliff is the current empirical optimum, re-test only with a different mechanism family. `EARN_BOOST_PUT_ADMIT=False` — boundary admission lost Phase 3A.2; smooth admission ramp untested. `DD_CIRCUIT_BREAKER` (retired 2026-05-11) — H3 soft-band is the only shipped DD allocation modifier; the 2026-05-12 sector-ETF breadth/POET rescue was tested and rejected, so any replacement needs a different mechanism family. Discrete `BUY_THRESHOLDS=[95,90,85,80,75,70]` cascade tiers — a continuous `alloc_pct=f(score)` curve is conceptually cleaner but never tested (cascade engine is tier-indexed throughout MC+backtest; high-effort refactor, modest expected EV).

**Concession criteria:** a hard gate is acceptable when (a) a gradient replacement was tested and failed Stage 1 W1-W6 or Stage 3 T1-T7, or (b) evidence concentrates at a single discrete cohort z>+5 AND next-cohort lift is within ±0.5pp baseline. Document the concession in known-issues.md "WHAT NOT TO DO" with the failed-gradient evidence.

**Hypothesis-formation rule:** default the first design draft to a gradient form; fall back to a threshold only if the gradient doesn't fit the empirical cohort shape (single-feature z>+5 concentration is the usual sign).

---

## Risk-budget ethos — drawdown tolerance scales with portfolio maturity

**DD is a budget, not a universal gate.** The "DD-primary, compound-secondary" Stage-3 rule ([assessment-backtest.md](assessment-backtest.md)) is the mature/large-portfolio view — correct when an 80% drawdown is unrecoverable. It's one view, not the only one — the right DD budget depends on portfolio maturity and cash-regeneration capacity; ask which regime the user is in before treating DD as primary.

**Early-stage, small book + regenerating income → high RECOVERABLE drawdown is a fair price for explosive growth.** A small book (≈$40-100k) with ongoing income has a bounded absolute-dollar DD loss, backstopped by human capital. Compounding off a small base benefits more from aggression than DD-shaving (the "young investors hold more equity" principle, generalized to leverage). As the book grows, the same % DD becomes catastrophic — so the trader migrates Apex → Core → Sentinel over the portfolio's life.

**Portfolio profiles are points on the return-vs-DD frontier (all at collapse=0):**

| Profile | Phase | Objective | DD budget |
|---|---|---|---|
| **Apex** (current default) | early/explosive buildout | max cost-adjusted return | high recoverable (80-90%) accepted; DD reported, not constrained |
| **Core** | mid | return under a moderate DD cap | moderate |
| **Sentinel** | mature/large | capital preservation (DD-primary, the legacy rule) | low (~30%) |

The Apex aggression lever is **threshold density (75+) + SL/TP**, NOT sizing up — v70 10y sweeps showed over-deployment HURTS: exposure peaks ~50% of the practical base and declines above it (10y MedRet 50%→+15,323% > 65%→+12,848% > 100%/off→+6,365%) — bigger sizing deepens drawdowns, less capital survives to compound (capital-velocity law). Profiles differentiate on threshold + exposure-as-a-dial (Apex 75+/~50%, Core ~40%, Sentinel 85+/lower) over the SAME SL-70/TP30 HOLD core, not on cranking allocation. Lowering the threshold below 75+ is also wrong — 70+ dilutes per-trade quality, adds spread cost, crosses into collapse at realistic spread. (Corrects an earlier draft that called allocation "the lever" — falsified by the data.)

**THE non-negotiable, even for Apex — collapse ≠ drawdown.** High recoverable DD (85% down and back up) is the Apex budget; a collapse (account→~0, margin-call/zeroed premium) is unrecoverable at any size — nothing survives to compound, human capital can't refill a wiped leveraged account. Collapse-rate=0 is a hard floor for every profile including Apex. The 100%-collapse configs found 2026-06 (puts-on HOLD, CUT-at-70+, SL=100% calls+puts) are RUIN, not aggression — forbidden regardless of risk appetite.

**Apex objective, stated precisely:** `maximize cost-adjusted 10y MedRet s.t. collapse_rate==0 across ALL windows incl 2020-COVID`, DD reported as a budget. Returns are always cost-adjusted under the ASYMMETRIC execution-cost canon (2026-06-02, see trading-strategy.md "Execution Cost Model"): mid-entry + limit-TP free, only forced exits pay the half-spread (−0.015) — not a flat round-trip. A frictionless number is the same self-deception as a look-ahead one, but so is over-taxing the ~85% of trades that win on a limit. (Context: the 2026-06 honest-v70 HOLD build's converged Apex call config — 75+ · SL-70 · TP30 · ~50% exposure · HOLD-to-day-15 — returns +15,323% at 83% DD, 0% collapse, survives COVID (N=150, full crash-inclusive 10y). A leveraged-momentum sleeve, not alpha — Apex tunes threshold/SL/TP/exposure for max survivable return on that substrate.)
