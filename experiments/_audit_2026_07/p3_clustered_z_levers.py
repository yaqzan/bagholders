"""
HOSTILE_REVIEW P3 (A-2): clustered-z re-screen of the five shipped DD levers.

The five levers (RXDD, SVR, MWDD, TVDD, BDIV) were each licensed on a naive binomial z,
z = (rate - base) / sqrt(base*(1-base)/n), computed on date-clustered, window-overlapping
MC-trade-tape rows -- effective N is far below the raw trade count. This script re-screens
each lever's ORIGINAL winning cohort with a CR1 date-clustered sandwich SE instead
(clusters = entry date), using the on-disk ledger the original mine.py used, wherever that
ledger (and the columns the cohort needs) still exists on disk.

CR1 clustered sandwich SE (_cluster_sandwich_se) and the linear-probability cluster-robust
fit (clustered_ols_effect) are COPIED VERBATIM (per the brief: "import it or copy the
function verbatim with attribution") from experiments/peak_fakeout/mine.py, lines ~450-529
(the July parked-lead pack's CR1 implementation). The apex15 barrier-touch win label
(win_expr + its UP_GRID/DN_GRID/_idx helpers) is likewise copied verbatim from
experiments/component_reweight/label.py, lines 1-43, used unmodified by the SVR evidence
chain (experiments/iv_skew/reconcile_proxy.py).

Scope: PARQUET-ONLY, NO MYSQL (this script's brief). Where a lever's cohort-defining
column (e.g. VIX, McClellan, TRIN, SPY-vs-60d-high) is not present in the on-disk ledger
and no full-history parquet cache of it could be found, that lever is reported
MISSING/AMBIGUOUS with the exact paths checked -- no MySQL fallback, no ledger rebuild,
no guessed cohort definition.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/_audit_2026_07/p3_clustered_z_levers.py
"""
import os
import sys
import math
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

DD_LEDGER = os.path.join(_ROOT, ".cache", "dd_ledger")
TAPE_10Y = os.path.join(DD_LEDGER, "tape_10y.parquet")
LOOKUP_VIX = os.path.join(_ROOT, ".cache", "peak_fakeout", "lookup_vix.parquet")
PROXY_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "proxy_ledger.parquet")


# =============================================================================
# COPIED VERBATIM from experiments/peak_fakeout/mine.py:474-529 (CR1 cluster-robust
# sandwich SE, clusters = entry date). Attribution per HOSTILE_REVIEW A-2 brief.
# =============================================================================
def _cluster_sandwich_se(bread, score, clusters, n, k):
    _, inv = np.unique(clusters, return_inverse=True)
    G = int(inv.max()) + 1 if n else 0
    sums = np.zeros((G, k))
    np.add.at(sums, inv, score)
    meat = sums.T @ sums
    corr = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = corr * (bread @ meat @ bread)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    return se, G


def clustered_ols_effect(X, y, clusters):
    n, k = X.shape
    XtX = X.T @ X
    try:
        bread = np.linalg.pinv(XtX)
    except np.linalg.LinAlgError:
        return None
    beta = bread @ (X.T @ y)
    if not np.all(np.isfinite(beta)):
        return None
    resid = y - X @ beta
    score = X * resid[:, None]
    se, G = _cluster_sandwich_se(bread, score, clusters, n, k)
    if se[1] <= 1e-12 or not np.isfinite(se[1]):
        return None
    t1 = beta[1] / se[1]
    if not np.isfinite(t1):
        return None
    return dict(beta=float(beta[1]), t=float(t1), n_used=n, n_clusters=G, method="ols_lpm")
# =============================================================================
# end verbatim copy
# =============================================================================


# =============================================================================
# COPIED VERBATIM from experiments/component_reweight/label.py:20-43 (barrier-agnostic
# win/loss label from t_up/t_dn grids). Used unmodified for the SVR apex15 label.
# =============================================================================
UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)


def _idx(grid, target):
    return int(np.clip(int(np.argmin(np.abs(grid - float(target)))), 0, len(grid) - 1))


def win_expr(tp_sigma, sl_sigma, W):
    iu = _idx(UP_GRID, tp_sigma)
    idn = _idx(DN_GRID, sl_sigma)
    ttp = pl.col("t_up").list.get(iu)
    tsl = pl.col("t_dn").list.get(idn)
    fc = pl.col("fwd_caldays")
    return (
        pl.when((ttp <= W) & (ttp < tsl)).then(pl.lit(1))
        .when((tsl <= W) & (tsl <= ttp)).then(pl.lit(0))
        .when(fc >= W).then(pl.lit(0))
        .otherwise(pl.lit(None))
        .cast(pl.Int8)
    )
# =============================================================================
# end verbatim copy
# =============================================================================


