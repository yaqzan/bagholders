"""Phase 3d-A attribution reader: apex-EV of a re-scored config vs the lean baseline.

Usage: apex_ev_of_parquet.py <config.parquet> [baseline.parquet] [label]

For the config's >=75 traded set on the 30dte_apex payoff (+0.30/-0.70/-0.40):
  - supply + mean apex-EV, by tier.
If a baseline (lean) parquet is given, decompose vs it:
  - ADDED   signals (>=75 in config, <75 in lean)  -> is this member's added supply accretive?
  - REMOVED signals (<75 in config, >=75 in lean)
  - COMMON  signals (>=75 in both)
A member EARNS its keep if its ADDED signals' EV >= the COMMON EV (accretive); it's DILUTIVE
if ADDED EV << COMMON EV. Read-only. In-sample.
"""
import os
import sys
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.barrier_cache import peaks_to_swing_results

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
PERIOD = "30d"


def ev_map_for(df):
    """Barrier-join the rows in df (symbol,date) on 30dte_apex -> {(sym,date):ev}."""
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    results, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            m[(r["symbol"], dk)] = EV[sw["result"]]
    return m


def stat(evs):
    a = np.array(evs, dtype=float)
    return (len(a), a.mean() * 100 if len(a) else float("nan"))


def main():
    cfg_path = sys.argv[1]
    base_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].endswith(".parquet") else None
    label = sys.argv[-1] if not sys.argv[-1].endswith(".parquet") else os.path.basename(cfg_path)

    cfg = pl.read_parquet(cfg_path).select(["symbol", "date", "overall"]).rename({"overall": "ov_cfg"})
    if base_path:
        base = pl.read_parquet(base_path).select(["symbol", "date", "overall"]).rename({"overall": "ov_base"})
        df = cfg.join(base, on=["symbol", "date"], how="full", coalesce=True)
    else:
        df = cfg.with_columns(ov_base=pl.lit(None))
    df = df.with_columns(
        ov_cfg=pl.col("ov_cfg").fill_null(0),
        ov_base=pl.col("ov_base").fill_null(0) if base_path else pl.lit(0),
    )
    # union of rows tradable in either
    rel = df.filter((pl.col("ov_cfg") >= 75) | (pl.col("ov_base") >= 75))
    evm = ev_map_for(rel)
    print(f"\n=== apex-EV [{label}]  (>=75 on 30dte_apex; barrier-matched {len(evm):,}/{rel.height:,}) ===")

    def evs_of(mask_df):
        out = []
        for s, d in zip(mask_df["symbol"], mask_df["date"]):
            v = evm.get((s, d))
            if v is not None:
                out.append(v)
        return out

    cfg75 = rel.filter(pl.col("ov_cfg") >= 75)
    n, e = stat(evs_of(cfg75))
    print(f"  config >=75:  N={n:,}  meanEV={e:+.2f}%")
    if base_path:
        base75 = rel.filter(pl.col("ov_base") >= 75)
        added  = rel.filter((pl.col("ov_cfg") >= 75) & (pl.col("ov_base") < 75))
        removed= rel.filter((pl.col("ov_cfg") < 75) & (pl.col("ov_base") >= 75))
        common = rel.filter((pl.col("ov_cfg") >= 75) & (pl.col("ov_base") >= 75))
        for nm, d in (("lean  >=75", base75), ("COMMON", common), ("ADDED ", added), ("REMOVED", removed)):
            n, e = stat(evs_of(d))
            print(f"  {nm}:  N={n:,}  meanEV={e:+.2f}%")
        na, ea = stat(evs_of(added)); nc, ec = stat(evs_of(common))
        if na and nc:
            verdict = "ACCRETIVE (added EV >= common)" if ea >= ec - 0.5 else "DILUTIVE (added EV < common)"
            print(f"  -> member supply: ADDED meanEV {ea:+.2f}% vs COMMON {ec:+.2f}%  => {verdict}")


if __name__ == "__main__":
    main()
