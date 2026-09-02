# Miss-Candidate Re-Mine on the Live v70 Apex HOLD Barrier (2026-06-09)

**Question.** The `alpha_mining/MISS_CANDIDATES.md` harmful-cohort table was mined by the v60
miss-analysis sweep on a **generic** barrier. The live strategy is now the v70 Apex **30-DTE HOLD**
(TP+30% / SL−70%, day-15 hard-sell) = `apex15` = (1.092σ, 2.548σ, 15d). Do these cohorts still flag
genuinely-worse signals when measured on the barrier we actually trade?

**Method.** Read-only eval over `.cache/component_reweight/ledger_v70_5y.parquet` (v70 scores, 10y,
4,699 signals @75+, barrier-agnostic forward-path). Per cohort: two-proportion z of cohort-WR vs
75+-rest-WR on `apex15` (PRIMARY) + `opt15` (tight option) + `gen15` (generic, ≈ what the v60 sweep
used). Harmful = cohort worse = NEGATIVE z; "survives" = `apex15 z ≤ −3`. Script: `eval.py`.

**75+ base WR:** apex15 70.0% · opt15 47.5% · gen15 66.3% (consistent with score_norm).

| family (v60 z) | N | apex15 z | gen15 z | read |
|---|---:|---:|---:|---|
| #1 score 75-79 (+4.38) | 3711 | **−0.6** | −1.7 | gone |
| #2 cont_lift on (+3.62) | 290 | **+0.9** | +0.6 | reversed (cohort better) |
| #4 scw_dampen on (+3.32) | 582 | **−2.0** | −1.3 | below bar |
| #4 stoch_low <30 (+2.54) | 1889 | **−2.2** | −4.3 | generic-vs-option trap (real on gen15, flat on apex15) |
| #5 trend_mid (+2.04) | 237 | **+0.7** | +1.2 | reversed |
| #5 macd_mid (+1.80) | 958 | **+0.7** | −0.3 | reversed |
| #6 sigma_wide (+3.96) | 1409 | **−0.2** | −0.7 | gone |
| #6 sigma_vwide (+4.73) | 470 | **−1.6** | −2.0 | shrank |
| #7 vmag_hi (+3.64) | 1597 | **−1.4** | −2.0 | shrank |

**Verdict: NONE clear z ≤ −3 on apex15.** Every family that looked harmful on the v60 generic
barrier (z +3 to +4.7) flattens to |z|<3 on the funded HOLD barrier; several REVERSE sign
(continuation, trend-mid, macd-mid become slightly cohort-*better*). The miss-candidate table does
**not** translate to the live v70 Apex strategy — these are generic-vs-option-trap artifacts, not
live residuals.

**Mechanism.** It's the *barrier*, not 30-vs-15-DTE. apex15 sells at **+30% (1.092σ — a low TP)**
with a **−70% SL (2.548σ — very wide)** + dead-hold. The miss-cohorts are mostly "signals that dip
first" — under a harder target (gen15 TP 1.414σ) or tight stop (opt15 SL 0.772σ) they miss; under
HOLD they still tag the near +30% TP and the wide SL forgives the dip. Selecting cohorts on the wrong
barrier manufactures phantom weakness.

**Implication.** MISS_CANDIDATES-led Stage-1 damp work is effectively closed for the current strategy
(no harmful residual to damp on the funded barrier). The retest VALIDATES the methodological instinct
(wrong-barrier evaluation → phantom cohorts) — corrected by always mining on `apex15`.

**NOT tested above (damp side only):** the *admission* side of Lead 1 — 70-74 cohorts that reach
≥75+ quality on apex15 and deserve promotion — is the more promising untested half. Out of scope:
#3 regime (needs regime-state capture), #8 ichimoku-put (puts OFF), #9/#10 weekly (look-ahead blocked).

---

## Fresh open-ended miss mine on apex15 (`fresh_mine.py`, 2026-06-09)

