# Gamma-Curve Calibration -- Phase 1 COVERAGE.md

Generated: 2026-07-18T07:11:32 | git HEAD: 1c15953660c79885cc5d74250d97762715637cae
Build outcome: **SUCCESS**

## OPEN QUESTIONS (blocking -- need FABLE/user SS A adjudication)

Per SS S rail 4 ("ambiguity -> STOP on that item, write the question into
COVERAGE.md, continue with the rest") these four items were NOT resolved by
executor choice. The build/fit proceed using the stated interim resolutions
so Phase 1 can produce a panel and a self-test report at all; OQ-1/OQ-2/OQ-3
materially affect the decision-layer bars population and should be
adjudicated before Phase 2 (2026-12-15+) locks in a reading. OQ-4 is more
severe: it BLOCKS the fit step outright under a strict reading (see below)
and should be adjudicated BEFORE the fit is ever run, not just before
Phase 2.

### OQ-1: "8 cells" vs the literal cell-edge arithmetic (12 cells)

PREREGISTRATION.md SS F states: "Core grid | the 8 cells with m in (-0.15,
+0.15]". DESIGN.md SS 3 repeats "8 cells" and separately confirms the FULL
grid is "5x4 = 20 cells" (5 moneyness bins from the 6-value edge list
`[-0.35,-0.15,-0.05,+0.05,+0.15,+0.35]`, times 4 tau bins from the 5-value
edge list `[5,10,17,28,45]`).

Under those edges (left-open, right-closed, as SS F itself specifies), the
m-bins are: (-0.35,-0.15], (-0.15,-0.05], (-0.05,0.05], (0.05,0.15],
(0.15,0.35]. The bins whose interval is a SUBSET of (-0.15,+0.15] are bins 2,
3, 4 -- THREE bins, not two. 3 m-bins x 4 tau-bins = 12 cells. There is no
selection of bins under the stated edge list that produces exactly 8 (that
would require exactly 2 m-bins, and no documented reason to drop one of the
three central bins, e.g. the ATM (-0.05,0.05] bin, is given anywhere in
either document).

**Resolution used in this build:** proceed with the literal-edge-derived
12-cell core grid (`CORE_GRID_M_BINS = {2,3,4}` in build_panel.py, m-bins
1-indexed). This is the only reading directly computable from SS F's own
edge list without inventing an unstated bin-drop rule.

**Downstream impact:** (a) the "bars population" (liquid CALL core grid --
the decision-layer MAE population for Bar A/B in Phase 2) is defined over 12
cells, not 8; (b) SS N's OOS floor language "6 of the 8 core cells with >=
1,000 OOS pairs each" is reinterpreted here as "6 of the {N_CORE_CELLS}
core cells" -- the absolute count 6 is held pinned, the denominator is
corrected to 12. This script does NOT infer a proportionally-scaled
replacement (e.g. "9 of 12" to preserve a 75% ratio) -- that would be an
additional, unstated assumption on top of an already-ambiguous spec.

If FABLE/user determines the intended core grid was narrower (e.g. dropping
the ATM bin, or narrower tau coverage), amend SS F via SS A and re-run
Phase 1 in full per rail SS S.2 (no silent refit).

### OQ-2: M0 self-test "on every panel row" vs the -1.0 P&L floor

PREREGISTRATION.md SS F's M0 self-test row requires: "assert V_d*(1+pnl)
equals the closed form on the pinned vector AND on every panel row to
1e-9". `option_pricing.option_pnl_pct` (the wrapper's mandated import,
DESIGN SS 2) applies `max(-1.0, pnl_pre_vega)` -- i.e. it floors the
predicted option value at $0 (an option cannot be worth less than zero).
The literal closed form `V_d*sqrt((tau_d-1)/tau_d) + 0.5*(S_d'-S_d)` has NO
such floor -- it can go negative for a sufficiently large adverse move on a
cheap (near price-floor V_d=0.10) contract, which is common enough in the
base-stratum population (not just a pathological corner case) that a literal
byte-for-byte comparison against the UNFLOORED closed form would fail on a
non-trivial fraction of real panel rows, even though the wrapper is being
called correctly.

**Resolution used in this build:** build_panel.py itself does not run this
assertion (fit_curve.py owns M0/M1/M2 per the deliverable split); this note
documents the interim reading fit_curve.py implements: the REFERENCE closed
form used for the comparison applies the SAME floor
(`max(0.0, V_d*sqrt((tau_d-1)/tau_d) + 0.5*(S_d'-S_d))`), matching the
wrapper's actual (floored) behavior -- i.e. "the closed form" is read as
shorthand for the engine's true floored output, and the assertion becomes a
translation-correctness check (are the right arguments being passed in the
right order/units) rather than a claim that the linear formula never
saturates. The 5 pinned vectors are all comfortably positive under the
UNFLOORED formula so they pass either way; only the "every panel row" clause
is affected. Flagged for FABLE/user confirmation this is the intended
reading before Phase 2 treats an M0 self-test PASS as meaningful.

