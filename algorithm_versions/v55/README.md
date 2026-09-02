# v55 Algorithm Snapshot

- Status: `shipped`
- DB version: `55`
- Commit: `bfad76a`
- Resolved commit: `bfad76abbba9c6f58a0cd59026fa59ede1546741`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Ship sector breadth seed099 score dampener

AlgorithmVersion message: Ship sector breadth seed099 score dampener
Commit subject: Ship sector breadth seed099 score dampener

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v54`
Diff range: `8af574bd..bfad76ab`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `database/utils/sector_breadth_wave.py`
- `market_breadth.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION                     |   2 +-
api.py                                |  14 ++++
database/models/core.py               |  14 ++++
database/utils/scoring.py             | 120 +++++++++++++---------------------
database/utils/sector_breadth_wave.py |   2 +-
market_breadth.py                     | 112 +++++++++++++++++++++++++------
strategy_config.py                    | 103 ++++++++++++-----------------
trader.py                             |  14 ++++
8 files changed, 222 insertions(+), 159 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.SECTOR_BREADTH_WAVE_CALL_MIN` = `70`
- Added `SCORING.SECTOR_BREADTH_WAVE_ENABLED` = `True`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_decay` = `0.8277690110627226`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_level` = `62.03404845065244`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_power` = `2.088278407656689`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_release_k` = `0.8708083294000379`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_velocity` = `60.646925383623845`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_wash_level` = `26.22552523349634`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.call_target` = `67.9111696519399`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_decay` = `0.8293421380642926`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_k` = `0.7749893764666053`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_power` = `1.1759310984435702`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_k` = `0.18819897434805938`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_target` = `43.99705733058449`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_full` = `59.4646105892826`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_start` = `54.4646105892826`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_full` = `94.02551477406715`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_start` = `56.42302402263576`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_k` = `0.19416269886562104`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_level` = `12.874193897976879`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_rsi` = `39.80229597898166`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_velocity` = `26.351273803670814`
- Added `SCORING.SECTOR_BREADTH_WAVE_PUT_MAX` = `25`
- Removed `SCORING.POET_BULL_DECAY` = `0.9660230462016697`
- Removed `SCORING.POET_BULL_LEVEL` = `81.6150704085743`
- Removed `SCORING.POET_BULL_POWER` = `1.6321451606694102`
- Removed `SCORING.POET_BULL_RELEASE_K` = `0.032504722010317295`
- Removed `SCORING.POET_BULL_VELOCITY` = `42.582415794006785`
- Removed `SCORING.POET_BULL_WASH_LEVEL` = `32.68693593017605`
- Removed `SCORING.POET_CALL_MIN` = `70`
- Removed `SCORING.POET_CALL_TARGET` = `60.24227950236402`
- Removed `SCORING.POET_CRASH_DECAY` = `0.8363462547374197`
- Removed `SCORING.POET_CRASH_K` = `0.6076307258820699`
- Removed `SCORING.POET_CRASH_POWER` = `1.9996887703843815`
- Removed `SCORING.POET_PUT_K` = `0.9856983213141354`
- Removed `SCORING.POET_PUT_MAX` = `25`
- Removed `SCORING.POET_PUT_TARGET` = `30.678601504270443`
- Removed `SCORING.POET_RELIEF_AVG5_FULL` = `54.848369518759554`
- Removed `SCORING.POET_RELIEF_AVG5_START` = `34.38625198767021`
- Removed `SCORING.POET_RELIEF_BRD_FULL` = `91.37244593241564`
- Removed `SCORING.POET_RELIEF_BRD_START` = `35.06289226117375`
- Removed `SCORING.POET_REPAIR_K` = `0.34001684822355804`
- Removed `SCORING.POET_SEED_LEVEL` = `13.738768982299018`
- Removed `SCORING.POET_SEED_RSI` = `33.9230904893635`
- Removed `SCORING.POET_SEED_VELOCITY` = `29.23226543609054`
- Removed `SCORING.POET_WAVE_ENABLED` = `True`

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
