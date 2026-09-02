# v14 Algorithm Snapshot

- Status: `shipped`
- DB version: `14`
- Commit: `410a055`
- Resolved commit: `410a055a3b2a0e4c573b0f07028d5f36885db9ee`
- Category: `scoring_revert`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Revert directional volume fix (83851db) due to bucket-level regression

AlgorithmVersion message: Revert directional volume fix (83851db) due to bucket-level regression
Commit subject: Revert directional volume fix (83851db) due to bucket-level regression

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v13`
Diff range: `89884745..410a055a`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `market_regime.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 100 ++++++++++++++++++++++++++++++++------
database/models/core.py   | 121 +++++++++++++++++++++++++++++++++++++++++-----
database/utils/scoring.py |  24 ++++++++-
market_regime.py          |  72 +++++++++++----------------
recalculate_scores.py     |  60 ++++++++++++++++++++---
simulator.py              |  15 +++++-
trader.py                 |  82 +++++++++++++++++--------------
volume_amplifier.py       |   2 +-
9 files changed, 360 insertions(+), 118 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/monte-carlo.md:23` - **Algorithm version**: 410a055

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