### OQ-3: cell assignment is left-open at a boundary the inclusion filter treats as closed

SS F's moneyness/DTE INCLUSION criteria are closed intervals: "m_d ... in
[-0.35, +0.35]" and "tau_d ... [5, 45] calendar days". SS F's CELL edges use
the SAME low-end numbers (-0.35 and 5) but are explicitly "left-open
right-closed" -- so a row with tau_d exactly 5, or m_d exactly -0.35, is
INCLUDED by the eligibility filter but has NO strictly-defined cell (the
first cell is `(5,10]` / `(-0.35,-0.15]`, both of which EXCLUDE their own
low edge). This is confirmed non-trivial in practice, not a theoretical
corner case: tau_d is an integer calendar-day count, so tau_d==5 is common
-- 294 of 67,404 eligible rows (0.44%) in a 2026-07-18 smoke sample (m_d
exactly -0.35 accounted for 0 of those 294, as expected for a continuous
log-moneyness value).

**Resolution used in this build:** these boundary rows are clipped into the
adjacent bin (bin 1 on the affected axis) rather than re-excluded -- silently
re-excluding them would narrow the frozen closed-interval inclusion range,
which SS F does not license. The count is tracked
(`n_boundary_clipped_cells_oq3` in panel_build_meta.json) and reported below.
Low severity (single-bin misassignment at an exact boundary, ~0.4% of
eligible rows in-sample) but flagged for completeness per rail SS S.4.

### OQ-4: the M1 self-test's 1e-12 bound vs `bs_value_frac`, PROVEN unreachable on a moved vector (fit_curve.py)

