# v3 Algorithm Snapshot

- Status: `shipped`
- DB version: `3`
- Commit: `b1cc55c`
- Resolved commit: `b1cc55cb1f15d0c2d790187bd67362f3369ff6b3`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Systemic backfill

AlgorithmVersion message: Systemic backfill
Commit subject: Systemic backfill

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v2`
Diff range: `c1f8fcf9..b1cc55cb`

Changed snapshot-tracked paths:
- `database/models/core.py`
- `recalculate_scores.py`
- `trader.py`

Git diff stat:

```text
database/models/core.py | 185 +++++++++++++++++++++++++++++++++++++++++++++---
recalculate_scores.py   | 103 +--------------------------
trader.py               |  35 ++++++++-
3 files changed, 213 insertions(+), 110 deletions(-)
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
