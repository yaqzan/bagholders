"""Integrity-audit A/B verdict engine (2026-06-10).

Joins the ab_eval.py arm parquets to the rebuilt barrier_outcomes cache
(DuckDB mirror, MAIN checkout) and produces per-mechanism verdicts:

  mis_stress : fixed vs ms_off   (CALL side)
  JA4        : fixed vs ja4_off  (PUT side)
  MCD        : fixed vs mcd_off  (CALL side) + PIT-mcap cohort ladder
  wave       : wave_on vs fixed  (both sides)

Per pair: per-discrete-bucket N/WR15 on the OPTION-ALIGNED barrier (primary)
+ generic sanity, two-proportion z, W6 gradient, and the DELTA-COHORT analysis
(the signals the mechanism admits/removes and their WR vs the shared baseline).

Decision doctrine (handoff §6): bias toward RETIREMENT when evidence is
marginal; a mechanism keeps its place only when its selective effect is
clearly positive on the option barrier.

Run: python experiments/integrity_audit_2026_06/analyze_ab.py
"""
import json
import math
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import duckdb
import polars as pl

OUT_DIR = os.path.join(_ROOT, ".cache", "integrity_audit_2026_06")
# the rebuilt barrier cache lives in the MAIN checkout's .cache
DUCK = "C:/Development/Trader/.cache/barrier_outcomes.duckdb"

CALL_BUCKETS = [(95, 100, "95+"), (90, 94, "90-94"), (85, 89, "85-89"),
                (80, 84, "80-84"), (75, 79, "75-79"), (70, 74, "70-74")]
PUT_BUCKETS = [(0, 5, "<=5"), (6, 10, "6-10"), (11, 15, "11-15"),
               (16, 20, "16-20"), (21, 25, "21-25"), (26, 30, "26-30")]

REPORT = []


def say(s=""):
    print(s, flush=True)
    REPORT.append(s)


def load_arm_with_outcomes(con, arm):
    p = os.path.join(OUT_DIR, f"arm_{arm}.parquet")
    con.execute(f"CREATE OR REPLACE TEMP TABLE arm AS SELECT * FROM read_parquet('{p}')")
    df = con.execute("""
        SELECT a.symbol, a.date, a.overall,
               oc.result  AS opt_call,  gc.result  AS gen_call,
               op.result  AS opt_put,   gp.result  AS gen_put
        FROM arm a
        LEFT JOIN barrier_outcomes oc ON oc.symbol=a.symbol AND oc.date=a.date
             AND oc.side='low'  AND oc.barrier_set='30dte_opt'     AND oc.w_days=15
        LEFT JOIN barrier_outcomes gc ON gc.symbol=a.symbol AND gc.date=a.date
             AND gc.side='low'  AND gc.barrier_set='30dte_generic' AND gc.w_days=15
        LEFT JOIN barrier_outcomes op ON op.symbol=a.symbol AND op.date=a.date
             AND op.side='high' AND op.barrier_set='30dte_opt'     AND op.w_days=15
        LEFT JOIN barrier_outcomes gp ON gp.symbol=a.symbol AND gp.date=a.date
             AND gp.side='high' AND gp.barrier_set='30dte_generic' AND gp.w_days=15
        WHERE a.overall >= 70 OR a.overall <= 30
    """).pl()
    return df


def wr(df, col):
    d = df.filter(pl.col(col).is_not_null())
    n = d.height
    if n == 0:
        return 0, None
    return n, 100.0 * d.filter(pl.col(col) == 1).height / n


def two_prop_z(w1, n1, w2, n2):
    if not n1 or not n2 or w1 is None or w2 is None:
        return None
    p1, p2 = w1 / 100.0, w2 / 100.0
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    den = math.sqrt(max(1e-12, p * (1 - p) * (1 / n1 + 1 / n2)))
    return (p1 - p2) / den


def bucket_table(name_a, da, name_b, db_, side):
    buckets = CALL_BUCKETS if side == "call" else PUT_BUCKETS
    col = "opt_call" if side == "call" else "opt_put"
    gcol = "gen_call" if side == "call" else "gen_put"
    say(f"  {'bucket':8} | {name_a:>26} | {name_b:>26} | z(opt)")
    say(f"  {'':8} | {'N':>7} {'opt%':>6} {'gen%':>6} {'':>4} | {'N':>7} {'opt%':>6} {'gen%':>6} {'':>4} |")
    rows = []
    for lo, hi, lab in buckets:
        fa = da.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        fb = db_.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        na, wa = wr(fa, col)
        nb, wb = wr(fb, col)
        _, ga = wr(fa, gcol)
        _, gb = wr(fb, gcol)
        z = two_prop_z(wa, na, wb, nb)
        say(f"  {lab:8} | {na:7d} {wa if wa is not None else float('nan'):6.1f} "
            f"{ga if ga is not None else float('nan'):6.1f} {'':>4} | "
            f"{nb:7d} {wb if wb is not None else float('nan'):6.1f} "
            f"{gb if gb is not None else float('nan'):6.1f} {'':>4} | "
            f"{z if z is not None else float('nan'):+6.2f}")
        rows.append({"bucket": lab, "n_a": na, "wr_a": wa, "n_b": nb, "wr_b": wb, "z": z})
    return rows


