# v61 Algorithm Snapshot

- Status: `shipped`
- DB version: `61`
- Commit: `e6fbdbde1`
- Resolved commit: `e6fbdbde10ee66784d3ab4fc2eee9237e97cfc94`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v61 scoring: add weekly mature call guard

AlgorithmVersion message: v61 scoring: add weekly mature call guard
Commit subject: v61 scoring: add weekly mature call guard

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v60`
Diff range: `d4a3e9fe..e6fbdbde`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `simulator.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  15 +++++
database/models/core.py   |  12 ++++
database/utils/scoring.py | 165 ++++++++++++++++++++++++++++++++++++++++++++++
mechanism_registry.py     |   8 ++-
simulator.py              |  29 ++++++--
strategy_config.py        |  54 ++++++++++++---
7 files changed, 269 insertions(+), 16 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_ENABLED` = `True`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_mid` = `6.358089714737812`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_weight` = `0.25938373337360876`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_width` = `2.3620738069477167`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_mid` = `68.73904300497583`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_weight` = `0.4363515673549808`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_width` = `5.201703912769646`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.k` = `0.21365891783568708`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_mid` = `3.5606659252920165`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_relief` = `0.45138359953402063`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_width` = `3.661404150277635`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_hi` = `81.42326751772556`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_hi_width` = `3.1545639155873073`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_lo` = `72.49981169711549`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_lo_width` = `4.318306546556302`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.target` = `60.18061302183849`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_mid` = `0.11181277557817569`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_weight` = `0.42930945850864727`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_width` = `0.037548436669286936`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_mid` = `0.08491400832688323`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_weight` = `0.06294045942335882`
- Added `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_width` = `0.022388072253294658`

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
