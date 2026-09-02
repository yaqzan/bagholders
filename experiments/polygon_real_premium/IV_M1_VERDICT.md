# gamma+IV M1 re-read — ADJUDICATION

**Date:** 2026-07-25 · Evidence: `IV_M1_REREAD.md` (harness `iv_m1_reread.py`) ·
Adjudicated against `experiments/iv_engine_pertrade/VERDICT.md` bars, verbatim.

---

## Verdict

**The park's stated structural cause is RESOLVED, and M1 — the bar that failed twice and caused the
park — is now MET. This is a RE-OPEN, not an adoption.** Two of the three gates remain unmet or
unmeasured, and one of them regressed.

| gate | published (2026-07-13) | corrected panel | status |
|---|---|---|---|
| **M1** premium level | **FAIL — 1/3 terciles**, both rounds | **2/3 terciles + beats RV overall**, both forms | **MET** |
| **M2** P&L fidelity | SPLIT | still SPLIT; d15 ex-earnings improves, d10 weakens, **earnings window worse** | unchanged-split |
| **M3** SL-FNR | UNDERPOWERED (N=33) | **not re-runnable** without a `build_ledger.py` re-run | unmeasured |

## Harness validity — reproduced exactly before re-measuring

Every published quantity reproduces: F2 coefficients to the published 5-sf rounding (deltas 1e-6),
R1i to 1e-7, tercile cutoffs and clamps to 0, **M1 = 1/3**, overall 0.19815 vs RV 0.21531, T1_low
+0.00665, T3_high +0.00435, calm-name signed bias **+0.32826**, M2 d10 −0.0576 (t −2.36), d15
ex-earnings −0.1829 (t −3.82), N = 2,551 / 563. Two bit-exact cross-checks fell out (the re-derived
`f2_shipped` premium equals the stored `premium_f2_pct`; the re-derived f2+gamma arm reproduces the
stored `f2gamma` MAE to the last digit), proving the vega-ratio convention and P&L dispatch match
`build_ledger.py`'s original run.

## Why the result is attributable to the label, not to anything else

The **coverage control is the decisive test.** Refitting BOTH panels restricted to their 8,620
common `(symbol, date)` rows, same forms, same ledger:

| model (common rows only) | tercile tally | T1_low gap |
|---|---|---|
| `f2_old_common` | 1/3 | +0.0350 |
| `r1i_old_common` | 1/3 | +0.0091 |
| **`f2_corrected_common`** | **2/3** | **−0.1066** |
| **`r1i_corrected_common`** | **2/3** | **−0.0682** |

Only the `atm_iv` target differs. The tally still moves 1/3 → 2/3.

The **mechanism confirms** exactly where the audit placed the drift: R1i's piecewise intercept moves
in *opposite directions* at the two ends — calm `a_T1` 0.17704 → **0.09860** (−0.078), high-vol
`a_T3` 0.30649 → **0.33790** (+0.031). The calm-name residual **changes sign on every fold**
(F2 +0.0826 → −0.0964; R1i +0.1224 → −0.0179). The statistically significant calm-tercile penalty
disappears (**t +3.10 / +2.80 → +0.88 / −0.75**), and A3's calm-name signed bias goes
**+0.328 → +0.009 / −0.127**. Fit quality rises sharply (F2 R² 0.368 → 0.649; R1i 0.381 → 0.705),
and the reverse-fold degradation that drove the original form-selection argument **inverts sign**
(+11.8% → −15.9%).

The failing tercile has *moved* from calm (T1_low) to high-RV (T3_high) rather than vanishing.

## Strongest objection (and why it does not overturn the result)

**The objection:** the corrected panel's IV is systematically lower (p50 0.4637 → 0.3850, max 4.45 →
2.45) and the derived clamps moved down with it ([0.163, 1.759] → [0.1375, 1.454]). A model fit to
lower IVs prices lower. If the RV baseline over-prices relative to `lastPrice`-derived costs, then
*any* downward level shift would improve the M1 metric without the model being smarter. That would
make this a measurement artifact of the same family as the bug it is correcting.

