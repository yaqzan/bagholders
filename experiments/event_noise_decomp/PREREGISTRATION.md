# Event-vs-Cyclical Noise Decomposition — PRE-REGISTRATION

**Date:** 2026-07-25 · **Author:** FABLE (architect) · **Status: LOCKED before any outcome column is read.**
**Origin:** user directive 2026-07-25 — *"filter out the news driven events from our scouting and keep it
as a noise constant... this way we extract out the noise and measure how much the algorithm works and then
apply a noise constant to the uncertainty."* Plus the companion idea: *"instead of combining scoring of
different indicators, score based on a binary checklist of favorable positions."*

Substrate: v74 (`f9fb7b934`), funded 75+ CALL signals, `30dte_apex` predictand barrier.
Holdout lock: `CALIBRATION_CUTOFF_DATE = 2026-06-15` enforced via `experiments/_holdout.py`.

---

## 0. Why this is not a re-run of a closed axis

Three closed verdicts sit near this work; each answered a **different** question:

| Closed result | What it measured | Why this study is distinct |
|---|---|---|
| `verify_value` (2026-06-24): "score gradient above the 70/75 gate is per-trade-inert on every axis" | Skill measured on the **POOLED** outcome distribution | Pooled outcomes are a MIXTURE of forecastable dynamics and unforecastable idiosyncratic jumps. An inert gradient on the mixture is consistent with a real gradient on the forecastable subset drowned by jump variance. **Nobody has decomposed the predictand before measuring skill on it.** |
| `weather_components` (2026-06-17) + reweight nulls (G35/G43) | Alternative **weightings** of the same 6 members | The checklist form is a different **functional form** (count-of-conditions, not weighted sum), and it is tested here on the decomposed predictand, not the mixture. |
| `peak_fakeout` (2026-07-15): earnings-proximity null | Earnings proximity as a **fakeout discriminator** at the score gate, pooled | Here earnings is one input to an **ex-ante event-RISK sizing** cohort (Stage-3), and separately a **classifier for skill measurement** (Stage-0 diagnostic). Different stage, different estimand. |

Weather-forecasting framing (this is the ethos this repo already adopted): a skilled forecaster does not
claim skill on the convective thunderstorm. They forecast the **synoptic-scale** signal, verify skill
there, and report the convective component as **irreducible uncertainty**. This study is that split.

## 0b. The honesty firewall (the single most important rule here)

**Event status is an EX-POST outcome attribute.** It is NOT knowable at entry. Therefore:

- Any finding of the form *"the score works better on trades that turned out not to jump"* is a
  **DIAGNOSTIC** result about irreducible noise. **It is NOT tradable and must never be reported as an
  edge.** This is the G27 trap (win-set correlated with trigger-set) wearing a new hat.
- A **tradable** claim requires an **ex-ante proxy** for event risk, built only from information
  timestamped at or before the entry close, and it must reproduce a stated fraction of the ex-post
  effect (bar B5 below).
- The two are reported in physically separate sections of FINDINGS.md and must never be merged.

---

## 1. Definitions (locked)

### 1.1 Idiosyncratic return and jumps (point-in-time)

For symbol s and trading day t:
- `beta_s(t)` = OLS slope of `r_s` on `r_SPY` over the trailing **120 trading days ending at t-1**.
  Requires ≥ 80 valid bars; else the row is `beta_unavailable` and excluded from classification
  (reported as a coverage number, never silently dropped).
- `r_idio_s(t) = r_s(t) - beta_s(t) * r_SPY(t)`   (simple close-to-close log returns)
- `sigma_idio_s(t)` = stdev of `r_idio_s` over the same trailing 120d window ending at **t-1**.
- **Jump day**: `|r_idio_s(t)| >= K * sigma_idio_s(t)`.
  Primary **K = 3.0**. Pre-registered robustness ladder: K in {2.5, 3.0, 4.0}. The primary verdict
  is read at K=3.0; the ladder is reported for stability, never for cell-shopping.

