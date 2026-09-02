# IV per-trade M1 RE-READ on the corrected as-traded-spot calibration panel

**Date:** 2026-07-25 · **Scope:** disciplined re-measurement of the FAILED, pre-registered M1 bar in
`experiments/iv_engine_pertrade/` after the calibration TARGET was found contaminated.
**Numbers and bar outcomes only. No verdict, no adoption recommendation** — FABLE adjudicates.

| item | value |
|---|---|
| harness | `experiments/polygon_real_premium/iv_m1_reread.py` (new; nothing in `experiments/iv_premium_model/`, `experiments/iv_engine_pertrade/` or `.cache/polygon_iv/` was written) |
| builder | `experiments/iv_premium_model/build_fit_model.py:build_fit_table()` called with its module-level `PANEL_PATH` / `FIT_TABLE_PATH` re-pointed at run time — same code path, no file edit |
| validation ledger | `.cache/iv_engine_pertrade/ledger_v1.parquet` — **UNCHANGED** (yfinance `lastPrice`, 2025-02-11..2026-06-12, per-trade contract persisted) |
| forms refit | **F2** (3-param) and **R1i** (5-param piecewise intercept) **ONLY** — no third form, no new functional family |
| bars | printed verbatim beside each result; no relaxation, no substitution |
| artifacts | `iv_m1_reread.{json,txt}` (full cut matrix), `.cache/polygon_real_premium/m1_reread/fit_table_{old,corrected}.parquet` |
| runtime | 10s foreground (2 bulk `price_history` reads, single-threaded; no queue job, no MySQL writes) |

Naming used throughout:

| model | premium form | calibration target |
|---|---|---|
| `f2_shipped` | F2, `monte_carlo.py`'s rounded shipped constants | old panel |
| `f2_old` | F2 refit by this harness | old panel |
| `r1i_old` | R1i refit by this harness (= the published refinement-round candidate) | old panel |
| `f2_corrected` | F2 refit | **corrected panel** |
| `r1i_corrected` | R1i refit | **corrected panel** |

---

## 1. STEP 1 — reproduction on the OLD panel

`experiments/iv_premium_model/fit_table.parquet` was missing and was rebuilt from
`.cache/polygon_iv/iv_ledger_polygon.parquet` (8,643 rows, 669 symbols, 2022-08-01..2026-05-15;
RV join 8,642/8,643, VIX join 8,643/8,643 — identical coverage to `fit_report.txt`).

### 1a. Fit constants, terciles, clamps

| quantity | published | recomputed | delta |
|---|---|---|---|
| F2 `a` | 0.149200 | 0.1492017 | +1.5e-06 |
| F2 `b_rv` | 0.095450 | 0.0954462 | -3.8e-06 |
| F2 `c_vix` | 0.006242 | 0.0062420 | -7.1e-09 |
| R1i `a_T1_low` | 0.17704 | 0.1770440 | -1.7e-07 |
| R1i `a_T2_mid` | 0.18533 | 0.1853316 | -5.8e-08 |
| R1i `a_T3_high` | 0.30649 | 0.3064908 | -1.4e-07 |
| R1i `b_rv` | 0.07519 | 0.0751881 | -1.2e-08 |
| R1i `c_vix` | 0.005719 | 0.0057189 | +1.1e-08 |
| R1i frozen tercile `t1` | 1.975436 | 1.975436 | 0 |
| R1i frozen tercile `t2` | 3.229416 | 3.229416 | 0 |
| clamp floor | 0.163326 | 0.163326 | 0 |
| clamp cap | 1.758980 | 1.758980 | 0 |

The F2 deltas are the published constants' own 5-significant-figure rounding; R1i reproduces to 1e-07.

### 1b. Bars on the ledger (liquid-primary, N=563; in-sample N=2,551 — both match)

