# v9 Algorithm Snapshot

- Status: `shipped`
- DB version: `9`
- Commit: `9c8cb86`
- Resolved commit: `9c8cb86a1ac96f0fe8101b93f2b7fa017db476ec`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

26.04.07 - Fix vol amplification over-firing in three scenarios

AlgorithmVersion message: 26.04.07 - Fix vol amplification over-firing in three scenarios
Commit subject: 26.04.07 - Fix vol amplification over-firing in three scenarios

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v8`
Diff range: `a1a32a27..9c8cb86a`

Changed snapshot-tracked paths:
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`

Git diff stat:

```text
api.py                    |  87 +++++++++++++++++----
database/models/core.py   | 193 ++++++++++++++++++++++++++++++++++++++++++++--
database/utils/scoring.py |  28 +++++++
recalculate_scores.py     |  14 +++-
simulator.py              |   5 ++
trader.py                 |  34 --------
6 files changed, 302 insertions(+), 59 deletions(-)
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
