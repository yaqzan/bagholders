# Skill Library Index — `.claude/skills/`

19 skills. **Not documentation** — each `SKILL.md` is a runbook: exact commands, GUARDS
(traps embedded inline), evidence pointers. Deep spec lives in `.claude/docs/*.md`; skills
teach you how to *operate* against that spec. Frontmatter is `name` + `description` only;
the description doubles as the trigger spec ("Use when the user says/asks...").

**Start here if you're new:** [`/onboard`](onboard/SKILL.md) — orientation, the 8-line
system model, the invariants that must never be violated, and this same routing table in
compressed form. Read it before touching anything; the rest of this file is a fuller index
once you're past that first orientation pass.

## Routing table — task shape → skill

| Task shape | Skill | Backing doc |
|---|---|---|
| First session on this repo / "how do I get oriented" | [onboard](onboard/SKILL.md) | — |
| `trader queue` usage, priority tiers, daemon health, wait/kill/hold | [queue-ops](queue-ops/SKILL.md) | `task-queue.md` |
| Update/close-update failed, MySQL zombie queries, queue daemon down, stale API config | [debug-pipeline](debug-pipeline/SKILL.md) | `task-queue.md` |
| "Why did SYM score X", intraday swing/fakeout, version confusion | [debug-scores](debug-scores/SKILL.md) | `assessment-backtest.md` "Intraday Score Audit Log" |
| Backfill scores, rebuild parquets, barrier-cache, breadth/regime backfill, purge | [data-ops](data-ops/SKILL.md) | CLAUDE.md tables |
| Run `trader assess`, read WR15/optTP tables, build research packs | [run-assessment](run-assessment/SKILL.md) | `assessment-backtest.md` |
| Portfolio Monte Carlo, DD sweeps, noise floors, Stage-3 A/B | [run-monte-carlo](run-monte-carlo/SKILL.md) | `monte-carlo.md` + `monte-carlo-sweeps.md` |
| "Does this ship?" — Stage 1/2/3 verdicts, growth gate, holdout lock | [ship-gates](ship-gates/SKILL.md) | `assessment-backtest.md` + `deploy.md` |
| New experiment end-to-end (hypothesis → worktree → evidence → closeout) | [run-experiment](run-experiment/SKILL.md) | `process.md` |
| Ship a scoring change (`ALGORITHM_VERSION` bump) | [ship-version](ship-version/SKILL.md) | `deploy.md` |
| Ship a portfolio-stage change (TP/SL/cascade/dampener, no version bump) | [ship-portfolio](ship-portfolio/SKILL.md) | `deploy.md` |
| Live Portfolio tracker ops (sync/status/pending/notify), profile switch | [portfolio-ops](portfolio-ops/SKILL.md) | `frontend.md` + `deploy.md` |
| New API endpoint/page, dashboard won't show a change, dev server restart | [frontend-ops](frontend-ops/SKILL.md) | `frontend.md` |
| "What should we mine next / is there alpha left in X" | [mine-alpha](mine-alpha/SKILL.md) | `alpha_mining/README.md`, `gameplan.md` |
| Discover + ship new scoring alpha autonomously | [find-and-ship-alpha](find-and-ship-alpha/SKILL.md) | — |
| Overnight autonomous find-and-ship-by-open run | [research](research/SKILL.md) | — |
| Hot-swap the active scoring algorithm to a silo'd version | [revert](revert/SKILL.md) | — |
| End a session / pass context to a fresh chat | [handoff](handoff/SKILL.md) | — |
| "How does X work?" / blast radius / rename / trace a bug via the code graph | `gitnexus/gitnexus-*` (below) | — |
| Strategic "what should I work on next" | — | [gameplan.md](../docs/gameplan.md) |

If nothing above fits: read [process.md](../docs/process.md) first (it's the meta-doc,
"Load FIRST" by its own header), then `known-issues.md`'s header block.

### `gitnexus/` — generic code-graph navigation (vendor-generated, non-domain)

| Task | Skill |
|---|---|
| Understand architecture / trace execution flows | [gitnexus-exploring](gitnexus/gitnexus-exploring/SKILL.md) |
| Trace a bug / "why is X failing" | [gitnexus-debugging](gitnexus/gitnexus-debugging/SKILL.md) |
| Blast radius before an edit | [gitnexus-impact-analysis](gitnexus/gitnexus-impact-analysis/SKILL.md) |
| Rename / extract / split / restructure safely | [gitnexus-refactoring](gitnexus/gitnexus-refactoring/SKILL.md) |
| Tools/resources/schema reference | [gitnexus-guide](gitnexus/gitnexus-guide/SKILL.md) |
| Index / status / clean / wiki CLI | [gitnexus-cli](gitnexus/gitnexus-cli/SKILL.md) |

These six use generic non-Trader examples (`validateUser`, payment processing) — they're
installer boilerplate, not project-authored. **Known conflict:** all six say "if the index
is stale, run `npx gitnexus analyze`"; the project's actual policy (CLAUDE.md, the *later*
of its two GitNexus blocks) is **post-push only** — never after a bare local commit/merge.
Follow CLAUDE.md, not the gitnexus skill wording, on this specific point.

