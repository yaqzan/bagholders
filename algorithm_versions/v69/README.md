# v69 Algorithm Snapshot

- Status: `shipped`
- DB version: `69`
- Commit: `8b59206c3e4778f0eea1d240c34a48522ee64d1d`
- Resolved commit: `8b59206c3e4778f0eea1d240c34a48522ee64d1d`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout)

AlgorithmVersion message: scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout)
Commit subject: scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v68`
Diff range: `bb6251c1..8b59206c`

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
api.py                    | 736 +++++++++++++++++++++++++++++++++++++++++-----
backtest_cascade.py       | 480 ++++++++++++++++++++++++++++--
backtest_cascade_15dte.py |  44 ++-
database/models/core.py   | 574 ++++++++++++++++++++++++++++++++----
database/utils/scoring.py | 139 +++------
mechanism_registry.py     |  81 ++++-
monte_carlo.py            | 455 +++++++++++++++++++++++++++-
simulator.py              |  55 +++-
strategy_config.py        | 130 +++++---
trader.py                 | 676 ++++++++++++++++++++++++++++++++++++------
11 files changed, 2952 insertions(+), 420 deletions(-)
```

### Structured Scoring Variable Delta

Structured diff unavailable for this version.

### Structured Portfolio Variable Delta

Structured diff unavailable for this version.

## Source References

- `.claude/docs/known-issues.md:122` - **v69 weekly transition blend shipped + ACTIVE 2026-05-31 (`8b59206c3`, DB version 69)** - FIRST honest (look-ahead-free) scoring version.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
