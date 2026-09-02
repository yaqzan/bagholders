# v6 Algorithm Snapshot

- Status: `shipped`
- DB version: `6`
- Commit: `061362d`
- Resolved commit: `061362d62db5f9310aa1fee618dd331a65cc3b94`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

merge conflict

AlgorithmVersion message: merge conflict
Commit subject: merge conflict

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v5`
Diff range: `a8cfc750..061362d6`

Changed snapshot-tracked paths:
- `api.py`
- `database/models/core.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`

Git diff stat:

```text
api.py                  | 118 ++++++++++++-----
database/models/core.py | 336 +++++++++++++++++++++++++++++++++++++++---------
recalculate_scores.py   |  94 +++++++++-----
simulator.py            |  15 ++-
trader.py               | 111 ++++++++--------
5 files changed, 488 insertions(+), 186 deletions(-)
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
