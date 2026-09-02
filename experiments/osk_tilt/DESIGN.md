# OSK Stage-3 Allocation Tilt — DESIGN (pre-registration)

**Date:** 2026-07-10 · **Status: PRE-REGISTERED, locked before running any variant.**

## Context (not re-derived; see cited files)

OSK (`opt_skew` = 10%-OTM put IV − 10%-OTM call IV) is a confirmed **in-era**
per-trade residual (`experiments/osk_era/STAGE1_VERDICT.md`, `osk_validation/VERDICT.md`).
The SCORE-MODIFIER form is **BLOCKED** at the faithful path
(`experiments/osk_era/FAITHFUL_PATH_RESULTS.md`: WR15-primary null t≈-0.27/-0.32;
pnl15-secondary sign-reversed t=-2.19 once the pre_boost regime-multiplier is
honestly carried through). OSK stays a per-trade residual only; the sanctioned
remaining surface is a **Stage-3 sizing tilt** (SVR precedent — a skew-adjacent
signal shipped as a CALL allocation lever, not a score change). This experiment
also executes the ONE allowed follow-up recorded in `osk_validation/VERDICT.md`
("the honest nuance"): residualized skew (skew orthogonalized on momentum) as
one of the tilt variants, since the backward-OOS suppression pattern there
(β +0.049, clustered t +4.60) is unverified but pre-authorized for exactly one
follow-up test.

**HARD CEILING (stated here and in every downstream output): option data spans
only 2025-02-01→2026-06-15 (≈1.3y). The canonical Stage-3 T1-T7 gate (N=500,
8 windows incl. 2020_crash) is UNREACHABLE on this data. The only licensed
outcomes are STAGE or PARK — never SHIP.**

## Locked parameters

### Tilt formula
Per qualified CALL entry (score tier ∈ {75-79, 80-84, 85-94, 95+}; the 70-74
overflow tier and all PUT entries are untouched, `m = 1` exactly):

```
m_raw  = clip(1 + g * z, 0.70, 1.30)
m      = m_raw * renorm_factor(signal_date)
```

Applied as a new, independent multiplicative term in `backtest_cascade.py`'s
existing per-entry sizing product (alongside RXDD/MWDD/TVDD/BDIV/SVR/SPREAD_TILT,
all of which stay exactly as shipped):

```
base_premium = alloc_frac * reg_scale * dd_scale * saw_scale * sat_scale
             * rxdd_scale * svr_scale * mwdd_scale * tvdd_scale * bdiv_scale
             * spread_tilt_scale * osk_scale * allocation_base
```

No-skew entries (z unavailable for that (symbol, date)): `m = 1` exactly
(matches the SVR/SPREAD_TILT no-op convention already in the engine).

### Renormalization ("sizing redistribution, not leverage")
Pooled **per calendar day**, across **all four 75+ tiers together** (not
per-tier — the day's 75+ CALL cohort is treated as one redistribution group,
the simpler and more literal reading of "the day's cascade budget"): let
`E_d` = the set of that day's 75+ CALL candidate entries (as scheduled by
`outcomes_by_date`, i.e. before MAX_POSITIONS/cash admission — admission
mechanics are left untouched and applied afterward exactly as today).
`renorm_factor(d) = |E_d| / Σ_{i∈E_d} m_raw_i`. This forces the day's *mean*
multiplier to exactly 1.0, so the day's total intended capital commitment to
the 75+ CALL cohort is unchanged — only its distribution across that day's
names shifts. **Mechanical consequence (expected, not a bug):** on a day with
exactly one 75+ CALL candidate, renormalization forces `m = 1` regardless of
`z` — redistribution requires ≥2 names to redistribute between. Given the
~1,646 signals / ~350 trading days base rate (~4.7/day), a material fraction
of the era's 75+ CALL days are singletons; the tilt's realized effect is
concentrated on multi-name days.

### Variants (5 + baseline; LOCKED, no expansion)

| # | Label | Skew source | Lag | g | Ledger |
|---|---|---|---|---|---|
| 0 | `baseline` | — | — | 0 (m≡1) | — |
| 1 | `raw_lag0_g015` | raw skew | 0 | 0.15 | `osk_era_ledger_65.parquet` |
| 2 | `raw_lag0_g030` | raw skew | 0 | 0.30 | `osk_era_ledger_65.parquet` |
| 3 | `raw_lag1_g030` | raw skew | 1 | 0.30 | `osk_era_ledger_lag1.parquet` |
| 4 | `resid_lag0_g030` | residualized skew | 0 | 0.30 | `osk_era_ledger_65.parquet` + rs_ledger |
| 5 | `resid_lag1_g030` | residualized skew | 1 | 0.30 | `osk_era_ledger_lag1.parquet` + rs_ledger |

No B3-style softening, no additional cells. `clip` bounds fixed at
`[0.70, 1.30]` for every variant.

