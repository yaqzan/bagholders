# v32 Algorithm Snapshot

- Status: `shipped`
- DB version: `32`
- Commit: `43eecea`
- Resolved commit: `43eeceab2f8d4ba6aed417e88d53c7f560a4d925`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v32 scoring: call-side WCF-mirror dampener (Priority #4 close)

AlgorithmVersion message: v32 scoring: call-side WCF-mirror dampener (Priority #4 close)
Commit subject: v32 scoring: call-side WCF-mirror dampener (Priority #4 close)

## Existing Documentation Hint

Source heading: `v32 - Call-Side WCF-Mirror Dampener (2026-05-01, `43eecea`)` (from `.claude/docs/version-history-archive.md`)

Mirrors the v27 put WCF lift on the call side. When `overall >= 75 ? wadj < 1`, dampens the score down toward 55:

## Code Delta From Previous Resolved Version

Previous resolved key: `v31`
Diff range: `f3ec7c12..43eeceab`

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
api.py                    | 206 ++++++++-----
backtest_cascade.py       | 603 +++++++++++++++++++++++++++---------
backtest_cascade_15dte.py | 554 +++++++++++++++++++++++++--------
database/models/core.py   |  77 +++++
database/utils/scoring.py |  33 ++
monte_carlo.py            | 439 ++++++++++++++++----------
monte_carlo_15dte.py      | 761 ++++++++++++++++++++++++++++++++--------------
strategy_config.py        | 499 ++++++++++++++++++++++++++++++
trader.py                 | 262 +++++++++-------
10 files changed, 2596 insertions(+), 840 deletions(-)
```

### Structured Scoring Variable Delta

- Removed `extraction_error` = `strategy_config.py is not present at this git ref`

### Structured Portfolio Variable Delta

- Added `assess_combos[0]` = `["30", "wr"]`
- Added `assess_combos[1]` = `["30", "tp"]`
- Added `mechanism_registry[0].config_fields` = `["CTSL_ENABLED", "CTSL_CALL_TREND_MAX", "CTSL_CALL_TARGET", "CTSL_CALL_ALPHA", "CTSL_CALL_TREND_POWER", "CTSL_CALL_TI...`
- Added `mechanism_registry[0].dte_15_reason` = `Not validated under bounded-fill MC for half-DTE strategy. Stage 1 WR7 calibration + Stage 3 N=500x8 portfolio MC ran...`
- Added `mechanism_registry[0].dte_15_status` = `disabled`
- Added `mechanism_registry[0].dte_15_wiring_mode` = `not_wired`
- Added `mechanism_registry[0].dte_30_reason` = ``
- Added `mechanism_registry[0].dte_30_status` = `enabled`
- Added `mechanism_registry[0].dte_30_wiring_mode` = `n/a`
- Added `mechanism_registry[0].engine_files_15` = `[]`
- Added `mechanism_registry[0].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py"]`
- Added `mechanism_registry[0].name` = `CTSL`
- Added `mechanism_registry[0].notes` = `Score-stage continuous lift applied at signal-load time inside monte_carlo.load_signals / load_put_signals (and mirro...`
- Added `mechanism_registry[0].ship_date_15` = ``
- Added `mechanism_registry[0].ship_date_30` = `2026-05-08`
- Added `mechanism_registry[1].config_fields` = `["SAW_PUT_UCURVE_ENABLED", "SAW_PUT_UCURVE_SHAPE", "SAW_PUT_UCURVE_MIDPOINT", "SAW_PUT_UCURVE_HALFWIDTH", "SAW_PUT_UC...`
- Added `mechanism_registry[1].dte_15_reason` = ``
- Added `mechanism_registry[1].dte_15_status` = `enabled`
- Added `mechanism_registry[1].dte_15_wiring_mode` = `n/a`
- Added `mechanism_registry[1].dte_30_reason` = ``
- Added `mechanism_registry[1].dte_30_status` = `enabled`
- Added `mechanism_registry[1].dte_30_wiring_mode` = `n/a`
- Added `mechanism_registry[1].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[1].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py"]`
- Removed `assess_combos` = ``
- Removed `extraction_error` = `strategy_config.py is not present at this git ref`
- Removed `mechanism_registry` = `[]`
- Removed `strategies.15dte` = ``
- Removed `strategies.30dte` = ``
- ... 236 additional structured changes omitted.

## Source References

- `.claude/docs/version-history-archive.md:1226` - 43eecea v32 scoring: call-side WCF-mirror dampener (Priority #4 close) [v32]
- `.claude/docs/version-history-archive.md:1227` - 27829a3 Bump ALGORITHM_VERSION to 43eecea (v32 call-WCF-mirror dampener)
- `.claude/docs/version-history-archive.md:1718` - ## v32 - Call-Side WCF-Mirror Dampener (2026-05-01, `43eecea`)
- `.claude/docs/scoring-algorithm.md:37` - - **Call-side WCF-mirror dampener (CWCF)** - ?
- `.claude/docs/known-issues.md:424` - ### 4.
- `.claude/docs/version-history.md:1282` - 43eecea v32 scoring: call-side WCF-mirror dampener (Priority #4 close) [v32]
- `.claude/docs/version-history.md:1283` - 27829a3 Bump ALGORITHM_VERSION to 43eecea (v32 call-WCF-mirror dampener)
- `.claude/docs/version-history.md:1774` - ## v32 - Call-Side WCF-Mirror Dampener (2026-05-01, `43eecea`)
- `.claude/docs/scoring-algorithm.md:37` - - **Call-side WCF-mirror dampener** (shipped 2026-05-01 as v32, `43eecea`): mirror of the v27 put WCF on the call side.
- `.claude/docs/known-issues.md:249` - ### 4.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
