# v18 Algorithm Snapshot

- Status: `shipped`
- DB version: `18`
- Commit: `17caf99`
- Resolved commit: `17caf99f6ee1a8b3bd0fd4fe556f571df125ebdb`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Asymmetric MACD gate for puts

AlgorithmVersion message: Asymmetric MACD gate for puts
Commit subject: Asymmetric MACD gate for puts

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v17`
Diff range: `ea8b9feb..17caf99f`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `market_regime.py`
- `monte_carlo.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 403 +++++++++++++++++++-
backtest_cascade.py       | 702 ++++++++++++++++++++++++++++++++++
database/models/core.py   |  54 ++-
database/utils/scoring.py |  31 ++
market_regime.py          | 335 +++++++++++++++-
monte_carlo.py            | 640 +++++++++++++++++++++++++++++++
trader.py                 | 948 +++++++++++++++++++++++++++++++++++++++++++++-
8 files changed, 3080 insertions(+), 35 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/trading-strategy.md:266` - \\| Asymmetric weekly put_scale=1.5x \\| over symmetric 1.0x \\| `experiments/asymmetric_weekly_sweep.py` (2026-04-17, 5y full universe): puts `<25` WR30 63.9% -> 65.5%, `<15` WR30 64.1% -> 69.3%, `<15` Ret30 ?0.28% -> +...
- `.claude/docs/trading-strategy.md:195` - \\| Asymmetric weekly put_scale=1.5x \\| over symmetric 1.0x \\| `experiments/asymmetric_weekly_sweep.py` (2026-04-17, 5y full universe): puts `<25` WR30 63.9% -> 65.5%, `<15` WR30 64.1% -> 69.3%, `<15` Ret30 ?0.28% -> +...

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
