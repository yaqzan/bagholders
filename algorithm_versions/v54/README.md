# v54 Algorithm Snapshot

- Status: `shipped`
- DB version: `54`
- Commit: `8af574b`
- Resolved commit: `8af574bd53efe0724432137a8436dcf370527b8b`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v54 scoring: Sector ETF breadth crash/recovery dampener

AlgorithmVersion message: v54 scoring: Sector ETF breadth crash/recovery dampener
Commit subject: Revert "Revert sector breadth score dampener"

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v53`
Diff range: `e3ed8060..8af574bd`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `database/utils/sector_breadth_wave.py`
- `market_breadth.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION                     |   2 +-
api.py                                |  53 +++++++-
database/models/core.py               |  29 +++++
database/utils/scoring.py             |  83 +++++++++++++
database/utils/sector_breadth_wave.py | 186 ++++++++++++++++++++++++++++
market_breadth.py                     | 220 +++++++++++++++++++++++++++++++++-
simulator.py                          |   2 +
strategy_config.py                    |  61 ++++++++++
trader.py                             |   6 +-
9 files changed, 634 insertions(+), 8 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.POET_BULL_DECAY` = `0.9660230462016697`
- Added `SCORING.POET_BULL_LEVEL` = `81.6150704085743`
- Added `SCORING.POET_BULL_POWER` = `1.6321451606694102`
- Added `SCORING.POET_BULL_RELEASE_K` = `0.032504722010317295`
- Added `SCORING.POET_BULL_VELOCITY` = `42.582415794006785`
- Added `SCORING.POET_BULL_WASH_LEVEL` = `32.68693593017605`
- Added `SCORING.POET_CALL_MIN` = `70`
- Added `SCORING.POET_CALL_TARGET` = `60.24227950236402`
- Added `SCORING.POET_CRASH_DECAY` = `0.8363462547374197`
- Added `SCORING.POET_CRASH_K` = `0.6076307258820699`
- Added `SCORING.POET_CRASH_POWER` = `1.9996887703843815`
- Added `SCORING.POET_PUT_K` = `0.9856983213141354`
- Added `SCORING.POET_PUT_MAX` = `25`
- Added `SCORING.POET_PUT_TARGET` = `30.678601504270443`
- Added `SCORING.POET_RELIEF_AVG5_FULL` = `54.848369518759554`
- Added `SCORING.POET_RELIEF_AVG5_START` = `34.38625198767021`
- Added `SCORING.POET_RELIEF_BRD_FULL` = `91.37244593241564`
- Added `SCORING.POET_RELIEF_BRD_START` = `35.06289226117375`
- Added `SCORING.POET_REPAIR_K` = `0.34001684822355804`
- Added `SCORING.POET_SEED_LEVEL` = `13.738768982299018`
- Added `SCORING.POET_SEED_RSI` = `33.9230904893635`
- Added `SCORING.POET_SEED_VELOCITY` = `29.23226543609054`
- Added `SCORING.POET_WAVE_ENABLED` = `True`

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
