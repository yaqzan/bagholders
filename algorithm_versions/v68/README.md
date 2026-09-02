# v68 Algorithm Snapshot

- Status: `missing`
- DB version: `68`
- Commit: `bb6251c14`
- Resolved commit: `bb6251c147540b1489e246477a75575c2cf6aa0d`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `yes`

## Intended Difference

VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave

AlgorithmVersion message: VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave
Commit subject: VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v67`
Diff range: `e85282f5..bb6251c1`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 441 +++--------------------------------
backtest_cascade.py       | 453 +++---------------------------------
backtest_cascade_15dte.py |  44 +---
database/models/core.py   | 457 ++++---------------------------------
mechanism_registry.py     |  81 +------
monte_carlo.py            | 435 ++---------------------------------
strategy_config.py        | 112 ++-------
trader.py                 | 569 ++++++++--------------------------------------
9 files changed, 243 insertions(+), 2351 deletions(-)
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
