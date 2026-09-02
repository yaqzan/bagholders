# v70 Algorithm Snapshot

- Status: `shipped`
- DB version: `70`
- Commit: `c70d16d22dc8ad900d19f7014907774edaa3ae26`
- Resolved commit: `c70d16d22dc8ad900d19f7014907774edaa3ae26`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated)

AlgorithmVersion message: v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated)
Commit subject: v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v69`
Diff range: `8b59206c..c70d16d2`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`
- `monte_carlo.py`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/utils/scoring.py | 12 +++++++----
monte_carlo.py            | 30 +++++++++++++++------------
strategy_config.py        | 52 +++++++++++++++++++++++++++++++----------------
4 files changed, 60 insertions(+), 36 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.EARN_BOOST_WINDOW`: `5` -> `7`
- `SCORING.PESS_DAYS_MAX`: `7` -> `5`

### Structured Portfolio Variable Delta

- `strategies.30dte.MAX_POSITIONS`: `14` -> `8`
- `strategies.30dte.MAX_POSITIONS_CALL`: `12` -> `7`
- `strategies.30dte.MAX_POSITIONS_PUT`: `8` -> `2`
- `strategies.30dte.PUT_TIER_ALLOC.put_low`: `0.08` -> `0.0`
- `strategies.30dte.PUT_TIER_ALLOC.put_mid`: `0.1` -> `0.0`
- `strategies.30dte.PUT_TIER_ALLOC.put_top`: `0.12` -> `0.0`
- `strategies.30dte.TIER_ALLOC.low`: `0.1` -> `0.0`
- `strategies.30dte.TIER_ALLOC.mid`: `0.1` -> `0.0`
- `strategies.30dte.TIER_ALLOC.top`: `0.15` -> `0.0975`
- `strategies.30dte.TIER_ALLOC.ultra`: `0.2` -> `0.13`
- `strategies.30dte.TP_SIGMA_BASE`: `1.2012` -> `1.0192`
- `strategies.30dte.option.NET_TP_BASE`: `0.33` -> `0.28`
- `strategies.30dte.option.TP_BASE`: `0.33` -> `0.28`

## Source References

- `.claude/docs/version-history.md:482` - v70 `c70d16d22`).
- `.claude/docs/version-history.md:525` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/version-history.md:587` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/version-history.md:652` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/version-history.md:703` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/version-history.md:732` - Portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/version-history.md:789` - Stage 3 portfolio-only ship (NO `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`).
- `.claude/docs/known-issues.md:213` - \\| Algorithm \\| **v70 (`c70d16d22`, honest)** \\| v70 (same) \\|

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