All windows end at t-1 — no same-day information enters beta or sigma. Trading-day indexing is derived
**empirically** from observed bars (a date is a trading day iff >= 50 distinct symbols have a bar), per
G50; the static `trading_calendar` holiday table is used only to extend past OHLC coverage, with an
overlap-agreement assert.

### 1.2 Holding window and trade classification (ex-post)

Holding window `W = [entry_date + 1, exit_date]`, where `exit_date` is the earlier of the
`30dte_apex` barrier touch and the 30-calendar-day expiry, taken from the barrier outcome record.

- `J` = set of jump days in W. `jump_ret = sum of r_idio over J`. `total_ret` = log return over W.
- **EVENT** : `|J| >= 1` AND `|jump_ret| >= 0.5 * |total_ret|`
- **CYCLICAL** : `|J| = 0`
- **MIXED** : `|J| >= 1` AND `|jump_ret| < 0.5 * |total_ret|`

Primary contrast is **EVENT vs CYCLICAL**. MIXED is reported but is not a verdict cell.

### 1.3 Ex-ante event-risk proxy (the only tradable form)

Built exclusively from data timestamped <= entry close:
- `d_earn_in_window` : a scheduled earnings date falls within [entry+1, entry+30cd]. Source =
  `EarningsDate`; **only rows whose announcement was known before the entry date may be used** — if
  the table cannot prove announcement-time priority, the feature is flagged `pit_unverified` and the
  entire ex-ante track is reported as such (a stated limitation, not a silent assumption).
- `jump_rate_252` : count of jump days in the trailing 252 trading days ending t-1, per symbol.
- `iv_rv` : option-implied over realized vol at entry, where option panel coverage exists
  (2025-02+ MySQL `option_prices`, plus the Polygon panel back to 2022-08). Coverage-limited; used
  only as a supplementary axis, never as the sole gate.

---

## 2. Metrics (locked)

- **M1 — Payoff:** apex option-EV under the funded map (win +0.30 / stop −0.70 / expire −0.40) and
  WR, per subset.
- **M2 — The noise constant:** (a) share of total outcome variance attributable to jump days;
  (b) `R^2` of `jump_ret` on `total_ret`; (c) the EVENT population share. Reported as a single
  headline number `nu` = fraction of funded-outcome variance that is idiosyncratic-jump driven,
  with a bootstrap CI (date-block bootstrap, 1000 draws).
- **M3 — Gradient skill within 75+:** Spearman(`overall`, apex outcome) and EV by band
  {75-79, 80-84, 85-89, 90+}, computed **per subset**, with **date-clustered CR1** standard errors
  (naive binomial z is banned — G50).
- **M4 — Skill vs baselines:** apex-EV vs climatology (subset base rate) and vs momentum
  (trailing 20d return rank), per subset, reusing the `skill_vs_baseline` scorecard logic.
- **M5 — Checklist form:** `n_checks` = count of the 6 stored components at or above a locked
  threshold (primary 70; ladder {60, 70, 80}); its EV monotonicity, its CR1 t, and the 2x2 of
  `n_checks` (hi/lo) x `overall` band for any orthogonal cell.

---

## 3. Pre-registered bars

### 3a. T2 — "the clean subset has real gradient skill" (DIAGNOSTIC claim)
ALL of:
- **B1** within-CYCLICAL Spearman(score, apex outcome), date-clustered |t| >= 3.0
- **B2** monotone band-EV spread >= 3.0pp between top and bottom 75+ band on CYCLICAL, top band above
  the pooled base
- **B3** sign-stable in >= 4 of 5 calendar-year windows (Psign >= 0.80)
- **B4** N >= 150 in every band used

### 3b. T2 — "...and it is TRADABLE" (SHIP-eligible claim)
B1-B4 **and**:
- **B5** an ex-ante proxy (section 1.3) reproduces >= 50% of the CYCLICAL-vs-EVENT EV gap, with its
  own date-clustered |t| >= 3.0 **and** no year-level sign flip (G26).

Failing B5 while passing B1-B4 = a genuine robustness/uncertainty finding, explicitly **NOT an edge**.

