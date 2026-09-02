"""
osk_reread.py -- re-run the OSK cross-regime validation statistics on EITHER the
OLD (contaminated) Polygon BS-IV panel or the CORRECTED as-traded-spot panel.

WHY THIS EXISTS
---------------
The OSK lead was KILLED 2026-07-07 by experiments/osk_validation/ on the
backward-OOS read (2022-08..2025-02-10, N=3,807, spearman -0.002; 75+ -0.073;
2022 bear tail -0.107 at N=569). That analysis ran on
`.cache/polygon_iv/iv_ledger_polygon.parquet`, which has since been shown to
carry TWO independent defects (see .claude/docs/traps.md and PANEL_AUDIT.md):

  1. WRONG SPOT. `price_history.close` (split+DIVIDEND back-adjusted) was used as
     the option spot -- both to select the "ATM" strike and as S in the BS solve
     -- against as-traded strikes. 31.4% of rows drift; 2022 is the WORST year
     (44.5% drift >2%, 30.3% picked a contract actually >5% ITM). That is exactly
     the slice the kill fired on. On drifted rows a correctly built ledger
     disagrees on strike 61.4% of the time.
  2. TRUNCATE-SUBSTITUTED pnl15 (the OUTCOME variable). The panel was built
     2026-07-07; commit ebca1e1b (2026-07-08) removed `fwd[min(14,len(fwd)-1)]`
     from polygon_iv_ingest.process_one and the panel was never rebuilt. The old
     panel reports a non-null pnl15 for 100% of rows where ~50% should be null.

This harness does NOT redesign the analysis. It imports
`experiments/osk_validation/osk_validation_test.py` and calls that module's own
functions, so every statistic (spearman, quintile table, standardized OLS with
plain + date-clustered sandwich SE, winsorization, N floors, era/split
definitions) is the ORIGINAL code, not a re-implementation. The only new things
are (a) a panel-path switch, (b) contamination stratification, (c) a ripeness
arm, and (d) side-by-side reporting.

MODES
-----
    --selftest                 run against the OLD panel and assert it reproduces
                               the published osk_validation numbers. THIS IS THE
                               GATE: if it cannot reproduce them, the re-read is
                               meaningless and the script exits non-zero.
    --panel old                run the OLD panel only.
    --panel corrected          run the CORRECTED panel, side by side with OLD.
    --panel-path PATH          explicit parquet (implies a side-by-side vs OLD
                               unless --no-compare).
    --compare                  force the OLD-vs-CORRECTED side-by-side.
    --allow-partial            permit running against an incomplete corrected
                               panel (default: REFUSED -- the corrected panel is
                               still being generated).

ARMS (see --help text in the output header)
    native        every panel judged on its own surviving rows (the original's
                  own per-cell drop_nulls). Coverage differs between panels.
    ripe_shared   the ONLY like-for-like arm: rows present in BOTH panels where
                  the corrected panel reports fwd_bars >= 15 (outcome genuinely
                  ripe). Requires the corrected panel.

STRATA (every headline statistic is also reported split this way)
    all / undrifted (adj_factor <= 1.001) / drifted (adj_factor > 1.001)
    adj_factor is derived identically for BOTH panels from two offline caches
    (as-traded spot vs price_history close), so the strata are row-identical
    across panels. The derivation reproduces PANEL_AUDIT.md exactly.

READ-ONLY w.r.t. every existing artifact. Writes only
experiments/polygon_real_premium/osk_reread_*.{txt,json}. Never touches
.cache/polygon_iv/*, experiments/osk_validation/*, or any other experiment.
Never queues anything. ASCII-only output.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/polygon_real_premium/osk_reread.py --selftest
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/polygon_real_premium/osk_reread.py --panel old
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/polygon_real_premium/osk_reread.py --panel corrected
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_OSK_DIR = os.path.join(_ROOT, "experiments", "osk_validation")
for _p in (_ROOT, _OSK_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# The ORIGINAL harness. Every statistic below is one of ITS functions.
import osk_validation_test as p1  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OLD_PANEL = os.path.join(_ROOT, ".cache", "polygon_iv", "iv_ledger_polygon.parquet")
CORRECTED_PANEL = os.path.join(_ROOT, ".cache", "polygon_real_premium", "iv_panel_corrected.parquet")
CORRECTED_META = os.path.join(_ROOT, ".cache", "polygon_real_premium", "iv_panel_corrected_meta.json")
# as-traded spot cache (yfinance auto_adjust=False x forward split ratios), built
# by pull.load_unadj_spots and reused verbatim by the audit + the rebuild.
SPOT_CACHE_CANDIDATES = [
    os.path.join(_ROOT, ".cache", "polygon_real_premium", "_iv_panel_spot.parquet"),
    os.path.join(_ROOT, ".cache", "polygon_real_premium", "_panel_audit_spot.parquet"),
]
# price_history.close (back-adjusted) -- the ORIGINAL harness's own price cache.
PRICE_CACHE = p1.PRICE_CACHE

OUT_DIR = _HERE

# ---------------------------------------------------------------------------
# Bars -- UNCHANGED from P2.4 (.claude/docs/gameplan.md row P2.4). Printed, never
# re-derived, never relaxed.
# ---------------------------------------------------------------------------
P2_4_BAR_TEXT = [
    "P2.4 (gameplan.md) -- OSK 4yr re-validation on Polygon skew: per-year sign table",
    "  (2021 bull AND 2022 bear separately -- the test that killed iv_rv), quintile",
    "  monotonicity, orthogonality to SVR/score/recent-return, holdout-locked.",
    "  *** PASS = t >= 3 same-sign in 2021 AND 2022. ***",
    "",
    "  DATA LIMITATION (not a relaxation of the bar): 2021 is UNAVAILABLE at Polygon",
    "  Developer depth -- the aggregates reach only ~2022-08. The original 2026-07-07",
    "  verdict therefore rested on 2022 ALONE, and so does this re-read. The bar is",
    "  printed as written; the 2021 leg is simply unevaluable with this vendor tier.",
]
ORIGINAL_DESIGN_BARS = [
    "DESIGN.md (osk_validation) bars, applied to the backward-OOS pooled decisive cell:",
    "  PASS = clustered t(skew) >= +2.5 AND spearman >= +0.05 AND >=2 of E1-E3 positive-sign",
    "         AND no era with spearman <= -0.05",
    "  WEAK = backward-OOS same sign with clustered t in [1.5, 2.5)",
    "  KILL = clustered t < 1.5, OR negative sign, OR an era <= -0.05 with N >= 500",
    "  (2026-07-07 result: era E1 spearman -0.107 at N=569 tripped the KILL clause.)",
]

# ---------------------------------------------------------------------------
# PUBLISHED reference values -- verbatim from
# experiments/osk_validation/osk_results_phase2.json (full precision; the VERDICT
# and the brief quote rounded forms of these).
# ---------------------------------------------------------------------------
PUBLISHED = {
    # univariate spearman: key -> (n, rho)
    "univ": {
        "backward_oos_pooled": (3807, -0.0017307338673685944),
        "backward_oos_pooled_overall>=75": (514, -0.07287911195106225),
        "full_pooled": (7425, 0.042905528200344815),
        "era_E1": (569, -0.10715942300921215),
        "era_E2": (834, 0.04507717157907133),
        "era_E3": (2092, 0.013309441422741475),
        "era_E4": (1207, 0.05697743804018722),
        "era_E5": (2723, 0.09185414805772318),
        "discovery_full": (3618, 0.08955337979279825),
        "discovery_overall>=75": (683, 0.11442236749083969),
    },
    # OLS: key -> (n, beta, t_plain, t_clust)
    "ols": {
        "backward_oos_pooled|raw": (3807, 0.048568163496767076, 3.0007393713875574, 4.5980655188217465),
        "backward_oos_pooled|winsorized_1pct": (3807, 0.0489863036364141, 3.0269713529227076, 4.471791290879435),
        "full_pooled|raw": (7424, 0.06130822208312515, 5.289511016581865, 5.972123171760483),
        "era_E1|raw": (569, -0.030161964735963262, -0.7232094330906474, -0.8701683989014992),
        "discovery_full|raw": (3617, 0.07678760201247699, 4.633175165520906, 3.375442771745531),
        "discovery_overall>=75|raw": (683, 0.13770221097361193, 3.623072652520614, 3.6896944896605524),
    },
    "meta": {
        "panel_n": 8643,
        "panel_symbols": 669,
        "split_backward_oos": 4597,
        "split_discovery": 4046,
        "e1_e3_positive_count": 2,
        "bar_label": "KILL",
    },
}
# Brief-quoted (rounded) headline forms, echoed for the reader.
BRIEF_HEADLINES = [
    ("backward-OOS pooled spearman", -0.002, "univ", "backward_oos_pooled"),
    ("backward-OOS 75+ spearman", -0.073, "univ", "backward_oos_pooled_overall>=75"),
    ("2022-bear-tail (E1) spearman", -0.107, "univ", "era_E1"),
    ("discovery-window spearman", 0.090, "univ", "discovery_full"),
    ("discovery orthogonalized clustered t", 3.4, "ols", "discovery_full|raw"),
]

TOL_CORR = 0.005      # flag any correlation deviation greater than this
TOL_T = 0.2           # flag any t deviation greater than this

# Correctness-check thresholds for the OLD-vs-CORRECTED comparison on UNDRIFTED
# rows (where the spot bug is inert by construction).
UNDRIFTED_RHO_WARN = 0.02
UNDRIFTED_T_WARN = 0.5

DRIFT_THRESHOLD = 1.001   # PANEL_AUDIT.md's own "uncontaminated" cut
MIN_COMPLETE_ROWS = 5000  # corrected-panel completeness guard (old panel = 8,643)

# Headline scopes reported for every (panel, arm, stratum) block. Keys match the
# original harness's own keys so they can be compared to PUBLISHED directly.
HEADLINE_SCOPES = [
    ("discovery_full", "discovery window 2025-02-11..2026-05-15, 70+ universe"),
    ("discovery_overall>=75", "discovery window, overall >= 75"),
    ("backward_oos_pooled", "BACKWARD-OOS 2022-08-01..2025-02-10 (the kill cell), 70+"),
    ("backward_oos_pooled_overall>=75", "backward-OOS, overall >= 75"),
    ("era_E1", "2022 BEAR TAIL 2022-08-01..2022-12-31 (the era that tripped KILL)"),
    ("full_pooled", "all dates pooled, 70+"),
]

_NONFINITE_T = []      # populated by _t_of(); a non-finite t is FATAL


def log(msg=""):
    print(msg, flush=True)


def _fmt(v, nd=4):
    if v is None:
        return "n/a"
    if isinstance(v, float) and not math.isfinite(v):
        return "NONFINITE"
    return ("%%.%df" % nd) % v


def _hr(ch="=", n=100):
    return ch * n


# ---------------------------------------------------------------------------
# Panel loading (panel-agnostic; the corrected panel's first 15 columns ARE the
# old panel's columns in the old panel's order -- REBUILD_NOTES.md section 2)
# ---------------------------------------------------------------------------
REQUIRED_COLS = ["symbol", "date", "overall", "vol_pct", "atm_iv", "entry_premium",
                 "iv_rv", "skew", "pnl15", "atm_strike", "dte"]


def load_panel_any(path: str, label: str) -> pl.DataFrame:
    """Load a panel parquet, normalize `date` to pl.Date, and apply the single
    fill_nan(None) choke point (polars drop_nulls does NOT drop float NaN)."""
    if not os.path.exists(path):
        raise SystemExit("ERROR: panel not found: %s" % path)
    df = pl.read_parquet(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise SystemExit("ERROR: panel %s is missing required columns: %s" % (path, missing))
    if df["date"].dtype != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    # ONE choke point: NaN -> null across every float column.
    float_cols = [c for c, t in df.schema.items() if t in (pl.Float64, pl.Float32)]
    if float_cols:
        df = df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])
    log("[%s] panel loaded: %s  N=%d  symbols=%d  dates %s..%s"
        % (label, path, df.height, df["symbol"].n_unique(), df["date"].min(), df["date"].max()))
    return df


def check_corrected_completeness(df: pl.DataFrame, allow_partial: bool):
    """The corrected panel is generated by a long resumable run. Refuse to
    analyze a partial file unless explicitly overridden -- a partial panel would
    produce a real-looking but meaningless comparison."""
    meta = {}
    if os.path.exists(CORRECTED_META):
        try:
            with open(CORRECTED_META, "r", encoding="ascii", errors="replace") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    n = df.height
    win = meta.get("window_requested", {})
    # Judge completeness from the PANEL's own coverage, not from meta
    # window_requested -- the rebuild is chunked/resumable, so meta describes only
    # the last invocation.
    d_min, d_max = str(df["date"].min()), str(df["date"].max())
    complete = (n >= MIN_COMPLETE_ROWS and d_min <= "2022-08-15" and d_max >= "2026-05-01")
    if complete:
        return
    msg = [
        "",
        _hr("!"),
        "REFUSING TO RUN: the corrected panel looks INCOMPLETE.",
        "  rows=%d (expected >= %d; the old panel has 8,643)" % (n, MIN_COMPLETE_ROWS),
        "  panel dates %s .. %s (need <= 2022-08-15 .. >= 2026-05-01)" % (d_min, d_max),
        "  meta (last invocation only) window_requested=%s  kept_rows=%s  built_at=%s"
        % (win, meta.get("kept_rows"), meta.get("built_at")),
        "  A partial panel yields a real-looking but MEANINGLESS old-vs-corrected delta.",
        "  Re-run once rebuild_iv_panel.py has finished, or pass --allow-partial if you",
        "  genuinely want a smoke-sized read (and label it as such).",
        _hr("!"),
        "",
    ]
    if allow_partial:
        log("\n".join(msg).replace("REFUSING TO RUN", "WARNING (--allow-partial given)"))
        return
    raise SystemExit("\n".join(msg))


# ---------------------------------------------------------------------------
# Contamination key: adj_factor, derived IDENTICALLY for both panels
# ---------------------------------------------------------------------------
def load_spot_cache() -> pl.DataFrame:
    for path in SPOT_CACHE_CANDIDATES:
        if os.path.exists(path):
            sp = pl.read_parquet(path)
            if sp["date"].dtype != pl.Date:
                sp = sp.with_columns(pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
            log("as-traded spot cache: %s (N=%d, symbols=%d)" % (path, sp.height, sp["symbol"].n_unique()))
            return sp.select(["symbol", "date", "close_unadj"])
    raise SystemExit("ERROR: no as-traded spot cache found in %s" % SPOT_CACHE_CANDIDATES)


def load_adjusted_close_cache() -> pl.DataFrame:
    if not os.path.exists(PRICE_CACHE):
        raise SystemExit("ERROR: price cache not found: %s (run osk_validation_test.py first)" % PRICE_CACHE)
    px = pl.read_parquet(PRICE_CACHE)
    if px["date"].dtype != pl.Date:
        px = px.with_columns(pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return px.select(["symbol", "date", "close"]).rename({"close": "close_adjusted"})


def attach_adj_factor(df: pl.DataFrame, spot: pl.DataFrame, adjclose: pl.DataFrame,
                      label: str) -> pl.DataFrame:
    """adj_factor = as_traded_close / price_history.close  (PANEL_AUDIT.md 1).
    Derived from caches -- NOT read from the corrected panel's own column -- so
    the strata are row-identical across panels. Where the corrected panel does
    carry adj_factor, the two are cross-checked below."""
    out = df.join(spot, on=["symbol", "date"], how="left").join(adjclose, on=["symbol", "date"], how="left")
    out = out.with_columns(
        pl.when(pl.col("close_adjusted") > 0)
          .then(pl.col("close_unadj") / pl.col("close_adjusted"))
          .otherwise(None).alias("adj_factor_derived")
    )
    out = out.with_columns(pl.col("adj_factor_derived").fill_nan(None))
    n_res = out["adj_factor_derived"].drop_nulls().len()
    n_drift = out.filter(pl.col("adj_factor_derived") > DRIFT_THRESHOLD).height
    log("[%s] adj_factor derived: resolved %d/%d (%.1f%%), drifted(>%.3f) %d (%.1f%% of resolved)"
        % (label, n_res, out.height, 100.0 * n_res / max(out.height, 1), DRIFT_THRESHOLD,
           n_drift, 100.0 * n_drift / max(n_res, 1)))
    # cross-check against the panel's own column when present
    if "adj_factor" in df.columns:
        chk = out.select(["adj_factor", "adj_factor_derived"]).drop_nulls()
        if chk.height:
            d = (chk["adj_factor"].to_numpy() - chk["adj_factor_derived"].to_numpy())
            log("[%s] adj_factor cross-check vs panel column: N=%d max|diff|=%.2e"
                % (label, chk.height, float(np.max(np.abs(d)))))
    return out.drop(["close_unadj", "close_adjusted"])


# ---------------------------------------------------------------------------
# Analysis-panel construction -- delegates to the ORIGINAL build_full_panel
# (stock_r20 from rs_ledger, semivol_r recomputed with the house formula, era +
# split labels). The price cache is a hit for the old panel's universe; a novel
# symbol would trigger the original's ONE bulk MySQL top-up query.
# ---------------------------------------------------------------------------
def build_analysis_panel(panel: pl.DataFrame, rs_slim: pl.DataFrame, label: str):
    assert_no_holdout_leak(panel, "osk_reread:%s:panel" % label)
    log("[%s] holdout guard PASSED (max date <= cutoff %s)" % (label, HOLDOUT_CUTOFF.isoformat()))
    syms = sorted(set(panel["symbol"].to_list()))
    if os.path.exists(PRICE_CACHE):
        cached = set(pl.read_parquet(PRICE_CACHE)["symbol"].unique().to_list())
        missing = sorted(set(syms) - cached)
        if missing:
            log("[%s] NOTE: %d symbols absent from the price cache -- the original harness will run"
                " ONE bulk MySQL top-up query (semivol_r inputs): %s%s"
                % (label, len(missing), missing[:8], " ..." if len(missing) > 8 else ""))
    full, price_meta = p1.build_full_panel(panel, rs_slim)
    assert_no_holdout_leak(full, "osk_reread:%s:full" % label)
    log("[%s] analysis panel: N=%d  stock_r20 non-null=%d  semivol_r non-null=%d  price_cache=%s"
        % (label, full.height, full["stock_r20"].drop_nulls().len(),
           full["semivol_r"].drop_nulls().len(), price_meta.get("source")))
    return full, price_meta


# ---------------------------------------------------------------------------
# Statistic wrappers -- every one of these calls the ORIGINAL functions
# ---------------------------------------------------------------------------
def scope_slice(full: pl.DataFrame, key: str) -> pl.DataFrame:
    if key == "discovery_full":
        return full.filter(pl.col("split") == "discovery")
    if key == "discovery_overall>=75":
        return full.filter((pl.col("split") == "discovery") & (pl.col("overall") >= 75))
    if key == "backward_oos_pooled":
        return full.filter(pl.col("split") == "backward_oos")
    if key == "backward_oos_pooled_overall>=75":
        return full.filter((pl.col("split") == "backward_oos") & (pl.col("overall") >= 75))
    if key == "full_pooled":
        return full
    if key == "full_pooled_overall>=75":
        return full.filter(pl.col("overall") >= 75)
    if key.startswith("era_") and key.endswith("_overall>=75"):
        return full.filter((pl.col("era") == key[4:-12]) & (pl.col("overall") >= 75))
    if key.startswith("era_"):
        return full.filter(pl.col("era") == key[4:])
    raise KeyError(key)


def _t_of(res: dict, tag: str):
    """Extract a clustered t and ASSERT it is finite. A NaN t silently reads as a
    null result -- exactly the failure mode that would fake a 'still killed'
    verdict here -- so it is recorded as FATAL, never quietly reported."""
    if res is None or res.get("skipped"):
        return None
    t = res.get("t_clust_feature")
    if t is None or not math.isfinite(float(t)):
        _NONFINITE_T.append(tag)
        return float("nan")
    return float(t)


def quintile_monotonicity(univ_entry: dict):
    """DERIVED READOUT (not part of the original harness): summarize the
    original's own quintile table as (up-steps out of 4, spearman of Q index vs
    mean pnl15). The underlying quintile numbers are the original's, unmodified."""
    q = (univ_entry or {}).get("quintiles", {})
    if not q or q.get("skipped"):
        return None
    rows = [r for r in q.get("quintiles", []) if not r.get("skipped")]
    if len(rows) < 3:
        return None
    means = [r["mean_pnl15"] for r in rows]
    idx = [r["q"] for r in rows]
    ups = sum(1 for a, b in zip(means, means[1:]) if b > a)
    from scipy.stats import spearmanr
    rho, _ = spearmanr(idx, means)
    return {"n_cells": len(rows), "up_steps": ups, "max_steps": len(rows) - 1,
            "q_rank_spearman": float(rho) if rho == rho else None,
            "means": means, "wr": [r["wr"] for r in rows]}


