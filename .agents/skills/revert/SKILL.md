---
name: revert
description: Revert the scoring algorithm to a prior AlgorithmVersion. Resolves the target version's commit, diffs scoring files against HEAD, restores them intelligently, and flips ALGORITHM_VERSION. Use when the user types /revert with a version id (v14), numeric id (14), or git hash.
---

# /revert — Revert scoring algorithm to a prior version

Goal: restore scoring behavior to a target AlgorithmVersion by reverting code AND flipping the `ALGORITHM_VERSION` pointer, so existing score rows for that version are used and future writes match.

## Arguments

The user invokes `/revert <token>` where token is one of:
- `v14` or `14` — AlgorithmVersion id
- full or prefix git short hash (e.g. `83851db`, `838`)

If no token is passed, ask the user which version.

## Scope — what to revert

Only these files are "scoring code". Restore from the target commit:

- `database/utils/scoring.py`
- `database/models/core.py`
- `volume_amplifier.py`
- `market_regime.py`
- `market_breadth.py`
- `historic_peaks.py`
- `dte_recommendation.py`

If `git diff` shows changes in non-scoring files between target and HEAD, leave those alone (the user explicitly wants only scoring reverted — unrelated features on HEAD stay).

## Steps

1. **Resolve the version → commit.** Run:
   ```bash
   cd c:/Development/Trader && python -c "from assess_scores import resolve_algorithm_version; v = resolve_algorithm_version('<token>'); print(v.id, v.git_commit, v.git_message) if v else print('NOT FOUND')"
   ```
   Abort if not found. Record `<target-sha>` and `<target-id>`.

2. **Verify the commit exists locally.** `git cat-file -e <target-sha>^{commit}`. If missing, tell the user to `git fetch` first.

3. **Check current state.**
   - `git status --porcelain` — abort if scoring files are dirty; ask the user to commit/stash first.
   - `git rev-parse --short HEAD` → record `<head-sha>`. If it equals `<target-sha>`, nothing to revert; flip ALGORITHM_VERSION only if needed and stop.
   - `cat ALGORITHM_VERSION` → record current pointer.

4. **Diff scoring files: HEAD vs target.** For each file in the scope list:
   ```bash
   git diff <target-sha>..HEAD -- <file>
   ```
   Summarize to the user: which scoring files differ, and roughly what changed (function signatures, constants, new functions). This is the "intelligent compare" step — surface anything that looks risky BEFORE restoring.

5. **Flag cross-file dependencies.** Check if HEAD code outside the scope list calls new symbols introduced after `<target-sha>` in scoring files. Grep for added top-level names from step 4's diff:
   ```
   Grep pattern="<new_symbol_name>" — exclude scope-list files
   ```
   If callers exist, warn the user — restoring will break those callers. Ask whether to proceed anyway, add the caller's file to the scope, or abort.

6. **Confirm plan with the user.** Print:
   - Target: `v<id> <sha> "<message>"`
   - Current HEAD: `<head-sha>`
   - Files to restore: (list)
   - Risks surfaced in step 5 (if any)
   - ALGORITHM_VERSION: `<current>` → `<target-sha>`

   Wait for user confirmation before mutating anything.

7. **Restore scoring files from target:**
   ```bash
   git checkout <target-sha> -- database/utils/scoring.py database/models/core.py \
       volume_amplifier.py market_regime.py market_breadth.py \
       historic_peaks.py dte_recommendation.py
   ```

8. **Flip ALGORITHM_VERSION.** Write `<target-sha>` into the `ALGORITHM_VERSION` file (single line, no trailing text beyond a newline).

9. **Show `git status`** so the user sees the pending changes. Do NOT commit automatically — leave staging/committing to the user unless they ask otherwise.

10. **Report:**
    - Files restored
    - ALGORITHM_VERSION new value
    - Reminder: old version's DB scores (peaks + assessment) already exist and will be used by the API once `ALGORITHM_VERSION` flip takes effect — no `recalculate` needed.
    - Reminder: today's `trader update` run will write new score rows under the target version's commit — those rows will be produced by the freshly restored code, so they'll be consistent with v<id>.

## What NOT to do

- Do not run `git revert <sha>` — messy multi-commit chain, touches non-scoring files.
- Do not commit or push — the user reviews first.
- Do not run `trader recalculate` — the point of this approach is to AVOID the 3-hour rebuild.
- Do not reach outside the scope list without flagging it in step 5 and getting confirmation.
- Do not delete or renumber `AlgorithmVersion` rows in the DB.

## CLI fallback

If the user just wants the pointer flip without code changes, `trader revert <token>` does that directly. This skill is the richer "code + pointer" path.
