# v20 Algorithm Snapshot

- Status: `shipped`
- DB version: `20`
- Commit: `66af13d`
- Resolved commit: `66af13d682984eb9386032c6eda1c61acab152cd`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Add capitulation gradient dampener to scoring

AlgorithmVersion message: Add capitulation gradient dampener to scoring
Commit subject: Add capitulation gradient dampener to scoring

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v19`
Diff range: `6656daa4..66af13d6`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `recalculate_scores.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |   5 +++
database/models/core.py   |  80 +++++++++++++++++++++++++++++-------
database/utils/scoring.py |  11 +++++
monte_carlo.py            |   6 +--
recalculate_scores.py     |   4 +-
trader.py                 | 102 ++++++++++++++++++++++++++++++++++------------
7 files changed, 163 insertions(+), 47 deletions(-)
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
