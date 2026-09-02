---
name: onboard
description: Orientation and routing for a fresh session on the Trader repo — the system in 8 lines, the invariants that must never be violated (scoring direction, assessment buckets vs signal thresholds, where the ACTIVE version/profile really live), the standards bar (DD-primary, collapse=0, N floors, honest evidence), and a task-shape-to-skill routing table. Use when starting a new session on this repo, when a user or model asks "what is this project / how do I get oriented / where do I start", before touching scoring or portfolio code for the first time, or whenever you're about to assume a version/profile/param instead of looking it up.
---

# /onboard — orient on the Trader repo before doing anything else

Directional technical-analysis platform that scores stocks 0-100 daily for **options
trading** (calls/puts, not shares). This skill is the front door: read it first in any
fresh session, then route to the specific skill/doc your task needs. It does not do
work itself — it tells you where the work happens and what never to violate while
doing it.

## GUARDS — read these before touching anything

1. **Scoring direction never inverts.** HIGH score (≥75) = bullish = **call**
   opportunity. LOW score (≤25) = bearish = **put** opportunity. 50 = neutral. The
   *tell*: if you ever find yourself reasoning "the score is high so the stock is
   overbought/due for a pullback, so buy a put" — stop, that's the mean-reversion
   framing and it is **backwards** for this system. Some archived docs/memory frame the
   system that way; it's wrong. See `.claude/docs/traps.md` "scoring direction" entry
   if present.
2. **Assessment buckets ≠ signal-classification thresholds — don't conflate them.**
   Assessment/backtest buckets are **≥70 / ≤30** (`assess_scores.py` `BUY_THRESHOLDS =
   [95,90,85,80,75,70]`, puts `SELL_THRESHOLDS=[30,25,20,15,10,5]`). The tighter
   **call/put signal** classification is **≥75 / ≤25**. A stock scoring 72 is in the
   assessment ≥70 bucket but is NOT a ≥75 call signal. Don't read "≥70 wins" as "72 is
   a buy."
3. **The live money-tracking profile and the default query/recalc profile are TWO
   DIFFERENT LOOKUPS — never assume, always check both.** `portfolio_profiles.py`
   `DEFAULT_PROFILE_KEY = "core"` is what `trader assess`/API calls fall back to when no
   `?profile=` is passed. That is **not** the same as which profile is actually
   tracking real money right now — that's `PortfolioRun.profile`
   (`database/models/portfolio.py`), read live via `GET /api/portfolio/state` or
   `PortfolioRun.select().where(PortfolioRun.active == True).get().profile`. The
   `PortfolioRun` model's own field-level default is `'apex'`
   (`database/models/portfolio.py`) — this is unrelated to which row is actually
   `active == True` right now, and is NOT evidence of what's live. `known-issues.md`'s
   CURRENT SHIP STATE header (verified 2026-06-17, commit `a8f1b2c1c`) documents the
   live row being migrated Apex→Core at that time; no later dated entry documents a
   reversion back to Apex, and commit `3a585c2ed` (2026-06-22, "Apex profile = 15-DTE
   risk-budget elbow") only added DTE/SL parameter overrides to the Apex profile
   *definition* — its diff never touches `database/models/portfolio.py` or flips any
   row's `active`/`profile` field. If the live lookup shows `apex` today, treat it as a
   manually-triggered state change (e.g. `trader portfolio reset` or a direct DB
   action), not something shipped in code — resolve by hitting the live
   lookup, never by reading one doc header or a model field default. See
   `.claude/docs/gameplan.md` §2 for a narrative snapshot (itself point-in-time —
   re-verify against the API).
4. **Active scoring version drifts constantly — always look it up, never hardcode
   it.** Canonical lookup: `python trader.py algorithm active` — this resolves via
   `AlgorithmVersion.get_active_scores_version()`, the SAME resolver the API/dashboard
   use (`algorithm_versions/manager.py` `cmd_active` →
   `active_algorithm_version(create_current=False)`). This is a different call from
   what `trader assess` uses when no `--version` is passed (Guard 5:
   `get_or_create_current()`, HEAD-ish) — these two can disagree (see `/revert`). As of 2026-07 the file reads
   `f9fb7b934` (v74 LEAN, shipped 2026-06-15) — **stamped, not a promise**; by the time
   you read this it may be v75+. Known-stale surfaces that lag behind whatever is
   actually active: `algorithm_versions/VERSION_GUIDE.md` and
   `.claude/docs/algorithm-version-index.md` (both top out around v66),
   `alpha_mining/NEW_LEADS.md`'s header line (says "v60" while its ranked list is
   current), `trading-strategy.md`'s header (says v73), `MISS_CANDIDATES.md`'s
   priority table (formally **INVALIDATED** — history only, do not triage from it).
