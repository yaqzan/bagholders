# Component-Ensemble Verification (weatherization) — 2026-06-17

**Ask (user, /research):** apply weather-forecast verification — "measure how well the
components work *in tandem*, which signs *don't* work when combined, and any other
insightful metric a forecast scientist would derive."

**What this is:** the substrate (`experiments/skill_vs_baseline/`) verified the OVERALL score
on the funded `30dte_apex` CALL payoff (win/stop/expire = +0.30/−0.70/−0.40, 30d). This
verifies the **6 ensemble members** (trend, bb, rsi, macd, stoch, technical_alignment[=ta])
the way a forecast scientist verifies ensemble members: individual skill, diversity/redundancy,
pairwise tandem/anti-synergy, conditional (multivariate) information, reliability/resolution,
ensemble-vs-best-member, and per-window stability.

**Substrate / method.** Read-only. Ledger = all 1,762,351 v74 stored stock-days (2016+) with the
6 component columns + overall + momentum proxy (`build_ledger.py` → `weather_comp_v74_2016`).
apex CALL outcome joined via the `30dte_apex` barrier set (DuckDB mirror). Universe metrics on a
uniform 300k sample (291,788 apex-matched; unbiased for rates); funded metrics on the full ≥75 book
(12,833 apex-matched). In-sample (data end 2026-06-12 < holdout 2026-06-15). Scripts:
`build_ledger.py`, `verify_components.py`, `robustness.py`.

---

## Headline (7 forecast-verification findings)

### 1. The DOMINANT member is the LEAST skillful — and regime-HARMFUL
TREND drives `overall` most (corr **+0.72**, the largest) and generates the 75+ supply, yet on the
funded payoff it has **zero resolution** (flat 71.1–71.8% apex-WR across *every* score band),
**zero univariate skill** (ΔEV −0.05, t −0.33), **zero conditional skill** (multivariate β −0.08,
t −0.70), and is **actively HARMFUL in bear/chop** (ΔEV vs clim: **2022 −2.42, 2023 −1.81**; neutral
2021/2024/2025). Trend is a *supply / momentum-extension* axis, not a forecaster — and the extension
it loads on is exactly the −70%-stop-prone tail that fails in bear/chop. **This is the mechanistic
root of the substrate's documented bear/chop weakness.**

### 2. MACD and RSI are the genuinely skillful members; RSI is the bear-robust one
- **MACD** — most stable positive member: univariate ΔEV +0.43 (t +2.31, the only univariate t≥2),
  strongest conditional β **+0.46 (t +3.45)**, positive in 4/5 windows.
- **RSI** — positive in **every** window and strongest exactly where trend fails
  (**2021 +5.10, 2022 +4.35** ΔEV); conditional β +0.32 (t +2.52). The mean-reversion lens earns its
  keep in chop (RSI fires bullish on oversold → wins when extended-trend names are getting stopped).
- **BB** — adds conditional skill (β +0.34, t +2.86) but regime-flippy univariate (+ in 2021/2023/2024,
  − in 2022/2025).
- **STOCH** — univariate looked OK (+0.24) but **redundant in the ensemble** (β +0.09, t +0.75): it is
  +0.56 correlated with RSI, which captures it.

### 3. Technical_alignment (TA) is a SUPPRESSOR
Univariate ≈neutral (ΔWin +0.08) but **conditional on the other members strongly NEGATIVE**
(β **−0.63, t −3.59**, robust in 4/5 windows) and anti-calibrated (apex-WR: ta 70-74 = **63.0%**,
75-79 = 68.9%, vs 72% base rate). TA is ~a linear combination of macd/rsi/bb (corr +0.67/+0.59/+0.53),
so its *residual* = "agreement not explained by the actual signals" = over-extension/crowding →
*worse* payoff. The textbook suppressor variable (positive-marginal flips negative-conditional).

### 4. Effective ensemble size ≈ 3.5 of 6 (redundancy)
Corr-matrix participation ratio = **3.47**. Redundant structure: rsi≈stoch (+0.56), ta≈{macd,rsi,bb},
trend≈−stoch (−0.43). The 6 "components" carry ~3.5 members of independent information — parsimony
evidence, reinforcing the v74-lean ethos.

