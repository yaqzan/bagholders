# v63 Algorithm Snapshot

- Status: `shipped`
- DB version: `63`
- Commit: `7b263922f`
- Resolved commit: `7b263922fd8524b439d2ceb97868be2a32fb5669`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v63 scoring: add BB location taper candidate

AlgorithmVersion message: v63 scoring: add BB location taper candidate
Commit subject: v63 scoring: add BB location taper candidate

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v62`
Diff range: `d4d63798..7b263922`

Changed snapshot-tracked paths:
- `database/utils/scoring.py`
- `strategy_config.py`

Git diff stat:

```text
database/utils/scoring.py | 144 +++++++++++++++++-----------------------------
strategy_config.py        |  40 +++++++------
2 files changed, 75 insertions(+), 109 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.BB_LOCATION_TAPER_CALL_DAMPEN` = `3.0`
- Added `SCORING.BB_LOCATION_TAPER_CALL_GATE` = `75`
- Added `SCORING.BB_LOCATION_TAPER_CALL_MID_HI` = `65.0`
- Added `SCORING.BB_LOCATION_TAPER_CALL_MID_LO` = `35.0`
- Added `SCORING.BB_LOCATION_TAPER_ENABLED` = `True`
- Added `SCORING.BB_LOCATION_TAPER_PUT_BB_LO` = `35.0`
- Added `SCORING.BB_LOCATION_TAPER_PUT_GATE` = `25`
- Added `SCORING.BB_LOCATION_TAPER_PUT_LIFT` = `3.0`
- Removed `SCORING.MACD_PUT_GATE_MODE` = `deep_salvage`
- Removed `SCORING.MACD_PUT_WAVE_BOOST` = `0.65`
- Removed `SCORING.MACD_PUT_WAVE_CENTER` = `26.0`
- Removed `SCORING.MACD_PUT_WAVE_MACD_WIDTH` = `2.5`
- Removed `SCORING.MACD_PUT_WAVE_MAX_FACTOR` = `1.0`
- Removed `SCORING.MACD_PUT_WAVE_MIN_FACTOR` = `0.0`
- Removed `SCORING.MACD_PUT_WAVE_SETUP_POWER` = `1.0`
- Removed `SCORING.MACD_PUT_WAVE_SUPPRESS` = `1.0`
- Removed `SCORING.MACD_PUT_WAVE_WIDTH` = `1.0`

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