def headline_block(full: pl.DataFrame, block_label: str, verbose_lines: list):
    """Run the six headline scopes with the ORIGINAL _univariate_one / _ols_scope."""
    univ, ols = {}, {}
    sink = []
    for key, _desc in HEADLINE_SCOPES:
        sub = scope_slice(full, key)
        p1._univariate_one(sub, key, sink, univ)
        p1._ols_scope(sub, key, sink, ols)
    verbose_lines.extend(["  " + ln for ln in sink])
    out = {"label": block_label, "rows": full.height, "cells": {}}
    for key, desc in HEADLINE_SCOPES:
        u = univ.get(key, {})
        o_raw = ols.get("%s|raw" % key, {})
        o_win = ols.get("%s|winsorized_1pct" % key, {})
        out["cells"][key] = {
            "desc": desc,
            "n_rows_scope": scope_slice(full, key).height,
            "univ_n": u.get("n"),
            "spearman": None if u.get("skipped") else u.get("spearman_rho"),
            "spearman_p": None if u.get("skipped") else u.get("spearman_p"),
            "quintiles": None if u.get("skipped") else u.get("quintiles"),
            "monotonicity": quintile_monotonicity(u),
            "ols_n": o_raw.get("n"),
            "ols_clusters": o_raw.get("n_clusters"),
            "beta": None if o_raw.get("skipped") else o_raw.get("beta_feature"),
            "t_plain": None if o_raw.get("skipped") else o_raw.get("t_plain_feature"),
            "t_clust": _t_of(o_raw, "%s/%s|raw" % (block_label, key)),
            "t_clust_wins": _t_of(o_win, "%s/%s|winsorized" % (block_label, key)),
        }
    return out


