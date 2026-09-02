# Per-Trade Engine Validation (F2-gamma vs production) -- DESIGN (pre-registered)

**Status: APPROVED AS AMENDED 2026-07-12 (FABLE). See AMENDMENTS section below (A1-A3) --
these are binding modifications applied BEFORE any compute, not optional guidance. Execution
proceeds via build_ledger.py (this document + recon.py + recon_results.{txt,json} +
build_ledger.py + the ledger parquet are the artifacts). No engine edits, no commits under
this document's authorship.**

## AMENDMENTS (2026-07-12, FABLE)

Binding modifications to the design below, applied BEFORE any compute. Where an amendment
conflicts with earlier prose in this document (sections 0-8), the amendment governs.

**A1 (bar evaluation + power honesty):** M1/M2/M3 bars are EVALUATED ON THE LIQUID-PRIMARY
STRATUM (volume>=5 at the chosen contract on entry date); the all-inclusive stratum is reported
as a robustness line WITH the lastPrice-staleness caveat, never as the deciding read. If a
bar's liquid-primary clustered CI cannot decide (spans both pass and meaningful degradation),
that bar's outcome is UNDERPOWERED-PENDING-DATA -- a pre-registered branch, not a failure: the
panel grows ~daily; state the projected N at +60 and +120 trading days in the results so the
re-read date is mechanical. Do not substitute the all-inclusive stratum to force a decision.

**A2 (forward-path freshness guard):** a path checkpoint (d5/d10/d15) is VALID only if a fresh
quote (volume>0) exists within +/-1 trading day of it; first-touch TP/SL events count only on
fresh-quote days. Per-trade stale-path fraction is a ledger column; report the distribution and
the fraction of trades losing each checkpoint. This is distinct from the ripeness (>=15-row)
guard -- both apply.

**A3 (telemetry):** alongside M1, report the implied best-fit ATM mapping constant vs the 0.4
Brenner-Subrahmanyam approximation (report-only, no retuning), and the realized F2-vs-real-
premium bias by RV tercile ON REAL CONTRACTS.

## 0. Goal + lineage

`experiments/iv_premium_model/VERDICT.md` ruling 2: the F2-priced gamma engine (`GAMMA_AWARE` +
`IV_PREMIUM`+`IV_MODEL`, both committed default-OFF) is measurement-valid and PASSED its
pre-registered A/B bars, but that A/B was engine-vs-engine (paired MC sweep), not
engine-vs-reality. Adoption (a future default-ON re-baseline) is explicitly NOT licensed by
that A/B. Its own gate for adoption: **per-trade validation of the model-priced engine against
REALIZED option P&L on our own `option_prices` MySQL panel** -- does the F2-priced engine track
real fills better than the RV-priced engine, per trade? This document is that pre-registration.

This is **instrument fidelity, not alpha mining** (gameplan section 7): no `ALGORITHM_VERSION`
bump, no score change, no portfolio-stage change. The eventual PASS/FAIL only advances or closes
an **adoption candidacy** for a later, separate, FABLE+user-visible re-baseline step (flip the
pair ON, re-run the canonical N=500 ship gates, audit which gate DECISIONS flip). Nothing in
this design licenses flipping `GAMMA_AWARE`/`IV_PREMIUM`/`IV_MODEL` in production.

The four arms (same matched real contract, same real underlying path):

| arm | premium model | P&L model | role |
|---|---|---|---|
| production | RV: `PREMIUM_MULT * realized_vol/100` | const-delta (`option_pnl_pct`) | shipped baseline |
| RV+gamma | RV | gamma-BS (`bs_option_pnl_pct`) | diagnostic (isolates P&L-model axis) |
| F2+const | F2 (see formula below) | const-delta | diagnostic (isolates premium axis) |
| candidate | F2 | gamma-BS | adoption candidate |

F2 formula (verbatim from `monte_carlo.py`'s already-committed, default-OFF `_iv_premium_pct`
model branch): `atm_iv_hat = clamp(0.14920 + 0.09545*RV + 0.006242*VIX, [0.163326, 1.758980])`;
`premium_pct = 0.4 * atm_iv_hat * sqrt(DTE/365)`, `DTE` = the matched contract's own DTE (not a
fixed nominal 30). RV = engine-faithful `realized_vol` (60-bar trailing population-stdev of daily
returns, `x100`), VIX = `MarketRegime.vix_close` at signal date.

**Real functions imported, never reimplemented**, per the mandate:
- `option_pricing.option_pnl_pct`, `option_pricing.bs_option_pnl_pct`, `option_pricing.DEFAULT_DELTA`
  (=0.5) -- called directly for both P&L arms (no `GAMMA_AWARE` env flip needed: both functions are
  independently callable regardless of that flag, exactly as `experiments/gamma_validation/validate_gamma.py`
  already does).