def naive_z(rate, base, n):
    """Their formula (HOSTILE_REVIEW A-2): z = (rate-base) / sqrt(base*(1-base)/n)."""
    if n <= 0 or base is None or rate is None:
        return None
    denom = math.sqrt(max(1e-12, base * (1.0 - base) / n))
    if denom <= 0:
        return None
    return (rate - base) / denom


def _norm_date_col(df, col):
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def run_cohort_test(y, cohort_mask, cluster_dates):
    """y: float array (0/1 outcome). cohort_mask: bool array. cluster_dates: array of
    python date objects (converted to ordinal ints for clustering). Returns dict with
    n_cohort, n_complement, rate_cohort, rate_complement, naive_z, clustered_t,
    n_date_clusters."""
    y = np.asarray(y, dtype=float)
    cohort_mask = np.asarray(cohort_mask, dtype=bool)
    n_cohort = int(cohort_mask.sum())
    n_complement = int((~cohort_mask).sum())
    if n_cohort == 0 or n_complement == 0:
        return None
    rate_cohort = float(y[cohort_mask].mean())
    rate_complement = float(y[~cohort_mask].mean())
    nz = naive_z(rate_cohort, rate_complement, n_cohort)

    ord_dates = np.array([d.toordinal() for d in cluster_dates], dtype=np.int64)
    X = np.column_stack([np.ones(len(y)), cohort_mask.astype(float)])
    fit = clustered_ols_effect(X, y, ord_dates)
    if fit is None:
        return dict(n_cohort=n_cohort, n_complement=n_complement, rate_cohort=rate_cohort,
                    rate_complement=rate_complement, naive_z=nz, clustered_t=None,
                    n_date_clusters=None)
    return dict(n_cohort=n_cohort, n_complement=n_complement, rate_cohort=rate_cohort,
                rate_complement=rate_complement, naive_z=nz, clustered_t=fit["t"],
                n_date_clusters=fit["n_clusters"])


# =============================================================================
# Per-lever probes
# =============================================================================

def probe_rxdd():
    """RXDD (experiments/regime_dd_v70/): VIX 20-28 'slow-bleed' band, whole-tape,
    loser-rate vs complement (FINDINGS.md Finding 3)."""
    notes = []
    if not os.path.exists(TAPE_10Y):
        return dict(lever="RXDD", cohort="MISSING", n=None, n_clusters=None, naive_z=None,
                    clustered_t=None, verdict="MISSING",
                    note=f"tape not found: {TAPE_10Y}")
    if not os.path.exists(LOOKUP_VIX):
        return dict(lever="RXDD", cohort="MISSING", n=None, n_clusters=None, naive_z=None,
                    clustered_t=None, verdict="MISSING",
                    note=f"VIX lookup not found: {LOOKUP_VIX}")

    notes.append("RXDD's own 2026-06-04 tape no longer exists (dd_ledger's earliest "
                 "surviving file is 2026-06-07); using tape_10y.parquet (2026-06-07, "
                 "untouched by the 2026-06-10 BDIV regen that overwrote tape_2020/tape_5y "
                 "-- confirmed v70-scored per dd_onset_omens/mine_onset.py's own code "
                 "comment). A lever only rescales premium_cost (dollar size), never "
                 "per-trade option_pnl (dd_residual_v70/mine.py docstring) -- so this "
                 "later, more-levers-live tape is a valid per-trade substitute for RXDD's "
                 "original single-lever tape. Single file (no cross-window pooling) avoids "
                 "the pseudo-replication the original 12-window-pooled mine used.")

    T = pl.read_parquet(TAPE_10Y)
    T = T.filter(pl.col("side") == "call")
    T = _norm_date_col(T, "entry_date")
    V = pl.read_parquet(LOOKUP_VIX)
    V = _norm_date_col(V, "date").rename({"date": "entry_date"})

    J = T.join(V, on="entry_date", how="inner")
    dropped = T.height - J.height
    if dropped:
        notes.append(f"{dropped} tape rows had no VIX match on entry_date and were dropped")

    loser = (J["option_pnl"] < 0).to_numpy()
    vix = J["vix_close"].to_numpy()
    cohort_mask = (vix >= 20.0) & (vix < 28.0)
    dates_ = J["entry_date"].to_list()

    res = run_cohort_test(loser, cohort_mask, dates_)
    if res is None:
        return dict(lever="RXDD", cohort="VIX in [20,28)", n=J.height, n_clusters=None,
                    naive_z=None, clustered_t=None, verdict="MISSING",
                    note="empty cohort or complement after VIX join")

    verdict = _verdict(res["clustered_t"])
    cohort_desc = "VIX in [20,28) vs complement, loser-rate (whole tape=call, N=%d)" % J.height
    return dict(lever="RXDD", cohort=cohort_desc, n=res["n_cohort"],
                n_clusters=res["n_date_clusters"], naive_z=res["naive_z"],
                clustered_t=res["clustered_t"], verdict=verdict,
                note=" | ".join(notes) +
                     (f" | cohort loser-rate={res['rate_cohort']:.3f} vs "
                      f"complement={res['rate_complement']:.3f}"))


