# FINDINGS — 30-DTE TP-fill fidelity (adjudicated 2026-08-10)

**Status: COMPLETE — MEASURED. The engine's intrabar TP fill is HALF-RIGHT on the
declared day and MOSTLY-RIGHT economically; the calibrated knob values are
TP_FILL_MISS_P ≈ 0.15 (economic) with GAP_AWARE fill-price semantics. Knobs remain
DEFAULT-OFF — any default flip is its own Stage-3 ship with an MC A/B.**

Adjudicated by the orchestrator from out/ tables + direct events-parquet checks.
Prereg: PREREG.md (locked f6f1e7fa pre-outcome; A1 coverage amendment 3a4b36fd,
also pre-outcome). Full run: queue #347. N: 4,403 ledger contracts -> ARM-30
3,216 declared TP events (2,257 matched-filter DTE 25-38) / ARM-15 3,829 (2,688).
Coverage 99.5% both arms. A1 integrity cross-check 0 mismatches / 5,822.

## The five headline facts (matched-filter slice; table: out/tp_fill_optimism_by_tier.md)

1. **Same-day fill is a coin flip, not a certainty.** On days the engine declares an
   intrabar TP touch, a real print at/above the barrier premium exists only
   **48.9%** of the time at TP+30 (52.3% at TP+15). 17.0% of declared touch days
   the contract did not print AT ALL. The daily-bar walk's same-day TP assumption
   is the single largest fidelity gap measured in this engine.
2. **But the resting limit heals most of it: economic never-fill is 15.8%** (ARM-30;
   14.1% ARM-15). 35% of declared touches fill LATE — median 4 calendar days,
   p90 15 — at the SAME limit price. The loss event is never-fill-by-deadline,
   and it is 3x smaller than the same-day miss rate.
3. **Fill-price optimism on genuine fills is ~1.5x, not catastrophic.** The default
   Uniform(barrier, high) fill credits ~+10.6pp of entry premium above barrier;
   real limit mechanics deliver ~+7.0pp, because **gamma = 46.5%** of fills have the
   contract's first print already at/above the barrier (median improvement +15.1pp
   on those). AT_BARRIER (credit 0) is too pessimistic; the default is ~50% too
   generous; GAP_AWARE-class semantics are the honest middle (see caveat C2).
4. **Liquidity tier structure is real and monotone** (the TP-side companion to
   FF-4's SL gap table): never-fill t1 20.4% -> t4 7.8% (t5 10.6% at N=94,
   CI-wide); no-print share t1 25.6% -> t5 3.2%. Thin names miss because they
   DON'T TRADE, not because they trade below the barrier.
5. **The sigma->option mapping reproduces exactly.** Unconditional touch-day
   high-mult median **1.3519** vs FF-1 Link-1's 1.352 (independent pipeline,
   different touch convention). The center of the mapping is not the problem;
   the tails and the fill mechanics are.

Declaration census (context): at TP30/SL-70, 73.0% of the 4,403 funded-signal
contracts declare TP within the 27-cal-day hold; 25.0% SL; 1.9% hard; both 6.

## Knob calibration (PREREG §5 mechanical mapping — numbers, then adjudication)

| Knob | ARM-30 | ARM-15 | Adjudicated value |
|---|---|---|---|
| TP_FILL_MISS_P (economic = never-fill) | 0.158 | 0.141 | **0.15** |
| TP_FILL_MISS_P (mechanical bound = same-day miss) | 0.511 | 0.477 | 0.50 (probe ceiling only) |
| Fill-price semantics | gamma 0.465 | gamma 0.537 | **GAP_AWARE** (rule: gamma>=3%) |
| Overshoot-credit optimism (default/real) | 1.50x | 1.42x | default arm ~1.5x generous |

Per-tier values in out/knob_calibration_draft.md — monotone by tier; a future
tier-aware penalty should use those, but the knobs are global today.

## Caveats (honest, load-bearing)

