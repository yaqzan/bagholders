"""
Mechanical execution of the LOCKED ct_bounce_formula PREREG (lock=df5ccf6d, 2026-08-13).
experiments/ct_bounce_formula_2026_08/PREREG.md

Pure file analysis over .cache/relabel_substrate/honest_ledger_v1.parquet (READ-ONLY,
LOCKED). No MySQL, no monte_carlo import. Mechanical role: raw model diagnostics only,
no ship/candidate editorializing beyond the prereg's own literal success-bar test.

Operationalization choices made where the prereg's natural-language spec required one
(all disclosed, all fixed BEFORE any validation outcome was read):

1. COHORT ANOMALY (structural, not a sampling artifact -- verified against
   tools/ct_predicate.py source): ct_tag(overall, trend, 'call') returns 'ct_call'
   iff overall>=70 AND trend<=CT_CALL_TREND_MAX(=20). Every row in this ledger
   already has overall>=70 by the substrate's own construction. Therefore, on this
   ledger, ct_flag==True is mathematically IDENTICAL to trend<=20 -- the prereg's
   "ct_flag OR trend<=20" cohort condition is a provable tautology here (confirmed
   empirically: all 153 qualifying rows satisfy BOTH conditions simultaneously,
   zero satisfy only one). Cohort N = 153 (embargo+ripe+cohort applied), not a
   larger population -- this is a genuinely small-N regime, which is presumably
   why the prereg locked "small-N discipline" model classes.
2. FEATURES -- "pullback depth/duration (price vs recent peak)" has NO literal
   column in the locked ledger (no recent-high/ATH/drawdown-from-peak field
   exists among the 83 columns), and reconstructing it from raw PriceHistory
   requires MySQL, which is explicitly out of scope for this execution (task
   brief: "Pure file analysis ... NO MySQL"). Substituted with the closest
   PIT-safe in-ledger proxies: `trend`, `bb`, `stoch` (the three technical
   components whose composites most directly encode price-vs-recent-range
   positioning). Flagged here and in the final report as a scope-forced
   substitution, not a silent one.
   Full locked feature set (24 raw columns, one row per named prereg category):
     pullback proxy:  trend, bb, stoch
     RSI:              rsi
     volume fields:    volume_signal, volume_magnitude, opt_vol_30d_atm,
                        opt_vol_30d_allcall, opt_vol_30d_total
     weekly fields:     w_comp, w_bias, w_mom, w_adj, wadj_completed, wadj_partial,
                        pre_regime, pre_boost, td, weekly_transition_t, weekly_adj_gap
     regime composite:  regime_composite
     liquidity tier:    liquidity_tier
     PIT mcap:          mcap_b_pit
     price level:       l2_entry (signal-date close; the L2 sim's own entry price,
                        at-signal, non-look-ahead)
   Explicitly EXCLUDED and why: delisted (banned by name -- outcome leakage);
   every l1_*/l2_* column that is a post-entry outcome or the label itself
   (l1_exit_return, l1_mae_pct, l2_kind, l2_expected, l2_cal_held, ...);
   ct_flag/ct_tag (cohort-definers, not named as a feature category, and per
   point 1 above ct_flag is degenerate -- constant True across this whole
   cohort, zero information); overall/macd/technical_alignment (not named);
   symbol (identity, not PIT feature); any calendar feature (none exist as
   raw columns anyway; `date` used only for the time split, never as a
   feature value).
3. MODEL CLASSES -- "Logistic regression and monotone-constrained shallow GBM"
   read as: LogisticRegression + HistGradientBoostingClassifier(max_depth=3,
   max_iter<=200, monotonic_cst=...) for the classification target
   (l2_kind=='tp'), and their natural linear-regression counterparts, Ridge
   (L2-regularized, matching sklearn LogisticRegression's default L2 penalty)
   + HistGradientBoostingRegressor(max_depth=3, max_iter<=200,
   monotonic_cst=...) for the regression target (l2_expected). "Logistic
   regression" cannot itself fit a continuous target, so this is the minimal,
   same-family substitution for the regression side, not a fifth model class.
   Monotonic constraint SIGNS are derived mechanically from the linear/logistic
   model's own fitted coefficient signs on each continuous raw feature (0 for
   one-hot categorical dummy columns, which have no ordinal meaning) -- this
   avoids injecting an undisclosed domain prior (this system's own doctrine is
   momentum, NOT mean-reversion -- traps.md item 0 -- so assuming "oversold ->
   bounce" a priori would be an uncontrolled researcher degree of freedom).
4. TIME SPLIT WITH 30-DAY PURGE: "train <=2025-06-30, validate
   2025-07-01..2026-06-15, with a 30-day purge gap" operationalized as: the
   purge zone (2025-06-01 .. 2025-06-30 inclusive, 30 days) is dropped from
   BOTH train and validation (removes training rows whose ~27-calendar-day
   L2 hold window could still be open at the validation boundary); actual
   TRAIN_FIT = date <= 2025-05-31; VALIDATION = date in
   [2025-07-01, 2026-06-15] (unpurged -- it is chronologically after training,
   no forward-leak risk into it).
5. "top-quintile-by-model" at small N: top_n = ceil(0.2 * N) for whatever N
   the comparison's own row set has (validation set, its L3-covered subset,
   or its delisted-excluded subset each get their own N and their own top_n --
   all three are reported so thinness is visible, never hidden). Ties in
   raw-`overall` ranking (an integer column, ties likely at this N) are broken
   by (overall desc, symbol asc, date asc) -- a fixed, non-outcome-derived key,
   never by the outcome being compared (that would bias the raw-ordering
   baseline).
6. Preprocessing: two parallel feature matrices built from the SAME 24 raw
   columns, both fit only on TRAIN_FIT: X_lr (continuous median-imputed +
   missing-indicator dummies + one-hot categoricals + StandardScaler, for
   Ridge/LogisticRegression, which cannot accept NaN) and X_gbm (continuous
   passed through with native NaN + the same one-hot categoricals, for
   HistGradientBoosting, which handles NaN natively and can use the
   missingness pattern itself as signal).
"""
import sys
import os
import json
import time
import math
import datetime
from pathlib import Path