- `monte_carlo.realized_vol` (bit-identical RV) and `monte_carlo.PREMIUM_MULT` (=1.82 for the 30DTE
  config) -- used directly for the RV-premium arm (this reproduces `monte_carlo.py`'s own inline
  `premium_pct = PREMIUM_MULT * vol / 100`, line ~2062; it is DTE-invariant in production, so using
  it as-is on 20-45 DTE contracts is a faithful reproduction of what the engine actually does for
  any DTE under `STRATEGY_30DTE`, not an approximation).
- `monte_carlo._iv_premium_pct` -- called DIRECTLY for the F2-premium arm, with `IV_PREMIUM=1` and
  `IV_MODEL=1` set in `os.environ` **before** `import monte_carlo` (these are module-load-time
  constants; the ledger-build script's first lines must set them, matching how the already-proven
  `IV_PREMIUM`/`GAMMA_AWARE` plugs are exercised in `run_gamma_ab.py`/`build_fit_model.py`). This
  reuses the exact production code path bit-for-bit, including its clamp and telemetry counters
  (`_MODEL_HITS`/`_MODEL_MISSES`/`_MODEL_CLAMP_FLOOR_HITS`/`_MODEL_CLAMP_CAP_HITS`).

---

## 1. RECON -- coverage + power table (the headline numbers)

Produced by `experiments/iv_engine_pertrade/recon.py` (read-only; reuses cached parquets wherever
possible; a handful of small/bounded live MySQL touches, all timed). Full output:
`experiments/iv_engine_pertrade/recon_results.{txt,json}`.

### 1a. `option_prices` / `options` schema + coverage

| metric | value |
|---|---|
| `option_prices` date range | 2025-02-10 .. 2026-07-10 |
| `option_prices` approx rows (information_schema) | ~89.9M (grown from the "~62M" figure quoted in older docs) |
| `options` approx rows | ~2.15M contracts |
| `options` distinct symbols WITH any chain | **776** |
| `options` expiration range | 2025-02-14 .. 2028-12-15 (some LEAPS) |

**776 distinct underlyings have any option chain at all** -- this is well below the full scoring
universe (~2000+ symbols per traps.md), so a material share of the match-rate shortfall below is
simply "this symbol has no chain," not a date/DTE/moneyness miss.

### 1b. Liquidity picture (volume/OI distributions, zero-volume fraction) -- table-wide

Monthly chunks (indexed by `date`, each independently timed; 16/18 months took >5s, up to 25s in
2026-06 as the table has grown -- **flagged as borderline for "light foreground"**; a full re-scan
at this granularity should go through the queue, not be repeated live):

| year-month | rows | distinct contracts | zero-volume frac | zero-OI frac | avg volume |
|---|---|---|---|---|---|
| 2025-02 | 3.19M | 337K | 78.2% | 21.3% | 30.7 |
| 2025-06 | 3.82M | 422K | 78.7% | 16.4% | 34.9 |
| 2025-10 | 5.54M | 500K | 76.5% | 6.9% | 42.9 |
| 2026-02 | 5.15M | 517K | 78.2% | 7.4% | 31.2 |
| 2026-06 | 10.91M | 798K | 79.2% | 9.5% | 28.1 |
| 2026-07 (partial) | 4.05M | 638K | 80.3% | 12.4% | 22.6 |

(full 18-month table in `recon_results.json["option_prices_monthly"]`)

**~76-81% of ALL option_prices quotes have zero volume, every month, no trend.** Zero-OI is much
lower (7-21%) -- most listed contracts DO carry open interest, they just didn't trade on any given
day. Confirms the task's short_put_hedge-lesson concern directly: illiquid/stale quotes are the
MAJORITY of this table, not a tail case.

**Quote-basis finding (source-verified, `trader.py:1202-1220`):** `option_prices.price` is
yfinance's `lastPrice`, pulled once per symbol per day by `pull_options()` (invoked from the
post-close `trader close-update` pipeline). There is no bid/ask/mid in this schema -- only one
daily snapshot. **On a zero-volume day, `lastPrice` is NOT a fresh EOD mark -- it is whatever the
option's last actual trade was, which can be stale by several days.** Combined with the ~78%
zero-volume base rate, this means a large share of "entry premium" values in ANY ledger built off
this table are stale carry-over prices, not same-day marks. This is why the ledger persists
`entry_volume`/`entry_open_interest` and why liquidity-stratification (section 3) is not optional
polish -- it is the difference between "real premium" and "stale premium mislabeled as real."

### 1c. Signal population (denominator) + matched-chain numerator (the power table)

