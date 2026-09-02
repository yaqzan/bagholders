# Trader Dashboard — Claude Code Context

## CLAUDE.md Growth Policy — READ FIRST

**Keep the authored section lean (~150 lines max).** The auto-generated GitNexus block (between
`<!-- gitnexus:start -->` and `<!-- gitnexus:end -->` below) does NOT count toward that budget and
is never hand-edited. This file is a navigational index — invariants, commands, one-line pointers.
Detail lives in `.claude/docs/`.

**Add to CLAUDE.md ONLY:** new files/CLI commands/API endpoints, critical invariants that apply to
ALL tasks, one-line pointers to sub-docs. **Never add:** phase logs, result tables, commit/version
history, sweep findings, mechanism descriptions, ship/audit checklists — those go in the matching
`.claude/docs/` file, with a pointer here if needed.

---

## What This System Does

Directional technical-analysis platform for **options trading**. Scores stocks 0-100 daily:
**≥75** = bullish → call opportunity | **≤25** = bearish → put opportunity | **50** = neutral.

**CRITICAL — scoring direction: HIGH score = CALL, LOW score = PUT. Never invert.**

Assessment buckets (≥70 / ≤30) are wider than the call/put signal cut (≥75 / ≤25) — don't conflate.

---

## Git Workflow

Global single-developer policy applies — edit directly, dirty state is normal, no branches/worktrees
unless asked. Exception: a scoring hypothesis involving sweeps/parquet caches/multi-variant
assessment → work in a worktree (`git worktree add ../Trader-exp-<name> -b algo-exp/<name>`) so
production `trader update` keeps running on `main`. Snapshot before elevation (`trader algorithm
snapshot-staging`); don't run `trader update`/`trader recalculate` from a scoring sandbox unless
shipping from the designated ship checkout. `gitnexus_detect_changes()` runs before committing, not
editing. **Index refresh: always `py scripts/gitnexus_refresh.py`, never bare `npx gitnexus
analyze`.** GitNexus pin: `gitnexus@1.6.9`, one global install for CLI+MCP. Full workflow, pin
upgrade steps, gotchas: [algorithm-version-silos.md](.claude/docs/algorithm-version-silos.md),
[traps.md](.claude/docs/traps.md) "GitNexus" entries.

---

## Review Posture

- Before implementing any non-trivial change, state the strongest case against it in ≤3 sentences.
- Every change proposal names: the metric it should move, what counts as failure, when we evaluate.
- Rank objections (blocking / serious / minor); raise only what would change the decision.
- Any statistical finding states N, hypotheses tested before finding it, and OOS survival.
- "Looks good" is banned — approval states what was checked and what's still unverified.
- Disagree once, in full; if overridden, log to DECISIONS.md and don't relitigate.

---

## Long-Running Compute — enqueue it, don't run it raw

A supervised task-queue daemon always runs on this box. Before launching anything minutes+ or
CPU/MySQL-heavy directly (`trader recalculate`, sweeps, Monte Carlo, research packs) — submit to
the queue instead; the harness's own `run_in_background` is NOT the queue and bypasses admission:

```bash
trader queue submit --priority low --db heavy --cpu 8 --restartable \
  --dedup <stable-key> --reason "<why>" -- <your command>
```

Sizing, priority tiers, DB budget=2 cap, oversubscription, notification pattern
(`trader queue wait <id>`): [task-queue.md](.claude/docs/task-queue.md).

---

## Vendor Data Assets