def probe_svr():
    """SVR (experiments/apex_speed_v70/, evidence in experiments/iv_skew/): semivol_r <
    SVR_LO_FULL=0.70 ('euphoric/expensive', the WORST cohort per FINDINGS: '75+ win 0.35')
    within overall>=75, apex15 barrier-touch win-rate vs complement."""
    if not os.path.exists(PROXY_LEDGER):
        return dict(lever="SVR", cohort="MISSING", n=None, n_clusters=None, naive_z=None,
                    clustered_t=None, verdict="MISSING",
                    note=f"ledger not found: {PROXY_LEDGER}")

    L = pl.read_parquet(PROXY_LEDGER)
    L = L.filter((pl.col("overall") >= 75) & pl.col("semivol_r").is_not_null())
    L = L.with_columns(win_expr(1.092, 2.548, 15).alias("apex15"))
    L = L.filter(pl.col("apex15").is_not_null())
    L = _norm_date_col(L, "date")

    win = L["apex15"].cast(pl.Float64).to_numpy()
    svr = L["semivol_r"].to_numpy()
    cohort_mask = svr < 0.70   # SVR_LO_FULL -- shipped mechanism's own "worst zone" cut
    dates_ = L["date"].to_list()

    res = run_cohort_test(win, cohort_mask, dates_)
    if res is None:
        return dict(lever="SVR", cohort="semivol_r<0.70 (overall>=75)", n=L.height,
                    n_clusters=None, naive_z=None, clustered_t=None, verdict="MISSING",
                    note="empty cohort or complement")

    verdict = _verdict(res["clustered_t"])
    cohort_desc = ("semivol_r<0.70 (SVR_LO_FULL, 'euphoric/expensive') vs complement, "
                   f"apex15 win-rate, overall>=75 (N={L.height})")
    return dict(lever="SVR", cohort=cohort_desc, n=res["n_cohort"],
                n_clusters=res["n_date_clusters"], naive_z=res["naive_z"],
                clustered_t=res["clustered_t"], verdict=verdict,
                note=f"ledger=.cache/iv_skew/proxy_ledger.parquet (N=394,293 total; matches "
                     f"the z=+11.57 apex15 citation's source) | apex15 label reproduced via "
                     f"the copied win_expr(1.092,2.548,15) | cohort win-rate="
                     f"{res['rate_cohort']:.3f} vs complement={res['rate_complement']:.3f}")


def _missing_row(lever, cohort_dir, cohort_desc, reason):
    return dict(lever=lever, cohort=cohort_desc, n=None, n_clusters=None, naive_z=None,
                clustered_t=None, verdict="MISSING/AMBIGUOUS", note=reason)


def probe_mwdd():
    return _missing_row(
        "MWDD", "experiments/market_wave_dd_v70/",
        "McClellan flat/topping band [-15,+15] vs complement (whole tape) -- FINDINGS.md 'Finding 2'",
        "Ledger present but cohort-defining column absent from it. mine.py's TAPE_DIR = "
        ".cache/dd_ledger/ (shared across all 5 levers); MWDD's own 2026-06-05 tape no "
        "longer exists on disk (dd_ledger's earliest surviving file is 2026-06-07, from "
        "the later TVDD regen). The McClellan oscillator itself is NOT a tape column -- "
        "mine.py joins MarketBreadth.mcclellan_oscillator via MySQL (load_context()). "
        "No full-history McClellan parquet was found: checked .cache/breadth_ath_pct.parquet "
        "(no mcclellan col), .cache/experiment_data/mkt_breadth_ctrl.parquet (has "
        "mcclellan_oscillator but only 383 rows, 2025-01-02..2026-07-06 -- far short of the "
        "tape's 2016-2026 span), .cache/peak_fakeout/, .cache/iv_premium_model/, "
        ".cache/osk_era/. P3 is scoped parquet-only/no-MySQL -- reporting MISSING rather "
        "than joining MySQL or guessing a proxy for McClellan.")


