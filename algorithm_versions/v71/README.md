# v71 Algorithm Snapshot

- Status: `shipped`
- DB version: `71`
- Commit: `04044b21b`
- Resolved commit: `04044b21b374bd955456395b8ec38764c340f7bb`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements

AlgorithmVersion message: v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements
Commit subject: v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements

## Existing Documentation Hint

Source heading: `v71 (`04044b21b`) - 2026-06-10 (Integrity-Audit Honest Fixes + 4 Retirements)` (from `.claude/docs/version-history.md`)

Scoring ship (ALGORITHM_VERSION bump `de7fa330e`, DB version 71, full 10y recalc).
Executes the 2026-06-09 scoring-integrity audit overnight via worktree
`algo-exp/integrity-audit` (handoff + full evidence:
`experiments/integrity_audit_2026_06/{AUDIT_FIX_HANDOFF,FINDINGS}.md`).

## Code Delta From Previous Resolved Version

Previous resolved key: `v70`
Diff range: `c70d16d2..04044b21`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `database/utils/sector_breadth_wave.py`
- `mechanism_registry.py`
- `monte_carlo.py`
- `monte_carlo_15dte.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION                     |   2 +-
api.py                                | 494 +++++++++++++++++++++-
backtest_cascade.py                   | 445 ++++++++++++++++++--
backtest_cascade_15dte.py             |   8 +-
database/models/core.py               | 142 ++++++-
database/utils/scoring.py             |  40 ++
database/utils/sector_breadth_wave.py |  34 ++
mechanism_registry.py                 | 132 ++++++
monte_carlo.py                        | 744 +++++++++++++++++++++++++++++++---
monte_carlo_15dte.py                  |   9 +-
simulator.py                          |  73 +++-
strategy_config.py                    | 322 ++++++++++++---
trader.py                             | 335 ++++++++++++---
volume_amplifier.py                   |  53 ++-
14 files changed, 2590 insertions(+), 243 deletions(-)
```

### Structured Scoring Variable Delta

- `CALIBRATION_CUTOFF_DATE`: `2026-05-15` -> ``
- `SCORING.MCD_ENABLED`: `True` -> `False`
- `SCORING.MIS_STRESS_CALL_DAMPEN`: `0.25` -> `0.0`
- `SCORING.SECTOR_BREADTH_WAVE_ENABLED`: `True` -> `False`

### Structured Portfolio Variable Delta

