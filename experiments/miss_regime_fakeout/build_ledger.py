"""Phase 1 -- ledger build for candidates 1-3 (AMENDED per A2/A6/A8/A9=queue discipline).

Extends experiments/miss_ledger/build_ledger.py's join pattern (Score components
+ weight_info features + 30dte_apex w=15 barrier outcome + breadth) with NEW
proxy features for the three LICENSED candidates (DESIGN.md A2):

  c1 -- reversal-class (ABSORPTION/CLIMAX) volume-blend transition guard proxy:
        is_reversal = volume_signal in (ABSORPTION,CLIMAX) AND volume_magnitude>0
        (the EXACT boolean gate condition volume_amplifier.py uses to switch to
        the blend model -- literal ratio_50 distance-to-threshold is NOT
        persisted historically on Score/weight_info, so this is the code-faithful
        substitute; documented, not an escalation -- see run notes.)
  c2 -- admission-boundary (68-77) component-disagreement guard proxy:
        spread = sqrt(pop-variance of [trend,bb,rsi,macd,stoch]/5), EXACT
        SPREAD_TILT formula (backtest_cascade.py _spread_tilt_load), reused verbatim.
  c3 -- regime-multiplier reapplication boundary-crossing guard proxy:
        pre_regime (from weight_info JSON) + regime_multiplier (Score column) ->
        derived post_regime (intraday_diagnostics._post_regime_value formula) ->
        near_gate_dist = min(|pre_regime-70|,|pre_regime-75|); actual crossing flags.

Scope: v74 (active), date 2016-01-01..2026-06-15 (holdout cutoff), overall>=68
OR overall<=25 (the c2 68-69 sliver needs the floor at 68, not build_ledger.py's 70).

A6: calls experiments._holdout.assert_no_holdout_leak on the built frame before
writing. Never HOLDOUT_DISABLE.

Outputs:
  .cache/experiment_data/miss_regime_fakeout_scores_v{ver}.parquet   (raw MySQL pull, bulk_cache)
  experiments/miss_regime_fakeout/results/ledger_v{ver}.parquet      (final joined+featured ledger)
  experiments/miss_regime_fakeout/results/phase1_build.txt / .json   (build report)

PYTHONUTF8=1 required (ASCII-only prints). Queued per A8 (market hours -> normal
priority, --db heavy --restartable --dedup miss_regime_fakeout_ledger).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import polars as pl

from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

RESULTS_DIR = os.path.join(_HERE, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

START_DATE = "2016-01-01"   # matches DESIGN.md S3e / S9 scope
END_DATE = HOLDOUT_CUTOFF.isoformat()   # 2026-06-15, single source of truth
BARRIER_SET = "30dte_apex"
W_DAYS = 15
ADMISSION_ZONE_LO, ADMISSION_ZONE_HI = 68, 77   # candidate 2 scope
GATE_70, GATE_75 = 70, 75                        # candidate 3 gates

# regime-cell bucketing, EXACT copy of DESIGN.md S3e cell definitions
def vix_band(v):
    if v is None:
        return None
    if v < 20:
        return "calm"
    if v < 26:
        return "slowbleed"
    if v < 28:
        return "elevated"
    return "panic"


def mcc_sign(m):
    if m is None:
        return None
    if m > 22:
        return "pos"
    if m < -22:
        return "neg"
    return "flat"


def post_regime_value(pre_regime, mult):
    """EXACT copy of intraday_diagnostics._post_regime_value's formula."""
    if pre_regime is None:
        return None
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adjusted = 50 + (pre_regime - 50) * mult
    else:
        adjusted = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adjusted))))


def log(msg):
    print(msg, flush=True)


