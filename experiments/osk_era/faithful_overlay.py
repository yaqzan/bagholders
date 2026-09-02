"""OSK era-layer -- FAITHFUL-PATH overlay (Phase C recovery block, B4 only).

stage1_sweep.py's 6-variant sweep (already run, see stage1_results.json /
STAGE1_VERDICT.md) applied each candidate lift as a NAIVE flat addition to the
STORED `overall` (adjusted = overall + lift). PROPOSAL.md section 2 places the
real plug-in point earlier in the pipeline -- post-component/volume-amplifier
`weighted_sum`, IMMEDIATELY BEFORE the `pre_regime_overall = clamp(weighted_sum)`
snapshot (database/utils/scoring.py:1259) -- i.e. before the regime multiplier
and the downstream dampener tail (_eff_mult, mis_stress, CAP/EXH/ext-focal,
WCF/CWCF/CWWD/CSWC, PCD/MCD/ICH, SCW, ...) all run on it. This script
reconstructs the ADJUSTED FINAL SCORE that plug-in point implies, WITHOUT
re-running scoring.py (no ScoreSimulator, no MySQL beyond the one queued
sidecar job) -- pure arithmetic on two cached parquets:

    rs_ledger.parquet   (MAIN, read-only) -- symbol/date/overall/pre_regime/
                          pre_boost/t_up/t_dn/fwd_caldays for the
                          component_reweight 60-99 CALL universe (the
                          "stored-score source").
    osk_era_ledger_65.parquet (this worktree, built by build_osk_ledger.py
                          --score-min 65) -- symbol/date/skew/pnl15/ripe
                          sidecar over the SAME window, floored at
                          overall>=65 (5pt margin below 70 = B4's lift cap,
                          so a row that migrates INTO 70+ under the lift is
                          actually present in the joined population instead
                          of being invisible-by-construction).

Reconstruction (per row):
    implied_regime_mult   = overall / pre_regime          -- reverse-engineered
                             net multiplier of every mechanism that runs
                             between the pre_regime snapshot and the final
                             `overall` (regime mult + whatever dampener tail
                             applies to THIS row), without re-deriving each
                             piece separately.
    lift                  = 0.0                              if skew is null (uncovered)
                           = 5 * tanh(z_skew / 1.5)            otherwise (B4: cap=5, scale=1.5)
                             z_skew computed against the POOLED mean/std of
                             skew over the covered (skew non-null) rows of
                             THIS join -- stated explicitly in the report,
                             not reused from stage1_sweep's primary/corroboration
                             panels (different population: 65-floor direct
                             rs_ledger join, not the Polygon discovery panel
                             or the 70-floor corroboration ledger).
    adjusted_pre_regime   = clip(pre_regime + lift, 0, 100)   -- mirrors
                             scoring.py:1259's own clamp, which the real
                             pipeline applies AFTER adding any weighted_sum
                             adjustment and BEFORE the regime multiplier.
    effective_lift        = adjusted_pre_regime - pre_regime  (== lift unless
                             clamping bound; see clamp_hits in the report)
    adjusted_overall      = overall + implied_regime_mult * effective_lift
                           = (pre_regime + effective_lift) * implied_regime_mult
                             [algebraically identical since, by construction,
                             overall == pre_regime * implied_regime_mult]

Rows with pre_regime null or <= 0 are EXCLUDED from the whole computation
(division is undefined) and counted separately.

Known approximation (documented, not corrected): rs_ledger's `pre_regime`
column is already int-truncated at storage time (scoring.py:1259 stores
`int(max(0, min(100, weighted_sum)))`), so `pre_regime + lift` reconstructs
from the POST-truncation integer, not the raw pre-truncation weighted_sum
(which is not stored anywhere). This is a sub-1-point rounding-order effect,
immaterial to bucket-level WR15 aggregates.

RECONSTRUCTION IDENTITY CHECK (must hold exactly): on every row where skew is
null (lift forced to 0.0 exactly), adjusted_overall must equal stored overall
bit-exact. This is asserted, not just eyeballed.

No scoring.py/core.py edits. No ScoreSimulator. No MySQL (the one queued
build_osk_ledger.py --score-min 65 job already ran separately; this script
only reads its parquet output). Overlay/analysis only, per HARD STOPS.

WORKTREE DISCIPLINE: runs only from Trader-exp-osk-era; writes only to this
worktree's experiments/osk_era/.

Usage:
    python experiments/osk_era/faithful_overlay.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_WORKTREE_MARKER = "Trader-exp-osk-era"
assert _WORKTREE_MARKER in _ROOT, (
    f"faithful_overlay.py must run from the {_WORKTREE_MARKER} worktree root, resolved _ROOT={_ROOT}"
)
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

import experiments._holdout as _holdout_mod  # noqa: E402
assert os.path.abspath(_holdout_mod.__file__).lower().startswith(_ROOT.lower()), (
    f"experiments._holdout resolved OUTSIDE the worktree root ({_ROOT}): {_holdout_mod.__file__}"
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

MAIN_ROOT = r"C:\Development\Trader"
RS_LEDGER_PATH = os.path.join(MAIN_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
CACHE_DIR = os.path.join(_ROOT, ".cache", "osk_era")
SIDECAR_PATH = os.path.join(CACHE_DIR, "osk_era_ledger_65.parquet")
SUPPLY_CHECK_PATH = os.path.join(CACHE_DIR, "supply_check.json")

OUT_TXT = os.path.join(_HERE, "faithful_overlay_results.txt")
OUT_JSON = os.path.join(_HERE, "faithful_overlay_results.json")
OUT_MD = os.path.join(_HERE, "FAITHFUL_PATH_RESULTS.md")

WIN_START = "2025-02-01"
WIN_END = "2026-06-15"       # == CALIBRATION_CUTOFF_DATE
SCORE_MIN_POP = 65

MIN_CELL_N = 30       # matches osk_validation_test.py / stage1_sweep.py convention
STAGE1_N_FLOOR = 100  # Stage-1 W-gate N-floor (report-only below this), matches stage1_sweep.py

VARIANTS = {
    "B4": {"id": "B4", "shape": "B", "cap": 5.0, "scale": 1.5},
    "B3": {"id": "B3", "shape": "B", "cap": 3.0, "scale": 1.5},
}

# --- canonical WR15 barrier (assess_scores.py) --------------------------------
# _peak_side(): score >= 50 -> 'low' side -> CALL. assess_scores.py:82-85:
#   SWING_K_LOW  = 2.0   CALL side: target = 2*sigma rise  @ 30d (SWING_REFERENCE_DAYS)
#   SWING_M_LOW  = 5.0   CALL side: stop   = 5*sigma drop  @ 30d
# _swing_walk() scales any other period W (calendar days) by sqrt(W/30) (line 540-542).
# This is the exact "dashboard generic WR15" definition (experiments/component_reweight/
# label.py's own "gen15" label uses the rounded 1.414/3.536 -- recomputed unrounded here).
SWING_K_LOW = 2.0
SWING_M_LOW = 5.0
SWING_REFERENCE_DAYS = 30
W_TARGET = 15
_SCALE_15 = math.sqrt(W_TARGET / SWING_REFERENCE_DAYS)
TP_SIGMA = SWING_K_LOW * _SCALE_15   # 1.41421356...
SL_SIGMA = SWING_M_LOW * _SCALE_15   # 3.53553391...

# Barrier grid -- bit-for-bit identical to experiments/component_reweight/label.py's
# UP_GRID/DN_GRID (reimplemented here, not imported, per this experiment stream's
# convention of not cross-importing sibling one-off scripts -- see stage1_sweep.py's
# own "Stats helpers... reimplemented here" note). t_up/t_dn in rs_ledger.parquet are
# List(Int64) of length 43/93 respectively -- asserted below, not assumed.
UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)
assert len(UP_GRID) == 43, f"UP_GRID length {len(UP_GRID)} != 43 (rs_ledger t_up width)"
assert len(DN_GRID) == 93, f"DN_GRID length {len(DN_GRID)} != 93 (rs_ledger t_dn width)"


def _idx(grid: np.ndarray, target: float) -> int:
    return int(np.clip(int(np.argmin(np.abs(grid - float(target)))), 0, len(grid) - 1))


TP_IDX = _idx(UP_GRID, TP_SIGMA)
SL_IDX = _idx(DN_GRID, SL_SIGMA)
TP_SIGMA_SNAPPED = float(UP_GRID[TP_IDX])
SL_SIGMA_SNAPPED = float(DN_GRID[SL_IDX])


def win_expr(W: int = W_TARGET, tp_idx: int = TP_IDX, sl_idx: int = SL_IDX) -> pl.Expr:
    """label.py's win_expr semantics, Float64 output (None -> NaN on extraction):
    win = 1  if t_up[tp_idx] <= W and t_up[tp_idx] < t_dn[sl_idx]   (TP touched first, in time)
        = 0  if t_dn[sl_idx] <= W and t_dn[sl_idx] <= t_up[tp_idx]  (SL touched first or same bar)
        = 0  if neither touched but fwd_caldays >= W                (day-W expire = no-TP = loss)
        = None if neither touched and fwd_caldays < W               (unresolved; too recent)
    Same-bar tie -> loss (conservative).
    """
    ttp = pl.col("t_up").list.get(tp_idx)
    tsl = pl.col("t_dn").list.get(sl_idx)
    fc = pl.col("fwd_caldays")
    return (
        pl.when((ttp <= W) & (ttp < tsl)).then(pl.lit(1.0))
        .when((tsl <= W) & (tsl <= ttp)).then(pl.lit(0.0))
        .when(fc >= W).then(pl.lit(0.0))
        .otherwise(pl.lit(None))
        .cast(pl.Float64)
    )


OUT_LINES: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)
    OUT_LINES.append(msg + "\n")


def n_ok(n) -> bool:
    return n is not None and n >= MIN_CELL_N


# ---------------------------------------------------------------------------
# Clustered-SE OLS dummy regression (promoted vs demoted gap test) -- exact
# reimplementation of stage1_sweep.py's ols_clustered_dummy/promoted_vs_demoted_test,
# generalized to any outcome array (win15 here; pnl15 for the secondary check).
# ---------------------------------------------------------------------------
def ols_clustered_dummy(y: np.ndarray, dummy: np.ndarray, groups: np.ndarray):
    n = len(y)
    if n < 2:
        return None
    Xc = np.column_stack([np.ones(n), dummy.astype(np.float64)])
    k = Xc.shape[1]
    XtX = Xc.T @ Xc
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)
    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta
    uniq = np.unique(groups)
    G = uniq.shape[0]
    meat = np.zeros((k, k))
    for g in uniq:
        m = groups == g
        Xg = Xc[m]
        eg = resid[m]
        s = Xg.T @ eg
        meat += np.outer(s, s)
    small = (G / max(G - 1, 1)) if G > 1 else 1.0
    cov = small * (XtX_inv @ meat @ XtX_inv)
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = beta / se
    return {"beta": float(beta[1]), "se_clust": float(se[1]), "t_clust": float(t[1]),
            "n": int(n), "g": int(G)}


def gap_test(y_promoted: np.ndarray, dates_promoted: np.ndarray,
             y_demoted: np.ndarray, dates_demoted: np.ndarray) -> dict:
    n_p, n_d = len(y_promoted), len(y_demoted)
    if not (n_ok(n_p) and n_ok(n_d)):
        return {"n_promoted": n_p, "n_demoted": n_d, "skipped": True,
                "reason": "both cohorts need N>=%d (got %d/%d)" % (MIN_CELL_N, n_p, n_d)}
    y = np.concatenate([y_promoted, y_demoted])
    dummy = np.concatenate([np.ones(n_p), np.zeros(n_d)])
    groups = np.concatenate([dates_promoted, dates_demoted])
    res = ols_clustered_dummy(y, dummy, groups)
    if res is None:
        return {"n_promoted": n_p, "n_demoted": n_d, "skipped": True, "reason": "degenerate OLS"}
    res["n_promoted"] = n_p
    res["n_demoted"] = n_d
    res["skipped"] = False
    res["gap"] = res["beta"]
    return res


def compute_lift(skew: np.ndarray, variant: dict) -> tuple:
    valid = ~np.isnan(skew)
    lift = np.zeros_like(skew)
    ref_meta = {"n_ref": int(valid.sum())}
    if valid.sum() < 2:
        ref_meta["degenerate"] = True
        return lift, ref_meta
    sv = skew[valid]
    mu = float(np.mean(sv))
    sd = float(np.std(sv, ddof=1))
    if sd < 1e-12:
        ref_meta["degenerate"] = True
        return lift, ref_meta
    z = (sv - mu) / sd
    lift[valid] = variant["cap"] * np.tanh(z / variant["scale"])
    ref_meta.update({"kind": "tanh_z", "pooled_mean_skew": mu, "pooled_std_skew": sd, "degenerate": False})
    return lift, ref_meta


# ---------------------------------------------------------------------------
# Bucket definitions
# ---------------------------------------------------------------------------
DISCRETE_BUCKETS = [(70.0, 75.0, "70-74"), (75.0, 80.0, "75-79"), (80.0, None, "80+")]
CUMULATIVE_BUCKETS = [(70.0, None, "70+ cum"), (75.0, None, "75+ cum"), (80.0, None, "80+ cum")]


def bucket_mask(score: np.ndarray, lo: float, hi) -> np.ndarray:
    m = score >= lo
    if hi is not None:
        m = m & (score < hi)
    return m


def nanmean_safe(x: np.ndarray) -> dict:
    valid = ~np.isnan(x)
    n = int(valid.sum())
    n_unresolved = int((~valid).sum())
    if n == 0:
        return {"n": 0, "n_unresolved": n_unresolved, "wr15": None}
    return {"n": n, "n_unresolved": n_unresolved, "wr15": float(np.mean(x[valid]))}


def evaluate_bucket(label, lo, hi, overall, adjusted_overall, win15, pnl15, dates):
    base_mask = bucket_mask(overall, lo, hi)
    adj_mask = bucket_mask(adjusted_overall, lo, hi)
    promoted_mask = (~base_mask) & adj_mask
    demoted_mask = base_mask & (~adj_mask)
    incumbent_mask = base_mask & adj_mask

    base_stat = nanmean_safe(win15[base_mask])
    adj_stat = nanmean_safe(win15[adj_mask])
    delta = (adj_stat["wr15"] - base_stat["wr15"]) if (base_stat["wr15"] is not None and adj_stat["wr15"] is not None) else None

    win_p = win15[promoted_mask]; dates_p = dates[promoted_mask]
    win_d = win15[demoted_mask]; dates_d = dates[demoted_mask]
    vp = ~np.isnan(win_p); vd = ~np.isnan(win_d)
    gap_win15 = gap_test(win_p[vp], dates_p[vp], win_d[vd], dates_d[vd])

    pnl_p = pnl15[promoted_mask]; pnl_d = pnl15[demoted_mask]
    vp2 = ~np.isnan(pnl_p); vd2 = ~np.isnan(pnl_d)
    gap_pnl15 = gap_test(pnl_p[vp2], dates_p[vp2], pnl_d[vd2], dates_d[vd2])

    return {
        "label": label, "lo": lo, "hi": hi,
        "n_baseline": int(base_mask.sum()), "n_adjusted": int(adj_mask.sum()),
        "wr15_baseline": base_stat["wr15"], "wr15_baseline_n": base_stat["n"],
        "wr15_baseline_n_unresolved": base_stat["n_unresolved"],
        "wr15_adjusted": adj_stat["wr15"], "wr15_adjusted_n": adj_stat["n"],
        "wr15_adjusted_n_unresolved": adj_stat["n_unresolved"],
        "wr15_delta": delta,
        "promoted_n": int(promoted_mask.sum()), "demoted_n": int(demoted_mask.sum()),
        "incumbent_n": int(incumbent_mask.sum()),
        "net_migration": int(promoted_mask.sum()) - int(demoted_mask.sum()),
        "thin_report_only": int(base_mask.sum()) < STAGE1_N_FLOOR,
        "gap_win15_promoted_vs_demoted": gap_win15,
        "gap_pnl15_promoted_vs_demoted": gap_pnl15,
        "_masks": (base_mask, adj_mask, promoted_mask, demoted_mask),
    }


def run_variant(variant: dict, overall, pre_regime, skew, win15, pnl15, dates) -> dict:
    lift, ref_meta = compute_lift(skew, variant)

    implied_regime_mult = overall / pre_regime
    raw_adjusted_pre_regime = pre_regime + lift
    adjusted_pre_regime = np.clip(raw_adjusted_pre_regime, 0.0, 100.0)
    effective_lift = adjusted_pre_regime - pre_regime
    adjusted_overall = overall + implied_regime_mult * effective_lift

    clamp_hits = int(np.sum(np.abs(effective_lift - lift) > 1e-12))

    uncovered = np.isnan(skew)
    n_uncovered = int(uncovered.sum())
    mismatch = adjusted_overall[uncovered] != overall[uncovered]
    n_mismatch = int(mismatch.sum())
    identity_ok = (n_mismatch == 0)

    mult_desc = {
        "min": float(np.min(implied_regime_mult)), "max": float(np.max(implied_regime_mult)),
        "mean": float(np.mean(implied_regime_mult)), "median": float(np.median(implied_regime_mult)),
        "n_outside_0.5_1.5": int(np.sum((implied_regime_mult < 0.5) | (implied_regime_mult > 1.5))),
        "n_nonfinite": int(np.sum(~np.isfinite(implied_regime_mult))),
    }

    buckets = {}
    for lo, hi, label in DISCRETE_BUCKETS:
        buckets[label] = evaluate_bucket(label, lo, hi, overall, adjusted_overall, win15, pnl15, dates)
    for lo, hi, label in CUMULATIVE_BUCKETS:
        buckets[label] = evaluate_bucket(label, lo, hi, overall, adjusted_overall, win15, pnl15, dates)

    return {
        "variant": variant["id"],
        "lift_ref_meta": ref_meta,
        "n_uncovered": n_uncovered,
        "reconstruction_identity_ok": identity_ok,
        "reconstruction_identity_n_checked": n_uncovered,
        "reconstruction_identity_n_mismatch": n_mismatch,
        "clamp_hits": clamp_hits,
        "implied_regime_mult": mult_desc,
        "buckets": buckets,
        "_adjusted_overall": adjusted_overall,
        "_lift": lift,
        "_skew_valid": ~np.isnan(skew),
    }


def blended_75(variant_result: dict, overall, adjusted_overall_arr, skew_valid, win15, pnl15,
                coverage_fraction_75: float, n_75_full_stream: int) -> dict:
    """Mirrors stage1_sweep.py's blended_full_stream(): migration measured on the
    skew-valid (chain-hit-strict) subset, expressed as a %% of the FULL-STREAM 75+
    population; quality/WR15 means computed UNRESTRICTED by skew-validity (an
    uncovered row has adjusted==overall so it is an inert incumbent in both)."""
    pre_75_strict = (overall >= 75.0) & skew_valid
    post_75_strict = (adjusted_overall_arr >= 75.0) & skew_valid
    pre_75_n = int(pre_75_strict.sum())
    promoted_n = int(((~pre_75_strict) & post_75_strict & (overall < 75.0)).sum())
    demoted_n = int((pre_75_strict & (~post_75_strict)).sum())
    net = promoted_n - demoted_n
    supply_delta_within_chainhit = (100.0 * net / pre_75_n) if pre_75_n else None
    blended_supply_delta_full_stream = (100.0 * net / n_75_full_stream) if n_75_full_stream else None

    pre_75_all = overall >= 75.0
    post_75_all = adjusted_overall_arr >= 75.0

    def wr15_of(mask):
        x = win15[mask]
        v = ~np.isnan(x)
        return (float(np.mean(x[v])), int(v.sum())) if v.sum() else (None, 0)

    def pnl_of(mask):
        x = pnl15[mask]
        v = ~np.isnan(x)
        return (float(np.mean(x[v])), int(v.sum())) if v.sum() else (None, 0)

    wr_pre, n_wr_pre = wr15_of(pre_75_all)
    wr_post, n_wr_post = wr15_of(post_75_all)
    wr15_shift = (wr_post - wr_pre) if (wr_pre is not None and wr_post is not None) else None
    blended_wr15_shift = coverage_fraction_75 * wr15_shift if wr15_shift is not None else None

    pnl_pre, n_pnl_pre = pnl_of(pre_75_all)
    pnl_post, n_pnl_post = pnl_of(post_75_all)
    pnl15_shift = (pnl_post - pnl_pre) if (pnl_pre is not None and pnl_post is not None) else None
    blended_pnl15_shift = coverage_fraction_75 * pnl15_shift if pnl15_shift is not None else None

    return {
        "coverage_fraction_75_used": coverage_fraction_75,
        "n_75_full_stream": n_75_full_stream,
        "pre_75_chainhit_strict_n": pre_75_n,
        "promoted_n_chainhit": promoted_n, "demoted_n_chainhit": demoted_n, "net_delta_n_chainhit": net,
        "supply_delta_pct_within_chainhit": supply_delta_within_chainhit,
        "blended_supply_delta_pct_full_stream": blended_supply_delta_full_stream,
        "wr15_pre_75_all": wr_pre, "wr15_pre_75_all_n": n_wr_pre,
        "wr15_post_75_all": wr_post, "wr15_post_75_all_n": n_wr_post,
        "wr15_shift_within_covered": wr15_shift,
        "blended_wr15_delta_full_stream": blended_wr15_shift,
        "pnl15_pre_75_all": pnl_pre, "pnl15_post_75_all": pnl_post,
        "pnl15_shift_within_covered": pnl15_shift,
        "blended_pnl15_delta_full_stream": blended_pnl15_shift,
    }


def fmt(x, spec="%.4f"):
    return (spec % x) if x is not None else "n/a"


def main() -> None:
    t0 = time.time()
    log("=== OSK era-layer FAITHFUL-PATH overlay (B4, post-hoc pre-regime reconstruction) ===")
    log("holdout cutoff: %s" % HOLDOUT_CUTOFF.isoformat())
    log("TP_SIGMA(exact)=%.6f -> grid-snapped idx=%d val=%.2f | SL_SIGMA(exact)=%.6f -> grid-snapped idx=%d val=%.2f  (W=%d cal days)" % (
        TP_SIGMA, TP_IDX, TP_SIGMA_SNAPPED, SL_SIGMA, SL_IDX, SL_SIGMA_SNAPPED, W_TARGET))
    log("K/M quoted from assess_scores.py: SWING_K_LOW=%.1f (CALL target, +sigma rise) / "
        "SWING_M_LOW=%.1f (CALL stop, -sigma drop) @ SWING_REFERENCE_DAYS=%d, scaled sqrt(%d/%d) to W=%d"
        % (SWING_K_LOW, SWING_M_LOW, SWING_REFERENCE_DAYS, W_TARGET, SWING_REFERENCE_DAYS, W_TARGET))
    log("")

    # --- Load rs_ledger (MAIN, read-only), filter window + score, holdout guard ---
    rs = pl.read_parquet(RS_LEDGER_PATH)
    pop_all = rs.filter(
        (pl.col("date") >= WIN_START) & (pl.col("date") <= WIN_END) & (pl.col("overall") >= SCORE_MIN_POP)
    )
    assert_no_holdout_leak(pop_all, context="osk_faithful")

    # t_up/t_dn width sanity (defensive; grids sized to 43/93 above)
    up_lens = pop_all["t_up"].list.len().unique().to_list()
    dn_lens = pop_all["t_dn"].list.len().unique().to_list()
    assert up_lens == [43], f"t_up widths in population: {up_lens} (expected [43])"
    assert dn_lens == [93], f"t_dn widths in population: {dn_lens} (expected [93])"

    # --- Compute win15 in polars BEFORE dropping to numpy (barrier outcome is
    #     independent of score/skew/lift -- pure price-path fact per row) ---
    pop_all = pop_all.with_columns(win_expr().alias("win15"))

    n_total_65 = pop_all.height
    excluded_mask_pl = pop_all["pre_regime"].is_null() | (pop_all["pre_regime"] <= 0)
    n_excluded = int(excluded_mask_pl.sum())
    pop = pop_all.filter(~excluded_mask_pl)
    log("population: rs_ledger overall>=%d, %s..%s -> N=%d total; excluded (pre_regime null/<=0)=%d; analysis N=%d" % (
        SCORE_MIN_POP, WIN_START, WIN_END, n_total_65, n_excluded, pop.height))
    log("  cross-check: overall>=70 in pop = %d, >=75 = %d, >=80 = %d" % (
        pop.filter(pl.col("overall") >= 70).height, pop.filter(pl.col("overall") >= 75).height,
        pop.filter(pl.col("overall") >= 80).height))
    log("")

    # --- Join sidecar skew/pnl15/ripe onto the full population (LEFT; uncovered -> null) ---
    sidecar = pl.read_parquet(SIDECAR_PATH).select(["symbol", "date", "skew", "pnl15", "ripe"])
    pop_j = pop.join(sidecar, on=["symbol", "date"], how="left")
    assert pop_j.height == pop.height, "join changed row count (dedup issue in sidecar?)"
    n_covered = int(pop_j["skew"].drop_nulls().len())
    log("joined to 65-sidecar (osk_era_ledger_65.parquet): N=%d, skew covered (chain-hit)=%d (%.1f%%)" % (
        pop_j.height, n_covered, 100.0 * n_covered / pop_j.height))
    log("")

    overall = pop_j["overall"].to_numpy().astype(np.float64)
    pre_regime = pop_j["pre_regime"].to_numpy().astype(np.float64)
    skew = pop_j["skew"].to_numpy().astype(np.float64)
    win15 = pop_j["win15"].to_numpy().astype(np.float64)
    pnl15 = pop_j["pnl15"].to_numpy().astype(np.float64)
    dates = pop_j["date"].to_numpy()

    with open(SUPPLY_CHECK_PATH, "r", encoding="utf-8") as f:
        supply_check = json.load(f)
    cov75 = supply_check["ground_truth_crosscheck"]["aggregates_70_75"]["ALL 75+"]["chain_hit_pct"] / 100.0
    n75_full_stream_supplycheck = supply_check["ground_truth_crosscheck"]["aggregates_70_75"]["ALL 75+"]["n_total"]
    n75_full_stream_measured = int((overall >= 75.0).sum())
    log("supply_check.json coverage_fraction_75 (chain-hit, ground-truth) = %.3f  "
        "(n_75_full_stream: supply_check=%d, measured-here=%d)" % (
            cov75, n75_full_stream_supplycheck, n75_full_stream_measured))
    log("")

    results = {"meta": {
        "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
        "window": [WIN_START, WIN_END], "score_min_pop": SCORE_MIN_POP,
        "n_total_65plus": n_total_65, "n_excluded_pre_regime": n_excluded, "n_analysis": pop.height,
        "n_covered_skew": n_covered,
        "tp_sigma_exact": TP_SIGMA, "sl_sigma_exact": SL_SIGMA,
        "tp_sigma_snapped": TP_SIGMA_SNAPPED, "sl_sigma_snapped": SL_SIGMA_SNAPPED,
        "tp_idx": TP_IDX, "sl_idx": SL_IDX, "w_target": W_TARGET,
        "coverage_fraction_75": cov75, "n_75_full_stream": n75_full_stream_measured,
    }, "variants": {}}

    def report_variant(vid: str) -> dict:
        variant = VARIANTS[vid]
        log("=" * 100)
        log("VARIANT %s  shape=%s cap=%s scale=%s" % (vid, variant["shape"], variant["cap"], variant["scale"]))
        log("=" * 100)
        vr = run_variant(variant, overall, pre_regime, skew, win15, pnl15, dates)
        log("lift ref: n_ref(covered)=%d pooled_mean_skew=%s pooled_std_skew=%s degenerate=%s" % (
            vr["lift_ref_meta"]["n_ref"],
            fmt(vr["lift_ref_meta"].get("pooled_mean_skew"), "%.4f"),
            fmt(vr["lift_ref_meta"].get("pooled_std_skew"), "%.4f"),
            vr["lift_ref_meta"].get("degenerate")))
        log("RECONSTRUCTION IDENTITY CHECK: uncovered N=%d, mismatches=%d -> %s" % (
            vr["n_uncovered"], vr["reconstruction_identity_n_mismatch"],
            "PASS (bit-exact)" if vr["reconstruction_identity_ok"] else "FAIL"))
        log("clamp_hits (pre_regime+lift outside [0,100]): %d" % vr["clamp_hits"])
        m = vr["implied_regime_mult"]
        log("implied_regime_mult: min=%.3f max=%.3f mean=%.3f median=%.3f  outside[0.5,1.5]=%d  nonfinite=%d" % (
            m["min"], m["max"], m["mean"], m["median"], m["n_outside_0.5_1.5"], m["n_nonfinite"]))
        log("")
        log("%-8s | %10s %10s | %9s %9s %8s | %9s %9s %8s | %s" % (
            "bucket", "N_base", "N_adj", "WR15base", "WR15adj", "delta",
            "promo", "demot", "net", "thin?"))
        for lo, hi, label in DISCRETE_BUCKETS + CUMULATIVE_BUCKETS:
            b = vr["buckets"][label]
            log("%-8s | %10d %10d | %9s %9s %8s | %9d %9d %+8d | %s" % (
                label, b["n_baseline"], b["n_adjusted"],
                "n/a" if b["wr15_baseline"] is None else "%.1f%%" % (100 * b["wr15_baseline"]),
                "n/a" if b["wr15_adjusted"] is None else "%.1f%%" % (100 * b["wr15_adjusted"]),
                "n/a" if b["wr15_delta"] is None else "%+.2fpp" % (100 * b["wr15_delta"]),
                b["promoted_n"], b["demoted_n"], b["net_migration"],
                "THIN" if b["thin_report_only"] else "-"))
        log("")
        for lo, hi, label in DISCRETE_BUCKETS + CUMULATIVE_BUCKETS:
            b = vr["buckets"][label]
            g = b["gap_win15_promoted_vs_demoted"]
            if g.get("skipped"):
                log("  [%s gap WR15 promoted-vs-demoted] %s" % (label, g.get("reason")))
            else:
                log("  [%s gap WR15 promoted-vs-demoted] gap=%+.4f t_clust=%.2f (N_p=%d N_d=%d G=%d dates)" % (
                    label, g["gap"], g["t_clust"], g["n_promoted"], g["n_demoted"], g["g"]))
            gp = b["gap_pnl15_promoted_vs_demoted"]
            if gp.get("skipped"):
                log("  [%s gap pnl15(option, secondary) promoted-vs-demoted] %s" % (label, gp.get("reason")))
            else:
                log("  [%s gap pnl15(option, secondary) promoted-vs-demoted] gap=%+.4f t_clust=%.2f (N_p=%d N_d=%d G=%d dates)" % (
                    label, gp["gap"], gp["t_clust"], gp["n_promoted"], gp["n_demoted"], gp["g"]))
        log("")

        blended = blended_75(vr, overall, vr["_adjusted_overall"], vr["_skew_valid"], win15, pnl15, cov75, n75_full_stream_measured)
        log("--- BLENDED FULL-STREAM (75+, coverage=%.1f%%) ---" % (cov75 * 100))
        log("  chain-hit(strict) pre_75_n=%d  within-chainhit promoted=%d demoted=%d net=%+d (%s%% of chainhit-75 pop)" % (
            blended["pre_75_chainhit_strict_n"], blended["promoted_n_chainhit"], blended["demoted_n_chainhit"],
            blended["net_delta_n_chainhit"], fmt(blended["supply_delta_pct_within_chainhit"], "%+.1f")))
        log("  BLENDED 75+ supply delta (full stream N=%d): %s%%" % (
            blended["n_75_full_stream"], fmt(blended["blended_supply_delta_pct_full_stream"], "%+.2f")))
        log("  WR15: pre=%s (N=%d) post=%s (N=%d) shift=%s -> BLENDED WR15 delta (full stream): %s" % (
            fmt(blended["wr15_pre_75_all"], "%.4f"), blended["wr15_pre_75_all_n"],
            fmt(blended["wr15_post_75_all"], "%.4f"), blended["wr15_post_75_all_n"],
            fmt(blended["wr15_shift_within_covered"], "%+.4f"), fmt(blended["blended_wr15_delta_full_stream"], "%+.4f")))
        log("  pnl15(option,secondary): pre=%s post=%s shift=%s -> BLENDED pnl15 delta: %s" % (
            fmt(blended["pnl15_pre_75_all"]), fmt(blended["pnl15_post_75_all"]),
            fmt(blended["pnl15_shift_within_covered"]), fmt(blended["blended_pnl15_delta_full_stream"])))
        log("")

        vr_out = {k: v for k, v in vr.items() if not k.startswith("_")}
        for label in vr_out["buckets"]:
            vr_out["buckets"][label] = {k: v for k, v in vr_out["buckets"][label].items() if not k.startswith("_")}
        vr_out["blended_75"] = blended
        return vr_out

    b4_out = report_variant("B4")
    results["variants"]["B4"] = b4_out

    # B3 fallback ONLY if B4's WR15 evidence is negative (blended WR15 delta < 0
    # AND cumulative 75+ WR15 delta < 0) -- per HARD STOPS: no variants beyond B4
    # (+conditional B3).
    b4_wr15_blended = b4_out["blended_75"]["blended_wr15_delta_full_stream"]
    b4_wr15_75cum = b4_out["buckets"]["75+ cum"]["wr15_delta"]
    b4_negative = (b4_wr15_blended is not None and b4_wr15_blended < 0) and \
                  (b4_wr15_75cum is not None and b4_wr15_75cum < 0)
    log("=" * 100)
    log("B4 WR15 evidence negative (blended=%s, 75+cum=%s) -> %s" % (
        fmt(b4_wr15_blended, "%+.4f"), fmt(b4_wr15_75cum, "%+.4f"),
        "RUNNING B3 FALLBACK" if b4_negative else "B4 evidence is non-negative -- B3 fallback SKIPPED"))
    log("=" * 100)
    if b4_negative:
        b3_out = report_variant("B3")
        results["variants"]["B3"] = b3_out
    else:
        results["variants"]["B3"] = None

    elapsed = time.time() - t0
    log("\n=== DONE in %.1fs ===" % elapsed)
    results["meta"]["elapsed_s"] = round(elapsed, 2)

    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(OUT_LINES)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump(results, f, indent=2, default=str)
    log("wrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)


if __name__ == "__main__":
    main()