| bar cell | published | recomputed | delta |
|---|---|---|---|
| M1 overall, candidate median err (R1i) | 0.1982 | 0.19815 | ~0 |
| M1 overall, RV median err | 0.2153 | 0.21531 | ~0 |
| M1 `T1_low` gap (cand - rv) | +0.0067 | +0.00665 | ~0 |
| M1 `T3_high` gap (cand - rv) | +0.0044 | +0.00435 | ~0 |
| **M1 tercile tally** | **1/3** | **1/3** | — |
| A3 calm-name (`T1_low`) signed bias, shipped F2 | +0.328 | +0.32826 | ~0 |
| M2 d10 liquid overall, R1i vs production | -0.0576 (t -2.36) | -0.0576 (t -2.36) | ~0 |
| M2 d15 liquid ex-earnings, R1i vs production | -0.1829 (t -3.82) | -0.1829 (t -3.82) | ~0 |
| ledger N matched / liquid-primary | 2,551 / 563 | 2,551 / 563 | 0 |

Premium and P&L columns were also checked against the archived ledgers directly:
`f2_shipped` reproduces `ledger_v1.premium_f2_pct` **bit-exactly** (max |diff| 0.0e+00);
`r1i_old` reproduces `ledger_v1_refined.premium_f2refined_pct` to max |diff| 4.3e-08; and this
harness's re-derived `f2_shipped`+gamma arm reproduces the ledger's own stored `f2gamma` arm MAE
**bit-exactly** at both d10 (0.32740964797) and d15 (0.58026078375) — i.e. the vega-ratio
convention, `option_pnl_pct` / `bs_option_pnl_pct` dispatch and the `GAMMA_AWARE=0` routing all
match `build_ledger.py`'s original run exactly.

**Reproduction result: EXACT on every published quantity.**

### 1c. One definitional correction found while reproducing

The VERDICT's load-bearing line — *"on the calibration panel's own reverse-fold OOF, F2
UNDER-predicts calm-name IV (resid +0.048)"* — is reproduced exactly (+0.047913 vs +0.047909), but
that number is **not** a fold-refit. `refit_premium_model.py`'s `F2_existing` OOF-by-tercile rows
evaluate the **frozen, clamped, full-sample** F2 on each fold's rows (`pred_f2_full[is_train_a]`,
lines 388/403-405). A *true* reverse fold (fit 2025+, predict pre-2025, `build_fit_model.py`'s own
unclamped OOF convention) gives **+0.0826** on the same cell — a larger, same-signed quantity.
Both are on the record in `iv_m1_reread.json`; the published number is the frozen-model one and it
reproduces. This does not change the direction of the finding.

---

## 2. STEP 2 — refit on the CORRECTED panel

`.cache/polygon_real_premium/iv_panel_corrected.parquet`: 9,287 rows, 683 symbols, same window
2022-08-01..2026-05-15, RV join 9,286/9,287, VIX join 100%.

### 2a. The target distribution moved down, hardest at the top

| panel | atm_iv p1 | p50 | p90 | p99 | max | derived clamp |
|---|---|---|---|---|---|---|
| old | 0.1633 | 0.4637 | 1.0354 | 1.7590 | 4.4542 | **[0.163326, 1.758980]** |
| corrected | 0.1375 | 0.3850 | 0.9241 | 1.4543 | 2.4516 | **[0.137500, 1.454308]** |

Clamps were re-derived by the same panel-quantile rule (`max(p1, 0.08)` / `min(p99, 2.50)`), keeping
the documented form-independence property.

### 2b. Fitted parameters

| form / param | old | corrected | change |
|---|---|---|---|
| F2 `a` | 0.149202 | **0.077549** | -0.0717 |
| F2 `b_rv` | 0.095446 | **0.105050** | +0.0096 |
| F2 `c_vix` | 0.006242 | **0.004088** | -0.0022 |
| R1i `a_T1_low` (calm) | 0.177044 | **0.098597** | **-0.0784** |
| R1i `a_T2_mid` | 0.185332 | **0.160763** | -0.0246 |
| R1i `a_T3_high` (vol) | 0.306491 | **0.337895** | **+0.0314** |
| R1i `b_rv` | 0.075188 | 0.072302 | -0.0029 |
| R1i `c_vix` | 0.005719 | 0.003084 | -0.0026 |
| R1i frozen terciles | 1.9754 / 3.2294 | 1.9709 / 3.2020 | ~0 |

The piecewise intercept moves in **opposite directions** at the two ends: the calm-tercile intercept
falls by 0.078 while the high-vol intercept rises by 0.031. The old panel's inflation was
concentrated exactly where the audit said the drift was.

