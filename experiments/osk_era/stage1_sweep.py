"""OSK era-layer score modifier -- Stage-1 evidence sweep (6-variant FIXED grid).

Per PROPOSAL.md (this worktree, algo-exp/osk-era-layer): Shapes A (rank lift)
and B (tanh on skew z-score) are approved for evidence testing; Shape C is
deferred. This script computes the Stage-1 currency (tier migration, cohort
quality, bucket enrichment, blended full-stream view) for all 6 fixed
variants on TWO panels:

  PRIMARY (discovery)  -- MAIN's Polygon iv_ledger_polygon.parquet, discovery
                          window 2025-02-11..2026-05-15, reused via
                          _build_primary_panel_export.py's cached parquet
                          output (that script called MAIN's
                          osk_validation_phase2.build_discovery_panel by
                          absolute-path import, in an ISOLATED one-shot
                          process -- see its docstring). This script itself
                          NEVER imports MAIN code; it only reads the parquet
                          that script already produced under THIS worktree's
                          .cache/osk_era/.
  CORROBORATION        -- this worktree's own osk_era_ledger.parquet (our
                          exact production recipe), restricted to ripe==True
                          rows (N~730; pnl15 only defined there).

No scoring.py/core.py edits. No ScoreSimulator. No MySQL (everything here
is a parquet/json read). No queue submission (this is a light, seconds-scale
foreground computation over cached parquets).

WORKTREE DISCIPLINE: runs only from Trader-exp-osk-era; writes only to this
worktree's experiments/osk_era/ and .cache/osk_era/.

Usage:
    python experiments/osk_era/stage1_sweep.py
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
    f"stage1_sweep.py must run from the {_WORKTREE_MARKER} worktree root, resolved _ROOT={_ROOT}"
)
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from scipy.stats import rankdata  # noqa: E402

import experiments._holdout as _holdout_mod  # noqa: E402
assert os.path.abspath(_holdout_mod.__file__).lower().startswith(_ROOT.lower()), (
    f"experiments._holdout resolved OUTSIDE the worktree root ({_ROOT}): {_holdout_mod.__file__}"
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

MAIN_ROOT = r"C:\Development\Trader"
CACHE_DIR = os.path.join(_ROOT, ".cache", "osk_era")
PRIMARY_PANEL_PATH = os.path.join(CACHE_DIR, "discovery_panel_primary.parquet")
CORR_LEDGER_PATH = os.path.join(CACHE_DIR, "osk_era_ledger.parquet")
SUPPLY_CHECK_PATH = os.path.join(CACHE_DIR, "supply_check.json")
MAIN_RS_LEDGER = os.path.join(MAIN_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")

OUT_TXT = os.path.join(_HERE, "stage1_results.txt")
OUT_JSON = os.path.join(_HERE, "stage1_results.json")

MIN_CELL_N = 30  # matches osk_validation_test.py's MIN_CELL_N convention throughout this stream

VARIANTS = [
    {"id": "A1", "shape": "A", "cap": 3.0, "scale": None},
    {"id": "A2", "shape": "A", "cap": 5.0, "scale": None},
    {"id": "B1", "shape": "B", "cap": 3.0, "scale": 1.0},
    {"id": "B2", "shape": "B", "cap": 5.0, "scale": 1.0},
    {"id": "B3", "shape": "B", "cap": 3.0, "scale": 1.5},
    {"id": "B4", "shape": "B", "cap": 5.0, "scale": 1.5},
]

OUT_LINES: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)
    OUT_LINES.append(msg + "\n")


def n_ok(n) -> bool:
    return n is not None and n >= MIN_CELL_N


def skip_str(n) -> str:
    return "SKIPPED (N=%d < %d)" % (0 if n is None else n, MIN_CELL_N)


# ---------------------------------------------------------------------------
# Stats helpers (self-contained; the clustered-SE OLS formula is the SAME
# house convention already used by experiments/gex/gex_test.py and
# experiments/osk_validation/osk_validation_test.py -- reimplemented here,
# not imported, so this script never touches MAIN's code).
# ---------------------------------------------------------------------------
def rank_pct_of(x: np.ndarray) -> np.ndarray:
    """Average-rank percentile in [0,1], ties averaged."""
    n = len(x)
    if n <= 1:
        return np.full(n, 0.5)
    r = rankdata(x, method="average")
    return (r - 1.0) / (n - 1.0)


def zscore_of(x: np.ndarray) -> tuple:
    mu = float(np.mean(x))
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    if sd < 1e-12:
        return np.zeros_like(x), mu, sd
    return (x - mu) / sd, mu, sd


def ols_clustered_dummy(y: np.ndarray, dummy: np.ndarray, groups: np.ndarray):
    """Single-regressor (binary dummy) OLS with date-clustered sandwich SE.
    beta[1] == mean(y|dummy=1) - mean(y|dummy=0) exactly (no other regressor).
    Returns dict(beta, se_clust, t_clust, n, g) or None if degenerate."""
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


def cohort_stat(pnl: np.ndarray) -> dict:
    n = len(pnl)
    if n == 0:
        return {"n": 0, "mean_pnl15": None, "wr": None}
    return {"n": int(n), "mean_pnl15": float(np.mean(pnl)), "wr": float(np.mean(pnl > 0))}


def promoted_vs_demoted_test(pnl_p: np.ndarray, dates_p: np.ndarray,
                              pnl_d: np.ndarray, dates_d: np.ndarray) -> dict:
    n_p, n_d = len(pnl_p), len(pnl_d)
    if not (n_ok(n_p) and n_ok(n_d)):
        return {"n_promoted": n_p, "n_demoted": n_d, "skipped": True,
                "reason": "both cohorts need N>=%d ripe/priced rows (got %d/%d)" % (MIN_CELL_N, n_p, n_d)}
    y = np.concatenate([pnl_p, pnl_d])
    dummy = np.concatenate([np.ones(n_p), np.zeros(n_d)])
    groups = np.concatenate([dates_p, dates_d])
    res = ols_clustered_dummy(y, dummy, groups)
    if res is None:
        return {"n_promoted": n_p, "n_demoted": n_d, "skipped": True, "reason": "degenerate OLS"}
    res["n_promoted"] = n_p
    res["n_demoted"] = n_d
    res["skipped"] = False
    res["gap_mean_pnl15"] = res["beta"]  # promoted - demoted, raw pnl15 units
    return res


# ---------------------------------------------------------------------------
# Lift application
# ---------------------------------------------------------------------------
def compute_lift(skew: np.ndarray, variant: dict) -> tuple:
    """Returns (lift array [0 where skew is NaN], ref_meta dict)."""
    valid = ~np.isnan(skew)
    lift = np.zeros_like(skew)
    ref_meta = {"n_ref": int(valid.sum())}
    if valid.sum() < 2:
        ref_meta["degenerate"] = True
        return lift, ref_meta
    sv = skew[valid]
    if variant["shape"] == "A":
        rp = rank_pct_of(sv)
        lift[valid] = variant["cap"] * (2.0 * rp - 1.0)
        ref_meta["kind"] = "rank_pct"
    else:
        z, mu, sd = zscore_of(sv)
        lift[valid] = variant["cap"] * np.tanh(z / variant["scale"])
        ref_meta["kind"] = "tanh_z"
        ref_meta["pooled_mean_skew"] = mu
        ref_meta["pooled_std_skew"] = sd
    ref_meta["degenerate"] = False
    return lift, ref_meta


# ---------------------------------------------------------------------------
# Panel loading
# ---------------------------------------------------------------------------
def load_primary() -> pl.DataFrame:
    if not os.path.exists(PRIMARY_PANEL_PATH):
        raise SystemExit(
            "Missing %s -- run experiments/osk_era/_build_primary_panel_export.py first "
            "(isolated one-shot MAIN-context process)." % PRIMARY_PANEL_PATH
        )
    df = pl.read_parquet(PRIMARY_PANEL_PATH)
    assert_no_holdout_leak(df, "osk_stage1_primary")
    return df


def load_corroboration_raw() -> pl.DataFrame:
    df = pl.read_parquet(CORR_LEDGER_PATH)
    assert_no_holdout_leak(df, "osk_stage1_corroboration_raw")
    return df


def load_mfe15_map() -> pl.DataFrame:
    rs = pl.read_parquet(MAIN_RS_LEDGER).select(["symbol", "date", "mfe15"])
    return rs


# ---------------------------------------------------------------------------
# Core per-panel, per-variant evidence computation
# ---------------------------------------------------------------------------
def evidence_for_variant(df: pl.DataFrame, variant: dict, panel_label: str,
                          require_pnl: bool = True) -> dict:
    """df must have columns: symbol, date, overall, skew, pnl15, mfe15 (mfe15
    may be all-null if join failed for a row -- handled via nan-aware ops)."""
    overall = df["overall"].to_numpy().astype(np.float64)
    skew = df["skew"].to_numpy().astype(np.float64)  # polars null -> nan for Float64
    pnl15 = df["pnl15"].to_numpy().astype(np.float64)
    mfe15 = df["mfe15"].to_numpy().astype(np.float64) if "mfe15" in df.columns else np.full(len(df), np.nan)
    dates = df["date"].to_numpy()
    n_total = len(df)

    lift, ref_meta = compute_lift(skew, variant)
    adjusted = overall + lift

    out = {"variant": variant["id"], "panel": panel_label, "n_population": n_total,
           "n_skew_valid": ref_meta["n_ref"], "ref_meta": ref_meta}

    def migration_block(thresh: float) -> dict:
        pre_mask = overall >= thresh
        post_mask = adjusted >= thresh
        pre_n = int(pre_mask.sum())
        promoted_mask = (~pre_mask) & post_mask
        demoted_mask = pre_mask & (~post_mask)
        incumbent_mask = pre_mask & post_mask
        promoted_n = int(promoted_mask.sum())
        demoted_n = int(demoted_mask.sum())
        block = {
            "threshold": thresh, "pre_n": pre_n,
            "promoted_n": promoted_n, "demoted_n": demoted_n,
            "net_delta_n": promoted_n - demoted_n,
            "promoted_pct_of_pre": (100.0 * promoted_n / pre_n) if pre_n else None,
            "demoted_pct_of_pre": (100.0 * demoted_n / pre_n) if pre_n else None,
            "net_delta_pct_of_pre": (100.0 * (promoted_n - demoted_n) / pre_n) if pre_n else None,
            "thin_report_only": pre_n < 100,  # Stage-1 W-gate N-floor context (100/300/500), separate from MIN_CELL_N=30 reporting floor
        }
        return block, promoted_mask, demoted_mask, incumbent_mask

    mig75, promo75, demo75, incu75 = migration_block(75.0)
    mig80, promo80, demo80, incu80 = migration_block(80.0)
    out["migration_75"] = mig75
    out["migration_80"] = mig80

    def quality_cohort(mask: np.ndarray) -> dict:
        pnl = pnl15[mask]
        valid = ~np.isnan(pnl)
        pnl_v = pnl[valid]
        base = cohort_stat(pnl_v)
        mfe = mfe15[mask]
        mfe_valid = ~np.isnan(mfe)
        base["n_mfe15"] = int(mfe_valid.sum())
        base["mean_mfe15"] = float(np.mean(mfe[mfe_valid])) if mfe_valid.sum() else None
        return base

    def cohort_quality_at(promo_mask, demo_mask, incu_mask, thresh_label) -> dict:
        promoted_q = quality_cohort(promo_mask)
        demoted_q = quality_cohort(demo_mask)
        incumbent_q = quality_cohort(incu_mask)

        pnl_valid_p = ~np.isnan(pnl15) & promo_mask
        pnl_valid_d = ~np.isnan(pnl15) & demo_mask
        gap = promoted_vs_demoted_test(
            pnl15[pnl_valid_p], dates[pnl_valid_p],
            pnl15[pnl_valid_d], dates[pnl_valid_d],
        )
        return {"promoted": promoted_q, "demoted": demoted_q, "incumbent": incumbent_q,
                "promoted_vs_demoted": gap}

    out["cohort_quality_75"] = cohort_quality_at(promo75, demo75, incu75, "75")
    out["cohort_quality_80"] = cohort_quality_at(promo80, demo80, incu80, "80")

    def enrichment_at(thresh: float, pre_mask: np.ndarray, post_mask: np.ndarray, pre_n: int) -> dict:
        pre_pnl = pnl15[pre_mask]
        pre_pnl_v = pre_pnl[~np.isnan(pre_pnl)]
        post_pnl = pnl15[post_mask]
        post_pnl_v = post_pnl[~np.isnan(post_pnl)]
        pre_stat = cohort_stat(pre_pnl_v)
        post_stat = cohort_stat(post_pnl_v)
        post_n_signals = int(post_mask.sum())
        skipped = (not n_ok(pre_stat["n"])) or (not n_ok(post_stat["n"]))
        return {
            "threshold": thresh,
            "pre": pre_stat, "post": post_stat,
            "supply_n_pre": pre_n, "supply_n_post": post_n_signals,
            "supply_delta_pct": (100.0 * (post_n_signals - pre_n) / pre_n) if pre_n else None,
            "quality_delta_mean_pnl15": (
                (post_stat["mean_pnl15"] - pre_stat["mean_pnl15"])
                if (post_stat["mean_pnl15"] is not None and pre_stat["mean_pnl15"] is not None) else None
            ),
            "quality_delta_wr_pp": (
                100.0 * (post_stat["wr"] - pre_stat["wr"])
                if (post_stat["wr"] is not None and pre_stat["wr"] is not None) else None
            ),
            "skipped": skipped,
            "reason": (None if not skipped else
                       "pre or post cohort N<%d (pre=%s post=%s)" % (MIN_CELL_N, pre_stat["n"], post_stat["n"])),
            "thin_report_only": pre_n < 100,
        }

    pre_mask_75 = overall >= 75.0
    post_mask_75 = adjusted >= 75.0
    out["bucket_enrichment_75"] = enrichment_at(75.0, pre_mask_75, post_mask_75, mig75["pre_n"])

    pre_mask_80 = overall >= 80.0
    post_mask_80 = adjusted >= 80.0
    out["bucket_enrichment_80"] = enrichment_at(80.0, pre_mask_80, post_mask_80, mig80["pre_n"])

    return out


# ---------------------------------------------------------------------------
# Blended full-stream view (item 4) -- corroboration ledger anchored, since
# the 42.8%/50.0% coverage fractions come from THIS ledger's own chain-hit
# definition (supply_check.json ground-truth cross-check).
# ---------------------------------------------------------------------------
def blended_full_stream(raw_corr: pl.DataFrame, variant: dict, supply_check: dict) -> dict:
    overall = raw_corr["overall"].to_numpy().astype(np.float64)
    skew = raw_corr["skew"].to_numpy().astype(np.float64)
    lift, _ = compute_lift(skew, variant)
    adjusted = overall + lift

    # "chain-hit-strict" = skew non-null (matches supply_check.json ground-truth
    # crosscheck's own gt_chain_hit definition: _gt_skew.is_not_null()). Rows in
    # this ledger with a null skew are functionally uncovered for the modifier
    # (lift=0) even though build_osk_ledger.py "kept" them (ATM leg found, OTM
    # leg(s) missing) -- see stage1_results.txt footnote.
    valid_skew = ~np.isnan(skew)

    gt = supply_check["ground_truth_crosscheck"]["aggregates_70_75"]
    cov_75 = gt["ALL 75+"]["chain_hit_pct"] / 100.0   # 0.500
    n_75_full_stream = gt["ALL 75+"]["n_total"]        # 860
    n_75_chainhit_strict_reported = gt["ALL 75+"]["n_chain_hit"]  # 430 (reference/consistency check)

    # Migration-count masks: skew-validity-gated (matches supply_check.json's own
    # gt_chain_hit=skew-not-null accounting, so the 430/860=50.0% anchor is used
    # self-consistently). A skew-null row can never migrate anyway (lift=0), so
    # this gating only affects which rows populate the DENOMINATOR, not whether
    # any migration is missed.
    pre_75_strict = (overall >= 75.0) & valid_skew
    post_75_strict = (adjusted >= 75.0) & valid_skew
    pre_75_n = int(pre_75_strict.sum())
    promoted_n = int(((~pre_75_strict) & post_75_strict & (overall < 75.0)).sum())
    demoted_n = int((pre_75_strict & (~post_75_strict)).sum())
    net_delta = promoted_n - demoted_n

    supply_delta_pct_within_chainhit = (100.0 * net_delta / pre_75_n) if pre_75_n else None
    blended_supply_delta_pct_full_stream = (
        100.0 * net_delta / n_75_full_stream if n_75_full_stream else None
    )

    # Quality-mean masks: UNRESTRICTED by skew-validity (matches
    # bucket_enrichment_75's own convention in evidence_for_variant -- a
    # skew-null row still has adjusted==overall, so it legitimately stays in
    # both pre/post as an inert incumbent; excluding it here would understate
    # the realized-quality baseline relative to the bucket_enrichment_75 numbers
    # reported alongside this block).
    pre_75_all = overall >= 75.0
    post_75_all = adjusted >= 75.0

    pnl15 = raw_corr["pnl15"].to_numpy().astype(np.float64)
    pre_pnl = pnl15[pre_75_all]
    pre_pnl_v = pre_pnl[~np.isnan(pre_pnl)]
    post_pnl = pnl15[post_75_all]
    post_pnl_v = post_pnl[~np.isnan(post_pnl)]
    pre_mean = float(np.mean(pre_pnl_v)) if len(pre_pnl_v) else None
    post_mean = float(np.mean(post_pnl_v)) if len(post_pnl_v) else None
    quality_shift_within_covered = (
        (post_mean - pre_mean) if (pre_mean is not None and post_mean is not None) else None
    )
    blended_quality_shift_full_stream = (
        cov_75 * quality_shift_within_covered if quality_shift_within_covered is not None else None
    )

    return {
        "coverage_fraction_75_used": cov_75,
        "n_75_full_stream": n_75_full_stream,
        "n_75_chainhit_strict_reported_supplycheck": n_75_chainhit_strict_reported,
        "n_75_chainhit_strict_measured_here": pre_75_n,
        "promoted_n_chainhit": promoted_n, "demoted_n_chainhit": demoted_n,
        "net_delta_n_chainhit": net_delta,
        "supply_delta_pct_within_chainhit": supply_delta_pct_within_chainhit,
        "blended_supply_delta_pct_full_stream": blended_supply_delta_pct_full_stream,
        "n_ripe_pre_covered": len(pre_pnl_v), "n_ripe_post_covered": len(post_pnl_v),
        "pre_mean_pnl15_covered": pre_mean, "post_mean_pnl15_covered": post_mean,
        "quality_shift_within_covered": quality_shift_within_covered,
        "blended_quality_shift_full_stream": blended_quality_shift_full_stream,
        "interpretation": (
            "blended_supply_delta_pct_full_stream = net tier-migration count at 75+ (measured on "
            "the chain-hit/skew-valid subset) expressed as a %% of the FULL signal stream's existing "
            "75+ population (N=%d) -- i.e. coverage_fraction(%.1f%%) x within-chainhit migration rate. "
            "blended_quality_shift_full_stream = coverage_fraction x the measured mean-pnl15 shift "
            "within the option-covered 75+ cohort -- uncovered rows have no option pnl15 at all (no "
            "chain = no premium to move), so their contribution is 'inert/unmeasured', not 'flat "
            "at some known value'; this number answers 'how much does the OPTION-TRADABLE-relevant "
            "slice of the full 75+ population shift', not a claim about the other half." % (
                n_75_full_stream, cov_75 * 100.0
            )
        ),
    }


def main() -> None:
    t0 = time.time()
    log("=== OSK era-layer Stage-1 evidence sweep (6-variant FIXED grid) ===")
    log("holdout cutoff: %s" % HOLDOUT_CUTOFF.isoformat())
    log("variants: %s" % ", ".join(v["id"] for v in VARIANTS))
    log("")

    with open(SUPPLY_CHECK_PATH, "r", encoding="utf-8") as f:
        supply_check = json.load(f)

    # --- PRIMARY panel ---
    primary = load_primary()
    log("PRIMARY (discovery) panel: N=%d rows (%s..%s), skew non-null=%d, pnl15 non-null=%d" % (
        primary.height, primary["date"].min(), primary["date"].max(),
        primary["skew"].drop_nulls().len(), primary["pnl15"].drop_nulls().len()))

    mfe_map = load_mfe15_map()
    primary_j = primary.join(mfe_map, on=["symbol", "date"], how="left")
    n_mfe_primary = primary_j["mfe15"].drop_nulls().len()
    log("PRIMARY joined to rs_ledger for mfe15: non-null=%d/%d ('net_path' searched -- NOT FOUND as "
        "a per-signal rs_ledger column; it only appears as the market-level 'cohort_net_path' "
        "construct in the GEX cohort-timing experiment [experiments/gex/spy_gex_test.py], a "
        "different substrate -- SKIPPED here with this reason)" % (n_mfe_primary, primary_j.height))
    log("")

    # --- CORROBORATION ledger (raw = all chain-hit kept rows; ripe = the panel per task spec) ---
    raw_corr = load_corroboration_raw()
    ripe_corr = raw_corr.filter(pl.col("ripe") == True)  # noqa: E712
    ripe_j = ripe_corr.join(mfe_map, on=["symbol", "date"], how="left")
    n_mfe_corr = ripe_j["mfe15"].drop_nulls().len()
    log("CORROBORATION ledger: raw chain-hit kept N=%d; ripe (the panel) N=%d (75+=%d, 80+=%d)" % (
        raw_corr.height, ripe_corr.height,
        ripe_corr.filter(pl.col("overall") >= 75).height, ripe_corr.filter(pl.col("overall") >= 80).height))
    log("CORROBORATION ripe joined to rs_ledger for mfe15: non-null=%d/%d" % (n_mfe_corr, ripe_j.height))
    log("")

    results = {"meta": {
        "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
        "primary_n": primary.height, "primary_skew_nonnull": int(primary["skew"].drop_nulls().len()),
        "corroboration_raw_n": raw_corr.height, "corroboration_ripe_n": ripe_corr.height,
        "min_cell_n": MIN_CELL_N,
        "variant_grid": VARIANTS,
        "multiplicity_note": (
            "6 variants tested against 2 panels (12 primary/corroboration cells x ~6 evidence "
            "blocks each); any single headline number below is a best-of-N read across this grid "
            "-- see the 6-row summary table for the full comparison, not a cherry-picked cell."
        ),
    }, "variants": {}}

    for variant in VARIANTS:
        vid = variant["id"]
        log("=" * 100)
        log("VARIANT %s  shape=%s  cap=%s  scale=%s" % (vid, variant["shape"], variant["cap"], variant["scale"]))
        log("=" * 100)

        primary_ev = evidence_for_variant(primary_j, variant, "primary_discovery")
        corr_ev = evidence_for_variant(ripe_j, variant, "corroboration_ripe")
        blended = blended_full_stream(raw_corr, variant, supply_check)

        results["variants"][vid] = {
            "params": variant, "primary": primary_ev, "corroboration": corr_ev, "blended": blended,
        }

        for label, ev in (("PRIMARY", primary_ev), ("CORROBORATION", corr_ev)):
            log("\n  --- %s ---" % label)
            m75 = ev["migration_75"]
            log("  [migration @75] pre_n=%d promoted=%d (+%s%%) demoted=%d (-%s%%) net=%+d (%s%%)%s" % (
                m75["pre_n"], m75["promoted_n"],
                ("%.1f" % m75["promoted_pct_of_pre"]) if m75["promoted_pct_of_pre"] is not None else "n/a",
                m75["demoted_n"],
                ("%.1f" % m75["demoted_pct_of_pre"]) if m75["demoted_pct_of_pre"] is not None else "n/a",
                m75["net_delta_n"],
                ("%+.1f" % m75["net_delta_pct_of_pre"]) if m75["net_delta_pct_of_pre"] is not None else "n/a",
                "  [THIN, report-only vs Stage-1 N-floor]" if m75["thin_report_only"] else "",
            ))
            m80 = ev["migration_80"]
            log("  [migration @80] pre_n=%d promoted=%d demoted=%d net=%+d%s" % (
                m80["pre_n"], m80["promoted_n"], m80["demoted_n"], m80["net_delta_n"],
                "  [THIN, report-only]" if m80["thin_report_only"] else "",
            ))
            cq = ev["cohort_quality_75"]
            for coh in ("promoted", "demoted", "incumbent"):
                c = cq[coh]
                log("  [cohort_quality@75 %-9s] N=%d mean_pnl15=%s WR=%s mean_mfe15=%s(N=%d)" % (
                    coh, c["n"],
                    ("%.4f" % c["mean_pnl15"]) if c["mean_pnl15"] is not None else "n/a",
                    ("%.1f%%" % (100 * c["wr"])) if c["wr"] is not None else "n/a",
                    ("%.4f" % c["mean_mfe15"]) if c["mean_mfe15"] is not None else "n/a",
                    c["n_mfe15"],
                ))
            gap = cq["promoted_vs_demoted"]
            if gap.get("skipped"):
                log("  [promoted-vs-demoted gap @75] %s" % gap.get("reason"))
            else:
                log("  [promoted-vs-demoted gap @75] gap=%.4f t_clust=%.2f (N_p=%d N_d=%d, G=%d dates)" % (
                    gap["gap_mean_pnl15"], gap["t_clust"], gap["n_promoted"], gap["n_demoted"], gap["g"]))
            be75 = ev["bucket_enrichment_75"]
            if be75["skipped"]:
                log("  [bucket_enrichment@75] %s" % be75["reason"])
            else:
                log("  [bucket_enrichment@75] pre: N=%d mean=%.4f WR=%.1f%%  ->  post: N=%d mean=%.4f WR=%.1f%%  "
                    "supply_delta=%+.1f%% quality_delta=%+.4f (%+.1fpp WR)" % (
                        be75["pre"]["n"], be75["pre"]["mean_pnl15"], 100 * be75["pre"]["wr"],
                        be75["post"]["n"], be75["post"]["mean_pnl15"], 100 * be75["post"]["wr"],
                        be75["supply_delta_pct"], be75["quality_delta_mean_pnl15"], be75["quality_delta_wr_pp"]))
            be80 = ev["bucket_enrichment_80"]
            if be80["skipped"]:
                log("  [bucket_enrichment@80] %s" % be80["reason"])
            else:
                log("  [bucket_enrichment@80] pre: N=%d mean=%.4f WR=%.1f%%  ->  post: N=%d mean=%.4f WR=%.1f%%  "
                    "supply_delta=%+.1f%%  [THIN, report-only]" % (
                        be80["pre"]["n"], be80["pre"]["mean_pnl15"], 100 * be80["pre"]["wr"],
                        be80["post"]["n"], be80["post"]["mean_pnl15"], 100 * be80["post"]["wr"],
                        be80["supply_delta_pct"]))

        log("\n  --- BLENDED FULL-STREAM (corroboration-ledger-anchored, coverage=%.1f%%) ---" % (
            blended["coverage_fraction_75_used"] * 100))
        log("  chain-hit(strict) 75+ N: measured_here=%d  supply_check_reported=%d  (delta from build's own "
            "'kept'-vs-'skew-non-null' distinction; see footnote)" % (
                blended["n_75_chainhit_strict_measured_here"], blended["n_75_chainhit_strict_reported_supplycheck"]))
        log("  within-chainhit: promoted=%d demoted=%d net=%+d (%s%% of chainhit-75 pop)" % (
            blended["promoted_n_chainhit"], blended["demoted_n_chainhit"], blended["net_delta_n_chainhit"],
            ("%+.1f" % blended["supply_delta_pct_within_chainhit"]) if blended["supply_delta_pct_within_chainhit"] is not None else "n/a"))
        log("  BLENDED 75+ supply delta (full stream, N=%d): %s%%" % (
            blended["n_75_full_stream"],
            ("%+.2f" % blended["blended_supply_delta_pct_full_stream"]) if blended["blended_supply_delta_pct_full_stream"] is not None else "n/a"))
        log("  covered-cohort quality shift: pre_mean=%s post_mean=%s shift=%s  ->  BLENDED full-stream quality shift: %s" % (
            ("%.4f" % blended["pre_mean_pnl15_covered"]) if blended["pre_mean_pnl15_covered"] is not None else "n/a",
            ("%.4f" % blended["post_mean_pnl15_covered"]) if blended["post_mean_pnl15_covered"] is not None else "n/a",
            ("%+.4f" % blended["quality_shift_within_covered"]) if blended["quality_shift_within_covered"] is not None else "n/a",
            ("%+.4f" % blended["blended_quality_shift_full_stream"]) if blended["blended_quality_shift_full_stream"] is not None else "n/a"))
        log("")

    # -----------------------------------------------------------------
    # GROWTH-GATE TOOL check (item 5)
    # -----------------------------------------------------------------
    log("=" * 100)
    log("GROWTH-GATE TOOL (experiments/version_scorecard/stage1_growth_gate.py + signal_supply.py)")
    log("=" * 100)
    algo_versions_cache = os.path.join(_ROOT, ".cache", "algorithm_versions")
    has_cache = os.path.isdir(algo_versions_cache)
    gg_note = (
        "stage1_growth_gate.py's load_bands() reads .cache/algorithm_versions/<version>/"
        "research_pack/utility_5y_wr15.json -- a version-KEYED research pack produced only after "
        "recalculate + assess + build_research_pack against a real ALGORITHM_VERSION row. This "
        "worktree's .cache/algorithm_versions/ %s. The OSK-era modifier has NO ALGORITHM_VERSION "
        "(it is Stage-1 pre-ship ledger evidence, not yet in scoring.py) so no candidate pack can "
        "exist. signal_supply.py additionally requires a live DB.execute_sql() query against the "
        "versioned `scores` table -- directly barred by this task's HARD STOP (no MySQL). "
        "CONCLUSION: neither tool is runnable against this ledger-based evidence; this is exactly "
        "the anticipated case flagged in the task brief. The manual W4/W5-ALIGNED numbers above "
        "(migration counts ~ W4 bucket-level view; blended supply/quality delta ~ the ebar/"
        "lambda_eff intuition W5 formalizes) are the Stage-1 currency available at this "
        "pre-simulation stage. A REAL SHIP/FLAG/BLOCK verdict requires the ScoreSimulator/"
        "faithful-path recalculation stage (out of scope here per HARD STOPS: no ScoreSimulator "
        "runs, no MySQL, no scoring.py edits)." % (
            "exists but has no OSK-related version pack" if has_cache else "does not exist at all in this worktree"
        )
    )
    log(gg_note)
    results["growth_gate_tool"] = {
        "algorithm_versions_cache_present": has_cache,
        "runnable": False,
        "reason": gg_note,
    }
    log("")

    # -----------------------------------------------------------------
    # 6-row summary table
    # -----------------------------------------------------------------
    log("=" * 100)
    log("6-VARIANT SUMMARY TABLE  (multiplicity: 6 variants x 2 panels -- best-of-N context applies)")
    log("=" * 100)
    log("PRIMARY (N=4046, larger/always-computable) carries the headline gap+t_clust; CORROBORATION")
    log("(N=730 ripe, our own recipe) shown alongside as the consistency check -- it is thinner and")
    log("SKIPs the gap test on 3/6 variants (A1,B1,B3) where a cohort falls below N=30.")
    header = ("%-4s | %-22s | %-24s | %-24s | %13s | %10s" % (
        "var", "promoted N (corr/prim)", "PRIMARY gap  t_clust", "CORROB.  gap  t_clust",
        "blend_supply%", "blend_qual"))
    log(header)
    summary_rows = []
    for variant in VARIANTS:
        vid = variant["id"]
        cor = results["variants"][vid]["corroboration"]
        pri = results["variants"][vid]["primary"]
        blended = results["variants"][vid]["blended"]
        gap_c = cor["cohort_quality_75"]["promoted_vs_demoted"]
        gap_p = pri["cohort_quality_75"]["promoted_vs_demoted"]
        row = {
            "variant": vid,
            "promoted_n_corroboration_75": cor["migration_75"]["promoted_n"],
            "promoted_n_primary_75": pri["migration_75"]["promoted_n"],
            "gap_promoted_minus_demoted_pnl15_primary": gap_p.get("gap_mean_pnl15"),
            "t_clust_primary": gap_p.get("t_clust"),
            "gap_primary_skipped": gap_p.get("skipped", False),
            "gap_promoted_minus_demoted_pnl15_corroboration": gap_c.get("gap_mean_pnl15"),
            "t_clust_corroboration": gap_c.get("t_clust"),
            "gap_corroboration_skipped": gap_c.get("skipped", False),
            "blended_75_supply_delta_pct_full_stream": blended["blended_supply_delta_pct_full_stream"],
            "blended_75_quality_delta_full_stream": blended["blended_quality_shift_full_stream"],
        }
        summary_rows.append(row)
        prim_str = ("%+.4f  t=%.2f" % (row["gap_promoted_minus_demoted_pnl15_primary"], row["t_clust_primary"])) \
            if not row["gap_primary_skipped"] else "SKIP N<30"
        corr_str = ("%+.4f  t=%.2f" % (row["gap_promoted_minus_demoted_pnl15_corroboration"], row["t_clust_corroboration"])) \
            if not row["gap_corroboration_skipped"] else "SKIP N<30"
        log("%-4s | %10d / %-9d | %-24s | %-24s | %13s | %10s" % (
            vid, row["promoted_n_corroboration_75"], row["promoted_n_primary_75"],
            prim_str, corr_str,
            ("%+.2f%%" % row["blended_75_supply_delta_pct_full_stream"]) if row["blended_75_supply_delta_pct_full_stream"] is not None else "n/a",
            ("%+.4f" % row["blended_75_quality_delta_full_stream"]) if row["blended_75_quality_delta_full_stream"] is not None else "n/a",
        ))
    results["summary_table"] = summary_rows

    elapsed = time.time() - t0
    log("\n=== DONE in %.1fs ===" % elapsed)
    results["meta"]["elapsed_s"] = round(elapsed, 2)

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(OUT_LINES)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump(results, f, indent=2, default=str)
    log("wrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)


if __name__ == "__main__":
    main()
