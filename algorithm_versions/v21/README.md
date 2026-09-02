# v21 Algorithm Snapshot

- Status: `shipped`
- DB version: `21`
- Commit: `aba4f5d`
- Resolved commit: `aba4f5dade4700322370f84a8d727c8b0e0c7ed6`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Add ext-focal gradient dampener for puts above EMA50

AlgorithmVersion message: Add ext-focal gradient dampener for puts above EMA50
Commit subject: Add ext-focal gradient dampener for puts above EMA50

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v20`
Diff range: `66af13d6..aba4f5da`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 131 +++++++++++++++++++++++++++-------------------
database/models/core.py   |   7 +++
database/utils/scoring.py |  32 +++++++++++
trader.py                 |   6 ++-
5 files changed, 122 insertions(+), 56 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:22` - - **Ext-focal gradient dampener** (shipped 2026-04-20 as v21, `aba4f5d`): puts (overall <= 25) with price ABOVE EMA50 are profit-taking pullbacks in uptrends, not breakdown setups.
- `.claude/docs/trading-strategy.md:285` - Each year independently from $50k, 500 iterations per (year x mode).
- `.claude/docs/trading-strategy.md:214` - Each year independently from $50k, 500 iterations per (year x mode).

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