### 3c. T3 — checklist form (user Idea B)
To be called a real improvement, `n_checks` must, on out-of-fold data:
- **C1** show monotone apex-EV in `n_checks` with date-clustered |t| >= 3.0, **and**
- **C2** beat the continuous `overall` gradient's own resolution on the same rows by >= 1.0pp EV
  spread (i.e. it must not merely re-express the gate), **and**
- **C3** hold in >= 4 of 5 year windows.
A checklist that only reproduces the 75-gate is a **re-expression, not an improvement** — that is the
expected outcome and will be reported as such.

### 3d. T4 — ex-ante event-risk Stage-3 sizing lever
Standard house Stage-3 path, no relaxation:
- low-EV **and** high dd-concentration cohort, coinciding, on the DD-active subset (dd >= DD_MIN, G21)
- survives the **all-shipped-levers-off slice** (F3F/RXDD/SVR/MWDD/TVDD/BDIV all inactive) — G21/G23
- per-lever orthogonality 2x2 vs the most-overlapping shipped lever; redundancy tell = the candidate's
  low-EV **inverts to good** where that lever is OFF (G44)
- sign-stable across reversal AND persistent years (G26)
- then B (N=100 x 6w) -> C (N=300 x 8w incl 2020_crash) -> D (N=500 x 8w) with Stage-3 T1-T7,
  collapse = 0 on every window.

---

## 4. Multiple-comparison discipline

Independent loci are counted, not raw cells (G49b). Nested conditioning states duplicate rows and
WR/EV outcomes mirror, so a raw "N cells at z>=3" count is not N findings. The pre-registered primary
loci are exactly: {T2 gradient on CYCLICAL}, {T3 n_checks}, {T4 ex-ante cohort}. Everything else in
the run is descriptive. Any cell discovered outside these loci is a **Dec-2026 OOS park candidate**,
never a same-run ship.

## 5. Amendment log

Amendments must be dated and recorded here BEFORE any outcome column is read for the amended leg.

**A1 — 2026-07-25, pre-run (substrate correction).** The funded ledger
(`.cache/trend_ma_lattice/ledger_v74_full.parquet`, N=5,854, 2021-01-04..2026-06-15) labels outcomes on
the **apex15** barrier (`APEX_UP=1.092σ`, `APEX_DN=2.548σ`, `HOLD_W=15` trading days, `MAXW_CALDAYS=20`),
not a 30-calendar-day hold. Section 1.2's holding window is therefore redefined as
`W = [entry_date + 1, entry_date + fwd_caldays]` using the ledger's own realized `fwd_caldays` column.
No other change; the barrier is the repo's funded predictand either way.

**A2 — 2026-07-25, pre-run (earnings PIT status, declared not assumed).** `earnings_dates` is a CURRENT
yfinance-sourced snapshot: `EarningsDate.build()` deletes ghost rows and reassigns dates, and
`effective_date` is recomputed on every save. The *existence* of a quarterly event ~90d out is knowable
in advance; the *exact date* for events >2-3 weeks out is revision-prone. Consequence, locked now:
the earnings feature is admissible as a **cohort label** for the diagnostic track, and the ex-ante
(tradable) track carries the standing flag `pit_unverified` for its earnings leg. A tradable claim
resting on earnings timing alone is therefore **not** available from this run; it would require a
PIT re-derivation of the calendar. We use `effective_date` (AMC-shifted), ghost-filtered — an
improvement over `peak_fakeout`, which used raw `.date`.

**A3 — 2026-07-25, pre-run (relationship to two adjacent documented nulls; cited, not evaded).**
- *Earnings proximity as a discriminator* on THIS ledger is CLOSED NULL (`peak_fakeout` F9,
  best z 1.84; known-issues.md:1069). This study does **not** re-test it. Days-to-earnings enters
  only as one input to an ex-ante event-RISK cohort (§1.3) and as a descriptive cohort label; the
  primary partition variable is **realized idiosyncratic jump magnitude**, which is a different
  estimand.
