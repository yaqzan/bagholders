# Gamma-Curve Calibration — PRE-REGISTRATION (frozen 2026-07-17, pre-peek; adversarial closures 2026-07-18)

**Registered by FABLE, Overnight #2, BEFORE any panel row was extracted or any model evaluated
on any pair.** Only instant coverage metadata was touched pre-freeze (option_prices span
2025-02-10 → 2026-07-17, ~89.9M rows est., ~2.15M contracts est.) — no prices, pairs, or deltas.
Companion: DESIGN.md (same commits). Two adversarial passes ran against the committed v1
(executor-gaming: 24 holes; methodology: PARTIAL — agent died at a session limit after surfacing
2 defects; both closed; see §G). Amendments only via §A. **A modified bar voids the read.** This
file never touches `holdout_oos_2026_12`'s frozen objects.

---

## §F. Frozen constants (verbatim inputs; no run-time discretion anywhere)

| Constant | Value |
|---|---|
| Train window (pairs by date `d`) | 2025-02-10 ≤ d, with pair-end ≤ **2026-06-15** |
| OOS window | 2026-06-16 ≤ d, pair-end ≤ the SEALED upper bound (§N) |
| Boundary source | FROZEN HERE at 2026-06-15. A December re-lock of `strategy_config.CALIBRATION_CUTOFF_DATE` does NOT move this boundary either way |
| Side | CALL = bars population; PUT = diagnostic appendix only |
| DTE range τ_d | [5, 45] calendar days |
| Pair definition | two rows of the SAME `option_id` at dates d < d′ where d′ is the next NYSE session strictly after d for which BOTH `option_prices` (this option) AND `price_history` (the symbol) have a row; g = d′ − d in calendar days, 1 ≤ g ≤ 4; intervening printless NYSE sessions allowed within the cap. NYSE calendar = the empirical trading-day index (per the wave_cycle_mine static-holiday-table trap), never a static table |
| Price floors | V_d ≥ 0.10, V_{d′} ≥ 0.05 (USD) |
| Base stratum | volume_d ≥ 5 |
| Liquid stratum | volume_d ≥ 50 AND open_interest_d ≥ 100. NULL or 0 in either column → the pair FAILS that stratum (counted, reported); no coalescing, no substitution, no dropping the predicate. If >20% of otherwise-liquid pairs fail on null OI, disclose in COVERAGE.md and proceed on non-null pairs — never relax |
| Moneyness range | m_d = ln(S_d/K) ∈ [−0.35, +0.35] |
| Cell edges | m: [−0.35, −0.15, −0.05, +0.05, +0.15, +0.35] × τ: [5, 10, 17, 28, 45]. Cell assignment: left-open right-closed on the printed edges |
| Core grid | the cells with m ∈ (−0.15, +0.15] — **12 cells** (3 m-bins × 4 τ-bins; corrected per Amendment A-1) |
| Bars population | liquid CALL ∩ core grid. The §N cell rule is a coverage GATE only — the pooled bars MAE includes EVERY bars-population pair regardless of its cell's count; thin cells are never dropped from the pool |
| Exclusion pipeline (ORDER PINNED, first-match attribution, per-stage counts reported) | pairs built → (1) corp-action (S-ratio within 1% of {2,3,4,5,10,½,⅓,¼,⅕,1/10} → symbol ±5 trading days; any \|r_S\|>0.25) → (2) earnings (d or d′ ∈ [T−1,T+1] of an EarningsDate) → (3) bad-print (V_d < intrinsic − 0.02·S_d, or \|r_V\|>3.0) → (4) IV-solve failure → (5) strike-centroid flag (computed on the rows surviving 1-4, base stratum) → stratum assignment → cell assignment |
| Strike-centroid gate | per (symbol, d): strikes K with \|ln(S_d/K)\| < 0.10 among surviving base-stratum CALL rows that day; per-strike weight = Σ volume_d across all expirations at that strike; centroid = Σ(K·w)/Σ(w); \|S_d/centroid − 1\| > 0.10 flags the symbol-day (excluded, counted). No qualifying strike → UNFLAGGED, retained, counted separately. Definition frozen; no per-symbol hand-tuning |
| Centroid STOP | flagged fraction > 2% of symbol-days → the automated build HALTS TERMINALLY: write the flagged-symbol table to COVERAGE.md and STOP. Resolution is a FABLE/user §A decision logged before any resumed run. The executor may NOT hand-select symbols to get under 2% |
| Implied-vol solve | bisection on normalized BS call (r=0, q=0), w ∈ [0.005, 3.0]; no solution → excluded at stage 4, counted |
| Error metric (bars) | MAE = unweighted arithmetic mean of \|(V̂_{d′} − V_{d′})/V_d\| over ALL bars-population pairs (equal weight PER PAIR). Unwinsorized, untrimmed; the §F exclusions are the ONLY filters — no read-time winsorization, trimming, or additional "hygiene" exclusion of any kind. Relative improvement = (MAE_A − MAE_B)/MAE_A on these pooled-per-pair MAEs |
| z statistic (bars) | CR sandwich on the PER-PAIR error difference (\|e_A\| − \|e_B\|) regressed on a constant, **two-way clustered by (date d, symbol)** (cameron-gelbach-miller); z = coef/SE, signed in the improvement direction. NOT a collapse-to-daily-means t-test. Print G_dates and G_symbols; assert G_dates == n_unique(d). Diagnostics (non-gating, reported): one-way date-clustered z and one-way symbol-clustered z. Two-way chosen at closure M-2: pairs of the same contract on adjacent days share a print (serial dependence one-way date clustering misses) |
| M2 fit | grid k ∈ [0.50, 2.00] step 0.01, objective = MAE (as defined above) on the TRAIN **liquid CALL core-grid** population (identical population definition to the bars, train window only; wings never enter fit or bars); tie-break = smallest \|k−1\|. NO golden-section refine (dropped at closure — one less unpinned DOF; 0.01 grid is adequate). k recorded numerically |
| CV folds | boundaries = the 3 interior calendar dates splitting [2025-02-10, 2026-06-15] (490 days) into 4 equal-CALENDAR-duration spans, boundary_i = start + round(i·490/4) days: **2025-06-12, 2025-10-13, 2026-02-13** (computed once, frozen here). Fold i (i=1..4) fits k on all train bars-population pairs with d ≤ boundary_i (expanding window; fold 4 = full train). Stability = max/min of the four fold-k's ≤ 1.5 |
| Self-test vectors (build STOPS on any failure) | **M0:** wrapper must CALL `option_pricing.option_pnl_pct(side='call', premium_pct=V_d/S_d, bars_held=1, total_dte=τ_d)` and assert V_d·(1+pnl) equals the closed form on the pinned vector AND on every panel row to 1e-9. Pinned vector (S_d, S_{d′}, V_d, K, τ_d): (100, 103, 3.00, 100, 30), (100, 97, 3.00, 100, 30), (100, 100, 3.00, 100, 30), (50, 50.5, 0.80, 52, 10), (200, 196, 12.00, 190, 42) — expected values computed by the reference implementation at build time and FROZEN into `fitted_curve_frozen.json` before any fit. **M1:** frozen reference implementation with R_θ(m,τ,w) = BS_value(m, w·sqrt((τ−g)/τ))/BS_value(m, w) at unchanged m; move-response = [BS(m_{d′}, w′) − BS(m_d, w′)]·K with w′ = w_d·sqrt((τ−g)/τ); assert M1 ≡ the `bs_value_frac`-based ATM roll at (m=0, g=1) to 1e-12, evaluated on the same pinned vector. **M2(k=1) ≡ M1** to 1e-12 |
| Carry conventions | M0: decrement 1 per pair, g-independent (engine family, charitable-incumbent framing per DESIGN §2 / closure M-1). M1/M2: decay over actual calendar gap g. Asymmetry disclosed; Monday-split diagnostic mandatory |

