"""
Residual DD mining on the v70 Apex MC trade-tape, AFTER RXDD+SVR+MWDD are LIVE.

Overnight Stage-3 research (2026-06-07). Three call-alloc DD levers already ship on
the live Apex sleeve: RXDD (VIX 20-28 slow-bleed band), SVR (semivol_r skew band-pass),
MWDD (McClellan flat/topping band). Goal: find a 4th *orthogonal* low-EV + DD-concentrated
call cohort the shipped levers do NOT already capture, contract its sizing (the RXDD recipe).

KEY: a lever only scales premium_cost (dollar weight), NOT per-trade option_pnl. So on this
full-lever tape, a cohort that is ALREADY captured shows LOW mean_pnl but REDUCED dd_conc
(its dollars were shrunk); a genuinely RESIDUAL axis shows BOTH low mean_pnl AND high dd_conc
AND survives the "all-shipped-levers-off" orthogonal slice.

Candidate NEW axes (none covered by a shipped lever):
  - TRIN (Arms index, volume-FLOW breadth)         <- distinct from McClellan (count-momentum)
  - VIX velocity (5d ROC)                            <- RXDD uses VIX LEVEL only; direction is new
  - concurrency entry_open_calls (book crowding)     <- only the hard MAX_POS_CALL=12 cap covers it
  - entry_dd (portfolio DD state)                    <- DD_SOFT_BAND covers 0.35-0.55 (confirm captured)
Reference (confirm now ~flat in dd_conc = captured): VIX level, McClellan, breadth level.

Reject any CRASH ARTIFACT (low-EV only in crash windows, positive-EV in bull years) -- the
documented "DD-mitigation is structural" trap.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/dd_residual_v70/mine.py
"""
import os, sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "dd_residual_v70"
OUT.mkdir(parents=True, exist_ok=True)

MIN_N = 200
# DD_FILTER: only mine trades whose running portfolio DD >= this. The shipped levers
# (RXDD/MWDD) only ACT when dd >= DD_MIN (~0.08/0.13) -- that DD-gate is what makes them
# Pareto (no-op in calm). So the residual a 4th lever could harvest lives in the DD-active
# subset. Default 0 = whole tape. Set DD_FILTER=0.13 to mine the lever-active region.
DD_FILTER = float(os.environ.get("DD_FILTER", "0"))
LOWEV = 0.045   # mean option pnl below this == "low-EV" (RXDD's bleed band was +0.027, MWDD flat +0.046)


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
        print(f"!! no tape_*.parquet in {TAPE_DIR}")
        sys.exit(2)
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        try:
            df = pl.read_parquet(f)
        except Exception as e:
            print(f"   skip {f.name}: {e}")
            continue
        df = df.with_columns(pl.lit(label).alias("window"))
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


