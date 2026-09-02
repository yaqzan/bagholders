# IV Premium Model -- calibrated ATM-IV premium, applied at ~100% dose

**Status: APPROVED AS AMENDED 2026-07-12 (FABLE).** See "Amendments (FABLE, 2026-07-12)"
below for the ruling. Wiring, sanity checks, and the 4-arm A/B queue submission proceed
under this amended document.

## Amendments (FABLE, 2026-07-12)

**A1. FORM RULING.** F2 (`atm_iv_hat = 0.14920 + 0.09545*RV + 0.006242*VIX`, clamped to
`[0.1633, 1.7590]`) is selected OVER the mechanical parsimony pick F0. Recorded rationale
(FABLE judgment): deployment is the reverse-fold direction (2016-2022 priced from a
2022-08+ fit) where F0 degrades ~40% vs F2's ~10%; F0's zero VIX-sensitivity cannot price
crash windows differently from calm tape and forecloses the P1.4 vega-state pairing; 3
params on 8,642 rows carries no overfit risk.

**A2. HARD CONSTRAINT restated.** The model changes option COST only -- `premium_pct` at
entry. `sigma_daily` / barrier definitions (`tp_price`/`sl_price`/`tp_level`/`sl_level`) /
MTM sigma stay RV-based everywhere. If the wiring spec's `_mtm_pnl`/`sigma_daily` reuse
pattern would let the model leak into sigma, STOP and escalate.

**A3. TELEMETRY amendment.** Per-window, emit (i) model-computed vs fallback counts
(pre-registered dose bar >=95% -- below it, COVERAGE-BLOCKED, do not read outcomes),
(ii) clamp-hit counts, (iii) the realized model-premium/RV-premium ratio distribution
(median + terciles) -- so the verdict can attribute compression (calm-cohort markup vs
crash-window VIX term) per the reframed thesis in section 5.

**A4. PASS bars unchanged** (P1.3 verbatim, `gamma_ivmodel` vs `base`, full 38-window grid,
2020-era windows collapse-screened).

## 1. Goal + lineage

`experiments/gamma_iv_phaseb/` (gameplan P1.3 + P2.3) proved the documented +1754% gamma
explosion is real and stable (reproduced fresh on 2026-07-10: gamma +895.5%/+2587.8% vs base
+42.5%/+303.7% median compound), but could not dose a decisive verdict off the raw Polygon
4yr IV panel: **19.3% overall coverage, 10.7% on the decisive 2022-08 bear window**, vs the
pre-registered 60% bar -- closed **COVERAGE-BLOCKED** (`experiments/gamma_iv_phaseb/VERDICT.md`),
same consequence as FAIL for the L3 gamma-buy case, but a distinct verdict ("underpowered", not
"tested and failed"). VERDICT.md's own prescribed continuation (ruling #3): fit a **model** of
`atm_iv` on the panel's covered rows (real-IV truth), then apply the **modeled** premium at
100% dose engine-wide -- the only form that can ever price the pre-2022 gate windows (2016+,
2020-crash) that no purchasable panel reaches for this universe. `traps.md`'s "Signal-keyed
sidecar panels under-dose" entry names this exact escape: *"converting the panel into a
calibration set for a MODELED input (fit on covered rows, applied at 100% dose) is the standard
escape -- pre-register it as its own experiment."* This document is that pre-registration.

This is **instrument fidelity, not alpha mining** (gameplan section 7): no `ALGORITHM_VERSION`
bump, no score change. The acceptance question is *"does any gate DECISION flip?"* P1.4
(crash-IV vega state) pairs with this workstream and is referenced but not designed here.

This design **extends** the already-committed `IV_PREMIUM` plug (`monte_carlo.py` +
`backtest_cascade.py`, default-OFF, flags-off inertness bit-proven 2026-07-10) -- it does not
replace it, and it does not touch `option_pricing.py`'s `GAMMA_AWARE` block (also unchanged,
default-OFF, per gameplan P0.6(d)).

## 2. Recon: fit table + join coverage

Built by `experiments/iv_premium_model/build_fit_model.py` (read-only; MySQL SELECT only, no
writes to MySQL). Grain: `(symbol, date)` from `.cache/polygon_iv/iv_ledger_polygon.parquet`
(8,643 rows, 669 symbols, 2022-08-01..2026-05-15, no duplicate symbol/date rows).

