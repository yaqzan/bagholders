# Trader - Codex Project Context

Codex import of the Claude project knowledge base. Keep this as a compact
navigational index. Put details in `.claude/docs/`, not here.

Authoritative sources:

1. `CLAUDE.md` for current project context.
2. `.claude/docs/process.md` for how to work in this repo. Load it first for
   substantive tasks.
3. Topic-specific `.claude/docs/*.md` files.
4. `CLAUDE_PROJECT_INSTRUCTIONS.md` only as historical background. It conflicts
   with current scoring direction, so do not rely on it for trade direction.

## Critical Invariants

- HIGH score = CALL opportunity. LOW score = PUT opportunity. Never invert.
- Scores are 0-100 daily: `>=75` bullish/call, `<=25` bearish/put, `50` neutral.
- Assessment wins: HIGH bucket wins when stock rises `K*sigma` before dropping
  `M*sigma`; LOW bucket wins when stock falls `K*sigma` before rising `M*sigma`.
- `strategy_config.py` is the single source of truth for shipped strategy params.
- Run `tests/test_strategy_config_drift.py` after strategy or portfolio config
  changes.
- Scoring-algorithm experiment/refinement tasks default to an isolated
  algorithm-refinement worktree. In that sandbox, scoring code is staging code
  and may be edited directly, then snapshotted/elevated through
  `trader algorithm`.
- Simulation sweeps in scoring worktrees should be staging-native by default:
  run `trader simulate`, `ScoreSimulator(scoring_fn=None)`, or experiment
  runners that import the checkout scoring code. Use external `scoring_fn`
  variants or monkey patches only for quick prototypes; migrate winners into
  staging scoring before validation.
- Calibration sweeps, miss-ledger mining scripts, and lift-table rebuilds must
  enforce `CALIBRATION_CUTOFF_DATE = "2026-05-15"` via `experiments._holdout`.
- Prefer smooth gradient mechanisms over hard thresholds unless evidence proves
  a threshold is optimal.
- `trader assess` uses positional lookback only. There is no `--days` flag.
- Never use a staging/candidate checkout to write rows for prior shipped
  versions. In particular, do not run `trader recalculate --score-versions`
  with old versions such as `v57` while testing or shipping a potential `v59`;
  shared component/context builders can come from the candidate checkout and
  overwrite old-version rows under the old `version_id`. Repair old versions
  only from that version's pointer checkout/runtime silo, and prefer research
  packs/existing rows for read-only comparisons.

## Working Process

Read `.claude/docs/process.md` before project work. It covers doc timing,
experiment hygiene, compute/caching patterns, and mechanism design ethos.

Long-running work should use a Codex-aware background pattern by default:
timestamped run directory, launch metadata, unbuffered `run.log`,
`status.json`, `pid.txt`, and terminal `done.json` / `failed.json`. Add a
heartbeat monitor when the user wants completion reporting.

When launching or reporting a long-running task that has a `run.log`, include
the log path in its own fenced code block during processing so the user can copy
it quickly and open it in a side window for live progress tracking. Keep
`run.log` append-only as the canonical log. For easier live viewing, add a
monitor/wrapper-owned `run.recent.log` sidecar when practical: refresh it on a
short time cadence with a capped newest-first window or latest-line snapshot,
using atomic overwrite/replace rather than prepending to `run.log`.

Set `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` in Python background runners.
The Trader CLI also configures UTF-8 streams defensively, but launchers should
still set the environment because redirected Windows output otherwise defaults
to a legacy code page and can crash on Unicode progress banners.

**Scoring recalc exception:** during market hours after an `ALGORITHM_VERSION`
bump, a background runner must preserve the staged sequence from
`.claude/docs/deploy.md`: first `trader recalculate 1d` (or
`trader recalculate --force 1d` if partial rows already exist), then
`trader recalculate --force` for the 5y dashboard backfill, and only run
`trader recalculate --force --full` off-hours or after the user explicitly
accepts that today's dashboard rows will wait. Do not collapse this into one
background `--force --full` command.

Task-type git workflow:

- Routine non-scoring work (docs, UI, API fixes, small ops patches) can stay on
  the current checkout, normally `main`, unless the user asks for isolation.
- Scoring-algorithm experiments/refinements must start in an isolated
  algorithm-refinement branch/worktree by default. That checkout is staging code:
  agents may edit `database/utils/scoring.py`, `strategy_config.SCORING`, and
  scoring-adjacent files directly while running simulations/sweeps.