Active scoring version confirmed: `id=74`, commit `f9fb7b934` (matches CLAUDE.md).

Denominator: candidate 70+ CALL signals, `date` in `[2025-02-10, 2026-06-15]` (CALIBRATION_CUTOFF_DATE,
hard cap), version_id=74. Sourced from `.cache/rel_strength/rs_ledger.parquet` (thru 2026-05-15) +
one live `scores` query for the one-month gap it doesn't cover (2026-05-16..06-15, fast, 0.05s).

Numerator: matched ATM-call rows from `.cache/iv_skew/iv_ledger.parquet` (thru 2026-05-15) +
`.cache/iv_skew/iv_ledger_ext.parquet` filtered `is_oos==False` (the 2026-05-16..06-15 gap) --
these already applied `atm_call()`'s exact selection (nearest-moneyness CALL, DTE 20-45,
`iv in [0.05,5.0]`, `price>0`, `open_interest>0`) and the (now-fixed, see section 6) honest
`pnl15` guard.

| band | candidate (denominator) | matched (numerator) | match rate | matched AND ripe (pnl15 not null) |
|---|---|---|---|---|---|
| 75+ | 991 | 564 | 56.9% | 564 (~100%) |
| 70-74 | 4,252 | 1,996 | 46.9% | 1,994 (~100%) |
| **total** | **5,243** | **2,560** | **48.8%** | **2,558** |

(Caught and fixed during this recon: the first pass under-counted the denominator by ~285 rows --
a naive `{**a, **b}` dict merge of the rs_ledger-derived month buckets and the live gap-query month
buckets silently OVERWROTE rather than ADDED the two half-month "2026-05" contributions, since both
sources independently produce a "2026-05" key. Fixed in `recon.py` to merge additively; re-ran and
these are the corrected totals. Worth remembering as its own small trap: any month-bucketed merge
from two date-disjoint-but-calendar-overlapping sources needs an additive merge, not `{**a,**b}`.)

**Liquidity stratum** (reconstructed by re-deriving `atm_call()`'s exact selection logic in polars
against `.cache/experiment_data/option_slice_ivledger.parquet`'s +/-25%-moneyness chain slice --
zero new MySQL cost; N=1,980 reconstructed rows vs iv_ledger's 2,018 for the same window, a ~98%
self-consistency check that the reconstruction matches the live SQL selection):

| stratum | N (of 1,980, thru 2026-05-15 portion only) | frac |
|---|---|---|
| zero-volume | 1,509 | 76.2% |
| volume < 5 | 1,608 | 81.2% |
| **volume >= 5 (liquid, primary)** | **372** | **18.8%** |

Extrapolating the 18.8% liquid-stratum rate to the full 2,560-row matched population (the gap
month's own liquidity was not separately re-derived -- would need one more query pass, deferred to
the ledger build): **estimated liquid-stratum headline N ~ 480**, all-inclusive N ~2,560.

**This is the actual power picture for the pre-registered bars: ~2,560 matched-and-ripe trades in
the secondary (all-inclusive) stratum, ~480 in the primary (liquid) stratum.** The liquid stratum,
further cut by RV tercile (3-way) x era (2-way) = 6 cells, averages ~80/cell but will be uneven;
expect some cells near or below the `N<30` SKIP floor -- pre-registered as SKIP, not
extrapolated (section 3).

### 1d. VIX join availability

`MarketRegime.vix_close` checked against every unique signal date in the recon population (337
distinct dates) via the already-cached `.cache/iv_premium_model/vix_series.parquet`
(1995-01-03..2026-07-10, zero new MySQL): **337/337 = 100.00%** hit. Confirms CLAUDE.md's "gapless
1995..now" claim for this specific window; no VIX-driven attrition expected in the ledger build.

---

## 2. Prior art -- what's reused vs what's new

