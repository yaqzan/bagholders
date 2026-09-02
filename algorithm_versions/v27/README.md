# v27 Algorithm Snapshot

- Status: `shipped`
- DB version: `27`
- Commit: `ad02704`
- Resolved commit: `ad02704d1a17d229ee9714988ccb4b4f8bc72801`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Weekly-confirmation floor lift on extreme puts (Priority #13)

AlgorithmVersion message: Weekly-confirmation floor lift on extreme puts (Priority #13)
Commit subject: Weekly-confirmation floor lift on extreme puts (Priority #13)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v26`
Diff range: `18c3e70f..ad02704d`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/models/technical.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION            |   2 +-
api.py                       | 160 +++++++++++++++++++++++++++++++++++++++-
database/models/core.py      |  53 +++++++++-----
database/models/technical.py | 163 ++++++++++++++++++++++++++++++++++++++++-
database/utils/scoring.py    |  35 +++++++++
monte_carlo.py               | 171 +++++++++++++++++++++++++++++++++++++++++--
recalculate_scores.py        |  34 ++++++---
simulator.py                 |  19 ++++-
trader.py                    |  19 ++++-
9 files changed, 614 insertions(+), 42 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:38` - - **Weekly-confirmation floor lift (WCF)** - ?
- `.claude/docs/scoring-algorithm.md:38` - - **Weekly-confirmation floor lift** (shipped 2026-04-27 as v27, `ad02704`): when a put score reaches extreme territory (`overall < 28`) but the weekly didn't strongly confirm the bearish thesis (`w_adj > -17`), lift...

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