## §N. N floors and the sealed OOS slice

- **Train floor:** liquid CALL core-grid pairs ≥ 50,000, else the protocol defers whole
  (INSUFFICIENT_N at build time; disclosed; re-attempt only as data accrues, constants unchanged).
- **Sealed OOS slice (closure of the roll-forward game):** at the FIRST Phase-2 invocation,
  compute and record ONCE in the attempt log: upper bound = min(2026-12-12, latest d′ whose
  option and underlying rows are complete in the production DB as of invocation). This bound is
  IDENTICAL for every re-attempt — it never rolls forward. INSUFFICIENT_N triggers the single
  extension (below) with a NEW sealed slice computed the same way at 2027-06-15; never
  incremental re-rolls.
- **OOS floors:** bars-population pairs ≥ 20,000 AND ≥ 9 of the 12 core cells with ≥ 1,000
  OOS pairs each (per Amendment A-1; coverage GATE, not a population filter); else
  INSUFFICIENT_N → ONE extension to 2027-06-15, bars unchanged, no second extension (then
  CLOSED-UNRESOLVED).
- **OOS provenance (closure of the backfill game):** OOS `option_prices`/`price_history` rows
  are ONLY those written by the production daily pull by their normal settle time; the build
  records per-day row counts and asserts no OOS row was inserted/modified materially after its
  trading date. NO December backfill, re-pull, or alternate-source fill of OOS months under any
  rationale. A pull gap → disclosed dated hole; a floor miss → INSUFFICIENT_N, never a patch.