import numpy as np
import polars as pl
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.inspection import permutation_importance
from sklearn.metrics import r2_score, mean_absolute_error, accuracy_score, roc_auc_score, brier_score_loss

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DRIVER_DIR = Path(__file__).resolve().parent
CAMPAIGN_DIR = DRIVER_DIR.parent
ROOT = CAMPAIGN_DIR.parent.parent
LEDGER_PATH = ROOT / '.cache' / 'relabel_substrate' / 'honest_ledger_v1.parquet'
OUT_DIR = CAMPAIGN_DIR / 'out'
LOG_DIR = CAMPAIGN_DIR / 'logs'
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------
EMBARGO_DATE = datetime.date(2026, 6, 15)
TRAIN_FIT_END = datetime.date(2025, 5, 31)      # actual train cutoff after purge
PURGE_LO = datetime.date(2025, 6, 1)            # purge zone start (inclusive)
PURGE_HI = datetime.date(2025, 6, 30)           # purge zone end (inclusive) = nominal train boundary
VAL_START = datetime.date(2025, 7, 1)
VAL_END = EMBARGO_DATE
EFFECT_BAR = 0.03           # +3.0pp, raw fraction units, same as residual_mining
RANDOM_STATE = 42
GBM_MAX_DEPTH = 3
GBM_MAX_ITER = 200

CT_CALL_TREND_MAX = 20      # from tools/ct_predicate.py / strategy_config.STRATEGY_30DTE

CONTINUOUS_RAW = [
    'trend', 'bb', 'stoch',                                   # pullback depth/duration proxy
    'rsi',                                                     # RSI
    'volume_magnitude', 'opt_vol_30d_atm', 'opt_vol_30d_allcall', 'opt_vol_30d_total',  # volume fields (continuous)
    'w_comp', 'w_bias', 'w_mom', 'w_adj', 'wadj_completed', 'wadj_partial',
    'pre_regime', 'pre_boost', 'td', 'weekly_transition_t', 'weekly_adj_gap',  # weekly fields (11)
    'regime_composite',                                        # regime composite
    'mcap_b_pit',                                              # PIT mcap
    'l2_entry',                                                # price level
]
CATEGORICAL_RAW = ['volume_signal', 'liquidity_tier']           # volume field (categorical) + liquidity tier
ALL_RAW_FEATURES = CONTINUOUS_RAW + CATEGORICAL_RAW
assert len(ALL_RAW_FEATURES) == 24, f'expected 24 raw features, got {len(ALL_RAW_FEATURES)}'
BANNED_EXPLICIT = ['delisted']  # explicit prereg ban -- asserted absent from feature set below


def log(msg, fh):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line)
    fh.write(line + '\n')
    fh.flush()


def ceil_top_n(n):
    return max(1, math.ceil(0.2 * n)) if n > 0 else 0


def _safe_auc(y_true, y_score):
    y_true = np.asarray(y_true)
    if len(np.unique(y_true)) < 2:
        return None  # AUC undefined with a single class present
    return float(roc_auc_score(y_true, y_score))


