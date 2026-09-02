# v22 Algorithm Snapshot

- Status: `shipped`
- DB version: `22`
- Commit: `41784e0`
- Resolved commit: `41784e02d46c32eef69141d09bff13260aa9d720`
- Category: `scoring_revert`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Add v22 X-confidence gate on trend dominance (REVERTED)

AlgorithmVersion message: Add v22 X-confidence gate on trend dominance (REVERTED)
Commit subject: Add v22 X-confidence gate on trend dominance

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v21`
Diff range: `aba4f5da..41784e02`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `database/utils/scoring.py`

Git diff stat:

```text
ALGORITHM_VERSION         |  2 +-
database/utils/scoring.py | 38 ++++++++++++++++++++++++++++----------
2 files changed, 29 insertions(+), 11 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:59` - - ~~**X-confidence gate on `d`** (briefly shipped 2026-04-21 as v22, `41784e0`, REVERTED same-day): repurposed the technical_alignment (X) component as a *structural confidence coefficient* gating trend dominance, not...
- `.claude/docs/scoring-algorithm.md:57` - - ~~**X-confidence gate on `d`** (briefly shipped 2026-04-21 as v22, `41784e0`, REVERTED same-day): repurposed the technical_alignment (X) component as a *structural confidence coefficient* gating trend dominance, not...

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