def full_battery(full: pl.DataFrame, label: str, out_lines: list):
    """The ORIGINAL Tests 2-4 verbatim (p1.test2_univariate / test3_ols /
    test4_stability), plus the original DESIGN.md bar application copied from
    osk_validation_phase2.gated_tests_2_4."""
    j = {}
    out_lines.append("\n%s\n=== FULL BATTERY (original Tests 2-4, verbatim) -- panel=%s ===\n%s\n"
                     % (_hr(), label, _hr()))
    n_b = full.filter(pl.col("split") == "backward_oos").height
    n_d = full.filter(pl.col("split") == "discovery").height
    n_o = full.filter(pl.col("split") == "other").height
    out_lines.append("  split counts: backward_oos=%d discovery=%d other=%d\n" % (n_b, n_d, n_o))
    for name, start, end in p1.ERAS:
        out_lines.append("    era=%s (%s..%s): N=%d\n" % (name, start, end,
                                                         full.filter(pl.col("era") == name).height))
    p1.test2_univariate(full, out_lines, j)
    p1.test3_ols(full, out_lines, j)
    p1.test4_stability(full, out_lines, j)

    # --- discovery cells (D2's scope), same functions ---
    out_lines.append("\n  --- DISCOVERY-WINDOW CELLS (D2 scope) ---\n")
    disc_univ, disc_ols = {}, {}
    disc = full.filter(pl.col("split") == "discovery")
    disc75 = disc.filter(pl.col("overall") >= 75)
    p1._univariate_one(disc, "discovery_full", out_lines, disc_univ)
    p1._univariate_one(disc75, "discovery_overall>=75", out_lines, disc_univ)
    p1._ols_scope(disc, "discovery_full", out_lines, disc_ols)
    p1._ols_scope(disc75, "discovery_overall>=75", out_lines, disc_ols)
    j["discovery_univariate"] = disc_univ
    j["discovery_ols"] = disc_ols

    # --- ORIGINAL DESIGN.md bar application (copied from phase2) ---
    b_ols = j.get("test3_ols", {}).get("backward_oos_pooled|raw", {})
    b_univ = j.get("test2_univariate", {}).get("backward_oos_pooled", {})
    sign_table = j.get("test4_stability", {}).get("sign_table", [])
    t_clust_bwd = _t_of(b_ols, "%s/backward_oos_pooled|raw" % label)
    spearman_bwd = b_univ.get("spearman_rho") if not b_univ.get("skipped") else None
    e1_e3_pos = sum(1 for r in sign_table if r.get("sign") == "+" and r["era"] in ("E1", "E2", "E3"))
    era_leq = []
    for name, _s, _e in p1.ERAS:
        eu = j.get("test2_univariate", {}).get("era_%s" % name, {})
        rho = eu.get("spearman_rho") if not eu.get("skipped") else None
        if rho is not None and rho <= -0.05:
            era_leq.append({"era": name, "spearman": rho, "n": eu.get("n")})
    any_era_leq = bool(era_leq)
    pass_bar = (t_clust_bwd is not None and spearman_bwd is not None
                and math.isfinite(t_clust_bwd)
                and t_clust_bwd >= 2.5 and spearman_bwd >= 0.05
                and e1_e3_pos >= 2 and not any_era_leq)
    weak_bar = (not pass_bar and t_clust_bwd is not None and spearman_bwd is not None
                and math.isfinite(t_clust_bwd)
                and t_clust_bwd >= 1.5 and (t_clust_bwd > 0) == (spearman_bwd > 0))
    bar_label = "PASS" if pass_bar else ("WEAK" if weak_bar else "KILL")
    out_lines.append(
        "\n  *** ORIGINAL DESIGN.md BAR APPLICATION (backward-OOS pooled decisive cell) ***\n"
        "    t_clust(skew)=%s  spearman=%s  E1-E3 positive-sign=%d/3  any era<=-0.05=%s\n"
        "    => %s\n" % (_fmt(t_clust_bwd, 3), _fmt(spearman_bwd), e1_e3_pos, any_era_leq, bar_label))
    j["bar_application"] = {"t_clust": t_clust_bwd, "spearman": spearman_bwd,
                            "e1_e3_positive_count": e1_e3_pos,
                            "any_era_leq_neg0.05": any_era_leq, "era_leq_detail": era_leq,
                            "bar_label": bar_label}
    return j


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_block_table(blocks: dict, out_lines: list, title: str):
    """blocks: {panel_label: headline_block dict}. Renders side by side."""
    labels = list(blocks.keys())
    out_lines.append("\n%s\n%s\n%s\n" % (_hr(), title, _hr()))
    hdr = "  %-34s" % "cell"
    for lb in labels:
        hdr += " | %-33s" % ("%s: N_uni   rho      t_clust" % lb[:9])
    out_lines.append(hdr + "\n")
    out_lines.append("  " + "-" * (34 + 36 * len(labels)) + "\n")
    for key, _desc in HEADLINE_SCOPES:
        row = "  %-34s" % key
        for lb in labels:
            c = blocks[lb]["cells"].get(key, {})
            row += " | %6s %8s %8s        " % (
                c.get("univ_n") if c.get("univ_n") is not None else "-",
                _fmt(c.get("spearman")), _fmt(c.get("t_clust"), 3))
        out_lines.append(row + "\n")
    if len(labels) == 2:
        a, b = labels
        out_lines.append("\n  deltas (%s minus %s):\n" % (b, a))
        for key, _desc in HEADLINE_SCOPES:
            ca = blocks[a]["cells"].get(key, {})
            cb = blocks[b]["cells"].get(key, {})
            dr = (cb.get("spearman") - ca.get("spearman")) if (
                ca.get("spearman") is not None and cb.get("spearman") is not None) else None
            dt = (cb.get("t_clust") - ca.get("t_clust")) if (
                ca.get("t_clust") is not None and cb.get("t_clust") is not None) else None
            out_lines.append("    %-34s d_rho=%9s  d_t=%9s   (N %s -> %s)\n"
                             % (key, _fmt(dr), _fmt(dt, 3), ca.get("univ_n"), cb.get("univ_n")))


