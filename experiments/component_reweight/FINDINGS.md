# Component-Reweight Investigation — v70 75+ CALLs (funded APEX sleeve)

Goal (user): bucket v70 75+ CALL signals by their scoring-component combination, see which
combos win vs lose on the funded sleeve, and tune the dynamic component weighting
(`w_trend/w_bb/w_rsi/w_macd/w_stoch/w_ta`, blended by `d=trend_dominance`) to reward the
winning combinations. Puts deferred. Portfolio test-runs deferred until the apex/sentinel/core
knobs (SL/TP/alloc/max-hold) are retuned by the other agent.

Status: **Stage A (descriptive) DONE. Stage B (reweight sweep) + Stage C (APEX MC) STAGED, HELD
for the incoming portfolio knobs.** All staging-native, zero DB writes, holdout-locked (<=2026-05-15).

---

## Setup

- Ledger `.cache/component_reweight/ledger_v70_5y.parquet`: 33,945 v70 CALL signals (overall 70-99,
  2016-05-16..2026-05-15), raw 6 component scores + volume + weight_info, and a **barrier-agnostic
  forward-path capture** (first calendar-day each favorable/adverse σ-grid level is touched,
  FIXED thresholds, intraday). `label.py` derives win/loss for ANY (TP_σ, SL_σ, max-hold) → the
  pending knob retune costs zero rebuilds.
- **Primary win label = funded APEX TP30/SL70/15d** = underlying reaches +1.092σ before −2.548σ
  within 15 cal days (TP+30%→+1.092σ, SL−70%→−2.548σ at PREMIUM_MULT=1.82, DELTA=0.5). This is
  the FIXED-threshold convention `monte_carlo.py` exits on — NOT the assess `√(W/30)`-scaled barrier.
- Win% here = the TP-before-SL touch rate. True sleeve EV is a bit better than the raw win% vs the
  70% break-even implies, because day-15 expires exit above −70% (the MC resolves this).

## Baselines (apex TP30/SL70/15d)

| band | N | apex win% |
|---|---:|---:|
| 70-74 | 29,219 | 68.1 |
| 75-79 | 3,711 | 69.8 |
| 80-84 | 584 | 68.8 |
| 85+ | 403 | 73.7 |
| **75+** | **4,699** | **70.0** |

Two structural notes: (1) the 70-74 reservoir (68.1) is nearly as good as 75-79 (69.8) — the 75
line barely separates apex win-rate there; (2) 80-84 (68.8) dips *below* 75-79.

## The pattern: trend-dominated scores underperform; oscillator-dominated scores outperform

**Lead component (max weighted contribution `w_i·(c_i−50)`), 75+ pool, apex win%:**

| lead | N | share | apex win% | z vs pool |
|---|---:|---:|---:|---:|
| RSI | 122 | 2.6% | 77.9 | +1.87 |
| BB | 30 | 0.6% | 76.7 | +0.79 |
| MACD | 626 | 13.3% | 73.3 | +1.70 |
| **TREND** | **3,913** | **83.3%** | **69.2** | **−0.81** |

TREND leads 83% of all 75+ signals yet underperforms the pool. The oscillator-led minority wins more.

**Per-raw-component tercile within 75+ (high−low apex win% spread):**

| comp | spread | z | reading |
|---|---:|---:|---|
| RSI | +4.1 | +2.56 | higher → wins more |
| STOCH | +3.4 | +2.04 | higher → wins more (weight is only 5!) |
| TA | +3.2 | +1.94 | higher → wins more |
| MACD | +2.2 | +1.32 | higher → wins more |
| TREND | −0.9 | −0.53 | higher → no help / slight hurt |
| BB | −1.3 | −0.77 | higher → slight hurt |