## §B. Bars (evaluated once, on the sealed OOS slice, bars population, ≥ 2026-12-15)

- **Bar A — is BS convexity real at daily grain?** PASS iff MAE_M1 < MAE_M0 with the §F two-way
  z ≥ 3 AND relative improvement ≥ 5%. PASS licenses exactly: "the BS path structure beats the
  charitable constant-delta incumbent at daily grain on own-panel data" + M1 (or M2 per below)
  becomes the designated candidate arm for the parked pair's re-open harness. NO flag flip, NO
  adoption, NO Stage-3 claim.
- **Bar B — does one fitted parameter earn its seat?** (evaluated only if the Phase-1 fit ran)
  PASS iff MAE_M2 < MAE_M1 with two-way z ≥ 3 AND relative ≥ 3% AND §F fold stability. FAIL →
  Tier-2 closes permanently on this panel class; M1 stands as candidate (if Bar A passed).
  Single-shot: no re-fits, no second forms, no grid extensions.
  **If Bar A FAILS, the governing verdict is the falsification regardless of Bar B; any Bar-B
  result is reported as MOOT/NON-LICENSING, and no "Bar B passed" sentence may appear without
  "Bar A failed → nothing designated, convexity axis closed" in the same sentence.**
- **Falsification:** Bar A FAIL → the daily-grain convexity axis CLOSES: constant-delta stands;
  the parked pair's re-open conditions are unaffected but proceed WITHOUT a calibrated-curve
  candidate; no retry on this panel class — next look only inside a re-open data class.
- **Unconditional reporting (closure of the skip-the-guard game):** FINDINGS.md MUST report, in
  EVERY outcome branch: signed two-way z for M1−M0 and M2−M1, both relative-MAE ratios, and an
  explicit "significant reversal (M1 worse at z ≥ 3)? yes/no" line. M1-worse at z ≥ 3 is an
  ACTIVE FINDING escalated to FABLE/user (it strengthens the park), not just filed. A FINDINGS
  missing any of these fields is void.
- **Proportionality note (bars set 2026-07-17 pre-peek):** consequences are small (which arm
  enters a future harness; the shape of one A/B-only engine); house-standard z≥3 + a
  meaningful-but-reachable margin is proportionate; no money moves on this read.

## §D. Mandatory diagnostics (non-gating, reported verbatim; FINDINGS void if any is missing)

Per-cell delta_hat table (both strata) vs BS-predicted delta · attenuation-by-volume table on
the ATM τ∈[17,28) cell, tiers {5-19, 20-49, 50-199, 200+} with the NUMERIC plateau rule:
plateau ⇔ \|delta_hat(200+) − delta_hat(50-199)\|/delta_hat(50-199) ≤ 0.10 AND the (200+ minus
50-199) difference is not > 0 at z ≥ 2 (else headline flagged ATTENUATION-SUSPECT — computed
mechanically, never judged) · Monday-split: per-model MAE on g ≥ 3 vs g = 1 pairs (if M1's
Bar-A edge exists only in g ≥ 3, FINDINGS must state "calendar-theta fidelity, not convexity")
· one-way date z + one-way symbol z (vs the primary two-way) · D-IV arm MAE (diagnostic only)
· PUT mirror table · per-stage exclusion counts · OOS pair counts by month (pull-gap
disclosure) · G_dates / G_symbols.

