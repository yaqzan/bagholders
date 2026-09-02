# v64 Algorithm Snapshot

- Status: `ship_candidate`
- DB version: `64`
- Commit: `1bba5f965`
- Resolved commit: `1bba5f965e8ed625675527e15ce6df01aa137a2b`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Fix v64 recalc signal sigma map

AlgorithmVersion message: Fix v64 recalc signal sigma map
Commit subject: Fix v64 recalc signal sigma map

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v63`
Diff range: `7b263922..1bba5f96`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`
- `database/utils/scoring.py`
- `simulator.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
database/models/core.py   |  11 +-
database/utils/scoring.py | 291 +++++++++++++++++++++++++++++++++++++++++++---
simulator.py              |   7 +-
strategy_config.py        |  30 +++++
5 files changed, 322 insertions(+), 19 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.CALL_BOUNDARY_FRESHNESS_ENABLED` = `True`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.agreement_w` = `0.6495131420472546`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.bias_mid` = `1.625528689023521`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.bias_width` = `8.994565973938322`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.comp_mid` = `69.80982351744979`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.comp_width` = `6.2851248864358675`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.cont_w` = `1.9850176086859672`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.damp_max` = `0.8`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.extension_w` = `0.9395830256136597`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.fresh_power` = `1.4571831994748121`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.mom_mid` = `1.7472197255054263`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.mom_width` = `2.3258830798657355`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.promote_max` = `2.75`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.regime_w` = `0.5657739250452438`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.risk_power` = `2.200368604352241`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.score_damp_power` = `1.5192401267522992`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.score_damp_width` = `5.613854416441694`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.scw_w` = `1.5611613132083748`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.sigma_w` = `1.4910393105390027`
- Added `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.volume_w` = `1.124889907058259`

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
