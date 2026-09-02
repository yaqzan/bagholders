# v72 Algorithm Snapshot

- Status: `shipped`
- DB version: `72`
- Commit: `fc5671200`
- Resolved commit: `fc5671200b8fbdb39a287147f9f21321fa13796c`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth)

AlgorithmVersion message: Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth)
Commit subject: Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth)

## Existing Documentation Hint

Source heading: `v72 (`fc5671200`) - 2026-06-11 (WCF Score-Gate Ramp)` (from `.claude/docs/version-history.md`)

Scoring ship (ALGORITHM_VERSION bump `97f5118e0`, DB version 72, silo
`71ee9d527`). Stability-motivated, **per-trade NEUTRAL** - smooths the v27 WCF
put-floor lift's binary score gate, the source of the largest recurring
intraday-fakeout family.

## Code Delta From Previous Resolved Version

Previous resolved key: `v71`
Diff range: `04044b21..fc567120`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  61 ++++++++++++++++++++++++-
backtest_cascade.py       | 108 +++++++++++++++++++++++++++++++++++++++++--
database/utils/scoring.py |  16 ++++++-
mechanism_registry.py     |  37 +++++++++++++++
monte_carlo.py            | 114 ++++++++++++++++++++++++++++++++++++++++++++--
strategy_config.py        |  83 +++++++++++++++++++++++++++++----
trader.py                 |  55 ++++++++++++++++++++--
8 files changed, 456 insertions(+), 20 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.WCF_LIFT_RAMP_TOP` = `33`

### Structured Portfolio Variable Delta

- `mechanism_registry[10].config_fields`: `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]` -> `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...`
- `mechanism_registry[10].name`: `DEAD_HOLD_POST_SL` -> `F3F_BREADTH_ALLOC`
- `mechanism_registry[10].notes`: `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...` -> `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...`
- `mechanism_registry[10].ship_date_15`: `2026-05-01` -> `2026-04-24`
- `mechanism_registry[10].ship_date_30`: `2026-05-01` -> `2026-04-24`
- `mechanism_registry[11].config_fields`: `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]` -> `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]`
- `mechanism_registry[11].name`: `CT_CASCADE_PROMOTION` -> `DEAD_HOLD_POST_SL`
- `mechanism_registry[11].notes`: `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...` -> `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...`
- `mechanism_registry[11].ship_date_15`: `2026-04-21` -> `2026-05-01`
- `mechanism_registry[11].ship_date_30`: `2026-04-21` -> `2026-05-01`
- `mechanism_registry[12].config_fields`: `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]` -> `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- `mechanism_registry[12].dte_15_reason`: `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.` -> ``
- `mechanism_registry[12].dte_15_status`: `disabled` -> `enabled`
- `mechanism_registry[12].dte_15_wiring_mode`: `wired_neutral` -> `n/a`
- `mechanism_registry[12].dte_30_reason`: `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...` -> ``
- `mechanism_registry[12].dte_30_status`: `disabled` -> `enabled`
- `mechanism_registry[12].dte_30_wiring_mode`: `wired_neutral` -> `n/a`
- `mechanism_registry[12].name`: `EARN_SUPP_PUT` -> `CT_CASCADE_PROMOTION`
- `mechanism_registry[12].notes`: `See known-issues.md "CLOSED - SHIPPED" timeline for retirement context.` -> `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...`
- `mechanism_registry[12].ship_date_15`: `2026-04-26` -> `2026-04-21`
- `mechanism_registry[12].ship_date_30`: `2026-04-26` -> `2026-04-21`
- `mechanism_registry[13].config_fields`: `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...` -> `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]`
- `mechanism_registry[13].dte_15_reason`: `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...` -> `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.`
- `mechanism_registry[13].dte_30_reason`: `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...` -> `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...`
- Added `mechanism_registry[14].config_fields` = `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...`
- Added `mechanism_registry[14].dte_15_reason` = `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...`
- Added `mechanism_registry[14].dte_15_status` = `disabled`
- Added `mechanism_registry[14].dte_15_wiring_mode` = `wired_neutral`
- Added `mechanism_registry[14].dte_30_reason` = `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...`
- Added `mechanism_registry[14].dte_30_status` = `disabled`
- Added `mechanism_registry[14].dte_30_wiring_mode` = `wired_neutral`
- Added `mechanism_registry[14].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[14].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[14].name` = `WEAK_WEEKLY_CALL_DROP`
- Added `mechanism_registry[14].notes` = `See experiments/call_wadj_70_filter/FINDINGS.md for history.`
- Added `mechanism_registry[14].ship_date_15` = ``
- Added `mechanism_registry[14].ship_date_30` = `2026-05-05`
- Added `strategies.15dte.BDIV_DEPTH` = `0.53`
- Added `strategies.15dte.BDIV_ENABLED` = `False`
- Added `strategies.15dte.BDIV_GAP_C` = `7.716`
- Added `strategies.15dte.BDIV_GAP_W` = `3.4571`
- Added `strategies.15dte.BDIV_PROX_CUT` = `0.0198`
- Added `strategies.15dte.BDIV_PROX_FULL` = `0.0075`
- Added `strategies.30dte.BDIV_DEPTH` = `0.53`
- Added `strategies.30dte.BDIV_ENABLED` = `True`
- Added `strategies.30dte.BDIV_GAP_C` = `7.716`
- Added `strategies.30dte.BDIV_GAP_W` = `3.4571`
- Added `strategies.30dte.BDIV_PROX_CUT` = `0.0198`
- ... 27 additional structured changes omitted.

## Source References

- `.claude/docs/version-history.md:375` - ## v72 (`fc5671200`) - 2026-06-11 (WCF Score-Gate Ramp)
- `.claude/docs/scoring-algorithm.md:40` - - **v72 score-gate ramp (shipped 2026-06-11, `fc5671200`):** the binary score gate fired on the POST-REGIME integer, so a 1-pt component/regime wobble at 27/28 toggled the full ~21.85-pt lift (the GIS/CBRE/GEHC lo=28/...
- `.claude/docs/known-issues.md:52` - **2026-06-11 v72 WCF SCORE-GATE RAMP shipped + ACTIVE (`fc5671200`, DB version 72, bump `97f5118e0`, silo `71ee9d527`, pushed):**

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