def render_monotonicity(blocks: dict, out_lines: list):
    out_lines.append("\n  quintile monotonicity (DERIVED readout over the original's own quintile table)\n")
    for lb, blk in blocks.items():
        for key, _d in HEADLINE_SCOPES:
            m = blk["cells"].get(key, {}).get("monotonicity")
            if not m:
                continue
            out_lines.append("    [%s] %-34s up-steps %d/%d  q-rank rho=%s  means=%s\n"
                             % (lb, key, m["up_steps"], m["max_steps"], _fmt(m["q_rank_spearman"], 3),
                                "[" + ", ".join(_fmt(v, 3) for v in m["means"]) + "]"))


# ---------------------------------------------------------------------------
# Arms + strata
# ---------------------------------------------------------------------------
def stratum_filter(full: pl.DataFrame, stratum: str) -> pl.DataFrame:
    if stratum == "all":
        return full
    if stratum == "undrifted":
        return full.filter(pl.col("adj_factor_derived") <= DRIFT_THRESHOLD)
    if stratum == "drifted":
        return full.filter(pl.col("adj_factor_derived") > DRIFT_THRESHOLD)
    raise KeyError(stratum)


def ripe_shared_keys(corrected: pl.DataFrame, old: pl.DataFrame):
    """The ONLY like-for-like row set: (symbol,date) present in BOTH panels AND
    the corrected panel says fwd_bars >= 15 (i.e. the 15-bar outcome genuinely
    resolved). The old panel has no fwd_bars column -- it truncate-substituted
    instead of nulling -- so ripeness can only come from the corrected side."""
    if "fwd_bars" not in corrected.columns:
        return None
    keys = corrected.filter(pl.col("fwd_bars") >= 15).select(["symbol", "date"]).unique()
    keys = keys.join(old.select(["symbol", "date"]).unique(), on=["symbol", "date"], how="inner")
    return keys


