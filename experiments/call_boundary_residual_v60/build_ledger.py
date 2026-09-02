"""Phase A — Active-baseline 75-79 CALL boundary residual ledger (v60).

Pulls v60 CALL peaks in [70,89] over a date window, computes the canonical
generic barrier-touch outcome (K=2.0/M=5.0, W=15 cal days) via the exact assess
path (_forward_walk_subset -> _peak_to_result), joins weight_info features, and
writes a parquet ledger for cohort-z mining.

Stage 1 primary metric = WR15 (win=1 if swing['15d']['result']=='win', else 0).
Also keeps WR7 / WR30 for W2 multi-barrier-window consistency.

Holdout: filters date <= CALIBRATION_CUTOFF_DATE.
"""
import os, sys, json, time
from collections import namedtuple

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from database.trader_database import DB
import assess_scores as A

# --- module barrier sanity: must be generic K=2/5 calls, no option DTE ---
assert A.SWING_K_LOW == 2.0 and A.SWING_M_LOW == 5.0, (A.SWING_K_LOW, A.SWING_M_LOW)

CUTOFF = "2026-05-15"
START = os.environ.get("LEDGER_START", "2021-05-15")   # 5y default
LO, HI = 70, 89   # include neighbors for gradient/spillover checks
VERSION_ID = 60

Peak = namedtuple("Peak", ["symbol_id", "date", "overall"])

WI_FEATURES = [
    "stoch", "trend", "macd", "bb", "rsi", "ta", "td",
    "w_adj", "w_comp", "w_bias", "w_mom",
    "wadj_completed", "wadj_partial", "weekly_adj_gap", "weekly_gap_flag",
    "cont_lift", "cont_raw_lift", "mcd_dampen", "mcd_mcap_b",
    "daily_volume_authority_wave", "pre_boost", "pre_regime",
    "mis_stress", "put_regime_mult",
]


def main():
    t0 = time.time()
    sql = (
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id=%d AND overall BETWEEN %d AND %d "
        "AND date BETWEEN '%s' AND '%s' AND weight_info IS NOT NULL"
        % (VERSION_ID, LO, HI, START, CUTOFF)
    )
    cur = DB.execute_sql(sql)
    rows = cur.fetchall()
    print("pulled scores:", len(rows), "in %.1fs" % (time.time() - t0))

    peaks = []
    wi_by_key = {}
    for sym, d, ov, wi in rows:
        p = Peak(sym, d, int(ov))
        peaks.append(p)
        try:
            wi_by_key[(sym, d)] = json.loads(wi)
        except Exception:
            wi_by_key[(sym, d)] = {}

    t1 = time.time()
    results = A._forward_walk_subset(peaks)
    print("walked:", len(results), "results in %.1fs" % (time.time() - t1))

    recs = []
    for r in results:
        sym = r["symbol"]
        d = r["date"]
        sw = r.get("swing", {})

        def win(label):
            s = sw.get(label)
            if s is None or s.get("result") is None:
                return None
            return 1 if s["result"] == "win" else 0

        rec = {
            "symbol": sym,
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "overall": int(r["score"]),
            "vol_pct": float(r["vol_pct"]) if r.get("vol_pct") is not None else None,
            "win7": win("7d"), "win15": win("15d"), "win30": win("30d"),
        }
        wi = wi_by_key.get((sym, d), {})
        for f in WI_FEATURES:
            v = wi.get(f)
            if isinstance(v, bool):
                v = float(int(v))
            elif isinstance(v, (int, float)):
                v = float(v)
            else:
                v = None
            rec[f] = v
        recs.append(rec)

    outdir = ".cache/call_boundary_residual_v60"
    os.makedirs(outdir, exist_ok=True)
    tag = "10y" if START <= "2016-05-15" else "5y"
    # persist raw recs first so a schema error never forces a re-walk
    with open(os.path.join(outdir, "recs_%s.json" % tag), "w") as f:
        json.dump(recs, f)

    import polars as pl
    df = pl.DataFrame(recs, infer_schema_length=None)
    df = df.filter(pl.col("win15").is_not_null())
    out = os.path.join(outdir, "ledger_%s.parquet" % tag)
    df.write_parquet(out)

    # quick summary
    def wr(sub, col="win15"):
        s = sub.filter(pl.col(col).is_not_null())
        return (s[col].mean() * 100 if s.height else None), s.height

    summ = {"out": out, "N_total": df.height, "window": [START, CUTOFF]}
    for lo, hi in [(70, 74), (75, 79), (80, 84), (85, 89)]:
        sub = df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        w15, n = wr(sub, "win15")
        w7, _ = wr(sub, "win7")
        w30, _ = wr(sub, "win30")
        summ["tier_%d_%d" % (lo, hi)] = {
            "N": n,
            "WR7": round(w7, 2) if w7 else None,
            "WR15": round(w15, 2) if w15 else None,
            "WR30": round(w30, 2) if w30 else None,
        }
    with open(os.path.join(outdir, "ledger_%s_summary.json" % tag), "w") as f:
        json.dump(summ, f, indent=2)
    print("SUMMARY", json.dumps(summ))


if __name__ == "__main__":
    main()