### 5. Anti-synergy: trend+macd is the pair that "doesn't work combined"
| pair (both ≥70) | EV both | EV best-single | tandem lift | t | per-window lift (21/22/23/24/25) |
|---|--:|--:|--:|--:|---|
| **trend+macd** | +2.29% | +2.88% (macd) | **−0.59** | −1.73 | +0.62 / −0.88 / **−4.38** / −1.09 / −1.11 (− in 4/5) |
| rsi+macd | +2.48% | +2.87% (rsi) | −0.39 | −0.43 | |
| bb+stoch | +2.44% | +2.82% (bb) | −0.38 | −0.84 | |
| … | | | | | |
| **trend+bb** | +2.86% | +1.98% | **+0.88** | **+2.92** | (significant synergy) |
| **bb+rsi** | **+4.71%** | +2.54% | **+2.17** | +1.76 | (best tandem) |

"lift = EV(both bullish) − max(EV(A-only), EV(B-only))." **trend+macd** is the most anti-synergistic:
trend's confirmation *poisons* macd's signal (robust − in 4/5 windows, −4.38 in 2023). Conversely
**trend's ONLY value is as a bb-confirmation partner / supply** (trend+bb +0.88, t +2.92), never
standalone, never with macd. **bb+rsi** is the best tandem (+4.71% both).

### 6. The ensemble BEATS its best member (the machinery IS worth it — via the joint threshold)
overall≥75 vs top-K-by-macd (matched count) on the apex payoff:
| window | overall≥75 EV | top-macd EV | ens−best | t |
|---|--:|--:|--:|--:|
| FULL | +3.17% | +0.83% | **+2.34pp** | +1.84 |
| 2021 | +4.10% | −5.23% | +9.34 | +2.62 |
| 2022 | +5.45% | −7.67% | **+13.12** | +2.82 |
| 2023 | −3.84% | +7.08% | −10.92 | −1.59 |
| 2024 | +8.99% | −0.34% | +9.34 | +2.30 |
| 2025 | +4.73% | +1.29% | +3.44 | +0.83 |

The ensemble's value is the **JOINT 75+ threshold**: requiring several *diverse* members to align
filters out the single-member-high lottery tail (the classic "consensus beats best member" principle,
and the per-trade complement to the MC finding that the score's selection is load-bearing vs momentum).
The exception is **2023 chop** (ens−best −10.92) — exactly where trend drags the conjunction into
extended names.

### 7. Reliability is non-monotonic for `overall` (85-89 = worst band) — a 2022 artifact
overall apex-WR by band: 70-74 71.8 → 75-79 **73.3** → 80-84 72.3 → 85-89 **65.9** → 90-100 **74.2**.
Reproduces the substrate's non-monotonic 70-89 flag. Funded-book apex-EV by tier confirms 85-89 is the
worst (FULL −0.37%) — but it's a **2022-bear effect** (85-89 EV: 2022 **−13.33%**, but +3.75/+5.45/+3.06
in 2021/2024/2025). High "conviction" (85-89) = high trend-extension = bear-stop-prone. Not regime-robust.

---

## Synthesis (the forecast-science picture)

The scoring ensemble is **structurally back-to-front on the funded payoff**: the member that drives the
score most (TREND) carries the least per-trade forecast skill and is regime-harmful, while the genuinely
skillful members (MACD, RSI) are minor score drivers. The "agreement" meta-member (TA) is a suppressor.
Yet the ensemble still **beats its best single member** — because its value is not any member's
discrimination but the **risk-filtering conjunction** of the 75+ threshold (several diverse members must
align), which avoids the −70%-stop lottery tail. This is fully consistent with the substrate's verdict:
the score is a **risk-shaped moderate-momentum selector**, not directional alpha — and now we know
*which members* do the shaping (macd/rsi/bb), *which provides the supply scaffold without skill* (trend),
*which is dead weight / a suppressor* (ta/stoch redundancy), and *where it breaks* (trend-driven over-
commitment in 2023-style chop).

---

## Leads (all STAGED — no production change tonight; same-day 09:30 open, "Stage > rush")

