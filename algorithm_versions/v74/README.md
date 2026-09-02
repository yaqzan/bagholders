# v74 Algorithm Snapshot

- Status: `shipped`
- DB version: `74`
- Commit: `f9fb7b934`
- Resolved commit: `f9fb7b9343cafeccb18fd4b9966d19441add00c6`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST)

AlgorithmVersion message: v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST)
Commit subject: v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST)

## Existing Documentation Hint

Source heading: `Active Version: v74 (`f9fb7b934`) - 2026-06-15 (Lean - post-pre_boost tail retired)` (from `.claude/docs/version-history.md`)

Scoring ship (ALGORITHM_VERSION bump `99cd2f0b1`, DB version 74, silo
`algorithm_versions/v74`, pushed `5a393f871`). Outcome of the weatherization
audit -> Verification Substrate -> Phase 3d parsimony ablation
(`experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md`).

## Code Delta From Previous Resolved Version

Previous resolved key: `v73`
Diff range: `07e9722b..f9fb7b93`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION  |  2 +-
api.py             | 67 +++++++++++++++++++++++++++++++++++++++++++++++++-----
strategy_config.py | 34 +++++++++++++++++----------
trader.py          |  4 ++--
4 files changed, 86 insertions(+), 21 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.CONT_BOOST_ENABLED`: `True` -> `False`
- `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_ENABLED`: `True` -> `False`
- `SCORING.EARN_BOOST_ENABLED`: `True` -> `False`
- `SCORING.WVD_WAVE_ENABLED`: `True` -> `False`

### Structured Portfolio Variable Delta

- `strategies.30dte.TIER_ALLOC.low`: `0.05` -> `0.03`
- `strategies.30dte.TIER_ALLOC.mid`: `0.1` -> `0.08`

## Source References

- `.claude/docs/version-history.md:3` - ## Active Version: v74 (`f9fb7b934`) - 2026-06-15 (Lean - post-pre_boost tail retired)
- `.claude/docs/version-history.md:34` - active scoring stays v74 `f9fb7b934`).
- `.claude/docs/version-history.md:96` - Portfolio-stage ship (NO `ALGORITHM_VERSION` bump; scoring stays v74 `f9fb7b934`).
- `.claude/docs/known-issues.md:17` - **2026-06-17 APEX/CORE RESTRUCTURE shipped + ACTIVE (portfolio-only, NO version bump; active scoring stays v74 `f9fb7b934`):**
- `.claude/docs/known-issues.md:31` - **2026-06-15 v74 LEAN SHIP - ACTIVE (`f9fb7b934`, DB version 74, bump `99cd2f0b1`, silo `algorithm_versions/v74`, pushed `5a393f871`):**
- `.claude/docs/known-issues.md:262` - (2026-06-15, `f9fb7b934` - see the v74 entry above): the whole continuation-echo score-stage

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