def delta_cohort(name, d_with, d_without, side, gate):
    """Signals qualifying (gate) WITH the mechanism but not WITHOUT, and vice versa."""
    col = "opt_call" if side == "call" else "opt_put"
    if side == "call":
        qa = d_with.filter(pl.col("overall") >= gate)
        qb = d_without.filter(pl.col("overall") >= gate)
    else:
        qa = d_with.filter(pl.col("overall") <= gate)
        qb = d_without.filter(pl.col("overall") <= gate)
    ka = set(zip(qa["symbol"].to_list(), qa["date"].to_list()))
    kb = set(zip(qb["symbol"].to_list(), qb["date"].to_list()))
    admits = ka - kb          # mechanism-ON admits these
    removals = kb - ka        # mechanism-ON removes these
    shared = ka & kb

    def cohort_wr(dsrc, keys):
        if not keys:
            return 0, None
        kdf = pl.DataFrame({"symbol": [k[0] for k in keys],
                            "date": [k[1] for k in keys]})
        sub = dsrc.join(kdf, on=["symbol", "date"], how="inner")
        return wr(sub, col)

    n_ad, wr_ad = cohort_wr(d_with, admits)
    n_rm, wr_rm = cohort_wr(d_without, removals)
    n_sh, wr_sh = cohort_wr(d_with, shared)
    say(f"  [{name}] {side} gate {'>=' if side=='call' else '<='}{gate}: "
        f"shared N={n_sh} WR={wr_sh if wr_sh is not None else float('nan'):.1f} | "
        f"ON-admits N={n_ad} WR={wr_ad if wr_ad is not None else float('nan') if wr_ad is None else wr_ad:.1f} | "
        f"ON-removes N={n_rm} WR={wr_rm if wr_rm is not None else float('nan') if wr_rm is None else wr_rm:.1f}")
    z_ad = two_prop_z(wr_ad, n_ad, wr_sh, n_sh)
    z_rm = two_prop_z(wr_rm, n_rm, wr_sh, n_sh)
    say(f"           admits-vs-shared z={z_ad if z_ad is not None else float('nan'):+.2f}   "
        f"removes-vs-shared z={z_rm if z_rm is not None else float('nan'):+.2f}")
    return {"shared": (n_sh, wr_sh), "admits": (n_ad, wr_ad, z_ad),
            "removals": (n_rm, wr_rm, z_rm)}


def gradient_check(d, side):
    buckets = CALL_BUCKETS if side == "call" else PUT_BUCKETS
    col = "opt_call" if side == "call" else "opt_put"
    vals = []
    for lo, hi, lab in buckets:
        n, w = wr(d.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi)), col)
        if n >= 30:
            vals.append((lab, w))
    ok = all(vals[i][1] >= vals[i + 1][1] - 3.0 for i in range(len(vals) - 1))
    return ok, vals


def mcd_ladder(con, d_mcd_off):
    """PIT-mcap cohort ladder on the UNdampened 70-84 call cohort."""
    f = pl.read_parquet(os.path.join(OUT_DIR, "features_mcap.parquet"))
    f = f.select(["symbol", "date", "pit_mcap_b", "static_mcap_b"])
    cohort = d_mcd_off.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 84))
    j = cohort.join(f, on=["symbol", "date"], how="inner")
    j = j.filter(pl.col("pit_mcap_b").is_not_null())
    say(f"\n  MCD PIT-mcap ladder (mcd_off 70-84 cohort, N={j.height}):")
    bins = [(0, 2, "micro<2B"), (2, 10, "small2-10B"), (10, 50, "mid10-50B"),
            (50, 200, "large50-200B"), (200, 1000, "xl200B-1T"), (1000, 1e9, "mega1T+")]
    out = []
    for lo, hi, lab in bins:
        sub = j.filter((pl.col("pit_mcap_b") >= lo) & (pl.col("pit_mcap_b") < hi))
        n, w = wr(sub, "opt_call")
        say(f"    {lab:14} N={n:6d}  optWR15={w if w is not None else float('nan'):6.1f}")
        out.append({"bin": lab, "n": n, "wr": w})
    # ladder z: smallest-two vs largest-two pooled
    los = [o for o in out[:2] if o["n"] > 0]
    his = [o for o in out[3:] if o["n"] > 0]
    if los and his:
        n_lo = sum(o["n"] for o in los)
        w_lo = sum(o["wr"] * o["n"] for o in los) / n_lo
        n_hi = sum(o["n"] for o in his)
        w_hi = sum(o["wr"] * o["n"] for o in his) / n_hi
        z = two_prop_z(w_hi, n_hi, w_lo, n_lo)
        say(f"    ladder: large-pool {w_hi:.1f}% (N={n_hi}) vs small-pool {w_lo:.1f}% "
            f"(N={n_lo})  z={z:+.2f}")
        return out, z
    return out, None


