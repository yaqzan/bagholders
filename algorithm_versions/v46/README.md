# v46 Algorithm Snapshot

- Status: `shipped`
- DB version: `46`
- Commit: `f274eb6`
- Resolved commit: `f274eb6550258f7dc39de7e5cc3de4d539caaa32`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v46 scoring: WVD-Wave score-stage inverted-U modulator on weekly volume force1

AlgorithmVersion message: v46 scoring: WVD-Wave score-stage inverted-U modulator on weekly volume force1
Commit subject: v46 scoring: WVD-Wave score-stage inverted-U modulator on weekly volume force1

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v45`
Diff range: `56eb1f83..f274eb65`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
backtest_cascade.py       |  68 ++++++++++++++++++++-
database/models/core.py   |  66 +++++++++++++++++---
database/utils/scoring.py | 150 ++++++++++++++++++++++++++++++++++++++++++++++
monte_carlo.py            |  16 ++---
simulator.py              |  13 +++-
strategy_config.py        |  55 +++++++++++++++++
trader.py                 |  61 +++++++++++++++++++
8 files changed, 412 insertions(+), 19 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- Added `strategies.15dte.SAW_PUT_UCURVE_CEIL` = `1.35`
- Added `strategies.15dte.SAW_PUT_UCURVE_ENABLED` = `False`
- Added `strategies.15dte.SAW_PUT_UCURVE_FLOOR` = `0.55`
- Added `strategies.15dte.SAW_PUT_UCURVE_HALFWIDTH` = `18.0`
- Added `strategies.15dte.SAW_PUT_UCURVE_K` = `5.0`
- Added `strategies.15dte.SAW_PUT_UCURVE_MIDPOINT` = `72.0`
- Added `strategies.15dte.SAW_PUT_UCURVE_POWER` = `3.0`
- Added `strategies.15dte.SAW_PUT_UCURVE_SHAPE` = `quadratic`
- Added `strategies.30dte.SAW_PUT_UCURVE_CEIL` = `1.35`
- Added `strategies.30dte.SAW_PUT_UCURVE_ENABLED` = `True`
- Added `strategies.30dte.SAW_PUT_UCURVE_FLOOR` = `0.55`
- Added `strategies.30dte.SAW_PUT_UCURVE_HALFWIDTH` = `18.0`
- Added `strategies.30dte.SAW_PUT_UCURVE_K` = `5.0`
- Added `strategies.30dte.SAW_PUT_UCURVE_MIDPOINT` = `72.0`
- Added `strategies.30dte.SAW_PUT_UCURVE_POWER` = `3.0`
- Added `strategies.30dte.SAW_PUT_UCURVE_SHAPE` = `quadratic`

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
