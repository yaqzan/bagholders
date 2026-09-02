"""Is the apex15 ASSESSMENT barrier faithful to real option contracts?

The apex15 predictand declares a call a WIN when the UNDERLYING touches +1.092
sigma within 15 trading days, and maps that to a +30% option gain; a STOP at
-2.548 sigma maps to -70%. Every Stage-1 number this repo reports (WR15,
apex-EV, the 0d skill gate) rides on that mapping. It has never been checked
against a traded option price, because until today we had no real contract paths
over a meaningful window.

This compares, on MATCHED (symbol, signal_date) rows:
    modeled  : apex_win / apex_ev from the funded ledger (sigma barriers)
    real     : path-resolved first-touch on the CONTRACT's own price
               TP  = intraday HIGH >= 1.30 x entry  (limit fill -- house canon)
               SL  = daily CLOSE  <= 0.30 x entry   (EOD forced exit)

Controls applied because they change the answer:
  - liquid_entry only (a stale print is not a price)
  - dte_cal in [25,38] (premium ~ sqrt(DTE); a 21-day contract decays faster than
    the 30-day nominal the barrier assumes, which would bias the stop rate up)
  - horizon capped at 15 MARKET days to match apex15

Read-only. ASCII output only. Reports numbers; no ship recommendation.
"""
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / ".cache/polygon_real_premium/real_premium_ledger.parquet"
FUNDED = ROOT / ".cache/trend_ma_lattice/ledger_v74_full.parquet"

HOLD = 15
DTE_LO, DTE_HI = 25, 38
EV = {"win": 0.30, "stop": -0.70, "expire": -0.40}


def resolve(df):
    """Path-resolved label from the real contract path."""
    return df.with_columns([
        pl.when(pl.col("tp30_touch_bar").is_not_null() & (pl.col("tp30_touch_bar") <= HOLD))
          .then(pl.col("tp30_touch_bar")).otherwise(None).alias("tp_b"),
        pl.when(pl.col("sl70_touch_bar").is_not_null() & (pl.col("sl70_touch_bar") <= HOLD))
          .then(pl.col("sl70_touch_bar")).otherwise(None).alias("sl_b"),
    ]).with_columns(
        pl.when(pl.col("tp_b").is_not_null() & (pl.col("sl_b").is_null()
                                                | (pl.col("tp_b") <= pl.col("sl_b"))))
          .then(pl.lit("win"))
          .when(pl.col("sl_b").is_not_null())
          .then(pl.lit("stop"))
          .otherwise(pl.lit("expire")).alias("real_label")
    ).with_columns(
        pl.col("real_label").replace_strict(EV, default=None).alias("real_ev")
    )


def block(tag, d):
    n = d.height
    if n < 50:
        print(f"  {tag:<34} (N={n}, too thin)")
        return None
    mw = d["apex_win"].mean()
    mev = d["apex_ev"].mean()
    rw = (d["real_label"] == "win").mean()
    rs = (d["real_label"] == "stop").mean()
    re_ = (d["real_label"] == "expire").mean()
    rev = d["real_ev"].mean()
    agree = (d["apex_win"].cast(pl.Boolean) == (d["real_label"] == "win")).mean()
    print(f"  {tag:<34} N={n:>5}")
    print(f"    {'':6}{'win':>9}{'stop':>9}{'expire':>9}{'EV':>10}")
    print(f"    {'model':<6}{mw:>9.4f}{'  (23.2%)':>9}{'':>9}{mev:>10.4f}")
    print(f"    {'real':<6}{rw:>9.4f}{rs:>9.4f}{re_:>9.4f}{rev:>10.4f}")
    print(f"    delta win {rw - mw:+.4f}   delta EV {rev - mev:+.4f}   "
          f"label agreement {agree:.4f}")
    return dict(n=n, model_win=mw, model_ev=mev, real_win=rw, real_stop=rs,
                real_expire=re_, real_ev=rev, agree=agree)