## The standards bar (one-pager)

Every ship decision in this repo answers to these regardless of which skill you're in.
Full detail: [onboard GUARDS + standards bar](onboard/SKILL.md), [ship-gates](ship-gates/SKILL.md).

1. **DD-primary.** 5y WorstDD is the primary portfolio ship metric; compound is
   secondary/sanity (±3 OOM — theoretical MC compounds in the 1e10%+ range are unrealizable).
2. **collapse=0 on EVERY window incl. 2020-COVID**, non-negotiable for held books. The
   opt-in Apex sprint is the one sanctioned exception (small, explicit, user-approved budget).
3. **Lock decisions on 5y; 22-now is confirmation-only; always screen `2020_crash`.**
4. **N floors: MC N=500 ship, N=300 screen** (±5-8pp DD noise / 1.6-1.8× compound noise at
   N=300), **N=100 probe.** A per-trade cohort z-score is not itself a portfolio result.
5. **Honest evidence is mandatory:** day-of-week option-TP split for any weekly feature
   (catches look-ahead), point-in-time reconstruction (never a completed-period aggregate as
   "partial"), PIT-mcap/survivorship checks, full-universe validation (never a small screen).
6. **Full-faithful validation before any ship claim:** `trader recalculate --force` +
   `trader assess --force`. A P0/sampled approximation is hypothesis-forming only.
7. **Execution cost is ASYMMETRIC.** Mid-entry and limit-TP are free; only forced exits
   (SL/hard-sell/dead-hold) pay the half-spread. Never model a flat round-trip cost.
8. **Bias to retire — parsimony.** Mechanisms re-earn their seat on honest evidence.
9. **Gradient over threshold, but never smooth a cliff blind** — run the dropped-rows
   apex-WR test before smoothing an existing component cliff; cliffs are sometimes the signal.
10. **Null discipline.** Check `known-issues.md` WHAT NOT TO DO + the CLOSED archive before
    proposing anything; a retry needs a genuinely new mechanism class, not a rephrasing.
11. **Scoring direction never inverts.** HIGH (≥75) = CALL, LOW (≤25) = PUT. Assessment
    buckets are the wider ≥70/≤30 — don't conflate with the tighter signal thresholds.
12. **Queue everything minutes+.** `trader queue submit`, never raw background/foreground
    heavy compute — the harness's own background flag is NOT the queue.
13. **FLAG is shippable with justification + a named post-ship watch metric** — the
    honest-era 0d skill gate's standing FLAG is by design, never auto-revert on it.

## Reading order for a new engineer (or a fresh agent session)

1. [`/onboard`](onboard/SKILL.md) — orientation, invariants, where truth lives.
2. [`.claude/docs/traps.md`](../docs/traps.md) — the cross-cutting trap/gotcha registry
   (infra/DB, evidence honesty, process/shipping, modeling/metrics, tooling staleness). Every
   skill's GUARDS section embeds the 2-6 traps specific to its own workflow and links back
   here for the rest — read this once for the traps that don't belong to any single skill.
3. [`.claude/docs/gameplan.md`](../docs/gameplan.md) — the strategic layer: mission,
   current state snapshot, the alpha-frontier verdict table (what's ACCEPT/REFINE/BACKLOG/
   HOLD/DEAD), priority stack, standing anti-goals. Answers "what should I work on next."
4. Task-specific skill from the routing table above, picked by what you're actually about
   to do — not every skill, just the one(s) your task needs.

## Maintenance note

**Self-update convention.** Every skill ends with a `## Self-update` section: if you hit a
trap it missed, append it to that skill's GUARDS **and** to `traps.md` in the same session.
This file (the index) doesn't carry that section itself — it has no GUARDS — but it does
drift: a new skill added under `.claude/skills/` needs a row here (routing table) and,
if it changes the reading order or standards bar, an edit to those sections too.

**Values drift — teach lookups, don't hardcode.** Every version/profile/param cited in any
skill in this library is stamped "as of 2026-07" and backed by a live lookup
(`trader algorithm active`, `strategy_config.py`, `portfolio_profiles.json`,
`GET /api/portfolio/state`, `tests/test_strategy_config_drift.py`) — never trust a cited
number over the lookup. Known-stale surfaces flagged throughout the library:
`algorithm_versions/VERSION_GUIDE.md` + `algorithm-version-index.md` (top out ~v66; active
is v74+), `alpha_mining/NEW_LEADS.md`'s header line (says v60; its ranked list is current),
`known-issues.md`'s CURRENT SHIP STATE header (dated 2026-06-17, pre-dates the 2026-06-22
live Apex-elbow switch — **the live portfolio profile is always a lookup, `GET
/api/portfolio/state` or `trader portfolio status`, never a doc header**),
`MISS_CANDIDATES.md`'s priority table (formally INVALIDATED — history only). Pre-v69
evidence (anything before the 2026-05-31 weekly-transition-blend ship) is look-ahead-inflated
by roughly 12 percentage points — any skill citing a historical WR table carries that warning.