Joins:
- **Engine-faithful realized_vol**: a bit-for-bit port of `monte_carlo.realized_vol(closes,
  base_idx, lookback=60)` / `backtest_cascade.realized_vol_pct` (verified identical formula and
  `VOL_LOOKBACK=60` / `VOL_BARS=60` constant in both files, both sourced from
  `strategy_config.STRATEGY_30DTE.VOL_LOOKBACK`) -- population stdev of trailing-60-bar simple
  daily returns x100, recomputed from `price_history` at each panel row's exact date (NOT the
  panel's own `vol_pct`/`iv_rv` columns, which trace to a different pipeline,
  `experiments/rel_strength/`, of unverified lookback convention -- deliberately not trusted).
- **VIX + regime**: `MarketRegime.vix_close` / `.regime_composite` by exact date.
- **McClellan** (secondary/optional): `MarketBreadth.mcclellan_oscillator` by exact date.

| join | hits | pct |
|---|---|---|
| realized_vol (engine-faithful) | 8,642 / 8,643 | 99.99% |
| VIX close | 8,643 / 8,643 | 100.00% |
| McClellan oscillator | 8,643 / 8,643 | 100.00% |
| **fit-eligible (RV + VIX)** | **8,642 / 8,643** | **99.99%** |

The single RV miss: `insufficient_bars` (one panel row lands <60 trading bars into that
symbol's `price_history`, inside the `PH_QUERY_START=2022-03-01` buffer window). Every other
category (`symbol_not_in_ph`, `date_not_in_ph`, `insufficient_valid_returns`) = 0. This
confirms the thesis: **switching from a signal-keyed raw-panel join (19.3% dosed) to a model
whose inputs are the engine's own always-available RV + VIX converts the same panel into a
~100%-dosed calibration set.**

Holdout: `experiments._holdout.assert_no_holdout_leak` run against the fit table (panel max
date 2026-05-15 < `CALIBRATION_CUTOFF_DATE` 2026-06-15) -- **PASS** (trivial by construction,
enforced per spec regardless).

Artifact: `experiments/iv_premium_model/fit_table.parquet` (8,643 rows, reproducible from the
build script).

## 3. Parsimony ladder (numpy `lstsq`, no sklearn/statsmodels)

Fit on all 8,642 fit-eligible rows. `RV` = engine-faithful realized_vol (raw, e.g. "2.49" =
2.49% daily stdev -- the SAME units/variable the engine already computes as `vol`/`sigma`, no
conversion needed at wiring time). `VIX` = `MarketRegime.vix_close` raw index value.

| form | params | R^2 | MAE | coefficients (full-sample) |
|---|---|---|---|---|
| F0 pure rescale (`k*RV`) | 1 | 0.1748 | 0.1725 | k=0.15311 |
| F1 affine RV (`a+b*RV`) | 2 | 0.3599 | 0.1723 | a=0.26411, b_rv=0.09622 |
| F2 affine RV+VIX (`a+b*RV+c*VIX`) | 3 | 0.3681 | 0.1709 | a=0.14920, b_rv=0.09545, c_vix=0.006242 |
| F3 interaction (`a+b*RV+c*VIX+d*RV*VIX`) | 4 | 0.3696 | 0.1708 | a=0.08389, b_rv=0.11610, c_vix=0.009721, d=-0.001090 |

**F0 answers its own framing question directly: is `PREMIUM_MULT` (1.82) just mis-scaled?**
The engine's current formula `premium_pct = PREMIUM_MULT*RV/100` implies an effective
atm_iv-per-unit-RV constant of `PREMIUM_MULT/(100*0.4*sqrt(30/365)) = 1.82/(40*0.2860) = 0.159`.
The directly-fit F0 k=0.1531 is only **~4% away**. A pure rescale would barely move the needle
-- the real gap (below) has to come from the intercept/VIX terms F0 lacks, not from a better
constant.

**The intercept matters far more than VIX or the interaction**: R^2 jumps +0.185 (0.175->0.360)
from F0->F1 (one parameter: an intercept), then only +0.008 and +0.001 for VIX (F2) and the
interaction (F3). Real IV has a large floor (~0.26) that a proportional-through-origin formula
cannot express.

### Out-of-fold

Time-fold split = 2025-01-01 (train_a n=4,241 pre-2025; test_a n=4,401, 2025+):

| form | MAE fwd (fit pre-2025, predict 2025+) | MAE rev (fit 2025+, predict pre-2025) | rev/fwd degradation |
|---|---|---|---|
| F0 | 0.1514 (best of all 8 cuts) | 0.2118 (**worst of all 8 cuts**) | +40% |
| F1 | 0.1555 | 0.1944 | +25% |
| F2 | 0.1728 | 0.1920 | +11% |
| F3 | 0.1727 | 0.1906 | +10% |

Symbol-fold (5-fold, `md5(symbol) mod 5` -- stable across process restarts, unlike salted
`hash()`), pooled OOF MAE: F0=0.1727, F1=0.1728, F2=0.1713 (best), F3=0.1718 -- all four within
0.0015 of each other; effectively a wash cross-sectionally.

Blended OOF MAE (mean of fwd/rev/symbol-fold): **F0=0.17862, F1=0.17421 (best), F2=0.17867,
F3=0.17838**. Threshold at 1.05x best = 0.18292.

### Parsimony rule applied (mechanical result, stated per spec)

Smallest form within the threshold: **F0 (0.17862 <= 0.18292)**. The literal, mechanically-applied
rule selects **F0**.

### Why this recon does NOT recommend shipping the mechanical winner

Flagging per spec ("flag if the winner's coefficients are unstable across folds") -- but the
more important instability is in the model **selection itself**, not just F0's own coefficient
(k ranges 0.1427..0.1736 across 8 fold variants, rel_range 20%, always positive -- not flagged
by the 50%-rel-range threshold, but non-trivial):

1. **F0's OOF ranking is direction-dependent, not uniformly good.** F0 wins the forward cut by
   the widest margin of any form-vs-form gap in the whole ladder, but posts the single worst
   cut anywhere in the table on the reverse fold (+40% degradation vs its own forward cut,
   roughly 4x worse than F2/F3's degradation). F0's OOF competitiveness is an artifact of
   averaging a great forward score with a terrible reverse score; F2/F3 are mediocre-but-even
   on both cuts.
2. **The deployment direction is backward, not forward.** The A/B this design specifies (section
   5) prices the full 2016-06-01..2026-04-15 grid -- i.e., applies a model fit on 2022-08+ data
   backward onto 2016-2022, years the panel never covers. That is structurally the "reverse"
   fold's extrapolation direction, not the "forward" one. On the metric that actually matches
   the use case, F0 is the worst of the four forms, not the best.
3. **F0 has zero VIX-sensitivity by construction** (`k*RV` only). Its own item-(c)
   extrapolation table (section 4) is **flat/vacuous** -- identical prediction at VIX 18, 30,
   50, and 80 -- which cannot satisfy the mandated "clamped extrapolation, hard cap at
   COVID-scale VIX" check at all, and forecloses the P1.4 vega-state pairing this whole
   workstream exists to support.
4. **F0's residuals show large, systematic, unexplained structure that F2 mostly removes.**
   By VIX band, F0's mean residual trends from +0.062 (calm) to +0.178 (elevated) to +0.154
   (panic) -- i.e., F0 under-prices IV increasingly as VIX rises through the slowbleed/elevated
   bands, exactly the vega-blindness P1.4 is meant to fix. F2's VIX-band residuals are an order
   of magnitude smaller and don't trend (-0.006, +0.015, +0.046, -0.020).
5. **F2's VIX coefficient (c_vix) is small and noisy in magnitude (rel_range 176% across
   folds) but is POSITIVE in all 8 fold variants -- never sign-flips.** A real, directionally
   robust market-wide-vol effect on single-name ATM IV beyond idiosyncratic RV, even though its
   size is uncertain. (F2's intercept `a` IS flagged unstable, rel_range 74% -- same regime-drift
   root cause as F1's -- a genuine data feature: measured IV-per-RV richness has been declining
   2022->2025 across the whole panel, visible in every form that has an intercept. This is not
   swept under the rug; see residual-by-year, section 4.)

**Recommendation for the wiring spec: F2 (affine RV + VIX)**, flagged here as a judgment call
for FABLE's explicit decision, not a silent override of the stated rule. F0 remains available
as a documented fallback if FABLE prefers the letter of the parsimony rule; the wiring spec
(section 6) parameterizes the coefficients as named constants so either form is a small,
mechanical edit.

## 4. Residual structure, clamps, and the VIX extrapolation check

### Residuals by year (full-sample fit)

| year | N | F0 mean resid | F0 MAE | F2 mean resid | F2 MAE |
|---|---|---|---|---|---|
| 2022 | 687 | +0.2852 | 0.3277 | +0.1501 | 0.2994 |
| 2023 | 1,027 | +0.1357 | 0.2006 | +0.0274 | 0.1922 |
| 2024 | 2,527 | +0.0978 | 0.1727 | +0.0045 | 0.1706 |
| 2025 | 2,857 | +0.0301 | 0.1635 | -0.0374 | 0.1570 |
| 2026 | 1,544 | +0.0407 | 0.1009 | -0.0231 | 0.1259 |

Monotonic decline in under-prediction from 2022->2025 in BOTH forms: measured IV-per-RV
richness has genuinely compressed across the panel's span (or signal composition has shifted --
either way, real, not a fold artifact, since it shows up in the full-sample fit too).

