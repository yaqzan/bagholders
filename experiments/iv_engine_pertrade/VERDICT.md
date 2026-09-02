# Per-Trade Engine Validation (F2+gamma vs production) — VERDICT MEMO (FINAL)

**Date:** 2026-07-13 · **Owner:** FABLE (architect) · **Status: REFINEMENT ROUND FAILED M1 →
the (GAMMA_AWARE + IV_MODEL) pair is PARKED as A/B-only infrastructure. No adoption, no further
refit rounds against this validation set (pre-registered single-round cap).**
**Pre-registration:** DESIGN.md (A1-A3) + the refinement scope (R1-R3) in the interim verdict,
each frozen before its compute. Ledger: queue #597, N=2,551 matched in-sample (563 liquid-primary),
per-trade contract selection persisted.

## Round 1 (initial bars, liquid-primary, date-clustered)
M1 FAIL (f2 beats rv in 1/3 terciles; calm-name signed bias +0.328 on REAL contracts) ·
M2 SPLIT (d10 PASS t −2.21; d15 tie, decomposing to ex-earnings PASS t −3.88 vs earnings-window
hostile t +2.20) · M3 TP-agreement noninferior (+0.019); SL-FNR point-worse on N=33 events —
UNDERPOWERED · A3: implied ATM constant ≈ 0.40 (mapping fine; the per-tercile LEVEL is the issue).

## Refinement round (R1-R3, executed 2026-07-13)
- **R1:** piecewise-intercept form chosen (R1i, 5 params; frozen a_T1=0.17704, a_T2=0.18533,
  a_T3=0.30649, b_rv=0.07519, c_vix=0.005719, clamp unchanged) — the mechanical OOF winner (R1iii
  per-tercile scale) was REJECTED on the same reverse-fold pathology that disqualified F0
  (+19.4% reverse degradation vs R1i's +11.8%).
- **M1 re-applied: STILL 1/3 terciles.** Overall now beats RV (0.1982 vs 0.2153) and the refit
  improves on F2 in EVERY liquid cell; T1_low gap narrowed 81% (+0.0361 → +0.0067) but is not a
  beat; T3_high unchanged (+0.0044 ≈ tie, not a beat). Bar: >=2/3. **FAIL.**
- **M2:** d10 PASS, marginally stronger (−0.0576, t −2.36; high-RV better). d15 ex-earnings
  (R2 deciding cell): PASS (−0.1829, t −3.82) — though the refit gave back some d15 path accuracy
  vs original F2 (+0.0229, t +2.83) while still clearing production comfortably: premium-level and
  path-fidelity accuracy pull in different directions. Earnings watch line still hostile
  (+0.5714, t +2.15) — P1.4 scope.
- **M3:** carried unchanged (first-touch walk not persisted at daily grain) — SL-FNR remains
  UNDERPOWERED at N=33.
- **R3 parity: consistent** — both the arms and production apply earnings-crush vega_ratio through
  the same option_pricing pathway (arms use the real empirical ratio, disclosed in DESIGN §3;
  production samples from the empirical pool). No omission; no both-ways rescore required.

## The load-bearing finding (why the park is structural, not tunable)
On the calibration panel's own reverse-fold OOF, F2 UNDER-predicts calm-name IV (resid +0.048);
on real contracts it OVER-prices them (signed bias +0.328). **The Polygon-panel BS-derived IV and
real yfinance contract premiums systematically disagree on calm names** — a refit on the panel is
calibrated blind to the exact pathology the real-contract bar measures. No functional form fixes a
calibration-target gap. (→ traps.md: panel-derived IV is not real premium.)

## Rulings
1. **PARKED:** GAMMA_AWARE + IV_MODEL stay default-OFF, A/B-only. Their banked value stands:
   the adversarially-verified MC explosion-killer result (experiments/iv_premium_model/VERDICT.md)
   and the committed engine A/B plumbing.
2. **Per-trade evidence map (for any future re-open):** the candidate is the better PATH model
   (d10 overall; d15 ex-earnings, both rounds, both strong) and a worse/equal PREMIUM-LEVEL model
   on calm names; earnings windows need vol dynamics (P1.4), not premium statics.
3. **Re-open conditions — NEW DATA CLASS ONLY, never another form iteration on this panel/ledger:**
   (a) real-fill capture from the P3.7 slippage loop (true premiums paid), (b) a mid-price/quote
   source replacing lastPrice-derived costs, or (c) P1.4 vega-state shipping (fixes the earnings
   regime and re-frames d15). Any re-open re-runs THIS harness (build_ledger.py + bars verbatim).
4. **December watch item (cheap, non-gating):** re-read M3 SL-FNR when real-SL event count
   ~doubles (~Dec-2026); ledger machinery exists, one queue job + one metrics pass.

## Artifacts
DESIGN.md (pre-reg + A1-A3 + refinement scope) · build_ledger.py / compute_metrics.py /
refit_premium_model.py / rescore_refined.py / compute_metrics_refined.py ·
refit_report.{json,txt} · results/pertrade_results.{json,txt} + refinement_results.{json,txt} ·
.cache/iv_engine_pertrade/ledger_v1{,_refined}.parquet · queue #597.
