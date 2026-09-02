# v15 Algorithm Snapshot

- Status: `shipped`
- DB version: `15`
- Commit: `83851db`
- Resolved commit: `83851dbbe129d30522f2934dd94144e44daa154b`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Fix zero-score clustering from asymmetric volume amplification

AlgorithmVersion message: none
Commit subject: Fix zero-score clustering from asymmetric volume amplification

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v14`
Diff range: `410a055a..83851dbb`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/utils/scoring.py | 30 ++++++++++++++++++++----------
2 files changed, 21 insertions(+), 11 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1174` - trader revert 83851db # by git commit (exact or unique prefix)
- `.claude/docs/version-history.md:1230` - trader revert 83851db # by git commit (exact or unique prefix)

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