### 2c. Fit quality

| form | panel | R2 | in-sample MAE | OOF fwd | OOF rev | symbol-fold | blended | rev/fwd degradation |
|---|---|---|---|---|---|---|---|---|
| F2 | old | 0.3681 | 0.1709 | 0.1728 | 0.1920 | 0.1713 | 0.17867 | +11.1% |
| F2 | **corrected** | **0.6494** | **0.1097** | 0.1216 | 0.1125 | 0.1101 | **0.11473** | **-7.4%** |
| R1i | old | 0.3810 | 0.1681 | 0.1722 | 0.1925 | 0.1688 | 0.17784 | +11.8% |
| R1i | **corrected** | **0.7049** | **0.1039** | 0.1180 | 0.0992 | 0.1045 | **0.10726** | **-15.9%** |

Blended OOF MAE falls ~36-40%. The reverse-fold *degradation* that drove the original F0-vs-F2 and
R1i-vs-R1iii selection arguments **inverts sign** on the corrected panel (reverse fold is now the
*better* direction), i.e. the "backward extrapolation is the hard direction" pathology was itself
partly an artifact of the contaminated labels, which are worst in the early (2022-2024) years.

### 2d. Per-RV-tercile residuals — the corrupted-label diagnostic

`mean_resid = actual_iv - model_iv`; **positive = model UNDER-predicts**.

F2, frozen-model-on-subsample (the published definition):

| panel | subsample | T1_low (calm) | T2_mid | T3_high |
|---|---|---|---|---|
| old | full | -0.0128 | -0.0248 | +0.0415 |
| old | pre-2025 rows | **+0.0479** | +0.0133 | +0.0455 |
| old | 2025+ rows | -0.0862 | -0.0759 | +0.0393 |
| corrected | full | **-0.0472** | -0.0178 | +0.0758 |
| corrected | pre-2025 rows | **-0.0518** | -0.0224 | +0.0366 |
| corrected | 2025+ rows | -0.0412 | -0.0112 | +0.0995 |

F2, true fold-refit OOF:

| panel | fold | T1_low | T2_mid | T3_high |
|---|---|---|---|---|
| old | rev (fit 2025+, predict pre-2025) | **+0.0826** | +0.0450 | +0.0637 |
| corrected | rev | **-0.0964** | -0.0566 | +0.0192 |
| old | fwd | -0.1430 | -0.1272 | +0.0068 |
| corrected | fwd | **-0.0056** | +0.0100 | +0.0599 |

R1i, true fold-refit OOF:

| panel | fold | T1_low | T2_mid | T3_high |
|---|---|---|---|---|
| old | rev | **+0.1224** | +0.0805 | -0.0120 |
| corrected | rev | **-0.0179** | -0.0172 | -0.0931 |
| old | fwd | -0.1543 | -0.1041 | -0.0069 |
| corrected | fwd | **+0.0106** | +0.0114 | +0.0546 |

**The calm-name residual changes sign and collapses in magnitude on every fold.** The old panel's
signature calm-tercile under-prediction (+0.048 frozen / +0.083 true-rev for F2; +0.122 for R1i) is
gone; R1i's corrected calm-tercile OOF residuals are -0.018 (rev) and +0.011 (fwd), i.e. within
noise of zero in both directions. The largest residual magnitude has migrated to `T3_high`.

### 2e. Coverage control — is this the labels or a different row set?

The corrected panel keeps 9,287 rows vs the old panel's 8,643 (common `(symbol,date)` = 8,620;
old-only 23; corrected-only 667). Both panels were refit **restricted to the 8,620 common rows** and
re-applied to the same ledger:

| model (common rows only) | M1 overall median err | gap vs RV | tercile tally | T1_low gap | T2_mid gap | T3_high gap |
|---|---|---|---|---|---|---|
| `f2_old_common` | 0.2097 | -0.0056 | **1/3** | +0.0350 | -0.0276 | +0.0047 |
| `r1i_old_common` | 0.1982 | -0.0171 | **1/3** | +0.0091 | -0.0397 | +0.0044 |
| `f2_corrected_common` | 0.1830 | -0.0323 | **2/3** | **-0.1066** | -0.0636 | +0.0203 |
| `r1i_corrected_common` | 0.1823 | -0.0330 | **2/3** | **-0.0682** | -0.0566 | +0.0068 |

