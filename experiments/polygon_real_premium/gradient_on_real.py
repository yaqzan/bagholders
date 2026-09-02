"""Does the score gradient predict REAL option returns above the 75 gate?

Independent replication of the repo's most load-bearing negative result. The
`verify_value` verdict ("the gradient above the gate is per-trade-inert") and
today's `event_noise_decomp` re-test (inert on the clean subset too) were BOTH
measured on the MODELED apex15 barrier. Real traded contract paths are a
different instrument measuring the same quantity.

Three outcome measures, all from real prices:
  R1  real path label  (TP-first / SL-first / neither, apex15 horizon)
  R2  real_pnl_d15     (the contract's actual return at day 15)
  R3  real_max_mult    (MFE -- did higher-scored names run further?)

Date-clustered CR1 t throughout (clusters = signal date). ASCII only.
"""
import sys
import importlib.util
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

REAL = ROOT / ".cache/polygon_real_premium/real_premium_ledger.parquet"
FUNDED = ROOT / ".cache/trend_ma_lattice/ledger_v74_full.parquet"
HOLD = 15
DTE_LO, DTE_HI = 25, 38
EVMAP = {"win": 0.30, "stop": -0.70, "expire": -0.40}


def _load_cr1():
    p = ROOT / "experiments/peak_fakeout/mine.py"
    spec = importlib.util.spec_from_file_location("_pf_mine_private2", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_pf_mine_private2"] = mod
    old = sys.argv
    sys.argv = ["mine.py", "--noop"]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return mod.clustered_ols_effect


clustered_ols_effect = _load_cr1()


def cr1(d, flag, y):
    dd = d.filter(pl.col(flag).is_not_null() & pl.col(y).is_not_null())
    if dd.height < 60:
        return None
    yy = dd[y].to_numpy().astype(float)
    ff = dd[flag].to_numpy().astype(float)
    fin = np.isfinite(yy) & np.isfinite(ff)
    yy, ff = yy[fin], ff[fin]
    cl = np.array([str(x) for x in dd["signal_date"].to_list()])[fin]
    if ff.sum() < 20 or (1 - ff).sum() < 20:
        return None
    if not (0.05 <= ff.mean() <= 0.95):
        return None
    X = np.column_stack([np.ones(len(ff)), ff])
    r = clustered_ols_effect(X, yy, cl)
    t = float(r["t"])
    if not np.isfinite(t):
        raise RuntimeError("non-finite t -- refusing false NULL")
    return dict(d=float(r["beta"]), t=t, n1=int(ff.sum()), n0=int((1 - ff).sum()))


def show(tag, r, scale=1.0, unit=""):
    if r is None:
        print(f"    {tag:<30} [gated/thin]")
    else:
        print(f"    {tag:<30} delta={r['d'] * scale:+8.3f}{unit}  t={r['t']:+6.2f}  "
              f"N_hi={r['n1']:>5} N_lo={r['n0']:>5}")


def main():
    real = pl.read_parquet(REAL).filter(pl.col("status") == "kept")
    real = real.with_columns([
        pl.when(pl.col("tp30_touch_bar").is_not_null() & (pl.col("tp30_touch_bar") <= HOLD))
          .then(pl.col("tp30_touch_bar")).otherwise(None).alias("tp_b"),
        pl.when(pl.col("sl70_touch_bar").is_not_null() & (pl.col("sl70_touch_bar") <= HOLD))
          .then(pl.col("sl70_touch_bar")).otherwise(None).alias("sl_b"),
    ]).with_columns(
        pl.when(pl.col("tp_b").is_not_null() & (pl.col("sl_b").is_null()
                                                | (pl.col("tp_b") <= pl.col("sl_b"))))
          .then(pl.lit("win"))
          .when(pl.col("sl_b").is_not_null()).then(pl.lit("stop"))
          .otherwise(pl.lit("expire")).alias("real_label"))
    real = real.with_columns([
        pl.col("real_label").replace_strict(EVMAP, default=None).alias("real_ev"),
        (pl.col("real_label") == "win").cast(pl.Float64).alias("real_win"),
    ])

    fd = pl.read_parquet(FUNDED).with_columns(pl.col("date").cast(pl.Utf8).alias("_d"))
    j = fd.join(real, left_on=["symbol", "_d"], right_on=["symbol", "signal_date"], how="inner")
    j = j.with_columns(pl.col("_d").alias("signal_date"))
    prim = j.filter(pl.col("liquid_entry") & (pl.col("dte_cal") >= DTE_LO)
                    & (pl.col("dte_cal") <= DTE_HI))

    print("=" * 92)
    print("SCORE GRADIENT vs REAL OPTION RETURNS (independent replication)")
    print("=" * 92)
    print(f"  matched {j.height}   primary (liquid + DTE {DTE_LO}-{DTE_HI}) {prim.height}")
    print(f"  window {j['_d'].min()} .. {j['_d'].max()}")

    for lab, d in [("ALL MATCHED", j), ("PRIMARY", prim)]:
        print(f"\n[{lab}]  N={d.height}")
        d = d.with_columns((pl.col("overall") >= 80).cast(pl.Int8).alias("hi80"))
        print("  2-band contrast 80+ vs 75-79:")
        show("R1 real_win", cr1(d, "hi80", "real_win"), 100, "pp")
        show("R1 real_ev", cr1(d, "hi80", "real_ev"), 100, "pp")
        show("R2 real_pnl_d15", cr1(d, "hi80", "real_pnl_d15"), 100, "pp")
        show("R3 real_max_mult", cr1(d, "hi80", "real_max_mult"), 1.0, "x")
        show("modeled apex_ev (control)", cr1(d, "hi80", "apex_ev"), 100, "pp")

        print("  by score band:")
        print(f"    {'band':<10}{'N':>6}{'real_win':>10}{'real_EV':>10}"
              f"{'d15_pnl':>10}{'max_mult':>10}{'model_EV':>10}")
        for lo, hi, nm in [(75, 80, "75-79"), (80, 85, "80-84"),
                           (85, 90, "85-89"), (90, 200, "90+")]:
            s = d.filter((pl.col("overall") >= lo) & (pl.col("overall") < hi))
            if s.height < 25:
                continue
            p15 = s["real_pnl_d15"].drop_nulls()
            print(f"    {nm:<10}{s.height:>6}{s['real_win'].mean():>10.4f}"
                  f"{s['real_ev'].mean():>10.4f}"
                  f"{(float(p15.mean()) if p15.len() else float('nan')):>10.4f}"
                  f"{float(s['real_max_mult'].drop_nulls().mean()):>10.3f}"
                  f"{s['apex_ev'].mean():>10.4f}")

        sp_w = np.corrcoef(d["overall"].to_numpy().astype(float),
                           d["real_win"].to_numpy().astype(float))[0, 1]
        pn = d.filter(pl.col("real_pnl_d15").is_not_null())
        sp_p = np.corrcoef(pn["overall"].to_numpy().astype(float),
                           pn["real_pnl_d15"].to_numpy().astype(float))[0, 1]
        mm = d.filter(pl.col("real_max_mult").is_not_null())
        sp_m = np.corrcoef(mm["overall"].to_numpy().astype(float),
                           mm["real_max_mult"].to_numpy().astype(float))[0, 1]
        print(f"  corr(overall, real_win)      = {sp_w:+.4f}")
        print(f"  corr(overall, real_pnl_d15)  = {sp_p:+.4f}  (N={pn.height})")
        print(f"  corr(overall, real_max_mult) = {sp_m:+.4f}  (N={mm.height})")

    print("\n" + "-" * 92)
    print("PER-YEAR 2-band real_ev delta (primary) -- sign stability")
    print("-" * 92)
    p = prim.with_columns((pl.col("overall") >= 80).cast(pl.Int8).alias("hi80"))
    for y in sorted(set(s[:4] for s in p["_d"].to_list())):
        s = p.filter(pl.col("_d").str.starts_with(y))
        r = cr1(s, "hi80", "real_ev")
        show(y, r, 100, "pp")


if __name__ == "__main__":
    sys.exit(main())
