# v44 Algorithm Snapshot

- Status: `shipped`
- DB version: `44`
- Commit: `d8024b9`
- Resolved commit: `d8024b9b6e313a797b46cd7d66c577363e96a867`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3)

AlgorithmVersion message: v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3)
Commit subject: v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v43`
Diff range: `e0830320..d8024b9b`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`
- `database/utils/scoring.py`
- `simulator.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
database/models/core.py   |  47 +++++++++++
database/utils/scoring.py | 202 ++++++++++++++++++++++++++++++++++++++++++++++
simulator.py              |  31 ++++++-
4 files changed, 279 insertions(+), 3 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:683` - ### v44 - ICH: Ichimoku Kijun-sen state dampener (ship 2026-05-07, `d8024b9`)
- `.claude/docs/version-history-archive.md:692` - ### v44 - ICH: Ichimoku Kijun-sen state dampener (ship 2026-05-07, `d8024b9`)
- `.claude/docs/known-issues.md:194` - **v44 ICH shipped 2026-05-07 (`d8024b9`)** - score-stage Ichimoku Kijun-sen state dampener (calls + puts), asymmetric-K power-law on call side.
- `.claude/docs/known-issues.md:481` - **Status: SHIPPED as v44 (`d8024b9`).** Phase H Rank #3 calibration with
- `.claude/docs/version-history.md:739` - ### v44 - ICH: Ichimoku Kijun-sen state dampener (ship 2026-05-07, `d8024b9`)
- `.claude/docs/version-history.md:748` - ### v44 - ICH: Ichimoku Kijun-sen state dampener (ship 2026-05-07, `d8024b9`)
- `.claude/docs/known-issues.md:77` - **v44 ICH shipped 2026-05-07 (`d8024b9`)** - score-stage Ichimoku Kijun-sen state dampener (calls + puts), asymmetric-K power-law on call side.
- `.claude/docs/known-issues.md:285` - **Status: SHIPPED as v44 (`d8024b9`).** Phase H Rank #3 calibration with

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
