# Overlap-robustness check for the single surviving A2 cell:
#   mkt_gex_ratio -> cohort_net_path, week-clustered tc = +3.13 (halves stable).
# Defect under test: cohort_net_path is a 15-CALENDAR-day forward label aggregated daily, so
# adjacent daily labels overlap ~90%; ISO-week clusters (1w) are shorter than the label overlap
# horizon (~3w) and under-correct the SEs. Re-cluster with 3-week blocks (>= label horizon).
# Pass rule (declared before running): the cell survives ONLY if 3-week-block |t| >= 2.5.
import os, sys
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spy_gex_test as S  # noqa: E402

CONTROLS = getattr(S, "CONTROL_COLS", ["vix_close", "vix_chg5", "mcclellan_oscillator", "trin", "breadth_ema"])


def main():
    spy_df, controls_df, _ = S.build_controls()          # cache hits, no MySQL
    market_panel = spy_df.join(controls_df, on="date", how="inner")
    a2_daily, status = S.build_a2_daily_series()
    assert status.get("status") == "real", status
    panel = a2_daily.join(market_panel, on="date", how="inner")
    net_path_daily, _pnl = S.build_a2_cohort_labels()
    panel = panel.join(net_path_daily.select(["date", "cohort_net_path"]), on="date", how="left")

    # 1) reproduce the harness cell exactly (sanity: must match tc ~ +3.13)
    base = S.run_ols_one_series(panel, "cohort_net_path", "mkt_gex_ratio", CONTROLS)
    print("reproduce  week-clustered: n=%d beta=%.4f t_plain=%.2f t_clust=%.2f g=%d"
          % (base["n"], base["beta"], base["t_plain"], base["t_clust"], base["n_week_clusters"]))

    # 2) identical pipeline, cluster key = 3-ISO-week blocks
    orig_key = S.iso_week_key

    def block3_key(dates):
        wk = orig_key(dates)
        uniq = np.unique(wk)
        ordmap = {k: i for i, k in enumerate(uniq.tolist())}
        return np.array([ordmap[k] // 3 for k in wk.tolist()], dtype=np.int64)

    S.iso_week_key = block3_key
    try:
        blk = S.run_ols_one_series(panel, "cohort_net_path", "mkt_gex_ratio", CONTROLS)
    finally:
        S.iso_week_key = orig_key
    print("3wk-block  clustered:      n=%d beta=%.4f t_plain=%.2f t_clust=%.2f g=%d"
          % (blk["n"], blk["beta"], blk["t_plain"], blk["t_clust"], blk["n_week_clusters"]))
    verdict = "SURVIVES (|t|>=2.5)" if abs(blk["t_clust"]) >= 2.5 else "KILLED by overlap-consistent clustering"
    print("VERDICT:", verdict)


if __name__ == "__main__":
    main()
