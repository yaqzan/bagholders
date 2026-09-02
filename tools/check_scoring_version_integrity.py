"""Scoring-version integrity check (closeout assertion + pre-push hook + CI).

Asserts that the live resolved scoring config/formula matches the recorded lock
for the AlgorithmVersion the `ALGORITHM_VERSION` pointer resolves to. Run it:

  * after absorbing/abandoning a candidate branch "without applying tree changes"
    — to prove no scoring flag/constant was left flipped (the v60-contamination
    class: a v6x scoring mechanism left enabled after the candidate was dropped);
  * as a pre-push hook on any branch that touches scoring code/config;
  * before/at the start of any production score write (the runtime guard in
    database/scoring_version_guard.py enforces this automatically; this is the
    manual/CI form).

Exit 0 = live scoring config matches the active version's lock (or no lock yet —
reported as a warning). Exit 1 = drift. Exit 2 = could not evaluate.

Override: TRADER_DEFINE_VERSION=1 makes the guard pass (ship path: minting /
redefining a version — then re-capture the lock).
"""
import sys

try:
    from database.scoring_version_guard import (
        verify_active_before_write,
        ScoringVersionMismatch,
        production_scoring_fingerprint,
        _load_locks,
    )
    from database.models.core import AlgorithmVersion
except Exception as exc:  # pragma: no cover
    print(f"[scoring-integrity] could not import guard/models: {exc}")
    sys.exit(2)


def main() -> int:
    try:
        cur = AlgorithmVersion.get_or_create_current()
    except Exception as exc:
        print(f"[scoring-integrity] could not resolve current version: {exc}")
        return 2
    try:
        fp = production_scoring_fingerprint()
    except Exception as exc:
        print(f"[scoring-integrity] could not fingerprint live scoring: {exc}")
        return 2
    lock = _load_locks().get(getattr(cur, "git_commit", None))
    print(f"[scoring-integrity] ALGORITHM_VERSION -> v{cur.id} ({cur.git_commit})")
    print(f"[scoring-integrity] live scoring fingerprint: {fp[:16]}")
    if lock is None:
        print(f"[scoring-integrity] WARNING: no scoring lock recorded for v{cur.id} "
              f"({cur.git_commit}); cannot verify. Run "
              f"`trader algorithm lock-scoring` / capture_lock() to enable enforcement.")
        return 0
    print(f"[scoring-integrity] locked fingerprint:      {lock.get('fingerprint','')[:16]}")
    try:
        verify_active_before_write(context="integrity-check")
    except ScoringVersionMismatch as exc:
        print("[scoring-integrity] FAIL")
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"[scoring-integrity] could not evaluate: {exc}")
        return 2
    print(f"[scoring-integrity] OK: live scoring config matches the v{cur.id} lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
