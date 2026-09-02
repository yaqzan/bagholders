# Score-Fidelity Audit — does each component score faithfully READ its indicator?

**/research 2026-06-24 (autonomous, pre-open).** Active v74 (`f9fb7b934`). The *analysis-quality*
complement to verify_value's *forecast-skill* work: not "does the score predict?" but "is the score a
faithful, low-artifact reading of the technical state?" Read-only.

**The funded bar (from verify_value):** a fidelity fix is funded-relevant only if it changes the **75+
gate membership** for the better, or removes a **fakeout/instability** source at the gate (a v72-class
stability win). Within-75+ re-ranking is inert; recalibration-for-prediction is a documented null
(reweight-trap). So this audit hunts **measurement errors at the gate** (discontinuities / saturation /
inversion), not alpha.

---

## Method

Joined stored v74 component scores (`scores.trend/bb/rsi/macd/stoch/technical_alignment`) to the raw
`indicators` table (rsi/stoch/macd_hist/bands) on (symbol,date), stride-3 sample, 316,886 rows. Per
component: monotonicity (Spearman vs raw), saturation (% pinned ≤2/≥98), discontinuity (max mean-jump
between adjacent raw bins), and context-dependence (within-raw-bin std), with a gate-neighborhood
(overall 68-77) focus. Then read each component-score FUNCTION in `core.py` to find the mechanism.

## Per-component catalog

| component | mapping | fidelity read |
|---|---|---|
| **rsi** | mean-reversion-lensed (high RSI → low score), + price push-bands | clean monotonic (−0.61); mild cliff at RSI≈75; OK |
| **macd** | 75% momentum-DYNAMICS (velocity 35% + **4-branch piecewise PHASE 40%**) + 25% contrarian level | **⚠ DISCONTINUITY** — see below. Worst gate-wobble (gateCtxStd 13.4). The −0.10 corr-with-histogram is BY DESIGN (it reads dynamics, not level). |
| **stoch** | `50+50·tanh((50−stoch)·0.04)` + small momentum branches | clean mean-reversion (−0.98); **20% saturation** but SMOOTH (tanh tail, not a cliff) — info loss at extremes, low priority |
| **bb** | trend-aware tanh base 70% + slide 20% + squeeze 10% | mostly tanh-smooth; minor V-recovery/squeeze gates; audit underpowered (sparse stored bb_position) |
| **trend** | 4 smooth tanh blends (position/alignment/momentum/macro) | **no discontinuity** (the "32% saturation" was tanh tails on sparse pct_ema50 data); OK |

**Headline: the only real fidelity bug is the MACD phase discontinuity.** Every other component is
tanh-smooth (saturation is gradual, not a cliff).

## The MACD phase discontinuity (the fix)

`calculate_macd_score` momentum-PHASE term (40% of the MACD score) is a 4-branch piecewise function of
`(vel_norm_phase v, accel_norm_phase a)`:
- peaking (v>0,a<0): `0.5+0.5v+0.3|a|` · building (v>0,a≥0): `0.3+0.4v+0.2a` · bottoming (v≤0,a>0):
  `−0.3+0.4|a|+0.2|v|` · declining (else): `−0.5−0.3|v|−0.2|a|`.

At **v=0** (MACD-histogram momentum flat) the branch flips bottoming(−0.3)→building(+0.3) = a **0.6 jump
in phase_raw from infinitesimal noise**. Function-level (at a=+0.5): base phase_score steps **+22.6 points
(≈ +9 macd-points) across v=±0.01** — a WCF-27/28-class cliff, exactly where the audit's gate-wobble
concentrated. (A smaller ~9-phase-pt jump exists at a=0.)