| script | what it already does | reused how |
|---|---|---|
| `experiments/gamma_validation/validate_gamma.py` | Per-trade CONST vs GAMMA P&L comparison against real `option_prices`, using the REAL empirical entry premium (`V0/U0`) as `premium_pct` for BOTH arms -- isolates ONLY the delta/gamma axis. Vega ratio on earnings-spanning trades = REAL empirical `iv_t/pre_iv` (clamped [0.2,2.0]), not a sampled draw. Reports by move-bucket, side, bar. | Direct precedent for M2's P&L-fidelity mechanics (bar-by-bar `option_pnl_pct`/`bs_option_pnl_pct` calls, vega-ratio convention, earnings-span detection). **What's new:** this experiment does NOT hold premium_pct at the real value -- it also swaps in the RV- or F2-ESTIMATED premium (what the engine actually uses in production, which never sees the real premium), so BOTH axes (premium source x P&L model) vary, not just one. That is the actual adoption-gate question; validate_gamma.py answered a narrower one (given the true premium, does gamma describe the path better). |
| `experiments/option_pricing_validation.py` | Older/simpler version of the same real-premium-held-fixed pattern; also the source of `load_forward_option_prices`/`load_forward_underlying`/`load_earnings_dates` helpers reused (imported, not copied) by validate_gamma.py. | Same relationship as above -- reused for load-forward-path patterns and the RMSE/MAE/bias reporting convention. |
| `experiments/iv_skew/build_iv.py` (+ `build_iv_ext.py`) | `atm_call()` (the exact nearest-moneyness CALL 20-45 DTE selection SQL), `fwd_pnl()` (the honest 15-row guard, **FIXED 2026-07-08**, commit `445658866` -- nulls `pnl15` instead of truncate-substituting the last available price), the 70+ signal-population convention. | Selection SQL + honest-guard PATTERN reused (not the parquet itself, which lacks the richer columns this ledger needs). **What's new / the OSK trap this design fixes:** neither `build_iv.py` nor `build_iv_ext.py` PERSISTS which contract was selected (`option_id`/strike/expiration) -- only derived values (`atm_iv`, `entry_premium`) land in the parquet. `project_osk_regime_conditional.md` documents the exact cost of this: "build_iv ledgers don't persist strike/DTE selection (transient SQL -- cross-source audits blocked)" and a recipe-divergence check that alone moved a correlation from ~0.11 to 0.199 depending on which selection recipe was used. **This ledger MUST persist the full selection** (`option_id`, `strike`, `expiration_date`, `dte_at_entry`, a `selection_rule` version string) so it can be audited/reproduced later without re-running live SQL against a table that keeps growing. |
| `experiments/gex/gex_test.py` | `ols_with_clustered_se()` -- plain OLS + date-clustered sandwich SE, `MIN_CELL_N=30` SKIP convention, `zscore()`/quintile helpers. | `ols_with_clustered_se` reused VERBATIM (import, not reimplement) for the clustered-t robustness check on each pre-registered bar's paired difference (section 3). `MIN_CELL_N=30` SKIP convention reused directly. |
| `experiments/_holdout.py` | `assert_no_holdout_leak`, `CUTOFF` (reads `strategy_config.CALIBRATION_CUTOFF_DATE` live, not hardcoded). | Called on the ledger's in-sample slice before any M1-M3 read. |
| `database/bulk_cache.py` | `materialize_polars`, `chunked_query_by_year` -- the house on-demand-parquet-cache pattern. | Ledger build writes to `.cache/iv_engine_pertrade/` via this exact pattern (see section 5). |
| `experiments/iv_premium_model/build_fit_model.py` | Bulk `fetch_price_history(symbols, start, end)` -- ONE query per symbol-set, not per-signal -- and the engine-faithful `realized_vol` port. | Bulk price-history pull pattern reused for the ledger build's RV computation AND its forward-underlying-path walk (see section 5's optimization note vs `build_option_slice.py`'s per-signal underlying queries). |

**Net new work this experiment adds, that no prior script does:** (1) swaps in the ESTIMATED
(RV/F2) premium rather than holding the real premium fixed -- the actual production-fidelity
question; (2) persists full contract selection (fixes the OSK trap); (3) computes first-touch
TP(+30%)/SL(-70%) EVENTS (not just a d15 scalar) for both the real path and all four arms; (4)
liquidity-stratifies the result (volume>=5 primary / all-inclusive secondary) rather than
reporting one pooled number; (5) validates the `0.4` Brenner-Subrahmanyam mapping constant itself
against real premiums (section 4).

---

## 3. Ledger spec

**Grain:** one row per matched `(symbol, signal_date)` 70+ CALL signal (band in `{75+, 70-74}`),
`signal_date <= CALIBRATION_CUTOFF_DATE` (2026-06-15). A separate, clearly-labeled
`is_shadow_oos=True` slice (`signal_date > CUTOFF`, up through whatever the option_prices max date
supports) is captured too, but **excluded from every M1-M3 statistic and from any selection** --
watch-only, descriptive appendix, matching the `iv_ledger_ext.parquet` `is_oos` precedent.

### Persisted selection (fixes the OSK non-persistence trap)
- `symbol`, `date` (signal/entry date), `overall`, `band` (`75+`|`70-74`)
- `option_id`, `strike`, `expiration_date`, `dte_at_entry` (`DATEDIFF(expiration_date, date)`)
- `selection_rule` = constant string `"nearest_moneyness_call_dte20_45_iv.05-5_price>0_oi>0_v1"`
  (bump the `v1` if the rule ever changes -- this string alone must be enough to reproduce the row)
