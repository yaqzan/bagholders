# v58 Algorithm Snapshot

- Status: `shipped`
- DB version: `58`
- Commit: `3cfc4dc2`
- Resolved commit: `3cfc4dc2fd9061556a07e16098ed3e1784023bcb`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Retune continuation echo weights for v57 WR7 utility

AlgorithmVersion message: Retune continuation echo weights for v57 WR7 utility
Commit subject: Retune continuation echo weights for v57 WR7 utility

## Existing Documentation Hint

Source heading: `v58 (`3cfc4dc2`) - 2026-05-13 (Continuation Echo Legacy WR7/N Retune, REVERTED 2026-05-15)` (from `.claude/docs/version-history-archive.md`)

Score-stage ship, later reverted. `ALGORITHM_VERSION` pointed to `3cfc4dc2` via
pointer commit `26317eba`; DB AlgorithmVersion row is v58. This retuned the existing
continuation echo wave on top of v57 direct Market Wave scores. The production
scorer keeps the existing hard-75 retention rule: sub-75 continuation lifts are
only persisted when the rounded destination score reaches the 75+ tradable
call gate.

## Code Delta From Previous Resolved Version

Previous resolved key: `v57`
Diff range: `e568b2f4..3cfc4dc2`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `strategy_config.py`

Git diff stat:

```text
ALGORITHM_VERSION  |  2 +-
strategy_config.py | 38 +++++++++++++++++++-------------------
2 files changed, 20 insertions(+), 20 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.CONT_BOOST_ALPHA`: `1.1669054395351226` -> `1.1681749185532881`
- `SCORING.CONT_BOOST_FIZZLER_PENALTY`: `0.4175780458412032` -> `0.8054693453381145`
- `SCORING.CONT_BOOST_LOSS_PENALTY`: `0.3845330519083049` -> `0.17226097568036594`
- `SCORING.CONT_BOOST_MAG_EXP`: `0.8435514312118402` -> `0.6802379841567793`
- `SCORING.CONT_BOOST_MAX_LIFT`: `4.473918889785761` -> `5.264597294605774`
- `SCORING.CONT_BOOST_SIG_MIN`: `0.025421147106629318` -> `0.06314416804507036`
- `SCORING.CONT_BOOST_SIG_NORM`: `0.9032562577567048` -> `1.0658971606985257`
- `SCORING.CONT_BOOST_TAU`: `38.15912046629926` -> `37.7342159721648`
- `SCORING.CONT_BOOST_W15`: `0.022856284102839568` -> `0.03293486929615211`
- `SCORING.CONT_BOOST_W30`: `0.6756014121839538` -> `0.9368411817737656`
- `SCORING.CONT_BOOST_W60`: `0.7661245978520328` -> `0.9492071805439206`
- `SCORING.CONT_BOOST_W7`: `0.18104215061573875` -> `0.28464019413712077`

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:108` - ## v58 (`3cfc4dc2`) - 2026-05-13 (Continuation Echo Legacy WR7/N Retune, REVERTED 2026-05-15)
- `.claude/docs/version-history-archive.md:110` - Score-stage ship, later reverted.
- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** - ?
- `.claude/docs/known-issues.md:185` - **v58 continuation echo retune shipped 2026-05-13 (`3cfc4dc2`, pointer commit `26317eba`) and REVERTED 2026-05-15** - score-stage retune of the existing continuation echo wave on top of v57 direct Market Wave scores.
- `.claude/docs/known-issues.md:280` - recalc-prior fix (`e3ed806`), and the active v58 retune (`3cfc4dc2`).
- `.claude/docs/active-investigations/continuation-boost.md:5` - **Follow-up flag (2026-05-13):** UAMY 2025-07-18 exposed a likely failure mode in the current continuation echo lineage: `cont_lift` can promote an exhaustion-entry candle into the tradable CALL gate.
- `.claude/docs/version-history.md:164` - ## v58 (`3cfc4dc2`) - 2026-05-13 (Continuation Echo Legacy WR7/N Retune, REVERTED 2026-05-15)
- `.claude/docs/version-history.md:166` - Score-stage ship, later reverted.
- `.claude/docs/scoring-algorithm.md:29` - - **Continuation Echo Wave** (legacy boost v33 `28fa5227`; temporal echo wave v52 `f66bf9b9`; prior-fix v53 `e3ed806`; v58 retune `3cfc4dc2` reverted 2026-05-15): same-side CALL prior wins echo into current CALL score...
- `.claude/docs/known-issues.md:68` - **v58 continuation echo retune shipped 2026-05-13 (`3cfc4dc2`, pointer commit `26317eba`) and REVERTED 2026-05-15** - score-stage retune of the existing continuation echo wave on top of v57 direct Market Wave scores.
- `.claude/docs/known-issues.md:147` - recalc-prior fix (`e3ed806`), and the active v58 retune (`3cfc4dc2`).

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
