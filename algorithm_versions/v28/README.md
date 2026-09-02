# v28 Algorithm Snapshot

- Status: `shipped`
- DB version: `28`
- Commit: `e3c8678`
- Resolved commit: `e3c8678f4ce2212348bc11af1f6d73e739e7e671`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

v28: earnings meta-score boost (WR-calibrated, log-smoothed)

AlgorithmVersion message: v28: earnings meta-score boost (WR-calibrated, log-smoothed)
Commit subject: v28: earnings meta-score boost (WR-calibrated, log-smoothed)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v27`
Diff range: `ad02704d..e3c8678f`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `simulator.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  68 ++++++----
backtest_cascade.py       | 143 ++++++++++++++++-----
database/models/core.py   |  98 ++++++++++++++-
database/utils/scoring.py | 133 ++++++++++++++++++++
monte_carlo.py            | 314 +++++++++++++++++++++++++++++++++-------------
simulator.py              |   8 ++
trader.py                 | 131 +++++++++----------
8 files changed, 688 insertions(+), 209 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:43` - - **Earnings meta-score boost** - ?
- `.claude/docs/trading-strategy.md:275` - \\| Earnings meta-score boost `EARN_BOOST_ENABLED=True`, `WINDOW=5`, `MAX_BOOST=0.50`, `LIFT_NORM_CALL=22.3`, `LIFT_NORM_PUT=16.3` \\| over no boost \\| **Shipped 2026-04-28 as v28 (`e3c8678`).** Score-stage WR-calibrate...
- `.claude/docs/trading-strategy.md:485` - ### Validation (N=500, all 8 windows, vs 30 DTE H5 baseline at N=200, v28 e3c8678)
- `.claude/docs/scoring-algorithm.md:41` - - **Earnings meta-score boost** (shipped 2026-04-28 as v28, `e3c8678`): WR-calibrated, log-smoothed multiplier on `(overall ?
- `.claude/docs/trading-strategy.md:204` - \\| Earnings meta-score boost `EARN_BOOST_ENABLED=True`, `WINDOW=5`, `MAX_BOOST=0.50`, `LIFT_NORM_CALL=22.3`, `LIFT_NORM_PUT=16.3` \\| over no boost \\| **Shipped 2026-04-28 as v28 (`e3c8678`).** Score-stage WR-calibrate...
- `.claude/docs/trading-strategy.md:372` - ### Validation (N=500, all 8 windows, vs 30 DTE H5 baseline at N=200, v28 e3c8678)

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
