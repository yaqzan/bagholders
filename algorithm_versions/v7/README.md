# v7 Algorithm Snapshot

- Status: `shipped`
- DB version: `7`
- Commit: `f1553a1`
- Resolved commit: `f1553a19a3ec5edb3c5f48bdf99ba8fff06562e6`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

recalculate cleanup

AlgorithmVersion message: recalculate cleanup
Commit subject: recalculate cleanup

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v6`
Diff range: `061362d6..f1553a19`

Changed snapshot-tracked paths:
- `database/models/core.py`
- `recalculate_scores.py`

Git diff stat:

```text
database/models/core.py | 18 ++++++++----
recalculate_scores.py   | 77 ++++++++++++++++++++++++++++++++++++-------------
2 files changed, 70 insertions(+), 25 deletions(-)
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
