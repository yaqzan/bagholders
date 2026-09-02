# v23 Algorithm Snapshot

- Status: `shipped`
- DB version: `23`
- Commit: `be057ce`
- Resolved commit: `be057ce043764c42dcecaa401d44310dfcce9804`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Ship v23: floored-regime x_conf gate (Priority #8 counter-trend capture)

AlgorithmVersion message: Ship v23: floored-regime x_conf gate (Priority #8 counter-trend capture)
Commit subject: Ship v23: floored-regime x_conf gate (Priority #8 counter-trend capture)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v22`
Diff range: `41784e02..be057ce0`

Changed snapshot-tracked paths:
- `api.py`
- `backtest_cascade.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `trader.py`

Git diff stat:

```text
api.py                    |  51 ++++++++++++++---
backtest_cascade.py       |  69 ++++++++++++++++++-----
database/utils/scoring.py |  84 ++++++++++++++++++---------
monte_carlo.py            |  74 +++++++++++++++++++-----
trader.py                 | 141 ++++++++++++++++++++++++++++++++++++----------
5 files changed, 325 insertions(+), 94 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