## §S. Schedule, executor guide, integrity rails

| When | What | Who |
|---|---|---|
| 2026-07-17/18 (done) | Phase 0: docs committed; adversarial passes; closures | FABLE |
| Post-migration, any time | Phase 1: build → COVERAGE.md → train-CV fit → `fitted_curve_frozen.json` committed (k pooled + per-fold, ALL §F constants echoed, train+OOS parquet sha256s, self-test expected values, build git HEAD, queue task ids). OOS emissions = counts-only (DESIGN §5) | Sonnet-class OK |
| 2026-12-15+ | Phase 2: one-shot OOS read → FINDINGS.md (§B verdicts + §D diagnostics + provenance block) | Sonnet-class OK; FABLE/user read the verdict |
| Event-gated | Phase 3: designated candidate into the verbatim `iv_engine_pertrade` re-open harness | FABLE/user only |

**Integrity rails (each violation voids the read):**
1. **Append-only attempt log** `results/attempt_log.md`: EVERY driver invocation — including
   dry runs and aborts — appends timestamp, git HEAD, exact command, outcome-or-abort.
   A FINDINGS.md not embedding the attempt log's final state, the sha256s
   (train parquet, OOS parquet, `fitted_curve_frozen.json`), queue task ids, and the sealed
   upper bound is void. **Overwriting FINDINGS.md or the attempt log is void.**
2. **Frozen-k seal:** k is frozen at Phase 1; Phase 2 LOADS it and never runs the fit. Phase 2
   asserts the train parquet sha256 equals the frozen value or the read is VOID. No December
   rebuild of train under any rationale ("caches were stale" included); genuine cache loss →
   re-run Phase 1 IN FULL under a logged §A amendment, never a silent refit. The OOS parquet's
   build date + the yfinance-adjustment epoch of `price_history` reads are recorded in FINDINGS.
3. **Date guard, no bypass:** the OOS A/B path has NO force-early/rehearsal flag (deliberately
   NOT mirroring the December pack's `--force-early`). Guard asserts: wall-clock ≥ 2026-12-15
   AND every scored row's d ∈ [2026-06-16, sealed bound] AND the train-sha seal (rail 2). The
   only permitted selftest loads ZERO OOS rows.
4. **Executor conduct:** boot via `/onboard`; read DESIGN §6 traps; every build/fit job via
   `trader queue submit` with `--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`; NO bar,
   constant, floor, window, or definition may be modified; ambiguity → STOP and write the
   question into COVERAGE.md; never resolve by choice.

## §A. Amendment protocol

Amendments (constants, bars, floors, schedule, rails) require FABLE-tier or user sign-off,
logged HERE with date + rationale BEFORE any amended run — identical discipline to
`holdout_oos_2026_12/PREREGISTRATION.md` §6. A run under an unlogged amendment is void.

**Amendment log:**
- **A-1 (2026-07-18, FABLE, pre-fit, arithmetic correction):** the frozen cell edges yield a
  core grid of **12 cells** (3 m-bins in (−0.15,+0.15] × 4 τ-bins), not the "8" written in
  §F/§N — an authoring arithmetic error, corrected to the edges' own arithmetic (the edges,
  which are the binding objects, are unchanged). §N OOS cell floor re-proportioned at the
  same 75% fraction: **≥ 9 of 12 core cells with ≥ 1,000 OOS pairs**. No bar softened.
