"""N1 dampener-ablation verdict engine (2026-06-12).

Joins ab_eval.py arm parquets to the barrier_outcomes DuckDB mirror and, per
mechanism (baseline=ON vs <mech>_off), produces:
  - per-discrete-bucket N/WR15 on the OPTION-ALIGNED barrier (primary)
    + generic sanity, two-proportion z
  - DELTA-COHORT analysis: the signals the mechanism admits/removes vs the
    shared cohort (the decisive read — far sharper than bucket-table diffs)

Doctrine (v71 campaign / handoff §6): bias toward RETIREMENT when marginal; a
dampener keeps its place only when its REMOVALS are clearly BELOW the shared
baseline on the option barrier (it must delete bad signals to earn its N cost).

Run: python experiments/dampener_ablation_v72/analyze.py
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

OUT_DIR = os.path.join(_ROOT, ".cache", "dampener_ablation_v72")
DUCK = os.path.join(_ROOT, ".cache", "barrier_outcomes.duckdb").replace("\\", "/")

CALL_BUCKETS = [(95, 100, "95+"), (90, 94, "90-94"), (85, 89, "85-89"),
                (80, 84, "80-84"), (75, 79, "75-79"), (70, 74, "70-74")]
PUT_BUCKETS = [(0, 5, "<=5"), (6, 10, "6-10"), (11, 15, "11-15"),
               (16, 20, "16-20"), (21, 25, "21-25"), (26, 30, "26-30")]

# mechanism -> (off-arm, sides to analyze)
MECHS = [
    ("WCF",  "wcf_off",  ["put"]),
    ("CWCF", "cwcf_off", ["call"]),
    ("CWWD", "cwwd_off", ["call"]),
    ("CSWC", "cswc_off", ["call"]),
    ("SCW",  "scw_off",  ["call"]),
    ("ICH",  "ich_off",  ["call", "put"]),
    ("WVD",  "wvd_off",  ["call", "put"]),
    # ship-handoff bundle-confirm pairs (2026-06-12): baseline=ON vs bundle=OFF,
    # so "ON-removes" = signals the bundle retirement RESTORES.
    ("BUNDLE_A", "bundle_a", ["call", "put"]),
    ("BUNDLE_B", "bundle_b", ["call", "put"]),
]

REPORT = []


def say(s=""):
    print(s, flush=True)
    REPORT.append(s)


def load_arm_with_outcomes(con, arm):
    p = os.path.join(OUT_DIR, f"arm_{arm}.parquet").replace("\\", "/")
    con.execute(f"CREATE OR REPLACE TEMP TABLE arm AS SELECT * FROM read_parquet('{p}')")
    return con.execute("""
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


def fmt(x, spec="6.1f"):
    return ("{:" + spec + "}").format(x) if x is not None else "  n/a "


def bucket_table(name_a, da, name_b, db_, side):
    buckets = CALL_BUCKETS if side == "call" else PUT_BUCKETS
    col = "opt_call" if side == "call" else "opt_put"
    gcol = "gen_call" if side == "call" else "gen_put"
    say(f"  {'bucket':8} | {name_a:>24} | {name_b:>24} | z(opt)")
    rows = []
    for lo, hi, lab in buckets:
        fa = da.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        fb = db_.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        na, wa = wr(fa, col)
        nb, wb = wr(fb, col)
        _, ga = wr(fa, gcol)
        _, gb = wr(fb, gcol)
        z = two_prop_z(wa, na, wb, nb)
        say(f"  {lab:8} | N={na:6d} opt={fmt(wa)} gen={fmt(ga)} | "
            f"N={nb:6d} opt={fmt(wb)} gen={fmt(gb)} | {fmt(z, '+6.2f')}")
        rows.append({"bucket": lab, "n_on": na, "wr_on": wa,
                     "n_off": nb, "wr_off": wb, "z": z})
    return rows


def delta_cohort(name, d_on, d_off, side, gate):
    col = "opt_call" if side == "call" else "opt_put"
    if side == "call":
        qa = d_on.filter(pl.col("overall") >= gate)
        qb = d_off.filter(pl.col("overall") >= gate)
    else:
        qa = d_on.filter(pl.col("overall") <= gate)
        qb = d_off.filter(pl.col("overall") <= gate)
    ka = set(zip(qa["symbol"].to_list(), qa["date"].to_list()))
    kb = set(zip(qb["symbol"].to_list(), qb["date"].to_list()))
    admits = ka - kb
    removals = kb - ka
    shared = ka & kb

    def cohort_wr(dsrc, keys):
        if not keys:
            return 0, None
        kdf = pl.DataFrame({"symbol": [k[0] for k in keys],
                            "date": [k[1] for k in keys]})
        sub = dsrc.join(kdf, on=["symbol", "date"], how="inner")
        return wr(sub, col)

    n_ad, wr_ad = cohort_wr(d_on, admits)
    n_rm, wr_rm = cohort_wr(d_off, removals)
    n_sh, wr_sh = cohort_wr(d_on, shared)
    z_ad = two_prop_z(wr_ad, n_ad, wr_sh, n_sh)
    z_rm = two_prop_z(wr_rm, n_rm, wr_sh, n_sh)
    g = (">=" if side == "call" else "<=") + str(gate)
    say(f"  [{name}] {side} {g}: shared N={n_sh} WR={fmt(wr_sh)} | "
        f"ON-admits N={n_ad} WR={fmt(wr_ad)} z={fmt(z_ad, '+5.2f')} | "
        f"ON-removes N={n_rm} WR={fmt(wr_rm)} z={fmt(z_rm, '+5.2f')}")
    return {"side": side, "gate": gate,
            "shared": (n_sh, wr_sh), "admits": (n_ad, wr_ad, z_ad),
            "removals": (n_rm, wr_rm, z_rm)}


def main():
    only = set(os.environ.get("ANALYZE_MECHS", "").split(",")) - {""}
    mechs = [m for m in MECHS if not only or m[0] in only]
    con = duckdb.connect(DUCK, read_only=True)
    arm_names = ["baseline"] + [m[1] for m in mechs]
    arms = {}
    for a in arm_names:
        arms[a] = load_arm_with_outcomes(con, a)
        say(f"loaded arm {a}: {arms[a].height} signal rows")

    verdicts = {}
    for mech, off_arm, sides in mechs:
        say(f"\n================ {mech} (baseline=ON vs {off_arm}) ================")
        for side in sides:
            bucket_table(f"{mech} ON", arms["baseline"], f"{mech} OFF",
                         arms[off_arm], side)
            gates = (75, 70) if side == "call" else (25, 30)
            for g in gates:
                verdicts[f"{mech}_{side}_{g}"] = delta_cohort(
                    mech, arms["baseline"], arms[off_arm], side, g)

    suf = os.environ.get("ANALYZE_SUFFIX", "")
    with open(os.path.join(OUT_DIR, f"verdict_data{suf}.json"), "w") as f:
        json.dump(verdicts, f, indent=2, default=str)
    with open(os.path.join(OUT_DIR, f"analysis_report{suf}.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(REPORT))
    say(f"\nwrote verdict_data{suf}.json + analysis_report{suf}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