- `mechanism_registry[0].config_fields`: `["DTE_ROUTER_ENABLED", "DTE_ROUTER_TARGET_DTE", "DTE_ROUTER_SCORE_MIN", "DTE_ROUTER_TREND_LT", "DTE_ROUTER_VIX_MIN",...` -> `["DTE_ROUTER_ENABLED", "DTE_ROUTER_TARGET_DTE", "DTE_ROUTER_SCORE_MIN", "DTE_ROUTER_TREND_LT", "DTE_ROUTER_VIX_MIN",...`
- `mechanism_registry[4].config_fields`: `["PRACTICAL_EXPOSURE_ENABLED", "PRACTICAL_CAPITAL_CEILING", "GROSS_PREMIUM_CAP", "CALL_PREMIUM_CAP", "PUT_PREMIUM_CAP...` -> `["RXDD_ENABLED", "RXDD_VIX_C", "RXDD_VIX_W", "RXDD_DEPTH", "RXDD_DD_MIN"]`
- `mechanism_registry[4].dte_15_reason`: `Not validated for 15 DTE. The half-DTE portfolio already uses a smaller eight-slot pool and different tail/theta dyna...` -> `VIX-band call dampener not validated under bounded-fill MC for the half-DTE strategy (smaller premium-mult, faster th...`
- `mechanism_registry[4].name`: `PRACTICAL_EXPOSURE_SATURATION` -> `RXDD`
- `mechanism_registry[4].notes`: `Stage 3 Sentinel profile candidate g80_c65_p25_ref16_4_pow05_floor55_25m. Caps deployable premium to a practical base...` -> `Smooth Gaussian contraction of CALL alloc in the low-EV VIX ~20-26 slow-bleed band: alloc *= 1 - DEPTH*exp(-0.5*((vix...`
- `mechanism_registry[4].ship_date_30`: `2026-05-21` -> `2026-06-04`
- `mechanism_registry[5].config_fields`: `["F3F_CALL_THRESH", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_PUT_THRESH", "F3F_PUT_FLOOR", "F3F_PUT_HIGH", "ALLOC_SCALE...` -> `["MWDD_ENABLED", "MWDD_MCC_C", "MWDD_MCC_W", "MWDD_DEPTH", "MWDD_DD_MIN", "MWDD_VIX_PANIC"]`
- `mechanism_registry[5].dte_15_reason`: `` -> `McClellan flat-band call dampener not validated under bounded-fill MC for the half-DTE strategy. Not wired into mc15/...`
- `mechanism_registry[5].dte_15_status`: `enabled` -> `disabled`
- `mechanism_registry[5].dte_15_wiring_mode`: `n/a` -> `not_wired`
- `mechanism_registry[5].engine_files_15`: `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `[]`
- `mechanism_registry[5].engine_files_30`: `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `["monte_carlo.py", "backtest_cascade.py"]`
- `mechanism_registry[5].name`: `F3F_BREADTH_ALLOC` -> `MWDD`
- `mechanism_registry[5].notes`: `At each signal date, lookup breadth_score and scale tier alloc. Asymmetric: cuts calls when breadth low, cuts puts wh...` -> `Smooth Gaussian contraction of CALL alloc in the low-EV flat/topping McClellan band (~0): alloc *= 1 - DEPTH*exp(-0.5...`
- `mechanism_registry[5].ship_date_15`: `2026-04-24` -> ``
- `mechanism_registry[5].ship_date_30`: `2026-04-24` -> `2026-06-05`
- `mechanism_registry[6].config_fields`: `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]` -> `["TVDD_ENABLED", "TVDD_TRIN_C", "TVDD_TRIN_W", "TVDD_DEPTH", "TVDD_DD_MIN", "TVDD_VIX_PANIC"]`
- `mechanism_registry[6].dte_15_reason`: `` -> `TRIN volume-flow neutral-band call dampener not validated under bounded-fill MC for the half-DTE strategy. Not wired...`
- `mechanism_registry[6].dte_15_status`: `enabled` -> `disabled`
- `mechanism_registry[6].dte_15_wiring_mode`: `n/a` -> `not_wired`
- `mechanism_registry[6].engine_files_15`: `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `[]`
- `mechanism_registry[6].engine_files_30`: `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]` -> `["monte_carlo.py", "backtest_cascade.py"]`
- `mechanism_registry[6].name`: `DEAD_HOLD_POST_SL` -> `TVDD`
- `mechanism_registry[6].notes`: `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...` -> `Smooth Gaussian contraction of CALL alloc in the low-EV neutral volume-flow band (TRIN ~1.0-1.3): alloc *= 1 - DEPTH*...`
- Added `mechanism_registry[10].config_fields` = `["DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL"]`
- Added `mechanism_registry[10].dte_15_reason` = ``
- Added `mechanism_registry[10].dte_15_status` = `enabled`
- Added `mechanism_registry[10].dte_15_wiring_mode` = `n/a`
- Added `mechanism_registry[10].dte_30_reason` = ``
- Added `mechanism_registry[10].dte_30_status` = `enabled`
- Added `mechanism_registry[10].dte_30_wiring_mode` = `n/a`
- Added `mechanism_registry[10].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[10].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[10].name` = `DEAD_HOLD_POST_SL`
- Added `mechanism_registry[10].notes` = `When SL fires AND realized option pnl <= TRIGGER_PNL, hold forward bar-by-bar instead of selling. Exit at intraday ex...`
- Added `mechanism_registry[10].ship_date_15` = `2026-05-01`
- Added `mechanism_registry[10].ship_date_30` = `2026-05-01`
- Added `mechanism_registry[11].config_fields` = `["CT_PROMOTE", "CT_PUT_TREND_MIN", "CT_CALL_TREND_MAX"]`
- Added `mechanism_registry[11].dte_15_reason` = ``
- Added `mechanism_registry[11].dte_15_status` = `enabled`
- Added `mechanism_registry[11].dte_15_wiring_mode` = `n/a`
- Added `mechanism_registry[11].dte_30_reason` = ``
- Added `mechanism_registry[11].dte_30_status` = `enabled`
- Added `mechanism_registry[11].dte_30_wiring_mode` = `n/a`
- Added `mechanism_registry[11].engine_files_15` = `["monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[11].engine_files_30` = `["monte_carlo.py", "backtest_cascade.py", "monte_carlo_15dte.py", "backtest_cascade_15dte.py"]`
- Added `mechanism_registry[11].name` = `CT_CASCADE_PROMOTION`
- Added `mechanism_registry[11].notes` = `ct_call (overall>=70 AND TREND<=MAX) -> ultra tier. ct_put (overall<=25 AND TREND>=MIN) -> put_top tier. Both DTEs: p...`
- ... 153 additional structured changes omitted.

## Source References

- `.claude/docs/version-history.md:275` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v71 `04044b21b`).
- `.claude/docs/version-history.md:353` - Stage-3 portfolio-only ship (NO version bump; scoring stays v71 `04044b21b`).
- `.claude/docs/version-history.md:424` - ## v71 (`04044b21b`) - 2026-06-10 (Integrity-Audit Honest Fixes + 4 Retirements)
- `.claude/docs/known-issues.md:71` - **2026-06-10 v71 INTEGRITY-AUDIT HONEST FIXES shipped + ACTIVE (`04044b21b`, DB version 71, bump `de7fa330e`):**

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
