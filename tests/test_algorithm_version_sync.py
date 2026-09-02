"""Drift-guard: ensure ALGORITHM_VERSION file points at the scoring commit.

If `database/utils/scoring.py`, `database/models/core.py`, or `simulator.py` was
modified in HEAD vs HEAD~ but ALGORITHM_VERSION doesn't match either HEAD or
HEAD~, recalc will tag scores under the wrong version (v33-formula-in-v32-rows
incident, broken three times). This test catches the mismatch BEFORE the 45-min
recalc burns. Merge commits are allowed when the pin matches the latest scoring
formula commit brought in by the merge side.

Allowed states:
  - File matches HEAD (ship complete, file points at scoring commit)
  - File matches HEAD~ (mid-ship: scoring commit landed, ALGORITHM_VERSION bump pending)
  - HEAD doesn't touch scoring files (any non-scoring change is fine)

Run: python tests/test_algorithm_version_sync.py
"""
from __future__ import annotations
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_VERSION_FILE = ROOT / 'ALGORITHM_VERSION'
PINNED_FORMULA_FILES = {
    'database/utils/scoring.py',
    'simulator.py',
}
SCORING_FILES = {
    *PINNED_FORMULA_FILES,
    'database/models/core.py',
}


def _git(*args: str) -> str:
    return subprocess.check_output(
        ['git', '-C', str(ROOT), *args], text=True
    ).strip()


def _try_git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ['git', '-C', str(ROOT), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return ''


def _short(ref: str) -> str:
    return _try_git('rev-parse', '--short', ref) or ref.strip()


def _latest_touch(ref: str, files: set[str]) -> str:
    return _try_git('log', '-n', '1', '--format=%h', ref, '--', *sorted(files))


def main() -> None:
    if not ALGORITHM_VERSION_FILE.exists():
        print(f'OK: {ALGORITHM_VERSION_FILE.name} does not exist (no version pinning)')
        return

    file_pin = _short(ALGORITHM_VERSION_FILE.read_text().strip())
    head = _git('rev-parse', '--short', 'HEAD')
    head_parent = _git('rev-parse', '--short', 'HEAD~')

    if file_pin == head or file_pin == head_parent:
        print(f'OK: ALGORITHM_VERSION ({file_pin}) matches HEAD ({head}) or HEAD~ ({head_parent})')
        return

    latest_formula = _latest_touch('HEAD', PINNED_FORMULA_FILES)
    merge_head = _try_git('rev-parse', '--short', 'MERGE_HEAD')
    merge_formula = _latest_touch('MERGE_HEAD', PINNED_FORMULA_FILES) if merge_head else ''
    if file_pin in {latest_formula, merge_formula}:
        source = 'MERGE_HEAD' if file_pin == merge_formula else 'HEAD'
        print(f'OK: ALGORITHM_VERSION ({file_pin}) matches latest scoring formula commit on {source}')
        return

    # File doesn't match HEAD or HEAD~. Check if HEAD modified scoring files.
    head_files = set(_git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').split('\n'))
    parent_files = set(_git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD~').split('\n'))
    touched_scoring = (head_files | parent_files) & SCORING_FILES

    if not touched_scoring:
        print(f'OK: ALGORITHM_VERSION ({file_pin}) is older than HEAD~ but neither '
              f'HEAD ({head}) nor HEAD~ ({head_parent}) modified scoring files.')
        return

    raise AssertionError(
        f'ALGORITHM_VERSION ({file_pin}) does not match HEAD ({head}) or HEAD~ ({head_parent}), '
        f'and recent commits modified scoring files: {sorted(touched_scoring)}. '
        f'Bump ALGORITHM_VERSION to the scoring commit before running recalculate, '
        f'or scores will tag under the wrong AlgorithmVersion. See deploy.md.'
    )


if __name__ == '__main__':
    main()
