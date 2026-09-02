# v33 Algorithm Snapshot

- Status: `shipped`
- DB version: `33`
- Commit: `28fa522`
- Resolved commit: `28fa5227077a3e1d9fc3cf9bae1ce5f235d230dc`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v33 scoring: continuation boost - elevate 70-74 calls with prior-winner support to 75

AlgorithmVersion message: v33 scoring: continuation boost - elevate 70-74 calls with prior-winner support to 75
Commit subject: v33 scoring: continuation boost - elevate 70-74 calls with prior-winner support to 75

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v32`
Diff range: `43eeceab..28fa5227`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `monte_carlo_15dte.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  20 +++-
backtest_cascade.py       |  15 ++-
backtest_cascade_15dte.py |  17 ++-
database/models/core.py   |  87 +++++++++++++++
database/utils/scoring.py |  96 ++++++++++++++++
monte_carlo.py            | 273 +++++++++++++++++++++++++++++++++++++++++++---
monte_carlo_15dte.py      |  17 ++-
strategy_config.py        |  43 +++++++-
trader.py                 |  99 +++++++++++------
10 files changed, 614 insertions(+), 55 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- `strategies.30dte.F3F_CALL_LOW`: `20.0` -> `30.0`
- Added `strategies.15dte.DD_SOFT_BAND_HI` = `0.0`
- Added `strategies.15dte.DD_SOFT_BAND_LO` = `0.0`
- Added `strategies.15dte.DD_SOFT_CALL_FLOOR` = `1.0`
- Added `strategies.30dte.DD_SOFT_BAND_HI` = `0.6`
- Added `strategies.30dte.DD_SOFT_BAND_LO` = `0.4`
- Added `strategies.30dte.DD_SOFT_CALL_FLOOR` = `0.5`

## Source References

- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** - ?
- `.claude/docs/known-issues.md:278` - **Version lineage to inspect:** legacy v33 continuation boost (`28fa5227`),
- `.claude/docs/active-investigations/continuation-boost.md:3` - **Status (2026-05-04):** SHIPPED as v33 (commit `28fa522`).
- `.claude/docs/active-investigations/continuation-boost.md:5` - **Follow-up flag (2026-05-13):** UAMY 2025-07-18 exposed a likely failure mode in the current continuation echo lineage: `cont_lift` can promote an exhaustion-entry candle into the tradable CALL gate.
- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** (legacy boost v33 `28fa5227`; temporal echo wave v52 `f66bf9b9`; prior-fix v53 `e3ed806`; v58 retune `3cfc4dc2` reverted 2026-05-15): same-side CALL prior wins echo into current CALL score...
- `.claude/docs/known-issues.md:145` - **Version lineage to inspect:** legacy v33 continuation boost (`28fa5227`),

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