- `entry_quote_basis` = constant string `"yfinance_lastPrice_postclose_pull"` (documents the
  section 1b finding -- there is no mid/close choice in this data, only this one daily snapshot)

### Entry state
- `entry_close` (U0, `price_history.close` on signal_date)
- `entry_premium` (V0, `option_prices.price` on signal_date -- the raw lastPrice quote)
- `entry_iv` (`option_prices.iv` on signal_date -- the REAL market-quoted implied vol)
- `entry_volume`, `entry_open_interest` (of the CHOSEN contract, at entry)
- `liquid_ge5` (bool, `entry_volume >= 5`), `liquid_gt0` (bool, `entry_volume > 0`)
- `rv` (engine-faithful `realized_vol`, 60-bar lookback, via `monte_carlo.realized_vol`)
- `vix` (`MarketRegime.vix_close` at signal_date)
- `rv_tercile` (computed fresh on THIS ledger's own `rv` distribution -- method reused from
  `iv_premium_model`, cut VALUES recomputed, not copied)
- `era` (`"2025"` if `date.year==2025` else `"2026H1"`)
- `earnings_span_flag` (bool -- any `EarningsDate` row for `symbol` in `(signal_date, signal_date+15tradingbars]`)

### Real forward path (honest-guarded)
- `fwd_rows_available` (count of `option_prices` rows found in the forward window)
- `ripe15` (bool: `fwd_rows_available >= 15` -- the fixed `build_iv.py` guard, applied fresh here)
- `real_premium_d5`/`_d10`/`_d15` (option EOD price at trading bars 5/10/15; **null if that bar
  index exceeds `fwd_rows_available`** -- never last-value-substituted)
