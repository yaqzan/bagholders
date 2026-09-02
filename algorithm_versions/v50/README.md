# v50 Algorithm Snapshot

- Status: `shipped`
- DB version: `50`
- Commit: `b0c1954`
- Resolved commit: `b0c19549026bfbe35790fb3acec4d4afa34323a0`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v50 scoring: conservative stoch conviction wave

AlgorithmVersion message: v50 scoring: conservative stoch conviction wave
Commit subject: v48 scoring: conservative stoch conviction wave

## Existing Documentation Hint

Source heading: `v50 (`b0c1954`) - 2026-05-11 (Conservative Stoch Conviction Wave)` (from `.claude/docs/version-history-archive.md`)

Score-stage ship. `ALGORITHM_VERSION` points to `b0c1954`; DB active
AlgorithmVersion is v50. The mechanism is a conservative call-side timing
dampener for low-stochastic / weak-weekly 70+ calls, with a wave-shaped
penalty that fades out at high conviction.

## Code Delta From Previous Resolved Version

Previous resolved key: `v48`
Diff range: `61561eed..b0c19549`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
api.py                    |  7 ++++---
database/models/core.py   | 25 +++++++++++++++++++++++++
database/utils/scoring.py |  8 +++-----
strategy_config.py        | 18 +++++++++---------
5 files changed, 42 insertions(+), 18 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.SCW_DECAY_POWER`: `4.0` -> `6.0`
- `SCORING.SCW_MAX_PENALTY`: `15.0` -> `8.0`
- `SCORING.SCW_STOCH_POWER`: `1.0` -> `1.5`
- `SCORING.SCW_WEEKLY_HI`: `18.0` -> `14.0`

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:191` - ## v50 (`b0c1954`) - 2026-05-11 (Conservative Stoch Conviction Wave)
- `.claude/docs/version-history-archive.md:193` - Score-stage ship.
- `.claude/docs/version-history-archive.md:243` - - `ALGORITHM_VERSION`: `b0c1954`.
- `.claude/docs/scoring-algorithm.md:27` - - **SCW - Stoch Conviction Wave** - ?
- `.claude/docs/known-issues.md:190` - **v50 Conservative Stoch Conviction Wave shipped 2026-05-11 (`b0c1954`)** - score-stage call-side timing dampener for `overall >= 70` when daily stochastic is low and weekly adjustment is not strongly confirming.
- `.claude/docs/version-history.md:247` - ## v50 (`b0c1954`) - 2026-05-11 (Conservative Stoch Conviction Wave)
- `.claude/docs/version-history.md:249` - Score-stage ship.
- `.claude/docs/version-history.md:299` - - `ALGORITHM_VERSION`: `b0c1954`.
- `.claude/docs/scoring-algorithm.md:27` - - **SCW - Stoch Conviction Wave** (v50 `b0c1954`, refined 2026-05-19 as v60 `d4a3e9fec`): conservative call-side timing dampener for `overall >= 70` when daily stochastic is low and weekly adjustment is not strongly c...
- `.claude/docs/known-issues.md:73` - **v50 Conservative Stoch Conviction Wave shipped 2026-05-11 (`b0c1954`)** - score-stage call-side timing dampener for `overall >= 70` when daily stochastic is low and weekly adjustment is not strongly confirming.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
