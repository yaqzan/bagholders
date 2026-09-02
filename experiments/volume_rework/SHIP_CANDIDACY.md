# Volume-Rework Ship-Candidacy — v70 Daily Volume Amplifier (CALL signals)

Source: multi-agent candidacy workflow (`wf_candidacy_v2.js`, run wf_00591357-911) reasoning over the
pre-computed v70 volume-edge surface (`characterize.py` → `characterization.json`), + the net-of-MCD
pre-check (`netmcd_check.py`). Read-only, holdout-locked (≤2026-05-15). 2026-06-03.

## Headline

**SIMPLIFICATION mandate, not an alpha opportunity.** No volume transform clears option-barrier
z≥+3 net of MCD/SCW. On the funded/option barrier the daily amplifier is mis-calibrated:
CONVICTION amplification is flat at every tier (z_opt +0.14..+0.67), `vol_mag` is anti-predictive
(q1 50.4 > q4 43.7), and the −30% THIN_AIR call suppression is empirically backwards. But volume is
load-bearing on **generic** WR30 (ablation: −5..−23pp if deleted) ⇒ **REBUILD/wholesale-delete is
forbidden**; the target is removing the option-mis-calibrated *tilt/cliff*, validated on BOTH barriers.

## Direction: SIMPLIFY (not REBUILD, not a new alpha layer)

- No option-barrier cohort at z≥+3 to build a new layer around (overbought hole is generic-only
  z_opt −1.27; the one big option-z cell — THIN_AIR 80-84 z_opt +2.62 — is N=39 noise).
- The mag inversion is the one real, net-new mis-calibration: **survives net of MCD+SCW+cont at
  z=+2.31** (q1 50.5 vs q4 42.4, N=1597) — real but sub-+3 ⇒ simplification, not alpha.

## Ranked candidates

| id | class | verdict | option-z | one-liner |
|---|---|---|---|---|
| `stab-confidence-gate-type` | stability (NO bump) | **SHIP** | N/A by construction | intraday-confidence gates the classification TYPE until volume confirmed → kills the ATI ~18pt TYPE-flip fakeout; zero EOD/backtest diff (validated by a no-op 1d assess). |
| `flatten-mag-scaling` | simplification (bump) | **SHIP-as-simplify (gated)** | +2.31 net (q1 vs q4) | drop the anti-predictive CONVICTION/THIN_AIR mag tilt (subsumes THIN_AIR-on-calls). Floor = flat option WR + simpler code; gate = generic-WR30 non-regression. |
| `collapse-wvforce1-doublecount` | layer-collapse (bump) | SHIP-as-simplify (last) | non-regression only | fold WVD-Wave (v46) + DVAW (v59) into one wv_force1 curve (both currently key it = double-count). Maintainability, not return. |
| `stab-cliff-smooth-continuous-conviction` | simplification | DROP (defer) | <+3 | dominated by flatten-mag-scaling; adds decay-echo + N-shift risk. |
| `vol-conv-overext-fade` | alpha (attempted) | DROP | ~−2, not +3 | VSG-FLAG pattern (generic-only overbought axis); re-parameterization of shipped DVAW. |

## Recommended next action (in flight)

**`flatten-mag-scaling` → parameter sweep, queue-governed.** ReSim fast-path: precompute captures
`compute_overall_score` args once (incl. `vol_mult`/`vol_sig`/`vol_mag`), then each variant overrides
`vol_mult` (flatten the mag tilt / THIN_AIR-off / cap high-mag) and re-scores — measuring opt15 AND
gen15 (generic WR30 non-regression) per tier + supply. NO scoring edit needed for the screen (captured-arg
override). Heavy precompute ⇒ submitted via `trader queue` (never raw).

`stab-confidence-gate-type` is the #1 overall (no sweep, no version bump) — a separate parallel track:
implement the confidence-gates-TYPE change in a worktree, validate by a zero-diff 1d assess. (Next after the sweep launches.)

## EMPIRICAL SWEEP RESULT (task #28, 5y ReSim re-score, 2026-06-03) — `flatten-mag-scaling` is NULL on the version-bump track

8 `vol_mult`-override variants re-scored over 5y (59,217 cached inputs, 2,186 baseline 75+). Baseline
funded WR: apex75 70% / opt75 50% / gen75 70%. **Every flatten variant leaves the funded barriers
(Apex + opt) invariant within ±1pp** — the CONVICTION mag-scaling carries **no funded edge** at the
75+ aggregate. Its only real lever is **supply**:

| variant | dApex75 | dOpt75 | dGen75 | dSupply% | read |
|---|---|---|---|---|---|
| conv_noamp | +0 | −0 | +2pp | **−39.8%** | remove amp → lose 40% of 75+ book at flat WR (hydration-negative) |
| conv_flat10 | −1pp | −1pp | −1pp | +13.7% | slightly negative |
| conv_flat20 | −1pp | +1pp | −1pp | **+92.9%** | floods book at flat-ish WR (growth-gate saturates → no credit) |
| conv_cap_q3 | +0 | −0 | +1pp | −21.1% | generic-only +1pp at −21% supply = SVD/VSG trap |
| thinair_off | +0 | +0 | +0 | +1.5% | **pure no-op** — backwards THIN_AIR-on-calls suppression is immaterial |
| capq3_thinoff | +0 | −0 | +1pp | −19.6% | same as cap_q3 |

**Conclusion: NO version-bump ship candidate from the daily amplifier.** The mag-scaling is
funded-WR-neutral; flattening only moves N at constant quality, so there is no
"flat-or-up funded WR AND supply-preserving" variant. The q1-vs-q4 mag inversion (z+2.31 in the
ledger) does not translate to a 75+ aggregate funded-WR gain when flattened, because flattening
re-shuffles supply rather than upgrading quality (the inversion is generic-leaning, same family as
the overbought hole). Don't force a marginal/FLAG ship.

**Remaining shippable item = the no-bump STABILITY track** (`stab-confidence-gate-type`, the ATI
intraday TYPE-flip fakeout fix — the original Turn-1 ask). `collapse-wvforce1-doublecount` stays a
maintainability-only option (non-return; not pursued unless we want the layer cleanup).

## NOT yet done (serial/queued follow-ups)

1. Heavy flatten sweep (queued) — q4-net-of-MCD already confirmed (z=+2.31); sweep now measures the
   re-scored both-barrier deltas + supply.
2. Stage-1 W2–W6 as **WR-non-regression** (simplification, not alpha gates).
3. W5 growth gate with REAL candidate supply (`signal_supply.py` first — the FALSE-SHIP fallback trap).
4. Generic WR30 non-regression (the load-bearing gate for a simplification — distinct from option-TP).
5. If clean: version bump + recalc (1d→force→full off-hours) + assess + research-pack — all queued deploy steps.
