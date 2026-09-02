"""
VIX-MOMENTUM (weekly MACD / velocity / acceleration) residual-DD mining on the
v70 Apex MC trade-tape (calls-only).  /research run 2026-06-09.

User hypothesis: VIX is under-explored as a *dynamic* (velocity / acceleration /
weekly-MACD-crossover) signal, not just a static level.  "VIX spikes and all my
calls collapse."

Prior art this must clear (G22 / G23, known-issues):
  * RXDD ships a VIX-*LEVEL* band (20-28 slow-bleed) CALL-alloc dampener (2026-06-04).
  * Daily VIX-*velocity* (vv5) was already tested and is DEAD: anti-aligned (rising
    VIX = high-EV bounce), the low-EV (falling) side is NOT dd-concentrated.
  * The regime/breadth/volume DD-sizing well is documented "rigorously dry" after
    RXDD+SVR+MWDD+TVDD+F3F.

What is genuinely UNTESTED (the user's exact framings):
  * VIX *WEEKLY* MACD histogram + crossover (a smoothed 12/26-week momentum, very
    different shape from the raw 5d velocity vv5 that G23 ruled out).
  * VIX *ACCELERATION* (2nd derivative) — could LEAD level, catching a spike from
    a calm base BEFORE VIX crosses RXDD's 20 floor.
The decisive question: does any VIX-momentum cohort show LOW-EV *and* high dd_conc
*and* survive the orthogonal slice where RXDD does NOT fire (vix<20), robustly
across windows (not crash-only)?  This tape already has RXDD+SVR+MWDD live, so any
residual VIX signal is orthogonal-to-RXDD-level by construction.

Pure offline (parquet + light DB read). No MC, no queue.
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/vix_weekly_v70/mine.py
    DD_FILTER=0.13 PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/vix_weekly_v70/mine.py
"""
import os, sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "vix_weekly_v70"
OUT.mkdir(parents=True, exist_ok=True)

MIN_N = 200
DD_FILTER = float(os.environ.get("DD_FILTER", "0"))   # 0.13 -> DD-active subset (where levers fire)
LOWEV = 0.045   # mean option pnl below this == "low-EV" (RXDD bleed band +0.027, MWDD flat +0.046)


def _norm_date(df, col):
    if col not in df.columns:
        return df
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def load_tapes():
    files = sorted(TAPE_DIR.glob("tape_*.parquet"))
    if not files:
        print(f"!! no tape_*.parquet in {TAPE_DIR}");  sys.exit(2)
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        try:
            df = pl.read_parquet(f).with_columns(pl.lit(label).alias("window"))
        except Exception as e:
            print(f"   skip {f.name}: {e}");  continue
        frames.append(df)
        print(f"   loaded {f.name:28s} rows={df.height}")
    T = pl.concat(frames, how="diagonal_relaxed")
    T = _norm_date(T, "entry_date")
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    T = T.with_columns(
        (pl.col("premium_cost").cast(pl.Float64) * pl.col("option_pnl").cast(pl.Float64)).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
    )
    return T


