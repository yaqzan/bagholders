"""CHEAP DECISIVE CUT for the retracement-entry hypothesis (NET/RKLB/IREN).
Premise to test BEFORE any MC: do EXTENDED / LATE-in-run call winners win via
DIP-then-RESWEEP (so a delayed entry would catch a better price), or do they just
RUN (so a 'wait for the pullback' rule would MISS the best trades)?

Run-position feature: cluster consecutive 75+ call-days per symbol (gap>7cal = new
cluster, historic_peaks rule); ordinal within cluster (1=base of run, >=3=late/extended).
Path-shape from the barrier-agnostic forward path (t_up/t_dn at fine sigma grids):
  dips_first  = adverse 0.5s reached BEFORE favorable 0.5s
  dip_b4_tp   = (winner) adverse 0.5s reached BEFORE the opt15 TP touch
  miss_by_dly = (winner) never retraced 0.5s within 5 trading-equiv days -> a 'wait
                for >=0.5s pullback in 5d' rule would NOT enter -> MISSED winner (the cost)
All holdout-locked (ledger <=2026-05-15). opt15 barrier = 0.901/0.772 @15d.
"""
import os, sys
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa

LED = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
PX = os.path.join(_ROOT, ".cache", "divergence_dampener", "px_panel.parquet")

iTP = L._idx(L.UP_GRID, 0.901)     # opt15 TP
iSL = L._idx(L.DN_GRID, 0.772)     # opt15 SL
iU05 = L._idx(L.UP_GRID, 0.50)
iD05 = L._idx(L.DN_GRID, 0.50)
iD03 = L._idx(L.DN_GRID, 0.30)


def main():
    df = pl.read_parquet(LED).filter(pl.col("overall") >= 75)
    assert df["date"].max() <= "2026-05-15", "HOLDOUT LEAK"
    px = pl.read_parquet(PX)
    df = df.join(px, on=["symbol", "date"], how="left")
    sig = pl.col("vol_pct") / 100.0
    df = df.with_columns([(pl.col("ret5") / (sig * 5 ** .5)).alias("v5"),
                          (pl.col("ret10") / (sig * 10 ** .5)).alias("v10")])

    # ---- consecutive-call clustering (gap>7 cal days = new run) + ordinal ----
    df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").alias("d"))
    df = df.sort(["symbol", "d"])
    gap = (pl.col("d") - pl.col("d").shift(1).over("symbol")).dt.total_days()
    df = df.with_columns(((gap.is_null()) | (gap > 7)).cast(pl.Int32).alias("newrun"))
    df = df.with_columns(pl.col("newrun").cum_sum().over("symbol").alias("cid"))
    df = df.with_columns([
        (pl.col("d").cum_count().over(["symbol", "cid"])).alias("ord"),     # 1=base,2,3,...
        (pl.col("d").count().over(["symbol", "cid"])).alias("csize"),
    ])

    # ---- path-shape + win exprs ----
    tTP = pl.col("t_up").list.get(iTP); tSL = pl.col("t_dn").list.get(iSL)
    fc = pl.col("fwd_caldays")
    win = pl.when((tTP <= 15) & (tTP < tSL)).then(1).when((tSL <= 15) & (tSL <= tTP)).then(0).when(fc >= 15).then(0).otherwise(None)
    df = df.with_columns([
        win.cast(pl.Int8).alias("opt15"),
        L.win_expr(1.092, 2.548, 15).alias("apex15"),
        (pl.col("t_dn").list.get(iD05) < pl.col("t_up").list.get(iU05)).cast(pl.Int8).alias("dips_first"),
        (pl.col("t_dn").list.get(iD05) < tTP).cast(pl.Int8).alias("dip_b4_tp"),
        (pl.col("t_dn").list.get(iD05) <= 5).cast(pl.Int8).alias("retr05_5d"),
        (pl.col("t_dn").list.get(iD03) <= 5).cast(pl.Int8).alias("retr03_5d"),
        pl.col("mae15").alias("mae"),
    ])

    # ---- buckets ----
    df = df.with_columns([
        pl.when(pl.col("ord") == 1).then(pl.lit("1_base"))
          .when(pl.col("ord") == 2).then(pl.lit("2_mid"))
          .otherwise(pl.lit("3_late")).alias("pos"),
        (pl.col("v5") >= 1.0).alias("ext"),
    ])

    def report(name, frame):
        n = frame.height
        if n < 20:
            print("  %-26s N=%-5d (small)" % (name, n)); return
        opt = frame["opt15"].drop_nulls(); apex = frame["apex15"].drop_nulls()
        w = frame.filter(pl.col("opt15") == 1)
        nb = w.height
        dipw = w["dip_b4_tp"].mean() if nb else 0          # winners that dipped 0.5s before TP
        miss = (w["retr05_5d"] == 0).mean() if nb else 0   # winners that never dipped 0.5s in 5d (delayed rule MISSES)
        maew = w["mae"].mean() if nb else 0
        print("  %-26s N=%-5d opt15=%4.1f%% apex=%4.1f%% | dips_first=%4.1f%% retr05<=5d=%4.1f%% || WINNERS: dip_b4_TP=%4.1f%% MISSdelay=%4.1f%% meanMAE=%.2fs" % (
            name, n, opt.mean()*100, apex.mean()*100, frame["dips_first"].mean()*100, frame["retr05_5d"].mean()*100,
            dipw*100, miss*100, maew))

    print("=== ALL 75+ (baseline) ===")
    report("ALL 75+", df)
    print("\n=== by run-position (ordinal in consecutive-call cluster) ===")
    for p in ["1_base", "2_mid", "3_late"]:
        report("pos=%s" % p, df.filter(pl.col("pos") == p))
    print("\n=== by extension (v5 run-up sigma) ===")
    report("NOT ext (v5<1)", df.filter(~pl.col("ext")))
    report("EXT (v5>=1)", df.filter(pl.col("ext")))
    print("\n=== the user's exact bucket: LATE & EXTENDED vs BASE ===")
    report("BASE (ord1)", df.filter(pl.col("ord") == 1))
    report("LATE>=3 & EXT(v5>=1)", df.filter((pl.col("ord") >= 3) & pl.col("ext")))
    report("LATE>=3 & EXT(v5>=1.5)", df.filter((pl.col("ord") >= 3) & (pl.col("v5") >= 1.5)))
    report("MID/LATE>=2 & EXT", df.filter((pl.col("ord") >= 2) & pl.col("ext")))

    # cluster-size sanity: how common are multi-day runs at all?
    cs = df.group_by("csize").agg(pl.len().alias("n")).sort("csize")
    tot = df.height
    print("\n=== cluster-size distribution (signals in runs of length L) ===")
    run = 0
    for r in cs.to_dicts():
        if r["csize"] <= 8:
            print("  csize=%d: %d signals (%.1f%%)" % (r["csize"], r["n"], 100*r["n"]/tot))
    multi = df.filter(pl.col("csize") >= 3).height
    print("  >=3-long runs: %.1f%% of signals; ord>=3 (late): %.1f%%" % (100*multi/tot, 100*df.filter(pl.col("ord")>=3).height/tot))

    print("\nDECISIVE READ: if LATE&EXT winners have LOW dip_b4_TP and HIGH MISSdelay (winners just RUN, no")
    print("pullback within 5d), a 'wait for the retracement' rule misses the best trades -> premise FALSE.")
    print("If dip_b4_TP and meanMAE are MUCH higher for LATE&EXT than BASE -> premise holds -> escalate.")


if __name__ == "__main__":
    main()
