# v43 Algorithm Snapshot

- Status: `shipped`
- DB version: `43`
- Commit: `e083032`
- Resolved commit: `e0830320b55a4491832adb64738f562b70b93d82`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

feat: MCD score-stage mcap dampener (mcap-aware call confidence weight)

AlgorithmVersion message: feat: MCD score-stage mcap dampener (mcap-aware call confidence weight)
Commit subject: feat: MCD score-stage mcap dampener (mcap-aware call confidence weight)

## Existing Documentation Hint

Source heading: `Active Version: v43 (`e083032`) - 2026-05-07 (MCD mcap dampener) [now superseded by v44]` (from `.claude/docs/version-history-archive.md`)

v43 = v39 (CWWD + PESS) + MCD (Mcap Dampener). Score-stage continuous
asymmetric dampener for calls in [70, 84] using log10(mcap_b) as a
confidence-shifter. Empirical cohort signal is structural (year-stable
2022-2025, monotonic across mcap bins): at fixed score, large-cap call
TP rate exceeds micro-cap by 8.2pp on 75+ at 5y. The dampener targets
the over-confidence inflation in mid/small-cap 80-84 signals while
leaving 70-72 small-caps and large-caps ($100B+) essentially untouched.

## Code Delta From Previous Resolved Version

Previous resolved key: `v42`
Diff range: `5e6e3d31..e0830320`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/models/technical.py`
- `database/utils/scoring.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION            |   2 +-
api.py                       |  43 ---------
database/models/core.py      | 206 ++++++++++--------------------------------
database/models/technical.py |  78 ----------------
database/utils/scoring.py    | 167 +++++++++++++++++-----------------
simulator.py                 |  82 +++--------------
strategy_config.py           |  50 +++++------
trader.py                    | 209 +++++++++++++++++++++++++++++++------------
8 files changed, 312 insertions(+), 525 deletions(-)
```

### Structured Scoring Variable Delta

- `CALIBRATION_CUTOFF_DATE`: `` -> `2026-05-15`

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:781` - ## Active Version: v43 (`e083032`) - 2026-05-07 (MCD mcap dampener) [now superseded by v44]
- `.claude/docs/version-history-archive.md:791` - ### v43 - MCD: Mcap Dampener (ship 2026-05-07, `e083032`)
- `.claude/docs/scoring-algorithm.md:23` - - **MCD - Mcap Dampener** - ?
- `.claude/docs/known-issues.md:196` - **v43 MCD shipped 2026-05-07 (`e083032`)** - score-stage continuous mcap dampener for calls in [70, 84].
- `.claude/docs/version-history.md:837` - ## Active Version: v43 (`e083032`) - 2026-05-07 (MCD mcap dampener) [now superseded by v44]
- `.claude/docs/version-history.md:847` - ### v43 - MCD: Mcap Dampener (ship 2026-05-07, `e083032`)
- `.claude/docs/scoring-algorithm.md:23` - - **MCD - Mcap Dampener** (shipped 2026-05-07 as v43, `e083032`): score-stage continuous, asymmetric (calls-only) confidence-shifter using `log10(mcap_b)` as a smooth structural weight.
- `.claude/docs/known-issues.md:79` - **v43 MCD shipped 2026-05-07 (`e083032`)** - score-stage continuous mcap dampener for calls in [70, 84].

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
