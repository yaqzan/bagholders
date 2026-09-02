# v67 Algorithm Snapshot

- Status: `missing`
- DB version: `67`
- Commit: `e85282f5a`
- Resolved commit: `e85282f5abceec22272a81f6fa17746ae2279ac6`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `yes`

## Intended Difference

VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump)

AlgorithmVersion message: VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump)
Commit subject: VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v66`
Diff range: `05d75b4a..e85282f5`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 441 ++++++++++++++++++++++++++++++++---
backtest_cascade.py       | 453 +++++++++++++++++++++++++++++++++---
backtest_cascade_15dte.py |  44 +++-
database/models/core.py   | 468 ++++++++++++++++++++++++++++++++++----
database/utils/scoring.py | 215 ++++++++----------
mechanism_registry.py     |  81 ++++++-
monte_carlo.py            | 435 +++++++++++++++++++++++++++++++++--
simulator.py              |   6 +
strategy_config.py        | 132 ++++++++++-
trader.py                 | 569 ++++++++++++++++++++++++++++++++++++++--------
11 files changed, 2482 insertions(+), 364 deletions(-)
```

### Structured Scoring Variable Delta

Structured diff unavailable for this version.

### Structured Portfolio Variable Delta

Structured diff unavailable for this version.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
