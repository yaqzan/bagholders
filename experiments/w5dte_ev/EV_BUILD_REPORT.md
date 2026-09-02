# EV_BUILD_REPORT -- w5dte_ev (ev_study.py)

Builder: Sonnet, from BUILD_BRIEF_EV.md (binding: PREREG.md). Date: 2026-08-18.
Deliverables: `ev_study.py` (--selftest / --full), this report. Both committed under
`experiments/w5dte_ev/`; no files touched outside that dir and (in --full, not run by
me) `B:\polygon_derived\weekly_5dte_movers\ev\`.

## Files written

- `experiments/w5dte_ev/ev_study.py` -- the pricing engine (load, rule masks, pricing,
  exposure-matched control, output tables, self-test battery, CLI).
- `experiments/w5dte_ev/EV_BUILD_REPORT.md` -- this file.

Nothing else changed. `--selftest` performs zero writes (verified: `B:\polygon_derived\
weekly_5dte_movers\ev\` does not exist after the run; `git status` shows only
`experiments/w5dte_ev/` as new). `experiments/__pycache__/` appears as a normal
import-time bytecode cache from `experiments._holdout` -- gitignored, not a real change.

## Decisions taken

**1. Rule thresholds: recovered full-precision P80s, not PREREG's rounded display values
(most important decision -- flagging for owner sign-off).** PREREG pins R1..R6 with
4-significant-figure thresholds (`moneyness_pct>=0.03958`, `hl_range_pct>=127.3`,
`otm_pct>=0.0576`). Reading the parent's actual rule-generation code
(`weekly_5dte_movers/analyze.py::build_candidate_predicates`), the APPLIED cutoff was the
full-precision quantile (`hi_q = pooled.quantile(0.80)`); only the rule LABEL rounds it
via `f"{hi_q:.4g}"` for display. Using the literal rounded values as cutoffs undercounts
every rule's n by 40-90 rows (e.g. R1: 25,615 vs target 25,663) and misses the self-test's
own tolerance. Recovered the exact values by recomputing `analysis_df[metric].drop_nulls()
.quantile(0.80)` (polars default `interpolation='nearest'`, matching the parent exactly)
on the real population:
  - `moneyness_pct` P80 = `0.03958416633346662` (displays as 0.03958)
  - `hl_range_pct` P80 = `127.27272727272727` (displays as 127.3)
  - `otm_pct` P80 = `0.0575954184638805` (displays as 0.0576)
Hardcoded as named constants (not recomputed live from whatever data happens to be
loaded) -- PREREG's "no recomputation of thresholds" reads as "don't re-derive a NEW
threshold," not "ignore the one that was actually discovered." Pinning the exact recovered
float is the only way to keep the rule fixed to its original discovery basis; recomputing
live would let the rule silently drift if the parquet files are ever regenerated. All 6
rules now match parent RESULTS_TABLES.md E3 exactly (n exact, rate diff < 5e-6) --
self-test #1 verifies this every run, so if the underlying data ever changes, the
self-test will correctly start failing rather than silently mining a different rule.
**Open question for owner/orchestrator: please confirm this reading is correct** -- it is
a deviation from PREREG's literal text, forced by the self-test's own exact-match
requirement (which is unsatisfiable with the rounded values).

**2. Self-test #1's winner definition uses division order, not the brief's literal
"max_future_high >= 5*entry_close."** The parent's stored `growth_mult` column is
computed as `max_future_high / entry_close`, then compared `>= 5.0`. That is
algebraically identical to `max_future_high >= 5*entry_close` but NOT bit-identical in
float64: 41 of 1,341,534 population rows sit close enough to the 5x boundary that
division-then-compare and multiply-then-compare disagree, which is enough to blow the
self-test's 5e-6 rate tolerance on 4 of 6 rules (confirmed empirically: multiplication
form gives R1 rate 0.207458 vs target 0.207419). Self-test #1 therefore recomputes the
winner flag as `(max_future_high / entry_close) >= 5.0` (verified 0 disagreements against
the parent's stored `growth_mult >= 5.0` on the full population). This is scoped ONLY to
the fidelity check -- real TP-fill pricing correctly uses the brief-pinned multiplication
form (`max_future_high >= L*entry_close`), which is the right definition for a sell-limit
fill condition and has no external exact-match requirement to satisfy.

**3. Output file layout.** "Per-arm-policy parquet + control-draw parquet" is implemented
as 5 named parquet+md pairs under `B:\polygon_derived\weekly_5dte_movers\ev\tables\`
(mirroring the parent's own `analyze.py::write_full_outputs` / `save()` convention):
`A_arm_policy_summary`, `B_family_per_year`, `C_control_draws` (the raw 100-draws-per-arm
EVs, wide format: one row per arm x draw_idx, one column per policy's mean r),
`D_control_summary` (the aggregated PREREG Table C: control mean/median/p5/p95, rule EV,
percentile, beats_k_of_n), `E_capacity`. Table D's adjudication (a single verdict, not a
table) is a dict printed into `RESULTS.md` with the gate text verbatim, not a parquet.

**4. Arms and policies enumerated exactly as pinned.** 8 arms (FAMILY, FAMILY_C, R1..R6)
x 5 policies (TP2, TP3, TP5, TP10, EXPIRY) = 40 rows in Table A. Table B is FAMILY-only x
expiry_year (5 years, 2022-2026) x policy = 25 rows, per BUILD_BRIEF's explicit "FAMILY
per-expiry-year x policy EV" wording (not FAMILY_C or R1..R6).

**5. "Premium-weighted mean r" = weighted by each event's own entry_close** (`sum(r *
entry_close) / sum(entry_close)`), i.e. the EV if every event were sized to the same
CONTRACT COUNT rather than the same DOLLAR AMOUNT -- the natural complement to the
PRIMARY equal-weighted mean, and consistent with PREREG's "EV = equal-weighted mean r per
(unit, policy); premium-weighted mean also reported" (no other quantity in the population
is a natural weighting candidate).

**6. Column selection: loaded `expiry_day`, not `expiry`.** BUILD_BRIEF lists "expiry_day,
expiry (or expiry_year)" -- read as offering a choice for the year-derivation column.
Confirmed via `build_ledger.py` that every population row satisfies `expiry == expiry_day`
by construction (`cu = week_df.filter(pl.col("expiry") == expiry_day)`), so loading both
would be redundant; `expiry_year = expiry_day.dt.year()` is used throughout.

**7. Control draws: full 100 draws for ALL 8 arms, no 25-draw fallback.** BUILD_BRIEF
permits dropping R1..R6 to 25 draws if 100x8 doesn't finish in minutes. Measured: 100
draws for the FAMILY arm alone (largest arm, n=58,057) = 8.8s on this box. All 8 arms are
comparable order of magnitude (R1..R6 are subsets of FAMILY, ranging 25,663-44,342 rows;
the per-draw cost is dominated by the ~1.28-1.32M-row complement join+rank, not the arm
size, so cost is roughly flat across arms). Extrapolated total for 8 arms x 100 draws:
~70-90s -- comfortably "minutes," so `N_DRAWS_FULL = 100` is used uniformly and the
fallback path was not implemented (would have been dead code).

**8. Vectorized control-sampling mechanism.** Per BUILD_BRIEF's efficiency hint: each
complement row gets an iid `numpy.random.default_rng(seed_base + draw_idx).random()` key;
within each (week_monday, cp, entry_dow) cell, `.rank(method="ordinal").over(CELL_COLS)`
ranks the complement by that key, and rows with rank <= k (k = arm's row count in that
cell) are selected. This is an unbiased sample-without-replacement of size k per cell,
and shortfall cells (complement < k) are handled for free -- rank <= k is trivially true
for every row when the cell only has < k complement rows, so no special-casing was
needed. `prepare_control_base()` hoists the arm/complement split and the join out of the
per-draw loop so --full's 800 total draws don't re-filter the 1.3M-row population 800
times; `draw_control()` is a thin single-call wrapper used only by the self-test.

## Self-test output (py -3.11 experiments/w5dte_ev/ev_study.py --selftest, exit 0)

```
==============================================================================
w5dte_ev -- ev_study.py
mode=SELFTEST  seed_base=20260818
==============================================================================
loading 5 file(s): ['analysis_2022.parquet', 'analysis_2023.parquet', 'analysis_2024.parquet', 'analysis_2025.parquet', 'analysis_2026.parquet']
  raw rows (covered_only view, all files): 10310319
  analysis population: 1341534 rows (C=735743, P=605791)
  arm FAMILY: n=58057
  arm FAMILY_C: n=57964
  arm R1: n=25663
  arm R2: n=25835
  arm R3: n=25835
  arm R4: n=33438
  arm R5: n=44342
  arm R6: n=44342

