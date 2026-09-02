# Equity-Milestone Glide Path (P3.4 / N2) — VERDICT (FABLE, 2026-07-14)

**CLOSED at screen-1 per the pre-registered else-clause: static + manual migration is the answer.**
Pre-registration: DESIGN.md (+ FABLE rulings 1-5, incl. the spend cap and the honest expectation).
Harness: composition over Core/Sentinel N=100 panels (queue 631), 39/39 policy tests, validation arm
bit-exact vs lifecycle_mc's core_only (0.0 relative diff on all 8 stats).

## Screen-1 (N=100, 24 cells + NODDSB ablation)
- **Every glide cell is an unconditional TIE with its static comparator**: medians within 0.1-1.1%,
  p10 and worstDD identical (71.9% — the DD trough occurs before any transition can fire, always).
  Collapse 0.0 everywhere.
- Transition rates at realistic capital are tiny (T500k: 8-19% at $25-50k starts; T1M/T2M mostly
  0-8%) — the reachability arithmetic in DESIGN.md predicted exactly this: the policy is a
  median-path no-op and cannot Pareto-dominate on the row's unconditional bar.
- sentinel_only as a START state is strictly dominated at small capital (half the terminal wealth
  for ~7pp less DD) — preservation-at-scale is not a small-account posture.
- NODDSB ablation: 6/18 glide cells flip a section-10 arithmetic flag — the DD-soft-band interacts
  with transition timing, noted for any future re-open; changes no conclusion here.
- Escalation to screen-2/gate NOT exercised (pre-registered skip on across-the-board ties).

## Rulings
1. **P3.4 CLOSED.** The N2 lead's own else-clause fires. Bonus correction recorded: the original
   lead's "Apex" predates the 2026-06-17 rename and meant today's CORE — the 3-tier reading was a
   naming artifact (DESIGN.md section 15, ruling 4).
2. **Re-open trigger is REAL-WORLD, not backtest:** when live account equity actually approaches a
   transition threshold (~$500k), re-run this exact harness (panels + policy.py, committed) with
   then-current profiles. Until then, manual migration per the capital-lifecycle framing stands.
3. Assets banked: the composition harness generalizes lifecycle_mc's (arm-agnostic panels +
   policy layer + late-DD-dollars metric) — reusable for any future path-dependent policy question.

## Artifacts
DESIGN.md (pre-reg + rulings) · envs.py / panels.py / policy.py / test_glide_policy.py ·
results/screen_n100.json + panels/ · queue task 631.
