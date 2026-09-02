"""
analyze.py -- weekly_5dte_movers, Stage C (STAGE_C_BRIEF.md).

Mining + ablation harness for PREREG.md sections A-F. Reads the pre-built Stage B
analysis frame (one row per (contract, entry_date) entry event, ~170 columns) and
runs: A census, B base rates, C univariate screens, D factor abstraction (Spearman
clustering + logistic/GBT AUC), E ablation (leave-family-out, leave-metric-out, rule
distillation + robustness grid).

Binding spec: PREREG.md (LOCKED) + STAGE_C_BRIEF.md. Read those first.

CLI:
    python analyze.py --smoke   # <=13.5k-row smoke parquet only; prints self-test block
    python analyze.py --full    # unions features/analysis_*.parquet; writes out/ + RESULTS_TABLES.md

Never reads features/analysis_*.parquet in --smoke mode (hard rule, may be absent/
partial); never reads the _smoke file in --full mode.
"""
from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from scipy.stats import rankdata  # noqa: E402
from scipy.optimize import minimize as _scipy_minimize  # noqa: E402

from experiments._holdout import assert_no_holdout_leak  # noqa: E402

try:
    import sklearn  # noqa: F401
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    SKLEARN_AVAILABLE = True
    SKLEARN_VERSION = sklearn.__version__
except ImportError:
    SKLEARN_AVAILABLE = False
    SKLEARN_VERSION = None

SEED = 20260817

# ---------------------------------------------------------------- paths (independent
# of build_ledger.py/build_features.py on purpose -- analyze.py is a pure downstream
# reader of the already-materialized analysis frame and never computes indicators, so
# it does not need talib or ff_common; paths are the literal ones STAGE_C_BRIEF.md and
# BUILD_REPORT.md give).

DERIVED_ROOT = Path("B:/polygon_derived")
OUT_ROOT = DERIVED_ROOT / "weekly_5dte_movers"
FEATURES_ROOT = OUT_ROOT / "features"
SMOKE_ANALYSIS_PARQUET = FEATURES_ROOT / "_smoke" / "analysis_smoke.parquet"
FULL_OUT_DIR = OUT_ROOT / "out"

REPO_RESULTS_TABLES_MD = os.path.join(_HERE, "RESULTS_TABLES.md")

# ---------------------------------------------------------------- metric family map
# (exact column names from STAGE_C_BRIEF.md "Metric families (column mapping)")

MA_PERIODS = [5, 8, 9, 10, 12, 20, 21, 26, 34, 50, 100, 150, 200]

_F4_MA_NUMERIC: list = []
for _p in MA_PERIODS:
    _F4_MA_NUMERIC += [f"sma_{_p}_pxrel", f"ema_{_p}_pxrel", f"sma_{_p}_slope5d", f"ema_{_p}_slope5d"]
_F4_MA_NUMERIC += ["ma_stack_count", "days_since_50_200_cross"]

F4_CROSS_BOOL = [
    "cross_sma5_gt_sma20", "cross_sma10_gt_sma50", "cross_sma20_gt_sma50",
    "cross_sma50_gt_sma200", "cross_ema12_gt_ema26",
]

FAMILIES_NUMERIC = {
    "F1": ["otm_pct", "moneyness_pct", "premium_over_spot", "entry_close", "strike",
           "dte_calendar", "dte_trading"],
    "F2": ["entry_volume", "entry_transactions", "entry_dollar_vol", "hl_range_pct",
           "close_vs_open_pct"],
    "F3": ["ret_1d", "ret_2d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d",
           "gap_1d", "realized_vol_5d", "realized_vol_10d", "realized_vol_20d",
           "realized_vol_60d", "atr14_pct", "dist_from_20d_high", "dist_from_20d_low",
           "dist_from_52w_high", "dist_from_52w_low", "dollar_vol_20d_avg",
           "dollar_vol_ratio", "prior_week_return"],
    "F4": _F4_MA_NUMERIC,
    "F5": ["rsi", "macd_hist", "stoch", "bb_pctb"],
    "F6": ["overall", "pre_regime", "pre_boost", "score_bb", "score_trend",
           "score_volume", "score_rsi", "score_macd", "score_stoch", "score_ma20",
           "score_technical_alignment"],
    "F7": ["regime_composite", "regime_multiplier", "vix_close", "mcclellan_oscillator",
           "trin", "ad_diff", "spy_ret_5d", "spy_ret_20d"],
    "F8": ["days_to_next_earnings", "days_since_last_earnings"],
}

FAMILIES_CATEGORICAL = {
    "F1": ["is_monthly_opex", "entry_dow"],
    "F4": list(F4_CROSS_BOOL),
    "F6": ["score_bucket"],
    "F8": ["earnings_in_window", "sector", "mcap_snapshot_bucket", "index_or_etf_underlying"],
}

ALL_NUMERIC_METRICS = [c for fam in FAMILIES_NUMERIC.values() for c in fam]
ALL_CATEGORICAL_METRICS = [c for fam in FAMILIES_CATEGORICAL.values() for c in fam]

METRIC_FAMILY_OF: dict = {}
for _fam, _cols in FAMILIES_NUMERIC.items():
    for _c in _cols:
        METRIC_FAMILY_OF[_c] = _fam
for _fam, _cols in FAMILIES_CATEGORICAL.items():
    for _c in _cols:
        METRIC_FAMILY_OF[_c] = _fam

TOTAL_METRICS_TESTED = len(ALL_NUMERIC_METRICS) + len(ALL_CATEGORICAL_METRICS)

WINNER_THRESHOLDS = (3.0, 5.0, 10.0)
PRIMARY_THRESHOLD = 5.0
MIN_CELL_N = 200
THIN_COVERAGE_NULL_FRAC = 0.40
TOP100_COLS = ["ticker", "underlying", "cp", "strike", "entry_date", "entry_dow",
               "entry_close", "entry_volume", "growth_mult", "max_high_date",
               "earnings_in_window", "otm_pct", "overall"]
N_PERMUTATIONS_SMALL = 100
N_PERMUTATIONS_LARGE = 10
LARGE_DATA_ROWS = 50_000
ABLATION_MAX_ROWS = 400_000
CORR_CLUSTER_THRESHOLD = 0.8
CORR_MIN_PERIODS = 30
MIN_TRAIN_DISTINCT_DATES = 15
EPV_PER_FEATURE = 2
CORR_MAX_ROWS = 300_000
RULE_MIN_N = 1000
RULE_MAX_CONJUNCTS = 4
RULE_TOP_K_ATOMS = 6
RULE_MAX_SELECTED = 6


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ================================================================== load + population

def _rename_for_holdout(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(pl.col("entry_date").alias("date"))


def fill_nan_to_null(df: pl.DataFrame) -> pl.DataFrame:
    """Null policy (STAGE_C_BRIEF traps): fill_nan -> null ONCE at load, so no
    downstream .rank() call ever sees a real NaN (which polars ranks as MAX)."""
    float_cols = [c for c, t in zip(df.columns, df.dtypes) if t in (pl.Float32, pl.Float64)]
    if not float_cols:
        return df
    return df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])


def load_smoke_frame() -> pl.DataFrame:
    if not SMOKE_ANALYSIS_PARQUET.exists():
        raise FileNotFoundError(f"smoke analysis parquet not found: {SMOKE_ANALYSIS_PARQUET}")
    df = pl.read_parquet(SMOKE_ANALYSIS_PARQUET)
    assert_no_holdout_leak(_rename_for_holdout(df), context="analyze.py --smoke load")
    return fill_nan_to_null(df)


def load_full_frame() -> pl.DataFrame:
    paths = sorted(FEATURES_ROOT.glob("analysis_*.parquet"))
    paths = [p for p in paths if "_smoke" not in p.parts]
    if not paths:
        raise FileNotFoundError(f"no analysis_*.parquet files found under {FEATURES_ROOT}")
    log(f"loading {len(paths)} full-run parquet file(s): {[p.name for p in paths]}")
    frames = [pl.read_parquet(p) for p in paths]
    df = pl.concat(frames, how="vertical_relaxed")
    assert_no_holdout_leak(_rename_for_holdout(df), context="analyze.py --full load")
    return fill_nan_to_null(df)


def add_population_flags(df: pl.DataFrame) -> pl.DataFrame:
    """Population definitions per STAGE_C_BRIEF.md exactly:
    tradeable = entry_close>=0.20 AND entry_volume>=100 AND entry_transactions>=10
                AND adjusted==False (standard OCC root)
    analysis_population = covered==1 AND no_later_print==0 AND tradeable
    covered_only_population = covered==1 AND no_later_print==0 (the "raw incl
        covered-only" floor variant used by E3's robustness grid)."""
    tradeable = (
        (pl.col("entry_close") >= 0.20)
        & (pl.col("entry_volume") >= 100)
        & (pl.col("entry_transactions") >= 10)
        & (~pl.col("adjusted"))
    )
    df = df.with_columns(tradeable.alias("tradeable"))
    df = df.with_columns(
        ((pl.col("covered") == 1) & (pl.col("no_later_print") == 0) & pl.col("tradeable"))
        .alias("analysis_population")
    )
    df = df.with_columns(
        ((pl.col("covered") == 1) & (pl.col("no_later_print") == 0))
        .alias("covered_only_population")
    )
    return df


def add_winner_flags(df: pl.DataFrame) -> pl.DataFrame:
    """no_later_print rows (growth_mult null) count as non-winners in every
    denominator, never as winners (PREREG "Outcome" definition) -- fill_null(False)."""
    exprs = [
        (pl.col("growth_mult") >= thr).fill_null(False).alias(f"winner_{int(thr)}x")
        for thr in WINNER_THRESHOLDS
    ]
    df = df.with_columns(exprs)
    df = df.with_columns(pl.col("expiry_day").dt.year().alias("expiry_year"))
    return df