Not the pre-selected families — scanned the **full v70 feature space** (42 cohorts, N≥80) and ranked
by harm on apex15. 75+ CALL, N=4,699, base WR 70.0% (miss rate 30%).

**Worst cohorts (most-negative z): c_trend≥70 −2.9 · c_stoch<30 −2.2 · scw_dampen-on −2.0 · vol_mag-hi
−1.6 · …  → 0 / 42 clear z≤−3.** There is **no statistically-real miss concentration in score-feature
space**. The "worst" (c_trend≥70, z−2.9) is 86% of the whole book (4040/4699) — not a discriminator.
SCW-dampened sits at only −2.0, i.e. SCW already absorbs that cohort and leaves no large residual.

**Why misses actually happen = vol PATH, not a feature cohort.** Loser anatomy (N=1,409):
- 82% of losers **never approached the +30% TP** — 26% never moved up (mfe<0.3σ), 28% partial, 28%
  close-miss; loser **median mfe15 = 0.62σ** vs TP 1.092σ.
- **77% of losers fell to the −70% SL** (mae15 ≥ 2.548σ); loser median mae15 = 3.62σ.
- Winner vs loser separation is entirely realized-path: mfe 3.06σ vs 0.62σ, mae 1.16σ vs 3.62σ.

A miss = the stock went down. That's not predictable from the entry-time technical score at the 15-day
horizon (the v69-honest "stock-selection alpha is statistically thin" result). Faint tilts exist but
all |z|<3: slightly worse = already-extended (high-trend / low-stoch / SCW-zone, which SCW already
catches); slightly better = weak-weekly (`w_adj lo` z+2.2) and low-trend pullbacks — the buy-weakness
mean-reversion edge.

**Conclusion: directional-score miss-fixing is closed for the live strategy.** No Stage-1 damp lever
survives on the funded barrier. Documented leverage remains option-pricing (skew/SVR) + portfolio/exec.

---

## Per-trade TP×SL grid — does the loser anatomy back a tighter stop? (`sl_grid.py`, 2026-06-09)

Tested the "losers blow past −70%, tune the stop" hypothesis directly. v70 75+ CALL, W=15.
SL row at the current +30% TP (nominal EV; dead-hold + velocity/collapse NOT modeled):

| SL | WR% | EV_nom | EV/bar | %SL-hit |
|---|---:|---:|---:|---:|
| −30% | 52.7 | +0.015 | 0.0047 | 46.5 |
| −50% | 63.7 | +0.013 | 0.0030 | 33.0 |
| −60% | 67.9 | +0.022 | 0.0045 | 27.0 |
| **−70% (cur)** | **70.0** | **+0.021** | 0.0039 | 23.2 |
| −90% | 72.7 | +0.024 | 0.0041 | 17.0 |