# ---------------------------------------------------------------------------
# VIX momentum feature engineering
# ---------------------------------------------------------------------------
def load_vix_features() -> pl.DataFrame:
    """Daily VIX series -> velocity / acceleration / daily-MACD / WEEKLY-MACD /
    weekly-RSI, forward-filled, ready to asof-join by entry_date."""
    from database.models.core import MarketRegime
    rows = [(r.date, float(r.vix_close)) for r in
            MarketRegime.select(MarketRegime.date, MarketRegime.vix_close)
            .where(MarketRegime.vix_close.is_null(False)).order_by(MarketRegime.date)]
    if not rows:
        print("!! no VIX rows in MarketRegime");  sys.exit(2)
    v = pl.DataFrame({"date": [r[0] for r in rows], "vix": [r[1] for r in rows]})
    v = _norm_date(v, "date").unique(subset=["date"]).sort("date")
    print(f"   VIX daily rows={v.height}  ({v['date'].min()} .. {v['date'].max()})")

    def ema(col, span):
        return pl.col(col).ewm_mean(span=span, adjust=False)

    # --- daily velocity / acceleration ---
    v = v.with_columns([
        (pl.col("vix") / pl.col("vix").shift(5) - 1.0).alias("vv5"),
        (pl.col("vix") / pl.col("vix").shift(10) - 1.0).alias("vv10"),
        (pl.col("vix") / pl.col("vix").shift(20) - 1.0).alias("vv20"),
    ])
    v = v.with_columns([
        (pl.col("vv5") - pl.col("vv5").shift(5)).alias("vacc"),       # accel = d(vv5)/5d (2nd deriv)
        (pl.col("vix") / ema("vix", 20) - 1.0).alias("vix_vs_ema20"),  # above/below 20d trend
    ])
    # --- daily MACD on VIX (smoothed momentum proxy, finer than weekly) ---
    v = v.with_columns((ema("vix", 12) - ema("vix", 26)).alias("dmacd"))
    v = v.with_columns((pl.col("dmacd") - pl.col("dmacd").ewm_mean(span=9, adjust=False)).alias("dmacd_hist"))

    # --- DAILY-EQUIVALENT WEEKLY MACD (the ENGINE-FAITHFUL ship feature):
    #     weekly 12/26/9 EMAs ~= daily 60/130/45 trading-day EMAs on the daily VIX.
    #     Inherently point-in-time (recursive EMA uses only past) -> no week-boundary
    #     look-ahead, trivial one-pass compute in-engine over the vix_map. `de_hist`
    #     is the MACD histogram; `de_sl` its 5d slope (the 'rising' component). ---
    v = v.with_columns((ema("vix", 60) - ema("vix", 130)).alias("de_macd"))
    v = v.with_columns((pl.col("de_macd") - pl.col("de_macd").ewm_mean(span=45, adjust=False)).alias("de_hist"))
    v = v.with_columns((pl.col("de_hist") - pl.col("de_hist").shift(5)).alias("de_sl"))

    # --- WEEKLY MACD (the user's explicit ask): resample to ISO-week last close ---
    vw = (v.with_columns(pl.col("date").dt.truncate("1w").alias("wk"))
            .group_by("wk").agg(pl.col("vix").last().alias("wvix")).sort("wk"))
    vw = vw.with_columns((pl.col("wvix").ewm_mean(span=12, adjust=False)
                          - pl.col("wvix").ewm_mean(span=26, adjust=False)).alias("wmacd"))
    vw = vw.with_columns((pl.col("wmacd") - pl.col("wmacd").ewm_mean(span=9, adjust=False)).alias("whist"))
    vw = vw.with_columns([
        pl.col("whist").shift(1).alias("whist_prev"),
        # weekly RSI(14) on weekly VIX
        (pl.col("wvix") - pl.col("wvix").shift(1)).alias("wdelta"),
    ])
    vw = vw.with_columns([
        pl.when(pl.col("wdelta") > 0).then(pl.col("wdelta")).otherwise(0.0).alias("wup"),
        pl.when(pl.col("wdelta") < 0).then(-pl.col("wdelta")).otherwise(0.0).alias("wdn"),
    ])
    vw = vw.with_columns([
        pl.col("wup").ewm_mean(span=14, adjust=False).alias("wrs_up"),
        pl.col("wdn").ewm_mean(span=14, adjust=False).alias("wrs_dn"),
    ])
    vw = vw.with_columns(
        (100.0 - 100.0 / (1.0 + pl.col("wrs_up") / (pl.col("wrs_dn") + 1e-9))).alias("wrsi"))
    # weekly MACD cycle quadrant (the crossover signal the user noticed)
    vw = vw.with_columns(
        pl.when((pl.col("whist") >= 0) & (pl.col("whist") >= pl.col("whist_prev"))).then(pl.lit("pos_rising"))
          .when((pl.col("whist") >= 0) & (pl.col("whist") < pl.col("whist_prev"))).then(pl.lit("pos_falling"))
          .when((pl.col("whist") < 0) & (pl.col("whist") >= pl.col("whist_prev"))).then(pl.lit("neg_rising"))
          .otherwise(pl.lit("neg_falling")).alias("wmacd_quad"))
    # LAST-COMPLETED-WEEK (point-in-time, NO look-ahead): shift the weekly series by
    # one week so a daily date in week W carries week (W-1)'s completed whist. This is
    # the literal "weekly MACD crossover you see on the chart" with no current-week peek.
    vw = vw.sort("wk").with_columns([
        pl.col("whist").shift(1).alias("whist_pit"),
        pl.col("whist").shift(2).alias("whist_pit2"),
    ])
    vw = vw.with_columns(
        pl.when((pl.col("whist_pit") >= 0) & (pl.col("whist_pit") >= pl.col("whist_pit2"))).then(pl.lit("pos_rising"))
          .when((pl.col("whist_pit") >= 0) & (pl.col("whist_pit") < pl.col("whist_pit2"))).then(pl.lit("pos_falling"))
          .when((pl.col("whist_pit") < 0) & (pl.col("whist_pit") >= pl.col("whist_pit2"))).then(pl.lit("neg_rising"))
          .otherwise(pl.lit("neg_falling")).alias("wquad_pit"))
    # map weekly features back onto every daily date (current week's state)
    v = v.with_columns(pl.col("date").dt.truncate("1w").alias("wk")).join(
        vw.select(["wk", "wmacd", "whist", "wrsi", "wmacd_quad", "whist_pit", "wquad_pit"]),
        on="wk", how="left")
    return v.drop("wk")