### Residuals by VIX band (full-sample fit)

| band | N | F0 mean resid | F0 MAE | F2 mean resid | F2 MAE |
|---|---|---|---|---|---|
| calm (<20) | 5,968 | +0.0622 | 0.1606 | -0.0060 | 0.1637 |
| slowbleed (20-26) | 1,829 | +0.1208 | 0.1826 | +0.0146 | 0.1773 |
| elevated (26-28) | 386 | +0.1776 | 0.2164 | +0.0461 | 0.1941 |
| panic (>=28) | 459 | +0.1538 | 0.2494 | -0.0197 | 0.2192 |

### Residuals by RV tercile (cuts at RV=1.975, 3.229; full-sample fit)

| tercile | N | F0 mean resid | F0 MAE | F2 mean resid | F2 MAE |
|---|---|---|---|---|---|
| T1 low RV | 2,881 | +0.1617 | 0.1726 | -0.0128 | 0.1716 |
| T2 mid RV | 2,880 | +0.0960 | 0.1439 | -0.0248 | 0.1564 |
| T3 high RV | 2,881 | -0.0039 | 0.2008 | +0.0376 | 0.1847 |

F0's residual is large-and-positive at low RV, ~zero at high RV -- direct evidence the missing
intercept is a low-RV problem, not a high-RV one (see section 5's mechanism-reframing note).

### Clamps

Panel `atm_iv` percentiles (full 8,643-row panel, not just fit-eligible): p1=0.1633,
p50=0.4637, p99=1.7590. Physically-motivated band (independent sanity check): [0.08, 2.50]
(8%..250% annualized). **Proposed clamp = [0.1633, 1.7590]** -- the data-derived percentile
band, which sits comfortably inside the physical band on both sides (tighter floor, tighter
cap), so it is used directly rather than loosened out to the physical band.

### VIX extrapolation check (panel's own observed VIX max = 52.33; MarketRegime confirms
2020-02..2020-05 COVID peak vix_close = 82.69, so VIX=80 below is a real, on-file COVID-scale
test point, not a hypothetical)

