# FINDINGS — CT-successor bounce formula

**STATUS: COMPLETE 2026-08. VERDICT (locked bar, verbatim): "raw score ordering stands;
no formula earned." 0/4 model rankings beat the raw-`overall` ordering on the validation
window — the ct15 paper sleeve keeps its MECHANICAL entry criteria; no model upgrade.**
Prereg `df5ccf6d`; driver `driver/bounce_formula.py`; diagnostics/coefficients/
calibration in `out/`.

## Why the null is trustworthy (and expected in hindsight)

- Cohort: N=153 total — and `ct_flag OR trend<=20` is a PROVABLE TAUTOLOGY on this
  ledger (ct_tag requires overall>=70 AND trend<=20; every ledger row is overall>=70),
  so the cohort is exactly the CT population. Train 133 / purge 1 / validation 19
  (top-quintile = 4 rows; L3-covered validation = 3 rows — bar 2 barely testable).
- Overfit receipts: GBM classifier train AUC 1.00 → val 0.65 (below majority baseline
  on accuracy); GBM regressor R² 0.72 train → **−1.66 OOS**. The model-class caps held;
  N=133 is the binding constraint, exactly as the prereg's small-N discipline predicted.
- All four models' validation top-quintile UNDERPERFORMED the raw-overall ordering
  (−0.6 to −21.6pp vs the +3.0pp bar).
- Disclosed substitution: no raw-price pullback-depth column exists in the locked
  ledger; proxied with trend/bb/stoch (reconstruction needed MySQL, out of scope).

## Disposition

- The designed sleeve (ct15 tape ARM B) keeps mechanical criteria. No amendment drafted.
- Revisit trigger: the 2027-06-15 CT checkpoint, when the paper tape + one more year of
  signals roughly double the cohort — and only via a fresh prereg with the same bars.
- Standing lesson: at ~30 signals/yr, model-fitting the CT cohort is structurally
  premature; the honest upgrades in the meantime are mechanical (liquidity screen,
  capacity clip — already in ARM B).