def attach(T: pl.DataFrame, V: pl.DataFrame) -> pl.DataFrame:
    V2 = V.rename({"date": "entry_date"}).sort("entry_date")
    return T.sort("entry_date").join_asof(V2, on="entry_date", strategy="backward")


def add_bins(T: pl.DataFrame) -> pl.DataFrame:
    return T.with_columns([
        # entry portfolio DD state
        pl.when(pl.col("entry_dd") < 0.10).then(pl.lit("dd_00_10"))
          .when(pl.col("entry_dd") < 0.20).then(pl.lit("dd_10_20"))
          .when(pl.col("entry_dd") < 0.35).then(pl.lit("dd_20_35"))
          .when(pl.col("entry_dd") < 0.55).then(pl.lit("dd_35_55"))
          .otherwise(pl.lit("dd_55p")).alias("b_dd"),
        # VIX level (RXDD axis — reference; should be ~captured/flat-conc now)
        pl.when(pl.col("vix") >= 28).then(pl.lit("vix_28p"))
          .when(pl.col("vix") >= 20).then(pl.lit("vix_20_28"))
          .when(pl.col("vix") >= 15).then(pl.lit("vix_15_20"))
          .otherwise(pl.lit("vix_u15")).alias("b_vix"),
        # 5d velocity (G23 ruled out — confirm)
        pl.when(pl.col("vv5") < -0.10).then(pl.lit("vv5_dn_strong"))
          .when(pl.col("vv5") < -0.03).then(pl.lit("vv5_dn"))
          .when(pl.col("vv5") <= 0.03).then(pl.lit("vv5_flat"))
          .when(pl.col("vv5") <= 0.15).then(pl.lit("vv5_up"))
          .otherwise(pl.lit("vv5_up_strong")).alias("b_vv5"),
        # ACCELERATION (untested 2nd deriv — the "spike beginning" signal)
        pl.when(pl.col("vacc") < -0.06).then(pl.lit("vacc_decel"))
          .when(pl.col("vacc") <= 0.06).then(pl.lit("vacc_flat"))
          .when(pl.col("vacc") <= 0.18).then(pl.lit("vacc_accel"))
          .otherwise(pl.lit("vacc_accel_hi")).alias("b_vacc"),
        # WEEKLY MACD quadrant (the user's crossover signal)
        pl.col("wmacd_quad").fill_null("na").alias("b_wquad"),
        # weekly MACD histogram magnitude/sign
        pl.when(pl.col("whist") < -0.5).then(pl.lit("wh_neg"))
          .when(pl.col("whist") < 0).then(pl.lit("wh_neg_sm"))
          .when(pl.col("whist") < 0.5).then(pl.lit("wh_pos_sm"))
          .otherwise(pl.lit("wh_pos")).alias("b_whist"),
        # weekly RSI of VIX
        pl.when(pl.col("wrsi") < 40).then(pl.lit("wrsi_lo"))
          .when(pl.col("wrsi") < 55).then(pl.lit("wrsi_mid"))
          .when(pl.col("wrsi") < 70).then(pl.lit("wrsi_hi"))
          .otherwise(pl.lit("wrsi_vhi")).alias("b_wrsi"),
        # VIX vs its own 20d EMA (rising-above-trend)
        pl.when(pl.col("vix_vs_ema20") < -0.08).then(pl.lit("below_trend"))
          .when(pl.col("vix_vs_ema20") <= 0.08).then(pl.lit("at_trend"))
          .otherwise(pl.lit("above_trend")).alias("b_vtrend"),
        # ENGINE-FAITHFUL daily-equivalent weekly MACD hist (the ship feature)
        pl.when(pl.col("de_hist") < -0.4).then(pl.lit("de_neg"))
          .when(pl.col("de_hist") < 0).then(pl.lit("de_neg_sm"))
          .when(pl.col("de_hist") < 0.4).then(pl.lit("de_pos_sm"))
          .otherwise(pl.lit("de_pos")).alias("b_dehist"),
        # de_hist quadrant (sign x slope) — the 'pos_rising' (building) cohort
        pl.when((pl.col("de_hist") >= 0) & (pl.col("de_sl") >= 0)).then(pl.lit("pos_rising"))
          .when((pl.col("de_hist") >= 0) & (pl.col("de_sl") < 0)).then(pl.lit("pos_falling"))
          .when((pl.col("de_hist") < 0) & (pl.col("de_sl") >= 0)).then(pl.lit("neg_rising"))
          .otherwise(pl.lit("neg_falling")).alias("b_dequad"),
        # LAST-COMPLETED-WEEK true-weekly MACD quadrant (point-in-time, no look-ahead)
        pl.col("wquad_pit").fill_null("na").alias("b_wquad_pit"),
    ])


