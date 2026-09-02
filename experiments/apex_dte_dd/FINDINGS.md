# Apex sprint DD/DTE optimization — FINDINGS

**Run:** overnight 2026-06-30 (autonomous /research). Portfolio-stage only — NO ALGORITHM_VERSION
bump, NO scoring change, NO recalc. Active scoring stays v74 (`f9fb7b934`).

**User context:** rebuilding from a drawdown (~$22k from an ~$80k peak); wants aggression +
compounding + DD reduction. **Relaxed constraint this run: collapse-rate ≤ 10% is acceptable**
(prior hard floor was collapse=0).

**Objective:** Pareto-optimize the fast-2x Apex sprint for MAX COMPOUNDING + MIN DRAWDOWN under
collapse ≤ 10%. Metric (concentration_2x harness, pooled over ~57 monthly-rolled 2y start windows
incl. 2020-COVID and 2022-bear starts): **P(2x within 2y) / median calendar-days-to-2x / median
compound / worst DD / collapse-rate**.

**Vehicle:** `experiments/concentration_2x/sweep.py` (faithful sprint MC — 4×25% flat sizing, ~100%
gross, all 5 DD levers + dead-hold ON, calls-only v74, bit-exact, checkpoint/resume). DTE set via
`NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS` env (premium + sigma barriers + theta auto-scale by √(DTE/30)).
SPREAD_TILT-on in the working tree (relative frontier unbiased; absolute numbers slightly affected).

> Pre-run recon (read-only, same session): the user's **VIX-trend / BB-middle-magnetism**
> hypothesis tested NULL on multi-year SPY data (rising-VIX-mid and "orderly rise" are the *better*
> call states; reproduces the documented `VXMD` null). The validated VIX→DD signal is the **level**
> band (VIX 20-28 = worst call downside, 24% loss-freq / −3.9%), i.e. the shipped **RXDD** lever —
> hence arm 2 re-tunes the level lever, not a trend lever.

---

## Result 1 (DECISIVE) — 30-DTE dominates 15-DTE for the sprint

Coarse drill, N=100 × ~57 monthly windows (step-2), $50k start, collapse gate ≤10%.

