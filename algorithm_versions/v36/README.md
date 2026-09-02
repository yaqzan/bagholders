# v36 Algorithm Snapshot

- Status: `shipped`
- DB version: `36`
- Commit: `d5ef1f5`
- Resolved commit: `d5ef1f5265134da281bef53eadf7af84dea2c702`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v36 scoring: CSWC re-calibration (K 0.30->0.50, wg 12->14)

AlgorithmVersion message: v36 scoring: CSWC re-calibration (K 0.30->0.50, wg 12->14)
Commit subject: v36 scoring: CSWC re-calibration (K 0.30->0.50, wg 12->14)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v35`
Diff range: `e77714f6..d5ef1f52`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`
- `monte_carlo_15dte.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
database/utils/scoring.py |  26 +++---
monte_carlo_15dte.py      |  59 ++++++++----
strategy_config.py        |  38 ++++++--
trader.py                 | 233 +++++++++++++++++++++++++++++++++++++++++++---
5 files changed, 302 insertions(+), 56 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- `strategies.15dte.PUT_TIER_ALLOC.put_low`: `0.12` -> `0.08`
- `strategies.15dte.PUT_TIER_ALLOC.put_mid`: `0.12` -> `0.08`
- `strategies.15dte.PUT_TIER_ALLOC.put_top`: `0.1` -> `0.08`
- `strategies.15dte.TIER_ALLOC.low`: `0.15` -> `0.08`
- `strategies.15dte.TIER_ALLOC.mid`: `0.15` -> `0.12`
- `strategies.15dte.TIER_ALLOC.top`: `0.12` -> `0.17`

## Source References

- No extra doc references found.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
