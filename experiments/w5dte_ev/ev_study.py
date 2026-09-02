"""
ev_study.py -- w5dte_ev EV pricing engine (BUILD_BRIEF_EV.md).

Prices the W5DTE rule family's rule-hits under a TP ladder + expiry-settlement baseline,
and tests the resulting EV against an exposure-matched random control. Binding spec:
PREREG.md (LOCKED, same dir) -- read that first. Owner context: OWNER_SPEC.md.

CLI:
    py -3.11 ev_study.py --selftest   # 5-check self-test battery on the REAL analysis
                                       # parquets (checks 1/3/4 need the real population
                                       # to reproduce exact parent numbers); writes NOTHING.
    py -3.11 ev_study.py --full       # glob features/analysis_*.parquet, price every
                                       # arm x policy, run the control, write
                                       # B:\polygon_derived\weekly_5dte_movers\ev\ +
                                       # experiments/w5dte_ev/RESULTS.md. Queued only --
                                       # never run directly by the builder agent.

Reads ONLY the analysis_*.parquet files named in PREREG (excludes the _smoke dir).
Never reads MySQL. Writes nothing outside experiments/w5dte_ev/ and
B:\polygon_derived\weekly_5dte_movers\ev\.
"""
from __future__ import annotations

import argparse
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

from experiments._holdout import assert_no_holdout_leak  # noqa: E402

# ================================================================== constants (PREREG pins)

SEED_BASE = 20260818

FEATURES_ROOT = Path("B:/polygon_derived/weekly_5dte_movers/features")
EV_OUT_ROOT = Path("B:/polygon_derived/weekly_5dte_movers/ev")
EV_TABLES_DIR = EV_OUT_ROOT / "tables"
REPO_RESULTS_MD = os.path.join(_HERE, "RESULTS.md")

# select only needed columns (BUILD_BRIEF_EV.md "Implementation pins"). "expiry_day" is
# used for expiry_year (the contract's parsed `expiry` always equals `expiry_day` by
# ledger construction -- cu = week_df.filter(expiry == expiry_day) -- so loading both
# would be redundant; see EV_BUILD_REPORT.md decision log).
NEEDED_COLS = [
    "ticker", "underlying", "cp", "sector", "is_monthly_opex", "entry_date", "entry_dow",
    "expiry_day", "week_monday", "entry_close", "entry_volume", "entry_transactions",
    "entry_dollar_vol", "adjusted", "covered", "no_later_print", "max_future_high",
    "close_at_expiry", "otm_pct", "moneyness_pct", "hl_range_pct",
]

# Rule thresholds -- FULL-PRECISION P80 quantiles recovered from the analysis population,
# reproducing the parent's actual applied cutoff (analyze.py build_candidate_predicates:
# `hi_q = pooled.quantile(0.80)`, default interpolation='nearest'; the LABEL rounds hi_q
# to 4 sig figs via `:.4g` for display, which is the ONLY form PREREG recorded). Using the
# PREREG-displayed rounded values as literal cutoffs undercounts every rule's n by 40-90
# rows and fails the self-test's own 5e-6 tolerance -- see EV_BUILD_REPORT.md decision #1.
#   moneyness_pct P80: rounds to 0.03958 (PREREG display)
#   hl_range_pct  P80: rounds to 127.3   (PREREG display)
#   otm_pct       P80: rounds to 0.0576  (PREREG display)
MONEYNESS_P80 = 0.03958416633346662
HLRANGE_P80 = 127.27272727272727
OTM_P80 = 0.0575954184638805

TP_LEVELS = (2.0, 3.0, 5.0, 10.0)
POLICIES = ("TP2", "TP3", "TP5", "TP10", "EXPIRY")
HAIRCUT_PCT = 0.0142
HAIRCUT_FLOOR = 0.025
CELL_COLS = ["week_monday", "cp", "entry_dow"]

N_DRAWS_FULL = 100          # PREREG: FAMILY needs >=100; measured fast enough (see report)
                            # for ALL 8 arms at 100 draws -- no 25-draw fallback needed.
N_DRAWS_SELFTEST = 3