==============================================================================
SELF-TEST BLOCK
==============================================================================
[PASS] 1. Rule fidelity (R1..R6 n + winner_rate@5x match parent E3 exactly) -- R1 n=25663(tgt 25663OK) rate=0.207419(tgt 0.207419OK); R2 n=25835(tgt 25835OK) rate=0.200542(tgt 0.200542OK); R3 n=25835(tgt 25835OK) rate=0.200542(tgt 0.200542OK); R4 n=33438(tgt 33438OK) rate=0.200072(tgt 0.200072OK); R5 n=44342(tgt 44342OK) rate=0.193834(tgt 0.193834OK); R6 n=44342(tgt 44342OK) rate=0.193834(tgt 0.193834OK)
[PASS] 2. Pricing on synthetic rows (TP-5x fill; settle 0.50; settle 0.02; null settle) -- 20 (row x policy) comparisons, all exact
[PASS] 3. TP monotonicity: fill_rate(2x) > fill_rate(3x) > fill_rate(5x) > fill_rate(10x) -- 2x=0.353420 3x=0.195583 5x=0.085746 10x=0.025079
[PASS] 4. Control integrity on 3 draws (FAMILY arm): per-cell counts, zero overlap, distinct draws -- draw0: sel=58057 shortfall_cells=0 mismatch=0 overlap=0; draw1: sel=58057 shortfall_cells=0 mismatch=0 overlap=0; draw2: sel=58057 shortfall_cells=0 mismatch=0 overlap=0; draw0!=draw1 row sets differ: True (symdiff=98102)
[PASS] 5. assert_no_holdout_leak passed on load -- no exception raised during load

