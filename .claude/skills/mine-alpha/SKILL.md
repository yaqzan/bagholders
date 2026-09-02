---
name: mine-alpha
description: Alpha-sourcing discipline for this repo — where to look for a new lead, how to null-check it against ~40 hard-won closed axes before spending compute, and which execution skill to route to once a hypothesis survives. Use when the user asks "what should we mine next", "is there alpha left in X", "find a new signal/edge/lever", proposes a scoring or portfolio idea from intuition, or asks "has this been tried before".
---

# /mine-alpha — source, null-check, and route a new alpha hypothesis

This skill is a **filter and a map, not an execution engine.** It answers "is this
idea worth building" and "who builds it" — it does not itself mine ledgers, run
sweeps, or ship anything. Read-heavy, compute-light: most of the value here is
killing bad ideas in minutes by checking them against evidence that already
exists, before anyone burns a queue slot on them.

Execution lives elsewhere: a Stage-1 scoring hypothesis that survives the
null-check goes to [/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md); a
Stage-3 portfolio-lever hypothesis goes to the DD-active-subset method below
(and then [/run-monte-carlo](../run-monte-carlo/SKILL.md) +
[/ship-gates](../ship-gates/SKILL.md)); anything needing a fresh experiment
directory goes to [/run-experiment](../run-experiment/SKILL.md) Phase 0
(the null-check machinery there and here is the same registry, described
once — don't run both greps separately, run them together).

## GUARDS (read before proposing anything)

1. **The null-check is not optional and not skippable for "just a quick look."**
   ~40 rules in `known-issues.md` WHAT NOT TO DO plus the "Low-Priority /
   Do-Not-Retry Traps" section of `NEW_LEADS.md` plus the trap registry in
   `.claude/docs/traps.md` represent real compute already spent. Skipping the
   check and re-running a closed experiment is the single most wasteful
   failure mode this skill exists to prevent.
2. **Retry requires a genuinely NEW mechanism class, not a rephrasing.** "Try
   it with a different lookback" or "what if we gate it differently" on a
   closed axis is NOT a new class. The bar used throughout the null registry:
   name the mechanism in one sentence and ask whether that sentence already
   appears, reworded, in a closed entry. If yes, stop. Concrete examples of
   *rephrasing that still counts as the same closed idea*: "%-at-ATH" ≡
   "narrow breadth" ≡ "froth at highs" (closed axis — `known-issues.md`
   WHAT NOT TO DO, grep "froth-at-highs", 2026-06-26 entry); "VIX weekly-MACD"
   ≡ "VIX velocity" ≡ "VIX acceleration" ≡ "VIX momentum crossover" (one
   closed axis, same file).
3. **`Score.overall` re-shaping is a DEAD, not just-null, class as of 2026-07.**
   Reweighting, normalizing, calibrating to P(win), z-scoring/percentile-
   rescaling, cliff-smoothing without the dropped-rows test, regime-
   conditioned dynamic weighting — all separately closed on honest v70-v74
   evidence (`gameplan.md` §4 calls this "Multi-layer closure 2026-06-24/25").
   Do not propose a scoring-gradient idea without first confirming it isn't
   one of these under a new name.
4. **The Stage-3 DD-sizing well is DRY.** Six levers already ship
   (RXDD/SVR/MWDD/TVDD/BDIV/F3F); the 2026-06-08 `dd_residual2_v70` sweep
   screened every remaining market-context axis (NH/NL, breadth-velocity,
   %-above-EMA, VIX-velocity, conviction-tier contraction) and found nothing
   orthogonal. A new portfolio-DD lead needs **extraordinary evidence** — pass
   the orthogonality gate (item 3 below) before spending queue time.
5. **Directional PRICE-technical alpha is a closed frontier at the score
   level.** The 2026-06-03/04 hunt (divergence, per-stock knobs, per-stock
   normalization, relative strength) converged on one finding, quoted
   verbatim from `NEW_LEADS.md` "Strategic framing": **opt15 WR15 is ~45-50%
   for essentially EVERY price-technical partition** — outcome is vol-path-
   dominated, not direction-dominated. A new price-technical lead needs to
   explain why it escapes this wall, not just present a new partition of the
   same price data.
6. **User intuitions get the identical null-check, not a pass.** Several
   closed axes in the registry started as a plausible-sounding user
   intuition ("breadth is narrow, that should hurt calls" — backwards; "VIX
   spikes, calls should collapse" — the dead-hold already handles it). Being
   asked to investigate something does not exempt it from Phase 1 below.
7. **Pre-v69 evidence is look-ahead-inflated (~12pp).** Any lead whose
   supporting numbers cite a pre-`8b59206c3` (2026-05-31) version or an
   un-flagged `NEW_LEADS.md`/`MISS_CANDIDATES.md` row needs re-verification
   against a post-honest-era pack before it's trusted — see GUARD 8 for the
   two known-stale headers.
8. **Known-stale surfaces — read the number, distrust the header.**
   `alpha_mining/NEW_LEADS.md` top-of-file banner still says "Last verified:
   2026-06-09" and "Active scoring in this checkout: v60" — both wrong (an
   inline 2026-06-03 note corrects to v70, and the real active version as of
   2026-07 is v74; verify with `trader algorithm active`, never trust the
   header). `alpha_mining/MISS_CANDIDATES.md`'s entire priority table carries
   a 2026-06-09 STATUS banner marking it **formally INVALIDATED** as an
   action queue (mined on a generic barrier, re-tested null on the live
   apex15 barrier) — treat it as history/reproducibility only, never as a
   ranked backlog to pull from.

## Phase 1 — Read the map before hunting

Three documents, in this order, before touching any code:

1. **`.claude/docs/gameplan.md`** — the current strategic layer (authored
   2026-07-06; if this checkout's copy is older, treat its dates as a floor,
   not gospel — machine truth in `strategy_config.py` /
   `portfolio_profiles.json` / `trader algorithm active` always wins over any
   doc). Section 4 "The alpha frontier" is the fastest gate: it verdicts every
   known class (ACCEPT / REFINE / BACKLOG / HOLD / **DEAD**) in one table. If
   your idea's class shows DEAD there, stop before Phase 2. Section 8
   "Standing anti-goals" is the condensed null wall for a first pass; the
   full detail is `known-issues.md` WHAT NOT TO DO (below).
2. **`alpha_mining/NEW_LEADS.md`** (1008 lines, per GUARD 8 header caveat) —
   the ranked lead backlog. Read `## Lead Ranking` for open/staged leads
   (status prefixes baked into headings: ✅ RESOLVED, ❌ TESTED-NULL, ◐
   PARTLY SHIPPED — an unmarked heading is open), then `## Low-Priority /
   Do-Not-Retry Traps` and `### Strategic framing` in full — these two
   sections are exactly the null registry for alpha-mining specifically
   (distinct from, but overlapping, `known-issues.md`). As of 2026-07, the
   highest-ranked genuinely open leads are **N2** (equity-milestone glide
   path, Stage 3, lifecycle-keyed not market-context-keyed so it doesn't
   re-open the dry well) and **N3** (historical options/IV data acquisition —
   now superseded by the priced plan in
   [data-acquisition.md](../../docs/data-acquisition.md), which routes to
   `gameplan.md` §5 P2). Verify current ranking directly — this list moves.
3. **`known-issues.md` `## WHAT NOT TO DO`** (~40 entries, ~line 958-999 as of
   2026-07 — confirm with `grep -n "^## WHAT NOT TO DO" .claude/docs/known-issues.md`
   since line numbers drift) — the hard-rule registry, each entry: what was
   tried, the evidence artifact, why it failed. Grep it and
   `known-issues-archive.md` for your topic's keywords; read every match, do
   not skim. `.claude/docs/traps.md` is the consolidated cross-cutting trap
   registry (infra, evidence honesty, process, modeling/metrics, tooling
   staleness); check it too.

This is the same registry `/run-experiment` Phase 0 checks — if you're about
to spin up an experiment directory anyway, do the null-check once there and
skip re-running it here.

## Phase 2 — The null-check ritual

```bash
cd C:/Development/Trader
grep -n -i "<mechanism keyword 1>\|<keyword 2>\|<keyword 3>" .claude/docs/known-issues.md
grep -n -i "<same keywords>" .claude/docs/known-issues-archive.md
grep -n -i "<same keywords>" alpha_mining/NEW_LEADS.md
grep -n -i "<same keywords>" .claude/docs/traps.md
```

Pick keywords from the **mechanism**, not the surface framing — a user saying
"the market feels toppy, shouldn't we cut calls" is the mechanism "breadth/
regime collapse → cut calls," which greps to `MWDD`, `BDIV`, `breadth`,
`collapse`, `Market-Wave`, and is CLOSED (three separate closed formulations
per `known-issues.md`: raw correlation-decoupling, SPY-up/breadth-down
divergence, the literal `collapse_flag` — all mean-reversion WINNERS in the
all-levers-off slice). Read every match; a single keyword hit that doesn't
actually cover your formulation is not clearance — read the artifact.

If nothing hits: this is likely genuinely new. Move to Phase 3. If something
hits but you believe your formulation escapes it (new mechanism class per
GUARD 2), **write down explicitly why** before building anything — this note
is what a reviewer needs to accept the retry, and it's what the eventual
FINDINGS.md "why this isn't the closed X" paragraph starts from.

## Phase 3 — Cheap decisive pre-tests (kill it before you build it)

Full catalog with worked examples lives at
[run-experiment/references/cheap-pretests.md](../run-experiment/references/cheap-pretests.md)
(read that file, don't re-derive it here — it is the canonical version).
Quick index, mapped to the idea shape you're likely holding:

| Idea shape | Cheap pre-test | What a NULL looks like |
|---|---|---|
| "This band/cohort should have better/worse EV" | EV-by-band on the existing tape (no MC) | Flat or monotone-wrong-direction across the band |
| "Smooth this component cliff, it looks noisy" | Dropped-rows apex-WR test (ScoreSimulator A/B) | Dropped rows have ABOVE-average WR — the cliff is a signal, not noise (this happened for MACD, `known-issues.md` WHAT NOT TO DO line 3) |
| "Reweight components by regime" | Component-tercile sign-consistency across ≥2 regime classifiers (composite AND VIX) | Sign flips across classifiers = noise, not a regime signal (this is the exact result on TREND/RSI/MACD, 2026-06-25) |
| "This weekly/lagging feature has edge" | Day-of-week look-ahead split (Mon vs Fri optTP) — mandatory, see GUARD 3 in `/find-and-ship-alpha` | Monday ≫ Friday, Friday ≈ baseline = look-ahead, not alpha |
| "New Stage-3 DD lever" | All-levers-off orthogonality slice + panic-exclusion check | Candidate dissolves once the confound lever is toggled off, OR inverts to good in the crash band (mean-reversion winner, not a lever) |
| "Does this survive scale" | N-escalation stability, 100→300→500 (screen at 300, ship at 500) | Sign or magnitude flips between N tiers = it was noise at the smaller N |

If the pre-test kills it (this is the *expected* outcome for most ideas at
this point — the registry above exists precisely because most mechanism
classes are exhausted), write the null and stop: this is the fast path, not
a shortcut around rigor. Log it per Phase 5 below even if you never open a
queue slot.

## Phase 4 — Route the survivor

| Hypothesis touches | Route to | Why |
|---|---|---|
| `Score.overall` / any score-stage component, weight, dampener, gate | [/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) | Full Stage-1 loop: option-augmented ledger, mine on OPTION-TP (not cumulative WR15 — GUARD 1 there), growth-gate sweep with real supply, ship path |
| Portfolio-stage DD/exposure/cascade/dead-hold/profile lever | The DD-active-subset method below, then [/run-monte-carlo](../run-monte-carlo/SKILL.md) for the A/B, then [/ship-gates](../ship-gates/SKILL.md) Stage 3 | Stage-3 findings live in the MC trade tape, not the score ledger |
| Anything needing a fresh `experiments/<name>/` directory, a worktree, or FINDINGS.md scaffolding | [/run-experiment](../run-experiment/SKILL.md) | A→Z lifecycle (worktree, staging-native variants, evidence format, closeout) |
| Option-pricing / IV / gamma / skew / data-depth lead | [data-acquisition.md](../../docs/data-acquisition.md) + `gameplan.md` §5 P2 | This is the one confirmed-open frontier (OSK, t=+4.04 raw) but it is data-gated — the next action is usually "check the priced de-risk plan," not "start mining" |
| Ambiguous / could be either stage | [/ship-gates](../ship-gates/SKILL.md) "Which stage applies?" table | Defaults to the more restrictive stage when unsure |

### The DD-active-subset method (Stage-3 portfolio leads specifically)

The proven vehicle behind every shipped DD lever (RXDD, SVR, MWDD, TVDD,
BDIV), from `known-issues.md`/`memory.md` process rule 16:

1. Regenerate the full-lever MC trade tape: `MC_TRADE_TAPE=1` env on
   `monte_carlo.py` (queue it — this is a full sweep, not a quick check).
2. **Mine the DD-active subset** (`dd >= DD_MIN`), not the whole tape — the
   whole tape dilutes any real signal below detection.
3. A real lever needs low-EV **AND** high `dd_conc` (drawdown-dollar
   concentration) coinciding on a cohort that is:
   - **Orthogonal** — prove it in the all-shipped-levers-off slice (toggle
     every existing lever OFF; does the candidate's effect survive, or was
     it riding an already-shipped lever's coattails? — this is exactly how
     the "%-at-ATH" and three separate "breadth collapse" formulations were
     each shown redundant with MWDD/BDIV).
   - **Sign-stable across regimes** (a 2x2: does the low-EV read hold in
     both a composite-based AND a VIX-based regime split? A flip between
     the two = noise, the G26 kill-test).
   - **Panic-excluded** — always leave the extreme/crash band (VIX≥28 or
     equivalent) untouched. Every shipped lever's mid/topping band is the
     DD signal; the crash extreme is a mean-reversion WINNER, and
     contracting into it is the single most common failure mode in this
     registry (Hindenburg omens, %-at-ATH, all three Market-Wave
     formulations, VIX-momentum — all died on exactly this confusion).
4. If it clears all three, it is a genuine 7th lever candidate — extremely
   rare given GUARD 4. Escalate via the standard staged N=100→300→500
   sweep to `/ship-gates` Stage 3.

## Phase 5 — Ledger hygiene (every outcome, no exceptions)

Whether the idea SHIPPED, is STAGED, or is a fresh TESTED-NULL:

1. **Update `alpha_mining/NEW_LEADS.md` in the same turn** as the findings
   artifact (its own `## Agent Update Rule`, verbatim): ranking position +
   movement, stage tag (Stage 1/2/3/ops/null-trap), why it matters, exact
   evidence artifact paths, next experiment if any, ship gates + stop rules.
   No duplicate notes — merge into the existing lead, rerank, move exhausted
   ideas to `## Low-Priority / Do-Not-Retry Traps` with the blocker named.
2. **Append a `known-issues.md` WHAT NOT TO DO entry** for a fresh null
   (house style: what was tried → evidence artifact → why it failed → what
   would count as a genuinely new retry). This is the registry Phase 2 reads
   for the NEXT person — an unlogged null gets re-mined.
3. **If it shipped:** also touch `version-history.md` (Stage 1) or the
   relevant `known-issues.md` ship-state section (Stage 3), per
   [/ship-version](../ship-version/SKILL.md) or the Stage-3 ship checklist in
   [/ship-gates](../ship-gates/SKILL.md).
4. `alpha_mining/README.md`'s rule applies verbatim: keep these docs compact,
   ranked, current; preserve enough evidence paths that another agent can
   continue without replaying terminal history; **this file is not
   permission to bump `ALGORITHM_VERSION`** — ship gates and deploy workflow
   still apply regardless of what the lead ledger says.

## Evidence / see also

- [.claude/docs/gameplan.md](../../docs/gameplan.md) — strategic layer, §4
  alpha-frontier verdict table, §8 anti-goals.
- [alpha_mining/README.md](../../../alpha_mining/README.md),
  [alpha_mining/NEW_LEADS.md](../../../alpha_mining/NEW_LEADS.md) — canonical
  lead ledger (header stale, list current — GUARD 8).
- [.claude/docs/known-issues.md](../../docs/known-issues.md) WHAT NOT TO DO —
  the hard-rule registry.
- [.claude/docs/traps.md](../../docs/traps.md) — consolidated cross-cutting
  trap registry.
- [/find-and-ship-alpha](../find-and-ship-alpha/SKILL.md) — Stage-1
  execution loop.
- [/run-experiment](../run-experiment/SKILL.md) — experiment lifecycle,
  shares the Phase 0 null-check and the cheap-pretests catalog.
- [/ship-gates](../ship-gates/SKILL.md) — stage routing table, thresholds.
- [/run-monte-carlo](../run-monte-carlo/SKILL.md) — Stage-3 A/B mechanics.
- [.claude/docs/data-acquisition.md](../../docs/data-acquisition.md) — the
  one confirmed-open frontier's de-risk plan.

## Self-update

If you hit a trap this skill missed — a "new" idea that turned out to be a
rephrased closed axis not covered above, or a cheap pre-test that should be
in the quick-index table — append it to GUARDS here **and** to
`.claude/docs/traps.md` in the same session.
