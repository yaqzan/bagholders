# v26 Algorithm Snapshot

- Status: `shipped`
- DB version: `26`
- Commit: `18c3e70`
- Resolved commit: `18c3e70fba2d139f0b6052b6edde9e7da926bf89`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

EARN_SUPP_PUT: drop puts in [16,20] within 5 trd days of earnings

AlgorithmVersion message: EARN_SUPP_PUT: drop puts in [16,20] within 5 trd days of earnings
Commit subject: EARN_SUPP_PUT: drop puts in [16,20] within 5 trd days of earnings

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v25`
Diff range: `9463f028..18c3e70f`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `backtest_cascade.py`
- `monte_carlo.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION   |   2 +-
backtest_cascade.py |  75 ++++++++++++++++-
monte_carlo.py      | 229 ++++++++++++++++++++++++++++++++++++++++++++++++----
trader.py           |  79 +++++++++++++++++-
4 files changed, 365 insertions(+), 20 deletions(-)
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