Same rows, same forms, same ledger — only the `atm_iv` target differs. The tally moves 1/3 -> 2/3.
The change is attributable to the **label**, not to coverage.

---

## 3. STEP 3 — the pre-registered bars re-applied verbatim

Validation ledger unchanged; in-sample N=2,551, liquid-primary N=563, shadow-OOS N=349 (excluded);
holdout guard passed at `CALIBRATION_CUTOFF_DATE=2026-06-15`. Every bar is evaluated on the
liquid-primary stratum (A1), with all-inclusive reported as robustness only.

### M1 — verbatim bar

> **M1 (premium level):** F2 median `|model-real|/real` entry premium beats RV-premium overall
> AND in >=2 of 3 RV terciles.

Liquid-primary (RV baseline medians: overall 0.2153 · T1_low 0.2925 · T2_mid 0.2538 · T3_high 0.1578):

| model | overall (N=563) | T1_low (N=163) | T2_mid (N=144) | T3_high (N=256) | overall beats | **tercile tally** |
|---|---|---|---|---|---|---|
| `f2_shipped` | 0.2096 (-0.0057) | 0.3286 (**+0.0361**) | 0.2269 (-0.0268) | 0.1623 (+0.0046) | yes | **1/3** |
| `f2_old` | 0.2096 (-0.0057) | 0.3286 (+0.0360) | 0.2269 (-0.0269) | 0.1624 (+0.0046) | yes | **1/3** |
| `r1i_old` | 0.1982 (-0.0172) | 0.2992 (+0.0066) | 0.2149 (-0.0388) | 0.1622 (+0.0044) | yes | **1/3** |
| `f2_corrected` | **0.1923 (-0.0231)** | **0.1744 (-0.1181)** | **0.1857 (-0.0681)** | 0.1966 (+0.0388) | yes | **2/3** |
| `r1i_corrected` | **0.1977 (-0.0176)** | **0.2219 (-0.0706)** | **0.2104 (-0.0434)** | 0.1834 (+0.0256) | yes | **2/3** |

**Bar outcome:** old-panel models 1/3 (bar requires >=2/3) — the published FAIL, reproduced.
Both corrected-panel models reach **2/3 terciles and beat RV overall**, which is the literal bar.
The failing tercile has moved from `T1_low` (calm) to `T3_high` (high-RV).

Date-clustered paired-difference companions (mean, not median; `cand - rv`, negative favours candidate):

| model | overall | T1_low | T2_mid | T3_high |
|---|---|---|---|---|
| `f2_shipped` | -0.0373 (t -0.25) | **+0.3450 (t +3.10)** | +0.0569 (t +2.55) | -0.3337 (t -1.05) |
| `r1i_old` | -0.0143 (t -0.13) | **+0.2968 (t +2.80)** | +0.0528 (t +2.33) | -0.2502 (t -1.06) |
| `f2_corrected` | -0.1837 (t -0.97) | +0.0394 (t +0.88) | **-0.0298 (t -2.55)** | -0.4124 (t -0.99) |
| `r1i_corrected` | -0.1321 (t -1.05) | -0.0140 (t -0.75) | +0.0047 (t +0.33) | -0.2843 (t -1.03) |

The statistically significant calm-tercile penalty (t +3.10 / +2.80) disappears (t +0.88 / -0.75).
No cell for a corrected model is significantly worse than production; every reported t is finite
(asserted in-harness).

All-inclusive (robustness only; lastPrice-staleness caveat; RV overall 0.2180, N=2,551):
`f2_shipped` 0.2395 (+0.0215, 1/3) · `r1i_old` 0.2262 (+0.0082, 1/3) ·
`f2_corrected` 0.1962 (-0.0217, 2/3) · `r1i_corrected` 0.1946 (-0.0234, 2/3).
Both strata agree in direction, which was not true of the old-panel models (they beat RV on the
liquid stratum overall but lost on the all-inclusive one).

Cross-cutting cells (liquid stratum, median err gap vs RV; negative = candidate better):