- *Gap/jump magnitude as an entry filter* is CLOSED NULL (`entry_timing_v71`; known-issues.md:1080) —
  every gap filter converges to ~71.5% WR because the σ-anchored TP moves with the entry price. That
  null is about **filtering on a pre-entry gap**. This study partitions on **jumps realized DURING the
  hold** to measure skill, which the same source explicitly records as a live 19.6pp outcome
  discriminator. Distinct framing, distinct estimand — and the honesty firewall (§0b) prevents the
  diagnostic from being smuggled back into a filter claim.

**A5 — 2026-07-25, pre-run (NEW LEG: pre-entry jump contamination of the SIGNAL — "filter the
news-driven events out of our scouting").** The user's directive has a second, distinct reading that
neither T2 nor T4 covers: *was this signal itself manufactured by a recent idiosyncratic jump?* A
name that gapped +15% on news carries inflated trend/RSI/MACD and can clear the 75 gate on an
unrepeatable event rather than on cyclical dynamics. This is a **score-validity** question, and it
is a distinct estimand from the two adjacent closed results:
- `entry_timing_v71` closed *gap filters as an ENTRY-PRICE policy* — every threshold converges to
  ~71.5% WR because the sigma-anchored TP moves with the entry price, i.e. **the entry-day gap is
  priced in by 09:30**. A jump that landed 5-20 trading days BEFORE entry is not an entry-price
  effect at all; it is a question about whether the score's inputs are contaminated.
- `peak_fakeout` closed peak-STATE and most texture features, with the climactic-acceleration
  TEXTURE cell explicitly PARKED (not closed) for the Dec-2026 OOS unlock. The feature defined here
  is jump-attribution from the beta-residual decomposition, not the parked texture cell; if the two
  turn out to be near-collinear (|rho| >= 0.7) this leg is ABANDONED to the parked cell rather than
  re-litigated, and that is recorded as the outcome.

**Feature (locked):** `pre_jump_k_n` = 1 if any jump day (same K-sigma idiosyncratic definition,
primary K=3.0) occurred in the **n trading days ending at entry**, for n in {5, 10, 20}; primary
**n=10**. Also `pre_jump_signed_ret` = the signed sum of idiosyncratic returns on those jump days
(a +15% news pop and a −15% news crash are different animals and must not be pooled).

**Bars (locked) — a contamination filter is only real if ALL of:**
- **D1** apex-EV of the contaminated cohort is below the uncontaminated base by >= 3.0pp with
  date-clustered |t| >= 3.0
- **D2** sign-stable in >= 4 of 5 calendar years (Psign >= 0.80), reversal AND persistent years
- **D3** N >= 150 in the contaminated cohort in every year used
- **D4** the effect survives controlling for `overall` (the score band) — if contamination merely
  proxies "high score", it is not a filter, it is the gate re-expressed
- **D5** not collinear with the parked `peak_fakeout` texture cell (|rho| < 0.7)

Expected outcome per the honest prior: the buy-weakness law (G19/G3) says positive jumps in our
universe tend to mean-revert INTO our calls, so the likely sign is the opposite of the user's
intuition. That expectation is recorded now so a null cannot be re-narrated later.

**A6 — 2026-07-25, pre-run (band-power fallback, fixed BEFORE any subset result is seen).**
Independent audit of the funded ledger (N=5,854, base apex-WR 0.7009, base apex-EV +0.0208) gives
band counts 75-79: 4,267 · 80-84: 1,217 · 85-89: 276 · 90+: 94. Any subset partition therefore
CANNOT satisfy bar B4 (N >= 150 per band) at 85-89 or 90+. This is a power fact about the substrate,
not a result, so the fallback is fixed now:

- The **primary** B2 contrast collapses to **two bands: 75-79 vs 80+**, which retains N >= 150 per
  side in any partition down to a ~25% subset share.
- The 4-band ladder is still reported, marked `underpowered` wherever N < 150, and is **descriptive
  only** — it may not carry a verdict leg in either direction.
- B1 (Spearman with clustered t) and B3 (year sign-stability) are unchanged; they use all rows and
  do not depend on banding.

Recorded pre-outcome so that neither a pass nor a fail can later be attributed to band choice.
For the same reason the pooled-baseline numbers above are logged here as the fixed comparison
anchor: **base WR 0.7009, base EV +0.0208, pooled band WRs 0.7010 / 0.6968 / 0.7065 / 0.7340**
(the flat, inert gradient that `verify_value` reported and that this study re-tests per subset).

**A7 — 2026-07-25, post-primary-run, PRE-confound-run (the one surviving cell + its kill-tests).**
The full mine returned exactly one cell clearing |t| >= 3.0 among the pre-registered ex-ante loci:
`prior_earn_jump_pct` T3_high (the symbol's most recent realized earnings-gap magnitude, top
tercile, > 7.43%): N=1857, WR 65.81% vs base 70.15%, EV −0.0100 vs base +0.0214 (dEV −3.14pp),
CR1 t_EV −3.33 / t_WIN −4.41, negative in 5 of 6 years (2024 +0.083 the lone flip), N>=150 every
year. The T-ladder is non-monotone: low +0.0260 / mid +0.0417 / high −0.0100 — the top tercile is
the outlier, not a gradient.

**Candidate mechanism (stated before testing, so a null cannot be re-narrated):** the apex barrier
is sigma-anchored and therefore assumes DIFFUSIVE risk. A name that routinely gaps 8-15% on
earnings carries the same realized sigma in a jump-dominated rather than diffusion-dominated form,
so a single print can reach the 2.548-sigma stop that diffusion would take weeks to approach. If
true, this is a barrier/instrument mis-specification for jumpy names, not directional alpha — which
also predicts the effect should be LARGER on the stop leg than on the win leg.

**Kill-tests (locked now; ALL are reported unconditionally, pass or fail — selecting among them
after the fact is prohibited):**
- **E1 realized-vol control.** The effect must survive within realized-volatility terciles. If
  `prior_earn_jump_pct` is merely a high-vol proxy, it dies here. Required: dEV <= −2.0pp with
  |t| >= 2.5 in at least 2 of 3 vol terciles, and no vol tercile showing a POSITIVE dEV >= +2.0pp.
- **E2 score-band control.** Must survive within the A6 2-band split (75-79 and 80+) with the same
  sign in both.
- **E3 orthogonality vs the shipped stack.** Correlation with `semivol_r` (SVR's feature) must be
  |rho| < 0.7, and the effect must persist in the all-shipped-levers-off slice (G21/G23).
- **E4 mechanism check.** Decompose dEV into its stop-rate and win-rate components. The stated
  mechanism predicts the stop-rate rise dominates. A result driven purely by fewer wins with no
  stop-rate change FALSIFIES the stated mechanism and downgrades the finding to unexplained
  (park, do not ship).
- **E5 PIT audit.** `prior_earn_jump_pct` uses a PAST, REPORTED earnings event, so it is materially
  safer than the forward-looking `d_earn_in_window` (A2). This must still be verified explicitly:
  every contributing event must have `reported_eps` non-null and `effective_date` strictly before
  the entry date. Any leakage found voids the leg.
- **E6 supply cost.** Report what excluding/de-rating T3_high does to 75+ signal supply. A −33%
  supply cut for a −3.14pp EV gain is a different trade than a −5% cut, and the Stage-3 gate reads
  it through the cascade.

Only if E1-E5 pass does this become a Stage-3 sizing candidate subject to the unchanged §3d gate
(low-EV AND dd-concentration coinciding, all-levers-off slice, G26 sign stability, B->C->D at
N=500 with collapse=0). E4 failing means park, not ship.

**A4 — 2026-07-25, pre-run (the noise constant gets a second, sharper definition).** In addition to the
variance-share `nu` of §2/M2, the headline deliverable is a **counterfactual outcome-flip rate**:
re-walk each trade's barrier with jump-day idiosyncratic returns removed (replaced by the trade's own
non-jump mean idiosyncratic drift), and report the fraction of win/stop/expire labels that CHANGE.
`nu_flip` = that fraction. Stated assumption, declared: jumps are treated as unforecastable additive
shocks to the idiosyncratic path. This is a diagnostic under that assumption, not a claim that the
counterfactual path is what would have happened. Derived honesty output: an effective sample size
`N_eff = N * (1 - nu_flip)` and correspondingly widened CIs on every WR/EV claim the system makes.
