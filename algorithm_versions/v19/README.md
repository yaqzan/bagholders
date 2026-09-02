# v19 Algorithm Snapshot

- Status: `shipped`
- DB version: `19`
- Commit: `6656daa`
- Resolved commit: `6656daa41668c1b08db5ed21792889e5c61bd244`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Ship JA4: asymmetric 25% SPY_wk blend for put regime multiplier

AlgorithmVersion message: Ship JA4: asymmetric 25% SPY_wk blend for put regime multiplier
Commit subject: Ship JA4: asymmetric 25% SPY_wk blend for put regime multiplier

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v18`
Diff range: `17caf99f..6656daa4`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    |  86 ++++++++--
backtest_cascade.py       | 100 +++++++++++-
database/models/core.py   |  90 +++++++++-
database/utils/scoring.py |  17 +-
monte_carlo.py            | 178 ++++++++++++++++++--
trader.py                 | 408 ++++++++++++++++++++++++++++++++++------------
7 files changed, 728 insertions(+), 153 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/monte-carlo-sweeps.md:43` - **v19 deterministic backtest (6656daa, `trader backtest --from 2021-01-01`, $25k start):**

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