# ---------------------------------------------------------------------------
# SELFTEST -- reproduce the published osk_validation numbers on the OLD panel
# ---------------------------------------------------------------------------
def run_selftest(out_lines: list):
    out_lines.append("\n%s\nSELFTEST -- reproduce experiments/osk_validation published numbers on the OLD panel\n%s\n"
                     % (_hr(), _hr()))
    old = load_panel_any(OLD_PANEL, "old")
    rs_slim = p1.load_rs_ledger_slim()
    full, _pm = build_analysis_panel(old, rs_slim, "old")
    j = full_battery(full, "old", out_lines)

    rows = []
    ok = True

    def cmp_corr(name, published, got, n_pub=None, n_got=None):
        nonlocal ok
        if got is None:
            rows.append((name, published, None, None, None, None, "MISSING"))
            ok = False
            return
        d = got - published
        status = "PASS" if abs(d) <= TOL_CORR else "DEVIATION"
        if n_pub is not None and n_got is not None and n_pub != n_got:
            status = ("N-MISMATCH" if status == "PASS" else status + "+N")
        if status != "PASS":
            ok = False
        rows.append((name, published, got, d, n_pub, n_got, status))

    def cmp_t(name, published, got, n_pub=None, n_got=None):
        nonlocal ok
        if got is None:
            rows.append((name, published, None, None, n_pub, n_got, "MISSING"))
            ok = False
            return
        if not math.isfinite(got):
            rows.append((name, published, got, None, n_pub, n_got, "NONFINITE"))
            ok = False
            return
        d = got - published
        status = "PASS" if abs(d) <= TOL_T else "DEVIATION"
        if n_pub is not None and n_got is not None and n_pub != n_got:
            status = ("N-MISMATCH" if status == "PASS" else status + "+N")
        if status != "PASS":
            ok = False
        rows.append((name, published, got, d, n_pub, n_got, status))

    t2 = j["test2_univariate"]
    t3 = j["test3_ols"]
    du = j["discovery_univariate"]
    do = j["discovery_ols"]

    for key, (n_pub, rho_pub) in PUBLISHED["univ"].items():
        src = du if key.startswith("discovery") else t2
        e = src.get(key, {})
        got = None if e.get("skipped") else e.get("spearman_rho")
        cmp_corr("spearman[%s]" % key, rho_pub, got, n_pub, e.get("n"))

    for key, tup in PUBLISHED["ols"].items():
        n_pub, beta_pub, tp_pub, tc_pub = tup
        src = do if key.startswith("discovery") else t3
        e = src.get(key, {})
        got_t = _t_of(e, "selftest/%s" % key)
        cmp_t("t_clust[%s]" % key, tc_pub, got_t, n_pub, e.get("n"))
        got_tp = None if e.get("skipped") else e.get("t_plain_feature")
        cmp_t("t_plain[%s]" % key, tp_pub, got_tp, n_pub, e.get("n"))
        got_b = None if e.get("skipped") else e.get("beta_feature")
        cmp_corr("beta[%s]" % key, beta_pub, got_b, n_pub, e.get("n"))

    # structural meta
    meta_rows = [
        ("panel_n", PUBLISHED["meta"]["panel_n"], full.height),
        ("split_backward_oos", PUBLISHED["meta"]["split_backward_oos"],
         full.filter(pl.col("split") == "backward_oos").height),
        ("split_discovery", PUBLISHED["meta"]["split_discovery"],
         full.filter(pl.col("split") == "discovery").height),
        ("e1_e3_positive_count", PUBLISHED["meta"]["e1_e3_positive_count"],
         j["test4_stability"]["e1_e3_positive_count"]),
        ("bar_label", PUBLISHED["meta"]["bar_label"], j["bar_application"]["bar_label"]),
    ]

    out_lines.append("\n%s\nSELFTEST REPRODUCTION TABLE (tolerance: corr %.3f, t %.2f; N must match exactly)\n%s\n"
                     % (_hr(), TOL_CORR, TOL_T, _hr()))
    out_lines.append("  %-46s %12s %12s %11s %8s %8s  %s\n"
                     % ("statistic", "published", "recomputed", "delta", "N_pub", "N_got", "status"))
    out_lines.append("  " + "-" * 112 + "\n")
    for (name, pub, got, d, n_pub, n_got, status) in rows:
        out_lines.append("  %-46s %12s %12s %11s %8s %8s  %s\n"
                         % (name, _fmt(pub, 5), _fmt(got, 5), _fmt(d, 6),
                            n_pub if n_pub is not None else "-",
                            n_got if n_got is not None else "-", status))
    out_lines.append("\n  structural checks:\n")
    for (name, pub, got) in meta_rows:
        st = "PASS" if pub == got else "DEVIATION"
        if st != "PASS":
            ok = False
        out_lines.append("  %-46s %12s %12s %11s %8s %8s  %s\n" % (name, pub, got, "-", "-", "-", st))

    out_lines.append("\n  brief-quoted (rounded) headlines vs recomputation:\n")
    for (nice, rounded, kind, key) in BRIEF_HEADLINES:
        if kind == "univ":
            src = du if key.startswith("discovery") else t2
            e = src.get(key, {})
            got = None if e.get("skipped") else e.get("spearman_rho")
            n = e.get("n")
        else:
            src = do if key.startswith("discovery") else t3
            e = src.get(key, {})
            got = _t_of(e, "brief/%s" % key)
            n = e.get("n")
        out_lines.append("    %-42s brief=%8s  recomputed=%9s  N=%s\n"
                         % (nice, _fmt(rounded, 3), _fmt(got, 4), n))

    if _NONFINITE_T:
        ok = False
        out_lines.append("\n  *** FATAL: non-finite clustered t at: %s ***\n" % _NONFINITE_T)

    out_lines.append("\n%s\n" % _hr())
    if ok:
        out_lines.append("SELFTEST RESULT: REPRODUCED. The harness recovers every published\n"
                         "osk_validation statistic on the OLD panel within tolerance, with exact N.\n"
                         "The re-read on the corrected panel is therefore interpretable.\n")
    else:
        out_lines.append("SELFTEST RESULT: *** FAILED TO REPRODUCE ***\n"
                         "STOP. The re-read would be meaningless: the recomputation does not match the\n"
                         "published osk_validation numbers, which means the mirrored statistic is NOT the\n"
                         "one that produced the kill. DO NOT 'fix' this by adjusting the statistic --\n"
                         "diagnose the definition mismatch against experiments/osk_validation/ first.\n")
    out_lines.append("%s\n" % _hr())
    return ok, j


