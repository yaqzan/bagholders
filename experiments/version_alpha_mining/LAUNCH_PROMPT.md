# Overnight Honest Version-Mining → v71 Ship — Launch Prompt

Paste this into a fresh Claude Code session in `C:\Development\Trader`. It is a
**time-boxed overnight workflow**. Use the **Workflow tool** for the parallel
analysis phases (you are opted in). Track local ET time at every phase.

---

## MISSION (hard morning deliverable)
By **9:30am ET market open**:
1. Honest-recalc the **highest-potential** pre-v69 scoring versions (2020-01-01→now,
   look-ahead-free), filling an **honest-only** version compare that grows as each lands.
2. From the honest findings, form + **ship v71** with **today's scores already
   calculated and live/ready-to-action by 9:30**.

Never run a recalc into market hours. If you cannot ship a *clean* v71 in time, **keep
v70 active and report** — do NOT force a bad ship.

## HARD CONSTRAINTS
- **Check ET time every phase:** `TZ=America/New_York date`. Market opens 9:30am ET.
- **Recalcs are SERIAL** — shared MySQL is disk-bound (117GB table / 4GB buffer; ~45 min
  per 2020-now version-recalc). Never run two recalcs at once.
- **HARD STOP the recalc grind by 06:00 ET.** Reserve 06:00→09:00 for the v71
  hypothesis → sweep → ship → recalc, and 09:00→09:30 as buffer so today's v71 row is
  live before the open.
