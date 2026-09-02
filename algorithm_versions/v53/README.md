# v53 Algorithm Snapshot

- Status: `shipped`
- DB version: `53`
- Commit: `e3ed806`
- Resolved commit: `e3ed806012b06eb544b0e79e24a6a07caf368644`
- Category: `scoring_context`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v53: Fix temporal echo recalc priors

AlgorithmVersion message: v53: Fix temporal echo recalc priors
Commit subject: Fix temporal echo recalc priors

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v52`
Diff range: `f66bf9b9..e3ed8060`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`

Git diff stat:

```text
ALGORITHM_VERSION       |  2 +-
database/models/core.py | 61 +++++++++++++++++++++++++++++++++++--------------
2 files changed, 45 insertions(+), 18 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** - ?
- `.claude/docs/known-issues.md:280` - recalc-prior fix (`e3ed806`), and the active v58 retune (`3cfc4dc2`).
- `.claude/docs/active-investigations/continuation-boost.md:5` - **Follow-up flag (2026-05-13):** UAMY 2025-07-18 exposed a likely failure mode in the current continuation echo lineage: `cont_lift` can promote an exhaustion-entry candle into the tradable CALL gate.
- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** (legacy boost v33 `28fa5227`; temporal echo wave v52 `f66bf9b9`; prior-fix v53 `e3ed806`; v58 retune `3cfc4dc2` reverted 2026-05-15): same-side CALL prior wins echo into current CALL score...
- `.claude/docs/known-issues.md:147` - recalc-prior fix (`e3ed806`), and the active v58 retune (`3cfc4dc2`).

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
