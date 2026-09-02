# v59 Algorithm Snapshot

- Status: `shipped`
- DB version: `59`
- Commit: `4fd7ffa9`
- Resolved commit: `4fd7ffa919da01c236893e24cbe0cd03c1900eac`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v59 scoring: daily volume authority wave

AlgorithmVersion message: v59 scoring: daily volume authority wave
Commit subject: v59 scoring: daily volume authority wave

## Existing Documentation Hint

Source heading: `v59 (`4fd7ffa9`) - 2026-05-15 (Daily Volume Authority Wave)` (from `.claude/docs/version-history-archive.md`)

Score-stage ship. `ALGORITHM_VERSION` points to `4fd7ffa9` via pointer commit
`8fc70f34`; DB AlgorithmVersion row is v59. This replaces the brittle daily
volume conviction contribution with the Daily Volume Authority Wave: daily
conviction still supplies the short-horizon impulse, but weekly volume force
governs how much authority that impulse is allowed to carry. The shipped shape
keeps the mechanism wave-like and smooth: high-quality weekly volume lets daily
conviction matter, weak weekly authority fades it, and high score tiers are
softened rather than cliff-filtered.

## Code Delta From Previous Resolved Version

Previous resolved key: `v58`
Diff range: `3cfc4dc2..4fd7ffa9`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/utils/scoring.py`
- `recalculate_scores.py`
- `strategy_config.py`
- `trader.py`
- `volume_amplifier.py`

Git diff stat:

```text
ALGORITHM_VERSION         |   2 +-
api.py                    | 550 ++++++++++++++++++++++++++++++++++++++++++++--
database/models/core.py   | 389 ++++++++++++++++++++++++--------
database/utils/scoring.py | 213 ++++++++++++++++++
recalculate_scores.py     | 236 +++++++++++++++++++-
strategy_config.py        |  84 +++++--
trader.py                 | 237 +++++++++++++++-----
volume_amplifier.py       |  39 ++--
8 files changed, 1538 insertions(+), 212 deletions(-)
```

### Structured Scoring Variable Delta

- `SCORING.CONT_BOOST_ALPHA`: `1.1681749185532881` -> `1.1669054395351226`
- `SCORING.CONT_BOOST_FIZZLER_PENALTY`: `0.8054693453381145` -> `0.4175780458412032`
- `SCORING.CONT_BOOST_LOSS_PENALTY`: `0.17226097568036594` -> `0.3845330519083049`
- `SCORING.CONT_BOOST_MAG_EXP`: `0.6802379841567793` -> `0.8435514312118402`
- `SCORING.CONT_BOOST_MAX_LIFT`: `5.264597294605774` -> `4.473918889785761`
- `SCORING.CONT_BOOST_SIG_MIN`: `0.06314416804507036` -> `0.025421147106629318`
- `SCORING.CONT_BOOST_SIG_NORM`: `1.0658971606985257` -> `0.9032562577567048`
- `SCORING.CONT_BOOST_TAU`: `37.7342159721648` -> `38.15912046629926`
- `SCORING.CONT_BOOST_W15`: `0.03293486929615211` -> `0.022856284102839568`
- `SCORING.CONT_BOOST_W30`: `0.9368411817737656` -> `0.6756014121839538`
- `SCORING.CONT_BOOST_W60`: `0.9492071805439206` -> `0.7661245978520328`
- `SCORING.CONT_BOOST_W7`: `0.28464019413712077` -> `0.18104215061573875`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_ENABLED` = `True`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_gate_hi` = `93.98705157221204`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_gate_lo` = `85.8134945749787`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_k` = `1.2315722683736037`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_mag_mid` = `0.22796926991766234`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_mag_slope` = `3.077358834523319`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_power` = `1.3130647510499514`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.damp_target` = `72.18430402574569`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.dv_mid` = `0.7350114747173406`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.dv_slope` = `2.176388827573777`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.ema_mid` = `26.03622155920991`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.ema_slope` = `0.12720210302809656`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.impulse_mid` = `0.51878283945562`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.impulse_slope` = `2.4988191070931958`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_gate_hi` = `73.40966934017388`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_gate_lo` = `71.40966934017388`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_k` = `0.6156692848396584`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_peak` = `0.08803824538588041`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_power` = `2.1835391672074738`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_target` = `80.10688367262878`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.lift_width` = `0.36519750239702536`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.max_dampen` = `12.746774026718285`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.max_lift` = `4.365319543060418`
- Added `SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.score_fade_family` = `1.0`
- ... 11 additional structured changes omitted.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:42` - ## v59 (`4fd7ffa9`) - 2026-05-15 (Daily Volume Authority Wave)
- `.claude/docs/version-history-archive.md:44` - Score-stage ship.
- `.claude/docs/version-history-archive.md:60` - against v59 (`db:59`, `4fd7ffa9`).
- `.claude/docs/scoring-algorithm.md:115` - **Daily Volume Authority Wave (v59, `4fd7ffa9`)** - ?
- `.claude/docs/known-issues.md:177` - **v59 Daily Volume Authority Wave shipped 2026-05-15 (`4fd7ffa9`, pointer commit `8fc70f34`)** - score-stage replacement for the daily volume conviction signal.
- `.claude/docs/version-history.md:98` - ## v59 (`4fd7ffa9`) - 2026-05-15 (Daily Volume Authority Wave)
- `.claude/docs/version-history.md:100` - Score-stage ship.
- `.claude/docs/version-history.md:116` - against v59 (`db:59`, `4fd7ffa9`).
- `.claude/docs/scoring-algorithm.md:113` - **Daily Volume Authority Wave (v59, `4fd7ffa9`)** replaces the daily-only
- `.claude/docs/known-issues.md:60` - **v59 Daily Volume Authority Wave shipped 2026-05-15 (`4fd7ffa9`, pointer commit `8fc70f34`)** - score-stage replacement for the daily volume conviction signal.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
