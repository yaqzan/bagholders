# v34 Algorithm Snapshot

- Status: `shipped`
- DB version: `34`
- Commit: `232a725`
- Resolved commit: `232a7255fe883cfc51f18d7c2bdcf697da085421`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v34 scoring: call stoch-weekly contradiction dampener (CSWC)

AlgorithmVersion message: v34 scoring: call stoch-weekly contradiction dampener (CSWC)
Commit subject: v34 scoring: call stoch-weekly contradiction dampener (CSWC)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v33`
Diff range: `28fa5227..232a7255`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/utils/scoring.py | 30 ++++++++++++++++++++++++++++++
trader.py                 |  1 -
3 files changed, 31 insertions(+), 2 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