- Scoring sweeps should evaluate the staging checkout path, not a detached
  experiment-only formula. Legacy `ScoreSimulator(scoring_fn=...)` and
  monkey-patch patterns remain acceptable for early probes, but any promising
  variant must be moved into staging scoring before W1-W6 validation.
- Worktree agents can continue from a stale base while researching. Before
  elevation, pull/merge latest as needed, snapshot with `trader algorithm
  snapshot-staging`, then follow the normal version bump, recalc, validation, and
  silo promotion flow.
- Worktree closeout is part of the task. Before leaving an isolated worktree,
  classify it as shipped, null/rejected, or still active. For shipped or
  null/rejected work, merge or document the outcome on `main`, push `main`,
  delete merged remote heads, and remove clean contained worktrees. For an
  abandoned scoring candidate that should remain inspectable without changing
  active code, use an `ours` merge into `main` after preserving useful artifacts;
  this keeps history reachable without replaying stale scoring,
  `strategy_config.py`, or `ALGORITHM_VERSION` changes. Dirty worktrees must
  either be committed intentionally, or have a salvage patch/artifact path
  recorded before deletion.
- Do not run shared production score writers (`trader update`, `trader
  recalculate`) from a scoring sandbox against active production version rows
  unless explicitly performing the elevation/ship process from the designated
  ship checkout. Recalculation is part of deploy/elevate/ship after the version
  bump, not the normal refinement loop.

GitNexus index refreshes are post-push maintenance: run
`gitnexus_detect_changes()` before committing, but do not run
`npx gitnexus analyze` after a local commit, merge, stale-index warning, or
pre-push status check. Run `npx gitnexus analyze` only after a successful push,
when explicitly asked, or when stale index data blocks the current GitNexus
query.

If post-push `npx gitnexus analyze` changes only generated GitNexus count lines
in `AGENTS.md` / `CLAUDE.md` or index metadata, do not commit or amend those
analyzer-only changes. Do not re-run analyze solely because a commit or generated
metadata makes the index appear one commit stale; report the post-push status
and stop.

Doc edits can block long-running work in Claude tooling. Default to batching doc
updates after experiments or ships unless the user asks for docs now.

Before declaring a ship done, check stale-data surfaces:

- `.claude/docs/known-issues.md` current ship state and timelines.
- `.claude/docs/version-history.md` version sections and commit lists.
- `.claude/docs/trading-strategy.md` authoritative strategy snapshot.
- `.claude/docs/scoring-algorithm.md` score-stage mechanisms.
- `mechanism_registry.py` portfolio mechanism coverage.

## System Overview

Trader is a directional technical-analysis platform for options trading.

- Backend: Python + Flask + Peewee ORM + MySQL.
- Frontend: React 18 + Tailwind + Chart.js / react-chartjs-2 under `src/`.
- Data source: yfinance daily OHLCV.
- Polling model: roughly 5-10 minutes; not an intraday execution system.

Data flow:

```text
yfinance -> PriceHistory -> Weekly aggregate
         -> Indicators -> Weekly Indicators
         -> MarketBreadth -> MarketRegime
         -> Score -> WeeklyScore -> HistoricPeaks
         -> Flask API -> React dashboard
```

`trader update` scores using the last available `MarketRegime`; after scoring it
delegates current/silo score writes to `score_calculation_service.py`, then
computes fresh regime and atomically patches today's scores via
`reapply_regime_today()`.

## Key Files

- `strategy_config.py`: single source of truth for shipped 30 DTE and 15 DTE params.
- `algorithm_versions/`: tracked scoring/portfolio silos, manifests, and
  per-version docs. Start with `algorithm_versions/VERSION_GUIDE.md`.
  Large artifacts stay under `.cache/algorithm_versions/`.
- `algorithm_versions/research_pack.py`: post-recalc evidence packs for version
  coverage, assessment readiness, WR15 utility, horizon utility, temporal stats,
  and named portfolio stress windows.
- `alpha_mining/`: canonical agent-facing alpha-mining source docs. Start with
  `alpha_mining/README.md`; use `alpha_mining/NEW_LEADS.md` for broad ranked
  leads and `alpha_mining/MISS_CANDIDATES.md` for miss-led score candidates.
- `score_calculation_service.py`: current and optional algorithm-silo score
  calculation service used by `trader update`.