def cohort(T, axes, base, title, topk=16):
    g = T.group_by(axes).agg(
        pl.len().alias("n"),
        pl.col("loser").mean().alias("rate"),
        pl.col("pnl_dollars").filter(pl.col("loser")).abs().sum().alias("loss_usd"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
    ).filter(pl.col("n") >= MIN_N)
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = T.height
    rows = []
    for r in g.iter_rows(named=True):
        n, rate = r["n"], r["rate"]
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if n else 0.0
        dd_share = (r["loss_usd"] or 0.0) / total_loss
        conc = dd_share / (n / total_n) if n else 0.0
        rows.append({"cohort": " & ".join(str(r[a]) for a in axes), "n": n,
                     "rate": round(rate, 4), "lift": round(rate / base, 2), "z": round(z, 1),
                     "dd_conc": round(conc, 2), "mean_pnl": round(r["mean_pnl"], 4),
                     "dd_share": round(dd_share, 4)})
    rows.sort(key=lambda x: -abs(x["z"]))
    print(f"\n### {title}  (base loser-rate={base:.4f}, N={total_n})")
    print(f"{'cohort':30s} {'n':>8s} {'rate':>6s} {'lift':>5s} {'z':>7s} {'conc':>6s} {'mpnl':>7s} {'ddsh':>6s}")
    for r in rows[:topk]:
        flag = "  <<< LOW-EV+CONC" if (r["mean_pnl"] < LOWEV and r["dd_conc"] > 1.3) else ""
        print(f"{r['cohort']:30s} {r['n']:>8d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
              f"{r['z']:>7.1f} {r['dd_conc']:>6.2f} {r['mean_pnl']:>7.3f} {r['dd_share']:>6.3f}{flag}")
    return rows


def per_window(T, filt_expr, label):
    print(f"\n=== ROBUSTNESS by window: {label} ===")
    out = {}
    for w in sorted(T["window"].unique().to_list()):
        sub = T.filter(pl.col("window") == w)
        base = sub["loser"].mean()
        pk = sub.filter(filt_expr)
        if pk.height >= 30:
            pr, pm = pk["loser"].mean(), pk["option_pnl"].mean()
            print(f"  {w:12s} base={base:.3f} | n={pk.height:6d} rate={pr:.3f} "
                  f"lift={pr/base:.2f} mpnl={pm:+.3f}")
            out[w] = {"n": pk.height, "rate": round(pr, 4), "lift": round(pr/base, 2),
                      "mean_pnl": round(pm, 4)}
    return out


def main():
    print("== loading tape ==")
    T = load_tapes()
    print(f"   total call trades: {T.height}")
    print("== loading VIX momentum features ==")
    V = load_vix_features()
    T = attach(T, V)
    # restrict to rows that actually have VIX context (drops 2018/2020 pre-2021-04)
    have = T.filter(pl.col("vix").is_not_null())
    print(f"   trades with VIX context: {have.height} / {T.height} "
          f"(windows: {sorted(have['window'].unique().to_list())})")
    T = add_bins(have)
    if DD_FILTER > 0:
        T = T.filter(pl.col("entry_dd") >= DD_FILTER)
        print(f"   DD-active subset (entry_dd >= {DD_FILTER}): {T.height} trades")

    base = T["loser"].mean()
    rep = {"n": T.height, "base": round(base, 4), "dd_filter": DD_FILTER,
           "windows": sorted(T["window"].unique().to_list()), "cohorts": {}}

    # de_hist / de_sl distribution (calibrate ship thresholds)
    q = T.select([
        pl.col("de_hist").quantile(p).alias(f"deh_{int(p*100)}") for p in (0.1, 0.25, 0.5, 0.75, 0.9)
    ] + [pl.col("de_sl").quantile(p).alias(f"des_{int(p*100)}") for p in (0.1, 0.5, 0.9)])
    print("\n=== de_hist / de_sl quantiles (ship-feature scale) ===")
    print(q.to_dicts()[0])

    # single-axis VIX-momentum scans
    for ax in ["b_vix", "b_vv5", "b_vacc", "b_wquad", "b_wquad_pit", "b_whist", "b_wrsi",
               "b_vtrend", "b_dehist", "b_dequad", "b_dd"]:
        rep["cohorts"][ax] = cohort(T, [ax], base, f"single: {ax}")

    # ---- THE DECISIVE ORTHOGONAL TEST: within vix<20 (RXDD does NOT fire) ----
    calm = T.filter(pl.col("vix") < 20)
    if calm.height >= MIN_N:
        cb = calm["loser"].mean()
        print(f"\n########## ORTHOGONAL SLICE: VIX < 20 (RXDD off) — N={calm.height} ##########")
        for ax in ["b_vacc", "b_vv5", "b_wquad", "b_whist", "b_wrsi", "b_vtrend",
                   "b_dehist", "b_dequad"]:
            rep["cohorts"][f"calm_{ax}"] = cohort(calm, [ax], cb, f"vix<20 slice: {ax}")

    # two-axis interactions of interest
    for axes in [["b_vix", "b_vacc"], ["b_vix", "b_wquad"], ["b_dd", "b_wquad"],
                 ["b_dd", "b_vacc"], ["b_vix", "b_vv5"]]:
        rep["cohorts"]["x".join(axes)] = cohort(T, axes, base, f"two-axis: {'x'.join(axes)}")

    # robustness of the two most plausible "spike-beginning" candidates, per window
    rep["robust_accel_calm"] = per_window(
        T, (pl.col("vix") < 20) & (pl.col("b_vacc").is_in(["vacc_accel", "vacc_accel_hi"])),
        "vix<20 AND accelerating (spike from calm)")
    rep["robust_wpos_rising"] = per_window(
        T, pl.col("b_wquad") == "pos_rising",
        "weekly MACD pos_rising (vol momentum building)")
    rep["robust_wneg_rising"] = per_window(
        T, pl.col("b_wquad") == "neg_rising",
        "weekly MACD neg_rising (just crossed up from below)")
    # ENGINE-FAITHFUL ship-feature robustness (crash-artifact check)
    rep["robust_de_pos"] = per_window(
        T, (pl.col("de_hist") > 0) & (pl.col("vix") < 28),
        "SHIP de_hist>0 AND vix<28 (weekly-MACD building, panic-excluded)")
    rep["robust_de_posrise"] = per_window(
        T, (pl.col("b_dequad") == "pos_rising") & (pl.col("vix") < 28),
        "SHIP de pos_rising AND vix<28")
    rep["robust_de_pos_ddact"] = per_window(
        T, (pl.col("de_hist") > 0) & (pl.col("vix") < 28) & (pl.col("entry_dd") >= 0.13),
        "SHIP de_hist>0 AND vix<28 AND dd>=0.13 (where the lever fires)")
    # last-completed-week weekly MACD (point-in-time) robustness
    rep["robust_wpit_posrise"] = per_window(
        T, (pl.col("b_wquad_pit") == "pos_rising") & (pl.col("vix") < 28) & (pl.col("entry_dd") >= 0.13),
        "PIT weekly-MACD pos_rising AND vix<28 AND dd>=0.13")

    (OUT / ("mine_report%s.json" % ("_ddact" if DD_FILTER > 0 else ""))).write_text(
        json.dumps(rep, indent=2, default=str))
    print(f"\n== wrote {OUT}/mine_report{'_ddact' if DD_FILTER>0 else ''}.json ==")


if __name__ == "__main__":
    main()
