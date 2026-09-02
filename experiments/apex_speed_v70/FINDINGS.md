# Apex 50k→200k speed re-tune — FINDINGS (v70 Apex, RXDD-on baseline)

Mission (`/research`, 2026-06-04 night): the user wants the **fastest reliable
path to turn $50k → $200k (4×)** with the early-stage book, treating DD as a
*budget* (collapse=0 still the hard floor). They asked to test running **100%
exposure** / dynamic-by-regime / size-conditional sizing, mine sector-ETF +
signal-density + call/put-ratio clues, and removed the holdout cutoff.

## The law (why aggression does NOT speed 50k→200k)
The v70 Apex is a **leveraged-momentum compounding sleeve**. Compounding is
path-dependent: a 50% drawdown needs a +100% gain just to recover (wasted time).
Mining the 6.4M-trade MC tape:
- **EV by entry running-DD**: dd[10,35) EV +0.071/+0.073 >> dd[35,55) EV +0.041
  (loser z+19.6). Deep-DD entries are the worst cohort.
- **Explosion analysis** (top-decile vs rest, every window): the explosive runs
  are NOT distinguished by entry VIX/breadth/tier — only by **lower mean
  entry_dd** (e.g. 2021 0.12 vs 0.21; 2023 0.315 vs 0.449; dip 0.066 vs 0.102).
=> For this sleeve, **drawdown-avoidance IS return-maximization** (capital-
velocity law). The fastest 50k→200k path minimizes hole depth; it does not crank
sizing.

## v70 Apex perf history (N=100, RXDD-on baseline; median terminal multiple)
| window | median × | P(reach 4×) | collapse |
|---|---|---|---|
| 2020_crash (COVID) | ×0.4 | 0% | 0% |
| 2022 (bear) | ×1.85 | 0% | 0% |
| dip (recent drawdown) | ×2.8 | 6% | 0% |
| 2021 (bull) | ×3.4 | 36% | 0% |
| 2025 (choppy) | ×8.8 | 98% | 0% |
| 2024 (monster bull) | ×58 | 100% | 0% |
| 5y | ×3,879 | 100% | 0% |
Read: in a normal-to-good year you blow past 4× (often in months); in a
bear/flat/drawdown year you stall short of 4× but never collapse; over any
multi-year stretch 4× is ~certain. Current regime (VIX 15.4 calm / NEUTRAL /
breadth 42) is a normal-EV environment — RXDD is dormant (only fires VIX 20-28).

## Phase B — exposure-cap scan + DD-band LHS (N=100 × 6 windows) — DECISIVE
**Exposure cap (the user's "100%" hypothesis):**
| cap | mean-log-compound | speed-win | bear-win | DD chg | 5y DD | collapse |
|---|---|---|---|---|---|---|
| 0.35 | −0.479 | −0.317 | −0.804 | −6.3 | 70.1 | 0% |
| 0.40 | −0.353 | −0.223 | −0.613 | −2.6 | 71.5 | 0% |
| **0.50 (base)** | **0** | **0** | **0** | 0 | 71.2 | 0% |
| 0.65 | −0.104 | +0.008 | −0.329 | +6.5 | 76.6 | 0% |
| 0.80 | −0.453 | −0.231 | −0.898 | +10.9 | 87.4 | 0% |
| **1.00** | **−0.670** | −0.335 | −1.339 | +15.5 | **99.2** | **10%** |

**Verdict: the user's "run 100%" hypothesis is FALSIFIED for static exposure.**
- The cap curve peaks at **0.50** and declines sharply above it.
- **Static 100% literally collapses 10% of the time** and compounds *slower* (5y
  DD 99%). The capital-velocity law dominates; the 50% cap is already the speed
  optimum. Raising it doesn't even speed the bull windows (cap 0.65 speed +0.008
  ≈ flat) because the marginal calls above 50% are low-EV (crowding, concur 10+
  EV +0.047).

**DD-band re-tune (the real, modest lever):** it's a frontier knob — higher floor
/ later contraction → +compound + DD; earlier/harder → −compound − DD. Best
collapse-safe: **c12 (LO 0.282 / HI 0.556 / FLOOR 0.49)** comp +0.039, bear
+0.099, collapse 0 (the user-aligned "lean aggressive" direction); c07 (0.36 /
0.525 / 0.44) +0.026, robust. Within the N=100 noise floor → N=300/500 confirms.
Aggressive bracket (cap 0.70 + loose band) had biggest compound +0.185 but
worst-window −0.28 + DD +10pp (high-variance) → tested at N=300 in Phase C.

