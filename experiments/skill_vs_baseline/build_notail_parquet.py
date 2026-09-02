"""Phase 3d: ablate the ENTIRE active score-stage dampener tail, exactly, from stored data.

`weight_info['pre_boost']` (scoring.py:1715) is an exact checkpoint of overall BEFORE the
post-process tail (continuation-echo + WVD + daily-volume-authority + EARN_BOOST; the rest —
MCD/ICH/SCW/sector/PCD — are inert or put-side in v73). So:
  score_real   = live v73 overall (tail ON)   -- baseline
  score_notail = pre_boost      (tail OFF)     -- ablation
Run both through the MC head-to-head harness (MOM_SCORE_COL) to see if the active tail earns
its keep on the funded Apex book (DD / collapse / compound). No re-scoring needed.
"""
import os
import sys
import json

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.models.core import AlgorithmVersion

# NOTAIL_VID_OVERRIDE (added 2026-07-29 for the N=1000 re-ablation): build against a
# NON-ACTIVE version's stored score rows (e.g. 73 for the v73-substrate re-run) without
# ever touching the live active-version pointer. Unset => active version, as before.
VID = int(os.environ.get('NOTAIL_VID_OVERRIDE') or AlgorithmVersion.get_active_scores_version().id)


def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id={vid} AND overall>=60 AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, vid=VID, timeout_ms=300_000,
    )
    syms, dts, real, notail = [], [], [], []
    miss = 0
    for sym, d, ov, wi in rows:
        pb = ov
        if wi:
            try:
                pb = int(json.loads(wi).get('pre_boost', ov))
            except Exception:
                miss += 1
        syms.append(sym)
        dts.append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        real.append(int(ov))
        notail.append(int(pb))
    if miss:
        print(f"  [warn] {miss} weight_info parse failures (fell back to overall)", flush=True)
    df = pl.DataFrame({"symbol": syms, "date": dts, "score_real": real, "score_notail": notail})
    # keep rows tradable under EITHER column
    return df.filter((pl.col("score_real") >= 70) | (pl.col("score_notail") >= 70))


def main():
    path = materialize_polars(f"notail_v{VID}", _build, force=True)
    df = pl.read_parquet(path)
    import numpy as np
    r = np.corrcoef(df["score_real"].to_numpy(), df["score_notail"].to_numpy())[0, 1]
    print(f"\nnotail parquet: {path}")
    print(f"  rows: {df.height:,}")
    print(f"  score_real  >=75: {(df['score_real']>=75).sum():,}   (tail ON = live v73)")
    print(f"  score_notail>=75: {(df['score_notail']>=75).sum():,}   (tail OFF = pre_boost)")
    print(f"  corr = {r:+.3f}")
    print(f"\nMOM_SCORE_PARQUET={path}  (cols: score_real | score_notail)")


if __name__ == "__main__":
    main()