**Pair matrix (confirms it's a trend-dominance problem):** TREND+/RSI+ = 63.3% (worst cell),
TREND−/RSI+ = 76.4, TREND−/MACD− = 77.4, TREND+/MACD− = 69.4. The high-trend signals are the losers.

## Marginal-boundary integrity (the decisive test — reweighting trades on the 70-79 boundary)

Reweighting promotes 70-74 winners into 75+ and demotes 75-79 losers out. The thesis holds only
if a component's effect is **consistent across both bands**. Tercile high−low spread:

| comp | 70-74 (N29k) | 75-79 (N3.7k) | consistent? |
|---|---:|---:|---|
| **TREND** | −1.9 (z −2.75) | −1.9 (z −1.00) | **YES — robustly negative → TRIM** |
| **TA** | +3.0 (z +4.59) | +3.8 (z +2.04) | **YES — robustly positive → BOOST** |
| RSI | +0.4 (z +0.52) | +3.8 (z +2.10) | partial — helps resident pool, flat at margin |
| STOCH | +0.6 (z +0.93) | +4.2 (z +2.29) | partial — helps resident pool, flat at margin |
| MACD | +1.0 (z +1.54) | +2.6 (z +1.38) | weakly positive, never significant |
| BB | +3.5 (z +5.19) | −1.0 (z −0.54) | **SIGN-FLIP — band-dependent, NOT a clean lever** |

**Robustness:** the lead-class ordering (RSI > BB > MACD > TREND) holds across TP25/30/35 and
SL50/70/100 — so it survives whatever apex/sentinel/core knobs land. (The growth-gate `opt`
barrier is flatter/noisier; the edge is specific to the funded apex barrier — which is the user's goal.)

**Momentum confound:** partly entangled — at high c_trend almost every signal is trend-led by
construction. But within the low-trend segment, MACD/RSI-led (73-78%) still beat trend-led
(68.5%), and the low-trend pool (70.3) ≈ high-trend pool (69.8). So it is not *pure* momentum;
it's a genuine "score-driven-by-oscillators-not-trend wins more" effect, holding trend ~constant.

## Reweight hypothesis (for Stage B, barrier-robust)

Current v70 weights: `TREND 18+10d, BB 18, RSI 25−9d, MACD 25−6d, STOCH 5, TA 9+6d`.

Cleanest, marginal-consistent moves:
1. **Trim TREND** (`W_TREND_BASE`/`W_TREND_SLOPE`) — robustly over-weighted (z up to −2.75; leads 83% but loses).
2. **Boost TA** (`W_TA_BASE`/`W_TA_SLOPE`) — robustly under-weighted (z up to +4.59).
3. Likely boost STOCH (weight just 5, strong 75-79 tercile) and RSI — caveat: flat at the 70-74
   margin, so they raise resident-pool quality more than they improve promoted signals.
4. Leave BB ~alone (sign-flip).

Caveats to respect in Stage B: (a) within-band tercile effects are mechanically entangled
(raising one raw component lowers others at fixed overall) — the re-simulate is the clean test;
(b) effect sizes are modest (the funded apex baseline 70.0% sits ~at the 70% TP30/SL70 break-even —
the honest edge is thin, consistent with prior findings); (c) the edge is mostly a momentum-timing
improvement of a leveraged-momentum sleeve, not non-momentum alpha (Part 10 of v69 MASTER_FINDINGS).

## Stage B RESULT — reweight cannot beat the supply/WR tradeoff (near-null; v70 already well-calibrated)

Fast harness: `ScoreSimulator.capture_inputs` caches weight-invariant per-signal inputs in ONE
pass (smoke-proved exact: 0/1535 mismatches), then each variant re-scores via
`compute_overall_score` only. Fidelity re-sim-vs-DB jaccard 0.903 / recall 0.921 (the ~8% gap is
cont_lift, which `simulate()` omits — uniform across variants, so relative ranking holds).
97 variants (6 hypothesis seeds + 90 LHS), funded barrier = Apex TP30/SL70/day-15. Baseline:
apex75 70.0%, supply 435/yr, opt75 47.7%, 85+ 73.4%.

**The Stage A direction is confirmed but non-exploitable.** Every clean trim-TREND/boost-oscillator
seed lifts apex75 only +0.6-0.9pp while **collapsing 75+ supply −30% to −49%** (S1 −194/yr,
S2 −213, S4 −133), several regressing 85+ (S3 −4.0pp, S6 −3.7pp). Mechanism: **TREND is
load-bearing for SUPPLY** — it's the high-weight component that pushes the trend-led majority
(83% of 75+ leads) over 75. Trimming it demotes them; you shrink the pool to the small
high-oscillator minority that's barely higher-WR. The within-pool tercile edge (RSI/STOCH/TA
high → +3-4pp) was real but a non-scalable minority — it does not survive being made the basis
of the weighting.

Supply-maintaining LHS winners: **t047** (apex75 +0.9pp, supply +5%, but **85+ −1.7pp**),
**t059** (apex75 +0.6pp, supply flat, 85+ +0.5pp). Both apex75 gains are **within noise** (z≈1
at N≈4,500) and **flat on the growth-gate `opt` barrier** (~48%). Max apex75 across all 97 =
72.3% only by crashing supply; the supply-maintaining ceiling is ~+0.9pp.

**Verdict: DO NOT SHIP a v71 reweight.** No candidate clears the Stage-1 scoring bar:
- per-trade apex WR gains are within noise (supply-maintaining ceiling ~+0.9pp);
- the official growth-gate `opt` barrier is flat across all variants;
- the only way to a larger WR gain is crashing supply (the seeds), which the funded sleeve
  cannot afford; and the best obj candidate regresses the 85+ tier the filled book relies on.

This is consistent with the honest-substrate priors (thin/momentum; no z≥3 call cohort; v70
dampener stack already absorbs the call signals). The component-reweight lever is **rigorously
closed**: the v70 dynamic weighting (`trend 18+10d, bb 18, rsi 25-9d, macd 25-6d, stoch 5,
ta 9+6d`) is already near-optimal for the funded Apex sleeve. The reusable value is the
QUANTIFIED tradeoff (TREND trades supply-for-nothing; oscillator edge is a non-scalable minority)
+ the fast capture/re-score harness + the barrier-agnostic ledger.

**Stage C (APEX MC) NOT auto-run:** the framework gates Stage-3 portfolio MC behind a Stage-1
pass, which this fails. A v71 recalc+MC would (a) write a new version's ~1M rows to the shared
production DB for a candidate that won't ship, and (b) per t047's 85+ regression, the filled
book (high-conviction-dominated) likely would NOT benefit. Held for explicit user greenlight if
the definitive portfolio number is wanted despite the thin Stage-1 signal.

## Non-linear lever (oscillator-alignment booster) — ALSO closed

Rationale: the linear reweight failed on the supply/WR tradeoff; an ADDITIVE booster could
instead promote/strengthen the high-WR oscillator-aligned cohort WITHOUT trimming TREND's
supply role. Profiled (`profile_oal.py`) + filled-book occupancy sim (`fillsim.py`).

W1 profile of `osc_agree` (c_rsi,c_stoch,c_macd all >=52) apex WR by band:
- 70-74: 69.3% (z+1.35) — FLAT (= 75-79 baseline) -> PROMOTION is EV-neutral (adds baseline-WR supply).
- 75-79: 75.8% (z+2.65, N=466), 80-84: 75.0% (small N) — +6pp; `TREND_lo & osc_agree` 76.5%.
- 85+: 70.5% (z-0.53) — NEGATIVE.
- 75+ pool: 75.1% (z+2.61, N=615 ~60/yr) — real but band-local (75-84 only), just sub-z3.

So only the STRENGTHENING form survives (lift osc-agree 75-84 for cascade priority; value is
filled-book-only, invisible to the pool). Tested directly via a filled-book occupancy replay
(MaxPos=14, conviction-sorted, same-symbol block, hold-to-resolution from the forward-path grids):

**Result: filled-book apex WR moves +0.03 to +0.19pp across ALL booster variants (K=3-8, T=52-55,
±trend-gate) — NOISE.** The book is already conviction-filled from the 85+/80-84 tiers; the
osc-agree 75-79 cohort (~55/yr) is marginal, so strengthening it just reshuffles near-equivalent
signals between bands without moving the aggregate. A full APEX MC is NOT warranted — the
filled-book proxy already shows the effect is below MC noise.

## FINAL VERDICT — v70 scoring is already well-calibrated; no shippable component lever

Tested three mechanism families on the honest substrate / funded Apex barrier, holdout-locked:
1. **Linear reweight** (Stage B, 97 variants): trim-TREND/boost-oscillator trades −30-49% supply
   for within-noise WR; supply-maintaining winners within-noise + flat opt-gate + 85+ risk.
2. **Non-linear promotion** booster: EV-neutral (70-74 osc cohort = 75-79 baseline).
3. **Non-linear strengthening** booster: +0.1pp filled-book WR (noise).

The winning combinations Stage A found (oscillator-aligned > trend-dominated) are REAL but
**not exploitable**: a non-scalable minority for reweighting, EV-neutral at the promotion margin,
and too marginal in the filled book to strengthen. Consistent with the honest-substrate priors
(thin/momentum; v70 dampener stack already absorbs the call signals). **DO NOT SHIP. DO NOT MC.**
Deliverables: this quantified closure + the reusable barrier-agnostic ledger + capture/fast-rescore
harness + filled-book occupancy sim (all in `experiments/component_reweight/`).

## PIVOT (user-prompted) — 70-74 OVERFLOW is a real, large RETURN lever (but hits collapse=0)

The reweight conclusion above stands (linear + booster reweights are null). But the user
challenged the "promotion is EV-neutral" framing, which was WRONG: I'd only filled-book-tested
the *strengthening* booster, never *promotion*. The filled-book occupancy sim (`promote_test.py`)
showed the 75+ book is mostly IDLE — enabling 70-74 nearly TRIPLES the filled book
(3204 -> 8821 trades) at ~68% WR (≈ 75-79's 70%). This is the disabled-overflow lever, re-tested
under the asymmetric canon (untaxed wins) + Apex risk budget. It is a PORTFOLIO knob (no recalc;
`monte_carlo.py` reads v70 scores; `TIER_OVERFLOW` env override).

APEX MC (`run_overflow_mc.py`, N=150, asymmetric canon, v70):
- **Return: HUGE.** overflow 0.05/0.10 → +3× to +17× compound on 5y/10y/22-now; supply 2-3×.
- **DD: +5-19pp** (ovf 0.05 ~80-87%; ovf 0.10 84-88%).
- **Collapse: BREACHES the hard floor in 2020-COVID** — baseline 0.7%, ovf 0.05 2.0%, ovf 0.10 21.3%.
  All other windows collapse=0. Ruin is the one Apex non-negotiable, so as-is it FAILS.

So overflow is **not shippable as-is** but is a genuine engineering problem (capture the upside at
collapse=0), NOT a null. Refinement (`run_overflow_refine.py`, N=300): sweep lower allocs
(0.02/0.03/0.04) on the collapse-critical windows to find a collapse-safe config; also re-baseline
at N=300 (N=150 baseline showed 0.7% COVID collapse — the shipped Apex sits near the edge there).
NOTE: 70-74 overflow is the OTHER agent's portfolio domain (Apex/Core/Sentinel profiles) — any
real enable is a coordinated `strategy_config` change, not a unilateral ship.

## OVERFLOW REFINEMENT (N=300) — collapse-safe at alloc <=0.04; THREADS THE NEEDLE

Collapse is NON-LINEAR in overflow alloc: ~0 up to 0.04, then jumps (2% at 0.05, 21% at 0.10).
N=300 (reliable tail), asymmetric canon, v70 scores:
- **collapse: 0.0-0.3% on EVERY window incl 2020-COVID for alloc 0.02/0.03/0.04** (= baseline ~0.3%;
  the N=150 0.7% baseline was noise). 0.04 COVID collapse = 0.0% (better than baseline).
- **return: 10y +12-15x, 5y +1.8-2.2x, 22-now +1.5-1.8x, 2022 +1.5-2.3x** vs baseline; supply ~3x.
- **DD: +1-9pp, within Apex's ~86% budget** (ovf_0.04 10y 85.1%, 5y 79.7%).

**Lead candidate: `TIER_OVERFLOW=0.04`** (highest return, collapse=0 in COVID, DD <=85% within budget).
`0.03` is the conservative fallback. Headline multiples are MC model-scale (leveraged-momentum
compounding of more +EV trades filling idle 75+ slots — NOT new alpha); the translatable metrics
(collapse=0, DD-in-budget) are what hold. This is a legitimate Apex enhancement under its
"max survivable compounding" mandate.

CAVEATS / NEXT: (1) N=300×5 is screen-grade; ship needs N=500 × 8-canonical-windows to robustly
confirm collapse=0 at 0.04 (it's near the 0.05 cliff). (2) 70-74 overflow is the OTHER agent's
portfolio domain (Apex profile) — the enable is a coordinated `strategy_config.TIER_ALLOC['overflow']`
change, not a unilateral ship. (3) the 2026-04 disable reason (correlated-crash DD) is REAL and is
exactly why alloc>=0.05 collapses — staying <=0.04 is load-bearing.

## OVERFLOW CLIFF PINNED (N=300) + APEX-FAITHFUL VALIDATION

Cliff sweep (`run_overflow_edge.py`, 0.035/0.040/0.045/0.050):
- **collapse-safe up to alloc 0.040** (2020-COVID collapse 0.0%); **0.045 BREACHES (1.3%)**, 0.050 (2.0%).
- **return is non-monotonic — 0.035 is the return-optimal safe alloc**: 10y +35.8M% (15.3×) > 0.040
  +34.2M% > baseline +2.34M%. Confirms the over-deployment law (alloc past ~0.035 deepens DD without
  adding 10y return). 22-now/5y rise modestly to 0.05 but 10y peaks ~0.035.

**CRITICAL correctness check — the overflow MC ran on the TRUE apex config** (verified by importing
`monte_carlo` under the driver's env): `PRACTICAL_EXPOSURE_ENABLED=True`, **`GROSS_PREMIUM_CAP=0.50`**,
`CALL_PREMIUM_CAP=0.50`, uncapped ceiling, MaxPos 14 / puts 0, TP+30%/SL−70%/HOLD-15, DD-band
0.35/0.55/0.40, SLIP sl/hard=−0.015 (asymmetric cost canon). So overflow's win is **WITHIN the 50%
gross cap** — it fills the IDLE book (occupancy: only 3204 of ~33917 eligible fill at 75+; overflow →
8821) without raising exposure past 50% and without displacing higher-conviction 75+ (which sort first).
This does **NOT** contradict the handoff's "over-deployment past 50% hurts" (that warned against RAISING
the cap; this UTILIZES the idle space inside it). **Overflow is the genuinely-untested utilization lever
the portfolio agent left at 0** (the 2026-04 disable predates the honest-v70 / wide-SL / 50%-cap apex).

## THREAD 1 — WEEKLY COMPOSITE + ICH (honest v70), look-ahead-guarded

`weekly_mine.py` on the ledger (apex barrier, calls). Honesty sanity passed: `w_adj ≈ (1−t)·completed
+ t·partial` to 0.023.

- **Strong weekly confirmation is CONTRARIAN in [70,84]** (the opposite of how the score treats it —
  the score ADDS w_adj). Across every tradeable band, the HIGH-weekly tercile WINS LESS. Cleanest
  signal = `wadj_completed` (prior completed week): HIGH tercile **66.8% WR (75+, z=−3.40)** and
  **66.5% (70-74, z=−4.29)** vs LOW ~69-72%. Pool base 70.0% == apex break-even (SL70/TP30) → the
  high-weekly cohort is the sub-break-even "buying an over-extended weekly uptrend" tail. (There is a
  v61 silo literally named "weekly mature call guard" — same intuition.)
- **LOOK-AHEAD GUARD PASSES (honest).** `wadj_completed` is the prior completed week (no future) and its
  contrarian edge **persists on Friday** (−2.4pp), no Monday-loaded/Friday-dead signature anywhere.
  v69's honest blend holds. The blended `w_adj` decays toward Friday only because Friday's blend = the
  weaker current-week partial — not a leak.
- **ICH** barely fires at ≥75 (N=36) — it already did its job pre-threshold; nothing to mine.

Effect at 85+ INVERTS/weakens (small N) → any mechanism must gate to [70,84].

**Temporal robustness (per-year, [70,84] band, high vs low wadj_completed):** the contrarian effect
holds in **10 of 11 years** (2016 −4.6, 2017 −3.3, 2018 −1.6, 2019 −0.9, 2020 **−5.0**, 2021 −3.0,
2023 −5.7, 2024 −0.4, 2025 −4.7, 2026 −3.0; POOL −2.7pp). **Strongest in the COVID crash year (2020,
−5.0pp)** — exactly where collapse-safety matters, supporting the collapse-reduction hypothesis. **One
inversion: 2022 +2.8pp** — in a sustained bear, strong prior-completed-week weekly is a genuine-survivor
signal, not over-extension. Documented caveat; 2022 is not a collapse window for apex (collapse 0.0% at
all overflow allocs), so the inversion costs a little 2022 return but doesn't threaten the floor.

**Filled-book pre-check (`weekly_fillsim.py`):** dropping the mature-weekly 70-74 (wadj_completed top
tercile, >8.4) from the overflow band lifts filled-book WR **+0.78pp at only −5% fills** (67.7→68.5%) —
4× the component-reweight strengthening (which was +0.1-0.19pp noise), and it's a DROP of an identified
sub-break-even cohort, not a reorder. Necessary condition for the edge passes. Decisive test queued
(`run_overflow_weekly.py`, MC #4): does weekly-filtered overflow LOWER COVID-collapse/DD at equal alloc
(fewer simultaneous sub-BE losers in a crash) OR raise the collapse-safe alloc ceiling past 0.040?
Wired as a default-OFF, read-only experimental knob `WEEKLY_OVF_FILTER` in `monte_carlo.py` (same class
as the existing `_OV` knobs; inert in production; touches no defaults/profiles/scoring/strategy_config).

**MC #4 RESULT (`run_overflow_weekly.py`, N=300) — REFUTES the collapse-reduction hypothesis; the
filter is a RETURN-vs-SURVIVAL dial:**
- **Return AMPLIFIER**: filt vs plain at equal alloc nearly **DOUBLES 5y/10y** (filt_0.035 5y +415,877%
  vs plain +225,130%; 10y +7.36e7% vs +3.58e7%; 22-now +88,559% vs +74,259%). Concentrating the
  overflow into the higher-WR low-weekly calls compounds harder in normal/bull tape.
- **But BREACHES the COVID collapse floor**: 2020_crash collapse 0.0%→**2.0%** (0.035), **4.7%** (0.040),
  **5.0%** (0.045). Plain overflow stays collapse=0 at ≤0.040. DD also +1-4pp on 5y/22-now; 2022 return
  worse (the bear inversion).
- **Mechanism (key risk-model lesson)**: dropping the mature-weekly overflow removes ~34 crash-window
  positions → less **diversification / cash-buffer** → more iterations tip to collapse. In a crash,
  **position COUNT (buffer) beats per-trade WR** — higher-WR-but-fewer is *worse* for survival. Same
  lesson as the handoff's put-closure (#5 "puts consume the cash buffer") and "over-deployment hurts."
- **Verdict (full-tercile)**: DISQUALIFIED for Apex (collapse floor is non-negotiable). Plain overflow
  @0.035 stands as the validated, collapse-safe edge. STRENGTH sweep queued (#6, `run_weekly_strength.py`,
  N=400, wcomp cut 8.4/11/14/16) to test whether a MILDER cut keeps enough diversification to stay
  collapse-safe while capturing some of the 2× boost — else the filter is fully closed.

**MC #6 STRENGTH SWEEP (N=400) — the filter is FULLY CLOSED; no free lunch.** The return boost is
*entirely coupled* to the collapse breach:
| cut | 2020_crash collapse | 5y MedRet | 10y MedRet |
|---|---|---:|---:|
| plain (none) | 0.2% ✓ | +217,744% | +3.55e7% |
| filt_8.4 (tercile) | **2.5% ✗** | +418,501% | +7.28e7% (2×) |
| filt_11 | 0.5% ✗ | +136,856% | +1.73e7% |
| filt_14 | 0.0% ✓ | +184,774% | +2.99e7% |
| filt_16 (~top 5%) | 0.2% ✓ | +190,935% | +3.74e7% |
Every collapse-SAFE cut (14/16) is **within noise of plain** on 10y (+5%) and *worse* on 5y; the only
return-boosting cut (8.4, 2×) **breaches** COVID. **There is no collapse-safe filter that beats plain
overflow.** The mature-weekly contrarian signal is REAL & honest but **cannot be harvested at the
portfolio level without sacrificing crash-survival.** Plain overflow's full breadth is load-bearing.
→ Thread 1 = characterization + null-for-shipping. **Reusable risk-model lesson: for a ruin-floored
leveraged book, BREADTH/diversification beats per-trade-WR in crashes** — concentrating to
higher-WR-but-fewer trades breaches the collapse floor (same physics as the handoff's put-closure
"puts consume the cash buffer" and "over-deployment hurts"). The `WEEKLY_OVF_FILTER` knob stays in
`monte_carlo.py` default-OFF (inert) as the experimental record.

**Also confirms plain overflow@0.035 is COVID-safe at N=400** (2020_crash collapse 0.2% = baseline,
2020 0.0%) — the N=300 cliff result holds at higher N. Ship-grade N=500 frontier queued (#8).

## THREAD 2 — 15DTE/WR7 under wide SL: MECHANISM ANSWERED (per-trade), portfolio MC queued

User's question: WR7 on v70 looks ≈ or > WR15, and wide SL makes painful losses acceptable — so why is
15DTE astronomically worse? `fifteen_dte.py` (barrier-agnostic ledger, same signals):

- **The user is right on WR**: 15DTE/WR7/SL100 TP-rate 71.5% ≥ 30DTE/WR15 ~70% (75+ pool).
- **But EV is worse**: 30DTE EV/trade +0.0207 (EV/day +0.00138) vs 15DTE/WR7/SL100 +0.0040 (+0.00057).
  Mechanism: 15DTE's smaller premium (1.29σ vs 1.82σ) means a wide-in-% stop is CLOSER in σ
  (SL−70%=1.806σ vs 2.548σ) → more SL hits, and each SL costs −70%/−100% of an already-small premium
  → bigger losses despite equal/higher TP%. Plus shorter window → more day-7 forced exits. **Even
  velocity-adjusted (EV/day, 2× faster recycle) 15DTE loses** — and the apex handoff's #1/#2 confirm the
  why: "velocity/fast-recycling is a frictionless-era fiction; at real ~3% spread, high turnover IS the
  bleed (CUT collapsed 100%)." 15DTE = more turnover = more spread-paying exits.
- **Regime-conditional exception**: 15DTE/WR7/SL100 beats 30DTE on EV/day ONLY in strong-bull years
  (2017/2024/2025/2026) where SL rarely fires; loses every other year (2016/18/19/20/21/22/23).

**PORTFOLIO MC (recovered from `run_15dte_apex.py` logs — driver parse broke on the 15DTE engine's
3-table SUMMARY; the per-window `seeded` lines were intact). The prediction was WRONG in the most
interesting way — under wide-SL HOLD + the asymmetric cost canon, 15DTE is NOT astronomically worse:**

| window | 30DTE ovf035 | 15DTE sl70 ovf035 | read |
|---|---:|---:|---|
| **22-now** | +74,259% / DD 79.0% | **+261,224% / DD 79.7%** | **15DTE 3.5× return, DD ~tie** |
| **5y** | +225,130% / DD 79.1% | +297,645% / **DD 87.8%** | 15DTE +32% return, **+8.7pp DD** |
| **2022 bear** | +44.4% | +10.3% | 30DTE wins (regime flip) |

- The OLD "15DTE astronomically worse / 30DTE wins all 8 windows" verdict was the **tight-SL** regime
  (small premium + tight stop = death). **Wide SL removes that**: in bull tape most exits are untaxed TP,
  so 15DTE's 7-day velocity compounds harder (velocity is NOT a fiction for a HOLD book whose exits are
  mostly untaxed TP — the handoff's "velocity fiction" was about CUT books that pay spread on frequent SL).
- **Regime-conditional**: 15DTE wins return in bull/recent (22-now 3.5×, 5y), loses the bear (2022),
  carries higher DD. → the fundable shape is a **regime-aware DTE ROUTER** (15DTE in bull/low-vol for the
  return boost, 30DTE in bear/crash for survival) — matches & should re-tune the existing production
  "Broad 15DTE router sleeve" for the wide-SL apex.
- **GATING question**: COVID-collapse + 10y were untestable (15DTE engine WINDOWS lacked them). Added the
  windows (additive parity edit) + queued `run_15dte_covid.py` (#7): if pure 15DTE breaches collapse in
  2020_crash, the router is MANDATORY (can't run pure 15DTE); if safe, 15DTE is more broadly viable.
  This is the portfolio agent's domain (router logic + DTE selection) — handing off the direction + evidence.

## (superseded) Stage B / C plan

- Stage B: re-simulate (ScoreSimulator, reassign `scoring.W_*` globals) → re-label new 75+ pool on
  the funded barrier → Bayesian sweep over the 6 base weights (renorm to 100) + 4 slopes; objective
  = supply × per-trade-edge (supply-constrained regime); guards on unlabeled-frac, 85+ non-regression,
  supply floor. Run with the FINAL apex barrier once knobs land.
- Stage C: real growth-gate verdict (opt barrier) + APEX MC (final TP/SL/exposure/max-hold) on any
  robust finalist, via worktree+recalc+driver.py vs v70. Check 22-now / 2020-now WorstDD + collapse=0.

---

# FINAL RESULTS (ship-grade, N=500) — session synthesis

## 🟢 OVERFLOW — VALIDATED & SHIPPABLE (the one portfolio edge)

`run_overflow_shipgrade.py`, N=500 × 9 windows, true apex canon. **collapse-safe on EVERY config ×
EVERY window** (max 0.2% = baseline; 0.040 hits 0.0% on COVID-crash). DD stays in the ~86% Apex budget.

| config | 10y MedRet | 5y | 22-now | 2022 | 2020_crash col | 10y DD | maxCol |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | +2.38M% | +100,784% | +43,212% | +19.9% | 0.2% | 83.5% | 0.2% |
| ovf_0.030 | +28.6M% | +178,193% | +65,671% | +46.5% | 0.2% | 85.8% | 0.2% |
| **ovf_0.035** | **+35.9M% (15×)** | +214,160% | +74,833% | +41.2% | 0.2% | 86.1% | 0.2% |
| ovf_0.040 | +33.6M% | +218,564% | +79,704% | +36.6% | 0.0% | 85.1% | 0.0% |

**Recommend `TIER_ALLOC['overflow']=0.035`** (10y peak, collapse-safe, DD-in-budget). 0.030 conservative,
0.040 upper. Two windows slightly LOWER return (dip −25%, low-N tail; 2024 mixed) but DD-in-budget,
collapse=0. → handed off in `OVERFLOW_HANDOFF.md` (portfolio agent's flip; I touched no
strategy_config/profiles/scoring).

## 🔴→🟢 15DTE — pure 15DTE is CRASH-RUIN; the DTE ROUTER is mandatory & the prize is ~10×

`run_15dte_covid.py` (engine WINDOWS extended to 2020_crash/2020/10y; robust seeded-line parse):

| config | 2020_crash col | 2020 col | 10y MedRet | 10y DD |
|---|---:|---:|---:|---:|
| dte30_ovf035 | 0.0% | 0.0% | +3.58e7% | 85.3% |
| dte15_sl70_noovf | **100.0% ✗** | 100.0% | +1.41e7% | 95.3% |
| dte15_sl70_ovf035 | **93.3% ✗** | 55.7% | **+3.62e8% (10×)** | 92.4% |
| dte15_sl100_ovf035 | 75.3% ✗ | 25.3% | +9.20e6% | 91.0% |

- **Pure 15DTE = RUIN entering a crash** (75-100% COVID collapse vs 30DTE 0%): small premium + 7-day
  window can't survive a gap-down. This is the mechanism behind the old "30DTE wins all 8 windows."
- **BUT 15DTE's 10y return is 10× the 30DTE's** (+3.62e8 vs +3.58e7), collapse=0 over the full decade
  (the crash is one slice the book recovers from; entering AT the crash is the killer). Under wide-SL +
  HOLD + asymmetric cost, 15DTE's velocity is a real bull-tape engine (the old "astronomically worse"
  was the TIGHT-SL regime).
- **Verdict**: a **regime-aware DTE ROUTER (15DTE in bull/low-VIX, 30DTE when crash-risk elevated) is
  MANDATORY** (pure 15DTE is forbidden by collapse) **and the prize is ~10× the bull-tape return.** The
  user's WR7/wide-SL intuition is validated. Hand off to the portfolio agent to build/re-tune the
  existing "Broad 15DTE router sleeve" for wide-SL apex with a crash-risk gate (VIX/breadth/drawdown).
  Engine parity edit (WINDOWS) is additive/inert; the per-signal router logic + validation is their domain.

## 🟡 WEEKLY — closed (real honest signal, not portfolio-harvestable)
Mature-weekly contrarian is real (z −3 to −5, look-ahead-clean, robust 10/11 yrs) but the ONLY
return-boosting filter strength breaches the COVID collapse floor; every collapse-safe strength is
within noise of plain. **Breadth beats per-trade-WR under a ruin floor.** Plain overflow's full breadth
is load-bearing. Characterization, not a lever.

## ⚪ COMPONENT / ICH reweight — null
Honest v70 scoring is already well-calibrated for the funded sleeve; within-band signals are
supply-for-noise and don't transfer. No scoring change.

**Net: ONE shippable edge (overflow, +15× 10y, ready for the portfolio flip), ONE big fundable direction
(15DTE router, ~10× bull-tape, needs the portfolio agent's router build), TWO rigorous nulls (weekly
filter, component/ICH). The honest v70 substrate's edge is portfolio-structural (utilization + DTE
routing), not scoring — exactly the kind of thing the look-ahead-removal would leave.**
