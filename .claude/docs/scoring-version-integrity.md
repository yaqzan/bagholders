# Scoring-Version Integrity — preventing version/config-drift contamination

**Status (2026-05-31): RESOLVED + GUARDED.** main's stored v60 rows restored to true v60; main's live code verified v60-identical; the v2 config/formula-lock guard + v60 lock + integrity check are on `main`. **Guard is dormant** (not yet called from write entry points) — see "Remaining wiring".

## What happened (v60 contamination, 2026-05-27/28)

Production `version_id=60` rows were silently rewritten with the wrong scoring behavior:
- `ALGORITHM_VERSION` (and the v60 DB row) pointed at commit `d4a3e9fec`.
- A routine `recalculate` ran, tagged rows v60, but computed them with drifted scoring behavior.
- Result: the 6 base components stayed byte-identical (data fine) but ~1.5% of final `overall` scores differed by 1-4 points near tier boundaries — enough to inflate high-tier N and depress WR vs true v60. Any "vs v60" comparison was invalid; a scoring candidate (VCBW) calibrated against the contaminated baseline showed a false win that evaporated on the clean baseline.

### Resolution (verified)

1. **Stored rows restored** — recalculated `version_id=60` from a pure `d4a3e9fec` checkout (`--force --full`). Verified clean: e.g. ONTO 2024-08-07 84->80; 80+ count 415->322 (== true v60).
2. **Live code is clean** — main's working tree, run through the scorer, produces v60-identical scores: 0/5,036 sampled 2024 signals differ vs the true `d4a3e9fec` tree. The +459 `core.py`/+104 `strategy_config.py` lines since v60 are infra (ExchangeRate, EarningsJump, SplitEvent, ScoreIntradayLog), portfolio (DTE_ROUTER, practical-exposure), and v61-v66 candidates that are flag-gated OFF/inert — none change v60's `Score.overall`.
3. **Other versions siloed** — v60-v66 each have a full `algorithm_versions/vNN/` silo (manifest + README + scoring snapshot); DB has v60-v68 (v67/v68 = rejected VCBW candidates). main is the v60 scorer.

The 05-27/28 trigger was transient intermediate code, not reproducible from main's current tree. Resolution is lock + document — prove/enforce live scoring config == v60 — rather than a risky surgical revert of interleaved scoring/infra in core.py.

## Why a file-byte fingerprint is the WRONG check (v1 guard's flaw)

Production runs a superset-of-versions + feature-flags model: one working tree carries code for many versions, `*_ENABLED` flags/constants in `strategy_config` select active mechanisms, `algorithm_versions/vNN/` silos archive snapshots. Live `strategy_config.py`/`database/models/core.py` ALWAYS differ from an old version's exact committed files (superset has extra fields; core.py carries non-scoring infra too). A raw-byte fingerprint vs the version commit false-flags every legitimate write. The contamination was a resolved-config behavior, so the right check is a resolved scoring config + formula lock (v2 guard below, replaces the byte-fingerprint v1).

## The guard (`database/scoring_version_guard.py`, v2)

A scoring lock captured from the live tree when a version is declared the active production config. Fingerprint = sha256 of:
- the resolved `strategy_config.SCORING` (all flags+constants, via `dataclasses.asdict`), PLUS the effective `database/utils/scoring.py` module constants (post-`_envb/_envi/_envf`, so env overrides are caught), PLUS `CALIBRATION_CUTOFF_DATE`; and
- the `database/utils/scoring.py` formula bytes (line-ending normalized; infra-free file, so its bytes mean "the formula").

`core.py` is deliberately NOT byte-fingerprinted (interleaves scoring with infra); flag/constant-gated scoring behavior is captured via the resolved-SCORING fingerprint instead.

Behavior (verified on main, active v60):
- `verify_active_before_write(context)` compares live fingerprint to the lock for the `ALGORITHM_VERSION`-resolved version. Clean -> PASS; config drift -> REFUSE; `TRADER_DEFINE_VERSION=1` ship-override -> PASS.
- `capture_lock(version)` records the live fingerprint as that version's lock in `algorithm_versions/scoring_locks.json`.
- No lock recorded for the active version -> warns, does not block (safe rollout before locks exist).
- Lives outside the fingerprinted files (no self-reference).

Would have blocked the v60 contamination at write time.

### Residual gap (documented)

An un-flagged change to scoring orchestration in `database/models/core.py` that alters scores without changing any SCORING value or `scoring.py` would not be caught by the lock. Covered instead by the post-absorb closeout assertion + code review.

## Integrity check (`tools/check_scoring_version_integrity.py`)

Standalone form for closeout assertions, a pre-push hook, and CI. Exit 0 = live config matches the active version's lock (or no lock -> warning); exit 1 = drift. On main today: OK, exit 0 (live fingerprint == v60 lock).

## v60 lock (`algorithm_versions/scoring_locks.json`)

Captured from main for commit `d4a3e9fec`/v60 (note records the 0/5036 verification + v61-v66 flag-gated-off + siloed).

## Remaining wiring (deploy coordinated; guard currently dormant)

1. **Call the guard at every score-WRITE entry point** (all outside the fingerprinted files):
   - `trader.py` `_recalc_impl`/`_update_impl` (top):
     ```python
     from database.scoring_version_guard import verify_active_before_write
     verify_active_before_write(context="recalculate")   # / "update"
     ```
   - the cron scoring service (`score_calculation_service.py`) and `recalculate_scores.py` — same call before batch scorers run.
2. **`trader algorithm lock-scoring`** convenience command wrapping `capture_lock(get_active_scores_version())`, run whenever the active version's config is (re)declared.
3. **Pre-push hook:** invoke `tools/check_scoring_version_integrity.py`; refuse on exit 1 (mirror the existing drift-guard/registry pre-commit hook).
4. **Link this doc** from `process.md` (Git workflow/worktree closeout) and `deploy.md` (Shipping a Scoring Change) with the rules below.

## Hardened workflow rules (for process.md / deploy.md)

- **Config must match the active version at write time.** Never run `update`/`recalculate` (or any score writer) when the live resolved scoring config/formula differs from the `ALGORITHM_VERSION`-resolved version's lock. The runtime guard enforces this; do not bypass with `TRADER_SCORING_GUARD_DISABLE`.
- **Abandon/absorb closeout MUST reset ALL of the candidate's flags.** After absorbing a candidate "without applying tree changes," run `python tools/check_scoring_version_integrity.py` — exit 0 required.
- **Sidecar `--score-versions` writes only with matching config.** Never write a prior shipped version's rows from a config that doesn't reproduce its scores.
- **Refinement worktrees never write prod versions.** Env overrides used for experiments are caught by the guard (effective-constant fingerprint), so they cannot silently write production-version rows.
- **Restoring a contaminated version:** set the live config to the version's scoring config, confirm `check_scoring_version_integrity.py` is OK, then `recalculate --force --full`. (This is how v60 was restored.)
- **When a version is declared active production config:** run `trader algorithm lock-scoring` so the lock matches the live tree.