5. **`trader assess` resolves its version via `get_or_create_current()` (HEAD-ish),
   NOT the active-scores pointer** — on a dirty working tree these can differ. Pass an
   explicit `--version vNN` when in doubt; see [run-assessment](../run-assessment/SKILL.md).
6. **Never `git add -A` / `git add .` in this repo.** The working tree routinely
   carries dozens of uncommitted production-code edits, ~100s of experiment sweep
   artifacts, and an auto-churning `.planning/HANDOFF.json` (a content-empty
   PostToolUse checkpoint stub — timestamp-only, touched by a hook on every tool call,
   never a source of in-flight state). Stage new files **explicitly by path**. Run
   `git status` and read it before staging anything.
7. **GitNexus: two auto-generated blocks live in CLAUDE.md and they disagree.** One
   near the top (mid-Growth-Policy-paragraph) implies analyze-after-commit-hook
   behavior with one symbol count; a second block further down states the
   authoritative project policy (`npx gitnexus analyze` **only after a successful
   push**, explicit user ask, or a stale index blocking the current query — never
   after a bare local commit/merge) with a different symbol count. **The LATER block
   (the one nearer the bottom of CLAUDE.md, with the "post-push-only" wording) is
   authoritative** — it's the project-authored override; the first is leftover
   installer boilerplate. `gitnexus_detect_changes()` (read-only, MCP) is always safe
   before a commit; `npx gitnexus analyze` (CLI, writes files) is the one to hold back.
   Loop-break rule: after an analyze run, `git diff --name-only` — if every changed
   file is in `{AGENTS.md, CLAUDE.md, .gitnexus/}`, stop, don't commit, don't re-run.
8. **Pre-v69 evidence is look-ahead-inflated by roughly 12 percentage points.** Any
   number you find in `known-issues-archive.md`, `version-history-archive.md`, or a
   pre-2026-05-31 memory file describing win rates should be read with that discount
   in mind — it is NOT comparable to post-v69 (honest) numbers without adjustment.
   Don't cite an archived WR table as if it were current evidence. Root cause: the
   pre-v69 weekly composite read the *current* week's completed `WeeklyScore` even for
   mid-week historical signals — a look-ahead that v69's PIT reconstruction removed.
9. **Null-check before proposing anything "new."** ~40 hard "never re-test" rules
   live in `known-issues.md` **WHAT NOT TO DO** (plus the CLOSED archive in
   `known-issues-archive.md` and the TESTED-NULLS in memory). Grep it for your
   hypothesis's keyword before building anything. A retry is only valid if it's a
   genuinely **new mechanism class**, not a rephrasing of a closed one (e.g. every
   "breadth collapse → cut calls" reformulation collapses back into the shipped
   MWDD/BDIV levers — closed 2026-06-23).

## The system in 8 lines

1. Daily pipeline (`trader update`) pulls OHLCV → indicators → components → weekly →
   volume amplifier → market regime → `Score.overall` (0-100) per stock per day.
2. `Score.overall` ≥75 = call signal, ≤25 = put signal (Guard 1); assessment buckets
   are the wider ≥70/≤30 (Guard 2).
3. Scores are **version-keyed** `(symbol, date, version_id)` — every shipped
   `AlgorithmVersion`'s scores coexist in the DB; nothing is overwritten on a version
   bump (Guard 4).
4. A **portfolio profile** (Apex / Core / Sentinel, in `portfolio_profiles.json` +
   `strategy_config.py`) turns qualifying signals into sized option positions with
   TP/SL/hold/DD-band rules — this is a *separate* ship track from scoring (Guard 3).
