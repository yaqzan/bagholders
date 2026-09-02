# v60 Algorithm Snapshot

- Status: `shipped`
- DB version: `60`
- Commit: `d4a3e9fec`
- Resolved commit: `d4a3e9fec93f6ea25a30861150f5424e8d798676`
- Category: `db_linked_snapshot`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

Ship v60 r054 SCW and DD call cap candidate

AlgorithmVersion message: Ship v60 r054 SCW and DD call cap candidate
Commit subject: Ship v60 r054 SCW and DD call cap candidate

## Existing Documentation Hint

Source heading: `Active Version: v60 (`d4a3e9fec`) - 2026-05-19 (r054 SCW + 30DTE DD Call Cap)` (from `.claude/docs/version-history-archive.md`)

Score-stage and 30DTE portfolio-stage ship. `ALGORITHM_VERSION` points to the
v60 scoring commit `d4a3e9fec`; DB AlgorithmVersion row is v60. The scoring
change refines the v50 Stoch
Conviction Wave into the r054 smooth scalar form: base low-stoch / weak-weekly
SCW remains, but boundary relief, raw-stochastic relief, and an overextension
taper shape how much penalty survives. Sector overlay parameters are present
for explainability/research continuity but `overlay_scale=0.0`, so no active
sector overlay ships in v60.

## Code Delta From Previous Resolved Version

Previous resolved key: `v59`
Diff range: `4fd7ffa9..d4a3e9fe`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `database/models/core.py`
- `database/models/technical.py`
- `database/utils/scoring.py`
- `market_breadth.py`
- `recalculate_scores.py`
- `simulator.py`
- `strategy_config.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION            |   2 +-
api.py                       | 269 ++++++++++++++++++++++--
database/models/core.py      | 149 +++++++++++++-
database/models/technical.py |  38 +++-
database/utils/scoring.py    | 182 +++++++++++++++-
market_breadth.py            |   5 +-
recalculate_scores.py        |  55 ++++-
simulator.py                 |  12 ++
strategy_config.py           |  38 +++-
trader.py                    | 481 ++++++++++++++++++++++++++++++++++++-------
10 files changed, 1108 insertions(+), 123 deletions(-)
```

### Structured Scoring Variable Delta

- Added `SCORING.SCW_BOUNDARY_RELIEF` = `1.35`
- Added `SCORING.SCW_BOUNDARY_WIDTH` = `0.65`
- Added `SCORING.SCW_CONFIRM_MID` = `0.2673067886722032`
- Added `SCORING.SCW_CONFIRM_RELIEF` = `0.0`
- Added `SCORING.SCW_EXT_TAPER_MID` = `1.0`
- Added `SCORING.SCW_EXT_TAPER_STRENGTH` = `0.25`
- Added `SCORING.SCW_EXT_TAPER_WIDTH` = `0.3`
- Added `SCORING.SCW_RAW_STOCH_MID` = `71.21723387348588`
- Added `SCORING.SCW_RAW_STOCH_RELIEF` = `0.05`
- Added `SCORING.SCW_SCALE` = `1.3`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.overlay_confirm_mid` = `0.4595284966001591`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.overlay_confirm_relief` = `0.32724843440236834`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.overlay_confirm_width` = `0.12`
- Added `SCORING.SECTOR_BREADTH_WAVE_PARAMS.overlay_scale` = `0.0`
- Added `SCORING.WEEKLY_PUT_WAVE_ENABLED` = `True`
- Added `SCORING.WEEKLY_PUT_WAVE_FLOOR` = `1.0`
- Added `SCORING.WEEKLY_PUT_WAVE_PEAK` = `1.5`
- Added `SCORING.WEEKLY_PUT_WAVE_POWER` = `1.0`
- Added `SCORING.WEEKLY_PUT_WAVE_WIDTH` = `10.0`

### Structured Portfolio Variable Delta

- `mechanism_registry[2].notes`: `When running portfolio DD ? [LO, HI], scale call alloc linearly from 1.0 down to FLOOR. Above HI = full floor. Below...` -> `When running portfolio DD ? [LO, HI], scale call alloc linearly from 1.0 down to FLOOR. Above HI = full floor. Below...`
- `strategies.30dte.DD_SOFT_BAND_HI`: `0.6` -> `0.55`
- `strategies.30dte.DD_SOFT_BAND_LO`: `0.4` -> `0.35`
- `strategies.30dte.DD_SOFT_CALL_FLOOR`: `0.5` -> `0.4`
- `strategies.30dte.MAX_POSITIONS_CALL`: `` -> `12`

## Source References

- `.claude/docs/version-history-archive.md:5` - ## Active Version: v60 (`d4a3e9fec`) - 2026-05-19 (r054 SCW + 30DTE DD Call Cap)
- `.claude/docs/version-history-archive.md:8` - v60 scoring commit `d4a3e9fec`; DB AlgorithmVersion row is v60.
- `.claude/docs/scoring-algorithm.md:27` - - **SCW - Stoch Conviction Wave** - ?
- `.claude/docs/known-issues.md:126` - **v60 r054 SCW + 30DTE DD call cap shipped 2026-05-19 (`d4a3e9fec`)** - score-stage refinement of the Stoch Conviction Wave plus a 30DTE-only Stage 3 portfolio overlay.
- `.claude/docs/known-issues.md:202` - **Current active scoring: v60 (`d4a3e9fec`)** - v38 (CWWD) + v39 (PESS) + v43 (MCD) + v44 (ICH) + v45 (Breadth ETF de-contamination) + v60 r054 SCW + pre-v58 continuation echo + v57 direct Market Wave score transform...
- `.claude/docs/trading-strategy.md:344` - > - `ALGORITHM_VERSION = d4a3e9fec` (v60 r054 SCW; v58 continuation retune remains reverted)
- `.claude/docs/version-history.md:61` - ## Active Version: v60 (`d4a3e9fec`) - 2026-05-19 (r054 SCW + 30DTE DD Call Cap)
- `.claude/docs/version-history.md:64` - v60 scoring commit `d4a3e9fec`; DB AlgorithmVersion row is v60.
- `.claude/docs/scoring-algorithm.md:27` - - **SCW - Stoch Conviction Wave** (v50 `b0c1954`, refined 2026-05-19 as v60 `d4a3e9fec`): conservative call-side timing dampener for `overall >= 70` when daily stochastic is low and weekly adjustment is not strongly c...
- `.claude/docs/known-issues.md:9` - **v60 r054 SCW + 30DTE DD call cap shipped 2026-05-19 (`d4a3e9fec`)** - score-stage refinement of the Stoch Conviction Wave plus a 30DTE-only Stage 3 portfolio overlay.
- `.claude/docs/known-issues.md:85` - **Current active scoring: v60 (`d4a3e9fec`)** - v38 (CWWD) + v39 (PESS) + v43 (MCD) + v44 (ICH) + v45 (Breadth ETF de-contamination) + v60 r054 SCW + pre-v58 continuation echo + v57 direct Market Wave score transform...
- `.claude/docs/known-issues.md:89` - \\| Algorithm \\| v60 (`d4a3e9fec`) \\| v60 (same) \\|
- `.claude/docs/trading-strategy.md:5` - **Active scoring version: v60 (`d4a3e9fec`, shipped 2026-05-19).** v60 adds the
- `.claude/docs/trading-strategy.md:231` - > - `ALGORITHM_VERSION = d4a3e9fec` (v60 r054 SCW; v58 continuation retune remains reverted)

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
