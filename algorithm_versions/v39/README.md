# v39 Algorithm Snapshot

- Status: `shipped`
- DB version: `39`
- Commit: `200f33a`
- Resolved commit: `200f33a273a76f77a000f117e5c071c32e179c28`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT

AlgorithmVersion message: v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT
Commit subject: v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT

## Existing Documentation Hint

Source heading: `v39 (`200f33a`) - 2026-05-06 (CWWD + PESS)` (from `.claude/docs/version-history-archive.md`)

v39 = v38 (CWWD) + PESS.  Both are score-stage replacements for cascade-stage
filters (WEAK_WEEKLY_CALL_DROP and EARN_SUPP_PUT respectively) that were
shipped earlier as portfolio-stage knobs.  The score-stage encoding fixes
the **dashboard divergence problem** - under the old filters, scores like
73 wadj-neg call or 18-with-earnings put would show on the dashboard with
green/red badges but the cascade would silently skip them.  Score-stage
versions drift the affected cohorts out of qualifying ranges so the
dashboard exactly matches what the cascade trades.

## Code Delta From Previous Resolved Version

Previous resolved key: `v38`
Diff range: `b093e2d1..200f33a2`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/utils/scoring.py | 43 +++++++++++++++++++++++++++++++++++++++++++
strategy_config.py        | 12 ++++++++++--
trader.py                 | 10 ++++------
4 files changed, 58 insertions(+), 9 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

- `strategies.15dte.EARN_SUPP_PUT`: `True` -> `False`
- `strategies.30dte.EARN_SUPP_PUT`: `True` -> `False`

## Source References

- `.claude/docs/version-history-archive.md:883` - ## v39 (`200f33a`) - 2026-05-06 (CWWD + PESS) - was active 2026-05-06 to 2026-05-07
- `.claude/docs/version-history-archive.md:1141` - ## v39 (`200f33a`) - 2026-05-06 (CWWD + PESS)
- `.claude/docs/version-history-archive.md:1256` - 200f33a v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT [v39]
- `.claude/docs/version-history-archive.md:1257` - d4f2a2c Bump ALGORITHM_VERSION to 200f33a (v39 PESS)
- `.claude/docs/version-history-archive.md:1262` - Active version returned to **v39 (`200f33a`)** after a two-stage rollback.
- `.claude/docs/version-history-archive.md:1291` - **SHIPPED then REVERTED 2026-05-07.
- `.claude/docs/version-history-archive.md:1354` - 3.
- `.claude/docs/scoring-algorithm.md:35` - - **PESS - Put Earnings Score Suppression** (shipped 2026-05-06 as v39, `200f33a`): score-stage replacement for the EARN_SUPP_PUT cascade-stage filter (10 days live before retirement).
- `.claude/docs/version-history.md:939` - ## v39 (`200f33a`) - 2026-05-06 (CWWD + PESS) - was active 2026-05-06 to 2026-05-07
- `.claude/docs/version-history.md:1197` - ## v39 (`200f33a`) - 2026-05-06 (CWWD + PESS)
- `.claude/docs/version-history.md:1312` - 200f33a v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT [v39]
- `.claude/docs/version-history.md:1313` - d4f2a2c Bump ALGORITHM_VERSION to 200f33a (v39 PESS)
- `.claude/docs/version-history.md:1318` - Active version returned to **v39 (`200f33a`)** after a two-stage rollback.
- `.claude/docs/version-history.md:1347` - **SHIPPED then REVERTED 2026-05-07.
- `.claude/docs/version-history.md:1410` - 3.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
