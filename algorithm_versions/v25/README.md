# v25 Algorithm Snapshot

- Status: `shipped`
- DB version: `25`
- Commit: `9463f02`
- Resolved commit: `9463f028a0aeb865f6ea24741146d4b8e593f40c`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

Mis-stress call softener: recover compressed call alpha on narrow-bull misclass days

AlgorithmVersion message: Mis-stress call softener: recover compressed call alpha on narrow-bull misclass days
Commit subject: Mis-stress call softener: recover compressed call alpha on narrow-bull misclass days

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v24`
Diff range: `41fa5666..9463f028`

Changed snapshot-tracked paths:
- `api.py`
- `backtest_cascade.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `simulator.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
api.py                    |  25 +++++
backtest_cascade.py       |  88 +++++++++++++----
database/models/core.py   | 242 ++++++++++++++++++++++++++++++----------------
database/utils/scoring.py |  20 ++++
simulator.py              | 110 +++++++++++++++++++--
trader.py                 |  61 ++++++++----
volume_amplifier.py       | 188 ++++-------------------------------
7 files changed, 429 insertions(+), 305 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/scoring-algorithm.md:41` - - **Mis-stress call softener** - ?
- `.claude/docs/trading-strategy.md:285` - Each year independently from $50k, 500 iterations per (year x mode).
- `.claude/docs/scoring-algorithm.md:39` - - **Mis-stress call softener** (`MIS_STRESS_CALL_DAMPEN = 0.25`, shipped 2026-04-26 as v25, `9463f02`): on objectively-bull-mislabeled-stress days, soften the call-side regime compression toward 1.0 by `(mis_stress x...
- `.claude/docs/trading-strategy.md:214` - Each year independently from $50k, 500 iterations per (year x mode).

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
