"""
Refinement grid for the div_brd survivor (SPY-near-highs + breadth rolling over).

mine_onset.py found div_brd10 (spy_from60h > -0.025 & brd_chg10 < -5, persist 10d) is the ONLY
candidate that is low-EV, 7/7-year sign-stable, and survives the all-levers-off orthogonal
slice. But coverage is 55% of trades (a regime label) and dd_conc only 1.29. This grid
tightens the definition and tests a CONTINUOUS div-depth gradient (ethos: gradient >
threshold) to find a contractable band: target coverage ~10-30%, deeper EV gap, conc up,
sign-stability preserved.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/dd_onset_omens/mine2_refine.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.dd_onset_omens.mine_onset import load_context, load_tapes, _norm_date  # noqa: E402

OUT = ROOT / "experiments" / "dd_onset_omens"
LOWEV = 0.045


def flag_stats(T, col, label, report):
    on = T.filter(pl.col(col))
    off = T.filter(~pl.col(col))
    if on.height < 5000:
        print(f"  {label:34s} coverage too small n={on.height}")
        return
    base = T["loser"].mean()
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    loss_on = on.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 0.0
    cov = on.height / T.height
    conc = (loss_on / total_loss) / cov
    rate = on["loser"].mean()
    z = (rate - base) / math.sqrt(base * (1 - base) / on.height)
    # per-year sign stability
    deltas = {}
    n_neg = n_yr = 0
    for yr in sorted(T["yr"].unique().to_list()):
        s_on = on.filter(pl.col("yr") == yr); s_off = off.filter(pl.col("yr") == yr)
        if s_on.height < 5000 or s_off.height < 5000:
            continue
        d = s_on["option_pnl"].mean() - s_off["option_pnl"].mean()
        deltas[int(yr)] = round(d, 4); n_yr += 1
        if d < 0.005:  # negative or ~flat counts as "not harmful to contract"
            n_neg += 1
    # orthogonal slice
    O = T.filter((pl.col("vix") < 20) & (pl.col("mcc").abs() > 30) & (pl.col("breadth") >= 40)
                 & ((pl.col("trin") < 1.0) | (pl.col("trin") > 1.3)))
    o_on = O.filter(pl.col(col)); o_off = O.filter(~pl.col(col))
    o_gap = None
    if o_on.height >= 2000 and o_off.height >= 2000:
        o_gap = round(o_on["option_pnl"].mean() - o_off["option_pnl"].mean(), 4)
    row = {"label": label, "coverage": round(cov, 3), "mpnl_on": round(on["option_pnl"].mean(), 4),
           "mpnl_off": round(off["option_pnl"].mean(), 4), "rate_z": round(z, 1),
           "dd_conc": round(conc, 2), "dd_share": round(loss_on / total_loss, 3),
           "yrs_stable": f"{n_neg}/{n_yr}", "yr_deltas": deltas, "ortho_gap": o_gap}
    report.append(row)
    print(f"  {label:34s} cov={cov:5.1%} mpnl {on['option_pnl'].mean():+.4f}/{off['option_pnl'].mean():+.4f} "
          f"z={z:+6.1f} conc={conc:4.2f} ddshr={loss_on/total_loss:5.3f} stable={n_neg}/{n_yr} ortho={o_gap}")


def main():
    print("== loading ==")
    ctx = load_context()
    T = load_tapes()
    T = T.sort("entry_date").join_asof(ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    base = T["loser"].mean()
    print(f"   base rate={base:.4f}  base mpnl={T['option_pnl'].mean():+.4f}")
    report = []

    # ---- threshold grid ----
    print("\n== THRESHOLD GRID: spy_from60h > -P  &  brd_chg10 < -G  (persist D) ==")
    for P in (0.025, 0.015, 0.010):
        for G in (5.0, 8.0, 12.0):
            for D in (0, 5, 10):
                raw = ((pl.col("spy_from60h") > -P) & (pl.col("brd_chg10") < -G)).fill_null(False)
                col = f"f_{int(P*1000)}_{int(G)}_{D}"
                C = ctx.with_columns(raw.alias("_raw"))
                if D > 0:
                    C = C.with_columns(pl.col("_raw").cast(pl.Int8).rolling_max(window_size=D)
                                       .fill_null(0).cast(pl.Boolean).alias(col))
                else:
                    C = C.with_columns(pl.col("_raw").alias(col))
                TT = T.drop([c for c in (col,) if c in T.columns]) \
                      .join(C.select(pl.col("date").alias("entry_date"), col), on="entry_date", how="left") \
                      .with_columns(pl.col(col).fill_null(False))
                flag_stats(TT, col, f"P{P} G{G} D{D}", report)

    # ---- continuous depth quintiles (the gradient form) ----
    print("\n== CONTINUOUS div_depth = clip(-brd_chg10-2,0,20) * clip(1 + spy_from60h/0.03, 0, 1) ==")
    C = ctx.with_columns(
        (pl.max_horizontal(-pl.col("brd_chg10") - 2.0, pl.lit(0.0)).clip(0, 20)
         * (1.0 + pl.col("spy_from60h") / 0.03).clip(0.0, 1.0)).alias("div_depth"))
    TT = T.join(C.select(pl.col("date").alias("entry_date"), "div_depth"), on="entry_date", how="left")
    TT = TT.with_columns(pl.col("div_depth").fill_null(0.0))
    qs = [0.0, 0.5, 2.0, 5.0, 9.0, 99.0]
    print(f"  {'depth band':18s} {'n':>9s} {'rate':>6s} {'mpnl':>8s} {'conc':>6s}")
    total_loss = TT.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    band_rows = []
    for i in range(len(qs) - 1):
        lo, hi = qs[i], qs[i + 1]
        sub = TT.filter((pl.col("div_depth") >= lo) & (pl.col("div_depth") < hi))
        if sub.height < 2000:
            continue
        loss = sub.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 0.0
        conc = (loss / total_loss) / (sub.height / TT.height)
        band_rows.append({"band": f"[{lo},{hi})", "n": sub.height, "rate": round(sub["loser"].mean(), 4),
                          "mpnl": round(sub["option_pnl"].mean(), 4), "conc": round(conc, 2)})
        print(f"  [{lo:4.1f},{hi:4.1f})        {sub.height:>9d} {sub['loser'].mean():>6.3f} "
              f"{sub['option_pnl'].mean():>+8.4f} {conc:>6.2f}")
    # per-year monotonicity of the depth-EV relationship (top band vs zero band)
    print("\n  per-year: depth>=5 vs depth<0.5  mpnl delta")
    yr_deltas = {}
    for yr in sorted(TT["yr"].unique().to_list()):
        s = TT.filter(pl.col("yr") == yr)
        hi_b = s.filter(pl.col("div_depth") >= 5.0); lo_b = s.filter(pl.col("div_depth") < 0.5)
        if hi_b.height < 3000 or lo_b.height < 3000:
            print(f"    {yr}: n_hi={hi_b.height} (skip)"); continue
        d = hi_b["option_pnl"].mean() - lo_b["option_pnl"].mean()
        yr_deltas[int(yr)] = round(d, 4)
        print(f"    {yr}: n_hi={hi_b.height:6d}  delta={d:+.4f}")
    # ortho slice for top band
    O = TT.filter((pl.col("vix") < 20) & (pl.col("mcc").abs() > 30) & (pl.col("breadth") >= 40)
                  & ((pl.col("trin") < 1.0) | (pl.col("trin") > 1.3)))
    o_hi = O.filter(pl.col("div_depth") >= 5.0); o_lo = O.filter(pl.col("div_depth") < 0.5)
    if o_hi.height >= 1000 and o_lo.height >= 1000:
        print(f"  ortho slice: depth>=5 mpnl={o_hi['option_pnl'].mean():+.4f} (n={o_hi.height}) "
              f"vs depth<0.5 {o_lo['option_pnl'].mean():+.4f} (n={o_lo.height})")

    # context-day coverage of depth bands (how often does the lever act?)
    days = C.select("date", "div_depth").filter(pl.col("div_depth").is_not_null())
    n_days = days.height
    for thr in (2.0, 5.0, 9.0):
        nd = days.filter(pl.col("div_depth") >= thr).height
        print(f"  context days with div_depth>={thr}: {nd}/{n_days} ({100*nd/n_days:.1f}%)")

    (OUT / "mine2_report.json").write_text(json.dumps(
        {"grid": report, "depth_bands": band_rows, "depth_yr_deltas": yr_deltas}, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine2_report.json'} ==")


if __name__ == "__main__":
    main()