def main():
    real = pl.read_parquet(REAL).filter(pl.col("status") == "kept")
    fd = pl.read_parquet(FUNDED)

    real = resolve(real)
    j = fd.with_columns(pl.col("date").cast(pl.Utf8).alias("_d")).join(
        real, left_on=["symbol", "_d"], right_on=["symbol", "signal_date"], how="inner")

    print("=" * 92)
    print("APEX15 BARRIER FIDELITY vs REAL TRADED CONTRACTS")
    print("=" * 92)
    print(f"  matched rows {j.height}  symbols {j['symbol'].n_unique()}  "
          f"window {j['_d'].min()} .. {j['_d'].max()}")
    print(f"  NOTE window differs from the full funded ledger (2021-01..2026-06):")
    print(f"       Polygon aggregates reach back only ~4 years, so 2021 is absent.")

    print("\n" + "-" * 92)
    print("PROGRESSIVE CONTROLS")
    print("-" * 92)
    r_all = block("all matched", j)
    r_liq = block("+ liquid_entry", j.filter(pl.col("liquid_entry")))
    tight = j.filter(pl.col("liquid_entry")
                     & (pl.col("dte_cal") >= DTE_LO) & (pl.col("dte_cal") <= DTE_HI))
    r_tight = block(f"+ DTE {DTE_LO}-{DTE_HI} (PRIMARY)", tight)

    print("\n" + "-" * 92)
    print("PER-YEAR (primary control set)")
    print("-" * 92)
    for y in sorted(set(s[:4] for s in tight["_d"].to_list())):
        block(y, tight.filter(pl.col("_d").str.starts_with(y)))

    print("\n" + "-" * 92)
    print("CONFUSION -- modeled label vs real path label (primary control set)")
    print("-" * 92)
    t = tight.with_columns(
        pl.when(pl.col("apex_win") == 1).then(pl.lit("win")).otherwise(pl.lit("not-win")).alias("m"))
    print(f"  {'model':<10}{'real=win':>10}{'real=stop':>11}{'real=expire':>13}")
    for m in ["win", "not-win"]:
        s = t.filter(pl.col("m") == m)
        if s.height == 0:
            continue
        print(f"  {m:<10}{s.filter(pl.col('real_label') == 'win').height:>10}"
              f"{s.filter(pl.col('real_label') == 'stop').height:>11}"
              f"{s.filter(pl.col('real_label') == 'expire').height:>13}")

    print("\n" + "-" * 92)
    print("SENSITIVITY -- does the conclusion depend on the SL convention?")
    print("-" * 92)
    print("  The engine has no option-price stop; it maps a -2.548 sigma UNDERLYING move")
    print("  to -70%. Re-resolving with a stricter SL (contract must close <= 0.30x)")
    print("  is the primary above. Alternative: never stop out, hold to d15.")
    hd = tight.filter(pl.col("real_pnl_d15").is_not_null())
    if hd.height > 100:
        hold_win = (hd["real_pnl_d15"] >= 0.30).mean()
        print(f"  hold-to-d15, no stop: real 'win' (d15 pnl >= +30%) = {hold_win:.4f}  "
              f"(N={hd.height}) vs modeled {hd['apex_win'].mean():.4f}")
        print(f"    median real d15 pnl overall = {float(hd['real_pnl_d15'].median()):+.4f}")
        print(f"    mean   real d15 pnl overall = {float(hd['real_pnl_d15'].mean()):+.4f}")

    print("\n" + "-" * 92)
    print("MFE -- how much is the +30% TP leaving on the table? (real contracts)")
    print("-" * 92)
    mx = tight["real_max_mult"].drop_nulls()
    if mx.len() > 100:
        for p in [0.25, 0.5, 0.75, 0.9]:
            print(f"    p{int(p * 100):<3} max multiple = {float(mx.quantile(p)):.3f}x")
        for thr in [1.3, 1.5, 2.0, 3.0]:
            print(f"    share reaching {thr:.1f}x = {float((mx >= thr).mean()):.4f}")


if __name__ == "__main__":
    sys.exit(main())
