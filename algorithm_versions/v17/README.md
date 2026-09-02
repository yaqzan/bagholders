# v17 Algorithm Snapshot

- Status: `shipped`
- DB version: `17`
- Commit: `ea8b9fe`
- Resolved commit: `ea8b9febe6924b9047df00791321973dd2293361`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Remove momentum confirmation gradient; cross-version volume seed fallback

AlgorithmVersion message: Remove momentum confirmation gradient; cross-version volume seed fallback
Commit subject: Remove momentum confirmation gradient; cross-version volume seed fallback

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v15`
Diff range: `83851dbb..ea8b9feb`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  69 ++++++++-
database/models/core.py   | 360 +++++++++++++++++++++++++++++++++++++++++++++-
database/utils/scoring.py |  45 ++----
trader.py                 |  90 ++++++------
5 files changed, 482 insertions(+), 84 deletions(-)
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