**At n4_a25 (the user's current concentration):**

| config | P(2x) | days-to-2x | median compound | worst DD | collapse |
|---|---:|---:|---:|---:|---:|
| **15-DTE apex SL−.865 (LIVE)** | 57.1% | 77d | **+4%** | 88.4% | 1.3% |
| 15-DTE SL−.70 (pure-DTE control) | 60.9% | 83d | +9% | 91.2% | 2.0% |
| **30-DTE SL−.70** | **71.8%** | 109d | **+50%** | **82.4%** | **0.0%** |

**30-DTE wins on P(2x), compound, DD, AND collapse — at every concentration.** It only loses raw
speed (~1 month slower to 2x). The 15-DTE-SL70 control isolates the effect to **DTE, not the stop**:
15-DTE is intrinsically worse because its smaller premium (1.29σ vs 1.82σ) means gap-downs wipe a
larger premium fraction → deeper DD, more collapse, lower compound (the documented 2026-05-01
"30-DTE beats 15-DTE on every window" law, now confirmed on the sprint config). **The user's live
sprint is on the wrong DTE.**

## Result 2 — concentration frontier (within 30-DTE), and the collapse-relaxation is a non-event

| cell | P(2x) | days | median compound | worst DD | collapse |
|---|---:|---:|---:|---:|---:|
| flat_n2_a50 | 58.9% | 57d | **−6%** | 91.4% | 2.4% |
| flat_n3_a33 | 67.0% | 97d | +36% | 85.6% | 0.2% |
| **flat_n4_a25** | 71.8% | 109d | +50% | 82.4% | 0.0% |
| **flat_n5_a20** | 73.4% | 116d | +50% | 80.7% | 0.0% |
| **flat_n10_a10** | 80.4% | 186d | **+114%** | **76.2%** | 0.0% |
| cascade_ref (14, Core) | 70.4% | 258d | +98% | 71.0% | 0.0% |

- **Relaxing collapse to ≤10% did NOT unlock the aggressive end.** Even *allowed*, n2_a50 is a trap
  (−6% median compound, 91% DD, 2.4% collapse) and n3 is dominated. The optimum stays in the
  **collapse-FREE** n4–n10 band — concentrating *further* than 4 names buys nothing but DD + ruin risk.
- The real DD-reduction lever inside 30-DTE is **MORE names**: n4→n10 cuts DD 82→76% AND raises
  compound +50→+114% — the cost is speed (109→186 days median-to-2x). n5 is ~free vs n4 (−1.7pp DD).

## Result 3 — VIX-level / DD-aware lever re-tune (arm 2): NULL for a Pareto DD cut

30-DTE n4_a25, N=100 × ~57 windows. Baseline = shipped levers (+50% / 82.4% DD / 0% coll).

| variant | P(2x) | days | median compound | worst DD | collapse |
|---|---:|---:|---:|---:|---:|
| BASELINE (shipped) | 71.8% | 109d | +50% | 82.4% | 0.0% |
| RXDD depth 0.65 (stronger VIX) | 71.6% | 110d | +51% | **82.4%** | 0.0% |
| DD-soft floor 0.25 | 63.9% | 91d | +21% | 77.8% | 0.0% |
| DD-soft LO.30/HI.65/floor.15 (deep) | 65.6% | 96d | +25% | 76.3% | 0.0% |
| RXDD.65 + DD-soft floor .20 | 60.4% | 87d | +15% | 76.7% | 0.0% |

- **Stronger RXDD (VIX-level) is a NO-OP** — DD 82.4→82.4 *identical*. When the sprint is fully
  deployed across 4 names, the VIX 20-28 band is too small a slice of the tape for its depth to
  matter; the level lever is already saturated. (Consistent with the recon: VIX-level is the right
  axis, but the sprint's DD isn't *dominated* by the VIX-20-28 cohort.)
- **DD-soft-band (running-DD contraction) cuts DD only by gutting compound** — a ~1:1 trade (floor
  0.25 → DD −4.6pp but compound +50%→+21%; deep → DD −6pp but +50%→+25%). The capital-velocity law
  (G16): contracting alloc *during* a drawdown slows the recovery that **is** the compound. Not a
  Pareto win; the user wants both, the lever delivers one-for-the-other.
- **Conclusion: no lever re-tune cuts the sprint's DD without sacrificing compound.** The shipped
  5-lever stack is already near-optimal for the sprint. The DD↔compound dial is the **number of
  names**, not a knob (Result 4).

## Result 4 — N=300 full-roll confirm (robustness): the frontier holds

30-DTE, N=300 × full monthly roll (step-1, ~113 windows). Reproduces the coarse ranking exactly.

| cell | P(2x) | days | median compound | worst DD | collapse |
|---|---:|---:|---:|---:|---:|
| **flat_n4_a25** | 71.3% | 113d | +50% | 82.4% | 0.0% |
| **flat_n5_a20** | 71.7% | 119d | +49% | 81.1% | 0.0% |
| **flat_n10_a10** | 79.2% | 191d | **+108%** | **76.2%** | 0.0% |

n10 **Pareto-beats** n4 (MORE compound +108% vs +50% AND LESS DD 76% vs 82%), at the cost of speed
(191 vs 113 median days-to-2x). n5 is a ~free −1.3pp DD vs n4.

**N=500 ship-gate confirm (full monthly roll, 56,500 paths/cell) reproduces it exactly** — n4
+50% / 82.9% DD / 0% coll / 113d; n10 +108% / 76.2% DD / 0% coll / 190d. The ranking is stable
across N=100 / 300 / 500 → high confidence.

---

## Recommendation (final)

1. **Switch the sprint DTE from 15 → 30.** This is the headline and a *strict* upgrade for the
   user's goals — at 4×25%: median compound **+4% → +50%**, worst DD **88% → 82%**, collapse
   **1.3% → 0%**, P(2x) **57% → 72%** — losing only ~1 month of speed (77→113 median days). Driven
   by DTE (premium cushion), not the stop (pure-DTE control confirms). The live 15-DTE sprint is
   the wrong DTE. *Forward-looking: existing 15-DTE positions ride out; new entries become 30-DTE.*
2. **The DD dial is the number of names, not a lever.** Pick the point on the 30-DTE frontier:
   - **n4_a25** (keep your concentration): +50% / **82% DD** / 0% coll / ~113d — fastest good option.
   - **n5_a20**: ~identical, −1.3pp DD (81%), ~6d slower — a free hair of DD.
   - **n10_a10** (the Pareto-best for "compound + DD reduction"): **+108% compound AND 76% DD**, 0%
     collapse — beats n4 on BOTH, costs ~2.6 months more patience (191d).
3. **No further DD lever helps** (arm 2 NULL): stronger RXDD = no-op; DD-soft = compound-for-DD swap.
   Leave the shipped 5-lever stack as-is.
4. **The collapse-≤10% relaxation changed nothing** — the aggressive n2/n3 are traps (negative/low
   compound, 90%+ DD) even when *allowed*; the optimum stays in the collapse-FREE n4-n10 band. More
   aggression than 4 names is counterproductive.

**Single recommended config:** **30-DTE, keep 4×25% (n4)** as the drop-in upgrade (same name-count,
just the better DTE) — or **30-DTE n10_a10** if you'll trade ~10 weeks of speed for 2× the median
compound *and* lower DD. Both crush the live 15-DTE sprint. Staged (not auto-applied) — see
`SHIP_HANDOFF.md`.

> Future lead (not tested, out of the 15-vs-30 scope): the 30>>15 premium-cushion trend suggests
> 45-DTE *might* cushion gaps even more — but theta drag + slower velocity likely peak it near 30
> (the documented "30-DTE is the definitive primary instrument"). A 45/60-DTE arm is a cheap
> follow-up if the user wants to push the DTE axis further.