def load_context():
    """date -> internal breadth (DB) + VIX (DB), with vix velocity + breadth velocity."""
    from database.models.core import MarketBreadth, MarketRegime
    brd_rows = list(MarketBreadth.select(
        MarketBreadth.date, MarketBreadth.breadth_score, MarketBreadth.mcclellan_oscillator,
        MarketBreadth.pct_above_ema50, MarketBreadth.pct_above_ema200, MarketBreadth.trin,
        MarketBreadth.ad_line, MarketBreadth.new_highs_52w, MarketBreadth.new_lows_52w))
    B = pl.DataFrame({
        "date": [r.date for r in brd_rows],
        "breadth": [float(r.breadth_score) if r.breadth_score is not None else None for r in brd_rows],
        "mcc": [float(r.mcclellan_oscillator) if r.mcclellan_oscillator is not None else None for r in brd_rows],
        "pe50": [float(r.pct_above_ema50) if r.pct_above_ema50 is not None else None for r in brd_rows],
        "trin": [float(r.trin) if r.trin is not None else None for r in brd_rows],
        "adline": [float(r.ad_line) if r.ad_line is not None else None for r in brd_rows],
        "nh": [r.new_highs_52w for r in brd_rows],
        "nl": [r.new_lows_52w for r in brd_rows],
    })
    B = _norm_date(B, "date").sort("date")
    B = B.with_columns([
        (pl.col("breadth") - pl.col("breadth").shift(5)).alias("bvel5"),
        (pl.col("adline") - pl.col("adline").shift(5)).alias("adslope5"),
        ((pl.col("nh") - pl.col("nl"))).alias("nhnl"),
    ])
    reg_rows = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close,
                                        MarketRegime.regime_multiplier, MarketRegime.regime_composite))
    R = pl.DataFrame({
        "date": [r.date for r in reg_rows],
        "vix": [float(r.vix_close) if r.vix_close is not None else None for r in reg_rows],
        "reg_mult": [float(r.regime_multiplier) if r.regime_multiplier is not None else None for r in reg_rows],
        "reg_comp": [float(r.regime_composite) if r.regime_composite is not None else None for r in reg_rows],
    })
    R = _norm_date(R, "date").sort("date")
    # VIX velocity computed on the pure regime grid (exact 5 trading-day ROC), before join.
    R = R.with_columns([
        (pl.col("vix") - pl.col("vix").shift(5)).alias("vixvel5"),
        (pl.col("vix") - pl.col("vix").shift(10)).alias("vixvel10"),
    ])
    ctx = B.join(R, on="date", how="full", coalesce=True).sort("date")
    fill = [c for c in ctx.columns if c != "date"]
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in fill])
    print(f"   context rows={ctx.height}  ({ctx['date'].min()} .. {ctx['date'].max()})")
    return ctx


def add_bins(T):
    e = [
        # ---- NEW candidate axes ----
        pl.when(pl.col("trin").is_null()).then(pl.lit("trin_na"))
          .when(pl.col("trin") < 0.7).then(pl.lit("trin1_froth_u07"))
          .when(pl.col("trin") < 1.0).then(pl.lit("trin2_bull_07_10"))
          .when(pl.col("trin") < 1.3).then(pl.lit("trin3_neutral_10_13"))
          .when(pl.col("trin") < 1.8).then(pl.lit("trin4_bear_13_18"))
          .otherwise(pl.lit("trin5_panic_18p")).alias("b_trin"),
        pl.when(pl.col("vixvel5").is_null()).then(pl.lit("vv_na"))
          .when(pl.col("vixvel5") < -3).then(pl.lit("vv1_falling_un3"))
          .when(pl.col("vixvel5") < -1).then(pl.lit("vv2_dn_3_1"))
          .when(pl.col("vixvel5") < 1).then(pl.lit("vv3_flat_1_1"))
          .when(pl.col("vixvel5") < 3).then(pl.lit("vv4_up_1_3"))
          .otherwise(pl.lit("vv5_rising_3p")).alias("b_vixvel"),
        pl.when(pl.col("entry_open_calls") <= 3).then(pl.lit("cc1_0_3"))
          .when(pl.col("entry_open_calls") <= 6).then(pl.lit("cc2_4_6"))
          .when(pl.col("entry_open_calls") <= 9).then(pl.lit("cc3_7_9"))
          .when(pl.col("entry_open_calls") <= 11).then(pl.lit("cc4_10_11"))
          .otherwise(pl.lit("cc5_12p")).alias("b_concur"),
        pl.when(pl.col("entry_dd") < 0.10).then(pl.lit("dd1_u10"))
          .when(pl.col("entry_dd") < 0.25).then(pl.lit("dd2_10_25"))
          .when(pl.col("entry_dd") < 0.35).then(pl.lit("dd3_25_35"))
          .when(pl.col("entry_dd") < 0.55).then(pl.lit("dd4_35_55"))
          .otherwise(pl.lit("dd5_55p")).alias("b_dd"),
        pl.when(pl.col("adslope5").is_null()).then(pl.lit("ad_na"))
          .when(pl.col("adslope5") < -1500).then(pl.lit("ad1_crash"))
          .when(pl.col("adslope5") < -300).then(pl.lit("ad2_dn"))
          .when(pl.col("adslope5") < 300).then(pl.lit("ad3_flat"))
          .otherwise(pl.lit("ad4_up")).alias("b_adslope"),
        # ---- reference axes (already covered by a shipped lever) ----
        pl.when(pl.col("vix") >= 28).then(pl.lit("vix_28p"))
          .when(pl.col("vix") >= 20).then(pl.lit("vix_20_28"))
          .when(pl.col("vix") >= 15).then(pl.lit("vix_15_20"))
          .otherwise(pl.lit("vix_u15")).alias("b_vix"),
        pl.when(pl.col("mcc") < -50).then(pl.lit("mcc1_sev_un50"))
          .when(pl.col("mcc") < -15).then(pl.lit("mcc2_neg_50_15"))
          .when(pl.col("mcc") < 15).then(pl.lit("mcc3_flat_15_15"))
          .otherwise(pl.lit("mcc4_pos_15p")).alias("b_mcc"),
        pl.when(pl.col("breadth") >= 55).then(pl.lit("brd_hi_55p"))
          .when(pl.col("breadth") >= 40).then(pl.lit("brd_mid_40_55"))
          .when(pl.col("breadth") >= 25).then(pl.lit("brd_lo_25_40"))
          .otherwise(pl.lit("brd_vlo_u25")).alias("b_brd"),
        pl.col("tier").alias("b_tier"),
    ]
    return T.with_columns(e)