def build_matrices(df_pd, train_mask):
    """Build X_lr (imputed+scaled+onehot) and X_gbm (native-NaN+onehot), fit
    all preprocessing statistics on train_mask rows ONLY, applied to all rows."""
    cont = df_pd[CONTINUOUS_RAW].astype(float)
    cat = df_pd[CATEGORICAL_RAW].astype(object)

    # --- X_gbm: continuous passthrough (native NaN) + one-hot (categories from train) ---
    gbm_parts = [cont.reset_index(drop=True)]
    gbm_cols = list(CONTINUOUS_RAW)
    cat_categories = {}
    for c in CATEGORICAL_RAW:
        train_cats = sorted(cat.loc[train_mask, c].dropna().unique().tolist())
        cat_categories[c] = train_cats
        for lvl in train_cats:
            colname = f'{c}__{lvl}'
            gbm_parts.append(pd.Series((cat[c] == lvl).astype(float).values, name=colname))
            gbm_cols.append(colname)
    X_gbm = pd.concat(gbm_parts, axis=1)
    X_gbm.columns = gbm_cols

    # --- X_lr: median-impute (train medians) + missing-indicator + onehot + scale ---
    lr_parts = []
    lr_cols = []
    train_medians = {}
    for c in CONTINUOUS_RAW:
        med = cont.loc[train_mask, c].median()
        train_medians[c] = med
        filled = cont[c].fillna(med)
        lr_parts.append(pd.Series(filled.values, name=c))
        lr_cols.append(c)
        n_missing_train = int(cont.loc[train_mask, c].isna().sum())
        if n_missing_train > 0:
            miss_ind = cont[c].isna().astype(float)
            colname = f'{c}__missing'
            lr_parts.append(pd.Series(miss_ind.values, name=colname))
            lr_cols.append(colname)
    for c in CATEGORICAL_RAW:
        for lvl in cat_categories[c]:
            colname = f'{c}__{lvl}'
            lr_parts.append(pd.Series((cat[c] == lvl).astype(float).values, name=colname))
            lr_cols.append(colname)
    X_lr_raw = pd.concat(lr_parts, axis=1)
    X_lr_raw.columns = lr_cols

    scaler = StandardScaler()
    scaler.fit(X_lr_raw.loc[train_mask].values)
    X_lr_scaled = pd.DataFrame(scaler.transform(X_lr_raw.values), columns=lr_cols)

    meta = dict(cat_categories=cat_categories, train_medians=train_medians,
                scaler_mean=dict(zip(lr_cols, scaler.mean_.tolist())),
                scaler_scale=dict(zip(lr_cols, scaler.scale_.tolist())))
    return X_gbm, X_lr_scaled, meta


def monotonic_cst_from_coefs(coef_dict, gbm_cols):
    """gbm_cols in X_gbm order. For each raw CONTINUOUS feature, look up its own
    (non-missing-indicator) coefficient sign from the linear/logistic fit on
    X_lr; one-hot dummy columns (both continuous-derived none, categorical
    dummies) get constraint 0 (no ordinal meaning to constrain)."""
    cst = []
    for col in gbm_cols:
        if col in CONTINUOUS_RAW:
            c = coef_dict.get(col, 0.0)
            cst.append(int(np.sign(c)) if abs(c) > 1e-12 else 0)
        else:
            cst.append(0)  # one-hot categorical dummy
    return cst


def top_quintile_mean(sub_pd, score_col, outcome_col, tie_cols=('overall', 'symbol', 'date')):
    """Rank sub_pd desc by score_col (ties broken by tie_cols, never by outcome),
    take ceil(0.2*N) rows, return (mean_outcome_top, n_top, n_total)."""
    n = len(sub_pd)
    if n == 0:
        return np.nan, 0, 0
    n_top = ceil_top_n(n)
    sort_cols = [score_col] + [c for c in tie_cols if c != score_col]
    ascending = [False] + [False if c == 'overall' else True for c in tie_cols if c != score_col]
    ordered = sub_pd.sort_values(sort_cols, ascending=ascending, kind='mergesort')
    top = ordered.head(n_top)
    return float(top[outcome_col].mean()), n_top, n


