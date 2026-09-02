# v66 Algorithm Snapshot

- Status: `ship_candidate`
- DB version: `66`
- Commit: `05d75b4ae`
- Resolved commit: `05d75b4aef0cc7409bbec4b764132bd1265d3ef1`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v66 scoring: apply weekly momentum envelope to v60

AlgorithmVersion message: v66 scoring: apply weekly momentum envelope to v60
Commit subject: v66 scoring: apply weekly momentum envelope to v60

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v65`
Diff range: `14a5981c..05d75b4a`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 441 ++++-------------------------------------
backtest_cascade.py       | 286 ++------------------------
backtest_cascade_15dte.py |  44 +---
database/models/core.py   | 496 +++++-----------------------------------------
database/utils/scoring.py | 147 +++++++++++---
mechanism_registry.py     |  39 +---
monte_carlo.py            | 298 ++--------------------------
simulator.py              |  22 --
strategy_config.py        |  61 +-----
trader.py                 | 487 ++++++++-------------------------------------
11 files changed, 337 insertions(+), 1986 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.WEEKLY_MOMENTUM_MAX_CONFIDENCE_BARS` = `5`

### Structured Portfolio Variable Delta

- `mechanism_registry[2].notes`: `When running portfolio DD ? [LO, HI], scale call alloc linearly from 1.0 down to FLOOR. Above HI = full floor. Below...` -> `When running portfolio DD ? [LO, HI], scale call alloc linearly from 1.0 down to FLOOR. Above HI = full floor. Below...`
- `mechanism_registry[3].config_fields`: `["PRACTICAL_EXPOSURE_ENABLED", "PRACTICAL_CAPITAL_CEILING", "GROSS_PREMIUM_CAP", "CALL_PREMIUM_CAP", "PUT_PREMIUM_CAP...` -> `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...`
- `mechanism_registry[3].dte_15_reason`: `Not validated for 15 DTE. The half-DTE portfolio already uses a smaller eight-slot pool and different tail/theta dyna...` -> ``
- `mechanism_registry[3].dte_15_status`: `disabled` -> `enabled`
- `mechanism_registry[3].dte_15_wiring_mode`: `not_wired` -> `n/a`
- `mechanism_registry[3].engine_files_15`: `[]` -> `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- `mechanism_registry[3].engine_files_30`: `["monte_carlo.py", "backtest_cascade.py"]` -> `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- `mechanism_registry[3].name`: `PRACTICAL_EXPOSURE_SATURATION` -> `F3F_BREADTH_ALLOC`
- `mechanism_registry[3].notes`: `Stage 3 Sentinel profile candidate g80_c65_p25_ref16_4_pow05_floor55_25m. Caps deployable premium to a practical base...` -> `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...`
- `mechanism_registry[3].ship_date_15`: `` -> `2026-04-24`
- `mechanism_registry[3].ship_date_30`: `2026-05-21` -> `2026-04-24`
- `mechanism_registry[4].config_fields`: `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...` -> `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]`
- `mechanism_registry[4].name`: `F3F_BREADTH_ALLOC` -> `DEAD_HOLD_POST_SL`
- `mechanism_registry[4].notes`: `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...` -> `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...`
- `mechanism_registry[4].ship_date_15`: `2026-04-24` -> `2026-05-01`
- `mechanism_registry[4].ship_date_30`: `2026-04-24` -> `2026-05-01`
- `mechanism_registry[5].config_fields`: `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]` -> `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- `mechanism_registry[5].name`: `DEAD_HOLD_POST_SL` -> `CT_CASCADE_PROMOTION`
- `mechanism_registry[5].notes`: `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...` -> `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...`
- `mechanism_registry[5].ship_date_15`: `2026-05-01` -> `2026-04-21`
- `mechanism_registry[5].ship_date_30`: `2026-05-01` -> `2026-04-21`
- `mechanism_registry[6].config_fields`: `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]` -> `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]`
- `mechanism_registry[6].dte_15_reason`: `` -> `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.`
- `mechanism_registry[6].dte_15_status`: `enabled` -> `disabled`
- Removed `mechanism_registry[8].config_fields` = `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...`
- Removed `mechanism_registry[8].dte_15_reason` = `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...`
- Removed `mechanism_registry[8].dte_15_status` = `disabled`
- Removed `mechanism_registry[8].dte_15_wiring_mode` = `wired_neutral`
- Removed `mechanism_registry[8].dte_30_reason` = `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...`
- Removed `mechanism_registry[8].dte_30_status` = `disabled`
- Removed `mechanism_registry[8].dte_30_wiring_mode` = `wired_neutral`
- Removed `mechanism_registry[8].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Removed `mechanism_registry[8].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Removed `mechanism_registry[8].name` = `WEAK_WEEKLY_CALL_DROP`
- Removed `mechanism_registry[8].notes` = `See experiments/call_wadj_70_filter/FINDINGS.md for history.`
- Removed `mechanism_registry[8].ship_date_15` = ``
- Removed `mechanism_registry[8].ship_date_30` = `2026-05-05`
- Removed `strategies.15dte.CALL_PREMIUM_CAP` = `0.0`
- Removed `strategies.15dte.GROSS_PREMIUM_CAP` = `0.0`
- Removed `strategies.15dte.OPP_SAT_CALL_REF` = `0.0`
- Removed `strategies.15dte.OPP_SAT_FLOOR` = `0.0`
- Removed `strategies.15dte.OPP_SAT_POWER` = `1.0`
- Removed `strategies.15dte.OPP_SAT_PUT_REF` = `0.0`
- Removed `strategies.15dte.PRACTICAL_CAPITAL_CEILING` = `0.0`
- Removed `strategies.15dte.PRACTICAL_EXPOSURE_ENABLED` = `False`
- Removed `strategies.15dte.PUT_PREMIUM_CAP` = `0.0`
- Removed `strategies.30dte.CALL_PREMIUM_CAP` = `0.65`
- Removed `strategies.30dte.GROSS_PREMIUM_CAP` = `0.8`
- ... 27 additional structured changes omitted.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