**F0 (mechanical winner) -- vacuous, zero VIX-sensitivity:**

| RV scenario | VIX=18 | VIX=30 | VIX=50 | VIX=80 |
|---|---|---|---|---|
| RV=1.785 (p25) | 0.2733 | 0.2733 | 0.2733 | 0.2733 |
| RV=2.471 (p50) | 0.3783 | 0.3783 | 0.3783 | 0.3783 |
| RV=3.735 (p75) | 0.5719 | 0.5719 | 0.5719 | 0.5719 |

Identical at every VIX level by construction -- this table cannot demonstrate "clamped
extrapolation" behavior because there is nothing to extrapolate.

**F2 (recommended) -- sane, monotonic, non-explosive; clamp is a backstop, not an active
constraint at these RV levels even at VIX=80:**

| RV scenario | VIX=18 | VIX=30 | VIX=50 | VIX=80 (beyond panel max 52.33) |
|---|---|---|---|---|
| RV=1.785 (p25) | 0.4319 | 0.5068 | 0.6317 | 0.8189 |
| RV=2.471 (p50) | 0.4974 | 0.5723 | 0.6971 | 0.8844 |
| RV=3.735 (p75) | 0.6181 | 0.6930 | 0.8178 | 1.0051 |

None of these nine cells hit the proposed cap (1.7590) -- raw and clamped predictions are
identical throughout this representative-RV grid. The clamp only binds for individually
extreme-RV names regardless of VIX (see LAES anchor below, RV=24.9), not for a VIX-80 shock at
typical RV. This is reassuring: F2's linear-in-VIX extrapolation does not blow up at
COVID-scale VIX for ordinary movers.

