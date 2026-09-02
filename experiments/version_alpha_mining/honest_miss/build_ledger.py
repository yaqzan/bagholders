"""Phase A — RQC active-baseline CALL boundary ledger (v60), GENERIC + OPTION barriers.

Upgrade over the archived VCBW ledger (call_boundary_residual_v60):
  * adds the OPTION-aligned 30dte_opt barrier outcome per peak (the growth gate's
    PRIMARY metric `tp`), not just the generic K=2/M=5 WR15. VCBW optimized on
    cumulative generic WR15 — the exact lens the new growth gate replaces — and
    nulled. We optimize on option-TP (filled-book ebar) instead.
  * captures full-universe per-date v60 supply (75+ call / <=25 put counts) so a
    candidate's REAL recycle_coverage can be synthesized (no FALLBACK_COVERAGE,
    which inflated the false v63 SHIP).

Holdout-locked at CALIBRATION_CUTOFF_DATE. Point-in-time weight_info features only.
"""
import os, sys, json, time
from collections import namedtuple

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from database.trader_database import DB
import assess_scores as A
from database.barrier_cache import BARRIER_SETS

# sanity: module starts at generic calls
assert A.SWING_K_LOW == 2.0 and A.SWING_M_LOW == 5.0, (A.SWING_K_LOW, A.SWING_M_LOW)
OPT = BARRIER_SETS['30dte_opt']           # k_low=1.274, m_low=1.092 (call option barrier)

CUTOFF = "2026-05-15"
START = os.environ.get("LEDGER_START", "2021-05-15")    # 5y default
LO, HI = 70, 89
VERSION_ID = 70

Peak = namedtuple("Peak", ["symbol_id", "date", "overall"])

WI_FEATURES = [
    "stoch", "trend", "macd", "bb", "rsi", "ta", "td",
    "w_adj", "w_comp", "w_bias", "w_mom",
    "wadj_completed", "wadj_partial", "weekly_adj_gap", "weekly_gap_flag",
    "cont_lift", "cont_raw_lift", "mcd_dampen", "mcd_mcap_b",
    "ich_dampen", "scw_dampen", "scw_scalar", "scw_raw_stoch", "scw_ext_index",
    "daily_volume_authority_wave", "sector_breadth_wave",
    "pre_boost", "pre_regime", "mis_stress",
]


def _walk_win(peaks, label_set):
    """Return {(sym,date): {7:0/1/None,15:...,30:...}} win flags for the current
    module barrier set."""
    res = A._forward_walk_subset(peaks)
    out = {}
    meta = {}
    for r in res:
        sw = r.get("swing", {})
        def win(lbl):
            s = sw.get(lbl)
            if s is None or s.get("result") is None:
                return None
            return 1 if s["result"] == "win" else 0
        out[(r["symbol"], r["date"])] = {7: win("7d"), 15: win("15d"), 30: win("30d")}
        meta[(r["symbol"], r["date"])] = {
            "score": int(r["score"]),
            "vol_pct": float(r["vol_pct"]) if r.get("vol_pct") is not None else None,
        }
    return out, meta


def main():
    t0 = time.time()
    sql = (
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id=%d AND overall BETWEEN %d AND %d "
        "AND date BETWEEN '%s' AND '%s' AND weight_info IS NOT NULL"
        % (VERSION_ID, LO, HI, START, CUTOFF)
    )
    rows = DB.execute_sql(sql).fetchall()
    print("pulled scores:", len(rows), "in %.1fs" % (time.time() - t0), flush=True)

    peaks, wi_by_key = [], {}
    for sym, d, ov, wi in rows:
        peaks.append(Peak(sym, d, int(ov)))
        try:
            wi_by_key[(sym, d)] = json.loads(wi)
        except Exception:
            wi_by_key[(sym, d)] = {}

    # GENERIC barriers (default 2/5)
    t1 = time.time()
    gen, meta = _walk_win(peaks, "generic")
    print("generic walk:", len(gen), "in %.1fs" % (time.time() - t1), flush=True)

    # OPTION barriers (30dte_opt 1.274/1.092 calls)
    t2 = time.time()
    A.SWING_K_LOW, A.SWING_M_LOW = OPT['k_low'], OPT['m_low']
    A.SWING_K_HIGH, A.SWING_M_HIGH = OPT['k_high'], OPT['m_high']
    try:
        opt, _ = _walk_win(peaks, "option")
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = 2.0, 5.0
        A.SWING_K_HIGH, A.SWING_M_HIGH = 1.0, 2.0
    print("option walk:", len(opt), "in %.1fs" % (time.time() - t2), flush=True)

    recs = []
    for (sym, d), m in meta.items():
        g = gen.get((sym, d), {}); o = opt.get((sym, d), {})
        rec = {
            "symbol": sym,
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "overall": m["score"],
            "vol_pct": m["vol_pct"],
            "gen7": g.get(7), "gen15": g.get(15), "gen30": g.get(30),
            "opt7": o.get(7), "opt15": o.get(15), "opt30": o.get(30),
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

    # full-universe per-date v60 supply (for candidate recycle_coverage synthesis)
    sup_sql = (
        "SELECT date, SUM(overall>=75) AS c75, SUM(overall<=25) AS p25 FROM scores "
        "WHERE version_id=%d AND date BETWEEN '%s' AND '%s' GROUP BY date"
        % (VERSION_ID, START, CUTOFF)
    )
    supply = [
        {"date": (d.isoformat() if hasattr(d, "isoformat") else str(d)),
         "c75": int(c or 0), "p25": int(p or 0)}
        for d, c, p in DB.execute_sql(sup_sql).fetchall()
    ]

    outdir = ".cache/honest_miss"
    os.makedirs(outdir, exist_ok=True)
    tag = "10y" if START <= "2016-05-15" else "5y"
    with open(os.path.join(outdir, "recs_%s.json" % tag), "w") as f:
        json.dump(recs, f)
    with open(os.path.join(outdir, "supply_v60_%s.json" % tag), "w") as f:
        json.dump(supply, f)

    import polars as pl
    df = pl.DataFrame(recs, infer_schema_length=None).filter(pl.col("opt15").is_not_null())
    out = os.path.join(outdir, "ledger_%s.parquet" % tag)
    df.write_parquet(out)

    def wr(sub, col):
        s = sub.filter(pl.col(col).is_not_null())
        return (round(s[col].mean() * 100, 2) if s.height else None), s.height

    summ = {"out": out, "N": df.height, "window": [START, CUTOFF], "supply_days": len(supply)}
    for lo, hi in [(70, 74), (75, 79), (80, 84), (85, 89)]:
        sub = df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        g15, n = wr(sub, "gen15"); o15, _ = wr(sub, "opt15")
        summ["t%d_%d" % (lo, hi)] = {"N": n, "genWR15": g15, "optTP15": o15}
    with open(os.path.join(outdir, "ledger_%s_summary.json" % tag), "w") as f:
        json.dump(summ, f, indent=2)
    print("SUMMARY", json.dumps(summ), flush=True)


if __name__ == "__main__":
    main()
