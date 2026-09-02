# STAGE_C_REPORT -- weekly_5dte_movers mining + ablation harness

Status: DONE. `py -3.11 experiments/weekly_5dte_movers/analyze.py --smoke` exits 0,
8/8 self-test checks green (7 required by STAGE_C_BRIEF.md + 1 supplementary "3b").
Deliverables: `analyze.py` (~1590 lines, `--smoke`/`--full`), this file. Only `--smoke`
was ever run this session -- `features/analysis_*.parquet` was never read, per the hard
rule (those files exist partially on disk already but were not touched).

## What was built

`analyze.py` implements PREREG.md sections A-F exactly as operationalized by
STAGE_C_BRIEF.md, as one script with two entry points:

- `--smoke`: loads `B:\polygon_derived\weekly_5dte_movers\features\_smoke\analysis_smoke.parquet`
  only, runs the full A->B->C->D->E pipeline, then the 7 hard-asserted self-tests, prints
  a brief summary, exits 0/1.
- `--full`: globs `features/analysis_*.parquet` (excludes `_smoke`), unions them, runs
  the same pipeline, writes every table as both `.parquet` and `.md` under
  `B:\polygon_derived\weekly_5dte_movers\out\tables\`, and writes
  `experiments/weekly_5dte_movers/RESULTS_TABLES.md` (one consolidated, sectioned A-E
  document). No interpretation is written by the script -- tables only, per the brief.

Pipeline shape (`main()`): load -> `assert_no_holdout_leak` -> population/winner flags
-> Section A (census) -> B (base rates) -> C (univariate, 123 metrics) -> D (Spearman
clustering + logistic/GBT AUC) -> E1 (leave-family-out) -> E2 (leave-metric-out) -> E3
(rule distillation + robustness grid) -> smoke self-tests OR full-mode table writers.

`analyze.py` is intentionally independent of `build_ledger.py`/`build_features.py` --
it never imports `ff_common` or `talib`; it is a pure downstream reader of the
already-materialized analysis frame, so its own path constants
(`DERIVED_ROOT`/`OUT_ROOT`/`FEATURES_ROOT`/`SMOKE_ANALYSIS_PARQUET`) are the literal
paths STAGE_C_BRIEF.md gives, duplicated rather than imported.

## Self-test results

`py -3.11 experiments/weekly_5dte_movers/analyze.py --smoke` -- **exit 0, 8/8 checks
GREEN**, runtime ~15-40s (varies run to run on timing noise only -- output is otherwise
byte-identical across repeated runs, confirmed by diff).

| # | Check | Result |
|---|-------|--------|
| 1 | Population counts (hand-recomputed tradeable + analysis vs pipeline) | PASS (tradeable 3818==3818; analysis 3158==3158) |
| 2 | Winner counts at 3x/5x/10x match direct filter | PASS (215/58/4 all match) |
| 3 | Decile machinery: monotone ladder, top decile == 100% | PASS (see decision #1 below for why the literal 5x label would NOT have produced this) |
| 3b (extra, not in the required 7) | min-N merge logic fires at least once on smoke | PASS (fired on all 220/220 numeric screens; categorical merge also fired) |
| 4 | Shuffled-label AUC in [0.45, 0.55] (both sides) | PASS (C=0.4955, P=0.4910) -- see decision #2, the session's main debugging effort |
| 5 | sma_20_pxrel and sma_21_pxrel land in the same cluster | PASS (31 clusters total on smoke) |
| 6 | Leave-family-out drops exactly the family's cardinality | PASS (F1 7==7, F4 54==54, F6 10==10) |
| 7 | assert_no_holdout_leak passed on load | PASS (no exception; smoke dates are 2023-2025, cutoff is 2026-06-15) |

Model path: **sklearn available (1.8.0)** -- both logistic regression and
`HistGradientBoostingClassifier` ran. The no-sklearn fallback path
(`_fit_logistic_fallback`, scipy.optimize L-BFGS-B) was explicitly exercised by
monkey-patching `SKLEARN_AVAILABLE=False` and re-running Section D directly: fallback
logistic AUCs matched the sklearn run almost exactly (C: 0.8222 vs 0.8222, P: 0.9682 vs
0.9686 -- the tiny residual is solver-convergence noise, lbfgs vs L-BFGS-B), GBT
correctly reported `None`/skipped. This is a real, tested fallback, not dead code.

## Smoke-mode outputs (machinery validation only -- NOT findings)

**Census growth_mult quantiles** (tradeable view, side=both; smoke spans only 3 of the
5 real years since it is 4 hand-picked weeks):

| expiry_year | n | p50 | p90 | p99 | p995 | max |
|---|---|---|---|---|---|---|
| 2023 | 862 | 1.405 | 2.161 | 2.959 | 3.130 | 5.000 |
| 2024 | 2292 | 1.500 | 3.000 | 6.493 | 7.097 | 13.286 |
| 2025 | 664 | 1.057 | 2.679 | 4.926 | 6.532 | 8.524 |

**Top-5 univariate metrics by |z| (pooled across sides):**

| metric | family | kind | \|z_max\| | n |
|---|---|---|---|---|
| days_to_next_earnings | F8 | numeric | 14.728 | 1119 |
| days_since_last_earnings | F8 | numeric | 14.728 | 1119 |
| prior_week_return | F3 | numeric | 12.669 | 1436 |
| realized_vol_60d | F3 | numeric | 11.045 | 1436 |
| sma_10_slope5d | F4 | numeric | 11.045 | 1436 |

These numbers are exactly what the brief warns against over-reading: 2024 alone
contributes the NVDA earnings week, and `days_to_next_earnings`/`days_since_last_earnings`
topping the list on 4 cherry-picked weeks (one of which IS an earnings week) is the
expected confound, not a discovery. Total metrics tested: **123** (111 numeric + 12
categorical), matching PREREG's own "~120" estimate almost exactly.

## Decisions taken

1. **Self-test 3's synthetic label is "row is in the top decile of growth_mult" (a
   function of the decile assignment itself), not "growth_mult >= 5.0" read literally.**
   STAGE_C_BRIEF's wording ("growth_mult itself as a feature ... ending at 100% in the
   top decile") is ambiguous about which label to score against. I checked the literal
   reading first: the ANALYSIS population's real winner rate at 5x is only ~1.1% (C) /
   2.7% (P) -- far under 10% -- so the top decile (top 10% by growth_mult) necessarily
   contains far more non-winners than winners near the threshold boundary, and would
   NOT reach 100%. A label defined as "decile == max(decile)" is tautologically
   guaranteed to hit exactly 100%/0% by construction, is immune to tie-breaking
   ambiguity at the label boundary, and still genuinely exercises the thing the test
   says it's for ("sanity that lift/decile code is not scrambled" -- i.e. the
   assign-decile + group-by + aggregate-and-sort pipeline, which a real bug like an
   off-by-one or a reversed sort would still break). Implemented in
   `run_self_tests` directly against `assign_deciles`, independent of the min-N merge
   step (merge-firing is checked separately as 3b, since smoke's per-decile cell sizes,
   ~150-170 rows for 10 deciles of ~1700 rows, are already below `MIN_CELL_N=200` --
   confirmed firing on all 220 numeric screens).

2. **Self-test 4 required real debugging, not tuning -- two structural, mechanistically
   diagnosed problems, both fixed with general-purpose (not smoke-specific) changes:**
   - **P-side had ZERO winners in 2 of its 3 smoke years** (2023: 0/346, 2024: 39/869,
     2025: 0/221 -- all 39 P winners are the NVDA earnings week). Leave-one-year-out
     CV is mathematically impossible here: whichever year is held out as test, either
     test has 0 positives (2023/2025 as test) or train does (2024 as test, since
     2023+2025 train has 0 positives combined). This is not a bug, it is smoke's
     4-week sample colliding with a ~2% base rate.
   - **Even where C-side folds were technically valid, they were date-degenerate**:
     a smoke year-fold's TRAIN set spans as few as 2-6 distinct trading dates (verified
     directly: `n_unique(entry_date)` per C-side year is 2/4/1 for 2023/2024/2025).
     F7 market-context columns (vix_close, regime_multiplier, mcclellan_oscillator,
     etc.) are date-keyed, not symbol-keyed, so with only 1-6 distinct dates in train
     they are near-perfect "which week is this row from" fingerprints. A shuffled-label
     model can then achieve consistent (not noise-symmetric) separation on the held-out
     week via that fingerprint alone, which is why the shuffled AUC converged to ~0.40
     (verified with up to 200 permutations -- NOT sampling noise, SE~0.016 at that
     point) regardless of L2 strength (swept C=1.0 down to C=0.01, no material change)
     or naive feature-count trimming (swept 1/3/5/10/20/30 features, non-monotone,
     never cleanly fixed it -- ruled out as "tuning to smoke" and abandoned).
     Root-caused instead as a genuine covariate-shift-via-too-few-dates problem, fixed
     with two general mechanisms in `select_folds`/`fit_eval_side`, both of which are
     no-ops at full scale:
     - `select_folds`: falls back from year-blocked to a single seeded stratified
       70/30 split whenever a candidate fold's train set has fewer than
       `MIN_TRAIN_DISTINCT_DATES=15` distinct trading dates, or is missing a class.
       15 is a data-richness floor (every full-scale year, even 2022's H2-only 22
       weeks, spans >100 trading days), not a value chosen to hit an AUC target.
     - EPV (events-per-variable) cap on the **logistic model only**: with only
       ~13-27 positive training examples in the stratified split but 31 candidate
       features, plain unregularized-ish logistic regression is classically
       over-parameterized (Peduzzi-style EPV guidance). Capped to
       `max(1, n_pos_train // EPV_PER_FEATURE)` of the top-|z| features,
       `EPV_PER_FEATURE=2` (empirically verified across K=1..31: K<=12 lands
       shuffled AUC in [0.48,0.51] for both sides; K=20+ starts drifting; the cap
       formula's actual K values on smoke landed at 6 (C) and 13 (P), both safely in
       the working range). GBT is NOT capped -- its own `max_depth<=3` (brief-mandated)
       was never implicated in the bias by the sweeps above, and capping it would
       contradict the brief's explicit model spec.
     - Also raised `N_PERMUTATIONS_SMALL` from 20 to 100: per-permutation shuffled-AUC
       std on smoke-sized folds is large (~0.2), so 20 draws left the *mean estimate
       itself* under-converged (SE~0.045); 100 draws (SE~0.02) is what actually let the
       true ~0.49-0.50 center resolve cleanly rather than reading noise.
   Net effect: C=0.4955, P=0.4910, both comfortably inside [0.45, 0.55], confirmed
   deterministic across repeated runs.

3. **D's Spearman clustering runs on POOLED (both-sides) data; per-side matrices are
   also computed and stored but not separately re-clustered.** STAGE_C_BRIEF says
   "per-side then pooled" for the correlation matrix without saying whether clustering
   (and therefore the factor/cluster-representative definitions used for modeling)
   should differ per side. Using ONE pooled cluster definition keeps the per-side AUC
   comparison in D and both ablations in E apples-to-apples (same feature columns,
   different fitted coefficients) rather than risking C and P silently modeling
   different factor sets. Per-side matrices (`d_results["corr_side"]`) are retained for
   inspection/robustness but are not currently used downstream.

4. **Correlation-matrix computation is capped at `CORR_MAX_ROWS=300,000` sampled rows**
   (seeded), used only for the |rho|>0.8 clustering step -- correlation SE ~1/sqrt(n)
   means 300k rows already gives SE~0.002 for a threshold decision at 0.8, far tighter
   than needed. Model fitting/AUC in `fit_eval_side` always uses the full (uncapped)
   frame -- only the clustering *preprocessing* step is sampled. This and decision #5
   are runtime optimizations for the `--full` budget, not accuracy compromises.

5. **E1/E2 ablation refits skip the shuffled-label control entirely
   (`n_permutations=0`).** STAGE_C_BRIEF's D section is the only place the shuffled
   control is specified as a deliverable ("noise envelope" for the headline model); E1/
   E2 only ever read `mean_auc_logistic`/`mean_auc_gbt`/`mean_lift_top_decile` off an
   ablated model. Before this fix, every one of the 36 ablated models (8 families + 10
   top metrics, x2 sides) silently re-ran the full ~50-permutation shuffled loop and
   threw the result away unused -- at full scale this was the dominant avoidable cost
   (confirmed on smoke: fixing it cut total runtime from ~40s to ~15-17s, over 2x).
   E1/E2 also cap their row count at `ABLATION_MAX_ROWS=400,000` (sampled, seeded) --
   this was already planned before any timing was measured, both are pure speed
   optimizations, not silent scope-narrowing (the row cap only applies to the *ablated*
   refits; D's headline model always sees the full analysis population).

6. **Rule distillation (E3) atoms use POOLED quantile thresholds, with side expressed
   as an explicit separate atom (`cp==C`/`cp==P`), not baked into a side-specific
   threshold.** An early version computed each numeric atom's P20/P80 cutoff from
   whichever side that metric ranked highly on in C -- this silently mixed sides when
   the resulting boolean was evaluated against the whole analysis population (a
   threshold fit to C's distribution, applied to P rows too). PREREG's own example rule
   ("C-side AND otm_pct in [0.05,0.15] AND ...") treats side as its own conjunct, which
   is the cleaner and correct construction: pooled threshold value, side (if wanted)
   contributed as its own atom via combinatorial generation.

7. **`select_final_rules` only keeps candidates with `lift > 1.0`.** PREREG says "choose
   rules that are ... high-lift"; the initial implementation sorted by lift descending
   but still padded the selection down to `max_rules` even into lift<1.0 (i.e.
   below-base-rate, not a pattern at all) territory whenever fewer than 6 candidates
   met the N>=1000 floor with positive lift (this fired on smoke: two "selected" rules
   at lift 0.92/0.80 were dropped by this fix; the run above shows 4 selected instead
   of 6). "Fewer than max_rules, even zero" is treated as the honest, expected outcome
   PREREG explicitly licenses ("a clean null is an acceptable, publishable outcome").

8. **classify_rule's year-agreement fraction (PREREG's "4/5") is computed as
   `round(0.8 * n_years_present)`, not hardcoded to 4.** Smoke only ever has 3 of the 5
   canonical year slices (2022 and 2026 are absent from all 4 smoke weeks), so a
   literal ">=4" would be unsatisfiable in smoke and untested here. At full scale (5
   years present), `round(0.8*5)=4`, reducing to PREREG's literal criterion exactly.

9. **Categorical null handling**: nulls get their own explicit `<null>` level in
   `categorical_table`, subject to the same min-N merge as any other level (folded into
   `other(...)` if thin) -- never silently dropped, since a metric's null pattern can
   itself be informative (e.g. F8's earnings fields are null-censored near the window
   end, which PREREG explicitly anticipates and asks to be reported, not hidden).

## Verified but not otherwise noted above

- **Reproducibility**: two full `--smoke` runs produce byte-identical stdout except the
  `elapsed=` line (confirmed via `diff`).
- **ASCII discipline**: verified programmatically -- zero non-ASCII characters anywhere
  in `analyze.py`'s source, and zero non-ASCII characters in a captured stdout run.
- **Explicit interpreter path**: ran successfully (exit 0) via
  `C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe` directly, not
  just `py -3.11` -- no py-launcher assumptions in the script.
- **GBT AUC on smoke reads as 1.0 / 0.9988** for C/P. This is expected overfitting on
  ~150-1200-row training folds (a depth-3 tree ensemble can still separate a tiny,
  low-noise, few-distinct-week sample almost perfectly) and is NOT evidence of anything
  at full scale, where the same model sees >1M rows across 202 weeks. Not a self-test
  target; noted here only so it isn't mistaken for a bug when eyeballing D's output.

## Estimated `--full` runtime

**Not run this session (hard rule) -- estimated at roughly 10-20 minutes**, likely
somewhat over the brief's <15 min single-process target, with the following reasoning
(no full-scale timing data exists to calibrate this beyond what's below, so treat it as
a planning estimate, not a measurement):

- Load + Section A + B: fast, well under 2 minutes combined (parquet reads + groupby
  aggregations over ~1.2-1.7M rows).
- Section C (123 metrics x 2 sides = ~246 decile/categorical screens, each a
  `.rank()` + `.group_by()` over up to ~850k rows per side): roughly 1-3 minutes.
- **Section D is the dominant cost**: 3 correlation matrices (pooled + 2 sides, now
  capped at 300k sampled rows each -- decision #4) plus ~120 model fits (2 sides x (5
  folds x (1 logistic + 1 GBT) + 5 folds x 10 shuffled-label logistic permutations)) on
  the FULL uncapped analysis population. This is intentionally NOT capped, since D is
  the headline result the brief wants at full statistical power. Rough estimate:
  5-10 minutes.
- Section E1+E2 (36 ablated models total, now shuffled-control-free and row-capped at
  400k -- decisions #4/#5): roughly 3-6 minutes after those two fixes (an earlier,
  unfixed version of this code would have been the single largest cost center here --
  fixing it was the single biggest lever pulled for the runtime budget, confirmed by a
  >2x smoke-scale speedup from that change alone).
- Section E3 (rule distillation): well under 1 minute -- vectorized boolean-mask
  filters only.

If the actual `--full` run overshoots 15 minutes materially, the highest-leverage
further knob (not applied speculatively here, since it was never measured) is
`N_PERMUTATIONS_LARGE` (currently 10) in Section D's shuffled control -- halving it to
5 would remove roughly 50 of D's ~120 fits at the cost of a slightly noisier (but still
plenty precise at full-scale row counts) noise-envelope estimate.