## 5. Model-vs-RV premium ratio -- a finding that revises the a-priori mechanism

Ratio = `premium_ivmodel_pct / premium_rv_pct`, where `premium_ivmodel_pct = 0.4 * clamped_model_iv
* sqrt(row_dte/365)` (row's own contract DTE, 20-45) and `premium_rv_pct = PREMIUM_MULT(1.82) *
engine_rv/100` (the engine's CURRENT fallback at `NOMINAL_CAL_DTE=30`, dte-invariant).

| RV tercile | F0 mean ratio | F0 median | F2 mean ratio | F2 median |
|---|---|---|---|---|
| T1 low RV | 0.945 | 0.881 | **1.676** | 1.611 |
| T2 mid RV | 0.946 | 0.881 | 1.254 | 1.215 |
| T3 high RV | 0.915 | 0.863 | **0.916** | 0.888 |

**This is the opposite gradient from the gamma_iv_phaseb "error cancellation" prior.** VERDICT.md
hypothesized realized-vol premium is too cheap specifically **for big movers**, i.e. the ratio
should rise with RV. Both forms show the ratio falling (or flat) from low-RV to high-RV. The
correction a calibrated model applies is concentrated at the LOW-RV end (F2: premium ~1.68x
richer for calm entries) and is roughly flat-to-slightly-cheaper than today's formula at the
high-RV end (F2: ~0.92x). Mechanism reframing for the eventual A/B's interpretation: **if the
explosion compresses under `ivmodel`, the likely channel is a broad premium markup concentrated
on calm/low-RV entries (which are numerically the majority of trades), not a big-mover-targeted
cost increase.** This does not by itself predict whether P(2x)/median-compound moves enough to
pass -- that is exactly what the A/B (section 6) is for -- but it is a concrete, falsifiable,
pre-registered expectation that the eventual result should be read against. Document as a major
finding either way (mission spec), not adjusted after the fact.

**Known limitation, flagged not fixed here**: this is a single global linear fit; if the true
atm_iv(RV, VIX) relationship is convex specifically in the extreme-RV tail (a small, thin
subpopulation, ~5% of rows are in the "panic" VIX band and a further-smaller subset combine
with extreme RV), OLS on a mostly-calm-market panel will average that tail's convexity away.
The original 19%-dosed A/B used RAW panel atm_iv values directly (wherever covered), which could
retain tail convexity a smoothed linear model discards. This is an open question for the
eventual A/B to surface, not something this recon can resolve without a richer (nonlinear /
tercile-interaction) form, which is out of scope for "one step richer."

**Known limitation #2**: the model can substantially over-price low-realized-vol, low-beta
instruments (e.g. HYG anchor below: actual atm_iv 0.0636, F2 raw prediction 0.3198, a global
intercept fit on a mostly-momentum-name panel overstates the IV floor for atypical low-vol
tickers). The clamp floor (0.1633) bounds the visible damage but does not fix the systematic
over-prediction. Recommend the eventual A/B's telemetry track clamp-hit rate and flag it if it
concentrates in any one symbol class.

### Sanity anchors

| symbol | date | dte | RV | VIX | actual IV | F0 model IV (raw) | F2 model IV (raw) | F0 ratio | F2 ratio |
|---|---|---|---|---|---|---|---|---|---|
| ELF (hand-verified, gamma_iv_phaseb DESIGN.md) | 2022-08-04 | 43 | 3.425 | 21.44 | 0.3486 | 0.5244 (0.5244) | 0.6099 (0.6099) | 1.155 | 1.343 |
| LAES | 2025-02-11 | 24 | 24.940 | 16.02 | 1.9359 | 1.7590 (3.8185, clamped) | 1.7590 (2.6296, clamped) | 0.397 | 0.397 |
| LAES | 2025-02-07 | 21 | 24.833 | 16.54 | 1.5202 | 1.7590 (3.8021, clamped) | 1.7590 (2.6227, clamped) | 0.373 | 0.373 |
| HYG (low-beta limitation case) | 2026-03-19 | 22 | 0.214 | 24.06 | 0.0636 | 0.1633 (0.0328, clamped up to floor) | 0.3198 (0.3198) | 4.118 | 8.063 |
| KO | 2023-05-31 | 23 | 0.610 | 17.94 | 0.1339 | 0.1633 (0.0933, clamped up to floor) | 0.3194 (0.3194) | 1.478 | 2.891 |
| C | 2025-01-17 | 21 | 1.855 | 15.97 | 0.4593 | 0.2840 (0.2840) | 0.4259 (0.4259) | 0.807 | 1.211 |
| BN (max atm_iv in panel, meme-like) | 2025-07-17 | 29 | 1.623 | 16.52 | 4.4542 | 0.2485 (0.2485) | 0.4072 (0.4072) | 0.949 | 1.554 |

BN and the two LAES rows show the model (either form) cannot and does not try to chase
single-name idiosyncratic IV extremes (4.45, or a 24.9-RV meme name) -- it recovers the
CENTRAL TENDENCY of atm_iv given RV/VIX, clamped at the panel's observed range. This is
expected and correct behavior for a calibration model, not a defect.

## 6. Wiring spec (design only -- NOT implemented by this recon)

New env `IV_MODEL` (default `'0'`), read alongside the existing `IV_PREMIUM`/`IV_PREMIUM_PANEL`
block in both `monte_carlo.py` and `backtest_cascade.py`. `IV_MODEL` is a **modifier of the
IV_PREMIUM path**, meaningful only when `IV_PREMIUM=1` -- when `IV_PREMIUM=0` the whole
`_iv_premium_pct` function early-returns (`if not IV_PREMIUM or symbol is None: return
fallback_premium_pct`) before `IV_MODEL` is ever consulted, so the flags-off path is provably
untouched by construction, matching the existing plug's inertness discipline (Amendment 1,
2026-07-10 same-day pristine-vs-edited proof). **A fresh same-day pristine-vs-edited bit-exact
re-proof is NOT required for this extension** provided the diff is confined strictly inside the
already-proven-inert `if IV_PREMIUM:` branch (or a sub-branch of it) -- the wiring engineer must
SHOW this confinement in the implementation report (a diff/grep review demonstrating every
changed line sits inside that guard), not re-run the heavy validation arm from scratch.

Model constants (named, not env-gated -- pin to whichever form FABLE approves; F2 shown as the
recommended default):

```
IV_MODEL_FORM       = 'F2_affine_rv_vix'   # experiments/iv_premium_model/DESIGN.md
IV_MODEL_COEF_A     = 0.14920
IV_MODEL_COEF_B_RV  = 0.09545
IV_MODEL_COEF_C_VIX = 0.006242
IV_MODEL_CLAMP_FLOOR = 0.163326
IV_MODEL_CLAMP_CAP   = 1.758980
```

(Refit on the full panel one more time immediately before wiring in case of any data refresh in
the interim; treat the values above as the pre-registered reference, not a frozen final.)

Mechanism:
- New lazy per-process loader `_load_vix_series()`, mirroring the existing `_load_iv_panel()`
  shape exactly (module-level cache dict, guarded lazy read, safe all-miss degrade, never
  raises). Source: a small offline export of `MarketRegime.vix_close` by date, materialized ONCE
  to `.cache/iv_premium_model/vix_series.parquet` (NOT a live MySQL query per sweep worker --
  same on-demand-parquet-cache precedent as the IV panel itself and `database/bulk_cache.py`).
  `MarketRegime` spans 1995-01-03..present with full VIX coverage across 2016 and the 2020
  COVID peak (vix_close 82.69 confirmed on file) -- the full 2016-2026 grid is priceable.
- `_iv_premium_pct` (or a new sibling `_iv_model_premium_pct`, engineer's choice) gains an
  OPTIONAL `rv=None` kwarg threaded from both call sites (`_iv_premium_pct(symbol, signal_date,
  _iv_dte, premium_pct, rv=vol)` in `monte_carlo.py`'s `compute_trade_outcome`/
  `compute_put_outcome`; `rv=sigma` in `backtest_cascade.py`'s `compute_outcome`/
  `compute_put_outcome`) -- backward-compatible, default `None` preserves existing behavior when
  `IV_MODEL` is off or unset.
- When `IV_PREMIUM=1 and IV_MODEL=1`: skip the raw panel exact/as-of lookup entirely (the raw
  panel loader stays present/importable but unused under this flag combination); look up
  `vix = _load_vix_series().get(signal_date)`. If found (expected ~100%, `rv` also always
  present since callers only reach this helper after `realized_vol`/`realized_vol_pct` already
  returned non-None): `atm_iv_pred = IV_MODEL_COEF_A + IV_MODEL_COEF_B_RV*rv +
  IV_MODEL_COEF_C_VIX*vix`, clamp to `[IV_MODEL_CLAMP_FLOOR, IV_MODEL_CLAMP_CAP]`, return
  `0.4 * atm_iv_pred_clamped * sqrt(dte/365)`. If `vix` missing (should be ~never in live
  trading history) or `rv is None` (a caller forgot to thread it): miss, fall back to
  `fallback_premium_pct`, counted.
- MTM re-mark site (`backtest_cascade.py`'s nested `_mtm_pnl`, `count=False` already, line
  ~2045): source-verified this ALREADY threads `sigma = pos.outcome.sigma_daily` -- a field
  cached on `TradeOutcome` at entry (`sigma_daily: float   # 60-bar realized vol %`, line 1369)
  -- and calls `_iv_premium_pct(sym, pos.outcome.signal_date, total_dte, premium_pct,
  count=False)` keyed on the position's ENTRY date, not `today`. No new caching mechanism is
  needed: thread `rv=pos.outcome.sigma_daily` into that same call (the value is already sitting
  on the outcome object) so the model path gets a real RV input for free, consistent with the
  existing `count=False` semantics (`compute_outcome`/`compute_put_outcome` already counted this
  same key once, at entry).

Telemetry (extends the existing per-window `_prepare_window` delta / bc `atexit`
`_flush_iv_coverage` counters with new keys, same shape):
- `model_hits` / `model_misses` per window (mc) and cumulative (bc) -- **dose must be >=95%**
  (pre-registered bar, tighter than the raw-panel plug's 60% because the model's only failure
  mode is a missing VIX row, which should be near-zero across all of trading history).
- `model_clamp_floor_hits` / `model_clamp_cap_hits` -- count of predictions that hit either
  clamp bound, reported alongside dose so clamp-saturation rate is visible per window (watch
  for concentration in specific symbol classes per the HYG/KO low-beta limitation above).

Files touched: `monte_carlo.py` + `backtest_cascade.py` only (same scope as the original
`IV_PREMIUM` plug). Not touched: `option_pricing.py`, `strategy_config.py`, scoring/core/
simulator/api/trader.

## 7. A/B plan (queued, post-FABLE-approval only)

**4 arms** (paired seeds; only env flags differ):

| arm | GAMMA_AWARE | IV_PREMIUM | IV_MODEL |
|---|---|---|---|
| base | unset | unset | unset |
| gamma | 1 | unset | unset |
| ivmodel | unset | 1 | 1 |
| gamma_ivmodel | 1 | 1 | 1 |

(The old raw-panel `iv`/`gammaiv` arms from gamma_iv_phaseb are superseded, not re-run -- the
panel's job now is calibration input, not a coverage-blocked treatment arm.)

**2 cells**: `flat_n4_a25` (staged fast-2x sprint) + `cascade_ref` (production Apex cascade),
same `experiments/concentration_2x/sweep.py --stage drill` harness, cash $50,000, workers 6,
calendar-hold 30-DTE engine (`calendar_hold=True, nominal_cal_dte=30, hold_cal_days=27`).

**Windows: the FULL original 38-window quarterly grid, 2016-06-01..2026-04-15** (no
`--hist-start` override -- `sweep.py`'s `monthly_windows()` default reproduces this exactly).
Unlike gamma_iv_phaseb's panel-clipped 13-window grid, the model prices all history, so the
**2020-crash-era windows are explicitly IN this run** -- the whole point of the model-form
escape (VERDICT.md ruling #3).

**N**: `N_ITER=300` per window per cell per arm -> N_paths = 38 x 300 = 11,400 per cell per arm.
Same-day paired (all 4 arms against the same DB state, same seeds -- `traps.md` "Archived MC
artifacts are NOT bit-reproducible" rule: never compare against the archived goff/gon reference
numbers as a pass/fail target, reference-only context). Any cell with N<30 is skipped (not
expected).

**Runtime estimate** (pre-registered): ~25 min/arm, extrapolated from the valgoff validation
arm's ~12 min at N=150 (single-cell, 38-window). 4 arms sequential ~= 1.5-2h, well under a
queue session ceiling. **Queue**: one task per arm, `--db heavy --cpu 6 --restartable`, per-arm
dedup keys; schedule off-hours/weekend at `--priority high` (no `trader update` to protect); if
run during market hours, use `--window off_market` or drop to `normal`/`low` so the scheduled
`trader update` is never outranked (CLAUDE.md queue doctrine).

## 8. PASS bars (verbatim, inherited from P1.3 -> P2.3; FABLE judges, runner only produces numbers)

"the +1754% explosion collapses to sane numbers -- median compound within ~2x the base arm,
P(2x) not pinned near 100%, DD within +/-5pp of base, collapse=0" -- judged on `gamma_ivmodel`
vs `base`, with the 2020-crash-era windows explicitly screened for collapse (not gated on
survivor-only comfort per gameplan P1.2's SCREEN-not-GATE doctrine, but any 2020-window collapse
in a treated arm is a mandatory-investigation red flag regardless).

## 9. Decision consequences

- **PASS** -> gamma + IV-model becomes an **engine-fidelity ADOPTION CANDIDATE** (gameplan
  section 7: no version bump; next step is the gate-decision-flip re-baseline + P1.4 vega-state
  pairing). FABLE arranges independent adversarial verification of a PASS before it is called
  real (same standing requirement as any positive engine-fidelity finding).
- **FAIL** (explosion survives a ~100%-dosed calibrated premium) -> the error-cancellation
  thesis needs revision; gamma stays parked. Document as a major finding either way -- section 5
  above already pre-registers a concrete, falsifiable expectation (a low-RV-concentrated premium
  markup, not a big-mover-targeted one) that a FAIL would be consistent with, given the fitted
  ratio structure.
- **Coverage/dose <95%** -> instrument bug (should not happen: VIX/RV are both ~100%-available
  historically) -- fix before reading outcomes, do not report a PASS/FAIL off a doubted dose.

## 10. Methodology commitments

- Same-day pairing only -- all 4 arms run against the same DB state in the same session; no
  comparison against archived MC/sweep artifacts as a validation or pass/fail target
  (`traps.md`).
- Every cell N-labeled in the output; N<30 cells skipped, not silently included.
- ASCII-only in code, logs, and this document.
- No `HOLDOUT_DISABLE`. No env-gates added to scoring/core/simulator/api/trader -- `IV_MODEL`
  lives entirely in the two engine files' premium plumbing, same precedent as `IV_PREMIUM` and
  `GAMMA_AWARE`.
- No engine edits while a queued job importing these files is in flight.
- No commits, no queue submissions, no engine wiring under this document's authorship -- FABLE
  approval required first.

## Artifacts

`DESIGN.md` (this file) + `build_fit_model.py` (recon script) + `fit_table.parquet` (8,643-row
derived cache) + `fit_report.json` / `fit_report.txt` (full ladder/OOF/stability/residual/clamp/
extrapolation/ratio/anchor tables for both F0 and F2, plus the recommendation rationale list).