# ---------------------------------------------------------------------------
# Main analysis path
# ---------------------------------------------------------------------------
def run_panels(panel_specs, out_lines: list, out_json: dict, allow_partial: bool):
    spot = load_spot_cache()
    adjclose = load_adjusted_close_cache()
    rs_slim = p1.load_rs_ledger_slim()

    # Load + validate EVERY panel before any statistic is computed, so an
    # incomplete corrected panel aborts the run rather than half-running it.
    raw, analysis = {}, {}
    for label, path in panel_specs:
        df = load_panel_any(path, label)
        if label != "old":
            check_corrected_completeness(df, allow_partial)
        raw[label] = df

    for label, path in panel_specs:
        df = raw[label]
        withadj = attach_adj_factor(df, spot, adjclose, label)
        analysis[label], pm = build_analysis_panel(withadj, rs_slim, label)
        out_json.setdefault("panels", {})[label] = {
            "path": path, "rows": df.height, "symbols": df["symbol"].n_unique(),
            "date_min": str(df["date"].min()), "date_max": str(df["date"].max()),
            "price_cache": pm,
            "pnl15_null_pct": round(100.0 * (df.height - df["pnl15"].drop_nulls().len()) / max(df.height, 1), 3),
            "has_fwd_bars": "fwd_bars" in df.columns,
        }

    # coverage summary
    out_lines.append("\n%s\nPANEL COVERAGE (why an N differs is as important as why a rho differs)\n%s\n"
                     % (_hr(), _hr()))
    out_lines.append("  %-12s %8s %8s %10s %12s %12s\n"
                     % ("panel", "rows", "symbols", "pnl15_null%", "skew_nonnull", "adj_resolved"))
    for label in analysis:
        df = raw[label]
        a = analysis[label]
        out_lines.append("  %-12s %8d %8d %9.1f%% %12d %12d\n"
                         % (label, df.height, df["symbol"].n_unique(),
                            100.0 * (df.height - df["pnl15"].drop_nulls().len()) / max(df.height, 1),
                            df["skew"].drop_nulls().len(),
                            a["adj_factor_derived"].drop_nulls().len()))
    if "corrected" in raw and "fwd_bars" in raw["corrected"].columns:
        c = raw["corrected"]
        out_lines.append("  corrected fwd_bars>=15: %d / %d (%.1f%%)\n"
                         % (c.filter(pl.col("fwd_bars") >= 15).height, c.height,
                            100.0 * c.filter(pl.col("fwd_bars") >= 15).height / max(c.height, 1)))

    # --- full battery per panel (native, all rows) ---
    for label in analysis:
        out_json.setdefault("full_battery", {})[label] = full_battery(analysis[label], label, out_lines)

    # --- arms x strata headline blocks ---
    arms = {"native": {lb: analysis[lb] for lb in analysis}}
    if "corrected" in analysis and "old" in analysis:
        keys = ripe_shared_keys(raw["corrected"], raw["old"])
        if keys is not None and keys.height:
            arms["ripe_shared"] = {lb: analysis[lb].join(keys, on=["symbol", "date"], how="semi")
                                   for lb in analysis}
            out_lines.append("\n  ripe_shared arm: %d shared (symbol,date) keys with corrected fwd_bars>=15\n"
                             % keys.height)
        else:
            out_lines.append("\n  ripe_shared arm: SKIPPED (no fwd_bars column or no shared ripe keys)\n")
    else:
        out_lines.append("\n  ripe_shared arm: SKIPPED (needs BOTH panels; it is the only like-for-like arm)\n")

    out_json["blocks"] = {}
    warn_lines = []
    undrifted_compared = False
    for arm, panels_in_arm in arms.items():
        for stratum in ("all", "undrifted", "drifted"):
            blocks = {}
            for lb, dfa in panels_in_arm.items():
                sub = stratum_filter(dfa, stratum)
                if sub.height == 0:
                    continue
                blocks[lb] = headline_block(sub, "%s/%s/%s" % (lb, arm, stratum), [])
            if not blocks:
                continue
            out_json["blocks"]["%s|%s" % (arm, stratum)] = blocks
            render_block_table(blocks, out_lines,
                               "ARM=%s   STRATUM=%s   (rows: %s)"
                               % (arm, stratum,
                                  ", ".join("%s=%d" % (k, v["rows"]) for k, v in blocks.items())))
            render_monotonicity(blocks, out_lines)
            # built-in correctness check
            if stratum == "undrifted" and len(blocks) == 2 and "old" in blocks and "corrected" in blocks:
                undrifted_compared = True
                for key, _d in HEADLINE_SCOPES:
                    ca = blocks["old"]["cells"].get(key, {})
                    cb = blocks["corrected"]["cells"].get(key, {})
                    if ca.get("spearman") is None or cb.get("spearman") is None:
                        continue
                    dr = abs(cb["spearman"] - ca["spearman"])
                    dt = (abs(cb["t_clust"] - ca["t_clust"])
                          if (ca.get("t_clust") is not None and cb.get("t_clust") is not None
                              and math.isfinite(ca["t_clust"]) and math.isfinite(cb["t_clust"])) else 0.0)
                    if dr > UNDRIFTED_RHO_WARN or dt > UNDRIFTED_T_WARN:
                        warn_lines.append(
                            "    [arm=%s] %-34s d_rho=%s d_t=%s  (bars: rho %.2f / t %.2f)"
                            % (arm, key, _fmt(dr), _fmt(dt, 3), UNDRIFTED_RHO_WARN, UNDRIFTED_T_WARN))

    if warn_lines:
        out_lines.append("\n%s\n*** LOUD WARNING: OLD and CORRECTED disagree on UNDRIFTED rows ***\n%s\n"
                         % (_hr("!"), _hr("!")))
        out_lines.append(("  On adj_factor <= %.3f rows the spot bug is inert, so the two panels should\n"
                         "  agree closely. They do not. Something OTHER than the spot bug differs --\n"
                         "  candidates: the pnl15 truncate-substitution (expected on arm=native, which is\n"
                         "  why arm=ripe_shared is the real check), a selection-rule drift in the rebuild,\n"
                         "  or a coverage difference. Diagnose before reading any old-vs-corrected delta.\n")
                         % DRIFT_THRESHOLD)
        out_lines.extend(ln + "\n" for ln in warn_lines)
        out_lines.append("%s\n" % _hr("!"))
        out_json["undrifted_disagreement_warnings"] = warn_lines
    elif undrifted_compared:
        out_lines.append("\n  undrifted-row agreement check: PASSED -- no cell exceeded the warn bars "
                         "(rho %.2f / t %.2f), so on rows where the spot bug is inert the two panels\n"
                         "  agree. Any old-vs-corrected delta on drifted rows is therefore attributable\n"
                         "  to the defect, not to an unrelated harness difference.\n"
                         % (UNDRIFTED_RHO_WARN, UNDRIFTED_T_WARN))
    else:
        out_lines.append("\n  undrifted-row agreement check: NOT APPLICABLE -- it needs BOTH panels.\n"
                         "  (Single-panel run: the undrifted/drifted split below is descriptive of that\n"
                         "  panel only and is NOT evidence about the corrected panel.)\n")
    out_json["undrifted_agreement_checked"] = undrifted_compared
    return out_json


