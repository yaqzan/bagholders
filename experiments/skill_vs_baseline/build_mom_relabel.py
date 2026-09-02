"""Build the momentum-vs-score head-to-head signal override.

One parquet, two score columns on the IDENTICAL (momentum-available) universe:
  - score_real : the real technical score (Arm A)
  - score_mom  : the SAME multiset of score values, reassigned by 12-1 momentum
                 rank (highest-momentum day gets the highest score). Identical
                 distribution -> identical selectivity & tier counts; only the
                 SELECTION (which day holds which score) differs.

The MC engine, run with this override, picks the top-momentum days in Arm B and
the top-score days in Arm A, through the exact same cascade/dead-hold/collapse.
"""
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, cache_path
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id


def _throw(msg):
    return lambda: (_ for _ in ()).throw(RuntimeError(msg))


def _build():
    sc = pl.read_parquet(materialize_polars(f"skill_v{VID}_allscores_2016", _throw("run skill.py")))
    pf = pl.read_parquet(materialize_polars("price_feats_raw_2015", _throw("run skill_returns.py"))).sort(["symbol", "date"])
    pf = pf.with_columns(
        mom=(pl.col("close").shift(21).over("symbol") / pl.col("close").shift(252).over("symbol") - 1.0)
    ).select(["symbol", "date", "mom"])
    df = (sc.select(["symbol", "date", "overall"])
          .join(pf, on=["symbol", "date"], how="inner")
          .drop_nulls(["mom"]))
    # reassign the score distribution by momentum rank
    df = df.sort("mom", descending=True)
    score_desc = df["overall"].sort(descending=True)          # the score multiset, desc
    df = df.with_columns(score_real=pl.col("overall"), score_mom=score_desc)
    return df.select(["symbol", "date", "score_real", "score_mom"])


def main():
    path = materialize_polars(f"mom_relabel_v{VID}", _build, force=True)
    df = pl.read_parquet(path)
    # sanity: same distribution, different assignment
    import numpy as np
    r = np.corrcoef(df["score_real"].to_numpy(), df["score_mom"].to_numpy())[0, 1]
    print(f"\nmom_relabel parquet: {path}")
    print(f"  rows: {df.height:,}")
    print(f"  score_real mean {df['score_real'].mean():.2f}  >=75: {(df['score_real']>=75).sum():,}")
    print(f"  score_mom  mean {df['score_mom'].mean():.2f}  >=75: {(df['score_mom']>=75).sum():,}  (must match real)")
    print(f"  corr(score_real, score_mom) = {r:+.3f}  (low => momentum picks very different days than the score)")
    print(f"\nMOM_SCORE_PARQUET={path}")


if __name__ == "__main__":
    main()
