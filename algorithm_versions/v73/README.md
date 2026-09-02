# v73 Algorithm Snapshot

- Status: `shipped`
- DB version: `73`
- Commit: `07e9722b5`
- Resolved commit: `07e9722b5a53080af91ee9fe3be1118fb4c857fc`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation)

AlgorithmVersion message: Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation)
Commit subject: Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation)

## Existing Documentation Hint

Source heading: `v73 (`07e9722b5`) - 2026-06-12 (Honest Dampener Retirements)` (from `.claude/docs/version-history.md`)

Scoring ship (ALGORITHM_VERSION bump `e32fb4ec6`, DB version 73, silo + scoring
lock `a51311fa7`). Executes the N1 dampener-stack ablation
(`experiments/dampener_ablation_v72/{FINDINGS,SHIP_HANDOFF}.md`) - the third
honest-era retirement campaign (v69 weekly, v71 four retirements, now this).
Worktree `algo-exp/v73-dampener-retire`; retirement-by-config (code paths stay).

## Code Delta From Previous Resolved Version

Previous resolved key: `v72`
Diff range: `fc567120..07e9722b`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION       |  2 +-
api.py                  | 54 +++++++++++++++++++++++++++++++++++++--
database/models/core.py | 36 +++++++++++++++++---------
strategy_config.py      | 68 +++++++++++++++++++++++++++++++++++--------------
trader.py               |  7 +++--
5 files changed, 131 insertions(+), 36 deletions(-)
```

### Structured Scoring Variable Delta

- `CALIBRATION_CUTOFF_DATE`: `` -> `2026-06-15`
- `SCORING.CSWC_DAMPEN_K`: `0.5` -> `0.0`
- `SCORING.CWCF_DAMPEN_K`: `0.95` -> `0.0`
- `SCORING.ICH_ENABLED`: `True` -> `False`
- `SCORING.SCW_ENABLED`: `True` -> `False`
- `SCORING.WCF_LIFT_K`: `0.95` -> `0.0`

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history.md:142` - ## v73 (`07e9722b5`) - 2026-06-12 (Honest Dampener Retirements)
- `.claude/docs/version-history.md:193` - Stage-3 portfolio-only ship (NO version bump; scoring stays v73 `07e9722b5`).
- `.claude/docs/known-issues.md:37` - **2026-06-12 v73 DAMPENER RETIREMENTS shipped + ACTIVE (`07e9722b5`, DB version 73, bump `e32fb4ec6`, silo + scoring lock `a51311fa7`):**
- `.claude/docs/trading-strategy.md:5` - **Active scoring version: v73 (`07e9722b5`, shipped 2026-06-12).** v72 minus

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