E3_TARGETS = {
    # (n, winner_rate at 5x) -- BUILD_BRIEF_EV.md self-test #1, verbatim from parent
    # RESULTS_TABLES.md E3. winner = max_future_high/entry_close >= 5.0 (DIVISION order,
    # matching how the parent actually computed growth_mult -- see decision #2).
    "R1": (25663, 0.207419), "R2": (25835, 0.200542), "R3": (25835, 0.200542),
    "R4": (33438, 0.200072), "R5": (44342, 0.193834), "R6": (44342, 0.193834),
}

RULE_LABELS = {
    "R1": 'moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND sector=="technology" AND is_monthly_opex==false',
    "R2": 'otm_pct>=0.0576 AND moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND sector=="technology"',
    "R3": 'otm_pct>=0.0576 AND hl_range_pct>=127.3 AND cp=="C" AND sector=="technology"',
    "R4": 'moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND cp=="C" AND sector=="technology"',
    "R5": 'otm_pct>=0.0576 AND moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND is_monthly_opex==false',
    "R6": 'otm_pct>=0.0576 AND hl_range_pct>=127.3 AND cp=="C" AND is_monthly_opex==false',
}

GATE_TEXT = (
    "PASS (paper tape licensed, owner lock #3) iff BOTH: "
    "(a) FAMILY TP-5x EV beats >= 99/100 control draws (empirical p <= 0.01), AND "
    "(b) FAMILY TP-5x EV > 0 after costs."
)


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ================================================================== load + population

def fill_nan_to_null(df: pl.DataFrame) -> pl.DataFrame:
    """Null policy (traps.md): fill_nan -> null ONCE at load, so no downstream .rank()
    call ever sees a real NaN (polars ranks NaN as MAX)."""
    float_cols = [c for c, t in zip(df.columns, df.dtypes) if t in (pl.Float32, pl.Float64)]
    if not float_cols:
        return df
    return df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])


