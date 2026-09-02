# Gamma-Curve Calibration — Empirical Delta/Gamma Surface at Daily Grain (DESIGN)

**Date:** 2026-07-17 (Overnight #2, authored FABLE) · **Status: DESIGN LOCKED tonight; Phase 1
queue-ready post-migration; the one-shot OOS read is 2026-12-15-anchored (PREREGISTRATION.md).**
**No compute ran tonight** (queue daemon stopped per Overnight #1 rule; brief's optional Phase-1
execution deferred to the next local session — nothing here depends on tonight's clock).

---

## 0. Position in the corpus — what this IS and IS NOT

The engine's option-P&L path model is production constant-delta=0.5 + sqrt-time theta
(`option_pricing.option_pnl_pct`), with a built, parked gamma-aware BS alternative
(`option_pricing.bs_*`, env `GAMMA_AWARE=1`, default OFF). The July evidence chain settled the
adoption question for now:

- `experiments/iv_premium_model/VERDICT.md` (2026-07-12): the error-cancellation thesis is
  CONFIRMED and quantified — gamma-alone is fantasy (+819% median compound), IV-premium-alone is
  catastrophic (17% calm-window collapse), the pair is strictly coupled both ways.
- `experiments/iv_engine_pertrade/VERDICT.md` (2026-07-13): the per-trade validation of the
  (GAMMA_AWARE + IV_MODEL) pair FAILED M1 (premium-level, calm names) → **pair PARKED as
  A/B-only. Re-open = NEW DATA CLASS ONLY** — (a) real fills from the P3.7 slippage loop,
  (b) a mid-price/quote source, (c) P1.4 vega-state — and any re-open re-runs THAT harness
  (`build_ledger.py` + bars verbatim). **Never another form iteration on that panel/ledger.**
- Same verdict, the asymmetry this design lives in: the pair FAILED as a *premium-level* model
  (calm names) but PASSED the *path-fidelity* cells (M2: d10 overall t −2.36; d15 ex-earnings
  t −3.82, both rounds). The path/convexity side is the promising half — and it has never been
  measured DIRECTLY.

**What this experiment IS:** a direct empirical measurement of the delta/gamma surface at daily
grain from OUR OWN yfinance contract panel (`options` + `option_prices`, 2025-02-10 →
2026-07-17 at design time, ~89.9M price rows / ~2.15M contracts, still accruing daily), plus a
pre-registered three-way path-model comparison (production constant-delta vs the existing BS
engine vs a one-parameter calibrated BS) judged on end-to-end one-day-ahead value prediction
error. Output: a measured delta surface (diagnostic tables) and, if the bars pass, a **frozen
calibrated curve that becomes the designated candidate arm** the day a re-open condition fires.

**What this experiment is NOT (hard non-goals — violating any of these voids the read):**
1. NOT an adoption vehicle. No production flag flips. `GAMMA_AWARE`, `IV_PREMIUM`, `IV_MODEL`
   stay default-OFF regardless of outcome (traps.md §4 coupling rule untouched).
2. NOT a premium-level model refit. The forbidden move from the park ruling — another form
   iteration of the IV/premium model on the Polygon panel or the yfinance trade ledger — is out
   of scope entirely. This design measures PATH response (ΔV given ΔS), not entry premium level.
3. NOT a re-run of `iv_engine_pertrade` bars. M1/M2/M3 are not re-litigated here; the December
   M3 SL-FNR re-read stays governed by `holdout_oos_2026_12/PREREGISTRATION.md` §11 PL-5 (and
   NOT via `compute_metrics_refined.py`, which carries the stale N=33 forward).
4. NOT Stage-1 scoring work. No `ALGORITHM_VERSION` bump under any outcome (engine-fidelity
   track; the adoption question, if ever reached, is "does any gate DECISION flip?" per the
   established engine-fidelity rule). This sits OUTSIDE the pre-December restraint clause
   (which targets in-sample scoring churn; gameplan-2026H2-DRAFT §1(b)/P2.B name this fidelity
   track as sanctioned work).

**Relation to the masterplan draft** (`.claude/docs/gameplan-2026H2-DRAFT.md`, PROPOSAL): Phase 1
here is a P1-era queue job (db-heavy build + cheap fit); any conditional Phase-3 Stage-3
re-validation runs at the D-tier of the proposed N-ladder (N=500-1000, certificate-class, not a
search). If the draft is amended Sunday, the N reference follows the ratified ladder; the bars
in PREREGISTRATION.md do not move.

---

## 1. The measurement — empirical delta at daily grain

**Unit of observation:** a same-contract consecutive-trading-day pair: option `i` (CALL primary)
observed at trading days `d` and `d+1` (calendar gap ≤ 4 days), with underlying closes
`S_d, S_{d+1}` (PriceHistory), option lastPrices `V_d, V_{d+1}` (option_prices), strike `K`,
remaining DTE `τ_d = expiration − d` in calendar days.

**Per-pair quantities:** moneyness `m_d = ln(S_d / K)`; observed one-day option return
`r_V = (V_{d+1} − V_d) / V_d`; underlying move `r_S = (S_{d+1} − S_d) / S_d`.

**Two layers, kept strictly separate:**

- **Diagnostic layer (interpretable, non-gating):** per-cell regression of `r_V` on `r_S·S_d/V_d`
  (i.e. the slope IS the dollar-delta estimate) with date-clustered CR1 standard errors
  (clustering method reused from `experiments/peak_fakeout/mine.py` precedent), intercept =
  cell-average one-day theta as a fraction of premium. Produces the measured delta surface
  `delta_hat(m, τ)` and its comparison against the BS-predicted per-cell mean delta. Gamma is
  read as the finite difference of `delta_hat` across adjacent moneyness cells — diagnostic
  only, never separately fit (BS ties delta and gamma; a free gamma would be a parameter zoo).
- **Decision layer (the bars):** end-to-end one-day-ahead value prediction error per model
  (§2), pooled over the **liquid CALL stratum restricted to the core grid (m ∈ (−0.15,+0.15])**,
  judged ONCE on the OOS window per PREREGISTRATION.md. Core-grid restriction is deliberate:
  deep wings have near-zero vega, so price-implied vol is ill-conditioned there (noise, not
  physics, would drive the ranking), and the engine's real paths live near ATM. The wings stay
  in the diagnostic layer. The bars never reference per-cell fits — cells are too many looks.

**Why daily grain:** the engine consumes the path model bar-by-bar (`fires_on_bar`,
`random_fill_pnl` are per-bar calls); daily pairs are exactly the grain the engine's error
compounds at, and the panel supports them at massive N. The per-trade horizon grain (d10/d15)
was already graded by `iv_engine_pertrade` M2 — this measurement is the microscope under it.

---

## 2. The three models (nested; exact per-pair forms)

All three share the increment structure **V̂_{d+1} = carry(V_d, τ_d) + move-response(ΔS)** and
are evaluated by identical code paths on identical pairs. `w_d` denotes the price-implied
normalized total vol: the solution of `BS_call(m_d, w) · K = V_d` (bisection; BS normalized
r=0, q=0 — the engine's own convention). Using the pair's OWN price to imply vol keeps M1/M2
panel-IV-independent — the per-trade round's load-bearing failure was a cross-source IV gap
(Polygon BS-IV vs yfinance premiums); this design never mixes sources.

- **M0 — production constant-delta (the incumbent, engine-VERBATIM including its calendar
  quirk):** `V̂_{d+1} = V_d · sqrt((τ_d − 1)/τ_d) + 0.5 · (S_{d+1} − S_d)`.
  This is exactly `option_pricing.option_pnl_pct`'s per-pair localization: the sqrt-remaining
  theta ratio (total-DTE cancels in the one-day ratio) plus dollar-delta 0.5. The decrement is
  **one unit per observed pair regardless of the calendar gap g** (the engine counts trading
  bars). **Charitable-incumbent framing (adversarial-pass closure M-1):** the deployed engine
  anchors its remaining-time clock to a NOMINAL total (30) minus trading bars held, which for a
  real contract drifts from true calendar τ; per-pair we evaluate the engine's MODEL FAMILY at
  the contract's TRUE remaining τ_d — the best-case version of the incumbent. If even this
  charitable M0 loses Bar A, the deployed nominal-anchored engine loses a fortiori; a
  charitable-M0 WIN is likewise a fortiori valid for the incumbent's defense. Implementation
  MUST import and wrap `option_pricing` (no re-derivation) and assert wrapper ≡
  `option_pnl_pct` on the PINNED self-test vector (PREREGISTRATION §F) before any run.
- **M1 — BS engine, price-implied vol (the parked gamma engine's structure, off-ATM-correct):**
  `V̂_{d+1} = V_d · R_θ(m_d, τ_d, w_d) + [BS_call(m_{d+1}, w'_d) − BS_call(m_d, w'_d)] · K`
  where `w'_d = w_d · sqrt((τ_d − g)/τ_d)` is the calendar-gap-decayed vol (g = actual
  calendar days in the pair), `R_θ` is BS's own no-move decay ratio at `w_d` over the same g,
  and `m_{d+1} = ln(S_{d+1}/K)`. At ATM entry this reduces to the engine's `bs_value_frac`
  roll; off-ATM it is the same model evaluated where the panel actually sits.
  **Disclosed asymmetry:** M0 decays engine-style (g-independent), M1/M2 decay in physical
  calendar time — a candidate implementation would be calendar-correct, so the comparison is
  incumbent-as-implemented vs candidate-as-would-be-implemented. Because part of an M1 win
  could therefore be weekend-theta handling rather than gamma, the read MUST report the
  Monday-pair (g ≥ 3) vs mid-week-pair (g = 1) MAE split (§ diagnostics): if M1's edge lives
  ONLY in g ≥ 3 pairs, the honest conclusion is "calendar-theta fidelity, not convexity," and
  the read must say so.
- **M2 — calibrated BS, one fitted parameter (the ONLY fitted object):**
  identical to M1 with the move-response evaluated at `w_eff = k · w'_d`; carry unchanged.
  `k` is a global gamma-dose dial: k<1 sharpens the delta ramp (more convexity), k>1 flattens
  it toward linear. **M2(k=1) ≡ M1 exactly** (nesting is asserted in code). Fit: grid
  k ∈ [0.50, 2.00] step 0.01 minimizing train MAE through the identical prediction pipeline,
  then one golden-section refine; tie-break = smallest |k−1|. Fold stability per
  PREREGISTRATION.md. **No richer form is fittable inside this protocol** — k-per-DTE-band,
  k-per-wing, per-cell lookups, or any second parameter require a NEW pre-registration through
  the Amendment Protocol, never a run-time choice. Residual structure found in diagnostics
  becomes a documented lead, not a same-read fit.

**Diagnostic-only fourth arm (non-bar, excluded from all claims):** D-IV = M1's form with the
panel's stored yfinance `iv` (where non-null) replacing price-implied vol — isolates how much
of M1's residual is vol-level noise vs model structure. Never claimable: panel IV does not
exist pre-2025, so it can never be the production path model, and panel-IV trust is exactly
what the per-trade round refuted.

---

## 3. Panel specification (all constants frozen in PREREGISTRATION.md §F)

**Source:** `options` ⋈ `option_prices` (composite PK `(option, date)`; covering index
`idx_op_covering (option_id, date, price, iv, open_interest)` makes the per-contract
consecutive-day self-join an index-only scan) ⋈ `price_history` for `S`. Extraction via the
`database/bulk_cache.py` pattern (`chunked_query_by_year` → version-stamped parquet under
`.cache/gamma_curve_calibration/`), submitted through `trader queue submit` (db heavy) — never
raw (CLAUDE.md long-running-compute rule; ~90M-row extraction is queue-scale).

**Inclusion (CALL primary; PUT mirror built for the diagnostic appendix only — puts are OFF
portfolio-wide):** τ_d ∈ [5, 45]; consecutive-day pair with calendar gap ≤ 4; `V_d ≥ 0.10` and
`V_{d+1} ≥ 0.05`; `volume_d ≥ 5` (base stratum); moneyness `m_d ∈ [−0.35, +0.35]`.
**Liquid stratum (the bars' population):** additionally `volume_d ≥ 50` AND
`open_interest_d ≥ 100` — mirroring the liquid-primary precedent from `iv_engine_pertrade`
(lastPrice on illiquid contracts is stale; see §4 attenuation).

**Exclusions (each counted and reported):**
1. **Earnings windows:** any pair where `d` or `d+1` falls in `[T−1, T+1]` of an `EarningsDate`
   for the symbol — the earnings vega/crush channel is modeled separately
   (`iv_crush_model`, P1.4 scope) and would contaminate the delta estimate.
2. **Bad prints / arb violations:** `V_d < max(0, S_d − K) − 0.02·S_d` (call below intrinsic
   beyond tolerance) or `|r_V| > 3.0` (±300%/day) — excluded, counted.
3. **Corporate-action straddles:** pairs where `S_{d+1}/S_d` is within 1% of a simple split
   ratio ({2,3,4,5,10} or reciprocals) → exclude the symbol's pairs within ±5 trading days;
   plus any pair with `|r_S| > 0.25`.
4. **Strike-space consistency (the retro-adjustment guard):** `PriceHistory.close` is subject
   to yfinance retro-adjustment (documented in `gamma_iv_phaseb/VERDICT.md` — the price
   substrate drifts), while strikes live in raw-price space. Per (symbol, d): compute the
   volume-weighted centroid of strikes with `|ln(S_d/K)| < 0.10` among that day's active CALL
   rows; if `|S_d/centroid − 1| > 0.10`, flag and exclude the symbol-day. One-day RATIOS are
   adjustment-invariant except across the action date itself (handled by exclusion 3); the
   centroid check protects the moneyness LEVEL used for cell assignment and BS inputs.
   The build must report the flagged fraction; >2% of symbol-days flagged → STOP, investigate
   before fitting (build-integrity gate, not a science bar).

**Cells (diagnostic layer):** moneyness edges [−0.35, −0.15, −0.05, +0.05, +0.15, +0.35] ×
τ edges [5, 10, 17, 28, 45] → 5×4 = 20 cells, × {base, liquid} strata. **Core grid** = the 8
cells with m ∈ (−0.15, +0.15] — the engine's realistic path range (ATM entry drifting to
TP ≈ +1-3% S move or SL/dead-hold territory).

**Dependency (ops):** OOS accrual requires the daily `trader close-update` options pull to keep
running post-migration — this rides on gameplan-draft P0.A's scheduled-task inventory. A pull
gap shows up as a dated hole in the OOS pair counts and is disclosed in the December read, not
patched.

---

## 4. Known biases, owned honestly

1. **lastPrice staleness attenuates measured delta toward zero** (a contract that didn't trade
   after the underlying moved prints ΔV=0). This is THE bias of this design. Controls:
   (a) the liquid stratum is the bars' population; (b) mandatory attenuation table —
   `delta_hat` per volume tier {5-19, 20-49, 50-199, 200+} on the ATM τ∈[17,28) cell; if
   `delta_hat` is still rising at the top tier (no plateau), the headline surface is flagged
   ATTENUATION-SUSPECT and the December read reports both the 200+ tier and liquid-stratum
   numbers. The A/B bars are less exposed than the surface (all models are scored on the same
   noisy target, and staleness noise is model-independent), but a stale target COMPRESSES
   inter-model MAE gaps — this biases toward NOT clearing Bar A/B, i.e. toward the incumbent;
   acceptable direction (conservative), disclosed.
2. **Overnight vs close-to-close mismatch:** V and S are same-day closes ~simultaneous
   (both EOD), so the pair measures the same 24h window — no timing skew — but option closes
   can print minutes before/after the underlying close; treated as noise, absorbed in MAE.
3. **No dividends/rates in BS (r=0, q=0)** — the engine's own convention; shared by M1/M2
   symmetrically, cancels in the A/B ranking; biases implied w slightly for high-div names
   (level, not ranking).
4. **Survivor tilt:** the panel only contains pulled (live) names; delisted-in-window names'
   final weeks are missing. Second-order for delta physics; disclosed, not blocking (this is
   not an EV claim).
5. **Weekend/holiday theta:** τ in calendar days, pairs span 1-4 calendar days but "one trading
   day"; all models see the SAME τ decrement (actual calendar gap `g`: carry uses
   `sqrt((τ−g)/τ)` and `w'` uses the same), so the gap is handled uniformly — `g` is an input,
   not a free choice.

---

## 5. Phases, runtime, commands

- **Phase 0 (tonight, done):** this DESIGN + PREREGISTRATION committed; adversarial pass;
  zero compute.
- **Phase 1 (post-migration, any time; Sonnet-class executor):** panel build → parquet +
  cell-N coverage report + integrity-gate check; train-side fit of k via 4 forward-chained
  date folds within train (≤ 2026-06-15); **frozen artifact** `fitted_curve_frozen.json`
  (k pooled + per-fold, filter constants echo, panel sha256s) committed. NO OOS row is
  evaluated in Phase 1 — the OOS parquet may be built, but Phase-1 may emit for OOS rows ONLY
  `(cell_id, integer pair-count, floor-met boolean)` — no moment, mean, quantile, regression,
  delta_hat, D-IV, or error of any OOS column, in any file including COVERAGE.md (closure of
  the coverage-peek channel). The OOS metrics script carries a date guard with NO force-early
  flag (PREREGISTRATION §S).
  Queue skeleton (verify flags against queue-ops skill at run time):
  `trader queue submit --priority high --db heavy --cpu 4 --restartable --window off_market
  --dedup gamma-curve-panel --reason "gamma_curve_calibration Phase 1 panel build"
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 -- python
  experiments/gamma_curve_calibration/build_panel.py` — expected 30-90 min (MySQL-bound
  extraction of ~90M rows, chunked; polars join minutes). Fit step: <10 min, CPU-light,
  separate queued job.
- **Phase 2 (2026-12-15+, one-shot):** the OOS read per PREREGISTRATION.md — mechanical,
  Sonnet-executable, <5 min compute. Runs ALONGSIDE (not inside) the December pack; it shares
  the date for discipline reasons only and touches none of the pack's frozen objects.
- **Phase 3 (event-gated, unscheduled):** when a per-trade re-open condition fires
  ((a) real fills N≥30-50 / (b) mid-quotes / (c) P1.4), the frozen curve (M2 if Bar B passed,
  else M1 if Bar A passed) is the candidate arm entering the VERBATIM `iv_engine_pertrade`
  harness re-run. If that ever licenses adoption, the engine-fidelity re-baseline ("does any
  gate DECISION flip?") runs at D-tier N=500-1000 paired seeds as a certificate. FABLE/user
  own Phase-3 adjudication; it is not licensed by anything in Phases 0-2.

---

## 6. Traps forwarded (executor MUST read before Phase 1)

From `.claude/docs/traps.md` + house memory, the ones that bite THIS build: queue jobs need
`--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1` and ASCII-only prints; never raw-background
heavy compute ("watching it" does not exempt the queue); peewee bulk loops read `.symbol_id`
not `.symbol` (~90M-row surface — use raw SQL/`.tuples()` throughout); polars heterogeneous
rows need `infer_schema_length=None`; MySQL >30s query = missing index, `SHOW FULL PROCESSLIST`
+ KILL on any 2013, never blind-retry; do not edit engine/config files while a queued job that
imports them is in flight; `CALIBRATION_CUTOFF_DATE` is read from `strategy_config.py` (the
ONLY source of truth) but this protocol's boundary is FROZEN at 2026-06-15 independently (§F) —
a December re-lock does NOT retroactively expand this train set; holdout enforcement patterns
via `experiments/_holdout.py` precedent.

## 7. Deliverable inventory

`DESIGN.md` (this file) · `PREREGISTRATION.md` (bars, frozen constants, executor guide) ·
Phase-1: `build_panel.py`, `fit_curve.py`, `.cache/gamma_curve_calibration/*.parquet`,
`fitted_curve_frozen.json`, `COVERAGE.md` · Phase-2: `oos_read.py` (date-guarded),
`FINDINGS.md` · referenced precedents: `option_pricing.py` (imported, never re-derived),
`experiments/peak_fakeout/mine.py` (CR1 clustering), `database/bulk_cache.py`,
`experiments/iv_engine_pertrade/` (park ruling + liquid-primary precedent).