# ================================================================== stats helpers

def two_prop_z(k_cell: int, n_cell: int, k_total: int, n_total: int) -> "float | None":
    """Two-proportion z-test, cell vs REST (total minus cell, so the two groups are
    independent -- a slightly stricter version of "cell vs whole population")."""
    k_rest = k_total - k_cell
    n_rest = n_total - n_cell
    if n_cell <= 0 or n_rest <= 0:
        return None
    p_cell = k_cell / n_cell
    p_rest = k_rest / n_rest
    p_pool = k_total / n_total if n_total else 0.0
    if not (0 < p_pool < 1):
        return 0.0 if p_cell == p_rest else None
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_cell + 1 / n_rest))
    if se == 0:
        return 0.0 if p_cell == p_rest else None
    return (p_cell - p_rest) / se


def lift_ratio(cell_rate: "float | None", base_rate: "float | None") -> "float | None":
    if cell_rate is None or base_rate is None or base_rate == 0:
        return None
    return cell_rate / base_rate


def compute_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    """Rank-sum / Mann-Whitney AUC -- verified bit-identical to sklearn.roc_auc_score
    on synthetic data, kept dependency-light (scipy.stats.rankdata only) so AUC
    computation never depends on sklearn being importable."""
    y = np.asarray(y_true)
    s = np.asarray(score)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    ranks = rankdata(s)
    sum_ranks_pos = ranks[y == 1].sum()
    return float((sum_ranks_pos - n1 * (n1 + 1) / 2) / (n1 * n0))


def robust_fit(x: np.ndarray) -> "tuple[float, float]":
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return 0.0, 1.0
    med = float(np.median(finite))
    q1, q3 = np.percentile(finite, [25, 75])
    iqr = float(q3 - q1)
    if not np.isfinite(iqr) or iqr < 1e-9:
        iqr = 1.0
    return med, iqr


# ================================================================== decile machinery