def cohort(T, axes, base, title, topn=16):
    g = T.group_by(axes).agg(
        pl.len().alias("n"),
        pl.col("loser").mean().alias("rate"),
        pl.col("pnl_dollars").filter(pl.col("loser")).abs().sum().alias("loss_usd"),
        pl.col("pnl_dollars").sum().alias("net_usd"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
    ).filter(pl.col("n") >= MIN_N)
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = T.height
    rows = []
    for r in g.iter_rows(named=True):
        n, rate = r["n"], r["rate"]
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if n else 0.0
        conc = ((r["loss_usd"] or 0.0) / total_loss) / (n / total_n) if n else 0.0
        dd_share = (r["loss_usd"] or 0.0) / total_loss
        rows.append({"cohort": " & ".join(str(r[a]) for a in axes), "n": n,
                     "rate": round(rate, 4), "lift": round(rate / base, 2), "z": round(z, 1),
                     "dd_conc": round(conc, 2), "dd_share": round(dd_share, 4),
                     "mean_pnl": round(r["mean_pnl"], 4)})
    rows.sort(key=lambda x: x["cohort"])
    print(f"\n### {title}  (base loser-rate={base:.4f}, N={total_n})")
    print(f"{'cohort':24s} {'n':>9s} {'rate':>6s} {'lift':>5s} {'z':>7s} {'conc':>6s} {'ddshr':>6s} {'mpnl':>7s}")
    for r in rows[:topn]:
        flag = "  <-LOW-EV" if r["mean_pnl"] < LOWEV else ""
        print(f"{r['cohort']:24s} {r['n']:>9d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
              f"{r['z']:>7.1f} {r['dd_conc']:>6.2f} {r['dd_share']:>6.3f} {r['mean_pnl']:>7.3f}{flag}")
    return rows


def robustness(T, bincol, target_vals, windows):
    """Per-window mean option pnl for target bin value(s) vs window base -> crash-artifact guard."""
    print(f"\n=== ROBUSTNESS: {bincol} in {target_vals} mean_pnl BY WINDOW (crash-artifact guard) ===")
    out = {}
    for w in windows:
        sub = T.filter(pl.col("window") == w)
        if sub.height == 0:
            continue
        base_m = sub["option_pnl"].mean()
        pk = sub.filter(pl.col(bincol).is_in(target_vals))
        if pk.height >= 30:
            pm = pk["option_pnl"].mean(); pr = pk["loser"].mean()
            tag = "LOW-EV" if pm < LOWEV else "ok"
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} rate={pr:.3f} mpnl={pm:+.3f}  [{tag}]")
            out[w] = {"base_mpnl": round(base_m, 4), "n": pk.height, "rate": round(pr, 4), "mean_pnl": round(pm, 4)}
        else:
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} (<30 skip)")
    return out


