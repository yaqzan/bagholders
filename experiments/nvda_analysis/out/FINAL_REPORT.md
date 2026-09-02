# NVDA Cross-Version Scoring Analysis (2024-01-02 → 2026-06-03)

Data: 62 algorithm versions × 607 price bars + raw indicators, dumped from MySQL to
`experiments/nvda_analysis/data/`. Single-symbol study — all claims are NVDA-specific.
Raw numbers: `experiments/nvda_analysis/out/RAW_RESULTS.txt`.

---

## TL;DR

1. **No version "got NVDA right" as a call/momentum signal.** IC(overall−50, fwd-15d return) is
   **negative on all 62 versions** (mean −0.051, best −0.005, worst −0.175). The score is, if anything,
   a *weak contrarian* indicator on NVDA — high scores cluster near tops, low scores near bottoms.
2. **The reversal signal exists in the components** (stoch/rsi invert correctly at every pivot) but
   **`trend` saturates (~95-100) and dominates the weighting, compressing `overall` into a dead 40-70 band.**
   NVDA never gets a put at a top or a strong call at a bottom.
3. **`technical_alignment` is inert** (sd=3.5, range 34-64, ~50 always). That's *why your trend↓/TA↑
   thesis failed — you promoted a flat line.* The lever that carries the signal is **stoch** (sd=35, the
   only high-information component), which gets just **5% weight** in trending regime.
4. **Now (NVDA ~$216, v70=79): a WEAK/speculative continuation call, not a high-conviction buy.** The
   texture is healthier than past NVDA ≥75 readings, but the reference class is brutal: NVDA ≥75 → only
   **11% positive at 15d**. Trade small, tight invalidation.
5. **Asymmetry you must respect:** catching NVDA **upswings (buy capitulation dips) is tractable**
   (trend-break + oversold-stoch → **72% up at 15d**); catching **downswings (shorting tops) is a trap**
   (oscillators scream "overbought" for months while price grinds higher).

---

## 1. Which versions got NVDA right?

**Honest answer: none, in the call direction. The whole field is between "useless" and "mildly contrarian."**

| Cohort | IC15 | %days in 40-70 | swing-direction-right | call ≥75 fwd15+ |
|---|---:|---:|---:|---:|
| Flagship recent (v44, v57, v59, v60, v64-66, **v70**) | −0.04 to −0.07 | **91-95%** | **8%** ← worst | 0-15% (callN 2-19) |
| Mid (v27-v53) | −0.03 to −0.06 | 90-94% | 23-31% | 0-20% |
| Old, less-compressed (v5-v13) | −0.06 to −0.10 | 87-89% | 25% | 27-31% (callN 26) |
| Outliers v15 / v42 | −0.005 / −0.175 | 89-93% | 25 / 42% | tiny-N noise |

- **`swing-direction-right`** = at NVDA's 14 major pivots, did the version's score lean the right way vs
  its own trailing-60d median (lower into a top / higher into a bottom)? The **flagship versions score 8%**
  — they lean the *wrong* way 92% of the time, because trend-dominance makes them most-bullish exactly into tops.
- The newer versions are *better for the portfolio* (universe-wide WR15, DD) but **blander on a single
  mega-trender** — they've been compressed into 40-70 on NVDA (95% of days). "Got better globally, went deaf on NVDA."
- The only versions that ever leaned right at pivots a third of the time (v7/v14/v17/v23/v33 ~33%, v42 42%)
  are noisy/tiny-N — **not** a real edge, just less trend-compression.

**Verdict:** rank is a wash. If you *inverted* the NVDA score you'd have a marginal contrarian tool, but
the magnitude (|IC|≤0.17) is too small to trade outright. This is a calibration finding, not a "pick v42" finding.

---

## 2. Is now a good buy / call?

**Current v70 (2026-06-03): overall 79 · trend 96 · macd 80 · stoch 70 · bb 70 · rsi 47 · TA 53 ·
price $215.78 (+4.5% vs EMA50, +16.1% vs EMA200) · bb_position 0.38 (mid-band) · regime 59 (neutral-healthy,
mult 0.99) · volume=CONVICTION · earnings ~84d out.**

**Verdict: WEAK / speculative CALL (continuation), low conviction. Not a green light.**

Two opposing reads, both real:

- **For (texture is healthy, not a blow-off):** This is **NOT** the NVDA exhaustion-top fingerprint
  (which is trend~100 **+ stoch_comp ~0 + price riding the upper Bollinger**). Here stoch=70 (not exhausted),
  rsi=47 (neutral, not overbought), bb_position 0.38 (mid-band, *room above*), macd=80 (momentum confirming),
  price only +4.5% over EMA50 (not stretched), CONVICTION volume. It reads like a recovery-continuation off
  the pullback from the May-14 $235 peak, not a top.