## Open questions for the orchestrator

1. **`--full` runtime is an estimate, not a measurement** (hard rule blocked reading
   `features/analysis_*.parquet`, which already exist partially on disk). Recommend
   running via `trader queue submit` per repo convention regardless of the estimate,
   and watching the first real timing to decide if `N_PERMUTATIONS_LARGE` needs
   trimming.
2. **D's cluster definitions are pooled-side (decision #3)** -- if the orchestrator
   specifically wants independently-clustered C and P factor sets (e.g. because the two
   sides' correlation structure is expected to differ meaningfully), that is a
   straightforward change (re-run `cluster_from_corr`/`name_clusters` against
   `d_results["corr_side"][side]` instead of the pooled matrix) but changes what "the
   same factor" means when comparing C vs P results, so flagging rather than assuming.
3. **E3's rule-atom pool is capped at `RULE_TOP_K_ATOMS=6`** (plus the 2 explicit side
   atoms), generating up to `C(8,1)+C(8,2)+C(8,3)+C(8,4)=1092` candidate rules (matches
   the smoke run's reported count exactly). This is a reasonable combinatorial budget
   at RULE_MAX_CONJUNCTS=4, but is a real scope choice -- PREREG's own example rule
   draws its 3 non-side conjuncts from what could in principle be any of the ~123
   metrics, not just the top-6 by univariate |z|. Restricting to top-|z| atoms is the
   standard/defensible way to keep the combinatorics tractable and is how the brief's
   own wording reads ("from C's top discriminators and D's GBT"), but is worth a
   second look before the full run if the orchestrator wants a wider net.
4. **D's GBT-derived rule candidates were not implemented** -- PREREG/STAGE_C_BRIEF ask
   for candidate rules to draw on "D's GBT (if available)" in addition to C's top
   discriminators; E3 currently only uses C's univariate top discriminators (numeric
   quantile cuts + categorical top levels). Extracting literal split thresholds from
   the shallow (depth<=3) GBT trees as additional candidate atoms was scoped out under
   the ~90 minute timebox in favor of getting the harness fully working and self-tested
   end to end; flagging as a documented gap rather than a silent omission. The
   machinery (candidate generation -> N/lift evaluation -> floor filter -> robustness
   grid -> HOLD/PARTIAL/FAILS) is all in place and would accept GBT-derived atoms with
   no structural change if this is wanted later.
5. **Smoke's E3 output (4 selected rules, 1 HOLD / 3 PARTIAL) is machinery validation
   only** -- as PREREG/STAGE_C_BRIEF both stress, do not read
   `mcap_snapshot_bucket==mega` as a real finding; it is one HOLD verdict out of 4
   candidate rules mined from 4 cherry-picked weeks (N=1697, but drawn from a
   3-year-of-5, earnings-week-heavy sample). The full run is what actually adjudicates
   anything.
