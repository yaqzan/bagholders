"""Phase 3d-A: attribute continuation-echo EXACTLY from production data.

cont is applied first in the tail (scoring.py:1335, to overall==pre_boost), so the stored
weight_info['cont_lift'] is cont's lift ON pre_boost. Thus lean+cont = round(pre_boost+cont_lift),
exactly — no re-score (cont is a no-op in ScoreSimulator, which lacks the barrier-win input).

Reports: cont-ADDED signals (pre_boost<75 & lean+cont>=75) meanEV vs COMMON (pre_boost>=75) on
the 30dte_apex payoff. Accretive vs dilutive. Read-only, full universe, exact.
"""
import os
import sys
import json
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}


def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, weight_info FROM scores "
        "WHERE version_id={vid} AND overall>=60 AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, vid=VID, timeout_ms=300_000,
    )
    syms, dts, pb, cl = [], [], [], []
    for sym, d, wi in rows:
        if not wi:
            continue
        try:
            w = json.loads(wi)
        except Exception:
            continue
        p = w.get("pre_boost")
        if p is None:
            continue
        syms.append(sym); dts.append(d.isoformat() if hasattr(d, "isoformat") else str(d))
        pb.append(int(p)); cl.append(float(w.get("cont_lift", 0.0) or 0.0))
    df = pl.DataFrame({"symbol": syms, "date": dts, "pre_boost": pb, "cont_lift": cl})
    return df.with_columns(score_cont=pl.min_horizontal(pl.lit(100), (pl.col("pre_boost") + pl.col("cont_lift")).round()).cast(pl.Int64))


def main():
    df = pl.read_parquet(materialize_polars(f"cont_recon_v{VID}", _build, force=True))
    nz = (df["cont_lift"] != 0.0).sum()
    print(f"\ncont reconstruction: {df.height:,} rows; cont_lift!=0 on {nz:,}")
    added = df.filter((pl.col("pre_boost") < 75) & (pl.col("score_cont") >= 75))
    common = df.filter(pl.col("pre_boost") >= 75)
    print(f"  cont ADDED >=75: {added.height:,}   COMMON (pre_boost>=75): {common.height:,}")

    Peak = namedtuple("Peak", "symbol_id date overall")
    rel = pl.concat([added.select(["symbol", "date"]), common.select(["symbol", "date"])]).unique()
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(rel["symbol"], rel["date"])]
    res, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    evm = {}
    for r in res:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EV:
            evm[(r["symbol"], dk)] = EV[sw["result"]]

    def ev_of(d):
        v = [evm[(s, dt)] for s, dt in zip(d["symbol"], d["date"]) if (s, dt) in evm]
        return (len(v), np.mean(v) * 100 if v else float("nan"))

    na, ea = ev_of(added); nc, ec = ev_of(common)
    print(f"  cont ADDED:  N={na:,}  meanEV={ea:+.2f}%")
    print(f"  COMMON    :  N={nc:,}  meanEV={ec:+.2f}%")
    if na and nc:
        print(f"  -> continuation-echo added supply: {'ACCRETIVE' if ea>=ec-0.5 else 'DILUTIVE'} "
              f"(ADDED {ea:+.2f}% vs COMMON {ec:+.2f}%)")


if __name__ == "__main__":
    main()