Polygon flat-file archive COMPLETE+VERIFIED (64 GB, `B:\polygon_flatfiles\`, NOT C: — no room);
Sharadar complete. Entitled: `us_options_opra/{day_aggs,trades,minute_aggs}_v1` only, window
2022-08-01→present (bucket LISTS back to 2014 but GETs 403 before 2022-08 — never infer coverage
from a listing). Sharadar fundamentals (SF1/SF2/SF3/daily/events) NOT entitled (403); `metrics` is
a present-day snapshot — joining it into a historical build is look-ahead. No real option prices
exist for 2020-03/2018-02/2015-08 — crash-window option-model fidelity is unchanged by this grab.
**Vendor data goes to `.cache/` or `B:\` — never MySQL.** Full record:
[data-acquisition.md](.claude/docs/data-acquisition.md) "ACTIVE DATA GRAB".

---

## Architecture

Two layers: **Backend** (Python/Flask + Peewee ORM → MySQL) — `trader.py` orchestrates OHLCV
fetch/indicator/score loops and the CLI, `api.py` is the Flask REST surface, `strategy_config.py`
is the single source of truth for shipped params, `algorithm_versions/` holds tracked
scoring/portfolio silos. **Frontend** (React 18 + Tailwind + Chart.js) under `frontend/src/`,
served via the Dashboard/StockDetail/Assessment/Portfolio pages. Full file map, scoring data flow
(yfinance → indicators → regime → Score → API), and the `ScoreIntradayLog` audit trail:
[architecture-and-cli.md](.claude/docs/architecture-and-cli.md).

---

## Algorithm Versioning

Scores keyed by `(symbol, date, version)`; new commit → new `AlgorithmVersion` row. **Bump
`ALGORITHM_VERSION`** when `Score.overall` changes; don't bump for portfolio-stage-only changes
(TP/SL/cascade/MaxPos/hold).

**Active scoring version: v74 (`f9fb7b934`)**, 2026-06-15. **Active portfolio strategy: Core/Apex/
Sentinel profiles** (default = Core, calls-only, puts-off).

Silo tooling: `trader algorithm snapshot-staging|snapshot-current|diff`. Full version history,
ship procedures, and silo/CLI/API reference: [version-history.md](.claude/docs/version-history.md),
[algorithm-version-silos.md](.claude/docs/algorithm-version-silos.md),
[architecture-and-cli.md](.claude/docs/architecture-and-cli.md) (API Endpoints + Trader CLI
Reference tables), [deploy.md](.claude/docs/deploy.md) (ship procedures).

---

## Sub-Document Index — Load When Relevant

Pointers, not auto-imports — read a row's doc with the Read tool when the task matches. Do not
convert these to `@`-imports (that force-loads every doc into every conversation).

| Doc | Load when working on... |
|-----|------------------------|
| [process.md](.claude/docs/process.md) | **Load FIRST.** Doc-update timing, agent/model tiering |
| [architecture-and-cli.md](.claude/docs/architecture-and-cli.md) | File map, data flow, API endpoints, `trader` CLI reference |
| [weatherization.md](.claude/docs/weatherization.md) | Verification-first scoring doctrine |
| [deploy.md](.claude/docs/deploy.md) | Shipping a scoring/portfolio change, post-ship audit |
| [scoring-algorithm.md](.claude/docs/scoring-algorithm.md) | Scoring formula, components, weights |
| [trading-strategy.md](.claude/docs/trading-strategy.md) | Shipped strategy params, cascade allocation, MC results |
| [known-issues.md](.claude/docs/known-issues.md) | Current ship state, open priorities, what NOT to do |
| [assessment-backtest.md](.claude/docs/assessment-backtest.md) | Assessment methodology, three-stage calibration framework |
| [monte-carlo.md](.claude/docs/monte-carlo.md) | MC methodology, how to re-run |
| [monte-carlo-sweeps.md](.claude/docs/monte-carlo-sweeps.md) | Bayesian sweep history |
| [historic-peaks.md](.claude/docs/historic-peaks.md) | Peaks cache, roll-up/re-entry pills |
| [options-pnl.md](.claude/docs/options-pnl.md) | PriceChart TP/SL projections, option P&L |
| [version-history.md](.claude/docs/version-history.md) | Commit logs, cross-version WR tables |
| [algorithm-version-index.md](.claude/docs/algorithm-version-index.md) | Agent entrypoint for per-version silo READMEs |
| [algorithm-version-silos.md](.claude/docs/algorithm-version-silos.md) | Silo layout, snapshot CLI, staging/elevation workflow |
| [frontend.md](.claude/docs/frontend.md) | React component details |
| [task-queue.md](.claude/docs/task-queue.md) | Queuing long compute, scheduler/daemon, audit/ops |
| [alpha_mining/README.md](alpha_mining/README.md) | Alpha-mining entrypoint |
| [data-acquisition.md](.claude/docs/data-acquisition.md) | Data gaps, pricing barriers, active data grab |
| [gameplan.md](.claude/docs/gameplan.md) | Strategic forward plan, priority stack |
| [capital-plan-2026.md](.claude/docs/capital-plan-2026.md) | Owner's debt + deployment program — load before any fund/deploy question |
| [traps.md](.claude/docs/traps.md) | Canonical trap/gotcha registry — load when debugging anything surprising |
| [succession-fable-2026-07.md](.claude/docs/succession-fable-2026-07.md) | Program boot doc — load FIRST after the 2026-07 migration |
| [.claude/skills/README.md](.claude/skills/README.md) | Skill-library index — new engineers/models start at `/onboard` |

---

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Trader** (48353 symbols, 127169 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, treat that as expected after local commits. Do not run `npx gitnexus analyze` until after a successful push unless the user explicitly asks or the stale index blocks the current query.

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

After pushing code changes, the GitNexus index may be refreshed as post-push maintenance. Do not run analyze after local commits, merges, stale-index warnings, or pre-push status checks unless the user explicitly asks or stale index data blocks the current query:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

If analyze changes only generated GitNexus count lines in `AGENTS.md` /
`CLAUDE.md` or index metadata, do not commit or amend those analyzer-only
changes. Do not loop on another analyze just because a commit or generated
metadata makes the index point at the previous commit.

> Claude Code users: do not use a commit/merge hook to run `analyze` in this repo. The refresh belongs after a successful push.

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
