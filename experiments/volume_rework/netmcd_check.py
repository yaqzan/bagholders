"""Decisive pre-check before the flatten-mag-scaling sweep:
Is the vol_mag option-WR inversion (q1>q4) NET of MCD, or is it just MCD's job?

If the q1>q4 inversion survives in the MCD-NOT-active subset -> flatten has net-new value -> sweep.
If it vanishes there -> MCD already captures it -> flatten is a no-op -> drop, stability-only.
Read-only polars on the v70 ledger.
"""
import os, sys, math
os.environ.setdefault("PYTHONIOENCODING", "utf-8"); os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (_ROOT, os.path.join(_ROOT, "experiments", "component_reweight")):
    if p not in sys.path:
        sys.path.insert(0, p)
import polars as pl
from label import add_label

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")


def ztest(w1, n1, w2, n2):
    if not n1 or not n2:
        return None
    p1, p2 = w1 / n1, w2 / n2
    pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    return None if se == 0 else round((p1 - p2) / se, 2)


def wr(df, col):
    s = df[col].drop_nulls()
    return (int(s.sum()), s.len(), round(100 * s.mean(), 1) if s.len() else None)


def quart(conv):
    cm = conv.drop_nulls("vol_mag")
    if cm.height < 40:
        return None
    q1v, q3v = cm["vol_mag"].quantile(0.25), cm["vol_mag"].quantile(0.75)
    q1 = cm.filter(pl.col("vol_mag") <= q1v)
    q4 = cm.filter(pl.col("vol_mag") > q3v)
    w1, n1, p1 = wr(q1, "opt15"); w4, n4, p4 = wr(q4, "opt15")
    g1 = wr(q1, "gen15")[2]; g4 = wr(q4, "gen15")[2]
    return {"q1_opt": p1, "q1_N": n1, "q4_opt": p4, "q4_N": n4,
            "z_q1_minus_q4_opt": ztest(w1, n1, w4, n4),
            "q1_gen": g1, "q4_gen": g4}


def main():
    df = pl.read_parquet(LEDGER)
    df = add_label(df, 1.092, 2.548, 15, "apex15")
    df = add_label(df, 0.901, 0.772, 15, "opt15")
    df = add_label(df, 1.414, 3.536, 15, "gen15")
    d = df.filter((pl.col("overall") >= 75) & (pl.col("vol_signal") == "CONVICTION"))
    print("CONVICTION 75+ N=", d.height)

    print("\n[ALL CONVICTION 75+]            ", quart(d))
    mcd_off = d.filter(pl.col("mcd_dampen").fill_null(0) <= 0.5)
    mcd_on  = d.filter(pl.col("mcd_dampen").fill_null(0) > 0.5)
    print("[MCD-NOT-active subset (~65%)]  ", quart(mcd_off), "  N=", mcd_off.height)
    print("[MCD-active subset]             ", quart(mcd_on), "  N=", mcd_on.height)

    # also strip SCW + cont to be safe (net of ALL existing call dampeners)
    clean = d.filter((pl.col("mcd_dampen").fill_null(0) <= 0.5)
                     & (pl.col("scw_dampen").fill_null(0) <= 0.5)
                     & (pl.col("cont_lift").fill_null(0).abs() <= 0.5))
    print("[net of MCD+SCW+cont]           ", quart(clean), "  N=", clean.height)

    print("\nVERDICT: flatten has net-new value IFF the q1>q4 opt inversion (z_q1_minus_q4_opt clearly >0)")
    print("         survives in the 'net of MCD+SCW+cont' row. If it collapses there, MCD/SCW already handle it -> no-op.")


if __name__ == "__main__":
    main()
