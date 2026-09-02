# v42 Algorithm Snapshot

- Status: `shipped`
- DB version: `42`
- Commit: `5e6e3d3`
- Resolved commit: `5e6e3d310e5dab47eccc6276828d56f46a8b9c14`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active

AlgorithmVersion message: P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active
Commit subject: P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v41`
Diff range: `917659cb..5e6e3d31`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/models/technical.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION            |  2 +-
api.py                       | 85 ++++++++++++++++++++++++++++++++++++-
database/models/core.py      | 99 ++++++++++++++++++++++++++++++++++++++++----
database/models/technical.py | 80 ++++++++++++++++++++++++++++++++++-
simulator.py                 | 47 +++++++++++++++++++--
strategy_config.py           | 30 ++++++++++++++
trader.py                    | 52 ++++++++++++++++++++---
7 files changed, 376 insertions(+), 19 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1267` - 5e6e3d3 P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active
- `.claude/docs/version-history-archive.md:1268` - 63e5825 Bump ALGORITHM_VERSION to 5e6e3d3 (rolling weekly P5 ship)
- `.claude/docs/version-history-archive.md:1291` - **SHIPPED then REVERTED 2026-05-07.
- `.claude/docs/version-history.md:1323` - 5e6e3d3 P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active
- `.claude/docs/version-history.md:1324` - 63e5825 Bump ALGORITHM_VERSION to 5e6e3d3 (rolling weekly P5 ship)
- `.claude/docs/version-history.md:1347` - **SHIPPED then REVERTED 2026-05-07.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
