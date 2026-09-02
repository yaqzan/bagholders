# v13 Algorithm Snapshot

- Status: `shipped`
- DB version: `13`
- Commit: `8988474`
- Resolved commit: `8988474543ead25152a6690b1ff247baa274ef87`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Some dashboard touch up, bug fix,new algoirthm

AlgorithmVersion message: Some dashboard touch up, bug fix,new algoirthm
Commit subject: Some dashboard touch up, bug fix,new algoirthm

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v12`
Diff range: `edf6bd10..89884745`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION       |   2 +-
api.py                  | 560 ++++++++++++++++++++++++------------------------
database/models/core.py | 112 +++++++++-
trader.py               |  28 ++-
volume_amplifier.py     |   5 +
5 files changed, 408 insertions(+), 299 deletions(-)
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
