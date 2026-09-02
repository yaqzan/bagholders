"""OSK era-layer LAG-ROBUSTNESS check -- B3/B4 only, corroboration panel.

PURPOSE: Stage-1 evidence for modifier variants B4 (tanh cap=5 scale=1.5) and B3
(tanh cap=3 scale=1.5) used SAME-DAY skew (build_osk_ledger.py's default
lag_days=0, see stage1_results.json). Live scoring may only have PRIOR-DAY
option chains available at score time (options are pulled post-close). This
script reruns the corroboration-panel Stage-1 evidence cells for B4/B3 on the
lag-1 ledger (osk_era_ledger_lag1.parquet, built by
`build_osk_ledger.py --lag-days 1` -- the two OTM skew legs are redirected to
the nearest prior option_prices date within 5 calendar days back; the ATM
leg / entry_premium / pnl15 / labels stay D-anchored, unchanged from
lag_days=0) and puts the result side-by-side with the lag_days=0 numbers
already computed in stage1_results.json, plus:
  - mechanical attrition: signals that HAD non-null skew at lag0 and LOST it
    entirely at lag1 (chain/OTM-leg missing on the lagged date), and
  - the lag0-vs-lag1 skew day-over-day correlation on rows where both exist
    (if daily skew is sticky/autocorrelated, a 1-day-stale value should still
    carry most of the same-day signal; this number is the direct read on
    that).

REUSE, NOT REIMPLEMENTATION: imports stage1_sweep.py's compute_lift,
evidence_for_variant, blended_full_stream, load_mfe15_map, VARIANTS, and
MIN_CELL_N -- the exact same clustered-SE OLS / migration / bucket-
enrichment / blended-full-stream formulas apply to both panels, no logic
drift. A small self-check near the top recomputes the lag0 corroboration
numbers via this reused code path and diffs them against the already-shipped
stage1_results.json values as a correctness gate on the reuse itself.

WORKTREE DISCIPLINE: runs only from Trader-exp-osk-era; writes only to this
worktree's experiments/osk_era/ (lag_robustness.txt/.json). Reads existing
.cache/osk_era/ parquets + supply_check.json -- NO MySQL, NO queue submission
(light, seconds-scale foreground computation over cached parquets, exactly
like stage1_sweep.py's own discipline / the task's HARD STOP: "MySQL only via
the queued ledger build").

Usage:
    python experiments/osk_era/lag_robustness_sweep.py
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_WORKTREE_MARKER = "Trader-exp-osk-era"
assert _WORKTREE_MARKER in _ROOT, (
    f"lag_robustness_sweep.py must run from the {_WORKTREE_MARKER} worktree root, "
    f"resolved _ROOT={_ROOT}"
)
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
while _HERE in sys.path:
    sys.path.remove(_HERE)
sys.path.insert(0, _HERE)  # sibling-module import: stage1_sweep.py lives here

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

import experiments._holdout as _holdout_mod  # noqa: E402
assert os.path.abspath(_holdout_mod.__file__).lower().startswith(_ROOT.lower()), (
    f"experiments._holdout resolved OUTSIDE the worktree root ({_ROOT}): {_holdout_mod.__file__}"
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

import stage1_sweep as s1  # noqa: E402  -- REUSE (see docstring), not reimplementation
assert os.path.abspath(s1.__file__).lower().startswith(_HERE.lower()), (
    f"stage1_sweep resolved OUTSIDE this worktree's osk_era dir ({_HERE}): {s1.__file__}"
)

CACHE_DIR = os.path.join(_ROOT, ".cache", "osk_era")
LAG0_LEDGER_PATH = os.path.join(CACHE_DIR, "osk_era_ledger.parquet")
LAG1_LEDGER_PATH = os.path.join(CACHE_DIR, "osk_era_ledger_lag1.parquet")
SUPPLY_CHECK_PATH = os.path.join(CACHE_DIR, "supply_check.json")
STAGE1_RESULTS_JSON = os.path.join(_HERE, "stage1_results.json")

OUT_TXT = os.path.join(_HERE, "lag_robustness.txt")
OUT_JSON = os.path.join(_HERE, "lag_robustness.json")

TARGET_VARIANT_IDS = ("B3", "B4")

OUT_LINES: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)
    OUT_LINES.append(msg + "\n")


def _key_skew_map(df: pl.DataFrame) -> dict:
    out = {}
    for sym, d, sk in zip(df["symbol"].to_list(), df["date"].to_list(), df["skew"].to_list()):
        out[(sym, d)] = sk
    return out


def main() -> None:
    t0 = time.time()
    log("=== OSK era-layer LAG-ROBUSTNESS check (B3/B4 only, corroboration panel) ===")
    log("holdout cutoff: %s" % HOLDOUT_CUTOFF.isoformat())
    log("lag0 ledger : %s" % LAG0_LEDGER_PATH)
    log("lag1 ledger : %s" % LAG1_LEDGER_PATH)
    log("")

    with open(SUPPLY_CHECK_PATH, "r", encoding="utf-8") as f:
        supply_check = json.load(f)

    lag0_raw = pl.read_parquet(LAG0_LEDGER_PATH)
    assert_no_holdout_leak(lag0_raw, "osk_lag_robustness_lag0_raw")
    lag1_raw = pl.read_parquet(LAG1_LEDGER_PATH)
    assert_no_holdout_leak(lag1_raw, "osk_lag_robustness_lag1_raw")

    lag_days_val = None
    if "lag_days" in lag1_raw.columns:
        nn = lag1_raw["lag_days"].drop_nulls()
        lag_days_val = int(nn[0]) if nn.len() else None

    lag0_ripe = lag0_raw.filter(pl.col("ripe") == True)  # noqa: E712
    lag1_ripe = lag1_raw.filter(pl.col("ripe") == True)  # noqa: E712

    log("lag0 raw kept N=%d (ripe=%d)   lag1 raw kept N=%d (ripe=%d)   lag_days column in lag1 = %s" % (
        lag0_raw.height, lag0_ripe.height, lag1_raw.height, lag1_ripe.height, lag_days_val))
    log("lag0 skew non-null=%d (%.1f%%)   lag1 skew non-null=%d (%.1f%%)" % (
        lag0_raw["skew"].drop_nulls().len(), 100.0 * lag0_raw["skew"].drop_nulls().len() / lag0_raw.height,
        lag1_raw["skew"].drop_nulls().len(), 100.0 * lag1_raw["skew"].drop_nulls().len() / lag1_raw.height,
    ))
    log("")

    mfe_map = s1.load_mfe15_map()
    lag0_ripe_j = lag0_ripe.join(mfe_map, on=["symbol", "date"], how="left")
    lag1_ripe_j = lag1_ripe.join(mfe_map, on=["symbol", "date"], how="left")

    # -----------------------------------------------------------------
    # Self-check: recompute lag0 corroboration evidence via the REUSED
    # functions and diff against the already-shipped stage1_results.json
    # values -- a correctness gate on the reuse itself, not a task deliverable.
    # -----------------------------------------------------------------
    log("=" * 100)
    log("SELF-CHECK: recomputed lag0 (same-day) evidence vs. stage1_results.json (reuse-correctness gate)")
    log("=" * 100)
    selfcheck = {"ok": True, "details": []}
    if os.path.exists(STAGE1_RESULTS_JSON):
        with open(STAGE1_RESULTS_JSON, "r", encoding="utf-8") as f:
            prior = json.load(f)
        for variant in s1.VARIANTS:
            vid = variant["id"]
            if vid not in TARGET_VARIANT_IDS:
                continue
            fresh = s1.evidence_for_variant(lag0_ripe_j, variant, "corroboration_ripe_lag0_selfcheck")
            fresh_blend = s1.blended_full_stream(lag0_raw, variant, supply_check)
            stored = prior["variants"][vid]["corroboration"]
            stored_blend = prior["variants"][vid]["blended"]
            m_ok = (fresh["migration_75"]["promoted_n"] == stored["migration_75"]["promoted_n"]
                    and fresh["migration_75"]["demoted_n"] == stored["migration_75"]["demoted_n"])
            b_ok = abs((fresh_blend["blended_supply_delta_pct_full_stream"] or 0)
                       - (stored_blend["blended_supply_delta_pct_full_stream"] or 0)) < 1e-6
            ok = m_ok and b_ok
            selfcheck["ok"] = selfcheck["ok"] and ok
            selfcheck["details"].append({
                "variant": vid, "migration_match": m_ok, "blended_match": b_ok,
                "fresh_promoted_n": fresh["migration_75"]["promoted_n"],
                "stored_promoted_n": stored["migration_75"]["promoted_n"],
            })
            log("  %s: migration_75 promoted/demoted fresh=(%d,%d) stored=(%d,%d) match=%s | "
                "blended_supply_delta fresh=%.4f stored=%.4f match=%s" % (
                    vid, fresh["migration_75"]["promoted_n"], fresh["migration_75"]["demoted_n"],
                    stored["migration_75"]["promoted_n"], stored["migration_75"]["demoted_n"], m_ok,
                    fresh_blend["blended_supply_delta_pct_full_stream"] or 0.0,
                    stored_blend["blended_supply_delta_pct_full_stream"] or 0.0, b_ok))
    else:
        log("  stage1_results.json not found -- self-check skipped")
        selfcheck = {"ok": None, "details": [], "reason": "stage1_results.json missing"}
    log("SELF-CHECK %s" % ("PASS" if selfcheck["ok"] else "FAIL/SKIPPED"))
    log("")

    # -----------------------------------------------------------------
    # Item 3: mechanical attrition + skew day-over-day correlation, on the
    # RAW chain-hit-kept population (both ledgers share the identical
    # universe/ATM-kept criterion, unaffected by lag_days -- confirmed
    # n_kept_rows: lag0=%d lag1=%d).
    # -----------------------------------------------------------------
    log("=" * 100)
    log("MECHANICAL ATTRITION + SKEW DAY-OVER-DAY CORRELATION (raw chain-hit-kept population)")
    log("=" * 100)

    m0 = _key_skew_map(lag0_raw)
    m1 = _key_skew_map(lag1_raw)
    keys0, keys1 = set(m0.keys()), set(m1.keys())
    common = keys0 & keys1
    only0 = keys0 - keys1
    only1 = keys1 - keys0

    n_lag0_nonnull = sum(1 for k in common if m0[k] is not None)
    n_lost = sum(1 for k in common if m0[k] is not None and m1[k] is None)
    n_gained = sum(1 for k in common if m0[k] is None and m1[k] is not None)
    n_both_nonnull = sum(1 for k in common if m0[k] is not None and m1[k] is not None)
    n_both_null = sum(1 for k in common if m0[k] is None and m1[k] is None)

    pairs = [(m0[k], m1[k]) for k in common if m0[k] is not None and m1[k] is not None]
    if len(pairs) >= 2:
        arr0 = np.array([p[0] for p in pairs], dtype=np.float64)
        arr1 = np.array([p[1] for p in pairs], dtype=np.float64)
        pearson_r = float(np.corrcoef(arr0, arr1)[0, 1])
        spearman_r, _ = spearmanr(arr0, arr1)
        spearman_r = float(spearman_r)
        mean_abs_diff = float(np.mean(np.abs(arr0 - arr1)))
    else:
        pearson_r = spearman_r = mean_abs_diff = None

    pct_lost = (100.0 * n_lost / n_lag0_nonnull) if n_lag0_nonnull else None
    log("universe match sanity: common=%d  lag0_only=%d  lag1_only=%d  (lag0 raw N=%d, lag1 raw N=%d)" % (
        len(common), len(only0), len(only1), lag0_raw.height, lag1_raw.height))
    log("lag0 non-null skew (of common)=%d" % n_lag0_nonnull)
    log("LOST skew entirely at lag1 (non-null@lag0 -> null@lag1) = %d  (%s%% of lag0 non-null)" % (
        n_lost, ("%.1f" % pct_lost) if pct_lost is not None else "n/a"))
    log("GAINED skew at lag1 (null@lag0 -> non-null@lag1, e.g. same-day chain gap) = %d" % n_gained)
    log("both-null (never had skew either way) = %d" % n_both_null)
    log("both-non-null (used for correlation) N=%d  pearson_r=%s  spearman_r=%s  mean_abs_diff=%s" % (
        n_both_nonnull,
        ("%.4f" % pearson_r) if pearson_r is not None else "n/a",
        ("%.4f" % spearman_r) if spearman_r is not None else "n/a",
        ("%.4f" % mean_abs_diff) if mean_abs_diff is not None else "n/a",
    ))
    log("")

    attrition = {
        "n_common_signals": len(common), "n_lag0_only": len(only0), "n_lag1_only": len(only1),
        "n_lag0_skew_nonnull": n_lag0_nonnull,
        "n_lost_skew_at_lag1": n_lost, "pct_lost_skew_of_lag0_nonnull": pct_lost,
        "n_gained_skew_at_lag1": n_gained, "n_both_null": n_both_null,
    }
    skew_corr = {
        "n_pairs_both_nonnull": n_both_nonnull, "pearson_r": pearson_r, "spearman_r": spearman_r,
        "mean_abs_diff": mean_abs_diff,
    }

    # -----------------------------------------------------------------
    # Item 2: B3/B4 side-by-side (corroboration-ripe evidence + chain-hit
    # blended full-stream), lag0 vs lag1, SAME real coverage fractions
    # (supply_check.json is passed unchanged to both blended_full_stream
    # calls -- only the migration numerator changes, not the denominator).
    # -----------------------------------------------------------------
    variants = [v for v in s1.VARIANTS if v["id"] in TARGET_VARIANT_IDS]
    results = {
        "meta": {
            "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
            "lag0_raw_n": lag0_raw.height, "lag0_ripe_n": lag0_ripe.height,
            "lag1_raw_n": lag1_raw.height, "lag1_ripe_n": lag1_ripe.height,
            "lag_days_used_in_lag1_ledger": lag_days_val,
            "min_cell_n": s1.MIN_CELL_N,
            "target_variants": list(TARGET_VARIANT_IDS),
            "coverage_fractions_note": (
                "blended_full_stream uses the SAME supply_check.json ground-truth anchor "
                "(coverage_fraction_75_used, n_75_full_stream, n_75_chainhit_strict_reported) "
                "for both lag0 and lag1 calls -- only the migration/quality numerator (measured "
                "from each ledger's own overall+skew+pnl15 columns) differs between the two."
            ),
        },
        "selfcheck_vs_stage1_results": selfcheck,
        "attrition": attrition,
        "skew_day_over_day_correlation": skew_corr,
        "variants": {},
    }

    for variant in variants:
        vid = variant["id"]
        log("=" * 100)
        log("VARIANT %s  shape=%s  cap=%s  scale=%s" % (vid, variant["shape"], variant["cap"], variant["scale"]))
        log("=" * 100)

        lag0_ev = s1.evidence_for_variant(lag0_ripe_j, variant, "corroboration_ripe_lag0")
        lag1_ev = s1.evidence_for_variant(lag1_ripe_j, variant, "corroboration_ripe_lag1")
        lag0_blend = s1.blended_full_stream(lag0_raw, variant, supply_check)
        lag1_blend = s1.blended_full_stream(lag1_raw, variant, supply_check)

        results["variants"][vid] = {
            "params": variant,
            "lag0": {"evidence": lag0_ev, "blended": lag0_blend},
            "lag1": {"evidence": lag1_ev, "blended": lag1_blend},
        }

        for label, ev, blend in (
            ("LAG0 (same-day skew)", lag0_ev, lag0_blend),
            ("LAG1 (D-1 chain skew)", lag1_ev, lag1_blend),
        ):
            log("\n  --- %s ---" % label)
            m75 = ev["migration_75"]
            log("  [migration @75] pre_n=%d promoted=%d demoted=%d net=%+d%s" % (
                m75["pre_n"], m75["promoted_n"], m75["demoted_n"], m75["net_delta_n"],
                "  [THIN, report-only]" if m75["thin_report_only"] else "",
            ))
            gap = ev["cohort_quality_75"]["promoted_vs_demoted"]
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
            log("  [blended 75+ full-stream, coverage=%.1f%%] chainhit_75 measured=%d  promoted=%d demoted=%d net=%+d  "
                "supply_delta=%s%%  quality_delta=%s" % (
                    blend["coverage_fraction_75_used"] * 100, blend["n_75_chainhit_strict_measured_here"],
                    blend["promoted_n_chainhit"], blend["demoted_n_chainhit"], blend["net_delta_n_chainhit"],
                    ("%+.2f" % blend["blended_supply_delta_pct_full_stream"]) if blend["blended_supply_delta_pct_full_stream"] is not None else "n/a",
                    ("%+.4f" % blend["blended_quality_shift_full_stream"]) if blend["blended_quality_shift_full_stream"] is not None else "n/a",
                ))
        log("")

    # -----------------------------------------------------------------
    # Compact side-by-side summary table
    # -----------------------------------------------------------------
    log("=" * 100)
    log("SIDE-BY-SIDE SUMMARY (lag0 vs lag1, B3/B4 only, corroboration panel)")
    log("=" * 100)
    header = "%-4s | %-5s | %8s | %8s | %-20s | %12s | %12s" % (
        "var", "panel", "N_promo", "N_demo", "gap  t_clust", "blend_sup%", "blend_qual")
    log(header)
    summary_rows = []
    for variant in variants:
        vid = variant["id"]
        for label, ev, blend in (
            ("lag0", results["variants"][vid]["lag0"]["evidence"], results["variants"][vid]["lag0"]["blended"]),
            ("lag1", results["variants"][vid]["lag1"]["evidence"], results["variants"][vid]["lag1"]["blended"]),
        ):
            gap = ev["cohort_quality_75"]["promoted_vs_demoted"]
            m75 = ev["migration_75"]
            row = {
                "variant": vid, "panel": label,
                "n_promoted": m75["promoted_n"], "n_demoted": m75["demoted_n"],
                "gap_mean_pnl15": gap.get("gap_mean_pnl15"), "t_clust": gap.get("t_clust"),
                "gap_skipped": gap.get("skipped", False), "gap_skip_reason": gap.get("reason"),
                "blended_supply_delta_pct": blend["blended_supply_delta_pct_full_stream"],
                "blended_quality_delta": blend["blended_quality_shift_full_stream"],
            }
            summary_rows.append(row)
            gap_str = ("%+.4f t=%.2f" % (row["gap_mean_pnl15"], row["t_clust"])) if not row["gap_skipped"] else "SKIP N<30"
            log("%-4s | %-5s | %8d | %8d | %-20s | %12s | %12s" % (
                vid, label, row["n_promoted"], row["n_demoted"], gap_str,
                ("%+.2f%%" % row["blended_supply_delta_pct"]) if row["blended_supply_delta_pct"] is not None else "n/a",
                ("%+.4f" % row["blended_quality_delta"]) if row["blended_quality_delta"] is not None else "n/a",
            ))
    results["summary_table"] = summary_rows

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
