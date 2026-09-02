# v52 Algorithm Snapshot

- Status: `shipped`
- DB version: `52`
- Commit: `f66bf9b`
- Resolved commit: `f66bf9b962ddfcd76a725a224013684d9c69a48e`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v52 scoring: temporal echo wave scoring

AlgorithmVersion message: v52 scoring: temporal echo wave scoring
Commit subject: Ship temporal echo wave scoring

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v50`
Diff range: `b0c19549..f66bf9b9`

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
api.py                    | 192 ++++++++++++--------------
backtest_cascade.py       | 148 +++++++++-----------
backtest_cascade_15dte.py |  26 +---
database/models/core.py   | 305 ++++++++++++++++++++++++++++++++++++++---
database/utils/scoring.py |  86 +++++++-----
mechanism_registry.py     |  17 ---
monte_carlo.py            |  58 ++++----
monte_carlo_15dte.py      |   9 --
strategy_config.py        |  64 +++++----
trader.py                 | 339 +++++++++++++++++++++++++++++++++-------------
11 files changed, 811 insertions(+), 435 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.CONT_BOOST_GATE_HI`: `74` -> `84`
- `SCORING.CONT_BOOST_GATE_LO`: `70` -> `50`
- `SCORING.CONT_BOOST_MAG_EXP`: `0.7` -> `0.8435514312118402`
- `SCORING.CONT_BOOST_SIG_MIN`: `0.2` -> `0.025421147106629318`
- `SCORING.CONT_BOOST_SIG_NORM`: `3.0` -> `0.9032562577567048`
- `SCORING.CONT_BOOST_TAU`: `40.0` -> `38.15912046629926`
- Added `SCORING.CONT_BOOST_ALPHA` = `1.1669054395351226`
- Added `SCORING.CONT_BOOST_FIZZLER_PENALTY` = `0.4175780458412032`
- Added `SCORING.CONT_BOOST_LOSS_PENALTY` = `0.3845330519083049`
- Added `SCORING.CONT_BOOST_MAX_LIFT` = `4.473918889785761`
- Added `SCORING.CONT_BOOST_TARGET` = `85.0`
- Added `SCORING.CONT_BOOST_W15` = `0.022856284102839568`
- Added `SCORING.CONT_BOOST_W30` = `0.6756014121839538`
- Added `SCORING.CONT_BOOST_W60` = `0.7661245978520328`
- Added `SCORING.CONT_BOOST_W7` = `0.18104215061573875`

### Structured Portfolio Variable Delta

- `mechanism_registry[5].config_fields`: `["DD_CIRCUIT_BREAKER"]` -> `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- `mechanism_registry[5].name`: `DD_CIRCUIT_BREAKER` -> `CT_CASCADE_PROMOTION`
- `mechanism_registry[5].notes`: `Pause new entries when running portfolio DD > threshold. Existing positions resolve normally. Both DTEs use 0.60.` -> `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...`
- `mechanism_registry[5].ship_date_15`: `2026-04-28` -> `2026-04-21`
- `mechanism_registry[5].ship_date_30`: `2026-05-01` -> `2026-04-21`
- `mechanism_registry[6].config_fields`: `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]` -> `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]`
- `mechanism_registry[6].dte_15_reason`: `` -> `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.`
- `mechanism_registry[6].dte_15_status`: `enabled` -> `disabled`
- `mechanism_registry[6].dte_15_wiring_mode`: `n/a` -> `wired_neutral`
- `mechanism_registry[6].dte_30_reason`: `` -> `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...`
- `mechanism_registry[6].dte_30_status`: `enabled` -> `disabled`
- `mechanism_registry[6].dte_30_wiring_mode`: `n/a` -> `wired_neutral`
- `mechanism_registry[6].name`: `CT_CASCADE_PROMOTION` -> `EARN_SUPP_PUT`
- `mechanism_registry[6].notes`: `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...` -> `See known-issues.md "CLOSED - SHIPPED" timeline for retirement context.`
- `mechanism_registry[6].ship_date_15`: `2026-04-21` -> `2026-04-26`
- `mechanism_registry[6].ship_date_30`: `2026-04-21` -> `2026-04-26`
- `mechanism_registry[7].config_fields`: `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]` -> `["WEAK_WEEKLY_CALL_DROP", "WEAK_WEEKLY_CALL_MIN_OV", "WEAK_WEEKLY_CALL_MAX_OV", "WEAK_WEEKLY_CALL_WADJ_LT", "WEAK_WEE...`
- `mechanism_registry[7].dte_15_reason`: `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.` -> `Was never validated for 15 DTE (filter shipped 30 DTE only); now superseded by v38 CWWD score-stage which is DTE-agno...`
- `mechanism_registry[7].dte_30_reason`: `RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit 200f33a). The score itself now lifts puts in [16,20]...` -> `RETIRED 2026-05-06: replaced by score-stage CWWD in v38 (commit b093e2d). The score itself now drifts wadj-neg 70-74...`
- `mechanism_registry[7].name`: `EARN_SUPP_PUT` -> `WEAK_WEEKLY_CALL_DROP`
- `mechanism_registry[7].notes`: `See known-issues.md "CLOSED - SHIPPED" timeline for retirement context.` -> `See experiments/call_wadj_70_filter/FINDINGS.md for history.`
- `mechanism_registry[7].ship_date_15`: `2026-04-26` -> ``
- `mechanism_registry[7].ship_date_30`: `2026-04-26` -> `2026-05-05`
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
- Removed `strategies.15dte.DD_CIRCUIT_BREAKER` = `0.6`
- Removed `strategies.30dte.DD_CIRCUIT_BREAKER` = `0.6`

## Source References

- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** - ?
- `.claude/docs/known-issues.md:279` - current temporal / continuation echo introduction in v52 (`f66bf9b9`), v53
- `.claude/docs/active-investigations/continuation-boost.md:5` - **Follow-up flag (2026-05-13):** UAMY 2025-07-18 exposed a likely failure mode in the current continuation echo lineage: `cont_lift` can promote an exhaustion-entry candle into the tradable CALL gate.
- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** (legacy boost v33 `28fa5227`; temporal echo wave v52 `f66bf9b9`; prior-fix v53 `e3ed806`; v58 retune `3cfc4dc2` reverted 2026-05-15): same-side CALL prior wins echo into current CALL score...
- `.claude/docs/known-issues.md:146` - current temporal / continuation echo introduction in v52 (`f66bf9b9`), v53

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
