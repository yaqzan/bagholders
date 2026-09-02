# v65 Algorithm Snapshot

- Status: `shipped`
- DB version: `65`
- Commit: `14a5981cb`
- Resolved commit: `14a5981cb3b986af401f134d30e374867e856334`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v65 scoring: unify weekly partial context

AlgorithmVersion message: v65 scoring: unify weekly partial context
Commit subject: v65 scoring: unify weekly partial context

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v64`
Diff range: `1bba5f96..14a5981c`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 225 ++++++++++++++++----
backtest_cascade.py       |  34 +++-
backtest_cascade_15dte.py |  44 +++-
database/models/core.py   | 507 +++++++++++++++++++++++++++++++++++++++++-----
database/utils/scoring.py | 356 ++++----------------------------
mechanism_registry.py     |   2 +-
simulator.py              |  31 ++-
strategy_config.py        |  54 +----
trader.py                 | 410 ++++++++++++++++++++++++++++++-------
10 files changed, 1094 insertions(+), 571 deletions(-)
```

### Structured Scoring Variable Delta

- Removed `SCORING.BB_LOCATION_TAPER_CALL_DAMPEN` = `3.0`
- Removed `SCORING.BB_LOCATION_TAPER_CALL_GATE` = `75`
- Removed `SCORING.BB_LOCATION_TAPER_CALL_MID_HI` = `65.0`
- Removed `SCORING.BB_LOCATION_TAPER_CALL_MID_LO` = `35.0`
- Removed `SCORING.BB_LOCATION_TAPER_ENABLED` = `True`
- Removed `SCORING.BB_LOCATION_TAPER_PUT_BB_LO` = `35.0`
- Removed `SCORING.BB_LOCATION_TAPER_PUT_GATE` = `25`
- Removed `SCORING.BB_LOCATION_TAPER_PUT_LIFT` = `3.0`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_ENABLED` = `True`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.agreement_w` = `0.6495131420472546`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.bias_mid` = `1.625528689023521`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.bias_width` = `8.994565973938322`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.comp_mid` = `69.80982351744979`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.comp_width` = `6.2851248864358675`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.cont_w` = `1.9850176086859672`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.damp_max` = `0.8`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.extension_w` = `0.9395830256136597`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.fresh_power` = `1.4571831994748121`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.mom_mid` = `1.7472197255054263`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.mom_width` = `2.3258830798657355`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.promote_max` = `2.75`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.regime_w` = `0.5657739250452438`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.risk_power` = `2.200368604352241`
- Removed `SCORING.CALL_BOUNDARY_FRESHNESS_PARAMS.score_damp_power` = `1.5192401267522992`
- ... 4 additional structured changes omitted.

### Structured Portfolio Variable Delta

- `mechanism_registry[3].notes`: `Stage 3 v62 candidate g80_c65_p25_ref16_4_pow05_floor55_25m. Caps deployable premium to a practical base: capital cei...` -> `Stage 3 Sentinel profile candidate g80_c65_p25_ref16_4_pow05_floor55_25m. Caps deployable premium to a practical base...`

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