### Skew sources and z-fitting (validated; `build_osk_zmaps.py`)

Key: `(symbol, date-as-ISO-string)` — the OSK ledgers store `date` as a string
column, not a polars Date; the engine-side lookup converts
`outcome.signal_date` (a real `datetime.date`) via `str()` to match.

z-score reference population = **all skew-covered rows of the given ledger
file** (the ledger's own score-floor — 65 for lag0, 70 for lag1 — not
restricted to 75+). This matches the established precedent in
`FAITHFUL_PATH_RESULTS.md` exactly (pooled_mean_skew=0.0411 / pooled_std=0.1209
there vs. 0.04111 / 0.12085 reproduced here on the same 65-floor ledger).

Residualization: OLS `skew ~ a + b1*stock_r20 + b2*stock_r60`, fit on the
ledger's covered population joined to `.cache/rel_strength/rs_ledger.parquet`
on `(symbol, date)`, dropping rows missing `stock_r20`/`stock_r60`. Residual
`z`-scored the same way over that (slightly smaller) joined population. Sign
convention unchanged — a large positive residual means skew is higher than
momentum alone predicts, same direction as raw skew.

Fitted stats (confirmed by running `build_osk_zmaps.py`, 2026-07-10, no MySQL):

| Map | N | coverage of ledger | mean | std | OLS R² |
|---|---:|---:|---:|---:|---:|
| `raw_lag0` | 9,091 | 99.0% | 0.04111 | 0.12085 | — |
| `resid_lag0` | 9,088 | 99.0% | ~0 (by construction) | 0.11997 | 0.0147 |
| `raw_lag1` | 1,807 | 89.5% | 0.03026 | 0.10978 | — |
| `resid_lag1` | 1,807 | 89.5% | ~0 (by construction) | 0.10876 | 0.0184 |

Manifest: `.cache/osk_tilt/zmap_manifest.json`. Low OLS R² (1.5-1.8%) confirms
momentum explains little raw-skew variance — expected given the "honest
nuance" was a suppression effect, not a strong direct relationship.

### Window and leak-guard
Signal window: **2025-02-01 → 2026-06-15** (== `CALIBRATION_CUTOFF_DATE`, the
live holdout lock). `experiments._holdout.assert_no_holdout_leak(df,
context='osk_tilt')` is called on every local-parquet read. The **engine run
itself** is called with `to_date=None` (unbounded) so trades entered near the
window's end can resolve using real subsequent price history through today's
data (2026-07-10) rather than being artificially starved of settlement time —
this is outcome-resolution for already-committed (pre-cutoff) entries using
real forward prices, not new calibration information, so it is not a holdout
violation (same principle every barrier-touch assessment in this repo already
relies on). The **patched engine** then discards any `outcomes_by_date` entry
dated after 2026-06-15 before the sizing loop runs, so no post-cutoff signal
is ever sized, admitted, or scored. A Python-side assertion after every
patched run additionally checks `max(entry_date) <= 2026-06-15` over the
returned trade log. Expected consequence: a small number of very-late-window
entries (last ~2 weeks) may still show as `open_holdings` rather than closed
trades if their ~27-calendar-day hold hasn't fully elapsed by today
(2026-07-10) — a known, small, honest truncation tail, separate from and
additional to the 1.3y hard ceiling above.

## Engine integration (no production file edits)

`backtest_cascade.py`'s `run_backtest()` is the ONE function that computes
per-entry sizing (`base_premium = ... * allocation_base`). `tilt_runner.py`:

1. `inspect.getsource(backtest_cascade.run_backtest)` — grabs the *current*
   exact source at run time (no hand-transcription).
2. Asserts two anchor substrings each occur **exactly once**: the
   `cfg = cfg or {}` line (top-of-function insertion point) and the
   `base_premium = alloc_frac * ... * allocation_base` line (multiplication
   insertion point). If backtest_cascade.py has drifted since this was
   written, the assertion fails loudly instead of silently patching the wrong
   thing.
3. Inserts (a) a date-filter + per-day renormalization precompute at the top,
   and (b) one additional multiplicative factor `* _osk_scale` at the
   `base_premium` line, reading from a small shared mutable dict
   (`_OSK_STATE`) that the driver script sets before each call.
4. `exec()`s the modified source with `backtest_cascade`'s own
   `__dict__` as globals (so every existing constant/helper — `TIER_ALLOC`,
   `_svr_call_scale`, `regime_on_or_before`, etc. — resolves exactly as
   production does, staying in sync with the shipped module automatically),
   producing `run_backtest_osk_tilted`.
5. Monkey-patches `backtest_cascade.run_backtest = run_backtest_osk_tilted`
   for the duration of the 6 patched calls only, inside `try/finally`
   (restored immediately after, whether or not an error occurs). One
   additional, fully **unpatched** reference call is made before the patch is
   installed at all.