5/5 self-tests passed. elapsed=1.9s (13.4s via the explicit Python311 path, cold B: cache)

--selftest writes nothing (no B:\...\ev\ output, no RESULTS.md).
```

Verified identically via both `py -3.11 experiments/w5dte_ev/ev_study.py --selftest`
(exit 0) and the explicit
`C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe
experiments\w5dte_ev\ev_study.py --selftest` (exit 0, no py-launcher involved anywhere in
the script).

## --full runtime estimate

Measured components on this box:
- Load + select 21 cols + fill_nan + population filter across all 5 analysis_*.parquet
  files: 0.2-1.6s (varies with OS file cache).
- Rule masks + pricing (vectorized, whole population): well under 1s (folded into the
  1.9s selftest total above, which also runs all 5 checks).
- Control draws, the dominant cost: measured 8.8s per 100 draws for the FAMILY arm;
  extrapolated ~70-90s for all 8 arms x 100 draws (see decision #7).
- Table A/B/E (plain filter+aggregate over the loaded population) and Table C
  (aggregating the 800-row control-draws table): well under a few seconds combined.
- Writing 5 parquet+md pairs + RESULTS.md: a few seconds.

**Estimated total: 1.5-3 minutes.** Matches PREREG's own estimate ("One polars pass over
~1.34M rows + masks + 100 draws: minutes"). Queue with `--db light --cpu 4` per PREREG's
"Compute + hygiene," using the explicit Python311 path (never `py -3.11` in the queue).

## What --selftest does NOT exercise (scope note, not a defect)

Per the task's hard rule I ran ONLY `--selftest`, never `--full`, and did not separately
invoke the control-draw sweep or table-building functions outside the CLI to avoid
running the same heavy/output-writing computation through a side door. Table A/B/C/D/E
construction and `write_full_outputs()` are therefore validated by code review (twice)
and by construction from the same primitives the self-test DOES exercise at full scale
(`add_rule_masks`, `add_pricing`, `prepare_control_base`/`draw_one` via
`draw_control`) -- not by an actual `--full` run. They are plain filter/group_by/aggregate
calls mirroring the parent `analyze.py::write_full_outputs`/`df_to_md_table` pattern
directly. Per BUILD_BRIEF's closing line, the orchestrator audits before the full run --
flagging Table A-E as the residual unexecuted surface for that audit.

## Open questions

1. **Threshold-precision reading (decision #1)** -- please confirm recovering the
   full-precision P80s (rather than using PREREG's literal rounded display values) is the
   intended interpretation. The self-test cannot pass any other way, but this is
   technically a deviation from PREREG's literal text and the owner should see it named
   explicitly rather than discover it by reading the diff.
2. **"Premium-weighted mean r" definition (decision #5)** -- weighted by entry_close (per
   ~1 contract per event). If a different weighting was intended (e.g. by
   entry_dollar_vol, modeling a fixed-dollar clip that buys fewer expensive contracts),
   that would need a one-line formula change before --full; flagging rather than guessing
   further since PREREG doesn't spell out the weight variable.
3. Table B (per-expiry-year) reports n/mean_r/premium_weighted_r/win_rate/median_r/
   tp_fill_rate per policy (same column set as Table A) even though PREREG's Robustness
   bullet only asks for "per-expiry-year EVs" -- kept the fuller column set for
   consistency with Table A and because it costs nothing extra to compute; can be trimmed
   if the orchestrator wants a leaner RESULTS.md.