5. Puts are OFF portfolio-wide (surface-only, assessment/backtest can still show put
   stats) — every honest attempt to fund them has failed the DD floor.
6. Everything ships through a **three-stage gate**: Stage 1 (scoring, WR15-primary,
   W1-W6) → Stage 2 (barriers, option-TP-primary, B1-B5) → Stage 3 (portfolio,
   5y-WorstDD-primary + collapse=0, T1-T7). See [ship-gates](../ship-gates/SKILL.md).
7. All heavy/long compute (recalcs, sweeps, MC, research packs) goes through
   `trader queue submit`, never raw foreground/background — see
   [queue-ops](../queue-ops/SKILL.md).
8. Deep knowledge lives in `.claude/docs/*.md`; CLAUDE.md and this skill are pointers,
   not the content itself. Auto-memory (`MEMORY.md` + topic files under
   `C:\Users\<user>\.claude\projects\...\memory\`) is the promotion source for
   traps/nulls — the (separate) `.claude/docs/traps.md` doc is the canonical
   in-repo distillation of it.

## Where truth actually lives (machine sources beat docs)

| Question | Look here, not a doc header |
|---|---|
| What version is active/shipping, or is the API/dashboard serving? | `python trader.py algorithm active` and `AlgorithmVersion.get_active_scores_version()` are the SAME lookup; also `GET /api/score/versions` |
| What version will the next `trader assess`/recalc (no `--version` passed) write under? | `AlgorithmVersion.get_or_create_current()` (HEAD-ish/writer path, Guard 5) — reads the `ALGORITHM_VERSION` file; can disagree with the row above on a dirty tree |
| What are the shipped strategy params (TP/SL/cascade/DD-band/dampener constants)? | `strategy_config.py` — `STRATEGY_30DTE` / `STRATEGY_15DTE`, single source of truth |
| Which portfolio profile is live-tracking real money? | `GET /api/portfolio/state`; `PortfolioRun.select().where(active=True)` (Guard 3) |
| What profile does a bare `trader assess`/API call default to? | `portfolio_profiles.DEFAULT_PROFILE_KEY` in `portfolio_profiles.py` (Guard 3 — NOT the same lookup as above) |
| Are shipped constants drifted from docs? | `python tests/test_strategy_config_drift.py` (~1s; run after any `strategy_config.py` edit) |
| Is a portfolio mechanism fully wired (both DTEs, engines, tests)? | `python tests/test_mechanism_registry.py`; `mechanism_registry.py` `REGISTRY` |
| Is the calibration holdout active, and at what cutoff? | `strategy_config.CALIBRATION_CUTOFF_DATE` (as of 2026-07: `"2026-06-15"`, re-locked 2026-06-11; OOS re-eval ≈2026-12-15) |
| Am I about to duplicate a closed investigation? | `known-issues.md` WHAT NOT TO DO + `known-issues-archive.md` CLOSED + `alpha_mining/NEW_LEADS.md` |

## The standards bar (compressed — full detail in the docs each row points to)

1. **DD-primary.** 5y WorstDD is the primary portfolio ship metric; compound is
   secondary/sanity only (±3 orders of magnitude — theoretical compounds in the
   1e10%+ range are unrealizable, don't chase the mantissa). → [ship-gates](../ship-gates/SKILL.md).
2. **collapse=0 on EVERY window including 2020-COVID, non-negotiable for held books.**
   The opt-in Apex sprint is the one sanctioned exception — a small, explicit,
   user-approved collapse budget for a stop-at-2x tool, never the default. →
   [run-monte-carlo](../run-monte-carlo/SKILL.md).
3. **Lock decisions on 5y; 22-now is confirmation-only; always screen 2020_crash.**
   A screen without the COVID window has hidden collapse before.
4. **N floors: MC N=500 to ship, N=300 to screen (±5-8pp DD noise, 1.6-1.8x compound
   noise at N=300), N=100 to probe.** A per-trade cohort z-score is evidence toward a
   hypothesis, never itself a portfolio result.
5. **Honest evidence is mandatory:** day-of-week option-TP split test for any weekly
   feature (catches within-week look-ahead), point-in-time reconstruction (never a
   stored *completed*-period aggregate as a "partial" feature), PIT-market-cap /
   survivorship checks, full-universe validation (never a small-stock screen).
6. **Full-faithful validation before any ship claim:** `trader recalculate --force` +
   `trader assess --force`. A P0/sampled approximation is hypothesis-forming only —
   never cite it as ship evidence (the v42 rolling-weekly P0 said +4.5pp, real assess
   said −13pp).
7. **Execution cost is ASYMMETRIC.** Mid-entry and limit-TP are free (liquidity
   provider side); only *forced* exits (SL / hard-sell / dead-hold-expiry) pay the
   half-spread (`SLIP_SL=SLIP_HARD=-0.015`/leg). Never model a flat round-trip cost —
   that produces false "doesn't beat SPY" verdicts.
8. **Bias to retire — parsimony.** Mechanisms re-earn their seat on honest evidence;
   v74's whole-tail retirement (four mechanisms, −10.8pp 5y DD at flat compound) is
   the house style.
9. **Gradient over threshold, but never smooth a cliff blind.** New mechanisms
   default to smooth gradients (log/power-law/tanh). Before smoothing an *existing*
   component cliff, run the dropped-rows-apex-WR test — cliffs are sometimes the
   signal, not an artifact (the MACD-phase cliff smoothing attempt was net-dilutive
   because the dropped rows had *higher* WR than the retained ones).
10. **Null discipline.** Check `known-issues.md` WHAT NOT TO DO + the CLOSED archive
    before proposing anything; a retry needs a genuinely new mechanism class (Guard 9).
11. **Scoring direction / assessment-vs-signal buckets** (Guards 1-2) — repeated here
    because it's the single most consequential invariant in the codebase.
12. **Queue everything minutes+.** `trader queue submit`, never raw
    background/foreground heavy compute — see [queue-ops](../queue-ops/SKILL.md).
13. **FLAG gate verdicts are shippable WITH justification + a named post-ship watch
    metric.** The honest-era 0d skill gate's standing FLAG ("risk-shaper": beats
    momentum, marginal vs climatology) is *by design* — do not treat it as a defect to
    auto-revert.

## Session hygiene

- **Queue discipline:** anything that runs minutes+ or hits MySQL at scale
  (`recalculate`, `assess`, Monte Carlo, research-pack builds, sweeps, old-version
  profile reruns) is `trader queue submit`, even read-only, even from a worktree.
  "I'm actively watching it this turn" does not exempt it — the harness's own
  `run_in_background` flag is NOT the queue and bypasses its CPU/DB admission
  entirely, which can let a scheduled `trader update` collide with your job on the
  tight-timeout MySQL connection. → [queue-ops](../queue-ops/SKILL.md).
- **Worktree trigger:** starting a new scoring hypothesis that will involve sweeps,
  parquet builds, or multi-variant assessment → create a worktree FIRST:
  `git worktree add ../Trader-exp-<name> -b algo-exp/<name>`. Inside it, the real
  scoring path is staging code and may be edited directly; `trader update` /
  `trader recalculate` remain elevation/ship-only operations, run only from the
  designated ship checkout. Global `PYTHONPATH` on this box includes the main repo —
  a worktree script's `sys.path[0]` is the *script's* dir, so it can silently import
  MAIN's modules and produce a false NULL. Pin `sys.path.insert(0, ROOT)` and assert
  the imported module's `__file__` resolves under the worktree root before trusting a
  0-diff A/B. → [run-experiment](../run-experiment/SKILL.md).
- **Doc-update timing:** `.claude/docs/*.md` and `CLAUDE.md` edits trigger a
  permission prompt that can stall for hours if the user is away. Update docs ONLY
  after a ship/experiment is fully complete, or immediately before queueing a
  long-running task that doesn't depend on the doc edit landing first. Never edit
  docs as the literal last action before a long task — if the user is away, the task
  never starts. Full timing table: `.claude/docs/process.md` "Doc-update timing."
- **Commit scoping:** never `git add -A` (Guard 6). Check `git status` first; this
  repo's working tree routinely has 10+ uncommitted production files and ~100s of
  experiment artifacts alongside whatever you're actually landing. Stage your files
  by explicit path.
- **Restart discipline:** after editing `api.py`, `strategy_config.py`,
  `backtest_cascade.py`, or `portfolio_param_manifest.py`, the Flask API (`:5000`)
  needs a restart to serve the change — run it via the PowerShell tool with
  `run_in_background: true` (`& C:\Development\server.bat restart -Service
  trader-api`). Running it foreground **hangs the agent forever** (a hidden
  persistent child holds the shell's stdout pipe open); the Bash tool's `cmd.exe /c`
  form silently no-ops (exit 0, nothing restarts). Verify with `GET
  http://127.0.0.1:5000/health` → 200. React dev (`:3000`) hot-reloads, no restart
  needed. → [frontend-ops](../frontend-ops/SKILL.md).

## Routing table — task shape → skill (and backing doc)

| If the task is about... | Go to | Backing doc |
|---|---|---|
| `trader queue` usage, priority tiers, market-hours interaction, daemon health | [queue-ops](../queue-ops/SKILL.md) | `.claude/docs/task-queue.md` |
| Update/close-update failed, MySQL zombie queries, queue daemon down | [debug-pipeline](../debug-pipeline/SKILL.md) | `.claude/docs/task-queue.md` |
| A score looks wrong / intraday swing or fakeout investigation | [debug-scores](../debug-scores/SKILL.md) | `.claude/docs/assessment-backtest.md` "Intraday Score Audit Log" |
| Backfilling scores, rebuilding parquets, barrier-cache, breadth/regime backfill | [data-ops](../data-ops/SKILL.md) | CLAUDE.md tables |
| Running `trader assess`, reading WR tables, research packs | [run-assessment](../run-assessment/SKILL.md) | `.claude/docs/assessment-backtest.md` |
| Portfolio Monte Carlo, sweep phases, noise floors | [run-monte-carlo](../run-monte-carlo/SKILL.md) | `.claude/docs/monte-carlo.md` + `monte-carlo-sweeps.md` |
| Does this pass the ship gate? Stage 1/2/3 mechanics | [ship-gates](../ship-gates/SKILL.md) | `.claude/docs/assessment-backtest.md` + `deploy.md` |
| Running a new experiment end-to-end (hypothesis → worktree → evidence → closeout) | [run-experiment](../run-experiment/SKILL.md) | `.claude/docs/process.md` |
| Shipping a new scoring version (`ALGORITHM_VERSION` bump) | [ship-version](../ship-version/SKILL.md) | `.claude/docs/deploy.md` |
| Shipping a portfolio-stage change (TP/SL/cascade/dampener, no version bump) | [ship-portfolio](../ship-portfolio/SKILL.md) | `.claude/docs/deploy.md` |
| Live Portfolio tracker ops (`trader portfolio sync/status/pending/notify`) | [portfolio-ops](../portfolio-ops/SKILL.md) | `.claude/docs/frontend.md` + `deploy.md` |
| React/Flask feature work, new endpoint, new page | [frontend-ops](../frontend-ops/SKILL.md) | `.claude/docs/frontend.md` |
| Finding new alpha (scoring OR portfolio) | [mine-alpha](../mine-alpha/SKILL.md) → routes to [find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) or [research](../research/SKILL.md) | `alpha_mining/README.md`, `.claude/docs/gameplan.md` |
| Hot-swapping the active scoring algorithm | [/revert](../revert/SKILL.md) | — |
| Ending a session / passing context to a fresh chat | [/handoff](../handoff/SKILL.md) | — |
| "How does X work?" / blast radius / rename-refactor / trace a bug via the code graph | `.claude/skills/gitnexus/gitnexus-*` (see CLAUDE.md "CLI" table) | — |
| Strategic "what should I work on next" | `.claude/docs/gameplan.md` | — |

If nothing above fits, read `.claude/docs/process.md` first (it's the meta-doc, "Load
FIRST" by its own header) and `known-issues.md`'s header block second.

## Self-update

If you hit a trap this skill missed — a wrong assumption, a stale pointer, a lookup
that turned out to have a second gotcha — append it as a new numbered GUARD here (in
the same session) and add the same entry to `.claude/docs/traps.md`.
