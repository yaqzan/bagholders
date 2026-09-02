# v37 Algorithm Snapshot

- Status: `shipped`
- DB version: `37`
- Commit: `6f9afda`
- Resolved commit: `6f9afda93c2b70c8f498ac196f4adc74acfa2589`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v37 scoring: post-crash put dampener (PCD)

AlgorithmVersion message: v37 scoring: post-crash put dampener (PCD)
Commit subject: v37 scoring: post-crash put dampener (PCD)

## Existing Documentation Hint

Source heading: `v37 - Post-Crash Put Dampener (PCD ship 2026-05-05, `6f9afda`)` (from `.claude/docs/version-history-archive.md`)

Score-stage dampener that lifts put scores OUT of any put bucket (<=25) when the underlying recently fell more than 1.0 stock-sigmas over the last 10 trading bars. Eliminates the put-cohort regression where puts firing immediately after sharp drops underperform the put baseline by -7.15pp WR15 at the option-aligned barrier (z=-6.88, N=2,767 over 5y).

## Code Delta From Previous Resolved Version

Previous resolved key: `v36`
Diff range: `d5ef1f52..6f9afda9`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/models/core.py`
- `database/utils/scoring.py`
- `simulator.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
database/models/core.py   |  27 ++++++++++++
database/utils/scoring.py | 110 ++++++++++++++++++++++++++++++++++++++++++++++
simulator.py              |   6 +++
4 files changed, 144 insertions(+), 1 deletion(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1244` - 6f9afda v37 scoring: post-crash put dampener (PCD) [v37]
- `.claude/docs/version-history-archive.md:1245` - 20f0fe8 Bump ALGORITHM_VERSION to 6f9afda (v37 PCD post-crash put dampener)
- `.claude/docs/version-history-archive.md:1373` - ## v37 - Post-Crash Put Dampener (PCD ship 2026-05-05, `6f9afda`)
- `.claude/docs/scoring-algorithm.md:42` - - **Post-Crash put Dampener (PCD)** (shipped 2026-05-05 as v37, `6f9afda`): vol-fair score-stage dampener that lifts put scores OUT of any put bucket (<=25) when the underlying recently fell more than 1.0 stock-sigmas...
- `.claude/docs/version-history.md:1300` - 6f9afda v37 scoring: post-crash put dampener (PCD) [v37]
- `.claude/docs/version-history.md:1301` - 20f0fe8 Bump ALGORITHM_VERSION to 6f9afda (v37 PCD post-crash put dampener)
- `.claude/docs/version-history.md:1429` - ## v37 - Post-Crash Put Dampener (PCD ship 2026-05-05, `6f9afda`)
- `.claude/docs/scoring-algorithm.md:40` - - **Post-Crash put Dampener (PCD)** (shipped 2026-05-05 as v37, `6f9afda`): vol-fair score-stage dampener that lifts put scores OUT of any put bucket (<=25) when the underlying recently fell more than 1.0 stock-sigmas...

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