**Lead A — TA-suppressor → v75-lean candidate (Stage-1). ❌ PROBED → TESTED-NULL (dilutive reweight-trap).**
TA is a robust universe-multivariate suppressor (β −0.63, t −3.59) and ~redundant (corr 0.5-0.67 with
macd/rsi/bb), so the v74-lean methodology *looked* applicable. **The probe killed it** (see PROBE RESULT
below): re-scoring a 298-symbol stride-3 sample with `W_TA_BASE=0 W_TA_SLOPE=0` (the v74-lean "disable the
member" intervention) cut 75+ supply −11.2% AND the dropped signals were HIGHER-EV than the kept ones in
4/5 windows (Simpson: pooled looked accretive, per-window dilutive). **The universe-multivariate suppressor
is NOT a removable component** — "holding the other 5 fixed, higher TA → worse" (the regression) ≠ "remove
TA's weight from the score" (which changes which names clear 75 and drops genuine confirmation). RE-CONFIRMS
the **reweight-null** (`project_component_reweight_null.md`) on the apex predictand. Residual untested
variant (low priority, expect neutral per the reweight-null): *redistribute* TA's weight to macd/rsi/bb
(renorm, preserve supply) rather than zero it — but that is squarely the reweight-null's domain.
**The funded-book TA signal is also NON-MONOTONIC** (TA 51-52 +4.42% best, TA≥53 +2.00%, anti-correlated
−0.44 with spread/SPREAD_TILT) → there is **no clean Stage-3 TA-tilt** either. TA is dead as an actionable lever.

**Lead B — regime-conditioned bear/chop defense, now mechanistically located (Stage-1, hard).** The
bear/chop weakness is specifically **TREND** (−2.42/−1.81 in 2022/2023) and **trend+macd** (−4.38 in 2023);
**RSI is the bear-robust member** (+5.10/+4.35). The mechanism: regime-conditionally *de-weight trend /
up-weight rsi* in bear/chop. Feeds the substrate's #1 MC-grounded lead. Hard because trend is supply-load-
bearing and regime-conditioning is a known-difficult area (regime-direction flips are documented-null).

**Lead C — 85-89 cascade trim (Stage-3, lower conviction).** Pooled worst band (−0.37%) but
2022-concentrated (−13.33% there, + in 3/5 windows). The v74 cascade gives 85-94→0.15 (TOP); trimming the
85-89 sub-band is a DD-shaping (not return) trade that needs MC (the dead-hold may rescue the 85-89
−70%-stops). Freshly v73-retuned cascade → low priority; STAGE only.

---

## PROBE RESULT — TA-retire (W_TA=0) is a DILUTIVE reweight-trap (2026-06-17, tested-NULL)

Two ScoreSimulator re-score arms on the SAME 298-symbol stride-3 sample, 10y (queue #240 baseline / #241
`W_TA_BASE=0 W_TA_SLOPE=0`; `probe_analyze.py` apex-EV decomposition vs `apex_ev_of_parquet.py`):

| window | COMMON EV (N) | REMOVED EV (N) | read |
|---|--:|--:|---|
| FULL | +4.08% (3,836) | +3.09% (495) | pooled "accretive" — **Simpson artifact** |
| 2021 | +3.52% (568) | **+6.05%** (81) | dilutive (dropped better) |
| 2022 | −0.41% (363) | **+3.33%** (45) | dilutive (dropped the POSITIVE ones in the bear) |
| 2023 | +6.05% (124) | +4.07% (27) | mildly accretive (thinnest window) |
| 2024 | +9.97% (330) | **+10.85%** (47) | dilutive |
| 2025 | −1.29% (279) | **+9.74%** (38) | dilutive (dropped the BEST ones) |

75+ supply **4,396 → 3,905 = −11.2%**. The retired signals are HIGHER-EV than the kept book in **4/5
windows**; the pooled +3.09<+4.08 "accretive" is a 2024-bull-weighted Simpson's-paradox aggregate.
**Conclusion: zeroing TA's weight drops the wrong names** (TA carries genuine confirmation that the other
5 components' weighted sum doesn't capture; removing its weight changes which names clear 75 and sheds
real signal). A universe-multivariate suppressor (β<0) is NOT a removable component — the regression's
"hold-others-fixed" statement ≠ the score's weight-removal operation. RE-CONFIRMS the reweight-null on the
apex predictand. **No v75 ship.** The probe (~1h, 2× sim arms) earned its keep by killing the lead.

