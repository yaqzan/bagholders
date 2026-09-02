"""Phase 2 addendum -- 12-cell regime cross (VIX band x McClellan sign, DESIGN.md S3e)
as a SECONDARY grouping axis for candidates 1-3, per DESIGN.md S9 Phase 2 compute plan
item (b): "the 12-cell regime cross from S3e as an additional grouping axis alongside
the existing (signal_type, score_bin) cohorts."

Descriptive/exploratory only -- does NOT change the pre-registered pooled/interleaved-half
ADVANCES bar (that bar is defined on the full population and the interleaved-year split,
not on individual regime cells). Reuses clustered_test/ols_with_clustered_se verbatim by
importing from mine_candidates.py (same directory, no reinvention).

PYTHONUTF8=1 required (ASCII-only prints). Foreground-safe: reads the existing ledger
parquet (<1min) + pure numpy/polars aggregation over ~9-12 small cells x 3 candidates.
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import polars as pl
# NOTE: importing mine_candidates performs its own sys.stdout UTF-8 rewrap as a
# module-level side effect -- do NOT also rewrap here (double-wrapping a
# colorama-intercepted stream on Windows raises "I/O operation on closed file").
from mine_candidates import (  # noqa: E402  -- reuse verbatim, no reinvention
    clustered_test, CONTROL_COLS, VIX_PANIC, MIN_CELL_N_FLOOR, MIN_CELL_N_SCREEN,
    SPREAD_TILT_LO, SPREAD_TILT_BAND_LO, SPREAD_TILT_BAND_HI,
)

RESULTS_DIR = os.path.join(_HERE, "results")
OUT_TXT = os.path.join(RESULTS_DIR, "regime_cell_cross.txt")
OUT_JSON = os.path.join(RESULTS_DIR, "regime_cell_cross.json")

VIX_BANDS = ["calm", "slowbleed", "elevated", "panic"]   # panic excluded from every candidate pop by construction (commitment 4)
MCC_SIGNS = ["neg", "flat", "pos"]


def log(msg):
    print(msg, flush=True)


def cell_cross(df: pl.DataFrame, treat_col: str, out_lines: list, out_json_key: str, out_json: dict):
    cells = {}
    for vb in VIX_BANDS:
        for mc in MCC_SIGNS:
            sub = df.filter((pl.col("vix_band") == vb) & (pl.col("mcc_sign") == mc))
            n_raw = sub.height
            if n_raw < MIN_CELL_N_FLOOR:
                cells[f"{vb}x{mc}"] = {"n": n_raw, "skipped": True}
                out_lines.append("    [%s x %s]: SKIPPED (N=%d < floor %d)\n" % (vb, mc, n_raw, MIN_CELL_N_FLOOR))
                continue
            r = clustered_test(sub, treat_col, "exit_return", CONTROL_COLS)
            cells[f"{vb}x{mc}"] = r
            if r.get("skipped"):
                out_lines.append("    [%s x %s]: SKIPPED (N=%d < floor %d after drop_nulls)\n" % (vb, mc, r.get("n", 0), MIN_CELL_N_FLOOR))
            else:
                below_screen = r["n"] < MIN_CELL_N_SCREEN
                flag = " (below practical-default N=%d, floor-only)" % MIN_CELL_N_SCREEN if below_screen else ""
                nt = r.get("n_treated")
                out_lines.append("    [%s x %s]: N=%d%s%s t_clust=%+.2f%s\n" %
                                  (vb, mc, r["n"], (" N_treated=%d" % nt) if nt is not None else "", "", r["t_clust"], flag))
    out_json[out_json_key] = cells
    return cells


def main():
    t0 = time.time()
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    ledger_path = os.path.join(RESULTS_DIR, f"ledger_v{av.id}.parquet")
    df = pl.read_parquet(ledger_path)
    log("=== Phase 2 addendum: 12-cell regime cross (VIX band x McClellan sign) -- loaded %d rows ===" % df.height)

    out_lines = ["=== miss_regime_fakeout Phase 2 addendum: 12-cell regime cross (DESIGN.md S3e) ===\n",
                 "descriptive/exploratory secondary grouping axis -- does NOT alter the pooled/interleaved-half ADVANCES bar\n",
                 "ledger: %s (%d rows)\n" % (ledger_path, df.height),
                 "SKIP<%d (absolute floor); N<%d flagged as below the practical-default screening N\n\n" % (MIN_CELL_N_FLOOR, MIN_CELL_N_SCREEN)]
    out_json = {"meta": {"version": av.id, "ledger_rows": df.height}}

    df_nonpanic = df.filter((pl.col("vix").is_null()) | (pl.col("vix") < VIX_PANIC))
    calls_nonpanic = df_nonpanic.filter(pl.col("signal_type") == "CALL")

    # ---- Candidate 1 population: CALLS, non-panic ----
    log("\ncandidate_1_reversal_volume_guard_CALLS x regime cell:")
    out_lines.append("--- candidate_1_reversal_volume_guard_CALLS (treat=c1_is_reversal) x regime cell ---\n")
    cell_cross(calls_nonpanic, "c1_is_reversal", out_lines, "candidate_1_reversal_volume_guard_CALLS", out_json)

    # ---- Candidate 2 population: CALLS admission zone [68,77], SPREAD_TILT carve-out (A7) ----
    tilted_mask = (
        (pl.col("overall") >= SPREAD_TILT_BAND_LO) & (pl.col("overall") <= SPREAD_TILT_BAND_HI) &
        pl.col("c2_spread").is_not_null() & (pl.col("c2_spread") > SPREAD_TILT_LO)
    )
    c2_zone_full = calls_nonpanic.filter(pl.col("c2_admission_zone") & pl.col("c2_spread").is_not_null())
    c2_carved = c2_zone_full.filter(~tilted_mask)
    log("\ncandidate_2_admission_boundary_spread_CARVED x regime cell:")
    out_lines.append("\n--- candidate_2_admission_boundary_spread_CARVED (treat=c2_spread, continuous) x regime cell ---\n")
    cell_cross(c2_carved, "c2_spread", out_lines, "candidate_2_admission_boundary_spread_CARVED", out_json)

    # ---- Candidate 3 population: CALLS, non-panic, pre_regime known ----
    c3_pop = calls_nonpanic.filter(pl.col("c3_near_gate_dist").is_not_null())
    c3_pop = c3_pop.with_columns((pl.col("c3_near_gate_dist") <= 2).alias("c3_near_gate_flag"))
    log("\ncandidate_3_regime_boundary_crossing_CALLS x regime cell:")
    out_lines.append("\n--- candidate_3_regime_boundary_crossing_CALLS (treat=c3_near_gate_flag) x regime cell ---\n")
    cell_cross(c3_pop, "c3_near_gate_flag", out_lines, "candidate_3_regime_boundary_crossing_CALLS", out_json)

    # note: panic band is empty by construction (commitment 4 excludes VIX>=28 from every candidate population)
    note = ("\nNOTE: 'panic' VIX-band cells are empty by construction -- commitment 4 (DESIGN.md S6) excludes "
            "VIX>=28 from every candidate's own base population, so only 3 vix_band x 3 mcc_sign = 9 cells "
            "(of the nominal 12) can ever be populated here.\n")
    out_lines.append(note)
    log(note.strip())

    elapsed = time.time() - t0
    out_lines.append("\n=== DONE in %.1fs ===\n" % elapsed)
    out_json["meta"]["elapsed_s"] = round(elapsed, 1)

    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(out_lines)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump(out_json, f, indent=2, default=str)
    log("\nwrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)
    log("=== DONE in %.1fs ===" % elapsed)


if __name__ == "__main__":
    main()
