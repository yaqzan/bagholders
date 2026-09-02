# v29 Algorithm Snapshot

- Status: `shipped`
- DB version: `29`
- Commit: `8473cba`
- Resolved commit: `8473cba17de3990edc66d3cbd7a74d874fe1bceb`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

v29: earnings volume suppression - V6 log-gradient (pre-only, W=2, M=1.0)

AlgorithmVersion message: none
Commit subject: v29: earnings volume suppression - V6 log-gradient (pre-only, W=2, M=1.0)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v28`
Diff range: `e3c8678f..8473cba1`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION   |   2 +-
volume_amplifier.py | 200 ++++++++++++++++++++++++++++++++++++----------------
2 files changed, 140 insertions(+), 62 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1876` - V6 (volume-amplifier earnings log-gradient, originally shipped as v29/`8473cba` and reverted) re-shipped after IV-crush evaluation revealed the revert was based on flat-pricing MC artifacts.
- `.claude/docs/version-history-archive.md:1879` - - `ALGORITHM_VERSION` flipped back to `8473cba` (v29 score rows already in DB)
- `.claude/docs/version-history-archive.md:1947` - ALGORITHM_VERSION_PIN=8473cba PYTHONIOENCODING=utf-8 python -u monte_carlo.py
- `.claude/docs/version-history.md:1932` - V6 (volume-amplifier earnings log-gradient, originally shipped as v29/`8473cba` and reverted) re-shipped after IV-crush evaluation revealed the revert was based on flat-pricing MC artifacts.
- `.claude/docs/version-history.md:1935` - - `ALGORITHM_VERSION` flipped back to `8473cba` (v29 score rows already in DB)
- `.claude/docs/version-history.md:2003` - ALGORITHM_VERSION_PIN=8473cba PYTHONIOENCODING=utf-8 python -u monte_carlo.py

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