**Per-trade EV is FLAT across the entire SL range** (+0.013 → +0.024, noise) and weakly favors
*wider*, not tighter. The killer: tightening −70%→−30% drops WR **70.0%→52.7%** — you convert ~17pp of
*eventual winners* (slow-runners that recover by day-15 HOLD) into losses; the smaller per-loss exactly
offsets the lost wins, so EV doesn't move. The only thing tightening buys is **EV/bar** (faster
resolution) = the capital-velocity signal that ALWAYS appears per-trade and ALWAYS reverses under
bounded-fill MC (collapse 34–55%, documented). The **dead-hold isn't even in this grid** — it holds the
deep losers (~40% recover to −15%), so the *real* −70% economics beat the nominal shown, widening the
gap further. **−70% HOLD is statistically backed; this grid IS the backing, pointing opposite the
intuition.** Re-confirmed at MC level by apex_speed_v70 (flat TP0.30/SL−0.70 near-optimal). The only
live descendant is mcap/realized-vol-CONDITIONAL barriers (Lead #17) — vol is observable so it sidesteps
the no-entry-predictor wall — but barriers are already σ-scaled, so it needs a σ-normalization check first.

---

## σ-normalization check for mcap-conditional barriers (`sigma_norm_mcap.py`, 2026-06-09) → CLOSED

v70 75+ CALL (N=4,475 with mcap) JOIN `Stock.market_cap`. Forward excursions are in σ units, so the
test is whether mcap bins differ AFTER σ-scaling (raw % move obviously differs; σ move is the question).

| mcap bin | N | apexWR | mfe15σ | mae15σ | %hitTP | %hitSL |
|---|---:|---:|---:|---:|---:|---:|
| micro <2B | 130 | 68.5% | 2.17 | 1.82 | 76.2 | 41.5 |
| small 2-10B | 551 | 69.5% | 2.54 | 1.75 | 76.0 | 35.2 |
| mid 10-50B | 1466 | 70.4% | 2.26 | 1.72 | 73.9 | 35.1 |
| large 50-200B | 1530 | 67.1% | 2.04 | 1.86 | 72.5 | 37.8 |
| xl 200B-1T | 587 | 75.8% | 2.60 | 1.49 | 80.6 | 30.8 |
| mega 1T+ | 211 | 75.8% | 2.38 | 1.47 | 78.7 | 31.8 |

**Decision metric (micro vs large+): mfe15σ ratio 0.99, mae15σ ratio 1.05 — both ≈1.0.** In σ units the
forward-path shape is the SAME across mcap. σ-scaling already neutralizes mcap → **per-mcap barriers add
nothing** (the σ-barriers already adapt to each stock's move scale). The residual WR gap (xl/mega 75.8%
vs micro 68.5%) is **non-monotonic** (large 50-200B is *lowest* at 67.1%), is score-stage/MCD turf
(already shipped), and is NOT barrier-addressable (the σ-path is identical). The one faint barrier signal
— micro hit the −70% SL more (41.5% vs 31.8% mega) — is tiny-N (130) tail/jump risk that points back into
the HOLD≫CUT collapse wall. **Lead #17 / Priority #16 closed on the σ-normalization pre-flight.**

## 70-74 promotion/admission check (`promote_check.py`, 2026-06-09) → no selective target

apex15 bars: 75+ base 70.0% · 75-79 tier 69.8% · **70-74 band 68.1% (N=29,245)** — the band is only
1.7pp under the lowest tradeable tier (which is WHY whole-band overflow@0.035 already works). But there
is **no selective-lift target**: the cohorts that *reach* the 69.8% bar (c_ta≥70 71.2%, cwcf-on 70.3%)
have NO within-band discriminator (z+1.0 / +0.5, tiny N = noise); the cohorts WITH a clean discriminator
(z≥+2: w_adj-lo, vol_mag-lo, vol_pct-lo, mid-stoch, c_bb — the calm/weak-weekly buy-weakness fingerprint)
all sit *just under* the bar at 68.9-69.7% and are z-negative vs the 75+ population. The 70-74 band is a
smooth continuation of the quality gradient (68.1 → 69.8 → 70.0), with no hidden-quality cliff to admit
selectively. The whole-band flat overflow (shipped) is the correct tool; a score-stage selective lift has
nothing real to grab.

## Thread summary (2026-06-09): the entire Stage-1-scoring + Stage-2-barrier space is closed for v70 Apex
1. Old miss-candidate families → invalidated on apex15 (generic-vs-option trap).
2. Fresh open-ended mine → no concentrated miss reason (0/42 z≤−3); misses are vol-PATH, not feature.
3. Global SL tuning → flat per-trade EV; tightening converts eventual-winners to losses; MC-settled.
4. mcap-conditional barriers → σ-scaling already neutralizes (ratios 0.99/1.05).
5. 70-74 promotion/admission → no selective-lift target (band is smooth-gradient near-tradeable;
   whole-band overflow already shipped is the right tool).
Remaining leverage (unchanged): option-pricing (skew/SVR, partly shipped, strong direct-skew piece
data-blocked) + portfolio/execution. The directional technical score has ~no edge on the 15-day option
outcome — re-grading scores or re-tuning barriers cannot move it on the live strategy.