| cell | N | `f2_shipped` | `r1i_old` | `f2_corrected` | `r1i_corrected` |
|---|---|---|---|---|---|
| era 2025 | 263 | -0.0252 | -0.0390 | -0.0769 | -0.0555 |
| era 2026H1 | 300 | +0.0264 | +0.0139 | +0.0258 | +0.0121 |
| band 75+ | 128 | +0.0410 | +0.0344 | +0.0430 | +0.0322 |
| band 70-74 | 435 | -0.0136 | -0.0254 | -0.0384 | -0.0273 |
| ex-earnings | 442 | -0.0117 | -0.0218 | -0.0394 | -0.0310 |
| earnings window | 121 | +0.0136 | -0.0116 | +0.0282 | +0.0070 |

Era sign-consistency is **not** achieved by any model, old or corrected: every model beats RV in
2025 and loses in 2026H1. The 75+ band is worse than RV for every model. These are unchanged
patterns, not introduced by the correction.

### A3 telemetry (report-only; `k` never retuned)

`k_implied` is a property of the ledger, identical across models: median 0.4024 overall
(T1_low 0.4157 · T2_mid 0.3963 · T3_high 0.4027) against the assumed 0.4.

Candidate-vs-real **signed** bias, median (positive = model OVER-prices the real contract):

| cell | `f2_shipped` | `r1i_old` | `f2_corrected` | `r1i_corrected` |
|---|---|---|---|---|
| overall | +0.0520 | +0.0617 | -0.0968 | -0.1125 |
| **T1_low (calm)** | **+0.3283** | +0.2933 | **+0.0086** | **-0.1266** |
| T2_mid | +0.1543 | +0.1382 | -0.0461 | -0.0150 |
| T3_high | -0.1292 | -0.1068 | -0.1810 | -0.1383 |

The **+0.328 calm-name over-pricing that the VERDICT identified as the structural blocker is
+0.009 / -0.127 after the target correction.** The residual mis-pricing is now a general
under-pricing, largest in the high-RV tercile.

### M2 — verbatim bar

> **M2 (P&L fidelity):** F2+gamma beats RV+const-delta on per-trade P&L MAE at d10 AND d15
> overall, and is not worse in the high-RV tercile.

