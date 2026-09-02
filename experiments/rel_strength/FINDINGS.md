# Relative Strength vs SPY — NULL (2026-06-03)

**Status: redundant with own momentum, regime-flipping → NULL. No production change.** Holdout-locked
(≤2026-05-15), universe (N=394,293 60-99 calls, rs20 100% coverage). Artifact: `rs_w1_results.txt`.

## Hypothesis
The score is single-stock-ABSOLUTE (`trend` = price vs own EMAs); it has no relative-strength feature.
RS vs SPY (stock_ret − spy_ret, 20d/60d) is orthogonal momentum. Does RS add OPTION-barrier info beyond
`overall`, and beyond the stock's OWN momentum (the relative part)?

## Result: NO.
**A. Within the call pool, RS quintiles are FLAT.** 75+ rs20 top-vs-bottom opt15 z=−0.90 / apex15 z=−0.09;
75+ rs60 z=+0.44 / +0.27; 70-74 z=−1.09; 80+ z=−1.46. No monotone gradient, nothing near ±3 anywhere.

**B. Controlling for own 20d momentum, RS adds nothing (slightly negative).** Within fixed stock_r20
quartiles, hi-RS vs lo-RS opt15 z = −2.95 / −0.18 / −1.31 / +0.79 — no consistent positive; the one
significant cell is NEGATIVE (high RS worse). So the *purely relative* part carries no option signal;
RS's apparent content is just own-momentum, which the score's `trend` already encodes.

**C. Direction is weak + REGIME-FLIPPING.** Laggards (rs20<−3%) opt15 49.7% vs leaders (>+3%) 47.4% — a
mild laggard/mean-reversion tilt, sub-significant. By year: 2022 hi-RS WORSE (z=−2.69, leaders crushed in
the bear), 2024 hi-RS BETTER (z=+2.32, leaders continue in the bull). Opposite signs by regime → fails
multi-window consistency; it's beta to the momentum factor's cyclicality, not a stable discriminator.

## The bigger pattern (across 4 investigations this session)
opt15 WR15 is **~45-50% for essentially EVERY price-technical partition** — the absolute tiers (75+ 47.5%,
70-74 46.8%, 80-84 45.0%, 85+ 48.0%), the divergence cohorts (44-48%), the normalized signals (44-46%),
and now RS quintiles (45-50%). apex15 is similarly flat at ~67-71%. **The directional signal explains very
little of the 15d option-TP-before-SL outcome** — at 15d that outcome is dominated by realized vol PATH
(TP/SL geometry × vol), not signal direction. This is consistent with the v69-honest finding that
stock-selection alpha is statistically thin (market-adj +1.03%/15d, t=1.25).

## Implication for the alpha hunt
More directional PRICE signals won't move the option barrier — they all land at ~47%. The genuinely
different doors are: (1) options-IMPLIED signals (IV / skew / IV-rank) that predict the VOL PATH the
barrier actually depends on — orthogonal to price, but `option_prices` coverage is Feb-2025+ (~1.3y, low
W1 power); (2) the portfolio/execution layer (where Apex's HOLD/exposure/dead-hold work gave real MC
gains); (3) the weekly look-ahead CORRECTNESS fix (honesty, not alpha). Re-grading `overall` or adding
another price-momentum feature is closed.

## Artifacts
`build_rs.py` (RS feature on the 60-99 ledger, no re-walk) · `rs_w1.py` + `rs_w1_results.txt`.
Ledger: `.cache/rel_strength/rs_ledger.parquet`.