SS F's M1 self-test row requires: "assert M1 = the bs_value_frac-based ATM
roll at (m=0, g=1) to 1e-12". `fit_curve.py --selftest` (run 2026-07-18,
verbatim output archived in `results/attempt_log.md`) proves this bound is
UNREACHABLE for a moved vector (S_d' != S_d) under EITHER choice of
w-source, and is not cleanly reachable for a zero-move vector either once M1
uses SS F's own w_d definition. Algebraically, at m_d=0: `M1(w) -
bs_value_frac_roll(w) = (BS(m_d',w') - BS(0,w')) * (K - V_d/BS(0,w))`
(both sides evaluated at a common w) -- this vanishes only if there is no
move, OR `BS(0,w)*K == V_d` exactly. SS F's ONLY definition of w_d
("Implied-vol solve" row) is the TRUE bisection solution of that equation,
which satisfies it exactly BY CONSTRUCTION -- but only for the side that
uses it. `option_pricing.bs_value_frac` always derives its OWN internal vol
via a documented LINEARIZED approximation (`_vol_total_from_premium`,
"<1% off" per its own docstring) that CANNOT be overridden from outside, so
the two sides are structurally evaluated at two different, both-imperfect
w-values. Measured: feeding M1 the bisection w_d (SS F's actual
definition), all three ATM pinned vectors fail 1e-12 (diffs 4.632e-04 /
2.405e-04 / 1.092e-08); feeding M1 the SAME linearized shortcut
`bs_value_frac` uses internally instead, the zero-move vector passes
exactly (both sides collapse to one shared w) but the two moved vectors
still fail (2.4e-4 / 4.2e-4) since a linearized w only approximately
satisfies `BS(0,w)*K=V_d`. No w-choice clears 1e-12 on a moved vector.

**Resolution used in this build:** `fit_curve.py` implements M1 with the
bisection w_d (SS F's actual definition, used everywhere else in the
protocol) and reports the resulting diffs honestly rather than switching to
whichever w-source minimizes them. Per SS F "build STOPS on any failure",
`fit_curve.py`'s self-test is a HARD FAIL under the strict, literal 1e-12
reading, which means **Phase 1's fit step cannot complete as literally
specified until this is adjudicated via SS A** -- either the tolerance for
this specific check needs loosening to a value achievable given
`bs_value_frac`'s own documented linearization error (~1e-3 to 1e-4
relative), or the reference comparison needs a different construction
entirely (e.g. comparing against a bisection-based reimplementation of the
ATM roll rather than the existing linearized `bs_value_frac`). This is the
single highest-severity open question in this pack: unlike OQ-1/OQ-2/OQ-3,
it is not a matter of interpretation but a proven mathematical
incompatibility between two SS F rows (the M1 self-test's tolerance and the
Implied-vol solve row's bisection definition of w_d, combined with
`bs_value_frac`'s fixed internal approximation). M0 and the
M2(k=1)==M1 nesting checks are UNAFFECTED (both pass cleanly, 10/10 of the
non-M1 pinned-vector checks) -- this is scoped narrowly to the M1-vs-
`bs_value_frac` cross-check only.

## IMPLEMENTATION NOTES (resolved via reasoned interpretation, not blocking)

- **Chunk granularity: SINGLE CALENDAR DAY, not month/year.** The brief
  suggested chunking option_prices extraction "by calendar month or year"
  (matching `database/bulk_cache.py`'s `chunked_query_by_year` precedent).
  Empirical, light foreground testing on 2026-07-18 (seconds-scale checks,
  never a full option_prices scan) measured ~67,000 rows/s for a
  `date BETWEEN` range on this table (MySQL 5.7.44; `options_prices_date_IDX`
  is used as a range scan but is NOT covering here -- EXPLAIN shows "Using
  where" without "Using index", so every match costs a base-table lookup).
  At that throughput a calendar-MONTH chunk (~5-20M rows depending on era)
  would run 75-300s+ per request -- a bad unit of work regardless of the
  exact timeout policy (too coarse for --restartable resumability, too easy
  to blow past any reasonable per-statement cap). This build chunks by
  single calendar day (`_daily_ranges`) instead: ~8-15s/day warm-cache,
  independently cached/resumable, with an automatic bisection-by-
  option_id-range fallback if a single day ever exceeds its (generous, 60s)
  budget. A SEPARATE effect was also observed and is worth naming
  explicitly: `SET SESSION MAX_EXECUTION_TIME` cold-cache runs can be
  meaningfully slower than an immediately-following warm-cache re-run of the
  IDENTICAL query (InnoDB buffer-pool warmup, not a query-plan regression) --
  per-chunk timeouts are therefore set well above the observed warm-cache
  norm (60s for day chunks vs an ~8-15s norm; 120-180s for the one-shot
  small-table pulls) rather than tightly to it.
- **`CAST(x AS DOUBLE)` is unavailable** (MySQL 5.7.44; that syntax needs
  8.0.17+). Used `col * 1e0` instead, which pymysql maps to a native Python
  float (avoiding ~180M scalar `decimal.Decimal`->float conversions in
  Python).
- **Earnings proximity uses `EarningsDate.effective_date`** (falling back to
  `date` when null), not the raw report `date` -- see the docstring on
  `extract_earnings()`.
- **Corp-action "action date"** is taken as `date_d'` (the day the ratio
  discontinuity first appears in the pair), matching SS F's own
  `S_{d+1}/S_d` numerator convention. The corp-action window check tests
  `date_d` only (not `date_d'`); the earnings window check tests both `date_d`
  and `date_d'` per SS F's explicit "d or d+1" wording.
- **Missing price_history at the naive next print date**: pairs are dropped
  (not multi-hop-searched forward) if either leg's (symbol, date) has no
  price_history row -- see `build_pairs()` docstring. Expected impact ~0 for
  actively-traded, non-delisted symbols.
- **OOS parquet upper bound in Phase 1** = whatever is naturally available in
  the production DB at build time (no artificial cap). The SS N "sealed OOS
  slice" (`min(2026-12-12, latest complete d')`) is explicitly a Phase-2,
  first-invocation-only computation per SS N -- Phase 1 does not compute or
  guess it.
- **COVERAGE.md is overwritten** on each successful run (it is not in rail
  SS S.1's "never overwrite" list, which names only `results/attempt_log.md`
  and `FINDINGS.md`); `results/attempt_log.md` is strictly append-only.
- **`volume_d >= 5` (base stratum)** is treated as a pre-pipeline ELIGIBILITY
  gate (DESIGN SS 3 lists it under "Inclusion", not "Exclusions"), not one of
  the 5 pinned, order-counted exclusion stages -- counted separately and
  disclosed, but not interleaved into the pinned stage-1..5 sequence.

## Extraction summary

- Raw option_prices rows assembled: 99,692,262
- Adjacent pairs (g in [1,4]) pre-eligibility: 84630691
- Eligible ('pairs built': DTE/price-floor/moneyness): 1687348
- Dropped for volume_d < 5 (base-stratum eligibility, NOT one of the pinned 5 stages): 17328433

## Exclusion pipeline (PINNED ORDER, first-match attribution)

| Stage | Excluded (first-match) | Cumulative surviving |
|---|---|---|
| 1 corp_action | 3,764 | 1,683,584 |
| 2 earnings | 142,290 | 1,541,294 |
| 3 bad_print | 296,191 | 1,245,103 |
| 4 iv_fail | 80,513 | 1,164,590 |
| 5 centroid | 330 | 1,164,260 |

## Strike-centroid gate

- Symbol-days evaluated: 60095
- No qualifying strike (UNFLAGGED, retained): 2599
- Flagged (excluded): 71
- Flagged fraction: 0.1181% (STOP threshold: 2.0%)

## Liquid-stratum null-OI disclosure

- Otherwise-liquid pairs (volume_d >= 50): 564317
- ...of which open_interest is NULL: 0 (0.00%)

## TRAIN -- cell-N tables (full detail; TRAIN only per rail)

### base stratum

| cell_id | m_bin | tau_bin | core_grid | n_call | n_put |
|---|---|---|---|---|---|
| m1_t1 | 1 | 1 | False | 19967 | 5164 |
| m1_t2 | 1 | 2 | False | 28283 | 5146 |
| m1_t3 | 1 | 3 | False | 39621 | 7243 |
| m1_t4 | 1 | 4 | False | 82001 | 13292 |
| m2_t1 | 2 | 1 | True | 35345 | 9196 |
| m2_t2 | 2 | 2 | True | 37195 | 8355 |
| m2_t3 | 2 | 3 | True | 43458 | 10584 |
| m2_t4 | 2 | 4 | True | 84483 | 20249 |
| m3_t1 | 3 | 1 | True | 54536 | 28450 |
| m3_t2 | 3 | 2 | True | 45175 | 23713 |
| m3_t3 | 3 | 3 | True | 47882 | 28078 |
| m3_t4 | 3 | 4 | True | 88026 | 55744 |
| m4_t1 | 4 | 1 | True | 24471 | 522 |
| m4_t2 | 4 | 2 | True | 21907 | 1287 |
| m4_t3 | 4 | 3 | True | 24220 | 2561 |
| m4_t4 | 4 | 4 | True | 41935 | 7572 |
| m5_t1 | 5 | 1 | False | 15830 | 5 |
| m5_t2 | 5 | 2 | False | 15528 | 10 |
| m5_t3 | 5 | 3 | False | 19122 | 12 |
| m5_t4 | 5 | 4 | False | 34587 | 56 |

### liquid stratum

| cell_id | m_bin | tau_bin | core_grid | n_call | n_put |
|---|---|---|---|---|---|
| m1_t1 | 1 | 1 | False | 10496 | 1046 |
| m1_t2 | 1 | 2 | False | 13686 | 895 |
| m1_t3 | 1 | 3 | False | 18381 | 1150 |
| m1_t4 | 1 | 4 | False | 35480 | 1944 |
| m2_t1 | 2 | 1 | True | 23147 | 2943 |
| m2_t2 | 2 | 2 | True | 20315 | 1880 |
| m2_t3 | 2 | 3 | True | 22379 | 2038 |
| m2_t4 | 2 | 4 | True | 37724 | 3747 |
| m3_t1 | 3 | 1 | True | 34341 | 14447 |
| m3_t2 | 3 | 2 | True | 24198 | 8566 |
| m3_t3 | 3 | 3 | True | 23625 | 8890 |
| m3_t4 | 3 | 4 | True | 36448 | 15315 |
| m4_t1 | 4 | 1 | True | 10319 | 292 |
| m4_t2 | 4 | 2 | True | 8265 | 531 |
| m4_t3 | 4 | 3 | True | 8390 | 947 |
| m4_t4 | 4 | 4 | True | 12843 | 2183 |
| m5_t1 | 5 | 1 | False | 4361 | 5 |
| m5_t2 | 5 | 2 | False | 4135 | 6 |
| m5_t3 | 5 | 3 | False | 4599 | 8 |
| m5_t4 | 5 | 4 | False | 7847 | 33 |

- TRAIN liquid CALL core-grid ('bars population') N = 261994 (floor: >= 50,000; MET: True)
- OQ-3 boundary-clipped rows (tau_d==5 or m_d==-0.35 exactly, clipped into bin 1; whole panel, both splits): 4916

## OOS -- COUNTS ONLY (critical rail: no means/quantiles/errors ever)

- Total OOS bars-population N: 27314 (floor: >= 20,000)
- Core cells meeting >= 1,000-pair floor: 9 / 12 required >= 6
- OOS floor met: **True**

| cell_id | OOS pair-count (bars population only) |
|---|---|
| m2_t1 | 2864 |
| m2_t2 | 2485 |
| m2_t3 | 2691 |
| m2_t4 | 3757 |
| m3_t1 | 3685 |
| m3_t2 | 2598 |
| m3_t3 | 2523 |
| m3_t4 | 3355 |
| m4_t1 | 919 |
| m4_t2 | 694 |
| m4_t3 | 701 |
| m4_t4 | 1042 |

- Train/OOS boundary-buffer pairs (neither train nor OOS: date_d <= 2026-06-15 < date_dprime): 16240

## Parquet integrity

- train_panel.parquet: 1505983 rows, sha256=72ad204e1024abbe9b7da22311834f142c66371f2f3bf3297a44ab9b120be2a6
- oos_panel.parquet: 165125 rows, sha256=26f5575c0006322c0a9ce1b7417846122aa4e6419fa956370d7e9f00846655f6