def success_bar_test(val_pd, score_col, model_name, fh):
    """Implements the LOCKED success bar's 3 parts for one model's score_col,
    on val_pd (already the validation-window rows). Returns a dict of raw
    diagnostics -- always computed and reported, never suppressed for thinness."""
    out = dict(model=model_name)

    # (1) top-quintile-by-model vs top-quintile-by-raw-overall, on l2_expected
    mean_model, n_top_model, n_val = top_quintile_mean(val_pd, score_col, 'l2_expected')
    mean_raw, n_top_raw, _ = top_quintile_mean(val_pd, 'overall', 'l2_expected')
    advantage_pp = (mean_model - mean_raw) * 100 if not (np.isnan(mean_model) or np.isnan(mean_raw)) else np.nan
    out.update(val_n=n_val, val_n_top=n_top_model,
               val_top_mean_model_l2ev=mean_model, val_top_mean_raw_l2ev=mean_raw,
               advantage_pp=advantage_pp,
               success_1_effect_pass=bool(not np.isnan(advantage_pp) and advantage_pp >= EFFECT_BAR * 100))

    # (2) same-sign advantage on the L3 gold subset (covered rows only)
    l3_sub = val_pd[val_pd['l3_realized_pnl_cd27'].notna()].copy()
    if len(l3_sub) >= 2:
        mean_model_l3, n_top_model_l3, n_l3 = top_quintile_mean(l3_sub, score_col, 'l3_realized_pnl_cd27')
        mean_raw_l3, n_top_raw_l3, _ = top_quintile_mean(l3_sub, 'overall', 'l3_realized_pnl_cd27')
        l3_advantage = (mean_model_l3 - mean_raw_l3) if not (np.isnan(mean_model_l3) or np.isnan(mean_raw_l3)) else np.nan
    else:
        mean_model_l3, mean_raw_l3, l3_advantage, n_top_model_l3, n_l3 = np.nan, np.nan, np.nan, 0, len(l3_sub)
    same_sign_l3 = bool(not np.isnan(l3_advantage) and not np.isnan(advantage_pp)
                         and np.sign(l3_advantage) == np.sign(advantage_pp) and advantage_pp != 0)
    out.update(l3_n=n_l3, l3_n_top=n_top_model_l3,
               l3_top_mean_model=mean_model_l3, l3_top_mean_raw=mean_raw_l3,
               l3_advantage=l3_advantage,
               success_2_l3_same_sign=same_sign_l3, l3_testable=bool(n_l3 >= 2))

    # (3) survives delisted-exclusion
    surv_sub = val_pd[~val_pd['delisted']].copy()
    if len(surv_sub) >= 2:
        mean_model_s, n_top_model_s, n_surv = top_quintile_mean(surv_sub, score_col, 'l2_expected')
        mean_raw_s, n_top_raw_s, _ = top_quintile_mean(surv_sub, 'overall', 'l2_expected')
        surv_advantage_pp = (mean_model_s - mean_raw_s) * 100 if not (np.isnan(mean_model_s) or np.isnan(mean_raw_s)) else np.nan
    else:
        surv_advantage_pp, n_top_model_s, n_surv = np.nan, 0, len(surv_sub)
    out.update(survivor_n=n_surv, survivor_n_top=n_top_model_s,
               survivor_advantage_pp=surv_advantage_pp,
               success_3_survivor_pass=bool(not np.isnan(surv_advantage_pp) and surv_advantage_pp >= EFFECT_BAR * 100))

    out['candidate_entry_criterion'] = bool(out['success_1_effect_pass'] and out['success_2_l3_same_sign']
                                             and out['success_3_survivor_pass'])
    log(f'  success-bar[{model_name}]: N_val={n_val} n_top={n_top_model} advantage={advantage_pp:.3f}pp '
        f'(bar1={out["success_1_effect_pass"]}) | L3 N={n_l3} n_top={n_top_model_l3} '
        f'advantage={l3_advantage if not np.isnan(l3_advantage) else float("nan"):.4f} (bar2={out["success_2_l3_same_sign"]}) | '
        f'survivor N={n_surv} advantage={surv_advantage_pp:.3f}pp (bar3={out["success_3_survivor_pass"]}) '
        f'=> candidate={out["candidate_entry_criterion"]}', fh)
    return out


def calibration_curve(pd_df, prob_col, label_col, n_bins, model_name, split_name):
    n = len(pd_df)
    if n == 0:
        return []
    n_bins_eff = max(1, min(n_bins, n))
    ranks = pd_df[prob_col].rank(method='first')
    bin_idx = np.minimum(n_bins_eff - 1, ((ranks - 1) * n_bins_eff / n).astype(int).values)
    rows = []
    for b in range(n_bins_eff):
        mask = bin_idx == b
        if mask.sum() == 0:
            continue
        rows.append(dict(model=model_name, split=split_name, bin=b, n=int(mask.sum()),
                          mean_predicted_prob=float(pd_df.loc[mask, prob_col].mean()),
                          realized_tp_rate=float(pd_df.loc[mask, label_col].mean())))
    return rows


