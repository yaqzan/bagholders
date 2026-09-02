# v56 Algorithm Snapshot

- Status: `shipped`
- DB version: `56`
- Commit: `c6f384ab`
- Resolved commit: `c6f384ab3288c32d864bfb71d7f89fc40e46906a`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Ship Market Wave dual-wave score dampener

AlgorithmVersion message: Ship Market Wave dual-wave score dampener
Commit subject: Ship Market Wave dual-wave score dampener

## Existing Documentation Hint

Source heading: `v56 (`c6f384ab`) - 2026-05-12 (Market Wave Dual-Wave Dampener)` (from `.claude/docs/version-history-archive.md`)

Score-stage ship. `ALGORITHM_VERSION` points to `c6f384ab` via pointer commit
`c979fc79`; full recalc is running for persisted v56 score rows at
`.cache/scoring_ships/v56_market_wave_dual_bayes150_20260512_210518`. The
mechanism is the sector ETF Market Wave dual-wave: Market Wave crash echo
dampens CALL signal-band scores, while bull-repair thrust neutralizes PUT
signal-band scores. It persists changed `Score.overall` values and exposes
`weight_info['sector_breadth_wave']` when active.

## Code Delta From Previous Resolved Version

Previous resolved key: `v55`
Diff range: `bfad76ab..c6f384ab`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`
- `database/utils/sector_breadth_wave.py`
- `market_breadth.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION                     |  2 +-
database/utils/scoring.py             |  8 +++-
database/utils/sector_breadth_wave.py | 84 +++++++++++++++++++++++++++++++++--
market_breadth.py                     | 46 ++++++++++++-------
strategy_config.py                    | 62 +++++++++++++++-----------
5 files changed, 154 insertions(+), 48 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_decay`: `0.8277690110627226` -> `0.8454284640158979`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_level`: `62.03404845065244` -> `79.1079876485021`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_power`: `2.088278407656689` -> `0.9340250161404585`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_release_k`: `0.8708083294000379` -> `0.14149458984622557`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_velocity`: `60.646925383623845` -> `29.375357313781933`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.bull_wash_level`: `26.22552523349634` -> `35.938321048622235`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.call_target`: `67.9111696519399` -> `67.4315538232377`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_decay`: `0.8293421380642926` -> `0.858756656076235`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_k`: `0.7749893764666053` -> `0.5555511684211012`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.crash_power`: `1.1759310984435702` -> `1.6599731280965706`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_k`: `0.18819897434805938` -> `0.14352001104338488`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.put_target`: `43.99705733058449` -> `42.792526892713646`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_full`: `59.4646105892826` -> `64.8875984427319`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_avg5_start`: `54.4646105892826` -> `56.9896680234689`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_full`: `94.02551477406715` -> `76.12377351762899`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.relief_brd_start`: `56.42302402263576` -> `58.02872184033419`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.repair_k`: `0.19416269886562104` -> `0.7051948336752132`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_level`: `12.874193897976879` -> `18.427346846669842`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_rsi`: `39.80229597898166` -> `33.13707121238443`
- `SCORING.SECTOR_BREADTH_WAVE_PARAMS.seed_velocity`: `26.351273803670814` -> `21.43138638181646`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_center` = `0.425`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d15_weight` = `0.35`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d1_weight` = `1.0`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_d5_weight` = `0.55`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_level200_weight` = `0.15`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_level50_weight` = `0.4`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_range10_weight` = `0.35`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_range30_weight` = `0.35`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.market_wave_scale` = `1.024`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.source` = `market_wave`

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:163` - ## v56 (`c6f384ab`) - 2026-05-12 (Market Wave Dual-Wave Dampener)
- `.claude/docs/version-history-archive.md:165` - Score-stage ship.
- `.claude/docs/version-history.md:219` - ## v56 (`c6f384ab`) - 2026-05-12 (Market Wave Dual-Wave Dampener)
- `.claude/docs/version-history.md:221` - Score-stage ship.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