def header_lines():
    L = []
    L.append("=== osk_reread.py -- OSK validation statistics re-read (old vs corrected panel) ===\n")
    L.append("run at: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("holdout cutoff (live, strategy_config.CALIBRATION_CUTOFF_DATE): %s\n" % HOLDOUT_CUTOFF.isoformat())
    L.append("\n" + _hr() + "\nBARS -- UNCHANGED, PRINTED NOT RE-DERIVED\n" + _hr() + "\n")
    for ln in P2_4_BAR_TEXT:
        L.append(ln + "\n")
    L.append("\n")
    for ln in ORIGINAL_DESIGN_BARS:
        L.append(ln + "\n")
    L.append("\n" + _hr() + "\nWHAT IS MIRRORED (and what was inferred)\n" + _hr() + "\n")
    for ln in [
        "  * Every statistic is the ORIGINAL code: this module imports",
        "    experiments/osk_validation/osk_validation_test.py and calls its",
        "    _univariate_one / _ols_scope / run_ols / ols_with_clustered_se /",
        "    build_full_panel / compute_semivol_r / test2..test4 directly. Nothing is",
        "    re-implemented, so the skew definition, N floor (MIN_CELL_N=30), quintile",
        "    edges, z-scoring, 1% winsorization and the date-clustered sandwich SE are",
        "    identical by construction.",
        "  * SKEW = the panel's stored `skew` column (Polygon otm_put_iv - otm_call_iv).",
        "    The original never recomputed it (FEATURE_COL='skew'); neither does this.",
        "  * '70+' is the UNIVERSE, not a cut: both panels are built from rs_ledger",
        "    overall>=70, so full_pooled / discovery_full ARE the 70+ cells. The original",
        "    code only ever cut explicitly at overall>=75. INFERRED, stated here.",
        "  * '2022 bear tail' = pre-registered era E1 (2022-08-01..2022-12-31).",
        "  * Splits: backward-OOS 2022-08-01..2025-02-10, discovery 2025-02-11..2026-05-15.",
        "  * Discovery cells here are the split=='discovery' slice of the one full panel;",
        "    phase2 built a separate discovery frame. stock_r20 and semivol_r are row-local,",
        "    so the two are numerically identical -- the selftest proves it against the",
        "    published discovery numbers (N=3618/3617).",
        "  * HOLDOUT: identical treatment to the original -- assert_no_holdout_leak() is",
        "    called on every frame, and NOTHING is filtered. The discovery window is",
        "    calibration-era and was used for continuity/measurement-robustness only, never",
        "    as validation evidence; the backward-OOS window is the validation. Both sit",
        "    entirely at or before the cutoff, so the guard passes without truncation.",
        "  * The 1%-winsorized OLS is reported alongside and NEVER gated (original rule).",
        "  * adj_factor = as_traded_close / price_history.close, derived from the two",
        "    offline caches for BOTH panels so the strata are row-identical. This",
        "    reproduces PANEL_AUDIT.md exactly (8,529 resolved of 8,643; 2,679 drifted =",
        "    31.4%; >1.02 1,389; >1.05 727; >1.10 310; min 0.9616 max 1.5221 mean 1.0139).",
        "  * pnl15 nulls are NEVER filled. N is printed for every statistic so the power",
        "    difference between panels is visible; arm=ripe_shared is the only",
        "    like-for-like comparison (rows in both panels with corrected fwd_bars>=15).",
        "  * Quintile monotonicity is the original's own quintile table; the up-step /",
        "    rank-correlation summary line is a DERIVED readability aid, labelled as such.",
    ]:
        L.append(ln + "\n")
    return L


def main():
    ap = argparse.ArgumentParser(description="Re-read the OSK validation statistics on either panel.")
    ap.add_argument("--panel", choices=["old", "corrected"], default="old")
    ap.add_argument("--panel-path", default=None, help="explicit parquet path (overrides --panel)")
    ap.add_argument("--compare", action="store_true", help="force old-vs-corrected side by side")
    ap.add_argument("--no-compare", action="store_true", help="run the selected panel alone")
    ap.add_argument("--selftest", action="store_true",
                    help="reproduce the published osk_validation numbers on the OLD panel; exit 1 on failure")
    ap.add_argument("--allow-partial", action="store_true",
                    help="permit an incomplete corrected panel (default: refuse)")
    ap.add_argument("--out-prefix", default=None)
    args = ap.parse_args()

    t0 = time.time()
    out_lines = header_lines()
    out_json = {"meta": {"run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
                         "p2_4_bar": " ".join(P2_4_BAR_TEXT[:4]),
                         "tolerances": {"corr": TOL_CORR, "t": TOL_T}}}
    for ln in out_lines:
        log(ln.rstrip("\n"))

    if args.selftest:
        ok, j = run_selftest(out_lines)
        out_json["selftest"] = {"reproduced": bool(ok), "battery": j}
        prefix = args.out_prefix or os.path.join(OUT_DIR, "osk_reread_selftest")
        exit_code = 0 if ok else 1
    else:
        specs = []
        if args.panel_path:
            specs.append(("custom", args.panel_path))
            if not args.no_compare:
                specs.append(("old", OLD_PANEL))
        elif args.panel == "corrected":
            specs.append(("corrected", CORRECTED_PANEL))
            if not args.no_compare:
                specs.append(("old", OLD_PANEL))
        else:
            specs.append(("old", OLD_PANEL))
            if args.compare and os.path.exists(CORRECTED_PANEL):
                specs.append(("corrected", CORRECTED_PANEL))
        # keep a stable order: old first, then the other panel
        specs = sorted(specs, key=lambda s: 0 if s[0] == "old" else 1)
        run_panels(specs, out_lines, out_json, args.allow_partial)
        prefix = args.out_prefix or os.path.join(
            OUT_DIR, "osk_reread_%s" % ("_".join(lb for lb, _ in specs)))
        exit_code = 0

    if _NONFINITE_T:
        banner = ("\n%s\n*** FATAL: NON-FINITE clustered t reported at: %s ***\n"
                  "A NaN t silently reads as a null result and would fake a 'still killed'\n"
                  "verdict. Treat this run as INVALID.\n%s\n"
                  % (_hr("!"), sorted(set(_NONFINITE_T)), _hr("!")))
        out_lines.append(banner)
        out_json["nonfinite_t"] = sorted(set(_NONFINITE_T))
        exit_code = 2

    out_lines.append("\n=== DONE in %.1fs ===\n" % (time.time() - t0))
    with open(prefix + ".txt", "w", encoding="ascii", errors="replace") as f:
        f.writelines(out_lines)
    with open(prefix + ".json", "w", encoding="ascii") as f:
        json.dump(out_json, f, indent=2, default=str)

    # echo the report body (the header was already echoed above)
    for ln in out_lines[len(header_lines()):]:
        log(ln.rstrip("\n"))
    log("\nwrote %s.txt" % prefix)
    log("wrote %s.json" % prefix)
    log("=== DONE in %.1fs (exit %d) ===" % (time.time() - t0, exit_code))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
