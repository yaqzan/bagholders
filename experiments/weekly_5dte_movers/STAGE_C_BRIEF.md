# STAGE C BRIEF -- weekly_5dte_movers mining + ablation harness (builder agent)

You implement the analysis harness for PREREG.md sections A-F (LOCKED -- read PREREG.md
first, then this brief). Develop and self-test ONLY against the smoke analysis parquet:
`B:\polygon_derived\weekly_5dte_movers\features\_smoke\analysis_smoke.parquet`
(13,454 rows, full column set). Do NOT read `features\analysis_*.parquet` (full-run
files; may be absent/partial while you work) and do NOT submit anything to the queue.
The orchestrator runs `--full` later.

Repo root: C:\Development\Trader. Interpreter: py -3.11 locally; the script must also
run via the explicit `C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe`
(no py-launcher assumptions inside). ASCII-only stdout. Seed everything with 20260817.

## Deliverable

`experiments/weekly_5dte_movers/analyze.py` with:
- `--smoke` : run on the smoke parquet, print the self-test block, exit 0 on green.
- `--full`  : glob `features/analysis_*.parquet`, run everything, write outputs to
  `B:\polygon_derived\weekly_5dte_movers\out\` (tables as .md + .parquet) and a
  machine-written `experiments/weekly_5dte_movers/RESULTS_TABLES.md` (ASCII tables,
  clearly sectioned A-E). The orchestrator writes FINDINGS.md verdicts by hand -- your
  job is complete, correct tables, not interpretation.
- `STAGE_C_REPORT.md`: what you built, self-test results, decisions taken, open questions.

On load (both modes): `from experiments._holdout import assert_no_holdout_leak` and run
it (the frame's date column is `entry_date`; rename to `date` for the assert).

## Population definitions (exact)

- ANALYSIS population: covered==1 AND no_later_print==0 AND tradeable, where tradeable =
  entry_close>=0.20 AND entry_volume>=100 AND entry_transactions>=10 AND adjusted==False.
- WINNER (primary): growth_mult >= 5.0. Sensitivity reruns: >=3.0 and >=10.0 (same
  pipeline, thresholds parameterized; also report the top-0.5%-by-growth_mult cutoff
  value per side).
- RAW view (census only, section A): no floors, covered or not; never used for C/D/E.
- Sides ALWAYS reported separately (cp C vs P) in every table; pooled rows may be added
  alongside, labeled "both".

## Metric families (column mapping -- these exact names exist in the analysis frame)

- F1 geometry: otm_pct, moneyness_pct, premium_over_spot, entry_close, strike,
  dte_calendar, dte_trading, is_monthly_opex, entry_dow.
- F2 tape: entry_volume, entry_transactions, entry_dollar_vol, hl_range_pct,
  close_vs_open_pct.
- F3 underlying state: ret_{1,2,3,5,10,20,60}d, gap_1d, realized_vol_{5,10,20,60}d,
  atr14_pct, dist_from_20d_high, dist_from_20d_low, dist_from_52w_high,
  dist_from_52w_low, dollar_vol_20d_avg, dollar_vol_ratio, prior_week_return.
- F4 MA ladder: sma_{p}_pxrel, ema_{p}_pxrel, sma_{p}_slope5d, ema_{p}_slope5d for p in
  {5,8,9,10,12,20,21,26,34,50,100,150,200}; ma_stack_count; cross_sma5_gt_sma20,
  cross_sma10_gt_sma50, cross_sma20_gt_sma50, cross_sma50_gt_sma200,
  cross_ema12_gt_ema26; days_since_50_200_cross.
- F5 stored indicators: rsi, macd_hist, stoch, bb_pctb.
- F6 score: overall, pre_regime, pre_boost, score_bb, score_trend, score_volume,
  score_rsi, score_macd, score_stoch, score_ma20, score_technical_alignment,
  score_bucket.
- F7 market context: regime_composite, regime_multiplier, vix_close,
  mcclellan_oscillator, trin, ad_diff, spy_ret_5d, spy_ret_20d.
- F8 events/identity: earnings_in_window, days_to_next_earnings,
  days_since_last_earnings, sector, mcap_snapshot_bucket, index_or_etf_underlying.

Null policy: fill_nan -> null once at load; every table reports its non-null N; never
call .rank() on a column that can hold NaN (the polars NaN-is-MAX-rank trap). Columns
with >40% null in the analysis population are excluded from D/E and listed in a
"thin coverage" table instead (days_to_next_earnings may be null-censored near the
window end -- that is expected, keep it but report null share).

## Sections to implement (PREREG protocol, operationalized)

A. CENSUS: growth_mult quantiles (p50/p90/p99/p995/max) by side x expiry-year, raw AND
   tradeable; winner counts at 3x/5x/10x per view; top-100 gallery (tradeable, columns:
   ticker, underlying, cp, strike, entry_date, entry_dow, entry_close, entry_volume,
   growth_mult, max_high_date, earnings_in_window, otm_pct, overall).
B. BASE RATES: P(winner) overall and by side / entry_dow / expiry-year, with N per cell.
C. UNIVARIATE: for every numeric metric above -- winner rate per decile of the ANALYSIS
   population (deciles computed per side), lift vs that side's base rate, z-score
   (two-proportion), N per decile; min cell N=200 (merge adjacent deciles below that).
   Categoricals (sector, mcap_snapshot_bucket, score_bucket, entry_dow, booleans):
   per-level winner rate/lift/z with the same min-N merge into "other". Output: one
   ranked table of |z_max| per metric (top-40) + full per-decile tables to parquet.
   Count and print the TOTAL number of metrics tested (the multiple-comparisons
   denominator for FINDINGS).
D. FACTORS: Spearman correlation matrix on numeric metrics (analysis population,
   per-side then pooled); hierarchical/greedy clustering merging |rho|>0.8; name each
   cluster by its most univariately-discriminative member; emit cluster membership
   table. Then models on cluster representatives (one column per cluster, the named
   member): (i) logistic regression (standardize train-only: median/IQR), (ii)
   HistGradientBoostingClassifier(max_depth=3) if sklearn is importable, else skip GBT
   and log it. CV: year-blocked 5-fold (2022H2, 2023, 2024, 2025, 2026H1) -- train on 4
   groups, test the held-out group; report per-fold and mean AUC per side, plus a
   shuffled-label control AUC (same folds, labels permuted with seed) as the noise
   envelope.
E. ABLATION:
   1. Leave-family-out: refit the D models with each family F1..F8 removed entirely;
      report delta mean AUC and delta lift@top-decile (score the test folds, take the
      top 10% by predicted probability, winner rate there vs base).
   2. Leave-metric-out: for the top-10 single metrics by |z| from C, refit dropping
      just that metric; same deltas.
   3. RULE DISTILLATION: from C's top discriminators and D's GBT (if available), define
      3-6 explicit human-readable rules as boolean predicates (e.g. "C-side AND otm_pct
      in [0.05,0.15] AND realized_vol_20d top-quintile AND earnings_in_window==1").
      Choose rules that are simple (<=4 conjuncts), high-lift, N>=1000 in the analysis
      population. For EACH rule emit the robustness grid: winner-rate lift + N in every
      slice of {expiry-year x side x entry_dow x threshold(3x/5x/10x) x floors(raw incl
      covered-only vs tradeable) x ex-earnings-week x ex-index-etf}. Apply PREREG's HOLD
      criterion mechanically and stamp HOLD / PARTIAL / FAILS per rule (>=4/5 year
      slices same direction AND both adjacent thresholds AND survives ex-earnings unless
      the rule includes earnings).
   Every ablation table states N and the direction of the delta; no interpretation prose.

## Self-tests (--smoke must hard-assert, print [PASS]/[FAIL], exit accordingly)

1. Population counts: recompute the tradeable filter by hand on the raw smoke frame and
   assert equality with the pipeline's analysis population count.
2. Winner counts at 3x/5x/10x match a direct polars filter count.
3. Decile machinery: a synthetic column (growth_mult itself as a feature) must produce a
   perfectly monotone winner-rate ladder ending at 100% in the top decile -- sanity that
   lift/decile code is not scrambled.
4. Shuffled-label AUC in [0.45, 0.55] on smoke (labels permuted, seed 20260817).
5. Correlation clustering: sma_20_pxrel and sma_21_pxrel must land in the SAME cluster
   (|rho|>0.8 by construction); assert.
6. Leave-family-out plumbing: removing a family actually removes its columns (assert
   feature-count drops by the family's cardinality).
7. assert_no_holdout_leak passes on load.
Smoke rows are few (~11k covered) -- self-tests validate MACHINERY, not findings; do not
tune anything to smoke results. Where a smoke cell is under min-N, the merge logic
should visibly fire (assert it does at least once on smoke).

## Traps (forwarded)

- ASCII-only stdout; PYTHONIOENCODING=utf-8 defensively set.
- polars: NaN vs null distinction (fill_nan first); .rank() NaN=MAX; infer_schema_length
  irrelevant here (parquet), but keep dtype casts explicit.
- Never read anything under features/ except the _smoke file until --full; --full globs
  analysis_*.parquet only (never the smoke file -- exclude the _smoke dir).
- sklearn may or may not exist for 3.11 -- probe with importlib, degrade gracefully,
  REPORT which path ran.
- Year-blocked CV groups by expiry-year (2022 group is H2-only, 2026 is H1-only --
  smaller folds, that is fine; never split a year across train/test).
- Runtime budget --full: the analysis population is ~1.2-1.7M rows x ~140 numeric cols.
  Everything here is column-sliced polars/numpy; avoid per-row python loops except the
  rule grid (which is vectorized boolean masks anyway). Target < 15 min single-process.
- Timebox ~60-90 min. Blockers >15 min -> STAGE_C_REPORT.md OPEN QUESTIONS +
  conservative fallback, keep moving.
