# Regime-conditioned dynamic component weighting — NULL at the funded gate

**/research 2026-06-25 (pre-open).** Active v74. Read-only, ~10 min, no rescore/sweep/recalc. Hypothesis
(user): combine the 6 component scores with weights that vary by regime/market-structure to improve the
funded book — e.g. de-weight TREND in bear/chop (the weather_components diagnostic: TREND is the dominant
score-driver but regime-HARMFUL in bear/chop) and up-weight RSI/MACD.

## Triage (G17) — heavily documented ground
Component reweighting is a documented null on the apex predictand: the reweight-trap (G35; the 2026-06-17
TA-suppressor probe re-confirmed zeroing a component cuts supply + drops higher-EV names), regime-direction
flips (v22/v23/v24 reverts; regime_ab_test F/G/H/I all lost), and the G26 reversal-trap (the
trend-extended-in-bear/chop cohort is not cleanly regime-separable). verify_value (this session): nothing
(components/vol/momentum) predicts apex above the 75 gate (components logistic BSS −0.0023), so a reweight
can ONLY help by changing 75+ GATE membership for the better. The score ALREADY has dynamic weighting (the
`d` sideways↔trending blend, V6: w = BASE + SLOPE·d).

## The decisive cheap pre-test (no rescore)
Within the 75+ cohort (apex-matched, N=12,851; base EV +2.90%), split by each component's tercile × regime,
and report the **hi-tercile-minus-regime-base apex EV** — the spread a reweight would exploit. A real lead
needs a LARGE spread that is **CONSISTENT across the regime classifiers a mechanism can use** (regime_composite
AND vix), not a single-window or single-classifier flicker.

### Result — NULL
**1. TREND (the motivating hypothesis) is the flattest of all** — ±0.8pp across every regime. The
weather_components 2022/2023 component-EV harm does NOT survive to the funded gate via a usable classifier:
by regime_composite high-TREND is *fine-or-better* in STRESS/NEUT (+0.65/+0.79) and only mildly worse in BULL
(−0.80) — *backwards*; by VIX it's uniformly −0.5 (no regime-specificity). The "trend harmful in bear/chop"
signal appears ONLY in the **2023 year-label** (−5.2pp, N=139) — 2022 (the real bear) is just −0.7pp, 2024/2025
flat-to-positive. A single-window artifact, not a regime-separable signal a mechanism can target.

**2. No component has a large, cross-classifier-consistent spread.** The hi-base EV matrix (pp):

| comp | STRESS<45 | NEUT45-60 | BULL>=60 | VIX>=22 | VIX<16 |
|---|---:|---:|---:|---:|---:|
| trend | +0.65 | +0.79 | −0.80 | −0.49 | −0.50 |
| bb | +3.44 | +1.84 | −3.52 | **−5.14** | **+5.74** |
| rsi | +4.12 | −1.77 | +1.33 | +2.78 | −0.91 |
| macd | −0.83 | +2.03 | −0.13 | +0.50 | −0.79 |
| stoch | −0.42 | +0.35 | +2.09 | +3.71 | −0.54 |
| ta | +2.98 | +0.22 | −0.70 | −0.88 | +0.86 |

**bb FLIPS SIGN across classifiers that should agree** — STRESS<45 +3.44 vs VIX≥22 **−5.14** (both "stress");
BULL −3.52 vs VIX<16 **+5.74** (both "calm"). That contradiction IS the noise floor (~±5pp from 30 cells). The
one semi-consistent cell (high-rsi in stress, +4.12/+2.78 agreeing) sits WITHIN that noise floor, is the
admission side (the reweight-trap — up-weighting adds names whose EV regresses to base), and reverses in
NEUT/calm.

## Verdict: NO SHIP
The dynamic/regime-conditioned component-reweighting space is **null at the funded gate** — confirmed
comprehensively (6 components × 5 regime classifiers). No component cleanly separates apex outcomes at the
gate in a way a real-time regime classifier can exploit; the few large cells are single-classifier or
sign-contradictory across composite-vs-VIX (noise). This is verify_value's gradient-inert finding + the
reweight-trap + the G26 reversal-trap, now confirmed per-component-per-regime. The score already carries the
only robust dynamic weighting (the `d` blend); further regime-conditioning of the component weights has no
separable apex signal. Killed by two read-only pre-tests, no rescore needed.

**Generalizable test:** for ANY regime-conditioned reweight/scoring idea, the cheap decisive pre-test is the
component-tercile-vs-regime-base apex EV at the GATE, checked for sign-consistency across the classifiers a
mechanism can actually use (composite AND VIX). A spread that flips sign across composite-vs-VIX within the
same regime (bb here) is classifier-dependent noise, not a regime signal — kills the idea in ~10 min without
a rescore/sweep.