(The refinement round's R2 re-scope made ex-earnings the deciding d15 cell; both readings below.)

MAE, liquid stratum, candidate = `<model>` + gamma-BS vs production `rvconst`:

| cell | N | rvconst | `f2_shipped` | `r1i_old` | `f2_corrected` | `r1i_corrected` |
|---|---|---|---|---|---|---|
| d10 overall | 496 | 0.3836 | 0.3274 | 0.3260 | 0.3334 | 0.3438 |
| d10 clustered diff (t) | | — | -0.0561 (-2.21) | **-0.0576 (-2.36)** | -0.0501 (-1.74) | -0.0398 (-1.51) |
| d10 ex-earnings | 390 | 0.4018 | 0.3200 | 0.3213 | **0.2990** | 0.3102 |
| d10 ex-earnings diff (t) | | — | -0.0818 (-2.83) | -0.0805 (-2.87) | **-0.1028 (-3.25)** | -0.0916 (-3.05) |
| d10 `T3_high` | 235 | 0.4327 | 0.3593 | 0.3542 | 0.3958 | 0.3760 |
| d15 overall | 210 | 0.5713 | 0.5803 | 0.5716 | 0.6181 | 0.6087 |
| d15 overall diff (t) | | — | +0.0090 (+0.11) | +0.0003 (+0.00) | +0.0468 (+0.45) | +0.0374 (+0.46) |
| **d15 ex-earnings (deciding)** | 159 | 0.5933 | 0.3875 | 0.4104 | **0.3482** | 0.3922 |
| d15 ex-earnings diff (t) | | — | -0.2058 (-3.88) | -0.1829 (-3.82) | **-0.2451 (-3.71)** | -0.2011 (-3.55) |
| d15 earnings window (watch) | 51 | 0.5027 | 1.1812 | 1.0741 | **1.4595** | 1.2837 |
| d15 earnings diff (t) | | — | +0.6785 (+2.20) | +0.5714 (+2.15) | +0.9568 (+2.61) | +0.7810 (+2.82) |
| d15 `T3_high` | 104 | 0.7719 | 0.8528 | 0.8405 | 0.9260 | 0.8687 |

**Bar outcome:** d10 overall — every candidate beats production; the corrected models beat it by
less than the old ones and at a weaker t (-1.74 / -1.51 vs -2.36). d15 overall — every candidate is
point-worse than production (the earnings window drives it), so the literal un-scoped d15 leg is not
met by any model, old or corrected. d15 ex-earnings (the R2 deciding cell) — every candidate clears
it comfortably; `f2_corrected` is the strongest (-0.2451, t -3.71). The "not worse in the high-RV
tercile" leg is not met at d15 by any model (all positive), and at d10 the corrected models are
point-better (-0.0369 / -0.0567) but by less than the old ones. The earnings-window watch line is
**worse** with the corrected target (+0.957 / +0.781 vs +0.679 / +0.571).

Direction summary: the corrected target improves the PREMIUM-LEVEL bar (M1) and the ex-earnings d15
path bar, and slightly degrades d10 overall and the earnings-window cell.

### M3 — verbatim bar

> **M3 (decision fidelity):** F2+gamma TP-hit classification agreement vs the real path >=
> production's, AND its SL-hit false-negative rate is not worse.

**Not re-derivable for any new premium model from the persisted ledger**, and carried forward
verbatim from `results/pertrade_results.json` (liquid|overall, ripe15 gate, N=212):

| arm | TP-agreement | SL-FNR (N_real_sl=33) |
|---|---|---|
| `rvconst` (production) | 0.811 | 0.152 |
| `f2gamma` (published candidate) | 0.830 | 0.212 |
| paired agreement diff | +0.0189 (t +0.96) | — |
| paired SL-FNR diff | — | +0.0606 (t +1.39), UNDERPOWERED at N=33 |

Reason: the first-touch TP/SL walk needs the DAILY t=1..15 forward underlying **and** option-IV
path; `ledger_v1.parquet` persists only the d5/d10/d15 checkpoints. Recomputing M3 for a new
premium requires re-running `build_ledger.py` (~2,551 forward `option_prices` path queries,
`db=heavy`) — deliberately not run here (the brief reserves heavy steps for the queue). The
2026-07-13 refinement round hit the same wall and made the same carry-forward.

---

## 4. STEP 4 — SUPPLEMENTARY read against real traded Polygon contract prices

**Clearly supplementary. The primary M1 is the verbatim Step-3 one.**

Target swapped to `.cache/polygon_real_premium/real_premium_ledger.parquet` (3,339 kept rows,
2022-08-09..2026-07-24, real traded Polygon daily contract prices — not `lastPrice`).
`real_premium_pct = entry_premium_real / spot_unadj`; model premium uses the Polygon contract's own
`dte_cal`; RV baseline is the ledger's own DTE-invariant `premium_rv_pct`; RV/VIX are the validation
ledger's engine-faithful values.

**Overlap N = 548** of 2,551 in-sample validation rows (2025-02-11..2026-06-12; by the validation
ledger's own band column, 343 are 75+ and 205 are 70-74).

> Provenance note found while joining: the two ledgers disagree on `overall` for **354 of the 548**
> overlapping rows. The validation ledger's `overall` matches `.cache/rel_strength/rs_ledger.parquet`
> **exactly** (0 mismatches on 2,009 joinable rows), while `polygon_real_premium/pull.py` reads the
> live `scores` table (`pull.py:308`), which has been overwritten in place by later recalculation.
> The live values are higher: median diff +1, mean +2.43, p95 +9. That is why 205 rows the real-
> premium ledger admitted as "75+" carry band `70-74` here. It does not affect the arithmetic below
> (every model input except the real-price target comes from the validation ledger), but it does
> mean the overlap is not a clean 75+ cohort.

Sub-strata: Polygon-liquid 428 · same strike AND same expiration as the yfinance contract 211 ·
same-contract AND Polygon-liquid 177 · yfinance `liquid_ge5` 148. Terciles inside each stratum run
33-211, all at or above the N=30 floor, so nothing is SKIPPED — but 148 and 177 are thin and the
two harnesses select different contracts on 61% of overlapping rows, so the same-contract strata are
the only apples-to-apples cells.

Median `|model-real|/real`, gap vs the RV baseline (negative = candidate better), tercile tally:

| stratum | N | RV median | `f2_shipped` | `r1i_old` | `f2_corrected` | `r1i_corrected` |
|---|---|---|---|---|---|---|
| all overlap | 548 | 0.1700 | +0.1091 (0/3) | +0.0916 (0/3) | +0.0415 (0/3) | **+0.0040 (1/3)** |
| Polygon-liquid | 428 | 0.1670 | +0.0916 (0/3) | +0.0811 (0/3) | +0.0463 (1/3) | **+0.0074 (2/3)** |
| yfinance liquid_ge5 | 148 | 0.1306 | +0.0996 (0/3) | +0.0885 (0/3) | +0.0641 (0/3) | +0.0414 (0/3) |
| same contract | 211 | 0.2009 | +0.0821 (1/3) | +0.0413 (1/3) | +0.0082 (2/3) | **-0.0307 (3/3)** |
| same contract + liquid | 177 | 0.2009 | +0.0467 (1/3) | +0.0282 (1/3) | +0.0125 (1/3) | **-0.0302 (3/3)** |

Clustered paired-difference t (mean errors, `cand - rv`):

| stratum | `f2_shipped` | `r1i_old` | `f2_corrected` | `r1i_corrected` |
|---|---|---|---|---|
| all overlap | +8.66 | +7.71 | +3.55 | **-0.58** |
| Polygon-liquid | +7.64 | +6.67 | +3.59 | **-0.43** |
| yfinance liquid_ge5 | +6.56 | +5.68 | +4.36 | +1.77 |
| same contract | +5.15 | +3.65 | -0.07 | -1.81 |
| same contract + liquid | +4.20 | +2.91 | +0.24 | -1.54 |

Calm-tercile gap against real prints (the diagnostic cell), all-overlap stratum:
`f2_shipped` **+0.4168** -> `f2_corrected` +0.0629 -> `r1i_corrected` **-0.0289**.

Two things the supplementary read says that the primary one does not:

1. Against real traded prints the **RV baseline is markedly better than against `lastPrice`**
   (median error 0.170 vs 0.215), so every model has a higher bar to clear. On the widest overlap
   strata no model beats it outright; the old-panel models lose by a highly significant margin
   (t +8.7 / +7.7) and the corrected models are at parity (t -0.58 / -0.43).
2. On the same-contract strata — the only cells where the two harnesses priced the *same* option —
   `r1i_corrected` beats the RV baseline overall and in **3/3** terciles (N=211 and N=177,
   t -1.81 / -1.54).

Both readings are underpowered relative to the primary bar and are reported as supplementary only.

---

## 5. Limitations of this re-read

1. **M3 was not recomputed** for any new premium model (see Step 3). Only M1/M2 are fresh.
2. The corrected panel carries a **delisted-name fallback**: where yfinance has no series the
   as-traded spot degrades to the adjusted close (`spot_source='ph_adjusted'`, ~1.3% of the old
   panel's rows per `REBUILD_NOTES.md`). Those rows were kept, mirroring the old panel; no
   sensitivity cut was run on them.
3. The corrected panel reproduces the old panel's `[d+20, d+45]` chain-window coverage limit
   (`miss:no_chain` on ~9.5% of pairs, biased toward weekly-listed large caps) deliberately, for
   comparability. Both panels share it.
4. The corrected panel's honest `pnl15`/`fwd_bars` fix is **not exercised** by this re-read — the
   premium model's regression target is `atm_iv` only. The `pnl15` defect matters to other
   consumers of that panel, not to M1/M2 here.
5. Era sign-inconsistency (2025 beats RV, 2026H1 does not) and the 75+-band cell being worse than RV
   are present in **every** model, old and corrected, and are untouched by the target correction.
6. The Step-4 overlap is not a clean 75+ cohort — the real-premium ledger's `overall` comes from
   the live, since-overwritten `scores` table and disagrees with the validation ledger / rs_ledger
   on 354/548 rows (median +1, p95 +9). See the provenance note in Step 4.
7. The validation ledger remains yfinance `lastPrice` with ~78% zero-volume base rate; the
   liquid-primary N=563 and the d15/M3 sub-cells (159-212, and 33 real SL events) are unchanged and
   still thin.
