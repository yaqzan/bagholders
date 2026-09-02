# verify_value — forecast-verification of the v74 score (Murphy/Brier + REV + ensemble)

**Date:** 2026-06-24 (overnight, autonomous). **Active version:** v74 (`f9fb7b934`).
**Barrier:** `30dte_apex` (call win +1.092σ / stop −2.548σ, 30d), EV map win/stop/expire = **+0.30/−0.70/−0.40** (v1: no theta-on-win / dead-hold / slippage). **All in-sample** (data ends < holdout cutoff 2026-06-15). Read-only — no recalc, no MC, no version change.

Scripts: `build_data.py` (cached pulls), `phase1_brier_rev.py`, `phase2_ensemble.py`. Results JSON alongside.

---

## TL;DR / verdicts

1. **#2 calibration (served P(win) map): NO-SHIP — it's a clean no-op.** Out-of-fold reliability = **0.00027**, BSS_oof = **−0.0007**. Every score band already maps to ~73% win and that's stable, so there's nothing to fix (no miscalibration) and nothing to preserve (no resolution). Serving a probability would assign ~every signal the same ~73% — useless for sizing. The "mud" instinct lands on the right answer for the better reason: not mud, *pointless*.

2. **#3 lineage ensemble (v70/v71/v73/v74): HOLD — a stability tool, not a skill tool.** Members are genuinely diverse (corr v70↔v74 = **0.675**, v73↔v74 = 0.855), but matched-selectivity apex WR/EV is within noise (top-25%: v74 73.5%/+3.95% vs ensemble 73.4%/+3.76%). You cannot average your way to resolution that none of the members have. Build it only if version-to-version whiplash/turnover is a real UX cost; it is NOT alpha.

3. **The real finding (bigger than 1/2/3):** on the funded apex payoff, **the score's *gradient above the 70 gate* is per-trade-inert on EVERY measured axis — direction (Phase 1/2), 3-outcome EV (Phase 1A), and run-magnitude (Phase 3: MFE-σ flat at ~4.0σ across all bands, Spearman −0.011, win-conditional run ~5σ regardless of score).** Nothing (components, vol, momentum) predicts apex outcome above the gate either. The score's entire apex value is the **70-gate selection** (+~1pp EV vs random, the only t≈2 effect). Evidence-backed **parsimony principle: score-magnitude precision above the gate is worthless on the funded payoff — only gate-membership and portfolio sizing matter.** (Lone watch: 90-100, N≈180, ticks mildly up on EV/MFE/top-quartile — too thin to bank.)

4. **No new scoring version shipped.** Nothing in these results supports a ship; they support the *freeze* + a parsimony lead (below). Shipping calibration would add machinery for zero benefit.

---

## Evidence

### 0d gate (full 300k sample, in-sample)
Climatology (random call) apex EV **+2.32%**, WR 71.81%. 75+ EV +2.66% (vs-clim t=**+0.36**). Beats raw momentum on *selectivity* (75+ vs top-0.8% mom, t=**+2.84**) but **skill-beyond-momentum at matched momentum = +0.33pp, t=+0.34 (≈0)**. 80+ EV (+2.03%) sits *below* 75+ — gradient flat-to-inverted. **VERDICT: FLAG.**

### Phase 1A — Resolution map (within-70+, Murphy/Brier, calibration-free)
70+ base WR **72.79%**. Within-70+ Resolution = **0.00003**, potential-BSS = Res/Unc = **0.0002** (≈ no skill; a useful weather forecast is 0.3–0.6). Band WRs are flat across the well-powered range and slightly *worse* in the tail:

| band | N | WR(win) | EV | (WR−base) |
|---|---:|---:|---:|---:|
| 70-74 | 31,519 | 72.91% | +3.42% | +0.11 |
| 75-79 | 9,298 | 73.03% | +3.44% | +0.23 |
| 80-84 | 2,663 | 71.57% | +1.97% | −1.22 |
| 85-89 | 703 | 68.99% | −0.37% | **−3.80** |
| 90-100 | 182 | 73.63% | +3.96% | +0.83 (N=182, noisy) |

→ **Corrects an earlier guess:** 85-89 is the *weakest* band, not the "skillful slice." Sentinel's (85+) low DD is **selectivity/concentration, not per-trade signal quality.**

### Phase 1B — Calibration generalization (the #2 decider)
Time-split 70+ (train < 2021-08-20, test after). Train-fit score→P map, measured out-of-fold on test: **reliability = 0.00027**, test Brier(calibrated) 0.1998 vs Brier(climatology) 0.1997 → **BSS_oof = −0.0007**. The map generalizes perfectly *because every bin is ~73% and stays ~73%* — i.e. there is nothing to calibrate. **#2 = no-op.**