def main():
    print("== loading tapes ==")
    T = load_tapes()
    print(f"   total call trades: {T.height}   windows: {sorted(T['window'].unique().to_list())}")
    print("== loading context ==")
    ctx = load_context()
    T = T.sort("entry_date").join_asof(ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    if DD_FILTER > 0:
        n0 = T.height
        T = T.filter(pl.col("entry_dd") >= DD_FILTER)
        print(f"   DD_FILTER={DD_FILTER}: kept {T.height}/{n0} trades ({100*T.height/n0:.1f}%) with entry_dd>={DD_FILTER}")
    T = add_bins(T)
    base = T["loser"].mean()
    wins = sorted(T["window"].unique().to_list())
    report = {"n_trades": T.height, "base_loser_rate": round(base, 4),
              "base_mean_pnl": round(T["option_pnl"].mean(), 4), "windows": wins,
              "cohorts": {}, "robustness": {}}

    print("\n\n########## SINGLE-AXIS EV + DD-CONC SCAN (full-lever tape) ##########")
    print("Look for: LOW mean_pnl (<%.3f) AND high dd_conc AND high dd_share = residual DD not yet captured." % LOWEV)
    for ax in ["b_trin", "b_vixvel", "b_concur", "b_dd", "b_adslope", "b_vix", "b_mcc", "b_brd", "b_tier"]:
        report["cohorts"][ax] = cohort(T, [ax], base, f"single: {ax}")

    # crash-artifact guards on the most-promising NEW low-EV bands
    report["robustness"]["trin_mid_10_13"] = robustness(T, "b_trin", ["trin3_neutral_10_13"], wins)
    report["robustness"]["trin_mid_07_13"] = robustness(T, "b_trin", ["trin2_bull_07_10", "trin3_neutral_10_13"], wins)
    report["robustness"]["trin_extremes"]  = robustness(T, "b_trin", ["trin1_froth_u07", "trin5_panic_18p"], wins)
    report["robustness"]["ad_up"]          = robustness(T, "b_adslope", ["ad4_up"], wins)

    # ORTHOGONAL SLICE: where ALL THREE shipped levers are ~OFF -- RXDD off (vix<20),
    # MWDD off (|mcc|>30, outside the flat band), F3F ~off (breadth>=40).
    # Does any NEW axis STILL isolate a low-EV DD cohort here? (true residual orthogonality)
    print("\n\n########## ORTHOGONAL SLICE: vix<20 AND |mcc|>30 AND breadth>=40 (RXDD+MWDD+F3F ~off) ##########")
    O = T.filter((pl.col("vix") < 20) & (pl.col("mcc").abs() > 30) & (pl.col("breadth") >= 40))
    if O.height >= MIN_N:
        ob = O["loser"].mean()
        print(f"   slice trades: {O.height}  ({100*O.height/T.height:.1f}% of book)  slice base loser-rate={ob:.4f}  slice mean_pnl={O['option_pnl'].mean():+.4f}")
        report["ortho_slice_n"] = O.height
        report["ortho"] = {}
        for ax in ["b_trin", "b_vixvel", "b_concur", "b_dd", "b_adslope"]:
            report["ortho"][ax] = cohort(O, [ax], ob, f"ORTHO {ax}")
    else:
        print("   (orthogonal slice too small)")

    (OUT / "mine_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