- **Contamination safety (non-negotiable):** before every recalc, set the worktree's
  `ALGORITHM_VERSION` to that version's *own* DB commit and verify
  `AlgorithmVersion.get_or_create_current().id == NN`. Candidate commits point at the
  WRONG version (v68's file said v59) — a miss silently corrupts another version. Do NOT
  rely on post-recalc COUNT queries (they time out under load); the pre-check is the gate.
- **Per-silo recipe ONLY**, never `--score-versions` sidecar (contamination guard).
- **Holdout lock:** any calibration sweep gates on `CALIBRATION_CUTOFF_DATE=2026-05-15`
  (`experiments/_holdout`).

## CURRENT STATE (already done — do NOT redo)
- **v70 shipped + honest + live** (active; honest EARN_BOOST recalibration, `c70d16d22`).
- **v69, v70 honest by construction** (v69 = the weekly look-ahead fix, `8b59206c3`).
- **v68 honest-recalc'd** (the proven template). Honest anchor: **75+ WR15 ≈ 50-51%**
  (v68 50.5 / v69 51.3 / v70 50.9). Any version far above that is still inflated.
- **v60 is REDUNDANT — DO NOT recalc it.** v69 IS honest-v60: the v60→v69 scoring diff is
  the weekly transition blend ONLY (0 VCBW lines), and v70 = honest-v60 + EARN_BOOST pre7.
  `rank_potential.py` already excludes it (`redundant_skip` in honest_versions.json).
  Lesson: never prioritize by inflated WR alone — v60 topped the inflated ranking but its
  honest form already exists as v69.
- **~50 versions remain.** Each = its own mechanism stack + honest weekly, so honest-vNN vs
  honest-v(NN-1) gives the per-mechanism marginal attribution.
- **Prioritize by MINING-VALUE, not age or inflated WR.** We cannot tell signal from
  inflation-noise in ANY version until honest-recalc — so do NOT deprioritize a version for
  being old or for having been reverted (that presumes the inflated-era verdicts were right,
  which is what we're disputing). The look-ahead was a WEEKLY, mechanism-dependent inflation
  (it hit weekly-leaning mechanisms hardest: EARN_BOOST pre1 was hugely inflated, pre7 was
  the honest gem). Highest expected information per recalc:
  1. **Weekly-leaning mechanisms** (honest differs MOST): v27 WCF, v32 WCF-mirror, v34/v36
     CSWC, v17 weekly-scaling, v61/v65/v66 weekly guards, v42 rolling-weekly (structural-revert
     caveat), v55/v56/v57 Market-Wave.
  2. **Mechanisms DROPPED on inflated evidence** = candidate honest gems: v40 SVD (reverted
     "precautionary", per-trade evidence intact), v33/v34, v24 — UNLESS the revert was
     structural (v42 bypassed the breakout-push bands — a real bug, not look-ahead).
  3. Other distinct shipped mechanisms: v59 volume-authority, v50 SCW, v43 MCD, v44 ICH,
     v37 PCD, v39/v38 PESS/CWWD, v25 mis-stress.
  Only deprioritize on **honest** evidence: v68/VCBW was recalc'd and added ~0 honestly, so
  skip its v67 VCBW twin. EARN_BOOST (v28/v35) is already mined → v70 (confirm-only).
  `rank_potential.py` flags WEEKLY* and re-eval to guide this; its inflated-WR sort is just a
  display, NOT the priority.
- Tools are built in `experiments/version_alpha_mining/`:
  - `honest_versions.json` — registry of honest versions (seed [68,69,70]). **APPEND**
    each version id the moment its honest recalc+assess finish.
  - `rank_potential.py` — ranks not-yet-honest versions by inflated WR15 (prioritization).
  - `compare_honest.py` — honest-ONLY compare by bucket (inflated withheld); grows with
    the registry; flags best-version-per-bucket.

## READ FIRST
Memory: `project_honest_cross_version_recalc.md` (the recipe + v59 landmine),
`project_earnboost_honest_recal.md` (EARN_BOOST→pre7 worked example),
`project_v69_weekly_blend_ship.md`, `reference_weekly_recalc_lookahead.md`,
`version-scorecard-framework.md`, `project_stage1_growth_gate.md`.
Also `experiments/earnboost_honest/FINDINGS.md` and `.claude/docs/assessment-backtest.md`
(Stage-1 W1-W6 gate + the growth gate).

---

## PHASE 0 — Prioritize (cheap, ~5 min)
`python experiments/version_alpha_mining/rank_potential.py` (its inflated-WR sort is just a
display — do NOT prioritize by it; see its WEEKLY*/re-eval flags + the mining-value rules in
CURRENT STATE above). Pick ~8-10 that fit the budget (~45 min/version, grind stops 06:00 ET),
ordered by expected information: **weekly-leaning mechanisms first** (honest differs most),
then **dropped-on-inflated-evidence** re-eval candidates, then other distinct shipped
mechanisms. Do not pre-judge by age or inflated WR. Write the chosen ordered list +
rationale to `experiments/version_alpha_mining/FINDINGS.md`.

## PHASE 1 — Honest-recalc grind (SERIAL, stop 06:00 ET)
For each chosen version `vNN`, highest-potential first:
```
# 1. worktree at vNN's DB commit  (commit = SELECT git_commit FROM algorithm_versions WHERE id=NN)
git worktree add ../Trader-honest-vNN <vNN_commit>
cd ../Trader-honest-vNN
# 2. apply the v69 weekly fix (auto-merges; if a version CONFLICTS, resolve minimally or skip+log)
git cherry-pick -n -x 8b59206c3
# 3. pin the version + VERIFY (the safety gate)
echo "<vNN_commit>" > ALGORITHM_VERSION
python -c "from database.models.core import AlgorithmVersion as A; \
  assert A.get_or_create_current().id==NN, 'WRONG VERSION — ABORT'; print('SAFE: version_id=NN')"
# 4. recalc 2020-01-01..now, scores-only (overwrites inflated rows with honest)
#    use a lookback that reaches 2020-01-01 (~2400 days). Verify the printed date range starts <=2020-01-01.
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u trader.py recalculate 2400 --force --scores-only
# 5. assess from MAIN (warm cache) — do this from C:/Development/Trader, NOT the worktree
cd /c/Development/Trader
PYTHONIOENCODING=utf-8 python -u trader.py assess --force --version NN
# 6. append NN to honest_versions.json, then compare + validate
python experiments/version_alpha_mining/compare_honest.py
python experiments/earnboost_honest/validate_honest_version.py NN   # honest-vs-inflated, sanity (expect 75+ ~50-51%)
git worktree remove --force ../Trader-honest-vNN
```
After each version: append to the registry, re-run `compare_honest.py`, and note in
FINDINGS.md **what that version's mechanism is best at** (honest, per bucket) + the
honest-vs-inflated delta (the deflation = how much of its edge was look-ahead). If a
version lands far from the ~50-51% anchor on 75+, the recipe failed for it — investigate
or skip+log, don't trust it.

**Sanity:** v68's honest recalc was a clean template (75+ 67.6→50.5; consistent footing;
v59 untouched). Expect the same shape.

## PHASE 2 — Portfolio params watch (secondary, runs alongside)
A separate agent is sweeping the optimal **sl/tp/allocation per regime**. The emerging
edge (per the user): a **hold-to-expiry / no-cash-recycling** profile, or **wide SL ~75%
regardless of regime + TP ~33%** — a big departure from the current fast-recycle MC.
Watch for that agent's params file (ask the user for the path if unknown; likely under
`experiments/` or `.codex/runs/`). When present, run the **portfolio** version-compare on
the honest scores using those params (this is the *proper* version comparison the user
wants). Until it appears, the scoring recalc + per-trade honest compare proceed — scoring
is independent of sl/tp.

