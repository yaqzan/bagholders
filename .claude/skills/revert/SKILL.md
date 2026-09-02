---
name: revert
description: Hot-swap the active scoring algorithm to any silo'd AlgorithmVersion by flipping the ALGORITHM_VERSION pointer. No git checkout — silos are self-contained and loaded on demand by algorithm_versions/runtime.py. Use when the user types /revert with a version id (v60), numeric id (60), or git hash.
---

# /revert — Hot-swap the active scoring algorithm to a silo'd version

Scoring lives in self-contained silos under `algorithm_versions/`. Every silo's
engine is loaded on demand by `algorithm_versions/runtime.py:load_scoring_engine`,
and the DB holds version-keyed score rows for each (`scores.version_id`). So
"reverting" is no longer a git operation that restores working-tree files — it is
flipping the `ALGORITHM_VERSION` pointer to the target silo. **No working-tree
files change; there is nothing to `git checkout`.**

This supersedes the old "restore scoring files from a prior commit" procedure.
Because silos are frozen and self-contained, there is no scope list to maintain
and no `strategy_config.py`/drift-guard coupling to worry about.

## Arguments

`/revert <token>` where token is one of:
- `v60` or `60` — AlgorithmVersion id
- full or prefix git short hash (e.g. `d4a3e9f`, `d4a`)

If no token is passed, ask the user which version.

## What `trader revert <token>` does (`revert_version.py`)

1. Resolves the token → target `AlgorithmVersion` (`assess_scores.resolve_algorithm_version`).
2. Writes the target commit hash into the `ALGORITHM_VERSION` file.
3. Demotes any newer `AlgorithmVersion` rows (clears their `git_message`) so the
   reader path `AlgorithmVersion.get_active_scores_version()` resolves back to the
   target. **Score rows are preserved** — nothing is rebuilt or deleted.

## Steps

1. **Confirm the target silo exists.** Check `algorithm_versions/<vNN>/` is a
   complete silo (has `scoring/`, `manifest.json`). If it's mid-creation, stop and
   tell the user.
2. **Run the swap:**
   ```bash
   cd c:/Development/Trader && python trader.py revert <token>
   ```
3. **Verify** the printed `Reader (get_active_scores_version) now resolves to: …`
   matches the intended target, and report it to the user.

## Reader vs writer — the one thing to know

- The flip changes which version the **API / dashboard dropdown serves** (the
  reader). Pre-computed score rows for the target are served immediately.
- For **future `trader update` writes** to also behave like the target, the
  target silo must be in the scoring **cadence** (`_cadence_version_tokens` →
  `score_calculation_service` loads it via `runtime.load_scoring_engine`). The
  pointer flip alone does not change what new scores are computed with — and it
  deliberately does **not** edit working-tree code.

## What NOT to do

- Do **not** `git checkout` scoring files — the silo is the frozen code; restoring
  files into the working tree is the obsolete pre-silo workflow.
- Do **not** run `trader recalculate` — the DB already holds the target version's
  rows; the point of the pointer flip is to avoid a multi-hour rebuild.
- Do **not** delete or renumber `AlgorithmVersion` rows.
- Do **not** commit/push automatically — the `ALGORITHM_VERSION` file change is the
  user's to commit.
