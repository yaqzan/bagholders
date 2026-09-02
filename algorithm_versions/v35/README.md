# v35 Algorithm Snapshot

- Status: `shipped`
- DB version: `35`
- Commit: `e77714f`
- Resolved commit: `e77714f61b9120459d82766941593fca10dae554`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores

AlgorithmVersion message: v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores
Commit subject: v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores

## Existing Documentation Hint

Source heading: `v35 - EARN_BOOST Recalibration on v34 Pre-Boost Scores (2026-05-04, `e77714f`)` (from `.claude/docs/version-history-archive.md`)

The v28 lift table (`experiments/v27_optimization/phase_tp3b_lift_table.json`) was built on v27 historical scores in April 2026. v32 (CWCF dampener), v33 (continuation boost), and v34 (CSWC dampener) shipped after, each shifting the call score distribution that the v28 boost amplifies. v35 rebuilds the lift table from v34 `pre_boost` scores (extracted from `weight_info`) so the boost is calibrated against the score mix actually arriving at the boost stage today.

## Code Delta From Previous Resolved Version

Previous resolved key: `v34`
Diff range: `232a7255..e77714f6`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 398 ++++++++++++++++++++++++++++++++++++----------
backtest_cascade.py       |  63 ++++++--
backtest_cascade_15dte.py |   8 +
database/models/core.py   | 149 ++++++++++++++++-
database/utils/scoring.py |  15 +-
monte_carlo.py            |  62 +++++---
simulator.py              | 126 +++++++++++++++
strategy_config.py        |  66 ++++++--
trader.py                 | 112 ++++++++-----
10 files changed, 810 insertions(+), 191 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- `strategies.30dte.PUT_TIER_ALLOC.put_low`: `0.12` -> `0.08`
- `strategies.30dte.PUT_TIER_ALLOC.put_mid`: `0.12` -> `0.1`
- `strategies.30dte.PUT_TIER_ALLOC.put_top`: `0.1` -> `0.12`
- `strategies.30dte.SL_SIGMA_BASE`: `1.092` -> `0.9828000000000001`
- `strategies.30dte.SL_SIGMA_STRESS`: `1.274` -> `1.4560000000000002`
- `strategies.30dte.TIER_ALLOC.mid`: `0.12` -> `0.1`
- `strategies.30dte.TP_SIGMA_BASE`: `1.274` -> `1.2012`
- `strategies.30dte.TP_SIGMA_STRESS`: `1.4560000000000002` -> `1.5288`
- `strategies.30dte.option.BREADTH_THRESHOLD`: `50` -> `40`
- `strategies.30dte.option.NET_SL_BASE`: `-0.3` -> `-0.27`
- `strategies.30dte.option.NET_SL_STRESS`: `-0.35` -> `-0.4`
- `strategies.30dte.option.NET_TP_BASE`: `0.35` -> `0.33`
- `strategies.30dte.option.NET_TP_STRESS`: `0.4` -> `0.42`
- `strategies.30dte.option.SL_BASE`: `-0.3` -> `-0.27`
- `strategies.30dte.option.SL_STRESS`: `-0.35` -> `-0.4`
- `strategies.30dte.option.TP_BASE`: `0.35` -> `0.33`
- `strategies.30dte.option.TP_STRESS`: `0.4` -> `0.42`
- Added `assess_combos[2]` = `["15", "tp"]`

## Source References

- `.claude/docs/version-history-archive.md:1236` - e77714f v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores
- `.claude/docs/version-history-archive.md:1237` - 5e8cf80 Bump ALGORITHM_VERSION to e77714f (v35 EARN_BOOST recalibration)
- `.claude/docs/version-history-archive.md:1544` - ## v35 - EARN_BOOST Recalibration on v34 Pre-Boost Scores (2026-05-04, `e77714f`)
- `.claude/docs/scoring-algorithm.md:47` - Defaults (v35 recalibration, 2026-05-04, `e77714f`): `EARN_BOOST_MAX = 0.55`, `LIFT_NORM_CALL = 14.0`, `LIFT_NORM_PUT = 16.3`, `MIN_N = 10`.
- `.claude/docs/version-history.md:1292` - e77714f v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores
- `.claude/docs/version-history.md:1293` - 5e8cf80 Bump ALGORITHM_VERSION to e77714f (v35 EARN_BOOST recalibration)
- `.claude/docs/version-history.md:1600` - ## v35 - EARN_BOOST Recalibration on v34 Pre-Boost Scores (2026-05-04, `e77714f`)
- `.claude/docs/scoring-algorithm.md:45` - Defaults (v35 recalibration, 2026-05-04, `e77714f`): `EARN_BOOST_MAX = 0.55`, `LIFT_NORM_CALL = 14.0`, `LIFT_NORM_PUT = 16.3`, `MIN_N = 10`.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