def _rename_for_holdout(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(pl.col("entry_date").alias("date"))


def load_population() -> pl.DataFrame:
    """Load analysis_*.parquet (exclude _smoke), select needed columns, apply PREREG's
    population filter verbatim, assert the holdout, and add a stable _row_id + expiry_year."""
    paths = sorted(FEATURES_ROOT.glob("analysis_*.parquet"))
    paths = [p for p in paths if "_smoke" not in p.parts]
    if not paths:
        raise FileNotFoundError(f"no analysis_*.parquet files found under {FEATURES_ROOT}")
    log(f"loading {len(paths)} file(s): {[p.name for p in paths]}")
    lfs = [pl.scan_parquet(str(p)).select(NEEDED_COLS) for p in paths]
    df = pl.concat(lfs, how="vertical_relaxed").collect()
    df = fill_nan_to_null(df)
    log(f"  raw rows (covered_only view, all files): {df.height}")

    assert_no_holdout_leak(_rename_for_holdout(df), context="ev_study.py load (entry_date)")

    pop = df.filter(
        (pl.col("covered") == 1)
        & (pl.col("no_later_print") == 0)
        & (pl.col("entry_close") >= 0.20)
        & (pl.col("entry_volume") >= 100)
        & (pl.col("entry_transactions") >= 10)
        & (~pl.col("adjusted"))
    )
    pop = pop.with_columns([
        pl.int_range(0, pop.height).alias("_row_id"),
        pl.col("expiry_day").dt.year().alias("expiry_year"),
    ])
    log(f"  analysis population: {pop.height} rows "
        f"(C={pop.filter(pl.col('cp') == 'C').height}, P={pop.filter(pl.col('cp') == 'P').height})")
    return pop


# ================================================================== rule masks (PREREG-pinned)

def _ge(col: str, val: float) -> pl.Expr:
    return (pl.col(col) >= val).fill_null(False)


def _eq_str(col: str, val: str) -> pl.Expr:
    return (pl.col(col) == val).fill_null(False)


def _eq_bool(col: str, val: bool) -> pl.Expr:
    return (pl.col(col) == val).fill_null(False)


def add_rule_masks(df: pl.DataFrame) -> pl.DataFrame:
    """R1..R6 exactly as PREREG pins them (nulls in any conjunct column fail that
    conjunct, never raise -- .fill_null(False) on every leaf comparison)."""
    r1 = (_ge("moneyness_pct", MONEYNESS_P80) & _ge("hl_range_pct", HLRANGE_P80)
          & _eq_str("sector", "technology") & _eq_bool("is_monthly_opex", False))
    r2 = (_ge("otm_pct", OTM_P80) & _ge("moneyness_pct", MONEYNESS_P80)
          & _ge("hl_range_pct", HLRANGE_P80) & _eq_str("sector", "technology"))
    r3 = (_ge("otm_pct", OTM_P80) & _ge("hl_range_pct", HLRANGE_P80)
          & _eq_str("cp", "C") & _eq_str("sector", "technology"))
    r4 = (_ge("moneyness_pct", MONEYNESS_P80) & _ge("hl_range_pct", HLRANGE_P80)
          & _eq_str("cp", "C") & _eq_str("sector", "technology"))
    r5 = (_ge("otm_pct", OTM_P80) & _ge("moneyness_pct", MONEYNESS_P80)
          & _ge("hl_range_pct", HLRANGE_P80) & _eq_bool("is_monthly_opex", False))
    r6 = (_ge("otm_pct", OTM_P80) & _ge("hl_range_pct", HLRANGE_P80)
          & _eq_str("cp", "C") & _eq_bool("is_monthly_opex", False))
    df = df.with_columns([
        r1.alias("_r1"), r2.alias("_r2"), r3.alias("_r3"),
        r4.alias("_r4"), r5.alias("_r5"), r6.alias("_r6"),
    ])
    family = (pl.col("_r1") | pl.col("_r2") | pl.col("_r3")
              | pl.col("_r4") | pl.col("_r5") | pl.col("_r6"))
    df = df.with_columns(family.alias("_family"))
    df = df.with_columns((pl.col("_family") & _eq_str("cp", "C")).alias("_family_c"))
    return df


ARMS = {"FAMILY": "_family", "FAMILY_C": "_family_c",
        "R1": "_r1", "R2": "_r2", "R3": "_r3", "R4": "_r4", "R5": "_r5", "R6": "_r6"}


# ================================================================== pricing (PREREG-pinned)

def add_pricing(df: pl.DataFrame) -> pl.DataFrame:
    """Vectorized pricing per PREREG "Exit policies" / BUILD_BRIEF_EV.md pins. No python
    per-row loops. entry is free for both arms (canon); TP limit fills are cost-free;
    expiry settlement is a FORCED exit and pays the half-spread haircut."""
    settle = pl.col("close_at_expiry").fill_null(0.0)
    haircut = pl.max_horizontal(HAIRCUT_PCT * settle, pl.lit(HAIRCUT_FLOOR))
    proceeds_expiry = (
        pl.when(settle > HAIRCUT_FLOOR)
        .then(pl.max_horizontal(settle - haircut, pl.lit(0.0)))
        .otherwise(pl.lit(0.0))
    )
    df = df.with_columns([
        settle.alias("_settle"),
        proceeds_expiry.alias("_proceeds_expiry"),
    ])
    for L in TP_LEVELS:
        Li = int(L)
        tp_fill = (pl.col("max_future_high") >= L * pl.col("entry_close")).fill_null(False)
        proceeds = pl.when(tp_fill).then(L * pl.col("entry_close")).otherwise(pl.col("_proceeds_expiry"))
        r = proceeds / pl.col("entry_close") - 1.0
        df = df.with_columns([
            tp_fill.alias(f"_tp_fill_{Li}"),
            r.alias(f"_r_TP{Li}"),
        ])
    df = df.with_columns((pl.col("_proceeds_expiry") / pl.col("entry_close") - 1.0).alias("_r_EXPIRY"))
    return df


def _expected_pricing_pure_python(entry_close: float, max_future_high: float,
                                   close_at_expiry) -> dict:
    """Independent (non-polars) reimplementation of the same PREREG spec, used ONLY to
    cross-check add_pricing()'s vectorized output in self-test #2 (a second, literal
    expression -- not the code under test)."""
    settle = close_at_expiry if close_at_expiry is not None else 0.0
    if settle > HAIRCUT_FLOOR:
        haircut = max(HAIRCUT_PCT * settle, HAIRCUT_FLOOR)
        proceeds_expiry = max(settle - haircut, 0.0)
    else:
        proceeds_expiry = 0.0
    out = {}
    for L in TP_LEVELS:
        fill = max_future_high >= L * entry_close
        proceeds = (L * entry_close) if fill else proceeds_expiry
        out[f"TP{int(L)}"] = proceeds / entry_close - 1.0
    out["EXPIRY"] = proceeds_expiry / entry_close - 1.0
    return out


SYNTHETIC_ROWS = [
    {"label": "TP-5x fill", "entry_close": 1.00, "max_future_high": 6.00, "close_at_expiry": 3.00},
    {"label": "no-fill settle 0.50", "entry_close": 2.00, "max_future_high": 2.50, "close_at_expiry": 0.50},
    {"label": "no-fill settle 0.02 (sub-halftick, expires worthless)",
     "entry_close": 1.50, "max_future_high": 1.60, "close_at_expiry": 0.02},
    {"label": "null settle", "entry_close": 1.00, "max_future_high": 1.20, "close_at_expiry": None},
]


# ================================================================== exposure-matched control

def prepare_control_base(pop_df: pl.DataFrame, arm_col: str) -> "tuple[pl.DataFrame, pl.DataFrame]":
    """Hoist the arm/complement split + per-cell k out of the per-draw loop (--full calls
    this once per arm, then draw_one() per draw -- avoids re-filtering 100x)."""
    arm_df = pop_df.filter(pl.col(arm_col))
    comp_df = pop_df.filter(~pl.col(arm_col))
    k_by_cell = arm_df.group_by(CELL_COLS).agg(pl.len().alias("_k"))
    comp_with_k = comp_df.join(k_by_cell, on=CELL_COLS, how="inner")
    return arm_df, comp_with_k


def draw_one(comp_with_k: pl.DataFrame, draw_idx: int, seed_base: int = SEED_BASE) -> pl.DataFrame:
    """One control draw: assign each complement row a per-draw random key (numpy
    default_rng(seed_base + draw_idx)) and take the k smallest per cell -- an unbiased
    sample-without-replacement of size k per cell (k = arm's row count in that cell).
    Cells where the complement has < k rows automatically select ALL of them (rank <= k
    is trivially true when the cell only has < k rows) -- the PREREG shortfall case,
    no special-casing needed."""
    rng = np.random.default_rng(seed_base + draw_idx)
    tmp = comp_with_k.with_columns(pl.Series("_rand", rng.random(comp_with_k.height)))
    tmp = tmp.with_columns(pl.col("_rand").rank(method="ordinal").over(CELL_COLS).alias("_rk"))
    return tmp.filter(pl.col("_rk") <= pl.col("_k"))


def draw_control(pop_df: pl.DataFrame, arm_col: str, draw_idx: int,
                  seed_base: int = SEED_BASE) -> pl.DataFrame:
    """Single-shot convenience wrapper (self-test uses this directly; --full uses
    prepare_control_base + draw_one to avoid re-filtering per draw)."""
    _, comp_with_k = prepare_control_base(pop_df, arm_col)
    return draw_one(comp_with_k, draw_idx, seed_base)


def compute_control_draws(df: pl.DataFrame, arms: dict, n_draws: int,
                           seed_base: int = SEED_BASE) -> pl.DataFrame:
    """Per arm x draw_idx: n_selected, shortfall_cells, mean r per policy. This is the
    'control-draw parquet' -- Table C is aggregated from this."""
    rows = []
    for arm_name, arm_col in arms.items():
        t_arm = time.time()
        _, comp_with_k = prepare_control_base(df, arm_col)
        k_by_cell = df.filter(pl.col(arm_col)).group_by(CELL_COLS).agg(pl.len().alias("_k"))
        for draw_idx in range(n_draws):
            sel = draw_one(comp_with_k, draw_idx, seed_base)
            row = {"arm": arm_name, "draw_idx": draw_idx, "n_selected": sel.height}
            for policy in POLICIES:
                row[f"mean_r_{policy}"] = sel[f"_r_{policy}"].mean() if sel.height else None
            sel_counts = sel.group_by(CELL_COLS).agg(pl.len().alias("sel_n"))
            merged = (k_by_cell.join(sel_counts, on=CELL_COLS, how="left")
                      .with_columns(pl.col("sel_n").fill_null(0)))
            row["shortfall_cells"] = merged.filter(pl.col("sel_n") < pl.col("_k")).height
            rows.append(row)
        log(f"    control draws for {arm_name}: {n_draws} draws in {time.time() - t_arm:.1f}s")
    return pl.DataFrame(rows)


# ================================================================== output tables (--full)

def policy_stats(sub: pl.DataFrame, policy: str) -> dict:
    r_col = f"_r_{policy}"
    n = sub.height
    if n == 0:
        return {"n": 0, "mean_r": None, "premium_weighted_r": None, "win_rate": None,
                "median_r": None, "tp_fill_rate": None}
    mean_r = sub[r_col].mean()
    median_r = sub[r_col].median()
    win_rate = float((sub[r_col] > 0).cast(pl.Int64).mean())
    denom = sub["entry_close"].sum()
    premium_weighted_r = float((sub[r_col] * sub["entry_close"]).sum() / denom) if denom else None
    if policy != "EXPIRY":
        Lstr = policy[2:]  # "TP2" -> "2", "TP10" -> "10"
        tp_fill_rate = float(sub[f"_tp_fill_{Lstr}"].mean())
    else:
        tp_fill_rate = None
    return {"n": n, "mean_r": mean_r, "premium_weighted_r": premium_weighted_r,
            "win_rate": win_rate, "median_r": median_r, "tp_fill_rate": tp_fill_rate}


def table_A_arm_policy(df: pl.DataFrame, arms: dict) -> pl.DataFrame:
    rows = []
    for arm_name, arm_col in arms.items():
        sub = df.filter(pl.col(arm_col))
        for policy in POLICIES:
            stats = policy_stats(sub, policy)
            rows.append({"arm": arm_name, "policy": policy, **stats})
    return pl.DataFrame(rows)


def table_B_family_per_year(df: pl.DataFrame) -> pl.DataFrame:
    sub = df.filter(pl.col("_family"))
    rows = []
    for year in sorted(sub["expiry_year"].unique().to_list()):
        yr_sub = sub.filter(pl.col("expiry_year") == year)
        for policy in POLICIES:
            stats = policy_stats(yr_sub, policy)
            rows.append({"expiry_year": year, "policy": policy, **stats})
    return pl.DataFrame(rows)


def table_C_control_summary(control_draws: pl.DataFrame, table_a: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for arm_name in ARMS.keys():
        arm_draws = control_draws.filter(pl.col("arm") == arm_name)
        n_draws = arm_draws.height
        for policy in POLICIES:
            col = f"mean_r_{policy}"
            draw_vals = arm_draws[col].drop_nulls()
            control_mean = draw_vals.mean() if draw_vals.len() else None
            control_median = draw_vals.median() if draw_vals.len() else None
            control_p5 = draw_vals.quantile(0.05) if draw_vals.len() else None
            control_p95 = draw_vals.quantile(0.95) if draw_vals.len() else None
            rule_row = table_a.filter((pl.col("arm") == arm_name) & (pl.col("policy") == policy))
            rule_ev = rule_row["mean_r"][0] if rule_row.height else None
            if rule_ev is not None and draw_vals.len() > 0:
                beats_k = int((draw_vals < rule_ev).sum())
            else:
                beats_k = None
            pct = (beats_k / n_draws * 100.0) if (beats_k is not None and n_draws) else None
            rows.append({"arm": arm_name, "policy": policy, "n_draws": n_draws,
                         "control_mean": control_mean, "control_median": control_median,
                         "control_p5": control_p5, "control_p95": control_p95,
                         "rule_ev": rule_ev, "beats_k_of_n": beats_k, "rule_percentile": pct})
    return pl.DataFrame(rows)


def table_D_adjudication(table_c: pl.DataFrame) -> dict:
    row = table_c.filter((pl.col("arm") == "FAMILY") & (pl.col("policy") == "TP5"))
    if row.height == 0:
        return {"verdict": "FAIL", "reason": "FAMILY/TP5 row missing from control summary",
                "gate_text": GATE_TEXT}
    r = row.to_dicts()[0]
    beats_k, n_draws, rule_ev = r["beats_k_of_n"], r["n_draws"], r["rule_ev"]
    cond_a = (beats_k is not None) and (n_draws >= 100) and (beats_k >= 99)
    cond_b = (rule_ev is not None) and (rule_ev > 0)
    verdict = "PASS" if (cond_a and cond_b) else "FAIL"
    return {"arm": "FAMILY", "policy": "TP5", "ev": rule_ev, "beats_k_of_n": beats_k,
            "n_draws": n_draws, "cond_a_beats_99of100": cond_a, "cond_b_ev_positive": cond_b,
            "verdict": verdict, "gate_text": GATE_TEXT}


def table_E_capacity(df: pl.DataFrame) -> pl.DataFrame:
    sub = df.filter(pl.col("_family"))
    n = sub.height
    dv = sub["entry_dollar_vol"]
    row = {
        "n_family_hits": n,
        "entry_dollar_vol_p10": dv.quantile(0.10) if n else None,
        "entry_dollar_vol_p50": dv.quantile(0.50) if n else None,
        "entry_dollar_vol_p90": dv.quantile(0.90) if n else None,
        "share_below_25k": float((dv < 25000).cast(pl.Int64).mean()) if n else None,
        "median_entry_premium": sub["entry_close"].median() if n else None,
        "expiry_settle_null_share": (sub["close_at_expiry"].null_count() / n) if n else None,
    }
    return pl.DataFrame([row])


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


def write_full_outputs(df: pl.DataFrame, table_a: pl.DataFrame, table_b: pl.DataFrame,
                        control_draws: pl.DataFrame, table_c: pl.DataFrame,
                        table_d: dict, table_e: pl.DataFrame) -> None:
    EV_TABLES_DIR.mkdir(parents=True, exist_ok=True)

    def save(name: str, d: pl.DataFrame):
        d.write_parquet(EV_TABLES_DIR / f"{name}.parquet")
        with open(EV_TABLES_DIR / f"{name}.md", "w", encoding="ascii", errors="replace") as f:
            f.write(df_to_md_table(d))
        log(f"  wrote {name}.parquet + .md ({d.height} rows)")

    log(f"writing tables to {EV_TABLES_DIR}")
    save("A_arm_policy_summary", table_a)
    save("B_family_per_year", table_b)
    save("C_control_draws", control_draws)
    save("D_control_summary", table_c)
    save("E_capacity", table_e)

    md = []
    md.append("# RESULTS -- w5dte_ev EV study (machine-written by ev_study.py --full)")
    md.append("")
    md.append(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} (local), seed_base={SEED_BASE}")
    md.append(f"Analysis population: {df.height} rows; FAMILY hits: "
              f"{df.filter(pl.col('_family')).height}; FAMILY_C hits: "
              f"{df.filter(pl.col('_family_c')).height}")
    md.append("")
    md.append("## Rules under test (PREREG-pinned; full-precision P80 thresholds -- see "
               "EV_BUILD_REPORT.md decision #1)")
    md.append("")
    for name, label in RULE_LABELS.items():
        md.append(f"- {name}: {label}")
    md.append("- FAMILY = R1 OR ... OR R6; FAMILY_C = FAMILY AND cp==C")
    md.append("")
    md.append("## Table A -- arm x policy EV")
    md.append("")
    md.append(df_to_md_table(table_a))
    md.append("")
    md.append("## Table B -- FAMILY per-expiry-year x policy EV")
    md.append("")
    md.append(df_to_md_table(table_b))
    md.append("")
    md.append("## Table C -- control summary per arm x policy")
    md.append("")
    md.append(df_to_md_table(table_c))
    md.append("")
    md.append("## Table D -- adjudication (PRIMARY: FAMILY, TP-5x)")
    md.append("")
    md.append(f"Gate (verbatim, PREREG.md 'Adjudication'): {table_d['gate_text']}")
    md.append("")
    for k, v in table_d.items():
        if k != "gate_text":
            md.append(f"- {k}: {v}")
    md.append("")
    md.append("## Table E -- capacity (FAMILY hits)")
    md.append("")
    md.append(df_to_md_table(table_e))
    md.append("")

    with open(REPO_RESULTS_MD, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(md) + "\n")
    log(f"wrote {REPO_RESULTS_MD}")


# ================================================================== self-tests (--selftest)

def check1_rule_fidelity(df: pl.DataFrame) -> "tuple[bool, str]":
    """Recomputed (n, winner_rate at 5x) for R1..R6 must match parent RESULTS_TABLES.md
    E3 EXACTLY. winner uses DIVISION order (max_future_high/entry_close >= 5.0) to
    bit-match how the parent actually computed growth_mult -- see decision #2 in
    EV_BUILD_REPORT.md (the algebraically-equivalent multiplication form used for real
    TP-fill pricing disagrees with growth_mult on 41/1,341,534 boundary rows, enough to
    blow the 5e-6 tolerance on 4/6 rules)."""
    e3_winner = ((pl.col("max_future_high") / pl.col("entry_close")) >= 5.0).fill_null(False)
    df2 = df.with_columns(e3_winner.alias("_e3_winner_5x"))
    ok = True
    details = []
    for name in ("R1", "R2", "R3", "R4", "R5", "R6"):
        col = "_" + name.lower()
        sub = df2.filter(pl.col(col))
        n = sub.height
        rate = sub["_e3_winner_5x"].mean() if n else None
        tgt_n, tgt_rate = E3_TARGETS[name]
        ok_n = (n == tgt_n)
        ok_rate = (rate is not None) and (abs(rate - tgt_rate) < 5e-6)
        ok = ok and ok_n and ok_rate
        rate_str = f"{rate:.6f}" if rate is not None else "None"
        details.append(f"{name} n={n}(tgt {tgt_n}{'OK' if ok_n else 'MISMATCH'}) "
                        f"rate={rate_str}(tgt {tgt_rate}{'OK' if ok_rate else 'MISMATCH'})")
    return ok, "; ".join(details)


def check2_synthetic_pricing() -> "tuple[bool, str]":
    syn_df = pl.DataFrame({
        "entry_close": [r["entry_close"] for r in SYNTHETIC_ROWS],
        "max_future_high": [r["max_future_high"] for r in SYNTHETIC_ROWS],
        "close_at_expiry": [r["close_at_expiry"] for r in SYNTHETIC_ROWS],
    })
    priced = add_pricing(syn_df)
    ok = True
    mismatches = []
    n_checked = 0
    for i, row in enumerate(SYNTHETIC_ROWS):
        expected = _expected_pricing_pure_python(row["entry_close"], row["max_future_high"],
                                                   row["close_at_expiry"])
        for policy in POLICIES:
            got = priced[f"_r_{policy}"][i]
            exp = expected[policy]
            n_checked += 1
            row_ok = got is not None and abs(got - exp) < 1e-9
            ok = ok and row_ok
            if not row_ok:
                mismatches.append(f"{row['label']}/{policy}: got={got} expected={exp}")
    detail = f"{n_checked} (row x policy) comparisons, all exact" if ok else "; ".join(mismatches)
    return ok, detail


def check3_tp_monotonicity(df: pl.DataFrame) -> "tuple[bool, str]":
    rates = {int(L): df[f"_tp_fill_{int(L)}"].mean() for L in TP_LEVELS}
    ok = rates[2] > rates[3] > rates[5] > rates[10]
    detail = f"2x={rates[2]:.6f} 3x={rates[3]:.6f} 5x={rates[5]:.6f} 10x={rates[10]:.6f}"
    return ok, detail


def check4_control_integrity(df: pl.DataFrame) -> "tuple[bool, str]":
    arm_col = "_family"
    arm_df = df.filter(pl.col(arm_col))
    draws = [draw_control(df, arm_col, i, SEED_BASE) for i in range(N_DRAWS_SELFTEST)]
    arm_cell_counts = arm_df.group_by(CELL_COLS).agg(pl.len().alias("k"))
    comp_cell_counts = df.filter(~pl.col(arm_col)).group_by(CELL_COLS).agg(pl.len().alias("comp_n"))
    ok = True
    details = []
    for i, sel in enumerate(draws):
        sel_counts = sel.group_by(CELL_COLS).agg(pl.len().alias("sel_n"))
        merged = (arm_cell_counts.join(sel_counts, on=CELL_COLS, how="left")
                  .join(comp_cell_counts, on=CELL_COLS, how="left")
                  .with_columns(pl.col("sel_n").fill_null(0), pl.col("comp_n").fill_null(0)))
        non_shortfall = merged.filter(pl.col("comp_n") >= pl.col("k"))
        shortfall = merged.filter(pl.col("comp_n") < pl.col("k"))
        mismatch_ns = non_shortfall.filter(pl.col("sel_n") != pl.col("k")).height
        mismatch_sf = shortfall.filter(pl.col("sel_n") != pl.col("comp_n")).height
        overlap = sel.join(arm_df.select("_row_id"), on="_row_id", how="inner").height
        draw_ok = (mismatch_ns == 0) and (mismatch_sf == 0) and (overlap == 0)
        ok = ok and draw_ok
        details.append(f"draw{i}: sel={sel.height} shortfall_cells={shortfall.height} "
                        f"mismatch={mismatch_ns + mismatch_sf} overlap={overlap}")
    set0 = set(draws[0]["_row_id"].to_list())
    set1 = set(draws[1]["_row_id"].to_list())
    different = set0 != set1
    ok = ok and different
    details.append(f"draw0!=draw1 row sets differ: {different} (symdiff={len(set0 ^ set1)})")
    return ok, "; ".join(details)


def run_self_tests(df: pl.DataFrame) -> list:
    results = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, bool(passed), detail))

    ok1, d1 = check1_rule_fidelity(df)
    check("1. Rule fidelity (R1..R6 n + winner_rate@5x match parent E3 exactly)", ok1, d1)

    ok2, d2 = check2_synthetic_pricing()
    check("2. Pricing on synthetic rows (TP-5x fill; settle 0.50; settle 0.02; null settle)", ok2, d2)

    ok3, d3 = check3_tp_monotonicity(df)
    check("3. TP monotonicity: fill_rate(2x) > fill_rate(3x) > fill_rate(5x) > fill_rate(10x)", ok3, d3)

    ok4, d4 = check4_control_integrity(df)
    check("4. Control integrity on 3 draws (FAMILY arm): per-cell counts, zero overlap, distinct draws", ok4, d4)

    # 5. assert_no_holdout_leak passed on load -- reaching this point without an
    # AssertionError during load_population() IS the pass (main() wraps the load call
    # and would already have reported [FAIL] + exited if it raised).
    check("5. assert_no_holdout_leak passed on load", True, "no exception raised during load")

    return results