`backtest_cascade.py` and `strategy_config.py` are never opened for writing.
All other mechanisms (RXDD/MWDD/TVDD/BDIV/SVR/SPREAD_TILT/CTSL/CT_PROMOTE/
SAW_PUT_UCURVE/DEAD_HOLD/PRACTICAL_EXPOSURE/BREADTH_ALLOC) are confirmed
**enabled=True** in the live `STRATEGY_30DTE` (`strategy_config.py`) — i.e.
`cfg=None` (no override) already equals the current shipped/default ("Core")
30 DTE cascade; this is deliberate so the tilt is tested against the real
current book, not a stripped-down toy config.

### Engine call parameters (identical across all 7 runs except the tilt state)
`version_id=74` (active scores, `AlgorithmVersion.get_active_scores_version()`),
`min_score=70.0` (production default — the 70-74 overflow tier must stay in
the admitted pool for realistic MAX_POSITIONS competition, even though it's
excluded from tilting), `from_date=2025-02-01`, `to_date=None`,
`initial=$50,000` (module default), `calls_only=True` (production
`MAX_POSITIONS_PUT=0` on `STRATEGY_30DTE` already excludes puts structurally —
`calls_only=True` just skips the wasted put-signal query), `cfg=None`.

### MANDATORY baseline-reproduction check
Run 1 (fully unpatched, `to_date=None`) vs. Run 2 (patched, `g=0`, same
call). Because `g=0` forces `osk_scale ≡ 1.0` at every entry regardless of
coverage, the ONLY structural difference between the two code paths for
dates ≤ 2026-06-15 is the date filter itself — which removes entries that
occur *after* the window end. The check compares, restricted to
`entry_date/date ≤ 2026-06-15`: (a) the full trade list (symbol, entry_date,
exit_date, premium, outcome, pnl_pct, pnl — exact match, no tolerance beyond
float epsilon) and (b) the equity curve (date, equity — exact match). A
mismatch anywhere fails the run before any variant is trusted.

## Metrics (per variant vs. `baseline`, paired — same deterministic engine, no RNG in the P&L path)

- **Era compound** — `(final_equity/initial - 1) * 100`, cost-basis
  (`result['final_equity']`, `result['max_dd']` is the cost-basis series that
  actually drives the DD-band/RXDD/MWDD/TVDD sizing gates in this engine —
  reported as primary; `max_dd_mtm` reported alongside as an honest
  mark-to-market secondary check).
- **Era WorstDD** — `result['max_dd'] * 100`.
- **Per-entry sizing-weighted mean pnl15 uplift** — computed post-hoc (not
  inside the engine) over every trade_log row with `side=='call'`,
  `tier != '70-74'`, joined to the OSK ledger's own realized `pnl15` field
  by `(symbol, str(entry_date))` (lag-invariant: pnl15/entry_premium never
  depend on `lag_days`, confirmed — the lag only redirects the OTM legs used
  for `skew`; canonical source = `osk_era_ledger_65.parquet`, `ripe==True`
  only, since unripe rows have no realized pnl15). For each such row, `m`
  is recomputed with the SAME (zmap, g, renorm_by_date) used by that
  variant's engine run. `baseline_mean = mean(pnl15)` (unweighted);
  `weighted_mean = Σ(m·pnl15)/Σ(m)`; `uplift = weighted_mean − baseline_mean`.
  **date-clustered t**: cluster bootstrap over unique entry dates (resample
  dates with replacement, 2,000 draws, fixed seed 20260710 for
  reproducibility), `t = uplift / std(bootstrap uplifts)`.
- **n_entries tilted vs. neutral** — among the same eligible population,
  `tilted` = raw skew/resid coverage exists for that (symbol,date) (z not
  null) *and* g≠0; `neutral` = no coverage (or baseline).

## LOCKED BARS

**STAGE** requires: **≥2 variants (at least one of the two lag1 variants)**
with (i) DD not worse than baseline (`worst_dd_pct <= baseline`), **AND**
(ii) compound ≥ baseline, **AND** (iii) clustered t ≥ 2 on the sizing-weighted
uplift. Anything less is **PARK** (lead recorded in the closing RESULTS.md,
revisit at the ~2026-10 forward re-read alongside the OSK era-layer handoff).
No softened/partial bar, no new variants — this file is the ceiling on scope
for this experiment.

## Outputs
`experiments/osk_tilt/tilt_runner.py`, `tilt_results.json`, `tilt_results.txt`,
`RESULTS.md` (verdict + the two decisive numbers). Cache:
`.cache/osk_tilt/zmap_manifest.json`.

## Hard stops (restated)
No edits to `backtest_cascade.py` / `strategy_config.py` / scoring /
production files. MySQL only via the one queued job running
`tilt_runner.py`. Files confined to `experiments/osk_tilt/` and
`.cache/osk_tilt/`. `HOLDOUT_DISABLE` never set. No variant expansion beyond
the 5+baseline table above.