- `tests/test_strategy_config_drift.py`: drift guard.
- `trader.py`: orchestration, score loops, CLI.
- `api.py`: Flask REST API and `/api/strategy/config`.
- `database/models/core.py`: Stock, Score, WeeklyScore, regime/breadth/peaks models.
- `database/models/technical.py`: price and indicator models.
- `assess_scores.py`: barrier-touch assessment and MAE/MFE metrics.
- `simulator.py`: in-memory scoring and diff assessment.
- `monte_carlo.py`, `monte_carlo_15dte.py`: portfolio simulations.
- `backtest_cascade.py`, `backtest_cascade_15dte.py`: deterministic backtests.
- `database/barrier_cache.py`: SQLite writer plus DuckDB read mirror.
- `database/bulk_cache.py`: on-demand parquet cache helper.
- `volume_amplifier.py`, `market_regime.py`, `market_breadth.py`: scoring context.
- `historic_peaks.py`, `option_pricing.py`, `dte_recommendation.py`: signal events,
  option P&L, thesis/DTE recommendation.

Frontend anchors: `src/context/StockContext.js`, `src/pages/Dashboard.js`,
`src/pages/StockDetail.js`, `src/pages/Assessment.js`, `src/pages/Historic.js`,
`src/pages/MarketTrends.js`, `src/pages/Backtest.js`, `src/components/`.

## Algorithm Versioning

Scores are keyed by `(symbol, date, version)`.

- Writer/current version: `AlgorithmVersion.get_or_create_current()`.
- Reader/active version: `AlgorithmVersion.get_active_scores_version()`.
- Bump `ALGORITHM_VERSION` only when `Score.overall` changes.
- Do not bump for portfolio-stage-only changes: TP/SL, cascade, F3F, MaxPos,
  hold, DD soft-band, dead-hold, etc.
- Use `trader algorithm snapshot-staging`, `trader algorithm snapshot-current`,
  `trader algorithm diff`, and `trader portfolio-snapshot` to capture and
  compare explicit local scoring/portfolio silos. See
  `.claude/docs/algorithm-version-index.md` and
  `.claude/docs/algorithm-version-silos.md`.
- After a recalc/assessment refresh, use
  `tools/build_research_pack.py --version vNN --run-portfolio-windows` to
  hydrate `.cache/algorithm_versions/vNN/research_pack/`; it is the reusable
  research surface for version utility, coverage, and March-2020/2020-now/22-now
  drawdown windows. Use `tools/backfill_research_packs.py` for retro hydration;
  it never recalculates scores.
- After a scoring ship, follow `.claude/docs/deploy.md`: during market hours,
  fill today first with `trader recalculate 1d`, then run
  `trader recalculate --force` for the 5y dashboard backfill; run
  `trader recalculate --force --full` off-hours for final 10y coverage.

Current state changes often. Check `CLAUDE.md`, `ALGORITHM_VERSION`, and
`.claude/docs/known-issues.md` before making claims about the active version.

## Ship Stages

- Stage 1: scoring calibration. Applies to `Score.overall`, score dampeners,
  weights, lifts, gates. Uses W1-W6, WR15-primary, recalc, version bump.
- Stage 2: barrier optimization. Applies to TP/SL/HOLD_DAYS/PREMIUM_MULT and
  breadth stress thresholds. Uses B1-B5, option TP primary, barrier rebuilds.
- Stage 3: tertiary portfolio. Applies to F3F, MaxPos, cascade allocation, DD
  breaker, sector-concentration waves, dead-hold, slippage, regime slopes,
  CT_PROMOTE, SAW, etc. Uses T1-T7, drawdown-primary MC.

Sector concentration / clustering controls are Stage 3 portfolio exposure waves;
do not contaminate the Stage 1 WR15 + high-tier-N scoring objective unless sector
exposure independently proves directional WR15 signal value.

Never chain Stage 1 -> Stage 2 -> Stage 3 sweeps into one ship.

## CLI