# ================================================================== main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="w5dte_ev EV pricing engine")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true",
                    help="5-check self-test battery on the real analysis parquets; writes nothing")
    g.add_argument("--full", action="store_true",
                    help="full run: price all arms/policies, run the control, write outputs")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    t0 = time.time()
    log("=" * 78)
    log("w5dte_ev -- ev_study.py")
    log(f"mode={'SELFTEST' if args.selftest else 'FULL'}  seed_base={SEED_BASE}")
    log("=" * 78)

    try:
        pop = load_population()
    except AssertionError as e:
        log(f"[FAIL] 5. assert_no_holdout_leak -- {e}")
        return 1

    pop = add_rule_masks(pop)
    pop = add_pricing(pop)
    for name, col in ARMS.items():
        n = pop.filter(pl.col(col)).height
        log(f"  arm {name}: n={n}")

    elapsed_load = time.time() - t0

    if args.selftest:
        self_test_results = run_self_tests(pop)
        log("\n" + "=" * 78)
        log("SELF-TEST BLOCK")
        log("=" * 78)
        for name, passed, detail in self_test_results:
            status = "PASS" if passed else "FAIL"
            log(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
        n_fail = sum(1 for _, p, _ in self_test_results if not p)
        log(f"\n{len(self_test_results) - n_fail}/{len(self_test_results)} self-tests passed. "
            f"elapsed={time.time() - t0:.1f}s")
        log("\n--selftest writes nothing (no B:\\...\\ev\\ output, no RESULTS.md).")
        return 0 if n_fail == 0 else 1
    else:
        log("\n--- pricing tables ---")
        table_a = table_A_arm_policy(pop, ARMS)
        log(f"  Table A: {table_a.height} rows")
        table_b = table_B_family_per_year(pop)
        log(f"  Table B: {table_b.height} rows")

        log("\n--- exposure-matched control (this is the slow part) ---")
        control_draws = compute_control_draws(pop, ARMS, N_DRAWS_FULL, SEED_BASE)
        log(f"  control draws: {control_draws.height} rows "
            f"({len(ARMS)} arms x {N_DRAWS_FULL} draws)")

        table_c = table_C_control_summary(control_draws, table_a)
        table_d = table_D_adjudication(table_c)
        log(f"\n  ADJUDICATION: {table_d}")

        table_e = table_E_capacity(pop)

        write_full_outputs(pop, table_a, table_b, control_draws, table_c, table_d, table_e)
        log(f"\n--full complete. elapsed={time.time() - t0:.1f}s (load+setup {elapsed_load:.1f}s)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
