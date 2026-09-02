# VEGA_STATE crash-vega A/B (P1.4) — read (FABLE, 2026-07-13)

**Verdict: CALIBRATION-BLOCKED.** The own-panel ATM-IV~VIX calibration (calibration.json, 1.4y
option_prices era Feb-2025→now) is unidentifiable: r² ≈ 0.001 with a slightly NEGATIVE VIX
coefficient — the panel era contains no VIX-stress episode, so there is no crash-vega signal in the
data to fit. The plug as calibrated is a near-null transform; the A/B (queue 608, N=300 × 9 windows
× Core/Apex, paired) therefore tests nothing about the hypothesis.

## Deltas (on − off)
- Collapse: **+0.00 in all 18 cells.**
- 2020_crash (the hypothesis's home window): Core dDD −0.06pp / dmedret +0.9; Apex −0.00pp — flat.
- Apex: all |dDD| ≤ 0.41pp — flat.
- Core long windows: dDD ~−1pp with medret +235 (22-now) / +823 (5y, ~+27% relative); one cell
  crosses the mechanical escalation line (2024: dDD −3.28pp, dmedret +43.5).

## Ruling
1. **The pre-registered |dDD|>3pp → N=500 escalation is NOT exercised.** It presumed a meaningful
   calibration; validating a favorable delta produced by a wrong-signed, r²≈0.001 transform is the
   instrument-before-outcome error (traps.md: sidecar dosing bar / check the dose before the
   outcome). CALIBRATION-BLOCKED supersedes, same verdict class as COVERAGE-BLOCKED.
2. The Core long-window drift (+27% 5y medret from a near-null transform) is recorded as an
   ARTIFACT NOTE — a tiny systematic mark repricing compounding over 5y — not evidence. Do not chase.
3. **Adoption talk is moot; VEGA_STATE stays default-OFF** (committed inert in `ab2726a3c`).
4. **Re-open conditions (data-shaped only):** (a) crash-spanning IV history — note this is now the
   THIRD independently-blocked justification pointing at crash-era option data (OSK dead, gamma
   raw-panel coverage-blocked, vega-state calibration-blocked); they accumulate as context but none
   individually reopens the L3 buy (its gate stays as written); (b) our own panel organically
   accumulating a VIX>40 episode; (c) a modest recalibration on the Polygon panel's 2022-08 tail
   (VIX~33 max) is possible but would still extrapolate to crash scale by assumption — permitted
   only as a pre-registered refinement WITH an identification check, not as a default.
5. The engine keeps its documented behavior: IV static except earnings; the dead-hold's crash-vega
   credit remains unmodeled — a known, now-quantified-as-unfixable-at-current-data fidelity gap.

## Artifacts
run_ab.py · calibration.json (+ build script) · results/summary.json + per-window jsons/logs ·
queue task 608.