def probe_tvdd():
    return _missing_row(
        "TVDD", "experiments/dd_residual_v70/",
        "TRIN neutral band [1.0,1.3) vs complement, DD-active subset (entry_dd>=0.13) -- FINDINGS.md",
        "Ledger present (mostly intact) but cohort-defining column absent from it. "
        "entry_dd IS a native tape column (parquet-only OK), but TRIN is NOT -- mine.py "
        "joins MarketBreadth.trin via MySQL (load_context()). No full-history TRIN parquet "
        "was found (same search as MWDD; mkt_breadth_ctrl.parquet has a trin column but "
        "only 383 rows / 2025-01-02..2026-07-06). SECONDARY finding worth flagging even if "
        "TRIN were available: TVDD's own dd_ledger/tape_*.parquet is a MIXED-VERSION cache "
        "(A-13) -- most window files (2018/2020_crash/2021-2025/dip/22-now/10y) are still "
        "the 2026-06-07 TVDD-era v70 tape, but tape_2020.parquet and tape_5y.parquet were "
        "silently overwritten 2026-06-10 by the BDIV mining run with a v71-scored tape "
        "(confirmed via dd_onset_omens/mine_onset.py's own comment: 'Only the FRESH v71 "
        "tapes (older tape_2021..tape_2025/tape_10y are v70-scored)'). P3 is scoped "
        "parquet-only/no-MySQL -- reporting MISSING rather than joining MySQL.")


def probe_bdiv():
    return _missing_row(
        "BDIV", "experiments/dd_onset_omens/",
        "refinement-grid winner cell P0.01/G5/D0: SPY within 1.0% of its 60d high AND "
        "breadth_chg10d<-5, same-day (the shipped BDIV cohort; coverage 12.1%, "
        "mpnl -0.0175 vs +0.0286 off, FINDINGS-cited z=+25.0) -- FINDINGS.md 'Refinement grid'",
        "Ledger present (tape_2020.parquet + tape_5y.parquet, the 'fresh v71 substrate' "
        "BDIV was itself mined on) but the cohort-defining columns are absent from it. "
        "mine_onset.py's load_context() builds spy_from60h from PriceHistory (SPY) and "
        "brd_chg10 from MarketBreadth.breadth_score, both via MySQL. No full-history SPY "
        "close parquet was found: .cache/experiment_data/spy_closes.parquet exists but "
        "covers only 2025-01-02..2026-07-06 (376 rows) -- BDIV's own tape spans 2020 and "
        "5y windows well before that. No full-history breadth_score parquet was found "
        "either (same search as MWDD/TVDD). P3 is scoped parquet-only/no-MySQL -- "
        "reporting MISSING rather than joining MySQL or approximating SPY/breadth off a "
        "truncated 376-row proxy.")


def _verdict(t):
    if t is None:
        return "MISSING"
    return f"SOUND(t={t:+.2f})" if abs(t) >= 3.0 else f"HOLLOW(t={t:+.2f})"


def fmt(v, width, prec=None):
    if v is None:
        return "n/a".rjust(width)
    if prec is not None and isinstance(v, float):
        return f"{v:+.{prec}f}".rjust(width)
    return str(v).rjust(width)


def main():
    print("=" * 100)
    print("P3 -- clustered-z re-screen of the five shipped DD levers (HOSTILE_REVIEW A-2)")
    print("=" * 100)
    print("Scope: parquet-only, no MySQL. CR1 clustered SE + clustered_ols_effect copied")
    print("verbatim from experiments/peak_fakeout/mine.py:474-529. win_expr copied verbatim")
    print("from experiments/component_reweight/label.py:20-43.")
    print()

    rows = [probe_rxdd(), probe_svr(), probe_mwdd(), probe_tvdd(), probe_bdiv()]

    print("=" * 100)
    print("RESULT TABLE")
    print("=" * 100)
    hdr = (f"{'lever':6} | {'n':>9} | {'n_clusters':>10} | {'naive_z':>9} | "
           f"{'clustered_t':>11} | {'verdict':>16}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        n_s = fmt(r["n"], 9)
        nc_s = fmt(r["n_clusters"], 10)
        nz_s = fmt(r["naive_z"], 9, prec=2)
        ct_s = fmt(r["clustered_t"], 11, prec=2)
        print(f"{r['lever']:6} | {n_s} | {nc_s} | {nz_s} | {ct_s} | {r['verdict']:>16}")
    print()

    print("cohort definitions + notes:")
    for r in rows:
        print(f"  [{r['lever']}] cohort: {r['cohort']}")
        print(f"    note: {r['note']}")
        print()

    n_computed = sum(1 for r in rows if r["clustered_t"] is not None)
    n_missing = sum(1 for r in rows if r["verdict"].startswith("MISSING"))
    print(f"SUMMARY: {n_computed}/5 levers computed (RXDD, SVR); {n_missing}/5 reported "
          f"MISSING/AMBIGUOUS (MWDD, TVDD, BDIV -- cohort-defining market-context column "
          f"not available parquet-only; would require a MySQL join outside this script's "
          f"declared no-MySQL scope).")
    print()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