```bash
trader update [--score-versions v57,v58]
trader close-update [--score-versions v57,v58]
trader recalculate [SYM] [lookback] [--force] [--full] [--all] [--rebuild-parquets]
trader rebuild-parquets [--exp NAME] [--all]
trader assess [--force] [--dte 30|15|both] [--profile sentinel|core|apex] [lookback]
trader backtest [--dte 15] [--from DATE] [--capital N]
trader explain-scores [SYM...] [days]
trader simulate [SYM...] [days] [--compare] [--assess] [--diff-assess]
trader historic-update [window]
trader breadth-backfill [days]
trader regime-backfill [days]
trader alloc <portfolio> [--strategy 30dte|15dte] [--staging sector-etf]
trader staging [list|migrate-legacy [--delete-legacy]]
trader algorithm [active|list|snapshot-staging|snapshot-current|diff|promote-candidate]
trader portfolio-snapshot [current|list|diff]
trader revert <version|v14|git-hash>
trader temporal-refresh [--dte 30|15|both] [--profiles all|sentinel,core,apex]
```

`trader update` can also read extra comparison versions from
`TRADER_SCORE_VERSIONS`.

Use `trader temporal-refresh` after portfolio-stage changes to refresh dashboard
calendar/monthly stats without re-running invariant per-bucket assessment.
Backtest temporal rows are keyed by scoring version, DTE strategy, and portfolio
profile; Sentinel/Core/Apex profiles are post-score portfolio overlays and never
become `ALGORITHM_VERSION` score-row labels.

## Topic Docs

Load only relevant docs, plus `process.md` first:

- `.claude/docs/process.md`: required workflow/process read.
- `.claude/docs/deploy.md`: scoring/portfolio ships, post-ship audits, mechanisms.
- `.claude/docs/scoring-algorithm.md`: score formula and score-stage mechanisms.
- `.claude/docs/trading-strategy.md`: shipped params, cascade, exits, MC results.
- `.claude/docs/known-issues.md`: current ship state, priorities, nulls, warnings.
- `.claude/docs/assessment-backtest.md`: assessment methodology and Stage 1/2/3 gates.
- `.claude/docs/monte-carlo.md`: MC methodology and reruns.
- `.claude/docs/monte-carlo-sweeps.md`: historical sweep findings.
- `.claude/docs/historic-peaks.md`: peak events and roll-up/re-entry behavior.
- `.claude/docs/options-pnl.md`: option P&L and chart projections.
- `.claude/docs/version-history.md`: version history and shipped summaries.
- `.claude/docs/algorithm-version-index.md`: agent entrypoint for
  `algorithm_versions/VERSION_GUIDE.md` and per-version silo READMEs.
- `.claude/docs/algorithm-version-silos.md`: scoring/portfolio silo layout,
  snapshot CLI, staging/elevation workflow, research packs, future multi-version
  update path.
- `.claude/docs/frontend.md`: React component details and UI behavior.
- `.claude/docs/staging-recalc.md`: staging recalculation workflow.
- `alpha_mining/README.md`: current alpha-mining entrypoint. Load before new
  scoring, portfolio, or miss-led mining work; update `NEW_LEADS.md` /
  `MISS_CANDIDATES.md` when leads are discovered, retired, or resolved by a
  ship.

## GitNexus - Code Intelligence

This repo is indexed by GitNexus as `Trader`. MCP resources may not be exposed in
every Codex session; if unavailable, use `npx gitnexus`.

Always:

- Run impact analysis before editing a function, class, or method:
  `gitnexus_impact({target: "symbolName", direction: "upstream"})`.
- Report blast radius: direct callers, affected processes, risk level.
- Warn the user before proceeding if risk is HIGH or CRITICAL.
- Run `gitnexus_detect_changes()` before committing.
- Use `gitnexus_query({query: "concept"})` for unfamiliar code.
- Use `gitnexus_context({name: "symbolName"})` for callers/callees/flows.

Never:

- Edit a function, class, or method without impact analysis first.
- Ignore HIGH or CRITICAL risk.
- Rename symbols with find-and-replace. Use `gitnexus_rename` dry-run first.
- Commit without `gitnexus_detect_changes()`.

Impact levels: `d=1` direct callers/importers must update; `d=2` indirect deps
should be tested; `d=3` transitive deps need critical-path testing.

If the index is stale, do not refresh it by default. Run `npx gitnexus analyze`
only after a successful push, when explicitly asked, or when stale index data
blocks the current GitNexus query. If `.gitnexus/meta.json` shows existing
embeddings, preserve them with `npx gitnexus analyze --embeddings`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Trader** (74479 symbols, 173645 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/Trader/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Trader/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Trader/clusters` | All functional areas |
| `gitnexus://repo/Trader/processes` | All execution flows |
| `gitnexus://repo/Trader/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
