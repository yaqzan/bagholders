# Event vs Cyclical Noise Decomposition — FINDINGS

**Date:** 2026-07-25 · **Substrate:** v74 (`f9fb7b934`), funded 75+ CALL ledger, apex15 predictand,
N=5,854 (5,839 usable), 2021-01-04..2026-06-15. Holdout `CALIBRATION_CUTOFF_DATE=2026-06-15` enforced.
**Contract:** `PREREGISTRATION.md` (bars locked before any outcome was read; amendments A1-A7 all dated
pre-run for their leg). **Harness:** `mine.py` (queue task #77, 14.2s), `confound_pej.py`,
`score_contamination.py`, `tape_*.py`.

**Harness validity:** counterfactual re-walk reproduces the stored ledger labels **5854/5854 = 100.000%**
(bar 99%); the A6 pooled anchor matches the orchestrator's independent audit exactly to 4 dp
(WR 0.7009, EV +0.0208, bands 0.7010/0.6968/0.7065/0.7340).

---

## Headline

**No ship.** Five pre-registered legs return null; one cell is statistically real but fails three of
its five locked kill-tests and is PARKED to the Dec-2026 OOS unlock. The run's durable output is a
**measured noise constant** and the closure of the last escape hatch for the score-gradient question.

---

## 1. The noise constant (the user's Idea A, answered)

The proposal was: separate news-driven from cyclical outcomes, measure the algorithm on the clean
part, and carry the event component as an uncertainty constant.

Both halves were measured. They disagree, and the disagreement is the finding.

| quantity | value | 95% CI |
|---|---|---|
| `nu_var` — share of holding-period **return variance** from idiosyncratic jump days | **0.3188** | [0.2549, 0.3857] |
| R^2 of jump-day return on total return | 0.3079 | [0.2182, 0.4104] |
| EVENT population share (K=3.0) | 0.1437 | [0.1334, 0.1535] |
| **`nu_flip`** — share of win/stop/expire **labels** that change when jumps are removed | **0.0339** | [0.0291, 0.0388] |

**Jumps carry ~32% of return variance but flip only 3.4% of outcome labels.** The barrier-based
predictand is far more robust to idiosyncratic news than the return series is. The counterfactual
confusion matrix is dominated by the diagonal (win->win 4,021, stop->stop 1,234, expire->expire 386)
with a small net rescue (stop->win 118 vs win->stop 71).

Consequence for the honesty layer: `N_eff = N(1 - nu_flip)` = 5,641 from 5,839, and the headline WR
interval widens from [68.96, 71.31] to **[68.94, 71.33]** — a ~0.02pp widening. K-ladder: `nu_flip`
= 0.0577 (K=2.5) / 0.0339 (K=3.0) / 0.0152 (K=4.0).

**Verdict on the idea: conceptually correct, empirically small.** A news-noise inflation factor on
our uncertainty is *not* a material correction, and reporting one would overstate its own importance.
This is a real robustness result: it says our win-rate estimates are NOT being meaningfully
destabilised by unforecastable news, which is the opposite of the intuitive worry.

### What events actually change

| subset | N | share | WR% | EV |
|---|---|---|---|---|
| ALL | 5,839 | 100% | 70.15 | +0.0214 |
| EVENT | 839 | 14.4% | **70.08** | **+0.0055** |
| CYCLICAL | 4,868 | 83.4% | **70.11** | **+0.0241** |
| MIXED | 132 | 2.3% | 71.97 | +0.0220 |

**Hit rate is identical (70.08 vs 70.11); only the payoff differs.** Events do not make the algorithm
miss more often — they make the misses cost more. That is a loss-composition effect, not a
forecast-skill effect, and it is the cleanest one-line summary of what news does to this system.

---

## 2. The primary hypothesis — FALSIFIED (this was the run's best shot)

**H:** the documented "score gradient above the gate is per-trade-inert" verdict (`verify_value`,
2026-06-24) was measured on the POOLED outcome mixture; if jump variance drowns a real gradient, the
CYCLICAL subset should reveal it.

**Result on CYCLICAL (N=4,868):**

| bar | required | measured | |
|---|---|---|---|
| B1 Spearman(overall, apex_win), CR1 t | \|t\| >= 3.0 | 0.0055, **t = +0.40** | FAIL |
| B2 band-EV spread (75-79 vs 80+) | >= 3.0pp | **+0.91pp**, t = +0.66 | FAIL |
| B3 year sign-stability | Psign >= 0.80 | +1.64 / −2.20 / −0.25 / −2.12 / +5.64 / +6.81 = **3/6** | FAIL |
| B4 N per band | >= 150 | met on the 2-band contrast | pass |

All three substantive bars fail. **The gradient is inert on the clean subset too.** The inertness of
the score above the gate is therefore NOT a signal-to-noise artifact — it is a property of the score.
This closes the last plausible escape hatch for that question and independently re-confirms
`verify_value` on a decomposed predictand it had never been tested against.

---

## 3. The checklist form (the user's Idea B, answered) — NULL

**H:** score by a binary checklist of favorable conditions; more checks = more confidence.

`n_checks` = count of {trend, bb, rsi, macd, stoch, technical_alignment} at or above 70 (ladder
{60,70,80} run; primary 70 reported here at threshold 60 for the fullest ladder):

| n_checks | N | WR% | EV |
|---|---|---|---|
| 1 | 147 | 67.35 | −0.0061 |
| 2 | 1,049 | 70.45 | +0.0214 |
| 3 | 3,330 | 69.88 | +0.0203 |
| 4 | 696 | 71.41 | **+0.0361** |
| 5 | 344 | 72.09 | +0.0340 |
| **6 (unanimous)** | 272 | **68.01** | **−0.0055** |

- **C1** monotone EV, CR1 t = **+0.10** (bar 3.0) — FAIL
- **C2** must beat the continuous score's own resolution by >= 1.0pp — measured **+0.92pp** pooled,
  **−0.32pp** on CYCLICAL — FAIL
- **C3** year stability — +1.92 / +4.62 / +6.79 / **−5.03** / +1.70 / **−4.34** — FAIL

**The premise is empirically inverted: unanimity is worse.** 6-of-6 agreement (EV −0.0055) is worse
than 4-of-6 (+0.0361) and worse than the base. The mechanism is already documented: the components
are correlated (effective ensemble size **3.5 of 6**), `technical_alignment` is a multivariate
suppressor, and `trend`+`macd` are anti-synergistic (`weather_components`, 2026-06-17). Requiring all
six to agree therefore does not select higher-quality signals — it selects a particular crowded,
extended state. A checklist is a **re-expression of the gate, not an improvement on it**, which is
what the pre-registration recorded as the expected outcome.

---

## 4. The one real cell — PARKED, not shipped

`prior_earn_jump_pct` = the symbol's most recent realized earnings-gap magnitude before entry
(terciles at 3.11% / 7.43%). This was the only cell in the entire run to clear |t| >= 3.0.

| tercile | N | WR% | EV | CR1 t (EV) | CR1 t (WIN) |
|---|---|---|---|---|---|
| T1 low | 1,859 | 71.33 | +0.0260 | +0.78 | +1.57 |
| T2 mid | 1,858 | 72.55 | +0.0417 | +2.71 | +3.01 |
| **T3 high** | 1,857 | **65.81** | **−0.0100** | **−3.33** | **−4.41** |

vs rest: dEV **−4.38pp**, dWR **−6.13pp**; negative in 5 of 6 years (2024 the lone flip); N>=150 every
year. Ex-ante and PIT-computable.

### The A7 kill-tests — 3 of 5 FAIL

| test | requirement | measured | |
|---|---|---|---|
| **E4 mechanism** | stated: stop-rate rises (jump reaches the 2.548-sigma stop) | stop **+0.29pp** (flat), win −6.13pp, **expire +5.85pp** | **FAIL** |
| **E2 score-band** | same sign in 75-79 and 80+ | 75-79 **−6.20pp** (t −4.12); 80+ **+0.32pp** (t +0.14) | **FAIL** |
| **E1 vol control** | dEV <= −2pp with \|t\|>=2.5 in >=2 of 3 vol terciles | −9.08 (t −2.36) / −3.22 (t −1.54) / −6.60 (t −3.28) = **1 of 3** | **FAIL** |
| E3 SVR orthogonality | \|rho\| < 0.7 | rho **−0.21** | PASS |
| E5 PIT audit | no leakage | 5,539 reported+strictly-prior, **leakage 0.0000** | PASS |
| E6 supply cost | reported | T3_high = **33.3% of supply**, rising 23.4% (2021) -> 43.6% (2026) | expensive + drifting |

**E4 is the decisive failure.** The pre-registered mechanism said jumpy names should hit the *stop*.
They do not — the stop rate is flat (23.24% -> 23.53%). What actually happens is that wins convert
into **expiries** (+5.85pp): these names go nowhere. The stated physical story is falsified, and A7
fixed in advance that a falsified mechanism means **park, do not ship**. Writing a new story around
the surprise is exactly the failure mode that bar exists to prevent.

**E2 compounds it:** the effect exists only in the marginal 75-79 band and is *exactly zero* at 80+.
A general risk factor should not switch off above a score threshold; an interaction with the gate is
the more parsimonious reading. Combined with the 2024 sign flip, the 33% supply cost, and a cohort
share that nearly doubles across the sample (a drifting prevalence, i.e. regime composition rather
than a stable factor), this has the profile of something that fails out of sample.

**PARKED to the 2026-12-15 OOS unlock, bars as locked in A7.** Re-read then, unchanged.

### Why it underperforms (diagnostic, no verdict leg)

The score-contamination hypothesis — that a violent gap inflates the technicals and pushes a name
across the gate — is **rejected**: `overall` is statistically identical between cohorts (78.35 vs
78.29, t +0.60; in 75-79, 76.55 vs 76.55, t +0.12), and the two score distributions are
indistinguishable (same p25/p50/p75, same share at 75-77 and at 80+).

But **one component differs sharply: `c_trend` +4.14 (t +9.31)** pooled, +3.46 in 75-79, +5.85 at
80+. Violent-earnings names earn their 75+ qualification disproportionately through TREND — the
member that `weather_components` measured as the dominant score-driver (corr +0.72 with `overall`)
with **zero per-trade resolution** and documented regime-harm in bear/chop. So the cohort is not
mis-scored; it is scored by the *unskilled* member. That is a coherent, evidence-consistent reading
and it is offered as diagnosis only.

Interaction noted for the park list (discovered outside the pre-registered loci, therefore
Dec-2026-only per §4 of the prereg): a recent jump is GOOD for clean names (EV +0.0469) and BAD for
chronically-jumpy names (EV −0.0278).

---

## 5. Other legs — all null

- **A5 pre-entry jump contamination of the signal** ("filter news-driven events out of scouting"):
  ANY-jump-in-trailing-10d dEV −1.12pp, **CR1 t −0.87**; POS-jump −1.58pp, t −1.13; NEG-jump +0.43pp,
  t +0.19. Year signs mixed (2021 +1.77 / 2022 −5.14 / 2023 −9.30 / 2024 +5.78 / 2025 −2.47 /
  2026 −5.80). Bars D1-D3 all fail. D5 collinearity with the parked `peak_fakeout` texture cell is
  clear (rho 0.20 parabolic / 0.52 climax_day, bar 0.7). **The user's directional intuition is
  backwards for this universe**, as the pre-registration predicted it would be: a recent jump is
  mildly GOOD, consistent with the documented buy-weakness/mean-reversion law.
- **T4 ex-ante earnings-spanning as a Stage-3 sizing lever:** NULL. Full record in
  `FINDINGS_T4_exante_earnings.md`. The −0.3794 EV gap on the naive flag was a hold-duration
  tautology; the honest ex-ante flag gives **+0.0118** (sign flip, 97% collapse), dd_conc 1.19,
  4/5 years positive.
- **`d_earn_in_window`** (scheduled earnings in the nominal hold): WR 72.67% vs 69.75%, EV +0.0323 vs
  +0.0196, CR1 t_EV +0.65 — mildly positive, not significant. Consistent with `earnboost_honest`
  (thin premium) and with EARN_BOOST's retirement as neutral-EV in the v74 lean.
- **`jump_rate_252`:** monotone-ish but weak (T1 +0.0146 / T2 +0.0227 / T3 +0.0345, best t +1.11).

---

## 6. Methodological output (promote to the trap registry)

**A cohort flag defined over a REALIZED holding window is outcome-conditioned and manufactures a
large fake effect whose sign tracks hold duration.** Diagnostic tell: an effect ~10x anything else in
the system (here −0.38 EV against a +0.0375 base) that collapses when the window is redefined from
the realized exit to the nominal/scheduled hold. Always define event-window flags from the ENTRY date
plus the NOMINAL hold, and report the hold-duration composition of both cohorts as standing proof the
confound was addressed.

**Second, softer lesson:** pre-registering the *mechanism* alongside the *effect* is what made the
`prior_earn_jump_pct` adjudication clean. The effect was real (t −3.3/−4.4, PIT-clean, 5/6 years);
without a locked mechanism prediction, the surprising expire-not-stop decomposition would have been
trivially re-narrated into a supporting story rather than counted as a falsification.
