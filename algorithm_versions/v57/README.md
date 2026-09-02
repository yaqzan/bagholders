# v57 Algorithm Snapshot

- Status: `shipped`
- DB version: `57`
- Commit: `e568b2f4`
- Resolved commit: `e568b2f4d8b646b296c1eff693462df9dfa98805`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Ship direct Market Wave score transform

AlgorithmVersion message: Ship direct Market Wave score transform
Commit subject: Ship direct Market Wave score transform

## Existing Documentation Hint

Source heading: `v57 (`e568b2f4`) - 2026-05-13 (Direct Market Wave Score Transform)` (from `.claude/docs/version-history-archive.md`)

Score-stage ship. v57 replaced the earlier v56 dual-wave Market Wave dampener
with the direct Market Wave score transform (`bayes_185`). It is the baseline
for the v58 continuation retune. v57 score rows were populated before v58:
active AlgorithmVersion v57, commit `e568b2f4`, pointer commit `440ebdc1`, row
coverage 2015-12-31 through 2026-05-13.

## Code Delta From Previous Resolved Version

Previous resolved key: `v56`
Diff range: `c6f384ab..e568b2f4`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/sector_breadth_wave.py`
- `market_breadth.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION                     |  2 +-
database/utils/sector_breadth_wave.py | 73 +++++++++++++++++++++++++++++++++--
market_breadth.py                     | 55 +++++++++++++++++++++++++-
strategy_config.py                    | 51 ++++++++----------------
4 files changed, 141 insertions(+), 40 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.call_target`: `67.4315538232377` -> `62.9712`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_k`: `0.14352001104338488` -> `0.356669`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_target`: `42.792526892713646` -> `28.643463`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.call_k` = `0.482941`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.mode` = `direct_market_wave`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.path` = `.cache/market_wave/predictive_market_wave_v57_source.csv`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_full` = `74.911472`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_power` = `1.660247`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_start` = `67.196295`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.stress_full` = `5.442232`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.stress_power` = `2.265087`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.stress_start` = `35.236419`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_decay` = `0.8454284640158979`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_level` = `79.1079876485021`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_power` = `0.9340250161404585`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_release_k` = `0.14149458984622557`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_velocity` = `29.375357313781933`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_wash_level` = `35.938321048622235`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_decay` = `0.858756656076235`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_k` = `0.5555511684211012`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_power` = `1.6599731280965706`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_center` = `0.425`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d15_weight` = `0.35`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d1_weight` = `1.0`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d5_weight` = `0.55`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_level200_weight` = `0.15`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_level50_weight` = `0.4`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_range10_weight` = `0.35`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_range30_weight` = `0.35`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_scale` = `1.024`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_full` = `64.8875984427319`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_start` = `56.9896680234689`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_full` = `76.12377351762899`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_start` = `58.02872184033419`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_k` = `0.7051948336752132`
- Removed `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_level` = `18.427346846669842`
- ... 2 additional structured changes omitted.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:77` - commit `e568b2f4`), restoring 80,278 v57 rows for 2025-12-16 through
- `.claude/docs/version-history-archive.md:93` - ## v57 rollback baseline (`e568b2f4`) - 2026-05-15 (v58 Retune Rolled Back)
- `.claude/docs/version-history-archive.md:97` - `ALGORITHM_VERSION` pointed back to `e568b2f4`, `strategy_config.py` restored the
- `.claude/docs/version-history-archive.md:153` - ## v57 (`e568b2f4`) - 2026-05-13 (Direct Market Wave Score Transform)
- `.claude/docs/version-history-archive.md:158` - active AlgorithmVersion v57, commit `e568b2f4`, pointer commit `440ebdc1`, row
- `.claude/docs/scoring-algorithm.md:31` - - **Sector ETF Market Wave direct transform** - ?
- `.claude/docs/known-issues.md:181` - **v57 repair note (2026-05-15)** - a stale cross-version sidecar recalc touched v57 rows in the latest 150-day window during the v59 ship.
- `.claude/docs/known-issues.md:183` - **v57 rollback baseline completed 2026-05-15 (`ALGORITHM_VERSION=e568b2f4`)** - v58's continuation-echo retune improved WR15 utility but failed the now-primary 30DTE portfolio gate.
- `.claude/docs/version-history.md:133` - commit `e568b2f4`), restoring 80,278 v57 rows for 2025-12-16 through
- `.claude/docs/version-history.md:149` - ## v57 rollback baseline (`e568b2f4`) - 2026-05-15 (v58 Retune Rolled Back)
- `.claude/docs/version-history.md:153` - `ALGORITHM_VERSION` pointed back to `e568b2f4`, `strategy_config.py` restored the
- `.claude/docs/version-history.md:209` - ## v57 (`e568b2f4`) - 2026-05-13 (Direct Market Wave Score Transform)
- `.claude/docs/version-history.md:214` - active AlgorithmVersion v57, commit `e568b2f4`, pointer commit `440ebdc1`, row
- `.claude/docs/scoring-algorithm.md:31` - - **Sector ETF Market Wave direct transform** (shipped 2026-05-13 as v57, `e568b2f4`): score-stage Market Wave dampener for broad crash/recovery states.
- `.claude/docs/known-issues.md:64` - **v57 repair note (2026-05-15)** - a stale cross-version sidecar recalc touched v57 rows in the latest 150-day window during the v59 ship.
- `.claude/docs/known-issues.md:66` - **v57 rollback baseline completed 2026-05-15 (`ALGORITHM_VERSION=e568b2f4`)** - v58's continuation-echo retune improved WR15 utility but failed the now-primary 30DTE portfolio gate.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