- **Against (the model is structurally late on this name):** when the v70-family last put NVDA ≥75
  (19 unique days), **fwd-15d was positive only 2/19 = 11%; fwd-30d 3/19 = 16%.** Those signals clustered at
  Jun-24, Aug-24, Oct-25 — all immediately *before* drops. The one clean win was Jan-2024. The score crossing
  75 on NVDA has historically been a *late* event.

**Recommendation:** if you take it, treat it as a small, defined-risk continuation bet, not conviction.
- **Invalidation:** stoch_comp collapsing toward <15 while price stalls (the exhaustion flip), **or** a close
  below EMA50 (~$206, the +4.5% cushion). Either flips this to no-trade.
- **Structure (system's own ladder):** score ≥75 → ~14-21 DTE ATM. Keep size modest given the 11% base rate.
- Bottom line: **mild-positive, humble.** The model's own NVDA track record argues against sizing this up.

---

## 3. The derivable technical pattern (capture swings better)

The pattern is clean and it's a **trend / oscillator divergence**, visible in the components at every pivot:

| | trend_comp | stoch_comp | rsi_comp | bb_comp | overall | (what the *raw* says) |
|---|---:|---:|---:|---:|---:|---|
| **PEAK avg (N=7)** | **91** | **2** | 40 | 48 | 62 | raw stoch ~85-94 (overbought), raw rsi 58-81 |
| **TROUGH avg (N=7)** | **49** | **88** | 60 | 62 | 51 | raw stoch 2-16 (oversold), price below EMA50 |

The mean-reversion oscillators flip **2-10 bars before** the price pivot and are *correct*, but `overall`
barely moves because trend outweighs them ~6:1. Rule tests on NVDA:

| Rule (v70 components) | bars flagged | pivots caught | fwd-15d correct |
|---|---:|---:|---:|
| ✅ **BOTTOM: `trend_comp ≤ 40 AND stoch_comp ≥ 85`** | 25 | 3/7 troughs | **18/25 = 72% UP** |
| ✅ BOTTOM: `stoch_comp ≥ 90 AND rsi_comp ≥ 55 AND bb_comp ≤ 40` | 3 | 1/7 | 3/3 = 100% (tiny-N) |
| ❌ TOP: `trend_comp ≥ 90 AND stoch_comp ≤ 10 AND bb_comp ≥ 60` | 10 | 0/7 | 4/10 = 40% |
| ❌ TOP: `trend_comp ≥ 90 AND stoch_comp ≤ 15 AND rsi_comp ≤ 35` | 58 | 4/7 | 7/57 = **12%** (fires all uptrend) |

**The actionable asymmetry:**
- **Upswings ARE catchable.** "Structural trend-break (trend_comp ≤ 40) + oversold reversion (stoch_comp ≥ 85)"
  flagged the Apr-24, Aug-24, Sep-24, Feb-25, Apr-25, Mar-26 capitulations and was **72% higher 15 days later**.
  This is a real dip-buy signal that the current `overall` *ignores* (it just reads ~50 there).
- **Downswings are a trap.** Every "short the top" rule either misses the tops or fires continuously through
  the uptrend (12-40% hit). On a secular trender, "overbought" is not a sell — the oscillators are right *eventually*
  but the timing risk is unbounded. **Don't try to make the score short NVDA tops; do make it stop buying them.**

---

## 4. Are the per-technical breakdowns accurately measuring the technical reading?

**Yes — the components are faithful. The failure is weighting, not measurement.** Cross-check (v70 vs raw, at pivots):

| Component | Faithful? | Evidence | corr(comp, fwd15) |
|---|---|---|---:|
| **rsi** | ✅ FAITHFUL (mean-reversion by design) | raw RSI 81→comp 18, raw 28→comp 54. Clean monotone inverse | −0.035 |
| **stoch** | ✅ FAITHFUL + **most informative** (sd=35) | raw stoch 94→comp 1, raw 2→comp 95 | **−0.123** |
| **macd** | ✅ FAITHFUL | hist +2.8→74, −2.4→48 | −0.163 |
| **bb** | ⚠ FAITHFUL but **mixed semantics** (trend-biased) | rides upper band→bullish in uptrend, oversold→bullish too | −0.171 |
| **trend** | ⚠ FAITHFUL but **SATURATED** | 50% of days ≥90, median 89, four stacked `50+50·tanh()` blocks | −0.028 (no info) |
| **technical_alignment** | ❌ **INERT** | sd **3.5**, range [34,64], ≈50 always | −0.137 (but contributes ~0) |
| overall | — | sd 8.2, range [35,79] | −0.070 |

Two structural facts:
- **TREND saturates.** It's `position(EMA50/200) ×.30 + alignment(EMA21/50) ×.25 + slope ×.20 + macro(EMA50/200) ×.25`,
  each a `50+50·tanh(...)`. In a persistent uptrend all four pin to ~100 → trend ≈ 100 for *months*, dropping only at
  genuine structural breaks (Feb-Apr 2025 it fell to 13). High weight (~28%), near-zero marginal information per day.
- **The 40-70 compression, arithmetic** (trending weights trend .28 / bb .18 / macd .19 / rsi .16 / TA .15 / stoch .05):
  - 2024-06-18 **top**: 100·.28 + 39·.18 + 53·.19 + 18·.16 + 48·.15 + **1·.05** = **55** → landed 51. The strongest
    reversal reading in the system (stoch=1) contributed **0.05 points.**
  - Today: 96·.28 + 70·.18 + 80·.19 + 47·.16 + 53·.15 + 70·.05 ≈ 74 → +weekly/CONVICTION → 79.
  - Trend (≈95 × .28 ≈ 27pts) is a near-constant *floor under the score*; the oscillators that carry the turn are
    rationed to 5-16%. The blend can't reach put territory at a top no matter how oversold the oscillators get.

---

## 5. Why "reduce trend, promote TA" failed — and the right lever

**Why it failed:** `technical_alignment` is a *consensus* score over only `{bb, rsi, macd}`:
`50 + avg_distance × signal_strength × agreement^1.5 × turbo`. On NVDA those three **systematically disagree**
(mean-reversion rsi pulls down at tops while trend-following macd/bb pull up), so agreement≈0.67, avg_distance≈0
→ **TA pins at ~50.** Promoting TA's weight just **added a constant ≈50 (pure neutral) with sd=3.5 of noise** —
it dilutes everything toward the midpoint and *erases* dynamic range. You moved weight onto a flat line, so the
score got blander, not sharper. (You also *kept* the real problem: trend still saturates.)

**The right lever (what the data says):** the signal lives in **stoch** (sd=35, |corr| 0.123, the only component
with real dynamic range and information) and secondarily **bb/macd** (|corr| 0.16-0.17, contrarian). These are
*starved* (stoch 5%, and the contrarian ones are read trend-following via bb's bias). The fix isn't "more TA" or
even "less trend globally" — it's **let the oscillators override trend specifically when they diverge hard from it.**

### Concrete next experiment (Stage-1 framing, falsifiable)

A **trend/oscillator divergence modulator** — same gradient ethos as the shipped SCW/ICH/MCD dampeners:

- **Cohort hypothesis (pre-flight W1, z≥+3 required first):** on the call side, bars where
  `trend_comp ≥ 90 AND stoch_comp ≤ 15 AND raw_RSI ≥ 70` (momentum exhaustion masked by saturated trend) have
  materially *lower* WR15 than same-tier non-divergent bars. Mine this cohort across the **full universe** with
  the option-augmented miss-ledger (`experiments/version_alpha_mining/`), on the **option-TP barrier**, not just NVDA.
- **Mechanism (if z clears):** a smooth dampener `overall −= K · g(stoch_comp) · g(trend_comp) · (overall − target)`
  that pulls saturated-trend + oversold-oscillator calls *down* toward neutral — i.e. stop the score buying
  exhaustion. Mirror (smaller) lift on the **dip-buy** side: `trend_comp ≤ 40 AND stoch_comp ≥ 85` (your 72%-up
  cohort) nudged *up*, so the system stops scoring capitulation as neutral.
- **Why this and not "reweight trend/TA":** reweighting is a *global linear* change that the universe-wide
  W1-W6 gate has repeatedly rejected (TA inert; trend load-bearing for *supply* — see the 2026-06-02
  component-reweight NULL in memory). A **conditional gradient gated on divergence** is the gradient-over-threshold
  pattern this codebase ships; it touches only the ~exhaustion/capitulation cohorts, preserving N elsewhere.
- **Gate:** Stage-1 W1-W6 on the full universe, WR15-primary, run through `stage1_growth_gate.py` with **real
  supply** (the supply-fallback false-SHIP trap is in memory). NVDA is the *case study that exposed it*, never the production target.

---

## 6. What NOT to conclude

- **This is one stock.** NVDA is a single, extreme, secular-uptrend mega-cap. The negative IC and the 72%
  dip-buy rule are NVDA-specific; **do not ship anything off this.** Everything here is a *hypothesis generator*.
- **Don't "pick v42"** or any version off this table — the ranking is noise-dominated (|IC|≤0.17, tiny call-N).
- **Don't build a top-shorting rule for NVDA** — the data says the short side is the trap, not the opportunity.
- **The score isn't "broken."** The components faithfully measure the technicals; the engine is *correctly*
  calibrated for portfolio-wide WR15/DD (per the ship gates). It's just structurally blind to single-name
  mean-reversion turns on saturated trenders — a known, acceptable trade-off until a divergence cohort proves
  out at z≥3 across the universe.
- **Any production change** goes through the full Stage-1 gate on the universe + holdout lock, not a single-ticker
  eyeball. Promote the *mechanism* (trend/oscillator divergence), never the *case study* (NVDA).