def fetch_scores(version_id: int) -> pl.DataFrame:
    """Pull Score rows (candidate population) from MySQL, year-chunked, bulk_cache'd."""
    from database.bulk_cache import materialize_polars, chunked_query_by_year

    cache_name = f"miss_regime_fakeout_scores_v{version_id}"

    def _build():
        t0 = time.time()
        rows = chunked_query_by_year(
            """
            SELECT symbol, date, overall, bb, trend, rsi, macd, stoch, technical_alignment,
                   volume_signal, volume_magnitude, regime_composite, regime_multiplier, weight_info
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{y_start}' AND date <= '{y_end}'
              AND overall IS NOT NULL
              AND (overall >= 68 OR overall <= 25)
            """,
            start_year=2016, end_year=2027, vid=version_id,
        )
        log("  [build] raw fetch: %d rows in %.1fs" % (len(rows), time.time() - t0))
        cols = ["symbol", "date", "overall", "bb", "trend", "rsi", "macd", "stoch", "ta",
                "vol_sig", "vol_mag", "reg_comp", "reg_mult", "weight_info"]
        data = {c: [] for c in cols}
        for r in rows:
            sym, d, ov, bb, tr, rs, mc, st, ta, vs, vm, rc, rm, wi = r
            data["symbol"].append(sym)
            data["date"].append(d.isoformat() if hasattr(d, "isoformat") else str(d))
            data["overall"].append(int(ov))
            data["bb"].append(int(bb) if bb is not None else None)
            data["trend"].append(int(tr) if tr is not None else None)
            data["rsi"].append(int(rs) if rs is not None else None)
            data["macd"].append(int(mc) if mc is not None else None)
            data["stoch"].append(int(st) if st is not None else None)
            data["ta"].append(int(ta) if ta is not None else None)
            data["vol_sig"].append(vs or "NEUTRAL")
            data["vol_mag"].append(float(vm) if vm is not None else 0.0)
            data["reg_comp"].append(float(rc) if rc is not None else None)
            data["reg_mult"].append(float(rm) if rm is not None else None)
            data["weight_info"].append(wi)
        return pl.DataFrame(data)

    path = materialize_polars(cache_name, _build)
    return pl.read_parquet(path)


def extract_pre_regime(weight_info_list):
    out = []
    for wi in weight_info_list:
        if not wi:
            out.append(None)
            continue
        try:
            d = json.loads(wi)
            pr = d.get("pre_regime")
            out.append(int(pr) if pr is not None else None)
        except Exception:
            out.append(None)
    return out


def fetch_barrier_outcomes(symbols_needed=None):
    """DuckDB read: 30dte_apex w=15 both sides, full history (fast, in-memory)."""
    import duckdb
    t0 = time.time()
    con = duckdb.connect(os.path.join(_ROOT, ".cache", "barrier_outcomes.duckdb"), read_only=True)
    df = con.execute(
        """
        SELECT symbol, date, side, result, exit_return, mae_pct, mfe_pct, entry_close, sigma_pct
        FROM barrier_outcomes
        WHERE barrier_set = ? AND w_days = ?
        """,
        [BARRIER_SET, W_DAYS],
    ).pl()
    con.close()
    log("  [build] barrier_outcomes (%s w=%d, both sides): %d rows in %.1fs" %
        (BARRIER_SET, W_DAYS, df.height, time.time() - t0))
    return df


def fetch_regime_breadth():
    from database.trader_database import DB
    vr = DB.execute_sql("SELECT date, vix_close FROM market_regime WHERE vix_close IS NOT NULL").fetchall()
    br = DB.execute_sql("SELECT date, mcclellan_oscillator, trin FROM market_breadth").fetchall()
    viz = pl.DataFrame({
        "date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in vr],
        "vix": [float(r[1]) for r in vr],
    })
    brd = pl.DataFrame({
        "date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in br],
        "mcc": [float(r[1]) if r[1] is not None else None for r in br],
        "trin": [float(r[2]) if r[2] is not None else None for r in br],
    })
    return viz, brd