- **A-2 (2026-07-18, FABLE, pre-fit):** the §F M1 self-test ("≡ bs_value_frac ATM roll to
  1e-12") is unreachable as written: `option_pricing.bs_value_frac` derives vol via its
  documented linearized inverse (~<1% error), while §F pins exact bisection w_d. Replaced by
  two checks: (i) FORMULA identity to 1e-12 — M1 evaluated WITH the engine's linearized w
  must equal the bs_value_frac roll at (m=0, g=1) on the pinned vectors; (ii) disclosed
  divergence statement — production M1 uses exact-bisection w_d (a deliberate fidelity
  improvement over the engine's linearization; direction and magnitude reported in
  COVERAGE.md). M0 and M2(k=1)≡M1 asserts unchanged (both pass at 1e-12).
  **A-2b refinement (same day, pre-fit, after the builder's algebra proved check (i) as first
  worded is still unreachable on moved vectors):** the formula identity additionally requires
  a model-consistent anchor — check (i) evaluates M1 with the engine's linearized w AND a
  synthetic entry value V*_d = BS(0, w_lin)·K, under which M1's increment form ≡ the engine's
  ratio roll algebraically for any w (true 1e-12 identity). The real-anchor/bisection-w
  divergence (~1e-4 class) is reported per vector as the disclosed A-2(ii) line, non-fatal.
- **A-3 (2026-07-18, FABLE, pre-fit):** M0's "closed form on every panel row" reference is
  FLOORED at −1.0 to match `option_pnl_pct`'s engine-true floor (an unfloored linear form
  diverges on deep-loss cheap contracts — engine-verbatim means floored).
- **A-4 (2026-07-18, FABLE, pre-fit):** boundary rows where τ_d = 5 or m_d = −0.35 (closed-
  inclusive by the inclusion filter, left-open by the cell edges) clip into the adjacent
  first bin; the clipped fraction is counted and reported (smoke sample: 0.44%).

## §G. Adversarial-pass record (2026-07-18, against committed v1 `ca361464c`)

**Pass 1 — executor-gaming (Opus, complete): 24 holes → all closed.** H1 attempt-log absence →
rail 1. H2 coverage-peek channel → counts-only rule (DESIGN §5). H3 unsealed train/k → rail 2.
H4 z ambiguity → §F z row (one pinned construction; upgraded two-way per M-2). H5 MAE pooling
ambiguity → §F error-metric row (per-pair, worked definitions). H6 rolling OOS bound → §N
sealed slice. H7 OOS backfill → §N provenance. H8 pair-calendar ambiguity → §F pair definition.
H9 fold construction → §F CV row (numeric boundaries). H10 fit population → §F M2-fit row
(core-grid). H11 M1 unanchored → §F self-test row (reference impl + 1e-12 ATM assert). H12
vacuous M0 vector → §F pinned vector. H13 exclusion order → §F pipeline row. H14 centroid DOF →
§F centroid row. H15 "investigate" license → §F centroid STOP (terminal + §A). H16 read-time
winsorization → §F error-metric row (forbidden). H17 OI null-handling → §F liquid-stratum row.
H18 Bar-B spin → §B moot clause. H19 sign-flip skip → §B unconditional reporting. H20 plateau
subjectivity → §D numeric rule. H21 cell floor as filter → §F bars-population row + §N gate
language. H22 golden-section DOF → dropped entirely. H23 diagnostics skipped → §D void-if-
missing. H24 date-guard bypass → rail 3 (no force-early).
**Pass 2 — methodology (Opus, PARTIAL — agent terminated by a session limit mid-run; disclosed):
2 defects surfaced before termination, both closed.** M-1: M0 "engine-verbatim" overclaim (the
engine anchors theta to nominal-30-minus-trading-bars, not true τ) → charitable-incumbent
reframing, DESIGN §2 + §F carry row (a fortiori semantics stated). M-2: one-way date clustering
misses within-contract serial dependence of overlapping pairs (pair (d,d+1) and (d+1,d+2) share
the d+1 print) → primary z upgraded to two-way (date, symbol); one-way variants demoted to
diagnostics. **Disclosed residual:** the methodology pass did not complete its remaining
surfaces (errors-in-variables direction, heteroskedastic pooling composition, regime-dependence
of k, power arithmetic at the 20k floor). These are NOT closed holes — they are open review
surfaces. An optional completion pass (any strong reviewer, post-migration) may ADD §G entries
via §A but may not weaken any existing bar. The floors/bars stand as frozen; if the completion
pass finds a FATAL defect, the §A protocol (FABLE/user) adjudicates before Phase 1 runs.