def main():
    con = duckdb.connect(DUCK, read_only=True)
    arms = {}
    for a in ["legacy", "fixed", "ms_off", "ja4_off", "mcd_off", "wave_on"]:
        arms[a] = load_arm_with_outcomes(con, a)
        say(f"loaded arm {a}: {arms[a].height} signal rows")

    say("\n================ headline: fixed (v71 core) vs legacy (pre-fix) ================")
    bucket_table("fixed", arms["fixed"], "legacy", arms["legacy"], "call")
    bucket_table("fixed", arms["fixed"], "legacy", arms["legacy"], "put")

    say("\n================ MIS_STRESS (fixed=ON vs ms_off) — CALL ================")
    bucket_table("ms ON (fixed)", arms["fixed"], "ms OFF", arms["ms_off"], "call")
    dc_ms_75 = delta_cohort("mis_stress", arms["fixed"], arms["ms_off"], "call", 75)
    dc_ms_70 = delta_cohort("mis_stress", arms["fixed"], arms["ms_off"], "call", 70)

    say("\n================ JA4 (fixed=ON vs ja4_off) — PUT ================")
    bucket_table("JA4 ON (fixed)", arms["fixed"], "JA4 OFF", arms["ja4_off"], "put")
    dc_ja4_25 = delta_cohort("JA4", arms["fixed"], arms["ja4_off"], "put", 25)
    dc_ja4_30 = delta_cohort("JA4", arms["fixed"], arms["ja4_off"], "put", 30)

    say("\n================ MCD (fixed=ON-PIT vs mcd_off) — CALL ================")
    bucket_table("MCD ON-PIT", arms["fixed"], "MCD OFF", arms["mcd_off"], "call")
    dc_mcd_75 = delta_cohort("MCD", arms["fixed"], arms["mcd_off"], "call", 75)
    dc_mcd_70 = delta_cohort("MCD", arms["fixed"], arms["mcd_off"], "call", 70)
    ladder, ladder_z = mcd_ladder(con, arms["mcd_off"])

    say("\n================ WAVE (wave_on vs fixed=OFF) ================")
    bucket_table("wave ON", arms["wave_on"], "wave OFF (fixed)", arms["fixed"], "call")
    bucket_table("wave ON", arms["wave_on"], "wave OFF (fixed)", arms["fixed"], "put")
    dc_wv_c = delta_cohort("wave", arms["wave_on"], arms["fixed"], "call", 75)
    dc_wv_p = delta_cohort("wave", arms["wave_on"], arms["fixed"], "put", 25)

    say("\n================ W6 gradient (fixed arm) ================")
    okc, gc_ = gradient_check(arms["fixed"], "call")
    okp, gp_ = gradient_check(arms["fixed"], "put")
    say(f"  call gradient ok={okc}: {gc_}")
    say(f"  put  gradient ok={okp}: {gp_}")

    verdicts = {
        "dc_ms_75": dc_ms_75, "dc_ms_70": dc_ms_70,
        "dc_ja4_25": dc_ja4_25, "dc_ja4_30": dc_ja4_30,
        "dc_mcd_75": dc_mcd_75, "dc_mcd_70": dc_mcd_70,
        "mcd_ladder": ladder, "mcd_ladder_z": ladder_z,
        "dc_wave_call": dc_wv_c, "dc_wave_put": dc_wv_p,
    }
    with open(os.path.join(OUT_DIR, "verdict_data.json"), "w") as f:
        json.dump(verdicts, f, indent=2, default=str)
    with open(os.path.join(OUT_DIR, "analysis_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT))
    say("\nwrote verdict_data.json + analysis_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
