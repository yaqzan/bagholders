# VCBW — Vol-Confidence Boundary Wave (NULL RESULT, archived)

**Status: NULL / NOT SHIPPED.** Archived 2026-05-31 for the experiment trail.

A Stage-1 scoring candidate that proposed a two-sided realized-vol confidence
residual on the CALL boundary tiers: LIFT low-vol 70-74 toward ~77 (promote into
the tradable band) and DAMP high-vol 75-79 toward ~72 (demote fragile incumbents);
80+ never touched.

## Why it's a null

It looked like a WR **and** N win when calibrated against the *contaminated* v60
score rows (which were computed with drifted v65-ish code, not true v60). On the
**true v60** baseline (`d4a3e9fec`) the clean with-vs-without isolation showed:

- **Full VCBW: 75+ WR15 −1.41pp** (worse) at +63% N — the lifted low-vol 70-74
  signals (~77% WR) sit *below* true-v60's already-high 75+ baseline (80.71%), so
  they dilute it.
- **Damp-only: 75+ WR15 +0.90pp** at −17% N — a WR-quality purification, not the
  "WR and N" win-win; not compelling enough to ship.

The contaminated-vs-true-v60 discrepancy is what first surfaced the v60 score
contamination (see `.claude/docs/scoring-version-integrity.md`); the
scoring-version guard now prevents that class.

DB AlgorithmVersion rows v67 (`e85282f5a`, VCBW on contaminated base) and v68
(`bb6251c14`, VCBW on true v60) are the rejected candidates. Their `algorithm_versions/`
silo dirs were not committed; regenerate via `trader algorithm snapshot-git-ref`
from those commits if ever needed.

## Scripts (calibration trail)

| Script | Role |
|---|---|
| `build_ledger.py` | Phase A: build the 75-79 CALL boundary feature ledger (v60, holdout-locked) |
| `mine_cohort.py` | Phase A.2: within-cohort z-mining for the discriminator (found realized vol) |
| `analyze_vol.py` | Phase A.3: two-sided vol thesis (vol-tier × score-tier grid) |
| `phase_b_sweep.py` | Phase B: fast-sim LHS sweep of the VCBW knobs |
| `phase_c_drill.py` | Phase C: basin drill + W1-W6 scorecard |
| `tighten.py` | Joint 5y+10y param tightening |
| `scorecard.py` | Full discrete-bucket WR/N scorecard |
| `staging_validate.py` | Real-scorer (ScoreSimulator) reproduction check |
| `verify_winner.py` | Ship-param verification on 5y/10y ledgers |

Do-not-retry: a vol-confidence LIFT on 70-74 dilutes true-v60's high-quality 75+
pool. Only a damp-only purification has signal, and it trades N for WR.
