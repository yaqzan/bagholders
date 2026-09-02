# PREREG — Sentinel calibrated-positive guard battery

STATUS: LOCKED 2026-08-12 before any guard outcome exists (git commit = lock).
Object under test: R1's discovery (`realism_default_flip_2026_08/out/flipR_r1.csv`) —
Sentinel (85+ band, cascade 0.2/0.15/0/0, mp14) reads calibrated-POSITIVE on recent
windows: 22-now +37.4% med / DD 43.5, 5y +45.2% / DD 46.2, 2023 +52.6%, collapse 0.
Prior: the last calibrated-positive config (S9 mp20) died the survivor check. This
battery decides UNVERIFIED → (guard-verified | S9-pattern-dead). No deployment
implication either way (G1/G2/fill-canon arbiters unchanged; plan posture unchanged).

## Arms (calibrated lens = engine default post-flip; env set explicitly anyway)

- **G-S1 survivor:** Sentinel × 12 windows × N=500, delisted-EXCLUDED universe.
  PASS = 22-now AND 5y medians remain > 0 AND their WorstDD ≤ full-universe + 3.0pp.
- **G-S2 buffer:** Sentinel × {22-now, 5y, 2023, 2024, 2025, dip} × N=500 at
  TP_FILL_MISS_P=0.20 + GAP_AWARE. PASS = 22-now AND 5y medians ≥ 0.
- **G-S3 trade-count floors (report-only, auto-downgrade rule):** per window, median
  trades per path and median concurrent positions. If median trades/path < 30 on a
  positive window → that window's read is flagged THIN; < 10 → the window is
  reclassified ANECDOTE and cannot support the upgrade regardless of G-S1/G-S2.

## Verdict mapping (LOCKED)

ALL of G-S1, G-S2 pass AND neither 22-now nor 5y is ANECDOTE-thin →
**"calibrated-positive, guard-verified (single N=500 battery; Dec-15 OOS + forward
ledger + fill-canon still arbiters)"** — capital-plan bullet updated to that exact
phrase. Any failure → stays UNVERIFIED with the failing guard named; if the survivor
arm kills the positive, record as S9-pattern (edge lives in delisted cohort).

## Stop rule

These arms only; no extensions after outcomes. FINDINGS.md either way.
~42 cells ≈ 15-20 min, queue high / --db light, fingerprint + close-boundary guards on.