def main():
    t_start = time.time()
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    log("=== Phase 1: miss_regime_fakeout ledger build (v%d, %s) ===" % (av.id, av.git_commit))
    log("window: %s..%s (holdout cutoff)" % (START_DATE, END_DATE))

    # ---- 1. Score population ----
    raw = fetch_scores(av.id)
    raw = raw.filter((pl.col("date") >= START_DATE) & (pl.col("date") <= END_DATE))
    log("[build] scope-filtered population: %d rows" % raw.height)

    # pre_regime from weight_info JSON (Python-side parse)
    pre_regime_vals = extract_pre_regime(raw["weight_info"].to_list())
    raw = raw.with_columns(pl.Series("pre_regime", pre_regime_vals, dtype=pl.Int64))
    raw = raw.drop("weight_info")

    # ---- 2. side / signal_type ----
    raw = raw.with_columns(
        pl.when(pl.col("overall") >= 68).then(pl.lit("low"))
          .when(pl.col("overall") <= 25).then(pl.lit("high"))
          .otherwise(None).alias("side"),
        pl.when(pl.col("overall") >= 68).then(pl.lit("CALL"))
          .when(pl.col("overall") <= 25).then(pl.lit("PUT"))
          .otherwise(None).alias("signal_type"),
    )

    # ---- 3. candidate proxy features ----
    # c1: reversal-class volume-blend guard (exact code-condition boolean)
    raw = raw.with_columns(
        (pl.col("vol_sig").is_in(["ABSORPTION", "CLIMAX"]) & (pl.col("vol_mag") > 0))
            .alias("c1_is_reversal"),
    )
    raw = raw.with_columns(
        pl.when(pl.col("c1_is_reversal")).then(pl.col("vol_mag")).otherwise(None).alias("c1_reversal_mag"),
    )

    # c2: admission-boundary component-disagreement (SPREAD_TILT formula, verbatim)
    members = ["trend", "bb", "rsi", "macd", "stoch"]
    mean5 = sum(pl.col(c) for c in members) / 5.0
    var5 = sum((pl.col(c) - mean5) ** 2 for c in members) / 5.0
    raw = raw.with_columns(var5.sqrt().alias("c2_spread"))
    raw = raw.with_columns(
        ((pl.col("overall") >= ADMISSION_ZONE_LO) & (pl.col("overall") <= ADMISSION_ZONE_HI))
            .alias("c2_admission_zone"),
    )

    # c3: regime-multiplier boundary-crossing guard
    post_regime_vals = [
        post_regime_value(pr, rm) for pr, rm in zip(raw["pre_regime"].to_list(), raw["reg_mult"].to_list())
    ]
    raw = raw.with_columns(pl.Series("post_regime", post_regime_vals, dtype=pl.Int64))
    raw = raw.with_columns(
        pl.when(pl.col("pre_regime").is_not_null())
          .then(pl.min_horizontal((pl.col("pre_regime") - GATE_70).abs(), (pl.col("pre_regime") - GATE_75).abs()))
          .otherwise(None).alias("c3_near_gate_dist"),
    )
    raw = raw.with_columns(
        (
            ((pl.col("pre_regime") < GATE_70) & (pl.col("post_regime") >= GATE_70)) |
            ((pl.col("pre_regime") >= GATE_70) & (pl.col("post_regime") < GATE_70))
        ).alias("c3_cross_70"),
        (
            ((pl.col("pre_regime") < GATE_75) & (pl.col("post_regime") >= GATE_75)) |
            ((pl.col("pre_regime") >= GATE_75) & (pl.col("post_regime") < GATE_75))
        ).alias("c3_cross_75"),
    )

    # generic boundary-distance proxies
    raw = raw.with_columns(
        (pl.col("overall") - GATE_70).alias("boundary_dist_70"),
        (pl.col("overall") - GATE_75).alias("boundary_dist_75"),
    )

    # ---- 4. barrier outcome join (DuckDB direct read, then polars join) ----
    barriers = fetch_barrier_outcomes()
    raw2 = raw.with_columns(pl.col("date").cast(pl.Utf8))
    barriers2 = barriers.with_columns(pl.col("date").cast(pl.Utf8))
    df = raw2.join(barriers2, on=["symbol", "date", "side"], how="inner")
    log("[build] joined with barrier_outcomes: %d rows (from %d population)" % (df.height, raw.height))

    # ---- 5. regime/breadth join ----
    viz, brd = fetch_regime_breadth()
    df = df.join(viz, on="date", how="left").join(brd, on="date", how="left")
    df = df.with_columns(
        pl.col("vix").map_elements(vix_band, return_dtype=pl.Utf8).alias("vix_band"),
        pl.col("mcc").map_elements(mcc_sign, return_dtype=pl.Utf8).alias("mcc_sign"),
    )

    # ---- 6. outcome labels ----
    df = df.with_columns(
        pl.when(pl.col("result") == 1).then(pl.lit("TP"))
          .when(pl.col("result") == 0).then(pl.lit("SL"))
          .otherwise(pl.lit("EXP")).alias("outcome"),
        pl.when(pl.col("result") == 1).then(0).otherwise(1).alias("is_miss"),
    )

    # ---- 7. year / era / interleaved-half columns (Phase 4 scaffolding, built now) ----
    df = df.with_columns(pl.col("date").str.slice(0, 4).cast(pl.Int64).alias("year"))
    df = df.with_columns(
        pl.when(pl.col("year") % 2 == 0).then(pl.lit("even")).otherwise(pl.lit("odd")).alias("interleave_half"),
        pl.when(pl.col("year") <= 2021).then(pl.lit("2016_2021")).otherwise(pl.lit("2022_2026")).alias("era_half"),
    )

    # ---- 8. score bins (miss_ledger convention) ----
    df = df.with_columns(
        pl.when(pl.col("overall") >= 95).then(pl.lit("95+"))
          .when(pl.col("overall") >= 90).then(pl.lit("90-94"))
          .when(pl.col("overall") >= 85).then(pl.lit("85-89"))
          .when(pl.col("overall") >= 80).then(pl.lit("80-84"))
          .when(pl.col("overall") >= 75).then(pl.lit("75-79"))
          .when(pl.col("overall") >= 70).then(pl.lit("70-74"))
          .when(pl.col("overall") >= 68).then(pl.lit("68-69"))
          .when(pl.col("overall") <= 5).then(pl.lit("0-5"))
          .when(pl.col("overall") <= 10).then(pl.lit("6-10"))
          .when(pl.col("overall") <= 15).then(pl.lit("11-15"))
          .when(pl.col("overall") <= 20).then(pl.lit("16-20"))
          .when(pl.col("overall") <= 25).then(pl.lit("21-25"))
          .otherwise(None).alias("score_bin"),
    )

    # fill_nan(None) before any rank/join/drop_nulls downstream consumers do (house rule)
    float_cols = [c for c, t in zip(df.columns, df.dtypes) if t in (pl.Float64, pl.Float32)]
    df = df.with_columns([pl.col(c).fill_nan(None) for c in float_cols])

    log("[build] final ledger: %d rows, %d cols" % (df.height, len(df.columns)))
    log("[build] outcome distribution:")
    log(str(df.group_by("outcome").len().sort("outcome")))
    log("[build] signal_type x outcome:")
    log(str(df.group_by(["signal_type", "outcome"]).len().sort(["signal_type", "outcome"])))

    # ---- A6: MANDATORY holdout guard on the built frame ----
    assert_no_holdout_leak(df, "miss_regime_fakeout_phase1_ledger")
    log("[build] assert_no_holdout_leak PASSED (max date <= cutoff %s)" % HOLDOUT_CUTOFF.isoformat())

    # ---- write ----
    out_pq = os.path.join(RESULTS_DIR, f"ledger_v{av.id}.parquet")
    df.write_parquet(out_pq)
    log("[build] wrote %s (%d rows, %d cols)" % (out_pq, df.height, len(df.columns)))

    # candidate coverage report
    n_c1 = int(df["c1_is_reversal"].sum())
    n_c2 = int(df["c2_admission_zone"].sum())
    n_c2_spread_ok = int(df.filter(pl.col("c2_admission_zone") & pl.col("c2_spread").is_not_null()).height)
    n_c3_near2 = int(df.filter(pl.col("c3_near_gate_dist") <= 2).height)
    n_c3_cross70 = int(df["c3_cross_70"].sum())
    n_c3_cross75 = int(df["c3_cross_75"].sum())

    report = {
        "version": av.id, "commit": av.git_commit,
        "window": [START_DATE, END_DATE],
        "n_population": raw.height,
        "n_joined": df.height,
        "n_c1_reversal": n_c1,
        "n_c2_admission_zone": n_c2,
        "n_c2_admission_zone_spread_ok": n_c2_spread_ok,
        "n_c3_near_gate_leq2": n_c3_near2,
        "n_c3_cross_70": n_c3_cross70,
        "n_c3_cross_75": n_c3_cross75,
        "year_range": [int(df["year"].min()), int(df["year"].max())],
        "elapsed_s": round(time.time() - t_start, 1),
    }
    out_json = os.path.join(RESULTS_DIR, "phase1_build.json")
    with open(out_json, "w", encoding="ascii") as f:
        json.dump(report, f, indent=2, default=str)
    out_txt = os.path.join(RESULTS_DIR, "phase1_build.txt")
    with open(out_txt, "w", encoding="ascii", errors="replace") as f:
        f.write("=== Phase 1 ledger build report ===\n")
        for k, v in report.items():
            f.write("%s: %s\n" % (k, v))

    log("[build] candidate coverage: c1_reversal=%d  c2_admission_zone=%d (spread_ok=%d)  "
        "c3_near_gate<=2=%d  c3_cross70=%d  c3_cross75=%d" %
        (n_c1, n_c2, n_c2_spread_ok, n_c3_near2, n_c3_cross70, n_c3_cross75))
    log("wrote %s" % out_json)
    log("wrote %s" % out_txt)
    log("=== DONE in %.1fs ===" % (time.time() - t_start))


if __name__ == "__main__":
    main()