## Mining of the adjacent angles the user asked about
- **call/put ratio**: NOT a clean clue (non-monotonic; balanced 1-3 best EV).
- **daily call-signal supply**: rare broad-breakout days (25-60 ≥75-signals) are
  high-EV (+0.25 vs +0.054) but only 0.26% of trades — too rare for a lever.
- **sector-ETF breadth**: source CSV absent + heavy prior NULL (Priority #13) →
  deferred to a next-lead.

## Phase C (running) — drill at N=300×8 incl COVID
Candidates: DD-band c12 + c07; static aggr cap 0.70; **EXR** (size+VIX-gated hot
exposure — the user's "100% early" made collapse-safe: hot cap while small, throttle
at scale, VIX-fade so a small book can't run hot into a crash); EXR pure-VIX-cap
(= regime-conditional exposure); EXR+c12 combo. Rank: collapse=0 every window incl
2020-COVID (hard floor) → max mean log-compound; DD reported, not constrained.

## Phase C — drill at N=300 × 8 windows INCL COVID (decisive)
| candidate | compound | speed | bear | worst-win | collapse | verdict |
|---|---|---|---|---|---|---|
| **c01 ddband07** (LO.36/HI.525/FL.44) | **+0.041** | +0.024 | +0.034 | −0.010 | **0%** | ✅ only clean winner |
| c00 ddband12 (FL.49) | +0.052 | +0.005 | +0.101 | −0.120 | **25%** | ❌ high floor → COVID collapse |
| c02 aggr70 (static .70 cap) | +0.041 | +0.185 | +0.128 | −1.030 | **100%** | ❌ static aggression = COVID ruin |
| c03 exr70 (HOT.70 gated) | −0.084 | −0.043 | −0.240 | −0.475 | 0% | ❌ collapse-safe but worse |
| c04 exr100dd (HOT1.0 tight gates) | −0.237 | −0.313 | −0.313 | −1.463 | 0% | ❌ gates kill upside |
| c05 exrVix (regime-cap, no size) | −0.079 | +0.026 | −0.301 | −0.356 | 0% | ❌ worse |
| c06 exr70+dd (combo) | −0.034 | −0.014 | −0.100 | −0.354 | **6.7%** | ❌ |

**Two hard conclusions:**
1. **EXR / "run 100% early" is empirically NULL.** The bull-lift (+22% on 2024 in
   the N=100 verify) does NOT survive the full window set — every collapse-safe EXR
   variant *underperforms* baseline (the velocity penalty in non-bull tape + the
   gating erase the bull gain), and the un-gated/high-floor versions collapse. The
   exposure-aggression hypothesis is now falsified at N=300 three independent ways
   (static cap, size-gated, regime-gated). EXR stays env-gated OFF (byte-identical).
2. **Phase B (no COVID window) HID a collapse.** ddband12 + static-aggr looked clean
   at N=100 but collapse 25–100% once 2020_crash is in. **Lesson: never rank a
   screening phase without a crash window.** The only config beating baseline AND
   collapse=0 incl COVID is **c01 ddband07** — a gentle DD-band re-tune
   (LO 0.35→0.36, HI 0.55→0.525, FLOOR 0.40→0.44): +4% mean log-compound, DD flat
   (dd5y 73.5 vs 73.0), worst-window −0.01 (rock-solid across all 8). Within the
   N=300 noise floor → Phase D (N=500) confirms before ship.

## Phase D — ship-gate (N=500 × 8 incl COVID) — NO SHIP
| candidate | compound | dd5y | worst-win | collapse |
|---|---|---|---|---|
| baseline | — | 73.2 | — | 0% |
| c00 dd07 (FL.44) — Phase-C winner | +0.036 | 73.5 | −0.017 | **0.2%** ❌ |
| c01 dd07b (FL.42) | +0.021 | 72.4 | −0.004 | 0% |
| c02 dd07c (FL.43) | +0.032 | 73.9 | −0.035 | 0% |

The ship-gate DOWNGRADED the win: the Phase-C "winner" (FLOOR 0.44) collapses
0.2% at N=500 (the hidden-collapse failure caught one level deeper). The
collapse-safe neighbors are WITHIN the MC noise floor (+2-3% compound, ±0.8pp DD).
=> **No confirmed DD-band improvement. v70 Apex sizing is already near-optimal**
(FLOOR 0.40 sits at the collapse boundary). Per the don't-ship-noise discipline,
NO SHIP. dd07b (0.35/0.525/0.42) is logged as an OPTIONAL marginal Pareto-non-
negative candidate (NEW_LEADS) but not worth the propagation churn.

## NET RESULT of the sizing exploration
The user's sizing-aggression hypothesis is **comprehensively falsified** (static
cap scan, EXR size+VIX+DD-gated, DD-band frontier — all at N=300-500 incl COVID).
The v70 Apex sizing config is near-optimal; drawdown-avoidance = return-max is the
governing law. The shippable alpha, if any, is NOT in sizing — it's in the one
orthogonal unexploited lever: **letting the high-conviction WINNERS run (trailing
stop)**, which the honest-v70 low-high-conviction-hydration regime may finally
make pay (see TRAILING-STOP experiment, next).

## The answer to the user's question
- **"Can we go faster by sizing up / 100% / regime-conditional exposure?"** — No.
  Rigorously falsified. Static 100% collapses 10% + compounds slower; size+VIX+DD
  -gated hot exposure (EXR) underperforms; the 50% cap is the velocity optimum.
- **The law:** for this leveraged-momentum sleeve, drawdown-avoidance IS
  return-maximization. The fastest 50k→200k path minimizes hole depth.
- **The shippable win:** none confirmed (DD-band tweak fell inside the noise floor
  at the N=500 ship-gate; the Phase-C candidate actually collapsed 0.2%). v70 Apex
  sizing is already at a robust local optimum.

## TRAILING STOP (user idea: let high-conviction winners run; honest-v70 low
## high-conviction hydration as the new condition) — RE-CONFIRMED NULL
Built TSL (env-gated): after +TP, trail TSL_SIGMA*σ below peak (floored at +TP),
conviction-gated to overall>=TSL_MIN_SCORE; off=byte-identical, on fires (verify:
2024 ×58->×4.5). Family screen (8 variants, 2024+5y, N=100):
| variant | compound (mean log) | collapse |
|---|---|---|
| trail 75+ (any σ 0.4-0.9) / trail-all (70) | −6.2 to −7.2 | **100%** |
| trail 80+ | −2.2 | 0% |
| trail 85+-only (σ 0.6-1.2, best case) | **−1.2** | 0% |
Monotonic: more trailing = worse. Trailing the bulk (75+) **collapses the 5y
window 100%** (held winners cluster + crash together in a drawdown) AND craters
compound (velocity loss). Even trailing only the rarest 85+ winners loses ~70%
compound. The low-hydration reframe is FALSIFIED: the freed slot's overflow refill
compounds more than holding the winner, and holding winners clusters DD/collapse.
Capital-velocity ("close fast, redeploy") is load-bearing on honest v70. TSL stays
env-gated OFF. (Null-with-condition: justified retry, retested, still null.)

## REGIME-ADAPTIVE TP/SL (user idea: TP/SL under stress) — NULL
N=100x6, no candidate clean-beats baseline. Stress-WIDER TP (the proven pre-v70
brd_TP30/35 +44% shape) BACKFIRES on the HOLD core (tpS42 comp −0.22, bear −0.65)
— HOLD + dead-hold already subsume the stress-ride. Calm-TIGHTER barriers
(TP0.28/SL−0.60) give bull speed (+0.18) but worse DD (+6.7pp) / bear (−0.25) /
worst-window (−0.48), likely COVID-collapse (untested). Flat TP0.30/SL−0.70 is
near-optimal. (One open sliver: tighter-calm-barriers is the night's only +speed
signal — a DD-relaxed book MIGHT accept it pending COVID/robustness validation.)

## REGIME MULTIPLIER (user idea: tighten it) — already well-calibrated, no headroom
Read-only 5y A/B (experiments/regime_ab_test.py) on honest v70. On 90+ CALL (the
P&L driver): current (B) WR15 77.9 / EV30 2.92 is OPTIMAL-or-best; no-regime (A)
−15.5pp WR15 (the mult correctly gates bad-tape-amplified 90+ peaks); wider bands
(G/I) worse. Tightening toward less modulation moves toward the much-worse
no-regime extreme → no headroom. (Stage-1, heavy to ship; honest-frontier caveat.)

## FINAL CONCLUSION — every requested lever evaluated
| user idea | stage | result |
|---|---|---|
| 100% / dynamic / size / regime-conditional EXPOSURE | S3 | FALSIFIED (static 100% collapses 10%; EXR null) |
| DD-band re-tune | S3 | within-noise at N=500 ship-gate; NO confirmed ship |
| trailing stop (let winners run) | S2 | RE-CONFIRMED NULL (100% collapse on bulk; velocity) |
| regime-adaptive TP/SL | S2 | NULL (stress-wider-TP backfires; HOLD subsumed it) |
| regime multiplier tighten | S1 | current well-calibrated; no headroom |
| signal-pool displacement/realloc | S2/3 | documented null + dead-hold conflict + cross-side moot |
| sector-ETF regime lever | — | blocked (source data missing + Priority-#13 null) |

**v70 Apex (scoring + portfolio) is at a robust, well-tuned optimum across all
these levers.** Drawdown-avoidance = return-maximization (capital-velocity) +
collapse=0 govern everything; every lever that adds exposure or holds positions
longer nulls or collapses. The remaining alpha is NOT in re-tuning these portfolio/
regime mechanisms — it's in the option-pricing layer (skew/semivol — top NEW_LEAD).

## SVR — semivol_r skew-bridge entry filter (the ONE real lead) — PARETO SIGNAL
The only orthogonal-alpha lead: `semivol_r` = 60d downside/upside realized-vol ratio
(the 10y MC-computable cousin of confirmed option put-skew; prior hunt
`experiments/iv_skew/`). Cohort (proxy_ledger 10y, by quintile within 75+/80+):
INVERTED-U — low svr (~0.5, call-euphoric/EXPENSIVE) is the WORST cohort (75+ win 0.35,
80+ pnl −0.14); very-high (~1.4, crash-mode) weak; middle-high (~0.9-1.1) sweet spot.
Built env-gated SVR band-pass alloc-filter in monte_carlo (downweight call alloc toward
SVR_FLOOR below SVR_LO_FULL / above SVR_HI_FULL; off=byte-identical). Feature cache
`.cache/apex_speed_v70/semivol_map.parquet` (393,960 ticker-keyed).

Phase B (N=100x6) — TOP CLEAN (collapse=0):
| candidate | compound | bear | dd5y | note |
|---|---|---|---|---|
| baseline | — | — | 71.2 | — |
| **lo07f50** (gentle low-cut svr<0.7→0.5) | **+0.034** | **+0.110** | **67.1 (−4.1pp)** | the win — +compound AND −DD |
| hi_only (crash-mode taper) | +0.023 | +0.033 | 71.2 | mildly + |
| lo08/floor0.3/band30 (over-filter) | −0.04 to −0.58 | — | — | reduces exposure too much |

**lo07f50 is +compound AND −4.1pp DD, collapse=0 — the ONLY both-axes improvement of
the night** (everything else was within-noise or collapsed). Real + orthogonal (options-
skew alpha via the semivol_r bridge); the GENTLE downweight of the worst (euphoric/expensive)
call cohort is the Pareto nudge; over-filtering hurts (exposure loss). Compound is within
the N=100 noise but the −4.1pp DD is the more reliable signal + the strong per-trade backing.
Phase C (N=300x8 incl COVID) — CONFIRMED + STRENGTHENED, ALL clean (collapse=0):
| candidate | compound | bear | dd5y | collapse |
|---|---|---|---|---|
| baseline | — | — | 73.0 | 0% |
| **gentleband** (downweight BOTH extremes gently) | **+0.124** | **+0.129** | **66.4 (−6.6pp)** | **0%** |
| lo07f50 / lo07f60 (low-cut only) | +0.045/+0.047 | +0.086 | 67.9/68.6 | 0% |
| hi_only | +0.033 | −0.03 | 72.0 | 0% |

**gentleband is a GENUINE Pareto win: +12.4% compound AND −6.6pp DD, collapse=0 on every
window incl 2020-COVID — and it STRENGTHENED N=100→N=300** (the band-pass beats low-cut-only;
the inverted-U cohort wants both extremes gently downweighted). The DD improvement is the
reliable signal; the compound sign is consistent across all candidates/windows. This is the
night's ONE real find — the confirmed option-skew alpha (`experiments/iv_skew/`, t=+4)
operationalized via the 10y `semivol_r` bridge, orthogonal to the price-technical score.
→ Phase D (N=500x8) ship-gate confirm, then STAGE (RXDD-scale new mechanism; the LIVE
semivol_r feature + 13-consumer wiring won't fit pre-open). Handoff: SVR_SHIP_HANDOFF.md.

## Artifacts / reversibility
- EXR + TSL: env-gated OFF (byte-identical), getattr-guarded, in monte_carlo.py;
  driver ENV_MAP extended. Validated-null infra, preserved for re-test.
- Holdout: CALIBRATION_CUTOFF_DATE=None (user-directed removal).
- drift-guard 591 + registry green. dd07b + tighter-calm-barriers = optional leads.
- **Current-regime guidance (VIX 15 calm / NEUTRAL / breadth 42):** a normal-EV
  environment — run standard Apex; in a normal-to-good year the median path 4×'s
  within ~a year (×8.8 in a 2025-like year, ×58 in a 2024-like year), in a
  bear/flat year you stall short of 4× but never collapse, over any multi-year
  stretch 4× is ~certain.