def main():
    log_path = LOG_DIR / 'bounce_formula.log'
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as fh:
        log('ct_bounce_formula_2026_08 -- mechanical execution start', fh)
        assert LEDGER_PATH.is_file(), f'LOCKED ledger not found at {LEDGER_PATH}'
        df_full = pl.read_parquet(LEDGER_PATH)
        n_total = df_full.height
        log(f'loaded {n_total} rows x {df_full.width} cols', fh)

        # --- HOLDOUT EMBARGO ------------------------------------------------
        n_embargo_dropped = df_full.filter(pl.col('date') > EMBARGO_DATE).height
        df = df_full.filter(pl.col('date') <= EMBARGO_DATE)
        assert df.filter(pl.col('date') > EMBARGO_DATE).height == 0
        log(f'HOLDOUT EMBARGO: dropped {n_embargo_dropped} rows with date > 2026-06-15; {df.height} remain', fh)

        # --- RIPE FILTER -------------------------------------------------------
        n_unripe = df.filter(~pl.col('l2_ripe')).height
        df = df.filter(pl.col('l2_ripe'))
        log(f'RIPE FILTER: dropped {n_unripe} unripe rows; {df.height} remain', fh)

        # --- COHORT: ct_flag OR trend<=20 ---------------------------------------
        n_pre_cohort = df.height
        n_ct = df.filter(pl.col('ct_flag')).height
        n_trend20 = df.filter(pl.col('trend') <= CT_CALL_TREND_MAX).height
        n_both = df.filter(pl.col('ct_flag') & (pl.col('trend') <= CT_CALL_TREND_MAX)).height
        cohort = df.filter(pl.col('ct_flag') | (pl.col('trend') <= CT_CALL_TREND_MAX))
        log(f'COHORT gate (ct_flag OR trend<={CT_CALL_TREND_MAX}) applied to {n_pre_cohort} ripe/embargoed rows: '
            f'ct_flag={n_ct}, trend<=20={n_trend20}, BOTH={n_both}, cohort N={cohort.height}', fh)
        if n_ct == n_trend20 == n_both == cohort.height:
            log(f'  ANOMALY (structural, confirmed against tools/ct_predicate.py source): '
                f'ct_flag is mathematically IDENTICAL to trend<=20 on this ledger, because '
                f'ct_tag() requires overall>=70 AND trend<=20, and every ledger row already '
                f'has overall>=70. The OR condition is a tautology here -- cohort N is genuinely '
                f'{cohort.height}, not a larger ct_flag-widened population.', fh)
        cohort_n = cohort.height
        log(f'cohort date range: {cohort["date"].min()} .. {cohort["date"].max()}', fh)

        df_pd = cohort.to_pandas()
        df_pd['date'] = pd.to_datetime(df_pd['date']).dt.date

        # --- TIME SPLIT with 30-day purge --------------------------------------
        train_mask = df_pd['date'] <= TRAIN_FIT_END
        purge_mask = (df_pd['date'] > TRAIN_FIT_END) & (df_pd['date'] <= PURGE_HI)
        val_mask = (df_pd['date'] >= VAL_START) & (df_pd['date'] <= VAL_END)
        assert (train_mask.sum() + purge_mask.sum() + val_mask.sum()) == cohort_n
        log(f'TIME SPLIT: train_fit(date<={TRAIN_FIT_END})={int(train_mask.sum())}, '
            f'purge({PURGE_LO}..{PURGE_HI})={int(purge_mask.sum())} [DROPPED], '
            f'validation({VAL_START}..{VAL_END})={int(val_mask.sum())}', fh)

        train_pd = df_pd[train_mask].reset_index(drop=True)
        val_pd = df_pd[val_mask].reset_index(drop=True)
        train_n, val_n = len(train_pd), len(val_pd)
        log(f'FINAL: train_n={train_n}, val_n={val_n} (SMALL-N regime -- top-quintile at val_n={val_n} '
            f'is only {ceil_top_n(val_n)} row(s); every downstream number carries this caveat)', fh)

        assert all(f not in ALL_RAW_FEATURES for f in BANNED_EXPLICIT)

        # --- targets -------------------------------------------------------------
        for part in (train_pd, val_pd, df_pd):
            part['y_clf'] = (part['l2_kind'] == 'tp').astype(int)
        y_reg_train = train_pd['l2_expected'].astype(float).values
        y_clf_train = train_pd['y_clf'].astype(int).values
        log(f'TRAIN target balance: l2_kind==tp {int(y_clf_train.sum())}/{train_n} '
            f'({100*y_clf_train.mean():.1f}%); l2_expected mean={y_reg_train.mean():.4f} std={y_reg_train.std():.4f}', fh)
        log(f'VAL target balance: l2_kind==tp {int(val_pd["y_clf"].sum())}/{val_n} '
            f'({100*val_pd["y_clf"].mean():.1f}%); l2_expected mean={val_pd["l2_expected"].mean():.4f}', fh)

        # --- feature matrices (fit on train only) ---------------------------------
        combined = pd.concat([train_pd, val_pd], axis=0, ignore_index=True)
        combined_train_mask = np.array([True] * train_n + [False] * val_n)
        X_gbm_all, X_lr_all, prep_meta = build_matrices(combined, combined_train_mask)
        X_gbm_train, X_gbm_val = X_gbm_all.iloc[:train_n].reset_index(drop=True), X_gbm_all.iloc[train_n:].reset_index(drop=True)
        X_lr_train, X_lr_val = X_lr_all.iloc[:train_n].reset_index(drop=True), X_lr_all.iloc[train_n:].reset_index(drop=True)
        log(f'feature matrices built: X_lr {X_lr_train.shape[1]} cols (imputed+onehot+scaled), '
            f'X_gbm {X_gbm_train.shape[1]} cols (native-NaN+onehot)', fh)
        log(f'  categorical levels observed in TRAIN: {prep_meta["cat_categories"]}', fh)

        # --- fit linear models (Ridge regression, Logistic classification) -------
        ridge = Ridge(alpha=1.0, random_state=RANDOM_STATE)
        ridge.fit(X_lr_train.values, y_reg_train)
        logit = LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)
        logit.fit(X_lr_train.values, y_clf_train)
        log(f'Ridge (l2_expected) fit on train_n={train_n}, R^2(train)={ridge.score(X_lr_train.values, y_reg_train):.4f}', fh)
        log(f'LogisticRegression (l2_kind==tp) fit on train_n={train_n}, '
            f'accuracy(train)={logit.score(X_lr_train.values, y_clf_train):.4f}', fh)

        ridge_coefs = dict(zip(X_lr_train.columns, ridge.coef_.tolist()))
        logit_coefs = dict(zip(X_lr_train.columns, logit.coef_[0].tolist()))

        # --- monotonic constraints derived from linear coefficient signs ---------
        mono_reg = monotonic_cst_from_coefs(ridge_coefs, list(X_gbm_train.columns))
        mono_clf = monotonic_cst_from_coefs(logit_coefs, list(X_gbm_train.columns))
        log(f'monotonic_cst (regressor, from Ridge signs): '
            + ', '.join(f'{c}={m}' for c, m in zip(X_gbm_train.columns, mono_reg) if m != 0), fh)
        log(f'monotonic_cst (classifier, from Logistic signs): '
            + ', '.join(f'{c}={m}' for c, m in zip(X_gbm_train.columns, mono_clf) if m != 0), fh)

        # --- fit monotone-constrained shallow GBMs --------------------------------
        gbm_reg = HistGradientBoostingRegressor(max_depth=GBM_MAX_DEPTH, max_iter=GBM_MAX_ITER,
                                                 monotonic_cst=mono_reg, random_state=RANDOM_STATE,
                                                 early_stopping=False)
        gbm_reg.fit(X_gbm_train.values, y_reg_train)
        gbm_clf = HistGradientBoostingClassifier(max_depth=GBM_MAX_DEPTH, max_iter=GBM_MAX_ITER,
                                                  monotonic_cst=mono_clf, random_state=RANDOM_STATE,
                                                  early_stopping=False)
        gbm_clf.fit(X_gbm_train.values, y_clf_train)
        log(f'HGB regressor fit: {gbm_reg.n_iter_} iterations used (cap {GBM_MAX_ITER}, depth<={GBM_MAX_DEPTH})', fh)
        log(f'HGB classifier fit: {gbm_clf.n_iter_} iterations used (cap {GBM_MAX_ITER}, depth<={GBM_MAX_DEPTH})', fh)

        # permutation importance (HGB has no native feature_importances_)
        pi_reg = permutation_importance(gbm_reg, X_gbm_train.values, y_reg_train, n_repeats=20,
                                         random_state=RANDOM_STATE)
        pi_clf = permutation_importance(gbm_clf, X_gbm_train.values, y_clf_train, n_repeats=20,
                                         random_state=RANDOM_STATE)

        # --- coefficients / importances table --------------------------------------
        coef_rows = []
        for c in X_lr_train.columns:
            coef_rows.append(dict(feature=c, ridge_coef_l2expected=ridge_coefs[c],
                                   logistic_coef_l2kind_tp=logit_coefs[c]))
        coef_df = pd.DataFrame(coef_rows).sort_values('logistic_coef_l2kind_tp', key=abs, ascending=False)
        coef_path = OUT_DIR / 'linear_coefficients.csv'
        coef_df.to_csv(coef_path, index=False)

        imp_rows = []
        for i, c in enumerate(X_gbm_train.columns):
            imp_rows.append(dict(feature=c,
                                  gbm_reg_perm_importance_mean=float(pi_reg.importances_mean[i]),
                                  gbm_reg_perm_importance_std=float(pi_reg.importances_std[i]),
                                  gbm_reg_monotonic_cst=mono_reg[i],
                                  gbm_clf_perm_importance_mean=float(pi_clf.importances_mean[i]),
                                  gbm_clf_perm_importance_std=float(pi_clf.importances_std[i]),
                                  gbm_clf_monotonic_cst=mono_clf[i]))
        imp_df = pd.DataFrame(imp_rows).sort_values('gbm_clf_perm_importance_mean', ascending=False)
        imp_path = OUT_DIR / 'gbm_importances_monotonicity.csv'
        imp_df.to_csv(imp_path, index=False)
        log(f'coefficients written: {coef_path}; GBM importances/monotonicity written: {imp_path}', fh)

        # --- validation-set predictions for all 4 models --------------------------
        val_pd = val_pd.copy()
        val_pd['score_ridge'] = ridge.predict(X_lr_val.values)
        val_pd['score_gbm_reg'] = gbm_reg.predict(X_gbm_val.values)
        val_pd['score_logit_proba'] = logit.predict_proba(X_lr_val.values)[:, 1]
        val_pd['score_gbm_clf_proba'] = gbm_clf.predict_proba(X_gbm_val.values)[:, 1]

        train_pd = train_pd.copy()
        train_pd['score_logit_proba'] = logit.predict_proba(X_lr_train.values)[:, 1]
        train_pd['score_gbm_clf_proba'] = gbm_clf.predict_proba(X_gbm_train.values)[:, 1]

        # --- success-bar test, all 4 model rankings --------------------------------
        log('SUCCESS-BAR TEST (validation set, identical rows across model vs raw-overall comparators):', fh)
        success_rows = []
        for score_col, name in [('score_ridge', 'ridge_regression'),
                                 ('score_gbm_reg', 'gbm_regressor'),
                                 ('score_logit_proba', 'logistic_regression'),
                                 ('score_gbm_clf_proba', 'gbm_classifier')]:
            success_rows.append(success_bar_test(val_pd, score_col, name, fh))
        success_df = pd.DataFrame(success_rows)
        success_path = OUT_DIR / 'success_bar_results.csv'
        success_df.to_csv(success_path, index=False)
        log(f'success-bar results written: {success_path}', fh)

        # --- calibration curves (classifiers only; both splits, small-N-aware bins) --
        log('CALIBRATION CURVES (n_bins capped by available N):', fh)
        calib_rows = []
        for score_col, name in [('score_logit_proba', 'logistic_regression'),
                                 ('score_gbm_clf_proba', 'gbm_classifier')]:
            calib_rows += calibration_curve(val_pd, score_col, 'y_clf', n_bins=3, model_name=name, split_name='validation')
            calib_rows += calibration_curve(train_pd, score_col, 'y_clf', n_bins=5, model_name=name, split_name='train_in_sample')
        calib_df = pd.DataFrame(calib_rows)
        calib_path = OUT_DIR / 'calibration_curve.csv'
        calib_df.to_csv(calib_path, index=False)
        log(f'calibration curve data written: {calib_path} ({len(calib_df)} bin-rows)', fh)
        for _, r in calib_df[calib_df['split'] == 'validation'].iterrows():
            log(f'  [{r["model"]}/{r["split"]}] bin={r["bin"]} n={r["n"]} '
                f'mean_pred={r["mean_predicted_prob"]:.3f} realized_tp_rate={r["realized_tp_rate"]:.3f}', fh)

        n_candidates = int(success_df['candidate_entry_criterion'].sum())
        log(f'CANDIDATE ENTRY CRITERION earned by {n_candidates}/4 model rankings', fh)

        # --- supplementary standard model diagnostics (train in-sample vs val OOS) --
        y_reg_val = val_pd['l2_expected'].astype(float).values
        y_clf_val = val_pd['y_clf'].astype(int).values
        diagnostics = {
            'ridge_regression': {
                'r2_train': float(r2_score(y_reg_train, ridge.predict(X_lr_train.values))),
                'r2_val': float(r2_score(y_reg_val, val_pd['score_ridge'].values)),
                'mae_train': float(mean_absolute_error(y_reg_train, ridge.predict(X_lr_train.values))),
                'mae_val': float(mean_absolute_error(y_reg_val, val_pd['score_ridge'].values)),
            },
            'gbm_regressor': {
                'r2_train': float(r2_score(y_reg_train, gbm_reg.predict(X_gbm_train.values))),
                'r2_val': float(r2_score(y_reg_val, val_pd['score_gbm_reg'].values)),
                'mae_train': float(mean_absolute_error(y_reg_train, gbm_reg.predict(X_gbm_train.values))),
                'mae_val': float(mean_absolute_error(y_reg_val, val_pd['score_gbm_reg'].values)),
            },
            'logistic_regression': {
                'accuracy_train': float(accuracy_score(y_clf_train, logit.predict(X_lr_train.values))),
                'accuracy_val': float(accuracy_score(y_clf_val, (val_pd['score_logit_proba'].values >= 0.5).astype(int))),
                'majority_class_baseline_train': float(max(y_clf_train.mean(), 1 - y_clf_train.mean())),
                'majority_class_baseline_val': float(max(y_clf_val.mean(), 1 - y_clf_val.mean())),
                'auc_train': _safe_auc(y_clf_train, train_pd['score_logit_proba'].values),
                'auc_val': _safe_auc(y_clf_val, val_pd['score_logit_proba'].values),
                'brier_train': float(brier_score_loss(y_clf_train, train_pd['score_logit_proba'].values)),
                'brier_val': float(brier_score_loss(y_clf_val, val_pd['score_logit_proba'].values)),
            },
            'gbm_classifier': {
                'accuracy_train': float(accuracy_score(y_clf_train, gbm_clf.predict(X_gbm_train.values))),
                'accuracy_val': float(accuracy_score(y_clf_val, (val_pd['score_gbm_clf_proba'].values >= 0.5).astype(int))),
                'majority_class_baseline_train': float(max(y_clf_train.mean(), 1 - y_clf_train.mean())),
                'majority_class_baseline_val': float(max(y_clf_val.mean(), 1 - y_clf_val.mean())),
                'auc_train': _safe_auc(y_clf_train, train_pd['score_gbm_clf_proba'].values),
                'auc_val': _safe_auc(y_clf_val, val_pd['score_gbm_clf_proba'].values),
                'brier_train': float(brier_score_loss(y_clf_train, train_pd['score_gbm_clf_proba'].values)),
                'brier_val': float(brier_score_loss(y_clf_val, val_pd['score_gbm_clf_proba'].values)),
            },
        }
        log('SUPPLEMENTARY MODEL DIAGNOSTICS (train in-sample vs val out-of-sample):', fh)
        for name, d in diagnostics.items():
            log(f'  {name}: {d}', fh)
        diag_path = OUT_DIR / 'model_diagnostics.json'
        with open(diag_path, 'w', encoding='utf-8') as df_:
            json.dump(diagnostics, df_, indent=2)
        log(f'model diagnostics written: {diag_path}', fh)

        wall = time.time() - t0
        meta = dict(
            run_timestamp=time.strftime('%Y-%m-%dT%H:%M:%S'),
            wall_time_sec=round(wall, 2),
            ledger_path=str(LEDGER_PATH),
            n_ledger_rows_total=n_total,
            n_embargo_dropped=n_embargo_dropped,
            n_unripe_dropped=n_unripe,
            cohort_n=cohort_n,
            cohort_n_ct_flag=n_ct,
            cohort_n_trend_le_20=n_trend20,
            cohort_n_both=n_both,
            cohort_or_is_tautology=(n_ct == n_trend20 == n_both == cohort_n),
            train_n=train_n,
            purge_n=int(purge_mask.sum()),
            val_n=val_n,
            val_top_quintile_n=ceil_top_n(val_n),
            effect_bar_pp=EFFECT_BAR * 100,
            gbm_max_depth=GBM_MAX_DEPTH,
            gbm_max_iter_cap=GBM_MAX_ITER,
            gbm_reg_n_iter_used=int(gbm_reg.n_iter_),
            gbm_clf_n_iter_used=int(gbm_clf.n_iter_),
            n_raw_features=len(ALL_RAW_FEATURES),
            n_lr_features_after_encoding=X_lr_train.shape[1],
            n_gbm_features_after_encoding=X_gbm_train.shape[1],
            n_models_earning_candidate_status=n_candidates,
            coefficients_csv=str(coef_path),
            importances_csv=str(imp_path),
            success_bar_csv=str(success_path),
            calibration_csv=str(calib_path),
            model_diagnostics_json=str(diag_path),
        )
        meta_path = OUT_DIR / 'run_meta.json'
        with open(meta_path, 'w', encoding='utf-8') as mf:
            json.dump(meta, mf, indent=2)
        log(f'run meta written: {meta_path}', fh)
        log(f'DONE in {wall:.1f}s', fh)

    return meta, success_df, coef_df, imp_df, calib_df


if __name__ == '__main__':
    main()
