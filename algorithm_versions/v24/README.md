# v24 Algorithm Snapshot

- Status: `shipped`
- DB version: `24`
- Commit: `41fa566`
- Resolved commit: `41fa56668e1c92d3fb40379499dc01541a1b0da1`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Fix volume amplifier earnings suppression: call_time-aware semantics

AlgorithmVersion message: Fix volume amplifier earnings suppression: call_time-aware semantics
Commit subject: Fix volume amplifier earnings suppression: call_time-aware semantics

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v23`
Diff range: `be057ce0..41fa5666`

Changed snapshot-tracked paths:
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `simulator.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
api.py                    | 509 +++++++++++++++++++++++++++++++--
backtest_cascade.py       | 703 ++++++++++++++++++++++++++++++++++------------
database/models/core.py   | 222 +++++++++++----
database/utils/scoring.py |  48 ----
monte_carlo.py            | 152 ++++++++--
simulator.py              |  13 +-
trader.py                 | 350 ++++++++++++++++++++---
volume_amplifier.py       | 188 +++++++++++--
8 files changed, 1799 insertions(+), 386 deletions(-)
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