## What did NOT pan out as a ship tonight (honest)
- **No clean Stage-3 sizing lever.** The component-disagreement/agreement axis is THIN on v74-lean:
  funded 75-79 spread-skill is only +0.14pp (substrate had +1.76pp on v73 — SPREAD_TILT's per-trade
  premise is weaker on the lean substrate; flag for its watch metric), and TA's funded signal is
  non-monotonic + overlaps spread.
- **The primary lead (TA-suppressor v75-lean) was PROBED → dilutive reweight-trap (above).** The remaining
  leads (regime trend/rsi defense, 85-89 trim) are both heavier (Stage-1-regime / MC) and were correctly
  not attempted on a same-day open. The deliverable is the diagnostic scorecard; no actionable ship tonight.

## REGIME-CONDITIONED PAIRWISE TANDEM — the grid + "is there alpha" (2026-06-17, `tandem_by_regime.py`)

The pooled Report 4 + per-year-trend+macd-only Report A were incomplete; this is the full 10-pair grid
× market environment (VIX band + year), both the forecaster lens (universe) and the alpha lens (funded 75+).

**Tandem lift (EV(both≥70) − max single) by VIX band — the robust structure:** component agreement
DEGRADES with VIX and INVERTS in panic. Calm → pairs synergize (bb+macd +1.62, rsi+macd +2.03, trend+bb
+0.96); panic ≥28 → nearly all sharply anti-synergistic (trend+macd −3.43, bb+macd −4.89, macd+stoch −3.70,
trend+stoch −4.76). trend+macd is anti-synergistic in 4/5 YEARS (2021-bull the exception) AND monotonic in
VIX — the most robust pair result. By-year echoes it: 2024-bull synergizes, 2022-bear/2023-chop most negative.
Mechanism = the component-lens view of trend's regime-harm: when everything confirms in a stressed tape,
it's an over-extended name that gets −70%-stopped.

**Alpha lens (funded 75+):** ONE cell with a tradeable shape — **trend+macd panic≥28: both-confirm apex-EV
+0.03% (N354) vs split +7.80% (N391), t −2.43** (corroborated: trend+bb elevated t−2.05, panic t−1.83 —
same direction). I.e. in high VIX, 75+ signals where trend AND macd both strongly confirm underperform
partial-confirm signals.

**Verdict — NOT demonstrated alpha; a stage-and-MC-gate candidate at best:** (1) heavy overlap with the
shipped **RXDD** VIX-band call dampener (orthogonal in *mechanism* — RXDD is agreement-blind, this cuts the
trend+macd-confirm sub-cohort within the band — so a possible *refinement*, not new alpha); (2) **crash-
artifact risk** (the documented trap): the substrate says panic entries are net mean-reversion winners, and
the split cohort IS +7.80% (winner) while both-confirm is +0.03% (FLAT, not a loser) → a tilt sheds flat-EV
panic participation RXDD deliberately keeps; (3) thin + multiple-comparison (1 of ~40 cells; direction
corroborated but magnitude not ship-grade). Needs Stage-3 MC (collapse=0, DD↓, orthogonality vs RXDD/SVR) —
couldn't fit a same-day open. Staged in NEW_LEADS. The grid itself is the deliverable: where the ensemble's
tandem-confirmation helps (calm/bull) vs hurts (stress/panic).

## Caveats
- In-sample (no forward window yet; cutoff 2026-06-15 > data end). v1 apex EV map (no dead-hold/theta in
  the EV — the relative member ranking is robust, but the funded 85-89 dip especially may shift under the
  dead-hold). Per-window N is small (read signs + stability, not precision). 30dte_apex CALL payoff only.

## Artifacts
- `experiments/weather_components/{build_ledger,verify_components,robustness}.py`
- cache: `weather_comp_v74_2016` parquet (12.6 MB, 1.76M rows); `30dte_apex` barrier set (existing).
