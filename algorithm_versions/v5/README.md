# v5 Algorithm Snapshot

- Status: `shipped`
- DB version: `5`
- Commit: `a8cfc75`
- Resolved commit: `a8cfc7502f3aa0ae2da4a7a35af4faffad06724a`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

26.04.06 - Lower max w_trend - Cap trend weight at 28 isntead of 35

AlgorithmVersion message: 26.04.06 - Lower max w_trend - Cap trend weight at 28 isntead of 35
Commit subject: 26.04.06 - Lower max w_trend - Cap trend weight at 28 isntead of 35

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v3`
Diff range: `b1cc55cb..a8cfc750`

Changed snapshot-tracked paths:
- `api.py`
- `database/models/core.py`
- `database/project_root.py`
- `database/utils/scoring.py`
- `market_breadth.py`
- `market_regime.py`
- `recalculate_scores.py`
- `simulator.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
api.py                    |  533 +++++++++++++---
database/models/core.py   | 1529 +++++++++++++++++++++++++++++++--------------
database/project_root.py  |   39 ++
database/utils/scoring.py |   97 +++
market_breadth.py         |  644 +++++++++++++++++++
market_regime.py          |  411 ++++++++++++
recalculate_scores.py     |   71 ++-
simulator.py              |  551 ++++++++++++++++
trader.py                 |  629 +++++++++++++++++--
volume_amplifier.py       |  297 ++++++++-
10 files changed, 4139 insertions(+), 662 deletions(-)
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
