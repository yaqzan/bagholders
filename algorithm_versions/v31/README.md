# v31 Algorithm Snapshot

- Status: `shipped`
- DB version: `31`
- Commit: `f3ec7c1`
- Resolved commit: `f3ec7c12793b91b661a51e262f9379f2a621332b`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Strict-future earnings semantics + precomputed effective_date column

AlgorithmVersion message: Strict-future earnings semantics + precomputed effective_date column
Commit subject: Strict-future earnings semantics + precomputed effective_date column

## Existing Documentation Hint

Source heading: `v31 - Strict-Future Earnings Semantics + effective_date Column (2026-04-30)` (from `.claude/docs/version-history-archive.md`)

**Fixes D? same-day-reaction edge case** in EARN_BOOST proximity and V6 volume-amplifier suppression.

## Code Delta From Previous Resolved Version

Previous resolved key: `v30`
Diff range: `9a9da33a..f3ec7c12`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`
- `monte_carlo.py`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION       |   2 +-
database/models/core.py | 108 +++++++++++++++++++++++++++++++++++++++++-------
monte_carlo.py          | 104 ++++++++++++++++++++++++++++------------------
volume_amplifier.py     |  43 ++++++++++++-------
4 files changed, 187 insertions(+), 70 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1215` - f3ec7c1 Strict-future earnings semantics + precomputed effective_date column [v31]
- `.claude/docs/version-history-archive.md:1216` - cd71bee Bump ALGORITHM_VERSION to f3ec7c1
- `.claude/docs/trading-strategy.md:82` - > **30 DTE is the definitive primary instrument (confirmed 2026-05-01).** The prior 15 DTE C1 superiority claim (+54x 22-now, +346x 5y) was validated under the old static 3-mode MC.
- `.claude/docs/version-history.md:1271` - f3ec7c1 Strict-future earnings semantics + precomputed effective_date column [v31]
- `.claude/docs/version-history.md:1272` - cd71bee Bump ALGORITHM_VERSION to f3ec7c1
- `.claude/docs/known-issues.md:842` - Bounded-fill option-pricing-aware MC, N=500, v31 f3ec7c1.
- `.claude/docs/trading-strategy.md:23` - > **30 DTE is the definitive primary instrument (confirmed 2026-05-01).** The prior 15 DTE C1 superiority claim (+54x 22-now, +346x 5y) was validated under the old static 3-mode MC.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
