"""Volume-rework characterization — read-only polars on the holdout-locked v70 ledger.

Produces the empirical volume-edge surface the workflow agents reason over (so they
don't have to orchestrate tools). NO MySQL / NO ScoreSimulator / NO recalc.

Outputs: experiments/volume_rework/characterization.json + printed summary.
"""
import os, sys, json, math
os.environ.setdefault("PYTHONIOENCODING", "utf-8"); os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (_ROOT, os.path.join(_ROOT, "experiments", "component_reweight")):
    if p not in sys.path:
        sys.path.insert(0, p)
import polars as pl
from label import add_label
from experiments._holdout import assert_no_holdout_leak

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
BARRIERS = [("apex15", 1.092, 2.548, 15), ("opt15", 0.901, 0.772, 15), ("gen15", 1.414, 3.536, 15)]


def ztest(w1, n1, w2, n2):
    if not n1 or not n2:
        return None
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    return None if se == 0 else round((p1 - p2) / se, 2)  # +z => group1 BETTER


def wr(df, col):
    s = df[col].drop_nulls()
    return (int(s.sum()), s.len(), round(100 * s.mean(), 1) if s.len() else None)


def main():
    df = pl.read_parquet(LEDGER)
    assert_no_holdout_leak(df, context="vol_rework_char")
    for name, tp, sl, w in BARRIERS:
        df = add_label(df, tp, sl, w, name)
    df = df.with_columns(pl.col("date").str.slice(0, 4).cast(pl.Int32).alias("yr"))
    out = {"window": [df["date"].min(), df["date"].max()], "N_total": df.height}

    d = df.filter(pl.col("overall") >= 75)
    out["call75_N"] = d.height

    # --- A) vol_signal x tier (opt15/gen15) ---
    tiers = [("75-79", 75, 79), ("80-84", 80, 84), ("85+", 85, 100)]
    sigs = ["CONVICTION", "THIN_AIR", "REJECTION", "ABSORPTION", "CLIMAX", "NEUTRAL"]
    A = {}
    for tname, lo, hi in tiers:
        td = d.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        base_opt = wr(td, "opt15"); base_gen = wr(td, "gen15")
        A[tname] = {"baseline": {"opt15": base_opt[2], "gen15": base_gen[2], "N": td.height}, "by_signal": {}}
        for sg in sigs:
            sd = td.filter(pl.col("vol_signal") == sg)
            if sd.height < 30:
                continue
            wo, no, po = wr(sd, "opt15"); wg, ng, pg = wr(sd, "gen15")
            A[tname]["by_signal"][sg] = {
                "N": sd.height, "opt15": po, "gen15": pg,
                "z_opt_vs_tierbase": ztest(wo, no, base_opt[0], base_opt[1]),
                "z_gen_vs_tierbase": ztest(wg, ng, base_gen[0], base_gen[1]),
            }
    out["A_signal_x_tier"] = A

    # --- B) CONVICTION 75+ split by c_stoch (overbought) ---
    conv = d.filter(pl.col("vol_signal") == "CONVICTION")
    B = {}
    bins = [("stoch<=10", 0, 10), ("11-20", 11, 20), ("21-35", 21, 35), ("stoch>35", 36, 100)]
    for bname, lo, hi in bins:
        sd = conv.filter((pl.col("c_stoch") >= lo) & (pl.col("c_stoch") <= hi))
        wo, no, po = wr(sd, "opt15"); wg, ng, pg = wr(sd, "gen15")
        B[bname] = {"N": sd.height, "opt15": po, "gen15": pg}
    # z: overbought (<=15) vs healthy (>35)
    ob = conv.filter(pl.col("c_stoch") <= 15); he = conv.filter(pl.col("c_stoch") > 35)
    for bar in ("opt15", "gen15", "apex15"):
        wo, no, _ = wr(ob, bar); wh, nh, _ = wr(he, bar)
        B[f"z_ob_vs_healthy_{bar}"] = ztest(wo, no, wh, nh)  # +z => OB better (we expect NEG)
    out["B_conviction_stoch"] = B

    # --- C) CONVICTION 75+ split by vol_mag quartile (does magnitude carry edge?) ---
    C = {}
    cm = conv.drop_nulls("vol_mag")
    if cm.height:
        qs = cm["vol_mag"].quantile(0.25), cm["vol_mag"].quantile(0.5), cm["vol_mag"].quantile(0.75)
        labels = [("q1", -1e9, qs[0]), ("q2", qs[0], qs[1]), ("q3", qs[1], qs[2]), ("q4", qs[2], 1e9)]
        for qn, lo, hi in labels:
            sd = cm.filter((pl.col("vol_mag") > lo) & (pl.col("vol_mag") <= hi))
            wo, no, po = wr(sd, "opt15")
            C[qn] = {"N": sd.height, "vol_mag_range": [round(lo, 3) if lo > -1e8 else None, round(hi, 3) if hi < 1e8 else None], "opt15": po}
    out["C_conviction_mag_quartile"] = C

    # --- D) CONVICTION vs NEUTRAL at same tier (does amplifying correlate with WR?) ---
    D = {}
    for tname, lo, hi in tiers:
        td = d.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        cc = td.filter(pl.col("vol_signal") == "CONVICTION"); nn = td.filter(pl.col("vol_signal") == "NEUTRAL")
        wc, nc, pc = wr(cc, "opt15"); wnu, nnu, pnu = wr(nn, "opt15")
        D[tname] = {"CONVICTION_opt15": pc, "CONVICTION_N": nc, "NEUTRAL_opt15": pnu, "NEUTRAL_N": nnu,
                    "z_conv_vs_neutral_opt": ztest(wc, nc, wnu, nnu)}
    out["D_conviction_vs_neutral"] = D

    # --- E) overlap: are volume-edge cohorts already moved by existing dampeners? ---
    E = {}
    for sg in ("CONVICTION", "THIN_AIR"):
        sd = d.filter(pl.col("vol_signal") == sg)
        n = max(1, sd.height)
        E[sg] = {
            "N": sd.height,
            "frac_scw_active": round(float((sd["scw_dampen"].fill_null(0) > 0.5).sum()) / n, 3),
            "frac_mcd_active": round(float((sd["mcd_dampen"].fill_null(0) > 0.5).sum()) / n, 3),
            "frac_cont_active": round(float((sd["cont_lift"].fill_null(0).abs() > 0.5).sum()) / n, 3),
            "frac_cwwd_active": round(float((sd["cwwd_dampen"].fill_null(0) > 0.5).sum()) / n, 3),
        }
    out["E_existing_mechanism_overlap"] = E

    with open(os.path.join(_HERE, "characterization.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