### Phase 1C — Relative Economic Value (cost-loss)
Climatology apex EV +2.30% → **−0.35% under a −0.80 stop** (a 10pp-worse fill flips random-call to −EV; the +EV is knife-edge on the stop). REV (fraction of the climatology→oracle gap captured by score≥τ) is **≈0 with no monotone structure** across τ (e.g. 75: −0.006, 78: +0.040, 82: −0.036; the −0.2 at 85/90 is N≈70-handful noise). The book's entire +EV is the climatology base rate, not the score's selection within 70+.

### Phase 2a — Lineage ensemble
Common 70+ keys (all four ≥70): 32,975. Inter-version corr(overall): v70/v71 0.822, v71/v73 0.889, v73/v74 0.855, **v70/v74 0.675**. Matched-selectivity apex (32.6k keys, base WR 73.15%): top-25% v74 73.5%/+3.95% vs ensemble 73.4%/+3.76% (overlap 78%); top-10% v74 72.3%/+2.72% vs ensemble 72.7%/+3.07% (overlap 73%). **Diversity is real but lives in "which 70-84 names" — a dimension that doesn't move apex outcome.** Ensemble = whiplash smoother.

### Phase 2b — Diverse panel (is apex-win predictable by ANYTHING, OOF?)
Logistic, 60/40 time-split, OOF BSS on apex-win within 70+:

| feature set | OOF BSS | OOF resolution |
|---|---:|---:|
| score (overall) | −0.0005 | 0.000137 |
| vol60 | +0.0005 | 0.000526 |
| components (5+ta) | **−0.0023** | 0.000147 |
| momentum | coverage-limited (pct200 nulls; covered by `verify_scorecard` §3-4) |

→ **Nothing reaches meaningful BSS.** A components logistic is *worse* OOF than the raw score → the "replace post-processing with independent scoring" path also has no per-trade-direction signal to extract.

---

### Phase 3 — Win-magnitude resolution (the caveat, tested)
The one dimension Phases 1-2 couldn't see: the σ-barrier caps a win at +30% TP, so it's blind to run SIZE, which the funded book monetizes via leverage + dead-hold. Metric: forward MFE-σ = (max high over next 20 bars / entry − 1)/vol60, within 70+ (`price_feats` has no low → MFE only).

| band | N | mean MFE-σ | median | win-cond mean |
|---|---:|---:|---:|---:|
| 70-74 | 31,376 | 4.088σ | 3.206σ | 5.004σ |
| 75-79 | 9,303 | 3.998σ | 3.106σ | 4.891σ |
| 80-84 | 2,666 | 4.092σ | 3.031σ | 5.098σ |
| 85-89 | 709 | 4.002σ | 3.187σ | 4.944σ |
| 90-100 | 181 | 4.366σ | 3.327σ | 5.224σ (N=181) |

**FLAT.** Spearman(overall, MFE-σ) = **−0.011**. P(top-quartile run) ~25% in every band (90-100 the lone 27.6% on N=181). Winners run ~5σ regardless of score. **The gradient predicts run size no better than win-rate.**

## Conclusion + the parsimony lead

The score's apex value is the **70-gate**; the **continuous gradient above 70 is per-trade-inert on direction, EV, AND magnitude.** Precise — and the honest distinction that keeps it from being "rip out the cascade":

- **Gate-acting** mechanisms (those that push a score ACROSS 70/75 — change *membership* — e.g. CWWD drifting a 73 below 70) still matter: the gate is where value lives.
- **Gradient-shaping** mechanisms (those that only re-rank/re-magnitude *within* 70+ without crossing a gate) cannot improve per-trade funded outcome — they can only matter via tier-sizing/DD (a portfolio effect, e.g. SPREAD_TILT's validated −4.1pp DD), or they're inert.
- The **portfolio cascade (20/15/8/3)** and the DD-validated levers are NOT implicated — they're sizing/DD choices that happen to key off the (skill-less) gradient. Don't cut them.

**The lead (read-only, evidence-backed "reduce variables, within reason"):** audit each shipped *score-stage* mechanism — does it cross a gate (keep) or only reshape the 70+ gradient (suspect)? For each pure-gradient-shaper, test whether it's inert on the funded book (no MC DD/return effect). Those are the lean candidates. This generalizes the v74-lean philosophy into a falsifiable rule.

## What did NOT change
Active version stays **v74**. No recalc, no ship, no config edits. (Doc deltas applied 2026-06-24: weatherization.md, known-issues.md CLOSED-NULL + WHAT-NOT-TO-DO, alpha_mining/NEW_LEADS.md.)