## PHASE 3 — Form the v71 hypothesis (~06:00-07:00 ET)
From `compare_honest.py` + per-version FINDINGS: which honest mechanism is **best at
what** (strongest honest WR15 on a binding tier, with z≥3 + 1y/3y/5y sign-consistency on
the affected cohort)? Form a v71 scoring hypothesis — recalibrate/combine the best honest
mechanism onto the v70 honest base (the EARN_BOOST→pre7 pattern: find the honest core,
re-fit it). Use the cached honest research packs for the quick screen. Gate via Stage-1
W1-W6 (`assessment-backtest.md`) + the growth gate
(`experiments/version_scorecard/stage1_growth_gate.py`) — run `signal_supply.py` for the
candidate FIRST (the gate false-SHIPs without real supply).

## PHASE 4 — Ship v71 + go live (~07:00-09:00 ET; buffer to 09:30)
**Only if** the candidate cleanly passes Stage-1 (W1-W6 + growth gate SHIP, holdout-locked):
1. commit scoring (scoped — leave any unrelated WIP unstaged) → bump `ALGORITHM_VERSION`
   to the new commit (separate commit) → run drift-guard + registry (pre-commit hook).
2. `python trader.py recalculate 1d --force` FIRST — gets **today's v71 row live** (the
   ready-to-action requirement). Confirm v71 is the active version + today's row exists.
3. then `recalculate --force` (5y) for dashboard depth; defer `--force --full` (10y) to
   after the open. `assess --force`. Snapshot the silo
   (`trader algorithm snapshot-git-ref --status shipped`).
**If NO clean candidate by ~08:30 ET:** keep v70 active, ensure v70's today row is live
(`recalculate 1d`), and write the v71 hypothesis + honest compare to FINDINGS.md for the
user to action manually. A correct no-ship beats a rushed bad ship.

## WORKFLOW USAGE
Use the Workflow tool to **fan out the per-version ATTRIBUTION** (read each honest
version's assess, compute affected-cohort honest WR vs predecessor, flag alpha) and the
**Phase 3 candidate screen** across agents. Run **Phase 1 recalcs as a SERIAL loop**
(DB-bound — do NOT put recalc writes in a parallel fan-out). The recalc grind is the
wall-clock bottleneck; the workflow accelerates the analysis, not the recalc.

## DELIVERABLES
- `experiments/version_alpha_mining/FINDINGS.md` — chosen order, per-version honest-vs-
  inflated + best-at-what, the honest compare table, ranked re-mine candidates, the v71
  rationale.
- `honest_versions.json` grown with every recalc'd version.
- **v71 shipped + today's scores live by 9:30am ET** (or v70 kept live + v71 hypothesis
  documented, with the reason).
- A one-paragraph morning summary: what's honest-best-at-what, what v71 is (or why not),
  and the open portfolio-comparison step (pending the sl/tp params).