- `real_iv_d5`/`_d10`/`_d15` (option's own quoted `iv` at the same bars, for vega-ratio calc)
- `real_underlying_d5`/`_d10`/`_d15` (`price_history.close` at the same trading-day bars)
- `real_pnl_d5`/`_d10`/`_d15` = `real_premium_dX / entry_premium - 1` (null where the premium is null)
- **First-touch event, generalizing the honest guard to an EVENT label, not a scalar:**
  `real_first_touch_bar`, `real_first_touch_type` in `{'TP','SL','none'}`, `real_tp_hit` (bool),
  `real_sl_hit` (bool) -- walked day-by-day over `real_pnl_dt` for every available `t` in `1..15`
  using EOD-close-to-close granularity (see rationale below). **A positive touch (`TP` or `SL`)
  is trusted whenever it occurs within the available (possibly partial) window. A negative
  read (`'none'`, i.e. `real_tp_hit=False` and `real_sl_hit=False`) is trusted ONLY when
  `ripe15=True`** -- a partial window that hasn't touched either barrier YET cannot be coded as
  "doesn't touch," it must be nulled. This is the correct generalization of `build_iv.py`'s
  scalar-`pnl15` guard to an event-based label.

**Granularity choice, stated explicitly:** both the real-touch walk above and every arm's
predicted-touch walk (below) use `price_history.close` at daily trading-bar checkpoints only --
NOT `option_pricing.fires_on_bar`/`bs_fires_on_bar` (which use the underlying's INTRADAY high/low,
what `monte_carlo.py`'s own forward SIMULATION trigger uses). Reason: `option_prices` has exactly
one quote per contract per day (no intraday option H/L exists to check the real side against), so
using intraday underlying H/L for the PREDICTED side while the REAL side is necessarily
EOD-sampled would bias predicted touches to be more trigger-happy than reality can ever confirm at
this data's granularity. Both sides get the same (conservative, likely-undercounting) daily
sampling -- an explicit, deliberate choice, not an oversight.

### Four arms -- predicted entry premium + predicted P&L/touch at same checkpoints
- `premium_rv_pct`, `premium_f2_pct` (the two premium-model outputs, per arm formula in section 0)
- For each of the four `{RVconst, RVgamma, F2const, F2gamma}` arms: `pred_pnl_d5`/`_d10`/`_d15`
  (via `option_pnl_pct`/`bs_option_pnl_pct` with that arm's premium, `total_dte=dte_at_entry`,
  `bars_held=t`, `vega_ratio` = REAL empirical `iv_dt/entry_iv` clamped `[0.2,2.0]` when
  `earnings_span_flag` else `1.0` -- same convention as `validate_gamma.py`, an explicit
  information advantage over production's own forward-looking `sample_vega_ratio()` that is
  flagged, not hidden: earnings-spanning trades are reported with-and-without per that script's
  own precedent), `pred_first_touch_type`, `pred_tp_hit`, `pred_sl_hit` (same day-by-day walk,
  same granularity, using the arm's own predicted P&L path in place of the real one).

Only `RVconst` (production) and `F2gamma` (candidate) are subject to the pre-registered PASS/FAIL
bars (section 4). `RVgamma`/`F2const` are diagnostic-only, to attribute any M2/M3 delta to the
premium axis vs the delta/gamma axis.

---

## 4. PRE-REGISTERED BARS (verbatim, FABLE's)

> **M1 (premium level):** F2 median `|model-real|/real` entry premium beats RV-premium overall
> AND in >=2 of 3 RV terciles.
>
> **M2 (P&L fidelity):** F2+gamma beats RV+const-delta on per-trade P&L MAE at d10 AND d15
> overall, and is not worse in the high-RV tercile.
>
> **M3 (decision fidelity):** F2+gamma TP-hit classification agreement vs the real path >=
> production's, AND its SL-hit false-negative rate is not worse (missed real SLs = hidden risk).
>
> All three with date-clustered SEs (reuse `experiments/gex/gex_test.py` clustering
> implementation), N-labels everywhere, SKIP<30 cells, era split (2025 vs 2026H1)
> sign-consistency reported, liquidity-stratified (volume>0 primary; all-inclusive as secondary).

### Operationalized

**M1.** Per matched-and-ripe trade: `err_rv = |premium_rv_pct - real_premium_pct| / real_premium_pct`,
`err_f2 = |premium_f2_pct - real_premium_pct| / real_premium_pct` (where
`real_premium_pct = entry_premium / entry_close`). PASS iff `median(err_f2) < median(err_rv)`
**overall**, AND in `>=2` of the 3 `rv_tercile` cells. Robustness companion (not the decision
criterion itself): `diff_i = err_f2_i - err_rv_i`, clustered-t via `ols_with_clustered_se(y=diff,
X=<none>, groups=dates)` (intercept-only regression -- a direct, minimal reuse of the gex_test.py
machinery for a paired-difference significance read alongside the literal median comparison).

**M2.** `MAE_arm(t) = mean(|pred_pnl_arm_t - real_pnl_t|)` over trades with `real_pnl_t` non-null
at that bar. PASS iff `MAE_F2gamma(d10) < MAE_RVconst(d10)` AND `MAE_F2gamma(d15) <
MAE_RVconst(d15)` **overall**, AND `MAE_F2gamma <= MAE_RVconst` in the high-RV tercile at both d10
and d15 (ties count as "not worse"). Clustered-t companion on
`diff_i = |pred_F2gamma_i - real_i| - |pred_RVconst_i - real_i|` per checkpoint.

**M3.** `real_tp_hit`/`pred_tp_hit_{arm}` as defined in section 3 (ripe-gated negatives).
TP-hit classification agreement = accuracy of `pred_tp_hit_arm == real_tp_hit` over `ripe15=True`
trades. SL-hit false-negative rate = `P(pred_sl_hit_arm==False | real_sl_hit==True)` (missed real
SLs), same `ripe15` gate. PASS iff `agreement(F2gamma) >= agreement(RVconst)` AND
`fnr_SL(F2gamma) <= fnr_SL(RVconst)`. Clustered-t companion on the per-trade agreement/FN
indicator differences.

### Cross-cutting requirements (apply to all three)
- **N-labels everywhere**; any cell (tercile x era x liquidity-stratum x band) with `N<30` is
  printed `SKIPPED (N=k)`, never silently included or extrapolated (`MIN_CELL_N=30`, reused from
  `gex_test.py`).
- **Era split** (`2025` vs `2026H1`) reported for sign-consistency on every bar -- does the
  F2-over-RV / gamma-over-const direction hold in BOTH eras, not just pooled. Given the recon
  power numbers (section 1c), expect thinner-but-plausible cells in 2025H2-2026H1 sub-splits;
  report honestly, do not force a PASS read on a thin/SKIPPED era cell.
- **Liquidity stratification**: every bar computed TWICE -- `liquid_ge5` (primary, ~480 estimated
  N) and all-inclusive (secondary, ~2,560 N). The PRIMARY read is the liquid stratum; the
  all-inclusive read is reported for power/context but is not itself sufficient for a PASS if the
  liquid stratum disagrees or is inconclusive.
- **Band** (`75+` vs `70-74`) reported as a breakdown, not a separate gate (the bars' own text
  says "overall" -- pooled across both bands is primary; band-split is diagnostic).

### DECISION RULE (verbatim)
PASS M1+M2+M3 -> adoption candidacy advances to the engine re-baseline procedure (separate,
later, FABLE+user-visible step: flip pair ON, re-run canonical N=500 gates, audit which gate
DECISIONS flip). Any FAIL -> at most ONE pre-registered refinement round (e.g., per-tercile
premium correction) before the pair parks as A/B-only infrastructure.

---

## 5. Also in scope: the 0.4 ATM mapping constant (cheap, no retune)

The F2 (and RV) premium formulas both route through the Brenner-Subrahmanyam ATM approximation
`premium_pct = k * IV * sqrt(T)` with `k=0.4` hardcoded. This check uses the ledger's own
**REAL, market-quoted** `entry_iv` (not F2's modeled IV -- that would just be re-testing F2's own
fit, already covered by M1) against the REAL `entry_premium`:

`k_implied_i = real_premium_pct_i / (entry_iv_i * sqrt(dte_at_entry_i / 365))`

Report `median(k_implied)`, `mean(k_implied)`, and the distribution by `rv_tercile` and
`liquid_ge5` vs all-inclusive, compared against the assumed `0.4`. **Do not retune `0.4` or
anything else in this pass** -- report the implied best-fit constant as a finding only; any
correction is exactly the kind of "one pre-registered refinement round" the decision rule allows
AFTER a FAIL, not something to fold in preemptively.

---

## 6. Compute plan

**The ledger build is a bulk `option_prices` scan -> QUEUE IT.** Do not run it directly. Proposed
submission (script name `experiments/iv_engine_pertrade/build_ledger.py` -- NOT written yet, this
is a design description, not code, pending approval):

```
trader queue submit --priority high --db heavy --cpu 2 --restartable \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --dedup iv_engine_pertrade_ledger \
  --reason "per-trade F2-gamma engine validation ledger (iv_premium_model VERDICT ruling 2)" \
  -- python experiments/iv_engine_pertrade/build_ledger.py
```

Market-hours note (CLAUDE.md queue doctrine): `--priority high` is fine off-hours/weekend (no
`trader update` to protect). If submitted during market hours, add `--window off_market` or drop
to `normal`/`low` so the scheduled `trader update` is never outranked -- this is `--db heavy`, not
`--db light`, so it does not qualify for the "keep high" exception.

**Query shape, optimized vs the per-signal patterns in `build_iv.py`/`build_option_slice.py`:**
1. ONE bulk `price_history` pull across all ~776 chain-bearing symbols (or the narrower set
   actually touched by the 5,243 candidate signals), spanning `[earliest_signal - 100 calendar
   days, cutoff + 30 calendar days]` -- covers BOTH the 60-bar trailing RV lookback and the
   forward 15-trading-day underlying walk in one shot (pattern from
   `iv_premium_model/build_fit_model.py`'s `fetch_price_history`). Eliminates the per-signal
   forward-underlying queries that `option_pricing_validation.py`/`validate_gamma.py` do.
2. ~5,243 per-candidate-signal chain-slice queries (one per `(symbol,date)`, mirrors
   `build_option_slice.py`'s SQL -- pulls the near-money band so selection AND liquidity land in
   one round trip, no separate `atm_call()` query needed).
3. ~2,560 per-matched-contract forward-option-price-path queries (mirrors
   `build_iv.py`'s `fwd_pnl`, honest-guarded).
4. VIX: zero MySQL -- read `.cache/iv_premium_model/vix_series.parquet` directly (confirmed 100%
   coverage, section 1d).
5. Materialize via `database/bulk_cache.py`'s `materialize_polars` pattern to
   `.cache/iv_engine_pertrade/ledger_v1.parquet`.

**Estimated runtime:** ~7,800 per-signal-scale indexed queries (5,243 + 2,560) at an estimated 50-200ms each
(consistent with similar `db=heavy` precedents in this repo) + the one bulk price-history pull
(seconds) => **roughly 15-40 minutes**. The build script should log per-signal throughput as it
runs so this estimate can be corrected from real numbers rather than re-guessed.

**Metric passes (M1-M3 + the 0.4-constant check) are foreground polars reads** against the
materialized parquet -- no MySQL, seconds each, no queue needed for that stage.

**MySQL discipline:** `SET SESSION MAX_EXECUTION_TIME` guard on every connection; no query
allowed to run past ~30s uninvestigated -- `SHOW FULL PROCESSLIST` + `KILL` the offending
connection id, never blind-retry (traps.md section 1). Use `.symbol_id` (raw FK column) instead of
peewee's `.symbol` accessor in any bulk loop over ORM rows to avoid the FK-per-row N+1 trap; this
design's own bulk pulls use raw `DB.execute_sql(...).fetchall()` tuples throughout (matching
`build_iv_ext.py`'s own convention), sidestepping the trap entirely rather than relying on
discipline inside a loop.

---

## 7. Known-traps section

- **`build_iv.py` truncated-`pnl15` (FIXED 2026-07-08, `445658866`):** the shared `fwd_pnl()`
  helper now nulls unripe windows instead of substituting the last available price. This ledger's
  own `ripe15`/first-touch-event guard (section 3) applies the SAME discipline, generalized from a
  scalar label to an event label (see the "positive touch trusted early, negative touch requires
  full ripeness" rule above) -- do not regress to a scalar-only guard.
- **polars NaN family** (`fill_nan(None)` before `.rank()`/joins/`spearmanr`/`drop_nulls`): apply
  to any float column before cross-sectional stats -- `.rank()`/`drop_nulls` alone do not catch a
  float NaN the way they catch a null.
- **OSK selection-recipe non-persistence** (`project_osk_regime_conditional.md`): the reason this
  ledger persists `option_id`/`strike`/`expiration_date`/`selection_rule` rather than only derived
  values -- a recipe change (e.g. nearest-strike-from-quotes vs nearest-traded-of-N) can move a
  correlation by ~0.09 (0.110 -> 0.199 documented) purely from selection divergence, invisible if
  the chosen contract itself isn't on record.
- **Signal-keyed sidecar panels under-dose engine-wide A/Bs -- check the DOSE before the outcome**
  (2026-07-10 trap, `experiments/gamma_iv_phaseb/`): not literally this experiment's shape (we
  query our OWN `option_prices` directly, not a third-party sidecar panel), but the SAME discipline
  applies -- report match-rate/dose prominently (section 1c) BEFORE reading M1-M3, and treat a
  dose collapse as a distinct "COVERAGE-BLOCKED" verdict, not a silent FAIL. The estimated
  ~480-row liquid-stratum N is thin enough that a further drop (e.g. a symbol/date join hiccup in
  the real build) should trigger the SAME coverage-blocked framing, not a forced read.
- **Earnings IV-crush windows:** `earnings_span_flag` trades get the REAL empirical vega ratio
  (an information advantage over production's forward-looking sampled vega -- flagged in section
  3), and are reported with-and-without per `validate_gamma.py`'s own precedent -- never silently
  pooled into the headline number without that split being visible.
- **Zero-volume quote noise:** ~76-81% of ALL `option_prices` rows are zero-volume every month
  (section 1b), and the reconstructed matched-contract check shows the SAME ~76% rate at the
  specific ATM/20-45DTE contracts this ledger selects -- "nearest ATM" does not find a
  meaningfully more liquid quote than the table average. This is why liquidity stratification is
  the PRIMARY read, not a secondary footnote.
- **pymysql literal-`%` escaping (NEW, found live during this recon):** a parameterized
  `DB.execute_sql(sql, params)` call whose SQL contains a literal `%` (e.g.
  `DATE_FORMAT(date,'%Y-%m')`) crashes with `ValueError: unsupported format character` --
  pymysql's `cursor.mogrify` runs the SQL string through Python `%`-formatting to splice in `%s`
  placeholders, so any other literal `%` must be doubled (`'%%Y-%%m'`). Hit and fixed in
  `recon.py` during this session; worth promoting to `.claude/docs/traps.md` if not already
  covered elsewhere (checked -- not currently in the registry).
- **Holdout lock:** `CALIBRATION_CUTOFF_DATE=2026-06-15` is the hard selection cap
  (`experiments/_holdout.py`); `is_shadow_oos` rows beyond it are watch-only, never used in
  M1-M3, never used to tune anything (section 3).
- **Archived-artifact non-reproducibility does not apply here** (no MC/seeded-sweep involved --
  this is a direct real-data ledger, not a simulation), but the general lesson (don't trust a
  stale cached number over a fresh same-session read) is why section 1c's liquid-stratum N is
  explicitly flagged as an ESTIMATE pending the real build's own count.

---

## 8. Methodology commitments

- ASCII-only in code, logs, and this document; `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` on the
  queued build.
- No engine edits under this document's authorship. `option_pricing.py`/`monte_carlo.py` are
  imported from, never modified, by the ledger build or the metric passes.
- No `HOLDOUT_DISABLE`. Selection is capped at `CALIBRATION_CUTOFF_DATE`; anything later is
  `is_shadow_oos` only.
- No commits, no queue submissions under this document's authorship -- **FABLE approval required
  before `build_ledger.py` is written or the queue job is submitted.**
- Every reported number carries its N; every stratification cell below N=30 prints SKIPPED.
- This is instrument fidelity, not alpha: no `ALGORITHM_VERSION` bump, no score change is in scope
  at any point in this workstream.

## Artifacts

`DESIGN.md` (this file) + `recon.py` (recon script) + `recon_results.{txt,json}` (full coverage/
power-table/liquidity output). `build_ledger.py` and the ledger parquet do not exist yet --
gated on this document's approval.