**Why it fails:** a pure level shift cannot move the two ends in opposite directions, and it did —
calm intercept down 0.078, high-vol intercept up 0.031. Nor can a level shift raise R² from 0.368 to
0.649, invert the reverse-fold degradation, or relocate the failing tercile from calm to high-RV.
The improvement is a recalibration of the RV→IV *shape*, not a translation of its level. The
objection is answered, but it is the right objection and it is why the coverage control and the
per-tercile intercepts were required rather than the headline tally alone.

## What is NOT resolved

1. **M2's earnings-window cell got materially WORSE**: +0.5714 (t +2.15) → **+0.9568 (t +2.61)**
   at N=51. Adopting now would ship a model that is worse precisely where it was already worst.
   This is the P1.4 vega-state scope, which is **calibration-blocked** (no VIX-stress episode in the
   panel) — so it is not fixable from here.
2. **The literal un-scoped d15-overall leg and the d15 high-RV "not worse" leg are unmet by every
   model, old and corrected** — unchanged, not a regression, but still unmet.
3. **d10 overall weakens**: t −2.36 → −1.74 (still beats production, by less).
4. **M3 is unmeasured.** The first-touch walk needs the daily t=1..15 forward underlying and
   option-IV path; `ledger_v1.parquet` persists only d5/d10/d15. Re-running it costs a
   `build_ledger.py` pass (~2,551 forward `option_prices` queries, db=heavy). It was UNDERPOWERED
   at N=33 before; it is absent now.

## Disclosure (per the Review Posture)

- **N:** liquid-primary 563 (T1_low 163 / T2_mid 144 / T3_high 256); in-sample 2,551; shadow-OOS 349
  excluded per the original protocol. M2 d15 cells N=210 overall / 159 ex-earnings / **51 earnings**.
  Supplementary real-print overlap 548, of which same-contract only **211 / 177**.
- **Hypothesis count:** ONE pre-registered re-run. The bar was fixed in advance and quoted verbatim;
  the two forms (F2, R1i) were already frozen and published — **no form search was performed**, since
  the single-round refit cap is spent. No multiplicity correction is owed.
- **OOS status:** the validation ledger is out-of-sample relative to the fit *target* (panel IV) but
  overlaps it in date/symbol space, and the corrected model has **not** been tested on data unseen by
  the corrected fit. This is the same design the original used; it is not a clean OOS validation and
  must not be described as one.
- **Cost-basis caveat, unchanged:** the validation ledger's costs are yfinance `lastPrice`, which the
  original verdict itself flagged as not a real fill. The supplementary real-Polygon-print read is
  directionally supportive (`r1i_corrected` beats RV in 3/3 terciles on the same-contract stratum;
  calm gap +0.4168 → −0.0289) but is thin and carries a newly-found cohort discrepancy: the real-price
  ledger's `overall` comes from the live `scores` table and disagrees with `rs_ledger` on 354 of 548
  overlapping rows.

## Falsification condition, and whether it held

Stated before the result: *if the 1/3 → 2/3 move did not survive the common-row coverage control, or
if the calm-name residual sign did not flip, the corrupted-label story is falsified.* **Both held.**
The claim survived its own test.

## What this licenses — and what it does not

**Licensed:** re-opening `experiments/iv_engine_pertrade/` on the corrected calibration input, and
completing the gate — specifically a `build_ledger.py` re-run to make M3 measurable, and a decision
on whether the earnings-window regression is disqualifying on its own.

**NOT licensed:** flipping `GAMMA_AWARE` / `IV_MODEL` on. Per the gameplan's own doctrine,
engine-fidelity adoptions are portfolio-stage measurement changes requiring a calendar-hold-style
re-baseline and an explicit *"does any gate DECISION flip?"* audit — and two of three gates are still
unmet or unmeasured. `GAMMA_AWARE` must never be flipped alone in any case (standing anti-goal;
gamma-alone is a +1754% explosion).

**Standing correction to the record:** the 2026-07-13 conclusion *"no functional form fixes a
calibration-target gap"* was exactly right. What it could not know is that the calibration target was
itself corrupted. The park was correct on the evidence available; the evidence has changed.