**Fix (faithful, surgical):** replace the hard branches with a CONTINUOUS soft-gate blend of the SAME four
formulas — `gv=σ(v/W)`, `ga=σ(a/W)`, `phase_raw = gv·ga·building + gv·(1−ga)·peaking + (1−gv)·ga·bottoming
+ (1−gv)·(1−ga)·declining`. As W→0 it reproduces the branches **exactly** (verified: matches base to the
decimal at branch centers); at W=0.15 it cuts the v=0 jump from **+22.6 → +1.7 phase-points (93% removed)**
while staying within ~0.1-0.4pt of base away from the boundary. No new signal — it removes a measurement
artifact. Same class as v72's WCF ramp.

## A/B (base v74 vs smooth-phase, ScoreSimulator stride-3, 10y) — NET-DILUTIVE NULL, NO SHIP

Three smoothing widths, same pattern:

| W | 75+ supply Δ | DROPPED-rows apex WR | stable apex WR | gate-crossing Δ | ≥10 macd-jump Δ |
|---|---|---|---|---|---|
| 0.05 | −48 (−1.1%) | **80.2%** (N=91) | 73.4% | −1.9% | +1.3% (worse) |
| 0.08 | −85 (−1.9%) | **80.5%** (N=149) | 73.3% | −2.8% | +2.1% (worse) |
| 0.15 | −152 (−3.4%) | 75.2% (N=266) | 73.5% | −4.7% | +3.5% (worse) |

**Two decisive observations:**
1. **Smoothing is NET-DILUTIVE.** At every W it REMOVES above-average-WR rows from the 75+ set
   (DROPPED-rows apex WR 75-80% > stable 73%) and shrinks supply (−1 to −3.4%). The "discontinuity" at
   velocity=0 partly encodes a **real momentum-transition signal** — the `vel>0` peaking/building branch
   is the "front-run the histogram peak" intent, and the boundary rows it scores high are GOOD trades
   (80% WR). Smoothing blends them toward neutral → demotes good signal.
2. **The stability gain is marginal** (gate-crossings −2 to −5%; ≥10 macd day-over-day jumps actually
   slightly WORSE) because the MACD component is **inherently volatile** (base mean |Δmacd| 8.16/day; it
   reads velocity/acceleration). The phase cliff is a small contributor to that natural jumpiness.

**The decisive test = the apex WR of the DROPPED rows** (the ones smoothing removes from 75+). >stable ⇒
the smoothing removes good signal ⇒ net-dilutive. This is the generalizable methodology for any
"smooth a component cliff" idea.

## Verdict: NO SHIP — and WHY it's the right answer

The score-fidelity audit is comprehensive (all 6 component functions read; the one real discontinuity
tested empirically across 3 W). It yields **no meaningful scoring ship**, for a structural reason:

- The one real cliff (MACD phase) is **net-dilutive to smooth** — it's a load-bearing momentum-transition
  signal, not a measurement artifact. This reconfirms, in the fidelity domain, the **v42 push-band lesson**
  (smoothing the rsi breakout/divergence pushes lost top-tier alpha) AND the **verify_value gradient-inert
  finding**. The codebase already embodies the distinction: it smoothed the rsi *range gates* ("taper
  instead of hard cutoff") but kept the rsi *pushes* (breakout signals); the MACD phase branches are
  push-like (signal), so leave them.
- The other components are faithfully **tanh-smooth** (stoch/trend saturation at the extremes is *correct
  representation* of deeply over/oversold states, not a bug) or **designed discrete signals** (rsi
  breakout/divergence pushes — v42-load-bearing).
- There is no fidelity fix that improves **gate selection**, because (verify_value) nothing — score
  gradient, components, vol, momentum — predicts the apex outcome above the gate. You cannot improve
  selection on an unpredictable target; the only available fidelity wins are stability, and the one
  stability candidate removes signal.

**Conclusion (mature frontier, from the measurement-quality angle):** the scoring layer is at its funded
optimum *given the inputs*. The fidelity audit complements verify_value (forecast-skill) — both converge:
no meaningful scoring ship is available. The next genuine ship is a **new INPUT** (option/IV data, NEW_LEADS
N3), not a scoring refinement. Do-not-retry: smoothing the MACD phase branches (net-dilutive; signal,
not artifact).
