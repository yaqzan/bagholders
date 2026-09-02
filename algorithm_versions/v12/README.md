# v12 Algorithm Snapshot

- Status: `shipped`
- DB version: `12`
- Commit: `edf6bd1`
- Resolved commit: `edf6bd10389aa0c17198bf0b02e40e67b58bd5f7`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

26.04.09 - Scoring: zero RSI for deeply bearish base signals (put calibration fix)

AlgorithmVersion message: 26.04.09 - Scoring: zero RSI for deeply bearish base signals (put calibration fix)
Commit subject: 26.04.09 - Scoring: zero RSI for deeply bearish base signals (put calibration fix)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v11`
Diff range: `d93ff2d4..edf6bd10`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`
- `database/utils/scoring.py`
- `market_regime.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/models/core.py   | 62 ++++++++++++++++++++++++++++++++++++++++-------
database/utils/scoring.py | 31 ++++++++++++++++++++++++
market_regime.py          |  7 ++++++
recalculate_scores.py     | 25 ++++++++++---------
simulator.py              | 10 +++++++-
trader.py                 | 15 ++++++++----
7 files changed, 124 insertions(+), 28 deletions(-)
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
