# v62 Algorithm Snapshot

- Status: `shipped`
- DB version: `62`
- Commit: `d4d63798e`
- Resolved commit: `d4d63798eaea34609f076025ea78cf251cd179c1`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Snapshot v62 MACD put wave candidate

AlgorithmVersion message: Snapshot v62 MACD put wave candidate
Commit subject: Snapshot v62 MACD put wave candidate

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v61`
Diff range: `e6fbdbde..d4d63798`

Changed snapshot-tracked paths:
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
api.py                    | 207 ++++++++++++++++++++++++++++++++
backtest_cascade.py       | 252 ++++++++++++++++++++++++++++++++++++++-
database/models/core.py   |  12 --
database/utils/scoring.py | 264 ++++++++++++++--------------------------
mechanism_registry.py     |  31 +++++
monte_carlo.py            | 298 +++++++++++++++++++++++++++++++++++++++++++---
simulator.py              |  29 +----
strategy_config.py        |  89 ++++++++------
trader.py                 |  77 ++++++++++--
9 files changed, 991 insertions(+), 268 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.MACD_PUT_GATE_MODE` = `deep_salvage`
- Added `SCORING.MACD_PUT_WAVE_BOOST` = `0.65`
- Added `SCORING.MACD_PUT_WAVE_CENTER` = `26.0`
- Added `SCORING.MACD_PUT_WAVE_MACD_WIDTH` = `2.5`
- Added `SCORING.MACD_PUT_WAVE_MAX_FACTOR` = `1.0`
- Added `SCORING.MACD_PUT_WAVE_MIN_FACTOR` = `0.0`
- Added `SCORING.MACD_PUT_WAVE_SETUP_POWER` = `1.0`
- Added `SCORING.MACD_PUT_WAVE_SUPPRESS` = `1.0`
- Added `SCORING.MACD_PUT_WAVE_WIDTH` = `1.0`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_ENABLED` = `True`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_mid` = `6.358089714737812`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_weight` = `0.25938373337360876`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.bias_width` = `2.3620738069477167`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_mid` = `68.73904300497583`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_weight` = `0.4363515673549808`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.comp_width` = `5.201703912769646`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.k` = `0.21365891783568708`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_mid` = `3.5606659252920165`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_relief` = `0.45138359953402063`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.mom_width` = `3.661404150277635`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_hi` = `81.42326751772556`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_hi_width` = `3.1545639155873073`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_lo` = `72.49981169711549`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.score_lo_width` = `4.318306546556302`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.target` = `60.18061302183849`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_mid` = `0.11181277557817569`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_weight` = `0.42930945850864727`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wk4_width` = `0.037548436669286936`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_mid` = `0.08491400832688323`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_weight` = `0.06294045942335882`
- Removed `SCORING.WEEKLY_MATURE_CALL_GUARD_PARAMS.wv_width` = `0.022388072253294658`

### Structured Portfolio Variable Delta

- `mechanism_registry[3].config_fields`: `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...` -> `["PRACTICAL_EXPOSURE_ENABLED", "PRACTICAL_CAPITAL_CEILING", "GROSS_PREMIUM_CAP", "CALL_PREMIUM_CAP", "PUT_PREMIUM_CAP...`
- `mechanism_registry[3].dte_15_reason`: `` -> `Not validated for 15 DTE. The half-DTE portfolio already uses a smaller eight-slot pool and different tail/theta dyna...`
- `mechanism_registry[3].dte_15_status`: `enabled` -> `disabled`
- `mechanism_registry[3].dte_15_wiring_mode`: `n/a` -> `not_wired`
- `mechanism_registry[3].engine_files_15`: `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `[]`
- `mechanism_registry[3].engine_files_30`: `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `["monte_carlo.py", "backtest_cascade.py"]`
- `mechanism_registry[3].name`: `F3F_BREADTH_ALLOC` -> `PRACTICAL_EXPOSURE_SATURATION`
- `mechanism_registry[3].notes`: `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...` -> `Stage 3 v62 candidate g80_c65_p25_ref16_4_pow05_floor55_25m. Caps deployable premium to a practical base: capital cei...`
- `mechanism_registry[3].ship_date_15`: `2026-04-24` -> ``
- `mechanism_registry[3].ship_date_30`: `2026-04-24` -> `2026-05-21`
- `mechanism_registry[4].config_fields`: `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]` -> `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...`
- `mechanism_registry[4].name`: `DEAD_HOLD_POST_SL` -> `F3F_BREADTH_ALLOC`
- `mechanism_registry[4].notes`: `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...` -> `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...`
- `mechanism_registry[4].ship_date_15`: `2026-05-01` -> `2026-04-24`
- `mechanism_registry[4].ship_date_30`: `2026-05-01` -> `2026-04-24`
- `mechanism_registry[5].config_fields`: `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]` -> `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]`
- `mechanism_registry[5].name`: `CT_CASCADE_PROMOTION` -> `DEAD_HOLD_POST_SL`
- `mechanism_registry[5].notes`: `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...` -> `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...`
- `mechanism_registry[5].ship_date_15`: `2026-04-21` -> `2026-05-01`
- `mechanism_registry[5].ship_date_30`: `2026-04-21` -> `2026-05-01`
- `mechanism_registry[6].config_fields`: `["EARN_SUPP_PUT", "EARN_SUPP_PUT_DAYS", "EARN_SUPP_PUT_MIN_OV", "EARN_SUPP_PUT_MAX_OV"]` -> `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- `mechanism_registry[6].dte_15_reason`: `RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.` -> ``
- `mechanism_registry[6].dte_15_status`: `disabled` -> `enabled`
- `mechanism_registry[6].dte_15_wiring_mode`: `wired_neutral` -> `n/a`
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
- Added `strategies.15dte.CALL_PREMIUM_CAP` = `0.0`
- Added `strategies.15dte.GROSS_PREMIUM_CAP` = `0.0`
- Added `strategies.15dte.OPP_SAT_CALL_REF` = `0.0`
- Added `strategies.15dte.OPP_SAT_FLOOR` = `0.0`
- Added `strategies.15dte.OPP_SAT_POWER` = `1.0`
- Added `strategies.15dte.OPP_SAT_PUT_REF` = `0.0`
- Added `strategies.15dte.PRACTICAL_CAPITAL_CEILING` = `0.0`
- Added `strategies.15dte.PRACTICAL_EXPOSURE_ENABLED` = `False`
- Added `strategies.15dte.PUT_PREMIUM_CAP` = `0.0`
- Added `strategies.30dte.CALL_PREMIUM_CAP` = `0.65`
- Added `strategies.30dte.GROSS_PREMIUM_CAP` = `0.8`
- ... 22 additional structured changes omitted.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