def assign_deciles(values: pl.Series) -> pl.Series:
    """Equal-count deciles 1..10 via ordinal rank (ties broken by row order, never by
    the NaN-is-MAX trap since callers always drop_nulls()/fill_nan(None) first).
    Ascending: decile 10 = the highest 10% of values."""
    n = values.len()
    if n == 0:
        return values.cast(pl.Int32)
    ranks = values.rank(method="ordinal")
    decile = (((ranks - 1) * 10 // n) + 1).clip(1, 10).cast(pl.Int32)
    return decile


def raw_decile_table(df_side: pl.DataFrame, metric: str, winner_col: str) -> "pl.DataFrame | None":
    """Unmerged 10-bucket winner-rate table -- the core primitive both section C's
    real screens AND self-test 3 (decile machinery) build on."""
    sub = df_side.select([metric, winner_col]).drop_nulls(metric)
    n = sub.height
    if n == 0:
        return None
    sub = sub.with_columns(assign_deciles(sub[metric]).alias("decile"))
    agg = (
        sub.group_by("decile")
        .agg([pl.len().alias("n"), pl.col(winner_col).cast(pl.Int64).sum().alias("winners")])
        .sort("decile")
    )
    agg = agg.with_columns((pl.col("winners") / pl.col("n")).alias("winner_rate"))
    return agg


def merge_thin_deciles(agg: pl.DataFrame, min_n: int = MIN_CELL_N) -> "tuple[pl.DataFrame, bool]":
    """Sequential adjacent-decile merge for cells with n<min_n; a leftover thin tail
    cell merges backward into the previous one."""
    rows = agg.sort("decile").to_dicts()
    if not rows:
        return pl.DataFrame({"decile_label": [], "n": [], "winners": [], "winner_rate": []}), False
    merged = []
    fired = False
    i = 0
    n_rows = len(rows)
    while i < n_rows:
        lo = rows[i]["decile"]
        hi = lo
        n = rows[i]["n"]
        w = rows[i]["winners"]
        j = i
        while n < min_n and j + 1 < n_rows:
            j += 1
            fired = True
            hi = rows[j]["decile"]
            n += rows[j]["n"]
            w += rows[j]["winners"]
        merged.append({"lo": lo, "hi": hi, "n": n, "winners": w})
        i = j + 1
    if len(merged) >= 2 and merged[-1]["n"] < min_n:
        fired = True
        last = merged.pop()
        merged[-1]["hi"] = last["hi"]
        merged[-1]["n"] += last["n"]
        merged[-1]["winners"] += last["winners"]
    out = []
    for r in merged:
        label = str(r["lo"]) if r["lo"] == r["hi"] else f"{r['lo']}-{r['hi']}"
        rate = (r["winners"] / r["n"]) if r["n"] else None
        out.append({"decile_label": label, "n": r["n"], "winners": r["winners"], "winner_rate": rate})
    return pl.DataFrame(out), fired


def compute_decile_screen(analysis_df: pl.DataFrame, metric: str, side: str, winner_col: str) -> "dict | None":
    df_side = analysis_df.filter(pl.col("cp") == side)
    base_n = df_side.height
    if base_n == 0:
        return None
    base_k = int(df_side[winner_col].cast(pl.Int64).sum())
    raw = raw_decile_table(df_side, metric, winner_col)
    if raw is None or raw.height == 0:
        return None
    merged, fired = merge_thin_deciles(raw)
    base_rate = base_k / base_n
    n_nonnull = int(raw["n"].sum())
    out_rows = []
    z_list = []
    for r in merged.to_dicts():
        z = two_prop_z(r["winners"], r["n"], base_k, base_n)
        lf = lift_ratio(r["winner_rate"], base_rate)
        out_rows.append({**r, "lift": lf, "z": z, "metric": metric, "side": side})
        if z is not None:
            z_list.append(z)
    z_max_abs = max((abs(z) for z in z_list), default=None)
    return {
        "metric": metric, "side": side, "n_nonnull": n_nonnull,
        "null_share": 1.0 - n_nonnull / base_n, "table": pl.DataFrame(out_rows),
        "z_max_abs": z_max_abs, "merge_fired": fired,
    }


# ================================================================== categorical machinery

def _level_expr(metric: str, dtype) -> pl.Expr:
    col = pl.col(metric)
    if dtype in (pl.Float32, pl.Float64):
        return col.round(0).cast(pl.Int64, strict=False).cast(pl.Utf8).fill_null("<null>")
    return col.cast(pl.Utf8).fill_null("<null>")


def merge_thin_categorical(level_rows: list, min_n: int = MIN_CELL_N) -> "tuple[list, bool]":
    """Order-independent pooling: every thin level (n<min_n) merges into ONE shared
    'other' bucket (unlike decile adjacency merge, categoricals have no natural order)."""
    fired = False
    kept = []
    thin_n = 0
    thin_w = 0
    thin_levels = []
    for r in level_rows:
        if r["n"] < min_n:
            fired = True
            thin_n += r["n"]
            thin_w += r["winners"]
            thin_levels.append(str(r["level"]))
        else:
            kept.append(dict(r))
    if thin_n > 0:
        label = f"other({'+'.join(thin_levels)})" if len(thin_levels) <= 4 else f"other({len(thin_levels)}_levels)"
        kept.append({"level": label, "n": thin_n, "winners": thin_w})
    return kept, fired


def categorical_table(df_side: pl.DataFrame, metric: str, winner_col: str, base_k: int, base_n: int,
                       min_n: int = MIN_CELL_N) -> "tuple[pl.DataFrame, bool]":
    dtype = df_side.schema.get(metric)
    sub = df_side.select([metric, winner_col]).with_columns(_level_expr(metric, dtype).alias("_level"))
    agg = sub.group_by("_level").agg([pl.len().alias("n"), pl.col(winner_col).cast(pl.Int64).sum().alias("winners")])
    rows = [{"level": r["_level"], "n": r["n"], "winners": r["winners"]} for r in agg.to_dicts()]
    rows, fired = merge_thin_categorical(rows, min_n=min_n)
    base_rate = base_k / base_n if base_n else None
    out = []
    for r in rows:
        rate = (r["winners"] / r["n"]) if r["n"] else None
        z = two_prop_z(r["winners"], r["n"], base_k, base_n)
        lf = lift_ratio(rate, base_rate)
        out.append({"level": r["level"], "n": r["n"], "winners": r["winners"],
                     "winner_rate": rate, "lift": lf, "z": z})
    tbl = pl.DataFrame(out) if out else pl.DataFrame(
        schema={"level": pl.Utf8, "n": pl.Int64, "winners": pl.Int64, "winner_rate": pl.Float64,
                "lift": pl.Float64, "z": pl.Float64})
    return tbl.sort("n", descending=True), fired


# ================================================================== Section A: census

def section_A(pop_df: pl.DataFrame) -> dict:
    tradeable_df = pop_df.filter(pl.col("tradeable"))
    years = sorted(pop_df["expiry_year"].unique().to_list())

    quantile_rows = []
    for view_name, view_df in (("raw", pop_df), ("tradeable", tradeable_df)):
        for side in ("C", "P", "both"):
            sub_side = view_df if side == "both" else view_df.filter(pl.col("cp") == side)
            for year in years:
                yr_sub = sub_side.filter(pl.col("expiry_year") == year)
                gm = yr_sub["growth_mult"].drop_nulls()
                if gm.len() == 0:
                    continue
                quantile_rows.append({
                    "view": view_name, "side": side, "expiry_year": year, "n": yr_sub.height,
                    "n_nonnull_growth": gm.len(),
                    "p50": gm.quantile(0.50), "p90": gm.quantile(0.90), "p99": gm.quantile(0.99),
                    "p995": gm.quantile(0.995), "max": gm.max(),
                })
    quantile_table = pl.DataFrame(quantile_rows)

    winner_count_rows = []
    for view_name, view_df in (("raw", pop_df), ("tradeable", tradeable_df)):
        for side in ("C", "P", "both"):
            sub_side = view_df if side == "both" else view_df.filter(pl.col("cp") == side)
            n_total = sub_side.height
            for t in WINNER_THRESHOLDS:
                n_win = sub_side.filter(pl.col("growth_mult") >= t).height
                winner_count_rows.append({"view": view_name, "side": side, "threshold": t,
                                           "n_total": n_total, "n_winners": n_win,
                                           "rate": (n_win / n_total) if n_total else None})
    winner_count_table = pl.DataFrame(winner_count_rows)

    galleries = {}
    for side in ("C", "P", "both"):
        sub_side = tradeable_df if side == "both" else tradeable_df.filter(pl.col("cp") == side)
        galleries[side] = (
            sub_side.sort("growth_mult", descending=True, nulls_last=True)
            .head(100).select(TOP100_COLS)
        )

    return {"quantiles": quantile_table, "winner_counts": winner_count_table, "galleries": galleries}


# ================================================================== Section B: base rates

def section_B(analysis_df: pl.DataFrame) -> pl.DataFrame:
    years = sorted(analysis_df["expiry_year"].unique().to_list())
    dims = [
        ("overall", [("all", pl.lit(True))]),
        ("side", [(s, pl.col("cp") == s) for s in ("C", "P")]),
        ("entry_dow", [(d, pl.col("entry_dow") == d) for d in ("Mon", "Tue")]),
        ("expiry_year", [(str(y), pl.col("expiry_year") == y) for y in years]),
    ]
    rows = []
    for dim_name, slices in dims:
        for label, expr in slices:
            sub = analysis_df.filter(expr)
            n = sub.height
            row = {"dim": dim_name, "slice": label, "n": n}
            for t in WINNER_THRESHOLDS:
                k = sub.filter(pl.col("growth_mult") >= t).height
                row[f"n_winners_{int(t)}x"] = k
                row[f"rate_{int(t)}x"] = (k / n) if n else None
            rows.append(row)
    return pl.DataFrame(rows)


# ================================================================== Section C: univariate

def section_C(analysis_df: pl.DataFrame) -> dict:
    winner_col = f"winner_{int(PRIMARY_THRESHOLD)}x"

    numeric_screens = []
    for metric in ALL_NUMERIC_METRICS:
        for side in ("C", "P"):
            res = compute_decile_screen(analysis_df, metric, side, winner_col)
            if res is not None:
                numeric_screens.append(res)

    categorical_rows = []
    cat_merge_fired = False
    for metric in ALL_CATEGORICAL_METRICS:
        for side in ("C", "P"):
            df_side = analysis_df.filter(pl.col("cp") == side)
            base_n = df_side.height
            if base_n == 0:
                continue
            base_k = int(df_side[winner_col].cast(pl.Int64).sum())
            tbl, fired = categorical_table(df_side, metric, winner_col, base_k, base_n)
            cat_merge_fired = cat_merge_fired or fired
            for r in tbl.to_dicts():
                categorical_rows.append({**r, "metric": metric, "side": side})

    # global (both-sides) null share -> thin-coverage exclusion for D/E
    thin_coverage = []
    usable_numeric = []
    total_n = analysis_df.height
    for metric in ALL_NUMERIC_METRICS:
        null_share = analysis_df[metric].null_count() / total_n if total_n else 1.0
        if null_share > THIN_COVERAGE_NULL_FRAC:
            thin_coverage.append({"metric": metric, "family": METRIC_FAMILY_OF[metric], "null_share": null_share})
        else:
            usable_numeric.append(metric)
    usable_numeric_set = set(usable_numeric)

    numeric_summary_rows = [
        {"metric": r["metric"], "side": r["side"], "family": METRIC_FAMILY_OF.get(r["metric"], "?"),
         "z_max_abs": r["z_max_abs"], "n_nonnull": r["n_nonnull"], "null_share": r["null_share"],
         "merge_fired": r["merge_fired"], "kind": "numeric", "_table": r["table"]}
        for r in numeric_screens
    ]

    cat_summary_map: dict = {}
    for r in categorical_rows:
        key = (r["metric"], r["side"])
        cur = cat_summary_map.setdefault(key, {"z_max_abs": None, "n_nonnull": 0})
        cur["n_nonnull"] += r["n"]
        if r["z"] is not None:
            az = abs(r["z"])
            if cur["z_max_abs"] is None or az > cur["z_max_abs"]:
                cur["z_max_abs"] = az
    cat_summary_rows = [
        {"metric": m, "side": s, "family": METRIC_FAMILY_OF.get(m, "?"), "z_max_abs": v["z_max_abs"],
         "n_nonnull": v["n_nonnull"], "null_share": None, "merge_fired": None, "kind": "categorical"}
        for (m, s), v in cat_summary_map.items()
    ]

    all_summary_rows = [{k: v for k, v in r.items() if k != "_table"} for r in numeric_summary_rows] + cat_summary_rows
    all_summary = pl.DataFrame(all_summary_rows)

    def top40(side_filter: "str | None") -> pl.DataFrame:
        sub = all_summary if side_filter is None else all_summary.filter(pl.col("side") == side_filter)
        sub = sub.filter(pl.col("z_max_abs").is_not_null())
        return sub.sort("z_max_abs", descending=True).head(40)

    top40_C = top40("C")
    top40_P = top40("P")

    pooled_map: dict = {}
    for r in all_summary_rows:
        if r["z_max_abs"] is None:
            continue
        cur = pooled_map.get(r["metric"])
        if cur is None or r["z_max_abs"] > cur["z_max_abs"]:
            pooled_map[r["metric"]] = r
    top40_pooled = (pl.DataFrame(list(pooled_map.values())).sort("z_max_abs", descending=True).head(40)
                    if pooled_map else pl.DataFrame(schema=all_summary.schema))

    numeric_pooled_z: dict = {}
    for r in numeric_summary_rows:
        if r["z_max_abs"] is None:
            continue
        cur = numeric_pooled_z.get(r["metric"])
        if cur is None or r["z_max_abs"] > cur:
            numeric_pooled_z[r["metric"]] = r["z_max_abs"]

    top_numeric_by_z_rows = sorted(
        (r for r in numeric_summary_rows if r["z_max_abs"] is not None and r["metric"] in usable_numeric_set),
        key=lambda r: r["z_max_abs"], reverse=True,
    )
    top_categorical_by_z_rows = sorted(
        (r for r in categorical_rows if r.get("z") is not None and not str(r["level"]).startswith("other")),
        key=lambda r: abs(r["z"]), reverse=True,
    )

    return {
        "numeric_screens": numeric_screens, "categorical_rows": categorical_rows,
        "cat_merge_fired": cat_merge_fired, "all_summary": all_summary,
        "top40_C": top40_C, "top40_P": top40_P, "top40_pooled": top40_pooled,
        "thin_coverage": thin_coverage, "usable_numeric": usable_numeric,
        "numeric_pooled_z": numeric_pooled_z,
        "top_numeric_by_z_rows": top_numeric_by_z_rows,
        "top_categorical_by_z_rows": top_categorical_by_z_rows,
        "n_tests_total": TOTAL_METRICS_TESTED,
    }


# ================================================================== Section D: factors

def spearman_corr_matrix(analysis_df: pl.DataFrame, cols: list):
    """Correlation estimates for |rho|>0.8 THRESHOLD clustering stabilize with far
    fewer rows than the full analysis population (SE ~ 1/sqrt(n); at n=300k, SE~0.002,
    vastly tighter than needed to resolve a 0.8 cut) -- sampling here is a runtime
    optimization for the (expensive, O(n*p^2)) clustering *preprocessing* step only.
    Model fitting/AUC in fit_eval_side always uses the full frame, uncapped."""
    df = analysis_df
    if df.height > CORR_MAX_ROWS:
        df = df.sample(n=CORR_MAX_ROWS, seed=SEED)
    pdf = df.select(cols).to_pandas()
    return pdf.corr(method="spearman", min_periods=CORR_MIN_PERIODS)


def cluster_from_corr(corr, cols: list, threshold: float = CORR_CLUSTER_THRESHOLD) -> list:
    """Greedy/hierarchical single-linkage clustering via union-find: any pair with
    |rho|>threshold gets merged transitively into one cluster."""
    parent = {c: c for c in cols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    sub = corr.loc[cols, cols]
    for i, a in enumerate(cols):
        row = sub[a]
        for b in cols[i + 1:]:
            v = row[b]
            if v == v and abs(v) > threshold:  # v==v excludes NaN without importing pandas.isna here
                union(a, b)
    groups: dict = {}
    for c in cols:
        r = find(c)
        groups.setdefault(r, []).append(c)
    return list(groups.values())


def name_clusters(clusters: list, z_lookup: dict) -> dict:
    """Names each cluster by its most univariately-discriminative member (highest
    pooled |z_max| from section C). Returns {representative_col: [member_cols]}."""
    named = {}
    for members in clusters:
        rep = max(members, key=lambda m: (z_lookup.get(m) or 0.0, m))
        named[rep] = sorted(members)
    return named


def build_feature_matrix(df: pl.DataFrame, cols: list) -> np.ndarray:
    return df.select(cols).to_numpy().astype(float)


def _fit_logistic(X_train: np.ndarray, y_train: np.ndarray, seed: int = SEED):
    if SKLEARN_AVAILABLE:
        model = LogisticRegression(max_iter=1000, C=1.0, random_state=seed)
        model.fit(X_train, y_train)
        return lambda X: model.predict_proba(X)[:, 1]
    return _fit_logistic_fallback(X_train, y_train)


def _fit_logistic_fallback(X_train: np.ndarray, y_train: np.ndarray, l2: float = 1.0):
    """No-sklearn fallback: L2-regularized logistic regression via scipy.optimize
    (analytic gradient, L-BFGS-B). Exercised whenever sklearn is not importable --
    STAGE_C_BRIEF requires logistic regression to run regardless of sklearn
    availability (only the GBT model is allowed to just skip+log)."""
    n, p = X_train.shape
    Xb = np.hstack([np.ones((n, 1)), X_train])
    y = y_train.astype(float)

    def nll_grad(beta):
        z = np.clip(Xb @ beta, -35, 35)
        prob = 1.0 / (1.0 + np.exp(-z))
        eps = 1e-12
        ll = -np.sum(y * np.log(prob + eps) + (1 - y) * np.log(1 - prob + eps))
        reg = 0.5 * l2 * np.sum(beta[1:] ** 2)
        grad = Xb.T @ (prob - y)
        grad_reg = np.concatenate([[0.0], l2 * beta[1:]])
        return ll + reg, grad + grad_reg

    beta0 = np.zeros(p + 1)
    res = _scipy_minimize(nll_grad, beta0, jac=True, method="L-BFGS-B")
    beta = res.x

    def predict(X):
        Xb2 = np.hstack([np.ones((X.shape[0], 1)), X])
        z = np.clip(Xb2 @ beta, -35, 35)
        return 1.0 / (1.0 + np.exp(-z))

    return predict


def _fit_gbt(X_train: np.ndarray, y_train: np.ndarray, seed: int = SEED):
    if not SKLEARN_AVAILABLE:
        return None
    model = HistGradientBoostingClassifier(max_depth=3, random_state=seed)
    model.fit(X_train, y_train)
    return lambda X: model.predict_proba(X)[:, 1]


def year_blocked_folds(df_side: pl.DataFrame, group_col: str = "expiry_year") -> list:
    groups = sorted(df_side[group_col].drop_nulls().unique().to_list())
    folds = []
    for g in groups:
        test_mask = (df_side[group_col] == g).to_numpy()
        train_mask = ~test_mask
        folds.append((train_mask, test_mask, g))
    return folds


def _stratified_fallback_split(df_side: pl.DataFrame, winner_col: str, seed: int = SEED) -> list:
    y_col = df_side[winner_col].cast(pl.Int64).to_numpy()
    n = df_side.height
    if n == 0 or y_col.min() == y_col.max():
        return []
    idx = np.arange(n)
    rng = np.random.RandomState(seed)
    pos_idx = idx[y_col == 1]
    neg_idx = idx[y_col == 0]
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)
    n_pos_test = max(1, int(round(len(pos_idx) * 0.3)))
    n_neg_test = max(1, int(round(len(neg_idx) * 0.3)))
    if n_pos_test >= len(pos_idx) or n_neg_test >= len(neg_idx):
        return []
    test_idx = np.concatenate([pos_idx[:n_pos_test], neg_idx[:n_neg_test]])
    test_mask = np.zeros(n, dtype=bool)
    test_mask[test_idx] = True
    train_mask = ~test_mask
    return [(train_mask, test_mask, "stratified_fallback")]


def select_folds(df_side: pl.DataFrame, winner_col: str, group_col: str = "expiry_year",
                  min_train_dates: int = MIN_TRAIN_DISTINCT_DATES, seed: int = SEED) -> "tuple[list, bool]":
    """Standard leave-one-year-out folds, UNLESS infeasible -- falls back to a single
    seeded stratified 70/30 split for the whole side. Infeasible means: fewer than 2
    year-groups present, a class missing from some fold's train or test, OR a
    candidate fold's TRAIN set spans too few distinct trading dates to be a meaningful
    unit of exchangeability (empirically diagnosed on smoke: with only 1-6 distinct
    dates in a fold, F7 market-context columns -- vix_close, regime_multiplier, etc,
    which are date-keyed not symbol-keyed -- become a near-perfect fingerprint of
    which week a row came from, biasing the shuffled-label null well away from 0.5;
    this never fires at full scale, where every year spans dozens to ~250 trading
    days). Returns (folds, used_fallback)."""
    raw_folds = year_blocked_folds(df_side, group_col=group_col)
    y_col = df_side[winner_col].cast(pl.Int64).to_numpy()
    feasible = len(raw_folds) >= 2
    if feasible:
        for train_mask, test_mask, _grp in raw_folds:
            y_train, y_test = y_col[train_mask], y_col[test_mask]
            if (len(y_train) == 0 or len(y_test) == 0
                    or y_train.min() == y_train.max() or y_test.min() == y_test.max()):
                feasible = False
                break
            n_train_dates = df_side["entry_date"].filter(pl.Series(train_mask)).n_unique()
            if n_train_dates < min_train_dates:
                feasible = False
                break
    if feasible:
        return raw_folds, False
    return _stratified_fallback_split(df_side, winner_col, seed=seed), True


def top_decile_lift(y_test: np.ndarray, proba: np.ndarray, base_rate: "float | None") -> "dict | None":
    n = len(y_test)
    if n == 0 or base_rate is None:
        return None
    k = max(1, int(math.ceil(n * 0.10)))
    order = np.argsort(-proba)
    top_idx = order[:k]
    top_rate = float(y_test[top_idx].mean())
    return {"n_top": k, "winner_rate_top": top_rate, "lift": (top_rate / base_rate) if base_rate else None}


def fit_eval_side(analysis_df: pl.DataFrame, feature_cols: list, winner_col: str, side: str,
                   z_lookup: "dict | None" = None, n_permutations: "int | None" = None,
                   seed: int = SEED) -> dict:
    df_side = analysis_df.filter(pl.col("cp") == side)
    n_rows = df_side.height
    if n_permutations is None:
        n_permutations = N_PERMUTATIONS_SMALL if n_rows < LARGE_DATA_ROWS else N_PERMUTATIONS_LARGE

    if n_rows == 0 or not feature_cols:
        return {"side": side, "n_rows": n_rows, "base_rate": None, "n_features": len(feature_cols),
                "feature_cols": list(feature_cols), "folds": [], "mean_auc_logistic": None,
                "mean_auc_gbt": None, "mean_shuffled_auc": None, "n_shuffled_samples": 0,
                "n_permutations": n_permutations, "mean_lift_top_decile": None, "used_fallback_split": None}

    # Features ordered by pooled |z| descending ONCE -- lets the per-fold EPV cap below
    # take a stable "top-K most discriminative" slice rather than an arbitrary one.
    z_lookup = z_lookup or {}
    feature_order = sorted(range(len(feature_cols)), key=lambda i: -(z_lookup.get(feature_cols[i]) or 0.0))

    y_all = df_side[winner_col].cast(pl.Int64).to_numpy()
    base_rate = float(y_all.mean())
    X_all_raw = build_feature_matrix(df_side, feature_cols)[:, feature_order]
    folds, used_fallback = select_folds(df_side, winner_col, seed=seed)

    fold_results = []
    shuffled_aucs = []
    rng = np.random.RandomState(seed)

    for train_mask, test_mask, grp in folds:
        y_train, y_test = y_all[train_mask], y_all[test_mask]
        if len(y_train) == 0 or len(y_test) == 0 or y_train.min() == y_train.max() or y_test.min() == y_test.max():
            fold_results.append({"group": grp, "n_train": int(train_mask.sum()), "n_test": int(test_mask.sum()),
                                  "auc_logistic": None, "auc_gbt": None, "lift_logistic": None,
                                  "lift_gbt": None, "skipped": True})
            continue

        Xtr_raw, Xte_raw = X_all_raw[train_mask], X_all_raw[test_mask]
        p = Xtr_raw.shape[1]
        meds = np.zeros(p)
        iqrs = np.ones(p)
        for j in range(p):
            meds[j], iqrs[j] = robust_fit(Xtr_raw[:, j])

        def _prep(X, meds=meds, iqrs=iqrs):
            Xc = np.where(np.isfinite(X), X, meds[np.newaxis, :])
            return (Xc - meds[np.newaxis, :]) / iqrs[np.newaxis, :]

        Xtr, Xte = _prep(Xtr_raw), _prep(Xte_raw)

        # Events-per-variable cap for LOGISTIC ONLY: p=len(feature_cols) raw features
        # against a handful of positive training events is classic over-parameterization
        # (Peduzzi-style EPV guidance) -- take only the top-K (by pooled |z|, i.e. the
        # already-computed feature_order) columns, K set from THIS fold's own positive
        # count. GBT keeps the full feature set (its max_depth<=3 cap is its own
        # regularization mechanism; empirically it was never the source of the bias
        # this cap fixes). Never binds at full scale (thousands of positives).
        n_pos_train = int(y_train.sum())
        k_cap = max(1, min(p, n_pos_train // EPV_PER_FEATURE))
        Xtr_log, Xte_log = Xtr[:, :k_cap], Xte[:, :k_cap]

        pred_log = _fit_logistic(Xtr_log, y_train, seed=seed)
        proba_log = pred_log(Xte_log)
        auc_log = compute_auc(y_test, proba_log)
        lift_log = top_decile_lift(y_test, proba_log, base_rate)

        auc_gbt = None
        lift_gbt = None
        if SKLEARN_AVAILABLE:
            pred_gbt = _fit_gbt(Xtr, y_train, seed=seed)
            proba_gbt = pred_gbt(Xte)
            auc_gbt = compute_auc(y_test, proba_gbt)
            lift_gbt = top_decile_lift(y_test, proba_gbt, base_rate)

        fold_results.append({"group": grp, "n_train": int(train_mask.sum()), "n_test": int(test_mask.sum()),
                              "auc_logistic": auc_log, "auc_gbt": auc_gbt, "lift_logistic": lift_log,
                              "lift_gbt": lift_gbt, "skipped": False, "k_cap_logistic": k_cap})

        # shuffled-label control: TRAIN labels permuted, evaluated against the REAL
        # (unshuffled) test labels -- the standard permutation-null baseline. Logistic
        # only (speed, and it is the model the EPV cap above targets); averaged over
        # n_permutations for a stable noise-envelope estimate (empirically, per-draw std
        # is large -- ~0.2 AUC on smoke-sized folds -- so N_PERMUTATIONS_SMALL=100, not
        # 20, is needed for the mean to actually converge near 0.5).
        for p_i in range(n_permutations):
            y_train_shuf = rng.permutation(y_train)
            if y_train_shuf.min() == y_train_shuf.max():
                continue
            pred_shuf = _fit_logistic(Xtr_log, y_train_shuf, seed=seed + p_i + 1)
            proba_shuf = pred_shuf(Xte_log)
            auc_shuf = compute_auc(y_test, proba_shuf)
            if not math.isnan(auc_shuf):
                shuffled_aucs.append(auc_shuf)

    valid_log = [f["auc_logistic"] for f in fold_results if not f["skipped"] and f["auc_logistic"] is not None
                 and not math.isnan(f["auc_logistic"])]
    valid_gbt = [f["auc_gbt"] for f in fold_results if not f["skipped"] and f["auc_gbt"] is not None
                 and not math.isnan(f["auc_gbt"])]
    lift_field = "lift_gbt" if SKLEARN_AVAILABLE else "lift_logistic"
    valid_lifts = [f[lift_field]["lift"] for f in fold_results
                   if not f["skipped"] and f.get(lift_field) and f[lift_field].get("lift") is not None]

    mean_auc_log = float(np.mean(valid_log)) if valid_log else None
    mean_auc_gbt = float(np.mean(valid_gbt)) if valid_gbt else None
    mean_shuffled_auc = float(np.mean(shuffled_aucs)) if shuffled_aucs else None
    mean_lift_top_decile = float(np.mean(valid_lifts)) if valid_lifts else None

    return {
        "side": side, "n_rows": n_rows, "base_rate": base_rate, "n_features": len(feature_cols),
        "feature_cols": list(feature_cols), "folds": fold_results,
        "mean_auc_logistic": mean_auc_log, "mean_auc_gbt": mean_auc_gbt,
        "mean_shuffled_auc": mean_shuffled_auc, "n_shuffled_samples": len(shuffled_aucs),
        "n_permutations": n_permutations, "mean_lift_top_decile": mean_lift_top_decile,
        "used_fallback_split": used_fallback,
    }


def section_D(analysis_df: pl.DataFrame, c_results: dict) -> dict:
    usable_numeric = c_results["usable_numeric"]
    z_lookup = c_results["numeric_pooled_z"]

    corr_pooled = spearman_corr_matrix(analysis_df, usable_numeric)
    clusters = cluster_from_corr(corr_pooled, usable_numeric)
    named = name_clusters(clusters, z_lookup)
    reps = list(named.keys())

    cluster_table_rows = []
    for rep, members in named.items():
        for m in members:
            cluster_table_rows.append({"cluster_rep": rep, "member": m,
                                        "family": METRIC_FAMILY_OF.get(m, "?"),
                                        "z_max_abs": z_lookup.get(m)})
    cluster_table = pl.DataFrame(cluster_table_rows).sort(["cluster_rep", "member"]) if cluster_table_rows \
        else pl.DataFrame(schema={"cluster_rep": pl.Utf8, "member": pl.Utf8, "family": pl.Utf8, "z_max_abs": pl.Float64})

    corr_side = {}
    for side in ("C", "P"):
        side_df = analysis_df.filter(pl.col("cp") == side)
        if side_df.height >= CORR_MIN_PERIODS:
            corr_side[side] = spearman_corr_matrix(side_df, usable_numeric)

    model_results = {side: fit_eval_side(analysis_df, reps, f"winner_{int(PRIMARY_THRESHOLD)}x", side,
                                          z_lookup=z_lookup)
                      for side in ("C", "P")}

    return {
        "usable_numeric": usable_numeric, "corr_pooled": corr_pooled, "corr_side": corr_side,
        "clusters": named, "reps": reps, "cluster_table": cluster_table, "models": model_results,
    }


# ================================================================== Section E: ablation

def numeric_metrics_excluding_family(usable_numeric: list, family: str) -> list:
    fam_cols = set(FAMILIES_NUMERIC.get(family, []))
    return [c for c in usable_numeric if c not in fam_cols]


def run_ablated_model(analysis_df: pl.DataFrame, corr_pooled, numeric_subset: list, z_lookup: dict,
                       side: str, winner_col: str) -> "dict | None":
    """n_permutations=0: E1/E2 only ever read mean_auc_*/mean_lift_top_decile off the
    result (see _model_auc / delta_lift below) -- the shuffled-label control is a D-only
    deliverable (STAGE_C_BRIEF section D), so computing it here would be up to ~50 wasted
    model fits per ablated model for a number nothing downstream consumes. This is the
    dominant runtime saving for --full (36 ablated models x up to 50 fits each)."""
    if not numeric_subset:
        return None
    clusters = cluster_from_corr(corr_pooled, numeric_subset)
    named = name_clusters(clusters, z_lookup)
    reps = list(named.keys())
    return fit_eval_side(analysis_df, reps, winner_col, side, z_lookup=z_lookup, n_permutations=0)


def _model_auc(m: "dict | None") -> "float | None":
    if m is None:
        return None
    return m["mean_auc_gbt"] if m["mean_auc_gbt"] is not None else m["mean_auc_logistic"]


def section_E1_leave_family_out(analysis_df: pl.DataFrame, d_results: dict, z_lookup: dict) -> pl.DataFrame:
    corr_pooled = d_results["corr_pooled"]
    usable = d_results["usable_numeric"]
    winner_col = f"winner_{int(PRIMARY_THRESHOLD)}x"
    ablation_df = analysis_df
    if analysis_df.height > ABLATION_MAX_ROWS:
        ablation_df = analysis_df.sample(n=ABLATION_MAX_ROWS, seed=SEED)

    rows = []
    for fam in sorted(FAMILIES_NUMERIC.keys()):
        subset = numeric_metrics_excluding_family(usable, fam)
        dropped = len(usable) - len(subset)
        for side in ("C", "P"):
            base = d_results["models"][side]
            ablated = run_ablated_model(ablation_df, corr_pooled, subset, z_lookup, side, winner_col)
            base_auc = _model_auc(base)
            abl_auc = _model_auc(ablated)
            delta_auc = (abl_auc - base_auc) if (base_auc is not None and abl_auc is not None) else None
            base_lift = base.get("mean_lift_top_decile")
            abl_lift = ablated.get("mean_lift_top_decile") if ablated else None
            delta_lift = (abl_lift - base_lift) if (base_lift is not None and abl_lift is not None) else None
            rows.append({
                "family": fam, "side": side, "cols_dropped": dropped,
                "n_features_full": base["n_features"],
                "n_features_ablated": ablated["n_features"] if ablated else None,
                "base_auc": base_auc, "ablated_auc": abl_auc, "delta_auc": delta_auc,
                "base_lift_top_decile": base_lift, "ablated_lift_top_decile": abl_lift,
                "delta_lift_top_decile": delta_lift,
                "delta_direction": (None if delta_auc is None else
                                    ("down" if delta_auc < 0 else ("up" if delta_auc > 0 else "flat"))),
                "n_rows_used": ablation_df.filter(pl.col("cp") == side).height,
            })
    return pl.DataFrame(rows)


def section_E2_leave_metric_out(analysis_df: pl.DataFrame, d_results: dict, c_results: dict,
                                 z_lookup: dict) -> pl.DataFrame:
    corr_pooled = d_results["corr_pooled"]
    usable = d_results["usable_numeric"]
    winner_col = f"winner_{int(PRIMARY_THRESHOLD)}x"

    seen = set()
    top10_metrics = []
    for r in c_results["top_numeric_by_z_rows"]:
        if r["metric"] not in seen:
            seen.add(r["metric"])
            top10_metrics.append(r["metric"])
        if len(top10_metrics) >= 10:
            break

    ablation_df = analysis_df
    if analysis_df.height > ABLATION_MAX_ROWS:
        ablation_df = analysis_df.sample(n=ABLATION_MAX_ROWS, seed=SEED)

    rows = []
    for metric in top10_metrics:
        subset = [c for c in usable if c != metric]
        for side in ("C", "P"):
            base = d_results["models"][side]
            ablated = run_ablated_model(ablation_df, corr_pooled, subset, z_lookup, side, winner_col)
            base_auc = _model_auc(base)
            abl_auc = _model_auc(ablated)
            delta_auc = (abl_auc - base_auc) if (base_auc is not None and abl_auc is not None) else None
            base_lift = base.get("mean_lift_top_decile")
            abl_lift = ablated.get("mean_lift_top_decile") if ablated else None
            delta_lift = (abl_lift - base_lift) if (base_lift is not None and abl_lift is not None) else None
            rows.append({"metric": metric, "side": side, "base_auc": base_auc, "ablated_auc": abl_auc,
                         "delta_auc": delta_auc, "base_lift_top_decile": base_lift,
                         "ablated_lift_top_decile": abl_lift, "delta_lift_top_decile": delta_lift,
                         "n_rows_used": ablation_df.filter(pl.col("cp") == side).height})
    return pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={"metric": pl.Utf8, "side": pl.Utf8, "base_auc": pl.Float64, "ablated_auc": pl.Float64,
                "delta_auc": pl.Float64, "base_lift_top_decile": pl.Float64,
                "ablated_lift_top_decile": pl.Float64, "delta_lift_top_decile": pl.Float64,
                "n_rows_used": pl.Int64})


# ---------------------------------------------------------------- E3: rule distillation

def build_candidate_predicates(analysis_df: pl.DataFrame, c_results: dict, top_k: int = RULE_TOP_K_ATOMS) -> list:
    candidates = []
    seen_metrics = set()

    for row in c_results["top_numeric_by_z_rows"][:top_k]:
        metric, side = row["metric"], row["side"]
        if metric in seen_metrics:
            continue
        tbl = row["_table"]
        if tbl is None or tbl.height < 2:
            continue
        first, last = tbl.row(0, named=True), tbl.row(-1, named=True)
        hi_side = (last["winner_rate"] or 0) >= (first["winner_rate"] or 0)
        pooled = analysis_df[metric].drop_nulls()
        if pooled.len() < 100:
            continue
        lo_q, hi_q = pooled.quantile(0.20), pooled.quantile(0.80)
        if lo_q is None or hi_q is None:
            continue
        seen_metrics.add(metric)
        if hi_side:
            expr = pl.col(metric) >= hi_q
            label = f"{metric}>=P80({hi_q:.4g})"
        else:
            expr = pl.col(metric) <= lo_q
            label = f"{metric}<=P20({lo_q:.4g})"
        candidates.append({"label": label, "expr": expr, "conjuncts": 1,
                            "uses_earnings": metric in ("days_to_next_earnings", "days_since_last_earnings"),
                            "direction_source_side": side})

    # explicit side atoms -- PREREG's own rule example includes "C-side" as a conjunct
    candidates.append({"label": "cp==C", "expr": pl.col("cp") == "C", "conjuncts": 1,
                        "uses_earnings": False, "direction_source_side": None})
    candidates.append({"label": "cp==P", "expr": pl.col("cp") == "P", "conjuncts": 1,
                        "uses_earnings": False, "direction_source_side": None})

    for row in c_results["top_categorical_by_z_rows"][:top_k]:
        metric, level = row["metric"], row["level"]
        if metric in seen_metrics or str(level).startswith("other") or level == "<null>":
            continue
        seen_metrics.add(metric)
        dtype = analysis_df.schema.get(metric)
        if dtype in (pl.Float32, pl.Float64):
            try:
                lvl_val = float(level)
            except (TypeError, ValueError):
                continue
            expr = pl.col(metric) == lvl_val
        elif dtype == pl.Boolean:
            expr = pl.col(metric) == (str(level).lower() == "true")
        else:
            expr = pl.col(metric) == level
        candidates.append({"label": f"{metric}=={level}", "expr": expr, "conjuncts": 1,
                            "uses_earnings": metric == "earnings_in_window", "direction_source_side": None})
    return candidates


def generate_rule_candidates(analysis_df: pl.DataFrame, c_results: dict,
                              max_conjuncts: int = RULE_MAX_CONJUNCTS, top_k: int = RULE_TOP_K_ATOMS) -> list:
    atoms = build_candidate_predicates(analysis_df, c_results, top_k=top_k)
    combos = []
    max_r = min(max_conjuncts, len(atoms))
    for r in range(1, max_r + 1):
        for combo in itertools.combinations(range(len(atoms)), r):
            exprs = [atoms[i]["expr"] for i in combo]
            labels = [atoms[i]["label"] for i in combo]
            uses_earn = any(atoms[i]["uses_earnings"] for i in combo)
            full_expr = exprs[0]
            for e in exprs[1:]:
                full_expr = full_expr & e
            combos.append({"label": " AND ".join(labels), "expr": full_expr,
                            "conjuncts": r, "uses_earnings": uses_earn})
    return combos


def evaluate_rule_candidates(analysis_df: pl.DataFrame, candidates: list, winner_col: str,
                              min_n: int = RULE_MIN_N) -> list:
    base_k = int(analysis_df[winner_col].cast(pl.Int64).sum())
    base_n = analysis_df.height
    base_rate = base_k / base_n if base_n else None
    out = []
    for c in candidates:
        sub = analysis_df.filter(c["expr"])
        n = sub.height
        k = int(sub[winner_col].cast(pl.Int64).sum()) if n else 0
        rate = (k / n) if n else None
        out.append({"label": c["label"], "conjuncts": c["conjuncts"], "uses_earnings": c["uses_earnings"],
                     "n": n, "winners": k, "winner_rate": rate, "lift": lift_ratio(rate, base_rate),
                     "meets_n_floor": n >= min_n, "_expr": c["expr"]})
    return out


def select_final_rules(evaluated: list, max_rules: int = RULE_MAX_SELECTED) -> list:
    # PREREG: "Choose rules that are ... high-lift" -- lift<=1.0 means at-or-below the
    # base rate, i.e. not a discovered pattern at all, so it is never worth padding the
    # selection down into that territory just to reach max_rules. Fewer than max_rules
    # (even zero) selected rules is an honest, expected outcome (PREREG "a clean null
    # is an acceptable, publishable outcome").
    candidates = [r for r in evaluated if r["meets_n_floor"] and r["lift"] is not None and r["lift"] > 1.0]
    candidates.sort(key=lambda r: r["lift"], reverse=True)
    selected = []
    selected_atom_sets = []
    for r in candidates:
        atom_set = frozenset(r["label"].split(" AND "))
        if any(atom_set <= s or s <= atom_set for s in selected_atom_sets):
            continue
        selected.append(r)
        selected_atom_sets.append(atom_set)
        if len(selected) >= max_rules:
            break
    return selected


def rule_robustness_grid(analysis_df: pl.DataFrame, covered_only_df: pl.DataFrame, rule: dict) -> list:
    expr = rule["_expr"]
    grid_rows = []

    def eval_slice(df: pl.DataFrame, winner_col: str, dim: str, slice_label: str):
        base_n = df.height
        base_k = int(df[winner_col].cast(pl.Int64).sum()) if base_n else 0
        base_rate = (base_k / base_n) if base_n else None
        sub = df.filter(expr)
        n = sub.height
        k = int(sub[winner_col].cast(pl.Int64).sum()) if n else 0
        rate = (k / n) if n else None
        grid_rows.append({"dim": dim, "slice": slice_label, "n": n, "winners": k, "winner_rate": rate,
                           "base_rate": base_rate, "lift": lift_ratio(rate, base_rate)})

    primary_col = f"winner_{int(PRIMARY_THRESHOLD)}x"
    for y in sorted(analysis_df["expiry_year"].unique().to_list()):
        eval_slice(analysis_df.filter(pl.col("expiry_year") == y), primary_col, "expiry_year", str(y))
    for s in ("C", "P"):
        eval_slice(analysis_df.filter(pl.col("cp") == s), primary_col, "side", s)
    for d in ("Mon", "Tue"):
        eval_slice(analysis_df.filter(pl.col("entry_dow") == d), primary_col, "entry_dow", d)
    for t in WINNER_THRESHOLDS:
        eval_slice(analysis_df, f"winner_{int(t)}x", "threshold", f"{t:g}x")
    eval_slice(analysis_df, primary_col, "floors", "tradeable")
    eval_slice(covered_only_df, primary_col, "floors", "covered_only")
    eval_slice(analysis_df.filter(pl.col("earnings_in_window") == 0), primary_col, "ex_earnings", "excl_earnings_week")
    eval_slice(analysis_df.filter(pl.col("index_or_etf_underlying") == 0), primary_col, "ex_index_etf", "excl_index_etf")
    return grid_rows


def classify_rule(grid_rows: list, rule: dict) -> dict:
    """PREREG's HOLD criterion, applied mechanically: HOLD = same direction in
    >=4/5 year slices AND both adjacent thresholds (3x,10x) AND survives ex-earnings
    (unless the rule includes earnings). Smoke only ever has a subset of the 5 canonical
    year slices present, so the 4/5 fraction (0.8) is applied to however many are
    present -- at full scale (5 years present) this reduces to the literal ">=4/5"."""
    year_rows = [r for r in grid_rows if r["dim"] == "expiry_year"]
    n_years = len(year_rows)
    year_same_dir = sum(1 for r in year_rows if (r["lift"] or 0) > 1.0)
    year_threshold = max(1, round(0.8 * n_years)) if n_years else 0
    year_ok = (n_years > 0) and (year_same_dir >= year_threshold)

    thr_rows = {r["slice"]: r for r in grid_rows if r["dim"] == "threshold"}
    thr3 = thr_rows.get("3x")
    thr10 = thr_rows.get("10x")
    thresholds_ok = bool(thr3 and thr10 and (thr3["lift"] or 0) > 1.0 and (thr10["lift"] or 0) > 1.0)

    if rule["uses_earnings"]:
        earnings_ok = True
        earnings_note = "n/a (rule includes earnings)"
    else:
        ex_row = next((r for r in grid_rows if r["dim"] == "ex_earnings"), None)
        earnings_ok = bool(ex_row and (ex_row["lift"] or 0) > 1.0)
        earnings_note = "checked"

    n_met = sum([year_ok, thresholds_ok, earnings_ok])
    verdict = "HOLD" if n_met == 3 else ("PARTIAL" if n_met == 2 else "FAILS")
    return {"verdict": verdict, "year_ok": year_ok, "year_same_dir": year_same_dir, "n_years": n_years,
            "year_threshold": year_threshold, "thresholds_ok": thresholds_ok, "earnings_ok": earnings_ok,
            "earnings_note": earnings_note}


def section_E3(analysis_df: pl.DataFrame, covered_only_df: pl.DataFrame, c_results: dict,
               min_n: int = RULE_MIN_N, max_rules: int = RULE_MAX_SELECTED) -> dict:
    winner_col = f"winner_{int(PRIMARY_THRESHOLD)}x"
    candidates = generate_rule_candidates(analysis_df, c_results)
    evaluated = evaluate_rule_candidates(analysis_df, candidates, winner_col, min_n=min_n)
    selected = select_final_rules(evaluated, max_rules=max_rules)
    graded = []
    for rule in selected:
        grid = rule_robustness_grid(analysis_df, covered_only_df, rule)
        verdict = classify_rule(grid, rule)
        graded.append({"rule": rule, "grid": grid, "verdict": verdict})
    return {"n_candidates": len(candidates), "n_meeting_floor": sum(1 for r in evaluated if r["meets_n_floor"]),
            "evaluated": evaluated, "selected": graded}


# ================================================================== self-tests (--smoke)

def run_self_tests(raw_df: pl.DataFrame, pop_df: pl.DataFrame, analysis_df: pl.DataFrame,
                    c_results: dict, d_results: dict) -> list:
    results = []

    def check(name: str, passed: bool, detail: str = "") -> bool:
        results.append((name, bool(passed), detail))
        return bool(passed)

    # 1. Population counts: hand-recompute tradeable + analysis-population filters
    # independently (a second, literal expression) and assert equality with the
    # pipeline's add_population_flags() output.
    tradeable_hand = raw_df.filter(
        (pl.col("entry_close") >= 0.20) & (pl.col("entry_volume") >= 100)
        & (pl.col("entry_transactions") >= 10) & (~pl.col("adjusted"))
    ).height
    tradeable_pipeline = pop_df.filter(pl.col("tradeable")).height
    analysis_hand = raw_df.filter(
        (pl.col("covered") == 1) & (pl.col("no_later_print") == 0)
        & (pl.col("entry_close") >= 0.20) & (pl.col("entry_volume") >= 100)
        & (pl.col("entry_transactions") >= 10) & (~pl.col("adjusted"))
    ).height
    analysis_pipeline = analysis_df.height
    check("1. Population counts (hand-recomputed tradeable + analysis vs pipeline)",
          tradeable_hand == tradeable_pipeline and analysis_hand == analysis_pipeline,
          f"tradeable hand={tradeable_hand} pipeline={tradeable_pipeline}; "
          f"analysis hand={analysis_hand} pipeline={analysis_pipeline}")

    # 2. winner counts at 3x/5x/10x match a direct polars filter
    ok2 = True
    detail2 = []
    for t in WINNER_THRESHOLDS:
        direct = analysis_df.filter(pl.col("growth_mult") >= t).height
        via_col = int(analysis_df[f"winner_{int(t)}x"].cast(pl.Int64).sum())
        ok2 = ok2 and (direct == via_col)
        detail2.append(f"{t:g}x direct={direct} col={via_col}")
    check("2. Winner counts at 3x/5x/10x match direct filter", ok2, "; ".join(detail2))

    # 3. Decile machinery: growth_mult as its own feature, synthetic label = "is this
    # row in the top decile of growth_mult" (tautological by construction -- this
    # isolates and tests the decile-assign + group-by-and-aggregate machinery itself,
    # independent of the real ~1-3% winner base rate which would NOT naturally put
    # 100% of winners in the top decile -- see STAGE_C_REPORT.md for why).
    n_c = analysis_df.filter(pl.col("cp") == "C").height
    n_p = analysis_df.filter(pl.col("cp") == "P").height
    side3 = "C" if n_c >= n_p else "P"
    df_side3 = analysis_df.filter(pl.col("cp") == side3).filter(pl.col("growth_mult").is_not_null())
    dec3 = assign_deciles(df_side3["growth_mult"])
    df_side3b = df_side3.with_columns(dec3.alias("decile"))
    max_dec = df_side3b["decile"].max()
    df_side3b = df_side3b.with_columns((pl.col("decile") == max_dec).cast(pl.Int64).alias("_syn"))
    agg3 = (df_side3b.group_by("decile").agg(pl.len().alias("n"), pl.col("_syn").mean().alias("rate"))
            .sort("decile"))
    rates3 = agg3["rate"].to_list()
    monotone3 = all(rates3[i] <= rates3[i + 1] + 1e-12 for i in range(len(rates3) - 1))
    top100_3 = abs(rates3[-1] - 1.0) < 1e-9
    bottom0_3 = all(r == 0.0 for r in rates3[:-1])
    check("3. Decile machinery: monotone ladder, top decile == 100%", monotone3 and top100_3 and bottom0_3,
          f"side={side3} n_deciles={len(rates3)} rates={['%.3f' % r for r in rates3]}")

    any_fired = any(r["merge_fired"] for r in c_results["numeric_screens"]) if c_results["numeric_screens"] else False
    n_fired = sum(1 for r in c_results["numeric_screens"] if r["merge_fired"])
    check("3b (extra). min-N merge logic fires at least once on smoke",
          any_fired or c_results["cat_merge_fired"],
          f"numeric screens fired {n_fired}/{len(c_results['numeric_screens'])}; "
          f"categorical merge fired={c_results['cat_merge_fired']}")

    # 4. Shuffled-label AUC in [0.45, 0.55]
    shuf_C = d_results["models"]["C"]["mean_shuffled_auc"]
    shuf_P = d_results["models"]["P"]["mean_shuffled_auc"]
    ok4 = (shuf_C is not None and 0.45 <= shuf_C <= 0.55) and (shuf_P is not None and 0.45 <= shuf_P <= 0.55)
    check("4. Shuffled-label AUC in [0.45, 0.55] (both sides)", ok4, f"C={shuf_C} P={shuf_P}")

    # 5. sma_20_pxrel and sma_21_pxrel in the same correlation cluster
    clusters = d_results["clusters"]
    same5 = any(("sma_20_pxrel" in members and "sma_21_pxrel" in members) for members in clusters.values())
    check("5. sma_20_pxrel and sma_21_pxrel land in the same cluster", same5,
          f"{len(clusters)} clusters total")

    # 6. Leave-family-out plumbing: dropping a family removes exactly its cardinality
    usable = d_results["usable_numeric"]
    ok6 = True
    detail6 = []
    for fam in ("F1", "F4", "F6"):
        subset = numeric_metrics_excluding_family(usable, fam)
        expected_drop = len([c for c in FAMILIES_NUMERIC[fam] if c in usable])
        actual_drop = len(usable) - len(subset)
        ok6 = ok6 and (actual_drop == expected_drop) and (expected_drop > 0)
        detail6.append(f"{fam} expected={expected_drop} actual={actual_drop}")
    check("6. Leave-family-out drops exactly the family's cardinality", ok6, "; ".join(detail6))

    # 7. assert_no_holdout_leak passed on load (execution reaching this point without
    # an AssertionError during load_smoke_frame() / load_full_frame() IS the pass --
    # main() wraps the load call and would already have reported [FAIL] + exited if not)
    check("7. assert_no_holdout_leak passed on load", True, "no exception raised during load")

    return results


# ================================================================== output writers (--full)

def df_to_md_table(df: pl.DataFrame, max_rows: "int | None" = None) -> str:
    if df.height == 0:
        return "(empty table)"
    d = df.head(max_rows) if max_rows is not None else df
    cols = d.columns

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)

    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in d.to_dicts():
        lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_full_outputs(pop_df: pl.DataFrame, analysis_df: pl.DataFrame, a: dict, b: pl.DataFrame,
                        c: dict, d: dict, e1: pl.DataFrame, e2: pl.DataFrame, e3: dict) -> None:
    tables_dir = FULL_OUT_DIR / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    def save(name: str, df: pl.DataFrame):
        df.write_parquet(tables_dir / f"{name}.parquet")
        with open(tables_dir / f"{name}.md", "w", encoding="ascii", errors="replace") as f:
            f.write(df_to_md_table(df))
        log(f"  wrote {name}.parquet + .md ({df.height} rows)")

    log(f"writing tables to {tables_dir}")
    save("A_census_quantiles", a["quantiles"])
    save("A_census_winner_counts", a["winner_counts"])
    for side, g in a["galleries"].items():
        save(f"A_top100_gallery_{side}", g)
    save("B_base_rates", b)
    save("C_all_metrics_summary", c["all_summary"])
    save("C_top40_C", c["top40_C"])
    save("C_top40_P", c["top40_P"])
    save("C_top40_pooled", c["top40_pooled"])
    thin_tbl = (pl.DataFrame(c["thin_coverage"]) if c["thin_coverage"]
                else pl.DataFrame(schema={"metric": pl.Utf8, "family": pl.Utf8, "null_share": pl.Float64}))
    save("C_thin_coverage", thin_tbl)
    save("D_cluster_table", d["cluster_table"])
    save("E1_leave_family_out", e1)
    save("E2_leave_metric_out", e2)

    rules_summary_rows = []
    rules_grid_rows = []
    for g in e3["selected"]:
        r, v = g["rule"], g["verdict"]
        rules_summary_rows.append({"label": r["label"], "conjuncts": r["conjuncts"], "n": r["n"],
                                    "winner_rate": r["winner_rate"], "lift": r["lift"],
                                    "verdict": v["verdict"], "year_same_dir": v["year_same_dir"],
                                    "n_years": v["n_years"], "thresholds_ok": v["thresholds_ok"],
                                    "earnings_ok": v["earnings_ok"]})
        for gr in g["grid"]:
            rules_grid_rows.append({"rule": r["label"], **gr})
    e3_summary = (pl.DataFrame(rules_summary_rows) if rules_summary_rows else pl.DataFrame(
        schema={"label": pl.Utf8, "conjuncts": pl.Int64, "n": pl.Int64, "winner_rate": pl.Float64,
                "lift": pl.Float64, "verdict": pl.Utf8, "year_same_dir": pl.Int64, "n_years": pl.Int64,
                "thresholds_ok": pl.Boolean, "earnings_ok": pl.Boolean}))
    e3_grid = (pl.DataFrame(rules_grid_rows) if rules_grid_rows else pl.DataFrame(
        schema={"rule": pl.Utf8, "dim": pl.Utf8, "slice": pl.Utf8, "n": pl.Int64, "winners": pl.Int64,
                "winner_rate": pl.Float64, "base_rate": pl.Float64, "lift": pl.Float64}))
    save("E3_rules_summary", e3_summary)
    save("E3_rules_grid", e3_grid)
    candidates_tbl = pl.DataFrame([{k: v for k, v in r.items() if k != "_expr"} for r in e3["evaluated"]])
    save("E3_candidates_all", candidates_tbl)

    md = []
    md.append("# RESULTS_TABLES -- weekly_5dte_movers Stage C (machine-written by analyze.py --full)")
    md.append("")
    md.append(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} (local), seed={SEED}, "
              f"sklearn={'yes ' + str(SKLEARN_VERSION) if SKLEARN_AVAILABLE else 'NOT AVAILABLE (fallback path)'}")
    md.append(f"Analysis population: {analysis_df.height} rows "
              f"(C={analysis_df.filter(pl.col('cp') == 'C').height}, "
              f"P={analysis_df.filter(pl.col('cp') == 'P').height})")
    md.append(f"Total metrics tested: {TOTAL_METRICS_TESTED} "
              f"({len(ALL_NUMERIC_METRICS)} numeric + {len(ALL_CATEGORICAL_METRICS)} categorical)")
    md.append("")
    md.append("## Section A -- Census")
    md.append("")
    md.append("### growth_mult quantiles by view x side x expiry_year")
    md.append("")
    md.append(df_to_md_table(a["quantiles"]))
    md.append("")
    md.append("### winner counts by view x side x threshold")
    md.append("")
    md.append(df_to_md_table(a["winner_counts"]))
    md.append("")
    md.append("## Section B -- Base rates")
    md.append("")
    md.append(df_to_md_table(b))
    md.append("")
    md.append("## Section C -- Univariate")
    md.append("")
    md.append(f"Thin-coverage numeric metrics excluded from D/E (>{int(THIN_COVERAGE_NULL_FRAC * 100)}% null, "
              f"{len(c['thin_coverage'])} metrics): {[t['metric'] for t in c['thin_coverage']]}")
    md.append("")
    md.append("### Top-40 |z| pooled")
    md.append("")
    md.append(df_to_md_table(c["top40_pooled"]))
    md.append("")
    md.append("### Top-40 |z| side=C")
    md.append("")
    md.append(df_to_md_table(c["top40_C"]))
    md.append("")
    md.append("### Top-40 |z| side=P")
    md.append("")
    md.append(df_to_md_table(c["top40_P"]))
    md.append("")
    md.append("## Section D -- Factors")
    md.append("")
    md.append(f"{len(d['reps'])} clusters from {len(d['usable_numeric'])} usable numeric metrics.")
    md.append("")
    md.append(df_to_md_table(d["cluster_table"], max_rows=200))
    md.append("")
    for side in ("C", "P"):
        m = d["models"][side]
        md.append(f"side={side}: mean AUC logistic={m['mean_auc_logistic']}, mean AUC gbt={m['mean_auc_gbt']}, "
                   f"shuffled control={m['mean_shuffled_auc']} (n_permutations={m['n_permutations']}), "
                   f"base_rate={m['base_rate']}")
    md.append("")
    md.append("## Section E -- Ablation")
    md.append("")
    md.append("### E1 leave-family-out")
    md.append("")
    md.append(df_to_md_table(e1))
    md.append("")
    md.append("### E2 leave-metric-out (top-10 by |z|)")
    md.append("")
    md.append(df_to_md_table(e2))
    md.append("")
    md.append(f"### E3 rule distillation: {e3['n_candidates']} candidates generated, "
              f"{e3['n_meeting_floor']} meet N>={RULE_MIN_N} floor, {len(e3['selected'])} selected")
    md.append("")
    md.append(df_to_md_table(e3_summary) if rules_summary_rows else "(no candidate rule met the N floor)")
    md.append("")

    with open(REPO_RESULTS_TABLES_MD, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(md) + "\n")
    log(f"wrote {REPO_RESULTS_TABLES_MD}")


def print_smoke_brief_summary(a_results: dict, c_results: dict) -> None:
    log("")
    log("=" * 78)
    log("SMOKE-MODE BRIEF SUMMARY -- MACHINERY VALIDATION ONLY, NOT FINDINGS (4 hand-picked weeks)")
    log("=" * 78)
    log("")
    log("Census growth_mult quantiles (tradeable view, side=both):")
    q = a_results["quantiles"].filter((pl.col("view") == "tradeable") & (pl.col("side") == "both")).sort("expiry_year")
    for r in q.to_dicts():
        log(f"  {r['expiry_year']}: n={r['n']} p50={r['p50']:.3f} p90={r['p90']:.3f} "
            f"p99={r['p99']:.3f} p995={r['p995']:.3f} max={r['max']:.3f}")
    log("")
    log("Top-5 univariate metrics by |z| (pooled across sides):")
    for r in c_results["top40_pooled"].head(5).to_dicts():
        log(f"  {r['metric']} ({r['family']}, {r['kind']}): |z_max|={r['z_max_abs']:.3f} n={r['n_nonnull']}")
    log("")
    log("(machinery-validation only -- smoke is 4 weeks / ~13k rows, not a statistical finding)")


# ================================================================== main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="weekly_5dte_movers Stage C mining + ablation harness")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true", help="run on the smoke parquet, print self-test block")
    g.add_argument("--full", action="store_true", help="glob features/analysis_*.parquet, write outputs")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    t0 = time.time()
    log("=" * 78)
    log("weekly_5dte_movers Stage C -- analyze.py")
    log(f"mode={'SMOKE' if args.smoke else 'FULL'}  seed={SEED}")
    log(f"sklearn: {'available (' + str(SKLEARN_VERSION) + ')' if SKLEARN_AVAILABLE else 'NOT available -- degraded fallback (logistic-only, no GBT)'}")
    log("=" * 78)

    try:
        raw_df = load_smoke_frame() if args.smoke else load_full_frame()
    except AssertionError as e:
        log(f"[FAIL] 7. assert_no_holdout_leak -- {e}")
        return 1

    pop_df = add_population_flags(raw_df)
    pop_df = add_winner_flags(pop_df)
    analysis_df = pop_df.filter(pl.col("analysis_population"))
    covered_only_df = pop_df.filter(pl.col("covered_only_population"))
    log(f"loaded {pop_df.height} rows; tradeable={pop_df.filter(pl.col('tradeable')).height}; "
        f"analysis_population={analysis_df.height} (C={analysis_df.filter(pl.col('cp') == 'C').height}, "
        f"P={analysis_df.filter(pl.col('cp') == 'P').height}); "
        f"covered_only_population={covered_only_df.height}")

    log("\n--- Section A: census ---")
    a_results = section_A(pop_df)
    log(f"  quantile rows={a_results['quantiles'].height} winner_count rows={a_results['winner_counts'].height} "
        f"galleries={ {k: v.height for k, v in a_results['galleries'].items()} }")

    log("--- Section B: base rates ---")
    b_results = section_B(analysis_df)
    log(f"  rows={b_results.height}")

    log(f"--- Section C: univariate screens ({len(ALL_NUMERIC_METRICS)} numeric + "
        f"{len(ALL_CATEGORICAL_METRICS)} categorical = {TOTAL_METRICS_TESTED} metrics tested) ---")
    c_results = section_C(analysis_df)
    log(f"  numeric screens run={len(c_results['numeric_screens'])} thin_coverage={len(c_results['thin_coverage'])} "
        f"usable_numeric={len(c_results['usable_numeric'])}")

    log("--- Section D: factor abstraction ---")
    d_results = section_D(analysis_df, c_results)
    log(f"  clusters={len(d_results['reps'])} from {len(d_results['usable_numeric'])} usable numeric metrics")
    for side in ("C", "P"):
        m = d_results["models"][side]
        log(f"  side={side}: auc_logistic={m['mean_auc_logistic']} auc_gbt={m['mean_auc_gbt']} "
            f"shuffled={m['mean_shuffled_auc']} (n_perm={m['n_permutations']})")

    log("--- Section E: ablation ---")
    z_lookup = c_results["numeric_pooled_z"]
    e1_results = section_E1_leave_family_out(analysis_df, d_results, z_lookup)
    log(f"  E1 leave-family-out rows={e1_results.height}")
    e2_results = section_E2_leave_metric_out(analysis_df, d_results, c_results, z_lookup)
    log(f"  E2 leave-metric-out rows={e2_results.height}")
    e3_results = section_E3(analysis_df, covered_only_df, c_results)
    log(f"  E3 rule distillation: {e3_results['n_candidates']} candidates, "
        f"{e3_results['n_meeting_floor']} meet N>={RULE_MIN_N}, {len(e3_results['selected'])} selected")
    for g in e3_results["selected"]:
        log(f"    [{g['verdict']['verdict']}] {g['rule']['label']} (n={g['rule']['n']} lift={g['rule']['lift']:.3f})")

    elapsed = time.time() - t0

    if args.smoke:
        self_test_results = run_self_tests(raw_df, pop_df, analysis_df, c_results, d_results)
        log("\n" + "=" * 78)
        log("SELF-TEST BLOCK")
        log("=" * 78)
        for name, passed, detail in self_test_results:
            status = "PASS" if passed else "FAIL"
            log(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
        n_fail = sum(1 for _, p, _ in self_test_results if not p)
        log(f"\n{len(self_test_results) - n_fail}/{len(self_test_results)} self-tests passed. elapsed={elapsed:.1f}s")

        print_smoke_brief_summary(a_results, c_results)
        return 0 if n_fail == 0 else 1
    else:
        write_full_outputs(pop_df, analysis_df, a_results, b_results, c_results, d_results,
                            e1_results, e2_results, e3_results)
        log(f"\n--full complete. elapsed={elapsed:.1f}s")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
