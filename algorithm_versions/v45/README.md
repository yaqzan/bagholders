# v45 Algorithm Snapshot

- Status: `shipped`
- DB version: `45`
- Commit: `56eb1f8`
- Resolved commit: `56eb1f83a3c5a6422cf54cdb0c58d16395934ab3`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Filter ETFs from production breadth universe

AlgorithmVersion message: Filter ETFs from production breadth universe
Commit subject: Filter ETFs from production breadth universe

## Existing Documentation Hint

Source heading: `Active Version: v45 (`56eb1f8`) - 2026-05-08 (Breadth ETF de-contamination)` (from `.claude/docs/version-history-archive.md`)

v45 is an **infrastructure ship** - no scoring formula change. The version bump
tracks the downstream `Score.overall` shift caused by removing 45 ETFs from the
production breadth universe. The breadth-score formula, regime composite weights,
and all score-stage dampeners are byte-identical to v44; only the breadth aggregator
input is cleaner.

## Code Delta From Previous Resolved Version

Previous resolved key: `v44`
Diff range: `d8024b9b..56eb1f83`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `market_breadth.py`
- `monte_carlo.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION |  2 +-
api.py            | 50 +++++++++++++++++++++++++++++++-----
market_breadth.py | 20 ++++++++++++++-
monte_carlo.py    | 77 ++++++++++++++++++++++++++++++++++++++++++++++++++++++-
trader.py         |  6 ++---
5 files changed, 143 insertions(+), 12 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:549` - ## Active Version: v45 (`56eb1f8`) - 2026-05-08 (Breadth ETF de-contamination)
- `.claude/docs/version-history-archive.md:557` - ### v45 - Breadth ETF de-contamination (ship 2026-05-08, `56eb1f8`)
- `.claude/docs/version-history-archive.md:634` - 2.
- `.claude/docs/scoring-algorithm.md:150` - **ETF EXCLUSION (v45 ship 2026-05-08, `56eb1f8`):** `_get_daily_breadth` restricts the universe to `Stock.sector IS NOT NULL`.
- `.claude/docs/known-issues.md:192` - **v45 Breadth ETF de-contamination shipped 2026-05-08 (`56eb1f8`)** - infrastructure ship, no scoring formula change.
- `.claude/docs/version-history.md:605` - ## Active Version: v45 (`56eb1f8`) - 2026-05-08 (Breadth ETF de-contamination)
- `.claude/docs/version-history.md:613` - ### v45 - Breadth ETF de-contamination (ship 2026-05-08, `56eb1f8`)
- `.claude/docs/version-history.md:690` - 2.
- `.claude/docs/scoring-algorithm.md:146` - **ETF EXCLUSION (v45 ship 2026-05-08, `56eb1f8`):** `_get_daily_breadth` restricts the universe to `Stock.sector IS NOT NULL`.
- `.claude/docs/known-issues.md:75` - **v45 Breadth ETF de-contamination shipped 2026-05-08 (`56eb1f8`)** - infrastructure ship, no scoring formula change.
- `.claude/docs/known-issues.md:831` - \\| **v45 infra: Breadth ETF de-contamination** \\| **v45 / 2026-05-08** \\| **Infrastructure ship - no scoring formula change.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
