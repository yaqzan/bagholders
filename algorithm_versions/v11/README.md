# v11 Algorithm Snapshot

- Status: `shipped`
- DB version: `11`
- Commit: `d93ff2d`
- Resolved commit: `d93ff2d4021421fbe9b19e5aad9aa5ec829d16cc`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

26.04.09 - Regime: gradient VIX scoring + dynamic breadth weighting

AlgorithmVersion message: 26.04.09 - Regime: gradient VIX scoring + dynamic breadth weighting
Commit subject: 26.04.09 - Regime: gradient VIX scoring + dynamic breadth weighting

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v10`
Diff range: `07bf8c43..d93ff2d4`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `market_breadth.py`
- `market_regime.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION       |   1 +
api.py                  | 100 +++++++++++++++++++++++++++++++--
database/models/core.py |  56 ++++++++++++++++++-
market_breadth.py       |  30 +++++-----
market_regime.py        | 145 ++++++++++++++++++++++++++++++++++--------------
trader.py               |  87 ++++++++++++++++++++++++++++-
6 files changed, 355 insertions(+), 64 deletions(-)
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