- **C1 — knob semantics vs reality:** the MISS_P branch forces an immediate
  uncontrolled same-day exit; reality for a never-fill is riding to SL/hard. The
  severity is approximately right and constant across cells (differential-neutral);
  it is a calibration, not a simulation, of the miss path.
- **C2 — gamma operationalization:** measured gamma uses the CONTRACT's first print
  (day-agg open) vs the barrier; the engine's TP_FILL_GAP_AWARE branch keys on the
  UNDERLYING's open. On sparse contracts the first print can be mid-day
  (FF-4: median 31 min latency, p90 4h), so measured gamma bundles true overnight
  gaps with print-latency gaps. For a resting limit placed on entry day the
  improvement is real either way; the +7.0pp real credit is the calibration
  target, whichever branch approximates it.
- **C3 — day-agg highs may include non-RTH prints** (T6); FF-4 minute overlap
  cross-check available as follow-up. Same provenance applies to the A1 opens.
- **C4 — queue-position not modeled:** "a print >= B occurred" is necessary, not
  sufficient, for a real fill (size/priority unmodeled). Fill rates here are
  therefore UPPER bounds on realized fills — the optimism direction is conservative
  for every consumer of this table.

## Registered-expectation deltas (PREREG §0 — surprises stated, not buried)

- Same-day miss measured **51%** vs registered expectation 15-40%: reality is WORSE
  than the registered prior. The economic rate (15.8%) landed inside intuition only
  because late fills are common and fast.
- gamma measured **46.5%** vs registered "low single digits": badly wrong prior —
  I anchored on FF-4's 3.6% UNDERLYING-side SL gap incidence; the contract-side
  first-print object is different and much larger (overnight premium gaps + sparse
  printing). Recorded in LESSONS.
- Tier monotonicity, no-print concentration in t1/t2, never-fill << same-day,
  touch-day median in band: all as registered.

## Tripwires (final adjudication)

A1 integrity 0/5,822 PASS · 6.1 coverage 99.5%/99.5% PASS · 6.3 arm divergence
(3,829 > 3,216) PASS · 6.5 N floors (2,257/2,688 vs 500) PASS · **6.2 PASS on the
correct population** (unconditional with-path median 1.3519 ∈ [1.20,1.50]; the
run report's 1.511 FAIL was computed fills-only — truncated >=1.30 by
construction — an evaluation bug adjudicated in LESSONS, not a data defect;
liquid-only cut not applied, immaterial at this margin).

## Consumers + what changes

- **tpsl_refine Phase D (§6 probe, locked at TP_FILL_MISS_P=0.10):** FLAG per that
  prereg's own grading — 0.10 understates the measured economic 0.15 (modestly)
  and the mechanical bound 0.50 (grossly). Recommended tightening-only amendment:
  add a 0.15 arm alongside the 0.10 probe. NOTE: their close-confirm audit arm is
  HARSHER than any miss probe and the differential already survived it; this is
  belt-and-suspenders for the differential, calibration for the absolute.
- **MC absolute realism (capital-plan gates, certs):** standing-MC-realism
  candidate = GAP_AWARE + TP_FILL_MISS_P 0.15, default-OFF, own Stage-3 A/B before
  any default flip. Absolute MC compound/DD numbers carry ~1.5x fill-price
  generosity on TP exits plus a ~15% unmodeled TP-to-adverse conversion until then.
- **FF-3' Stage B' / liquidity program:** per-tier TP fill-reality surface now
  measured (companion to FF-2 spreads + FF-4 SL side) — the two-component penalty
  has its TP-side data.
- **December PL-5:** TP-side fill-realism input banked (this file + parquets).

## Artifacts

out/{declarations,events}_arm{30,15}.parquet · out/tp_fill_optimism_by_tier.{md,csv}
· out/knob_calibration_draft.md · out/dose_accounting.md · out/bindings_echo.json ·
out/smoke_report.md · driver/run.py (29 selftests) · queue #342 (OHLC pull), #347
(full run). Underlying pull: .cache/tp_fill_fidelity_30dte/underlying_ohlc.parquet.
