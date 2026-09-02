# v48 Algorithm Snapshot

- Status: `shipped`
- DB version: `48`
- Commit: `61561ee`
- Resolved commit: `61561eedf1f62413dd2521fc50941e85608503d7`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v48 scoring: add stoch conviction wave

AlgorithmVersion message: v48 scoring: add stoch conviction wave
Commit subject: v47 scoring: add stoch conviction wave

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v46`
Diff range: `f274eb65..61561eed`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `monte_carlo_15dte.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 274 +++++++++++++++++--
backtest_cascade.py       | 103 +++++++
backtest_cascade_15dte.py |  78 +++++-
database/models/core.py   | 178 +++++++++++-
database/utils/scoring.py | 675 +++++++++++++++++++++++-----------------------
mechanism_registry.py     | 462 +++++++++++++++++++++++++++++++
monte_carlo.py            | 160 ++++++++++-
monte_carlo_15dte.py      | 130 ++++++++-
strategy_config.py        | 535 +++++++++++++++++++++++++++++++++++-
trader.py                 | 341 +++++++++++++++++++++--
11 files changed, 2524 insertions(+), 414 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.BB_DOMINANCE_DAMP` = `0.5`
- Added `SCORING.BLEND_MACD_HI` = `60.0`
- Added `SCORING.BLEND_MACD_LO` = `40.0`
- Added `SCORING.BLEND_REVERSAL_GATE` = `25.0`
- Added `SCORING.BLEND_REVERSAL_HALF` = `0.5`
- Added `SCORING.BLEND_VOL_TARGET_HI` = `65.0`
- Added `SCORING.BLEND_VOL_TARGET_LO` = `35.0`
- Added `SCORING.BLEND_W_CAP` = `0.6`
- Added `SCORING.CAP_BASE_LIFT` = `5.0`
- Added `SCORING.CAP_EMA_THRESH` = `-10.0`
- Added `SCORING.CAP_GATE_SCORE` = `0`
- Added `SCORING.CAP_LIFT_CAP` = `20`
- Added `SCORING.CAP_RAMP_OFFSET` = `10.0`
- Added `SCORING.CONT_BOOST_ENABLED` = `True`
- Added `SCORING.CONT_BOOST_GATE_HI` = `74`
- Added `SCORING.CONT_BOOST_GATE_LO` = `70`
- Added `SCORING.CONT_BOOST_MAG_EXP` = `0.7`
- Added `SCORING.CONT_BOOST_PROMOTE_TARGET` = `75`
- Added `SCORING.CONT_BOOST_SIG_MIN` = `0.2`
- Added `SCORING.CONT_BOOST_SIG_NORM` = `3.0`
- Added `SCORING.CONT_BOOST_TAU` = `40.0`
- Added `SCORING.CONVICTION_MACD_CAP` = `0.25`
- Added `SCORING.CSWC_DAMPEN_GATE` = `75`
- Added `SCORING.CSWC_DAMPEN_K` = `0.5`
- Removed `SCORING` = ``
- ... 127 additional structured changes omitted.

### Structured Portfolio Variable Delta

- `mechanism_registry[5].config_fields`: `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]` -> `["DD_CIRCUIT_BREAKER"]`
- `mechanism_registry[5].name`: `CT_CASCADE_PROMOTION` -> `DD_CIRCUIT_BREAKER`
- `mechanism_registry[5].notes`: `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...` -> `Pause new entries when running portfolio DD > threshold. Existing positions resolve normally. Both DTEs use 0.60.`
- `mechanism_registry[5].ship_date_15`: `2026-04-21` -> `2026-04-28`
- `mechanism_registry[5].ship_date_30`: `2026-04-21` -> `2026-05-01`
- `mechanism_registry[6].config_fields`: `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]` -> `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- `mechanism_registry[6].dte_15_reason`: `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.` -> ``
- `mechanism_registry[6].dte_15_status`: `disabled` -> `enabled`
- `mechanism_registry[6].dte_15_wiring_mode`: `wired_neutral` -> `n/a`
- `mechanism_registry[6].dte_30_reason`: `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...` -> ``
- `mechanism_registry[6].dte_30_status`: `disabled` -> `enabled`
- `mechanism_registry[6].dte_30_wiring_mode`: `wired_neutral` -> `n/a`
- `mechanism_registry[6].name`: `EARN_SUPP_PUT` -> `CT_CASCADE_PROMOTION`
- `mechanism_registry[6].notes`: `See known-issues.md "CLOSED - SHIPPED" timeline for retirement context.` -> `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...`
- `mechanism_registry[6].ship_date_15`: `2026-04-26` -> `2026-04-21`
- `mechanism_registry[6].ship_date_30`: `2026-04-26` -> `2026-04-21`
- `mechanism_registry[7].config_fields`: `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...` -> `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]`
- `mechanism_registry[7].dte_15_reason`: `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...` -> `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.`
- `mechanism_registry[7].dte_30_reason`: `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...` -> `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...`
- `mechanism_registry[7].name`: `WEAK_WEEKLY_CALL_DROP` -> `EARN_SUPP_PUT`
- `mechanism_registry[7].notes`: `See experiments/call_wadj_70_filter/FINDINGS.md for history.` -> `See known-issues.md "CLOSED - SHIPPED" timeline for retirement context.`
- `mechanism_registry[7].ship_date_15`: `` -> `2026-04-26`
- `mechanism_registry[7].ship_date_30`: `2026-05-05` -> `2026-04-26`
- `strategies.15dte.SAW_PUT_UCURVE_CEIL`: `1.35` -> `1.0`
- Added `mechanism_registry[8].config_fields` = `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...`
- Added `mechanism_registry[8].dte_15_reason` = `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...`
- Added `mechanism_registry[8].dte_15_status` = `disabled`
- Added `mechanism_registry[8].dte_15_wiring_mode` = `wired_neutral`
- Added `mechanism_registry[8].dte_30_reason` = `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...`
- Added `mechanism_registry[8].dte_30_status` = `disabled`
- Added `mechanism_registry[8].dte_30_wiring_mode` = `wired_neutral`
- Added `mechanism_registry[8].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[8].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[8].name` = `WEAK_WEEKLY_CALL_DROP`
- Added `mechanism_registry[8].notes` = `See experiments/call_wadj_70_filter/FINDINGS.md for history.`
- Added `mechanism_registry[8].ship_date_15` = ``
- Added `mechanism_registry[8].ship_date_30` = `2026-05-05`
- Added `strategies.15dte.CTSL_CALL_ALPHA` = `0.56`
- Added `strategies.15dte.CTSL_CALL_SCORE_NORM_POWER` = `2.27`
- Added `strategies.15dte.CTSL_CALL_SCORE_NORM_WEIGHT` = `0.75`
- Added `strategies.15dte.CTSL_CALL_TARGET` = `98.4`
- Added `strategies.15dte.CTSL_CALL_TIER_FLOOR` = `74.7`
- Added `strategies.15dte.CTSL_CALL_TREND_MAX` = `15`
- Added `strategies.15dte.CTSL_CALL_TREND_POWER` = `2.82`
- Added `strategies.15dte.CTSL_ENABLED` = `False`
- Added `strategies.15dte.CTSL_PUT_ALPHA` = `0.83`
- Added `strategies.15dte.CTSL_PUT_SCORE_NORM_POWER` = `1.68`
- Added `strategies.15dte.CTSL_PUT_SCORE_NORM_WEIGHT` = `-0.22`
- ... 26 additional structured changes omitted.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
