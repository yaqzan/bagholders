# v38 Algorithm Snapshot

- Status: `shipped`
- DB version: `38`
- Commit: `b093e2d`
- Resolved commit: `b093e2d1612a6dfc9b359615a3a7b6082760f809`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75

AlgorithmVersion message: v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75
Commit subject: v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v37`
Diff range: `6f9afda9..b093e2d1`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `monte_carlo_15dte.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 124 +++++++++++++++++++++
backtest_cascade.py       |  43 +++++++-
backtest_cascade_15dte.py |  43 +++++++-
database/utils/scoring.py |  38 +++++++
monte_carlo.py            | 270 +++++++++++++++++++++++++++++++++++++++-------
monte_carlo_15dte.py      |  49 ++++++++-
strategy_config.py        |  33 ++++++
trader.py                 |  53 ++++++++-
9 files changed, 606 insertions(+), 49 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- Added `strategies.15dte.WEAK_WEEKLY_CALL_DROP` = `False`
- Added `strategies.15dte.WEAK_WEEKLY_CALL_MAX_OV` = `84`
- Added `strategies.15dte.WEAK_WEEKLY_CALL_MIN_OV` = `70`
- Added `strategies.15dte.WEAK_WEEKLY_CALL_STOCH_GE` = `35`
- Added `strategies.15dte.WEAK_WEEKLY_CALL_WADJ_LT` = `0.0`
- Added `strategies.30dte.WEAK_WEEKLY_CALL_DROP` = `False`
- Added `strategies.30dte.WEAK_WEEKLY_CALL_MAX_OV` = `84`
- Added `strategies.30dte.WEAK_WEEKLY_CALL_MIN_OV` = `70`
- Added `strategies.30dte.WEAK_WEEKLY_CALL_STOCH_GE` = `35`
- Added `strategies.30dte.WEAK_WEEKLY_CALL_WADJ_LT` = `0.0`

## Source References

- `.claude/docs/version-history-archive.md:1254` - b093e2d v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75 [v38]
- `.claude/docs/version-history-archive.md:1255` - 86ddb4f Bump ALGORITHM_VERSION to b093e2d (v38 CWWD)
- `.claude/docs/version-history-archive.md:1353` - 2.
- `.claude/docs/scoring-algorithm.md:33` - - **CWWD - Call Weak-Weekly Dampener** (shipped 2026-05-06 as v38, `b093e2d`): score-stage extension of CWCF below 75 (CWCF gates `overall>=75 ?
- `.claude/docs/version-history.md:1310` - b093e2d v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75 [v38]
- `.claude/docs/version-history.md:1311` - 86ddb4f Bump ALGORITHM_VERSION to b093e2d (v38 CWWD)
- `.claude/docs/version-history.md:1409` - 2.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
